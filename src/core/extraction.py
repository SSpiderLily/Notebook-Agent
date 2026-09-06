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


def build_extraction_prompt(relative_path: str, content: str) -> str:
    """构造事件抽取的结构化提示词。

    明确规定 JSON 结构与 events 元素字段，保证模型输出与 ExtractionDraft schema 一致，
    避免模型自由发挥 `date/name/type` 等字段导致结构化校验失败。测试录制/回放也复用此构建。
    """
    return (
        "请把下面这篇笔记提炼为结构化 JSON，直接输出 JSON 对象，不要额外解释，不要用 Markdown 围栏。\n"
        "顶层字段（全部为字符串或数组）："
        "title（标题）、summary（摘要）、keywords（关键词数组）、candidate_tags（候选标签数组，可含 # 前缀）。\n"
        "events：把笔记拆成若干独立动作/事件组成的数组；每个元素必须是对象，字段："
        "content（必填字符串：该动作/事件的描述）、"
        "time_clue（可选字符串：原笔记中出现的时间线索，如日期/早上/上周，无则 null）、"
        "status_clue（可选字符串：原笔记中表示完成/进行/搁置的状态线索，无则 null）、"
        "order_in_note（必填整数：该事件在原文中的出现顺序，从 0 开始）。\n"
        "要求：事件粒度为一篇内的独立动作，宁可多拆不合并；若笔记没有明显动作仅概述，events 可为空数组。\n"
        f"笔记路径：{relative_path}\n"
        f"{content}"
    )


def extract_note(gateway: LLMGateway, note: dict, *, run_id: str, stage: str = "extract") -> ExtractionDraft:
    prompt = build_extraction_prompt(note["relative_path"], note["content"])
    try:
        return gateway.structured(prompt, ExtractionDraft)
    except Exception as exc:
        raise ExtractionError(f"{note['relative_path']}: {exc}") from exc
