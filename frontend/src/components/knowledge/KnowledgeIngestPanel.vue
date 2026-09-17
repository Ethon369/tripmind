<template>
  <section class="ing">
    <a-collapse v-model:activeKey="activeKeys" :bordered="false" class="ing-collapse">
      <a-collapse-panel key="ingest">
        <template #header>
          <div class="ing-header">
            <WarningOutlined class="ing-header-icon" aria-hidden="true" />
            <span class="ing-header-title">灌库 / 重建</span>
            <span class="ing-header-desc">改完攻略或库存后，需要重新灌入才能生效</span>
          </div>
        </template>

        <!-- ---------------- 步骤 1：预览 ---------------- -->
        <div class="ing-step">
          <div class="ing-step-head">
            <span class="ing-step-no">1</span>
            <span class="ing-step-title">预览将要入库的内容</span>
            <a-button
              size="small"
              :loading="previewLoading"
              class="ing-step-action"
              @click="loadPreview"
            >
              预览
            </a-button>
          </div>

          <p v-if="previewError" class="ing-err">{{ previewError }}</p>

          <div v-if="preview" class="ing-preview">
            <div class="ing-preview-row">
              <span>POI 事实层</span>
              <span class="u-num">{{ preview.poi.total }} 条</span>
            </div>
            <p v-if="previewCityText" class="ing-preview-sub">{{ previewCityText }}</p>

            <div class="ing-preview-row">
              <span>城市攻略层</span>
              <span class="u-num">{{ preview.guides.total }} 块</span>
            </div>
            <p v-if="previewFileText" class="ing-preview-sub">{{ previewFileText }}</p>

            <p class="ing-preview-note">{{ preview.note }}</p>
          </div>

          <p v-else-if="!previewLoading" class="ing-hint">
            先点「预览」看看会入库多少条 —— 这一步只读文件、不调用 embedding，不花钱。
          </p>
        </div>

        <!-- ---------------- 步骤 2：选择 ---------------- -->
        <div class="ing-step">
          <div class="ing-step-head">
            <span class="ing-step-no">2</span>
            <span class="ing-step-title">选择范围与方式</span>
          </div>

          <div class="ing-field">
            <label class="ing-label">灌哪一层</label>
            <a-radio-group v-model:value="source" button-style="solid">
              <a-radio-button value="all">全部</a-radio-button>
              <a-radio-button value="frozen">仅 POI 事实</a-radio-button>
              <a-radio-button value="guides">仅城市攻略</a-radio-button>
            </a-radio-group>
          </div>

          <div class="ing-field ing-field-danger">
            <div class="ing-switch-row">
              <a-switch v-model:checked="recreate" />
              <span class="ing-switch-label">重建 collection（危险）</span>
            </div>
            <p class="ing-switch-desc">
              关闭 = upsert 幂等覆盖，重复执行安全，推荐；
              <strong>开启 = 先删除 collection 再重建，其中数据全部丢失。</strong>
            </p>
          </div>
        </div>

        <!-- ---------------- 步骤 3：执行 ---------------- -->
        <div class="ing-step">
          <div class="ing-step-head">
            <span class="ing-step-no">3</span>
            <span class="ing-step-title">执行</span>
          </div>

          <div class="ing-actions">
            <!-- recreate 关：普通二次确认即可 -->
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

            <!-- recreate 开：走输入确认文字的弹窗 -->
            <a-button v-else type="primary" danger :disabled="isRunning" @click="confirmOpen = true">
              重建并灌库
            </a-button>

            <a-button
              v-if="task && !isRunning"
              @click="reset"
            >
              清空结果
            </a-button>
          </div>

          <p v-if="submittingError" class="ing-err">{{ submittingError }}</p>
        </div>

        <!-- ---------------- 步骤 4：进度 ---------------- -->
        <div v-if="task" class="ing-step ing-step-progress">
          <div class="ing-step-head">
            <span class="ing-step-no">4</span>
            <span class="ing-step-title">
              {{ stateText }}
            </span>
          </div>

          <!-- 用**真实**的 processed/total。
               后端能给出准确进度，就不该像行程生成那样只能做不确定进度。 -->
          <div v-if="isRunning" class="ing-progress">
            <a-progress :percent="progress" :status="'active'" />
            <div class="ing-progress-meta">
              <span class="u-num">
                {{ task.processed }} / {{ task.total || '?' }}
                <template v-if="task.current"> · 当前 {{ task.current }}</template>
              </span>
              <span class="ing-progress-warn">请勿关闭页面</span>
            </div>
          </div>

          <div v-else-if="task.state === 'done'" class="ing-result">
            <a-alert type="success" show-icon :message="task.message || '灌库完成'" />
            <ul class="ing-result-list">
              <li v-for="r in task.results" :key="r.namespace">
                <span class="ing-result-ns">{{ r.namespace }}</span>
                <span class="u-num">{{ r.count }} 条</span>
                <span class="ing-result-coll u-num">{{ r.collection }}</span>
              </li>
            </ul>
            <p class="ing-hint">上方的计数已自动刷新。</p>
          </div>

          <div v-else-if="task.state === 'error'" class="ing-result">
            <a-alert
              type="error"
              show-icon
              message="灌库失败"
              :description="task.error || '原因未记录'"
            />
            <p class="ing-hint">
              常见原因：Milvus 未启动、embedding key 未配置或额度不足。修好后可以重新执行。
            </p>
          </div>
        </div>
      </a-collapse-panel>
    </a-collapse>

    <!-- ---------------- 重建确认弹窗 ----------------
         对照 GitHub 删仓库的做法：要求手输确认文字。
         这里必须把「会删掉多少条」写出来 —— 泛泛说「数据会丢失」不足以让人停手。 -->
    <a-modal
      v-model:open="confirmOpen"
      title="确认重建 collection"
      :ok-button-props="{ danger: true, disabled: confirmText !== CONFIRM_WORD, loading: submitting }"
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
        <p>删除后需要重新灌库才能恢复。如果只是想更新内容，请改用「upsert 覆盖」（关闭上面的重建开关）。</p>
        <p class="cn-prompt">
          请输入 <code>{{ CONFIRM_WORD }}</code> 以确认：
        </p>
        <a-input v-model:value="confirmText" :placeholder="CONFIRM_WORD" />
      </div>
    </a-modal>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { WarningOutlined } from '@ant-design/icons-vue';
