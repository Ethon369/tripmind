<template>
  <div class="landing">
    <!-- ============ 0. 公告条 ============ -->
    <div class="notice">
      <div class="wrap">
        <span class="dot" aria-hidden="true"></span>
        <span><strong>多智能体协作版已上线</strong> · AI 生成结果仅供参考</span>
        <router-link to="/login">查看说明</router-link>
      </div>
    </div>

    <!-- ============ 1. 吸顶导航 ============ -->
    <header class="nav-bar">
      <div class="wrap">
        <span class="brand">
          <span class="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none">
              <circle cx="12" cy="12" r="8.4" stroke="currentColor" stroke-width="1.9" />
              <rect
                x="8.7" y="8.7" width="6.6" height="6.6" rx="1.6"
                transform="rotate(45 12 12)" stroke="currentColor" stroke-width="1.9"
              />
            </svg>
          </span>
          <span class="brand-name">途灵 TripMind</span>
        </span>

        <nav class="nav">
          <a href="#modes">规划方式</a>
          <a href="#why">为什么准</a>
          <a href="#agents">多智能体</a>
          <a href="#start">开始使用</a>
        </nav>

        <div class="nav-actions">
          <router-link to="/login" class="btn btn-ghost">登录</router-link>
          <router-link to="/register" class="btn btn-primary">免费开始</router-link>
        </div>
      </div>
    </header>

    <!-- ============ 2. Hero ============ -->
    <section class="hero">
      <div class="wrap hero-grid">
        <div class="hero-copy">
          <p class="eyebrow reveal" style="--i: 0">AI 旅行规划</p>

          <h1 class="reveal" style="--i: 1">
            把想去的地方，<br />交给途灵 <em>排成行程</em>
          </h1>

          <p class="lede reveal" style="--i: 2">
            一句话说出你的旅行想法，途灵自动生成逐日行程 ——
            景点、天气、酒店、预算一次给全。景点坐标来自高德实时查询，
            而不是让模型凭记忆编造。
          </p>

          <div class="cta-row reveal" style="--i: 3">
            <router-link to="/register" class="btn btn-primary btn-lg">免费开始规划</router-link>
            <a class="btn btn-quiet btn-lg" href="#modes">看看它怎么做 ↓</a>
          </div>

          <ul class="chips reveal" style="--i: 4">
            <li class="chip"><i aria-hidden="true"></i>4 个 Agent 分工</li>
            <li class="chip"><i aria-hidden="true"></i>高德实时坐标</li>
            <li class="chip"><i aria-hidden="true"></i>导出图片与 PDF</li>
          </ul>

          <div class="mini-grid">
            <a class="mini reveal" style="--i: 5" href="#agents">
              <h3>
                多智能体工作流
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" stroke-width="2.2" aria-hidden="true">
                  <path d="M5 12h14M13 6l6 6-6 6" />
                </svg>
              </h3>
              <p>景点、天气、酒店由三个专职 Agent 分头查，最后统一收敛成结构化行程。</p>
              <span>看看管线</span>
            </a>

            <a class="mini reveal" style="--i: 6" href="#why">
              <h3>
                知识库接入
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" stroke-width="2.2" aria-hidden="true">
                  <path d="M5 12h14M13 6l6 6-6 6" />
                </svg>
              </h3>
              <p>上传攻略后，门票、预约、闭馆日这些容易过时的信息会带着出处一起进提示词。</p>
              <span>了解知识库</span>
            </a>
          </div>
        </div>

        <!-- 产品预览：用 HTML 搭出来，不用假截图 ——
             假截图一旦和真实界面不一致，比没有更糟。 -->
        <div
          class="preview reveal"
          style="--i: 3"
          role="img"
          aria-label="途灵产品界面预览：输入一句话需求，生成逐日行程"
        >
          <div class="preview-bar">
            <span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>
            <b>途灵 · 智能规划</b>
          </div>

          <div class="preview-body">
            <p class="bubble-user">十月想去成都待 3 天，喜欢历史文化和本地小吃，不想太赶</p>

            <div class="ai-row">
              <span class="ai-avatar" aria-hidden="true">途</span>
              <p class="ai-text">
                <span class="name">途灵 AI</span>
                已经为你排好一条节奏舒缓的市区线，景点之间都在地铁可达范围内。
              </p>
            </div>

            <div class="plan-card">
              <div class="plan-head">
                <b>成都 文化美食线 · 3 天</b>
                <span class="pill">轻松节奏</span>
              </div>
              <div v-for="day in PREVIEW_DAYS" :key="day.d" class="plan-row">
                <span class="d">{{ day.d }}</span>
                <span class="t">
                  {{ day.route }}
                  <small>{{ day.note }}</small>
                </span>
              </div>
            </div>

            <p class="typing">
              <span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>
              正在补全每日交通与预算拆分…
            </p>
          </div>
        </div>
      </div>
    </section>

    <!-- ============ 3. 两种规划方式 ============ -->
    <section id="modes" class="section">
      <div class="wrap">
        <div class="section-head">
          <p class="eyebrow reveal">两种方式</p>
          <h2 class="reveal" style="--i: 1">想全权交给 AI，<br />还是亲自参与每一步？</h2>
          <p class="reveal" style="--i: 2">两种模式共用同一套数据与降级策略，随时可以来回切换。</p>
        </div>

        <div class="grid-2">
          <div class="mode reveal" style="--i: 0">
            <span class="no">模式一 · 全自动</span>
            <h3>一句话直接出行程</h3>
            <p>在首页写一句想去哪、几天、什么偏好，剩下的交给管线。</p>
            <ul>
              <li>自动补全日期、天数与偏好，不用填长表单</li>
              <li>景点、天气、酒店分头查询后统一收敛</li>
              <li>生成完直接落库，链接可分享、可导出</li>
            </ul>
          </div>

          <div class="mode reveal" style="--i: 1">
            <span class="no">模式二 · 交互式</span>
            <h3>逐项确认后再生成</h3>
            <p>先确认日期、交通、住宿和偏好，再开始生成。</p>
            <ul>
              <li>创建页逐项设置，避免「生成完才发现日期错了」</li>
              <li>生成后仍可增删景点、调整顺序并保存</li>
              <li>改动不会影响已记录的成本统计</li>
            </ul>
          </div>
        </div>
      </div>
    </section>

    <!-- ============ 4. 为什么值得信 ============ -->
    <section id="why" class="section section-alt">
      <div class="wrap">
        <div class="section-head">
          <p class="eyebrow reveal">为什么值得信</p>
          <h2 class="reveal" style="--i: 1">
            AI 写的行程，<br />最容易出问题的地方我们单独处理了
          </h2>
          <p class="reveal" style="--i: 2">
            「编造一个不存在的景点」「门票价格想当然」「明明闭馆还安排进去」——
            这些不是模型不够聪明，是缺了实时数据与出处。
          </p>
        </div>

        <div class="grid-4">
          <div v-for="(card, i) in WHY_CARDS" :key="card.title" class="card reveal" :style="{ '--i': i }">
            <span class="kicker">{{ card.kicker }}</span>
            <h3>{{ card.title }}</h3>
            <p>{{ card.body }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- ============ 5. 多智能体管线 ============ -->
    <section id="agents" class="section">
      <div class="wrap">
        <div class="section-head">
          <p class="eyebrow reveal">管线</p>
          <h2 class="reveal" style="--i: 1">四个 Agent，四件不同的事</h2>
          <p class="reveal" style="--i: 2">
            顺序固定、职责单一：前三个各自去查事实，最后一个把它们收敛成结构化的行程 JSON。
          </p>
        </div>

        <div class="grid-4">
          <div v-for="(step, i) in PIPELINE" :key="step.no" class="fact reveal" :style="{ '--i': i }">
            <div class="num">{{ step.no }}</div>
            <div class="label">{{ step.label }}</div>
          </div>
        </div>
      </div>
    </section>

    <!-- ============ 6. 收尾 CTA ============ -->
    <section id="start" class="section section-tight">
      <div class="wrap">
        <div class="final reveal">
          <h2>现在就把下一趟行程排出来</h2>
          <p>注册一个账号，写一句话，两分钟后你会拿到一份可以直接出发的逐日行程。</p>
          <div class="cta-row cta-center">
            <router-link to="/register" class="btn btn-white btn-lg">免费开始</router-link>
            <router-link to="/login" class="btn btn-outline btn-lg">已有账号，登录</router-link>
          </div>
        </div>
      </div>
    </section>

    <!-- ============ 7. 页脚 ============ -->
    <footer class="foot">
      <div class="wrap">
        <span class="copy">途灵 TripMind</span>
        <nav>
          <a href="#modes">规划方式</a>
          <a href="#why">为什么准</a>
          <a href="#agents">多智能体</a>
          <router-link to="/login">登录</router-link>
        </nav>
      </div>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue';

/**
 * 落地页 —— 打开站点看到的第一个页面。
 *
 * 设计语言对齐用户给的参考站（helloaitrip.cn）。用脚本抓它的 CSS 之后
 * 发现一件事：**它的中性层和本项目 `tokens.css` 本来就是同一批值** ——
 * 底色 `#f4f5f9`、正文 `#1e293b`、次要 `#64748b`、三级 `#94a3b8`、
 * 分隔线 `#edeff5`、卡片圆角 16px，全部同值。
 *
 * 所以这一页**直接读本项目的设计令牌**（`--brand-*` / `--text-*` / `--line-*`
 * / `--radius-*` / `--shadow-*`），而不是在 SFC 里再抄一份色值。
 * 好处是以后调品牌色只需要改 `tokens.css` 一处，落地页和应用内页一起变 ——
 * 抄一份的话它们迟早会漂开。
 *
 * 参考站与本项目唯一的实质差别是主色相（参考站靛紫、本项目青蓝）。
 * 按用户决定：**用本项目现有的青蓝色**，不跟随参考站。
 */

const PREVIEW_DAYS = [
  { d: 'D1', route: '宽窄巷子 → 人民公园', note: '鹤鸣茶社喝盖碗茶，傍晚转锦里' },
  { d: 'D2', route: '成都博物馆 → 杜甫草堂', note: '博物馆需提前预约，周一闭馆' },
  { d: 'D3', route: '青羊宫 → 文殊院', note: '周边小吃 3 处推荐，含人均价格' }
];

const WHY_CARDS = [
  {
    kicker: '实时数据',
    title: '坐标来自高德',
    body: '景点名称与坐标由地图服务实时查询，不是模型生成的文本。查不到就如实留空，不补一个假坐标。'
  },
  {
    kicker: '可追溯',
    title: '知识出处可见',
    body: '行程里「需提前 7 天预约」这类结论会标明来自哪份攻略的哪一节，你能自己核对。'
  },
  {
    kicker: '不装作没事',
    title: '降级就明说',
    body: '数据不完整时，行程会被标成「部分降级」并写明原因，而不是给一份看似完整的空框架。'
  },
  {
    kicker: '成本透明',
    title: '每次生成花了多少',
    body: 'tokens 与费用逐条记在历史里，累计花费随时可查 —— 不用猜「跑一次多少钱」。'
  }
];

const PIPELINE = [
  { no: '01', label: '景点搜索 · 查 POI 与分类' },
  { no: '02', label: '天气查询 · 按日期取预报' },
  { no: '03', label: '酒店推荐 · 按预算档位' },
  { no: '04', label: '行程整合 · 收敛为结构化 JSON' }
];

/* ---------------- 滚动进入：模块「缓慢推出」 ----------------
 *
 * 三个参数一起决定手感，**只调其中一个会坏**：
 *   时长 1000ms（长） + 位移 26px（小） + cubic-bezier(.16,1,.3,1)（强减速）
 * 位移大了像弹窗、时长短了像闪烁，只有「慢 + 小位移」才是"缓慢推出"。
 * 错峰用 --i 算延迟（60ms 一档），同一排卡片依次出现而不是一起蹦。
 *
 * 用 IntersectionObserver 而不是监听 scroll：后者每帧都要读所有元素的
 * 位置，长页面上会掉帧。而且这里**触发一次就注销** —— 滚回来看不重播，
 * 重播会让人觉得页面在抖。
 */
let observer: IntersectionObserver | null = null;

onMounted(() => {
  const items = Array.from(document.querySelectorAll<HTMLElement>('.landing .reveal'));

  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduced || !('IntersectionObserver' in window)) {
    // 关掉动效偏好时**不要**留下 opacity:0 的元素 —— 那会是一页空白。
    items.forEach((el) => el.classList.add('in'));
    return;
  }

  observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('in');
        observer?.unobserve(entry.target);
      });
    },
    {
      // 提前 12% 视口高度触发：等元素完全进视口才开始会慢半拍，
      // 用户在滚动过程中就该看到它"推出来"。
      rootMargin: '0px 0px -12% 0px',
      threshold: 0.01
    }
  );

  items.forEach((el) => observer?.observe(el));
});

