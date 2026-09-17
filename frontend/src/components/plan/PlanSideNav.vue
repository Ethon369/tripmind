<template>
  <!-- ---------------- 桌面：常驻侧栏 ---------------- -->
  <nav v-if="mode === 'sidebar'" class="sn-sidebar" aria-label="行程导航">
    <a-affix :offset-top="80">
      <ul class="sn-list">
        <li v-for="s in flatSections" :key="s.key">
          <button
            type="button"
            class="sn-item"
            :class="{ 'is-active': s.key === activeKey, [`sn-level-${s.level}`]: true }"
            :aria-current="s.key === activeKey ? 'true' : undefined"
            @click="emit('navigate', s.key)"
          >
            <span class="sn-icon" aria-hidden="true">{{ s.icon }}</span>
            <span class="sn-label">{{ s.label }}</span>
          </button>
        </li>
      </ul>
    </a-affix>
  </nav>

  <!-- ---------------- 手机/平板：横向 Pill 条 ----------------
       刻意不用 Drawer。抽屉一关锚点就消失了，
       用户读到第 3 天想跳回概览还得先打开抽屉再点一次；
       横向条常驻可见、单手可点、当前位置也能看出来。 -->
  <nav v-else class="sn-pills" aria-label="行程导航">
    <div class="sn-pills-scroll">
      <button
        v-for="s in flatSections"
        :key="s.key"
        type="button"
        class="sn-pill"
        :class="{ 'is-active': s.key === activeKey }"
        :aria-current="s.key === activeKey ? 'true' : undefined"
        @click="emit('navigate', s.key)"
      >
        <span aria-hidden="true">{{ s.icon }}</span>
        {{ s.label }}
      </button>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { computed } from 'vue';

export interface NavSection {
  key: string;
  label: string;
  /** 1 = 顶层（概览/预算/地图/天气），2 = 每日行程下的细项 */
  level?: 1 | 2;
  icon?: string;
}

const props = withDefaults(
  defineProps<{
    sections: NavSection[];
    activeKey: string;
    mode?: 'sidebar' | 'pills';
  }>(),
  { mode: 'sidebar' }
);

const emit = defineEmits<{ navigate: [key: string] }>();

/** 手机上的 Pill 条只显示顶层，避免横向滚动太长 */
const flatSections = computed(() => {
  if (props.mode === 'sidebar') return props.sections;
  const top = props.sections.filter((s) => s.level !== 2);
  // 每日行程那组压缩成一项，点进去滚到「每日行程」区块
  const hasDays = props.sections.some((s) => s.level === 2);
  return hasDays ? [...top, { key: 'days-anchor', label: '每日行程', level: 1, icon: '📅' }] : top;
});
</script>

<style scoped>
/* ---------------- 侧栏 ---------------- */
.sn-sidebar {
  width: 220px;
  flex-shrink: 0;
}

.sn-list {
  list-style: none;
  margin: 0;
  padding: var(--sp-2);
  background: var(--bg-card);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sn-item {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  width: 100%;
  padding: var(--sp-2) var(--sp-3);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-2);
  font-family: inherit;
  font-size: var(--fs-sm);
  text-align: left;
  cursor: pointer;
  min-height: 36px;
  transition: color var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out);
}

.sn-item:hover {
  color: var(--brand-600);
  background: var(--brand-50);
}

/* 选中态用浅底 + 左侧色条，而不是满色块 —— 侧栏里满色块会显得很重 */
.sn-item.is-active {
  color: var(--brand-600);
  background: var(--brand-50);
  font-weight: var(--fw-medium);
  box-shadow: inset 2px 0 0 var(--brand-500);
}

.sn-level-2 {
  padding-left: var(--sp-6);
  font-size: var(--fs-caption);
}

.sn-icon {
  font-size: 14px;
  line-height: 1;
}

/* ---------------- Pill 条 ---------------- */
.sn-pills {
  position: sticky;
  /* 顶栏高度由 CSS 变量给，窄屏是 56px。这里 +1px 盖住边框 */
  top: calc(var(--header-h) + 1px);
  z-index: 20;
  background: var(--bg-page);
  padding: var(--sp-2) 0;
  margin-bottom: var(--sp-2);
}

.sn-pills-scroll {
  display: flex;
  gap: var(--sp-2);
  overflow-x: auto;
  overflow-y: hidden;
  padding: 0 var(--sp-1) var(--sp-1);
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}

.sn-pills-scroll::-webkit-scrollbar {
  display: none;
}

.sn-pill {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  flex: 0 0 auto;
  min-height: var(--touch-min);
  padding: 0 var(--sp-4);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-pill);
  background: var(--bg-card);
  color: var(--text-2);
  font-family: inherit;
  font-size: var(--fs-sm);
  white-space: nowrap;
  cursor: pointer;
  transition: color var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out);
}

.sn-pill.is-active {
  color: var(--brand-600);
  background: var(--brand-50);
  border-color: var(--brand-500);
  font-weight: var(--fw-medium);
}

@media (max-width: 575px) {
  .sn-pills {
    top: calc(var(--header-h-mobile) + 1px);
  }
}
</style>
