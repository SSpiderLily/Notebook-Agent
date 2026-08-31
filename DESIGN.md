# NoteAgent 概要设计（DESIGN.md）v1.1

> 依据：`REQUIREMENTS.md`（SRS v1.0）。本文档定义系统架构、数据模型、API 契约、Agent 设计与里程碑排期，是详细设计与实现的依据。
> 设计第一原则：**基础设施与可观测先行**——先搭好"能查、能续、能重放"的地基，再堆功能，避免后期出错无从查起、基础设施不稳导致流程级重试浪费。

## 一、设计原则

1. **可观测优先**：任何 LLM 调用、阶段状态、中间产物全量留痕，可回放、可追溯（NFR-2/3/4 的地基）
2. **故障隔离三级模型**：
   - **条目级**：单篇笔记/单个事件失败只进失败清单，不阻塞阶段（FR-2）
   - **阶段级**：阶段中断可从本阶段断点或上一阶段落盘产物恢复，不回退整条流水线（FR-7）
   - **运行级**：服务重启后从 SQLite 中的 run/stage 状态恢复任务（FR-8）
3. **LLM 唯一出口**：所有 LLM 调用必须经 `LLMGateway`，禁止散落的直连调用——重试/校验/台账/成本统计/录制回放只实现一次
4. **安全写抽象**：对 vault 的一切写操作走 `SafeWriter`（preview → diff → 确认 → 备份 → 原子写），不存在第二条写路径
5. **已验证结构只追加**（★追加原则）：用户确认过的树，增量运行只允许**追加**新节点，**绝不自动重组/拆分/移动**；重组必须人工触发。宁可保守，不可摧毁信任
6. **配置即代码**：全部路径/模型/排除项/费率/阈值经 pydantic-settings 集中管理，`.env.example` 与实现同步

## 二、系统架构

### 2.1 分层与目录

```
frontend/                     Vue 3 + Vite + Element Plus（展示层）
    │  HTTP / SSE
src/
├── api/                      FastAPI 路由层（薄，只做参数校验与调用 services）
│   ├── tasks.py              触发/试算/查询/取消任务，SSE 进度
│   ├── forest.py             森林/树查询与修正
│   ├── writeback.py          双写回预览/确认/备份恢复
│   ├── chat.py               问答会话
│   ├── observe.py            可观测查询（运行历史/LLM调用/失败清单）
│   └── app.py                应用工厂、本地防护中间件、静态托管
├── services/                 应用服务层（用例编排）
│   ├── pipeline.py           整理流水线（九阶段编排、断点续跑）
│   ├── task_manager.py       进程内异步任务管理（互斥/恢复/取消）
│   ├── reconcile.py          ★孤儿对账（vault 与库一致性）
│   ├── adjustment.py         Web 修正的持久化与应用
│   ├── writeback.py          双写回用例（标签/双链）
│   └── reset.py              重置与版本化
├── core/                     领域逻辑（沿用 Base* 抽象基类模式）
│   ├── extraction.py         提炼与事件抽取（LLM·抽取器）
│   ├── association.py        关联候选生成 + LLM 判定（判断器）
│   ├── tree_rebuild.py       树重建编排（调用 agents/，执行追加原则）
│   ├── status.py             状态判定与断头检测（判断器）
│   └── artifact.py           树页/森林总览产物生成（撰写者）
├── agents/                   两个真 Agent
│   ├── tree_builder.py       树重建 ReAct Agent（工具型）
│   ├── qa.py                 问答对话 Agent（记忆+指令集）
│   └── tools.py              Agent 工具定义（白名单）
├── data/                     数据访问层
│   ├── loader.py / parser.py # 采集与 Obsidian 解析（改造现有实现）
│   ├── vector_store.py       Chroma 实现（实现现有 BaseVectorStore，含模型变更检测）
│   └── repositories/         SQLAlchemy 仓储（notes/events/trees/...）
├── infra/                    ★ 基础设施（M0，被所有层依赖）
│   ├── config.py             pydantic-settings（路径/模型/排除/忽略/费率/阈值）
│   ├── logging.py            loguru 结构化日志（run_id/stage 绑定，轮转）
│   ├── run_manager.py        Run/阶段状态机（SQLite）
│   ├── stage_io.py           阶段中间产物落盘（data/runs/<run_id>/，带 schema 版本）
│   ├── llm_gateway.py        LLM 唯一出口：重试/校验/台账/成本/录制与回放
│   ├── backup.py             时间戳备份（保留 N 次）
│   └── safe_writer.py        vault 安全写（diff/原子写/双链转义）
├── models/                   pydantic schema + SQLAlchemy ORM 模型
│   ├── orm.py                表定义
│   └── schemas.py            API/内部 DTO
└── config/                   （保留现有，逐步并入 infra/config.py）
```

