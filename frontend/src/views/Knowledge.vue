<template>
  <div class="kb">
    <div class="kb-inner">
      <!-- ---------------- 页头 ---------------- -->
      <header class="masthead">
        <h1 class="masthead-title">知识库</h1>
        <p class="masthead-sub">
          行程生成时参考的外部知识：1750 条带真实坐标的 POI 事实，加 5 篇城市攻略。
          在这里可以试检索、确认它是否生效，以及改动后如何重新灌入。
        </p>
      </header>

      <!-- ---------------- 状态：一行 ----------------
           原来这里是三张卡片，吃掉整个首屏。现在压成一行，
           详情收进「详情」按钮 —— 首屏应该留给真正要做的事。 -->
      <KnowledgeStatusBar
        :status="status"
        :loading="loading"
        :error="error"
        :counts-error="countsError"
        :loading-counts="loadingCounts"
        @refresh="refresh"
        @goto-ingest="scrollToIngest"
        @copy-env="copyEnableSnippet"
      />

      <!-- ---------------- 检索（主体） ---------------- -->
      <section ref="searchEl" class="kb-section">
        <KnowledgeSearchPanel ref="searchPanelRef" @goto-ingest="scrollToIngest" />
      </section>

      <!-- ---------------- 维护（折叠：灌库 + 数据源） ---------------- -->
      <section ref="ingestEl" class="kb-section">
        <KnowledgeMaintenancePanel
          :status="status"
          @finished="onIngestFinished"
          @try-search="onTrySearch"
        />
      </section>

      <!-- ---------------- 说明：收成一行链接 ----------------
           原来是常驻的折叠区块，占了页面底部一整块。
           这是「需要时才会看」的背景知识，放进弹窗更合适。 -->
      <footer class="kb-foot">
        <a-button type="text" @click="helpOpen = true">这套知识库是怎么工作的？</a-button>
      </footer>
    </div>

    <a-modal
      v-model:open="helpOpen"
      title="知识库说明"
      :footer="null"
      :width="720"
    >
      <KnowledgeHelpPanel />
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { message } from 'ant-design-vue';
import KnowledgeStatusBar from '@/components/knowledge/KnowledgeStatusBar.vue';
import KnowledgeSearchPanel from '@/components/knowledge/KnowledgeSearchPanel.vue';
import KnowledgeMaintenancePanel from '@/components/knowledge/KnowledgeMaintenancePanel.vue';
import KnowledgeHelpPanel from '@/components/knowledge/KnowledgeHelpPanel.vue';
import { useKnowledgeStatus } from '@/composables/useKnowledgeStatus';

/**
 * 知识库页面（壳层）。
 *
 * 这一版做了**减法**。原来页面是 5 个平铺区块：
 *   环境状态（拆成 3 张卡）／ 检索调试 ／ 数据源 ／ 灌库 ／ 说明
 * 其中数据源、灌库、说明是"偶尔用一次"的东西，却占了约 60% 的页面，
 * 而首屏被三张状态卡占满 —— 用户进来是想检索的，不是来读状态面板的。
 *
 * 现在：
 *   状态 → 一行（详情可展开）
 *   检索 → 主体
 *   维护 → 折叠（灌库 + 数据源合并，因为预览接口本来就在统计数据源）
 *   说明 → 底部一行链接，点开弹窗
 */

const { status, loading, error, countsError, loadingCounts, refresh, refreshCounts } =
  useKnowledgeStatus();

const helpOpen = ref(false);

const searchPanelRef = ref<InstanceType<typeof KnowledgeSearchPanel> | null>(null);
const searchEl = ref<HTMLElement | null>(null);
const ingestEl = ref<HTMLElement | null>(null);

function scrollTo(el: HTMLElement | null) {
  el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

const scrollToIngest = () => scrollTo(ingestEl.value);

function onTrySearch(query: string) {
  scrollTo(searchEl.value);
  searchPanelRef.value?.search(query);
}

/** 灌库完成后只刷新计数，不重跑可用性检查（可用性不会因为灌库而变化） */
function onIngestFinished() {
  refreshCounts();
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
</script>

<style scoped>
.kb {
  min-height: 100%;
  background: var(--bg-page);
  padding: var(--space-7) 0 var(--space-9);
}

.kb-inner {
  max-width: var(--container-narrow);
  margin: 0 auto;
  padding-inline: var(--pad-page-sm);
}

@media (min-width: 768px) {
  .kb-inner {
    padding-inline: var(--pad-page-md);
  }
}

@media (min-width: 992px) {
  .kb-inner {
    padding-inline: 0;
  }
}

/* ---------------- 页头 ----------------
 * 功能页用左对齐的常规页头，不做居中的 hero ——
 * 用户是来做事的，不是来看封面。
 */
.masthead {
  margin-bottom: var(--space-6);
  animation: fadeInUp var(--dur-slow) var(--ease-out) both;
}

.masthead-title {
  font-size: var(--fs-h1);
  font-weight: var(--fw-semibold);
  letter-spacing: var(--ls-tight);
  color: var(--text-1);
  margin-bottom: var(--space-2);
}

.masthead-sub {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--text-2);
  max-width: 62ch;
}

/* ---------------- 区块 ---------------- */
.kb-section {
  margin-top: var(--space-6);
  /* 锚点滚动留出吸顶栏的高度，否则 scrollIntoView 会把标题顶到栏后面 */
  scroll-margin-top: calc(var(--header-h) + var(--space-5));
}

/* ---------------- 页脚 ---------------- */
.kb-foot {
  margin-top: var(--space-6);
  display: flex;
  justify-content: center;
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 767px) {
  .kb {
    padding: var(--space-6) 0 var(--space-8);
  }

  .kb-inner {
    padding-inline: var(--space-3);
  }

  .kb-section {
    margin-top: var(--space-5);
  }
}

@media (max-width: 575px) {
  .kb-section {
    scroll-margin-top: calc(var(--header-h-mobile) + var(--space-4));
  }
}
</style>
