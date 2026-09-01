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
from src.models.orm import Base, Event, Extraction, LLMCall
from sqlalchemy import create_engine


class Pipeline:
    """最小可观测核心链路：采集 → Replay 抽取 → SQLite 持久化。"""
    def __init__(self, vault_dir: Path | str, db_path: Path | str, runs_dir: Path | str, recordings_dir: Path | str, mode: str = "replay"):
        self.rm = RunManager(db_path)
        self.io = StageIO(runs_dir)
        self.collector = Collector(vault_dir)
        self.gateway = LLMGateway(recordings_dir, mode=mode)
        self.snapshot_path = Path(runs_dir).parent / "collection_snapshot.json"
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)

    def retry_failed(self, run_id: str) -> dict:
        """按 extract 产物中的失败清单重试，不重跑已成功条目。"""
        artifact = self.io.read(run_id, "extract")
        retried, failures = [], []
        current = {n["note_id"]: n for n in self.collector.collect()}
        with Session(self.engine) as session:
            for item in artifact.get("failures", []):
                note = current.get(item["note_id"])
                if not note or note["vault_status"] != "active":
                    failures.append(item)
                    continue
                try:
                    draft = extract_note(self.gateway, note, run_id=run_id)
                    extraction = Extraction(note_id=note["note_id"], run_id=run_id, title=draft.title, summary=draft.summary, raw_json=draft.model_dump_json())
                    session.add(extraction)
                    session.flush()
                    for event in draft.events:
                        session.add(Event(note_id=note["note_id"], extraction_id=extraction.id, content=event.content, time_clue=event.time_clue, status_clue=event.status_clue, order_in_note=event.order_in_note))
                    retried.append({"note_id": item["note_id"], "draft": draft.model_dump()})
                except ExtractionError as exc:
                    failures.append({"note_id": item["note_id"], "error": str(exc)})
            session.commit()
        updated = {"results": artifact.get("results", []) + retried, "failures": failures}
        self.io.write(run_id, "extract", updated)
        updated["retried"] = retried
        return updated

    def run(self) -> str:
        setup_logging()
        run = self.rm.start_run(scope=str(self.collector.vault), trigger="pipeline")
        sink = add_run_log_file(run.id)
        try:
            with bind_run(run.id):
                self.rm.set_stage(run.id, "init", "running"); self.rm.set_stage(run.id, "init", "done")
                self.rm.set_stage(run.id, "collect", "running")
                rows = self.collector.collect()
                previous = {}
                if self.snapshot_path.exists():
                    previous = json.loads(self.snapshot_path.read_text(encoding="utf-8")).get("notes", {})
                for row in rows:
                    row["changed"] = previous.get(row["relative_path"], {}).get("content_hash") != row["content_hash"]
                self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                self.snapshot_path.write_text(json.dumps({"notes": {r["relative_path"]: r for r in rows}}, ensure_ascii=False), encoding="utf-8")
                collect_path = self.io.write(run.id, "collect", rows)
                self.rm.bump_items(run.id, "collect", total=len(rows), done=len(rows))
                self.rm.set_stage(run.id, "collect", "done", checkpoint_path=str(collect_path))
                self.rm.set_stage(run.id, "extract", "running")
                results, failures = [], []
                with Session(self.engine) as session:
                    for note in rows:
                        if note["vault_status"] != "active": continue
                        if not note["changed"]:
                            self.rm.bump_items(run.id, "extract", done=1)
                            continue
                        try:
                            draft = extract_note(self.gateway, note, run_id=run.id)
                            if self.gateway.calls:
                                call = self.gateway.calls[-1]
                                session.add(LLMCall(run_id=run.id, stage="extract", caller="event_extractor", model=call["model"], prompt_tokens=call.get("prompt_tokens", 0), completion_tokens=call.get("completion_tokens", 0), cost_est=call.get("cost_est", 0.0), retries=call.get("retries", 0), status=call.get("status", "ok"), digest=call["digest"]))
                            extraction = Extraction(note_id=note["note_id"], run_id=run.id, title=draft.title, summary=draft.summary, raw_json=draft.model_dump_json())
                            session.add(extraction); session.flush()
                            for event in draft.events:
                                session.add(Event(note_id=note["note_id"], extraction_id=extraction.id, content=event.content, time_clue=event.time_clue, status_clue=event.status_clue, order_in_note=event.order_in_note))
                            session.commit()
                            results.append({"note_id": note["note_id"], "draft": draft.model_dump()}); self.rm.bump_items(run.id, "extract", done=1)
                        except ExtractionError as exc:
                            failures.append({"note_id": note["note_id"], "error": str(exc)}); self.rm.bump_items(run.id, "extract", failed=1)
                    session.commit()
                extract_path = self.io.write(run.id, "extract", {"results": results, "failures": failures})
                self.rm.bump_items(run.id, "extract", total=len(results) + len(failures))
                final = "failed" if failures and not results else "done"
                self.rm.set_stage(run.id, "extract", final, checkpoint_path=str(extract_path), error=f"{len(failures)} 条失败" if failures else None)
                self.rm.finish_run(run.id, final)
                return run.id
        except Exception:
            self.rm.finish_run(run.id, "failed"); raise
        finally:
            remove_sink(sink)
