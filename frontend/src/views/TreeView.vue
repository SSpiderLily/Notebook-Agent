<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, friendlyMessage } from '../api.js'

const route = useRoute()
const router = useRouter()
const treeId = route.params.id
const tree = ref(null)
const loading = ref(false)

const STATUS_LABEL = {
  complete: '已完成', in_progress: '进行中',
  dangling_confirmed: '断头·已确认', dangling_suspected: '断头·疑似',
}

function tagType(s) {
  return {
    complete: 'success', in_progress: 'primary',
    dangling_confirmed: 'warning', dangling_suspected: 'danger',
  }[s] || 'info'
}

function noteTagType(status) {
  return { done: 'success', in_progress: 'primary', pending: 'info' }[status] || 'info'
}
function noteTagLabel(status) {
  return { done: '已完成', in_progress: '进行中', pending: '未开始' }[status] || status
}

async function load() {
  loading.value = true
  try {
    tree.value = await api.get(`/trees/${treeId}`)
  } catch (e) {
    ElMessage.error(friendlyMessage(e))
  } finally {
    loading.value = false
  }
}
onMounted(load)

function openObsidian(uri) {
  if (uri) window.open(uri, '_blank')
}
</script>

<template>
  <div>
    <el-card shadow="never" v-loading="loading" class="page-card">
      <template #header>
        <div v-if="tree" style="display: flex; justify-content: space-between; align-items: center;">
          <div>
            <b style="font-size: 16px">{{ tree.title }}</b>
            <el-tag :type="tagType(tree.status)" style="margin-left: 8px">{{ STATUS_LABEL[tree.status] || tree.status }}</el-tag>
            <el-tag v-if="tree.verified" type="primary" style="margin-left: 4px">已确认</el-tag>
          </div>
          <div style="display: flex; align-items: center; gap: 16px">
            <div class="muted">进度 {{ Math.round((tree.progress || 0) * 100) }}% · 完成 {{ tree.done_count }}/{{ tree.total_count }} 篇笔记</div>
            <el-button size="small" @click="router.push({ path: '/workbench', query: { tree: tree.id } })">去确认工作台</el-button>
          </div>
        </div>
      </template>

      <div v-if="tree && tree.note_tree && tree.note_tree.length">
        <div class="muted" style="margin-bottom: 8px">任务下的笔记父子关系（点击 [[]] 跳转原笔记）</div>
        <el-tree
          :data="tree.note_tree"
          :props="{ label: 'title', children: 'children' }"
          default-expand-all
          node-key="note_id"
        >
          <template #default="{ data: node }">
            <div style="display: inline-flex; align-items: center; gap: 8px; font-size: 13px; padding: 2px 0">
              <span>{{ node.title }}</span>
              <el-tag size="small" :type="noteTagType(node.status)">{{ noteTagLabel(node.status) }}</el-tag>
              <a v-if="node.obsidian_uri" @click.prevent="openObsidian(node.obsidian_uri)" style="cursor: pointer">
                <el-link type="primary" :underline="false" size="small">[[{{ node.filename }}]]</el-link>
              </a>
              <el-tooltip v-if="node.summary" :content="node.summary" placement="top">
                <span style="color: #909399; cursor: help">ℹ</span>
              </el-tooltip>
            </div>
            <!-- 展开看该笔记内部的事件明细 -->
            <div v-if="node.events && node.events.length" style="padding-left: 24px; color: #909399; font-size: 12px">
              <div v-for="e in node.events" :key="e.event_id" style="display: flex; gap: 6px; align-items: center">
                <span>{{ e.content }}</span>
                <el-tag v-if="e.status_clue" size="small" type="warning" effect="plain">{{ e.status_clue }}</el-tag>
              </div>
            </div>
          </template>
        </el-tree>
      </div>
      <el-empty v-else-if="tree" description="该任务下暂无笔记节点" />
      <el-empty v-else description="任务不存在或加载失败" />
    </el-card>
  </div>
</template>