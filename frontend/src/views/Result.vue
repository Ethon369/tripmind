<template>
  <div class="result">
    <!-- ---------------- 页头 ---------------- -->
    <header class="rh">
      <a-button class="rh-back" @click="goBack">
        <template #icon><ArrowLeftOutlined /></template>
        返回
      </a-button>

      <div class="rh-actions">
        <!-- 分享模式下整组编辑/删除按钮都不出现（不是禁用 ——
             别人打开链接看到的应该是一份干净的行程，而不是一排点不动的按钮） -->
        <template v-if="!editMode && !isShareMode">
          <a-button @click="toggleEdit">
            <template #icon><EditOutlined /></template>
            <span class="rh-btn-text">编辑行程</span>
          </a-button>
        </template>
        <template v-else-if="editMode">
          <a-button type="primary" :loading="saving" @click="saveChanges">
            <template #icon><SaveOutlined /></template>
            <span class="rh-btn-text">保存修改</span>
          </a-button>
          <a-button @click="cancelEdit">
            <span class="rh-btn-text">取消编辑</span>
          </a-button>
        </template>

        <a-button v-if="planId && !editMode" @click="shareOpen = true">
          <template #icon><LinkOutlined /></template>
          <span class="rh-btn-text">分享</span>
        </a-button>

        <a-dropdown v-if="!editMode && state === 'ready'">
          <template #overlay>
            <a-menu>
              <a-menu-item key="image" @click="onExportImage">导出为图片</a-menu-item>
              <a-menu-item key="pdf" @click="onExportPdf">导出为 PDF</a-menu-item>
            </a-menu>
          </template>
          <a-button>
            <template #icon><DownloadOutlined /></template>
            <span class="rh-btn-text">导出</span>
          </a-button>
        </a-dropdown>
      </div>
    </header>

    <!-- ---------------- 撤销删除提示条 ----------------
         原来删除景点只有一句 message.success，误删只能靠「取消编辑」整体回滚，
         粒度太粗。这里给一个 8 秒的撤销窗口。 -->
    <div v-if="undoState" class="undo-bar" role="status">
      <span>已删除「{{ undoState.name }}」</span>
      <a-button type="link" size="small" @click="undoDelete">撤销</a-button>
    </div>

    <!-- 降级行程的顶部警示 -->
    <a-alert
      v-if="state === 'ready' && meta?.status === 'fallback'"
      class="fallback-alert"
      type="warning"
      show-icon
      message="此行程的部分数据不可靠"
      :description="(meta.warnings || []).join('；') || '生成过程中有环节降级，内容可能不完整。'"
    />

    <!-- ---------------- 四态 ----------------
         加载态与空态在这里是**互斥的两个分支**。
         原实现是 `v-if="tripPlan" ... v-else <空态>`，而 tripPlan 在
         await getPlan() 返回前是 falsy —— 于是加载期间用户先看到
         「没有找到旅行计划数据 / 请先创建行程」，一秒后才被真实内容顶掉。 -->
    <StatePanel v-if="state === 'loading'" state="loading" skeleton="plan" />

    <StatePanel
      v-else-if="state === 'error'"
      state="error"
      :error-kind="errorKind"
      :detail="errorDetail"
      @retry="load"
    >
      <template #extra>
        <a-button @click="goHistory">查看历史行程</a-button>
      </template>
    </StatePanel>

    <div v-else-if="state === 'ready' && tripPlan" class="plan-view">
      <!-- ---------------- 分页 Tab ----------------
           总览 / Day 1 / Day 2 … 各自是一个"页面"，用 ?day=N 记在地址上 ——
           刷新、收藏、发给同行的朋友都能直接落到同一页。
           原来是一个超长页面靠滚动，多天行程只能靠数第几天。

           切页用 router.replace 而不是 push：不写历史记录，
           否则 5 天的行程按一次返回要退 5 次才能离开这个页面。 -->
      <nav class="tabs" aria-label="行程分页">
        <button
          type="button"
          class="tab"
          :class="{ 'is-active': activeDay === null }"
          :aria-current="activeDay === null ? 'page' : undefined"
          @click="setDay(null)"
        >
          总览
        </button>

        <button
          v-for="(d, i) in tripPlan.days"
          :key="d.date"
          type="button"
          class="tab"
          :class="{ 'is-active': activeDay === i }"
          :aria-current="activeDay === i ? 'page' : undefined"
          @click="setDay(i)"
        >
          Day {{ d.day_index + 1 }}
          <em v-if="tabWeather(d.date)" class="tab-weather">{{ tabWeather(d.date) }}</em>
        </button>
      </nav>

      <!-- ---------------- 内容 + 地图 ----------------
           桌面：左边内容、右边地图常驻全高。
           地图常驻的好处是——切到哪天，地图就跟着聚焦到那天的景点，
           不用滚回去找地图。 -->
      <div class="rc-body">
        <div class="rc-content">
          <OverviewPanel
            v-if="activeDay === null"
            :plan="tripPlan"
            :knowledge="knowledgeSources"
            @open-day="setDay"
          />
          <DayTimeline
            v-else
            :day="tripPlan.days[activeDay]"
            :editable="editMode"
            @changed="onDayChanged"
            @remove="onRemoveAttraction"
          />
        </div>

        <aside
          class="rc-map"
          :class="{ 'is-collapsed': isMobile && mapCollapsed }"
          aria-label="行程地图"
        >
          <div class="map-head">
            <h2 class="map-title">
              地图
              <span v-if="activeDay !== null" class="map-sub">
                Day {{ (tripPlan.days[activeDay]?.day_index ?? activeDay) + 1 }}
              </span>
            </h2>
            <a-button
              v-if="isMobile"
              type="link"
              size="small"
              @click="mapCollapsed = !mapCollapsed"
            >
              {{ mapCollapsed ? '展开' : '收起' }}
            </a-button>
          </div>

          <div
            class="map-box"
            role="img"
            :aria-label="`行程包含 ${totalAttractions} 个景点，左侧时间轴中有等价文字信息`"
          >
            <div id="amap-container" class="map-inner"></div>
          </div>

          <p v-if="mapError" class="map-error">{{ mapError }}</p>
        </aside>
      </div>

      <!-- ---------------- 导出用的离屏完整视图 ----------------
           分页之后可见区域一次只渲染一页，但「导出行程」要的是**整份** ——
           否则点一下导出只拿到当前这一页。导出期间临时挂上这份视图
           （见 withFullCapture），html2canvas 抓它，和分页前的长页内容一致。

           离屏而不是 display:none：隐藏掉会让 html2canvas 量不到尺寸，
           截出来是 0×0 的空白图（usePlanExport 里有同样的注释）。 -->
      <div v-if="captureMode" class="capture-stage plan-capture-root" aria-hidden="true">
        <OverviewPanel :plan="tripPlan" :knowledge="knowledgeSources" />
        <DayTimeline v-for="(d, i) in tripPlan.days" :key="`cap-${i}`" :day="d" />
        <!-- 地图位。用 data-export-map 而不是 id：真实地图已经占了 #amap-container -->
        <div class="map-box" data-export-map></div>
      </div>
    </div>

    <a-back-top :visibility-height="400" />

    <!-- ---------------- 分享链接弹窗 ----------------
         原来是复制失败时把 URL 塞进 message 里显示 8 秒 ——
         长 URL 在 toast 里会折行成一团，且一闪而过。 -->
    <a-modal v-model:open="shareOpen" title="分享这份行程" :footer="null" :width="520">
      <p class="share-hint">拿到链接的人可以只读查看这份行程，不含编辑入口。</p>
      <div class="share-row">
        <a-input :value="shareUrl" readonly />
        <a-button type="primary" @click="copyShareLink">复制</a-button>
      </div>
    </a-modal>

    <!-- ---------------- 未保存离开确认 ---------------- -->
    <a-modal v-model:open="unsavedVisible" title="有未保存的修改" :footer="null" :width="420">
      <p class="unsaved-text">离开后这次编辑的内容会丢失，确定要离开吗？</p>
      <div class="unsaved-actions">
        <a-button @click="cancelLeave">继续编辑</a-button>
        <a-button danger @click="confirmLeave">放弃修改并离开</a-button>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import {
  ArrowLeftOutlined,
  DownloadOutlined,
  EditOutlined,
  LinkOutlined,
  SaveOutlined,
} from '@ant-design/icons-vue';
import AMapLoader from '@amap/amap-jsapi-loader';
import StatePanel from '@/components/common/StatePanel.vue';
import OverviewPanel from '@/components/plan/OverviewPanel.vue';
import DayTimeline from '@/components/plan/DayTimeline.vue';
import { usePlanCache } from '@/composables/usePlanCache';
import { usePlanExport } from '@/composables/usePlanExport';
import { useScreen } from '@/composables/useScreen';
import { useUnsavedGuard } from '@/composables/useUnsavedGuard';
import { getPlan, updatePlan } from '@/services/api';
import type {
  Attraction,
  KnowledgeSource,
  PlanSummary,
  TripPlan,
  WeatherInfo,
} from '@/types';

