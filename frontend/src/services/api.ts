import axios from 'axios'
import type {
  TripFormData,
  TripPlan,
  TripPlanResponse,
  PlanListResponse,
  PlanDetailResponse,
  PlanScope,
  KnowledgeStatus,
  KnowledgeSearchRequest,
  KnowledgeSearchResponse,
  KnowledgeIngestSource,
  KnowledgeIngestPreview,
  KnowledgeIngestTask,
  KnowledgeSources,
  KnowledgeDocParse,
  KnowledgeDocListResponse,
  KnowledgeDocDetail,
  KnowledgeRagToggle,
  AuthUser,
  LoginResponse,
  MeResponse,
  ChangePasswordResponse,
  UserListResponse,
  UserRole
} from '@/types'
import { STORAGE_KEYS } from '@/constants/storage'

/**
 * 后端地址前缀。默认空串 —— 也就是走相对路径。
 *
 * 为什么默认空串而不是写死后端地址:
 * - 开发时请求发给 vite(5173),由 vite.config.ts 里的 proxy 转给后端。
 *   走相对路径的话 proxy 才真正生效;写绝对地址就是浏览器直连后端,
 *   要额外依赖后端配好 CORS,而且换个端口就得改两处。
 * - 生产环境前后端同源,相对路径天然可用;写死的绝对地址会在打包时
 *   被固化进产物,换域名就失效。
 * - 从手机等别的设备访问开发服务器时,写死 127.0.0.1 会指向手机自己。
 *
 * 用 ?? 而不是 ||:空串是"明确表示用相对路径",不该被当成没配。
 * 需要直连后端(比如临时调试)时,在 .env 里写全 VITE_API_BASE_URL=http://...
 *
 * 导出是为了让 Result.vue 之类的组件共用 —— 之前那边另写死了一份
 * `http://localhost:8000`,改端口时两边会不同步。
 */
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

/**
 * 默认超时 = 为「生成行程」这个最慢的接口定的,其它接口都按需向下覆盖。
 *
 * ⚠️ 不要改小。这个值是**整条超时链路**中的一环,和另外两处必须保持有序:
 *
 *     单次 LLM 调用  LLM_TIMEOUT (backend/.env)  180s
 *     前端 axios     这里                        300s   ← 本行
 *     nginx 反代      proxy_read_timeout         330s
 *
 * 生成一次行程要调 7 次 LLM:三个专家 agent 各一次(合计约 45s),
 * 最后 planner 一次性生成完整行程 JSON —— **实测输出 7500+ tokens、
 * 耗时 110 秒**,2 日行程整体约 160 秒。原来的 120s 会让网页必然失败,
 * 而 nginx 在 330s 才断,顺序反了(nginx 会先于前端友好提示抛 504)。
 *
 * 所以取值原则是:LLM < axios < nginx —— 让前端先超时并给出可读文案,
 * 而不是把 nginx 的 504 直接甩给用户。
 */
const TRIP_PLAN_TIMEOUT_MS = 300000 // 5 分钟

// ============ 登录态 ============
//
// 这一整块取代了原来的「管理口令」。两者的形状差别很大，值得说清楚：
//
//   旧：一个共享口令，前端维护一张「哪些请求要附口令」的清单
//       （ADMIN_GUARDED），只在写接口上带 `X-Admin-Token`。
//   新：一个按人签发的会话令牌，**每个请求都带** `Authorization: Bearer`，
//       因为服务端需要知道"你是谁"才能判断"这条数据你能不能看"。
//
// 所以那张清单**整个消失了** —— 不是被简化，而是不再需要。这也是为什么
// 原来的 `test_admin_token_contract.py`（校验前后端两份清单逐条对上）
// 没有对应的新测试：它防的那个失效模式在新设计里不存在。

/**
 * 当前登录令牌。读取点收敛在这里，组件不直接碰存储。
 *
 * 存储读写在隐私模式/被策略禁用时会抛异常，所以必须包 try ——
 * 读不到就当没登录，让请求按未登录走，后端回 401，用户看到的是登录页
 * 而不是一个白屏。为一个存储异常把整页打挂是不划算的。
 */
export function getToken(): string {
  try {
    return localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN) || ''
  } catch {
    return ''
  }
}

