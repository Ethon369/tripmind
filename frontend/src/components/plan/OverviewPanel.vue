<template>
  <div class="overview">
    <!-- ---------------- 城市 ---------------- -->
    <section class="city">
      <div class="city-head">
        <h2 class="city-name">{{ plan.city }}</h2>
        <span class="city-count u-num">{{ totalAttractions }} 个景点</span>
      </div>
      <p v-if="plan.overall_suggestions" class="city-desc">{{ plan.overall_suggestions }}</p>
      <p class="city-range u-num">{{ plan.start_date }} 至 {{ plan.end_date }}</p>
    </section>

    <!-- ---------------- 预算 ---------------- -->
    <section v-if="plan.budget" class="block">
      <h3 class="block-title">预算明细</h3>
      <div class="budget-grid">
        <div class="budget-item">
          <span class="budget-label">景点门票</span>
          <span class="budget-value u-num">¥{{ plan.budget.total_attractions }}</span>
        </div>
        <div class="budget-item">
          <span class="budget-label">酒店住宿</span>
          <span class="budget-value u-num">¥{{ plan.budget.total_hotels }}</span>
        </div>
        <div class="budget-item">
          <span class="budget-label">餐饮费用</span>
          <span class="budget-value u-num">¥{{ plan.budget.total_meals }}</span>
        </div>
        <div class="budget-item">
          <span class="budget-label">交通费用</span>
          <span class="budget-value u-num">¥{{ plan.budget.total_transportation }}</span>
        </div>
      </div>
      <div class="budget-total">
        <span>预估总费用</span>
        <strong class="u-num">¥{{ plan.budget.total }}</strong>
      </div>
    </section>

    <!-- ---------------- 每日摘要 ----------------
         每一天一行：标题 + 汇总数字 + 景点条。
         点整行跳到那一天的详情 —— 这是"总览 → 某天"最自然的入口。 -->
    <section class="block">
      <h3 class="block-title">行程一览</h3>

      <ul class="days">
        <li v-for="(day, i) in plan.days" :key="day.date" class="day-item">
          <button type="button" class="day-row" @click="emit('openDay', i)">
            <span class="day-head">
              <span class="day-no">Day {{ day.day_index + 1 }}</span>
              <span class="day-date u-num">{{ day.date }}</span>
              <span class="day-stat u-num">
                {{ summaryOf(day).attractions }} 个景点
                <template v-if="summaryOf(day).travelMeters">
                  · {{ formatDistance(summaryOf(day).travelMeters) }}
                </template>
                · {{ formatDuration(summaryOf(day).totalMinutes) }}
              </span>
            </span>

            <span class="day-chips">
              <span
                v-for="(a, ai) in day.attractions"
                :key="`${ai}-${a.name}`"
                class="day-chip"
              >
                {{ a.name }}
                <em v-if="a.visit_duration" class="u-num">{{ formatDuration(a.visit_duration) }}</em>
              </span>
              <span v-if="!day.attractions?.length" class="day-chip is-empty">暂无安排</span>
            </span>
          </button>
        </li>
      </ul>
    </section>

    <!-- ---------------- 天气 ---------------- -->
    <section v-if="plan.weather_info?.length" class="block">
      <h3 class="block-title">天气</h3>
      <ul class="weather-grid">
        <li v-for="w in plan.weather_info" :key="w.date" class="weather-card">
          <p class="weather-date u-num">{{ w.date }}</p>
          <div class="weather-row">
            <span class="weather-slot">白天</span>
            <span>{{ w.day_weather }}</span>
            <span class="weather-temp u-num">{{ w.day_temp }}°C</span>
          </div>
          <div class="weather-row">
            <span class="weather-slot">夜间</span>
            <span>{{ w.night_weather }}</span>
            <span class="weather-temp u-num">{{ w.night_temp }}°C</span>
          </div>
          <p class="weather-wind">{{ w.wind_direction }} {{ w.wind_power }}</p>
        </li>
      </ul>
    </section>

    <!-- ---------------- 知识出处 ----------------
         放在总览页而不是单独一页:它是"这份行程的可信度依据",
         看过总览的人顺手就能核对来源。 -->
    <section v-if="knowledge.length" class="block">
      <div class="block-head">
        <h3 class="block-title">这份行程参考了什么</h3>
        <span class="block-note u-num">{{ knowledge.length }} 条</span>
      </div>

      <ul class="knowledge-list">
        <li
          v-for="(k, i) in visibleKnowledge"
          :key="`${k.source}-${k.heading_path}-${i}`"
          class="knowledge-item"
        >
          <div class="knowledge-head">
            <a-tag :color="knowledgeNamespaceOf(k.namespace).color" class="knowledge-tag">
              {{ knowledgeNamespaceOf(k.namespace).text }}
            </a-tag>
            <span class="knowledge-where u-num">
              {{ k.source }}<template v-if="k.heading_path"> &gt; {{ k.heading_path }}</template>
            </span>
            <span class="knowledge-score u-num">{{ k.score.toFixed(2) }}</span>
          </div>
          <p class="knowledge-snippet">{{ k.snippet }}</p>
        </li>
      </ul>

      <a-button
        v-if="knowledge.length > PREVIEW"
        type="link"
        size="small"
        class="knowledge-more"
        @click="showAll = !showAll"
      >
        {{ showAll ? '收起' : `展开全部 ${knowledge.length} 条` }}
      </a-button>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { knowledgeNamespaceOf } from '@/constants/tripOptions';
import { summarizeDay, formatDistance, formatDuration } from '@/utils/tripTimeline';
import type { DayPlan, KnowledgeSource, TripPlan } from '@/types';

