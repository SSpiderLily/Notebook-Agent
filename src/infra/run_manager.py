"""Run/阶段状态机（DESIGN.md 3.1）：SQLite 持久化、互斥、断点恢复的查询基础。

- 每次整理任务 = 一个 Run（UUID），九阶段固定枚举（confirm 为人工环节不占运行态）
- `runs` 表 running 状态行兼作互斥锁：运行中再次 start_run 抛 RunAlreadyActiveError
- 断点续跑：上层通过 get_last_finished_run / get_stages 读取上一 Run 状态与产物复用
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from src.infra.logging import logger
from src.models.orm import RUN_STATUSES, STAGE_ORDER, STAGE_STATUSES, Base, Run, Stage, now_iso


class RunAlreadyActiveError(RuntimeError):
    """已有运行中的任务（互斥，FR-8 返回 409）。"""


class UnknownStageError(ValueError):
    pass


class RunManager:
    def __init__(self, db_path: Path | str):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(self.engine)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    # ── Run 生命周期 ──

    def start_run(self, scope: str = "", trigger: str = "api") -> Run:
        """创建新 Run 及九阶段 pending 行；存在 running 的 Run 则拒绝（互斥）。"""
        with self._session_factory() as session:
            if self.get_active_run(session):
                raise RunAlreadyActiveError("已有运行中的整理任务")
            run = Run(id=str(uuid.uuid4()), status="running", scope=scope, trigger=trigger)
            session.add(run)
            for stage in STAGE_ORDER:
                session.add(Stage(run_id=run.id, stage=stage))
            session.commit()
            logger.info(f"Run 启动: {run.id} scope={scope} trigger={trigger}")
            return run

    def get_active_run(self, session: Session | None = None) -> Run | None:
        if session is None:
            with self._session_factory() as s:
                return self.get_active_run(s)
        return session.scalar(select(Run).where(Run.status == "running").limit(1))

    def get_run(self, run_id: str) -> Run | None:
        with self._session_factory() as session:
            return session.get(Run, run_id)

    def list_runs(self) -> list[Run]:
        with self._session_factory() as session:
            return list(session.scalars(select(Run).order_by(Run.started_at.desc())))

    def get_last_run(self, status: str | None = None) -> Run | None:
        """最近一次（可选按状态过滤）完成的 Run，供断点续跑读取。"""
        if status is not None and status not in RUN_STATUSES:
            raise ValueError(f"未知 Run 状态: {status}")
        with self._session_factory() as session:
            q = select(Run).order_by(Run.started_at.desc(), text("rowid DESC"))
            if status is not None:
                q = q.where(Run.status == status)
            return session.scalar(q.limit(1))

    def finish_run(self, run_id: str, status: str, cost_est: float | None = None) -> Run:
        """结束 Run 并释放互斥锁。status ∈ done | failed | cancelled。"""
        if status not in ("done", "failed", "cancelled"):
            raise ValueError(f"结束状态非法: {status}")
        with self._session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise LookupError(f"Run 不存在: {run_id}")
            # 同一时刻把仍处于 running 的阶段标记为 failed（中断遗留）
            session.execute(
                update(Stage)
                .where(Stage.run_id == run_id, Stage.status == "running")
                .values(status="failed", error="Run 提前结束", finished_at=now_iso())
            )
            run.status = status
            run.finished_at = now_iso()
            if cost_est is not None:
                run.cost_est = cost_est
            session.commit()
            logger.info(f"Run 结束: {run_id} status={status} cost_est={cost_est}")
            return run

    # ── Stage 状态机 ──

    def get_stages(self, run_id: str) -> list[Stage]:
        """按九阶段固定顺序返回该 Run 的全部阶段行。"""
        with self._session_factory() as session:
            stages = {
                s.stage: s
                for s in session.scalars(select(Stage).where(Stage.run_id == run_id))
            }
            return [stages[name] for name in STAGE_ORDER if name in stages]

    def set_stage(
        self,
        run_id: str,
        stage: str,
        status: str,
        error: str | None = None,
        checkpoint_path: str | None = None,
    ) -> Stage:
        if stage not in STAGE_ORDER:
            raise UnknownStageError(f"未知阶段: {stage}")
        if status not in STAGE_STATUSES:
            raise ValueError(f"未知阶段状态: {status}")
        with self._session_factory() as session:
            row = session.get(Stage, (run_id, stage))
            if row is None:
                raise LookupError(f"阶段行不存在: run={run_id} stage={stage}")
            row.status = status
            row.error = error
            if checkpoint_path is not None:
                row.checkpoint_path = checkpoint_path
            if status == "running":
                row.started_at = now_iso()
            elif status in ("done", "failed", "skipped"):
                row.finished_at = now_iso()
            session.commit()
            logger.info(f"阶段状态: run={run_id} stage={stage} → {status}")
            return row

    def bump_items(
        self, run_id: str, stage: str, done: int = 0, failed: int = 0, total: int | None = None
    ) -> Stage:
        """实时更新条目计数（FR-8 SSE 进度的数据来源）。"""
        if stage not in STAGE_ORDER:
            raise UnknownStageError(f"未知阶段: {stage}")
        with self._session_factory() as session:
            row = session.get(Stage, (run_id, stage))
            if row is None:
                raise LookupError(f"阶段行不存在: run={run_id} stage={stage}")
            row.items_done += done
            row.items_failed += failed
            if total is not None:
                row.items_total = total
            session.commit()
            return row
