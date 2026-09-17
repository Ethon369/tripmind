<template>
  <div class="history">
    <header class="hd">
      <div class="hd-text">
        <h1 class="hd-title">历史行程</h1>
        <p class="hd-sub">所有生成过的行程都在这里，点一条可以查看完整内容</p>
      </div>
      <div class="hd-actions">
        <a-button :loading="loading" @click="load">
          <template #icon><ReloadOutlined /></template>
          刷新
        </a-button>
        <a-button type="primary" @click="router.push('/')">
          <template #icon><PlusOutlined /></template>
          新建行程
        </a-button>
      </div>
    </header>

    <!-- ---------------- 聚合统计 ---------------- -->
    <!-- 没有数据时整块不显示，免得出现四个 0 -->
    <a-row v-if="stats && stats.total > 0" :gutter="[16, 16]" class="stats">
      <a-col :xs="12" :sm="6">
        <div class="stat">
          <span class="stat-label">行程总数</span>
          <span class="stat-value u-num">{{ stats.total }}<em>条</em></span>
        </div>
      </a-col>
      <a-col :xs="12" :sm="6">
        <div class="stat">
          <span class="stat-label">累计花费</span>
          <span class="stat-value u-num">¥{{ stats.cost_cny.toFixed(4) }}</span>
        </div>
      </a-col>
      <a-col :xs="12" :sm="6">
        <div class="stat">
          <span class="stat-label">累计 Token</span>
          <span class="stat-value u-num">{{ fmtNum(stats.total_tokens) }}</span>
        </div>
      </a-col>
      <a-col :xs="12" :sm="6">
        <div class="stat">
          <span class="stat-label">LLM 调用</span>
          <span class="stat-value u-num">{{ stats.llm_calls }}<em>次</em></span>
        </div>
      </a-col>
    </a-row>

    <!-- ---------------- 筛选 ----------------
         在已取到的数据里过滤，不打后端。
         代价是只能筛最近 200 条（接口上限）—— 这一点在列表底部明确写出来，
         免得用户以为「搜不到就是没有」。 -->
    <div v-if="!error && allRows.length" class="filters">
      <a-input-search
        v-model:value="keyword"
        class="filters-search"
        placeholder="按城市或标题搜索"
        allow-clear
      />
      <a-select v-model:value="statusFilter" class="filters-status">
        <a-select-option value="all">全部状态</a-select-option>
        <a-select-option v-for="(v, k) in PLAN_STATUS_MAP" :key="k" :value="k">
          {{ v.text }}
        </a-select-option>
      </a-select>
    </div>

    <!-- ---------------- 内容：四态 ---------------- -->

    <StatePanel
      v-if="error"
      state="error"
      error-kind="network"
      title="读取历史行程失败"
      :detail="error"
      @retry="load"
    />

    <StatePanel v-else-if="loading" state="loading" skeleton="table" :skeleton-count="6" />

    <StatePanel
      v-else-if="!allRows.length"
      state="empty"
      title="还没有任何行程"
      description="生成第一个行程后，它会自动保存在这里。"
    >
      <template #extra>
        <a-button type="primary" @click="router.push('/')">去生成一个</a-button>
      </template>
    </StatePanel>

    <StatePanel
      v-else-if="!filtered.length"
      state="empty"
      title="没有匹配的行程"
      description="换个关键词，或清除筛选条件试试。"
    >
      <template #extra>
        <a-button @click="clearFilters">清除筛选</a-button>
      </template>
    </StatePanel>

    <template v-else>
      <!-- 桌面：表格 -->
      <a-card v-if="!isMobile" :bordered="false" class="table-card">
        <a-table
          :data-source="paged"
          :columns="columns"
          row-key="id"
          :pagination="false"
          :scroll="{ x: 'max-content' }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'title'">
              <a class="plan-link" @click="openPlan(record.id)">
                {{ record.title || `${record.city} ${record.travel_days}日游` }}
              </a>
              <div class="plan-sub">{{ record.city }} · {{ record.attractions }} 个景点</div>
            </template>

            <template v-else-if="column.key === 'dates'">
              <div>{{ fmtDate(record.start_date) }}</div>
              <div class="plan-sub">至 {{ fmtDate(record.end_date) }}</div>
            </template>

            <template v-else-if="column.key === 'cost'">
              <div class="u-num">{{ fmtCost(record.cost_cny) }}</div>
              <div class="plan-sub u-num">{{ fmtNum(record.total_tokens) }} tokens</div>
            </template>

            <template v-else-if="column.key === 'budget'">
              <span class="u-num">
                {{ record.total_budget ? `¥${fmtNum(record.total_budget)}` : '—' }}
              </span>
            </template>

            <template v-else-if="column.key === 'status'">
              <PlanStatusTag :status="record.status" :warnings="record.warnings" />
            </template>

            <template v-else-if="column.key === 'action'">
              <a-space>
                <a-button type="link" size="small" @click="openPlan(record.id)">查看</a-button>
                <a-popconfirm
                  title="删除后无法恢复，确定吗？"
                  ok-text="删除"
                  cancel-text="取消"
                  @confirm="removePlan(record.id)"
                >
                  <a-button type="link" size="small" danger>删除</a-button>
                </a-popconfirm>
              </a-space>
            </template>
          </template>
        </a-table>
      </a-card>

      <!-- 手机：卡片列表。7 列表格在 375px 屏上必须横向滚动，
           且最重要的「行程标题」会被挤成几个字 —— 不如换形态。 -->
      <PlanCardList v-else :rows="paged" @open="openPlan" @remove="removePlan" />

      <div class="pager">
        <a-pagination
          v-model:current="page"
          :page-size="pageSize"
          :total="filtered.length"
          :show-size-changer="false"
          :simple="isMobile"
          size="small"
        />
      </div>

      <p v-if="truncated" class="truncate-note">
        共 {{ stats?.total || allRows.length }} 条，此处仅加载最近 {{ LOAD_LIMIT }} 条。
      </p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { ReloadOutlined, PlusOutlined } from '@ant-design/icons-vue';
