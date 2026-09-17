<template>
  <section class="maint">
    <a-collapse v-model:activeKey="activeKeys" :bordered="false">
      <a-collapse-panel key="maint">
        <template #header>
          <div class="maint-head">
            <span class="maint-title">维护</span>
            <span class="maint-desc">灌库与数据源 —— 改动攻略或库存后需要重新灌入才会生效</span>
          </div>
        </template>

        <!-- ---------------- 1. 数据源与预览 ----------------
             原来「数据源」是页面上独立的一块（占 60% 面积却很少用到），
             现在并入这里：它本来就是灌库前要确认的东西，
             而且预览接口返回的数字（按城市分布 / 每篇切几块）正是数据源的全部内容。 -->
        <div class="step">
          <div class="step-head">
            <span class="step-no">1</span>
            <span class="step-title">数据源与预览</span>
            <a-button
              size="small"
              :loading="previewLoading"
              class="step-action"
              @click="loadPreview"
            >
              预览
            </a-button>
          </div>

          <p v-if="previewError" class="err">{{ previewError }}</p>

          <p v-if="!preview && !previewLoading" class="hint">
            点「预览」查看会入库多少条。这一步只读本地文件，不调用 embedding，不花钱。
          </p>

          <div v-if="preview" class="preview">
            <div class="preview-cols">
              <!-- POI 事实层 -->
              <div class="preview-col">
                <p class="preview-head">
                  POI 事实层
                  <strong class="u-num">{{ preview.poi.total.toLocaleString('zh-CN') }}</strong> 条
                </p>
                <p v-if="preview.poi.path" class="preview-path u-num">
                  {{ preview.poi.path }}
                  <span v-if="preview.poi.size_kb"> · {{ preview.poi.size_kb }} KB</span>
                </p>
                <ul class="bars">
                  <li v-for="c in poiCities" :key="c.name" class="bar-row">
                    <span class="bar-name">{{ c.name }}</span>
                    <span class="bar-track">
                      <span class="bar-fill" :style="{ width: c.pct }"></span>
                    </span>
                    <span class="bar-num u-num">{{ c.value }}</span>
                  </li>
                </ul>
              </div>

              <!-- 城市攻略层 -->
              <div class="preview-col">
                <p class="preview-head">
                  城市攻略层
                  <strong class="u-num">{{ preview.guides.total }}</strong> 块
                </p>
                <p v-if="preview.guides.dir" class="preview-path u-num">{{ preview.guides.dir }}</p>
                <ul class="files">
                  <li v-for="f in guideFiles" :key="f.name" class="file">
                    <span class="file-main">
                      <span class="file-name">{{ f.name }}</span>
                      <span class="file-meta u-num">{{ f.sections }} 块 · {{ f.size_kb }} KB</span>
                    </span>
                    <a-button size="small" @click="emit('try-search', fileToQuery(f.name))">
                      试检索
                    </a-button>
                  </li>
                </ul>
              </div>
            </div>

            <p class="preview-note">{{ preview.note }}</p>
          </div>
        </div>

        <!-- ---------------- 2. 选择 ---------------- -->
        <div class="step">
          <div class="step-head">
            <span class="step-no">2</span>
            <span class="step-title">范围与方式</span>
          </div>

          <div class="field">
            <span class="field-label">灌哪一层</span>
            <a-radio-group v-model:value="source" button-style="solid">
              <a-radio-button value="all">全部</a-radio-button>
              <a-radio-button value="frozen">仅 POI 事实</a-radio-button>
              <a-radio-button value="guides">仅城市攻略</a-radio-button>
            </a-radio-group>
          </div>

          <div class="field field-danger">
            <div class="switch-row">
              <a-switch v-model:checked="recreate" />
              <span class="switch-label">重建 collection（危险）</span>
            </div>
            <p class="switch-desc">
              关闭 = upsert 幂等覆盖，重复执行安全，推荐；
              <strong>开启 = 先删除 collection 再重建，其中数据全部丢失。</strong>
            </p>
          </div>
        </div>

        <!-- ---------------- 3. 执行 ---------------- -->
        <div class="step">
          <div class="step-head">
            <span class="step-no">3</span>
            <span class="step-title">执行</span>
          </div>

          <div class="actions">
            <a-popconfirm
              v-if="!recreate"
              title="确定开始灌库吗？会调用 embedding 接口并产生费用。"
              ok-text="开始"
              cancel-text="取消"
              :disabled="isRunning"
              @confirm="doSubmit"
            >
              <a-button type="primary" :loading="submitting" :disabled="isRunning">
                开始灌库
              </a-button>
            </a-popconfirm>

            <a-button v-else type="primary" danger :disabled="isRunning" @click="confirmOpen = true">
              重建并灌库
            </a-button>

            <a-button v-if="task && !isRunning" @click="reset">清空结果</a-button>
          </div>

          <p v-if="submittingError" class="err">{{ submittingError }}</p>

          <!-- 进度：用**真实**的 processed/total。后端能给出准确进度，
               就不该像行程生成那样只能做不确定进度。 -->
          <div v-if="task" class="progress">
            <p class="progress-state">{{ stateText }}</p>

            <template v-if="isRunning">
              <a-progress :percent="progress" :status="'active'" :show-info="false" />
              <p class="progress-meta u-num">
                {{ task.processed }} / {{ task.total || '?' }}
                <template v-if="task.current"> · {{ task.current }}</template>
              </p>
              <p class="progress-warn">请勿关闭页面</p>
            </template>

            <template v-else-if="task.state === 'done'">
              <ul class="result-list">
                <li v-for="r in task.results" :key="r.namespace">
                  <span class="result-ns u-num">{{ r.namespace }}</span>
                  <span class="u-num">{{ r.count }} 条</span>
                </li>
              </ul>
              <p class="hint">上方的计数已自动刷新。</p>
            </template>

            <template v-else-if="task.state === 'error'">
              <p class="err">{{ task.error || '原因未记录' }}</p>
              <p class="hint">
                常见原因：Milvus 未启动、embedding key 未配置或额度不足。修好后可重新执行。
              </p>
            </template>
          </div>
        </div>
      </a-collapse-panel>
    </a-collapse>

    <!-- ---------------- 重建确认 ----------------
         对照 GitHub 删仓库的做法：要求手输确认文字。
         必须把「会删掉多少条」写出来 —— 泛泛说「数据会丢失」不足以让人停手。 -->
    <a-modal
      v-model:open="confirmOpen"
      title="确认重建 collection"
      :ok-button-props="{
        danger: true,
        disabled: confirmText !== CONFIRM_WORD,
        loading: submitting,
      }"
      ok-text="确认重建"
      cancel-text="取消"
      @ok="onRecreateConfirm"
    >
      <div class="cn-body">
        <p class="cn-danger">此操作会先删除 collection，其中数据全部丢失且无法恢复。</p>
        <ul class="cn-list">
          <li v-if="willDropPoi">
            <code>{{ poiCollection }}</code> —— 现有 {{ dropPoiCount }} 条 POI 将被删除
          </li>
          <li v-if="willDropGuides">
            <code>{{ guideCollection }}</code> —— 现有 {{ dropGuideCount }} 块攻略将被删除
          </li>
        </ul>
        <p>删除后需要重新灌库才能恢复。如果只是想更新内容，请改用「upsert 覆盖」（关闭重建开关）。</p>
        <p class="cn-prompt">请输入 <code>{{ CONFIRM_WORD }}</code> 以确认：</p>
        <a-input v-model:value="confirmText" :placeholder="CONFIRM_WORD" />
      </div>
    </a-modal>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { previewKnowledgeIngest } from '@/services/api';