/** 写令牌。传空串等于清除。 */
export function setToken(token: string): void {
  const value = (token || '').trim()
  try {
    if (value) localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, value)
    else localStorage.removeItem(STORAGE_KEYS.AUTH_TOKEN)
  } catch {
    /* 存储不可用时忽略：本次会话内的请求会按未登录发出，后端会回 401 */
  }
}

export function clearToken(): void {
  setToken('')
}

export function hasToken(): boolean {
  return getToken() !== ''
}

/**
 * 未授权（401）的**全局**回调。
 *
 * 为什么用回调而不是在拦截器里直接 `router.push('/login')`：
 * api.ts 是纯服务层，import 路由实例会让它和 Vue 应用强耦合 ——
 * 那样这个文件就没法在测试或脚本里单独用了。注册点放在 `main.ts`。
 *
 * 为什么"过期"要走全局：令牌过期（或账号被停用）会让**任何一个**
 * 请求 401。如果每个调用点各自处理，就会出现"六个组件各自弹一次
 * 登录已过期"的情形。统一在一处处理 = 清掉失效令牌 + 跳一次登录页。
 */
type UnauthorizedHandler = () => void

let onUnauthorized: UnauthorizedHandler | null = null

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler
}

/**
 * 这个 401 是不是"登录态失效"。
 *
 * **登录接口自己被拒不算** —— 那是"密码输错了"，不是"会话过期了"。
 * 不排除它的话，用户第一次输错密码就会被跳转到登录页（他本来就在登录页），
 * 并且那一瞬间本地令牌会被清掉 —— 表现是"输错一次密码页面闪一下"，
 * 而真正的错误提示被这次跳转冲掉。
 */
function isSessionExpiry(url: string | undefined): boolean {
  const path = (url || '').split('?')[0]
  return !path.endsWith('/api/auth/login')
}

/**
 * 已登录账号的本地缓存（只用于**首屏渲染**）。
 *
 * 存它是为了让顶栏在 `/api/auth/me` 返回之前就能显示用户名 ——
 * 否则每次刷新页面，右上角都会先空一下再跳出来。
 *
 * ⚠️ 它**不是**权限依据。界面要不要显示「账号管理」入口看的是它，
 * 但那只影响"显示了不该显示的按钮"（点了会被后端 403 挡住）。
 * 真正的判定始终在服务端 —— 本地缓存被改也拿不到任何数据。
 */
const USER_KEY = 'tripmindAuthUser'

export function readCachedUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    const u = parsed as Partial<AuthUser>
    return typeof u.id === 'string' && typeof u.username === 'string'
      ? (u as AuthUser)
      : null
  } catch {
    return null
  }
}

export function cacheUser(user: AuthUser | null): void {
  try {
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user))
    else localStorage.removeItem(USER_KEY)
  } catch {
    /* 同上：缓存失败只是首屏少一个用户名，不影响功能 */
  }
}

/** 清掉本地登录态（令牌 + 缓存账号）。登出、401、改密码失败时都会用到。 */
export function clearAuth(): void {
  clearToken()
  cacheUser(null)
}

/**
 * 构造 Authorization 头。给绕过 axios 的 fetch 调用用
 * （目前只有 parseKnowledgeDoc，它要自己算 multipart boundary）。
 */
