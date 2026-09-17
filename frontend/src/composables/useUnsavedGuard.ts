import { onBeforeRouteLeave } from 'vue-router';
import { ref, onMounted, onUnmounted } from 'vue';

/**
 * 「编辑未保存时离开」的保护。
 *
 * 为什么需要：结果页的编辑模式里，用户改了景点顺序或描述之后如果点
 * 「返回首页」或者按浏览器后退，改动会**静默丢失** —— 没有任何提示。
 * 这类问题用户往往过了很久才发现，而且没法复现。
 *
 * 两道防线：
 * 1. 站内路由跳转 —— onBeforeRouteLeave 拦下，弹确认框
 * 2. 浏览器刷新/关闭标签页 —— beforeunload（只能用原生 dialog，
 *    浏览器不允许自定义文案，这是规范限制）
 *
 * 用 Promise 把「等用户点确认」这件事变成可 await 的：
 * vue-router 4 的守卫支持返回 Promise<boolean>，返回 false 就取消导航。
 */
export function useUnsavedGuard(isDirty: () => boolean) {
  /** 确认框是否可见。由调用方在模板里绑到 a-modal */
  const visible = ref(false);

  let resolveFn: ((ok: boolean) => void) | null = null;

  onBeforeRouteLeave(async () => {
    if (!isDirty()) return true;

    visible.value = true;
    const ok = await new Promise<boolean>((resolve) => {
      resolveFn = resolve;
    });
    return ok;
  });

  function confirmLeave() {
    resolveFn?.(true);
    resolveFn = null;
    visible.value = false;
  }

  function cancelLeave() {
    resolveFn?.(false);
    resolveFn = null;
    visible.value = false;
  }

  const beforeUnloadHandler = (e: BeforeUnloadEvent) => {
    if (!isDirty()) return;
    // 现代浏览器的规范：设置 returnValue 即视为「有未保存改动」，
    // 文案由浏览器统一提供，自定义内容会被忽略
    e.preventDefault();
    e.returnValue = '';
  };

  onMounted(() => {
    window.addEventListener('beforeunload', beforeUnloadHandler);
  });

  onUnmounted(() => {
    window.removeEventListener('beforeunload', beforeUnloadHandler);
    // 组件被卸载时如果还挂着 Promise，要放行，否则导航会永远卡住
    resolveFn?.(true);
    resolveFn = null;
  });

  return { visible, confirmLeave, cancelLeave };
}
