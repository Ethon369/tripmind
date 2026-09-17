<template>
  <!-- 结果页骨架屏：形状贴近真实内容，减少加载完成时的视觉跳动 -->
  <div v-if="variant === 'plan'" class="sk-plan" aria-busy="true">
    <div class="sk-row">
      <div class="sk-col-left">
        <div class="skeleton-block sk-card sk-h-140"></div>
        <div class="skeleton-block sk-card sk-h-180"></div>
      </div>
      <div class="skeleton-block sk-card sk-col-right sk-h-360"></div>
    </div>
    <div class="skeleton-block sk-card sk-h-220 sk-mt"></div>
  </div>

  <div v-else-if="variant === 'list'" class="sk-list" aria-busy="true">
    <div v-for="i in count" :key="i" class="skeleton-block sk-card sk-h-88 sk-mb"></div>
  </div>

  <div v-else class="sk-table" aria-busy="true">
    <div class="skeleton-block sk-h-44 sk-mb"></div>
    <div v-for="i in count" :key="i" class="skeleton-block sk-h-56 sk-mb"></div>
  </div>
</template>

<script setup lang="ts">
/**
 * 骨架屏。
 *
 * 为什么要有它：结果页原来是 `v-if="tripPlan" ... v-else <a-empty 没有找到旅行计划数据>`,
 * 而 tripPlan 在 await getPlan() 返回之前是 falsy —— 于是**加载期间用户看到的是
 * 「暂无数据 / 请先创建行程」**，一秒后才被真实内容顶掉。
 * 骨架屏把这个空窗期填成「正在加载」的视觉语言。
 */
withDefaults(
  defineProps<{
    variant?: 'plan' | 'list' | 'table';
    /** 列表/表格骨架的重复行数 */
    count?: number;
  }>(),
  {
    variant: 'plan',
    count: 5,
  }
);
</script>

<style scoped>
.sk-plan,
.sk-list,
.sk-table {
  width: 100%;
}

.sk-row {
  display: flex;
  gap: var(--sp-5);
  align-items: stretch;
}

.sk-col-left {
  flex: 0 0 400px;
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.sk-col-right {
  flex: 1;
  min-width: 0;
}

.sk-card {
  border-radius: var(--radius-md);
}

.sk-h-44 {
  height: 44px;
}
.sk-h-56 {
  height: 56px;
}
.sk-h-88 {
  height: 88px;
}
.sk-h-140 {
  height: 140px;
}
.sk-h-180 {
  height: 180px;
}
.sk-h-220 {
  height: 220px;
}
.sk-h-360 {
  height: 360px;
}

.sk-mb {
  margin-bottom: var(--sp-3);
}

.sk-mt {
  margin-top: var(--sp-5);
}

/* 与结果页同样的响应式：窄屏不再保留 400px 固定左栏 */
@media (max-width: 991px) {
  .sk-row {
    flex-direction: column;
  }

  .sk-col-left {
    flex: 1 1 auto;
  }

  .sk-col-right {
    height: 260px;
  }
}
</style>