import StatePanel from '@/components/common/StatePanel.vue';
import PlanCardList from '@/components/history/PlanCardList.vue';
import PlanStatusTag from '@/components/history/PlanStatusTag.vue';
import { useScreen } from '@/composables/useScreen';
import { listPlans, deletePlan } from '@/services/api';
import { PLAN_STATUS_MAP } from '@/constants/tripOptions';
import type { PlanStats, PlanSummary } from '@/types';

/**
 * 历史行程。
 *
 * 本轮修掉的问题：
 * 1. **第 51 条之后的行程永远看不到**（原来是固定 limit=50 + 前端分页，且无任何提示）。
 *    现在按接口上限取 200 条，并在底部说明只加载了最近 200 条。
 * 2. **对比度不合规**：原来的 `.muted` 是 12px + rgba(0,0,0,.45)，白底对比度约 3.36:1，
 *    低于 WCAG AA 要求的 4.5:1。现在统一走 --text-2（约 7:1）。
 * 3. 手机上表格难用 —— 改为卡片列表。
 * 4. 状态标签原来定义在本文件的 script 里，结果页要用就得复制；现抽成公共组件。
 */

const router = useRouter();
const { isMobile } = useScreen();

/** 接口 limit 上限就是 200（后端会 clamp），这里取满 */
const LOAD_LIMIT = 200;
const PAGE_SIZE_DESKTOP = 10;
const PAGE_SIZE_MOBILE = 5;

const allRows = ref<PlanSummary[]>([]);
const stats = ref<PlanStats | null>(null);
const loading = ref(false);
const error = ref('');

const keyword = ref('');
const statusFilter = ref('all');
const page = ref(1);

const pageSize = computed(() => (isMobile.value ? PAGE_SIZE_MOBILE : PAGE_SIZE_DESKTOP));

const columns = [
  { title: '行程', key: 'title', width: 220 },
  { title: '日期', key: 'dates', width: 160 },
  { title: '天数', dataIndex: 'travel_days', key: 'days', width: 70 },
  { title: '总预算', key: 'budget', width: 110 },
  { title: '成本', key: 'cost', width: 150 },
  { title: '状态', key: 'status', width: 130 },
  { title: '操作', key: 'action', width: 140 },
];

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return allRows.value.filter((r) => {
    if (statusFilter.value !== 'all' && r.status !== statusFilter.value) return false;
    if (!kw) return true;
    const haystack = `${r.city || ''} ${r.title || ''}`.toLowerCase();
    return haystack.includes(kw);
  });
});

