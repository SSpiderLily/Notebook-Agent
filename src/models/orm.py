"""SQLAlchemy 2.0 ORM 表定义（DESIGN.md 4.1）。

M0 阶段先落 runs/stages 两张核心表（运行状态机），
notes/extractions/trees 等业务表随对应里程碑加入。
时间字段统一存本地时区 ISO8601 字符串。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 九阶段固定枚举（confirm 为 Web 人工环节，不占运行态）
STAGE_ORDER: list[str] = [
    "init",
    "collect",
    "extract",
    "associate",
    "tree_rebuild",
    "status_judge",
    "artifact",
    "writeback",
]

RUN_STATUSES = ("running", "done", "failed", "cancelled")
STAGE_STATUSES = ("pending", "running", "done", "failed", "skipped")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class Run(Base):
    """一次整理任务（DESIGN.md 3.1）。running 状态行兼作互斥锁。"""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # RUN_STATUSES
    scope: Mapped[str] = mapped_column(String(512), default="")  # 整理范围（全仓库或子目录）
    trigger: Mapped[str] = mapped_column(String(32), default="api")
    started_at: Mapped[str] = mapped_column(String(32), default=now_iso)
    finished_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cost_est: Mapped[float | None] = mapped_column(nullable=True)  # 累计预估费用（元）


class Extraction(Base):
    __tablename__ = "extractions"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    note_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str] = mapped_column(String)
    raw_json: Mapped[str] = mapped_column(String)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    note_id: Mapped[str] = mapped_column(String(64), index=True)
    extraction_id: Mapped[int] = mapped_column(index=True)
    content: Mapped[str] = mapped_column(String)
    time_clue: Mapped[str | None] = mapped_column(String, nullable=True)
    status_clue: Mapped[str | None] = mapped_column(String, nullable=True)
    order_in_note: Mapped[int] = mapped_column(default=0)


class Stage(Base):
    """阶段状态机：pending → running → done | failed | skipped。"""

    __tablename__ = "stages"
    __table_args__ = (UniqueConstraint("run_id", "stage", name="uq_run_stage"),)

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    stage: Mapped[str] = mapped_column(String(32), primary_key=True)  # STAGE_ORDER 之一
    status: Mapped[str] = mapped_column(String(16), default="pending")
    items_total: Mapped[int] = mapped_column(default=0)
    items_done: Mapped[int] = mapped_column(default=0)
    items_failed: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(nullable=True)
    checkpoint_path: Mapped[str | None] = mapped_column(nullable=True)  # 断点/中间产物路径
    started_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
