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
    # 以 tm.cancel 的实际结果为准：无已注册协作取消信号（非本管理器启动）视为取消失败
    if not tm.cancel(run_id):
        raise HTTPException(status_code=409, detail={"code": "cancel_failed", "message": "该任务无法协作式取消（无已注册取消信号）", "detail": None})
    return {"accepted": True, "run_id": run_id}


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.get("/{run_id}/stream")
async def stream(run_id: str, request: Request, tm: TaskManager = Depends(get_task_manager)):
    """SSE 进度：阶段状态/条目计数变化即推送，空闲期发心跳保活，Run 结束推 done 并关闭。"""
    if tm.get(run_id) is None:
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": f"Run 不存在: {run_id}", "detail": None})

    async def gen():
        last: dict[str, tuple] = {}
        try:
            while True:
                # 客户端断开：尽早退出，避免后台生成器空转
                if await request.is_disconnected():
                    break
                run = tm.get(run_id)
                if run is None:
                    yield f"data: {json.dumps({'event': 'done', 'status': 'unknown', 'run_id': run_id}, ensure_ascii=False)}\n\n"
                    return
                emitted = False
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
                        emitted = True
                if run.status != "running":
                    yield f"data: {json.dumps({'event': 'done', 'status': run.status, 'run_id': run_id}, ensure_ascii=False)}\n\n"
                    return
                if not emitted:
                    # 心跳注释行：保持连接活跃（代理/中间层超时防护）
                    yield ": ping\n\n"
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            # 客户端断开导致的生成器取消：正常收尾，不吞异常
            raise

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)
