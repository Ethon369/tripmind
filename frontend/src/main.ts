import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import Antd from 'ant-design-vue'

// 样式引入顺序有讲究：
// 1. antd 的 reset 打底
// 2. tokens 定义设计令牌
// 3. base 全局排版 + 焦点 + 动效降级
// 4. transitions 过渡与 keyframes
// 5. antd-overrides 用令牌覆写 antd 组件（必须最后，否则被 reset 压掉）
import 'ant-design-vue/dist/reset.css'
import './styles/tokens.css'
import './styles/base.css'
import './styles/transitions.css'
import './styles/antd-overrides.css'

import App from './App.vue'
import Home from './views/Home.vue'

// Home 保持同步加载 —— 它是落地页，首屏就要渲染。
// 其余页面懒加载：Result 拖着 html2canvas / jspdf / 高德 JS，
// Knowledge 也单独成块，一起打进首屏包会让首页白屏时间明显变长。
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'Home',
      component: Home,
      meta: { title: '规划行程' }
    },
    {
      // :id? 让旧的 /result(sessionStorage 传数据)继续可用。
      // 带 id 时走接口取历史行程,刷新/换标签页都不丢。
      path: '/result/:id?',
      name: 'Result',
      component: () => import('./views/Result.vue'),
      meta: { title: '行程详情' }
    },
    {
      // 分享链接。和 /result/:id 用同一个组件,由 route.path 前缀
      // 判断是否隐藏"编辑/删除"按钮。
      //
      // 这里刻意写成两条独立路由而不是 alias:/result/:id? 与 /share/:id
      // 的参数可选性不一致,某些 vue-router 4 版本会告警。
      path: '/share/:id',
      name: 'Share',
      component: () => import('./views/Result.vue'),
      meta: { title: '分享的行程' }
    },
    {
      // 创建行程。从首页「开始规划」或热门城市卡片跳过来，
      // 承载原来挤在首页里的「详细设置」（日期、天数、偏好、交通、住宿）。
      path: '/create',
      name: 'Create',
      component: () => import('./views/Create.vue'),
      meta: { title: '创建行程' }
    },
    {
      path: '/history',
      name: 'History',
      component: () => import('./views/History.vue'),
      meta: { title: '历史行程' }
    },
    {
      path: '/knowledge',
      name: 'Knowledge',
      component: () => import('./views/Knowledge.vue'),
      meta: { title: '知识库' }
    },
    {
      // 必须放最后,否则会吃掉上面所有路由
      path: '/:pathMatch(.*)*',
      name: 'NotFound',
      component: () => import('./views/NotFound.vue'),
      meta: { title: '页面不存在' }
    }
  ],

  // 切换路由时回到顶部。
  // 原来没有这个配置,vue-router 默认保留滚动位置 —— 从长行程页往下滚了很远
  // 再点「历史行程」,新页面会停在中部,看起来像是内容没加载出来。
  scrollBehavior(_to, _from, savedPosition) {
    return savedPosition ?? { top: 0 }
  }
})

// 标签页标题。原来所有页面都叫同一个名字,多开几个标签页就分不清哪个是哪个。
router.afterEach((to) => {
  const title = to.meta.title as string | undefined
  document.title = title ? `${title} · 途灵 TripMind` : '途灵 TripMind'
})

const app = createApp(App)

app.use(router)
app.use(Antd)

app.mount('#app')
