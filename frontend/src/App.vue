<template>
  <div id="app">
    <a-layout style="min-height: 100vh">
      <a-layout-header class="app-header">
        <router-link to="/" class="brand">🌍 途灵 TripMind</router-link>

        <nav class="nav">
          <router-link to="/" class="nav-link">首页</router-link>
          <router-link to="/history" class="nav-link">历史行程</router-link>
        </nav>
      </a-layout-header>

      <a-layout-content style="padding: 24px">
        <router-view />
      </a-layout-content>

      <a-layout-footer style="text-align: center">
        途灵 TripMind ©2025 基于HelloAgents框架
      </a-layout-footer>
    </a-layout>
  </div>
</template>

<script setup lang="ts">
// 导航用 router-link 而不是 a-menu:
// header 是深色底,而 antd Menu 的选中态样式要额外覆写才能压住,
// 一个尚未成型的导航不值得引入这套对抗。原生链接 + 三行 CSS 就够。
</script>

<style>
#app {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial,
    'Noto Sans', sans-serif;
}
</style>

<style scoped>
.app-header {
  background: #001529;
  padding: 0 50px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.brand {
  color: #fff;
  font-size: 24px;
  font-weight: bold;
  text-decoration: none;
  white-space: nowrap;
}

.brand:hover {
  color: #fff;
}

.nav {
  display: flex;
  gap: 8px;
}

.nav-link {
  color: rgba(255, 255, 255, 0.65);
  font-size: 16px;
  padding: 0 16px;
  line-height: 64px;
  text-decoration: none;
  transition: color 0.2s;
}

.nav-link:hover {
  color: #fff;
}

/* 用 exact-active 而不是 active:否则访问 /result/xxx 时"首页"也会亮,
   因为 / 是每个路径的前缀。 */
.nav-link.router-link-exact-active {
  color: #fff;
  font-weight: 600;
  box-shadow: inset 0 -2px 0 #1677ff;
}

/* 窄屏下缩进,免得 header 内容溢出 */
@media (max-width: 768px) {
  .app-header {
    padding: 0 16px;
  }

  .brand {
    font-size: 18px;
  }

  .nav-link {
    padding: 0 8px;
    font-size: 14px;
  }
}
</style>