依赖方向：`api → services → (core | agents) → data → infra`；`infra` 为横切层，任何人可用，但不依赖业务层。

### 2.2 运行时视图

```
浏览器(Vue) ──HTTP──▶ FastAPI(uvicorn, 仅127.0.0.1 + Host/Origin校验)
                        │
                        ├─ TaskManager（asyncio 后台任务，进程内）
                        │     └─ Pipeline（九阶段，读写 StageIO/RunManager）
                        │           ├─ LLMGateway ──▶ DeepSeek/OpenAI兼容API（或离线回放）
                        │           ├─ Chroma（data/chroma/）
                        │           └─ SQLite（data/noteagent.db）
                        └─ QA Agent（常驻，按会话）
vault（Obsidian 仓库）──只读扫描──▶ Pipeline ＋ Reconciler（对账）
vault ◀──SafeWriter（仅双写回）── writeback 服务
产物 ──▶ vault/_noteagent/（版本化）
```

## 三、基础设施与可观测体系（M0 详解）

### 3.1 Run 与阶段状态机（`infra/run_manager.py`）

- 每次整理任务 = 一个 **Run**（UUID）；九阶段固定枚举：`init / collect / extract / associate / tree_rebuild / status_judge / artifact / (confirm*) / writeback*`（confirm 为 Web 人工环节，不占运行态）
- `stages.status`: `pending → running → done | failed | skipped`；条目计数 `items_total/done/failed` 实时更新
- **断点续跑**：新 Run 启动时读取上一 Run 的阶段状态与 StageIO 产物，已完成阶段直接复用（skipped），失败/中断阶段从断点重入
- **互斥**：`runs` 表以 `running` 状态行做互斥锁，运行中触发返回 409
- **scope 字段**：Run 记录整理范围（全仓库或指定子目录），支持试跑与正式跑区分

### 3.2 LLMGateway（`infra/llm_gateway.py`）

所有 LLM 调用唯一出口，两种调用模式：

- `chat(prompt) -> str`：自由文本（撰写者/问答用）
- `structured(prompt, schema) -> BaseModel`：JSON 结构化（抽取器/判断器/Agent 用）——失败自动修复重试（最多 N 次），仍失败抛 `LLMFormatError`（**条目级失败，不重试整阶段**）

内置策略：

- 重试：仅网络错误/429/5xx 指数退避；**schema 错误不属于可重试错误**（修复式重试单独计数）——直接回应"基础设施不稳导致流程级错误重试"的担忧
- 超时、并发信号量（默认 4）、每 Run 成本上限护栏（超限暂停任务并标记，Web 端可见）
- 底层用 langchain-openai 的 `ChatOpenAI`（兼容 DeepSeek/通义千问，配置切换）

**调用台账**：每次调用记录 `run_id, stage, caller, model, prompt_tokens, completion_tokens, cost_est, latency_ms, retries, status`；完整 prompt/response 存 `data/runs/<run_id>/llm/<call_id>.json`——**任何"为什么这样判定"都能回放现场**。

**★录制/回放模式**（replay）：

