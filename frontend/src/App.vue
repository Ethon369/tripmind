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

          <!-- 管理口令的入口放在导航尾部，**全局只有一个**。
               受保护的写操作分散在「历史行程」（改写/删除行程）和
               「知识库」（灌库/删攻略/切开关）两个页面 —— 按页面各放一个，
               会让人以为它们互不相干的两套东西。
               样式上刻意不跟三个导航项一样做药丸底：它是"维护入口"，
               不是第四个页面，视觉层级要低一档。 -->
          <button
            type="button"
            class="admin-entry"
            :class="{ 'is-set': adminTokenSet }"
            :aria-label="adminTokenSet ? '管理口令（已设置）' : '管理口令（未设置）'"
            @click="openTokenDialog"
          >
            <span>管理口令</span>
            <!-- 已设置时点一个小绿点。不做成"已设置"三个字：
                 它平时不需要被读到，只要"扫一眼知道有没有"就够了。 -->
            <span v-if="adminTokenSet" class="admin-dot" aria-hidden="true"></span>
          </button>
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

  <!-- 管理口令弹窗。
       放在 a-layout 外面：antd 的 Modal 默认 teleport 到 body，
       留在 layout 里只是多一个不渲染的占位节点，反而干扰 flex 布局。 -->
  <a-modal v-model:open="tokenOpen" title="管理口令" :width="460" :centered="true">
    <p class="token-lead">
      知识库的灌库与删除、RAG 开关，以及历史行程的改写与删除，都会改动数据。
      这些接口需要一道共享口令，避免任何人打开网页就能误删资料。
    </p>

    <a-input-password
      v-model:value="tokenDraft"
      placeholder="粘贴管理员分配的口令"
      autocomplete="off"
      allow-clear
      @press-enter="saveToken"
    />

    <p class="token-foot">
      口令只存在当前标签页，关掉浏览器即失效；不会写进磁盘，也不会出现在网址里。
      生成行程、检索、查看历史这些读操作不需要口令。
    </p>

    <template #footer>
      <a-button v-if="adminTokenSet" class="token-clear" danger type="text" @click="clearToken">
        清除口令
      </a-button>
      <a-button @click="tokenOpen = false">取消</a-button>
      <a-button type="primary" @click="saveToken">保存</a-button>
    </template>
  </a-modal>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { message } from 'ant-design-vue';
import { getAdminToken, hasAdminToken, setAdminToken } from '@/services/api';

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

// ---------------- 管理口令 ----------------
//
// 背景见 services/api.ts 里「管理口令」那一段：后端的写接口加了一道共享口令，
// 前端负责把它带上。这里只做一件事 —— 提供一个让用户填口令的地方。

/**
 * 是否已设置口令。
 *
 * hasAdminToken() 读的是 sessionStorage，那不是响应式数据源，
 * 所以用一个 ref 镜像它。写入点只有下面两个函数，
 * 不存在"别处改了它、这里不知道"的情况。
 * sessionStorage 是每个标签页独立的，也不需要监听 storage 事件。
 */
const adminTokenSet = ref(hasAdminToken());
const tokenOpen = ref(false);
const tokenDraft = ref('');

function openTokenDialog() {
  // 打开时回填：用户点进来多半是想改，先让他看见当前是什么。
  // input-password 默认打码，需要核对时点右侧小眼睛展开。
  tokenDraft.value = getAdminToken();
  tokenOpen.value = true;
}

function saveToken() {
  const had = adminTokenSet.value;
  // 传空串等于清除 —— 所以"不填直接点保存"不会留下一个空口令，
  // 而是显式地清掉，语义上没有歧义。
  setAdminToken(tokenDraft.value);
  adminTokenSet.value = hasAdminToken();
  tokenOpen.value = false;

  if (!adminTokenSet.value) {
    message.info(had ? '已清除管理口令，写操作将无法进行' : '口令为空，未做改动');
  } else {
    message.success(had ? '管理口令已更新' : '管理口令已保存');
  }
}

function clearToken() {
  setAdminToken('');
  adminTokenSet.value = false;
  tokenDraft.value = '';
  tokenOpen.value = false;
  message.info('已清除管理口令，写操作将无法进行');
}
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

/* ---------------- 管理口令入口 ----------------
 * 从属于导航，但视觉层级比三个页面低一档：它是「维护入口」，不是第四个页面。
 * 用 button 而不是 router-link —— 它打开弹窗，不切换路由。
 */
.admin-entry {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 36px;
  /* 和第三个导航项之间留出距离，再由 ::before 画一条竖线：
     "从这里开始是另一种入口"。 */
  margin-left: var(--space-3);
  padding: 0 var(--space-4);
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  font-family: inherit;
  font-size: var(--fs-caption);
  font-weight: var(--fw-medium);
  color: var(--text-3);
  cursor: pointer;
  white-space: nowrap;
  transition: color var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out);
}

/* 竖分隔线用伪元素而不是 border-left：圆角药丸配上左侧 border，
   悬停变底色时左边缘会出现一个直角缺口。 */
.admin-entry::before {
  content: '';
  width: 1px;
  height: 16px;
  background: var(--line-1);
}

.admin-entry:hover {
  color: var(--text-1);
  background: var(--bg-sunken);
}

/* 已设置口令时变成品牌色 + 一个绿点。改色是因为
   "写操作现在可用"是个值得一眼看到的状态。 */
.admin-entry.is-set {
  color: var(--brand-600);
}

.admin-entry.is-set:hover {
  background: var(--brand-50);
}

.admin-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success);
  flex-shrink: 0;
}

/* ---------------- 管理口令弹窗 ---------------- */
.token-lead,
.token-foot {
  margin: 0;
  font-size: var(--fs-caption);
  line-height: var(--lh-body);
  color: var(--text-2);
}

.token-lead {
  margin-bottom: var(--space-4);
}

.token-foot {
  margin-top: var(--space-4);
  color: var(--text-3);
}

/* 「清除口令」推到左侧，和右侧的取消/保存分开 —— 它是破坏性操作，
   不该和「保存」并排站在一起。
   antd 的 modal footer 是 text-align:end 而不是 flex，所以用 float；
   这个类挂在自己渲染的按钮上，scoped 样式能命中。 */
.token-clear {
  float: left;
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

  /* 窄屏只留图形标，隐藏「途灵 TripMind」文字。
     顶栏是 brand + 三个导航项 + 管理口令入口的单行布局，
     这些文字在 375px 宽的手机上放不下 —— 实测宽度会超出视口，
     表现是整条顶栏被挤爆（这一条在加管理口令入口之前就已经临界了）。
     图形标 + 页面标题已经足够表明身份。 */
  .brand-name {
    display: none;
  }

  .nav {
    gap: 0;
  }

  .nav-link {
    min-height: var(--touch-min);
    padding: 0 var(--space-3);
    font-size: var(--fs-caption);
  }

  .admin-entry {
    min-height: var(--touch-min);
    margin-left: var(--space-2);
    padding: 0 var(--space-2);
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
