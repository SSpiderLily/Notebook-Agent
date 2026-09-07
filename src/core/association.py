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
            # 关键词相交是同文件夹内候选的保守补充信号，避免整目录两两送入模型。
            source_keywords = {str(x).strip().lower() for x in (_value(source, "keywords", []) or []) if str(x).strip()}
            target_keywords = {str(x).strip().lower() for x in (_value(target, "keywords", []) or []) if str(x).strip()}
            common_keywords = sorted(source_keywords & target_keywords)
            if common_keywords:
                basis.append("keyword"); evidence.append(f"关键词相交: {', '.join(common_keywords)}"); features["keyword"] = 1.0
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
            # 候选门槛：单独"同文件夹"不再进入候选（避免整目录噪声对大量送入模型），
            # 必须叠加 naming/temporal/semantic/keyword 任一信号，或用关键词相交作为补充。
            if basis and not (basis == ["folder"]):
                result[(sid, tid)] = AssociationCandidate(source_id=sid, target_id=tid, basis=basis, evidence=evidence, features=features)
    return list(result.values())


def build_association_prompt(candidate: AssociationCandidate) -> str:
    """构造关联判定提示词，明确规定输出 JSON 的字段（与 AssociationJudgement schema 一致）。"""
    return (
        "判断下面两条笔记是否存在实质关联（同一任务/想法/事件的后续推进，或主题强相关）。\n"
        "只输出一个 JSON 对象，不要额外解释、不要 Markdown 围栏。字段：\n"
        "source_id（字符串：原样返回给定值）、target_id（字符串：原样返回给定值）、\n"
        "related（布尔：是否关联）、confidence（0~1 数字）、\n"
        "evidence（字符串数组：判定依据）、rationale（字符串：一句理由）。\n"
        "候选输入（source_id/target_id 必须原样回填）：\n"
        + candidate.model_dump_json()
    )


def judge_candidates(gateway: Any, candidates: list[AssociationCandidate]) -> list[AssociationJudgement]:
    judged = []
    for candidate in candidates:
        prompt = build_association_prompt(candidate)
        value = gateway.structured(prompt, AssociationJudgement)
        if value.source_id != candidate.source_id or value.target_id != candidate.target_id:
            raise ValueError("LLM 判定 ID 与候选不一致")
        judged.append(value)
    return judged

# 便于调用方使用的别名
AssociationCandidateDraft = AssociationCandidate
AssociationJudgementDraft = AssociationJudgement
