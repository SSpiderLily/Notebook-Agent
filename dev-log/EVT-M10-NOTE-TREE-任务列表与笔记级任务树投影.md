# 任务列表与笔记级任务树投影

- 事件 ID：EVT-M10-NOTE-TREE
- 阶段：M10
- 日期：2026-09-08
- 状态：completed

## 事件内容

新增笔记级树投影与任务进度统计；森林页改为任务列表，新增任务树页面展示笔记父子关系，并保留确认工作台的事件级结构。

## 验证

pytest tests/：142 passed；frontend npm run build：成功；浏览器验证 /#/forest 与 /#/tree/:id

## 提交

cd5597a

## 归属

- 进度看板：[[进度看板]]
