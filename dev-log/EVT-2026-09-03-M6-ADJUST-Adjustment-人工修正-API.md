# Adjustment 人工修正 API

- 事件 ID：EVT-2026-09-03-M6-ADJUST
- 阶段：M6
- 日期：2026-09-03
- 状态：completed

## 事件内容

新增 Adjustment ORM、事务化 set_status/retitle/move 修正、人工 reorg 记录、修正查询与撤销接口，并校验跨树父节点与循环。

## 验证

pytest tests/test_m6_adjustments_api.py -q: 3 passed; git diff --check: passed

## 提交

pending

## 归属

- 进度看板：[[进度看板]]
