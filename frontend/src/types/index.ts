// 类型定义

export interface Location {
  longitude: number
  latitude: number
}

export interface Attraction {
  name: string
  address: string
  location: Location
  visit_duration: number
  description: string
  category?: string
  rating?: number
  image_url?: string
  ticket_price?: number
  // 后端 Attraction 里有这两个字段,历史页/分享页要用
  poi_id?: string
  photos?: string[]
}

export interface Meal {
  type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  name: string
  address?: string
  location?: Location
  description?: string
  estimated_cost?: number
}

export interface Hotel {
  name: string
  address: string
  location?: Location
  price_range: string
  rating: string
  distance: string
  type: string
  estimated_cost?: number
}

export interface Budget {
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  total: number
}

export interface DayPlan {
  date: string
  day_index: number
  description: string
  transportation: string
  accommodation: string
  hotel?: Hotel
  attractions: Attraction[]
  meals: Meal[]
}

export interface WeatherInfo {
  date: string
  day_weather: string
  night_weather: string
  day_temp: number
  night_temp: number
  wind_direction: string
  wind_power: string
}

export interface TripPlan {
  city: string
  start_date: string
  end_date: string
  days: DayPlan[]
  weather_info: WeatherInfo[]
  overall_suggestions: string
  budget?: Budget
}

export interface TripFormData {
  city: string
  start_date: string
  end_date: string
  travel_days: number
  transportation: string
  accommodation: string
  preferences: string[]
  free_text_input: string
}

export interface TripPlanResponse {
  success: boolean
  message: string
  data?: TripPlan
  /** 行程 ID。前端用它跳 /result/{id}、拼分享链接、回写编辑。 */
  plan_id?: string
}

// ============ 历史行程(对应后端 PlanSummary / PlanStats) ============

/** 历史列表里的一条。刻意不含完整行程 —— 只带列表要显示的东西。 */
export interface PlanSummary {
  id: string
  title?: string
  city: string
  start_date: string
  end_date: string
  travel_days: number
  /** running / ok / fallback / error */
  status: string
  created_at: string
  updated_at: string
  total_tokens: number
  cost_cny: number
  latency_ms: number
  llm_calls: number
  usage_source?: string
  /** 降级原因,非空说明这次生成有部分数据不可靠 */
  warnings: string[]
  attractions: number
  total_budget: number
}

/** 历史页顶部的聚合数字。 */
export interface PlanStats {
  total: number
  cost_cny: number
  total_tokens: number
  llm_calls: number
}

export interface PlanListResponse {
  success: boolean
  message: string
  data: PlanSummary[]
  stats?: PlanStats
}

/** 生成这份行程时,知识库给出的一条参考出处 */
export interface KnowledgeSource {
  /** 'poi_facts' | 'city_guides' */
  namespace: string
  /** 文件名 / poi_inventory.json */
  source: string
  /** 章节路径,如「门票与预约」 */
  heading_path: string
  /** COSINE 相似度 */
  score: number
  /** 内容片段(已截断) */
  snippet: string
}

/** 单个行程详情。data 直接是 TripPlan,渲染逻辑可与新生成的行程复用。 */
export interface PlanDetailResponse {
  success: boolean
  message: string
  data?: TripPlan
  meta?: PlanSummary
  /** 这次生成检索到的知识出处(按相关度降序)。没开 RAG / 没命中时为空数组 */
  knowledge?: KnowledgeSource[]
}

// ============ 知识库(RAG) ============
// 契约对应 backend/app/api/routes/knowledge.py

export type KnowledgeNamespace = 'poi_facts' | 'city_guides' | 'uploaded'
export type KnowledgeNamespaceFilter = KnowledgeNamespace | 'all'
export type KnowledgeIngestSource = 'frozen' | 'guides' | 'all'

/** 上传文档的来源。图片刻意不支持 —— 原因见后端 doc_parser.py 顶部注释 */
export type KnowledgeDocOrigin = 'md' | 'txt' | 'pdf' | 'paste'

/** 一条检索命中。score 是 COSINE 相似度,值域 [-1,1] */
export interface KnowledgeHit {
  namespace: string
  content: string
  score: number
  source: string
  heading_path: string
}

/** 单层 collection 的统计。该层统计失败时 error 非空(例如 Milvus 没起来) */
export interface KnowledgeNamespaceStat {
  collection: string
  exists?: boolean
  count?: number
  error?: string
}

