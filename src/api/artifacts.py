"""M9 产物版本查询与安全回退接口。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from src.api.task_manager import TaskManager
from src.services.artifact import ArtifactService

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

def get_tm(request: Request) -> TaskManager:
    return request.app.state.tasks

def service(tm: TaskManager) -> ArtifactService:
    p = tm.pipeline()
    return ArtifactService(p.collector.vault, p.db_path.parent / "backups")

@router.get("/versions")
def versions(path: str, tm: TaskManager = Depends(get_tm)):
    try:
        return {"path": path, "versions": service(tm).list_versions(path)}
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, detail={"code": "artifact_not_found", "message": str(exc), "detail": None}) from exc

@router.post("/rollback")
def rollback(path: str, version: int, confirm: bool = False, tm: TaskManager = Depends(get_tm)):
    if not confirm:
        raise HTTPException(400, detail={"code": "confirmation_required", "message": "回退必须显式 confirm=true", "detail": None})
    if tm.current() is not None:
        raise HTTPException(409, detail={"code": "task_active", "message": "运行中不可回退产物", "detail": None})
    try:
        return service(tm).rollback(path, version)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, detail={"code": "artifact_version_not_found", "message": str(exc), "detail": None}) from exc
