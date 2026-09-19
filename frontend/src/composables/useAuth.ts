import { computed, ref } from 'vue';
import type { AuthUser } from '@/types';
import { cacheUser, clearAuth, fetchMe, getToken, readCachedUser } from '@/services/api';

/**
 * 当前登录账号。**应用的单一登录态来源。**
 *
 * 为什么用模块级 ref 而不是 Pinia
 * --------------------------------
 * 项目里没有装状态管理库，为此引一个不合适。需要被全局共享的登录态只有
 * 一个对象，模块级 `ref` 就够 —— 导出的 ref 是同一个实例，任何组件
 * `useAuth()` 拿到的都是它。
 *
 * 状态**从本地缓存初始化**，然后由 `refresh()` 向服务端核对。
 * 两段式的原因：
 *
 *   只信本地 → 令牌过期了界面还显示着用户名和「账号管理」入口，
 *             点进去才 401，体验和安全性都不对。
 *   只信服务端 → 每次刷新页面顶栏都要先空一下再跳出用户名，
 *               而且离线/接口慢时会长时间"未登录"。
 *
 * 所以先用缓存渲染（顶栏不闪），同时发一次 `/auth/me` 校正；
 * 对不上就以服务端为准。
 */

/** 缓存的账号。`null` = 未登录（或缓存不可读）。 */
const user = ref<AuthUser | null>(readCachedUser());

/** 是否已经跟服务端核对过一次。首屏据此决定要不要显示"检查登录中"。 */
const verified = ref(false);

/** 正在核对。防止并发调用 /auth/me（比如守卫和壳层同时触发）。 */
let inflight: Promise<AuthUser | null> | null = null;

function setUser(next: AuthUser | null): void {
  user.value = next;
  cacheUser(next);
}

/**
 * 向服务端核对登录态。
 *
 * **不会抛异常** —— 核对失败（网络问题、令牌过期）一律当作"未登录"，
 * 由调用方决定要不要跳登录页。让这个函数抛异常的话，路由守卫里
 * 就得包 try，而"守卫本身出错"会把用户卡在一个空白页上。
 */
async function refresh(): Promise<AuthUser | null> {
  if (!getToken()) {
    // 连令牌都没有，不用问服务端。这里**必须**清掉缓存的账号：
    // 令牌被别处清了但账号缓存还在，是"看起来登录着、实际什么都拿不到"
    // 那种最难解释的状态。
    setUser(null);
    verified.value = true;
    return null;
  }

  // 复用进行中的那次请求：路由守卫和 App.vue 都会在启动时调它，
  // 不复用就是同一秒内发两次 /auth/me。
  if (inflight) return inflight;

  inflight = (async () => {
    try {
      const me = await fetchMe();
      setUser(me);
      return me;
    } catch {
      // 401 已经由响应拦截器 clearAuth() 了；这里再兜一次是为了覆盖
      // "网络不通"这类没有 401 的情况 —— 那种情况下令牌可能还是好的，
      // 但**不能**因此认为已登录（会显示一个点任何东西都失败的界面）。
      setUser(null);
      return null;
    } finally {
      verified.value = true;
      inflight = null;
    }
  })();

  return inflight;
}

function signOut(): void {
  clearAuth();
  user.value = null;
  verified.value = true;
}

const isAuthenticated = computed(() => user.value !== null);
const isAdmin = computed(() => user.value?.role === 'admin');

/** 顶栏显示用：优先显示名，回退到用户名 */
const displayName = computed(
  () => user.value?.display_name || user.value?.username || ''
);

export function useAuth() {
  return {
    user,
    verified,
    isAuthenticated,
    isAdmin,
    displayName,
    refresh,
    setUser,
    signOut
  };
}
