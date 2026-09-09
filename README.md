# NoteAgent

Obsidian 笔记自动整理智能体：本机运行的本地 Web 服务（后端 Python + FastAPI，前端 Vue 3 + Vite）。

笔记本质是"森林"结构——一个任务/想法 = 一棵树，后续动作/事件 = 节点，事件完成则路径闭合。系统通过 **采集 → 事件抽取 → 关联推断 → 树重建（单次结构化判断器）→ 状态判定/断头检测 → 产物生成 → Web 确认 → 双写回** 重建这棵森林。

- **当前阶段**：M0–M9 核心实现已完成；观测页、运行历史、失败/Vault 状态查询、生成物安全重置已落地，真实副本验收为收尾项。第 14 轮需求已将树重建从多轮 ReAct Agent 简化为单次结构化调用（控制成本、恢复确定性回放）。
- **需求与设计**：完整需求见 [REQUIREMENTS.md](REQUIREMENTS.md)（SRS v1.0，为一切开发的依据）；概要设计见 [DESIGN.md](DESIGN.md)（分层架构、数据模型、API 契约、里程碑 M0–M9）。

## 当前进度（里程碑）

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M0 | 基础设施：集中配置、日志、RunManager、StageIO、LLMGateway（录制/回放）、SafeWriter/Backup、FastAPI + SSE、任务 API | ✅ 完成 |
| M1 | 采集、解析、对账：Vault 递归扫描、稳定 ID/内容哈希、YAML/标签/双链解析、变更快照、试算报告 | ✅ 完成 |
| M2 | 事件抽取：LLM 批量抽取、SQLite 持久化、增量跳过、失败隔离与重试、调用台账与成本护栏、真实 Provider 接入 | ✅ 完成 |
| M3 | 向量与关联推断：Chroma 双集合、确定性嵌入、模型指纹检测、文件夹/命名/时间/语义候选、LLM 关联判定（并发化） | ✅ 完成 |
| M4 | 树重建与状态判定：单次结构化树重建判断器（每事件一次调用）、草稿森林、verified 只追加、四状态断头检测、人工复核、逐事件进度上报 | ✅ 完成 |
| M5 | 产物生成：Markdown 树页 + 森林总览、SafeWriter 原子写入、版本目录、幂等重跑 | ✅ 完成 |
| M6 | Web 查询与人工修正：森林/树查询、时间线、obsidian:// 跳转、Adjustment（set_status/retitle/move）、撤销、局部重生成 | ✅ 完成 |
| M7 | 双写回：标签/双链写回、diff 预览、逐项确认、备份恢复、只增不删、幂等原子写 | ✅ 完成 |
| M8 | 问答 Agent：会话记忆、森林查询工具、多轮聊天、/clear /regen /export、引用跳转、Vue 聊天页 | ✅ 完成 |
| M9 | 观测页完善（LLM 调用浏览器、Vault 状态报告）、重置/产物版本回退、真实副本验收、全部 SRS 验收要点 | ✅ 核心完成 |

> 详细进度以 `dev-log/进度看板.md` 为准，README 不再逐项罗列。

## 核心流程

```
采集 → 事件抽取 → 关联推断 → 树重建(单次结构化判断) → 状态判定/断头 → 产物生成(树页+森林总览)
     → Web 确认/人工修正 → 双写回(标签+双链)
```

人工确认后才固化：树重建输出为"草稿森林"（附证据 + 置信度），经 Web 预览确认后方写回 vault。

## 项目结构

