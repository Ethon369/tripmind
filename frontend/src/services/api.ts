import axios from 'axios'
import type {
  TripFormData,
  TripPlan,
  TripPlanResponse,
  PlanListResponse,
  PlanDetailResponse,
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
  KnowledgeRagToggle
} from '@/types'

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

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 120000, // 2分钟超时
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
apiClient.interceptors.request.use(
  (config) => {
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
    console.error('响应错误:', error.response?.status, error.message)
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
    throw toApiError(error, '生成旅行计划失败')
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
 * 历史行程列表(按创建时间倒序)+ 累计统计
 */
export async function listPlans(limit = 50, offset = 0): Promise<PlanListResponse> {
  try {
    const response = await apiClient.get<PlanListResponse>('/api/trip/plans', {
      params: { limit, offset }
    })
    return response.data
  } catch (error: unknown) {
    console.error('读取历史行程失败:', error)
    throw toApiError(error, '读取历史行程失败')
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
    throw toApiError(error, '保存行程失败')
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
    throw toApiError(error, '删除行程失败')
  }
}

// ============ 知识库(RAG) ============
//
// 这些接口的 timeout 单独设置:默认的 120 秒是给「生成行程」用的,
// 知识库的状态/检索都应该在几秒内返回。用 120 秒的话,
// Milvus 没起来时页面会转两分钟的圈才给反馈。

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
    body: form
  })
  // fetch 不像 axios 那样在非 2xx 时抛异常,也不会替我们把 body 解析成 JSON,
  // 所以这两件事都得手动做。`json()` 的返回类型是 `any`,这里显式收成
  // `unknown`,再按需要收窄 —— 否则一个 `any` 会顺着 data 传下去。
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
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

export default apiClient;

