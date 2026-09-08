"""笔记级树投影：把事件级树折叠成"以笔记为节点"的任务父子树。

当前引擎的树节点是事件（`TreeNode.event_id` 为主，`note_id` 仅作来源链接）。用户需要的
是"任务列表 + 点进任务看以笔记为节点的父子树"。本模块不改动底层事件引擎，只做**投影**：
把一棵树内属于同一篇笔记的若干事件节点折叠成一个笔记节点，并由事件级父子关系推导
笔记间的父子关系，同时给出任务级进度统计。折叠是纯函数，可离线测试。

对应 DESIGN.md §2.1 领域模型（笔记=树上的节点）、FR-4/5；不改动抽取/关联/树重建/状态判定。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

# 完成/未完成线索（与 src/core/status.py 的确定性兜底线索保持一致）
_DONE_MARKERS = ("done", "完成", "已完成", "finished", "closed", "收尾", "结案")


def _event_done(status_clue: str | None) -> bool:
    clue = (status_clue or "").lower()
    return any(m in clue for m in _DONE_MARKERS)


def _note_status(events: list[Mapping[str, Any]]) -> str:
    """由笔记内事件的状态线索汇总出笔记级状态：done / in_progress / pending。"""
    if not events:
        return "pending"
    done = sum(1 for e in events if _event_done(e.get("status_clue")))
    if done == len(events):
        return "done"
    if done == 0:
        return "pending"
    return "in_progress"


def _obsidian_uri(path: str | None) -> str | None:
    from urllib.parse import quote

    if not path:
        return None
    return "obsidian://open?path=" + quote(path, safe="/")


def build_note_tree(
    nodes: list[Mapping[str, Any]],
    event_map: Mapping[int, Mapping[str, Any]],
    note_map: Mapping[str, Mapping[str, Any]],
    *,
    extraction_map: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """把一棵树的事件级节点折叠成笔记级父子树。

    入参：
    - nodes：该树的 `TreeNode` 列表，每项含 ``id``（事件节点 id）、``event_id``、
      ``note_id``、``parent_id``（指向同表其它节点 id，None 为根）、``order``、``confidence``、``origin``；
    - event_map：``event_id -> {content, time_clue, status_clue, order_in_note}``；
    - note_map：``note_id -> {filename, path}``；
    - extraction_map：``note_id -> {title, summary}``（最新提炼结果，用于笔记标题/摘要）。

    返回：``{roots, progress, done_count, total_count, root_note_id}``，其中
    roots 为嵌套笔记树（每篇笔记一个节点，含 ``children``、``events`` 明细与聚合状态）。
    """
    extraction_map = extraction_map or {}
    present: dict[int, Mapping[str, Any]] = {n["id"]: n for n in nodes}
    children: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    roots: list[Mapping[str, Any]] = []
    for n in nodes:
        pid = n.get("parent_id")
        if pid is not None and pid in present:
            children[pid].append(n)
        else:
            roots.append(n)

    # 事件级深度（BFS from 根），用于"同一笔记多事件落在不同层级"时的最浅归并
    depth: dict[int, int] = {}
    stack = [(r, 0) for r in roots]
    while stack:
        node, d = stack.pop()
        if node["id"] in depth:
            continue
        depth[node["id"]] = d
        for c in children.get(node["id"], []):
            stack.append((c, d + 1))

    # 每个事件节点推导出的"父笔记"（父节点来源笔记，且与之不同；None=根或同笔记内部）
    node_parent_note: dict[int, str | None] = {}
    for n in nodes:
        pid = n.get("parent_id")
        if pid is not None and pid in present:
            pn = present[pid]
            node_parent_note[n["id"]] = pn["note_id"] if pn.get("note_id") and pn.get("note_id") != n.get("note_id") else None
        else:
            node_parent_note[n["id"]] = None

    # 按笔记归并事件；把 event_map 的事件字段（content/status_clue 等）并入每个节点，
    # 供状态汇总与摘要读取（节点字典本身不含事件内容）。
    note_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in nodes:
        eg = event_map.get(n["event_id"]) or {}
        resolved = {
            **n,
            "depth": depth.get(n["id"], 0),
            "content": eg.get("content", ""),
            "time_clue": eg.get("time_clue"),
            "status_clue": eg.get("status_clue"),
            "order_in_note": eg.get("order_in_note"),
        }
        note_events.setdefault(n["note_id"], []).append(resolved)

    # 每篇笔记取一个父笔记：用其最浅事件的父笔记（同笔记内部事件不会是最浅，见推导）
    note_parent: dict[str, str | None] = {}
    for note_id, evs in note_events.items():
        shallow = min(evs, key=lambda e: (e["depth"], e.get("order", 0)))
        note_parent[note_id] = node_parent_note.get(shallow["id"])

    note_children: dict[str, list[str]] = defaultdict(list)
    for note_id, parent in note_parent.items():
        if parent is not None:
            note_children[parent].append(note_id)
    note_roots = [nid for nid, p in note_parent.items() if p is None]

    def _sort_key(note_id: str) -> tuple[int, int]:
        evs = note_events[note_id]
        return min((e.get("order", 0), e["depth"]) for e in evs)

    def _build(note_id: str) -> dict[str, Any]:
        evs = sorted(note_events[note_id], key=lambda e: (e.get("order", 0), e["depth"]))
        note = note_map.get(note_id) or {}
        ex = extraction_map.get(note_id) or {}
        status = _note_status(evs)
        path = note.get("path") or ""
        done_events = [e for e in evs if _event_done(e.get("status_clue"))]
        confidence = max((float(e.get("confidence", 0.0)) for e in evs), default=0.0)
        origin = "human" if any(e.get("origin") == "human" for e in evs) else "agent"
        children = sorted(note_children.get(note_id, []), key=_sort_key)
        return {
            "note_id": note_id,
            "title": ex.get("title") or note.get("filename") or note_id,
            "filename": note.get("filename"),
            "path": path,
            "obsidian_uri": _obsidian_uri(path),
            "summary": ex.get("summary") or (evs[0]["content"] if evs else ""),
            "status": status,
            "is_done": status == "done",
            "event_count": len(evs),
            "done_event_count": len(done_events),
            "confidence": round(confidence, 3),
            "origin": origin,
            "events": [
                {
                    "event_id": e["event_id"],
                    "content": (event_map.get(e["event_id"]) or {}).get("content", ""),
                    "time_clue": (event_map.get(e["event_id"]) or {}).get("time_clue"),
                    "status_clue": (event_map.get(e["event_id"]) or {}).get("status_clue"),
                    "order_in_note": (event_map.get(e["event_id"]) or {}).get("order_in_note"),
                }
                for e in evs
                if e["event_id"] is not None
            ],
            "children": [_build(c) for c in children],
        }

    roots_out = [_build(nid) for nid in sorted(note_roots, key=_sort_key)]
    total = len(note_parent)
    done = sum(1 for nid in note_parent if _note_status(note_events[nid]) == "done")
    return {
        "roots": roots_out,
        "progress": round((done / total), 3) if total else 0.0,
        "done_count": done,
        "total_count": total,
        "root_note_id": note_roots[0] if note_roots else None,
    }


def task_name(tree: Mapping[str, Any], note_tree: dict[str, Any], extraction_map: Mapping[str, Mapping[str, Any]]) -> str:
    """任务名称：优先取根笔记标题（避免大量"未命名树"），回退到 tree.title。"""
    title = (tree.get("title") or "").strip()
    root_note_id = note_tree.get("root_note_id")
    if root_note_id:
        root_title = (extraction_map.get(root_note_id) or {}).get("title") or ""
        if root_title:
            return root_title
    return title or "未命名任务"
