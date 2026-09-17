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
  KnowledgeSources
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
 * 生成旅行计划
 */
export async function generateTripPlan(formData: TripFormData): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.post<TripPlanResponse>('/api/trip/plan', formData)
    return response.data
  } catch (error: any) {
    console.error('生成旅行计划失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '生成旅行计划失败')
  }
}

/**
 * 健康检查
 */
export async function healthCheck(): Promise<any> {
  try {
    // 之前这里写的是 '/health',但路由挂载在 /api/trip 下,
    // 真实路径是 /api/trip/health —— 所以这个函数一直是 404。
    const response = await apiClient.get('/api/trip/health')
    return response.data
  } catch (error: any) {
    console.error('健康检查失败:', error)
    throw new Error(error.message || '健康检查失败')
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
  } catch (error: any) {
    console.error('读取历史行程失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '读取历史行程失败')
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
  } catch (error: any) {
    // 404 单独抛,前端据此显示"行程不存在"而不是"加载失败"
    if (error.response?.status === 404) {
      throw new Error(error.response?.data?.detail || '行程不存在或已被删除')
    }
    console.error('读取行程详情失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '读取行程详情失败')
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
  } catch (error: any) {
    console.error('保存行程失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '保存行程失败')
  }
}

/**
 * 从历史记录中永久删除一个行程。
 */
export async function deletePlan(planId: string): Promise<void> {
  try {
    await apiClient.delete(`/api/trip/plans/${encodeURIComponent(planId)}`)
  } catch (error: any) {
    console.error('删除行程失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '删除行程失败')
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
  } catch (error: any) {
    // 超时和「后端没起」要分开:前者是知识库慢,后者是服务不在
    if (error.code === 'ECONNABORTED') {
      throw new Error('状态检查超时。知识库可能正在启动,或 Milvus 连接卡住了。')
    }
    throw new Error(error.response?.data?.detail || error.message || '读取知识库状态失败')
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
  } catch (error: any) {
    if (error.code === 'ECONNABORTED') {
      throw new Error('检索超时。embedding 接口可能较慢或不可达。')
    }
    throw new Error(error.response?.data?.detail || error.message || '检索失败')
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
  } catch (error: any) {
    throw new Error(error.response?.data?.detail || error.message || '读取灌库预览失败')
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
  } catch (error: any) {
    // 409 = 已有任务在跑。这句话直接给用户看,不用改写
    throw new Error(error.response?.data?.detail || error.message || '提交灌库任务失败')
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
  } catch (error: any) {
    if (error.response?.status === 404) {
      throw new Error('任务不存在(后端可能已重启)')
    }
    throw new Error(error.response?.data?.detail || error.message || '查询任务失败')
  }
}

/** 数据源元信息:POI 库存与攻略目录 */
export async function getKnowledgeSources(): Promise<KnowledgeSources> {
  try {
    const response = await apiClient.get<KnowledgeSources>('/api/knowledge/sources', {
      timeout: 20000
    })
    return response.data
  } catch (error: any) {
    throw new Error(error.response?.data?.detail || error.message || '读取数据源信息失败')
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

