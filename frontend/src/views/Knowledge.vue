<template>
  <div class="kb">
    <div class="kb-inner">
      <!-- ---------------- 页头 ---------------- -->
      <header class="masthead">
        <h1 class="masthead-title">知识库</h1>
        <p class="masthead-sub">
          把你自己找到的攻略放进来，生成行程时会参考这些内容，并在行程里标出来源。
          内置的 POI 事实与城市攻略照常生效。
        </p>
      </header>

      <!-- ---------------- RAG 开关警示 ----------------
         这是这一页**最重要的一条状态**:ENABLE_RAG 默认是关的(评测基线)。
         关着的时候上传能成功、入库也成功,但生成行程时完全不会用到 ——
         用户无从察觉。所以必须放在最上面,而不是折叠进状态条里。 -->
      <div v-if="status && !status.enabled" class="rag-off" role="status">
        <p class="rag-off-text">
          生成行程时<strong>不会</strong>使用知识库 —— 现在上传的内容不会被用到。
        </p>
        <a-button size="small" type="primary" :loading="toggling" @click="enableRag">
          立即开启
        </a-button>
      </div>
      <div v-else-if="status?.enabled_source === 'runtime'" class="rag-runtime" role="status">
        知识库已开启（上传时自动打开的临时开关，重启后恢复 .env 里的设置）
      </div>

      <!-- ---------------- 主体:上传 ---------------- -->
      <section class="kb-section">
        <KnowledgeUploadPanel
          :available="status?.available ?? false"
          :reason="status?.reason ?? ''"
          @ingested="onDocsChanged"
        />
      </section>

      <!-- ---------------- 主体:我上传的攻略 ---------------- -->
      <section class="kb-section">
        <KnowledgeDocList
          :docs="docs"
          :summary="docsSummary"
          :loading="docsLoading"
          @refresh="loadDocs"
          @changed="loadDocs"
          @deleted="onDocsChanged"
        />
      </section>

      <!-- ---------------- 进阶(折叠) ----------------
         状态条、检索调试、内置库维护都是**给维护者看**的东西 ——
         用户传攻略用不上它们,但排查问题时缺一不可。
         所以不是删掉,是收进折叠区:默认收起,展开即用。 -->
      <section class="kb-section">
        <a-collapse v-model:activeKey="advOpen" ghost class="adv">
          <a-collapse-panel key="advanced" header="进阶：状态 · 检索调试 · 内置库维护">
            <div class="adv-body">
              <KnowledgeStatusBar
                :status="status"
                :loading="loading"
                :error="error"
                :counts-error="countsError"
                :loading-counts="loadingCounts"
                @refresh="refresh"
                @goto-ingest="openAdvanced"
                @copy-env="copyEnableSnippet"
              />

              <KnowledgeSearchPanel ref="searchPanelRef" @goto-ingest="openAdvanced" />

              <KnowledgeMaintenancePanel
                :status="status"
                @finished="onMaintenanceFinished"
                @try-search="onTrySearch"
              />
            </div>
          </a-collapse-panel>
        </a-collapse>
      </section>

      <!-- ---------------- 说明 ---------------- -->
      <footer class="kb-foot">
        <a-button type="text" @click="helpOpen = true">这套知识库是怎么工作的？</a-button>
      </footer>
    </div>

    <a-modal v-model:open="helpOpen" title="知识库说明" :footer="null" :width="720">
      <KnowledgeHelpPanel />
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { message } from 'ant-design-vue';
import KnowledgeStatusBar from '@/components/knowledge/KnowledgeStatusBar.vue';
import KnowledgeSearchPanel from '@/components/knowledge/KnowledgeSearchPanel.vue';
import KnowledgeMaintenancePanel from '@/components/knowledge/KnowledgeMaintenancePanel.vue';
import KnowledgeHelpPanel from '@/components/knowledge/KnowledgeHelpPanel.vue';
import KnowledgeUploadPanel from '@/components/knowledge/KnowledgeUploadPanel.vue';
import KnowledgeDocList from '@/components/knowledge/KnowledgeDocList.vue';
import { useKnowledgeStatus } from '@/composables/useKnowledgeStatus';
import {
  listKnowledgeDocs,
  toggleKnowledgeRag,
} from '@/services/api';
import type { KnowledgeDocSummary, KnowledgeDocsSummary } from '@/types';