import { useIngestTask } from '@/composables/useIngestTask';
import type { KnowledgeIngestPreview, KnowledgeIngestSource, KnowledgeStatus } from '@/types';

const props = defineProps<{
  /** 用于在确认弹窗里写明「会删掉多少条」 */
  status: KnowledgeStatus | null;
}>();

const emit = defineEmits<{
  finished: [];
  'try-search': [query: string];
}>();

const CONFIRM_WORD = 'DROP';

const activeKeys = ref<string[]>([]);
const source = ref<KnowledgeIngestSource>('all');
const recreate = ref(false);

const preview = ref<KnowledgeIngestPreview | null>(null);
const previewLoading = ref(false);
const previewError = ref('');

const confirmOpen = ref(false);
const confirmText = ref('');

const {
  task,
  submitting,
  error: submittingError,
  progress,
  isRunning,
  submit,
  reset,
} = useIngestTask(() => emit('finished'));

/* ---------------- 预览 ----------------
 * 面板一展开就自动拉一次（只读本地文件，不花钱），
 * 省掉「先点预览才知道有什么」这一步。 */
async function loadPreview() {
  previewLoading.value = true;
  previewError.value = '';
  try {
    preview.value = await previewKnowledgeIngest(source.value);
  } catch (e: unknown) {
    previewError.value = (e as Error)?.message || '预览失败';
    preview.value = null;
  } finally {
    previewLoading.value = false;
  }
}

