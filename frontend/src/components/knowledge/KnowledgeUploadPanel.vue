<template>
  <section class="up" aria-label="上传攻略">
    <!-- ---------------- 环境不可用 ----------------
         这里必须拦住:Milvus/embedding 不可用时,上传能解析成功,
         但入库一定会失败 —— 与其让用户走完流程再撞墙,不如一开始就说明白。 -->
    <div v-if="!available" class="up-blocked">
      <p class="up-blocked-title">知识库当前不可用，暂时不能上传</p>
      <p class="up-blocked-reason">{{ reason }}</p>
      <p class="up-blocked-hint">处理好之后点右上角的「刷新」，不用重新上传文件。</p>
    </div>

    <template v-else>
      <!-- ---------------- 入口 ---------------- -->
      <div v-if="!parsed" class="up-inputs">
        <div
          class="up-drop"
          :class="{ 'is-drag': dragging, 'is-busy': parsing }"
          role="button"
          tabindex="0"
          aria-label="选择或拖入攻略文件"
          @click="pickFile"
          @keydown.enter.prevent="pickFile"
          @keydown.space.prevent="pickFile"
          @dragover.prevent="dragging = true"
          @dragleave.prevent="dragging = false"
          @drop.prevent="onDrop"
        >
          <p class="up-drop-main">
            把攻略拖到这里，或 <span class="up-drop-link">选择文件</span>
          </p>
          <p class="up-drop-sub">Markdown · 纯文本 · PDF（需有文字层）· 图片（png / jpg / webp）· 单个不超过 5 MB</p>
          <p class="up-drop-sub">图片会由视觉模型识别成文字，识别结果里的数字请自行核对</p>
          <!-- 视觉上隐藏,但保持在无障碍树里,键盘用户用上面的 role=button 触发 -->
          <input
            ref="fileEl"
            class="up-file"
            type="file"
            accept=".md,.markdown,.txt,.text,.pdf,.png,.jpg,.jpeg,.webp,.bmp,.gif"
            @change="onFileChange"
          />
        </div>

        <div class="up-or" aria-hidden="true"><span>或</span></div>

        <div class="up-paste">
          <textarea
            v-model="pasteText"
            class="up-paste-area"
            rows="4"
            placeholder="也可以直接粘贴攻略文字，比如从小红书复制过来的那段"
            :disabled="parsing"
          ></textarea>
          <div class="up-paste-foot">
            <span class="up-paste-count">{{ pasteChars }} 字</span>
            <a-button
              type="primary"
              size="small"
              :loading="parsing"
              :disabled="!pasteText.trim()"
              @click="submitPaste"
            >
              解析这段文字
            </a-button>
          </div>
        </div>
      </div>

      <!-- ---------------- 预览 / 入库 ----------------
           解析与入库拆成两步:入库要真金白银调 embedding,而且一旦切坏了
           坏数据就在库里了。让用户先看到"切成几块、每块讲什么"再确认。 -->
      <div v-else class="up-review">
        <header class="up-review-head">
          <div class="up-review-title">
            <h3 class="up-review-name">{{ parsed.title }}</h3>
            <span class="up-review-origin">{{ originLabel }}</span>
          </div>
          <a-button size="small" :disabled="submitting || isRunning" @click="reset">
            重新选择
          </a-button>
        </header>

        <ul class="up-review-stats">
          <li><strong class="u-num">{{ parsed.char_count }}</strong> 字</li>
          <li>切成 <strong class="u-num">{{ parsed.chunk_count }}</strong> 块</li>
          <li>入库约需 <strong class="u-num">{{ parsed.embed_requests }}</strong> 次 embedding 请求</li>
          <li v-if="parsed.duplicate" class="up-review-dup">
            这份内容 {{ parsed.existing_status === 'ingested' ? '已经在库里了' : '之前传过' }}
          </li>
        </ul>

        <ul v-if="parsed.warnings.length" class="up-review-warn">
          <li v-for="(w, i) in parsed.warnings" :key="i">{{ w }}</li>
        </ul>

        <ol class="up-chunks">
          <li v-for="c in visibleChunks" :key="c.idx" class="up-chunk">
            <span class="up-chunk-head">{{ c.heading_path || '（开头）' }}</span>
            <span class="up-chunk-chars u-num">{{ c.chars }} 字</span>
            <p class="up-chunk-snippet">{{ c.snippet }}…</p>
          </li>
        </ol>
        <a-button
          v-if="parsed.preview.length > PREVIEW_COUNT"
          type="text"
          size="small"
          class="up-more"
          @click="showAllChunks = !showAllChunks"
        >
          {{ showAllChunks ? '收起' : `展开全部 ${parsed.chunk_count} 块` }}
        </a-button>

        <!-- 入库进度 -->
        <div v-if="task" class="up-progress">
          <div class="up-progress-head">
            <span>{{ task.state === 'done' ? '入库完成' : '正在写入知识库' }}</span>
            <span class="u-num">{{ progress }}%</span>
          </div>
          <div class="up-progress-track" role="progressbar" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100">
            <div class="up-progress-bar" :style="{ width: `${progress}%` }"></div>
          </div>
          <p v-if="task.message" class="up-progress-msg">{{ task.message }}</p>
        </div>

        <p v-if="error" class="up-error">{{ error }}</p>

        <div class="up-actions">
          <a-button
            v-if="!task || task.state === 'error'"
            type="primary"
            :loading="submitting || isRunning"
            @click="ingest"
          >
            确认入库
          </a-button>
          <a-button v-if="task?.state === 'done'" @click="reset">再传一份</a-button>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { message } from 'ant-design-vue';
