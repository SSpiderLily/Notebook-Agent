#!/usr/bin/env python3
"""
NoteAgent - 智能笔记整理与思维链生成工具
"""

def main():
    print("NoteAgent 启动中...")
    
    from src.config import get_settings
    settings = get_settings()
    
    print(f"配置加载完成")
    print(f"模型: {settings.model_name}")
    print(f"向量数据库: {settings.chroma_db_path}")
    print(f"笔记目录: {settings.notebooks_dir}")
    print(f"输出目录: {settings.output_dir}")

if __name__ == '__main__':
    main()
