<template>
  <div class="day">
    <!-- ---------------- 天头 ---------------- -->
    <header class="day-head">
      <div class="day-title-row">
        <h2 class="day-title">Day {{ day.day_index + 1 }}</h2>
        <time class="day-date u-num">{{ day.date }}</time>
      </div>

      <p v-if="day.description" class="day-desc">{{ day.description }}</p>

      <ul class="day-stats">
        <li><strong class="u-num">{{ stats.attractions }}</strong> 个景点</li>
        <li v-if="stats.travelMeters">
          <strong class="u-num">{{ formatDistance(stats.travelMeters) }}</strong> 路程
        </li>
        <li><strong class="u-num">{{ formatDuration(stats.totalMinutes) }}</strong> 在外</li>
      </ul>
    </header>

    <!-- ---------------- 时间轴 ---------------- -->
    <ol v-if="stops.length" class="timeline">
      <li v-for="(stop, i) in stops" :key="`${i}-${stop.attraction.name}`" class="stop">
        <!-- 交通衔接。夹在两个点之间，标"预估"是因为它是直线距离推的，不是真实路线 -->
        <div v-if="stop.leg" class="leg">
          <span class="leg-rail" aria-hidden="true"></span>
          <span class="leg-text u-num">
            {{ stop.leg.mode }} {{ formatDistance(stop.leg.meters) }} · 约
            {{ stop.leg.minutes }} 分钟
          </span>
        </div>

        <div class="stop-row">
          <time class="stop-time u-num">{{ stop.time }}</time>

          <span class="stop-node" aria-hidden="true">{{ i + 1 }}</span>

          <article class="stop-card">
            <div class="stop-main">
              <h3 class="stop-name">{{ stop.attraction.name }}</h3>

              <template v-if="editable">
                <label class="field">
                  <span class="field-label">地址</span>
                  <a-input v-model:value="stop.attraction.address" size="small" />
                </label>
                <label class="field">
                  <span class="field-label">游览时长（分钟）</span>
                  <a-input-number
                    v-model:value="stop.attraction.visit_duration"
                    :min="10"
                    :max="480"
                    size="small"
                    style="width: 100%"
                  />
                </label>
                <label class="field">
                  <span class="field-label">描述</span>
                  <a-textarea v-model:value="stop.attraction.description" :rows="2" size="small" />
                </label>
              </template>

              <template v-else>
                <p v-if="stop.attraction.address" class="stop-addr">
                  {{ stop.attraction.address }}
                </p>
                <p v-if="stop.attraction.description" class="stop-desc">
                  {{ stop.attraction.description }}
                </p>

                <ul class="stop-meta">
                  <li class="u-num">{{ formatDuration(stop.stayMinutes) }}</li>
                  <li v-if="stop.attraction.ticket_price" class="u-num">
                    ¥{{ stop.attraction.ticket_price }}
                  </li>
                  <li v-if="stop.attraction.rating" class="u-num">
                    {{ stop.attraction.rating }} 分
                  </li>
                </ul>
              </template>
            </div>

            <div v-if="editable" class="stop-actions">
              <a-button size="small" :disabled="i === 0" @click="move(i, -1)">
                <template #icon><ArrowUpOutlined /></template>
              </a-button>
              <a-button size="small" :disabled="i === stops.length - 1" @click="move(i, 1)">
                <template #icon><ArrowDownOutlined /></template>
              </a-button>
              <a-button size="small" danger @click="remove(i)">
                <template #icon><DeleteOutlined /></template>
              </a-button>
            </div>
          </article>
        </div>
      </li>
    </ol>

    <p v-else class="day-empty">这一天还没有安排景点。</p>

    <!-- ---------------- 住宿 ---------------- -->
    <section v-if="day.hotel" class="sub">
      <h3 class="sub-title">住宿</h3>
      <div class="hotel">
        <p class="hotel-name">{{ day.hotel.name }}</p>
        <ul class="hotel-meta">
          <li v-if="day.hotel.address">{{ day.hotel.address }}</li>
          <li v-if="day.hotel.price_range">{{ day.hotel.price_range }}</li>
          <li v-if="day.hotel.distance">{{ day.hotel.distance }}</li>
        </ul>
      </div>
    </section>

    <!-- ---------------- 餐饮 ---------------- -->
    <section v-if="day.meals?.length" class="sub">
      <h3 class="sub-title">餐饮</h3>
      <ul class="meals">
        <li v-for="meal in day.meals" :key="meal.type" class="meal">
          <span class="meal-slot">{{ mealLabelOf(meal.type) }}</span>
          <span class="meal-name">{{ meal.name }}</span>
          <span v-if="meal.estimated_cost" class="meal-cost u-num">¥{{ meal.estimated_cost }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { ArrowUpOutlined, ArrowDownOutlined, DeleteOutlined } from '@ant-design/icons-vue';
import { mealLabelOf } from '@/constants/tripOptions';
import {
  buildDayTimeline,
  summarizeDay,
  formatDistance,
  formatDuration,
} from '@/utils/tripTimeline';
import type { DayPlan } from '@/types';

/**
 * 一天的详情：时间轴 + 住宿 + 餐饮。
 *
 * 时间轴的时间由 `utils/tripTimeline.ts` 算出来：从 09:00 起，
 * 逐点累加「游览时长 + 到下一段的交通耗时」。
 *
 * 编辑模式下**直接改 props 里的对象** —— 这与改造前的做法一致，
 * 因为 `day` 就是 `tripPlan.days[i]` 的引用，改它等于改行程数据。
 * 不在这里做拷贝，否则"保存"时还得想办法合并回去。
 */
const props = withDefaults(
  defineProps<{
    day: DayPlan;
    editable?: boolean;
  }>(),
  { editable: false }
);

const emit = defineEmits<{
  changed: [];
  /**
   * 请求删除某个景点。**不在这里真的删** ——
   * 撤销栈、"每天至少留一个景点"的判断都在父组件（那里有全局 message 和地图实例）。
   * 组件只负责表达"用户点了这一项"，不负责决定删不删得成。
   */
  remove: [index: number];
}>();

const stops = computed(() => buildDayTimeline(props.day));
const stats = computed(() => summarizeDay(props.day));

function move(index: number, delta: -1 | 1) {
  const list = props.day.attractions;
  const target = index + delta;
  if (target < 0 || target >= list.length) return;
  [list[index], list[target]] = [list[target], list[index]];
  emit('changed');
}

function remove(index: number) {
  emit('remove', index);
}
</script>

<style scoped>
.day {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

/* ---------------- 天头 ---------------- */
.day-head {
  padding-bottom: var(--space-4);
  border-bottom: 1px solid var(--line-1);
}

.day-title-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
}

.day-title {
  font-size: var(--fs-h1);
  font-weight: var(--fw-semibold);
  letter-spacing: var(--ls-tight);
  color: var(--text-1);
}

.day-date {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.day-desc {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--text-2);
  max-width: 62ch;
  margin-bottom: var(--space-3);
}

.day-stats {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.day-stats strong {
  color: var(--text-1);
  font-weight: var(--fw-semibold);
}

.day-empty {
  font-size: var(--fs-caption);
  color: var(--text-3);
  padding: var(--space-5) 0;
}

/* ---------------- 时间轴 ---------------- */
.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
}

/* 交通衔接：左侧一条虚竖线，和上面的点连起来 */
.leg {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) 0 var(--space-2)
    calc(52px + var(--space-3) + 13px);
}

