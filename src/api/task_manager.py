"""任务编排：把 Pipeline 包装为可被 API 触发的异步长任务（FR-8）。

- `start()` 在后台线程执行 Pipeline（传入预创建 run_id，互斥/scope 由 RunManager 保证）
- 每个运行一个 `threading.Event` 作为协作式取消信号，经 `pipeline.run(should_cancel=...)` 消费
- `preview()` 复用 Collector.estimate 做只读试算，不触发任何运行
"""
from __future__ import annotations

import threading
from pathlib import Path

from src.data.collection import Collector
from src.services.pipeline import Pipeline
from src.infra.run_manager import Run


class TaskManager:
    def __init__(
        self,
        vault_dir: Path | str,
        db_path: Path | str,
        runs_dir: Path | str,
        recordings_dir: Path | str,
        *,
        mode: str = "replay",
        transport=None,
    ) -> None:
        self.vault_dir = Path(vault_dir)
        self.db_path = Path(db_path)
        self.runs_dir = Path(runs_dir)
        self.recordings_dir = Path(recordings_dir)
        self.mode = mode
        self.transport = transport
        self._lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._pipeline: Pipeline | None = None

    # ── 惰性单例 Pipeline（与 API 同生命周期，复用引擎/RunManager）──
    def pipeline(self) -> Pipeline:
        if self._pipeline is None:
            self._pipeline = Pipeline(
                self.vault_dir,
                self.db_path,
                self.runs_dir,
                self.recordings_dir,
                mode=self.mode,
                transport=self.transport,
            )
        return self._pipeline

    # ── 只读查询 ──
    def get(self, run_id: str) -> Run | None:
        return self.pipeline().rm.get_run(run_id)

    def current(self) -> Run | None:
        return self.pipeline().rm.get_active_run()

    def list(self) -> list[Run]:
        return self.pipeline().rm.list_runs()

    def stages(self, run_id: str):
        return self.pipeline().rm.get_stages(run_id)

    # ── 试算（只扫描，不运行）──
    def preview(self, scope: str | None = None) -> dict:
        pipeline = self.pipeline()
        rows = pipeline.collector.collect()
        if scope:
            rows = [r for r in rows if r.get("relative_path", "").startswith(scope)]
        est = Collector.estimate(rows)
        return {
            "scope": scope,
            "notes": est.notes,
            "characters": est.characters,
            "calls": est.calls,
            "estimated_cost_cny": est.estimated_cost_cny,
            "estimated_minutes": est.estimated_minutes,
        }

    # ── 启动后台任务（互斥：已有 running 则抛 RunAlreadyActiveError）──
    def start(self, scope: str | None = None) -> Run:
        pipeline = self.pipeline()
        run = pipeline.rm.start_run(
            scope=scope or str(pipeline.collector.vault), trigger="api"
        )
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[run.id] = cancel_event

        def _worker() -> None:
            try:
                pipeline.run(
                    run_id=run.id,
                    trigger="api",
                    scope=scope,
                    should_cancel=cancel_event.is_set,
                )
            finally:
                with self._lock:
                    self._cancel_events.pop(run.id, None)

        threading.Thread(
            target=_worker, name=f"run-{run.id[:8]}", daemon=True
        ).start()
        return run

    # ── 协作式取消：置位信号，pipeline 在阶段边界检查并收尾为 cancelled ──
    def cancel(self, run_id: str) -> bool:
        with self._lock:
            evt = self._cancel_events.get(run_id)
        if evt is None:
            return False
        evt.set()
        return True
