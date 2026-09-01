"""写回前的时间戳备份。"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


class BackupManager:
    def __init__(self, root: Path | str, keep: int = 5):
        self.root, self.keep = Path(root), keep

    def backup(self, path: Path | str) -> Path:
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(source)
        dest = self.root / datetime.now().strftime("%Y%m%d-%H%M%S-%f") / source.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        dirs = sorted((p for p in self.root.iterdir() if p.is_dir()), reverse=True)
        for old in dirs[self.keep:]:
            shutil.rmtree(old)
        return dest
