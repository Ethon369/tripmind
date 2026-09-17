/**
 * 本地存储的键名集中定义
 *
 * 原来 'tripPlan' 这个字符串散在 Home.vue 和 Result.vue 两处，
 * 改名要全局搜、还容易漏。收在这里之后，
 * 组件只通过 usePlanCache() 访问，不直接碰 localStorage/sessionStorage。
 *
 * 用 sessionStorage 而不是 localStorage 是既有决策：
 * 行程数据可能很大（几十 KB），且只在当前标签页有意义；
 * 真正的持久化在后端 plan_store（SQLite）里，这里只是「跳转时不用等接口」的缓存。
 */

export const STORAGE_KEYS = {
  /** 刚生成的行程。Home 写入，Result 读取 */
  TRIP_PLAN: 'tripPlan',
  /**
   * 创建行程的草稿。首页「开始规划」写入，Create 页读取。
   *
   * 为什么需要它：首页只让用户写一句描述 + 挑城市，详细的日期与偏好
   * 挪到了独立的创建页。这两页之间要传递"用户刚才说了什么"。
   * 放 sessionStorage 而不是 URL query —— 描述可能很长，塞进地址栏既难看又有长度限制。
   */
  TRIP_DRAFT: 'tripDraft',
} as const;

export type StorageKey = (typeof STORAGE_KEYS)[keyof typeof STORAGE_KEYS];
