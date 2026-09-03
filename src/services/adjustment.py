"""M6 人工修正服务：事务化应用树/节点调整并保留审计快照。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.orm import Adjustment, Tree, TreeNode

_ALLOWED = {"set_status", "retitle", "move", "reorg"}
_STATUSES = {"complete", "in_progress", "dangling_confirmed", "dangling_suspected"}


class AdjustmentError(ValueError):
    pass


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _tree_snapshot(tree: Tree) -> dict[str, Any]:
    return {"id": tree.id, "title": tree.title, "status": tree.status, "verified": tree.verified}


def _node_snapshot(node: TreeNode) -> dict[str, Any]:
    return {"id": node.id, "tree_id": node.tree_id, "parent_id": node.parent_id, "order": node.order, "origin": node.origin}


def apply_adjustment(session: Session, tree_id: str, action: str, payload: dict[str, Any], *, run_id: str | None = None) -> Adjustment:
    if action not in _ALLOWED:
        raise AdjustmentError(f"不支持的修正动作: {action}")
    tree = session.get(Tree, tree_id)
    if tree is None:
        raise LookupError(f"树不存在: {tree_id}")
    if action == "move":
        node = session.get(TreeNode, int(payload.get("node_id", 0)))
        if node is None or node.tree_id != tree_id:
            raise AdjustmentError("节点不存在或不属于目标树")
        parent_id = payload.get("parent_id")
        parent = None if parent_id is None else session.get(TreeNode, int(parent_id))
        if parent is not None and parent.tree_id != tree_id:
            raise AdjustmentError("父节点必须属于目标树")
        if parent is not None and parent.id == node.id:
            raise AdjustmentError("节点不能以自身为父节点")
        ancestor = parent
        while ancestor is not None:
            if ancestor.parent_id == node.id:
                raise AdjustmentError("移动会形成父子循环")
            ancestor = session.get(TreeNode, ancestor.parent_id) if ancestor.parent_id else None
        before = _node_snapshot(node)
        node.parent_id = parent_id
        if "order" in payload:
            node.order = int(payload["order"])
        node.origin = "human"
        after = _node_snapshot(node)
        target_type, target_id = "tree_node", str(node.id)
    else:
        before = _tree_snapshot(tree)
        if action == "set_status":
            status = str(payload.get("status", ""))
            if status not in _STATUSES:
                raise AdjustmentError(f"非法树状态: {status}")
            tree.status = status
        elif action == "retitle":
            title = str(payload.get("title", "")).strip()
            if not title:
                raise AdjustmentError("树标题不能为空")
            tree.title = title
        after = _tree_snapshot(tree)
        target_type, target_id = "tree", tree.id
    adjustment = Adjustment(target_type=target_type, target_id=target_id, action=action,
                             payload_json=_dump(payload), before_json=_dump(before), after_json=_dump(after),
                             status="applied", applied_run_id=run_id)
    session.add(adjustment)
    session.flush()
    return adjustment


def adjustment_out(item: Adjustment) -> dict[str, Any]:
    return {"id": item.id, "target_type": item.target_type, "target_id": item.target_id,
            "action": item.action, "payload": json.loads(item.payload_json or "{}"),
            "before": json.loads(item.before_json or "{}"), "after": json.loads(item.after_json or "{}"),
            "status": item.status, "created_at": item.created_at, "applied_run_id": item.applied_run_id}
