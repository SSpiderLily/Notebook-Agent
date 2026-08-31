import re
from typing import Dict, Any, List, Optional
from datetime import datetime

from .models import Note


class NoteParser:
    """笔记内容解析器"""
    
    def __init__(self):
        """初始化解析器"""
        self.title_pattern = re.compile(r'^#\s+(.+)$', re.MULTILINE)
        self.metadata_pattern = re.compile(r'^---\s*\n(.*?)\n---', re.DOTALL)
        self.keywords_pattern = re.compile(r'#(\w+)')
        self.frontmatter_patterns = {
            'yaml': re.compile(r'^---\s*\n(.*?)\n---', re.DOTALL),
            'json': re.compile(r'^{\s*"(.+?)"\s*:\s*".+?"\s*}'),
        }
    
    def parse_title(self, content: str) -> str:
        """
        从内容中提取标题
        
        Args:
            content: 笔记内容
            
        Returns:
            提取的标题，如果找不到返回"无标题"
        """
        match = self.title_pattern.search(content)
        if match:
            return match.group(1).strip()
        return "无标题"
    
    def parse_metadata(self, content: str) -> Dict[str, Any]:
        """
        解析YAML元数据
        
        Args:
            content: 笔记内容
            
        Returns:
            元数据字典
        """
        match = self.frontmatter_patterns['yaml'].search(content)
        if not match:
            return {}
        
        metadata_text = match.group(1)
        metadata = {}
        
        for line in metadata_text.strip().split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                metadata[key.strip()] = value.strip()
        
        return metadata
    
    def extract_keywords(self, content: str) -> List[str]:
        """
        提取标签作为关键词
        
        Args:
            content: 笔记内容
            
        Returns:
            关键词列表
        """
        matches = self.keywords_pattern.findall(content)
        return list(set(matches))
    
    def extract_content_without_metadata(self, content: str) -> str:
        """
        移除元数据块后的纯内容
        
        Args:
            content: 原始内容
            
        Returns:
            纯内容
        """
        return self.frontmatter_patterns['yaml'].sub('', content).strip()
    
    def parse(self, content: str, filepath: str = None) -> Dict[str, Any]:
        """
        完整解析笔记内容
        
        Args:
            content: 笔记内容
            filepath: 文件路径（用于生成元数据）
            
        Returns:
            解析后的字典
        """
        title = self.parse_title(content)
        keywords = self.extract_keywords(content)
        metadata = self.parse_metadata(content)
        
        if not metadata and filepath:
            metadata = {
                'source': filepath,
                'created': datetime.now().isoformat()
            }
        
        return {
            'title': title,
            'content': self.extract_content_without_metadata(content),
            'keywords': keywords,
            'metadata': metadata
        }
    
    def parse_to_note(self, content: str, note_id: int = None, 
                     filepath: str = None) -> Note:
        """
        解析内容为Note对象
        
        Args:
            content: 笔记内容
            note_id: 笔记ID
            filepath: 文件路径
            
        Returns:
            Note对象
        """
        parsed = self.parse(content, filepath)
        
        created_at = None
        updated_at = None
        
        if filepath:
            from pathlib import Path
            path = Path(filepath)
            created_at = datetime.fromtimestamp(path.stat().st_ctime)
            updated_at = datetime.fromtimestamp(path.stat().st_mtime)
        
        return Note(
            id=note_id or hash(filepath or content),
            title=parsed['title'],
            content=parsed['content'],
            keywords=parsed['keywords'],
            metadata=parsed['metadata'],
            filepath=filepath,
            filename=Path(filepath).name if filepath else None,
            created_at=created_at,
            updated_at=updated_at
        )


class MarkdownParser(NoteParser):
    """Markdown格式解析器（继承自NoteParser）"""
    
    def __init__(self):
        super().__init__()
        self.code_block_pattern = re.compile(r'```[\s\S]*?```')
        self.inline_code_pattern = re.compile(r'`[^`]+`')
        self.image_pattern = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
        self.link_pattern = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')
    
    def extract_code_blocks(self, content: str) -> List[Dict[str, str]]:
        """
        提取代码块
        
        Args:
            content: 笔记内容
            
        Returns:
            代码块列表
        """
        blocks = []
        for match in re.finditer(r'```(\w*)\n([\s\S]*?)```', content):
            blocks.append({
                'language': match.group(1),
                'code': match.group(2)
            })
        return blocks
    
    def extract_images(self, content: str) -> List[Dict[str, str]]:
        """
        提取图片
        
        Args:
            content: 笔记内容
            
        Returns:
            图片列表
        """
        images = []
        for match in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', content):
            images.append({
                'alt': match.group(1),
                'url': match.group(2)
            })
        return images
    
    def clean_content(self, content: str) -> str:
        """
        清理内容（移除代码块、图片等）
        
        Args:
            content: 原始内容
            
        Returns:
            清理后的内容
        """
        content = self.code_block_pattern.sub('', content)
        content = self.image_pattern.sub('', content)
        content = self.inline_code_pattern.sub('', content)
        return content.strip()
