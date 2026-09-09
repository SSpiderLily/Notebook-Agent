# AGENTS.md — NoteAgent

Obsidian 笔记自动整理智能体：笔记本质是"森林"结构（一个任务/想法 = 一棵树，后续动作/事件 = 节点，事件完成则路径闭合）。系统通过 采集 → 事件抽取 → 关联推断 → 树重建（单次结构化判断器）→ 状态判定/断头检测 → 产物生成（树页+森林总览）→ Web 确认 → 双写回（标签+双链）重建这棵森林。完整需求见 `REQUIREMENTS.md`（SRS v1.0，已经 14 轮讨论确认，为一切开发的依据）。当前已实现到 M10（本地 Web 服务 + 问答 Agent），前后端均已落地。

## 项目结构

- `src/data/` — 数据层：`models.py`（`Note` dataclass）、`loader.py`、`parser.py`、`processor.py`、`collection.py`、`vector_store.py`
- `src/core/` — 业务抽象层 `Base*` ABC + 具体实现：`association.py`（关联推断）、`extraction.py`（事件抽取）、`status.py`（状态判定/断头检测）、`tree_rebuild.py`（单次结构化树重建判断器）、`artifact.py`、`note_tree.py`
- `src/infra/` — 基础设施：`config.py`（pydantic `Settings`，**当前实际配置来源**，经 `src.infra.config.get_settings()` 访问）、`llm_gateway.py`（LLM 唯一出口，含录制/回放）、`safe_writer.py`（一切 vault 写操作唯一通道）、`backup.py`、`logging.py`、`run_manager.py`、`stage_io.py`
- `src/api/` — FastAPI Web 层：`app.py`（`create_app`）、`task_manager.py`、`chat.py`（M8 问答）、`forest.py`、`writeback.py`（双写回）、`adjustments.py`、`schemas.py`
- `src/agents/` — 真 Agent：`qa.py`（问答会话）
- `src/services/` — 业务编排：`pipeline.py`、`writeback.py`、`artifact.py`、`adjustment.py`
- `src/models/orm.py` — SQLite ORM（SQLAlchemy + Alembic 迁移）
- `src/config/settings.py` — **旧版** dotenv 配置（已被 `src/infra/config.py` 取代，勿再依赖）
- `src/cli/`、`src/utils/` — CLI 与工具函数
- `frontend/` — Vue 3 + Vite 前端（确认工作台、问答聊天页）
- `notebooks/` — 待处理的 `.md` 笔记输入（开发样例仓库）；`data/`、`output/`、`logs/` — 运行时产物（已 gitignore）
- `dev-log/` — 进度看板 `进度看板.md` + `EVT-*` 原子事件文档（由 `scripts/record-progress.py` 维护）
- `data-accept*/`、`real-vault/` — 验收用真实保险库副本（gitignored，不提交）

## 命令

```bash
pip install -r requirements.txt                    # fastapi/chromadb/sqlalchemy/litellm/pytest…
python -m src.main                                 # 启动本地 Web 服务（FastAPI + uvicorn），必须从仓库根目录运行
pytest tests/                                      # 核心逻辑用 pytest（pytest-asyncio 支持异步）
cd frontend && npm install && npm run dev          # 前端（Vue 3 + Vite）
python scripts/record-progress.py                  # 记录一个原子进度事件并同步看板
python scripts/record-error.py                     # 新增/更新 dev-log/错误记录.md 的一条错误记录
```

- 源码内 import 统一用 `from src.xxx import ...`，任何入口都必须从仓库根目录运行。
- 无 pyproject / pytest.ini / lint / typecheck 配置；直接 `pytest tests/` 收集。`tests/test_data.py` 为历史遗留脚本。

## 约定与注意事项

