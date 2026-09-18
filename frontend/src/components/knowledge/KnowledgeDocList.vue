<template>
  <section class="dl" aria-label="我上传的攻略">
    <header class="dl-head">
      <h3 class="dl-title">
        我上传的攻略
        <span v-if="summary?.docs" class="u-num dl-count">{{ summary.docs }} 份</span>
      </h3>
      <a-button size="small" type="text" :loading="loading" @click="emit('refresh')">刷新</a-button>
    </header>

    <p v-if="!loading && !docs.length" class="dl-empty">
      还没有上传过攻略。传一份你自己的攻略，生成行程时就会参考它。
    </p>

    <a-spin v-else-if="loading && !docs.length" />

    <ul v-else class="dl-list">
      <li v-for="doc in docs" :key="doc.id" class="dl-item">
        <div class="dl-main">
          <p class="dl-name">
            {{ doc.title }}
            <a-tag
              :color="statusMeta(doc.status).color"
              class="dl-status"
            >
              {{ statusMeta(doc.status).text }}
            </a-tag>
          </p>
          <p class="dl-meta">
            <span class="u-num">{{ doc.chunk_count }}</span> 段 ·
            {{ shortDate(doc.created_at) }} · 来自 {{ originLabel(doc.origin) }}
            <span v-if="doc.status === 'failed' && doc.error" class="dl-error">
              —— {{ doc.error }}
            </span>
          </p>
        </div>

        <div class="dl-actions">
          <a-button size="small" type="text" :disabled="doc.status === 'pending'" @click="view(doc)">
            查看分块
          </a-button>
          <a-popconfirm
            :title="`删除《${doc.title}》？会同时从知识库里移除它的所有分块。`"
            ok-text="删除"
            cancel-text="取消"
            :ok-button-props="{ danger: true }"
            @confirm="remove(doc)"
          >
            <a-button size="small" type="text" danger :loading="deletingId === doc.id">删除</a-button>
          </a-popconfirm>
        </div>
      </li>
    </ul>

    <!-- 分块预览:看"它到底被切成了什么样"是排查检索问题的第一步 -->
    <a-modal
      v-model:open="detailOpen"
      :title="detail ? `《${detail.doc.title}》的分块` : '分块'"
      :footer="null"
      :width="640"
    >
      <a-spin v-if="detailLoading" />
      <template v-else-if="detail">
        <p class="dl-detail-hint">
          共 <span class="u-num">{{ detail.chunks.length }}</span> 块。
          每一块都会被独立检索，标题是它在原文里的位置。
        </p>
        <ol class="dl-detail-list">
          <li v-for="c in detail.chunks" :key="c.idx" class="dl-detail-item">
            <div class="dl-detail-head">
              <span class="dl-detail-path">{{ c.heading_path || '（开头）' }}</span>
              <span class="u-num dl-detail-chars">{{ c.chars }} 字</span>
            </div>
            <p class="dl-detail-snippet">{{ c.snippet }}…</p>
          </li>
        </ol>
      </template>
    </a-modal>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { message } from 'ant-design-vue';
import { deleteKnowledgeDoc, getKnowledgeDoc } from '@/services/api';
import type {
  KnowledgeDocOrigin,
  KnowledgeDocStatus,
  KnowledgeDocSummary,
  KnowledgeDocsSummary,
} from '@/types';

/**
 * 「我上传的攻略」列表。
 *
 * **删除必须有** —— 不只是产品完整度的问题:同一篇攻略传两次,
 * 库里就会有语义几乎相同的两份内容,检索返回的几条全是同一件事。
 * 没有删除的话,用户唯一的出路是重新灌整个库,连自己上传的都一起清掉。
 */

// 不赋值给变量:字段只在模板里用,赋了反而触发 noUnusedLocals
defineProps<{
  docs: KnowledgeDocSummary[];
  summary?: KnowledgeDocsSummary | null;
  loading?: boolean;
}>();

