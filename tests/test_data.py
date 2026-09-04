#!/usr/bin/env python3
"""
笔记处理模块测试
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.models import Note
from src.data.loader import NoteLoader
from src.data.parser import NoteParser, MarkdownParser
from src.data.processor import NoteProcessor


def test_note_model():
    """测试Note数据模型"""
    print("\n=== 测试Note数据模型 ===")
    
    note = Note(
        id=1,
        title="测试笔记",
        content="这是测试内容",
        keywords=["测试", "示例"],
        metadata={"source": "manual"}
    )
    
    print(f"标题: {note.title}")
    print(f"关键词: {note.keywords}")
    print(f"字典格式: {note.to_dict()}")
    
    note.add_keyword("新关键词")
    print(f"添加关键词后: {note.keywords}")
    
    note.add_metadata("author", "test_user")
    print(f"添加元数据后: {note.metadata}")


def test_note_loader():
    """测试NoteLoader加载器"""
    print("\n=== 测试NoteLoader加载器 ===")
    
    loader = NoteLoader('./notebooks')
    
    print(f"笔记目录: {loader.notebooks_dir}")
    print(f"目录存在: {loader.notebooks_dir.exists()}")
    
    files = loader.scan_directory()
    print(f"找到 {len(files)} 个Markdown文件")
    
    if files:
        first_file = files[0]
        print(f"\n加载文件: {first_file}")
        
        raw_data = loader.load_single(first_file)
        print(f"文件名: {raw_data['filename']}")
        print(f"文件大小: {raw_data['size']} 字节")
        print(f"内容长度: {len(raw_data['content'])} 字符")


def test_note_parser_date_metadata_json_safe():
    """回归：frontmatter 中的日期应归一化为字符串，保证采集快照可 json.dumps。"""
    import json
    content = """---
title: 测试
created: 2026-03-02
nested:
  updated: 2026-03-05 10:00:00
tags: [a]
---

# 正文
"""
    metadata = NoteParser().parse_metadata(content)
    assert isinstance(metadata["created"], str)
    assert metadata["created"] == "2026-03-02"
    assert isinstance(metadata["nested"]["updated"], str)
    # 整棵元数据必须可 JSON 序列化（采集快照依赖此特性）
    json.dumps({"notes": {"a.md": {"metadata": metadata}}})
    assert metadata["tags"] == ["a"]


def test_note_parser():
    """测试NoteParser解析器"""
    print("\n=== 测试NoteParser解析器 ===")
    
    content = """---
title: 测试笔记
author: test_user
tags: [test, example]
---

# 这是标题

这是内容，包含#关键词1 和 #关键词2。

## 子标题

更多内容。
"""
    
    parser = NoteParser()
    
    title = parser.parse_title(content)
    print(f"标题: {title}")
    
    keywords = parser.extract_keywords(content)
    print(f"关键词: {keywords}")
    
    metadata = parser.parse_metadata(content)
    print(f"元数据: {metadata}")
    
    parsed = parser.parse(content, "test.md")
    print(f"\n完整解析结果:")
    print(f"  标题: {parsed['title']}")
    print(f"  关键词: {parsed['keywords']}")
    print(f"  元数据: {parsed['metadata']}")
    print(f"  内容长度: {len(parsed['content'])}")


def test_markdown_parser():
    """测试MarkdownParser"""
    print("\n=== 测试MarkdownParser ===")
    
    content = """# 标题

这是一个代码块：

```python
def hello():
    print("Hello")
```

这是图片：![alt text](image.png)

这是链接：[Google](https://google.com)

关键词：#python #test
"""
    
    parser = MarkdownParser()
    
    code_blocks = parser.extract_code_blocks(content)
    print(f"代码块数量: {len(code_blocks)}")
    if code_blocks:
        print(f"代码语言: {code_blocks[0]['language']}")
    
    images = parser.extract_images(content)
    print(f"图片数量: {len(images)}")
    
    clean_content = parser.clean_content(content)
    print(f"\n清理后内容长度: {len(clean_content)}")


def test_note_processor():
    """测试NoteProcessor处理器"""
    print("\n=== 测试NoteProcessor处理器 ===")
    
    processor = NoteProcessor('./notebooks')
    
    files = processor.loader.scan_directory()
    
    if files:
        print(f"找到 {len(files)} 个文件")
        
        note = processor.process_file(files[0])
        print(f"\n处理单个文件:")
        print(f"  ID: {note.id}")
        print(f"  标题: {note.title}")
        print(f"  关键词: {note.keywords}")
        
        processor.clear_processed_notes()
        
        print(f"\n处理整个目录:")
        notes = processor.process_directory()
        print(f"\n共处理 {len(notes)} 篇笔记")
        
        exported = processor.export_to_dict()
        print(f"导出为字典: {len(exported)} 条记录")
    else:
        print("没有找到Markdown文件，请在./notebooks目录下添加测试文件")


def main():
    """运行所有测试"""
    print("=" * 50)
    print("笔记处理模块测试")
    print("=" * 50)
    
    test_note_model()
    test_note_loader()
    test_note_parser()
    test_markdown_parser()
    test_note_processor()
    
    print("\n" + "=" * 50)
    print("测试完成")
    print("=" * 50)


if __name__ == '__main__':
    main()
