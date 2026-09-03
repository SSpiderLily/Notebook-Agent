# AGENTS.md — NoteAgent

Obsidian 笔记自动整理智能体：笔记本质是"森林"结构（一个任务/想法 = 一棵树，后续动作/事件 = 节点，事件完成则路径闭合）。系统通过 采集 → 事件抽取 → 关联推断 → 树重建（ReAct Agent）→ 状态判定/断头检测 → 产物生成（树页+森林总览）→ Web 确认 → 双写回（标签+双链）重建这棵森林。完整需求见 `REQUIREMENTS.md`（SRS v1.0，已经 13 轮讨论确认，为一切开发的依据）。早期开发阶段，很多模块只有抽象基类。

## 项目结构

- `src/data/` — 数据层（**已实现**）：`models.py`（`Note` dataclass）、`loader.py`、`parser.py`、`processor.py`；`vector_store.py` 目前只有 `BaseVectorStore` 抽象基类
- `src/core/` — 业务抽象层（**全部只有抽象基类**）：`base.py`/`agent.py`/`analyzer.py`/`cot.py`/`exporter.py` 中的 `Base*` ABC
- `src/config/settings.py` — 配置，从 `.env` 读取（通过 `src.config.get_settings()` 单例访问）
- `src/cli/`、`src/utils/` — CLI 与工具函数（基本是占位）
- `notebooks/` — 待处理的 `.md` 笔记输入；`output/`、`data/chroma/` — 运行时输出（已 gitignore）

## 命令

```bash
pip install -r requirements.txt   # langchain, chromadb, python-dotenv, markdown, tqdm
python -m src.main                # 主程序入口（必须在仓库根目录运行）
python tests/test_data.py         # 测试是普通脚本（print 断言风格），不是 pytest
```

- 没有 pytest / lint / typecheck 配置。
- 测试脚本用 `sys.path.insert(0, ...)` 自举路径；源码内 import 统一用 `from src.xxx import ...`，因此任何入口都必须从仓库根目录运行。

## 约定与注意事项

- **gitignore 纪律**：新增重要文件/目录（密钥、运行时产物、虚拟环境、本地工具状态等）时，必须同步写入 `.gitignore` 并提交，确保生成物与敏感信息不进仓库。
- **产品形态（SRS 已定）**：本机运行的本地 Web 服务——后端 Python + FastAPI，前端 Vue 3（简单展示层）；直接读写 vault 文件夹（唯一数据通道），不要求 Obsidian 运行；前端用 `obsidian://` URI 跳转原笔记。
- **Agent 架构**：混合架构，主干由代码编排（幂等/断点/成本可控）；仅两个环节用真 Agent——树重建（ReAct 工具型）与问答（多轮+记忆+指令集）；其余 LLM 环节（抽取/判定/撰写）为单次结构化调用。
- **安全边界（最高优先级约束）**：默认绝不修改/移动/删除用户原始笔记；唯一例外是"双写回"（标签回写+双链写回），必须走 Web 预览 → 确认 → 只增不删 → 时间戳备份 → 幂等流程。所有产物写入 vault 内 `_noteagent/` 专用目录。
- **关键领域事实**：现有笔记无双链，结构隐式；可用信号=文件夹结构+文件命名规律；笔记粒度为一篇多动作（需事件抽取）；树重建输出为"草稿森林"（附证据+置信度），经人工确认后才固化。
- 技术栈：Python、LangChain、Chroma 向量库、SQLite（状态）、OpenAI 兼容 API（当前 `.env` 默认 DeepSeek，通义千问经 `OPENAI_BASE_URL`/`MODEL_NAME` 切换）；全部笔记内容可上云（已确认）。
- `.env` 含真实 API key，**不要读取/提交其内容**；README 提到的 `.env.example` 尚不存在，键名参考 `src/config/settings.py`。
- 新核心功能先在 `src/core/` 定义/继承 `Base*` 抽象基类，具体实现放对应子包；核心模块（解析/事件抽取校验/树构建/写回安全）需配 pytest。
- **开发数据**：开发调试用样例仓库（20–50 篇，埋入烂尾/跨文件夹/日记式/命名规律等边界案例）；验收用真实仓库副本。
- 注释与文档使用中文；文档类命名不一致：`Plan.md`（README 写作 `plan.md`）。
- 仓库位于上级目录的 git 仓库内（NoteAgent 本身未独立初始化 git），提交操作前先确认目标仓库。
- Python 版本：`Plan.md` 目标 3.12，本机现存 `__pycache__` 为 3.10 编译。

## 自动提交与进度同步规则

每个**可验收子步骤**完成后，按以下顺序执行并形成一个可回滚提交：

1. 运行相关测试与验证命令；测试失败时不得标记完成、提交或推送。
2. 检查 `git diff --check`、`git status` 和敏感/运行时文件的忽略规则。
3. 更新 `dev-log/进度看板.md`：当前焦点只保留真实下一步；已完成事项在时间线**追加**记录，不覆盖历史。
4. 使用 `python scripts/record-progress.py` 新增一个原子事件文档并同步看板；一个文档只记录一个阶段完成或一次额外工作，不覆盖历史。
5. 确认事件文档与看板双向链接均存在：事件文档包含 `[[进度看板]]`，看板包含对应 `[[事件文档]]`。
6. 只暂存本次相关文件，禁止无选择性执行 `git add .`。
7. 提交并推送：`git commit` 后执行 `git push origin main`。

每条进度记录应包含日期、阶段编号、完成内容、测试结果、详情文档和 commit。新增重要文件/目录（密钥、运行时产物、虚拟环境、本地工具状态等）必须同步写入 `.gitignore`。禁止自动提交 `.env`、`.venv/`、`data/`、`logs/`、`.zcode/` 或 `.DS_Store`；禁止 force push、删除远程分支和自动 reset。测试失败或实现未完成时，应记录阻塞原因，不得伪装为完成。

## 设计文档

**改动任何核心逻辑前必读 `REQUIREMENTS.md`**（SRS v1.0：领域模型、FR-1~13、NFR-1~10、九阶段工作流、验收标准、13 轮需求确认记录）。**写代码前必读 `DESIGN.md`**（概要设计 v1.1：分层架构、数据模型、API 契约、Agent 设计、里程碑 M0~M9、设计决策记录）。核心设计原则：基础设施与可观测先行（M0）、LLM 唯一出口 Gateway（含录制/回放）、已验证树只追加不自动重组、一切 vault 写操作走 SafeWriter。`Plan.md` 为原始需求历史留档；`README.md` 的进度表已过时，进入实现阶段后需同步更新。
