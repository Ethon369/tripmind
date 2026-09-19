<template>
  <div class="login">
    <div class="card">
      <router-link to="/" class="back">← 返回首页</router-link>

      <div class="head">
        <span class="mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none">
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
        <h1 class="title">登录途灵 TripMind</h1>
        <p class="sub">登录后可以生成行程、保存历史记录</p>
      </div>

      <a-form layout="vertical" :model="form" @finish="submit">
        <a-form-item
          label="用户名"
          name="username"
          :rules="[{ required: true, message: '请输入用户名' }]"
        >
          <a-input
            v-model:value="form.username"
            size="large"
            placeholder="用户名"
            autocomplete="username"
            :disabled="loading"
          >
            <template #prefix><UserOutlined /></template>
          </a-input>
        </a-form-item>

        <a-form-item
          label="密码"
          name="password"
          :rules="[{ required: true, message: '请输入密码' }]"
        >
          <a-input-password
            v-model:value="form.password"
            size="large"
            placeholder="密码"
            autocomplete="current-password"
            :disabled="loading"
          >
            <template #prefix><LockOutlined /></template>
          </a-input-password>
        </a-form-item>

        <!-- 错误提示固定在表单和按钮之间。
             不用 message.error 常驻：它三秒后就没了，而用户这时往往
             正在低头重敲密码，抬头时提示已经消失 —— 于是他会以为是
             "点了没反应"。 -->
        <a-alert
          v-if="error"
          type="error"
          :message="error"
          show-icon
          class="alert"
        />

        <a-button
          type="primary"
          html-type="submit"
          size="large"
          block
          :loading="loading"
        >
          登录
        </a-button>
      </a-form>

      <p class="foot">
        还没有账号？<router-link to="/register">免费注册</router-link>
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { UserOutlined, LockOutlined } from '@ant-design/icons-vue';
import { login, setToken, cacheUser } from '@/services/api';

/**
 * 登录页。
 *
 * ⚠️ 这里**没有**"记住我"开关。本地令牌一律写 localStorage（理由见
 * `constants/storage.ts`）—— 多一个开关就多一种"为什么我明明勾了却要
 * 重新登录"的可能，而它带来的差别（关标签页要不要登出）对用户来说
 * 远不如"别让我一直输密码"重要。
 *
 * ⚠️ 也**没有**"忘记密码"。找回密码需要邮件/短信通道，这个项目没有；
 * 界面上给一个点了没用的链接比不给更糟。底部给的是**可执行**的替代：
 * 去注册（自助注册已开放），或者找管理员重置。
 *
 * 注册是独立的一页（`/register`）而不是本页的一个切换 —— 两张表单字段不同
 * （注册要确认密码），塞进一页会让"我到底在登录还是注册"变得要靠小字判断。
 */

const router = useRouter();
const route = useRoute();

const loading = ref(false);
const error = ref('');

const form = reactive({ username: '', password: '' });

async function submit() {
  if (loading.value) return;
  loading.value = true;
  error.value = '';

  try {
    const res = await login(form.username, form.password);

    // **先落盘再跳转**。顺序反了的话新页面上的第一个请求会带着旧令牌
    // （或没令牌）发出去，于是刚登录完就被弹回登录页 ——
    // 而用户看到的是一次莫名其妙的"登录失败"。
    setToken(res.token);
    cacheUser(res.user);

    message.success(`欢迎回来，${res.user.display_name || res.user.username}`);

    // 回到被拦下来的那个地址。守卫会把原始目标写在 query.redirect 里；
    // 没有就回规划首页 `/app`（不是 `/` —— `/` 是给未登录访客看的落地页，
    // 刚登录完的人想看的是自己的规划台）。
    //
    // ⚠️ 用 `redirect` 之前要确认它是**站内路径**，否则
    // `/login?redirect=https://evil.example` 会变成一个开放重定向
    // —— 用户刚在真站点输完密码，被跳到一个长得一样的钓鱼页。
    // 只接受以单个 `/` 开头的相对路径。
    const target = typeof route.query.redirect === 'string' ? route.query.redirect : '';
    const safe = /^\/(?!\/)/.test(target) ? target : '/app';
    await router.replace(safe);
  } catch (e: unknown) {
    error.value = (e as Error)?.message || '登录失败，请重试';
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--sp-10) var(--sp-4);
  min-height: 100dvh;
  background: var(--bg-page);
}

.card {
  position: relative;
  width: 100%;
  max-width: 380px;
  background: var(--bg-card);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-lg);
  padding: var(--sp-8) var(--sp-6);
  box-shadow: var(--shadow-3);
}

/* 回落地页的入口。这一页是 bare 路由（不套应用外壳），
   没有别的地方能回去。 */
.back {
  position: absolute;
  top: var(--sp-4);
  left: var(--sp-5);
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.back:hover {
  color: var(--brand-600);
}

.head {
  text-align: center;
  margin-bottom: var(--sp-6);
}

.mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: var(--brand-gradient);
  color: #fff;
  box-shadow: var(--shadow-brand);
  margin-bottom: var(--sp-3);
}

.title {
  font-size: var(--fs-h3);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
  margin-bottom: var(--sp-1);
}

.sub {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.alert {
  margin-bottom: var(--sp-4);
}

.foot {
  margin: var(--sp-5) 0 0;
  text-align: center;
  font-size: var(--fs-caption);
  color: var(--text-3);
}

@media (max-width: 575px) {
  .login {
    padding: var(--sp-6) var(--sp-3);
    align-items: flex-start;
  }

  .card {
    padding: var(--sp-6) var(--sp-4);
  }
}
</style>