- **gitignore 纪律**：新增重要文件/目录（密钥、运行时产物、虚拟环境、本地工具状态等）时，必须同步写入 `.gitignore` 并提交，确保生成物与敏感信息不进仓库。
- **产品形态（SRS 已定）**：本机运行的本地 Web 服务——后端 Python + FastAPI，前端 Vue 3（简单展示层）；直接读写 vault 文件夹（唯一数据通道），不要求 Obsidian 运行；前端用 `obsidian://` URI 跳转原笔记。
- **Agent 架构**：混合架构，主干由代码编排（幂等/断点/成本可控）；**真 Agent 仅问答**（多轮+记忆+指令集）；树重建已简化为单次结构化判断器（每事件一次调用），其余 LLM 环节（抽取/判定/撰写）同为单次结构化调用。
- **安全边界（最高优先级约束）**：默认绝不修改/移动/删除用户原始笔记；唯一例外是"双写回"（标签回写+双链写回），必须走 Web 预览 → 确认 → 只增不删 → 时间戳备份 → 幂等流程。所有产物写入 vault 内 `_noteagent/` 专用目录。
- **关键领域事实**：现有笔记无双链，结构隐式；可用信号=文件夹结构+文件命名规律；笔记粒度为一篇多动作（需事件抽取）；树重建输出为"草稿森林"（附证据+置信度），经人工确认后才固化。
- 技术栈：Python 3.12（`.venv` 已装）、FastAPI + uvicorn、Chroma 向量库、SQLite（SQLAlchemy + Alembic）、OpenAI 兼容 API（`.env` 默认 DeepSeek，通义千问经 `OPENAI_BASE_URL`/`MODEL_NAME` 切换）；前端 Vue 3 + Vite；日志用 loguru。全部笔记内容可上云（已确认）。
- **配置来源是 `src/infra/config.py`**（pydantic `Settings`），经 `src.infra.config.get_settings()` 单例访问；键名见 `src/infra/config.py`（`OPENAI_API_KEY`/`OPENAI_BASE_URL`/`MODEL_NAME`/`LLM_MODE`=record|replay/`LLM_CONCURRENCY`/`VAULT_DIR` 等）。`.env` 与 `.env.example` 均存在。
- `.env` 含真实 API key，**不要读取/提交其内容**（已 gitignore）。
- 新核心功能先在 `src/core/` 定义/继承 `Base*` 抽象基类，具体实现放对应子包；核心模块（解析/事件抽取校验/树构建/写回安全）需配 pytest。
- **开发数据**：开发调试用 `notebooks/` 样例仓库（20–50 篇，埋入烂尾/跨文件夹/日记式/命名规律等边界案例）；验收用 `data-accept*/`、`real-vault/` 真实仓库副本。
- 注释与文档使用中文；文档类命名不一致：`Plan.md`（README 写作 `plan.md`）。
- NoteAgent **已是独立 git 仓库**（分支 `main`，远程 `origin/main`），直接在此提交/推送；禁止自动提交 `.env`、`.venv/`、`data/`、`data-accept*/`、`real-vault/`、`logs/`、`.zcode/` 或 `.DS_Store`。
- Python 版本：`Plan.md` 目标 3.12，`.venv` 为 Python 3.12.9。

## 自动提交与进度同步规则

每个**可验收子步骤**完成后，按以下顺序执行并形成一个可回滚提交：

1. 运行相关测试与验证命令；测试失败时不得标记完成、提交或推送。
2. 检查 `git diff --check`、`git status` 和敏感/运行时文件的忽略规则。
3. 更新 `dev-log/进度看板.md`：当前焦点只保留真实下一步；已完成事项在时间线**追加**记录，不覆盖历史。
4. 使用 `python scripts/record-progress.py` 新增一个原子事件文档并同步看板；一个文档只记录一个阶段完成或一次额外工作，不覆盖历史。
5. 确认事件文档与看板双向链接均存在：事件文档包含 `[[进度看板]]`，看板包含对应 `[[事件文档]]`。
6. **错误记录自动更新**：本步骤遇到并已解决的任何错误（报错、异常行为、回归），用 `python scripts/record-error.py` 在 `dev-log/错误记录.md` 新增/更新对应条目并翻转状态为「已解决」；尚未解决的错误也须登记为「待解决」，不得静默吞掉。格式与模板见该文档。
7. 只暂存本次相关文件，禁止无选择性执行 `git add .`。
8. 提交并推送：`git commit` 后执行 `git push origin main`。

每条进度记录应包含日期、阶段编号、完成内容、测试结果、详情文档和 commit。新增重要文件/目录（密钥、运行时产物、虚拟环境、本地工具状态等）必须同步写入 `.gitignore`。禁止自动提交 `.env`、`.venv/`、`data/`、`logs/`、`.zcode/` 或 `.DS_Store`；禁止 force push、删除远程分支和自动 reset。测试失败或实现未完成时，应记录阻塞原因，不得伪装为完成。

## 设计文档

**改动任何核心逻辑前必读 `REQUIREMENTS.md`**（SRS v1.0：领域模型、FR-1~13、NFR-1~10、九阶段工作流、验收标准、13 轮需求确认记录）。**写代码前必读 `DESIGN.md`**（概要设计 v1.1：分层架构、数据模型、API 契约、Agent 设计、里程碑 M0~M9、设计决策记录）。核心设计原则：基础设施与可观测先行（M0）、LLM 唯一出口 Gateway（含录制/回放）、已验证树只追加不自动重组、一切 vault 写操作走 SafeWriter。`Plan.md` 为原始需求历史留档；`README.md` 的进度表已过时，进入实现阶段后需同步更新。
