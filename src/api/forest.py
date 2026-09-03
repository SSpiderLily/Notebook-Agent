"""森林、树详情与时间线查询接口（M6）。"""
from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.task_manager import TaskManager
from src.models.orm import Event, Note, Tree, TreeNode

router = APIRouter(tags=["forest"])


def get_task_manager(request: Request) -> TaskManager:
    return request.app.state.tasks


def _json(value: str | None) -> list | dict:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed
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


def _tree_out(session: Session, tree: Tree, *, include_nodes: bool) -> dict:
    nodes = list(session.scalars(select(TreeNode).where(TreeNode.tree_id == tree.id).order_by(TreeNode.order)))
    events = {e.id: e for e in session.scalars(select(Event).where(Event.id.in_([n.event_id for n in nodes if n.event_id is not None])))}
    notes = {n.id: n for n in session.scalars(select(Note).where(Note.id.in_([n.note_id for n in nodes])))}
    result = {
        "id": tree.id,
        "title": tree.title,
        "root_note_id": tree.root_note_id,
        "status": tree.status,
        "confidence": tree.confidence,
        "verified": tree.verified,
        "locked": tree.locked,
        "evidence": _json(tree.evidence),
        "narrative": tree.narrative,
        "run_id": tree.run_id,
        "node_count": len(nodes),
    }
    if include_nodes:
        result["nodes"] = [_node_out(n, events.get(n.event_id), notes.get(n.note_id)) for n in nodes]
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
        return {"trees": [_tree_out(session, tree, include_nodes=False) for tree in trees], "count": len(trees)}


@router.get("/api/trees/{tree_id}")
def tree_detail(tree_id: str, tm: TaskManager = Depends(get_task_manager)):
    with Session(tm.pipeline().engine) as session:
        tree = session.get(Tree, tree_id)
        if tree is None:
            raise HTTPException(status_code=404, detail={"code": "tree_not_found", "message": f"树不存在: {tree_id}", "detail": None})
        return _tree_out(session, tree, include_nodes=True)


@router.get("/api/trees/{tree_id}/timeline")
def tree_timeline(tree_id: str, tm: TaskManager = Depends(get_task_manager)):
    with Session(tm.pipeline().engine) as session:
        tree = session.get(Tree, tree_id)
        if tree is None:
            raise HTTPException(status_code=404, detail={"code": "tree_not_found", "message": f"树不存在: {tree_id}", "detail": None})
        nodes = _tree_out(session, tree, include_nodes=True)["nodes"]
        return {"tree_id": tree_id, "items": nodes, "count": len(nodes)}
