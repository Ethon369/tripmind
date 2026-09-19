<template>
  <!-- bare 路由（落地页 / 登录 / 注册）不套应用外壳。
       它们各自占据完整一屏，而且——更重要的——外壳导航里的
       「历史行程」「知识库」「账号管理」都需要登录，给一个还没登录的人
       看这些入口只会通向一串跳转。落地页有自己的营销导航。 -->
  <router-view v-if="isBare" />

  <a-layout v-else class="app-shell">
    <a-layout-header class="app-header">
      <div class="header-inner">
        <router-link to="/app" class="brand" aria-label="途灵 TripMind 规划台">
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
          <!-- 导航项按角色过滤。知识库基于**全站共用**的向量库，
               普通用户既读不到自己的东西、也没有写权限，给他一个入口
               只会通向"操作被拒"；账号管理更是只有管理员才有意义。
               过滤是**界面层**的体贴，不是权限 —— 真正的判定在后端
               每个端点上，直接敲 URL 也进不去（路由守卫 + 403）。 -->
          <router-link v-for="item in navItems" :key="item.to" :to="item.to" class="nav-link">
            {{ item.label }}
          </router-link>

          <!-- 账号区。
               未登录时是一个「登录」按钮；已登录时是用户名 + 下拉菜单。
               原来这里是一个「管理口令」弹窗入口 —— 那个模型下全站只有一个
               身份，所以入口放在导航尾部、和三个页面平级是对的；
               现在身份是"人"，它就该长得像一个账号入口。 -->
          <template v-if="isAuthenticated">
            <a-dropdown :trigger="['click']" placement="bottomRight">
              <button type="button" class="account-entry" aria-label="账号菜单">
                <span class="account-avatar" aria-hidden="true">{{ initial }}</span>
                <span class="account-name">{{ displayName }}</span>
                <!-- 角色徽标。用文字而不是只有颜色 —— 颜色单独承载信息
                     对色觉障碍用户不可读，而"是不是管理员"是这里最要紧的一条。 -->
                <span v-if="isAdmin" class="account-role">管理员</span>
                <DownOutlined class="account-caret" />
              </button>
              <template #overlay>
                <a-menu @click="onAccountMenu">
                  <a-menu-item key="me" disabled>
                    <span class="menu-username">{{ user?.username }}</span>
                  </a-menu-item>
                  <a-menu-divider />
                  <a-menu-item v-if="isAdmin" key="users">
                    <TeamOutlined /> 账号管理
                  </a-menu-item>
                  <a-menu-item key="password">
                    <KeyOutlined /> 修改密码
                  </a-menu-item>
                  <a-menu-divider />
                  <a-menu-item key="logout" danger>
                    <LogoutOutlined /> 退出登录
                  </a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
          </template>
          <router-link v-else to="/login" class="account-entry is-guest">
            登录
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
      </div>
    </a-layout-footer>
  </a-layout>

  <!-- 修改密码弹窗。放在 a-layout 外面：antd 的 Modal 默认 teleport 到 body，
       留在 layout 里只是多一个不渲染的占位节点，反而干扰 flex 布局。
       只在有外壳时才有意义（入口在账号菜单里）—— bare 路由下整块不渲染。 -->
  <a-modal
    v-if="!isBare"
    v-model:open="pwdOpen"
    title="修改密码"
    :width="460"
    :centered="true"
    :confirm-loading="pwdSaving"
    ok-text="保存"
    @ok="submitPassword"
  >
    <p class="token-lead">
      修改成功后，你在<strong>其它设备上的登录会全部失效</strong>，
      当前这台会继续使用（服务端会换发一个新令牌）。
    </p>

    <a-form layout="vertical">
      <a-form-item label="当前密码">
        <a-input-password
          v-model:value="pwdForm.oldPassword"
          placeholder="请输入当前密码"
          autocomplete="current-password"
        />
      </a-form-item>
      <a-form-item label="新密码">
        <a-input-password
          v-model:value="pwdForm.newPassword"
          placeholder="至少 6 位；不能与用户名相同"
          autocomplete="new-password"
        />
      </a-form-item>
      <a-form-item label="确认新密码">
        <a-input-password
          v-model:value="pwdForm.confirm"
          placeholder="再输一次"
          autocomplete="new-password"
        />
      </a-form-item>
    </a-form>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Modal, message } from 'ant-design-vue';
