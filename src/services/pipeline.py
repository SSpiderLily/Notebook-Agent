from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import create_engine, delete, or_, select
from sqlalchemy.orm import Session

from src.core.association import generate_candidates, judge_candidates
from src.core.extraction import ExtractionError, extract_note
from src.data.collection import Collector
from src.data.vector_store import ChromaVectorStore, local_hash_embedding
from src.infra.llm_gateway import LLMCostCapExceeded, LLMGateway
from src.infra.logging import add_run_log_file, bind_run, remove_sink, setup_logging
from src.infra.run_manager import RunFinishedError, RunManager
from src.infra.stage_io import StageIO
from src.models.orm import (
    TERMINAL_RUN_STATUSES,
    Base,
    Association,
    Event,
    Extraction,
    LLMCall,
    Note,
)


class Pipeline:
    """最小可观测核心链路：采集 → Replay 抽取 → SQLite 持久化 → 关联（Chroma+LLM 判定）。"""
    def __init__(self, vault_dir: Path | str, db_path: Path | str, runs_dir: Path | str, recordings_dir: Path | str, mode: str = "replay", *, chroma_path: Path | str | None = None, embedding_function: Callable[[list[str]], list[list[float]]] | None = None, embedding_model: str = "local-hash-v1", transport: Callable[[str], str] | None = None):
        self.rm = RunManager(db_path)
        self.io = StageIO(runs_dir)
        self.collector = Collector(vault_dir)
        self.gateway = LLMGateway(recordings_dir, mode=mode, transport=transport)
        self.snapshot_path = Path(runs_dir).parent / "collection_snapshot.json"
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        self.chroma_path = Path(chroma_path) if chroma_path is not None else Path(db_path).parent / "chroma"
        self.embedding_function = embedding_function if embedding_function is not None else local_hash_embedding
        self.embedding_model = embedding_model
        Base.metadata.create_all(self.engine)

    def retry_failed(self, run_id: str) -> dict:
        """按 extract 产物中的失败清单重试，不重跑已成功条目。

        幂等：同一 Run 下已成功抽取的 note（extractions 表已有该 run 的行）直接跳过，
        即使失败清单未及时清理也不产生重复行；DB 落库与产物更新之间若中断，重试一次即可收敛。
        台账/状态尽量兼容：每次成功重试把 LLM 调用写入 llm_calls 台账；若该 Run 在 DB 中存在，
        尽力把 extract 阶段推进到 done 并以 done 收尾（缺失/终态时静默跳过，不阻塞调用方）。
        """
        artifact = self.io.read(run_id, "extract")
        retried, failures = [], []
        current = {n["note_id"]: n for n in self.collector.collect()}
        with Session(self.engine) as session:
            existing = set(
                session.scalars(
                    select(Extraction.note_id).where(Extraction.run_id == run_id)
                )
            )
            for item in artifact.get("failures", []):
                note = current.get(item["note_id"])
                if (
                    not note
                    or note["vault_status"] != "active"
                    or item["note_id"] in existing
                ):
                    failures.append(item)
                    continue
                try:
                    draft = extract_note(self.gateway, note, run_id=run_id)
                    if self.gateway.calls:
                        call = self.gateway.calls[-1]
                        session.add(LLMCall(run_id=run_id, stage="extract", caller="event_extractor", model=call["model"], prompt_tokens=call.get("prompt_tokens", 0), completion_tokens=call.get("completion_tokens", 0), cost_est=call.get("cost_est", 0.0), retries=call.get("retries", 0), status=call.get("status", "ok"), digest=call["digest"]))
                    extraction = Extraction(note_id=note["note_id"], run_id=run_id, title=draft.title, summary=draft.summary, keywords=json.dumps(draft.keywords, ensure_ascii=False), candidate_tags=json.dumps(draft.candidate_tags, ensure_ascii=False), model=getattr(self.gateway, "model", ""), raw_json=draft.model_dump_json())
                    session.add(extraction)
                    session.flush()
                    for event in draft.events:
                        session.add(Event(note_id=note["note_id"], extraction_id=extraction.id, content=event.content, time_clue=event.time_clue, status_clue=event.status_clue, order_in_note=event.order_in_note))
                    retried.append({"note_id": item["note_id"], "draft": draft.model_dump()})
                    existing.add(item["note_id"])
                except ExtractionError as exc:
                    failures.append({"note_id": item["note_id"], "error": str(exc)})
            session.commit()
        updated = {"results": artifact.get("results", []) + retried, "failures": failures}
        self.io.write(run_id, "extract", updated)
        # 台账/状态尽量兼容：仅在 Run 存在且未终态时尽力推进，否则静默跳过
        run = self.rm.get_run(run_id)
        if run is not None and run.status not in TERMINAL_RUN_STATUSES:
            try:
                self.rm.set_stage(run_id, "extract", "running")
                if retried:
                    self.rm.bump_items(run_id, "extract", done=len(retried))
                if not failures:
                    self.rm.set_stage(run_id, "extract", "done")
                    self.rm.finish_run(run_id, "done", cost_est=self.gateway.cost)
            except Exception:
                pass  # 状态收尾尽力而为，不阻塞重试结果返回
        updated["retried"] = retried
        return updated

    @staticmethod
    def _normalize_scope(scope: str | None) -> str | None:
        """把 scope 归一化为仓库内相对 posix 路径；拒绝绝对路径与目录穿越（..）。

        归一化后用于采集过滤 / 快照 tombstone / 关联过滤的统一键，保证 scope 边界安全。
        """
        if not scope:
            return None
        p = Path(scope)
        if p.is_absolute():
            raise ValueError(f"scope 必须为仓库内相对路径: {scope}")
        norm = p.as_posix().strip("/")
        parts = [x for x in norm.split("/") if x and x not in (".",)]
        if any(part == ".." for part in parts):
            raise ValueError(f"scope 不允许目录穿越: {scope}")
        return "/".join(parts)

    @staticmethod
    def _note_has_extraction(session: Session, note_id: str) -> bool:
        """该 note 是否已有成功抽取记录（作为增量跳过与失败重试的判据）。"""
        return (
            session.scalar(
                select(Extraction.id).where(Extraction.note_id == note_id).limit(1)
            )
            is not None
        )

    def run(self, run_id: str | None = None, *, trigger: str = "pipeline", scope: str | None = None, should_cancel: Callable[[], bool] | None = None) -> str:
        """执行整理流水线。

        未传 run_id 时自行 start_run（触发主体=trigger，scope 默认全仓库）；传入预创建 run_id
        时直接使用该 Run（互斥/scope 已由上层 RunManager 保证，供 API/TaskManager 使用，避免
        二次 start_run 触发互斥）。每个阶段切换前检查 should_cancel()，收到取消信号则协作式
        收尾为 cancelled（M0 为阶段边界粒度，随后续里程碑细化到条目级）。
        """
        setup_logging()
        scope = self._normalize_scope(scope)
        if run_id is None:
            run = self.rm.start_run(scope=scope or str(self.collector.vault), trigger=trigger)
            run_id = run.id
        else:
            # 已结束的 Run 拒绝继续执行：调用方传入的预创建 run_id 若已进入终态，
            # 说明该 Run 已被收尾/回收，不能在其上推进阶段或写入产物。
            run = self.rm.get_run(run_id)
            if run is None:
                raise LookupError(f"Run 不存在: {run_id}")
            if run.status in TERMINAL_RUN_STATUSES:
                raise RunFinishedError(f"Run 已结束({run.status})，拒绝继续执行: {run_id}")
        sink = add_run_log_file(run_id)
        cancelled = (lambda: bool(should_cancel and should_cancel())) if should_cancel else (lambda: False)

        def finish_cancelled() -> None:
            try:
                self.rm.finish_run(run_id, "cancelled", cost_est=self.gateway.cost)
            except LookupError:
                pass  # 已被提前终结（如取消发生在收尾之后），幂等忽略

        try:
            with bind_run(run_id):
                if cancelled():
                    finish_cancelled(); return run_id
                self.rm.set_stage(run_id, "init", "running"); self.rm.set_stage(run_id, "init", "done")
                if cancelled():
                    finish_cancelled(); return run_id
                self.rm.set_stage(run_id, "collect", "running")
                rows = self.collector.collect()
                if scope:
                    scope_path = scope  # 已由 _normalize_scope 归一化的仓库内相对路径
                    rows = [r for r in rows if r["relative_path"] == scope_path or r["relative_path"].startswith(scope_path + "/")]
                previous = {}
                if self.snapshot_path.exists():
                    previous = json.loads(self.snapshot_path.read_text(encoding="utf-8")).get("notes", {})
                # A scoped run must not tombstone notes outside its scope.
                previous_scope = previous
                if scope:
                    previous_scope = {p: row for p, row in previous.items() if p == scope_path or p.startswith(scope_path + "/")}
                missing = sorted(set(previous_scope) - {r["relative_path"] for r in rows})
                for row in rows:
                    row["changed"] = previous.get(row["relative_path"], {}).get("content_hash") != row["content_hash"]
                self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                snapshot_notes = dict(previous)
                snapshot_notes.update({r["relative_path"]: r for r in rows})
                for path in missing:
                    snapshot_notes.pop(path, None)
                self.snapshot_path.write_text(json.dumps({"notes": snapshot_notes}, ensure_ascii=False), encoding="utf-8")
                collect_path = self.io.write(run_id, "collect", {"rows": rows, "missing": missing})
                with Session(self.engine) as session:
                    missing_ids = []
                    for path in missing:
                        old = previous_scope[path]
                        note_id = old.get("note_id")
                        if note_id:
                            missing_ids.append(note_id)
                        tombstone = session.get(Note, note_id)
                        if tombstone is not None:
                            tombstone.vault_status = "missing"
                            tombstone.last_run_id = run_id
                    # 删除墓碑关联：删除/缺失笔记不应继续出现在关联结果中。
                    # missing_ids 来自 previous_scope，因此 scoped run 不会触碰范围外关联。
                    if missing_ids:
                        session.execute(
                            delete(Association).where(
                                or_(
                                    Association.src_id.in_(missing_ids),
                                    Association.dst_id.in_(missing_ids),
                                )
                            )
                        )
                    for note in rows:
                        record = session.get(Note, note["note_id"])
                        if record is None:
                            record = Note(id=note["note_id"]); session.add(record)
                        record.path = note.get("filepath", "")
                        record.folder = note.get("folder", "")
                        record.filename = note.get("filename", "")
                        record.mtime = note.get("modified_time")
                        record.content_hash = note.get("content_hash", "")
                        record.parse_status = note.get("parse_status", "ok")
                        record.vault_status = note.get("vault_status", "active")
                        record.last_run_id = run_id
                    session.commit()
                self.rm.bump_items(run_id, "collect", total=len(rows), done=len(rows))
                self.rm.set_stage(run_id, "collect", "done", checkpoint_path=str(collect_path))
                if cancelled():
                    finish_cancelled(); return run_id
                self.rm.set_stage(run_id, "extract", "running")
                results, failures = [], []
                with Session(self.engine) as session:
                    for note in rows:
                        if cancelled():
                            break
                        if note["vault_status"] != "active": continue
                        # 只有已有成功抽取的笔记才能按快照跳过；失败抽取不污染成功快照，
                        # 即使内容未变也必须在下一 Run 重试。
                        if not note["changed"] and (
                            self._note_has_extraction(session, note["note_id"])
                            or previous.get(note["relative_path"], {}).get("extracted") is True
                        ):
                            self.rm.bump_items(run_id, "extract", done=1)
                            continue
                        try:
                            draft = extract_note(self.gateway, note, run_id=run_id)
                            if self.gateway.calls:
                                call = self.gateway.calls[-1]
                                session.add(LLMCall(run_id=run_id, stage="extract", caller="event_extractor", model=call["model"], prompt_tokens=call.get("prompt_tokens", 0), completion_tokens=call.get("completion_tokens", 0), cost_est=call.get("cost_est", 0.0), retries=call.get("retries", 0), status=call.get("status", "ok"), digest=call["digest"]))
                            extraction = Extraction(note_id=note["note_id"], run_id=run_id, title=draft.title, summary=draft.summary, keywords=json.dumps(draft.keywords, ensure_ascii=False), candidate_tags=json.dumps(draft.candidate_tags, ensure_ascii=False), model=getattr(self.gateway, "model", ""), raw_json=draft.model_dump_json())
                            session.add(extraction); session.flush()
                            for event in draft.events:
                                session.add(Event(note_id=note["note_id"], extraction_id=extraction.id, content=event.content, time_clue=event.time_clue, status_clue=event.status_clue, order_in_note=event.order_in_note))
                            session.commit()
                            note["extracted"] = True
                            results.append({"note_id": note["note_id"], "draft": draft.model_dump()}); self.rm.bump_items(run_id, "extract", done=1)
                        except ExtractionError as exc:
                            failures.append({"note_id": note["note_id"], "error": str(exc)}); self.rm.bump_items(run_id, "extract", failed=1)
                    session.commit()
                if cancelled():
                    # 未完成条目不落 extract 产物即可，直接收尾
                    finish_cancelled(); return run_id
                # 仅在抽取成功后提交成功标记，失败项不会被下一轮误判为 unchanged。
                snapshot_notes = dict(previous)
                snapshot_notes.update({r["relative_path"]: r for r in rows if r.get("extracted") is True})
                self.snapshot_path.write_text(json.dumps({"notes": snapshot_notes}, ensure_ascii=False), encoding="utf-8")
                extract_path = self.io.write(run_id, "extract", {"results": results, "failures": failures})
                self.rm.bump_items(run_id, "extract", total=len(results) + len(failures))
                final = "failed" if failures and not results else "done"
                self.rm.set_stage(run_id, "extract", final, checkpoint_path=str(extract_path), error=f"{len(failures)} 条失败" if failures else None)
                if final != "done":
                    self.rm.set_stage(run_id, "associate", "skipped")
                    self.rm.finish_run(run_id, final, cost_est=self.gateway.cost)
                    return run_id
                # ── associate：Chroma 语义 + 结构/时间信号 → 候选 → LLM 判定 → associations 入库 ──
                self.rm.set_stage(run_id, "associate", "running")
                store = ChromaVectorStore(self.chroma_path, model_name=self.embedding_model, embedding_function=self.embedding_function)
                active_ids = [n["note_id"] for n in rows if n["vault_status"] == "active"]
                assoc_notes: list[dict[str, Any]] = []
                event_rows: list[Event] = []
                with Session(self.engine) as session:
                    # 增量运行跳过未变更笔记（无当前 run 的 extraction 行），取每个 active 笔记的最新提炼结果
                    latest: dict[str, Extraction] = {}
                    for ex in session.scalars(select(Extraction).where(Extraction.note_id.in_(active_ids)).order_by(Extraction.id.desc())):
                        latest.setdefault(ex.note_id, ex)
                    for note in rows:
                        if note["vault_status"] != "active":
                            continue
                        ex = latest.get(note["note_id"])
                        if ex is None:
                            continue
                        updated_at = ""
                        mt = note.get("modified_time")
                        if mt:
                            updated_at = datetime.fromtimestamp(mt).isoformat(timespec="seconds")
                        keywords = list(json.loads(ex.keywords)) if isinstance(ex.keywords, str) else (ex.keywords or [])
                        assoc_notes.append({"id": note["note_id"], "folder": note.get("folder", ""), "filename": note.get("filename", ""), "title": ex.title, "summary": ex.summary, "keywords": keywords, "updated_at": updated_at})
                    ex_ids = [ex.id for ex in latest.values()]
                    if ex_ids:
                        event_rows = list(session.scalars(select(Event).where(Event.extraction_id.in_(ex_ids))))
                # 事件向量（DESIGN.md 4.2）：id 按 note_id + 顺序稳定，重提炼不累积旧 extraction 向量。
                # 先清理本次 scope 的现有事件（含旧 extraction），再写入 active 笔记最新快照；
                # 删除墓碑笔记的全部事件向量，scope 外不触碰。
                scoped_note_ids = [n["note_id"] for n in rows]
                store.delete_events_by_note(scoped_note_ids)
                store.delete_events_by_note(
                    [n["note_id"] for n in self.collector.collect() if n["vault_status"] != "active"]
                )
                store.add_events([
                    {"id": f"{e.note_id}:{e.order_in_note}", "content": e.content, "time_clue": e.time_clue or "", "note_id": e.note_id}
                    for e in event_rows
                ])
                store.add_notes(assoc_notes)
                candidates = generate_candidates(assoc_notes, vector_store=store, k=5)
                call_start = len(self.gateway.calls)
                # 失败隔离：单个候选判定失败不中断整个阶段
                judgements, failed = [], []
                for cand in candidates:
                    try:
                        judgements.append(judge_candidates(self.gateway, [cand])[0])
                    except Exception as exc:
                        failed.append({"source_id": cand.source_id, "target_id": cand.target_id, "error": str(exc)})
                with Session(self.engine) as session:
                    for call in self.gateway.calls[call_start:]:
                        session.add(LLMCall(run_id=run_id, stage="associate", caller="association_judger", model=call["model"], prompt_tokens=call.get("prompt_tokens", 0), completion_tokens=call.get("completion_tokens", 0), cost_est=call.get("cost_est", 0.0), retries=call.get("retries", 0), status=call.get("status", "ok"), digest=call["digest"]))
                    cand_by_pair = {(c.source_id, c.target_id): c for c in candidates}
                    for j in judgements:
                        if not j.related:
                            continue
                        cand = cand_by_pair.get((j.source_id, j.target_id))
                        basis = sorted(cand.features.keys()) if cand and cand.features else (cand.basis if cand else [])
                        association = session.scalar(select(Association).where(Association.src_type == "note", Association.src_id == j.source_id, Association.dst_id == j.target_id))
                        if association is None:
                            association = Association(src_type="note", src_id=j.source_id, dst_id=j.target_id)
                            session.add(association)
                        # 幂等：同一有向关联只更新为最新判定，不累积重复行
                        association.basis = json.dumps(basis, ensure_ascii=False)
                        association.confidence = j.confidence
                        association.evidence = json.dumps(j.evidence, ensure_ascii=False)
                        association.run_id = run_id
                    session.commit()
                assoc_path = self.io.write(run_id, "associate", {"candidates": [c.model_dump() for c in candidates], "judgements": [j.model_dump() for j in judgements], "failures": failed})
                self.rm.bump_items(run_id, "associate", total=len(candidates), done=len(judgements), failed=len(failed))
                self.rm.set_stage(run_id, "associate", "done", checkpoint_path=str(assoc_path))
                self.rm.finish_run(run_id, "done", cost_est=self.gateway.cost)
                return run_id
        except LLMCostCapExceeded as exc:
            # 成本护栏是 Run 级停止条件：不继续后续阶段，保留累计费用。
            try:
                self.rm.finish_run(run_id, "failed", cost_est=self.gateway.cost)
            except LookupError:
                pass
            return run_id
        except Exception:
            try:
                self.rm.finish_run(run_id, "failed", cost_est=self.gateway.cost)
            except LookupError:
                pass
            raise
        finally:
            remove_sink(sink)
