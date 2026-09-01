"""LLM 唯一出口的最小 RECORD/REPLAY 实现。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMReplayMiss(LookupError):
    pass


class LLMCostCapExceeded(RuntimeError):
    pass


class LLMGateway:
    def __init__(self, recordings_dir: Path | str, mode: str = "replay", model: str = "test", cost_cap: float = 20.0, transport: Callable[[str], str] | None = None):
        self.root, self.mode, self.model, self.cost_cap, self.transport = Path(recordings_dir), mode.lower(), model, cost_cap, transport
        self.cost = 0.0

    def _key(self, prompt: str, schema: Any = None) -> str:
        name = getattr(schema, "__name__", "text")
        return hashlib.sha256(f"{self.model}\n{name}\n{prompt}".encode()).hexdigest()

    def chat(self, prompt: str) -> str:
        if self.cost >= self.cost_cap:
            raise LLMCostCapExceeded("已超过 Run 成本上限")
        key = self._key(prompt)
        path = self.root / f"{key}.json"
        if self.mode == "replay":
            if not path.exists():
                raise LLMReplayMiss(key)
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        if self.transport is None:
            raise RuntimeError("RECORD 模式需要注入 transport")
        response = self.transport(prompt)
        self.root.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"prompt": prompt, "response": response}, ensure_ascii=False), encoding="utf-8")
        return response

    def structured(self, prompt: str, schema: type[T]) -> T:
        raw = self.chat(prompt)
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
            return schema.model_validate(value)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError("LLM 结构化输出校验失败") from exc
