<template>
  <div class="create">
    <!-- ---------------- 顶部 ---------------- -->
    <header class="create-head">
      <button type="button" class="back" @click="cancel">
        <ArrowLeftOutlined aria-hidden="true" />
        取消创建
      </button>
    </header>

    <div class="create-body">
      <!-- ---------------- 用户那句话 ----------------
           把用户在首页写的内容原样回显成一条消息气泡。
           作用是让用户确认"我刚才说的东西它收到了"，而不是到了新页面从零开始。 -->
      <div v-if="draftMessage" class="bubble-row">
        <p class="bubble">{{ draftMessage }}</p>
      </div>

      <!-- ---------------- AI 引导卡片 ----------------
           生成期间整张表单被 GeneratingPanel 顶替：此时改任何字段都不会生效
           （请求已经发出去了），留着它只会让人反复确认自己填对没有。
           顶部「取消创建」保持可用，那是反悔的出口。

           生成中的面板用「不确定进度条 + 已用计时 + 阶段轮播」，刻意不显示
           百分比 —— 后端是一次性响应，前端拿不到任何中间信号；编一个百分比
           会在真卡住时停在 90% 一动不动，反而让用户以为页面死了去刷新。 -->
      <section v-if="!loading" class="panel">
        <p class="lead">{{ leadText }}</p>

        <!-- 从用户那句话里读出来的信息。
             显示成标签而不是"悄悄填进表单"，是为了让用户能确认
             「它确实读懂了我说的话」—— 而不是自己发现字段被改了却不知道谁改的。 -->
        <div v-if="parsedCity || parsedDays" class="parsed">
          <span class="parsed-label">已从你的描述里读出</span>
          <span v-if="parsedCity" class="parsed-tag">{{ parsedCity }}</span>
          <span v-if="parsedDays" class="parsed-tag">{{ parsedDays }} 天</span>
          <span class="parsed-note">不对的话直接改下面</span>
        </div>

        <!-- 目的地。
             这里刻意**不放热门城市快捷选择** —— 首页已经有「热门目的地」卡片区了，
             到创建页再列一遍是重复；而且绝大多数情况下目的地已经
             从用户那句话里解析出来并填好了（见上面的标签）。 -->
        <div class="field">
          <span class="field-label">目的地</span>
          <div class="field-body">
            <a-input
              v-model:value="formData.city"
              class="city-input"
              placeholder="例如：重庆"
              size="large"
              :maxlength="LIMITS.MAX_CITY_LEN"
              allow-clear
            />
          </div>
        </div>

        <!-- 日期：出发与返程都能直接选。
             出发用 :value + @change（为了保持行程长度平移），返程用 v-model。 -->
        <div class="field">
          <span class="field-label">日期</span>
          <div class="field-body field-row">
            <a-date-picker
              :value="formData.start_date"
              size="large"
              placeholder="出发"
              :disabled-date="disabledStart"
              @change="onStartChange"
            />
            <span class="date-arrow" aria-hidden="true">→</span>
            <a-date-picker
              v-model:value="formData.end_date"
              size="large"
              placeholder="返程"
              :disabled-date="disabledEnd"
            />
            <span class="date-total">
              共 <strong class="u-num">{{ travelDays }}</strong> 天
            </span>
          </div>
        </div>

        <!-- 旅行偏好（多选） -->
        <div class="field">
          <span class="field-label">偏好</span>
          <div class="field-body">
            <div class="chips">
              <button
                v-for="o in PREFERENCE_OPTIONS"
                :key="o.value"
                type="button"
                class="chip"
                :class="{ 'is-active': formData.preferences.includes(o.value) }"
                :disabled="isPreferenceDisabled(o.value)"
                @click="togglePreference(o.value)"
              >
                {{ o.label }}
              </button>
            </div>
            <p class="field-hint">
              最多选 {{ LIMITS.MAX_PREFERENCES }} 个，已选 {{ formData.preferences.length }} 个
            </p>
          </div>
        </div>

        <!-- 交通方式（单选） -->
        <div class="field">
          <span class="field-label">交通</span>
          <div class="field-body">
            <div class="chips">
              <button
                v-for="o in TRANSPORTATION_OPTIONS"
                :key="o.value"
                type="button"
                class="chip"
                :class="{ 'is-active': formData.transportation === o.value }"
                @click="formData.transportation = o.value"
              >
                {{ o.label }}
              </button>
            </div>
          </div>
        </div>

        <!-- 住宿偏好（单选） -->
        <div class="field">
          <span class="field-label">住宿</span>
          <div class="field-body">
            <div class="chips">
              <button
                v-for="o in ACCOMMODATION_OPTIONS"
                :key="o.value"
                type="button"
                class="chip"
                :class="{ 'is-active': formData.accommodation === o.value }"
                @click="formData.accommodation = o.value"
              >
                {{ o.label }}
              </button>
            </div>
          </div>
        </div>

        <!-- 补充说明 -->
        <div class="field">
          <span class="field-label">补充</span>
          <div class="field-body">
            <a-textarea
              v-model:value="formData.free_text_input"
              placeholder="还有什么要交代的？例如：想去看升旗、需要无障碍设施、对海鲜过敏"
              :rows="3"
              size="large"
              :maxlength="LIMITS.MAX_FREE_TEXT_LEN"
              show-count
            />
          </div>
        </div>

        <!-- ---------------- 提交 ---------------- -->
        <a-alert
          v-if="submitError"
          class="submit-error"
          type="error"
          show-icon
          :message="submitError"
          description="后端服务可能未启动或出错，请检查后重试。"
        />

        <a-button
          type="primary"
          size="large"
          block
          :loading="loading"
          class="submit-btn"
          @click="submit"
        >
          <template v-if="!loading">生成我的行程</template>
          <template v-else>正在生成…</template>
        </a-button>

          <p class="foot-note">
            生成一次约需 30–60 秒，期间请不要关闭页面。想改主意直接
            <a @click="cancel">返回首页</a>。
          </p>
        </section>
        <GeneratingPanel v-else :city="formData.city" :days="travelDays" />
      </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import dayjs, { type Dayjs } from 'dayjs';
