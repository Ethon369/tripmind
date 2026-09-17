import { ref, onMounted, onUnmounted } from 'vue';

/**
 * 断点响应式。
 *
 * 只在**壳层视图**里用（Home / Result / History / Knowledge），
 * 由壳层把结果通过 props 传给子组件 —— 子组件自己判断视口会让测试和复用都变复杂。
 *
 * 断点与 tokens.css 里注释的基准一致，与 antd Grid 对齐：
 *   xs < 576   sm >= 576   md >= 768   lg >= 992   xl >= 1200
 */

const MQ = {
  /** >= 768px：平板竖屏及以上 */
  md: '(min-width: 768px)',
  /** >= 992px：平板横屏 / 小笔记本及以上 */
  lg: '(min-width: 992px)',
} as const;

export function useScreen() {
  /** 手机竖屏 / 小屏手机（< 768）。侧栏收起、栅格单列 */
  const isMobile = ref(false);
  /** 中等屏（768–991）。平板竖屏 */
  const isTablet = ref(false);
  /** 桌面（>= 992）。侧栏常驻 */
  const isDesktop = ref(false);

  let mqlMd: MediaQueryList | null = null;
  let mqlLg: MediaQueryList | null = null;

  const sync = () => {
    const md = mqlMd?.matches ?? false;
    const lg = mqlLg?.matches ?? false;
    isDesktop.value = lg;
    isMobile.value = !md;
    isTablet.value = md && !lg;
  };

  onMounted(() => {
    mqlMd = window.matchMedia(MQ.md);
    mqlLg = window.matchMedia(MQ.lg);
    sync();
    mqlMd.addEventListener('change', sync);
    mqlLg.addEventListener('change', sync);
  });

  onUnmounted(() => {
    mqlMd?.removeEventListener('change', sync);
    mqlLg?.removeEventListener('change', sync);
    mqlMd = null;
    mqlLg = null;
  });

  return { isMobile, isTablet, isDesktop };
}