- `RECORD`（默认）：真实调用并落台账
- `REPLAY`：按调用指纹（prompt+schema 哈希）从台账库匹配已录制响应，离线返回——不花钱、确定性、无网络
- 匹配不到时按配置决定：报错（CI 严格模式）或转真实调用（开发宽松模式）
- **pytest 的 LLM 桩与它是同一套机制**：测试夹具就是一份录制好的台账目录

### 3.3 阶段中间产物（`infra/stage_io.py`）

- 每阶段输出 JSON 至 `data/runs/<run_id>/stages/<stage>.json`，带 `schema_version` 字段，读取时校验兼容性
- 用途：阶段重放（单独重跑某阶段而不重跑之前）、断点恢复、Web 观测页展示中间结果

### 3.4 结构化日志（`infra/logging.py`）

- loguru：JSON 行落 `logs/noteagent.log`（按大小轮转，保留 N 份）+ 控制台人类可读格式
- 每 Run 独立日志 `logs/runs/<run_id>.log`，整次运行可独立检索

### 3.5 备份与安全写（`infra/backup.py` / `safe_writer.py`）

- `BackupManager`：写回前将目标文件复制到 `data/backups/<时间戳>/`（保留最近 N 次可配置）
- `SafeWriter`：`preview()` 产出逐文件 diff → `apply()` 备份 + 临时文件原子替换；幂等（重复 apply 检测无变化则跳过）
- **双链转义**：生成 `[[链接]]` 时处理文件名中的特殊字符（`[]|#` 等）与空格，必要时用 `[[路径|别名]]` 形式

### 3.6 Web 观测页（对应 `/api/observe/*`）

- 运行历史列表（状态/耗时/费用/scope）
- 阶段时间线（九阶段状态与条目计数）
- **LLM 调用浏览器**：按 run/stage/状态筛选，点开看完整 prompt/response
- 失败清单（条目级错误汇总）
- 孤儿/失效笔记报告（对账结果）

### 3.7 本地服务防护（★安全）

- 服务绑定 `127.0.0.1` 之外，增加 **Host/Origin 校验中间件**：`Host` 头必须是 `127.0.0.1:<port>` / `localhost:<port>` 白名单；带 `Origin` 头的请求（跨站）一律 403——防恶意网页/DNS rebinding 打本地接口（写回接口能改笔记，必须防）
- 破坏性端点（`/api/reset`、写回 confirm、备份恢复）要求请求体带 `confirm: true` 显式确认字段

## 四、数据模型

### 4.1 SQLite 表（SQLAlchemy 2.0 + Alembic 迁移）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `notes` | id(稳定hash), path, folder, filename, mtime, content_hash, parse_status, **vault_status(active/missing/ignored)**, last_run_id | 采集登记、增量判断、**对账状态与忽略标记** |
| `extractions` | note_id, run_id, title, summary, keywords, candidate_tags, raw_json, model | FR-2 提炼结果（按 run 保留历史，取最新有效） |
| `events` | id, note_id, extraction_id, content, time_clue, status_clue, order_in_note | 从笔记拆出的事件（树的原子节点） |
| `associations` | id, src_type, src_id, dst_id, basis(folder/naming/semantic/temporal), confidence, evidence, run_id | FR-3 带证据关联 |
| `trees` | id, root_note_id, title, status(complete/in_progress/dangling_confirmed/dangling_suspected), confidence, **verified**, **locked(=verified，追加原则标记)**, evidence, narrative | FR-4/5 草稿森林与状态 |
| `tree_nodes` | tree_id, event_id, note_id, parent_id, order, confidence, evidence, **origin(agent/human)** | 树结构；human 节点受追加原则保护 |
| `adjustments` | id, target_type, target_id, action(move/merge/split/set_status/retitle/reorg), payload_json, applied_run_id | FR-9 Web 修正记录（持久化、可撤销；reorg=人工触发的重组） |
| `runs` | id, status, **scope**, trigger, started_at, finished_at, cost_est | Run 主表 |
| `stages` | run_id, stage, status, items_total/done/failed, error, checkpoint_path | 阶段状态机 |
| `llm_calls` | id, run_id, stage, caller, model, tokens, cost_est, latency_ms, retries, status, digest, **replay(bool)** | LLM 台账 |
| `sessions` / `messages` | 会话与多轮消息 | FR-13 问答记忆 |
| `writeback_jobs` / `writeback_items` | job(kind=tags/links, status), item(note_id, diff_json, applied) | FR-10 预览与确认 |
| `artifacts` | version, kind(tree_page/overview), tree_id, path, run_id | FR-11 产物版本化 |

