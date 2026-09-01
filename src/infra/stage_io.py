"""阶段中间产物：版本化 JSON、原子写入与恢复。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class StageArtifactError(RuntimeError):
    pass


class StageIO:
    schema_version = 1

    def __init__(self, runs_dir: Path | str):
        self.runs_dir = Path(runs_dir)

    def path(self, run_id: str, stage: str) -> Path:
        return self.runs_dir / run_id / "stages" / f"{stage}.json"

    def write(self, run_id: str, stage: str, payload: Any) -> Path:
        target = self.path(run_id, stage)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = {"schema_version": self.schema_version, "run_id": run_id, "stage": stage, "payload": payload}
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        return target

    def read(self, run_id: str, stage: str) -> Any:
        target = self.path(run_id, stage)
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StageArtifactError(f"阶段产物不可读: {target}") from exc
        if data.get("schema_version") != self.schema_version or data.get("run_id") != run_id or data.get("stage") != stage:
            raise StageArtifactError(f"阶段产物版本或归属不匹配: {target}")
        return data["payload"]