const emit = defineEmits<{
  /** 列表内容变了(删了一篇),父组件刷新 */
  changed: [];
  refresh: [];
  /** 删除成功。与 changed 分开:父组件要同时刷新状态里的 uploaded 计数 */
  deleted: [];
}>();

const deletingId = ref('');
const detailOpen = ref(false);
const detailLoading = ref(false);
const detail = ref<Awaited<ReturnType<typeof getKnowledgeDoc>> | null>(null);

function statusMeta(status: KnowledgeDocStatus): { text: string; color: string } {
  switch (status) {
    case 'ingested':
      return { text: '已入库', color: 'green' };
    case 'pending':
      return { text: '待入库', color: 'orange' };
    case 'failed':
      return { text: '入库失败', color: 'red' };
    default:
      return { text: status, color: 'default' };
  }
}

function originLabel(origin: KnowledgeDocOrigin): string {
  const map: Record<KnowledgeDocOrigin, string> = {
    md: 'Markdown',
    txt: '文本',
    pdf: 'PDF',
    image: '图片识别',
    paste: '粘贴',
  };
  return map[origin] ?? origin;
}

function shortDate(iso: string): string {
  // created_at 是 "2026-09-17T22:30:00" 这种,列表里只留月日
  const m = iso.match(/^\d{4}-(\d{2})-(\d{2})/);
  return m ? `${Number(m[1])} 月 ${Number(m[2])} 日` : iso.slice(0, 10);
}

async function view(doc: KnowledgeDocSummary) {
  detailOpen.value = true;
  detailLoading.value = true;
  detail.value = null;
  try {
    detail.value = await getKnowledgeDoc(doc.id);
  } catch (e: unknown) {
    message.error((e as Error)?.message || '读取分块失败');
    detailOpen.value = false;
  } finally {
    detailLoading.value = false;
  }
}

async function remove(doc: KnowledgeDocSummary) {
  deletingId.value = doc.id;
  try {
    const res = await deleteKnowledgeDoc(doc.id);
    message.success(res.message || '已删除');
    emit('changed');
    emit('deleted');
  } catch (e: unknown) {
    message.error((e as Error)?.message || '删除失败');
  } finally {
    deletingId.value = '';
  }
}
</script>

<style scoped>
.dl {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.dl-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.dl-title {
  margin: 0;
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.dl-count {
  margin-left: var(--space-2);
  font-size: var(--fs-caption);
  font-weight: var(--fw-regular);
  color: var(--text-3);
}

.dl-empty {
  margin: 0;
  padding: var(--space-5);
  text-align: center;
  font-size: var(--fs-caption);
  color: var(--text-3);
  background: var(--bg-sunken);
  border-radius: var(--radius-md);
}

.dl-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.dl-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
}

.dl-item + .dl-item {
  border-top: 1px solid var(--line-1);
}

.dl-main {
  min-width: 0;
}

.dl-name {
  margin: 0 0 var(--space-1);
  font-size: var(--fs-body);
  font-weight: var(--fw-medium);
  color: var(--text-1);
}

.dl-status {
  margin-left: var(--space-2);
}

.dl-meta {
  margin: 0;
  font-size: var(--fs-micro);
  color: var(--text-2);
}

.dl-error {
  color: var(--warning);
}

.dl-actions {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex-shrink: 0;
}

/* ---------------- 分块弹窗 ---------------- */
.dl-detail-hint {
  margin: 0 0 var(--space-3);
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.dl-detail-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-height: 55vh;
  overflow-y: auto;
}

.dl-detail-item {
  padding: var(--space-3);
  border: 1px solid var(--line-1);
  border-radius: var(--radius-sm);
}

.dl-detail-head {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
  margin-bottom: var(--space-1);
}

.dl-detail-path {
  font-size: var(--fs-caption);
  font-weight: var(--fw-medium);
  color: var(--brand-600);
}

.dl-detail-chars {
  font-size: var(--fs-micro);
  color: var(--text-3);
}

.dl-detail-snippet {
  margin: 0;
  font-size: var(--fs-micro);
  line-height: var(--lh-body);
  color: var(--text-2);
}
</style>