.leg-rail {
  width: 2px;
  height: 18px;
  background: var(--line-2);
  border-radius: 1px;
  flex-shrink: 0;
  margin-left: -1px;
}

.leg-text {
  font-size: var(--fs-micro);
  color: var(--text-3);
}

.stop-row {
  display: grid;
  grid-template-columns: 52px 26px 1fr;
  gap: var(--space-3);
  align-items: start;
}

.stop-time {
  font-size: var(--fs-caption);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
  padding-top: 10px;
  text-align: right;
}

/* 序号节点：主色实心圆，是时间轴上的锚 */
.stop-node {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  margin-top: 6px;
  border-radius: 50%;
  background: var(--brand-500);
  color: #fff;
  font-size: var(--fs-micro);
  font-weight: var(--fw-semibold);
  flex-shrink: 0;
}

.stop-card {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
  padding: var(--space-4);
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out);
}

.stop-card:hover {
  border-color: var(--brand-300);
  box-shadow: var(--shadow-2);
}

.stop-main {
  flex: 1;
  min-width: 0;
}

.stop-name {
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
  margin-bottom: var(--space-1);
}

.stop-addr,
.stop-desc {
  font-size: var(--fs-caption);
  line-height: var(--lh-body);
  color: var(--text-2);
}

.stop-desc {
  margin-top: var(--space-1);
  /* 描述可能很长，截到 2 行，保持时间轴的节奏 */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.stop-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  list-style: none;
  margin: var(--space-2) 0 0;
  padding: 0;
}

.stop-meta li {
  padding: 1px var(--space-3);
  border-radius: var(--radius-pill);
  background: var(--bg-tint);
  color: var(--brand-ink);
  font-size: var(--fs-micro);
}

.stop-actions {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  flex-shrink: 0;
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin-bottom: var(--space-3);
}

.field-label {
  font-size: var(--fs-micro);
  color: var(--text-2);
}

/* ---------------- 住宿 / 餐饮 ---------------- */
.sub-title {
  font-size: var(--fs-micro);
  font-weight: var(--fw-semibold);
  color: var(--text-2);
  margin-bottom: var(--space-3);
}

.hotel {
  padding: var(--space-4);
  background: var(--bg-sunken);
  border-radius: var(--radius-md);
}

.hotel-name {
  font-size: var(--fs-body);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
  margin-bottom: var(--space-2);
}

.hotel-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.meals {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.meal {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  font-size: var(--fs-caption);
}

.meal-slot {
  min-width: 40px;
  color: var(--text-2);
}

.meal-name {
  color: var(--text-1);
  flex: 1;
}

.meal-cost {
  color: var(--text-2);
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 575px) {
  /* 手机上时间轴改上下排：时间与序号一行，卡片另起一行 */
  .stop-row {
    grid-template-columns: 44px 24px 1fr;
    gap: var(--space-2);
  }

  .stop-time {
    font-size: var(--fs-micro);
  }

  .stop-node {
    width: 24px;
    height: 24px;
  }

  .stop-card {
    flex-direction: column;
    padding: var(--space-3);
  }

  .stop-actions {
    flex-direction: row;
    width: 100%;
    justify-content: flex-end;
  }

  .leg {
    padding-left: calc(44px + var(--space-2) + 12px);
  }
}
</style>