onBeforeUnmount(() => {
  observer?.disconnect();
  observer = null;
});
</script>

<style scoped>
/* ==========================================================================
   落地页局部令牌
   --------------------------------------------------------------------------
   只定义**项目令牌里没有的**那几项（水洗背景、顶部公告条、推入节奏）。
   其余一律用全局令牌，不重复定义色值。
   ========================================================================== */
.landing {
  --wash-1: color-mix(in srgb, var(--brand-50) 70%, #fff);
  --wash-2: color-mix(in srgb, var(--brand-100) 52%, #fff);
  /* 项目的 --ease-out 是 cubic-bezier(.4,0,.2,1)（Material 标准曲线），
     减速不够"拖尾"，用在小动效上没问题，但 1 秒的位移会显得发涩。
     这里单独给一条更弱的起始加速度。 */
  --reveal-ease: cubic-bezier(0.16, 1, 0.3, 1);
  --reveal-dur: 1000ms;
  --reveal-shift: 26px;

  background: var(--bg-page);
  color: var(--text-1);
  min-height: 100dvh;
}

.wrap {
  max-width: var(--container);
  margin: 0 auto;
  padding-inline: var(--pad-page);
}

/* ---------------- 0. 公告条 ---------------- */
.notice {
  background: var(--brand-50);
  color: var(--brand-700);
  font-size: var(--fs-caption);
  line-height: 1;
}
.notice .wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding-block: 9px;
  flex-wrap: wrap;
}
.notice strong {
  font-weight: var(--fw-semibold);
}
.notice .dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--brand-500);
  flex: none;
}
.notice a {
  text-decoration: underline;
  text-underline-offset: 3px;
  opacity: 0.85;
}
.notice a:hover {
  opacity: 1;
}