function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: TRIP_PLAN_TIMEOUT_MS,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
apiClient.interceptors.request.use(
  (config) => {
    // **每个请求都带**令牌，不再是"筛出需要权限的那些"。
    //
    // 这是因为权限模型从"少数写接口要口令"变成了"服务端要知道你是谁"：
    // 连 `GET /api/trip/plans` 都要按账号过滤，`GET /api/auth/me` 更离不开它。
    // 维护一张"哪些请求要带"的清单在这个模型下只会不断漏项。
    //
    // 代价是**读接口也会带上自定义头**：非同源部署时会给它们各加一次
    // CORS 预检。生产环境前后端同源（见 docs/DEPLOYMENT.md），无所谓；
    // 本地开发走 vite 代理，也是同源。
    const token = getToken()
    if (token) config.headers.set('Authorization', `Bearer ${token}`)

    console.log('发送请求:', config.method?.toUpperCase(), config.url)
    return config
  },
  (error) => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
apiClient.interceptors.response.use(
  (response) => {
    console.log('收到响应:', response.status, response.config.url)
    return response
  },
  (error) => {
    const status = error?.response?.status
    console.error('响应错误:', status, error.message)

    if (status === 401 && isSessionExpiry(error?.config?.url)) {
      // 先清本地再通知 —— 通知的接收方（main.ts 里的守卫）会去读登录态，
      // 顺序反了它会读到一份还没清掉的令牌，于是"以为还登着"而不跳转。
      clearAuth()
      onUnauthorized?.()
    }

    return Promise.reject(error)
  }
)

/**
 * 一个 axios 错误里我们真正会用到的字段。
 *
 * 为什么要有这个类型:axios 的 `catch` 拿到的在 TS 里是 `unknown` ——
 * 不能直接点 `.response.data.detail`。以前每个方法都写 `catch (error: any)`,
 * 等于把这一整片都退出类型检查,属性名写错、后端换了字段名都不会报错。
 */
interface ApiErrorShape {
  /** HTTP 状态码。网络层就失败(断网、超时)时是 undefined */
  status?: number
  /** axios 自己的错误码,如 'ECONNABORTED'(超时)、'ERR_NETWORK' */
  code?: string
  /** 后端 FastAPI 返回的 `{"detail": "..."}` */
  detail?: string
  /** axios 生成的消息 */
  message?: string
}

function readApiError(error: unknown): ApiErrorShape {
  if (typeof error !== 'object' || error === null) {
    return { message: typeof error === 'string' ? error : undefined }
  }
  // 收窄到我们关心的那几个字段。用 unknown 逐层判定,而不是 `as any` ——
  // 这样 axios 将来改结构时,这里会先报类型错,而不是运行时静默变成 undefined。
  const e = error as { code?: unknown; message?: unknown; response?: unknown }
  const response = e.response as { status?: unknown; data?: unknown } | undefined
  const data = response?.data as { detail?: unknown } | undefined

  return {
    status: typeof response?.status === 'number' ? response.status : undefined,
    code: typeof e.code === 'string' ? e.code : undefined,
    detail: typeof data?.detail === 'string' ? data.detail : undefined,
    message: typeof e.message === 'string' ? e.message : undefined
  }
}

/** 按状态码或 axios 错误码替换文案 */
interface ApiErrorMessages {
  /** HTTP 状态码 → 文案,如 `{ 404: '行程不存在或已被删除' }` */
  byStatus?: Record<number, string>
  /** axios 错误码 → 文案,如 `{ ECONNABORTED: '状态检查超时' }` */
  byCode?: Record<string, string>
}

/**
 * 把后端错误映射成一句能给用户看的话。
 *
 * 优先级:**错误码 → 后端 detail → 状态码 → axios 消息 → fallback**。
 *
 * 为什么 detail 排在状态码前面:后端的 detail 是 FastAPI 里写好的业务说明
 * (如"这篇文档不存在（可能已被删除）。请重新上传。"),它比前端本地
 * 硬编码的那句更具体。状态码映射只是"后端没给理由时"的兜底。
 *
 * 错误码(超时/断网)排最前是因为那时**后端根本没参与**,
 * 也就没有 detail 可用。
 *
 * 这个函数替代了 17 处几乎一样的
 * `throw toApiError(error, '…')`。
 */
function toApiError(
  error: unknown,
  fallback: string,
  messages: ApiErrorMessages = {}
): Error {
  const { status, code, detail, message } = readApiError(error)

  const byCode = code ? messages.byCode?.[code] : undefined
  if (byCode) return new Error(byCode)

  if (detail) return new Error(detail)

  const byStatus = status !== undefined ? messages.byStatus?.[status] : undefined
  if (byStatus) return new Error(byStatus)

  return new Error(message || fallback)
}

/**
 * 生成旅行计划
 */
export async function generateTripPlan(formData: TripFormData): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.post<TripPlanResponse>('/api/trip/plan', formData)
    return response.data
  } catch (error: unknown) {
    console.error('生成旅行计划失败:', error)
    // 生成行程现在要求登录。理论上未登录的用户到不了这一步（路由守卫会先拦），
    // 但**会话在第 90 秒过期**这类情况是真实存在的 —— 那时他已经在等了，
    // 所以要给一句能解释"为什么白等了"的话，而不是笼统的"生成失败"。
    throw toApiError(error, '生成旅行计划失败', {
      byStatus: { 401: '登录已过期，请重新登录后再生成。这次没有产生任何记录。' }
    })
  }
}