时间字段统一存本地时区 ISO8601 字符串。

### 4.2 Chroma 集合

- `notes`：embedding 文本 = 提炼摘要 + 关键词 + 文件夹路径（元数据带 note_id/folder）
- `events`：embedding 文本 = 事件内容 + 时间线索（树重建 Agent 检索用）
- **★模型变更检测**：collection 元数据记录 embedding 模型名；启动与写入前校验，与当前配置不符即拒绝写入并提示重建（提供重建命令，附重嵌入数量与费用预估）——防止切换模型后相似度静默失效

### 4.3 文件布局

```
vault/
├── **/*.md                   原始笔记（只读，除双写回；可被 noteagent:ignore 排除）
├── _noteagent/               全部生成物（版本化子目录 v1/ v2/ ...）
│   ├── trees/<tree>.md
│   ├── overview.md
│   └── v<N>/...              历史版本
data/
├── noteagent.db              SQLite
├── chroma/                   向量库（元数据含模型指纹）
├── runs/<run_id>/            阶段产物 + LLM 全量记录
├── llm_recordings/           ★跨 run 的回放录制库（按指纹索引）
└── backups/<时间戳>/          写回备份
logs/  (noteagent.log, runs/<run_id>.log)
```

### 4.4 忽略机制（★）

优先级从高到低：笔记 frontmatter `noteagent: ignore` > 配置 glob（`IGNORE_PATHS`，如 `私有/**`、`**/日记-*.md`）> 目录排除（`EXCLUDE_DIRS`）。被忽略笔记入 `notes.vault_status=ignored`，不参与提炼与建树，但保留在库中可查。

## 五、API 契约（v1 摘要）

| 域 | 端点 | 说明 |
|---|---|---|
| 任务 | `POST /api/tasks/preview`（★只扫描试算：篇数/字数/预估费用/预估耗时）· `POST /api/tasks/run`（可选 `scope` 子目录试跑；运行中 409）· `GET /api/tasks/current` · `GET /api/tasks/{run_id}` · `GET /api/tasks/{run_id}/stream`（SSE 进度）· `POST /api/tasks/{run_id}/cancel` | FR-8/9 |
| 森林 | `GET /api/forest?status=&min_confidence=` · `GET /api/trees/{id}` · `GET /api/trees/{id}/timeline` · `POST /api/trees/{id}/reorg`（★人工触发的重组） | FR-12 |
| 修正 | `POST /api/trees/{id}/adjust` · `GET/DELETE /api/adjustments[/{id}]` · `POST /api/trees/{id}/regenerate` | FR-9 |
| 写回 | `POST /api/writeback/preview` · `GET /api/writeback/jobs/{id}` · `POST /api/writeback/jobs/{id}/confirm` · `GET /api/writeback/backups` · `POST /api/writeback/backups/{id}/restore` | FR-10 |
| 重置 | `POST /api/reset` · `GET /api/artifacts/versions` · `POST /api/artifacts/rollback` | FR-11 |
| 问答 | `POST /api/chat/sessions` · `POST/GET /api/chat/sessions/{id}/messages` · `DELETE /api/chat/sessions/{id}`（清除上下文） | FR-13 |
| 观测 | `GET /api/observe/runs[/{id}]` · `GET /api/observe/llm-calls[/{id}]` · `GET /api/observe/failures` · `GET /api/observe/vault-status`（★孤儿/忽略报告） | §3.6 |
| 笔记 | `GET /api/notes?query=&status=` · `GET /api/notes/{id}` | 辅助 |

