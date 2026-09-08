"""森林、树详情与时间线查询接口（M6）。

在保留事件级树（供确认工作台）之上，新增**任务级**视图：森林列表带任务进度/完成情况，
树详情带笔记级父子树 `note_tree`（把事件级节点折叠为以笔记为节点的树）。
"""
from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.task_manager import TaskManager
from src.core.note_tree import build_note_tree, task_name
from src.models.orm import Event, Extraction, Note, Tree, TreeNode

router = APIRouter(tags=["forest"])


def get_task_manager(request: Request) -> TaskManager:
    return request.app.state.tasks


def _json(value: str | None) -> list | dict:
    if not value:
        return []
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return [value]


def _note_link(path: str | None) -> str | None:
    if not path:
        return None
    return "obsidian://open?path=" + quote(path, safe="/")


def _node_out(node: TreeNode, event: Event | None, note: Note | None) -> dict:
    path = note.path if note else None
    return {
        "id": node.id,
        "tree_id": node.tree_id,
        "event_id": node.event_id,
        "note_id": node.note_id,
        "parent_id": node.parent_id,
        "order": node.order,
        "confidence": node.confidence,
        "evidence": _json(node.evidence),
        "origin": node.origin,
        "event": {
            "content": event.content,
            "time_clue": event.time_clue,
            "status_clue": event.status_clue,
            "order_in_note": event.order_in_note,
        } if event else None,
        "note": {
            "path": path,
            "filename": note.filename if note else None,
            "obsidian_uri": _note_link(path),
        },
    }


def _latest_extractions(session: Session, note_ids: list[str]) -> dict[str, dict]:
    """按 note 取最新提炼结果（标题/摘要），用于任务名与笔记摘要。"""
    if not note_ids:
        return {}
    rows: dict[str, Extraction] = {}
    for ex in session.scalars(
        select(Extraction).where(Extraction.note_id.in_(note_ids)).order_by(Extraction.id.desc())
    ):
        rows.setdefault(ex.note_id, ex)
    return {nid: {"title": ex.title, "summary": ex.summary} for nid, ex in rows.items()}


def _note_tree_data(session: Session, tree: Tree) -> tuple[dict, dict[str, dict]]:
    """加载一棵树的事件级数据并折叠成笔记级 note_tree；返回 (note_tree, extraction_map)。"""
    nodes = list(
        session.scalars(select(TreeNode).where(TreeNode.tree_id == tree.id).order_by(TreeNode.order))
    )
    ev_ids = [n.event_id for n in nodes if n.event_id is not None]
    note_ids = [n.note_id for n in nodes if n.note_id]
    events = {}
    if ev_ids:
        for e in session.scalars(select(Event).where(Event.id.in_(ev_ids))):
            events[e.id] = {
                "content": e.content,
                "time_clue": e.time_clue,
                "status_clue": e.status_clue,
                "order_in_note": e.order_in_note,
            }
    notes = {}
    if note_ids:
        for n in session.scalars(select(Note).where(Note.id.in_(note_ids))):
            notes[n.id] = {"filename": n.filename, "path": n.path}
    ex = _latest_extractions(session, note_ids)
    node_dicts = [
        {
            "id": n.id, "event_id": n.event_id, "note_id": n.note_id, "parent_id": n.parent_id,
            "order": n.order, "confidence": n.confidence, "origin": n.origin,
        }
        for n in nodes
    ]
    return build_note_tree(node_dicts, events, notes, extraction_map=ex), ex


def _tree_out(session: Session, tree: Tree, *, include_nodes: bool, note_tree: dict | None = None, extraction_map: dict | None = None) -> dict:
    nodes = list(session.scalars(select(TreeNode).where(TreeNode.tree_id == tree.id).order_by(TreeNode.order)))
    events = {e.id: e for e in session.scalars(select(Event).where(Event.id.in_([n.event_id for n in nodes if n.event_id is not None])))}
    notes = {n.id: n for n in session.scalars(select(Note).where(Note.id.in_([n.note_id for n in nodes])))}
    if note_tree is None:
        note_tree = {"roots": [], "progress": 0.0, "done_count": 0, "total_count": 0, "root_note_id": None}
    if extraction_map is None:
        extraction_map = {}
    result = {
        "id": tree.id,
        "title": task_name({"title": tree.title}, note_tree, extraction_map),
        "root_note_id": tree.root_note_id,
        "status": tree.status,
        "confidence": tree.confidence,
        "verified": tree.verified,
        "locked": tree.locked,
        "evidence": _json(tree.evidence),
        "narrative": tree.narrative,
        "run_id": tree.run_id,
        "node_count": len(nodes),
        # 任务级视图字段
        "progress": note_tree["progress"],
        "done_count": note_tree["done_count"],
        "total_count": note_tree["total_count"],
        "note_count": note_tree["total_count"],
    }
    if include_nodes:
        result["nodes"] = [_node_out(n, events.get(n.event_id), notes.get(n.note_id)) for n in nodes]
        result["note_tree"] = note_tree["roots"]
    return result


@router.get("/api/forest")
def forest(
    status: str | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    tm: TaskManager = Depends(get_task_manager),
):
    with Session(tm.pipeline().engine) as session:
        statement = select(Tree).order_by(Tree.confidence.asc(), Tree.id)
        if status:
            statement = statement.where(Tree.status == status)
        if min_confidence is not None:
            statement = statement.where(Tree.confidence >= min_confidence)
        trees = list(session.scalars(statement))
        out = []
        for tree in trees:
            note_tree, ex = _note_tree_data(session, tree)
            out.append(_tree_out(session, tree, include_nodes=False, note_tree=note_tree, extraction_map=ex))
        return {"trees": out, "count": len(out)}


@router.get("/api/trees/{tree_id}")
def tree_detail(tree_id: str, tm: TaskManager = Depends(get_task_manager)):
    with Session(tm.pipeline().engine) as session:
        tree = session.get(Tree, tree_id)
        if tree is None:
            raise HTTPException(status_code=404, detail={"code": "tree_not_found", "message": f"树不存在: {tree_id}", "detail": None})
        note_tree, ex = _note_tree_data(session, tree)
        return _tree_out(session, tree, include_nodes=True, note_tree=note_tree, extraction_map=ex)


@router.get("/api/trees/{tree_id}/timeline")
def tree_timeline(tree_id: str, tm: TaskManager = Depends(get_task_manager)):
    with Session(tm.pipeline().engine) as session:
        tree = session.get(Tree, tree_id)
        if tree is None:
            raise HTTPException(status_code=404, detail={"code": "tree_not_found", "message": f"树不存在: {tree_id}", "detail": None})
        nodes = _tree_out(session, tree, include_nodes=True)["nodes"]
        return {"tree_id": tree_id, "items": nodes, "count": len(nodes)}