/**
 * 总览页。
 *
 * 结构来自参考产品：城市卡 → 预算 → 行程一览（每天一行 + 景点条）→ 天气 → 知识出处。
 *
 * "行程一览"是这一页的核心 —— 它让用户在**一屏里看完整个行程的骨架**，
 * 点任意一行就进那一天的详情。没有它，多天行程只能靠滚动去数第几天。
 */

const props = withDefaults(
  defineProps<{
    plan: TripPlan;
    knowledge?: KnowledgeSource[];
  }>(),
  { knowledge: () => [] }
);

const emit = defineEmits<{ openDay: [index: number] }>();

const PREVIEW = 4;

const showAll = ref(false);

const knowledge = computed(() => props.knowledge);
const visibleKnowledge = computed(() =>
  showAll.value ? knowledge.value : knowledge.value.slice(0, PREVIEW)
);

const totalAttractions = computed(() =>
  (props.plan.days || []).reduce((n, d) => n + (d.attractions?.length || 0), 0)
);

/** 缓存每天汇总，避免模板里对同一份数据反复算（有 haversine，不是零成本） */
const summaryCache = new Map<string, ReturnType<typeof summarizeDay>>();
function summaryOf(day: DayPlan) {
  const key = `${day.date}-${day.attractions?.length ?? 0}-${day.attractions?.map((a) => a.visit_duration).join(',')}`;
  let cached = summaryCache.get(key);
  if (!cached) {
    cached = summarizeDay(day);
    summaryCache.set(key, cached);
  }
  return cached;
}
</script>

<style scoped>
.overview {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

/* ---------------- 城市 ---------------- */
.city {
  padding-bottom: var(--space-5);
  border-bottom: 1px solid var(--line-1);
}

.city-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
}

.city-name {
  font-size: var(--fs-h1);
  font-weight: var(--fw-semibold);
  letter-spacing: var(--ls-tight);
  color: var(--text-1);
}

.city-count {
  padding: 2px var(--space-3);
  border-radius: var(--radius-pill);
  background: var(--bg-tint);
  color: var(--brand-ink);
  font-size: var(--fs-caption);
}

.city-desc {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--text-2);
  max-width: 68ch;
  margin-bottom: var(--space-2);
}

.city-range {
  font-size: var(--fs-caption);
  color: var(--text-3);
}

/* ---------------- 通用区块 ---------------- */
.block-title {
  font-size: var(--fs-micro);
  font-weight: var(--fw-semibold);
  color: var(--text-2);
  margin-bottom: var(--space-3);
}

.block-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.block-note {
  font-size: var(--fs-caption);
  color: var(--text-3);
}

/* ---------------- 预算 ---------------- */
.budget-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.budget-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3) var(--space-4);
  background: var(--bg-sunken);
  border-radius: var(--radius-md);
}

.budget-label {
  font-size: var(--fs-micro);
  color: var(--text-2);
}

.budget-value {
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.budget-total {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: var(--space-4) var(--space-5);
  border-radius: var(--radius-md);
  background: var(--brand-gradient);
  color: #fff;
  font-size: var(--fs-caption);
}

.budget-total strong {
  font-size: var(--fs-h2);
  font-weight: var(--fw-semibold);
}

/* ---------------- 行程一览 ---------------- */
.days {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.day-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-4);
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  border-radius: var(--radius-md);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--ease-out);
}

.day-row:hover {
  border-color: var(--brand-300);
  box-shadow: var(--shadow-2);
  transform: translateY(-1px);
}

.day-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.day-no {
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.day-date {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.day-stat {
  margin-left: auto;
  font-size: var(--fs-micro);
  color: var(--text-3);
}

.day-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.day-chip {
  display: inline-flex;
  align-items: baseline;
  gap: var(--space-1);
  padding: 2px var(--space-3);
  border-radius: var(--radius-pill);
  background: var(--bg-tint);
  color: var(--brand-ink);
  font-size: var(--fs-micro);
}

.day-chip em {
  font-style: normal;
  color: var(--text-2);
}

.day-chip.is-empty {
  background: var(--bg-sunken);
  color: var(--text-3);
}

/* ---------------- 天气 ---------------- */
.weather-grid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: var(--space-3);
}

.weather-card {
  padding: var(--space-3) var(--space-4);
  background: var(--bg-sunken);
  border-radius: var(--radius-md);
}

.weather-date {
  font-size: var(--fs-caption);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
  margin-bottom: var(--space-2);
}

.weather-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--fs-caption);
  color: var(--text-1);
  padding: 1px 0;
}

.weather-slot {
  min-width: 28px;
  color: var(--text-3);
  font-size: var(--fs-micro);
}

.weather-temp {
  margin-left: auto;
  font-weight: var(--fw-medium);
}

.weather-wind {
  margin-top: var(--space-2);
  font-size: var(--fs-micro);
  color: var(--text-3);
}

/* ---------------- 知识出处 ---------------- */
.knowledge-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.knowledge-item {
  padding: var(--space-4);
  background: var(--bg-sunken);
  border-radius: var(--radius-md);
}

.knowledge-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
  flex-wrap: wrap;
}

.knowledge-tag {
  flex-shrink: 0;
}

.knowledge-where {
  flex: 1;
  min-width: 0;
  font-size: var(--fs-micro);
  color: var(--text-2);
  word-break: break-all;
}

.knowledge-score {
  flex-shrink: 0;
  font-size: var(--fs-caption);
  font-weight: var(--fw-semibold);
  color: var(--brand-600);
}

.knowledge-snippet {
  font-size: var(--fs-caption);
  line-height: var(--lh-body);
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.knowledge-more {
  margin-top: var(--space-3);
  padding: 0;
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 575px) {
  .budget-grid {
    gap: var(--space-2);
  }

  .day-stat {
    margin-left: 0;
    width: 100%;
  }

  .day-row {
    padding: var(--space-3);
  }
}
</style>
