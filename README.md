# NoteAgent

一个智能笔记整理与思维链生成工具

## 项目结构

```
NoteAgent/
├── src/              # 源代码
│   ├── cli/          # 命令行界面
│   ├── config/       # 配置管理
│   ├── core/         # 核心业务逻辑
│   ├── data/         # 数据处理
│   │   ├── models.py      # 数据模型（Note）
│   │   ├── loader.py      # 笔记加载器
│   │   ├── parser.py      # 笔记解析器
│   │   └── processor.py   # 笔记处理器
│   ├── utils/        # 工具函数
│   └── main.py       # 主程序入口
├── tests/            # 测试文件
├── notebooks/        # 笔记文件存储
├── output/           # 输出结果
├── data/             # 数据存储（向量数据库等）
├── requirements.txt  # 依赖包
├── .env.example      # 环境变量示例
├── .gitignore        # Git忽略配置
├── plan.md           # 项目规划文档
└── README.md         # 项目说明文档
```

## 安装

```bash
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填写你的API密钥
```

## 使用

### 运行主程序

```bash
python -m src.main
```

### 运行测试

```bash
python tests/test_data.py
```

### 使用笔记处理模块

```python
from src.data.processor import NoteProcessor

# 创建处理器
processor = NoteProcessor('./notebooks')

# 处理单个文件
note = processor.process_file('./notebooks/test.md')

# 处理整个目录
notes = processor.process_directory()

# 查看处理结果
for note in notes:
    print(f"标题: {note.title}")
    print(f"关键词: {note.keywords}")
    print(f"内容: {note.content[:100]}...")
```

## 核心功能

### 笔记处理模块
- ✅ 多文件批量导入
- ✅ 笔记结构化（提取标题、内容、关键词）
- ✅ 元数据管理（时间戳、标签等）
- ✅ Markdown格式支持

### 待实现功能
- ⏳ 通义千问API接入
- ⏳ 向量数据库集成
- ⏳ 语义关联分析
- ⏳ 思维链生成
- ⏳ 命令行交互界面

## 开发计划

1. **第一阶段** - 笔记处理模块 ✅
   - 笔记加载器
   - 笔记解析器
   - 笔记处理器

2. **第二阶段** - AI模型集成
   - API客户端
   - 提示词模板
   - 内容提炼

3. **第三阶段** - 知识组织
   - 向量数据库
   - 语义检索
   - 关联分析

4. **第四阶段** - 高级功能
   - 思维链生成
   - CLI界面
   - 导出功能

## 依赖

- langchain
- langchain-openai
- chromadb
- python-dotenv
- markdown
- tqdm

## 许可证

MIT License
