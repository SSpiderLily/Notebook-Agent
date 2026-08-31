from typing import List, Dict, Any, Optional
from pathlib import Path

from .models import Note
from .loader import NoteLoader
from .parser import NoteParser


class NoteProcessor:
    """笔记处理器 - 整合加载和解析"""
    
    def __init__(self, notebooks_dir: str = './notebooks', 
                 parser: Optional[NoteParser] = None):
        """
        初始化处理器
        
        Args:
            notebooks_dir: 笔记目录
            parser: 解析器实例
        """
        self.loader = NoteLoader(notebooks_dir)
        self.parser = parser or NoteParser()
        self.processed_notes: List[Note] = []
    
    def process_file(self, filepath: str, note_id: int = None) -> Note:
        """
        处理单个文件：加载 + 解析
        
        Args:
            filepath: 文件路径
            note_id: 笔记ID
            
        Returns:
            处理后的Note对象
        """
        raw_data = self.loader.load_single(filepath)
        
        parsed = self.parser.parse(
            raw_data['content'],
            raw_data['filepath']
        )
        
        note = Note(
            id=note_id or hash(raw_data['filepath']),
            title=parsed['title'],
            content=parsed['content'],
            keywords=parsed['keywords'],
            metadata=parsed['metadata'],
            filepath=raw_data['filepath'],
            filename=raw_data['filename'],
            created_at=None,
            updated_at=None
        )
        
        self.processed_notes.append(note)
        return note
    
    def process_directory(self, directory: Optional[str] = None) -> List[Note]:
        """
        处理目录下所有笔记
        
        Args:
            directory: 目录路径
            
        Returns:
            笔记列表
        """
        files = self.loader.scan_directory(directory)
        print(f"找到 {len(files)} 个Markdown文件")
        
        notes = []
        for i, filepath in enumerate(files, start=1):
            try:
                note = self.process_file(filepath, note_id=i)
                print(f"✓ 已处理: {note.filename} - {note.title}")
                notes.append(note)
            except Exception as e:
                print(f"✗ 处理失败 {filepath}: {e}")
        
        return notes
    
    def process_batch(self, filepaths: List[str]) -> List[Note]:
        """
        批量处理指定文件
        
        Args:
            filepaths: 文件路径列表
            
        Returns:
            笔记列表
        """
        notes = []
        for i, filepath in enumerate(filepaths, start=1):
            try:
                note = self.process_file(filepath, note_id=i)
                notes.append(note)
            except Exception as e:
                print(f"✗ 处理失败 {filepath}: {e}")
        
        return notes
    
    def get_processed_notes(self) -> List[Note]:
        """
        获取已处理的笔记列表
        
        Returns:
            Note对象列表
        """
        return self.processed_notes
    
    def clear_processed_notes(self):
        """清空已处理的笔记列表"""
        self.processed_notes = []
    
    def export_to_dict(self, notes: List[Note] = None) -> List[Dict[str, Any]]:
        """
        将笔记导出为字典列表
        
        Args:
            notes: 笔记列表，None则使用已处理的笔记
            
        Returns:
            字典列表
        """
        if notes is None:
            notes = self.processed_notes
        
        return [note.to_dict() for note in notes]