/* ---------------- 1. 吸顶导航 ---------------- */
.nav-bar {
  position: sticky;
  top: 0;
  z-index: 40;
  background: color-mix(in srgb, var(--bg-surface) 82%, transparent);
  backdrop-filter: blur(14px) saturate(1.6);
  border-bottom: 1px solid var(--line-1);
}
.nav-bar .wrap {
  display: flex;
  align-items: center;
  gap: var(--space-6);
  height: 64px;
}

.brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: none;
}
.brand-mark {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: var(--brand-gradient);
  display: grid;
  place-items: center;
  color: #fff;
  flex: none;
  box-shadow: var(--shadow-brand);
}
.brand-name {
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
  letter-spacing: var(--ls-tight);
  white-space: nowrap;
}

.nav {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}
.nav a {
  padding: 8px 14px;
  border-radius: var(--radius-pill);
  font-size: var(--fs-body);
  color: var(--text-2);
  transition: color var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out);
  white-space: nowrap;
}
.nav a:hover {
  color: var(--brand-600);
  background: var(--brand-50);
}

.nav-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

/* ---------------- 按钮 ---------------- */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  height: 40px;
  padding-inline: 20px;
  border-radius: var(--radius-pill);
  font-size: var(--fs-body);
  font-weight: var(--fw-medium);
  white-space: nowrap;
  text-decoration: none;
  transition: transform var(--dur-base) var(--reveal-ease),
    background-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-base) var(--ease-out), color var(--dur-fast) var(--ease-out);
}
.btn:active {
  transform: translateY(1px);
}
.btn-ghost {
  color: var(--text-2);
}
.btn-ghost:hover {
  color: var(--text-1);
  background: var(--bg-sunken);
}
.btn-primary {
  background: var(--brand-gradient);
  color: #fff;
  box-shadow: var(--shadow-brand);
}
.btn-primary:hover {
  box-shadow: 0 6px 20px var(--brand-a34);
}
.btn-quiet {
  color: var(--text-2);
}
.btn-quiet:hover {
  background: var(--bg-sunken);
  color: var(--text-1);
}
.btn-lg {
  height: 48px;
  padding-inline: 26px;
  font-size: var(--fs-h3);
}
.btn-white {
  background: #fff;
  color: var(--brand-700);
  box-shadow: var(--shadow-3);
}
.btn-white:hover {
  transform: translateY(-1px);
}
.btn-outline {
  border: 1px solid rgba(255, 255, 255, 0.55);
  color: #fff;
}
.btn-outline:hover {
  background: rgba(255, 255, 255, 0.14);
}

