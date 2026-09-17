import { STORAGE_KEYS } from '@/constants/storage';

/**
 * 创建行程的草稿传递。
 *
 * 首页（Home）只让用户写一句描述、挑一个城市，然后跳到创建页（Create）
 * 去填日期与偏好。这两页之间需要传递"用户刚才说了什么"。
 *
 * 用 sessionStorage 而不是 URL query：
 * 描述可能很长（上限 200 字），塞进地址栏既难看又有长度限制，
 * 而且用户刷新页面时草稿还在 —— 这正好是想要的。
 */

export interface TripDraft {
  /** 目的地城市（从热门城市卡片或首页输入框来） */
  city?: string;
  /** 用户的自然语言描述，最终作为 TripRequest.free_text_input 提交 */
  free_text_input?: string;
}

const MAX_LEN = 2000;

export function useTripDraft() {
  const save = (draft: TripDraft): void => {
    try {
      sessionStorage.setItem(STORAGE_KEYS.TRIP_DRAFT, JSON.stringify(draft));
    } catch (e) {
      // 写不进去不影响流程 —— 创建页会退化成空白表单
      console.warn('草稿写入失败（不影响创建）:', e);
    }
  };

  /**
   * 读取草稿。结构不合法时清掉并返回 null，
   * 避免脏数据让创建页在渲染阶段出错。
   */
  const load = (): TripDraft | null => {
    const raw = sessionStorage.getItem(STORAGE_KEYS.TRIP_DRAFT);
    if (!raw) return null;

    try {
      const parsed = JSON.parse(raw) as unknown;
      if (!parsed || typeof parsed !== 'object') throw new Error('不是对象');

      const d = parsed as TripDraft;
      const city = typeof d.city === 'string' ? d.city.slice(0, 50) : undefined;
      const free = typeof d.free_text_input === 'string' ? d.free_text_input.slice(0, MAX_LEN) : undefined;

      if (!city && !free) {
        sessionStorage.removeItem(STORAGE_KEYS.TRIP_DRAFT);
        return null;
      }
      return { city, free_text_input: free };
    } catch (e) {
      console.warn('草稿解析失败，已清理:', e);
      sessionStorage.removeItem(STORAGE_KEYS.TRIP_DRAFT);
      return null;
    }
  };

  const clear = (): void => {
    sessionStorage.removeItem(STORAGE_KEYS.TRIP_DRAFT);
  };

  return { save, load, clear };
}
