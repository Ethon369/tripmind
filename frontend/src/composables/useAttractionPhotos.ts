import { ref, onUnmounted } from 'vue';
import { API_BASE_URL } from '@/services/api';
import type { TripPlan } from '@/types';

/**
 * 景点配图的批量获取。
 *
 * 原来这段逻辑直接写在 Result.vue 里：对每个景点并发发一个
 * `/api/poi/photo?name=` 请求。问题是：
 * - **没有并发上限**：20 个景点就是 20 个并发请求，浏览器会排队，
 *   排在后面的长时间出不来，而且会把后端打出一片 429/超时。
 * - **没有去重**：同一个景点出现在两天里就会请求两次。
 * - **没有取消机制**：组件卸载后请求还在跑，回来时往已销毁的引用上写数据。
 *
 * 这里补齐这三点，并把「失败降级为渐变占位图」的责任明确放在调用方
 * （组件拿不到 url 就自己画占位图，不需要这里造一个假 url）。
 */

/** 同时最多 4 个请求。再多对首屏没有帮助，只会让请求排队 */
const CONCURRENCY = 4;

async function mapWithLimit<T>(items: T[], limit: number, fn: (item: T) => Promise<void>) {
  const queue = [...items];
  const workerCount = Math.min(limit, queue.length);

  const workers = Array.from({ length: workerCount }, async () => {
    for (;;) {
      const next = queue.shift();
      if (next === undefined) return;
      await fn(next);
    }
  });

  await Promise.all(workers);
}

export function useAttractionPhotos() {
  const photos = ref<Record<string, string>>({});
  const loading = ref(false);

  /**
   * 递增的代号。每次 load 自增，回调里比对当前代号 ——
   * 对不上说明这次请求已经过期（用户切了行程 / 组件已卸载），直接丢弃结果。
   * 比逐个 AbortController 简单，且对 fetch 的兼容性要求最低。
   */
  let generation = 0;

  async function fetchOne(name: string, gen: number): Promise<void> {
    try {
      const res = await fetch(`${API_BASE_URL}/api/poi/photo?name=${encodeURIComponent(name)}`);
      const data = await res.json();
      if (gen !== generation) return;
      if (data?.success && data?.data?.photo_url) {
        photos.value = { ...photos.value, [name]: data.data.photo_url };
      }
    } catch (e) {
      // 配图拿不到不影响行程阅读，静默降级。这里不打 console.error，
      // 免得网络不好时控制台被几十条同样的错误刷屏。
      if (gen === generation) {
        console.debug(`景点配图获取失败（已降级为占位图）: ${name}`, e);
      }
    }
  }

  /** 拉取整份行程的配图。重复调用会重新开始，旧的一批结果自动作废 */
  async function load(plan: TripPlan): Promise<void> {
    const names = new Set<string>();
    plan.days?.forEach((day) => {
      day.attractions?.forEach((a) => {
        if (a?.name) names.add(a.name);
      });
    });

    if (!names.size) {
      photos.value = {};
      return;
    }

    generation += 1;
    const gen = generation;
    loading.value = true;

    try {
      await mapWithLimit([...names], CONCURRENCY, (name) => fetchOne(name, gen));
    } finally {
      if (gen === generation) loading.value = false;
    }
  }

  function photoOf(name: string): string | undefined {
    return photos.value[name];
  }

  function reset() {
    generation += 1; // 让在途请求的结果失效
    photos.value = {};
    loading.value = false;
  }

  onUnmounted(reset);

  return { photos, loading, load, photoOf, reset };
}

/**
 * 拿不到真实配图时用的渐变占位图（内联 SVG，不发请求）。
 * 按序号轮换色板，让同一天的景点卡片不至于全是同一个颜色。
 */
export function placeholderImage(name: string, index: number): string {
  const palettes = [
    ['#667eea', '#764ba2'],
    ['#f093fb', '#f5576c'],
    ['#4facfe', '#00f2fe'],
    ['#43e97b', '#38f9d7'],
    ['#fa709a', '#fee140'],
  ];
  const [start, end] = palettes[index % palettes.length];

  // 名字里的特殊字符要先转义，否则会破坏 SVG 结构
  const safeName = name.replace(/[<>&"']/g, '');
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">
    <defs>
      <linearGradient id="g${index}" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="${start}" stop-opacity="1" />
        <stop offset="100%" stop-color="${end}" stop-opacity="1" />
      </linearGradient>
    </defs>
    <rect width="400" height="300" fill="url(#g${index})"/>
    <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle"
      font-family="sans-serif" font-size="24" font-weight="bold" fill="#ffffff">${safeName}</text>
  </svg>`;

  // btoa 只接受 latin1，中文要先经 encodeURIComponent 转义
  return `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`;
}
