import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 生产形态：build 产物由后端 FastAPI 在 127.0.0.1:8686 同端口托管（DESIGN.md 3.3），
// 故 base 用相对路径 './'，保证 dist 里的资源引用相对可移植。
// dev 仅本机自测：vite server 代理 /api 到后端，并在转发时去掉 Origin，规避本机 Origin 护栏。
export default defineConfig({
  plugins: [vue()],
  base: './',
  build: { outDir: 'dist' },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8686',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyReq', (req) => req.removeHeader('origin'))
        },
      },
    },
  },
})
