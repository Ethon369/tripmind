<template>
  <section class="kbar">
    <!-- 首屏加载：只占位，不显示空态文案 -->
    <div v-if="loading && !status" class="skeleton-block kbar-skel"></div>

    <template v-else>
      <!-- ---------------- 一行状态条 ----------------
           原来这里是三张卡片，占满整个首屏。压缩成一行之后，
           用户一眼就能看出「能不能用 / 有多少 / 生成时用不用」，
           然后把注意力留给真正要做的事：检索。 -->
      <div class="kbar-row">
        <span class="kbar-state" :class="stateClass">
          <span class="kbar-mark" aria-hidden="true"></span>
          {{ stateText }}
        </span>

        <span class="kbar-divider" aria-hidden="true"></span>

        <span class="kbar-stat">
          POI <strong class="u-num">{{ fmtCount(namespaces.poi_facts) }}</strong>
        </span>
        <span class="kbar-stat">
          攻略 <strong class="u-num">{{ fmtCount(namespaces.city_guides) }}</strong>
        </span>

        <span class="kbar-divider" aria-hidden="true"></span>

        <!-- 全页最容易被忽略、又最需要被看见的一条信息 -->
        <span class="kbar-flag" :class="status?.enabled ? 'is-on' : 'is-off'">
          {{ status?.enabled ? '生成时已启用' : '生成时未启用' }}
        </span>

        <span class="kbar-actions">
          <a-button type="text" size="small" @click="expanded = !expanded">
            {{ expanded ? '收起' : '详情' }}
          </a-button>
          <a-button type="text" size="small" :loading="loading" @click="emit('refresh')">
            刷新
          </a-button>
        </span>
      </div>

      <!-- 展开区才有错误详情与修复指引 —— 这些是排错时才需要的，不该常驻首屏 -->
      <div v-if="expanded" class="kbar-detail">
        <StatePanel
          v-if="error"
          state="error"
          error-kind="network"
          title="读不到知识库状态"
          :detail="error"
          @retry="emit('refresh')"
        />

        <template v-else-if="status">
          <dl class="kbar-dl">
            <div class="kbar-dl-row">
              <dt>可用性</dt>
              <dd>{{ status.available ? '正常' : '异常' }}</dd>
            </div>
            <div class="kbar-dl-row">
              <dt>Milvus</dt>
              <dd class="u-num">{{ status.milvus_uri }}</dd>
            </div>
            <div class="kbar-dl-row">
              <dt>collection</dt>
              <dd class="u-num">
                {{ status.collections.poi_facts }} · {{ status.collections.city_guides }}
              </dd>
            </div>
            <div class="kbar-dl-row">
              <dt>向量维度</dt>
              <dd class="u-num">{{ status.dim_expected }}</dd>
            </div>
            <div class="kbar-dl-row">
              <dt>注入条数</dt>
              <dd class="u-num">top_k = {{ status.top_k }}</dd>
            </div>
          </dl>

          <!-- 失败原因原样展示 —— 后端刻意不把它吞成一句笼统的「RAG 不可用」 -->
          <p v-if="!status.available" class="kbar-reason">{{ status.reason }}</p>

          <div v-if="!status.available" class="kbar-block">
            <p class="kbar-block-title">常见原因</p>
            <ul class="kbar-list">
              <li>
                Milvus 没启动：<code>cd D:/devlop/Milvus &amp;&amp; docker compose up -d</code>
              </li>
              <li>
                <code>EMBED_API_KEY</code> / <code>EMBED_BASE_URL</code> 没配：检查
                <code>backend/.env</code>
              </li>
            </ul>
          </div>

          <p v-if="countsError" class="kbar-warn">{{ countsError }}</p>

          <!-- 计数为 0 是「检索没结果」的头号原因，这里直接给出口 -->
          <p v-if="isEmpty" class="kbar-warn">
            向量库还没有数据，检索不会有结果。
            <a-button type="link" size="small" @click="emit('goto-ingest')">去灌库</a-button>
          </p>

          <!-- ENABLE_RAG 说明 -->
          <div v-if="!status.enabled" class="kbar-block">
            <p class="kbar-block-title">开启生成时注入</p>
            <p class="kbar-block-text">
              在 <code>backend/.env</code> 写入下面这行，然后重启后端。环境变量在进程启动时读取，
              所以没有做在线开关 —— 改了不重启不生效，做个开关反而是欺骗。
            </p>
            <div class="kbar-code">
              <code>ENABLE_RAG=true</code>
              <a-button type="text" size="small" @click="emit('copy-env')">复制</a-button>
            </div>
          </div>
        </template>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import StatePanel from '@/components/common/StatePanel.vue';
import type { KnowledgeNamespaceStat, KnowledgeStatus } from '@/types';

/**
 * 知识库状态条。
 *
 * 替代原来那个占满首屏的「三张卡片」区块。三张卡各自完整展示可用性、
 * 计数、启用状态，信息密度很低却吃掉整个首屏 —— 用户进来是想检索的，
 * 不是来读状态面板的。
 *
 * 现在压缩成一行白卡片，把错误详情、修复指引、开关说明全部收进「详情」。
 * 关键信息一个没少，但首屏从"要读三块"变成"一眼看完"。
 */

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