```
NoteAgent/
├── src/
│   ├── data/         # 数据层：models/loader/parser/processor/collection/vector_store
│   ├── core/         # 业务抽象层 Base* ABC + 实现：association/extraction/status/tree_rebuild/artifact…
│   ├── infra/        # 基础设施：config / llm_gateway / safe_writer / backup / logging / run_manager / stage_io
│   ├── api/          # FastAPI Web 层：app / task_manager / chat / forest / writeback / adjustments / schemas
│   ├── agents/       # 真 Agent：qa（问答会话）
│   ├── services/     # 业务编排：pipeline / writeback / artifact / adjustment
│   ├── models/orm.py # SQLite ORM（SQLAlchemy + Alembic 迁移）
│   ├── config/       # 旧版 dotenv 配置（已被 src/infra/config.py 取代，勿再依赖）
│   ├── cli/、utils/  # CLI 与工具函数
│   └── main.py       # Web 服务入口
├── frontend/         # Vue 3 + Vite + Element Plus 前端（确认工作台、问答聊天页）
├── tests/            # pytest 测试（含 pytest-asyncio）
├── notebooks/        # 待处理的 .md 笔记输入（开发样例仓库）
├── dev-log/          # 进度看板 + EVT-* 原子事件文档 + 错误记录
├── data/、output/、logs/   # 运行时产物（已 gitignore）
└── real-vault/、data-accept*/  # 验收用真实保险库副本（已 gitignore，不提交）
```

## 快速开始

```bash
pip install -r requirements.txt        # 依赖
cp .env.example .env                    # 填写 API key 等配置（见下方配置）
python -m src.main                      # 启动本地 Web 服务（FastAPI + uvicorn，必须从仓库根目录运行）
pytest tests/                           # 运行测试
cd frontend && npm install && npm run dev   # 启动前端开发服务器
```

前端构建产物（`frontend/dist/`）由 `create_app` 在 FastAPI 同端口静态托管。

## 配置

配置来源统一为 `src/infra/config.py`（pydantic `Settings` 单例），经 `src.infra.config.get_settings()` 访问。通过环境变量 / `.env` 配置，键名见 `.env.example` 与 `src/infra/config.py`：

- `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `MODEL_NAME`：OpenAI 兼容 API（默认 DeepSeek，通义千问经 `OPENAI_BASE_URL` / `MODEL_NAME` 切换）
- `EMBEDDING_MODEL_NAME`：向量嵌入模型
- `LLM_MODE`：`record` | `replay`（启用 LLM 录制/回放）
- `LLM_CONCURRENCY` / `LLM_TIMEOUT_S` / `LLM_MAX_RETRIES` / `LLM_SCHEMA_FIX_RETRIES`：LLM 调用并发与重试
- `LLM_RUN_COST_CAP_CNY`：每 Run 成本上限
- `VAULT_DIR` / `DATA_DIR` / `LOGS_DIR`：数据与日志目录
- `ARTIFACTS_DIRNAME`：产物目录（vault 内 `_noteagent/`）
- `BACKUP_KEEP` / `ARTIFACT_VERSIONS_KEEP`：备份与产物版本保留数
- `HOST` / `PORT`：Web 服务地址
- `AGENT_MAX_STEPS` / `CONFIDENCE_REVIEW_THRESHOLD`：Agent 步数与置信度复核阈值

> `.env` 含真实 API key，已 gitignore，**不要读取/提交其内容**。

## 测试

```bash
pytest tests/        # 全量回归（当前 ≥128 项通过）
```

核心模块（解析 / 事件抽取校验 / 树构建 / 写回安全 / API / Agent）均配有 pytest；`tests/test_data.py` 为历史遗留脚本。

## 安全边界（最高优先级约束）

- 默认绝不修改 / 移动 / 删除用户原始笔记。
- 唯一例外是"双写回"（标签回写 + 双链写回），必须走 Web 预览 → 确认 → 只增不删 → 时间戳备份 → 幂等流程。
- 所有产物写入 vault 内 `_noteagent/` 专用目录。

## 相关文档

- [REQUIREMENTS.md](REQUIREMENTS.md) — SRS v1.0（领域模型、FR-1~13、NFR-1~10、九阶段工作流、验收标准）
- [DESIGN.md](DESIGN.md) — 概要设计 v1.1（分层架构、数据模型、API 契约、Agent 设计、里程碑、设计决策）
- `dev-log/进度看板.md` — 进度看板（最可靠的最新进度来源）
- `dev-log/错误记录.md` — 错误台账（现象→根因→解决→验证→提交）
- [Plan.md](Plan.md) / `plan.md` — 原始需求历史留档（文档类命名不一致，两者均存在）

## 许可证

MIT License