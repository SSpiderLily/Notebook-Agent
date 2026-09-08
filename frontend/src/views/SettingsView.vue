<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, friendlyMessage } from '../api.js'

const loading = ref(false)
const current = ref(null)
const vaults = ref([])
const newPath = ref('')
const newName = ref('')

async function load() {
  loading.value = true
  try {
    const [list, cur] = await Promise.all([api.get('/vaults'), api.get('/vaults/current')])
    vaults.value = list.vaults || []
    current.value = cur
  } catch (e) {
    ElMessage.error(friendlyMessage(e))
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function registerVault() {
  if (!newPath.value.trim()) {
    ElMessage.warning('请输入仓库路径')
    return
  }
  try {
    const v = await api.post('/vaults/register', { path: newPath.value.trim(), name: newName.value.trim() || null })
    ElMessage.success(`已登记仓库「${v.name}」`)
    newPath.value = ''
    newName.value = ''
    await load()
  } catch (e) {
    ElMessage.error(friendlyMessage(e))
  }
}

async function switchVault(v) {
  try {
    await ElMessageBox.confirm(
      `切换到仓库「${v.name}」？后续页面将读取该仓库的数据。`,
      '切换仓库', { type: 'warning', confirmButtonText: '切换', cancelButtonText: '取消' },
    )
  } catch { return }
  try {
    await api.post(`/vaults/${v.id}/switch`, { confirm: true })
    ElMessage.success('已切换仓库')
    await load()
  } catch (e) {
    ElMessage.error(friendlyMessage(e))
  }
}

async function removeVault(v) {
  try {
    await ElMessageBox.confirm(
      `从注册表移除「${v.name}」？不会删除该仓库内容或其数据目录。`,
      '移除仓库', { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' },
    )
  } catch { return }
  try {
    await api.del(`/vaults/${v.id}`)
    ElMessage.success('已移除')
    await load()
  } catch (e) {
    ElMessage.error(friendlyMessage(e))
  }
}
</script>

<template>
  <div style="display: flex; gap: 16px; flex-wrap: wrap">
    <!-- 当前仓库 -->
    <el-card shadow="never" class="page-card" style="flex: 1; min-width: 300px">
      <template #header><b>当前仓库</b></template>
      <el-descriptions v-if="current" :column="1" border>
        <el-descriptions-item label="名称">{{ current.name }}</el-descriptions-item>
        <el-descriptions-item label="路径">{{ current.path }}</el-descriptions-item>
        <el-descriptions-item label="数据目录">{{ current.data_dir }}</el-descriptions-item>
        <el-descriptions-item label="数据库">{{ current.db_path }}</el-descriptions-item>
      </el-descriptions>
      <el-empty v-else description="未登记任何仓库" />
    </el-card>

    <!-- 登记新仓库 -->
    <el-card shadow="never" class="page-card" style="flex: 1; min-width: 300px">
      <template #header><b>登记新仓库</b></template>
      <el-input v-model="newPath" placeholder="Obsidian 仓库绝对路径，如 /Users/you/MyVault" style="margin-bottom: 10px" />
      <el-input v-model="newName" placeholder="仓库名称（可选，默认取文件夹名）" style="margin-bottom: 12px" />
      <el-button type="primary" @click="registerVault" :loading="loading">登记</el-button>
    </el-card>

    <!-- 仓库列表 -->
    <el-card shadow="never" class="page-card" style="width: 100%">
      <template #header>
        <div class="toolbar" style="margin-bottom: 0; justify-content: space-between">
          <b>已登记仓库</b>
          <span class="muted">{{ vaults.length }} 个</span>
        </div>
      </template>
      <el-table :data="vaults" v-loading="loading">
        <el-table-column label="名称" min-width="140">
          <template #default="{ row }">
            {{ row.name }}
            <el-tag v-if="row.is_current" type="success" size="small" style="margin-left: 6px">当前</el-tag>
            <el-tag v-else-if="row.is_default" type="info" size="small" effect="plain" style="margin-left: 6px">默认</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="path" label="路径" min-width="260" show-overflow-tooltip />
        <el-table-column label="操作" width="180" align="right">
          <template #default="{ row }">
            <el-button v-if="!row.is_current" size="small" type="primary" plain @click="switchVault(row)">切换</el-button>
            <el-button v-if="!row.is_current" size="small" type="danger" plain @click="removeVault(row)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!vaults.length && !loading" description="尚未登记仓库" />
    </el-card>
  </div>
</template>