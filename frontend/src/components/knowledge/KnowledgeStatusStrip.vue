<template>
  <div class="ks">
    <!-- 首屏加载：只占位，不显示空态文案 -->
    <div v-if="loading && !status" class="ks-grid">
      <div v-for="i in 3" :key="i" class="skeleton-block ks-skel"></div>
    </div>

    <!-- 连状态都拿不到（后端没起、超时） -->
    <StatePanel
      v-else-if="error"
      state="error"
      error-kind="network"
      :title="'读不到知识库状态'"
      :detail="error"
      @retry="emit('refresh')"
    />

    <template v-else-if="status">
      <div class="ks-grid">
        <!-- ---------------- 1. 可用性 ---------------- -->
        <section class="ks-card">
          <header class="ks-head">
            <component
              :is="status.available ? CheckCircleFilled : CloseCircleFilled"
              class="ks-dot"
              :class="status.available ? 'is-ok' : 'is-bad'"
              aria-hidden="true"
            />
            <h3 class="ks-title">知识库状态</h3>
            <a-button type="text" size="small" :loading="loading" @click="emit('refresh')">
              <template #icon><ReloadOutlined /></template>
              刷新
            </a-button>
          </header>

          <p class="ks-main">
            {{ status.available ? 'embedding 与 Milvus 均正常' : '不可用' }}
          </p>

          <!-- 失败原因原样展示。这是排错的唯一线索 —— 后端刻意不把它
               吞成一句笼统的「RAG 不可用」（见 knowledge_service.available） -->
          <p v-if="!status.available" class="ks-reason">{{ status.reason }}</p>

          <dl v-if="!status.available" class="ks-kv">
            <dt>Milvus 地址</dt>
            <dd class="u-num">{{ status.milvus_uri }}</dd>
          </dl>

          <div v-if="!status.available" class="ks-fix">
            <p class="ks-fix-title">常见原因：</p>
            <ul>
              <li>Milvus 没启动 —— <code>cd D:/devlop/Milvus &amp;&amp; docker compose up -d</code></li>
              <li><code>EMBED_API_KEY</code> / <code>EMBED_BASE_URL</code> 没配 —— 检查 <code>backend/.env</code></li>
            </ul>
          </div>
        </section>

        <!-- ---------------- 2. 两层计数 ---------------- -->
        <section class="ks-card">
          <header class="ks-head">
            <DatabaseOutlined class="ks-dot is-neutral" aria-hidden="true" />
            <h3 class="ks-title">已写入向量库</h3>
          </header>

          <div v-if="loadingCounts && !hasCounts" class="ks-counts-skeleton">
            <div class="skeleton-block ks-skel-line"></div>
            <div class="skeleton-block ks-skel-line"></div>
          </div>

          <template v-else>
            <div class="ks-count-row">
              <span class="ks-count-label">POI 事实</span>
              <span class="ks-count-value u-num" :class="countClass(namespaces.poi_facts)">
                {{ formatCount(namespaces.poi_facts) }}
              </span>
            </div>
            <div class="ks-count-row">
              <span class="ks-count-label">城市攻略</span>
              <span class="ks-count-value u-num" :class="countClass(namespaces.city_guides)">
                {{ formatCount(namespaces.city_guides) }}
              </span>
            </div>

            <dl class="ks-kv">
              <dt>collection</dt>
              <dd class="u-num">{{ status.collections.poi_facts }}</dd>
              <dt>维度</dt>
              <dd class="u-num">{{ status.dim_expected }}</dd>
            </dl>

            <p v-if="countsError" class="ks-warn">{{ countsError }}</p>

            <!-- 计数为 0 是「检索没结果」的头号原因，所以直接给去灌库的入口 -->
            <a-alert
              v-if="isEmpty"
              class="ks-alert"
              type="warning"
              show-icon
              message="向量库还没有数据"
              description="检索不会有结果，需要先灌库。"
            />
            <a-button v-if="isEmpty" type="primary" size="small" @click="emit('goto-ingest')">
              去灌库
            </a-button>
          </template>
        </section>

        <!-- ---------------- 3. 生成时是否启用 ----------------
             这三张卡里最重要的一张。
             现状：用户把库灌好了、检索也通，但 ENABLE_RAG 默认 false，
             生成行程时**完全不用知识库**，而界面上没有任何地方能看出这件事。
             enabled 与 available 是两件不同的事，必须分开呈现。 -->
        <section class="ks-card">
          <header class="ks-head">
            <ThunderboltOutlined
              class="ks-dot"
              :class="status.enabled ? 'is-ok' : 'is-warn'"
              aria-hidden="true"
            />
            <h3 class="ks-title">生成时是否启用</h3>
          </header>

          <a-tag :color="status.enabled ? 'success' : 'warning'" class="ks-tag">
            {{ status.enabled ? '已启用' : '未启用' }}
          </a-tag>

          <p class="ks-main">
            {{
              status.enabled
                ? '生成行程时会注入知识库检索到的内容。'
                : '知识库本身可用，但生成行程时不会使用它。'
            }}
          </p>

          <div v-if="!status.enabled" class="ks-fix">
            <p class="ks-fix-title">开启方式（改完需重启后端）：</p>
            <div class="ks-code">
              <code>ENABLE_RAG=true</code>
              <a-button
                type="text"
                size="small"
                :aria-label="'复制配置片段'"
                @click="emit('copy-env')"
              >
                <template #icon><CopyOutlined /></template>
              </a-button>
            </div>
            <p class="ks-fix-note">
              该开关默认关闭，是为了让「开启 RAG」与「关闭 RAG」两次评测的 prompt
              逐字一致、数字可直接对比。日常使用时想让它生效就打开。
            </p>
          </div>

          <dl class="ks-kv">
            <dt>注入条数</dt>
            <dd class="u-num">top_k = {{ status.top_k }}</dd>
          </dl>
        </section>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import {
  CheckCircleFilled,
  CloseCircleFilled,
  DatabaseOutlined,
  ThunderboltOutlined,
  ReloadOutlined,
  CopyOutlined,
} from '@ant-design/icons-vue';
import StatePanel from '@/components/common/StatePanel.vue';
import type { KnowledgeNamespaceStat, KnowledgeStatus } from '@/types';

