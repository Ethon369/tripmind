import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  },
  server: {
    port: 5173,
    // 显式绑定 IPv4。
    // vite 默认的 host 是 'localhost',在 Windows 上会解析成 IPv6 的 ::1,
    // 于是只监听 [::1]:5173 —— 浏览器敲 127.0.0.1:5173 会直接连不上。
    // 绑到 127.0.0.1 之后:浏览器访问 localhost 时若 ::1 拒绝会自动回落
    // 到 IPv4,两种写法都能打开。
    host: '127.0.0.1',
    proxy: {
      // 前端发相对路径(见 src/services/api.ts),由这里转发到后端。
      // 后端换端口只需要改这一处。
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true
      }
    }
  }
})

