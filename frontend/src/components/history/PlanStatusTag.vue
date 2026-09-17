<template>
  <span class="pst">
    <a-tag :color="info.color" class="pst-tag">{{ info.text }}</a-tag>
    <!-- 降级过的行程点进去可能数据不全，把原因透出来。
         用 tooltip 而不是常驻文本：列表里每行都摊开会让表格变得很难扫。 -->
    <a-tooltip v-if="warnings.length" :title="warnings.join('；')">
      <span class="pst-warn" role="img" aria-label="该行程有降级警告，悬停查看原因">!</span>
    </a-tooltip>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { planStatusOf } from '@/constants/tripOptions';

/**
 * 行程状态标签。全站唯一定义。
 *
 * 原来定义在 History.vue 的 script 里，结果页要用就得复制一份 ——
 * 两处文案不一致时（一边「部分降级」一边「已降级」）没人会发现。
 */
const props = withDefaults(
  defineProps<{
    status: string;
    warnings?: string[];
  }>(),
  { warnings: () => [] }
);

const info = computed(() => planStatusOf(props.status));
</script>

<style scoped>
.pst {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
}

.pst-tag {
  margin: 0;
}

/* 状态色本身是次要信息载体 —— 文字（已完成/部分降级）才是主载体，
   所以色盲用户不影响理解（WCAG 1.4.1 非颜色唯一信息） */
.pst-warn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--warning);
  color: #fff;
  font-size: 11px;
  font-weight: var(--fw-bold);
  cursor: help;
  flex-shrink: 0;
}
</style>