/** 后端 `/api/trip/health` 的返回(见 backend/app/api/routes/trip.py) */
export interface TripHealthResponse {
  status: string
  service: string
  /** 4 个 agent 的名字 */
  agents: Record<string, string>
  /** 每个 agent 注册了多少个工具 */
  agent_tools: Record<string, number>
  /**
   * 高德 MCP 展开出的工具数量。
   *
   * ⚠️ 它应当是 **16**。高德工具加载失败时服务照常启动、接口照常 200,
   * 但 agent 会转而编造景点与坐标 —— 从外部完全看不出来。
   * 所以这是判断"服务真的健康"的唯一外部信号,比 status 字段更可信。
   */
  mcp_tools_count: number
}

/**
 * 健康检查
 *
 * 注意它返回的是 200 + `status='healthy'`,**不是**一个布尔值;
 * 真正的健康信号是 `mcp_tools_count === 16`。
 */
export async function healthCheck(): Promise<TripHealthResponse> {
  try {
    // 之前这里写的是 '/health',但路由挂载在 /api/trip 下,
    // 真实路径是 /api/trip/health —— 所以这个函数一直是 404。
    const response = await apiClient.get<TripHealthResponse>('/api/trip/health')
    return response.data
  } catch (error: unknown) {
    console.error('健康检查失败:', error)
    throw toApiError(error, '健康检查失败')
  }
}

// ============ 历史行程 ============

/**
 * 历史行程列表（按创建时间倒序）+ 累计统计
 *
 * @param scope `mine` 只看自己的；`all` 看全部用户 —— **仅管理员可用**，
 *              普通账号调它会拿到 403（后端是显式拒绝，不是静默降级）。
 */
export async function listPlans(
  limit = 50,
  offset = 0,
  scope: PlanScope = 'mine'
): Promise<PlanListResponse> {
  try {
    const response = await apiClient.get<PlanListResponse>('/api/trip/plans', {
      params: { limit, offset, scope }
    })
    return response.data
  } catch (error: unknown) {
    console.error('读取历史行程失败:', error)
    throw toApiError(error, '读取历史行程失败', {
      byStatus: { 403: '只有管理员可以查看全部用户的行程' }
    })
  }
}

/**
 * 单个行程详情。
 *
 * 注意:后端对 status='running' 的记录会返回 success=false 且 data 为空,
 * 但 **不是** HTTP 错误 —— 调用方要检查 success 而不是只看有没有抛异常。
 */
export async function getPlan(planId: string): Promise<PlanDetailResponse> {
  try {
    const response = await apiClient.get<PlanDetailResponse>(
      `/api/trip/plans/${encodeURIComponent(planId)}`
    )
    return response.data
  } catch (error: unknown) {
    // 404 要单独给文案:前端据此显示"行程不存在"而不是笼统的"加载失败"。
    // 后端对 404 的 detail 会优先于这里的兜底(见 toApiError 的优先级说明)。
    console.error('读取行程详情失败:', error)
    throw toApiError(error, '读取行程详情失败', {
      byStatus: { 404: '行程不存在或已被删除' }
    })
  }
}

/**
 * 回写编辑后的行程。只改内容,不动成本统计。
 */
export async function updatePlan(planId: string, plan: TripPlan): Promise<PlanDetailResponse> {
  try {
    const response = await apiClient.put<PlanDetailResponse>(
      `/api/trip/plans/${encodeURIComponent(planId)}`,
      plan
    )
    return response.data
  } catch (error: unknown) {
    console.error('保存行程失败:', error)
    // 403 是"这条行程不是你的"。要让用户看懂是哪一种拒绝 ——
    // 会话过期是 401，那条路径由响应拦截器统一处理（清登录态 + 跳登录页）。
    throw toApiError(error, '保存行程失败', {
      byStatus: { 403: '只能修改自己的行程' }
    })
  }
}

/**
 * 从历史记录中永久删除一个行程。
 */
export async function deletePlan(planId: string): Promise<void> {
  try {
    await apiClient.delete(`/api/trip/plans/${encodeURIComponent(planId)}`)
  } catch (error: unknown) {
    console.error('删除行程失败:', error)
    throw toApiError(error, '删除行程失败', {
      byStatus: { 403: '只能删除自己的行程' }
    })
  }
}

// ============ 知识库(RAG) ============
//
// 这些接口的 timeout 单独设置:默认的 300 秒是给「生成行程」用的,
// 知识库的状态/检索都应该在几秒内返回。用默认值的话,
// Milvus 没起来时页面会转五分钟的圈才给反馈。

