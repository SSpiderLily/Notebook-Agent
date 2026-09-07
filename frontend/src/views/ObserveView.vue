<script setup>
import { ref, onMounted } from 'vue'
import { api, friendlyMessage } from '../api'
const runs=ref([]), calls=ref([]), failures=ref(null), vault=ref(null), error=ref('')
async function load(){ try { [runs.value,calls.value,failures.value,vault.value]=await Promise.all([api.get('/observe/runs'),api.get('/observe/llm-calls'),api.get('/observe/failures'),api.get('/observe/vault-status')]) } catch(e){error.value=friendlyMessage(e)} }
onMounted(load)
</script>
<template><div class="observe"><el-alert v-if="error" :title="error" type="error" />
<h2>运行历史</h2><el-table :data="runs" stripe><el-table-column prop="id" label="Run" width="260"/><el-table-column prop="status" label="状态"/><el-table-column prop="started_at" label="开始时间"/><el-table-column prop="cost_est" label="费用"/></el-table>
<h2>LLM 调用</h2><el-table :data="calls" stripe><el-table-column prop="id" label="ID" width="80"/><el-table-column prop="stage" label="阶段"/><el-table-column prop="caller" label="调用方"/><el-table-column prop="model" label="模型"/><el-table-column prop="status" label="状态"/><el-table-column prop="cost_est" label="费用"/></el-table>
<h2>Vault 状态</h2><el-space><el-tag>总计 {{vault?.total||0}}</el-tag><el-tag type="success">正常 {{vault?.counts?.active||0}}</el-tag><el-tag type="warning">忽略 {{vault?.counts?.ignored||0}}</el-tag><el-tag type="danger">缺失 {{vault?.counts?.missing||0}}</el-tag></el-space>
<h2>失败清单</h2><el-empty v-if="!failures?.stages?.length&&!failures?.llm_calls?.length" description="暂无失败"/><pre v-else>{{ JSON.stringify(failures, null, 2) }}</pre></div></template>
<style scoped>.observe{max-width:1100px}.observe h2{margin:22px 0 10px}.observe pre{background:#f5f7fa;padding:12px;white-space:pre-wrap}</style>