/**
 * 行程详情。
 *
 * 三种到达方式（与 CLAUDE.md 记录的保持一致）：
 *   1. /result/{id}  从后端取（刷新、换标签页、分享都走这条）
 *   2. /result       回落到 sessionStorage（兼容旧路径）
 *   3. /share/{id}   同一组件，只读
 *
 * ## 布局：Tab 分页 + 右侧常驻地图
 *
 * 改造前是一个**超长页面**：概览、预算、地图、每日行程、天气、知识出处全部竖着摞，
 * 多天行程只能靠滚。现在按参考产品改成：
 *   顶部 Tab（总览 / Day 1 / Day 2 …）+ 左边内容 + 右边地图常驻。
 *
 * 这么改还有一个实际好处：**地图不用滚回去找了**。
 * 切换 Tab 时地图会跟着聚焦到那一天的景点（见 focusDay）。
 *
 * Tab 状态记在 `?day=N` 上（不写 = 总览）：刷新、收藏、分享链接都落在同一页。
 * 切页用 `router.replace`，所以浏览器的返回键是"离开这个行程"而不是"退回上一个 Tab" ——
 * 5 天的行程不该让人连按 5 次返回才走得掉。
 */

const route = useRoute();
const router = useRouter();
const { isMobile } = useScreen();
const planCache = usePlanCache();
const { exportImage, exportPdf } = usePlanExport();

