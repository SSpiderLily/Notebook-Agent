# M9 / SRS 验收矩阵

日期：2026-09-07

## 功能需求

| 要求 | 证据 | 结果 |
|---|---|---|
| FR-1 采集 | `tests/test_m1_collection.py`；真实副本可采集 175 篇 Markdown | 通过 |
| FR-2 抽取 | `tests/test_pipeline.py`、M2 重试测试；失败清单 | 通过 |
| FR-3 关联 | `tests/test_m3_associate.py`、`test_m3_association_reduce.py` | 通过 |
| FR-4 树重建 | `tests/test_m4_tree_rebuild.py` | 通过 |
| FR-5 状态/断头 | `tests/test_core_tree_status.py` | 通过 |
| FR-6 产物 | `tests/test_artifact.py` | 通过 |
| FR-7 增量 | M1/M2/M3 增量与跨 Run 缓存测试 | 通过 |
| FR-8 异步 Web | `tests/test_api.py`、SSE/互斥测试 | 通过 |
| FR-9 确认修正 | `tests/test_m6_adjustments_api.py` | 通过 |
| FR-10 双写回 | `tests/test_m7_writeback.py` | 通过 |
| FR-11 重置/版本回退 | `tests/test_m9_observe.py`、`test_m9_artifact_rollback.py`；新增 `/api/artifacts/*` | 通过 |
| FR-12 前端展示 | `tests/test_m8_frontend.py`；`npm run build` | 通过 |
| FR-13 问答 | M8 Chat API/前端测试 | 通过 |

## 非功能需求

| 要求 | 证据 | 结果 |
|---|---|---|
| NFR-1 原文安全 | SafeWriter/Reset/Writeback 测试；真实副本仅 replay | 通过 |
| NFR-2 可恢复/幂等 | RunManager、StageIO、Artifact、Writeback 测试 | 通过 |
| NFR-3 透明性 | 证据/置信度字段与森林工作台测试 | 通过 |
| NFR-4 编排护栏 | Agent 工具白名单、步数与成本测试 | 通过 |
| NFR-5 成本 | 成本上限与调用台账测试 | 通过 |
| NFR-6 性能 | 关联候选门槛/缓存优化；未做 1000 篇实测 | 部分通过 |
| NFR-7 易用性 | Vue 工作台、聊天与观测页构建 | 通过 |
| NFR-8 可维护性 | 配置透传、分层模块、全量回归 | 通过 |
| NFR-9 本机边界 | Host/Origin 中间件测试 | 通过 |
| NFR-10 可测试性 | 全量 pytest 136 passed | 通过 |

## 真实副本验收

- `real-vault`：发现 175 篇 Markdown，可正常采集。
- 使用 `data-accept/llm_recordings` 对 `real-vault` 执行 replay：抽取阶段失败，原因是录制内容哈希与真实副本不一致；系统正确生成失败清单并停止后续阶段，未修改原始笔记。该项标记为**环境阻塞**，不是功能通过。
- 全量代码测试：136 passed，1 个既有 Chroma embedding warning。
- 前端生产构建：通过；仅有 bundle size warning。
