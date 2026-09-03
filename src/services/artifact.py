"""M5 产物生成服务：版本化、安全、幂等写入 Vault。"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.core.artifact import ArtifactRenderer, safe_filename
from src.infra.backup import BackupManager
from src.infra.safe_writer import SafeWriter


class ArtifactService:
    """生成树页与森林总览，不修改用户原始笔记。"""

    def __init__(self, vault_dir: Path | str, backup_dir: Path | str, versions_keep: int = 5):
        self.vault_dir = Path(vault_dir).resolve()
        self.root = self.vault_dir / "_noteagent"
        self.versions_keep = max(1, int(versions_keep))
        self.writer = SafeWriter(BackupManager(backup_dir, keep=self.versions_keep), self.vault_dir)
        self.renderer = ArtifactRenderer()

    def _write(self, relative: str, content: str) -> dict[str, Any]:
        path = self.vault_dir / relative
        old = path.read_text(encoding="utf-8") if path.is_file() else None
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        current_version = self._version(relative)
        if old == content:
            return {"path": relative, "changed": False, "version": current_version or 1, "content_hash": digest}
        version = current_version + 1
        if old is not None:
            rel = Path(relative)
            archive = self.root / f"v{version}" / rel.relative_to("_noteagent").parent
            archive.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, archive / path.name)
        self.writer.apply(relative, content, confirm=True)
        self._prune_versions()
        return {"path": relative, "changed": True, "version": version, "content_hash": digest}

    def _version(self, relative: str) -> int:
        name = Path(relative).name
        versions = []
        for p in self.root.glob("v*/**/" + name):
            try:
                versions.append(int(p.relative_to(self.root).parts[0][1:]))
            except (ValueError, IndexError):
                pass
        return max(versions, default=0)

    def _prune_versions(self) -> None:
        versions = sorted((p for p in self.root.glob("v*" ) if p.is_dir()), key=lambda p: p.name, reverse=True)
        for old in versions[self.versions_keep:]: shutil.rmtree(old, ignore_errors=True)

    def generate(self, trees: Sequence[Mapping[str, Any]], nodes_by_tree: Mapping[str, Sequence[Mapping[str, Any]]], events: Mapping[int, Mapping[str, Any]], notes: Mapping[str, Mapping[str, Any]], run_id: str) -> dict[str, Any]:
        results, links = [], {}
        for tree in trees:
            tid = str(tree.get("id", ""))
            filename = safe_filename(tid) + ".md"
            relative = f"_noteagent/trees/{filename}"
            content = self.renderer.render_tree(tree, nodes_by_tree.get(tid, ()), events, notes)
            result = self._write(relative, content); result.update({"kind": "tree_page", "tree_id": tid}); results.append(result)
            links[tid] = f"trees/{filename}"
        overview = self.renderer.render_overview(trees, links, run_id)
        result = self._write("_noteagent/overview.md", overview)
        result.update({"kind": "overview", "tree_id": None}); results.append(result)
        return {"run_id": run_id, "artifacts": results, "failures": []}