const expanded = ref(false);

const namespaces = computed(() => ({
  poi_facts: props.status?.poi_facts ?? null,
  city_guides: props.status?.city_guides ?? null,
}));

const stateText = computed(() => {
  if (!props.status) return '未知';
  return props.status.available ? '正常' : '异常';
});

const stateClass = computed(() => {
  if (!props.status) return 'is-unknown';
  return props.status.available ? 'is-ok' : 'is-bad';
});

/** 两层都查不出来才算「库为空」；只查不出来一层时不该下这个结论 */
const isEmpty = computed(() => {
  const { poi_facts: p, city_guides: g } = namespaces.value;
  if (p?.error || g?.error) return false;
  return (p?.count ?? 0) === 0 && (g?.count ?? 0) === 0;
});

function fmtCount(stat: KnowledgeNamespaceStat | null | undefined): string {
  if (!stat) return '—';
  if (stat.error) return '取不到';
  if (typeof stat.count !== 'number') return '—';
  return stat.count.toLocaleString('zh-CN');
}
</script>

<style scoped>
.kbar {
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-1);
}

.kbar-skel {
  height: 56px;
  border-radius: var(--radius-lg);
}

/* ---------------- 一行状态条 ---------------- */
.kbar-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  min-height: 56px;
  padding: var(--space-3) var(--space-5);
  font-size: var(--fs-caption);
}

.kbar-mark {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: var(--space-2);
  flex-shrink: 0;
}

.kbar-state {
  display: inline-flex;
  align-items: center;
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.is-ok .kbar-mark {
  background: var(--success);
  box-shadow: 0 0 0 3px var(--success-tint);
}
.is-bad .kbar-mark {
  background: var(--error);
  box-shadow: 0 0 0 3px var(--error-tint);
}
.is-unknown .kbar-mark {
  background: var(--text-3);
}

.kbar-divider {
  width: 1px;
  height: 14px;
  background: var(--line-2);
}

.kbar-stat {
  color: var(--text-2);
}

.kbar-stat strong {
  color: var(--text-1);
  font-weight: var(--fw-semibold);
}

/* 「生成时是否启用」用色块标出 —— 这是全页最需要被看见的一条状态 */
.kbar-flag {
  padding: 3px var(--space-3);
  border-radius: var(--radius-pill);
  font-size: var(--fs-micro);
  font-weight: var(--fw-medium);
}

.kbar-flag.is-on {
  background: var(--success-tint);
  color: #1a8f66;
}

.kbar-flag.is-off {
  background: var(--warning-tint);
  color: var(--warning);
}

.kbar-actions {
  margin-left: auto;
  display: flex;
  gap: var(--space-2);
}

/* ---------------- 展开区 ---------------- */
.kbar-detail {
  padding: 0 var(--space-5) var(--space-5);
  animation: fadeIn var(--dur-base) var(--ease-out) both;
}

.kbar-dl {
  margin: 0 0 var(--space-4);
  padding-top: var(--space-4);
  border-top: 1px solid var(--line-1);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--fs-caption);
}

.kbar-dl-row {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: var(--space-4);
}

.kbar-dl dt {
  color: var(--text-2);
}

.kbar-dl dd {
  margin: 0;
  color: var(--text-1);
  word-break: break-all;
}

.kbar-reason {
  font-size: var(--fs-caption);
  color: var(--error);
  background: var(--error-tint);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-4);
  word-break: break-word;
}

.kbar-block {
  margin-bottom: var(--space-4);
}

.kbar-block-title {
  font-size: var(--fs-micro);
  font-weight: var(--fw-semibold);
  color: var(--text-2);
  margin-bottom: var(--space-2);
}

.kbar-block-text {
  font-size: var(--fs-caption);
  color: var(--text-2);
  line-height: var(--lh-body);
  margin-bottom: var(--space-2);
  max-width: 64ch;
}

.kbar-list {
  margin: 0;
  padding-left: 1.2em;
  font-size: var(--fs-caption);
  color: var(--text-2);
  line-height: var(--lh-body);
}

.kbar-list li + li {
  margin-top: var(--space-1);
}

.kbar-code {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  background: var(--bg-sunken);
  border-radius: var(--radius-md);
  padding: var(--space-1) var(--space-2) var(--space-1) var(--space-3);
}

.kbar-warn {
  font-size: var(--fs-caption);
  color: var(--warning);
  margin-bottom: var(--space-3);
}

code {
  font-family: var(--font-num);
  font-size: 0.95em;
  background: var(--bg-sunken);
  padding: 1px 5px;
  border-radius: var(--radius-xs);
}

.kbar-code code {
  background: transparent;
  padding: 0;
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 767px) {
  .kbar-row {
    gap: var(--space-2);
    padding: var(--space-3) var(--space-4);
  }

  .kbar-divider {
    display: none;
  }

  .kbar-actions {
    margin-left: 0;
    width: 100%;
    justify-content: flex-end;
  }

  .kbar-detail {
    padding: 0 var(--space-4) var(--space-4);
  }

  .kbar-dl-row {
    grid-template-columns: 76px 1fr;
  }
}
</style>
