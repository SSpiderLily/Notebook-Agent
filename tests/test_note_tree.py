"""笔记级树投影（src/core/note_tree.py）单元测试：折叠、父子推导、进度、任务名。"""
import pytest

from src.core.note_tree import build_note_tree, task_name


def _node(nid, event_id, note_id, parent_id=None, order=0, origin="agent"):
    return {
        "id": nid, "event_id": event_id, "note_id": note_id,
        "parent_id": parent_id, "order": order, "confidence": 0.9, "origin": origin,
    }


def _events(mapping):
    return {eid: {"content": c, "time_clue": None, "status_clue": s, "order_in_note": 0}
            for eid, (c, s) in mapping.items()}


def _notes(mapping):
    return {nid: {"filename": f, "path": f} for nid, f in mapping.items()}


def _ex(mapping):
    return {nid: {"title": t, "summary": s} for nid, (t, s) in mapping.items()}


def test_basic_note_parent_child():
    """两篇笔记：A 根事件 + B 子事件 → 折叠成 A(父) → B(子)；进度 1/2。"""
    nodes = [
        _node(1, 101, "nA", parent_id=None, order=0),
        _node(2, 102, "nB", parent_id=1, order=0),
    ]
    events = _events({101: ("写立项文档", "已完成"), 102: ("开发主逻辑", None)})
    note_map = _notes({"nA": "立项A.md", "nB": "开发B.md"})
    ex = _ex({"nA": ("立项A", "摘要A"), "nB": ("开发B", "摘要B")})

    nt = build_note_tree(nodes, events, note_map, extraction_map=ex)

    assert nt["total_count"] == 2
    assert nt["done_count"] == 1
    assert nt["progress"] == 0.5
    assert nt["root_note_id"] == "nA"
    assert len(nt["roots"]) == 1
    root = nt["roots"][0]
    assert root["note_id"] == "nA"
    assert root["title"] == "立项A"
    assert root["status"] == "done"
    assert root["event_count"] == 1
    assert len(root["children"]) == 1
    child = root["children"][0]
    assert child["note_id"] == "nB"
    assert child["status"] == "pending"
    assert child["children"] == []


def test_single_note_multiple_events_collapse():
    """同一篇笔记多个事件落在不同层级：折叠成单一笔记节点，事件并入明细。"""
    nodes = [
        _node(1, 101, "nA", parent_id=None, order=0),
        _node(2, 102, "nA", parent_id=1, order=1),  # nA 内部事件
        _node(3, 103, "nB", parent_id=2, order=0),
    ]
    events = _events({101: ("起子任务", "已完成"), 102: ("中间步骤", None), 103: ("收尾", "已完成")})
    note_map = _notes({"nA": "A.md", "nB": "B.md"})

    nt = build_note_tree(nodes, events, note_map)

    assert len(nt["roots"]) == 1
    root = nt["roots"][0]
    assert root["note_id"] == "nA"
    assert root["event_count"] == 2
    assert {e["event_id"] for e in root["events"]} == {101, 102}
    # A 一完成一未完成 → in_progress；B 已完成 → done
    assert root["status"] == "in_progress"
    assert len(root["children"]) == 1
    assert root["children"][0]["note_id"] == "nB"
    assert root["children"][0]["status"] == "done"


def test_two_roots():
    """无父子关系的一批笔记 → 全部为根，进度 day/n 汇总。"""
    nodes = [
        _node(1, 101, "nA", parent_id=None, order=0),
        _node(2, 102, "nB", parent_id=None, order=0),
    ]
    events = _events({101: ("甲", "已完成"), 102: ("乙", None)})
    note_map = _notes({"nA": "A.md", "nB": "B.md"})

    nt = build_note_tree(nodes, events, note_map)

    assert len(nt["roots"]) == 2
    assert {r["note_id"] for r in nt["roots"]} == {"nA", "nB"}
    assert nt["total_count"] == 2
    assert nt["progress"] == 0.5


def test_empty_tree():
    nt = build_note_tree([], {}, {})
    assert nt["roots"] == []
    assert nt["progress"] == 0.0
    assert nt["total_count"] == 0


def test_task_name_prefers_root_title():
    """任务名优先取根笔记标题，回退 tree.title。"""
    tree = {"title": "未命名树"}
    nt = {"root_note_id": "nA", "roots": [], "progress": 0.0, "done_count": 0, "total_count": 0}
    ex = _ex({"nA": ("做网站", "摘要")})
    assert task_name(tree, nt, ex) == "做网站"
    assert task_name({"title": "已有任务名"}, {"root_note_id": None, "roots": [], "progress": 0.0}, {}) == "已有任务名"