watch(activeKeys, (keys) => {
  if (keys.includes('maint') && !preview.value) void loadPreview();
});

/** 切换范围时预览数字会变，重新拉一次 */
watch(source, () => {
  if (activeKeys.value.includes('maint')) void loadPreview();
});

const poiCities = computed(() => {
  const per = preview.value?.poi?.per_city || {};
  const entries = Object.entries(per).map(([name, value]) => ({ name, value }));
  const max = Math.max(1, ...entries.map((e) => e.value));
  return entries
    .sort((a, b) => b.value - a.value)
    .map((e) => ({ ...e, pct: `${((e.value / max) * 100).toFixed(1)}%` }));
});

const guideFiles = computed(() => preview.value?.guides?.files || []);

/** 从文件名推出可检索的词：`北京.md` → `北京` */
const fileToQuery = (name: string) => name.replace(/\.md$/i, '');

/* ---------------- 确认弹窗 ---------------- */

const poiCollection = computed(() => props.status?.collections?.poi_facts || 'poi_facts');
const guideCollection = computed(() => props.status?.collections?.city_guides || 'city_guides');

const willDropPoi = computed(() => source.value === 'frozen' || source.value === 'all');
const willDropGuides = computed(() => source.value === 'guides' || source.value === 'all');

const dropPoiCount = computed(() => props.status?.poi_facts?.count ?? 0);
const dropGuideCount = computed(() => props.status?.city_guides?.count ?? 0);

async function onRecreateConfirm() {
  if (confirmText.value !== CONFIRM_WORD) return;
  confirmOpen.value = false;
  confirmText.value = '';
  activeKeys.value = ['maint'];
  await submit(source.value, true);
}

async function doSubmit() {
  await submit(source.value, false);
}

const STATE_TEXT: Record<string, string> = {
  pending: '任务已排队…',
  running: '正在灌库…',
  done: '灌库完成',
  error: '灌库失败',
};

const stateText = computed(() => (task.value ? STATE_TEXT[task.value.state] || '处理中' : ''));
</script>

<style scoped>
.maint-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.maint-title {
  font-size: var(--fs-body);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.maint-desc {
  font-size: var(--fs-caption);
  color: var(--text-2);
  font-weight: var(--fw-regular);
}

/* ---------------- 步骤 ---------------- */
.step {
  padding: var(--space-4) 0;
  border-top: 1px solid var(--line-1);
}

.step:first-of-type {
  border-top: none;
  padding-top: 0;
}

.step-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

/* 序号：浅紫圆 + 主色数字。这套视觉里"标记"统一是浅底 + 主色。 */
.step-no {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--bg-tint);
  color: var(--brand-600);
  font-size: var(--fs-micro);
  font-weight: var(--fw-semibold);
  flex-shrink: 0;
}

.step-title {
  font-size: var(--fs-body);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.step-action {
  margin-left: auto;
}

.hint {
  font-size: var(--fs-caption);
  color: var(--text-2);
  line-height: var(--lh-body);
}

.err {
  font-size: var(--fs-caption);
  color: var(--error);
  margin-top: var(--space-2);
}

/* ---------------- 预览两栏 ---------------- */
.preview {
  background: var(--bg-sunken);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  margin-top: var(--space-3);
}

.preview-cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-5);
}

