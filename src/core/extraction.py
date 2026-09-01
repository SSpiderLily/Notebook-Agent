from __future__ import annotations

from pydantic import BaseModel, Field

from src.infra.llm_gateway import LLMGateway


class EventDraft(BaseModel):
    content: str
    time_clue: str | None = None
    status_clue: str | None = None
    order_in_note: int = 0


class ExtractionDraft(BaseModel):
    title: str
    summary: str
    keywords: list[str] = Field(default_factory=list)
    candidate_tags: list[str] = Field(default_factory=list)
    events: list[EventDraft] = Field(default_factory=list)


class ExtractionError(RuntimeError):
    pass


def extract_note(gateway: LLMGateway, note: dict, *, run_id: str, stage: str = "extract") -> ExtractionDraft:
    prompt = f"请提炼以下笔记为 JSON（title, summary, keywords, candidate_tags, events）；笔记路径：{note['relative_path']}\n{note['content']}"
    try:
        return gateway.structured(prompt, ExtractionDraft)
    except Exception as exc:
        raise ExtractionError(f"{note['relative_path']}: {exc}") from exc
