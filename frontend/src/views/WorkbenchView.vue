<script setup>
import { ref, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, friendlyMessage } from '../api.js'

const route = useRoute()
const trees = ref([])
const loadingTrees = ref(false)
const selected = ref(null) // 选中的树（含 nodes）
const loadingDetail = ref(false)
const adjustments = ref([])
const historyVisible = ref(false)

const STATUS_META = {
  complete: { label: '已完成', type: 'success' },
  in_progress: { label: '进行中', type: 'primary' },
  dangling_confirmed: { label: '断头·已确认', type: 'warning' },
  dangling_suspected: { label: '断头·疑似', type: 'danger' },
}

async function loadTrees() {
  loadingTrees.value = true
  try {
    const data = await api.get('/forest')
    trees.value = data.trees.slice().sort((a, b) => a.confidence - b.confidence) // 低置信优先
  } catch (e) {
    ElMessage.error(friendlyMessage(e))
  } finally {
    loadingTrees.value = false
  }
}

async function selectTree(id) {
  selected.value = null
  loadingDetail.value = true
  try {
    selected.value = await api.get(`/trees/${id}`)
  } catch (e) {
    ElMessage.error(friendlyMessage(e))
  } finally {
    loadingDetail.value = false
  }
}

watch(() => route.query.tree, (id) => { if (id) selectTree(id) })
onMounted(async () => {
  await loadTrees()
  if (route.query.tree) selectTree(route.query.tree)
})

function tagType(s) { return (STATUS_META[s] || {}).type || 'info' }

async function adjust(action, payload) {
  if (!selected.value) return
  try {
    const res = await api.post(`/trees/${selected.value.id}/adjust`, { action, payload })
    ElMessage.success('修正已生效')
    await selectTree(selected.value.id)
    await loadTrees()
    await loadAdjustments()
    return res
  } catch (e) {
    ElMessage.error(friendlyMessage(e))
  }
}

function onSetStatus() {
  ElMessageBox.prompt('选择新状态', '修改状态', {
    inputType: 'select', inputOptions: Object.entries(STATUS_META).map(([v, m]) => ({ value: v, label: m.label })),
  }).then(({ value }) => adjust('set_status', { status: value })).catch(() => {})
}

function onRetitle() {
  ElMessageBox.prompt('输入新标题', '重命名树', {
    inputValue: selected.value?.title, inputPlaceholder: '新标题',
  }).then(({ value }) => adjust('retitle', { title: value })).catch(() => {})
}

function onRegenerate() {
  ElMessageBox.confirm('仅重新生成该树的产物页并刷新森林总览，确定？', '局部重生成', {
    type: 'warning',
  }).then(async () => {
    try {
      await api.post(`/trees/${selected.value.id}/regenerate`)
      ElMessage.success('已重生成')
    } catch (e) { ElMessage.error(friendlyMessage(e)) }
  }).catch(() => {})
}

async function loadAdjustments() {
  try { adjustments.value = (await api.get('/adjustments')).adjustments } catch { /* 忽略 */ }
}

function openAdjustments() {
  loadAdjustments()
  historyVisible.value = true
}

function openObsidian(note) {
  if (note?.obsidian_uri) window.open(note.obsidian_uri, '_blank')
}

function buildTree(nodes) {
  const map = new Map(nodes.map((n) => [n.id, { ...n, children: [] }]))
  const roots = []
  for (const n of map.values()) {
    if (n.parent_id != null && map.has(n.parent_id)) map.get(n.parent_id).children.push(n)
    else roots.push(n)
  }
  const sortRec = (arr) => {
    arr.sort((a, b) => a.order - b.order)
    arr.forEach((c) => sortRec(c.children))
  }
  sortRec(roots)
  return roots
}
</script>

