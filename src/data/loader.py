import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from .models import Note


class NoteLoader:
    """笔记文件加载器"""
    
    def __init__(self, notebooks_dir: str = './notebooks'):
        """
        初始化加载器
        
        Args:
            notebooks_dir: 笔记文件夹路径
        """
        self.notebooks_dir = Path(notebooks_dir)
        self._validate_directory()
    
    def _validate_directory(self):
        """验证目录是否存在，不存在则创建"""
        if not self.notebooks_dir.exists():
            self.notebooks_dir.mkdir(parents=True, exist_ok=True)
    
    def load_single(self, filepath: str) -> Dict[str, Any]:
        """
        加载单个笔记文件
        
        Args:
            filepath: 文件路径
            
        Returns:
            包含文件信息和内容的字典
        """
        path = Path(filepath)
        
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {filepath}")
        
        if path.suffix != '.md':
            raise ValueError(f"不是Markdown文件: {filepath}")
        
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return {
            'filepath': str(path),
            'filename': path.name,
            'content': content,
            'size': path.stat().st_size,
            'created_time': path.stat().st_ctime,
            'modified_time': path.stat().st_mtime
        }
    
    def load_multiple(self, filepaths: List[str]) -> List[Dict[str, Any]]:
        """
        批量加载笔记文件
        
        Args:
            filepaths: 文件路径列表
            
        Returns:
            笔记信息列表
        """
        notes = []
        for filepath in filepaths:
            try:
                note = self.load_single(filepath)
                notes.append(note)
            except Exception as e:
                print(f"加载失败 {filepath}: {e}")
        return notes
    
    def scan_directory(self, directory: Optional[str] = None) -> List[str]:
        """
        扫描目录下的所有Markdown文件
        
        Args:
            directory: 扫描目录，None则使用默认目录
            
        Returns:
            Markdown文件路径列表
        """
        target_dir = Path(directory) if directory else self.notebooks_dir
        
        if not target_dir.exists():
            raise FileNotFoundError(f"目录不存在: {target_dir}")
        
        md_files = list(target_dir.glob('*.md'))
        return [str(f) for f in sorted(md_files)]
    
    def load_all(self, directory: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        加载目录下所有Markdown文件
        
        Args:
            directory: 目录路径
            
        Returns:
            笔记数据列表
        """
        files = self.scan_directory(directory)
        return self.load_multiple(files)
    
    def load_to_note(self, filepath: str, note_id: int = None) -> Note:
        """
        加载文件并转换为Note对象
        
        Args:
            filepath: 文件路径
            note_id: 笔记ID，None则自动生成
            
        Returns:
            Note对象
        """
        raw_data = self.load_single(filepath)
        
        if note_id is None:
            note_id = hash(raw_data['filepath'])
        
        return Note(
            id=note_id,
            title=raw_data['filename'],
            content=raw_data['content'],
            filepath=raw_data['filepath'],
            filename=raw_data['filename'],
            created_at=datetime.fromtimestamp(raw_data['created_time']),
            updated_at=datetime.fromtimestamp(raw_data['modified_time'])
        )
    
    def load_all_to_notes(self, directory: Optional[str] = None) -> List[Note]:
        """
        加载目录下所有文件为Note对象列表
        
        Args:
            directory: 目录路径
            
        Returns:
            Note对象列表
        """
        files = self.scan_directory(directory)
        notes = []
        
        for i, filepath in enumerate(files, start=1):
            try:
                note = self.load_to_note(filepath, note_id=i)
                notes.append(note)
            except Exception as e:
                print(f"加载失败 {filepath}: {e}")
        
        return notes
