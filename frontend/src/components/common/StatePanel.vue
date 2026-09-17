<template>
  <!-- success 时完全透明：只负责把 default slot 渲染出来 -->
  <slot v-if="state === 'success'" />

  <!-- 加载中 -->
  <div v-else-if="state === 'loading'" class="sp sp-loading" aria-busy="true" aria-live="polite">
    <PlanSkeleton v-if="skeleton !== 'none'" :variant="skeleton" :count="skeletonCount" />
    <div v-else class="sp-inline-loading">
      <LoadingOutlined class="sp-spin" aria-hidden="true" />
      <span>{{ title || '正在加载…' }}</span>
    </div>
  </div>

  <!-- 错误 -->
  <div v-else-if="state === 'error'" class="sp sp-error" role="alert">
    <ExclamationCircleOutlined class="sp-icon sp-icon-error" aria-hidden="true" />
    <h3 class="sp-title">{{ resolvedTitle }}</h3>
    <p v-if="resolvedDescription" class="sp-desc">{{ resolvedDescription }}</p>
    <pre v-if="showDetail" class="sp-detail">{{ detail }}</pre>
    <div class="sp-actions">
      <a-button v-if="showRetry" type="primary" @click="emit('retry')">
        <ReloadOutlined aria-hidden="true" />
        重试
      </a-button>
      <slot name="extra" />
    </div>
  </div>

  <!-- 空态 -->
  <div v-else class="sp sp-empty">
    <InboxOutlined class="sp-icon sp-icon-empty" aria-hidden="true" />
    <h3 class="sp-title">{{ title || '暂无数据' }}</h3>
    <p v-if="description" class="sp-desc">{{ description }}</p>
    <div class="sp-actions">
      <slot name="extra" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import {
  LoadingOutlined,
  ExclamationCircleOutlined,
  InboxOutlined,
  ReloadOutlined,
} from '@ant-design/icons-vue';
import PlanSkeleton from './PlanSkeleton.vue';

/**
 * 四态统一出口：loading / empty / error / success。
 *
 * **这个组件存在的意义是防错，不是省代码。**
 * 结果页曾经有过一个体验缺陷：`v-if="tripPlan"` + `v-else <空态>`，
 * 而 tripPlan 在接口返回前是 falsy —— 于是加载期间显示「没有找到旅行计划数据」，
 * 拿到数据后才被顶掉。用户看到的是「暂无数据」闪一下。
 *
 * 根因不是写错了某个条件，而是**四态没有统一出口**：随手写 v-if/v-else
 * 就很容易把 loading 和 empty 混成一个分支。
 * 强制所有数据区域走这个组件之后，state='loading' 在结构上就不可能
 * 渲染出空态文案。
 */

type ErrorKind = 'network' | 'server' | 'notFound' | 'business';
type SkeletonVariant = 'plan' | 'list' | 'table' | 'none';

const props = withDefaults(
  defineProps<{
    state: 'loading' | 'empty' | 'error' | 'success';
    title?: string;
    description?: string;
    /** 错误类型决定默认文案与恢复动作 */
    errorKind?: ErrorKind;
    /** 后端返回的 detail / message，原样展示 */
    detail?: string;
    skeleton?: SkeletonVariant;
    skeletonCount?: number;
    showRetry?: boolean;
  }>(),
  {
    errorKind: 'network',
    skeleton: 'list',
    skeletonCount: 5,
    showRetry: true,
  }
);

const emit = defineEmits<{ retry: [] }>();

/**
 * 四类错误必须分开措辞 —— 这是原代码做得不够的地方：
 * 网络不通、服务端 500、行程不存在、后端业务降级（HTTP 200 但 success=false），
 * 四种情况的**恢复动作完全不同**，混成一句「加载失败」等于让用户自己猜。
 */
const ERROR_TEXT: Record<ErrorKind, { title: string; description: string; retry: boolean }> = {
  network: {
    title: '连不上服务',
    description: '请确认后端服务已启动（默认 127.0.0.1:8001），然后重试。',
    retry: true,
  },
  server: {
    title: '服务端出错了',
    description: '后端处理这次请求时抛了异常。下面是服务端返回的原因。',
    retry: true,
  },
  notFound: {
    title: '行程不存在',
    description: '它可能已被删除，或者链接里的 id 不对。',
    retry: false,
  },
  business: {
    title: '这次没能生成行程内容',
    description: '记录已保存，可以在「历史行程」里查看。下面是具体原因。',
    retry: true,
  },
};

const resolvedTitle = computed(() => props.title || ERROR_TEXT[props.errorKind].title);

const resolvedDescription = computed(
  () => props.description || ERROR_TEXT[props.errorKind].description
);

/** notFound 没有「重试」的意义，重试多少次都是 404 */
const showRetry = computed(() => props.showRetry && ERROR_TEXT[props.errorKind].retry);

/** 后端 detail 只在 server / business 两种情况下有意义（是真实原因，不是模板文案） */
const showDetail = computed(
  () => !!props.detail && (props.errorKind === 'server' || props.errorKind === 'business')
);
</script>

<style scoped>
.sp {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: var(--sp-10) var(--sp-4);
  background: var(--bg-card);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-1);
}

.sp-loading {
  padding: 0;
  background: transparent;
  box-shadow: none;
}

.sp-inline-loading {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-6);
  color: var(--text-2);
}

.sp-spin {
  color: var(--brand-500);
}

.sp-icon {
  font-size: 40px;
  margin-bottom: var(--sp-4);
}

.sp-icon-error {
  color: var(--error);
}

.sp-icon-empty {
  color: var(--text-3);
}

.sp-title {
  font-size: var(--fs-h3);
  font-weight: var(--fw-medium);
  color: var(--text-1);
  margin-bottom: var(--sp-2);
}

.sp-desc {
  color: var(--text-2);
  font-size: var(--fs-body);
  max-width: 60ch;
}

/* 后端 detail 可能很长且带堆栈痕迹，用等宽 + 可滚动容器收住 */
.sp-detail {
  margin: var(--sp-4) 0 0;
  padding: var(--sp-3);
  max-width: 100%;
  max-height: 160px;
  overflow: auto;
  text-align: left;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-num);
  font-size: var(--fs-caption);
  color: var(--text-2);
  background: var(--bg-sunken);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-sm);
}

.sp-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--sp-3);
  margin-top: var(--sp-5);
}
</style>