import { message } from 'ant-design-vue';
import { ArrowLeftOutlined } from '@ant-design/icons-vue';
import { generateTripPlan, parseIntentWithLLM } from '@/services/api';
import GeneratingPanel from '@/components/trip/GeneratingPanel.vue';
import { usePlanCache } from '@/composables/usePlanCache';
import { useTripDraft } from '@/composables/useTripDraft';
import { parseTripIntent } from '@/utils/parseTripIntent';
import {
  TRANSPORTATION_OPTIONS,
  ACCOMMODATION_OPTIONS,
  PREFERENCE_OPTIONS,
  FORM_DEFAULTS,
  LIMITS,
} from '@/constants/tripOptions';
import type { TripFormData } from '@/types';

/**
 * 创建行程页。
 *
 * 从首页的「详细设置」搬过来，并按参考站改成了**对话式引导 + 点选式表单**：
 * 能用点的不让打字，能自动算的不让用户自己填。
 *
 * 三处关键交互：
 *   1. **天数点选** → 自动推算返程日期。用户只需要给一个出发日 + 一个天数，
 *      不用去算"到几号回"。这也顺手消掉了原来那个"结束日期早于开始日期"的报错路径。
 *   2. **偏好/交通/住宿全部用标签点选**，不用下拉框 —— 少一次点击，且选项一眼看全。
 *   3. **出发日期默认明天**。不给默认值的话用户会被"请选择日期"拦一次，
 *      而 90% 的人不会当天出发。
 *
 * 首页传来的描述会原样回显成一条消息气泡，让用户确认"刚才说的它收到了"。
 */

const router = useRouter();
const planCache = usePlanCache();
const tripDraft = useTripDraft();

const loading = ref(false);
const submitError = ref('');
/** 首页传来的那句话，只用于回显（可编辑的是下面表单里的「补充」） */
const draftMessage = ref('');