import { previewKnowledgeIngest } from '@/services/api';
import { useIngestTask } from '@/composables/useIngestTask';
import type {
  KnowledgeIngestPreview,
  KnowledgeIngestSource,
  KnowledgeStatus,
} from '@/types';

const props = defineProps<{
  /** 用于在确认弹窗里写明「会删掉多少条」 */
  status: KnowledgeStatus | null;
}>();

const emit = defineEmits<{ finished: [] }>();

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

/* ---------------- 预览 ---------------- */

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

const previewCityText = computed(() => {
  const per = preview.value?.poi?.per_city;
  if (!per) return '';
  return Object.entries(per)
    .map(([c, n]) => `${c} ${n}`)
    .join(' / ');
});

const previewFileText = computed(() => {
  const per = preview.value?.guides?.per_file;
  if (!per) return '';
  return Object.entries(per)
    .map(([f, n]) => `${f} ${n} 块`)
    .join(' / ');
});

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
  await submit(source.value, true);
  activeKeys.value = ['ingest'];
}

async function doSubmit() {
  await submit(source.value, false);
}

/* ---------------- 状态文案 ---------------- */

const STATE_TEXT: Record<string, string> = {
  pending: '任务已排队…',
  running: '正在灌库…',
  done: '灌库完成',
  error: '灌库失败',
};

const stateText = computed(() => (task.value ? STATE_TEXT[task.value.state] || '处理中' : ''));
</script>

<style scoped>
.ing-collapse {
  background: var(--bg-card);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-md);
}

.ing-collapse :deep(.ant-collapse-header) {
  padding: var(--sp-4) var(--sp-5);
}

.ing-collapse :deep(.ant-collapse-content-box) {
  padding: 0 var(--sp-5) var(--sp-5);
}

