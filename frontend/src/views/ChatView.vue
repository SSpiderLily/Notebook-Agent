<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const session = ref(null); const messages = ref([]); const input = ref(''); const loading = ref(false)
async function load() { if (!session.value) session.value = await api.post('/chat/sessions', { title: '问答会话' }); messages.value = (await api.get(`/chat/sessions/${session.value.id}/messages`)).messages }
async function send(content = input.value) { if (!content.trim() || loading.value) return; input.value = ''; loading.value = true; try { const out = await api.post(`/chat/sessions/${session.value.id}/messages`, {content}); if (content === '/clear') messages.value = []; else if (out.role) messages.value.push({ role:'user', content }, out) } finally { loading.value = false } }
async function exportChat() { const out = await api.post(`/chat/sessions/${session.value.id}/messages`, {content:'/export'}); const blob = new Blob([out.markdown || ''], {type:'text/markdown'}); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'noteagent-chat.md'; a.click(); URL.revokeObjectURL(a.href) }
onMounted(load)
</script>
<template>
  <div class="chat-page">
    <div class="chat-toolbar"><el-button size="small" @click="send('/clear')">清空</el-button><el-button size="small" @click="send('/regen')">重新生成</el-button><el-button size="small" @click="exportChat">导出</el-button></div>
    <div class="chat-messages"><el-empty v-if="!messages.length" description="开始一段关于森林的问答" /><div v-for="(m,i) in messages" :key="m.id || i" class="chat-message" :class="m.role"><el-tag size="small">{{ m.role === 'user' ? '我' : '助手' }}</el-tag><div class="chat-content">{{ m.content }}</div><div v-if="m.citations?.length" class="citations"><a v-for="c in m.citations" :key="c.id" :href="c.uri">引用：{{ c.title }}</a></div></div></div>
    <div class="chat-input"><el-input v-model="input" type="textarea" :rows="3" placeholder="询问你的笔记森林…" @keydown.enter.exact.prevent="send()" /><el-button type="primary" :loading="loading" @click="send()">发送</el-button></div>
  </div>
</template>
<style scoped>.chat-page{max-width:900px;margin:auto}.chat-toolbar{display:flex;gap:8px;margin-bottom:16px}.chat-messages{min-height:55vh}.chat-message{padding:12px;margin:10px 0;border-radius:8px;background:#f5f7fa}.chat-message.user{background:#ecf5ff}.chat-content{white-space:pre-wrap;margin-top:8px}.citations{display:flex;gap:12px;margin-top:8px;font-size:12px}.chat-input{display:flex;gap:12px;align-items:flex-end}.chat-input .el-input{flex:1}</style>
