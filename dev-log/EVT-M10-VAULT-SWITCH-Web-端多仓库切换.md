# Web 端多仓库切换

- 事件 ID：EVT-M10-VAULT-SWITCH
- 阶段：M10
- 日期：2026-09-08
- 状态：completed

## 事件内容

新增持久化仓库注册表(VaultRegistry)与 /api/vaults，实现仓库登记/切换/移除与每仓库数据目录隔离；切换=替换 app.state.tasks，active run 时拒绝；前端新增"仓库"设置页。

## 验证

pytest tests/：149 passed；npm run build：成功；浏览器+API 验证注册/切换/隔离/预览读新仓库

## 提交

18eb21c

## 归属

- 进度看板：[[进度看板]]