.ing-header {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.ing-header-icon {
  color: var(--warning);
  font-size: 16px;
}

.ing-header-title {
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.ing-header-desc {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

/* ---------------- 步骤 ---------------- */
.ing-step {
  padding: var(--sp-4) 0;
  border-top: 1px solid var(--border-2);
}

.ing-step:first-of-type {
  border-top: none;
}

.ing-step-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin-bottom: var(--sp-3);
}

.ing-step-no {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--brand-50);
  color: var(--brand-600);
  font-size: var(--fs-caption);
  font-weight: var(--fw-semibold);
  flex-shrink: 0;
}

.ing-step-title {
  font-size: var(--fs-body);
  font-weight: var(--fw-medium);
  color: var(--text-1);
}

.ing-step-action {
  margin-left: auto;
}

.ing-hint {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.ing-err {
  font-size: var(--fs-caption);
  color: var(--error);
  margin-top: var(--sp-2);
}

/* ---------------- 预览 ---------------- */
.ing-preview {
  background: var(--bg-sunken);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-sm);
  padding: var(--sp-4);
}

.ing-preview-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-3);
  font-size: var(--fs-sm);
  color: var(--text-1);
}

.ing-preview-sub {
  margin: var(--sp-1) 0 var(--sp-3);
  font-size: var(--fs-caption);
  color: var(--text-2);
  word-break: break-word;
}

.ing-preview-note {
  margin-top: var(--sp-2);
  padding-top: var(--sp-3);
  border-top: 1px dashed var(--border-1);
  font-size: var(--fs-caption);
  color: var(--warning);
}

/* ---------------- 表单 ---------------- */
.ing-field {
  margin-bottom: var(--sp-4);
}

.ing-label {
  display: block;
  margin-bottom: var(--sp-2);
  font-size: var(--fs-sm);
  color: var(--text-2);
}

.ing-field-danger {
  padding: var(--sp-3);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-sm);
  background: var(--bg-sunken);
}

.ing-switch-row {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
}

.ing-switch-label {
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);
  color: var(--text-1);
}

.ing-switch-desc {
  margin-top: var(--sp-2);
  font-size: var(--fs-caption);
  color: var(--text-2);
  line-height: var(--lh-body);
}

.ing-switch-desc strong {
  color: var(--error);
}

.ing-actions {
  display: flex;
  gap: var(--sp-3);
  flex-wrap: wrap;
}

/* ---------------- 进度 ---------------- */
.ing-step-progress {
  background: var(--bg-sunken);
  border-radius: var(--radius-sm);
  padding: var(--sp-4);
  border-top: none;
}

.ing-progress-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  flex-wrap: wrap;
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.ing-progress-warn {
  color: var(--warning);
}

.ing-result-list {
  list-style: none;
  margin: var(--sp-3) 0 0;
  padding: 0;
  font-size: var(--fs-sm);
}

.ing-result-list li {
  display: flex;
  align-items: baseline;
  gap: var(--sp-3);
  flex-wrap: wrap;
  padding: var(--sp-1) 0;
}

.ing-result-ns {
  font-weight: var(--fw-medium);
  color: var(--text-1);
  min-width: 90px;
}

.ing-result-coll {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

/* ---------------- 确认弹窗 ---------------- */
.cn-body p {
  margin-bottom: var(--sp-3);
  font-size: var(--fs-sm);
  color: var(--text-2);
  line-height: var(--lh-body);
}

.cn-danger {
  color: var(--error);
  font-weight: var(--fw-medium);
}

.cn-list {
  margin: 0 0 var(--sp-3);
  padding-left: 1.2em;
  font-size: var(--fs-sm);
  color: var(--text-1);
}

.cn-list li {
  margin-bottom: var(--sp-1);
}

.cn-prompt {
  color: var(--text-1);
}

code {
  font-family: var(--font-num);
  font-size: 0.95em;
  background: var(--bg-sunken);
  padding: 1px 5px;
  border-radius: 4px;
  border: 1px solid var(--border-2);
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 767px) {
  .ing-collapse :deep(.ant-collapse-header) {
    padding: var(--sp-3) var(--sp-4);
  }

  .ing-collapse :deep(.ant-collapse-content-box) {
    padding: 0 var(--sp-4) var(--sp-4);
  }

  .ing-actions :deep(.ant-btn) {
    flex: 1 1 auto;
  }
}
</style>