/* ---------------- 2. Hero ---------------- */
.hero {
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(900px 520px at 78% -8%, var(--wash-2), transparent 62%),
    radial-gradient(700px 460px at 8% 4%, var(--wash-1), transparent 60%);
}
/* 底部人字形切边 —— 参考站那条折线边界。
   用两层错位的渐变拼出来而不是图片：换品牌色时自动跟着变。 */
.hero::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 74px;
  background:
    linear-gradient(135deg, var(--wash-1) 25%, transparent 25%) -32px 0 / 64px 64px repeat-x,
    linear-gradient(-135deg, var(--wash-2) 25%, transparent 25%) -32px 0 / 64px 64px repeat-x;
  opacity: 0.75;
  pointer-events: none;
}
.hero-grid {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.02fr);
  gap: 56px;
  align-items: center;
  padding-block: 88px 116px;
}

.eyebrow {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--fs-caption);
  letter-spacing: 0.06em;
  color: var(--brand-600);
  font-weight: var(--fw-medium);
  margin: 0 0 20px;
}
.eyebrow::before {
  content: '';
  width: 26px;
  height: 1px;
  background: currentColor;
  opacity: 0.55;
}

h1 {
  font-size: clamp(40px, 4.4vw, 60px);
  line-height: 1.16;
  font-weight: var(--fw-bold);
  letter-spacing: -0.035em;
  margin: 0 0 22px;
}
h1 em {
  font-style: normal;
  background: var(--brand-gradient);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.lede {
  font-size: var(--fs-h3);
  line-height: 1.85;
  color: var(--text-2);
  max-width: 30em;
  margin: 0 0 34px;
}
.cta-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-bottom: 30px;
}
.cta-center {
  justify-content: center;
  margin-bottom: 0;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin: 0 0 38px;
  padding: 0;
  list-style: none;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding-inline: 14px;
  border-radius: var(--radius-pill);
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  font-size: var(--fs-caption);
  color: var(--text-2);
  box-shadow: var(--shadow-1);
}
.chip i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--brand-500);
  flex: none;
}

