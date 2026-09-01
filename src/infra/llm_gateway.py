"""LLM 唯一出口的最小 RECORD/REPLAY 实现。"""
from __future__ import annotations

import hashlib
import json
import time
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
        self.calls: list[dict[str, Any]] = []
        self.max_retries = 2

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
            response = json.loads(path.read_text(encoding="utf-8"))["response"]
            self.calls.append({"digest": key, "model": self.model, "mode": "replay", "status": "ok"})
            return response
        if self.transport is None:
            try:
                from litellm import completion
            except ImportError as exc:
                raise RuntimeError("未安装 LiteLLM，无法执行真实调用") from exc
            def invoke(_: str) -> Any:
                return completion(model=self.model, messages=[{"role": "user", "content": prompt}])
            transport = invoke
        else:
            transport = self.transport
        response = None
        started = time.monotonic()
        for attempt in range(self.max_retries + 1):
            try:
                response = transport(prompt)
                break
            except Exception:
                if attempt >= self.max_retries:
                    self.calls.append({"digest": key, "model": self.model, "mode": "record", "status": "failed", "retries": attempt})
                    raise
                time.sleep(0.05 * (2 ** attempt))
        if not isinstance(response, str):
            response = response.choices[0].message.content
        self.root.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"prompt": prompt, "response": response}, ensure_ascii=False), encoding="utf-8")
        self.calls.append({"digest": key, "model": self.model, "mode": "record", "status": "ok", "retries": attempt, "latency_ms": round((time.monotonic() - started) * 1000, 2)})
        return response

    def structured(self, prompt: str, schema: type[T]) -> T:
        raw = self.chat(prompt)
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
            return schema.model_validate(value)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError("LLM 结构化输出校验失败") from exc