/** 知识库状态。withCounts=false 时跳过 Milvus 计数,用于页面首屏 */
export async function getKnowledgeStatus(withCounts = true): Promise<KnowledgeStatus> {
  try {
    const response = await apiClient.get<KnowledgeStatus>('/api/knowledge/status', {
      params: { with_counts: withCounts },
      timeout: 15000
    })
    return response.data
  } catch (error: unknown) {
    // 超时和「后端没起」要分开:前者是知识库慢,后者是服务不在。
    // 用 byCode 而不是 if 提前抛 —— 超时的时候后端根本没参与,
    // 也就没有 detail 可用,只能靠 axios 的错误码区分。
    throw toApiError(error, '读取知识库状态失败', {
      byCode: { ECONNABORTED: '状态检查超时。知识库可能正在启动，或 Milvus 连接卡住了。' }
    })
  }
}

/** 检索知识库。namespace='all' 时后端两层归并后按分数降序 */
export async function searchKnowledge(
  req: KnowledgeSearchRequest
): Promise<KnowledgeSearchResponse> {
  try {
    const response = await apiClient.post<KnowledgeSearchResponse>('/api/knowledge/search', req, {
      timeout: 30000
    })
    return response.data
  } catch (error: unknown) {
    throw toApiError(error, '检索失败', {
      byCode: { ECONNABORTED: '检索超时。embedding 接口可能较慢或不可达。' }
    })
  }
}

/** 灌库预览。不调 embedding,不花钱 */
export async function previewKnowledgeIngest(
  source: KnowledgeIngestSource
): Promise<KnowledgeIngestPreview> {
  try {
    const response = await apiClient.post<KnowledgeIngestPreview>(
      '/api/knowledge/ingest/preview',
      { source },
      { timeout: 20000 }
    )
    return response.data
  } catch (error: unknown) {
    throw toApiError(error, '读取灌库预览失败')
  }
}

/**
 * 提交灌库任务。立即返回 task_id,需要轮询 getKnowledgeIngestTask 拿进度。
 *
 * recreate=true 时后端要求 confirm='DROP',缺失会返回 400 —— 这是后端的守卫,
 * 前端不能只依赖自己的二次确认弹窗。
 */
export async function startKnowledgeIngest(
  source: KnowledgeIngestSource,
  recreate = false
): Promise<KnowledgeIngestTask> {
  try {
    const response = await apiClient.post<KnowledgeIngestTask>(
      '/api/knowledge/ingest',
      { source, recreate, confirm: recreate ? 'DROP' : '' },
      { timeout: 20000 }
    )
    return response.data
  } catch (error: unknown) {
    // 409 = 已有任务在跑。这句话直接给用户看,不用改写
    throw toApiError(error, '提交灌库任务失败')
  }
}

/** 查询灌库任务进度 */
export async function getKnowledgeIngestTask(taskId: string): Promise<KnowledgeIngestTask> {
  try {
    const response = await apiClient.get<KnowledgeIngestTask>(
      `/api/knowledge/ingest/${encodeURIComponent(taskId)}`,
      { timeout: 10000 }
    )
    return response.data
  } catch (error: unknown) {
    // 404 说明后端的任务表里没有这个 id —— 最常见的原因是后端重启过,
    // 而任务状态是**进程内**的(见后端 knowledge.py 的 _TASKS)。
    // 这句提示要留住,否则用户只看到"查询失败",会一直重试一个不存在的任务。
    throw toApiError(error, '查询任务失败', {
      byStatus: { 404: '任务不存在（后端可能已重启）' }
    })
  }
}

/** 数据源元信息:POI 库存与攻略目录 */
export async function getKnowledgeSources(): Promise<KnowledgeSources> {
  try {
    const response = await apiClient.get<KnowledgeSources>('/api/knowledge/sources', {
      timeout: 20000
    })
    return response.data
  } catch (error: unknown) {
    throw toApiError(error, '读取数据源信息失败')
  }
}

// ---- 上传攻略(用户文档) ----

/**
 * 解析一篇上传的攻略,**不入库** —— 返回切块预览,确认后调 ingestKnowledgeDoc。
 *
 * 用 fetch 而不是 apiClient:apiClient 的默认头是 `application/json`,
 * 而这个请求的 body 是 FormData,Content-Type 必须由浏览器自己算 boundary。
 * 与其依赖 axios 对「FormData + 预设 JSON 头」的推导,不如绕开它 ——
 * 少一个隐式行为,就少一类查不到原因的 422。
 */