.mini-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
}
.mini {
  display: block;
  padding: 18px;
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  box-shadow: var(--shadow-1);
  text-decoration: none;
  color: inherit;
  transition: transform var(--dur-base) var(--reveal-ease),
    box-shadow var(--dur-base) var(--ease-out), border-color var(--dur-base) var(--ease-out);
}
.mini:hover {
  transform: translateY(-3px);
  box-shadow: var(--shadow-4);
  border-color: var(--line-2);
}
.mini h3 {
  font-size: var(--fs-body);
  font-weight: var(--fw-semibold);
  margin: 0 0 7px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.mini h3 svg {
  color: var(--text-3);
  transition: transform var(--dur-base) var(--reveal-ease);
}
.mini:hover h3 svg {
  transform: translateX(3px);
}
.mini p {
  font-size: var(--fs-caption);
  line-height: 1.7;
  color: var(--text-2);
  margin: 0;
}
.mini span {
  display: inline-block;
  margin-top: 12px;
  font-size: var(--fs-caption);
  font-weight: var(--fw-medium);
  color: var(--brand-600);
}

/* ---- 产品预览卡 ---- */
.preview {
  border-radius: var(--radius-xl);
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  box-shadow: var(--shadow-4);
  overflow: hidden;
}
.preview-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 12px var(--space-4);
  border-bottom: 1px solid var(--line-1);
  background: var(--bg-sunken);
}
.preview-bar .dots {
  display: flex;
  gap: 6px;
}
.preview-bar .dots i {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--line-2);
}
.preview-bar .dots i:nth-child(1) {
  background: #f2b8ae;
}
.preview-bar .dots i:nth-child(2) {
  background: #f2d9a8;
}
.preview-bar .dots i:nth-child(3) {
  background: #bfe3c8;
}
.preview-bar b {
  margin-left: 6px;
  font-size: var(--fs-caption);
  font-weight: var(--fw-medium);
  color: var(--text-2);
}
.preview-body {
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.bubble-user {
  align-self: flex-end;
  max-width: 82%;
  margin: 0;
  padding: 11px 15px;
  border-radius: 14px 14px 3px 14px;
  background: var(--brand-gradient);
  color: #fff;
  font-size: var(--fs-caption);
  line-height: 1.65;
}
.ai-row {
  display: flex;
  gap: var(--space-2);
  align-items: flex-start;
}
.ai-avatar {
  width: 26px;
  height: 26px;
  border-radius: var(--radius-sm);
  background: var(--brand-gradient);
  display: grid;
  place-items: center;
  color: #fff;
  font-size: var(--fs-micro);
  font-weight: var(--fw-semibold);
  flex: none;
}
.ai-text {
  font-size: var(--fs-caption);
  line-height: 1.7;
  margin: 0;
}
.ai-text .name {
  font-weight: var(--fw-semibold);
  margin-right: 6px;
}

.plan-card {
  border: 1px solid var(--line-1);
  border-radius: var(--radius-md);
  background: var(--bg-surface);
  overflow: hidden;
}
.plan-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: 11px 14px;
  border-bottom: 1px solid var(--line-1);
}
.plan-head b {
  font-size: var(--fs-caption);
  font-weight: var(--fw-semibold);
}
.pill {
  font-size: var(--fs-micro);
  padding: 3px 9px;
  border-radius: var(--radius-pill);
  background: var(--brand-50);
  color: var(--brand-700);
  font-weight: var(--fw-medium);
  white-space: nowrap;
}
.plan-row {
  display: flex;
  gap: 11px;
  padding: 11px 14px;
  border-bottom: 1px solid var(--divider);
}
.plan-row:last-child {
  border-bottom: 0;
}
.plan-row .d {
  font-family: var(--font-num);
  font-size: var(--fs-micro);
  color: var(--brand-600);
  background: var(--brand-50);
  border-radius: var(--radius-xs);
  padding: 2px 6px;
  height: fit-content;
  flex: none;
  font-weight: var(--fw-semibold);
}
.plan-row .t {
  font-size: var(--fs-caption);
  line-height: 1.6;
}
.plan-row .t small {
  display: block;
  color: var(--text-2);
  font-size: var(--fs-caption);
  margin-top: 2px;
}