const tripPlan = ref<TripPlan | null>(null);
const meta = ref<PlanSummary | null>(null);
/** 这次生成用到的知识出处。为空时总览页不显示那一块 */
const knowledgeSources = ref<KnowledgeSource[]>([]);

type ViewState = 'loading' | 'ready' | 'error';
const state = ref<ViewState>('loading');
const errorKind = ref<'network' | 'server' | 'notFound' | 'business'>('network');
const errorDetail = ref('');

const editMode = ref(false);
const saving = ref(false);
const originalPlan = ref<TripPlan | null>(null);

const shareOpen = ref(false);
const mapCollapsed = ref(false);
const mapError = ref('');

let map: any = null;
let AMapRef: any = null;
/** 按天分组的标记，供 focusDay 用 */
const dayMarkers = new Map<number, any[]>();

const planId = computed(() => (route.params.id as string) || '');
const isShareMode = computed(() => route.path.startsWith('/share'));
const shareUrl = computed(() => `${window.location.origin}/share/${planId.value}`);

const totalAttractions = computed(
  () => tripPlan.value?.days?.reduce((n, d) => n + (d.attractions?.length || 0), 0) ?? 0
);

/**
 * 当前 Tab：null = 总览；数字 = 第几天（0 起）。
 * 从地址栏读，所以刷新、前进后退都保持在同一页。
 */
const activeDay = computed<number | null>(() => {
  const raw = route.query.day;
  if (raw === undefined || raw === '') return null;

  const n = Number(raw);
  if (!Number.isFinite(n) || n < 0) return null;

  // 越界的 day 当成总览，避免手改地址栏把页面搞崩
  const days = tripPlan.value?.days?.length ?? 0;
  return n < days ? n : null;
});

