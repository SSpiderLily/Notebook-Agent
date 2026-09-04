<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, friendlyMessage } from '../api.js'

const job = ref(null)
const loading = ref(false)
const backups = ref([])
const backupsVisible = ref(false)

const KIND_LABEL = { tags: '标签回写', links: '双链写回' }
const JOB_STATUS_META = {
  previewed: { label: '待确认', type: 'info' },
  applied: { label: '已应用', type: 'success' },
  partially_applied: { label: '部分应用', type: 'warning' },
  failed: { label: '失败', type: 'danger' },
}

async function preview(kind) {
  loading.value = true
  try {
    job.value = await api.post('/writeback/preview', { kind })
    ElMessage.success(`生成 ${kind} 预览：${job.value.count} 篇笔记需要写回`)
  } catch (e) {
    ElMessage.error(friendlyMessage(e))
  } finally {
    loading.value = false
  }
}

async function refreshJob() {
  if (!job.value) return
  try { job.value = await api.get(`/writeback/jobs/${job.value.id}`) } catch (e) { ElMessage.error(friendlyMessage(e)) }
}

async function confirmJob() {
  if (!job.value) return
  try {
    await ElMessageBox.confirm(
      `确认执行${KIND_LABEL[job.value.kind]}？将只增不删地写入 vault，并生成时间戳备份。`,
      '写回确认', { type: 'warning', confirmButtonText: '确认写回' },
    )
    await api.post(`/writeback/jobs/${job.value.id}/confirm`, { confirm: true })
    ElMessage.success('写回完成')
    await refreshJob()
  } catch (e) {
    if (e === 'cancel' || e?.message === 'cancel') return
    ElMessage.error(friendlyMessage(e))
  }
}

async function loadBackups() {
  try { backups.value = (await api.get('/writeback/backups')).backups } catch (e) { ElMessage.error(friendlyMessage(e)) }
}
function openBackups() { loadBackups(); backupsVisible.value = true }

async function restoreBackup(row) {
  try {
    await ElMessageBox.confirm('将备份文件恢复到原始笔记，当前内容会被覆盖？此操作需确认。', '恢复备份', {
      type: 'warning', confirmButtonText: '确认恢复',
    })
    const res = await api.post(`/writeback/backups/${row.id}/restore`, { confirm: true })
    ElMessage.success(`已恢复 ${res.count} 个文件`)
  } catch (e) {
    if (e === 'cancel' || e?.message === 'cancel') return
    ElMessage.error(friendlyMessage(e))
  }
}

onMounted(() => {
  // 最近一个任务页运行后有意留空；避免误签历史 job。
  api.get('/writeback/backups').then(() => {}).catch(() => {})
})
</script>

<template>
  <div>
    <el-card class="page-card" shadow="never">
      <div class="toolbar">
        <el-button type="primary" :loading="loading" @click="preview('tags')">标签回写 · 预览</el-button>
        <el-button type="success" :loading="loading" @click="preview('links')">双链写回 · 预览</el-button>
        <el-button @click="openBackups">备份管理</el-button>
        <span class="muted">仅对已确认（verified）的树执行双链写回；写回前请先人工确认森林</span>
      </div>
    </el-card>

    <el-card v-if="job" class="page-card" shadow="never">
      <template #header>
        <span>预览任务 #{{ job.id }} · {{ KIND_LABEL[job.kind] }}</span>
        <el-tag :type="JOB_STATUS_META[job.status]?.type || 'info'" style="margin-left: 8px">{{ JOB_STATUS_META[job.status]?.label || job.status }}</el-tag>
        <span class="muted" style="margin-left: 8px">{{ job.itemCount ? '' : '' }}共 {{ job.items?.length || 0 }} 项 · 创建于 {{ job.created_at }}</span>
      </template>

      <div class="toolbar" v-if="job.status !== 'applied'">
        <el-button type="danger" @click="confirmJob">确认写回</el-button>
        <el-button @click="refreshJob">刷新</el-button>
      </div>

      <el-collapse v-if="job.items && job.items.length">
        <el-collapse-item v-for="item in job.items" :key="item.id" :name="item.id">
          <template #title>
            <span style="font-size: 13px">{{ item.path }}</span>
            <el-tag size="small" :type="item.applied ? 'success' : 'info'" style="margin-left: 8px">{{ item.applied ? '已写' : '待写' }}</el-tag>
            <span v-if="item.error" style="color: #f56c6c; font-size: 12px; margin-left: 8px">{{ item.error }}</span>
          </template>
          <pre class="mono" style="background: #fafafa; border: 1px solid #eee; border-radius: 4px; padding: 8px; max-height: 240px; overflow: auto">{{ item.diff }}</pre>
        </el-collapse-item>
      </el-collapse>
    </el-card>

    <el-empty v-else description="点击上方按钮生成写回预览，逐篇核对 diff 后确认" />
  </div>

  <!-- 备份管理 -->
  <el-drawer v-model="backupsVisible" title="写回备份" size="520px">
    <el-table :data="backups" size="small">
      <el-table-column prop="id" label="备份ID" min-width="160" />
      <el-table-column prop="files.length" label="文件数" width="90" />
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button size="small" type="danger" plain @click="restoreBackup(row)">恢复</el-button>
        </template>
      </el-table-column>
      </el-table>
    <template #footer>
      <el-button size="small" @click="backupsVisible = false">关闭</el-button>
    </template>
  </el-drawer>
</template>