export async function parseKnowledgeDoc(payload: {
  file?: File
  text?: string
  title?: string
}): Promise<KnowledgeDocParse> {
  const form = new FormData()
  if (payload.file) form.append('file', payload.file)
  if (payload.text) form.append('text', payload.text)
  if (payload.title) form.append('title', payload.title)

  const response = await fetch(`${API_BASE_URL}/api/knowledge/docs/parse`, {
    method: 'POST',
    // 这个接口是管理员专属，但它绕过了 axios，拦截器管不到它 ——
    // 所以 Authorization 要在这里手动带上（authHeaders 和拦截器
    // 读的是同一处存储，两边的令牌不会不一致）。
    headers: authHeaders(),
    body: form
  })
  // fetch 不像 axios 那样在非 2xx 时抛异常,也不会替我们把 body 解析成 JSON,
  // 所以这两件事都得手动做。`json()` 的返回类型是 `any`,这里显式收成
  // `unknown`,再按需要收窄 —— 否则一个 `any` 会顺着 data 传下去。
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    // 401/403 要**优先于**后端的 detail 说清楚，和 axios 那条路径同样的取舍：
    // 后端回的是"该操作需要管理员权限。"（它在回答"为什么被拒"），
    // 而用户需要的是"我现在该做什么"。
    //
    // ⚠️ 401 这里**不触发**全局登出回调 —— 因为这个函数走的是原生 fetch，
    // 拦截器根本不经过它。所以令牌真过期时，只有这一次调用会被拒。
    // 下一次走 axios 的请求会把登录态清掉并跳转，用户不会卡住。
    if (response.status === 401) {
      throw new Error('登录已过期，请重新登录后再上传。')
    }
    if (response.status === 403) {
      throw new Error('上传攻略需要管理员权限。')
    }
    // 后端对"文件不适合入库"这类问题统一回 422 + 一句能照做的话
    const detail = (body as { detail?: unknown } | null)?.detail
    throw new Error(
      typeof detail === 'string' ? detail : `解析失败(HTTP ${response.status})`
    )
  }
  return body as KnowledgeDocParse
}

/** 入库一篇已解析的攻略。202 + task_id,用 getKnowledgeIngestTask 轮询进度 */
export async function ingestKnowledgeDoc(docId: string): Promise<KnowledgeIngestTask> {
  try {
    const response = await apiClient.post<KnowledgeIngestTask>(
      `/api/knowledge/docs/${encodeURIComponent(docId)}/ingest`,
      {},
      { timeout: 30000 }
    )
    return response.data
  } catch (error: unknown) {
    throw toApiError(error, '提交入库任务失败')
  }
}

/** 上传攻略列表 */
export async function listKnowledgeDocs(): Promise<KnowledgeDocListResponse> {
  try {
    const response = await apiClient.get<KnowledgeDocListResponse>('/api/knowledge/docs', {
      timeout: 20000
    })
    return response.data
  } catch (error: unknown) {
    throw toApiError(error, '读取攻略列表失败')
  }
}

/** 单篇详情(含分块预览)。用于「查看来源」 */
export async function getKnowledgeDoc(docId: string): Promise<KnowledgeDocDetail> {
  try {
    const response = await apiClient.get<KnowledgeDocDetail>(
      `/api/knowledge/docs/${encodeURIComponent(docId)}`,
      { timeout: 20000 }
    )
    return response.data
  } catch (error: unknown) {
    // 404 给具体文案:文档被删掉是常见情况,提示要能指向"重新上传"
    throw toApiError(error, '读取攻略详情失败', {
      byStatus: { 404: '这篇攻略不存在（可能已被删除）' }
    })
  }
}

/**
 * 删除一篇上传攻略。后端会先删向量库里的块、再删记录 ——
 * 顺序反过来会留下检索得到却无记录的孤儿块。
 */
export async function deleteKnowledgeDoc(
  docId: string
): Promise<{ success: boolean; message: string; deleted_chunks: number }> {
  try {
    const response = await apiClient.delete(`/api/knowledge/docs/${encodeURIComponent(docId)}`, {
      timeout: 60000
    })
    return response.data
  } catch (error: unknown) {
    throw toApiError(error, '删除失败')
  }
}

