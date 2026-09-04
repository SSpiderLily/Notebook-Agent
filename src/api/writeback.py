"""M7 双写回 API（DESIGN.md 五、FR-10）：预览 / 确认 / 备份恢复。

安全边界：修改原笔记仅限本通道；确认与恢复为破坏性端点，强制 confirm=true，
再加本地 Host/Origin 中间件防护（app.py）。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.schemas import BackupRestoreRequest, WritebackConfirmRequest, WritebackPreviewRequest
from src.api.task_manager import TaskManager
from src.infra.backup import BackupManager
from src.models.orm import WritebackItem, WritebackJob
from src.services.writeback import WritebackError, confirm, job_out, plan

router = APIRouter(prefix="/api/writeback", tags=["writeback"])


def get_task_manager(request: Request) -> TaskManager:
    return request.app.state.tasks


def _not_found(code: str, message: str):
    return HTTPException(status_code=404, detail={"code": code, "message": message, "detail": None})


def _paths(tm: TaskManager) -> tuple[Path, Path]:
    pipeline = tm.pipeline()
    vault = pipeline.collector.vault
    backup_dir = Path(tm.db_path).parent / "backups"
    return vault, backup_dir


@router.post("/preview")
def preview(body: WritebackPreviewRequest, tm: TaskManager = Depends(get_task_manager)):
    vault, backup_dir = _paths(tm)
    try:
        with Session(tm.pipeline().engine) as session:
            job = plan(session, vault, backup_dir, body.kind, note_ids=body.note_ids)
            session.refresh(job)
            items = list(session.scalars(select(WritebackItem).where(WritebackItem.job_id == job.id)))
            return job_out(job, items)
    except WritebackError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_writeback", "message": str(exc), "detail": None}) from exc


@router.get("/jobs/{job_id}")
def get_job(job_id: int, tm: TaskManager = Depends(get_task_manager)):
    with Session(tm.pipeline().engine) as session:
        job = session.get(WritebackJob, job_id)
        if job is None:
            raise _not_found("writeback_job_not_found", f"写回任务不存在: {job_id}")
        items = list(session.scalars(select(WritebackItem).where(WritebackItem.job_id == job_id)))
        return job_out(job, items)


@router.post("/jobs/{job_id}/confirm")
def confirm_job(job_id: int, body: WritebackConfirmRequest, tm: TaskManager = Depends(get_task_manager)):
    if not body.confirm:
        raise HTTPException(status_code=400, detail={"code": "confirmation_required", "message": "双写回确认必须显式 confirm=true", "detail": None})
    vault, backup_dir = _paths(tm)
    try:
        with Session(tm.pipeline().engine) as session:
            job = confirm(session, job_id, vault, backup_dir)
            session.refresh(job)
            items = list(session.scalars(select(WritebackItem).where(WritebackItem.job_id == job_id)))
            return job_out(job, items)
    except LookupError as exc:
        raise _not_found("writeback_job_not_found", str(exc)) from exc


@router.get("/backups")
def list_backups(tm: TaskManager = Depends(get_task_manager)):
    _, backup_dir = _paths(tm)
    manager = BackupManager(backup_dir)
    backups = []
    for manifest_path in manager.list_manifests():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        backups.append({"id": manifest_path.parent.name, "path": str(manifest_path.parent),
                        "files": data.get("files", [])})
    return {"backups": backups, "count": len(backups)}


@router.post("/backups/{backup_id}/restore")
def restore_backup(backup_id: str, body: BackupRestoreRequest, tm: TaskManager = Depends(get_task_manager)):
    if not body.confirm:
        raise HTTPException(status_code=400, detail={"code": "confirmation_required", "message": "备份恢复必须显式 confirm=true", "detail": None})
    _, backup_dir = _paths(tm)
    manager = BackupManager(backup_dir)
    target = (backup_dir / backup_id).resolve()
    if target.parent != backup_dir.resolve() or not target.is_dir():
        raise _not_found("backup_not_found", f"备份不存在: {backup_id}")
    try:
        restored = manager.restore(target)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "backup_verification_failed", "message": str(exc), "detail": None}) from exc
    return {"backup_id": backup_id, "restored": [str(p) for p in restored], "count": len(restored)}