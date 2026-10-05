import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 开发环境把 API 请求代理到 FastAPI 后端
      '/api': 'http://127.0.0.1:8000',
      '/reports': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
  },
})
