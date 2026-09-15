<template>
  <div class="history-container">
    <div class="page-header">
      <div>
        <h2 class="page-title">📚 历史行程</h2>
        <p class="page-subtitle">所有生成过的行程都在这里,点一条可以查看完整内容</p>
      </div>
      <a-space>
        <a-button :loading="loading" @click="load">🔄 刷新</a-button>
        <a-button type="primary" @click="router.push('/')">＋ 新建行程</a-button>
      </a-space>
    </div>

    <!-- 聚合统计。没有数据时整块不显示,免得出现四个 0 -->
    <a-row v-if="stats && stats.total > 0" :gutter="16" class="stats-row">
      <a-col :xs="12" :sm="6">
        <a-card size="small">
          <a-statistic title="行程总数" :value="stats.total" suffix="条" />
        </a-card>
      </a-col>
      <a-col :xs="12" :sm="6">
        <a-card size="small">
          <a-statistic title="累计花费" :value="stats.cost_cny" :precision="4" prefix="¥" />
        </a-card>
      </a-col>
      <a-col :xs="12" :sm="6">
        <a-card size="small">
          <a-statistic title="累计 Token" :value="stats.total_tokens" />
        </a-card>
      </a-col>
      <a-col :xs="12" :sm="6">
        <a-card size="small">
          <a-statistic title="LLM 调用" :value="stats.llm_calls" suffix="次" />
        </a-card>
      </a-col>
    </a-row>

    <!-- 错误提示。有它才不会在接口挂掉时显示成"暂无数据" -->
    <a-alert
      v-if="error"
      type="error"
      show-icon
      class="error-alert"
      :message="error"
      description="请确认后端服务已启动,然后点「刷新」重试。"
    />

    <a-card>
      <a-table
        :data-source="rows"
        :columns="columns"
        :loading="loading"
        row-key="id"
        :pagination="{ pageSize: 10, hideOnSinglePage: true }"
        :locale="{ emptyText: loading ? ' ' : '还没有任何行程，去生成一个吧' }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'title'">
            <a class="plan-link" @click="openPlan(record.id)">
              {{ record.title || `${record.city} ${record.travel_days}日游` }}
            </a>
            <div class="muted">{{ record.city }} · {{ record.attractions }} 个景点</div>
          </template>

          <template v-else-if="column.key === 'dates'">
            <div>{{ fmtDate(record.start_date) }}</div>
            <div class="muted">至 {{ fmtDate(record.end_date) }}</div>
          </template>

          <template v-else-if="column.key === 'cost'">
            <div>{{ fmtCost(record.cost_cny) }}</div>
            <div class="muted">{{ fmtNum(record.total_tokens) }} tokens</div>
          </template>

          <template v-else-if="column.key === 'budget'">
            {{ record.total_budget ? `¥${fmtNum(record.total_budget)}` : '—' }}
          </template>

          <template v-else-if="column.key === 'status'">
            <a-tag :color="statusOf(record.status).color">
              {{ statusOf(record.status).text }}
            </a-tag>
            <!-- 降级过的行程点进去可能数据不全,这里先透出原因 -->
            <a-tooltip v-if="record.warnings && record.warnings.length" :title="record.warnings.join('；')">
              <span class="muted">ⓘ</span>
            </a-tooltip>
          </template>

          <template v-else-if="column.key === 'action'">
            <a-space>
              <a-button type="link" size="small" @click="openPlan(record.id)">查看</a-button>
              <a-popconfirm
                title="删除后无法恢复,确定吗?"
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
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { listPlans, deletePlan } from '@/services/api'
import type { PlanStats, PlanSummary } from '@/types'

const router = useRouter()

const rows = ref<PlanSummary[]>([])
const stats = ref<PlanStats | null>(null)
const loading = ref(false)
const error = ref('')

// 列定义。宽度是给宽屏的,窄屏 antd 会自动横向滚动。
const columns = [
  { title: '行程', key: 'title', width: 220 },
  { title: '日期', key: 'dates', width: 160 },
  { title: '天数', dataIndex: 'travel_days', key: 'days', width: 70 },
  { title: '总预算', key: 'budget', width: 110 },
  { title: '成本', key: 'cost', width: 150 },
  { title: '状态', key: 'status', width: 120 },
  { title: '操作', key: 'action', width: 140 }
]

const STATUS_MAP: Record<string, { color: string; text: string }> = {
  ok: { color: 'success', text: '已完成' },
  fallback: { color: 'warning', text: '部分降级' },
  error: { color: 'error', text: '生成失败' },
  running: { color: 'processing', text: '生成中' }
}

const statusOf = (s: string) => STATUS_MAP[s] || { color: 'default', text: s }

const fmtDate = (s: string) => (s || '').slice(0, 10) || '—'
const fmtCost = (n: number) => `¥${(n || 0).toFixed(4)}`
const fmtNum = (n: number) => (n || 0).toLocaleString('zh-CN')

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await listPlans()
    rows.value = res.data || []
    stats.value = res.stats || null
  } catch (e: any) {
    // 接口挂了要和"确实没有数据"区分开 —— 否则用户会以为行程丢了
    error.value = e.message || '读取历史行程失败'
    rows.value = []
    stats.value = null
  } finally {
    loading.value = false
  }
}

const openPlan = (id: string) => {
  router.push(`/result/${id}`)
}

const removePlan = async (id: string) => {
  try {
    await deletePlan(id)
    message.success('已删除')
    await load()
  } catch (e: any) {
    message.error(e.message || '删除失败')
  }
}

onMounted(load)
</script>

<style scoped>
.history-container {
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
  flex-wrap: wrap;
}

.page-title {
  margin: 0 0 4px;
}

.page-subtitle {
  margin: 0;
  color: rgba(0, 0, 0, 0.45);
}

.stats-row {
  margin-bottom: 16px;
}

.error-alert {
  margin-bottom: 16px;
}

.plan-link {
  font-weight: 600;
}

.muted {
  color: rgba(0, 0, 0, 0.45);
  font-size: 12px;
}
</style>