/** 运行时开关知识库。只影响当前进程,重启恢复 .env 的值(评测基线不动) */
export async function toggleKnowledgeRag(enabled: boolean): Promise<KnowledgeRagToggle> {
  try {
    const response = await apiClient.post<KnowledgeRagToggle>(
      '/api/knowledge/rag-toggle',
      { enabled },
      { timeout: 15000 }
    )
    return response.data
  } catch (error: unknown) {
    throw toApiError(error, '切换失败')
  }
}

/**
 * 按地标名取一张照片（后端接的是 Unsplash 关键词搜索）。
 *
 * **拿不到图不是错误** —— 所以这里不抛异常，失败一律返回 null，
 * 由调用方降级成占位图。首页城市卡片的照片是"锦上添花"，
 * 因为一张搜不到的图让整页报错不合理。
 *
 * 另注：它是**关键词搜索**，不是精确匹配。"外滩"和"西湖"有可能返回同一张图 ——
 * 所以调用方不要假设每个名字都能拿到唯一且准确的画面。
 */
export async function getPoiPhoto(name: string): Promise<string | null> {
  try {
    const response = await apiClient.get<{ success: boolean; data?: { photo_url?: string } }>(
      '/api/poi/photo',
      { params: { name }, timeout: 15000 }
    )
    return response.data?.data?.photo_url || null
  } catch {
    return null
  }
}

/** 后端 LLM 解析出的意图 */
export interface ParsedIntentData {
  city?: string;
  days?: number | null;
  preferences?: string[];
  transportation?: string | null;
  accommodation?: string | null;
  /** 一句自然语言的回应，比前端模板文案自然得多 */
  greeting?: string;
}

export interface ParseIntentResponse {
  success: boolean;
  /** `llm` = 模型解析成功；`unavailable` = 模型不可用，调用方应回退到规则解析 */
  source: 'llm' | 'unavailable' | string;
  message?: string;
  data?: ParsedIntentData;
}

/**
 * 用后端 LLM 解析用户的一句话。
 *
 * **拿不到结果不是错误** —— 所以不抛异常，失败返回 null。
 * 前端本来就有一套规则解析（`utils/parseTripIntent.ts`）能兜住，
 * 这个接口是"读得更准"的增强，不是依赖。模型没配 key、超时、返回格式异常，
 * 都不该让创建页出问题。
 */
export async function parseIntentWithLLM(text: string): Promise<ParseIntentResponse | null> {
  try {
    const response = await apiClient.post<ParseIntentResponse>(
      '/api/trip/parse',
      { text },
      // 比生成行程快得多，但比普通接口慢 —— 模型要跑一轮推理
      { timeout: 30000 }
    );
    return response.data;
  } catch {
    return null;
  }
}

// ============ 账号 ============

/**
 * 登录。成功后**由调用方决定**要不要写本地存储。
 *
 * 这里不自动 `setToken`，是为了让"登录成功"和"记住登录状态"两件事显式分开：
 * 调用方（Login.vue）拿到响应后再写 —— 如果写失败了（隐私模式），
 * 至少还能提示用户"本次登录在刷新后会失效"，而不是静默地不生效。
 */
export async function login(username: string, password: string): Promise<LoginResponse> {
  const response = await apiClient.post<LoginResponse>(
    '/api/auth/login',
    { username, password },
    // 登录要跑一次 pbkdf2（服务端约 0.3 秒），但绝不该用默认的 300 秒 ——
    // 密码错了要立刻知道，而不是转五分钟的圈。
    { timeout: 20000 }
  )
  return response.data
}

/**
 * 注册。成功后直接返回登录令牌 —— 不用让用户再手打一次刚设的密码。
 *
 * ⚠️ **没有 `role` 参数**,后端也不接受(见 `routes/auth.py::register`)。
 * 注册出来的账号固定是普通用户 —— 这是整套权限模型的地基。
 */
export async function register(
  username: string,
  password: string,
  displayName?: string
): Promise<LoginResponse> {
  try {
    const response = await apiClient.post<LoginResponse>(
      '/api/auth/register',
      { username, password, display_name: displayName || null },
      // 注册要跑一次 pbkdf2(服务端约 0.3 秒),用默认的 300 秒会让
      // 出错的用户白等 —— 20 秒足够。
      { timeout: 20000 }
    )
    return response.data
  } catch (error: unknown) {
    // 400 / 409 的 detail 里是后端写好的可照做的话
    // (如「用户名「x」已被占用」「密码至少 6 位」),优先透传;
    // 403 是站点关闭了自助注册;429 是节流。
    throw toApiError(error, '注册失败', {
      byStatus: {
        403: '本站已关闭自助注册，请联系管理员开通账号',
        429: '注册请求过于频繁，请稍后再试'
      }
    })
  }
}