function setDay(index: number | null) {
  router.replace({ query: index === null ? {} : { day: String(index) } });

  if (index !== null) void focusDay(index);

  // 换页后回到页首。
  // 不这么做的话：从很长的 Day 1 切到很短的 Day 3 时，原滚动位置超出新内容的长度，
  // 浏览器会把它钳到新内容的底部 —— 用户一进来看到的是行程的结尾。
  // 只有真的滚下去了才动，免得在页首点 Tab 时多一次无谓的滚动动画。
  nextTick(() => {
    if (window.scrollY > 0) window.scrollTo({ top: 0, behavior: 'smooth' });
  });
}

/* ---------------- 天气（Tab 上的一小片提示） ---------------- */

const weatherOf = (date: string): WeatherInfo | undefined =>
  tripPlan.value?.weather_info?.find((w) => w.date === date);

/**
 * Tab 上那行天气小字。天气文案塞不进 Tab，只取两三个字。
 *
 * 这里**返回空串而不是 undefined** —— 模板里 `v-if` 和插值是两个独立的表达式，
 * TypeScript 无法跨它们做类型收窄，传 `WeatherInfo | undefined` 进 `shortWeather`
 * 会直接报 TS2345。让它自己处理"没有这天的天气"，模板就只需要判断一次。
 */
function tabWeather(date: string): string {
  const text = weatherOf(date)?.day_weather || '';
  return text.length > 4 ? text.slice(0, 4) : text;
}

/* ---------------- 未保存保护 ---------------- */

/** 编辑模式下只要进过编辑态就视为脏 —— 逐字段比对成本高于收益 */
const isDirty = computed(() => editMode.value);

const {
  visible: unsavedVisible,
  confirmLeave,
  cancelLeave,
} = useUnsavedGuard(() => isDirty.value);

/* ---------------- 加载 ---------------- */

async function renderPlan(plan: TripPlan) {
  tripPlan.value = plan;

  // 必须先切到 ready，地图容器才会被渲染出来 —— initMap() 依赖 #amap-container 在 DOM 里。
  // （原实现把 state 设在 renderPlan 返回之后，导致 initMap 时容器还不存在，
  //   而 initMap 开头有个静默 return，于是地图永远不初始化、也不报错。）
  state.value = 'ready';

  if (map) {
    map.destroy();
    map = null;
  }
  dayMarkers.clear();

  await nextTick();
  await initMap();
}

async function load() {
  const id = route.params.id as string | undefined;
  state.value = 'loading';
  errorDetail.value = '';

  // ---- 路径 1：带 id，从后端取 ----
  if (id) {
    try {
      const res = await getPlan(id);
      if (res.success && res.data) {
        meta.value = res.meta || null;
        // 知识出处跟着详情一起回来。没开 RAG / 没命中时后端给空数组
        knowledgeSources.value = res.knowledge || [];
        await renderPlan(res.data);
        return;
      }
      // 后端对 status='running' 的行程返回 success=false 而**不是** HTTP 错误
      errorKind.value = 'business';
      errorDetail.value = res.message || '该行程尚未生成完成';
      state.value = 'error';
    } catch (e: unknown) {
      const msg = (e as Error)?.message || '读取行程失败';
      errorKind.value = msg.includes('不存在') ? 'notFound' : 'network';
      errorDetail.value = msg;
      state.value = 'error';
    }
    return;
  }

  // ---- 路径 2：无 id，回落本地缓存 ----
  const cached = planCache.load();
  if (!cached) {
    router.replace('/');
    return;
  }
  await renderPlan(cached);
}

onMounted(load);

onUnmounted(() => {
  map?.destroy();
  map = null;
});

/* ---------------- 编辑 ---------------- */

function toggleEdit() {
  editMode.value = true;
  originalPlan.value = JSON.parse(JSON.stringify(tripPlan.value));
  message.info('已进入编辑模式，改动需点「保存修改」才会写入');
}

