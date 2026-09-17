<template>
  <ul class="pcl">
    <li v-for="row in rows" :key="row.id" class="pcl-item">
      <!-- 整张卡可点进入详情。用 button 语义而不是 div+click：
           键盘能 Tab 到、回车能触发，屏幕阅读器会读成可操作元素。 -->
      <button type="button" class="pcl-main" @click="emit('open', row.id)">
        <span class="pcl-title">{{ row.title || `${row.city} ${row.travel_days}日游` }}</span>
        <span class="pcl-sub">{{ row.city }} · {{ row.attractions }} 个景点 · {{ row.travel_days }} 天</span>
        <span class="pcl-meta">
          <span>{{ fmtDate(row.start_date) }} 至 {{ fmtDate(row.end_date) }}</span>
          <span v-if="row.total_budget" class="u-num">预算 ¥{{ fmtNum(row.total_budget) }}</span>
        </span>
      </button>

      <div class="pcl-foot">
        <PlanStatusTag :status="row.status" :warnings="row.warnings" />
        <span class="pcl-cost u-num">{{ fmtCost(row.cost_cny) }}</span>
        <a-popconfirm
          title="删除后无法恢复，确定吗？"
          ok-text="删除"
          cancel-text="取消"
          @confirm="emit('remove', row.id)"
        >
          <a-button type="text" size="small" danger>删除</a-button>
        </a-popconfirm>
      </div>
    </li>
  </ul>
</template>

<script setup lang="ts">
import PlanStatusTag from './PlanStatusTag.vue';
import type { PlanSummary } from '@/types';

/**
 * 历史行程的手机形态。
 *
 * 为什么手机上不用表格：7 列合计约 970px 宽，375px 屏上要横向滚动，
 * 而且滚动时「行程标题」这个最重要的信息被挤成几个字、表头和内容还容易脱节。
 * 卡片能把「标题 + 城市 + 天数 + 状态」在窄屏里完整呈现。
 */
defineProps<{ rows: PlanSummary[] }>();

const emit = defineEmits<{
  open: [id: string];
  remove: [id: string];
}>();

const fmtDate = (s: string) => (s || '').slice(0, 10) || '—';
const fmtCost = (n: number) => `¥${(n || 0).toFixed(4)}`;
const fmtNum = (n: number) => (n || 0).toLocaleString('zh-CN');
</script>

<style scoped>
.pcl {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.pcl-item {
  border: 1px solid var(--border-1);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  overflow: hidden;
}

/* 用 button 承载，重置掉默认样式 */
.pcl-main {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  width: 100%;
  padding: var(--sp-4);
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  min-height: var(--touch-min);
  transition: background-color var(--dur-fast) var(--ease-out);
}

.pcl-main:hover {
  background: var(--bg-hover);
}

.pcl-title {
  font-size: var(--fs-body);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.pcl-sub {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.pcl-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-1) var(--sp-3);
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.pcl-foot {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-2) var(--sp-4);
  border-top: 1px solid var(--border-2);
  background: var(--bg-sunken);
}

.pcl-cost {
  margin-left: auto;
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.pcl-foot :deep(.ant-btn) {
  min-height: var(--touch-min);
}
</style>