/**
 * 登出。**服务端失败也要清本地** ——
 * 用户点了"退出"，本地就必须退干净；否则会出现"看起来登出了、
 * 刷新一下又回来了"这种最让人不安的状态。
 */
export async function logout(): Promise<void> {
  try {
    await apiClient.post('/api/auth/logout', {}, { timeout: 15000 })
  } catch (error: unknown) {
    console.warn('服务端登出失败，本地登录态已清除:', error)
  } finally {
    clearAuth()
  }
}

/** 当前账号。页面刷新时用它恢复登录态。 */
export async function fetchMe(): Promise<AuthUser> {
  const response = await apiClient.get<MeResponse>('/api/auth/me', { timeout: 15000 })
  return response.data.user
}

/**
 * 修改自己的密码。需要原密码。
 *
 * 返回的新令牌必须由调用方写回本地 —— 服务端改密时会吊销全部会话
 * （包括当前这个），不写回的话用户改完密码立刻变成未登录。
 */
export async function changePassword(
  oldPassword: string,
  newPassword: string
): Promise<ChangePasswordResponse> {
  try {
    const response = await apiClient.post<ChangePasswordResponse>(
      '/api/auth/password',
      { old_password: oldPassword, new_password: newPassword },
      { timeout: 20000 }
    )
    return response.data
  } catch (error: unknown) {
    throw toApiError(error, '修改密码失败', {
      byStatus: { 401: '原密码不正确' }
    })
  }
}

// ---- 账号管理（仅管理员） ----

export async function listUsers(): Promise<UserListResponse> {
  try {
    const response = await apiClient.get<UserListResponse>('/api/auth/users', {
      timeout: 20000
    })
    return response.data
  } catch (error: unknown) {
    throw toApiError(error, '读取账号列表失败', {
      byStatus: { 403: '需要管理员权限' }
    })
  }
}

export async function createUser(payload: {
  username: string
  password: string
  role: UserRole
  display_name?: string
}): Promise<AuthUser> {
  try {
    const response = await apiClient.post<AuthUser>('/api/auth/users', payload, {
      timeout: 20000
    })
    return response.data
  } catch (error: unknown) {
    // 409 是后端明确回的"用户名已被占用"，它那句 detail 比前端能编的准，交给 toApiError 透传
    throw toApiError(error, '创建账号失败', {
      byStatus: { 403: '需要管理员权限' }
    })
  }
}

/**
 * 改账号。**只传要改的字段** —— 后端用 `model_fields_set` 区分
 * "没传"和"传了 null"，所以少传一个键不会被当成"清空它"。
 */
export async function updateUser(
  userId: string,
  payload: {
    password?: string
    role?: UserRole
    is_active?: boolean
    display_name?: string | null
  }
): Promise<AuthUser> {
  try {
    const response = await apiClient.patch<AuthUser>(
      `/api/auth/users/${encodeURIComponent(userId)}`,
      payload,
      { timeout: 20000 }
    )
    return response.data
  } catch (error: unknown) {
    throw toApiError(error, '修改账号失败', {
      byStatus: { 403: '需要管理员权限', 404: '账号不存在' }
    })
  }
}

/** 删账号。名下的行程不会被删除，只转为无主（仅管理员可见）。 */
export async function deleteUser(userId: string): Promise<void> {
  try {
    await apiClient.delete(`/api/auth/users/${encodeURIComponent(userId)}`, {
      timeout: 20000
    })
  } catch (error: unknown) {
    throw toApiError(error, '删除账号失败', {
      byStatus: { 403: '需要管理员权限', 404: '账号不存在' }
    })
  }
}

/** 强制某账号下线（保留账号）。用于"怀疑某人的令牌泄露了"。 */
export async function revokeUserSessions(userId: string): Promise<string> {
  try {
    const response = await apiClient.post<{ message: string }>(
      `/api/auth/users/${encodeURIComponent(userId)}/sessions/revoke`,
      {},
      { timeout: 20000 }
    )
    return response.data.message
  } catch (error: unknown) {
    throw toApiError(error, '强制下线失败', {
      byStatus: { 403: '需要管理员权限', 404: '账号不存在' }
    })
  }
}

export default apiClient;

