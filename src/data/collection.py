from __future__ import annotations

import fnmatch
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.infra.config import Settings, get_settings
from src.data.loader import NoteLoader
from src.data.parser import NoteParser

@dataclass
class ChangeSet:
    added: list[str]
    modified: list[str]
    unchanged: list[str]
    missing: list[str]

@dataclass
class Estimate:
    notes: int
    characters: int
    calls: int
    estimated_cost_cny: float
    estimated_minutes: float

class Collector:
    def __init__(self, vault_dir: str | Path, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.vault = Path(vault_dir)
        self.loader = NoteLoader(self.vault, self.settings)
        self.parser = NoteParser()

    def collect(self) -> list[dict[str, Any]]:
        rows = []
        for raw in self.loader.load_all():
            parsed = self.parser.parse(raw["content"], raw["filepath"])
            fm_ignore = parsed["metadata"].get("noteagent") == "ignore" or parsed["metadata"].get("noteagent:ignore") is True
            glob_ignore = next((pat for pat in self.settings.ignore_paths if fnmatch.fnmatch(raw["relative_path"], pat)), None)
            if fm_ignore or glob_ignore:
                status, reason = "ignored", "frontmatter" if fm_ignore else f"glob:{glob_ignore}"
            else:
                status, reason = "active", None
            rows.append({**raw, **parsed, "vault_status": status, "ignore_reason": reason, "parse_status": "ok"})
        return rows

    @staticmethod
    def diff(current: list[dict[str, Any]], previous: dict[str, Any]) -> ChangeSet:
        now = {r["relative_path"]: r for r in current}
        old = previous.get("notes", previous)
        added, modified, unchanged = [], [], []
        for path, row in now.items():
            if path not in old: added.append(path)
            elif old[path].get("content_hash") != row["content_hash"]: modified.append(path)
            else: unchanged.append(path)
        missing = sorted(set(old) - set(now))
        return ChangeSet(sorted(added), sorted(modified), sorted(unchanged), missing)

    @staticmethod
    def estimate(rows: list[dict[str, Any]], settings: Settings | None = None) -> Estimate:
        settings = settings or get_settings()
        active = [r for r in rows if r.get("vault_status") == "active"]
        chars = sum(len(r.get("content", "")) for r in active)
        inp, out = settings.price_of(settings.model_name)
        cost = (chars / 4 * inp + len(active) * 300 * out) / 1_000_000
        return Estimate(len(active), chars, len(active), round(cost, 6), round(len(active) / 60, 2))

    @staticmethod
    def save_snapshot(path: str | Path, rows: list[dict[str, Any]]) -> None:
        Path(path).write_text(json.dumps({"notes": {r["relative_path"]: r for r in rows}}, ensure_ascii=False, indent=2), encoding="utf-8")
