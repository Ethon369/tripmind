<template>
  <a-layout class="app-shell">
    <a-layout-header class="app-header">
      <div class="header-inner">
        <router-link to="/" class="brand" aria-label="途灵 TripMind 首页">
          <!-- 项目此前没有标识，用一只 emoji 顶着。这里补一个简洁的图形标：
               紫色渐变圆角方块 + 一条上升的路线。
               不承认真实品牌资产的场合，我宁可画一个中性的几何标，
               也不用彩色方块写品牌名糊弄。 -->
          <span class="brand-mark" aria-hidden="true">
            <!-- 指南针：外圈圆环 + 内嵌斜置菱形指针。
                 上一版是"上升折线 + 圆点"，读起来像业绩增长曲线，不像旅行。
                 指南针的语义直白得多，而且在中性色块上辨识度更高。 -->
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none">
              <circle cx="12" cy="12" r="8.4" stroke="currentColor" stroke-width="1.9" />
              <rect
                x="8.7"
                y="8.7"
                width="6.6"
                height="6.6"
                rx="1.6"
                transform="rotate(45 12 12)"
                stroke="currentColor"
                stroke-width="1.9"
              />
            </svg>
          </span>
          <span class="brand-name">途灵 TripMind</span>
        </router-link>

        <nav class="nav" aria-label="主导航">
          <router-link v-for="item in NAV_ITEMS" :key="item.to" :to="item.to" class="nav-link">
            {{ item.label }}
          </router-link>
        </nav>
      </div>
    </a-layout-header>

    <a-layout-content class="app-content">
      <!--
        页面过渡用 mode="out-in"：先出后进。
        避免两页同时存在把页面撑高、以及过渡期间滚动位置错乱。
      -->
      <router-view v-slot="{ Component }">
        <transition name="page" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </a-layout-content>

    <a-layout-footer class="app-footer">
      <div class="footer-inner">
        <span class="footer-brand">途灵 TripMind</span>
        <span>基于 HelloAgents 框架</span>
      </div>
    </a-layout-footer>
  </a-layout>
</template>

<script setup lang="ts">
/**
 * 导航项。
 *
 * 保持无图标：三项文字导航已足够清晰，
 * 而这套视觉的轻盈感来自留白和色彩，不来自图标堆叠。
 */
const NAV_ITEMS = [
  { to: '/', label: '首页' },
  { to: '/history', label: '历史行程' },
  { to: '/knowledge', label: '知识库' },
] as const;
</script>

<style scoped>
.app-shell {
  min-height: 100dvh;
  background: var(--bg-page);
}

/* ---------------- 顶栏 ----------------
 * 白色 + 一条极浅的下边线。不做毛玻璃也不做深色，
 * 因为它只是承载导航，不该抢内容区"白卡片"的注意力。
 */
.app-header {
  height: var(--header-h);
  line-height: normal;
  padding: 0;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--line-1);
  position: sticky;
  top: 0;
  z-index: 100;
}

.header-inner {
  height: 100%;
  /* 撑满视口宽度，品牌贴左、导航贴右。
     原来这里限了 `max-width: 1120px` 并居中 —— 在宽屏上两侧各留出几百像素空白，
     整条导航看起来是"缩在中间"的一小撮，而不是一条横贯的栏。
     内容区仍然用 --container 限宽（正文需要舒适行长），但导航栏不需要。 */
  padding-inline: var(--pad-page);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
}

.brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--touch-min);
  text-decoration: none;
  white-space: nowrap;
}

/* 渐变圆角方块：这套视觉里唯一的"实色品牌块" */
.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  /* 圆角比全局的 --radius-sm 稍大一档：这块是品牌标识，
     更接近 squircle 的轮廓在导航栏里更"立得住" */
  border-radius: 9px;
  background: var(--brand-gradient);
  color: #fff;
  box-shadow: var(--shadow-brand);
  flex-shrink: 0;
}

.brand-name {
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
  letter-spacing: var(--ls-tight);
  color: var(--text-1);
}

.brand:hover .brand-name {
  color: var(--brand-600);
}

/* ---------------- 导航 ----------------
 * 当前项用**浅紫底药丸** —— 这套视觉里"选中"的统一表达。
 */
.nav {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.nav-link {
  display: inline-flex;
  align-items: center;
  min-height: 36px;
  padding: 0 var(--space-4);
  border-radius: var(--radius-pill);
  font-size: var(--fs-body);
  font-weight: var(--fw-medium);
  color: var(--text-2);
  text-decoration: none;
  white-space: nowrap;
  transition: color var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out);
}

.nav-link:hover {
  color: var(--brand-600);
  background: var(--brand-50);
}

/* 用 exact-active 而不是 active：否则访问 /result/xxx 时「首页」也会亮，
   因为 / 是每个路径的前缀。 */
.nav-link.router-link-exact-active {
  color: var(--brand-600);
  background: var(--bg-tint);
  font-weight: var(--fw-semibold);
}

/* ---------------- 内容 ---------------- */
.app-content {
  background: var(--bg-page);
  min-height: calc(100dvh - var(--header-h) - 68px);
}

/* ---------------- 页脚 ---------------- */
.app-footer {
  padding: 0;
  background: transparent;
}

.footer-inner {
  max-width: var(--container);
  margin: 0 auto;
  padding: var(--space-6) var(--pad-page);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
  color: var(--text-3);
  font-size: var(--fs-caption);
}

.footer-brand {
  font-weight: var(--fw-semibold);
  color: var(--text-2);
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 991px) {
  .header-inner,
  .footer-inner {
    padding-inline: var(--pad-page-md);
  }
}

@media (max-width: 575px) {
  .app-header {
    height: var(--header-h-mobile);
  }

  .header-inner,
  .footer-inner {
    padding-inline: var(--pad-page-sm);
  }

  .brand-mark {
    width: 26px;
    height: 26px;
  }

  .brand-name {
    font-size: var(--fs-body);
  }

  .nav {
    gap: 0;
  }

  .nav-link {
    min-height: var(--touch-min);
    padding: 0 var(--space-3);
    font-size: var(--fs-caption);
  }

  .app-content {
    min-height: calc(100dvh - var(--header-h-mobile) - 68px);
  }

  .footer-inner {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-1);
  }
}
</style>
