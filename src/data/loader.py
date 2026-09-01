from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional

from src.infra.config import Settings, get_settings


class NoteLoader:
    """只读递归加载 Vault 中的 Markdown 笔记。"""

    def __init__(self, notebooks_dir: str | Path = "./notebooks", settings: Settings | None = None):
        self.notebooks_dir = Path(notebooks_dir)
        self.settings = settings or get_settings()
        self.failures: list[dict[str, str]] = []

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.notebooks_dir).as_posix()

    def _excluded(self, path: Path) -> bool:
        rel = path.relative_to(self.notebooks_dir)
        return any(part in self.settings.exclude_dirs for part in rel.parts[:-1])

    def scan_directory(self, directory: Optional[str | Path] = None) -> list[str]:
        target = Path(directory) if directory else self.notebooks_dir
        if not target.exists():
            raise FileNotFoundError(f"目录不存在: {target}")
        if not target.is_dir():
            raise NotADirectoryError(target)
        files = [p for p in target.rglob("*.md") if p.is_file() and not self._excluded(p)]
        return [str(p) for p in sorted(files, key=lambda p: self._relative(p))]

    def load_single(self, filepath: str | Path) -> dict[str, Any]:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {filepath}")
        if path.suffix.lower() != ".md":
            raise ValueError(f"不是Markdown文件: {filepath}")
        content = path.read_text(encoding="utf-8")
        rel = self._relative(path)
        return {
            "filepath": str(path), "relative_path": rel, "folder": str(Path(rel).parent.as_posix()) if Path(rel).parent.as_posix() != "." else "",
            "filename": path.name, "content": content, "size": path.stat().st_size,
            "created_time": path.stat().st_ctime, "modified_time": path.stat().st_mtime,
            "note_id": hashlib.sha256(rel.encode()).hexdigest(),
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        }

    def load_multiple(self, filepaths: list[str | Path]) -> list[dict[str, Any]]:
        notes = []
        self.failures = []
        for filepath in filepaths:
            try:
                notes.append(self.load_single(filepath))
            except Exception as exc:
                self.failures.append({"filepath": str(filepath), "error": str(exc)})
        return notes

    def load_all(self, directory: Optional[str | Path] = None) -> list[dict[str, Any]]:
        return self.load_multiple(self.scan_directory(directory))