/* 这里原来有 HOT_CITIES（热门城市快捷选择）和 DAY_OPTIONS（天数按钮组），均已移除：
 *   - 城市快捷选择与首页的「热门目的地」卡片区重复，
 *     而且绝大多数情况下目的地已经从用户那句话里解析出来并填好了，再列一遍是冗余；
 *   - 天数按钮组占了三行（1–21 一行 + 30 一行 + 说明文字），
 *     而日期行末尾的「共 N 天」已经表达了同一个信息 —— 改返程日期就是改天数，更直接。
 * 天数依然由起止日期 computed 得出（见 travelDays），逻辑没有变化。 */

/**
 * 表单状态里**没有 travel_days**。
 *
 * 天数不是一个独立字段，而是由起止日期推出来的（见下面的 travelDays）。
 * 否则"点天数写日期"和"改日期算天数"会变成**两个真相来源**：两边互相覆写，
 * 出现"我改了返程日期、天数却还是旧的"这类问题。
 */
type TripFormState = Omit<TripFormData, 'start_date' | 'end_date' | 'travel_days'> & {
  start_date: Dayjs | null;
  end_date: Dayjs | null;
};

/** 默认明天出发 —— 见文件头第 3 点 */
const DEFAULT_START = dayjs().add(1, 'day').startOf('day');
/** 默认 3 天：短途最常见，且能装下"一座城 + 一个近郊"的经典结构 */
const DEFAULT_DAYS = 3;

const formData = reactive<TripFormState>({
  city: '',
  start_date: DEFAULT_START,
  // 返程日期在初始化时就用局部常量算好。
  // 不能写成 `formData.start_date.add(...)` —— 它在类型上是 `Dayjs | null`，
  // 而且同一个对象字面量里引用自己也不是好写法。
  end_date: DEFAULT_START.add(DEFAULT_DAYS - 1, 'day'),
  transportation: FORM_DEFAULTS.transportation,
  accommodation: FORM_DEFAULTS.accommodation,
  preferences: [...FORM_DEFAULTS.preferences],
  free_text_input: '',
});

/**
 * 天数 = 返程 − 出发 + 1。**唯一真相是那两个日期。**
 *
 * 三条操作路径都收敛到它，不存在互相覆写：
 *   点天数按钮 → 写 end_date
 *   改返程日期 → 写 end_date
 *   改出发日期 → 保持天数、平移 end_date
 */
const travelDays = computed(() => {
  const { start_date: s, end_date: e } = formData;
  if (!s || !e) return 1;
  const d = e.diff(s, 'day') + 1;
  return Math.min(Math.max(d, 1), LIMITS.MAX_DAYS);
});

const today = () => dayjs().startOf('day');

/** 记录「哪几项是从用户那句话里解析出来的」—— 只有解析来的才显示成标签 */
const parsedCity = ref('');
const parsedDays = ref(0);
/** 后端 LLM 生成的引导语。比前端模板自然得多，拿到就用它 */
const aiGreeting = ref('');

/* ---------------- 从首页带来的草稿 ---------------- */
onMounted(() => {
  const draft = tripDraft.load();
  if (!draft) return;

  if (draft.city) formData.city = draft.city;
  if (!draft.free_text_input) return;

  formData.free_text_input = draft.free_text_input;
  draftMessage.value = draft.free_text_input;

  /* 解析分**两道**，这是有意的：
   *
   * 1. 规则解析（本地、同步、0 延迟）—— 立刻把表单填好，用户不用等
   * 2. LLM 解析（网络、异步）—— 回来后补上规则没读出来的字段
   *
   * 为什么不只用 LLM：它要 1–3 秒，用户会先看到一张空表再被填充。
   * 为什么不只用规则：它认不出「5d」「住民宿」「坐地铁」这类表达。
   * 两者叠加的效果是「秒填 + 随后变准」。 */
  const parsed = parseTripIntent(draft.free_text_input);

  if (!formData.city && parsed.city) {
    formData.city = parsed.city;
    parsedCity.value = parsed.city;
  }
  if (parsed.days) {
    setDays(parsed.days);
    parsedDays.value = parsed.days;
  }

  void refineWithLLM(draft.free_text_input);
});

