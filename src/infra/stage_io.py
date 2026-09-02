"""阶段产物安全持久化。"""
from __future__ import annotations
import json, os, tempfile, uuid
from pathlib import Path
from typing import Any

class StageArtifactError(RuntimeError): pass

class StageIO:
    schema_version = 1
    ALLOWED_STAGES = frozenset({'init','collect','extract','associate','tree_rebuild','status_judge','artifact','confirm','writeback'})
    def __init__(self, runs_dir: Path | str): self.runs_dir = Path(runs_dir).resolve()
    @staticmethod
    def _id(value: str) -> str:
        value = str(value)
        if not value or value in {'.','..'} or '/' in value or '\\' in value or value.startswith('.'): raise StageArtifactError('非法 run_id')
        return value
    def _stage(self, stage: str) -> str:
        if stage not in self.ALLOWED_STAGES: raise StageArtifactError(f'非法阶段: {stage}')
        return stage
    def path(self, run_id: str, stage: str) -> Path:
        p = (self.runs_dir / self._id(run_id) / 'stages' / f'{self._stage(stage)}.json').resolve()
        if self.runs_dir not in p.parents: raise StageArtifactError('路径越界')
        return p
    def write(self, run_id: str, stage: str, payload: Any) -> Path:
        target = self.path(run_id, stage); target.parent.mkdir(parents=True, exist_ok=True)
        data = {'schema_version': self.schema_version, 'run_id': run_id, 'stage': stage, 'payload': payload}
        fd, name = tempfile.mkstemp(prefix=f'.{target.name}.', dir=target.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
            os.replace(name, target)
            dfd = os.open(target.parent, os.O_RDONLY); os.fsync(dfd); os.close(dfd)
        finally:
            try: os.unlink(name)
            except FileNotFoundError: pass
        return target
    def read(self, run_id: str, stage: str) -> Any:
        target = self.path(run_id, stage)
        try: data = json.loads(target.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc: raise StageArtifactError(f'阶段产物不可读: {target}') from exc
        if data.get('schema_version') != self.schema_version or data.get('run_id') != run_id or data.get('stage') != stage or 'payload' not in data: raise StageArtifactError(f'阶段产物版本或归属不匹配: {target}')
        return data['payload']
