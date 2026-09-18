import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    {
      // 开发环境下把 index.html 里的 /config.js 标签去掉。
      //
      // 为什么:`/config.js` 是**容器启动时**由 nginx 生成的(运行时注入的
      // 高德 Key),开发环境根本没有这个文件。而 vite 的 SPA 回退会把
      // 这个路径**返回成 index.html** —— 浏览器拿 text/html 当脚本加载,
      // 控制台就多一条 "Mismatched MIME type" 的报错。
      //
      // 它无害(不影响任何功能),但每个开发会话都刷一条看不懂的错误,
      // 会把真正的问题淹掉 —— 留着不值。
      name: 'tripmind-strip-runtime-config-in-dev',
      transformIndexHtml: {
        order: 'pre',
        handler(html: string, ctx: { server?: unknown }) {
          if (!ctx.server) return html // 构建时保留,容器里要靠它
          return html.replace(
            /\s*<!--\s*@runtime-config\s*-->[\s\S]*?<!--\s*\/@runtime-config\s*-->/,
            ''
          )
        }
      }
    }
  ],
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