/**
 * 用后端 LLM 把解析做得更准。
 *
 * **只补规则没读出来的字段**，不覆盖已有的值 —— 规则的结果此刻已经在界面上
 * 显示了一两秒，直接改掉会让用户觉得"我刚看到的怎么又变了"；
 * 而且用户可能已经开始手输了。
 *
 * 偏好 / 交通 / 住宿这三项规则压根不解析，所以只要模型给了就直接用。
 */
async function refineWithLLM(text: string) {
  const res = await parseIntentWithLLM(text);
  // 模型不可用（没配 key / 超时 / 返回格式异常）—— 静默保持规则结果，不打扰用户
  if (!res?.success || !res.data) return;

  const d = res.data;

  if (!formData.city && d.city) {
    formData.city = d.city;
    parsedCity.value = d.city;
  }
  if (!parsedDays.value && d.days) {
    setDays(d.days);
    parsedDays.value = d.days;
  }
  if (!formData.preferences.length && d.preferences?.length) {
    formData.preferences = d.preferences.slice(0, LIMITS.MAX_PREFERENCES);
  }
  if (d.transportation) formData.transportation = d.transportation;
  if (d.accommodation) formData.accommodation = d.accommodation;
  if (d.greeting) aiGreeting.value = d.greeting;
}

/* ---------------- 引导文案 ---------------- */
const leadText = computed(() => {
  // 模型生成的最自然，优先用
  if (aiGreeting.value) return aiGreeting.value;

  const city = formData.city;

  // 规则读出来了 —— 先复述一遍，让他确认"它听懂了"
  if (city && parsedDays.value) {
    return `${city} ${travelDays.value} 天，记下了。再确认几个细节，我就开始排。`;
  }
  if (city) return `${city}，好地方。先确认下面几项，我好把行程排得合你心意。`;
  return '先告诉我目的地和出行时间，我来把每天的路线排顺。';
});

/* ---------------- 日期 ↔ 天数 ---------------- */

/** 出发日期不能早于今天 */
const disabledStart = (d: Dayjs) => d.isBefore(today(), 'day');

/** 返程：不早于出发，且整段不超过 30 天（与后端 travel_days: le=30 对齐） */
const disabledEnd = (d: Dayjs) => {
  const start = formData.start_date ?? today();
  return d.isBefore(start, 'day') || d.isAfter(start.add(LIMITS.MAX_DAYS - 1, 'day'), 'day');
};

/** 点天数 → 写返程日期；天数由 travelDays 自动得出 */
function setDays(n: number) {
  if (!formData.start_date) {
    message.info('先选出发日期');
    return;
  }
  formData.end_date = formData.start_date.add(n - 1, 'day');
}

/**
 * 改出发日期时**保持行程长度不变**（返程整体平移）。
 *
 * 这里刻意用 `:value` + `@change` 而不是 `v-model`：
 * v-model 会先把 start_date 改掉，那时再想"按原天数平移"就拿不到原天数了
 * —— travelDays 已经按「新 start + 旧 end」算过一轮，值已经不队。
 * 手动赋值可以先读旧天数，再一次性写好两个日期。
 */
function onStartChange(value: Dayjs | null) {
  if (!value) {
    // 一起清掉：否则会出现"没有出发日期、却还显示着返程日期"的怪状态
    formData.start_date = null;
    formData.end_date = null;
    return;
  }

  // 此刻 formData.start_date 还是旧值，所以能拿到"用户原本想要的行程长度"
  const prevDays =
    formData.start_date && formData.end_date
      ? formData.end_date.diff(formData.start_date, 'day') + 1
      : DEFAULT_DAYS;
  const days = Math.min(Math.max(prevDays, 1), LIMITS.MAX_DAYS);

  formData.start_date = value;
  formData.end_date = value.add(days - 1, 'day');
}

/**
 * 兜底：返程早于出发（disabledEnd 已挡住大部分）或整段超过 30 天时收一下。
 * 后者是用户**直接改返程日期**时能碰到的 —— 选一个离出发很远的日期。
 */