async function saveChanges() {
  if (!tripPlan.value) return;
  saving.value = true;

  try {
    if (planId.value) {
      await updatePlan(planId.value, tripPlan.value);
      message.success('修改已保存');
    } else {
      message.success('修改已保存（仅在本标签页，刷新后丢失）');
    }
    planCache.save(tripPlan.value);
    editMode.value = false;

    // 景点可能被增删，地图要跟着变
    rebuildMarkers();
  } catch (e: unknown) {
    // 没存上就不能假装成功 —— 保持编辑态，让用户知道改动还在手上
    message.error((e as Error)?.message || '保存失败，请重试');
  } finally {
    saving.value = false;
  }
}

function cancelEdit() {
  if (originalPlan.value) {
    tripPlan.value = JSON.parse(JSON.stringify(originalPlan.value));
  }
  editMode.value = false;
  message.info('已取消编辑');
}

/* ---------------- 撤销栈（删除景点） ---------------- */

const undoState = ref<{ dayIndex: number; index: number; name: string; item: Attraction } | null>(
  null
);
let undoTimer: ReturnType<typeof setTimeout> | null = null;

/** DayTimeline 里改了顺序或字段 —— 重建标记（顺序变了，编号也得变） */
function onDayChanged() {
  rebuildMarkers();
}

/**
 * 删除景点。**由父组件执行**，因为这三件事都只有这里能做：
 * 撤销栈、全局 message、地图标记重建。
 * 子组件只负责 emit「用户点了删除」。
 */
function onRemoveAttraction(index: number) {
  const dayIndex = activeDay.value;
  if (dayIndex === null) return;

  const day = tripPlan.value?.days?.[dayIndex];
  if (!day) return;

  if (day.attractions.length <= 1) {
    // 每天至少留一个景点 —— 说明原因，而不是静默拒绝
    message.warning('每天至少需要保留一个景点');
    return;
  }

  const [removed] = day.attractions.splice(index, 1);
  pushUndo(dayIndex, index, removed);
  rebuildMarkers();
}

/** 把删除记进撤销栈，给 8 秒反悔窗口 */
function pushUndo(dayIndex: number, index: number, item: Attraction) {
  undoState.value = { dayIndex, index, name: item.name, item };
  if (undoTimer) clearTimeout(undoTimer);
  undoTimer = setTimeout(() => {
    undoState.value = null;
  }, 8000);
}

function undoDelete() {
  const snapshot = undoState.value;
  if (!snapshot) return;
  const day = tripPlan.value?.days?.[snapshot.dayIndex];
  if (day) {
    day.attractions.splice(snapshot.index, 0, snapshot.item);
    rebuildMarkers();
  }
  undoState.value = null;
  if (undoTimer) clearTimeout(undoTimer);
}

/* ---------------- 导出 ---------------- */

/**
 * 导出期间临时挂上的「完整行程」离屏视图。
 *
 * 为什么需要它：分页把一次滚动的长页拆成了多页，而 `usePlanExport` 抓的是
 * `.plan-capture-root` 的 innerHTML —— 分页后那就是**当前这一页**，
 * 用户点「导出为 PDF」只会拿到 Day 2 一页，这是分页改造引入的副作用。
 *
 * 所以导出前把整份行程渲染到离屏容器里，让导出抓它；导出结束立刻卸掉。
 * 日常渲染完全不受影响（没有额外的一直存在的 DOM）。
 */
const captureMode = ref(false);

async function withFullCapture<T>(run: () => Promise<T>): Promise<T> {
  captureMode.value = true;
  await nextTick(); // 等离屏视图进 DOM —— 导出靠 querySelector 找它
  try {
    return await run();
  } finally {
    captureMode.value = false;
  }
}

const onExportImage = () => withFullCapture(() => exportImage(tripPlan.value?.city || '行程'));

const onExportPdf = () => withFullCapture(() => exportPdf(tripPlan.value?.city || '行程'));

async function copyShareLink() {
  try {
    await navigator.clipboard.writeText(shareUrl.value);
    message.success('链接已复制');
    shareOpen.value = false;
  } catch {
    // 非 HTTPS 或浏览器不给剪贴板权限。链接已经在弹窗的输入框里了，
    // 用户可以手动选中复制，不用再想办法把 URL 塞进 toast。
    message.info('当前浏览器不允许自动复制，请手动选中链接复制');
  }
}