import {
  DownOutlined,
  KeyOutlined,
  LogoutOutlined,
  TeamOutlined
} from '@ant-design/icons-vue';
import { useAuth } from '@/composables/useAuth';
import { changePassword, logout, setToken } from '@/services/api';

/**
 * 应用外壳：顶栏 + 内容区 + 页脚 + 账号菜单。
 *
 * 顶栏的「管理口令」入口在账号体系上线后换成了**账号入口** —— 那不是换个皮：
 * 管理口令模型下全站只有一个身份（"知道口令的人"），所以那个入口只是一个
 * 设置开关；账号模型下身份是具体的人，而且有一组和它相关的动作
 * （看自己是谁、改密码、管理账号、退出），那是一个菜单。
 *
 * 角色徽标是这里唯一新增的"信息展示"：管理员和普通用户看到的导航项不一样，
 * 不给一个明确的标记，用户会以为"我的知识库入口不见了"。
 */

const router = useRouter();
const route = useRoute();

/** 不套外壳的路由（落地页 / 登录 / 注册）。见模板顶部的说明。 */
const isBare = computed(() => route.meta.bare === true);

// 账号状态。放在顶部的理由是模板里到处要用（角色徽标、账号菜单），
// 写在前面省得读的时候还要往下找。
const { user, isAuthenticated, isAdmin, displayName, signOut, refresh } = useAuth();

/**
 * 导航项。
 *
 * 保持无图标：三项文字导航已足够清晰，
 * 而这套视觉的轻盈感来自留白和色彩，不来自图标堆叠。
 *
 * ⚠️ 「首页」指向 `/app`（规划页）而不是 `/`：落地页是**对外**的门面，
 *    已经登录的人点"首页"想看到的是自己的规划台，不是又一遍产品介绍。
 *    知识库那一项按角色过滤 —— 理由见模板里的注释。
 */
const navItems = computed(() => {
  const items = [
    { to: '/app', label: '规划行程' },
    { to: '/history', label: '历史行程' },
    // 知识库对**所有登录用户**开放(每人管自己的攻略)。
    // 页内仍按角色隐藏"灌内置库 / 切 RAG 开关"那两个管理员操作。
    { to: '/knowledge', label: '知识库' }
  ];
  return items;
});

// ---------------- 账号 ----------------

/** 头像位置显示用户名首字母。中文名取第一个字也是合理的。 */
const initial = computed(() => (displayName.value || '?').trim().charAt(0).toUpperCase());

// 启动时向服务端核对一次登录态。
// 本地缓存已经让顶栏渲染出用户名了，这一步是为了纠正"令牌已失效但缓存还在"。
// 失败不提示 —— 用户会直接看到界面变成未登录，那本身就是提示。
onMounted(() => {
  void refresh();
});

function onAccountMenu(e: unknown) {
  const key = typeof e === 'object' && e !== null ? String((e as { key?: unknown }).key ?? '') : '';
  if (key === 'users') return void router.push('/users');
  if (key === 'password') return openPasswordDialog();
  if (key === 'logout') return confirmLogout();
}

function confirmLogout() {
  Modal.confirm({
    title: '退出登录？',
    content: '退出后需要重新输入用户名和密码才能生成或查看自己的行程。',
    okText: '退出',
    cancelText: '取消',
    onOk: async () => {
      // logout() 内部已经保证"服务端失败也清本地"（见 api.ts）。
      await logout();
      signOut();
      message.success('已退出登录');
      // 回到首页 —— 它是需要登录的页面，守卫会把用户引到登录页。
      // 不直接 push('/login')：留着当前页（比如某个行程详情）让他
      // 能继续看完，而需要权限的动作届时会引导他登录。
      await router.push('/');
    }
  });
}

// ---------------- 修改密码 ----------------

const pwdOpen = ref(false);
const pwdSaving = ref(false);
const pwdForm = reactive({ oldPassword: '', newPassword: '', confirm: '' });

function openPasswordDialog() {
  pwdForm.oldPassword = '';
  pwdForm.newPassword = '';
  pwdForm.confirm = '';
  pwdOpen.value = true;
}