watch(
  () => formData.end_date,
  (end) => {
    const start = formData.start_date;
    if (!end || !start) return;

    if (end.isBefore(start, 'day')) {
      formData.end_date = start;
      return;
    }

    if (end.diff(start, 'day') + 1 > LIMITS.MAX_DAYS) {
      formData.end_date = start.add(LIMITS.MAX_DAYS - 1, 'day');
      message.warning(`行程最长 ${LIMITS.MAX_DAYS} 天，返程已调整`);
    }
  }
);

/* ---------------- 偏好多选 ---------------- */
const isPreferenceDisabled = (value: string) =>
  formData.preferences.length >= LIMITS.MAX_PREFERENCES &&
  !formData.preferences.includes(value);

function togglePreference(value: string) {
  const list = formData.preferences;
  const i = list.indexOf(value);
  if (i >= 0) {
    list.splice(i, 1);
    return;
  }
  if (list.length >= LIMITS.MAX_PREFERENCES) {
    message.warning(`最多选 ${LIMITS.MAX_PREFERENCES} 个，先取消一个再选`);
    return;
  }
  list.push(value);
}

/* ---------------- 提交 ---------------- */

function describeError(e: unknown): string {
  const err = e as { response?: { status?: number; data?: { detail?: string } }; message?: string };
  const status = err?.response?.status;
  if (status === 422) return '提交的参数不合法，请检查目的地与日期。';
  if (status && status >= 500) return err?.response?.data?.detail || '后端服务出错了。';
  if (status === 404) return '接口不存在，请确认后端版本与前端匹配。';
  return err?.message || '生成旅行计划失败，请稍后重试。';
}

async function submit() {
  submitError.value = '';

  if (!formData.city.trim()) {
    message.warning('先填一下目的地');
    return;
  }
  if (!formData.start_date || !formData.end_date) {
    message.warning('先选出发日期');
    return;
  }

  loading.value = true;

  const payload: TripFormData = {
    city: formData.city.trim(),
    start_date: formData.start_date.format('YYYY-MM-DD'),
    end_date: formData.end_date.format('YYYY-MM-DD'),
    travel_days: travelDays.value,
    transportation: formData.transportation,
    accommodation: formData.accommodation,
    preferences: formData.preferences,
    free_text_input: formData.free_text_input,
  };

  try {
    const response = await generateTripPlan(payload);

    if (response.success && response.data) {
      planCache.save(response.data);
      // 草稿已经用掉了，清掉 —— 否则下次进创建页会带着上次的描述
      tripDraft.clear();
      const planId = response.plan_id;
      router.push(planId ? `/result/${planId}` : '/result');
      return;
    }

    // success=false：后端把降级做成了「有记录但内容为空」，不是异常
    message.error(response.message || '未能生成行程内容');
  } catch (e) {
    submitError.value = describeError(e);
  } finally {
    loading.value = false;
  }
}

function cancel() {
  router.push('/');
}
</script>

<style scoped>
.create {
  min-height: 100%;
  background: var(--bg-page);
  padding-bottom: var(--space-9);
}

/* ---------------- 顶部 ---------------- */
.create-head {
  max-width: 640px;
  margin: 0 auto;
  padding: var(--space-5) var(--space-4) var(--space-2);
}

.back {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 36px;
  padding: 0;
  border: none;
  background: transparent;
  font-family: inherit;
  font-size: var(--fs-caption);
  color: var(--text-2);
  cursor: pointer;
  transition: color var(--dur-fast) var(--ease-out);
}

.back:hover {
  color: var(--brand-600);
}

.create-body {
  max-width: 640px;
  margin: 0 auto;
  padding: 0 var(--space-4);
}

/* ---------------- 用户消息气泡 ----------------
 * 右对齐、浅紫底、右下角收一个尖角 —— 一眼能看出"这是我说的"。
 */
.bubble-row {
  display: flex;
  justify-content: flex-end;
  margin-bottom: var(--space-4);
  animation: fadeInUp var(--dur-slow) var(--ease-out) both;
}