const goBack = () => router.push('/');
const goHistory = () => router.push('/history');

/* ---------------- 地图 ---------------- */

function collectAttractions(dayIndex?: number) {
  const out: Array<Attraction & { dayIndex: number; attrIndex: number }> = [];
  tripPlan.value?.days?.forEach((day, di) => {
    if (dayIndex !== undefined && di !== dayIndex) return;
    day.attractions?.forEach((a, attrIndex) => {
      if (a.location?.longitude && a.location?.latitude) {
        out.push({ ...a, dayIndex: di, attrIndex });
      }
    });
  });
  return out;
}

/** 建标记。抽取出来是为了"编辑后重建"和"初次初始化"共用一份 */
function rebuildMarkers() {
  if (!map || !AMapRef) return;

  map.clearMap();
  dayMarkers.clear();

  const AMap = AMapRef;
  const all = collectAttractions();

  all.forEach((a) => {
    const marker = new AMap.Marker({
      position: [a.location.longitude, a.location.latitude],
      title: a.name,
      label: {
        content: `<div style="background:#0e7490;color:#fff;padding:2px 7px;border-radius:10px;font-size:12px;">${a.attrIndex + 1}</div>`,
        offset: new AMap.Pixel(0, -28),
      },
    });

    const infoWindow = new AMap.InfoWindow({
      content: `<div style="padding:10px;max-width:260px;">
          <h4 style="margin:0 0 6px;">${escapeHtml(a.name)}</h4>
          <p style="margin:4px 0;"><strong>地址：</strong>${escapeHtml(a.address || '—')}</p>
          <p style="margin:4px 0;"><strong>游览时长：</strong>${a.visit_duration} 分钟</p>
          <p style="margin:4px 0;color:#0e7490;">第 ${a.dayIndex + 1} 天 · 第 ${a.attrIndex + 1} 个</p>
        </div>`,
      offset: new AMap.Pixel(0, -28),
    });

    marker.on('click', () => infoWindow.open(map, marker.getPosition()));

    const list = dayMarkers.get(a.dayIndex) ?? [];
    list.push(marker);
    dayMarkers.set(a.dayIndex, list);
    map.add(marker);
  });

  // 每天一条折线
  const byDay = new Map<number, typeof all>();
  all.forEach((a) => {
    const list = byDay.get(a.dayIndex) ?? [];
    list.push(a);
    byDay.set(a.dayIndex, list);
  });
  byDay.forEach((list) => {
    if (list.length < 2) return;
    map.add(
      new AMap.Polyline({
        path: list.map((a) => [a.location.longitude, a.location.latitude]),
        strokeColor: '#0e7490',
        strokeWeight: 4,
        strokeOpacity: 0.75,
        showDir: true,
      })
    );
  });
}

async function initMap() {
  const container = document.getElementById('amap-container');
  if (!container) {
    // 不静默 return：正是「什么都不做、也不报错」让曾经的时序 bug 藏了很久
    mapError.value = '地图容器未就绪，请刷新页面重试。';
    console.error('initMap: 未找到 #amap-container，已跳过地图初始化');
    return;
  }

  mapError.value = '';
  const points = collectAttractions();

  // 高德 2021-12 之后申请的 key 需要配套的安全密钥，否则会报 INVALID_USER_SCODE。
  // 必须在 AMapLoader.load() **之前**设置才生效；没配就不设（老 key 不需要）。
  const securityCode = import.meta.env.VITE_AMAP_SECURITY_CODE;
  if (securityCode) {
    (
      window as unknown as { _AMapSecurityConfig?: { securityJsCode: string } }
    )._AMapSecurityConfig = { securityJsCode: securityCode };
  }

  try {
    const AMap = await AMapLoader.load({
      key: import.meta.env.VITE_AMAP_WEB_JS_KEY,
      version: '2.0',
      plugins: ['AMap.Marker', 'AMap.Polyline', 'AMap.InfoWindow'],
    });
    AMapRef = AMap;

    // 首屏中心点用行程里的第一个景点，而不是硬编码北京
    const first = points[0];
    const center: [number, number] = first
      ? [first.location.longitude, first.location.latitude]
      : [116.397128, 39.916527];

    map = new AMap.Map('amap-container', { zoom: 12, center, viewMode: '3D' });

    rebuildMarkers();

    const all = Array.from(dayMarkers.values()).flat();
    if (all.length) map.setFitView(all);

    // 如果地址栏本来就带着 ?day=N，进去就聚焦那一天
    if (activeDay.value !== null) void focusDay(activeDay.value);
  } catch (e: unknown) {
    // 地图加载失败不该弹全局 toast（用户是来看行程的，不是来看地图报错的），
    // 在卡片内给一行说明即可。错误码原样带出来 —— 它区分了四种完全不同的故障：
    //   INVALID_USER_KEY / INVALID_USER_DOMAIN / USER_KEY_PLAT_NOMATCH / INVALID_USER_SCODE
    console.error('地图加载失败:', e);
    const info = (e as { info?: string })?.info || (e as Error)?.message || '';
    mapError.value = info
      ? `地图加载失败：${info}`
      : '地图加载失败。请确认 frontend/.env 里已配置高德 Web端(JS API) 的 Key。';
  }
}