const paged = computed(() => {
  const start = (page.value - 1) * pageSize.value;
  return filtered.value.slice(start, start + pageSize.value);
});

/** 后端总数超过本次加载量时给出说明 */
const truncated = computed(() => {
  const total = stats.value?.total ?? 0;
  return total > allRows.value.length;
});

// 筛选条件变了要回到第一页 —— 否则会停在一个已经不存在的页码上，显示空白
watch([keyword, statusFilter], () => {
  page.value = 1;
});

const fmtDate = (s: string) => (s || '').slice(0, 10) || '—';
const fmtCost = (n: number) => `¥${(n || 0).toFixed(4)}`;
const fmtNum = (n: number) => (n || 0).toLocaleString('zh-CN');

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const res = await listPlans(LOAD_LIMIT, 0);
    allRows.value = res.data || [];
    stats.value = res.stats || null;
    page.value = 1;
  } catch (e: unknown) {
    // 接口挂了必须和「确实没有数据」区分开 —— 否则用户会以为行程丢了
    error.value = (e as Error)?.message || '读取历史行程失败';
    allRows.value = [];
    stats.value = null;
  } finally {
    loading.value = false;
  }
}

const openPlan = (id: string) => {
  router.push(`/result/${id}`);
};

async function removePlan(id: string) {
  try {
    await deletePlan(id);
    message.success('已删除');
    await load();
  } catch (e: unknown) {
    message.error((e as Error)?.message || '删除失败');
  }
}

function clearFilters() {
  keyword.value = '';
  statusFilter.value = 'all';
}

onMounted(load);
</script>

<style scoped>
.history {
  max-width: var(--container-max);
  margin: 0 auto;
  padding: var(--sp-8) var(--sp-4) var(--sp-10);
}

/* ---------------- 页头 ---------------- */
.hd {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-4);
  flex-wrap: wrap;
  margin-bottom: var(--sp-6);
}

.hd-title {
  margin-bottom: var(--sp-1);
}

.hd-sub {
  color: var(--text-2);
  font-size: var(--fs-sm);
}

.hd-actions {
  display: flex;
  gap: var(--sp-3);
  flex-wrap: wrap;
}

/* ---------------- 统计 ---------------- */
.stats {
  margin-bottom: var(--sp-5);
}

.stat {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  background: var(--bg-card);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-md);
  padding: var(--sp-4);
  height: 100%;
}

.stat-label {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.stat-value {
  font-size: var(--fs-h2);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.stat-value em {
  font-style: normal;
  font-size: var(--fs-caption);
  font-weight: var(--fw-regular);
  color: var(--text-2);
  margin-left: 2px;
}

/* ---------------- 筛选 ---------------- */
.filters {
  display: flex;
  gap: var(--sp-3);
  flex-wrap: wrap;
  margin-bottom: var(--sp-5);
}

.filters-search {
  max-width: 320px;
}

.filters-status {
  width: 140px;
}

/* ---------------- 表格 ---------------- */
.table-card :deep(.ant-card-body) {
  padding: var(--sp-2) var(--sp-4) var(--sp-4);
}

.plan-link {
  font-weight: var(--fw-semibold);
  cursor: pointer;
}

.plan-sub {
  color: var(--text-2);
  font-size: var(--fs-caption);
}

/* ---------------- 分页 ---------------- */
.pager {
  display: flex;
  justify-content: center;
  margin-top: var(--sp-5);
}

.truncate-note {
  margin-top: var(--sp-3);
  text-align: center;
  font-size: var(--fs-caption);
  color: var(--text-2);
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 767px) {
  .history {
    padding: var(--sp-4) var(--sp-3) var(--sp-8);
  }

  .hd-actions {
    width: 100%;
  }

  .hd-actions :deep(.ant-btn) {
    flex: 1 1 0;
    min-height: var(--touch-min);
  }

  .filters {
    flex-direction: column;
    gap: var(--sp-2);
  }

  .filters-search,
  .filters-status {
    max-width: 100%;
    width: 100%;
  }

  .filters :deep(.ant-select) {
    width: 100%;
  }
}
</style>
