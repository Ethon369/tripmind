/**
 * 表单选项与状态映射 —— 原来硬编码在 Home.vue / History.vue 的模板与脚本里
 *
 * 抽出来的好处：加一个偏好标签、改一个状态文案，只动这里。
 */

export interface Option {
  value: string;
  /** 展示文案。emoji 属于装饰性前缀，选项本身有文字，不做无障碍特殊处理 */
  label: string;
}

/** 交通方式。值是中文，直接发给后端（后端 Transportation 是自由字符串） */
export const TRANSPORTATION_OPTIONS: Option[] = [
  { value: '公共交通', label: '🚇 公共交通' },
  { value: '自驾', label: '🚗 自驾' },
  { value: '步行', label: '🚶 步行' },
  { value: '混合', label: '🔀 混合' },
];

/** 住宿偏好 */
export const ACCOMMODATION_OPTIONS: Option[] = [
  { value: '经济型酒店', label: '💰 经济型酒店' },
  { value: '舒适型酒店', label: '🏨 舒适型酒店' },
  { value: '豪华酒店', label: '⭐ 豪华酒店' },
  { value: '民宿', label: '🏡 民宿' },
];

/** 旅行偏好标签 */
export const PREFERENCE_OPTIONS: Option[] = [
  { value: '历史文化', label: '🏛️ 历史文化' },
  { value: '自然风光', label: '🏞️ 自然风光' },
  { value: '美食', label: '🍜 美食' },
  { value: '购物', label: '🛍️ 购物' },
  { value: '艺术', label: '🎨 艺术' },
  { value: '休闲', label: '☕ 休闲' },
];

/** 表单默认值。与后端 TripRequest 的默认语义对齐 */
export const FORM_DEFAULTS = {
  transportation: '公共交通',
  accommodation: '经济型酒店',
  preferences: [] as string[],
};

/* ---------------- 校验边界 ----------------
 * 这些数字必须与后端 pydantic 约束一致，否则会出现
 * 「前端放行、后端 422」。核对来源：backend/app/models/schemas.py
 */
export const LIMITS = {
  /** TripRequest.travel_days: Field(ge=1, le=30) */
  MIN_DAYS: 1,
  MAX_DAYS: 30,
  /** 后端 city 无长度限制，前端加保护以免超长城市名拖垮 prompt */
  MAX_CITY_LEN: 20,
  /** 后端 free_text_input 无长度限制，前端加保护 */
  MAX_FREE_TEXT_LEN: 200,
  /** 偏好标签上限：过多会让规划 prompt 发散，也与「偏好」语义不符 */
  MAX_PREFERENCES: 3,
} as const;

/** 行程状态 → tag 颜色与文案。全站唯一来源（历史页、结果页警示条共用） */
export const PLAN_STATUS_MAP: Record<string, { color: string; text: string }> = {
  ok: { color: 'success', text: '已完成' },
  fallback: { color: 'warning', text: '部分降级' },
  error: { color: 'error', text: '生成失败' },
  running: { color: 'processing', text: '生成中' },
};

export function planStatusOf(status: string): { color: string; text: string } {
  return PLAN_STATUS_MAP[status] || { color: 'default', text: status };
}

/** 餐饮类型 → 中文。后端 Meal.type 是 breakfast/lunch/dinner/snack */
export const MEAL_LABELS: Record<string, string> = {
  breakfast: '早餐',
  lunch: '午餐',
  dinner: '晚餐',
  snack: '小吃',
};

export function mealLabelOf(type: string): string {
  return MEAL_LABELS[type] || type;
}

/** 知识层 namespace → 展示名与徽标色 */
export const KNOWLEDGE_NAMESPACE_MAP: Record<string, { text: string; color: string }> = {
  poi_facts: { text: 'POI 事实', color: 'default' },
  city_guides: { text: '城市攻略', color: 'purple' },
};

export function knowledgeNamespaceOf(ns: string): { text: string; color: string } {
  return KNOWLEDGE_NAMESPACE_MAP[ns] || { text: ns, color: 'default' };
}

/** 检索调试的预置示例。紫禁城/兵马俑 走的是 POI 别名，是最能体现检索效果的两条 */
export const KNOWLEDGE_SAMPLE_QUERIES = [
  '北京 历史文化 一日游',
  '紫禁城',
  '兵马俑',
  '成都 美食',
] as const;
