import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import Antd, { message } from 'ant-design-vue'

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
import Landing from './views/Landing.vue'
import { useAuth } from './composables/useAuth'
import { setUnauthorizedHandler } from './services/api'

// Landing 保持同步加载 —— 它是**站点的第一个页面**（打开链接看到的就是它），
// 首屏就要渲染，不能等一个额外的 chunk 请求。
// 其余页面懒加载：Result 拖着 html2canvas / jspdf / 高德 JS，
// Knowledge 也单独成块，一起打进首屏包会让落地页白屏时间明显变长。
//
// ⚠️ 这也意味着 Landing 的 CSS 会进主包。它比别的页面大一些（这一页本来
//    就是视觉最重的一页），但换来的是"打开就有内容"—— 值得。
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      // 落地页。**公开** —— 这是打开链接看到的第一个页面。
      // `bare: true` 表示不套应用外壳（顶栏/页脚），它有自己的营销导航。
      path: '/',
      name: 'Landing',
      component: Landing,
      meta: { title: 'AI 行程规划助手', bare: true }
    },
    {
      // 规划首页。原来是 `/`，落地页上线后挪到这里。
      // **需要登录** —— 生成行程要落归属（见后端 `POST /api/trip/plan`）。
      path: '/app',
      name: 'Home',
      component: () => import('./views/Home.vue'),
      meta: { title: '规划行程', requiresAuth: true }
    },
    {
      // :id? 让旧的 /result(sessionStorage 传数据)继续可用。
      // 带 id 时走接口取历史行程,刷新/换标签页都不丢。
      //
      // ⚠️ **不要登录** —— 它是分享链接的落地页。见下面 /share/:id 的注释。
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
      //
      // ⚠️ 这条路由**必须保持公开**。分享出去的行程是给不认识的人看的
      // （后端 `GET /api/trip/plans/{id}` 也是公开的）。加上 requiresAuth
      // 之后，收到链接的人会先撞到登录页 —— 分享功能就等于没有了。
      path: '/share/:id',
      name: 'Share',
      component: () => import('./views/Result.vue'),
      meta: { title: '分享的行程' }
    },
    {
      // 登录与注册。两条 `bare` 路由,不套应用外壳 ——
      // 外壳的导航里有「历史行程」「知识库」,而那些页面都需要登录,
      // 给一个还没登录的人看这些入口只会通向一串跳转。
      path: '/login',
      name: 'Login',
      component: () => import('./views/Login.vue'),
      meta: { title: '登录', bare: true }
    },
    {
      path: '/register',
      name: 'Register',
      component: () => import('./views/Register.vue'),
      meta: { title: '注册', bare: true }
    },
    {
      // 创建行程。从首页「开始规划」或热门城市卡片跳过来，
      // 承载原来挤在首页里的「详细设置」（日期、天数、偏好、交通、住宿）。
      path: '/create',
      name: 'Create',
      component: () => import('./views/Create.vue'),
      meta: { title: '创建行程', requiresAuth: true }
    },
    {
      path: '/history',
      name: 'History',
      component: () => import('./views/History.vue'),
      // 历史行程按账号过滤，未登录看只会看到空列表 —— 与其让用户以为
      // "我的行程丢了"，不如直接引到登录页。
      meta: { title: '历史行程', requiresAuth: true }
    },
    {
      path: '/knowledge',
      name: 'Knowledge',
      component: () => import('./views/Knowledge.vue'),
      // 知识库现在是**每人一份**:上传与管理自己的攻略,所有登录用户都能用。
      // 页内仍按角色隐藏"灌内置库 / 切 RAG 开关"那两个管理员操作 ——
      // 它们动的是全站共用资源,不该由一个人替所有人决定。
      meta: { title: '知识库', requiresAuth: true }
    },
    {
      path: '/users',
      name: 'Users',
      component: () => import('./views/Users.vue'),
      meta: { title: '账号管理', requiresAuth: true, requiresAdmin: true }
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

/**
 * 全局登录守卫。
 *
 * 为什么判据放在**本地状态 + 一次服务端核对**，而不是"只看有没有令牌"：
 * 只看令牌的话，一个过期/被吊销的令牌会让用户进到页面里，
 * 然后每个接口依次 401 —— 表现是页面各处轮流弹"登录已过期"。
 * 这里先 `refresh()` 核对一次（它内部有防并发），核对不过就统一拦在门口。
 *
 * `meta.requiresAdmin` 的判定也用服务端返回的角色。**这只是界面层**：
 * 真正的权限判定在后端每个端点上（`require_admin_role`），
 * 前端藏掉入口只是避免"给了一个点了会被拒的按钮"。
 */
router.beforeEach(async (to) => {
  const { user: current, refresh } = useAuth()

  // 已有账号信息时不必每次都问服务端 —— 但**首次**必须核对，
  // 否则"令牌过期 + 本地还有缓存"会让用户直接进到页面里。
  if (!current.value) {
    await refresh()
  }
  const me = useAuth().user.value

  const requiresAuth = to.meta.requiresAuth === true
  const requiresAdmin = to.meta.requiresAdmin === true

  if (!requiresAuth && !requiresAdmin) return true

  if (!me) {
    // 把想去的地方带上，登录后能回到原处。
    // 用 `redirect` 而不是 `next`，并且 Login.vue 侧会校验它是不是站内路径
    // —— 不校验的话这里就是一个开放重定向。
    return { name: 'Login', query: { redirect: to.fullPath } }
  }

  if (requiresAdmin && me.role !== 'admin') {
    // 不跳 404（那会让人以为链接坏了），而是明确说一句"权限不够"再回首页。
    // 不用专门的 403 页面：这个项目只有两个角色，为它多一个页面不值得。
    message.warning('这个页面需要管理员权限');
    return { name: 'Home' };
  }

  return true
})

/**
 * 令牌失效的全局出口。
 *
 * 响应拦截器发现 401 时会调它（见 `services/api.ts`）。这里做两件事：
 * 清掉本地状态、跳登录页。**允许重复触发** —— 同时发出的几个请求
 * 会各调一次，但 `router.push` 到当前已经是的路由是幂等的。
 */
setUnauthorizedHandler(() => {
  useAuth().signOut()
  const current = router.currentRoute.value
  if (current.name !== 'Login') {
    void router.replace({ name: 'Login', query: { redirect: current.fullPath } })
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
