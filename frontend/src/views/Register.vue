<template>
  <div class="auth">
    <div class="card">
      <router-link to="/" class="back">← 返回首页</router-link>

      <div class="head">
        <span class="mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none">
            <circle cx="12" cy="12" r="8.4" stroke="currentColor" stroke-width="1.9" />
            <rect
              x="8.7" y="8.7" width="6.6" height="6.6" rx="1.6"
              transform="rotate(45 12 12)" stroke="currentColor" stroke-width="1.9"
            />
          </svg>
        </span>
        <h1 class="title">创建账号</h1>
        <p class="sub">注册后直接开始生成行程，不需要再登录一次</p>
      </div>

      <a-form layout="vertical" :model="form" @finish="submit">
        <a-form-item
          label="用户名"
          name="username"
          :rules="[{ required: true, message: '请输入用户名' }]"
          :help="USERNAME_HINT"
        >
          <a-input
            v-model:value="form.username"
            size="large"
            placeholder="3-32 位，字母或数字开头"
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
            placeholder="至少 6 位"
            autocomplete="new-password"
            :disabled="loading"
          >
            <template #prefix><LockOutlined /></template>
          </a-input-password>
        </a-form-item>

        <a-form-item
          label="确认密码"
          name="confirm"
          :rules="[{ required: true, message: '请再输入一次密码' }]"
        >
          <a-input-password
            v-model:value="form.confirm"
            size="large"
            placeholder="再输入一次"
            autocomplete="new-password"
            :disabled="loading"
          >
            <template #prefix><LockOutlined /></template>
          </a-input-password>
        </a-form-item>

        <a-alert v-if="error" type="error" :message="error" show-icon class="alert" />

        <a-button
          type="primary"
          html-type="submit"
          size="large"
          block
          :loading="loading"
        >
          注册并开始
        </a-button>
      </a-form>

      <p class="foot">
        已经有账号了？<router-link to="/login">去登录</router-link>
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { UserOutlined, LockOutlined } from '@ant-design/icons-vue';
import { register, setToken, cacheUser } from '@/services/api';

/**
 * 注册页。
 *
 * 与前端的"注册"按钮配套 —— 落地页的「免费开始」直接落到这里。
 *
 * 三件事刻意**不做**:
 *
 * 1. **不问角色。** 后端也不接受 `role` 参数(见 `routes/auth.py::register`)。
 *    在界面上放一个"角色"选择器,即使后端忽略它,也会让人以为可以选 ——
 *    那比不放更糟。
 * 2. **不做用户名实时查重。** 那需要一个"这个用户名存在吗"的公开接口,
 *    而它是一个现成的账号枚举器。重名留给提交时的 409 —— 代价是用户
 *    多填一次,换来的是别人不能拿它来扫账号。
 * 3. **不做密码强度条。** 后端的规则只有"长度 + 不能与用户名相同 + 不能全同字符",
 *    在界面上画一条"弱/中/强"的进度条会暗示一套并不存在的规则。
 *    规则用一句提示说清楚比画一条不准的条更有用。
 */

const USERNAME_HINT = '字母或数字开头，可含下划线、点和短横线';

const router = useRouter();
const route = useRoute();

const loading = ref(false);
const error = ref('');
const form = reactive({ username: '', password: '', confirm: '' });

async function submit() {
  if (loading.value) return;

  // 两次密码一致性在**前端**判而不是交给后端:后端根本不收 confirm 字段,
  // 传过去只会是一个被忽略的参数。
  if (form.password !== form.confirm) {
    error.value = '两次输入的密码不一致';
    return;
  }

  loading.value = true;
  error.value = '';

  try {
    const res = await register(form.username.trim(), form.password);

    // 先落盘再跳转 —— 顺序反了的话新页面上的第一个请求会带着旧令牌(或没令牌)
    // 发出去,刚注册完就被弹回登录页。
    setToken(res.token);
    cacheUser(res.user);

    message.success('账号已创建，开始规划你的行程吧');

    // 注册成功后回落地页带来的那个目标地址。与登录页同样的开放重定向防护:
    // 只接受以单个 `/` 开头的站内相对路径。
    const target = typeof route.query.redirect === 'string' ? route.query.redirect : '';
    const safe = /^\/(?!\/)/.test(target) ? target : '/app';
    await router.replace(safe);
  } catch (e: unknown) {
    error.value = (e as Error)?.message || '注册失败，请重试';
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.auth {
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
  max-width: 400px;
  background: var(--bg-card);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-lg);
  padding: var(--sp-8) var(--sp-6);
  box-shadow: var(--shadow-3);
}

/* 返回落地页的入口。放在卡片内左上角而不是页头 ——
   这一页没有应用外壳,没有别的地方能回去。 */
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
  color: var(--text-2);
}
.foot a {
  color: var(--brand-600);
  font-weight: var(--fw-medium);
}

@media (max-width: 575px) {
  .auth {
    padding: var(--sp-6) var(--sp-3);
    align-items: flex-start;
  }
  .card {
    padding: var(--sp-8) var(--sp-4) var(--sp-6);
  }
}
</style>
