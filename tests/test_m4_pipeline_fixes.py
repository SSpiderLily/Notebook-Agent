"""M4/M5 主链路结构性缺口回归测试（G1/G2/G3）。

从仓库根目录运行：.venv/bin/python -m pytest tests/test_m4_pipeline_fixes.py -q

覆盖：
- G1：M3 持久化的 note→note 关联证据能传入树 Agent（不再恒为空列表）。
- G2：树节点父子关系真正落库（child.parent_id 指向 parent 的 node id）。
- G3：树 Agent 执行失败进失败清单，不再伪造成"正常新树"。

做法：用真实 Pipeline + RECORD transport 驱动 extract/status 阶段，仅替换
TreeBuilder 为可控桩（返回建根/追加判定或抛异常），从而精确、离线地验证
pipeline 内 tree_rebuild 段的编排逻辑，不触网、不提交。
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.core.tree_rebuild import TreeAssignment
from src.models.orm import Association, Tree, TreeNode
from src.services.pipeline import Pipeline


class _StubBuilder:
    """替换 TreeBuilder：按调用次数返回建根/追加判定，并记录收到的 event。

    `fail_at` 指定第几轮调用抛异常（模拟 Agent 失败，用于 G3）。
    """

    def __init__(self, fail_at: int | None = None):
        self.calls: list[tuple[dict, object]] = []  # (event, verified_tree_ids)
        self.fail_at = fail_at

    def run(self, event, verified_tree_ids=None):
        self.calls.append((event, verified_tree_ids))
        idx = len(self.calls)
        if self.fail_at is not None and idx == self.fail_at:
            raise RuntimeError("simulated agent failure")
        root_eid = self.calls[0][0]["event_id"]
        if idx == 1:
            return TreeAssignment(tree_id="NEW", event_id=event["event_id"],
                                  note_id=event["note_id"], confidence=0.9,
                                  evidence="建根", action="append")
        # 后续事件追加到第一棵树的根下
        return TreeAssignment(tree_id=f"T-{root_eid}", event_id=event["event_id"],
                              note_id=event["note_id"], parent_event_id=root_eid,
                              confidence=0.8, evidence="追加", action="append")


def _transport():
    """RECORD transport：按 prompt 关键字返回结构化 JSON。

    - extract → 单篇笔记提炼为 2 个事件
    - status  → 判定为 in_progress
    """
    def transport(prompt: str) -> str:
        if "提炼以下笔记" in prompt:
            return json.dumps({
                "title": "项目",
                "summary": "推进项目",
                "keywords": ["项目"],
                "candidate_tags": ["项目"],
                "events": [
                    {"content": "发起项目", "time_clue": "", "status_clue": "", "order_in_note": 0},
                    {"content": "推进进展", "time_clue": "", "status_clue": "", "order_in_note": 1},
                ],
            })
        if "判定以下树的状态" in prompt:
            return json.dumps({"tree_id": "T-1", "status": "in_progress", "confidence": 0.9,
                               "evidence": [], "rationale": ""})
        return '{}'
    return transport


def _mk_pipeline(tmp_path):
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "项目.md").write_text("# 项目\n推进项目", encoding="utf-8")
    recordings = tmp_path / "recordings"
    return Pipeline(vault, tmp_path / "db.sqlite", tmp_path / "runs", recordings,
                    mode="record", transport=_transport())


def _monkeypatch_builder(monkeypatch, pipeline, fail_at=None):
    from src.services import pipeline as pipeline_mod
    stub = _StubBuilder(fail_at=fail_at)
    monkeypatch.setattr(pipeline_mod, "TreeBuilder", lambda gateway: stub)
    return stub


def test_G1_associations_passed_to_tree_agent(monkeypatch, tmp_path):
    """G1：预置一条笔记间关联，断言该关联证据被传给树 Agent（非空）。"""
    pipeline = _mk_pipeline(tmp_path)
    # 预置一条 note→note 关联（src 为该笔记 id），验证 tree_rebuild 阶段读取。
    vault_note_id = pipeline.collector.collect()[0]["note_id"]
    with Session(pipeline.engine) as session:
        session.add(Association(src_type="note", src_id=vault_note_id, dst_id="other-note",
                                basis='["folder"]', confidence=0.9, evidence='[]', run_id="seed"))
        session.commit()
    stub = _monkeypatch_builder(monkeypatch, pipeline)

    pipeline.run()

    assert stub.calls, "树 Agent 应至少被调用一次"
    event, _ = stub.calls[0]
    assocs = event.get("associations")
    assert assocs, "树 Agent 输入里的 associations 不应为空（G1）"
    assert any(a["related_note_id"] == "other-note" and "folder" in a["basis"] for a in assocs), \
        "应包含预置关联及其 evidence/basis"


def test_G2_tree_node_parent_relationship_persisted(monkeypatch, tmp_path):
    """G2：建根+追加两事件后，DB 中树节点父子关系应正确回填 parent_id。"""
    pipeline = _mk_pipeline(tmp_path)
    _monkeypatch_builder(monkeypatch, pipeline)

    pipeline.run()

    engine = create_engine(f"sqlite:///{pipeline.db_path}")
    with Session(engine) as session:
        nodes = list(session.scalars(select(TreeNode).order_by(TreeNode.order)))
        assert len(nodes) == 2, "应有 2 个树节点"
        root = next(n for n in nodes if n.parent_id is None)
        child = next(n for n in nodes if n.parent_id is not None)
        assert child.parent_id == root.id, f"追加节点应指向根节点 id; got {child.parent_id} != {root.id}"
        # 两节点同属一棵树
        trees = list(session.scalars(select(Tree)))
        assert len(trees) == 1
        assert {n.tree_id for n in nodes} == {trees[0].id}


def test_G3_agent_failure_goes_to_failures_not_new_tree(monkeypatch, tmp_path):
    """G3：Agent 失败的事件应进 failure 清单，而不是伪造成 NEW 树。"""
    pipeline = _mk_pipeline(tmp_path)
    stub = _monkeypatch_builder(monkeypatch, pipeline, fail_at=2)  # 第 2 个事件抛异常

    pipeline.run()

    # 失败事件不应出现在 assignments（不产生伪树）
    assert len(stub.calls) == 2, "应只尝试两次事件"
    last = pipeline.rm.get_last_run()
    assert last is not None
    # 通过 StageIO 读取 tree_rebuild 产物（payload 内容，非内层包裹）
    raw = pipeline.io.read(last.id, "tree_rebuild")
    payload = raw["payload"] if isinstance(raw, dict) and "payload" in raw else raw
    assert any("agent_error" in f["error"] for f in payload["failures"]), \
        "失败事件应记录 agent_error 到 failures 清单"
    # 失败事件不得伪造为 0 置信度新树
    assert 0 not in [float(a.get("confidence", 0)) for a in payload["assignments"]], \
        "不应出现 agent_error 伪树"
    # DB 只应有 1 棵正常树（建根），失败事件不建树
    engine = create_engine(f"sqlite:///{pipeline.db_path}")
    with Session(engine) as session:
        nodes = list(session.scalars(select(TreeNode)))
        assert len(nodes) == 1, "失败事件不应生成节点/树"