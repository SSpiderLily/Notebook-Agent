"""Vault 安全写入：预览、备份、原子替换、幂等。"""
from __future__ import annotations

import difflib
import os
from pathlib import Path

from src.infra.backup import BackupManager


class SafeWriter:
    def __init__(self, backup: BackupManager):
        self.backup = backup

    def preview(self, path: Path | str, content: str) -> str:
        path = Path(path)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        return "".join(difflib.unified_diff(old.splitlines(True), content.splitlines(True), fromfile=str(path), tofile=str(path)))

    def apply(self, path: Path | str, content: str, confirm: bool = False) -> bool:
        if not confirm:
            raise PermissionError("写回必须显式 confirm=True")
        path = Path(path)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        if old == content:
            return False
        if path.exists():
            self.backup.backup(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
        return True