<template>
  <div style="display: flex; gap: 16px">
    <!-- 左：树列表（低置信优先） -->
    <el-card shadow="never" style="width: 300px; flex-shrink: 0">
      <template #header>
        <div class="toolbar" style="margin-bottom: 0; justify-content: space-between">
          <span>树列表</span>
          <el-button size="small" @click="loadTrees">刷新</el-button>
        </div>
      </template>
      <div v-loading="loadingTrees">
        <div
          v-for="t in trees" :key="t.id"
          @click="selectTree(t.id)"
          :style="{ padding: '8px 10px', borderBottom: '1px solid #f0f0f0', cursor: 'pointer', borderRadius: 4,
            background: selected?.id === t.id ? '#ecf5ff' : 'transparent' }"
        >
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span style="font-size: 13px">{{ t.title }}</span>
            <el-tag size="small" :type="tagType(t.status)">{{ (STATUS_META[t.status] || {}).label || t.status }}</el-tag>
          </div>
          <div class="muted" style="margin-top: 2px">
            置信 {{ (t.confidence * 100).toFixed(0) }}% · {{ t.node_count }} 节点
            <el-tag v-if="!t.verified" size="small" type="info" effect="plain">草稿</el-tag>
          </div>
        </div>
        <el-empty v-if="!trees.length" :image-size="50" description="暂无树，请先在任务页运行" />
      </div>
    </el-card>

    <!-- 右：树详情 + 修正 -->
    <el-card shadow="never" v-loading="loadingDetail" style="flex: 1">
      <template #header>
        <div v-if="selected" style="display: flex; justify-content: space-between; align-items: center">
          <span>
            <b>{{ selected.title }}</b>
            <el-tag :type="tagType(selected.status)" style="margin-left: 8px">{{ (STATUS_META[selected.status] || {}).label }}</el-tag>
            <el-tag v-if="selected.verified" type="primary" style="margin-left: 4px">已确认</el-tag>
            <span class="muted" style="margin-left: 8px">run: {{ selected.run_id }}</span>
          </span>
          <span>
            <el-button size="small" @click="openAdjustments">修正历史</el-button>
            <el-button size="small" type="success" plain @click="onRegenerate">重生成</el-button>
            <el-button size="small" type="primary" plain @click="onSetStatus">改状态</el-button>
            <el-button size="small" @click="onRetitle">重命名</el-button>
          </span>
        </div>
      </template>

      <div v-if="selected">
        <el-alert
          v-if="selected.confidence < 0.6"
          type="warning" :closable="false" show-icon
          style="margin-bottom: 12px"
          title="低置信树：请人工核对证据与结构后再确认"
        />

        <div v-if="selected.narrative" class="page-card">
          <div class="muted" style="margin-bottom: 4px">综述</div>
          <div style="font-size: 13px; line-height: 1.6">{{ selected.narrative }}</div>
        </div>

        <div class="muted" style="margin-bottom: 4px">树结构（点击节点可核对证据；obsidian:// 跳转原笔记）</div>
        <el-tree
          :data="buildTree(selected.nodes || [])"
          :props="{ label: 'event_content', children: 'children' }"
          default-expand-all
        >
          <template #default="{ data: node }">
            <div style="display: inline-flex; align-items: center; gap: 8px; font-size: 13px">
              <span>{{ node.event?.content || ('节点 ' + node.id) }}</span>
              <el-tag size="small" v-if="node.origin === 'human'" type="primary" effect="plain">人工</el-tag>
              <a v-if="node.note?.obsidian_uri" @click.prevent="openObsidian(node.note)" style="cursor: pointer">
                <el-link type="primary" :underline="false">[[{{ node.note.filename }}]]</el-link>
              </a>
              <el-tooltip
                v-if="node.evidence && node.evidence.length"
                :content="JSON.stringify(node.evidence, null, 2)"
              >
                <el-tag size="small" type="info" effect="plain">{{ (node.confidence * 100).toFixed(0) }}%</el-tag>
              </el-tooltip>
            </div>
          </template>
        </el-tree>
      </div>
      <el-empty v-else description="从左侧选择一棵树进行确认与修正" />
    </el-card>
  </div>

  <!-- 修正历史 -->
  <el-drawer v-model="historyVisible" title="修正历史" size="560px">
    <el-table :data="adjustments" size="small">
      <el-table-column prop="action" label="动作" width="100" />
      <el-table-column prop="target_type" label="目标" width="90" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 'applied' ? 'success' : 'info'">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="内容" min-width="200">
        <template #default="{ row }">
          <pre class="mono" style="max-height: 80px; overflow: auto">{{ JSON.stringify(row.payload || row.after, null, 2) }}</pre>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="时间" width="150" />
    </el-table>
    <template #footer>
      <el-button size="small" @click="historyVisible = false">关闭</el-button>
    </template>
  </el-drawer>
</template>