.typing {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--fs-caption);
  color: var(--text-2);
  margin: 0;
}
.typing .dots {
  display: inline-flex;
  gap: 3px;
}
.typing .dots i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--text-3);
  animation: bounce 1.1s infinite ease-in-out;
}
.typing .dots i:nth-child(2) {
  animation-delay: 0.15s;
}
.typing .dots i:nth-child(3) {
  animation-delay: 0.3s;
}
@keyframes bounce {
  0%, 60%, 100% {
    transform: translateY(0);
    opacity: 0.45;
  }
  30% {
    transform: translateY(-4px);
    opacity: 1;
  }
}

/* ---------------- 3. 通用 section ---------------- */
.section {
  padding-block: 96px;
}
.section-alt {
  background: var(--bg-surface);
  border-block: 1px solid var(--line-1);
}
.section-tight {
  padding-top: 0;
}
.section-head {
  max-width: 44em;
  margin-bottom: 52px;
}
.section-head .eyebrow {
  margin-bottom: var(--space-4);
}
h2 {
  font-size: clamp(28px, 3vw, 38px);
  line-height: 1.28;
  font-weight: var(--fw-bold);
  letter-spacing: -0.03em;
  margin: 0 0 var(--space-4);
}
.section-head > p:last-child {
  font-size: var(--fs-h3);
  line-height: 1.85;
  color: var(--text-2);
  margin: 0;
}

.grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-5);
}
.grid-4 {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-4);
}

.card {
  padding: 26px;
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  box-shadow: var(--shadow-1);
}
.card h3 {
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
  margin: 0 0 10px;
}
.card p {
  font-size: var(--fs-body);
  line-height: 1.8;
  color: var(--text-2);
  margin: 0;
}
.card .kicker {
  display: inline-block;
  font-size: var(--fs-micro);
  font-weight: var(--fw-semibold);
  letter-spacing: 0.04em;
  color: var(--brand-600);
  margin-bottom: 12px;
}

