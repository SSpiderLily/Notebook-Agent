from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from src.core.extraction import ExtractionError, extract_note
from src.data.collection import Collector
from src.infra.llm_gateway import LLMGateway
from src.infra.logging import add_run_log_file, bind_run, remove_sink, setup_logging
from src.infra.run_manager import RunManager
from src.infra.stage_io import StageIO
from src.models.orm import Event, Extraction


class Pipeline:
    """最小可观测核心链路：采集 → Replay 抽取。"""
    def __init__(self, vault_dir: Path | str, db_path: Path | str, runs_dir: Path | str, recordings_dir: Path | str, mode: str = "replay"):
        self.rm = RunManager(db_path)
        self.io = StageIO(runs_dir)
        self.collector = Collector(vault_dir)
        self.gateway = LLMGateway(recordings_dir, mode=mode)

    def run(self) -> str:
        setup_logging()
        run = self.rm.start_run(scope=str(self.collector.vault), trigger="pipeline")
        sink = add_run_log_file(run.id)
        try:
            with bind_run(run.id):
                self.rm.set_stage(run.id, "init", "running")
                self.rm.set_stage(run.id, "init", "done")
                self.rm.set_stage(run.id, "collect", "running")
                rows = self.collector.collect()
                collect_path = self.io.write(run.id, "collect", rows)
                self.rm.bump_items(run.id, "collect", total=len(rows), done=len(rows))
                self.rm.set_stage(run.id, "collect", "done", checkpoint_path=str(collect_path))
                self.rm.set_stage(run.id, "extract", "running")
                results, failures = [], []
                for note in rows:
                    if note["vault_status"] != "active":
                        continue
                    try:
                        draft = extract_note(self.gateway, note, run_id=run.id)
                        results.append({"note_id": note["note_id"], "draft": draft.model_dump()})
                        self.rm.bump_items(run.id, "extract", done=1)
                    except ExtractionError as exc:
                        failures.append({"note_id": note["note_id"], "error": str(exc)})
                        self.rm.bump_items(run.id, "extract", failed=1)
                extract_path = self.io.write(run.id, "extract", {"results": results, "failures": failures})
                self.rm.bump_items(run.id, "extract", total=len(results) + len(failures))
                self.rm.set_stage(run.id, "extract", "failed" if failures and not results else "done", checkpoint_path=str(extract_path), error=f"{len(failures)} 条失败" if failures else None)
                self.rm.finish_run(run.id, "failed" if failures and not results else "done")
                return run.id
        except Exception:
            self.rm.finish_run(run.id, "failed")
            raise
        finally:
            remove_sink(sink)
