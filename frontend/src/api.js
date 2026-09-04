// 统一 fetch 封装：前端与后端同源（FastAPI 静态托管），基路径固定 /api。
// 后端错误统一为 {code, message, detail}，这里归一化为带 code 的 Error。

async function request(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  let body = null
  const text = await res.text()
  if (text) {
    try { body = JSON.parse(text) } catch { body = null }
  }
  if (!res.ok) {
    const code = body?.code || 'http_error'
    const message = body?.message || `请求失败（HTTP ${res.status}）`
    const err = new Error(message)
    err.code = code
    err.detail = body?.detail
    err.status = res.status
    throw err
  }
  return body
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
  del: (path) => request(path, { method: 'DELETE' }),
}

// 便捷错误提示：把 code 映射为更友好的中文说明。
export function friendlyMessage(err) {
  const map = {
    forbidden_host: '仅允许本机访问',
    forbidden_origin: '来源不被允许',
    validation_error: '请求参数校验失败',
    internal_error: '服务内部错误',
    task_active: '已有任务在运行中',
    run_not_found: '任务不存在',
    run_not_active: '任务已不在运行',
    tree_not_found: '树不存在',
    adjustment_not_found: '修正记录不存在',
    writeback_job_not_found: '写回任务不存在',
    backup_not_found: '备份不存在',
    confirmation_required: '需要确认才能执行该操作',
    backup_verification_failed: '备份校验失败',
  }
  return map[err?.code] || err?.message || '未知错误'
}
