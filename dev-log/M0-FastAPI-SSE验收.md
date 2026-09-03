# M0 FastAPI / SSE 验收

## 交付内容

- FastAPI 应用工厂与本机 Host/Origin 安全中间件。
- 任务接口：preview、run、current、detail、cancel。
- SSE 阶段进度流：状态、阶段、完成数、总数和费用。
- TaskManager 负责进程内异步任务、Run 预创建和协作式取消。
- 统一请求校验和脱敏错误响应。

## 验证结果

API 测试覆盖健康检查、Host/Origin 拒绝、任务互斥、预览、取消和 SSE 响应。

## 当前边界

当前仅提供任务基础 API；森林、观测、写回、聊天等完整业务路由按后续 M6～M9 实施。

## 状态

- 完成（2026-09-02）

## 归属

- 进度看板：[[进度看板]]
