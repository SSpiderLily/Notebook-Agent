"""任务域路由（DESIGN.md 五）：触发/试算/查询/取消任务，SSE 进度推送。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.api.schemas import PreviewOut, RunOut, RunRequest, StageOut
from src.api.task_manager import TaskManager
from src.infra.run_manager import RunAlreadyActiveError

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def get_task_manager(request: Request) -> TaskManager:
    return request.app.state.tasks


def _run_out(tm: TaskManager, run) -> RunOut:
    stages = [
        StageOut(
            stage=s.stage,
            status=s.status,
            items_total=s.items_total,
            items_done=s.items_done,
            items_failed=s.items_failed,
            error=s.error,
            checkpoint_path=s.checkpoint_path,
        )
        for s in tm.stages(run.id)
    ]
    return RunOut(
        id=run.id,
        status=run.status,
        scope=run.scope,
        trigger=run.trigger,
        started_at=run.started_at,
        finished_at=run.finished_at,
        cost_est=run.cost_est,
        stages=stages,
    )


@router.post("/preview", response_model=PreviewOut)
def preview(body: RunRequest | None = None, tm: TaskManager = Depends(get_task_manager)):
    """只扫描试算：篇数/字数/预估费用/预估耗时，不触发运行。"""
    scope = (body.scope if body else None) or None
    return tm.preview(scope)


@router.post("/run", response_model=RunOut)
def run(body: RunRequest | None = None, tm: TaskManager = Depends(get_task_manager)):
    """触发整理任务（异步后台执行）。运行中重复触发返回 409。"""
    scope = (body.scope if body else None) or None
    try:
        run = tm.start(scope=scope)
    except RunAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail={"code": "task_active", "message": str(exc), "detail": None})
    return _run_out(tm, run)


@router.get("/current", response_model=RunOut | None)
def current(tm: TaskManager = Depends(get_task_manager)):
    run = tm.current()
    return _run_out(tm, run) if run else None


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: str, tm: TaskManager = Depends(get_task_manager)):
    run = tm.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": f"Run 不存在: {run_id}", "detail": None})
    return _run_out(tm, run)


@router.post("/{run_id}/cancel")
def cancel(run_id: str, tm: TaskManager = Depends(get_task_manager)):
    """请求取消运行中任务（协作式，阶段边界生效）。"""
    run = tm.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": f"Run 不存在: {run_id}", "detail": None})
    if run.status != "running":
        raise HTTPException(status_code=409, detail={"code": "run_not_active", "message": f"Run 非运行中: {run.status}", "detail": None})
    tm.cancel(run_id)
    return {"accepted": True, "run_id": run_id}


@router.get("/{run_id}/stream")
async def stream(run_id: str, tm: TaskManager = Depends(get_task_manager)):
    """SSE 进度：阶段状态/条目计数变化即推送，Run 结束推 done 事件并关闭。"""
    if tm.get(run_id) is None:
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": f"Run 不存在: {run_id}", "detail": None})

    async def gen():
        last: dict[str, tuple] = {}
        while True:
            run = tm.get(run_id)
            if run is None:
                yield f"data: {json.dumps({'event': 'done', 'status': 'unknown', 'run_id': run_id}, ensure_ascii=False)}\n\n"
                return
            for s in tm.stages(run_id):
                state = (s.status, s.items_done, s.items_failed, s.items_total)
                if last.get(s.stage) != state:
                    last[s.stage] = state
                    payload = {
                        "stage": s.stage,
                        "status": s.status,
                        "items_done": s.items_done,
                        "items_total": s.items_total,
                        "items_failed": s.items_failed,
                        "cost_est": run.cost_est,
                        "run_id": run_id,
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if run.status != "running":
                yield f"data: {json.dumps({'event': 'done', 'status': run.status, 'run_id': run_id}, ensure_ascii=False)}\n\n"
                return
            await asyncio.sleep(0.2)

    return StreamingResponse(gen(), media_type="text/event-stream")