约定：全部 JSON；错误统一 `{code, message, detail}`；SSE 事件 `{stage, status, items_done, items_total, cost_est}`；仅绑 127.0.0.1 + Host/Origin 校验；破坏性端点要求 `confirm: true`。

## 六、Agent 设计

### 6.1 树重建 Agent（`agents/tree_builder.py`，ReAct）

- **输入**：未挂接的事件批次（含其 note 摘要/时间线索/文件夹/命名信号）；已验证树的只读快照
- **工具白名单**（`agents/tools.py`）：
  1. `search_candidate_trees(query)`：向量+文件夹+命名检索候选树（含 verified 标记），返回树摘要
  2. `read_note(note_id)`：回读笔记原文（截断保护）
  3. `get_tree_timeline(tree_id)`：该树现有节点与时间线
  4. `search_events(query)`：事件库语义检索
  5. `submit_assignment(tree_id | NEW, parent_event_id?, confidence, evidence)`：提交判定（终态工具）
- **追加原则的执行**：Agent 对 `verified=true` 的树只能提交"追加叶子"决策；任何移动/拆分/改父级的决策会被网关层拒绝并记录为建议，进人工复核队列
- **输出**：挂接决策（含置信度与证据）或"新建树"决策；置信度 < 0.6 自动标记待人工复核
- **护栏**：最大 12 步/事件、单事件超时、工具白名单硬编码、单 Run 成本上限（LLMGateway 统一执行）
- **实现**：LangChain tool-calling agent（`create_tool_calling_agent`），底层模型经 LLMGateway

### 6.2 问答 Agent（`agents/qa.py`，对话型）

- **工具**：`search_forest(query)`（笔记+事件混合检索）· `get_tree(id|title)` · `list_dangling()` · `list_recent_runs()`
- **记忆**：`sessions/messages` 表持久化；上下文窗口 = 最近 N 轮原文 + 更早轮次滚动摘要
- **指令集**：`/clear`（清除上下文）· `/regen`（重新生成上一回答）· `/export`（导出会话 markdown）
- **溯源**：system prompt 强制回答附引用块（笔记标题/树标题），前端渲染为 `obsidian://` 跳转链接

## 七、前端页面（Vue 3 + Element Plus）

| 页面 | 内容 |
|---|---|
| 森林总览 | 树卡片列表，状态/置信度筛选，断头优先展示 |
| 树详情 | 路径结构（时间线视图）、节点证据、综述、`obsidian://` 跳转、修正操作 |
| 确认工作台 | 低置信队列 + 修正（移动/合并/拆分/改状态）、重组建议队列（追加原则拦截项）、修正历史 |
| 任务页 | **试算报告卡（触发前展示）**、触发整理（可 选子目录试跑）、九阶段进度时间线、失败清单、费用 |
| 写回页 | 逐文件 diff 预览、确认、备份恢复 |
| 观测页 | 运行历史、LLM 调用浏览器、失败清单、vault 状态报告（孤儿/忽略） |
| 聊天页 | 多轮对话、指令按钮、引用跳转 |

技术：Vite + Vue 3 + Element Plus；TypeScript 起步，嫌重可降 JS（开发中定）。

## 八、里程碑排期