/**
 * 知识库页面（壳层）。
 *
 * 这一版把页面的**主角换掉了**。原来的定位是「RAG 的仪表盘 + 调试台」,
 * 回答的是"我的 RAG 现在什么状态" —— 只有维护者能用,普通用户进来看不懂。
 *
 * 现在回答的是「我想把我自己的攻略放进来」:
 *   上传(主体) → 我上传的攻略(列表) → 生成行程时被引用,并标出来源
 *
 * 原来的四块并没有删,全部收进了「进阶」折叠区:
 *   状态条 / 检索调试 / 内置库维护 —— 排查问题时缺一不可,
 *   但它们不是用户进这个页面的理由。
 *
 * 检索调试**刻意保留**而不是删掉:它是"证明 RAG 确实在工作"的唯一工具
 * (能搜「紫禁城」命中别名字段、能看到相似度分数),
 * 演示和排错都靠它。它的问题只是不该占据页面主体。
 */

const { status, loading, error, countsError, loadingCounts, refresh, refreshCounts } =
  useKnowledgeStatus();

const helpOpen = ref(false);
const toggling = ref(false);
const advOpen = ref<string[]>([]);

// ---- 上传的攻略列表 ----
const docs = ref<KnowledgeDocSummary[]>([]);
const docsSummary = ref<KnowledgeDocsSummary | null>(null);
const docsLoading = ref(false);

const searchPanelRef = ref<InstanceType<typeof KnowledgeSearchPanel> | null>(null);

function openAdvanced() {
  advOpen.value = ['advanced'];
}

async function loadDocs() {
  docsLoading.value = true;
  try {
    const res = await listKnowledgeDocs();
    docs.value = res.docs;
    docsSummary.value = res.summary;
  } catch (e: unknown) {
    message.error((e as Error)?.message || '读取攻略列表失败');
  } finally {
    docsLoading.value = false;
  }
}

/** 文档列表变化(入库/删除)要同时刷两处:列表本身 + 状态里的 uploaded 计数 */
function onDocsChanged() {
  void loadDocs();
  refreshCounts();
}

/** 内置库灌完只影响计数,不影响上传文档 */
function onMaintenanceFinished() {
  refreshCounts();
}

async function enableRag() {
  toggling.value = true;
  try {
    const res = await toggleKnowledgeRag(true);
    message.success(res.message);
    await refresh();
  } catch (e: unknown) {
    message.error((e as Error)?.message || '开启失败');
  } finally {
    toggling.value = false;
  }
}

function onTrySearch(query: string) {
  searchPanelRef.value?.search(query);
}

async function copyEnableSnippet() {
  const snippet = 'ENABLE_RAG=true';
  try {
    await navigator.clipboard.writeText(snippet);
    message.success('已复制到剪贴板');
  } catch {
    // 非 HTTPS 或浏览器不给剪贴板权限时走到这里，让用户自己抄
    message.info(`请手动添加到 backend/.env：${snippet}`, 8);
  }
}

onMounted(() => {
  void loadDocs();
});
</script>

<style scoped>
.kb {
  padding: var(--space-6) var(--space-4) var(--space-9);
}

.kb-inner {
  max-width: var(--container);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

/* ---------------- 页头 ---------------- */
.masthead-title {
  margin: 0 0 var(--space-2);
  font-size: var(--fs-h1);
  font-weight: var(--fw-semibold);
  letter-spacing: var(--ls-tight);
  color: var(--text-1);
}

.masthead-sub {
  margin: 0;
  max-width: 62ch;
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--text-2);
}

/* ---------------- RAG 开关 ---------------- */
.rag-off {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding: var(--space-3) var(--space-4);
  background: var(--warning);
  border-radius: var(--radius-md);
}

.rag-off-text {
  margin: 0;
  font-size: var(--fs-caption);
  color: #4b2a05;
}

.rag-off-text strong {
  color: #4b2a05;
}

.rag-runtime {
  padding: var(--space-2) var(--space-4);
  background: var(--bg-tint);
  border-radius: var(--radius-md);
  font-size: var(--fs-micro);
  color: var(--brand-ink);
}

/* ---------------- 区块 ---------------- */
.kb-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

/* ---------------- 进阶 ---------------- */
.adv {
  border-top: 1px solid var(--line-1);
}

.adv-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  padding-top: var(--space-4);
}

.kb-foot {
  display: flex;
  justify-content: center;
}

@media (max-width: 575px) {
  .kb {
    padding: var(--space-4) var(--space-3) var(--space-8);
  }
}
</style>
