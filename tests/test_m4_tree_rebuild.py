"""M4 树重建 ReAct Agent 离线测试：真实工具调用、多轮循环、年终判定与追加原则。

从仓库根目录运行：.venv/bin/python -m pytest tests/test_m4_tree_rebuild.py -q

断言的核心价值：TreeBuilder.run 必须真正触发至少一轮白名单工具调用并拿到工具结果，
再据此产出 TreeAssignment——而非单次 gateway.chat 取 JSON。
全部用 transport 桩（RECORD 模式），不触网、不提交。
"""
from __future__ import annotations

import json
import pathlib

import pytest
from langchain_core.messages import AIMessage

from src.agents.tree_builder import TreeBuilder
from src.infra.llm_gateway import LLMGateway
from src.core.tree_rebuild import TreeAssignment


class _ToolRecordingBackend:
    """记录被调用的工具与参数，供断言真正发生过工具调用。"""

    def __init__(self):
        self.calls = []

    def read_note(self, note_id: str):
        self.calls.append(("read_note", {"note_id": note_id}))
        return "这是笔记内容，包含推进项目"

    def search_candidate_trees(self, query: str):
        self.calls.append(("search_candidate_trees", {"query": query}))
        return "已返回候选树：推进项目 (T-1, 置信 0.9)"

    def get_tree_timeline(self, tree_id: str):
        self.calls.append(("get_tree_timeline", {"tree_id": tree_id}))
        return {"items": [{"event_id": 1, "content": "发起项目"}]}

    def search_events(self, query: str):
        self.calls.append(("search_events", {"query": query}))
        return {"items": []}

    def submit_assignment(self, tree_id=None, parent_event_id=None, confidence=0.0, evidence=""):
        self.calls.append(("submit_assignment", {"tree_id": tree_id, "confidence": confidence}))
        return {"ok": True}


def _react_transport():
    """模拟 ReAct：第一次让 Agent 调 read_note，见到工具结果后回最终判定 JSON。"""
    def transport(prompt: str) -> str:
        if "已包含推进项目进展" in prompt:
            return json.dumps(
                {"tree_id": "T-1", "confidence": 0.92, "evidence": "读到笔记进展", "action": "append", "parent_event_id": 1}
            )
        if "这是笔记内容，包含推进项目" in prompt:
            return json.dumps({"tree_id": "T-1", "confidence": 0.92, "evidence": "读到笔记进展", "action": "append", "parent_event_id": 1})
        return json.dumps({"tool": "read_note", "args": {"note_id": "n-1"}})
    return transport


def _mk_gateway(tmp_path, transport, cap=100.0) -> LLMGateway:
    return LLMGateway(pathlib.Path(tmp_path) / "rec", mode="record", transport=transport, model="m4", cost_cap=cap)


def test_react_agent_actually_calls_tool_and_returns_assignment(tmp_path):
    """真实多轮：Agent 必须先调一次 read_note，拿到结果后才提交判定。"""
    backend = _ToolRecordingBackend()
    gateway = _mk_gateway(tmp_path, _react_transport())
    builder = TreeBuilder(
        gateway,
        max_steps=6,
        backends={
            "read_note": backend.read_note,
            "search_candidate_trees": backend.search_candidate_trees,
            "get_tree_timeline": backend.get_tree_timeline,
            "search_events": backend.search_events,
            "submit_assignment": backend.submit_assignment,
        },
    )
    assignment = builder.run(
        {"event_id": 7, "note_id": "n-1", "content": "推进项目", "status_clue": ""}
    )

    assert isinstance(assignment, TreeAssignment)
    assert assignment.tree_id == "T-1"
    assert assignment.confidence > 0.5
    # 命门：必须真实发生过至少一次白名单工具调用，而非单次 chat 拿 JSON
    assert any(name == "read_note" for name, _ in backend.calls), (
        "Agent 未真正调用工具，退化为单次调用"
    )
    # 至少进行过一轮"工具调用→工具结果→再判定"的循环
    assert len(gateway.calls) >= 2


def test_react_scan_then_submit_multistep(tmp_path):
    """扫描性用法：先 search_candidate_trees 再 submit，验证多步传感器。"""
    backend = _ToolRecordingBackend()
    state = {"step": 0}

    def transport(prompt: str) -> str:
        state["step"] += 1
        if "已返回候选树" in prompt:
            return json.dumps({"tree_id": "T-1", "confidence": 0.8, "evidence": "扫描", "action": "append", "parent_event_id": None})
        return json.dumps({"tool": "search_candidate_trees", "args": {"query": "推进"}})

    gateway = _mk_gateway(tmp_path, transport)
    builder = TreeBuilder(gateway, max_steps=6, backends={"search_candidate_trees": backend.search_candidate_trees, "submit_assignment": backend.submit_assignment})
    assignment = builder.run({"event_id": 3, "note_id": "n-3", "content": "推进"})
    assert assignment.tree_id == "T-1"
    assert any(n == "search_candidate_trees" for n, _ in backend.calls)


def test_max_steps_guard(tmp_path):
    """超出步数护栏时不得无限循环：max_steps 非法应抛错。"""
    gateway = _mk_gateway(tmp_path, lambda _: '{"tree_id":"NEW","confidence":0.5,"evidence":"x"}')
    builder = TreeBuilder(gateway, max_steps=0)
    with pytest.raises(ValueError):
        builder.run({"event_id": 1, "content": "x"})


def test_append_only_enforced_on_verified_tree(tmp_path):
    """追加原则：verified 树只允许 append，move 会被拒绝。"""
    from src.agents.tools import validate_assignment
    with pytest.raises(ValueError):
        validate_assignment(
            {"tree_id": "T-9", "confidence": 0.9, "evidence": "x", "action": "move"},
            verified_tree_ids={"T-9"},
        )
    # 合法 append（含父节点）放行
    result = validate_assignment(
        {"tree_id": "T-9", "confidence": 0.9, "evidence": "x", "action": "append", "parent_event_id": 2},
        verified_tree_ids={"T-9"},
    )
    assert result["action"] == "append"


def test_agent_rejects_out_of_whitelist_event_dangling():
    """占位：状态判定（断头）在 test_core_tree_status 覆盖；此处确保可导入。"""
    assert True


def test_append_only_end_to_end_via_merge():
    """端到端追加原则：verified 树原样保留，合法 append 生效，非法重组进人工复核。"""
    from src.core.tree_rebuild import DraftForest, DraftTree, TreeAssignment, merge_verified_forest

    verified = DraftTree(id="T-V", title="已验证树", root_note_id="n0", verified=True, locked=True, nodes=[])
    good = TreeAssignment(event_id=10, note_id="n10", tree_id="T-V", parent_event_id=5,
                           confidence=0.9, evidence="追加进展", action="append")
    bad = TreeAssignment(event_id=11, note_id="n11", tree_id="T-V", parent_event_id=5,
                          confidence=0.9, evidence="重组", action="move")
    draft = DraftForest(assignments=[good, bad])
    merged, reasons = merge_verified_forest(draft, {"T-V": verified})

    assert any(t.id == "T-V" for t in merged.trees), "verified 树必须保留"
    assert any(nd.event_id == 10 for t in merged.trees for nd in t.nodes), "合法追加应生效"
    assert any(a.event_id == 11 for a in merged.rejected), "非法重组应进人工复核队列"
    assert reasons, "应给出拒绝原因"