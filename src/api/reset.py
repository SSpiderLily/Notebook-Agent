"""M9 安全重置接口：只清理 NoteAgent 生成物，不触碰原始笔记。"""
from __future__ import annotations
import shutil
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from src.api.task_manager import TaskManager

router = APIRouter(prefix="/api/reset", tags=["reset"])
class ResetRequest(BaseModel):
    confirm: bool = False
    scope: str = "generated"

def get_tm(request: Request) -> TaskManager: return request.app.state.tasks

@router.post("")
def reset(body: ResetRequest, tm: TaskManager = Depends(get_tm)):
    if not body.confirm:
        raise HTTPException(400, detail={"code":"confirmation_required","message":"需要 confirm=true","detail":None})
    if body.scope not in {"generated", "artifacts", "index"}:
        raise HTTPException(422, detail={"code":"invalid_scope","message":"不支持的重置范围","detail":body.scope})
    if tm.current() is not None:
        raise HTTPException(409, detail={"code":"task_active","message":"运行中不可重置","detail":None})
    p = tm.pipeline(); removed=[]
    if body.scope in {"generated", "artifacts"}:
        root = p.collector.vault / "_noteagent"
        if root.exists(): shutil.rmtree(root); removed.append(str(root))
    if body.scope in {"generated", "index"}:
        for root in (p.chroma_path, p.runs_dir):
            if Path(root).exists(): shutil.rmtree(root); removed.append(str(root))
    return {"accepted": True, "scope": body.scope, "removed": removed}
