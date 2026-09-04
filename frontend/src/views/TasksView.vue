<script setup>
import { ref, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { api, friendlyMessage } from '../api.js'

const scope = ref('')
const preview = ref(null)
const run = ref(null)
const previewLoading = ref(false)
const runLoading = ref(false)

let es = null

const STAGE_ORDER = ['init', 'collect', 'extract', 'associate', 'tree_rebuild', 'status_judge', 'artifact', 'writeback']
const STAGE_LABEL = {
  init: '初始化', collect: '采集', extract: '事件抽取', associate: '关联推断',
  tree_rebuild: '树重建', status_judge: '状态判定', artifact: '产物生成', writeback: '双写回',
}

async function doPreview() {
  previewLoading.value = true
  try {
    preview.value = await api.post('/tasks/preview', scope.value ? { scope: scope.value } : {})
  } catch (e) {
    ElMessage.error(friendlyMessage(e))
  } finally {
    previewLoading.value = false
  }
}

async function doRun() {
  try {
    run.value = await api.post('/tasks/run', scope.value ? { scope: scope.value } : {})
    ElMessage.success(`任务已启动：${run.value.id}`)
    startStream(run.value.id)
  } catch (e) {
    ElMessage.error(friendlyMessage(e))
  }
}

async function doCancel() {
  if (!run.value) return
  try {
    await api.post(`/tasks/${run.value.id}/cancel`)
    ElMessage.info('已请求取消')
  } catch (e) {
    ElMessage.error(friendlyMessage(e))
  }
}

function startStream(runId) {
  es?.close()
  // EventSource 默认走 /api 前缀之外的根路径，这里手动拼到本服务（同源）。
  es = new EventSource(`/api/tasks/${runId}/stream`)
  es.onmessage = (ev) => {
    let data
    try { data = JSON.parse(ev.data) } catch { return }
    if (data.event === 'done') {
      ElMessage.info(`任务结束：${data.status}`)
      es?.close(); es = null
      refreshRun()
      return
    }
    // 阶段进度 diff 推送
    if (run.value && data.stage) {
      const found = run.value.stages.find((s) => s.stage === data.stage)
      if (found) {
        found.status = data.status
        found.items_done = data.items_done
        found.items_total = data.items_total
        found.items_failed = data.items_failed
      }
    }
  }
  es.onerror = () => { es?.close(); es = null }
}

async function refreshRun() {
  try {
    const cur = await api.get('/tasks/current')
    if (cur) run.value = cur
  } catch { /* 无运行 */ }
}

onBeforeUnmount(() => es?.close())
refreshRun()
</script>

<template>
  <div>
    <el-card class="page-card" shadow="never">
      <div class="toolbar">
        <el-input v-model="scope" placeholder="可选子目录 scope（例如 notes/股市）" style="width: 240px" clearable />
        <el-button type="primary" :loading="previewLoading" @click="doPreview">试算</el-button>
        <el-button type="success" :loading="runLoading" @click="doRun">开始运行</el-button>
        <el-button v-if="run && run.status === 'running'" type="danger" plain @click="doCancel">取消</el-button>
      </div>

      <el-descriptions v-if="preview" title="试算报告" :column="4" border>
        <el-descriptions-item label="笔记数">{{ preview.notes }}</el-descriptions-item>
        <el-descriptions-item label="字符数">{{ preview.characters }}</el-descriptions-item>
        <el-descriptions-item label="预计调用">{{ preview.calls }}</el-descriptions-item>
        <el-descriptions-item label="预计费用(元)">{{ preview.estimated_cost_cny }}</el-descriptions-item>
        <el-descriptions-item label="预计耗时(分钟)">{{ preview.estimated_minutes }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card v-if="run" class="page-card" shadow="never">
      <template #header>
        <span>运行 <code>{{ run.id }}</code> · {{ run.status }}</span>
        <el-tag v-if="run.cost_est" style="margin-left: 8px">费用 ≈ {{ run.cost_est }} 元</el-tag>
      </template>
      <el-table :data="STAGE_ORDER.map((st) => {
        const s = run.stages.find((x) => x.stage === st)
        return { stage: st, label: STAGE_LABEL[st] || st, ...(s || {}) }
      })" size="small">
        <el-table-column prop="label" label="阶段" width="120" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.status === 'done' ? 'success' : (row.status === 'running' ? 'primary' : (row.status === 'failed' ? 'danger' : 'info'))">{{ row.status || 'pending' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="160">
          <template #default="{ row }">
            <el-progress
              v-if="row.items_total"
              :percentage="Math.round((row.items_done / row.items_total) * 100)"
              :status="row.status === 'failed' ? 'exception' : undefined"
            />
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="完成/失败" width="120">
          <template #default="{ row }">
            {{ row.items_done || 0 }} / <span style="color: #f56c6c">{{ row.items_failed || 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="error" label="错误">
          <template #default="{ row }">
            <span v-if="row.error" style="color: #f56c6c; font-size: 12px">{{ row.error }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-empty v-else description="尚无运行任务，点击「开始运行」启动采集→森林→产物流程" />
  </div>
</template>