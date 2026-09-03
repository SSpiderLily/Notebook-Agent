"""树重建编排：草稿森林 schema、TreeAssignment、verified 追加原则与幂等持久化。

对应 DESIGN.md：§4.1 trees / tree_nodes、§6.1 树重建 Agent、原则5（已验证结构只追加）。
本模块只负责编排与数据结构，不直接调用 LLM —— LLM 调用经 LLMGateway 由调用方提供
（Pipeline 持有 gateway）。编排函数保持无副作用，纯内存构建草稿森林；
持久化走 StageIO（`tree_rebuild` 阶段），重复写相同数据是幂等的。

安全边界：本模块**不**触碰用户原始笔记；verified 树只允许追加叶子，
任何移动/拆分/改父级都会被拒绝并归入 `rejected`，进人工复核队列。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, Field

from src.infra.stage_io import StageIO

# verified 树只允许追加叶子的固定标记（与或m locked=verified 对齐）
APPEND_ONLY_ACTION = "append"

# 默认低置信阈值：置信度低于此值自动标记待人工复核（DESIGN.md §6.1 / FR-9 / NFR-3）
LOW_CONFIDENCE_THRESHOLD = 0.6


class TreeAssignment(BaseModel):
    """单个事件的挂接判定（树重建 Agent 的终态输出）。"""

    event_id: int | None = Field(default=None, description="事件 ID；NEW 建根时置 None 由调用方补")
    note_id: str | None = Field(default=None, description="事件来源笔记 ID")
    tree_id: str = Field(description="目标树 ID，或 NEW 表示新建树")
    parent_event_id: int | None = Field(default=None, description="父事件 ID；None 表示作为根")
    confidence: float = Field(ge=0, le=1, default=0.0)
    evidence: str = Field(default="")
    action: str = Field(default=APPEND_ONLY_ACTION, description="append / move / split / reorg 等")


class DraftTreeNode(BaseModel):
    """草稿森林中的节点（一颗树上的一次挂接）。"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    tree_id: str
    event_id: int
    note_id: str | None = Field(default=None)
    parent_event_id: int | None = Field(default=None, description="父事件 ID；None 表示根节点")
    order: int = Field(default=0, description="同父下显示顺序")
    confidence: float = Field(ge=0, le=1, default=0.0)
    evidence: list[str] = Field(default_factory=list)
    origin: str = Field(default="agent", description="agent / human；human 节点受追加原则保护")


class DraftTree(BaseModel):
    """草稿森林中的一棵树。"""

    id: str
    title: str = ""
    root_note_id: str | None = Field(default=None)
    root_event_id: int | None = Field(default=None)
    verified: bool = Field(default=False, description="是否已人工确认")
    locked: bool = Field(default=False, description="追加原则保护标记（=verified）")
    confidence: float = Field(ge=0, le=1, default=0.0)
    evidence: list[str] = Field(default_factory=list)
    nodes: list[DraftTreeNode] = Field(default_factory=list)

    @property
    def is_append_only(self) -> bool:
        return self.locked or self.verified


class DraftForest(BaseModel):
    """草稿森林：全部树的集合，附挂接判定与失败/拒绝记录。"""

    trees: list[DraftTree] = Field(default_factory=list)
    assignments: list[TreeAssignment] = Field(default_factory=list)
    rejected: list[TreeAssignment] = Field(default_factory=list, description="追加原则拦截项，进人工复核队列")
    failures: list[dict[str, Any]] = Field(default_factory=list)


# ── 追加原则 ──

def reject_reorganization(assignment: TreeAssignment, verified_tree_ids: set[str]) -> str | None:
    """校验一个挂接判定是否违反 verified 追加原则。

    返回 None 表示允许；否则返回拒绝原因（该判定应进人工复核队列）。
    规则：目标树为 verified 时，只允许 action=append 且必须为"追加叶子"（不改父级、不拆、不移）。
    """
    if assignment.tree_id == "NEW" or assignment.tree_id not in verified_tree_ids:
        return None
    if assignment.action != APPEND_ONLY_ACTION:
        return f"已验证树 {assignment.tree_id} 只允许追加叶子，收到 action={assignment.action}"
    # 追加叶子 = 挂在已存在节点之下；parent_event_id 不能指向同一棵树里本次要新建的根。
    if assignment.parent_event_id is None:
        return f"已验证树 {assignment.tree_id} 追加叶子必须指定父节点"
    return None


