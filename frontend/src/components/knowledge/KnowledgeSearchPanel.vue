<template>
  <section class="sp">
    <header class="sp-head">
      <SearchOutlined class="sp-head-icon" aria-hidden="true" />
      <h2 class="sp-head-title">检索调试</h2>
      <span class="sp-head-desc">用真实的问法试一下知识库里能搜到什么</span>
    </header>

    <!-- ---------------- 检索栏 ---------------- -->
    <div class="sp-form">
      <a-input-search
        v-model:value="query"
        class="sp-input"
        size="large"
        placeholder="例如：北京 历史文化 一日游"
        :loading="loading"
        enter-button="检索"
        allow-clear
        @search="doSearch"
      />

      <div class="sp-filters">
        <a-radio-group v-model:value="namespace" size="small" button-style="solid">
          <a-radio-button value="all">全部</a-radio-button>
          <a-radio-button value="city_guides">城市攻略</a-radio-button>
          <a-radio-button value="poi_facts">POI 事实</a-radio-button>
        </a-radio-group>

        <label class="sp-topk">
          <span class="sp-topk-label">条数</span>
          <a-select v-model:value="topK" size="small" style="width: 76px">
            <a-select-option v-for="k in TOP_K_OPTIONS" :key="k" :value="k">{{ k }}</a-select-option>
          </a-select>
        </label>
      </div>

      <div class="sp-samples">
        <span class="sp-samples-label">试试：</span>
        <a-button
          v-for="s in KNOWLEDGE_SAMPLE_QUERIES"
          :key="s"
          type="link"
          size="small"
          @click="runSample(s)"
        >
          {{ s }}
        </a-button>
      </div>
    </div>

    <!-- ---------------- 结果 ---------------- -->

    <!-- 请求本身失败（后端没起、超时） -->
    <StatePanel
      v-if="requestError"
      state="error"
      error-kind="network"
      title="检索请求失败"
      :detail="requestError"
      @retry="doSearch"
    />

    <!-- 还没搜过 -->
    <p v-else-if="!searched" class="sp-idle">
      输入关键词后回车即可检索。知识库分两层：城市攻略回答「怎么安排合理」，POI 事实回答「有哪些真实景点、在哪」。
    </p>

    <StatePanel v-else-if="loading" state="loading" skeleton="list" :skeleton-count="3" />

    <template v-else-if="response">
      <!-- 后端吞掉了异常、只留下 last_error —— 这是区分「连不上」与「没命中」的唯一依据 -->
      <a-alert
        v-if="response.error && !response.hits.length"
        class="sp-alert"
        type="error"
        show-icon
        message="检索失败"
        :description="response.error"
      />

      <!-- 一层成功一层失败 -->
      <a-alert
        v-else-if="response.partial"
        class="sp-alert"
        type="warning"
        show-icon
        message="仅部分知识层返回了结果"
        :description="`另一层检索失败：${response.error || '原因未记录'}。下面的结果不完整。`"
      />

      <!-- 库是空的 -->
      <a-alert
        v-else-if="libraryEmpty"
        class="sp-alert"
        type="warning"
        show-icon
        message="知识库还没有数据"
        description="两层 collection 的条数都是 0，检索不可能有结果。先灌库再回来试。"
      >
        <template #action>
          <a-button size="small" type="primary" @click="emit('goto-ingest')">去灌库</a-button>
        </template>
      </a-alert>

      <!-- 确实没有命中 -->
      <a-alert
        v-else-if="!response.hits.length"
        class="sp-alert"
        type="info"
        show-icon
        message="没有检索到相关内容"
        description="换一个更具体的问法，或者试试「紫禁城」「兵马俑」这类走别名的名称 —— 别名是这套知识库最容易被漏掉、又最影响效果的部分。"
      />

      <template v-else>
        <div class="sp-meta">
          <span>命中 {{ response.hits.length }} 条</span>
          <span class="sp-sep" aria-hidden="true">·</span>
          <span class="u-num">耗时 {{ response.elapsed_ms }} ms</span>
          <span class="sp-sep" aria-hidden="true">·</span>
          <span>分数为 COSINE 相似度，两层共用同一模型故可直接比较</span>
          <a-button
            v-if="response.error"
            type="link"
            size="small"
            @click="showPartialDetail = !showPartialDetail"
          >
            部分失败详情
          </a-button>
        </div>
        <p v-if="showPartialDetail && response.error" class="sp-partial-detail">
          {{ response.error }}
        </p>

        <ul class="sp-list">
          <li
            v-for="(hit, i) in response.hits"
            :key="`${hit.namespace}-${i}`"
            class="sp-item anim-stagger"
            :style="{ '--i': i }"
          >
            <div class="sp-item-top">
              <div class="sp-score">
                <span class="sp-score-bar">
                  <span class="sp-score-fill" :style="{ width: scoreWidth(hit.score) }"></span>
                </span>
                <span class="sp-score-num u-num">{{ hit.score.toFixed(4) }}</span>
              </div>
              <a-tag :color="knowledgeNamespaceOf(hit.namespace).color" class="sp-ns">
                {{ knowledgeNamespaceOf(hit.namespace).text }}
              </a-tag>
            </div>

            <p class="sp-source">{{ hit.source }}<span v-if="hit.heading_path"> &gt; {{ hit.heading_path }}</span></p>

            <p class="sp-content" :class="{ 'is-expanded': expanded.has(i) }">{{ hit.content }}</p>

            <a-button
              v-if="hit.content.length > 120"
              type="link"
              size="small"
              class="sp-toggle"
              @click="toggleExpanded(i)"
            >
              {{ expanded.has(i) ? '收起' : '展开全文' }}
            </a-button>
          </li>
        </ul>
      </template>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { SearchOutlined } from '@ant-design/icons-vue';
