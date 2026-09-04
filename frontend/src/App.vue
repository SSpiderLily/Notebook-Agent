<script setup>
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <el-container style="height: 100vh">
    <el-aside width="200px" style="background: #1d2b3a">
      <div style="padding: 16px 20px; color: #fff; font-weight: 600; letter-spacing: 0.5px">
        NoteAgent
      </div>
      <el-menu
        :default-active="route.path"
        router
        background-color="#1d2b3a"
        text-color="#c9d4e0"
        active-text-color="#409eff"
      >
        <el-menu-item index="/forest">森林总览</el-menu-item>
        <el-menu-item index="/workbench">确认工作台</el-menu-item>
        <el-menu-item index="/writeback">双写回</el-menu-item>
        <el-menu-item index="/tasks">任务</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header style="background: #fff; border-bottom: 1px solid #e4e7ed; display: flex; align-items: center">
        <span style="font-size: 16px; font-weight: 600">{{ route.meta.title || 'NoteAgent' }}</span>
      </el-header>
      <el-main style="overflow: auto">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>