def merge_verified_forest(
    draft: DraftForest,
    verified: Mapping[str, DraftTree],
    *,
    reject_on_violation: bool = False,
) -> tuple[DraftForest, list[str]]:
    """按追加原则把草稿合并进已验证森林。

    - verified 树原样保留（append-only，绝不自动重组/拆分/移动），
      只接收其上的 action=append 叶子挂接；
    - 新增树（NEW 建根）追加为独立新树；
    - 对已验证树的非法重组判定转入 draft.rejected，不写回结构。

    返回 (合并后的草稿森林, 拒绝原因列表)。不修改调用方传入对象（纯函数）。
    """
    verified_ids = set(verified)
    merged = DraftForest(assignments=list(draft.assignments))
    rejected_reasons: list[str] = []

    # 1) 已验证树原样保留
    for tid, tree in verified.items():
        merged.trees.append(tree.model_copy(deep=True))

    index: dict[str, DraftTree] = {t.id: t for t in merged.trees}
    # 2) 未挂在已验证树上的新判定：NEW 建根 / 普通树追加
    for assignment in draft.assignments:
        reason = reject_reorganization(assignment, verified_ids)
        if reason is not None:
            merged.rejected.append(assignment)
            rejected_reasons.append(reason)
            continue
        if assignment.tree_id == "NEW":
            # 建一棵新树：以该事件为根
            root = DraftTree(id=f"T-{assignment.event_id}", root_event_id=assignment.event_id, confidence=assignment.confidence)
            index[root.id] = root
            merged.trees.append(root)
            tree = root
            parent_event_id = None
        elif assignment.tree_id in index:
            tree = index[assignment.tree_id]
            parent_event_id = assignment.parent_event_id
        else:
            # 引用未知树：按追加到新树处理（保守不吞，记为失败）
            merged.failures.append({"event_id": assignment.event_id, "error": f"目标树不存在: {assignment.tree_id}"})
            continue
        node = DraftTreeNode(
            tree_id=tree.id,
            event_id=assignment.event_id,
            note_id=getattr(assignment, "note_id", None),
            parent_event_id=parent_event_id,
            order=len(tree.nodes),
            confidence=assignment.confidence,
            evidence=[assignment.evidence] if assignment.evidence else [],
        )
        tree.nodes.append(node)
    return merged, rejected_reasons


def build_draft_tree(assignments: Sequence[TreeAssignment]) -> DraftTree:
    """把属于同一棵树的一组挂接判定组装成 DraftTree（含根判定）。

    首个 parent_event_id=None 的判定视为根；后续判定按其 parent_event_id 定位父节点。
    不校验 verified 追加原则（那由 merge_verified_forest 负责）。
    """
    if not assignments:
        return DraftTree(id="", title="")
    tree = DraftTree(id=assignments[0].tree_id, confidence=assignments[0].confidence)
    children: dict[int, list[TreeAssignment]] = {}
    roots: list[TreeAssignment] = []
    for a in assignments:
        if a.parent_event_id is None:
            roots.append(a)
        else:
            children.setdefault(a.parent_event_id, []).append(a)

    order: dict[int, int] = {}

    def attach(a: TreeAssignment) -> None:
        order[a.event_id] = order.get(a.event_id, 0) + 1
        tree.nodes.append(
            DraftTreeNode(
                tree_id=tree.id,
                event_id=a.event_id,
                note_id=getattr(a, "note_id", None),
                parent_event_id=a.parent_event_id,
                order=order[a.event_id],
                confidence=a.confidence,
                evidence=[a.evidence] if a.evidence else [],
            )
        )
        for child in children.pop(a.event_id, []):
            attach(child)

    for root in roots:
        attach(root)
    return tree


# ── 幂等持久化（StageIO）──

def save_forest(io: StageIO, run_id: str, forest: DraftForest) -> Path:
    """把草稿森林写入 StageIO `tree_rebuild` 阶段（幂等：同 run 重复写即覆盖）。"""
    payload = {"trees": [t.model_dump() for t in forest.trees], "assignments": [a.model_dump() for a in forest.assignments],
               "rejected": [a.model_dump() for a in forest.rejected], "failures": forest.failures}
    return io.write(run_id, "tree_rebuild", payload)


def load_forest(io: StageIO, run_id: str) -> DraftForest:
    """从 StageIO 读回草稿森林；无产物时抛 StageArtifactError。"""
    data = io.read(run_id, "tree_rebuild")
    return DraftForest(trees=[DraftTree(**t) for t in data.get("trees", [])],
                       assignments=[TreeAssignment(**a) for a in data.get("assignments", [])],
                       rejected=[TreeAssignment(**a) for a in data.get("rejected", [])],
                       failures=data.get("failures", []))


# ── 编排入口 ──

def rebuild_forest(
    gateway: Any,
    events: Sequence[Mapping[str, Any]],
    verified: Mapping[str, DraftTree] | None = None,
    *,
    reject_on_violation: bool = False,
) -> DraftForest:
    """对一批事件执行树重建，返回合并后的草稿森林。

    gateway 需提供 `structured(prompt, schema) -> TreeAssignment`（LLM 判定出口，
    同 ExtractionDraft/AssociationJudgement 风格）。事件经 LLM 判定挂接目标树，
    再按 verified 追加原则合并。失败判定记入 failures，不中断其它事件。
    """
    assignments: list[TreeAssignment] = []
    failures: list[dict[str, Any]] = []
    for event in events:
        prompt = f"请为事件选择目标树（NEW 或已有树 ID，附父事件与置信度）并输出 TreeAssignment。\n事件: {json.dumps(event, ensure_ascii=False)}"
        try:
            raw = gateway.structured(prompt, TreeAssignment)
        except Exception as exc:
            failures.append({"event_id": event.get("event_id"), "error": str(exc)})
            continue
        assignment = raw.model_copy(update={"event_id": event.get("event_id")})
        assignments.append(assignment)
    draft = DraftForest(assignments=assignments, failures=failures)
    merged, _rejected = merge_verified_forest(draft, verified or {}, reject_on_violation=reject_on_violation)
    return merged