const props = defineProps<{
  status: KnowledgeStatus | null;
  loading: boolean;
  error: string;
  countsError: string;
  loadingCounts: boolean;
}>();

const emit = defineEmits<{
  refresh: [];
  'goto-ingest': [];
  'copy-env': [];
}>();

const namespaces = computed(() => ({
  poi_facts: props.status?.poi_facts ?? null,
  city_guides: props.status?.city_guides ?? null,
}));

const hasCounts = computed(() => {
  const { poi_facts: p, city_guides: g } = namespaces.value;
  return typeof p?.count === 'number' || typeof g?.count === 'number';
});

/** 两层都查不出来才算「库为空」；只查不出来一层时不该下这个结论 */
const isEmpty = computed(() => {
  const { poi_facts: p, city_guides: g } = namespaces.value;
  if (p?.error || g?.error) return false;
  const pc = p?.count ?? 0;
  const gc = g?.count ?? 0;
  return pc === 0 && gc === 0;
});

function formatCount(stat: KnowledgeNamespaceStat | null): string {
  if (!stat) return '—';
  if (stat.error) return '取不到';
  if (typeof stat.count !== 'number') return '—';
  return `${stat.count} 条`;
}

/** 0 条或取不到时用警示色 —— 这是「检索没结果」最常见的原因，要显眼 */
function countClass(stat: KnowledgeNamespaceStat | null): string {
  if (!stat) return '';
  if (stat.error) return 'is-bad';
  if (!stat.count) return 'is-warn';
  return 'is-ok';
}
</script>

<style scoped>
.ks-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--sp-4);
}

.ks-card {
  background: var(--bg-card);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-md);
  padding: var(--sp-5);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  align-items: flex-start;
}

.ks-skel {
  height: 180px;
  border-radius: var(--radius-md);
}

.ks-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  width: 100%;
}

.ks-head .ant-btn {
  margin-left: auto;
}

.ks-dot {
  font-size: 16px;
  flex-shrink: 0;
}

.is-ok {
  color: var(--success);
}
.is-warn {
  color: var(--warning);
}
.is-bad {
  color: var(--error);
}
.is-neutral {
  color: var(--text-3);
}

.ks-title {
  font-size: var(--fs-body);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.ks-main {
  font-size: var(--fs-sm);
  color: var(--text-2);
}

.ks-tag {
  margin: 0;
}

.ks-reason {
  font-size: var(--fs-caption);
  color: var(--error);
  background: #fff2f0;
  border: 1px solid #ffccc7;
  border-radius: var(--radius-sm);
  padding: var(--sp-2) var(--sp-3);
  word-break: break-word;
  width: 100%;
}

.ks-kv {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--sp-1) var(--sp-3);
  margin: 0;
  width: 100%;
  font-size: var(--fs-caption);
}

.ks-kv dt {
  color: var(--text-2);
  white-space: nowrap;
}

.ks-kv dd {
  margin: 0;
  color: var(--text-1);
  word-break: break-all;
}

.ks-count-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-3);
  width: 100%;
}

.ks-count-label {
  font-size: var(--fs-sm);
  color: var(--text-2);
}

.ks-count-value {
  font-size: var(--fs-h2);
  font-weight: var(--fw-semibold);
}

.ks-counts-skeleton {
  width: 100%;
}

.ks-skel-line {
  height: 28px;
  margin-bottom: var(--sp-2);
}

.ks-alert {
  width: 100%;
}

.ks-warn {
  font-size: var(--fs-caption);
  color: var(--warning);
}

.ks-fix {
  width: 100%;
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.ks-fix-title {
  margin-bottom: var(--sp-1);
  color: var(--text-1);
}

.ks-fix ul {
  margin: 0;
  padding-left: 1.2em;
}

.ks-fix li {
  margin-bottom: var(--sp-1);
}

.ks-fix-note {
  margin-top: var(--sp-2);
  color: var(--text-2);
}

.ks-code {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  background: var(--bg-sunken);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-sm);
  padding: 2px var(--sp-2);
}

.ks-code code {
  font-family: var(--font-num);
  font-size: var(--fs-caption);
  color: var(--text-1);
}

code {
  font-family: var(--font-num);
  font-size: 0.95em;
  background: var(--bg-sunken);
  padding: 1px 4px;
  border-radius: 4px;
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 991px) {
  .ks-grid {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 767px) {
  .ks-grid {
    grid-template-columns: 1fr;
  }

  .ks-card {
    padding: var(--sp-4);
  }
}
</style>