export interface KnowledgeStatus {
  success: boolean
  /**
   * 生成行程时是否注入知识库。
   * 注意它与 enabled_source 搭配着读:runtime 来源的开启**重启即失效**。
   */
  enabled: boolean
  /** enabled 的来源:'env' = .env 基线;'runtime' = 业务流程(上传攻略)临时打开 */
  enabled_source: 'env' | 'runtime'
  /** 知识库本身能不能用(embedding 与 Milvus 都通) */
  available: boolean
  /** available 的原因。不可用时原样展示给用户 —— 这是排错的唯一线索 */
  reason: string
  milvus_uri: string
  last_error: string | null
  dim_expected: number
  top_k: number
  collections: Record<string, string>
  poi_facts?: KnowledgeNamespaceStat | null
  city_guides?: KnowledgeNamespaceStat | null
  /** 用户上传攻略那一层 */
  uploaded?: KnowledgeNamespaceStat | null
  /** 上传文档的概览(各状态几份、共多少块) */
  docs?: KnowledgeDocsSummary | null
}

// ---- 上传攻略(用户文档) ----

export type KnowledgeDocStatus = 'pending' | 'ingested' | 'failed'

export interface KnowledgeDocsSummary {
  docs: number
  chunks: number
  ingested: { docs: number; chunks: number }
  pending: { docs: number; chunks: number }
  failed: { docs: number; chunks: number }
}

export interface KnowledgeDocSummary {
  id: string
  title: string
  origin: KnowledgeDocOrigin
  char_count: number
  chunk_count: number
  status: KnowledgeDocStatus
  error: string | null
  created_at: string
  ingested_at: string | null
}

export interface KnowledgeDocChunkPreview {
  idx: number
  heading_path: string
  chars: number
  snippet: string
}

/** 解析结果(入库前预览)。后端这一步**不写向量库** */
export interface KnowledgeDocParse {
  success: boolean
  doc_id: string
  title: string
  origin: KnowledgeDocOrigin
  char_count: number
  chunk_count: number
  /** 预计要发几次 embedding 请求 —— 让用户对开销有数 */
  embed_requests: number
  /** 同一份内容之前已经传过。不拦截,但必须说出来 */
  duplicate: boolean
  existing_status: KnowledgeDocStatus | null
  warnings: string[]
  preview: KnowledgeDocChunkPreview[]
}

export interface KnowledgeDocListResponse {
  success: boolean
  docs: KnowledgeDocSummary[]
  summary: KnowledgeDocsSummary
}

export interface KnowledgeDocDetail {
  success: boolean
  doc: KnowledgeDocSummary & { content_hash?: string }
  chunks: KnowledgeDocChunkPreview[]
}

export interface KnowledgeRagToggle {
  success: boolean
  enabled: boolean
  enabled_source: 'env' | 'runtime'
  message: string
}

export interface KnowledgeSearchRequest {
  query: string
  namespace: KnowledgeNamespaceFilter
  top_k?: number
}

export interface KnowledgeSearchResponse {
  success: boolean
  hits: KnowledgeHit[]
  elapsed_ms: number
  /** 一层成功一层失败。前端要提示「仅部分层返回结果」 */
  partial: boolean
  /**
   * 检索失败原因。后端 retrieve() 内部吞掉异常返回空列表,
   * 不读这个字段的话「连不上 Milvus」和「没有命中」在界面上长得一样。
   */
  error: string | null
  /** 每层现有条数。-1 表示这一层统计不出来。用于区分「库为空」和「没命中」 */
  namespace_counts: Record<string, number>
}

export interface KnowledgeIngestPreview {
  success: boolean
  source: KnowledgeIngestSource
  poi: { total: number; per_city: Record<string, number>; path?: string; size_kb?: number; exists?: boolean }
  guides: {
    total: number
    per_file: Record<string, number>
    files?: Array<{ name: string; sections: number; size_kb: number }>
    dir?: string
    exists?: boolean
  }
  embed_requests: number
  note: string
}

export type KnowledgeIngestState = 'pending' | 'running' | 'done' | 'error'

export interface KnowledgeIngestTask {
  success: boolean
  task_id: string
  state: KnowledgeIngestState
  source: string
  recreate: boolean
  total: number
  processed: number
  current: string | null
  started_at: string
  finished_at: string | null
  results: Array<{
    namespace: string
    collection: string
    count: number
    cities?: string[]
    files?: Record<string, number>
  }>
  error: string | null
  message: string
}

export interface KnowledgeSources {
  success: boolean
  poi_inventory: {
    total: number
    per_city: Record<string, number>
    path?: string
    size_kb?: number
    exists?: boolean
  }
  city_guides: {
    total: number
    per_file: Record<string, number>
    files?: Array<{ name: string; sections: number; size_kb: number }>
    dir?: string
    exists?: boolean
  }
}

