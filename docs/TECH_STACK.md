# NoteAgent 技术选型

> 状态：已确认（2026-09-01）

## 总体原则

业务代码不直接依赖供应商 SDK。所有模型调用经过项目自己的 `LLMGateway`，由 Gateway 统一处理 Run/Stage 归属、成本护栏、台账和录制回放。

## 选型表

| 领域 | 选型 | 使用边界 | 选择理由 |
|---|---|---|---|
| LLM Provider | LiteLLM | Gateway 的底层模型适配与调用 | 统一 DeepSeek、通义千问及其他 OpenAI-compatible/主流供应商接口 |
| 项目调用边界 | `src/infra/llm_gateway.py` | 所有抽取、判断、Agent、问答调用 | 绑定 run/stage/caller，执行项目级成本、重试、审计和回放策略 |
| Agent 编排 | LangGraph | 树重建 Agent、问答 Agent | 显式状态图、节点和恢复边界更适合长流程与多轮会话 |
| 结构化输出 | Pydantic v2 | 抽取器、判断器、Agent 工具结果 | schema 明确、校验结果可持久化，兼容现有模型层 |
| 重试 | LiteLLM 能力 + 项目策略 | 仅网络错误、429、5xx | 避免业务层散落重复重试；结构化校验错误单独处理 |
| 本地回放 | 自建 ReplayStore | 测试、离线开发、CI | 不依赖网络，按模型+prompt+schema 指纹复现结果 |
| 调用观测 | SQLite/JSON 本地记录 | 必选运行能力 | 支持断点、审计、Web 查询和离线恢复；不依赖外部平台 |
| 外部观测 | Langfuse（后续可选） | Prompt 调试和质量分析 | 只作增强，不作为系统运行依赖 |
| Web 后端 | FastAPI + Uvicorn | 本地 API、SSE | 与异步长任务和本地服务形态匹配 |
| 持久化 | SQLite + SQLAlchemy 2.0 + Alembic | 状态、台账、会话和业务数据 | 单用户本地部署简单，支持迁移和恢复 |
| 向量库 | Chroma | notes/events 检索 | 本地持久化、依赖简单，符合当前设计 |
| 前端 | Vue 3 + Vite + Element Plus | 展示与确认工作台 | 中文组件生态成熟，适合简单本地 Web 展示层 |

## 关键边界

- LangGraph 负责 Agent 状态图，不负责取代 RunManager、StageIO 或 SafeWriter。
- LiteLLM 负责供应商适配，不负责取代项目级 Gateway 和本地台账。
- 外部观测平台不能成为离线回放或任务恢复的前置条件。
- 业务层禁止直接实例化 `ChatOpenAI`、供应商 SDK 或 LiteLLM client。

## 替换策略

替换 Provider 只应新增/替换 `src/infra/llm/` 下的适配器，不改变抽取器、Agent 和 API 的调用契约。替换 Agent 框架仅影响 `src/agents/`，不改变 Gateway、RunManager 和 StageIO。

## 分阶段引入

1. M0/M1：固定 Gateway、ReplayStore、Pydantic 契约；继续使用离线回放测试。
2. M2：在 Gateway 内接入 LiteLLM 真实 Provider，补 token/费用台账和网络重试。
3. M4/M8：用 LangGraph 实现树重建和问答状态图。
4. M9：评估是否接入 Langfuse，不影响本地运行闭环。
