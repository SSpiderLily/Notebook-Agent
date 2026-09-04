<script setup>
import { ref, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, friendlyMessage } from '../api.js'

const router = useRouter()
const trees = ref([])
const status = ref('')
const minConfidence = ref(null)
const loading = ref(false)

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'complete', label: '已完成' },
  { value: 'in_progress', label: '进行中' },
  { value: 'dangling_confirmed', label: '断头·已确认' },
  { value: 'dangling_suspected', label: '断头·疑似' },
]

async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (status.value) params.set('status', status.value)
    if (minConfidence.value != null && minConfidence.value !== '') params.set('min_confidence', minConfidence.value)
    const qs = params.toString()
    const data = await api.get('/forest' + (qs ? `?${qs}` : ''))
    trees.value = data.trees
  } catch (e) {
    ElMessage.error(friendlyMessage(e))
  } finally {
    loading.value = false
  }
}

watch([status, minConfidence], () => load())
onMounted(load)

function tagType(s) {
  return {
    complete: 'success', in_progress: 'primary',
    dangling_confirmed: 'warning', dangling_suspected: 'danger',
  }[s] || 'info'
}

function openTree(row) {
  router.push({ path: '/workbench', query: { tree: row.id } })
}
</script>

<template>
  <el-card class="page-card" shadow="never">
    <div class="toolbar">
      <el-select v-model="status" style="width: 160px">
        <el-option v-for="o in STATUS_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
      </el-select>
      <el-input-number v-model="minConfidence" :min="0" :max="1" :step="0.1" placeholder="最低置信度" />
      <el-button :loading="loading" @click="load">刷新</el-button>
      <span class="muted">共 {{ trees.length }} 棵树</span>
    </div>

    <el-table :data="trees" v-loading="loading" @row-click="openTree" style="cursor: pointer">
      <el-table-column prop="title" label="树标题" min-width="180" />
      <el-table-column label="状态" width="130">
        <template #default="{ row }">
          <el-tag :type="tagType(row.status)">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="置信度" width="100">
        <template #default="{ row }">
          <el-tag :type="row.confidence >= 0.6 ? 'success' : 'warning'">{{ (row.confidence * 100).toFixed(0) }}%</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="node_count" label="节点数" width="80" />
      <el-table-column label="人工确认" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.verified" type="primary">已确认</el-tag>
          <el-tag v-else type="info" effect="plain">草稿</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="narrative" label="综述" min-width="260" show-overflow-tooltip />
    </el-table>
  </el-card>
</template>