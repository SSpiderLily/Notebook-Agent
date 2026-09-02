from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AssociationCandidate(BaseModel):
    source_id: str
    target_id: str
    basis: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    features: dict[str, float] = Field(default_factory=dict)


class AssociationJudgement(BaseModel):
    source_id: str
    target_id: str
    related: bool
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    rationale: str = ""


def _value(item: Any, key: str, default: Any = "") -> Any:
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def generate_candidates(notes: list[Any], vector_store: Any = None, *, k: int = 5, min_similarity: float | None = None) -> list[AssociationCandidate]:
    """仅生成候选，不调用 LLM；结构/时间信号与语义检索均保留证据。"""
    result: dict[tuple[str, str], AssociationCandidate] = {}
    for i, source in enumerate(notes):
        sid = str(_value(source, "id"))
        sfolder = str(_value(source, "folder", _value(source, "filepath", ""))).rsplit("/", 1)[0]
        sname = str(_value(source, "filename", _value(source, "title", "")))
        stime = str(_value(source, "updated_at", _value(source, "created_at", "")))
        for target in notes[i + 1:]:
            tid = str(_value(target, "id"))
            tfolder = str(_value(target, "folder", _value(target, "filepath", ""))).rsplit("/", 1)[0]
            tname = str(_value(target, "filename", _value(target, "title", "")))
            basis, evidence, features = [], [], {}
            if sfolder and sfolder == tfolder:
                basis.append("folder"); evidence.append(f"同文件夹: {sfolder}"); features["folder"] = 1.0
            # common naming stem is a useful deterministic signal
            stem_a, stem_b = sname.rsplit(".", 1)[0], tname.rsplit(".", 1)[0]
            if stem_a and stem_b and (stem_a in stem_b or stem_b in stem_a):
                basis.append("naming"); evidence.append(f"命名相关: {sname} / {tname}"); features["naming"] = 1.0
            ttime = str(_value(target, "updated_at", _value(target, "created_at", "")))
            if stime and ttime and stime[:10] == ttime[:10]:
                basis.append("temporal"); evidence.append(f"同日: {stime[:10]}"); features["temporal"] = 1.0
            if vector_store is not None:
                for hit in vector_store.search(str(_value(source, "summary", _value(source, "content", ""))), k=k):
                    if str(hit["id"]) == tid and (min_similarity is None or (hit.get("distance") is not None and hit["distance"] <= min_similarity)):
                        basis.append("semantic"); evidence.append("向量检索相似"); features["semantic"] = 1.0
            if basis:
                result[(sid, tid)] = AssociationCandidate(source_id=sid, target_id=tid, basis=basis, evidence=evidence, features=features)
    return list(result.values())


def judge_candidates(gateway: Any, candidates: list[AssociationCandidate]) -> list[AssociationJudgement]:
    judged = []
    for candidate in candidates:
        prompt = "请判断以下候选笔记是否存在关联，仅输出符合 schema 的 JSON：\n" + candidate.model_dump_json()
        value = gateway.structured(prompt, AssociationJudgement)
        if value.source_id != candidate.source_id or value.target_id != candidate.target_id:
            raise ValueError("LLM 判定 ID 与候选不一致")
        judged.append(value)
    return judged

# 便于调用方使用的别名
AssociationCandidateDraft = AssociationCandidate
AssociationJudgementDraft = AssociationJudgement
