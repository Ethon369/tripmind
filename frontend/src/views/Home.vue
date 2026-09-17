<template>
  <div class="home">
    <!-- ================= Hero + 起步区 =================
         首页只负责一件事：让用户「说一句」或「挑一个」，然后带着这些信息去创建页。
         详细的日期与偏好已经搬到 /create —— 那些是"决定好要去了"之后才需要填的东西，
         全堆在首页会让落地页变成一个长表单。 -->
    <section class="hero">
      <span class="hero-glow glow-1" aria-hidden="true"></span>
      <span class="hero-glow glow-2" aria-hidden="true"></span>

      <div class="hero-inner">
        <span class="hero-badge">
          <svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true" fill="currentColor">
            <path d="M12 2l1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8z" />
          </svg>
          AI 智能行程规划
        </span>

        <h1 class="hero-title">想去哪儿，先说说你的计划</h1>
        <p class="hero-sub">
          描述目的地和天数，或者从下面的热门攻略里挑一个。下一步再确认细节，就能生成含景点、天气、住宿与预算的完整安排。
        </p>
      </div>

      <!-- ---------------- 大输入框 ---------------- -->
      <div class="starter">
        <div class="starter-box">
          <SearchOutlined class="starter-lead" aria-hidden="true" />

          <input
            v-model="description"
            class="starter-input"
            type="text"
            :maxlength="LIMITS.MAX_FREE_TEXT_LEN"
            placeholder="例如：国庆想去云南待五天，喜欢自然风光和古镇"
            aria-label="描述你的旅行计划"
            @keyup.enter="goCreate()"
          />

          <!-- 截图/文件上传（目前只做前端展示，见 script 里的说明） -->
          <label class="starter-attach" title="上传截图或文件">
            <input
              type="file"
              multiple
              accept="image/*,.pdf,.md,.txt,.doc,.docx"
              hidden
              @change="onFilesPicked"
            />
            <PaperClipOutlined aria-hidden="true" />
            <span class="sr-only">上传截图或文件</span>
          </label>

          <a-button type="primary" class="starter-btn" @click="goCreate()">
            开始规划
            <template #icon><ArrowRightOutlined /></template>
          </a-button>
        </div>

        <!-- 热门目的地 + 上传 -->
        <div class="starter-chips">
          <span class="chips-label">热门</span>
          <button
            v-for="c in HOT_CITIES"
            :key="c"
            type="button"
            class="chip"
            @click="goCreate(c)"
          >
            {{ c }}
          </button>

          <label class="chip chip-upload">
            <input
              type="file"
              multiple
              accept="image/*,.pdf,.md,.txt,.doc,.docx"
              hidden
              @change="onFilesPicked"
            />
            <UploadOutlined aria-hidden="true" />
            上传截图/文件
          </label>
        </div>

        <!-- 已选文件。当前只做到"选中并展示"，没有真正上传 —— 见 script 里的说明 -->
        <p v-if="pickedFiles.length" class="starter-files">
          <CheckCircleOutlined aria-hidden="true" />
          已选择 {{ pickedFiles.length }} 个文件：{{ pickedFiles.join('、') }}
          <span class="files-note">（当前仅前端展示，未上传到后端）</span>
          <button type="button" class="files-clear" @click="pickedFiles = []">清空</button>
        </p>
      </div>
    </section>

    <!-- ================= 热门目的地 =================
         下滑可见。每张卡片配一张照片（来自 /api/poi/photo 的 Unsplash 搜索），
         取不到图就降级成渐变占位块 —— 照片是锦上添花，不该让整页出错。

         TODO: 点卡片现在只是把城市名带到创建页；后续可接 city_guides 检索接口
         做成真正的「攻略详情页」。 -->
    <section class="cities">
      <div class="cities-inner">
        <div class="section-head">
          <h2 class="section-title">热门目的地</h2>
          <span class="section-note">点一张卡片，直接带着目的地去创建行程</span>
        </div>

        <div class="city-grid">
          <button
            v-for="(c, i) in CITY_CARDS"
            :key="c.city"
            type="button"
            class="city-card"
            @click="goCreate(c.city)"
          >
            <span class="city-photo">
              <img
                :src="cityPhoto(c) || placeholderImage(c.city, i)"
                :alt="`${c.city} · ${c.landmark}`"
                loading="lazy"
                decoding="async"
                @error="onPhotoError"
              />
              <span class="city-days u-num">{{ c.days }} 天</span>
            </span>

            <span class="city-body">
              <span class="city-head">
                <span class="city-name">{{ c.city }}</span>
                <span class="city-tag">{{ c.tags }}</span>
              </span>
              <span class="city-desc">{{ c.desc }}</span>
              <span class="city-more">
                看看{{ c.city }}怎么玩
                <ArrowRightOutlined aria-hidden="true" />
              </span>
            </span>
          </button>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import {
  SearchOutlined,
  PaperClipOutlined,
  ArrowRightOutlined,
  UploadOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons-vue';
import { useTripDraft } from '@/composables/useTripDraft';
import { placeholderImage } from '@/composables/useAttractionPhotos';
import { getPoiPhoto } from '@/services/api';
import { LIMITS } from '@/constants/tripOptions';

/**
 * 首页。
 *
 * 职责被收窄成两件事：**让用户说一句** 或 **让用户挑一个**，
 * 然后带着这些信息跳到 `/create`。
 *
 * 原来挤在这里的「详细设置」（日期、天数、偏好、交通、住宿）已经搬到创建页 ——
 * 那些是"决定要去了"之后才需要填的东西，全堆在落地页会让它变成一个长表单。
 *
 * ## 关于上传
 * **目前只做前端展示**：可以选中文件、显示文件名、清空，但不会上传。
 * 后端没有接收文件的接口。要真做需要先加一个解析接口
 * （文件接收 + OCR/文档解析 → 行程草稿），那是后端的工作。
 */

const router = useRouter();
const tripDraft = useTripDraft();

/** 用户的一句话描述，最终作为 TripRequest.free_text_input 提交 */
const description = ref('');
const pickedFiles = ref<string[]>([]);

/** 热门目的地：与后端 knowledge_base 里已有深度攻略的城市一致 */
const HOT_CITIES = ['北京', '上海', '成都', '西安', '杭州'] as const;

/**
 * 城市卡片。
 *
 * ⚠️ **取图关键词必须用英文**（`photoQuery`）。
 *
 * 后端接的是 Unsplash 关键词搜索，而**它对中文基本无效** —— 实测：
 *   「外滩」和「西湖」返回了同一张图；
 *   「成都大熊猫繁育研究基地」直接搜不到（返回空）。
 * 换成对应的英文词后，五个城市各拿到不同的、语义正确的图。
 *
 * `landmark` 保留中文，只用于图片的 alt 文本（给屏幕阅读器读）。
 *
 * TODO: 点击目前只是把城市名带到创建页；后续可接 `city_guides` 检索接口
 * 做成真正的「攻略详情页」（后端已有按城市检索攻略的能力）。
 */
const CITY_CARDS = [
  {
    city: '北京',
    photoQuery: 'Forbidden City Beijing',
    landmark: '故宫博物院',
    days: 3,
    tags: '历史文化',
    desc: '中轴线、长城与胡同，景点尺度极大，怎么排最省体力',
  },
  {
    city: '上海',
    photoQuery: 'The Bund Shanghai',
    landmark: '外滩',
    days: 2,
    tags: '城市漫步',
    desc: '外滩、武康路、杨浦滨江工业带，把几段 citywalk 串成一条线',
  },
  {
    city: '成都',
    photoQuery: 'Chengdu panda base',
    landmark: '成都大熊猫繁育研究基地',
    days: 3,
    tags: '美食',
    desc: '熊猫基地、茶馆与苍蝇馆子，附排队时段与避坑提示',
  },
  {
    city: '西安',
    photoQuery: 'Terracotta Army Xian',
    landmark: '秦始皇帝陵博物院',
    days: 3,
    tags: '历史',
    desc: '城墙内外步行可达，而兵马俑在 40 公里外 —— 结构完全不一样',
  },
  {
    city: '杭州',
    photoQuery: 'West Lake Hangzhou',
    landmark: '西湖',
    days: 2,
    tags: '自然风光',
    desc: '环湖怎么走不绕路，灵隐与西溪怎么取舍',
  },
] as const;

/* ---------------- 城市照片 ----------------
 * 并发拉一次，不阻塞首屏：卡片先用渐变占位块渲染，图回来再替换
 * （配合 img 的 loading="lazy"，滚到那儿才真正加载）。
 */
const cityPhotos = ref<Record<string, string>>({});

/** 以 photoQuery 为键缓存，同一个关键词只请求一次 */
const cityPhoto = (c: (typeof CITY_CARDS)[number]) => cityPhotos.value[c.photoQuery] || '';

/** 通用灰底占位：照片 URL 失效（过期/被墙）时用，避免出现浏览器的裂图图标 */
const PHOTO_FALLBACK =
  'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="400" height="260"%3E%3Crect width="400" height="260" fill="%23e8f4f7"/%3E%3C/svg%3E';

function onPhotoError(e: Event) {
  const img = e.target as HTMLImageElement;
  img.src = PHOTO_FALLBACK;
  // 防止占位图本身也失败时无限触发
  img.onerror = null;
}

async function loadCityPhotos() {
  await Promise.all(
    CITY_CARDS.map(async (c) => {
      const url = await getPoiPhoto(c.photoQuery);
      if (url) cityPhotos.value = { ...cityPhotos.value, [c.photoQuery]: url };
    })
  );
}

onMounted(loadCityPhotos);

/** 上传（仅前端展示） */
function onFilesPicked(e: Event) {
  const input = e.target as HTMLInputElement;
  const names = Array.from(input.files || []).map((f) => f.name);
  if (names.length) {
    pickedFiles.value = [...new Set([...pickedFiles.value, ...names])];
  }
  // 清空 value：否则连续选同一个文件不会再触发 change
  input.value = '';
}

/**
 * 去创建页。
 *
 * 把「说了什么」和「挑了哪个城市」写进草稿带过去 ——
 * 创建页会把它回显成一条消息气泡，让用户确认"刚才说的它收到了"。
 */
function goCreate(city?: string) {
  tripDraft.save({
    city,
    free_text_input: description.value.trim() || undefined,
  });
  router.push('/create');
}
</script>

<style scoped>
.home {
  min-height: 100%;
  background: var(--bg-page);
  padding-bottom: var(--space-9);
}

/* ==========================================================
   Hero + 起步区
   ========================================================== */
.hero {
  position: relative;
  overflow: hidden;
  padding: var(--space-8) var(--space-4) var(--space-7);
}

/* 柔和的紫色光晕。局部点缀，**不铺满** ——
   铺满整屏就变成了烂大街的"紫渐变背景"。 */
.hero-glow {
  position: absolute;
  border-radius: 50%;
  pointer-events: none;
  filter: blur(80px);
  opacity: 0.5;
}

.glow-1 {
  width: 360px;
  height: 360px;
  background: var(--brand-a26);
  top: -150px;
  left: 10%;
}

.glow-2 {
  width: 300px;
  height: 300px;
  /* 第二个光晕用更淡的主色，两个之间才有层次 */
  background: var(--brand-a14);
  top: -70px;
  right: 8%;
}

.hero-inner {
  position: relative;
  z-index: 1;
  max-width: var(--container-narrow);
  margin: 0 auto var(--space-6);
  text-align: center;
  animation: fadeInUp var(--dur-slow) var(--ease-out) both;
}

.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 5px var(--space-3);
  margin-bottom: var(--space-4);
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.75);
  border: 1px solid var(--brand-100);
  color: var(--brand-600);
  font-size: var(--fs-micro);
  font-weight: var(--fw-semibold);
}