import { parseKnowledgeDoc, ingestKnowledgeDoc } from '@/services/api';
import { useIngestTask } from '@/composables/useIngestTask';
import type { KnowledgeDocParse } from '@/types';

/**
 * 上传攻略面板 —— 知识库页的**主体**。
 *
 * 三步:解析 → 预览 → 确认入库。
 * 预览这一步不是多余的仪式感,它同时承担两件事:
 *   1. 花销透明(几次 embedding 请求)
 *   2. 质量把关(PDF 没文字层在这里就被拦下,而不是生成行程时才发现检索是空的)
 */

// 不赋值给变量:模板里直接用 available / reason,赋了反而触发 noUnusedLocals
defineProps<{
  /** 知识库(embedding + Milvus)是否可用。不可用时整个面板变成说明 */
  available?: boolean;
  /** 不可用的具体原因 */
  reason?: string;
}>();

const emit = defineEmits<{
  /** 入库成功,父组件据此刷新状态与列表 */
  ingested: [];
}>();

const PREVIEW_COUNT = 4;

const fileEl = ref<HTMLInputElement | null>(null);
const dragging = ref(false);
const parsing = ref(false);
const parsed = ref<KnowledgeDocParse | null>(null);
const pendingDocId = ref('');
const pasteText = ref('');
const parseError = ref('');
const showAllChunks = ref(false);

const pasteChars = computed(() => pasteText.value.trim().length);

const originLabel = computed(() => {
  const map: Record<string, string> = {
    md: 'Markdown',
    txt: '纯文本',
    pdf: 'PDF',
    image: '图片识别',
    paste: '粘贴',
  };
  return map[parsed.value?.origin ?? ''] ?? '';
});

const visibleChunks = computed(() => {
  const list = parsed.value?.preview ?? [];
  return showAllChunks.value ? list : list.slice(0, PREVIEW_COUNT);
});

const onIngested = () => {
  message.success(`《${parsed.value?.title ?? '攻略'}》已入库，生成行程时会参考它`);
  emit('ingested');
};

/**
 * 入库与进度轮询**复用同一套任务机制**(`useIngestTask`)。
 * 上传走的是单篇入库接口、灌库走的是全量接口,但任务结构与轮询方式一致 ——
 * 传一个自己的提交函数进来即可。这里只换提交函数,**不另写轮询**:
 * 另写一份的话,两边对"失败几次算断线"的判断迟早会不一致。
 */
const {
  task,
  submitting,
  progress,
  isRunning,
  error: ingestError,
  submit: submitIngestTask,
  reset: resetTask,
} = useIngestTask(onIngested, () => ingestKnowledgeDoc(pendingDocId.value));

const busy = computed(() => parsing.value || submitting.value || isRunning.value);
const error = computed(() => parseError.value || ingestError.value);

function pickFile() {
  if (busy.value) return;
  fileEl.value?.click();
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = ''; // 允许用户选同一个文件再来一次
  if (file) void parseFile(file);
}

function onDrop(event: DragEvent) {
  dragging.value = false;
  const file = event.dataTransfer?.files?.[0];
  if (file) void parseFile(file);
}

async function parseFile(file: File) {
  parsing.value = true;
  parseError.value = '';
  resetTask();
  try {
    parsed.value = await parseKnowledgeDoc({ file });
    pendingDocId.value = parsed.value.doc_id;
    if (parsed.value.duplicate) {
      message.info('这份内容之前已经传过，重复入库不会产生重复的检索结果');
    }
  } catch (e: unknown) {
    // 解析失败的话术是后端按"用户能照做"写的,原样展示
    parseError.value = (e as Error)?.message || '解析失败';
    parsed.value = null;
  } finally {
    parsing.value = false;
  }
}

async function submitPaste() {
  if (!pasteText.value.trim() || busy.value) return;
  parsing.value = true;
  parseError.value = '';
  resetTask();
  try {
    parsed.value = await parseKnowledgeDoc({ text: pasteText.value });
    pendingDocId.value = parsed.value.doc_id;
    pasteText.value = '';
  } catch (e: unknown) {
    parseError.value = (e as Error)?.message || '解析失败';
    parsed.value = null;
  } finally {
    parsing.value = false;
  }
}

/** 入库。第二个参数被自定义的 submitFn 忽略,传占位值只是为了满足签名 */
function ingest() {
  if (!pendingDocId.value) return;
  void submitIngestTask('all', false);
}