import StatePanel from '@/components/common/StatePanel.vue';
import { searchKnowledge } from '@/services/api';
import { KNOWLEDGE_SAMPLE_QUERIES, knowledgeNamespaceOf } from '@/constants/tripOptions';
import type { KnowledgeNamespaceFilter, KnowledgeSearchResponse } from '@/types';

const emit = defineEmits<{ 'goto-ingest': [] }>();

const TOP_K_OPTIONS = [3, 4, 6, 10] as const;

const query = ref('');
const namespace = ref<KnowledgeNamespaceFilter>('all');
const topK = ref<number>(4);

const loading = ref(false);
const searched = ref(false);
const requestError = ref('');
const response = ref<KnowledgeSearchResponse | null>(null);
const showPartialDetail = ref(false);

/** 展开状态按命中序号记 —— 结果换了之后旧的展开状态自然失效 */
const expanded = ref(new Set<number>());

/**
 * 「库是空的」判断。
 * namespace_counts 里 -1 表示那一层统计不出来，此时不能下「库是空的」这个结论。
 */
const libraryEmpty = computed(() => {
  const counts = response.value?.namespace_counts;
  if (!counts) return false;
  const values = Object.values(counts);
  if (!values.length) return false;
  if (values.some((v) => v < 0)) return false;
  return values.every((v) => v === 0);
});

function scoreWidth(score: number): string {
  // COSINE 值域 [-1,1]，实际命中集中在 [0.5,0.9]。
  // 这里只做线性映射，**不做颜色分级** —— 「大于 0.7 就算好结果」这种判断
  // 没有依据，用颜色暗示会给用户错误信号。
  const clamped = Math.max(0, Math.min(1, score));
  return `${(clamped * 100).toFixed(1)}%`;
}

function toggleExpanded(i: number) {
  const next = new Set(expanded.value);
  if (next.has(i)) next.delete(i);
  else next.add(i);
  expanded.value = next;
}

async function doSearch() {
  const q = query.value.trim();
  if (!q) return;

  loading.value = true;
  requestError.value = '';
  showPartialDetail.value = false;
  expanded.value = new Set();

  try {
    response.value = await searchKnowledge({
      query: q,
      namespace: namespace.value,
      top_k: topK.value,
    });
    searched.value = true;
  } catch (e: unknown) {
    requestError.value = (e as Error)?.message || '检索失败';
    response.value = null;
    searched.value = true;
  } finally {
    loading.value = false;
  }
}

function runSample(sample: string) {
  query.value = sample;
  doSearch();
}

/** 暴露给壳层：数据源区点「试检索」时，把城市名填进来并立即检索 */
defineExpose({ search: runSample });
</script>

<style scoped>
.sp {
  background: var(--bg-card);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-md);
  padding: var(--sp-5);
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}

.sp-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.sp-head-icon {
  font-size: 16px;
  color: var(--brand-500);
}

.sp-head-title {
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
}

.sp-head-desc {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.sp-form {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.sp-input {
  max-width: 640px;
}

.sp-filters {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  flex-wrap: wrap;
}

.sp-topk {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
}

.sp-topk-label {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.sp-samples {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  flex-wrap: wrap;
}

.sp-samples-label {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.sp-idle {
  font-size: var(--fs-sm);
  color: var(--text-2);
  padding: var(--sp-3) 0;
}

.sp-alert {
  margin-bottom: 0;
}

.sp-meta {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.sp-sep {
  color: var(--text-3);
}

.sp-partial-detail {
  font-size: var(--fs-caption);
  color: var(--warning);
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: var(--radius-sm);
  padding: var(--sp-2) var(--sp-3);
  font-family: var(--font-num);
  word-break: break-word;
}

.sp-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.sp-item {
  border: 1px solid var(--border-1);
  border-radius: var(--radius-sm);
  padding: var(--sp-4);
  background: var(--bg-card);
  transition: border-color var(--dur-fast) var(--ease-out);
}

.sp-item:hover {
  border-color: var(--brand-300);
}

.sp-item-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  margin-bottom: var(--sp-2);
}

.sp-score {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  min-width: 0;
  flex: 1;
}

.sp-score-bar {
  position: relative;
  display: block;
  width: 120px;
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--brand-50);
  overflow: hidden;
  flex-shrink: 0;
}

.sp-score-fill {
  position: absolute;
  inset: 0 auto 0 0;
  background: var(--brand-500);
  border-radius: var(--radius-pill);
}

.sp-score-num {
  font-size: var(--fs-caption);
  color: var(--text-1);
  font-weight: var(--fw-medium);
}

.sp-ns {
  margin: 0;
  flex-shrink: 0;
}

.sp-source {
  font-family: var(--font-num);
  font-size: var(--fs-caption);
  color: var(--text-2);
  margin-bottom: var(--sp-2);
  word-break: break-all;
}

.sp-content {
  font-size: var(--fs-sm);
  color: var(--text-1);
  line-height: var(--lh-body);
  /* 默认 3 行截断。命中的 chunk 可能上千字符，全展开会把列表撑得极长 */
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-word;
}

.sp-content.is-expanded {
  display: block;
  overflow: visible;
}

.sp-toggle {
  padding: 0;
  height: auto;
  margin-top: var(--sp-1);
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 767px) {
  .sp {
    padding: var(--sp-4);
  }

  .sp-score-bar {
    width: 72px;
  }

  .sp-filters {
    gap: var(--sp-2);
  }
}

@media (max-width: 575px) {
  .sp-input {
    max-width: 100%;
  }
}
</style>