.hero-title {
  font-size: var(--fs-display);
  font-weight: var(--fw-semibold);
  line-height: 1.25;
  letter-spacing: var(--ls-tight);
  color: var(--text-1);
  margin-bottom: var(--space-3);
}

.hero-sub {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--text-2);
  max-width: 54ch;
  margin: 0 auto;
}

/* ---------------- 起步区 ---------------- */
.starter {
  position: relative;
  z-index: 1;
  max-width: 760px;
  margin: 0 auto;
  animation: fadeInUp var(--dur-slow) var(--ease-out) 80ms both;
}

/* 大输入框：白底药丸 + 柔和大阴影，是整个首屏的视觉焦点 */
.starter-box {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 6px 6px 6px var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  border-radius: var(--radius-pill);
  box-shadow: var(--shadow-4);
  transition: border-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out);
}

.starter-box:focus-within {
  border-color: var(--brand-400);
  box-shadow: var(--shadow-4), 0 0 0 4px var(--brand-a14);
}

.starter-lead {
  font-size: 18px;
  color: var(--text-3);
  flex-shrink: 0;
}

/* 输入本身不要边框不要背景 —— 容器已经是输入框了 */
.starter-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-family: inherit;
  font-size: var(--fs-body);
  color: var(--text-1);
  padding: var(--space-3) 0;
}

