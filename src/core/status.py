"""状态判定与断头检测：结构化 schema、证据、四状态与低置信度标记。

对应 DESIGN.md：§2.1 路径状态（已完成/进行中/断头）、§6.2 状态判定判断器、
FR-5（断头检测为核心）、NFR-3（低置信项优先暴露）。四状态在 orm 中为
complete / in_progress / dangling_confirmed / dangling_suspected。

判定统一走单次结构化 LLM 调用（判断器），并保留确定性兜底，供无 LLM 的
纯逻辑路径（如测试/离线对账）使用。置信度 < LOW_CONFIDENCE_THRESHOLD 自动标记。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, Field

from src.infra.stage_io import StageIO
from src.models.orm import DANGLING_TREE_STATUSES, TREE_STATUSES

LOW_CONFIDENCE_THRESHOLD = 0.6

COMPLETE = "complete"
IN_PROGRESS = "in_progress"
DANGLING_CONFIRMED = "dangling_confirmed"
DANGLING_SUSPECTED = "dangling_suspected"


class StatusJudgement(BaseModel):
    """单棵树的四状态判定。"""

    tree_id: str
    status: str = Field(description="complete / in_progress / dangling_confirmed / dangling_suspected")
    confidence: float = Field(ge=0, le=1, default=0.0)
    evidence: list[str] = Field(default_factory=list)
    rationale: str = Field(default="")
    low_confidence: bool = Field(default=False, description="置信度低于阈值时自动标记")

    @property
    def is_dangling(self) -> bool:
        return self.status in DANGLING_TREE_STATUSES


class StatusResult(BaseModel):
    """一批判定结果。"""

    judgements: list[StatusJudgement] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)


def mark_low_confidence(judgement: StatusJudgement, threshold: float = LOW_CONFIDENCE_THRESHOLD) -> StatusJudgement:
    """按置信度自动标记 low_confidence（幂等，供后处理与前端优先暴露）。"""
    low = judgement.confidence < threshold
    if judgement.low_confidence != low:
        return judgement.model_copy(update={"low_confidence": low})
    return judgement


# ── 确定性兜底逻辑（无 LLM 路径）──

def _event_done(event: Mapping[str, Any]) -> bool:
    clue = str(event.get("status_clue") or "").lower()
    done_markers = ("done", "完成", "已完成", "finished", "closed", "收尾", "结案")
    return any(m in clue for m in done_markers)


def infer_tree_status(
    events: Sequence[Mapping[str, Any]],
    *,
    evidence: list[str] | None = None,
) -> StatusJudgement:
    """确定性兜底判定：据事件状态线索推断树状态（四状态）。

    规则（简单启发，供无 LLM / 离线场景）：
    - 全部事件已完成        → complete
    - 无已完成事件（全部未闭合）→ dangling_suspected（疑似断头）
    - 部分完成、部分未完成    → in_progress
    确定性路径的置信度固定为 1.0（规则明确），低置信度不会触发。
    """
    evidence = list(evidence or [])
    if not events:
        status = IN_PROGRESS
        evidence.append("无事件节点，按进行中处理")
    else:
        done = sum(1 for e in events if _event_done(e))
        if done == len(events):
            status = COMPLETE
            evidence.append(f"{done}/{len(events)} 事件均完成")
        elif done == 0:
            status = DANGLING_SUSPECTED
            evidence.append("无事件完成，疑似断头（确定性兜底，需 LLM 复核）")
        else:
            status = IN_PROGRESS
            evidence.append(f"{done}/{len(events)} 事件完成，其余进行中")
    return mark_low_confidence(StatusJudgement(tree_id="", status=status, confidence=1.0, evidence=evidence))


# ── LLM 结构化判定 ──

def judge_tree_status(
    gateway: Any,
    tree_id: str,
    tree_events: Sequence[Mapping[str, Any]],
    *,
    evidence_context: Mapping[str, Any] | None = None,
) -> StatusJudgement:
    """对一棵树做四状态判定（单次结构化调用 + 证据）。

    gateway 需提供 `structured(prompt, schema)`，schema 复用 StatusJudgement
    （与 extraction/association 判断器风格一致）。树证据（节点完成情况、时间线）
    由调用方传入 tree_events 与 evidence_context。
    """
    prompt = (
        f"请判定以下树的状态，输出符合 StatusJudgement schema 的 JSON。\n"
        f"四状态: {list(TREE_STATUSES)}\n"
        f"tree_id: {tree_id}\n"
        f"树证据: {json.dumps(evidence_context or {}, ensure_ascii=False)}\n"
        f"事件: {json.dumps([{**e} for e in tree_events], ensure_ascii=False, default=str)}"
    )
    judgement = gateway.structured(prompt, StatusJudgement)
    judgement.tree_id = tree_id
    return mark_low_confidence(judgement)


def judge_forest(
    gateway: Any,
    trees: Mapping[str, list[Mapping[str, Any]]],
    *,
    fallback: bool = True,
) -> StatusResult:
    """对森林中全部树做状态判定；单树失败隔离，fallback 时用确定性兜底。

    trees 形如 {tree_id: [events...]}。返回 StatusResult（judgements + failures）。
    """
    result = StatusResult()
    for tree_id, events in trees.items():
        try:
            judgement = judge_tree_status(gateway, tree_id, events)
        except Exception as exc:
            if fallback:
                judgement = infer_tree_status(events)
                judgement.tree_id = tree_id
                judgement.rationale = f"LLM 判定失败，采用确定性兜底: {exc}"
                result.judgements.append(judgement)
            else:
                result.failures.append({"tree_id": tree_id, "error": str(exc)})
            continue
        result.judgements.append(judgement)
    return result


# ── 幂等持久化（StageIO）──

def save_statuses(io: StageIO, run_id: str, result: StatusResult) -> Path:
    """把判定结果写入 StageIO `status_judge` 阶段（幂等：同 run 重复写即覆盖）。"""
    payload = {"judgements": [j.model_dump() for j in result.judgements], "failures": result.failures}
    return io.write(run_id, "status_judge", payload)


def load_statuses(io: StageIO, run_id: str) -> StatusResult:
    """从 StageIO 读回判定结果。"""
    data = io.read(run_id, "status_judge")
    return StatusResult(judgements=[StatusJudgement(**j) for j in data.get("judgements", [])],
                        failures=data.get("failures", []))


def dangling_list(result: StatusResult) -> list[StatusJudgement]:
    """提取断头判定清单（一级输出，FR-5）。"""
    return [j for j in result.judgements if j.is_dangling]