.bubble {
  max-width: 80%;
  padding: var(--space-3) var(--space-4);
  background: var(--brand-gradient);
  color: #fff;
  border-radius: var(--radius-lg) var(--radius-lg) var(--radius-xs) var(--radius-lg);
  font-size: var(--fs-body);
  line-height: 1.6;
  word-break: break-word;
  box-shadow: var(--shadow-brand);
}

/* ---------------- AI 引导卡片 ---------------- */
.panel {
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-2);
  padding: var(--space-6);
  animation: fadeInUp var(--dur-slow) var(--ease-out) 60ms both;
}

.lead {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--text-1);
  margin-bottom: var(--space-4);
}

/* ---------------- 解析结果标签 ----------------
 * 从用户那句话里读出来的信息。用**浅青底药丸** ——
 * 与表单里"已选中"的样式同源，暗示「这些已经替你填好了」。
 */
.parsed {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-6);
  background: var(--bg-tint);
  border-radius: var(--radius-md);
}

.parsed-label {
  font-size: var(--fs-micro);
  color: var(--brand-ink);
}

.parsed-tag {
  padding: 2px var(--space-3);
  border-radius: var(--radius-pill);
  background: var(--bg-surface);
  color: var(--brand-600);
  font-size: var(--fs-caption);
  font-weight: var(--fw-medium);
  box-shadow: var(--shadow-1);
}

.parsed-note {
  font-size: var(--fs-micro);
  color: var(--text-2);
  margin-left: auto;
}

/* ---------------- 字段 ----------------
 * 左侧是字段名（固定宽），右侧是点选项。
 * 这个"左标签 + 右内容"的结构来自参考站，比竖排的表单更紧凑，
 * 一屏能看到更多选项。
 */
.field {
  display: grid;
  grid-template-columns: 52px 1fr;
  gap: var(--space-3);
  align-items: start;
  margin-bottom: var(--space-5);
}

.field-label {
  font-size: var(--fs-caption);
  color: var(--text-2);
  line-height: 32px;
}

.field-body {
  min-width: 0;
}

.field-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.field-hint {
  margin-top: var(--space-2);
  font-size: var(--fs-micro);
  color: var(--text-3);
}

.city-input {
  max-width: 280px;
}

/* 两个日期选择器并排：固定宽度，保证「出发 → 返程 共 N 天」能在一行里放下 */
.field-row :deep(.ant-picker) {
  width: 152px;
}

.date-arrow {
  color: var(--text-3);
}

.date-total {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.date-total strong {
  color: var(--brand-600);
  font-weight: var(--fw-semibold);
  margin: 0 2px;
}

/* ---------------- 标签组 ---------------- */
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 32px;
  padding: 0 var(--space-4);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  background: var(--bg-surface);
  font-family: inherit;
  font-size: var(--fs-caption);
  color: var(--text-2);
  cursor: pointer;
  transition: border-color var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out);
}

.chip:hover:not(:disabled) {
  border-color: var(--brand-400);
  color: var(--brand-600);
}

/* 选中态：浅紫底 + 主色文字 + 主色描边。这套视觉里"选中"的统一表达 */
.chip.is-active {
  border-color: var(--brand-500);
  background: var(--bg-tint);
  color: var(--brand-600);
  font-weight: var(--fw-medium);
}

.chip:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ---------------- 提交 ---------------- */
.submit-error {
  margin-bottom: var(--space-4);
}

.submit-btn {
  height: 48px;
  font-size: var(--fs-body);
  font-weight: var(--fw-semibold);
}

.foot-note {
  margin-top: var(--space-4);
  text-align: center;
  font-size: var(--fs-micro);
  color: var(--text-3);
  line-height: 1.7;
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 575px) {
  .create-body,
  .create-head {
    padding-inline: var(--space-3);
  }

  .panel {
    padding: var(--space-5);
    border-radius: var(--radius-lg);
  }

  .bubble {
    max-width: 88%;
  }

  /* 手机上字段名移到上方，避免左侧 52px 挤压选项空间 */
  .field {
    grid-template-columns: 1fr;
    gap: var(--space-2);
  }

  .field-label {
    line-height: 1.4;
  }

  .city-input {
    max-width: 100%;
  }

  .submit-btn {
    height: var(--touch-min);
  }
}
</style>