.starter-input::placeholder {
  color: var(--text-3);
}

.starter-attach {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  color: var(--text-3);
  cursor: pointer;
  flex-shrink: 0;
  transition: color var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out);
}

.starter-attach:hover {
  color: var(--brand-600);
  background: var(--brand-50);
}

.starter-btn {
  height: 44px;
  padding-inline: var(--space-5);
  font-weight: var(--fw-semibold);
  flex-shrink: 0;
}

/* 热门标签行 */
.starter-chips {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-5);
}

.chips-label {
  font-size: var(--fs-caption);
  color: var(--text-3);
  margin-right: var(--space-1);
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 5px var(--space-4);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.8);
  font-family: inherit;
  font-size: var(--fs-caption);
  color: var(--text-2);
  cursor: pointer;
  transition: border-color var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out);
}

.chip:hover {
  border-color: var(--brand-400);
  background: var(--brand-50);
  color: var(--brand-600);
}

.chip-upload {
  border-style: dashed;
}

/* 已选文件提示 */
.starter-files {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-4);
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.starter-files :deep(.anticon) {
  color: var(--success);
}

.files-note {
  color: var(--text-3);
}

.files-clear {
  border: none;
  background: transparent;
  padding: 0;
  font-family: inherit;
  font-size: var(--fs-caption);
  color: var(--brand-ink);
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}

