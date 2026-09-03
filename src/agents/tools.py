"""树重建 Agent 的严格白名单工具。"""
from __future__ import annotations
from typing import Any, Callable, Mapping
from langchain_core.tools import StructuredTool

TOOL_NAMES = ("search_candidate_trees", "read_note", "get_tree_timeline", "search_events", "submit_assignment")

def build_tools(backend: Mapping[str, Callable[..., Any]] | None = None) -> list[StructuredTool]:
    """从后端回调构造工具；未提供回调时返回安全的空结果工具。"""
    backend = backend or {}
    def call(name: str, **kwargs: Any) -> Any:
        fn = backend.get(name)
        if fn is None: return {"items": [], "message": "暂无数据"}
        return fn(**kwargs)
    return [
        StructuredTool.from_function(lambda query: call("search_candidate_trees", query=query), name="search_candidate_trees", description="搜索候选树。"),
        StructuredTool.from_function(lambda note_id: call("read_note", note_id=note_id), name="read_note", description="回读笔记原文。"),
        StructuredTool.from_function(lambda tree_id: call("get_tree_timeline", tree_id=tree_id), name="get_tree_timeline", description="查询树时间线。"),
        StructuredTool.from_function(lambda query: call("search_events", query=query), name="search_events", description="搜索事件库。"),
        StructuredTool.from_function(lambda tree_id, parent_event_id=None, confidence=0.0, evidence="": call("submit_assignment", tree_id=tree_id, parent_event_id=parent_event_id, confidence=confidence, evidence=evidence), name="submit_assignment", description="提交终态挂接判定。"),
    ]

def validate_assignment(a: Mapping[str, Any], verified_tree_ids: set[str] | None = None) -> dict[str, Any]:
    """校验终态输出；已验证树只允许追加叶子。"""
    required = ("tree_id", "confidence", "evidence")
    if any(k not in a for k in required): raise ValueError("assignment 缺少必填字段")
    confidence = float(a["confidence"])
    if not 0 <= confidence <= 1: raise ValueError("confidence 必须在 0 到 1 之间")
    tree_id = str(a["tree_id"])
    verified_tree_ids = verified_tree_ids or set()
    if tree_id in verified_tree_ids and a.get("action", "append") != "append":
        raise ValueError("已验证树只允许追加叶子")
    return {**dict(a), "tree_id": tree_id, "confidence": confidence, "action": a.get("action", "append")}
