<template>
  <section class="src">
    <header class="src-head">
      <FileTextOutlined class="src-head-icon" aria-hidden="true" />
      <h2 class="src-head-title">数据源</h2>
      <span class="src-head-desc">向量库里的内容来自这两处本地文件</span>
    </header>

    <div v-if="loading" class="src-grid">
      <div class="skeleton-block src-skel"></div>
      <div class="skeleton-block src-skel"></div>
    </div>

    <StatePanel
      v-else-if="error"
      state="error"
      error-kind="network"
      title="读不到数据源信息"
      :detail="error"
      @retry="emit('refresh')"
    />

    <div v-else-if="sources" class="src-grid">
      <!-- ---------------- POI 事实层 ---------------- -->
      <div class="src-col">
        <h3 class="src-sub">POI 事实层</h3>
        <p class="src-path u-num">
          {{ sources.poi_inventory.path || '—' }}
          <span v-if="sources.poi_inventory.size_kb"> · {{ sources.poi_inventory.size_kb }} KB</span>
        </p>

        <p v-if="!poiMissing" class="src-total">
          共 <strong class="u-num">{{ sources.poi_inventory.total }}</strong> 条
        </p>
        <p v-else class="src-missing">未找到库存文件</p>

        <ul class="src-bars">
          <li v-for="c in poiCities" :key="c.name" class="src-bar-row">
            <span class="src-bar-name">{{ c.name }}</span>
            <span class="src-bar-track">
              <span class="src-bar-fill" :style="{ width: c.pct }"></span>
            </span>
            <span class="src-bar-num u-num">{{ c.value }}</span>
          </li>
        </ul>

        <p class="src-note">
          来源是冻结的高德 POI 库存（带真实坐标），灌库不调用高德接口、不花额度。
        </p>
      </div>

      <!-- ---------------- 城市攻略层 ---------------- -->
      <div class="src-col">
        <h3 class="src-sub">城市攻略层</h3>
        <p class="src-path u-num">{{ sources.city_guides.dir || '—' }}</p>

        <p v-if="guideFiles.length" class="src-total">
          共 <strong class="u-num">{{ sources.city_guides.total }}</strong> 块
          <span class="src-sub-note">（{{ guideFiles.length }} 篇手写攻略）</span>
        </p>
        <p v-else class="src-missing">目录下没有 .md 攻略文件</p>

        <ul class="src-files">
          <li v-for="f in guideFiles" :key="f.name" class="src-file">
            <div class="src-file-main">
              <span class="src-file-name">{{ f.name }}</span>
              <span class="src-file-meta u-num">{{ f.sections }} 块 · {{ f.size_kb }} KB</span>
            </div>
            <!-- 一键带着城市名去检索：看到某篇攻略后想验证它能不能被搜到，
                 比让用户自己回到上面的输入框敲字顺手 -->
            <a-button size="small" @click="emit('try-search', fileToQuery(f.name))">试检索</a-button>
          </li>
        </ul>

        <p class="src-note">
          攻略回答的是「怎么安排才合理」—— 门票、预约、淡旺季、避坑，
          这些是模型最容易记错或过时的部分。
        </p>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { FileTextOutlined } from '@ant-design/icons-vue';
import StatePanel from '@/components/common/StatePanel.vue';
import type { KnowledgeSources } from '@/types';

const props = defineProps<{
  sources: KnowledgeSources | null;
  loading: boolean;
  error: string;
}>();

const emit = defineEmits<{
  refresh: [];
  'try-search': [query: string];
}>();

const poiMissing = computed(() => props.sources?.poi_inventory?.exists === false);

/** 按最大值归一，条形长度才有可比性 */
const poiCities = computed(() => {
  const per = props.sources?.poi_inventory?.per_city || {};
  const entries = Object.entries(per).map(([name, value]) => ({ name, value }));
  const max = Math.max(1, ...entries.map((e) => e.value));
  return entries
    .sort((a, b) => b.value - a.value)
    .map((e) => ({ ...e, pct: `${((e.value / max) * 100).toFixed(1)}%` }));
});

const guideFiles = computed(() => props.sources?.city_guides?.files || []);

/** 从文件名推出一个可检索的查询词：`北京.md` → `北京` */
function fileToQuery(name: string): string {
  return name.replace(/\.md$/i, '');
}
</script>

<style scoped>
.src {
  background: var(--bg-card);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-md);
  padding: var(--sp-5);
}

.src-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  margin-bottom: var(--sp-4);
}

.src-head-icon {
  font-size: 16px;
  color: var(--brand-500);
}

.src-head-title {
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
}

.src-head-desc {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.src-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-6);
}

.src-skel {
  height: 200px;
  border-radius: var(--radius-md);
}

.src-col {
  min-width: 0;
}

.src-sub {
  font-size: var(--fs-body);
  font-weight: var(--fw-semibold);
  margin-bottom: var(--sp-2);
}

.src-path {
  font-size: var(--fs-caption);
  color: var(--text-2);
  word-break: break-all;
  margin-bottom: var(--sp-2);
}

.src-total {
  font-size: var(--fs-sm);
  color: var(--text-1);
  margin-bottom: var(--sp-3);
}

.src-sub-note {
  color: var(--text-2);
  font-size: var(--fs-caption);
}

.src-missing {
  font-size: var(--fs-sm);
  color: var(--warning);
  margin-bottom: var(--sp-3);
}

.src-bars {
  list-style: none;
  margin: 0 0 var(--sp-3);
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.src-bar-row {
  display: grid;
  grid-template-columns: 48px 1fr 48px;
  align-items: center;
  gap: var(--sp-3);
}

.src-bar-name {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.src-bar-track {
  position: relative;
  height: 8px;
  border-radius: var(--radius-pill);
  background: var(--bg-sunken);
  overflow: hidden;
}

.src-bar-fill {
  position: absolute;
  inset: 0 auto 0 0;
  background: var(--brand-300);
  border-radius: var(--radius-pill);
}

.src-bar-num {
  font-size: var(--fs-caption);
  color: var(--text-1);
  text-align: right;
}

.src-files {
  list-style: none;
  margin: 0 0 var(--sp-3);
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.src-file {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
  border: 1px solid var(--border-2);
  border-radius: var(--radius-sm);
  background: var(--bg-sunken);
}

.src-file-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.src-file-name {
  font-size: var(--fs-sm);
  color: var(--text-1);
  font-weight: var(--fw-medium);
}

.src-file-meta {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.src-note {
  font-size: var(--fs-caption);
  color: var(--text-2);
  line-height: var(--lh-body);
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 991px) {
  .src-grid {
    grid-template-columns: 1fr;
    gap: var(--sp-5);
  }
}

@media (max-width: 767px) {
  .src {
    padding: var(--sp-4);
  }

  .src-file {
    flex-direction: column;
    align-items: stretch;
  }

  .src-file :deep(.ant-btn) {
    min-height: var(--touch-min);
  }
}
</style>
