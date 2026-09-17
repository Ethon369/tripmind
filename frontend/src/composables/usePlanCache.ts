import type { TripPlan } from '@/types';
import { STORAGE_KEYS } from '@/constants/storage';

/**
 * 行程本地缓存的唯一入口。
 *
 * 存在的意义：
 * 1. 消除隐式的 sessionStorage 契约 —— 原来 'tripPlan' 这个键名散在
 *    Home.vue 与 Result.vue 里，两边各写各的 JSON.parse / stringify。
 * 2. 承载「坏数据处理」—— 缓存里的数据是**可能损坏**的（早先版本写入的、
 *    手工改过的、写到一半被中断的）。原来 Result.vue 里有一段
 *    try/catch + removeItem，逻辑是对的但散在组件中，换个人来写很容易漏掉，
 *    结果就是每次进页面都在同一个地方崩。
 *
 * 注意这只是**缓存**，不是真相来源。真正的持久化在后端 plan_store。
 * 带 plan_id 的页面应该优先走接口，缓存只用于「刚生成完、不想等接口」这一条路径。
 */

const isTripPlan = (v: unknown): v is TripPlan => {
  if (!v || typeof v !== 'object') return false;
  const p = v as Partial<TripPlan>;
  return typeof p.city === 'string' && Array.isArray(p.days);
};

export function usePlanCache() {
  /** 写入。容量超限（QuotaExceeded）时静默失败 —— 缓存丢了不影响主流程 */
  const save = (plan: TripPlan): void => {
    try {
      sessionStorage.setItem(STORAGE_KEYS.TRIP_PLAN, JSON.stringify(plan));
    } catch (e) {
      console.warn('行程缓存写入失败（不影响使用）:', e);
    }
  };

  /**
   * 读取。结构不合法或解析失败时**自动清理脏数据**并返回 null，
   * 避免每次进页面都在同一处崩。
   */
  const load = (): TripPlan | null => {
    const raw = sessionStorage.getItem(STORAGE_KEYS.TRIP_PLAN);
    if (!raw) return null;

    try {
      const parsed = JSON.parse(raw);
      if (!isTripPlan(parsed)) {
        console.warn('行程缓存结构不合法，已清理');
        sessionStorage.removeItem(STORAGE_KEYS.TRIP_PLAN);
        return null;
      }
      return parsed;
    } catch (e) {
      console.warn('行程缓存解析失败，已清理:', e);
      sessionStorage.removeItem(STORAGE_KEYS.TRIP_PLAN);
      return null;
    }
  };

  const clear = (): void => {
    sessionStorage.removeItem(STORAGE_KEYS.TRIP_PLAN);
  };

  return { save, load, clear };
}
