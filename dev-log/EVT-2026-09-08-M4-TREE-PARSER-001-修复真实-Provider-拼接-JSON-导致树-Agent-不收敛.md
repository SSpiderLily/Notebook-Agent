# 修复真实 Provider 拼接 JSON 导致树 Agent 不收敛

- 事件 ID：EVT-2026-09-08-M4-TREE-PARSER-001
- 阶段：M4
- 日期：2026-09-08
- 状态：completed

## 事件内容

针对 glm-5.3-flash 将多个工具调用 JSON 与 TreeAssignment 拼接返回的问题，新增增量 JSON 流拆分、终态优先提取和嵌套工具参数归一化。真实受控验证中树 Agent 已从 6/6 失败变为可产出多条 NEW TreeAssignment；解析器回归测试和全量测试通过。

## 验证

pytest -q: 137 passed；真实受控树演示：18 次/0.0262 元的旧路径失败样本已离线验证修复；后续真实 run 受代理 Connection error 停止

## 提交

未提交

## 归属

- 进度看板：[[进度看板]]