/* ==========================================================
   热门目的地（带照片）
   ========================================================== */
.cities {
  padding: var(--space-6) var(--space-4) 0;
}

.cities-inner {
  max-width: var(--container);
  margin: 0 auto;
}

.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
  margin-bottom: var(--space-5);
}

.section-title {
  font-size: var(--fs-h2);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.section-note {
  font-size: var(--fs-caption);
  color: var(--text-3);
}

.city-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-4);
}

/* 卡片本身就是按钮：整张可点，键盘也能 Tab 到 */
.city-card {
  display: flex;
  flex-direction: column;
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-1);
  overflow: hidden;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--ease-out);
}

.city-card:hover {
  border-color: var(--brand-300);
  box-shadow: var(--shadow-4);
  transform: translateY(-3px);
}

/* 照片区：固定 16:10，object-fit 保证不被拉伸变形 */
.city-photo {
  position: relative;
  display: block;
  aspect-ratio: 16 / 10;
  background: var(--bg-sunken);
  overflow: hidden;
}

.city-photo img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform var(--dur-slow) var(--ease-out);
}

/* 悬停时照片轻微推近。只动 transform，不触发重排 */
.city-card:hover .city-photo img {
  transform: scale(1.04);
}

/* 天数徽标压在照片左下角 */
.city-days {
  position: absolute;
  left: var(--space-3);
  bottom: var(--space-3);
  padding: 3px var(--space-3);
  border-radius: var(--radius-pill);
  background: rgba(15, 23, 42, 0.6);
  color: #fff;
  font-size: var(--fs-micro);
  font-weight: var(--fw-medium);
}

.city-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-5) var(--space-5);
  flex: 1;
}

.city-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}

.city-name {
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.city-tag {
  padding: 1px var(--space-3);
  border-radius: var(--radius-pill);
  background: var(--bg-tint);
  color: var(--brand-ink);
  font-size: var(--fs-micro);
  white-space: nowrap;
}

.city-desc {
  font-size: var(--fs-caption);
  line-height: var(--lh-body);
  color: var(--text-2);
  flex: 1;
}

.city-more {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--fs-caption);
  font-weight: var(--fw-medium);
  color: var(--brand-ink);
}

/* ==========================================================
   响应式
   ========================================================== */
@media (max-width: 991px) {
  .city-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .hero {
    padding: var(--space-7) var(--space-3) var(--space-6);
  }

  .hero-glow {
    filter: blur(60px);
    opacity: 0.4;
  }

  .glow-1 {
    width: 240px;
    height: 240px;
  }

  .glow-2 {
    width: 200px;
    height: 200px;
  }

  /* 手机上输入框改为两行：上面是输入，下面是附件与按钮 */
  .starter-box {
    flex-wrap: wrap;
    padding: var(--space-4);
    border-radius: var(--radius-xl);
    gap: var(--space-2);
  }

  .starter-input {
    width: 100%;
    flex: 1 1 100%;
    padding: var(--space-2) 0;
  }

  .starter-lead {
    display: none;
  }

  .starter-attach {
    margin-left: auto;
    width: var(--touch-min);
    height: var(--touch-min);
  }

  .starter-btn {
    flex: 1;
    height: var(--touch-min);
  }

  .cities {
    padding-inline: var(--space-3);
  }

  .city-grid {
    grid-template-columns: 1fr;
  }

  /* 手机上卡片是整屏宽，16:10 会显得太高，压扁到 16:9 */
  .city-photo {
    aspect-ratio: 16 / 9;
  }
}
</style>
