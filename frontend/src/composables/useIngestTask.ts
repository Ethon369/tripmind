import { ref, computed, onUnmounted } from 'vue';
import { startKnowledgeIngest, getKnowledgeIngestTask } from '@/services/api';
import type { KnowledgeIngestSource, KnowledgeIngestTask } from '@/types';

/** 轮询间隔。灌库是分钟级任务，2 秒足够细腻，也不会把后端打满 */
const POLL_INTERVAL_MS = 2000;

/** 连续失败多少次才停止轮询。偶发网络抖动不该中断一个正在跑的任务 */
const MAX_POLL_FAILURES = 3;

/**
 * 灌库任务的提交与进度轮询。
 *
 * 后端把灌库做成了长任务（1750 条要 55 批 embedding 请求，分钟级），
 * 所以这里必须轮询 —— 不能用一次同步请求等它跑完。
 *
 * `submitFn` 默认是内置库的灌库接口;上传攻略走的是另一个接口
 * （POST /knowledge/docs/{id}/ingest），但**任务结构与轮询方式完全一致** ——
 * 传一个自己的提交函数进来即可复用,不必为它再写一套轮询。
 *
 * ⚠️ 页面刷新会失去 task_id，此时后端任务仍在继续，前端只是不再跟踪。
 * 这是可接受的取舍：与其做一套任务列表恢复机制，不如在界面上说清楚
 * 「刷新后请稍等片刻再点刷新计数」。硬要恢复跟踪反而容易显示过期状态。
 */
export function useIngestTask(
  onFinished?: () => void,
  submitFn: (source: KnowledgeIngestSource, recreate: boolean) => Promise<KnowledgeIngestTask> = startKnowledgeIngest
) {
  const task = ref<KnowledgeIngestTask | null>(null);
  const submitting = ref(false);
  const polling = ref(false);
  const error = ref('');

  let timer: ReturnType<typeof setInterval> | null = null;
  let failures = 0;

  /** 真实进度。后端能给准确的 processed/total，所以这里不做假进度 */
  const progress = computed(() => {
    const t = task.value;
    if (!t || !t.total) return 0;
    return Math.min(100, Math.round((t.processed / t.total) * 1000) / 10);
  });

  const isRunning = computed(() => {
    const s = task.value?.state;
    return s === 'pending' || s === 'running';
  });

  function stopPolling() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    polling.value = false;
  }

  async function pollOnce() {
    const current = task.value;
    if (!current) return;

    try {
      const next = await getKnowledgeIngestTask(current.task_id);
      task.value = next;
      failures = 0;

      if (next.state === 'done' || next.state === 'error') {
        stopPolling();
        if (next.state === 'done') onFinished?.();
      }
    } catch (e: unknown) {
      failures += 1;
      if (failures >= MAX_POLL_FAILURES) {
        stopPolling();
        error.value = (e as Error)?.message || '查询任务进度失败';
      }
    }
  }

  function startPolling() {
    stopPolling();
    polling.value = true;
    timer = setInterval(pollOnce, POLL_INTERVAL_MS);
  }

  /**
   * 提交任务。
   *
   * recreate=true 时后端会要求 confirm='DROP'（由 api 层拼上），
   * 但**前端的二次确认弹窗不可省略** —— 它是给用户看的，
   * 而 api 层那个字段只是满足接口约定。
   */
  async function submit(source: KnowledgeIngestSource, recreate = false) {
    submitting.value = true;
    error.value = '';
    try {
      const created = await submitFn(source, recreate);
      task.value = created;
      failures = 0;
      startPolling();
    } catch (e: unknown) {
      error.value = (e as Error)?.message || '提交灌库任务失败';
    } finally {
      submitting.value = false;
    }
  }

  /** 清掉当前任务面板（用户看完结果后手动关闭） */
  function reset() {
    stopPolling();
    task.value = null;
    error.value = '';
    failures = 0;
  }

  // 组件卸载时必须停掉定时器，否则离开页面后还在每 2 秒打一次接口
  onUnmounted(stopPolling);

  return {
    task,
    submitting,
    polling,
    error,
    progress,
    isRunning,
    submit,
    reset,
    stopPolling,
  };
}