.mode {
  padding: 30px;
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
  box-shadow: var(--shadow-1);
}
.mode .no {
  font-family: var(--font-num);
  font-size: var(--fs-micro);
  font-weight: var(--fw-semibold);
  color: var(--brand-600);
  letter-spacing: 0.08em;
}
.mode h3 {
  font-size: 20px;
  font-weight: var(--fw-semibold);
  margin: 12px 0 10px;
}
.mode p {
  font-size: var(--fs-body);
  line-height: 1.8;
  color: var(--text-2);
  margin: 0 0 var(--space-4);
}
.mode ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 9px;
}
.mode li {
  font-size: var(--fs-caption);
  color: var(--text-2);
  display: flex;
  gap: 9px;
  align-items: flex-start;
}
.mode li::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--brand-500);
  margin-top: 9px;
  flex: none;
}

.fact {
  padding: 22px;
  border-radius: var(--radius-md);
  background: var(--bg-surface);
  border: 1px solid var(--line-1);
}
.fact .num {
  font-family: var(--font-num);
  font-size: 26px;
  font-weight: var(--fw-semibold);
  color: var(--text-1);
  letter-spacing: -0.02em;
  line-height: 1.2;
}
.fact .label {
  font-size: var(--fs-caption);
  color: var(--text-2);
  margin-top: 4px;
}

/* ---------------- 收尾 CTA ---------------- */
.final {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-xl);
  background: var(--brand-gradient);
  color: #fff;
  padding: 64px 56px;
  text-align: center;
  box-shadow: 0 24px 60px -24px var(--brand-a34);
}
.final::before {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(600px 320px at 20% 0%, rgba(255, 255, 255, 0.26), transparent 60%);
  pointer-events: none;
}
.final h2 {
  position: relative;
  color: #fff;
  margin-bottom: var(--space-4);
}
.final > p {
  position: relative;
  font-size: var(--fs-h3);
  opacity: 0.92;
  max-width: 34em;
  margin: 0 auto 32px;
}
.final .btn {
  position: relative;
}

/* ---------------- 页脚 ---------------- */
.foot {
  border-top: 1px solid var(--line-1);
  padding-block: 40px;
  margin-top: var(--space-6);
}
.foot .wrap {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-5);
  flex-wrap: wrap;
}
.foot nav {
  display: flex;
  gap: 22px;
  font-size: var(--fs-caption);
  color: var(--text-2);
}
.foot nav a:hover {
  color: var(--text-1);
}
.foot .copy {
  font-size: var(--fs-caption);
  color: var(--text-3);
}

/* ==========================================================================
   滚动进入：模块「缓慢推出」
   ========================================================================== */
.reveal {
  opacity: 0;
  transform: translateY(var(--reveal-shift));
  transition: opacity var(--reveal-dur) var(--reveal-ease),
    transform var(--reveal-dur) var(--reveal-ease);
  transition-delay: calc(var(--i, 0) * 60ms);
  will-change: opacity, transform;
}
.reveal.in {
  opacity: 1;
  transform: none;
}

/* 关掉动效偏好时直接静态呈现 —— 用 !important 压掉内联的 --i 延迟 */
@media (prefers-reduced-motion: reduce) {
  .reveal {
    opacity: 1 !important;
    transform: none !important;
    transition: none !important;
  }
  .typing .dots i {
    animation: none;
  }
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 991px) {
  .wrap {
    padding-inline: var(--pad-page-md);
  }
  .hero-grid {
    grid-template-columns: 1fr;
    gap: 44px;
    padding-block: 64px 96px;
  }
  .grid-4 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .nav {
    display: none;
  }
}

@media (max-width: 575px) {
  .wrap {
    padding-inline: var(--pad-page-sm);
  }
  .section {
    padding-block: 64px;
  }
  .nav-bar .wrap {
    height: 58px;
    gap: var(--space-3);
  }
  .brand-name {
    display: none;
  }
  .nav-actions .btn {
    height: 36px;
    padding-inline: 14px;
  }
  h1 {
    font-size: 34px;
  }
  .mini-grid,
  .grid-2,
  .grid-4 {
    grid-template-columns: 1fr;
  }
  .cta-row .btn {
    flex: 1 1 auto;
  }
  .final {
    padding: 44px 24px;
  }
}
</style>
