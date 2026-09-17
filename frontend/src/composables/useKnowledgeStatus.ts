import { ref, onMounted } from 'vue';
import { getKnowledgeStatus } from '@/services/api';
import type { KnowledgeStatus } from '@/types';

/**
 * 知识库状态。**分级加载**。
 *
 * 为什么要分两级：
 * `/knowledge/status` 里最慢的部分是「每层调一次 Milvus 的 exists() + count()」。
 * Milvus 没起来时这些调用可能长时间阻塞（后端有 8 秒超时保护，但 8 秒对首屏仍然太久）。
 *
 * 所以先发一个 with_counts=false 的请求，把「能不能用 / 开关开没开」先画出来；
 * 计数再单独补。这样即使计数失败，页面第一屏仍然有信息可看 ——
 * 而不是整页空白等一个数字。
 */
export function useKnowledgeStatus() {
  const status = ref<KnowledgeStatus | null>(null);
  const loading = ref(false);
  /** 首屏（可用性 + 开关）的失败原因 */
  const error = ref('');
  /** 计数部分的失败原因，与 error 分开 —— 计数失败不该让整页变成错误态 */
  const countsError = ref('');
  const loadingCounts = ref(false);

  async function loadFast() {
    status.value = await getKnowledgeStatus(false);
  }

  async function loadCounts() {
    loadingCounts.value = true;
    countsError.value = '';
    try {
      status.value = await getKnowledgeStatus(true);
    } catch (e: unknown) {
      countsError.value = (e as Error)?.message || '读取知识库计数失败';
    } finally {
      loadingCounts.value = false;
    }
  }

  async function refresh() {
    loading.value = true;
    error.value = '';
    countsError.value = '';
    try {
      await loadFast();
      // 首屏拿到之后立刻补计数，不阻塞渲染
      await loadCounts();
    } catch (e: unknown) {
      status.value = null;
      error.value = (e as Error)?.message || '读取知识库状态失败';
    } finally {
      loading.value = false;
    }
  }

  /** 灌库完成后只需要刷新计数，不必重跑一次可用性检查 */
  const refreshCounts = loadCounts;

  // 不做自动轮询：知识库状态不会自己变化，定时拉取只是白白压后端。
  // 页面提供「刷新」按钮，灌库完成后由 useIngestTask 主动触发 refreshCounts。
  onMounted(refresh);

  return { status, loading, error, countsError, loadingCounts, refresh, refreshCounts };
}