function reset() {
  parsed.value = null;
  pendingDocId.value = '';
  pasteText.value = '';
  parseError.value = '';
  showAllChunks.value = false;
  resetTask();
}
</script>

<style scoped>
.up {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

/* ---------------- 不可用 ---------------- */
.up-blocked {
  padding: var(--space-6);
  background: var(--bg-sunken);
  border: 1px dashed var(--line-2);
  border-radius: var(--radius-lg);
  text-align: center;
}

.up-blocked-title {
  margin: 0 0 var(--space-2);
  font-size: var(--fs-body);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.up-blocked-reason {
  margin: 0 0 var(--space-2);
  font-size: var(--fs-caption);
  color: var(--warning);
}

.up-blocked-hint {
  margin: 0;
  font-size: var(--fs-caption);
  color: var(--text-3);
}

/* ---------------- 拖拽区 ---------------- */
.up-drop {
  position: relative;
  padding: var(--space-7) var(--space-5);
  text-align: center;
  background: var(--bg-surface);
  border: 1px dashed var(--brand-300);
  border-radius: var(--radius-lg);
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out);
}

.up-drop:hover,
.up-drop:focus-visible,
.up-drop.is-drag {
  background: var(--brand-50);
  border-color: var(--brand-500);
}

.up-drop.is-busy {
  opacity: 0.6;
  pointer-events: none;
}

.up-drop-main {
  margin: 0 0 var(--space-2);
  font-size: var(--fs-body);
  font-weight: var(--fw-medium);
  color: var(--text-1);
}

.up-drop-link {
  color: var(--brand-600);
  text-decoration: underline;
}

.up-drop-sub {
  margin: var(--space-1) 0 0;
  font-size: var(--fs-micro);
  color: var(--text-3);
}

/* 隐藏原生 input 但保留可聚焦性 */
.up-file {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

.up-or {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  color: var(--text-3);
  font-size: var(--fs-micro);
}

.up-or::before,
.up-or::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--line-1);
}

/* ---------------- 粘贴 ---------------- */
.up-paste-area {
  width: 100%;
  padding: var(--space-3) var(--space-4);
  font-family: inherit;
  font-size: var(--fs-caption);
  line-height: var(--lh-body);
  color: var(--text-1);
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  border-radius: var(--radius-md);
  resize: vertical;
}

.up-paste-area:focus {
  outline: none;
  border-color: var(--brand-500);
}

.up-paste-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-top: var(--space-2);
}

.up-paste-count {
  font-size: var(--fs-micro);
  color: var(--text-3);
}

/* ---------------- 预览 ---------------- */
.up-review {
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  border-radius: var(--radius-lg);
}

.up-review-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.up-review-title {
  min-width: 0;
}

.up-review-name {
  margin: 0;
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.up-review-origin {
  font-size: var(--fs-micro);
  color: var(--text-3);
}

.up-review-stats {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  margin: 0 0 var(--space-3);
  padding: 0;
  list-style: none;
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.up-review-stats strong {
  color: var(--brand-600);
  font-weight: var(--fw-semibold);
}

.up-review-dup {
  color: var(--warning);
}

.up-review-warn {
  margin: 0 0 var(--space-3);
  padding: var(--space-2) var(--space-3);
  list-style: none;
  background: var(--brand-50);
  border-radius: var(--radius-sm);
  font-size: var(--fs-micro);
  color: var(--text-2);
}

.up-chunks {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.up-chunk {
  padding: var(--space-3);
  border: 1px solid var(--line-1);
  border-radius: var(--radius-sm);
}

.up-chunk-head {
  font-size: var(--fs-caption);
  font-weight: var(--fw-medium);
  color: var(--brand-ink);
}

.up-chunk-chars {
  margin-left: var(--space-2);
  font-size: var(--fs-micro);
  color: var(--text-3);
}

.up-chunk-snippet {
  margin: var(--space-1) 0 0;
  font-size: var(--fs-micro);
  line-height: var(--lh-body);
  color: var(--text-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.up-more {
  margin-top: var(--space-2);
}

/* ---------------- 进度 ---------------- */
.up-progress {
  margin-top: var(--space-4);
  padding-top: var(--space-4);
  border-top: 1px solid var(--line-1);
}

.up-progress-head {
  display: flex;
  justify-content: space-between;
  font-size: var(--fs-caption);
  color: var(--text-2);
  margin-bottom: var(--space-2);
}

.up-progress-track {
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--brand-50);
  overflow: hidden;
}

.up-progress-bar {
  height: 100%;
  border-radius: var(--radius-pill);
  background: var(--brand-500);
  transition: width var(--dur-base) var(--ease-out);
}

.up-progress-msg {
  margin: var(--space-2) 0 0;
  font-size: var(--fs-micro);
  color: var(--text-3);
}

.up-error {
  margin: var(--space-3) 0 0;
  font-size: var(--fs-caption);
  color: var(--warning);
}

.up-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

@media (max-width: 575px) {
  .up-review {
    padding: var(--space-4);
  }
}
</style>