/**
 * 把视野聚焦到某一天的景点。
 *
 * 这是"地图常驻右侧"带来的新能力：切 Tab 地图就跟着走，
 * 不用像以前那样滚回去找地图、再等它重新渲染。
 */
async function focusDay(index: number) {
  await nextTick();
  const markers = dayMarkers.get(index);
  if (!map || !markers?.length) return;
  map.setFitView(markers, false, [60, 60, 60, 60]);
}

function escapeHtml(s: string): string {
  return (s || '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string
  );
}
</script>

<style scoped>
.result {
  max-width: var(--container-wide);
  margin: 0 auto;
  padding: var(--space-5) var(--space-4) var(--space-8);

  /* Tab 栏占的垂直空间 = 36px 按钮 + 上下各 8px padding + 1px 下边线 ≈ 53px，
     取 56px 留余量。吸顶的 Tab 和吸顶的地图都要用这个数，
     否则地图顶端会被 Tab 盖住一截。 */
  --tabs-h: 56px;
}

/* ---------------- 页头 ---------------- */
.rh {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-bottom: var(--space-4);
}

.rh-actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.undo-bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-4);
  margin-bottom: var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--brand-50);
  border: 1px solid var(--brand-100);
  font-size: var(--fs-sm);
  color: var(--text-1);
  animation: fadeInDown var(--dur-base) var(--ease-out) both;
}

.fallback-alert {
  margin-bottom: var(--space-4);
}

/* ---------------- Tab ----------------
 * 横向可滚动，天数多的时候不会挤成一团（手机上尤其重要）。
 *
 * **吸顶**：Day 1 的内容一长，Tab 一旦滚出视口，想切到 Day 2 就得先滚回顶部 ——
 * 分页的意义（随手切换）就没了。z-index 取 20：要盖住右侧吸顶地图，
 * 但不能越过全局顶栏（App.vue 里是 100）和 antd 的弹层（1000+）。
 * 背景必须给实色，否则内容会从吸顶栏下面透出来。
 */
.tabs {
  position: sticky;
  top: var(--header-h);
  z-index: 20;
  background: var(--bg-page);
  display: flex;
  gap: var(--space-2);
  overflow-x: auto;
  overflow-y: hidden;
  padding: var(--space-2) 0;
  margin-bottom: var(--space-4);
  border-bottom: 1px solid var(--line-1);
  scrollbar-width: none;
}

.tabs::-webkit-scrollbar {
  display: none;
}

.tab {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex: 0 0 auto;
  min-height: 36px;
  padding: 0 var(--space-4);
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  background: transparent;
  font-family: inherit;
  font-size: var(--fs-caption);
  font-weight: var(--fw-medium);
  color: var(--text-2);
  white-space: nowrap;
  cursor: pointer;
  transition: color var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out);
}

.tab:hover {
  color: var(--brand-600);
  background: var(--brand-50);
}

.tab.is-active {
  color: var(--brand-600);
  background: var(--bg-tint);
  border-color: var(--brand-300);
  font-weight: var(--fw-semibold);
}

