"""Vault 安全写入：范围、类型、预览哈希、备份与原子替换。"""
from __future__ import annotations
import difflib, hashlib, os, tempfile
from pathlib import Path
from src.infra.backup import BackupManager

class SafeWriter:
    def __init__(self, backup: BackupManager, vault_dir: Path | str | None = None):
        self.backup = backup; self.vault_dir = Path(vault_dir).resolve() if vault_dir else None
    def _target(self, path):
        p = Path(path)
        if not p.is_absolute():
            if self.vault_dir is None: raise ValueError('必须配置 vault_dir 才能使用相对路径')
            p = self.vault_dir / p
        p = p.resolve(strict=False)
        if self.vault_dir and self.vault_dir not in p.parents: raise PermissionError('写入路径超出 vault')
        if p.exists() and (p.is_symlink() or not p.is_file()): raise PermissionError('目标必须是普通文件且不可为 symlink')
        if p.suffix.lower() != '.md': raise ValueError('SafeWriter 仅允许写入 Markdown 文件')
        return p
    def preview(self, path, content: str) -> str:
        p=self._target(path); old=p.read_text(encoding='utf-8') if p.exists() else ''
        return ''.join(difflib.unified_diff(old.splitlines(True), content.splitlines(True), fromfile=str(p), tofile=str(p)))
    def preview_hash(self, path, content: str) -> str:
        """返回绑定当时文件内容的预览令牌，而非仅绑定 diff。"""
        p = self._target(path)
        old = p.read_text(encoding='utf-8') if p.exists() else ''
        diff = ''.join(difflib.unified_diff(old.splitlines(True), content.splitlines(True), fromfile=str(p), tofile=str(p)))
        return hashlib.sha256(old.encode('utf-8') + b'\0' + diff.encode('utf-8')).hexdigest()
    def apply(self, path, content: str, confirm=False, preview_hash=None) -> bool:
        if not confirm: raise PermissionError('写回必须显式 confirm=True')
        p=self._target(path); old=p.read_text(encoding='utf-8') if p.exists() else ''
        if preview_hash is not None and preview_hash != self.preview_hash(p, content): raise ValueError('preview hash 不匹配')
        if old == content: return False
        if p.exists(): self.backup.backup(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd,name=tempfile.mkstemp(prefix=f'.{p.name}.', dir=p.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f: f.write(content); f.flush(); os.fsync(f.fileno())
            os.replace(name,p)
            d=os.open(p.parent,os.O_RDONLY); os.fsync(d); os.close(d)
        finally:
            try: os.unlink(name)
            except FileNotFoundError: pass
        return True
