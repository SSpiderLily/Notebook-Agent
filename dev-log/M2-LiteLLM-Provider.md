# M2 LiteLLM Provider

## 交付内容

- LLMGateway 接入 LiteLLM 默认 Provider。
- 支持 OpenAI 兼容 API、可注入 transport 和模型配置切换。
- 网络错误、429、5xx 指数退避重试。
- 记录 latency、retries、token 和费用估算。
- 保持 LLMGateway 为唯一 LLM 出口。

## 验证结果

Provider、重试元数据和失败记录测试通过；真实 Provider 使用兼容端点完成单篇在线验收。

## 状态

- 完成（2026-09-01）
- 相关提交：`c84439b`

## 归属

- 进度看板：[[进度看板]]
