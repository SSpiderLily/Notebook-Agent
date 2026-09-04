import { createRouter, createWebHashHistory } from 'vue-router'
import TasksView from './views/TasksView.vue'
import ForestView from './views/ForestView.vue'
import WorkbenchView from './views/WorkbenchView.vue'
import WritebackView from './views/WritebackView.vue'

// 用 hash 模式路由：同端口静态托管时无需服务端 SPA fallback（DESIGN.md 3.3）。
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/forest' },
    { path: '/tasks', component: TasksView, meta: { title: '任务' } },
    { path: '/forest', component: ForestView, meta: { title: '森林总览' } },
    { path: '/workbench', component: WorkbenchView, meta: { title: '确认工作台' } },
    { path: '/writeback', component: WritebackView, meta: { title: '双写回' } },
  ],
})