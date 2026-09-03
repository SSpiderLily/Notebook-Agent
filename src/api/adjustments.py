"""M6 人工修正与重组建议 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.schemas import AdjustmentRequest, ReorgRequest
from src.api.task_manager import TaskManager
from src.models.orm import Adjustment, Tree
from src.services.adjustment import AdjustmentError, adjustment_out, apply_adjustment

router = APIRouter(prefix="/api", tags=["adjustments"])


def get_task_manager(request: Request) -> TaskManager:
    return request.app.state.tasks


def _not_found(code: str, message: str):
    return HTTPException(status_code=404, detail={"code": code, "message": message, "detail": None})


@router.post("/trees/{tree_id}/adjust")
def adjust(tree_id: str, body: AdjustmentRequest, tm: TaskManager = Depends(get_task_manager)):
    try:
        with Session(tm.pipeline().engine) as session:
            item = apply_adjustment(session, tree_id, body.action, body.payload)
            session.commit()
            return {"adjustment": adjustment_out(item), "affected_tree_ids": [tree_id]}
    except LookupError as exc:
        raise _not_found("tree_not_found", str(exc)) from exc
    except AdjustmentError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_adjustment", "message": str(exc), "detail": None}) from exc


@router.post("/trees/{tree_id}/reorg")
def reorg(tree_id: str, body: ReorgRequest, tm: TaskManager = Depends(get_task_manager)):
    if not body.confirm:
        raise HTTPException(status_code=400, detail={"code": "confirmation_required", "message": "人工重组必须显式 confirm=true", "detail": None})
    try:
        with Session(tm.pipeline().engine) as session:
            item = apply_adjustment(session, tree_id, "reorg", body.payload)
            session.commit()
            return {"adjustment": adjustment_out(item), "affected_tree_ids": [tree_id]}
    except LookupError as exc:
        raise _not_found("tree_not_found", str(exc)) from exc
    except AdjustmentError as exc:
        # reorg 首版只记录显式人工请求，不直接改写树结构，避免未定义 merge/split 语义。
        raise HTTPException(status_code=422, detail={"code": "invalid_reorg", "message": str(exc), "detail": None}) from exc


@router.get("/adjustments")
def list_adjustments(tm: TaskManager = Depends(get_task_manager)):
    with Session(tm.pipeline().engine) as session:
        items = list(session.scalars(select(Adjustment).order_by(Adjustment.id.desc())))
        return {"adjustments": [adjustment_out(item) for item in items], "count": len(items)}


@router.get("/adjustments/{adjustment_id}")
def get_adjustment(adjustment_id: int, tm: TaskManager = Depends(get_task_manager)):
    with Session(tm.pipeline().engine) as session:
        item = session.get(Adjustment, adjustment_id)
        if item is None:
            raise _not_found("adjustment_not_found", f"修正不存在: {adjustment_id}")
        return adjustment_out(item)


@router.delete("/adjustments/{adjustment_id}")
def delete_adjustment(adjustment_id: int, tm: TaskManager = Depends(get_task_manager)):
    with Session(tm.pipeline().engine) as session:
        item = session.get(Adjustment, adjustment_id)
        if item is None:
            raise _not_found("adjustment_not_found", f"修正不存在: {adjustment_id}")
        # 首版只允许撤销未改变结构的 tree 修正；保留记录并标记 reverted。
        item.status = "reverted"
        session.commit()
        return {"id": item.id, "status": item.status}
