"""Run/阶段状态机（DESIGN.md 3.1）：SQLite 持久化、互斥、断点恢复的查询基础。

- 每次整理任务 = 一个 Run（UUID），九阶段固定枚举（confirm 为人工环节不占运行态）
- `runs` 表 running 状态行兼作互斥锁：运行中再次 start_run 抛 RunAlreadyActiveError
- 断点续跑：上层通过 get_last_finished_run / get_stages 读取上一 Run 状态与产物复用

并发与可靠性：
- 连接级 pragma：WAL 日志模式 + `busy_timeout`（默认 5s），写事务缺省 `BEGIN IMMEDIATE`
  （`setup_immediate_begin`），使「检查 running → 插入新 Run」的互斥判定原子化——
  并发触发时只有先夺得写锁的一个会成功，其余在重试窗口后读到锁行并抛 RunAlreadyActiveError。
- bump_items 用服务端原子自增（`UPDATE ... SET items_done = items_done + n`），
  避免读-改-写在事务外被并发覆盖（lost update）。
- 阶段迁移受 `ALLOWED_STAGE_TRANSITIONS` 白名单约束；Run 进入终态后拒绝再 bump/set_stage。
- 孤儿恢复：服务重启后调用 `recover_orphans()`，把遗留 running 的 Run 及其 running 阶段
  回收为 failed（释放互斥锁），满足 FR-8「服务重启可恢复」。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import create_engine, event, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from src.infra.logging import logger
from src.models.orm import (
    ALLOWED_STAGE_TRANSITIONS,
    RUN_STATUSES,
    STAGE_ORDER,
    STAGE_STATUSES,
    TERMINAL_RUN_STATUSES,
    Base,
    Run,
    Stage,
    now_iso,
)


class RunAlreadyActiveError(RuntimeError):
    """已有运行中的任务（互斥，FR-8 返回 409）。"""


class UnknownStageError(ValueError):
    pass


class RunFinishedError(RuntimeError):
    """Run 已进入终态，拒绝再变更阶段/计数（终态保护）。"""


class IllegalStageTransitionError(ValueError):
    """阶段迁移不符合白名单（如 done → running）。"""


class RunManager:
    def __init__(
        self,
        db_path: Path | str,
        *,
        busy_timeout_ms: int = 5000,
        recover_orphans_on_startup: bool = True,
    ) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
        )
        self._busy_timeout_ms = busy_timeout_ms
        self._setup_pragmas()
        self._setup_immediate_begin()
        Base.metadata.create_all(self.engine)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        if recover_orphans_on_startup:
            # 启动策略：服务（重）启动即回收遗留 running，避免僵尸锁阻塞新任务
            recovered = self.recover_orphans()
            if recovered:
                logger.info(f"启动恢复孤儿 Run: {recovered} 个")

    # ── SQLite 连接级配置 ──

    def _setup_pragmas(self) -> None:
        """每连接启用 WAL 日志、busy_timeout、外键。WAL 在库文件上持久化。"""

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_conn, _conn_record) -> None:  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            cur.fetchall()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.fetchall()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.fetchall()
            cur.close()

    def _setup_immediate_begin(self) -> None:
        """把每个事务的 BEGIN 提升为 BEGIN IMMEDIATE，使互斥检查+写入原子化。
        该写法为 SQLAlchemy/SQLite 写锁的通行做法；busy_timeout 负责等待竞争者。
        """

        @event.listens_for(self.engine, "begin")
        def _immediate_begin(conn) -> None:  # noqa: ANN001
            conn.exec_driver_sql("BEGIN IMMEDIATE")

    # ── Run 生命周期 ──

    def start_run(self, scope: str = "", trigger: str = "api") -> Run:
        """创建新 Run 及九阶段 pending 行；存在 running 的 Run 则拒绝（互斥）。

        在 BEGIN IMMEDIATE 事务内「查锁 → 插入」原子完成，并发触发只会有一个成功。
        """
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
        """结束 Run 并释放互斥锁。status ∈ done | failed | cancelled。

        终态保护 + 幂等：若 Run 已处于终态，重复 finish 为 no-op（返回当前 Run），
        兼容 pipeline 对 finish_cancelled / 异常路径的重复调用。
        """
        if status not in ("done", "failed", "cancelled"):
            raise ValueError(f"结束状态非法: {status}")
        with self._session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise LookupError(f"Run 不存在: {run_id}")
            if run.status in TERMINAL_RUN_STATUSES:
                return run  # 已终态，幂等 no-op
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

    def recover_orphans(self, *, mark_error: str = "进程中断，孤儿运行被回收") -> int:
        """孤儿恢复（FR-8 启动策略）：把遗留 running 的 Run 及其 running 阶段回收为 failed，
        释放互斥锁，返回回收的 Run 数。幂等：无遗留 running 时返回 0。"""
        with self._session_factory() as session:
            runs = list(session.scalars(select(Run).where(Run.status == "running")))
            for run in runs:
                session.execute(
                    update(Stage)
                    .where(Stage.run_id == run.id, Stage.status == "running")
                    .values(status="failed", error=mark_error, finished_at=now_iso())
                )
                run.status = "failed"
                run.finished_at = now_iso()
                logger.info(f"孤儿 Run 回收: {run.id}")
            session.commit()
            return len(runs)

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
            run = session.get(Run, run_id)
            if run is None:
                raise LookupError(f"Run 不存在: {run_id}")
            if run.status in TERMINAL_RUN_STATUSES:
                raise RunFinishedError(f"Run 已结束({run.status})，不能再变更阶段状态")
            row = session.get(Stage, (run_id, stage))
            if row is None:
                raise LookupError(f"阶段行不存在: run={run_id} stage={stage}")
            if status not in ALLOWED_STAGE_TRANSITIONS[row.status]:
                raise IllegalStageTransitionError(
                    f"非法阶段迁移: stage={stage} {row.status} → {status}"
                )
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
        """实时更新条目计数（FR-8 SSE 进度的数据来源）。

        使用服务端原子自增（`items_done = items_done + n`）在单个事务内完成，
        规避读-改-写在多线程进度上报下丢失更新。total 为覆盖式写入。
        """
        if stage not in STAGE_ORDER:
            raise UnknownStageError(f"未知阶段: {stage}")
        if done < 0 or failed < 0 or (total is not None and total < 0):
            raise ValueError("条目计数不能为负")
        with self._session_factory() as session:
            run = session.get(Run, run_id)
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                # 阶段行可能不存在（非法 run/stage），统一以 Run 的存在性为先
                if run is None:
                    raise LookupError(f"Run 不存在: {run_id}")
                raise RunFinishedError(f"Run 已结束({run.status})，不能再更新计数")
            values: dict = {}
            if done:
                values["items_done"] = Stage.items_done + done
            if failed:
                values["items_failed"] = Stage.items_failed + failed
            if total is not None:
                values["items_total"] = total
            if values:
                result = session.execute(
                    update(Stage)
                    .where(Stage.run_id == run_id, Stage.stage == stage)
                    .values(**values)
                )
                if result.rowcount == 0:
                    raise LookupError(f"阶段行不存在: run={run_id} stage={stage}")
            # 服务端自增后，强制刷新 ORM 以读取真实新值
            session.expire_all()
            row = session.get(Stage, (run_id, stage))
            if row is None:
                raise LookupError(f"阶段行不存在: run={run_id} stage={stage}")
            session.commit()
            return row