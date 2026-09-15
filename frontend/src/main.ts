import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'
import Home from './views/Home.vue'

// Home 保持同步加载 —— 它是落地页,首屏就要渲染。
// 其余页面懒加载:Result 拖着 html2canvas / jspdf / 高德 JS,
// 一起打进首屏包会让首页白屏时间明显变长。
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'Home',
      component: Home
    },
    {
      // :id? 让旧的 /result(sessionStorage 传数据)继续可用。
      // 带 id 时走接口取历史行程,刷新/换标签页都不丢。
      path: '/result/:id?',
      name: 'Result',
      component: () => import('./views/Result.vue')
    },
    {
      // 分享链接。和 /result/:id 用同一个组件,由 route.path 前缀
      // 判断是否隐藏"编辑/删除"按钮。
      //
      // 这里刻意写成两条独立路由而不是 alias:/result/:id? 与 /share/:id
      // 的参数可选性不一致,某些 vue-router 4 版本会告警。
      path: '/share/:id',
      name: 'Share',
      component: () => import('./views/Result.vue')
    },
    {
      path: '/history',
      name: 'History',
      component: () => import('./views/History.vue')
    },
    {
      // 必须放最后,否则会吃掉上面所有路由
      path: '/:pathMatch(.*)*',
      name: 'NotFound',
      component: () => import('./views/NotFound.vue')
    }
  ]
})

const app = createApp(App)

app.use(router)
app.use(Antd)

app.mount('#app')
