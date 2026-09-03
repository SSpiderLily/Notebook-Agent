"""核心模块单测：树重建编排（追加原则/幂等持久化）与状态判定（四状态/低置信度）。"""
import json

import pytest

from src.core.status import (
    COMPLETE,
    DANGLING_SUSPECTED,
    IN_PROGRESS,
    StatusJudgement,
    StatusResult,
    dangling_list,
    infer_tree_status,
    judge_forest,
    judge_tree_status,
    load_statuses,
    mark_low_confidence,
    save_statuses,
)
from src.core.tree_rebuild import (
    DraftForest,
    DraftTree,
    DraftTreeNode,
    TreeAssignment,
    build_draft_tree,
    load_forest,
    merge_verified_forest,
    rebuild_forest,
    reject_reorganization,
    save_forest,
)
from src.infra.stage_io import StageIO


class _Gateway:
    """最小可复现的 LLM gateway 桩：按场景返回固定 JSON。"""

    def __init__(self, payload):
        self.payload = payload

    def structured(self, prompt, schema):
        return schema.model_validate(self.payload)


# ── 追加原则 ──

def test_reject_reorganization_allows_append_on_verified():
    assert reject_reorganization(TreeAssignment(tree_id="T1", parent_event_id=5, confidence=.8, action="append"), {"T1"}) is None


def test_reject_reorganization_blocks_move_split_reorg():
    for action in ("move", "split", "reorg"):
        assert reject_reorganization(TreeAssignment(tree_id="T1", parent_event_id=5, confidence=.8, action=action), {"T1"}) is not None


def test_reject_reorganization_blocks_append_without_parent():
    assert reject_reorganization(TreeAssignment(tree_id="T1", parent_event_id=None, confidence=.8, action="append"), {"T1"}) is not None


def test_merge_verified_keeps_verified_intact_and_rejects_violation():
    verified_tree = DraftTree(id="T1", verified=True, locked=True, nodes=[DraftTreeNode(tree_id="T1", event_id=1, order=0)])
    draft = DraftForest(assignments=[
        TreeAssignment(event_id=9, tree_id="T1", parent_event_id=1, confidence=.9, action="append"),   # 合法追加
        TreeAssignment(event_id=10, tree_id="T1", parent_event_id=1, confidence=.8, action="move"),    # 非法重组
    ])
    merged, reasons = merge_verified_forest(draft, {"T1": verified_tree})
    assert len(merged.trees) == 1
    assert merged.trees[0].id == "T1"
    # 合法追加叶子已挂上，非法 move 被拦截进 rejected
    assert any(n.event_id == 9 for n in merged.trees[0].nodes)
    assert len(merged.rejected) == 1 and merged.rejected[0].action == "move"
    assert len(reasons) == 1


def test_merge_verified_creates_new_tree():
    draft = DraftForest(assignments=[TreeAssignment(event_id=3, tree_id="NEW", confidence=.7)])
    merged, _ = merge_verified_forest(draft, {})
    assert len(merged.trees) == 1
    assert merged.trees[0].root_event_id == 3
    assert merged.trees[0].nodes[0].event_id == 3


def test_build_draft_tree_orders_by_parent():
    ass = [
        TreeAssignment(event_id=1, tree_id="T", parent_event_id=None, confidence=.9),
        TreeAssignment(event_id=2, tree_id="T", parent_event_id=1, confidence=.8),
        TreeAssignment(event_id=3, tree_id="T", parent_event_id=2, confidence=.7),
    ]
    tree = build_draft_tree(ass)
    assert [n.event_id for n in tree.nodes] == [1, 2, 3]


# ── 幂等持久化 ──

def test_save_load_forest_roundtrip(tmp_path):
    io = StageIO(tmp_path / "runs")
    forest = DraftForest(trees=[DraftTree(id="T1", nodes=[DraftTreeNode(tree_id="T1", event_id=1)])],
                         rejected=[TreeAssignment(event_id=9, tree_id="T1", parent_event_id=1, action="move", confidence=.5)])
    save_forest(io, "run-1", forest)
    loaded = load_forest(io, "run-1")
    assert loaded.trees[0].id == "T1"
    assert loaded.trees[0].nodes[0].event_id == 1
    assert loaded.rejected[0].action == "move"


def test_save_forest_idempotent(tmp_path):
    io = StageIO(tmp_path / "runs")
    f1 = DraftForest(trees=[DraftTree(id="T1")])
    save_forest(io, "run-1", f1)
    save_forest(io, "run-1", f1)  # 重复写不报错、不累积
    loaded = load_forest(io, "run-1")
    assert len(loaded.trees) == 1


def test_rebuild_forest_uses_gateway_and_merges(tmp_path):
    gateway = _Gateway({"tree_id": "NEW", "parent_event_id": None, "confidence": 0.7, "evidence": "证据", "action": "append"})
    events = [{"event_id": 1, "note_id": "n1", "content": "开始项目"}]
    forest = rebuild_forest(gateway, events)
    assert len(forest.trees) == 1
    assert forest.assignments[0].tree_id == "NEW"


# ── 状态判定 ──

def test_infer_tree_status_complete():
    j = infer_tree_status([{"status_clue": "已完成"}, {"status_clue": "done"}])
    assert j.status == COMPLETE and j.confidence == 1.0


def test_infer_tree_status_dangling():
    j = infer_tree_status([{"status_clue": "计划"}, {"status_clue": "待办"}])
    assert j.status == DANGLING_SUSPECTED


def test_infer_tree_status_in_progress():
    j = infer_tree_status([{"status_clue": "完成"}, {"status_clue": "进行中"}])
    assert j.status == IN_PROGRESS


def test_mark_low_confidence_flag():
    j = mark_low_confidence(StatusJudgement(tree_id="T1", status=COMPLETE, confidence=0.4))
    assert j.low_confidence is True
    assert mark_low_confidence(StatusJudgement(tree_id="T1", status=COMPLETE, confidence=0.7)).low_confidence is False


def test_judge_tree_status_uses_gateway():
    gateway = _Gateway({"tree_id": "T1", "status": COMPLETE, "confidence": 0.4, "evidence": ["全部完成"]})
    j = judge_tree_status(gateway, "T1", [{"status_clue": "已完成"}])
    assert j.status == COMPLETE and j.low_confidence is True


def test_judge_forest_fallback_on_failure():
    class _Fail:
        def structured(self, prompt, schema):
            raise RuntimeError("boom")
    result = judge_forest(_Fail(), {"T1": [{"status_clue": "计划"}], "T2": [{"status_clue": "完成"}]}, fallback=True)
    assert len(result.judgements) == 2
    assert result.judgements[0].status == DANGLING_SUSPECTED
    assert "兜底" in result.judgements[0].rationale


def test_save_load_statuses_roundtrip(tmp_path):
    io = StageIO(tmp_path / "runs")
    result = StatusResult(judgements=[StatusJudgement(tree_id="T1", status=COMPLETE, confidence=.9)])
    save_statuses(io, "run-1", result)
    loaded = load_statuses(io, "run-1")
    assert loaded.judgements[0].tree_id == "T1"
    assert loaded.judgements[0].status == COMPLETE


def test_dangling_list_extracts_only_dangling():
    result = StatusResult(judgements=[
        StatusJudgement(tree_id="T1", status=COMPLETE),
        StatusJudgement(tree_id="T2", status=DANGLING_SUSPECTED),
        StatusJudgement(tree_id="T3", status="dangling_confirmed"),
    ])
    dl = dangling_list(result)
    assert {j.tree_id for j in dl} == {"T2", "T3"}
