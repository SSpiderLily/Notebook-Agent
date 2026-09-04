"""API 契约 DTO（DESIGN.md 五、API 契约 v1）。

- 全部 JSON；错误统一 `{code, message, detail}`
- SSE 事件 `{stage, status, items_done, items_total, cost_est}`
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class StageOut(BaseModel):
    stage: str
    status: str
    items_total: int = 0
    items_done: int = 0
    items_failed: int = 0
    error: str | None = None
    checkpoint_path: str | None = None


class RunOut(BaseModel):
    id: str
    status: str
    scope: str = ""
    trigger: str = "api"
    started_at: str = ""
    finished_at: str | None = None
    cost_est: float | None = None
    stages: list[StageOut] = Field(default_factory=list)


class ErrorOut(BaseModel):
    code: str
    message: str
    detail: str | None = None


class RunRequest(BaseModel):
    """POST /api/tasks/run 请求体（`scope` 可选子目录试跑）。"""

    scope: str | None = None


class PreviewOut(BaseModel):
    """POST /api/tasks/preview 试算结果（篇数/字数/预估费用/预估耗时）。"""

    scope: str | None = None
    notes: int = 0
    characters: int = 0
    calls: int = 0
    estimated_cost_cny: float = 0.0
    estimated_minutes: float = 0.0


class AdjustmentRequest(BaseModel):
    action: str
    payload: dict = Field(default_factory=dict)


class ReorgRequest(BaseModel):
    confirm: bool = False
    payload: dict = Field(default_factory=dict)


class WritebackPreviewRequest(BaseModel):
    """POST /api/writeback/preview：kind = tags | links，note_ids 可选筛选。"""

    kind: str
    note_ids: list[str] | None = Field(default=None)


class WritebackConfirmRequest(BaseModel):
    """POST /api/writeback/jobs/{id}/confirm（破坏性，需 confirm=true）。"""

    confirm: bool = False


class BackupRestoreRequest(BaseModel):
    """POST /api/writeback/backups/{id}/restore（破坏性，需 confirm=true）。"""

    confirm: bool = False


class SSEEvent(BaseModel):
    """SSE 进度事件负载。"""

    stage: str
    status: str
    items_done: int = 0
    items_total: int = 0
    items_failed: int = 0
    cost_est: float | None = None
    run_id: str | None = None