/* 天气小字：藏在 Tab 里，切页时顺手看一眼那天什么天 */
.tab-weather {
  font-style: normal;
  font-size: var(--fs-micro);
  color: var(--text-3);
}

/* ---------------- 内容 + 地图 ---------------- */
.rc-body {
  display: grid;
  /* 左内容自适应、右地图固定 —— 地图的宽度不该随内容长度变化 */
  grid-template-columns: minmax(0, 1fr) 42%;
  gap: var(--space-5);
  align-items: start;
}

.rc-content {
  min-width: 0;
}

/* 地图常驻：sticky 让它在内容很长时也钉在视口里，
   这样"左边的行程滚到哪，地图都在眼前"。
   top 要跳过顶栏**和**吸顶的 Tab，否则地图顶端会被 Tab 压住。 */
.rc-map {
  position: sticky;
  top: calc(var(--header-h) + var(--tabs-h) + var(--space-4));
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-1);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.map-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.map-title {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.map-sub {
  font-size: var(--fs-micro);
  font-weight: var(--fw-regular);
  color: var(--brand-600);
}

.map-box {
  position: relative;
  width: 100%;
  /* 高度按视口算：地图常驻时应该"长满一屏"，而不是固定值留出空白。
     减去顶栏、吸顶 Tab、页头与卡片内边距的高度；上下限保证极端尺寸下仍可用。 */
  height: clamp(360px, calc(100dvh - var(--header-h) - var(--tabs-h) - 220px), 720px);
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: var(--bg-sunken);
}

.rc-map.is-collapsed .map-box {
  height: 0;
}

.map-inner {
  width: 100%;
  height: 100%;
}

.map-error {
  font-size: var(--fs-caption);
  color: var(--warning);
}

/* ---------------- 导出用的离屏完整视图 ----------------
 * 只活到导出结束。放在视口外而不是 display:none —— 后者量不到尺寸，
 * html2canvas 会截出 0×0 的空白图。
 * pointer-events:none 让它在离屏期间也绝不拦截点击；
 * aria-hidden 在模板上，避免读屏重复朗读整份行程。
 */
.capture-stage {
  position: fixed;
  left: -20000px;
  top: 0;
  width: 1000px;
  box-sizing: border-box;
  padding: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  pointer-events: none;
}

/* ---------------- 弹窗 ---------------- */
.share-hint {
  font-size: var(--fs-caption);
  color: var(--text-2);
  margin-bottom: var(--space-3);
}

.share-row {
  display: flex;
  gap: var(--space-3);
}

.unsaved-text {
  font-size: var(--fs-caption);
  color: var(--text-1);
  margin-bottom: var(--space-5);
}

.unsaved-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}

/* ---------------- 响应式 ----------------
 * 991 以下地图不再常驻右侧：横向排不下两块内容。
 * 改成"Tab → 地图 → 内容"的纵向结构，地图高度回到固定值。
 */
@media (max-width: 991px) {
  .rc-body {
    grid-template-columns: 1fr;
  }

  .rc-map {
    position: static;
    order: -1; /* 地图放在内容上方：先看空间关系再看文字 */
  }

  .map-box {
    height: 320px;
  }
}

@media (max-width: 767px) {
  .result {
    padding: var(--space-4) var(--space-3) var(--space-6);
  }

  .rh-actions {
    width: 100%;
  }

  .rh-actions :deep(.ant-btn) {
    flex: 1 1 0;
    min-height: var(--touch-min);
  }

  .map-box {
    height: 260px;
  }

  .rc-map {
    padding: var(--space-3);
  }
}

/* 窄屏只留图标：三个带文字的按钮在 375px 上放不下 */
@media (max-width: 575px) {
  /* 顶栏在 575 以下换成 56px 高（见 App.vue），吸顶的 Tab 得跟着走 ——
     否则 Tab 上方会空出一道缝，把下面的内容露出来 */
  .tabs {
    top: var(--header-h-mobile);
  }

  .rh-btn-text {
    display: none;
  }

  .rh-back {
    min-width: var(--touch-min);
  }
}
</style>
