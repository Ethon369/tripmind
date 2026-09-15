import axios from 'axios'
import type {
  TripFormData,
  TripPlan,
  TripPlanResponse,
  PlanListResponse,
  PlanDetailResponse
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

export default apiClient

