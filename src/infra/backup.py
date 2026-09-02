"""写回前的可验证时间戳备份。"""
from __future__ import annotations
import hashlib, json, os, shutil, tempfile
from datetime import datetime, timezone
from pathlib import Path

class BackupManager:
    def __init__(self, root: Path | str, keep: int = 5):
        self.root=Path(root).resolve()
        self.keep=int(keep)
        if self.keep < 1:
            raise ValueError('keep 必须大于等于 1')
    def backup(self, path):
        source=Path(path)
        if not source.is_file() or source.is_symlink(): raise FileNotFoundError(source)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f'); destdir=self.root/stamp; destdir.mkdir(parents=True)
        dest=destdir/source.name; shutil.copy2(source,dest)
        manifest={'version':1,'files':[{'source':str(source),'path':source.name,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}]}
        (destdir/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        dirs=sorted((p for p in self.root.iterdir() if p.is_dir()),key=lambda p:p.name,reverse=True)
        for old in dirs[self.keep:]: shutil.rmtree(old)
        return dest
    def list(self):
        """列出备份目录（兼容旧测试的 list API）。"""
        if not self.root.exists(): return []
        return sorted((p for p in self.root.iterdir() if p.is_dir()),key=lambda p:p.name,reverse=True)

    def list_manifests(self):
        """返回有效 manifest 路径，供恢复/检查调用。"""
        return [d / 'manifest.json' for d in self.list() if (d / 'manifest.json').is_file()]

    def restore(self, backup_dir, target=None):
        d=Path(backup_dir)
        # 旧调用可能传入 backup() 返回的文件路径。
        if d.is_file(): d = d.parent
        manifest=json.loads((d/'manifest.json').read_text(encoding='utf-8'))
        results=[]
        for item in manifest['files']:
            src=d/item['path']; out=Path(target) if target else Path(item['source'])
            # target 目录保持旧 API 语义；单文件备份时也接受目标文件。
            if target and Path(target).is_dir(): out=Path(target)/item['path']
            if hashlib.sha256(src.read_bytes()).hexdigest()!=item['sha256']: raise ValueError('备份校验失败')
            out.parent.mkdir(parents=True,exist_ok=True); fd,name=tempfile.mkstemp(prefix=f'.{out.name}.',dir=out.parent)
            try:
                with os.fdopen(fd,'wb') as f: f.write(src.read_bytes()); f.flush(); os.fsync(f.fileno())
                os.replace(name,out)
            finally:
                try: os.unlink(name)
                except FileNotFoundError: pass
            results.append(out)
        return results
