# M9 产物回退与 SRS 验收闭环

- 事件 ID：EVT-M9-CLOSE-001
- 阶段：M9
- 日期：2026-09-07
- 状态：blocked

## 事件内容

新增产物版本查询/安全回退 API 与观测页入口；完成真实副本采集盘点和 replay 验收，因录制指纹与真实副本不一致形成环境阻塞；建立 FR/NFR 验收矩阵。

## 验证

pytest -q: 136 passed；frontend npm run build: passed；real-vault: 175 篇可采集，replay 抽取因录制指纹不匹配失败清单

## 提交

pending

## 归属

- 进度看板：[[进度看板]]