.preview-head {
  font-size: var(--fs-caption);
  color: var(--text-2);
  margin-bottom: var(--space-2);
}

.preview-head strong {
  font-size: var(--fs-h3);
  color: var(--text-1);
  margin: 0 2px;
}

.preview-path {
  font-size: var(--fs-micro);
  color: var(--text-3);
  word-break: break-all;
  margin-bottom: var(--space-3);
}

.preview-note {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--line-2);
  font-size: var(--fs-caption);
  color: var(--warning);
}

/* POI 分布条形 */
.bars {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.bar-row {
  display: grid;
  grid-template-columns: 44px 1fr 48px;
  align-items: center;
  gap: var(--space-2);
}

.bar-name {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.bar-track {
  height: 8px;
  border-radius: var(--radius-pill);
  background: #e4e7f2;
  overflow: hidden;
}

.bar-fill {
  display: block;
  height: 100%;
  border-radius: var(--radius-pill);
  background: var(--brand-400);
}

.bar-num {
  font-size: var(--fs-caption);
  color: var(--text-1);
  text-align: right;
}

/* 攻略文件 */
.files {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.file {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  background: var(--bg-surface);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-2) var(--space-2) var(--space-3);
}

.file-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  margin-right: auto;
}

.file-name {
  font-size: var(--fs-caption);
  font-weight: var(--fw-medium);
  color: var(--text-1);
}

.file-meta {
  font-size: var(--fs-micro);
  color: var(--text-3);
}

/* ---------------- 选择 ---------------- */
.field {
  margin-bottom: var(--space-4);
}

.field-label {
  display: block;
  font-size: var(--fs-micro);
  font-weight: var(--fw-semibold);
  color: var(--text-2);
  margin-bottom: var(--space-2);
}

.field-danger {
  background: var(--warning-tint);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  margin-bottom: 0;
}

.switch-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.switch-label {
  font-size: var(--fs-body);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.switch-desc {
  margin-top: var(--space-2);
  font-size: var(--fs-caption);
  color: var(--text-2);
  line-height: var(--lh-body);
}

.switch-desc strong {
  color: var(--error);
}

.actions {
  display: flex;
  gap: var(--space-3);
  flex-wrap: wrap;
}

/* ---------------- 进度 ---------------- */
.progress {
  margin-top: var(--space-4);
  background: var(--bg-sunken);
  border-radius: var(--radius-md);
  padding: var(--space-4);
}

.progress-state {
  font-size: var(--fs-body);
  font-weight: var(--fw-semibold);
  margin-bottom: var(--space-3);
}

.progress-meta {
  font-size: var(--fs-caption);
  color: var(--text-2);
  margin-top: var(--space-2);
}

.progress-warn {
  font-size: var(--fs-caption);
  color: var(--warning);
  margin-top: var(--space-1);
}

.result-list {
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: var(--fs-caption);
}

.result-list li {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-1) 0;
}

.result-ns {
  min-width: 90px;
  font-weight: var(--fw-semibold);
}

/* ---------------- 确认弹窗 ---------------- */
.cn-body p {
  margin-bottom: var(--space-3);
  font-size: var(--fs-caption);
  color: var(--text-2);
  line-height: var(--lh-body);
}

.cn-danger {
  color: var(--error) !important;
  font-weight: var(--fw-semibold);
}

.cn-list {
  margin: 0 0 var(--space-3);
  padding-left: 1.2em;
  font-size: var(--fs-caption);
  color: var(--text-1);
}

.cn-list li {
  margin-bottom: var(--space-1);
}

.cn-prompt {
  color: var(--text-1) !important;
}

code {
  font-family: var(--font-num);
  font-size: 0.95em;
  background: var(--bg-sunken);
  padding: 1px 5px;
  border-radius: var(--radius-xs);
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 767px) {
  .preview-cols {
    grid-template-columns: 1fr;
    gap: var(--space-4);
  }

  .actions :deep(.ant-btn) {
    flex: 1 1 auto;
  }
}
</style>