| # | 里程碑 | 内容 | 验收 |
|---|---|---|---|
| **M0** | **基础设施与可观测** | config/.env.example（含费率表/忽略 glob）、日志+轮转、RunManager 状态机、StageIO（schema版本）、LLMGateway（台账+**录制/回放**+成本护栏）、BackupManager/SafeWriter（双链转义）、Host/Origin 中间件、pytest 脚手架+样例仓库 fixture、FastAPI 骨架+最小任务页(SSE) | 假任务验证：阶段状态落库、SSE 进度、台账可查、**回放模式离线重放成功**、中断后可恢复、非本机请求 403、备份可回滚 |
| M1 | 采集/解析/对账 | 递归扫描、frontmatter(YAML)/中文嵌套标签解析、稳定 ID+内容哈希、排除+**忽略机制（frontmatter/glob）**、变更清单、**孤儿对账（missing/ignored 标记+报告）**、**试算报告（篇数/字数/费用/时长预估）** | 样例仓库扫描齐全；改名/删除笔记后对账正确；pytest 覆盖解析与对账；试算数字合理 |
| M2 | 提炼与事件抽取 | LLM 批量抽取（经 Gateway）、失败清单、增量缓存、events 入库 | 样例仓库全量提炼；重跑零调用；失败项隔离；**用回放夹具离线测试** |
| M3 | 向量与关联 | Chroma 集合、**模型变更检测**、候选生成（文件夹/命名/向量/时间）、LLM 判定入 associations | 关联带证据可查；**改模型配置后被检测拦截** |
| M4 | 树重建 Agent + 状态判定 | ReAct Agent、草稿森林、**追加原则执行**、状态/断头判定 | 样例仓库树结构人工核对可接受；断头案例被识别；**verified 树在增量运行中未被自动重组** |
| M5 | 产物生成 | 树页/森林总览 markdown、版本化、`_noteagent/` 写入 | Obsidian 中双链可跳转；特殊文件名笔记链接正确 |
| M6 | Web 确认工作台 | 森林/树/工作台页面 + 修正 API + 重组建议队列 | 修正持久化并触发局部重生成 |
| M7 | 双写回 | 标签/双链预览、确认、备份恢复 | diff 与实际一致；幂等；备份可恢复 |
| M8 | 问答 Agent | 聊天页、会话记忆、指令集、引用跳转 | 多轮连贯；指令生效；可溯源 |
| M9 | 观测页完善 + 收尾 | LLM 调用浏览器、vault 状态报告、重置/版本管理、真实副本验收 | 全部 SRS 验收要点通过 |

依赖：M0 → M1 → M2 → M3 → M4 → M5 → (M6/M7/M8 可并行) → M9。每个里程碑完成即更新 README 进度表并 git 提交。

## 九、技术选型汇总

- **后端**：Python 3.12、FastAPI、uvicorn、SQLAlchemy 2.0、Alembic、pydantic v2、pydantic-settings、loguru、langchain + langchain-openai、chromadb、PyYAML、httpx
- **测试**：pytest、pytest-asyncio；核心模块全覆盖（infra/解析/对账/树构建/写回安全），LLM 一律走回放夹具
- **前端**：Vite、Vue 3、Element Plus
- **环境管理**：建议 uv（或 venv+pip）；requirements.txt 同步维护
- **新增依赖需进 requirements.txt 并在本文档说明用途**

## 十、设计决策记录

| 日期 | 决策 | 理由 |
|---|---|---|
| 08-31 | 可观测=全套+Web 观测页（台账/时间线/调用浏览器） | 用户明确要求基础设施与可观测先行 |
| 08-31 | SQLAlchemy 2.0 + Alembic | 工业标准，学习价值；表结构演进有迁移保障 |
| 08-31 | 长任务=进程内 asyncio（不用 Celery/Redis） | 本地单用户；状态落 SQLite 即满足恢复/互斥；降低运维成本 |
| 08-31 | 前端 Element Plus | 中文生态最全，"简单展示"定位下查资料成本最低 |
| 08-31 | LLM 唯一出口 Gateway；schema 错误不算可重试错误 | 防止流程级错误重试烧钱（用户核心担忧） |
| 08-31 | 追加原则：已验证树只追加不自动重组 | 增量运行不得破坏用户已确认的结构（信任保护） |
| 08-31 | 孤儿对账 + 忽略机制（frontmatter/glob） | vault 是活的：删除/改名必须对账；日记等需笔记级排除 |
| 08-31 | Host/Origin 校验 + 破坏性端点 confirm 字段 | 写回接口可改笔记，防恶意网页打本地接口 |
| 08-31 | 首跑试算 + 子目录试跑 | 触发前知道规模与费用，避免盲跑 |
| 08-31 | LLM 录制/回放模式，与 pytest 桩同源 | 调试免费、结果可复现、CI 无网络依赖 |
| 08-31 | 向量库模型指纹校验 | 切换 embedding 模型后相似度静默失效的防护 |