async function submitPassword() {
  if (!pwdForm.oldPassword || !pwdForm.newPassword) {
    message.warning('请填写当前密码和新密码');
    return;
  }
  if (pwdForm.newPassword !== pwdForm.confirm) {
    message.warning('两次输入的新密码不一致');
    return;
  }

  pwdSaving.value = true;
  try {
    const res = await changePassword(pwdForm.oldPassword, pwdForm.newPassword);
    // **必须写回新令牌**：服务端改密码时吊销了全部会话（包括当前这个），
    // 不写回的话用户改完自己的密码立刻变成未登录 —— 会被理解成
    // "改密码把账号弄坏了"。
    if (res.token) setToken(res.token);
    pwdOpen.value = false;
    message.success(res.message || '密码已修改');
  } catch (e: unknown) {
    message.error((e as Error)?.message || '修改密码失败');
  } finally {
    pwdSaving.value = false;
  }
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

/* ---------------- 账号入口 ----------------
 * 从属于导航，但视觉上比三个页面低一档：它不是一个页面，是一组
 * 和"我"相关的动作。所以用带底色的轻按钮，而不是导航项那种药丸高亮。
 */
.account-entry {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 36px;
  /* 和最后一个导航项之间留出距离，再由 ::before 画一条竖线：
     "从这里开始是另一种入口"。 */
  margin-left: var(--space-3);
  padding: 0 var(--space-3) 0 var(--space-1);
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  font-family: inherit;
  font-size: var(--fs-caption);
  font-weight: var(--fw-medium);
  color: var(--text-2);
  cursor: pointer;
  white-space: nowrap;
  text-decoration: none;
  transition: color var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out);
}

/* 竖分隔线用伪元素而不是 border-left：圆角药丸配上左侧 border，
   悬停变底色时左边缘会出现一个直角缺口。 */
.account-entry::before {
  content: '';
  width: 1px;
  height: 16px;
  background: var(--line-1);
  margin-right: var(--space-2);
}

.account-entry:hover {
  color: var(--text-1);
  background: var(--bg-sunken);
}

/* 首字母头像。比图标更省事，也比空白更能让人一眼认出"这是账号区"。 */
.account-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--brand-gradient);
  color: #fff;
  font-size: var(--fs-micro);
  font-weight: var(--fw-semibold);
  flex-shrink: 0;
}

.account-name {
  max-width: 10ch;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 角色徽标。**文字 + 颜色**两重表达：只靠颜色区分对色觉障碍用户不可读，
   而"我是不是管理员"恰恰是这里最要紧的一条信息。 */
.account-role {
  padding: 0 6px;
  border-radius: var(--radius-pill);
  background: var(--brand-50);
  color: var(--brand-600);
  font-size: var(--fs-micro);
  line-height: 16px;
}

.account-caret {
  font-size: 10px;
  color: var(--text-3);
}

/* 未登录时的「登录」是一个链接，需要把伪元素分隔线保留住，
   同时去掉按钮的默认样式差异。 */
.account-entry.is-guest {
  padding: 0 var(--space-4) 0 var(--space-1);
  color: var(--brand-600);
}

.menu-username {
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

/* ---------------- 修改密码弹窗 ---------------- */
.token-lead {
  margin: 0 0 var(--space-4);
  font-size: var(--fs-caption);
  line-height: var(--lh-body);
  color: var(--text-2);
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
     顶栏是 brand + 导航项 + 账号入口的单行布局，
     这些文字在 375px 宽的手机上放不下 —— 实测宽度会超出视口，
     表现是整条顶栏被挤爆（这一条在加账号入口之前就已经临界了）。
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

  .account-entry {
    min-height: var(--touch-min);
    margin-left: var(--space-2);
    padding: 0 var(--space-2) 0 0;
  }

  .account-entry::before {
    margin-right: var(--space-2);
  }

  /* 手机上只留头像和角色点 —— 用户名和「管理员」文字放不下。
     角色信息没丢：管理员的下拉菜单里第一项就是「账号管理」，
     而且导航项本身也按角色变了。 */
  .account-name,
  .account-role,
  .account-caret {
    display: none;
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
