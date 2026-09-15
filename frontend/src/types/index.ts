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

/** 单个行程详情。data 直接是 TripPlan,渲染逻辑可与新生成的行程复用。 */
export interface PlanDetailResponse {
  success: boolean
  message: string
  data?: TripPlan
  meta?: PlanSummary
}

