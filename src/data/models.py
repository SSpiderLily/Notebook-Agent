from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime


@dataclass
class Note:
    """笔记数据模型"""
    id: int
    title: str
    content: str
    keywords: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    filepath: Optional[str] = None
    filename: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    vector: Optional[List[float]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            'id': self.id,
            'title': self.title,
            'content': self.content,
            'keywords': self.keywords,
            'metadata': self.metadata
        }
        
        if self.filepath:
            result['filepath'] = self.filepath
        if self.filename:
            result['filename'] = self.filename
        if self.created_at:
            result['created_at'] = self.created_at.isoformat()
        if self.updated_at:
            result['updated_at'] = self.updated_at.isoformat()
        
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Note':
        """从字典创建Note实例"""
        metadata = data.get('metadata', {})
        
        created_at = None
        if data.get('created_at'):
            created_at = datetime.fromisoformat(data['created_at'])
        
        updated_at = None
        if data.get('updated_at'):
            updated_at = datetime.fromisoformat(data['updated_at'])
        
        return cls(
            id=data['id'],
            title=data['title'],
            content=data['content'],
            keywords=data.get('keywords', []),
            metadata=metadata,
            filepath=data.get('filepath'),
            filename=data.get('filename'),
            created_at=created_at,
            updated_at=updated_at,
            vector=data.get('vector')
        )
    
    def add_keyword(self, keyword: str):
        """添加关键词"""
        if keyword not in self.keywords:
            self.keywords.append(keyword)
    
    def add_metadata(self, key: str, value: Any):
        """添加元数据"""
        self.metadata[key] = value
        self.updated_at = datetime.now()
