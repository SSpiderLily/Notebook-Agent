# M3 关联判定调用量优化完成

- 事件 ID：EVT-M3-ASSOC-REDUCE-001
- 阶段：M3
- 日期：2026-09-07
- 状态：completed

## 事件内容

新增关联候选门槛、关键词相交与可选语义距离阈值；增加跨 Run 判定缓存，输入内容或模型未变化时复用历史结果并跳过 LLM；补充 SQLite 旧库幂等增列迁移与配置透传。

## 验证

pytest tests/test_m3_association_reduce.py -q: 4 passed; pytest -q: 134 passed（1 个既有 Chroma embedding 警告）；git diff --check: passed

## 提交

pending

## 归属

- 进度看板：[[进度看板]]
