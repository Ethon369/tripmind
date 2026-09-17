<template>
  <section class="gp" aria-live="polite" aria-busy="true">
    <div class="gp-head">
      <span class="gp-pulse" aria-hidden="true"></span>
      <h3 class="gp-title">正在为你规划{{ city ? ` ${city}` : '' }}{{ days ? ` ${days} 天` : '' }}行程</h3>
    </div>

    <!--
      不确定进度条：纯装饰的流动光带，不表示「完成了百分之多少」。

      为什么不用百分比 —— 后端 POST /api/trip/plan 是一次性响应（没有 SSE /
      流式接口），整个过程中前端拿不到任何中间信号。原来那个
      setInterval 每 500ms 加 10%、封顶 90% 的进度条是编出来的：
      后端卡在 MCP 工具超时的那 60 秒里，它会一动不动停在 90%，
      用户以为页面死了就去刷新 —— 反而把这次生成丢掉了。

      诚实的做法就是承认不知道进度，但要让用户确信「页面还活着」。
      计时器承担的就是后一件事。
    -->
    <div class="gp-track" role="progressbar" aria-label="正在生成行程，进度未知">
      <div class="gp-bar"></div>
    </div>

    <div class="gp-meta">
      <span class="gp-timer u-num">已用 {{ elapsedText }}</span>
      <span class="gp-sep" aria-hidden="true">·</span>
      <span class="gp-hint">通常需要 30–60 秒</span>
    </div>

    <p v-if="isSlow" class="gp-slow">
      比平时慢了一些。后端可能正在重试地图工具调用，请再等一会儿 —— 刷新页面会丢掉这次生成。
    </p>
    <p v-else class="gp-warn">生成过程中请不要关闭或刷新页面。</p>

    <ul class="gp-stages">
      <li v-for="(s, i) in STAGES" :key="s" :class="{ 'is-active': i === activeStage }">
        {{ s }}
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';

/**
 * 生成中面板。
 *
 * 阶段文案是**推测性的提示**，不是后端回传的真实阶段 —— 界面上刻意用
 * 「轮播高亮」而不是「第 2/4 步」的形式表达，就是为了不把它伪装成精确进度。
 * 想做成真实阶段需要后端加 SSE，属于后续增强。
 */
const STAGES = [
  '搜索真实景点与坐标',
  '查询天气与路线',
  '筛选中转住宿',
  '整合成每日行程',
];

// 模板里直接访问 city / days（script setup 会自动解包 props），
// 不需要把它们赋给一个变量 —— 赋了反而触发 noUnusedLocals
withDefaults(
  defineProps<{
    city?: string;
    days?: number;
  }>(),
  { city: '', days: 0 }
);

const seconds = ref(0);
const activeStage = ref(0);

let timer: ReturnType<typeof setInterval> | null = null;
let stageTimer: ReturnType<typeof setInterval> | null = null;

const elapsedText = computed(() => {
  const s = seconds.value;
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  return `${m} 分 ${String(s % 60).padStart(2, '0')} 秒`;
});

/** 超过 90 秒给一句解释。此时用户最容易以为卡死了 */
const isSlow = computed(() => seconds.value >= 90);

onMounted(() => {
  timer = setInterval(() => {
    seconds.value += 1;
  }, 1000);

  // 每 6 秒推进一个阶段文案，循环播放
  stageTimer = setInterval(() => {
    activeStage.value = (activeStage.value + 1) % STAGES.length;
  }, 6000);
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
  if (stageTimer) clearInterval(stageTimer);
  timer = null;
  stageTimer = null;
});
</script>

<style scoped>
.gp {
  padding: var(--sp-6);
  background: var(--bg-sunken);
  border: 1px dashed var(--brand-300);
  border-radius: var(--radius-md);
  text-align: center;
}

.gp-head {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-3);
  margin-bottom: var(--sp-5);
}

.gp-pulse {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--brand-500);
  animation: pulse 1.6s var(--ease-inout) infinite;
  flex-shrink: 0;
}

.gp-title {
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
  margin: 0;
}

/* 不确定进度条：一条光带在轨道里来回流动，不表示具体百分比 */
.gp-track {
  position: relative;
  height: 8px;
  border-radius: var(--radius-pill);
  background: var(--brand-100);
  overflow: hidden;
}

.gp-bar {
  position: absolute;
  inset: 0 auto 0 0;
  width: 40%;
  border-radius: var(--radius-pill);
  background: var(--brand-gradient);
  animation: gp-slide 1.6s var(--ease-inout) infinite;
}

@keyframes gp-slide {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(250%);
  }
}

.gp-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: var(--sp-2);
  margin-top: var(--sp-4);
  color: var(--text-2);
  font-size: var(--fs-sm);
}

.gp-timer {
  font-weight: var(--fw-medium);
  color: var(--text-1);
}

.gp-sep {
  color: var(--text-3);
}

.gp-hint {
  color: var(--text-2);
}

.gp-warn,
.gp-slow {
  margin: var(--sp-3) 0 0;
  font-size: var(--fs-sm);
}

.gp-warn {
  color: var(--text-2);
}

.gp-slow {
  color: var(--warning);
}

.gp-stages {
  list-style: none;
  margin: var(--sp-5) 0 0;
  padding: var(--sp-4);
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--sp-2) var(--sp-4);
  border-top: 1px solid var(--border-2);
}

.gp-stages li {
  font-size: var(--fs-caption);
  color: var(--text-3);
  transition: color var(--dur-base) var(--ease-out);
}

.gp-stages li.is-active {
  color: var(--brand-600);
  font-weight: var(--fw-medium);
}

@media (max-width: 575px) {
  .gp {
    padding: var(--sp-4);
  }

  .gp-stages {
    flex-direction: column;
    align-items: center;
    gap: var(--sp-2);
  }
}
</style>
