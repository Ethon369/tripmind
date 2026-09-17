# 途灵 TripMind 前端界面设计文档

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | **已实施** —— 见 `docs/IMPLEMENTATION_REPORT.md`；视觉令牌以 `docs/DESIGN_SYSTEM.md` 为准 |
| 范围 | `frontend/` 全部界面、交互、响应式与无障碍 |
| 不在范围内 | 后端接口语义变更（除第 9 节标注的两项可选改造）、评测链路、RAG |
| 技术栈前提 | 沿用现有 Vue 3 + TS + Vite + Ant Design Vue 4，**不引入新的 UI 框架** |

> 本文档的每一条问题都标注了**证据**（文件 + 行号）。你可以逐条核对，也可以直接否定其中任意一条判断。
> 第 9 节是需要你拍板的 5 个决策点，第 10 节是按批次拆分的实施计划。

---

## 1. 现状盘点

### 1.1 代码地图

| 文件 | 行数 | 角色 | 备注 |
|---|---:|---|---|
| `frontend/src/views/Home.vue` | 656 | 行程表单 + 生成 | 巨石组件：标题、装饰、表单、假进度条全在一个文件 |
| `frontend/src/views/Result.vue` | 1499 | 行程详情 | 巨石组件：地图、导出、编辑、天气、预算全在一个文件 |
| `frontend/src/views/History.vue` | 220 | 历史列表 | 已用响应式 `a-col :xs/:sm`，是全项目最规范的一个 |
| `frontend/src/views/NotFound.vue` | 27 | 404 | 无问题 |
| `frontend/src/App.vue` | 99 | 外壳 | 头部 + 内容 + 尾部 |
| `frontend/src/main.ts` | 55 | 路由注册 | Home 同步加载，其余懒加载（这个决策是对的） |
| `frontend/src/services/api.ts` | 156 | 接口封装 | 结构清晰，保留 |
| `frontend/src/types/index.ts` | 148 | 类型契约 | 已与后端对齐，保留 |
| `frontend/src/components/` | **不存在** | — | README 里画了 `components/`，但目录从未创建 |

合计约 2757 行，其中 84% 集中在两个视图文件里。

### 1.2 后端可用接口（真实清单）

| 方法 | 路径 | 前端是否在用 | 说明 |
|---|---|---|---|
| POST | `/api/trip/plan` | 是 | 生成行程。**同步阻塞 30–60 秒** |
| GET | `/api/trip/health` | 封装了但没调用 | 返回 `mcp_tools_count`，正常应为 16 |
| GET | `/api/trip/plans` | 是 | 列表 + 聚合统计，支持 `limit`/`offset` |
| GET | `/api/trip/plans/{id}` | 是 | 详情。`status=running` 时 `success=false` 但 **HTTP 200** |
| PUT | `/api/trip/plans/{id}` | 是 | 回写编辑 |
| DELETE | `/api/trip/plans/{id}` | 是 | 删除 |
| GET | `/api/poi/photo?name=` | 是 | 景点配图（Result 里 N+1 调用） |
| GET | `/api/poi/detail/{poi_id}` | 否 | 未使用 |
| GET | `/api/poi/search` | 否 | 未使用 |
| GET | `/api/map/poi` | 否 | 未使用 |
| GET | `/api/map/weather` | 否 | 未使用（天气走的是行程数据里的 `weather_info`） |
| POST | `/api/map/route` | 否 | 未使用（路线是前端用坐标直连画折线） |

**关键约束：没有流式/SSE 接口。** 生成过程是一锤子买卖，前端拿不到任何中间进度。这直接决定了第 7.3 节的进度反馈设计。

### 1.3 问题清单

按"用户是否真的会碰到"排序。

#### P0 — 移动端实际上不可用

| # | 问题 | 证据 | 后果 |
|---|---|---|---|
| P0-1 | Home 表单用固定栅格 `:span="8"/"6"/"6"/"4"`，**没有任何响应式属性** | `Home.vue:33,50,64,78` | 375px 手机上"目的地城市"输入框只有约 100px 宽，日期选择器更窄到无法点击。antd 的 `:span` 是固定值，不会在窄屏自动堆叠 |
| P0-2 | 结果页左栏写死 `flex: 0 0 400px` | `Result.vue:1231-1236` | 375px 视口下 400px 固定列 → **整页横向溢出**，出现横向滚动条 |
| P0-3 | 结果页侧边导航写死 `width: 240px`，无断点 | `Result.vue:1053-1056` | 手机上主内容只剩约 120px |
| P0-4 | 全项目只有 **2 处** media query，且都是单一 768px 断点 | `App.vue:85`、`Result.vue:1488` | 平板（768–1024）这个区间完全没有适配，而这是最容易被忽略又最常见的尺寸 |

#### P1 — 反馈不诚实或缺失

| # | 问题 | 证据 | 后果 |
|---|---|---|---|
| P1-1 | 进度条是 `setInterval` 每 500ms 加 10% 的**假进度**，到 90% 就停 | `Home.vue:262-277` | 后端卡在 MCP 超时的 60 秒里，进度条一动不动停在 90%，用户会以为页面死了并刷新——直接丢掉这次生成 |
| P1-2 | 结果页加载期间会先闪一段"没有找到旅行计划数据" | `Result.vue:50` + `Result.vue:303` | `v-if="tripPlan"` 在 `await getPlan()` 完成前是 falsy，`v-else` 的 `a-empty` **先渲染出来**。用户看到"暂无数据 / 请先创建行程"，1 秒后才出现内容。这是典型的"加载态与空态混淆" |
| P1-3 | 每次进入结果页都弹 `message.success('地图加载成功')` | `Result.vue:916` | 用户来是看行程的，不是看地图加载成功的。属于噪音式反馈 |
| P1-4 | 删除景点**无撤销** | `Result.vue:498-509` | 误删只能靠"取消编辑"整体回滚，粒度太粗 |
| P1-5 | 编辑模式无离开保护 | `Result.vue` 全局 | 改到一半点"返回首页"或刷新，改动**静默丢失** |
| P1-6 | 日期超限时直接**清空**用户已选的结束日期 | `Home.vue:236-249` | 用户选了 35 天跨度，结果整个 `end_date` 被抹掉，得重选。这是破坏性反馈，且只配了一句 toast 解释 |

#### P1 — 视觉与可访问性

| # | 问题 | 证据 | 后果 |
|---|---|---|---|
| P1-7 | `.muted` 用 `rgba(0,0,0,0.45)` 配 12px 字号，白底对比度约 **3.36:1** | `History.vue:216-219` | 低于 WCAG 2.1 AA 对正文要求的 4.5:1。小字 + 低对比度是本项目最明确的合规缺口 |
| P1-8 | 三页视觉语言不统一 | Home 深紫渐变底 + 白卡；Result 浅蓝灰渐变；History 纯白 + antd 默认 | 像三个不同产品。且**大面积紫色渐变背景 + 白卡片**是典型的模板感配色 |
| P1-9 | emoji 当图标用，无 `aria-hidden` | 全项目，如 `App.vue:5`、`Result.vue:11-46` | 屏幕阅读器会把"✈️""🗺️"念出来；且 emoji 在 Windows/macOS/Linux 上造型完全不同，无法继承文字颜色，在深色底上对比度不可控 |
| P1-10 | 结果页地图 `min-height: 500px`，卡片体高度用 `calc(100% - 57px)` 硬编码卡头高度 | `Result.vue:1324-1332` | 手机上 500px 地图占掉整屏；`57px` 这个魔数在 antd 版本或字号变化后会错位 |

#### P2 — 一致性与可维护性

| # | 问题 | 证据 | 后果 |
|---|---|---|---|
| P2-1 | `exportAsImage` 与 `exportAsPDF` 有约 **130 行逐字重复的代码** | `Result.vue:604-738` 与 `741-895` | 改一处必漏另一处。两段唯一区别是最后输出 PNG 还是走 jsPDF |
| P2-2 | `sessionStorage` 键名 `'tripPlan'` 字符串散落两处 | `Home.vue:300`、`Result.vue:395,463` | 隐式全局契约，改键名要全局搜 |
| P2-3 | 交通/住宿/偏好选项硬编码在模板里 | `Home.vue:106-139` | 想加一个偏好标签要动模板 |
| P2-4 | 品牌渐变 `#667eea → #764ba2` 硬编码 10 次以上 | `Home.vue:328,525,571,597`、`Result.vue:1071,1107` | 换品牌色要全局替换 |
| P2-5 | `.form-section:hover` 让整个表单区块位移 2px | `Home.vue:452-455` | 鼠标划过输入区时区块会动，干扰输入定位 |
| P2-6 | 首屏对每个景点并发一个 `/api/poi/photo` 请求 | `Result.vue:543-558` | 20 个景点 = 20 个并发请求，且无懒加载、无去重、无失败重试 |
| P2-7 | 历史页固定取 50 条做前端分页 | `api.ts:94` + `History.vue:54` | 第 51 条之后的行程**永远看不到**，且用户完全无感。删除后也没重算分页 |
| P2-8 | 景点编辑允许改 `address`，但坐标 `location` 不可编辑 | `Result.vue:217` | 改了地址坐标还是旧值 → 地图标记与实际地址不符，且用户不知道 |

---

## 2. 设计目标与原则

### 2.1 三条目标

1. **任何尺寸可用**：320px 手机到 2560px 桌面，核心任务（填表 → 生成 → 看行程）全程不需要横向滚动。
2. **状态始终明确**：用户在任何时刻都能回答"现在发生了什么、我要等多久、出错了怎么办"。
3. **视觉收敛为一个产品**：统一令牌，统一四态，统一动效节奏。

### 2.2 五条原则

| 原则 | 含义 | 对应的反面案例 |
|---|---|---|
| 诚实优先 | 不知道进度就说不知道，不画假进度条 | P1-1 |
| 加载态 ≠ 空态 | 未拿到数据 ≠ 没有数据，两者的文案和视觉必须区分 | P1-2 |
| 破坏性操作可逆 | 删除给撤销，编辑给离开保护 | P1-4、P1-5 |
| 保护优于报错 | 用 `disabled-date` 让非法日期选不出来，而不是选完再弹错 | P1-6 |
| 只用令牌 | 不写死颜色、圆角、阴影、断点 | P2-4、P1-10 |

### 2.3 视觉方向调整（重要）

**保留**品牌紫 `#667eea → #764ba2` 作为识别色 —— 这是既有品牌资产，不推翻。

**收敛**它的使用面积：

| | 现在 | 建议 |
|---|---|---|
| 页面背景 | 大面积紫色渐变铺满 | 中性浅灰 `#f7f8fa`（结果页）/ 顶部一段品牌色 hero（首页，高度 ≤ 280px） |
| 卡片 | 半透明白 + 大阴影 | 纯白 + 1px 描边 + 极轻阴影 |
| 品牌紫用途 | 背景、按钮、标签、徽标、卡片头 | **仅**主按钮、选中态、进度、强调数字、hero 渐变 |

理由：紫色渐变铺满整屏会让内容失去视觉层级，也让品牌色失去强调作用；同时这是"AI 生成模板"最典型的视觉特征。收敛后页面更像产品，而非脚手架。

---

## 3. 设计令牌（Design Token）

新建 `frontend/src/styles/tokens.css`，全局引入。**所有组件不再写死颜色和圆角。**

### 3.1 色彩

```css
:root {
  /* ---- 品牌色 ---- */
  --brand-50:  #f0f3ff;
  --brand-100: #dfe5ff;
  --brand-300: #a3b0f5;
  --brand-500: #667eea;   /* 主色 */
  --brand-600: #5568d3;   /* hover */
  --brand-700: #764ba2;   /* 渐变终点 / active */
  --brand-gradient: linear-gradient(135deg, var(--brand-500) 0%, var(--brand-700) 100%);

  /* ---- 中性色（与 antd 对齐，保证混用不跳色） ---- */
  --text-1: rgba(0, 0, 0, 0.88);   /* 标题 */
  --text-2: rgba(0, 0, 0, 0.65);   /* 正文 / 次要信息，对比度 ≈ 7.0:1 ✓ AA */
  --text-3: rgba(0, 0, 0, 0.45);   /* 仅用于 ≥14px 的辅助说明 */
  --text-disabled: rgba(0, 0, 0, 0.25);
  --border-1: #e8e8e8;
  --border-2: #f0f0f0;
  --bg-page:  #f7f8fa;
  --bg-card:  #ffffff;
  --bg-sunken: #fafafa;

  /* ---- 语义色 ---- */
  --success: #52c41a;
  --warning: #faad14;
  --error:   #ff4d4f;
  --info:    #1677ff;

  /* ---- 行程状态色（与 History 的 tag 一一对应，全站统一） ---- */
  --status-ok:       #52c41a;   /* 已完成 */
  --status-fallback: #faad14;   /* 部分降级 */
  --status-error:    #ff4d4f;   /* 生成失败 */
  --status-running:  #1677ff;   /* 生成中 */
}
```

> **对比度修正**：`--text-3` 对比度约 3.36:1，**不满足 AA**，因此它的使用场景被限制为"≥14px 的辅助说明"，不得用于 12px 正文。原 `History.vue` 的 `.muted`（12px + 0.45）改为 12px + `--text-2`。

### 3.2 字体与字阶

```css
:root {
  --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
               'Hiragino Sans GB', 'Microsoft YaHei', 'Helvetica Neue', Arial, sans-serif;
  --font-num: 'SF Mono', 'Cascadia Mono', Consolas, monospace;  /* 金额/数字等宽，避免跳动 */

  --fs-display: clamp(26px, 5vw, 38px);  /* 收小：原 56px 固定值在手机上过大且不缩 */
  --fs-h1: 24px;
  --fs-h2: 20px;
  --fs-h3: 16px;
  --fs-body: 14px;
  --fs-sm: 13px;
  --fs-caption: 12px;

  --lh-tight: 1.3;   /* 标题 */
  --lh-body: 1.6;    /* 中文正文 */

  --fw-regular: 400;
  --fw-medium: 500;
  --fw-semibold: 600;
  --fw-bold: 700;
}
```

规则：正文行高用 1.6（中文比英文需要更多呼吸）；金额、Token 数、天数一律用 `--font-num` 等宽，避免数字变化时布局抖动。

### 3.3 间距 / 圆角 / 阴影

```css
:root {
  --sp-1: 4px;   --sp-2: 8px;   --sp-3: 12px;  --sp-4: 16px;
  --sp-5: 20px;  --sp-6: 24px;  --sp-8: 32px;  --sp-10: 40px;  --sp-12: 48px;

  --radius-sm: 8px;    /* tag、小按钮 */
  --radius-md: 12px;   /* 输入框、卡片 */
  --radius-lg: 16px;   /* 大卡片、面板 */
  --radius-xl: 24px;   /* 首页表单卡 */
  --radius-pill: 999px;

  --shadow-1: 0 1px 2px rgba(0, 0, 0, 0.04);                /* 卡片默认 */
  --shadow-2: 0 2px 8px rgba(0, 0, 0, 0.06);                /* 卡片 hover */
  --shadow-3: 0 8px 24px rgba(102, 126, 234, 0.12);         /* 浮起、聚焦容器 */
  --shadow-4: 0 16px 48px rgba(0, 0, 0, 0.12);              /* 弹层、hero 卡 */
}
```

### 3.4 断点（对齐 antd Grid，不自创）

| 名称 | 范围 | 典型设备 | 容器最大宽 |
|---|---|---|---|
| `xs` | < 576px | 手机竖屏（含 320/375/414） | 100% − 32px |
| `sm` | ≥ 576px | 手机横屏、大屏手机 | 100% − 40px |
| `md` | ≥ 768px | 平板竖屏 | 720px |
| `lg` | ≥ 992px | 平板横屏、小笔记本 | 960px |
| `xl` | ≥ 1200px | 桌面 | 1140px |
| `xxl` | ≥ 1600px | 宽屏 | 1320px（结果页内容上限 1400px） |

**断点是"最小改动能兼顾的尺寸"，不是设备清单。** 所有断点写在 `tokens.css` 里作为注释基准，媒体查询直接写像素值（CSS 变量不能用于 media query 条件）。

### 3.5 动效令牌

```css
:root {
  --dur-instant: 100ms;  /* 按下反馈 */
  --dur-fast:    150ms;  /* hover、focus */
  --dur-base:    240ms;  /* 展开、页面过渡 */
  --dur-slow:    400ms;  /* 大区块进场 */

  --ease-out:   cubic-bezier(0.16, 1, 0.3, 1);    /* 进场：快起慢收 */
  --ease-in:    cubic-bezier(0.7, 0, 0.84, 0);    /* 退场 */
  --ease-inout: cubic-bezier(0.4, 0, 0.2, 1);     /* 通用 */
}

/* 全局降级：尊重系统"减少动态效果"设置 */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

> **只允许动 GPU 属性**：`transform`、`opacity`、`filter`、`clip-path`。
> 严禁动 `width`/`height`/`top`/`left`/`margin`/`padding`/`font-size` —— 现有 `.weather-card:hover { transform: translateY(-4px) }`（`Result.vue:1143`）是合规写法，保留；`.ant-list-item:hover { transform: scale(1.02) }`（`Result.vue:1460`）会让整行缩放导致相邻内容抖动，改为阴影 + 描边变化。

### 3.6 令牌落地方式

1. `src/styles/tokens.css` 定义变量 → `main.ts` 全局引入，**第一顺位**。
2. `src/styles/antd-overrides.css` 用变量覆写 antd 组件（沿用项目已有的 `:deep()` 覆写模式，已验证可用）。
3. 可选：`<a-config-provider :theme="{ token: { colorPrimary: '#667eea', borderRadius: 12 } }">` 直接对齐 antd 内部 token。
   **落地前需先确认**本机 `ant-design-vue@4.2.6` 是否已导出 `theme`；若不可用则只走方案 1+2，不引入任何降级 hack。
4. 首页 hero、结果页侧栏等局部主题色用局部变量覆盖，不新建全局色。

---

## 4. 响应式设计规范

### 4.1 栅格使用规则（硬性）

**禁止使用无响应式属性的 `:span`。** 全部改写为阶梯式：

```html
<!-- ❌ 现在（Home.vue:33） -->
<a-col :span="8">

<!-- ✅ 改为：手机整行 → 平板半行 → 桌面 1/3 -->
<a-col :xs="24" :sm="24" :md="12" :lg="8">
```

### 4.2 各页面适配矩阵

#### 首页 Home

| 元素 | xs <576 | sm ≥576 | md ≥768 | lg ≥992 | xl ≥1200 |
|---|---|---|---|---|---|
| 页面内边距 | 16px | 24px | 32px | 48px | 48px |
| Hero 标题 | 26px | 30px | 34px | 38px | 38px |
| Hero 副标题 | 14px | 15px | 16px | 16px | 16px |
| 装饰浮动圆 | **隐藏**（省电省流量） | 1 个 | 2 个 | 3 个 | 3 个 |
| 表单卡圆角 | 16px | 16px | 20px | 24px | 24px |
| 表单卡内边距 | 16px | 20px | 24px | 32px | 32px |
| 城市 | `xs=24` | `sm=24` | `md=12` | `lg=8` | `lg=8` |
| 开始/结束日期 | `xs=24` 各占一行 | `sm=12` 并排 | `md=6` | `lg=6` | `lg=6` |
| 天数 | 并入日期行右侧，显示为"共 N 天"文字 | 同左 | `md=24` 独立一行 | `lg=4` 徽标方块 | `lg=4` |
| 交通 / 住宿 | `xs=24` | `sm=24` | `md=12` | `lg=8` | `lg=8` |
| 偏好标签 | `xs=24`，胶囊自动换行 | 同左 | `md=24` | `lg=8` | `lg=8` |
| 额外要求 | `xs=24` rows=4 | rows=4 | `md=24` rows=3 | rows=3 | rows=3 |
| 提交按钮 | 全宽、高 48px、`position: sticky; bottom: 16px` | 全宽 52px | 全宽 56px | 全宽 56px | 全宽 56px |

**天数控件的改动**：原设计是 `:span="4"` 的紫色小方块（`Home.vue:83-87`），在手机上必然被压扁。改为：
- `lg` 及以上：保留方块徽标，但与日期同排。
- `md` 及以下：改为一行文字提示 **"共 3 天 · 由起止日期自动计算"**，右对齐日期行，弱化为 `--text-2`，明确告知这是派生值不可手改。

**手机端提交按钮吸底**：表单较长，`xs`/`sm` 下按钮 `sticky` 吸底但仍随文档流（不遮挡最后一个字段，靠 `padding-bottom` 预留 72px）。

#### 结果页 Result

| 元素 | xs <576 | sm ≥576 | md ≥768 | lg ≥992 | xl ≥1200 |
|---|---|---|---|---|---|
| 页面内边距 | 12px | 16px | 20px | 32px | 40px |
| 侧边导航 | **改为顶部 sticky 横向滚动 Pill 条**（高 44px） | 同左 | 同左 | 240px 固定侧栏 + `a-affix` | 240px |
| 顶部信息区 | 纵向堆叠 | 纵向堆叠 | 纵向堆叠 | `400px + 1fr` 并排 | `400px + 1fr` |
| 左栏 | `1fr` | `1fr` | `1fr` | `0 0 400px` | `0 0 400px` |
| 地图高度 | 260px | 300px | 360px | `min 500px` | `min 500px` |
| 景点卡片列数 | **1 列** | 1 列 | 2 列 | 2 列 | 2 列 |
| 天气卡片列数 | **1 列** | 2 列 | 2 列 | 3 列 | 3 列 |
| 预算网格 | 2 列 | 2 列 | 2 列 | 2 列 | 2 列 |
| 每日行程 | 默认**全部收起**，仅展开第 1 天 | 同左 | 同左 | 展开第 1 天 | 展开第 1 天 |
| 操作按钮组 | 图标按钮 + 收纳进"更多"下拉 | 部分图标化 | 完整文字 | 完整文字 | 完整文字 |
| 页面头部 | 标题与按钮**上下堆叠**，按钮行横向滚动 | 堆叠 | 左右分排 | 左右分排 | 左右分排 |
| 地图卡片体高度 | 用 `flex: 1` 撑满，**删除 `calc(100% - 57px)`**（P1-10） | 同左 | 同左 | 同左 | 同左 |

**侧栏在手机上的处理取舍**：不用抽屉（Drawer）。抽屉一关锚点就消失了，用户浏览到第 3 天想跳回概览还得先打开抽屉。改为 **sticky 横向滚动 Pill 条**，锚点常驻、单手可点、滚动位置可见。

#### 历史页 History

| 元素 | xs <576 | sm ≥576 | md ≥768 | lg+ |
|---|---|---|---|---|
| 统计卡片 | 2×2（`:xs="12"`，保留） | 4×1 | 4×1 | 4×1 |
| 列表形态 | **卡片列表**（每行程一张卡） | 卡片列表 | 表格 + `scroll={{x:'max-content'}}` | 完整表格 |
| 表格隐藏列 | — | 隐藏"成本" | 隐藏"成本" | 全部 |
| 头部按钮 | 上下堆叠，右对齐 | 并排 | 并排 | 并排 |
| 分页 | 每页 5，前后翻页 | 每页 10 | 每页 20 | 每页 20 |

**为什么手机不用表格**：7 列合计约 970px 宽，横向滚动时表头与内容容易脱节，且"行程标题"这一最重要的信息被挤到左侧只能看到几个字。卡片列表能把"标题 + 城市 + 天数 + 状态"在 375px 里完整呈现。

#### 404 NotFound

居中单列，图标 64px（手机 48px），两个动作按钮在 `xs` 下改为竖排全宽。

### 4.3 触摸与输入差异

| 项 | 规则 |
|---|---|
| 最小触摸目标 | 44×44px（iOS HIG）/ 48×48px（Material）。现有小图标按钮（`Result.vue:174-194` 的 ↑↓🗑️）在手机上必须撑到 44px |
| 输入框高度 | 手机 ≥ 48px（现有 `size="large"` 是 40px，手机上加 `--sp-3` 内边距） |
| 输入字号 | 手机 ≥ 16px，**否则 iOS Safari 聚焦时会自动放大页面** |
| hover 语义 | 所有 hover 效果必须能在无 hover 设备上有等价表达（`@media (hover: hover)` 包裹） |
| 安全区 | `padding-bottom: max(16px, env(safe-area-inset-bottom))`，适配全面屏底部手势条 |
| 滚动容器 | 横向 Pill 条 `-webkit-overflow-scrolling: touch` + `scroll-behavior: smooth` |
| 视口高度 | 用 `min-height: 100dvh` 而非 `100vh`（现有 `App.vue:3` 用的 `100vh`，在移动端浏览器地址栏伸缩时会跳动） |

---

## 5. 页面与功能模块

### 5.1 路由表（建议保留现状，仅补两处）

| 路径 | 页面 | 改动 |
|---|---|---|
| `/` | Home | 无 |
| `/result/:id?` | Result | 无 |
| `/share/:id` | Result（只读） | 无 |
| `/history` | History | 无 |
| `/:pathMatch(.*)*` | NotFound | 无 |
| — | — | **新增**：`scrollBehavior` 返回 `{ top: 0 }`，现在切换路由不会重置滚动位置 |
| — | — | **新增**：路由级 `meta.title` + `document.title` 同步，现在所有页面标签页标题都是同一句 |

### 5.2 模块清单

#### M1 首页 · 行程表单

- **输入**：城市、起止日期、交通方式、住宿偏好、旅行偏好标签、额外要求
- **派生**：旅行天数（由日期自动计算，只读）
- **动作**：提交生成、重置表单
- **状态**：待填 → 校验失败（字段级错误）→ 生成中（见 M2）→ 成功跳转 / 失败提示
- **校验细节**：见 7.1

#### M2 首页 · 生成中面板

- 阶段文案轮播（不绑定百分比）
- 已用时长计时器
- 预计耗时提示 + "请勿关闭页面"
- 可中止？
- **详见 7.3，含 3 个待你选择的方案**

#### M3 结果页 · 行程概览

- 城市 + 日期区间 + 整体建议
- 降级行程顶部警示条（`meta.status === 'fallback'` 时）
- 生成元信息（可选折叠）：耗时、Token、成本、LLM 调用次数 —— 数据已在 `PlanSummary` 里，现在只展示在历史页

#### M4 结果页 · 预算明细

- 4 项分项（门票/住宿/餐饮/交通）+ 总计
- 金额等宽字体，进入视口时数字滚动动画（`prefers-reduced-motion` 下直接显示终值）
- 手机上总额吸顶在预算卡内（不吸全局）

#### M5 结果页 · 景点地图

- 高德 JS API，按天分色折线
- **首屏中心点用行程第一个景点的坐标**，不再硬编码北京（`Result.vue:909`），避免先闪一下北京再 `setFitView` 跳走
- 地图与列表**双向联动**：点标记 → 滚动并高亮对应景点卡；点景点卡"在地图查看" → 地图 `setCenter` + 打开 InfoWindow
- 手机上提供"收起地图"按钮（260px 地图仍占屏）
- 地图加载失败时降级为静态提示，**不弹全局 message**（P1-3）
- 无障碍：地图容器加 `role="img"` + `aria-label` 描述景点数量，并提供"景点列表"作为等价文本

#### M6 结果页 · 每日行程

- 折叠面板，每天一个 Panel
- 每天含：描述 / 交通 / 住宿 / 景点列表 / 酒店 / 餐饮
- 编辑模式（仅非分享模式）：景点增删、上下移、改地址/时长/描述
- **改动**：编辑模式禁用 `a-collapse` 的 `accordion`（改一个景点时其余天不该自动收起），且编辑态展开全部天

#### M7 结果页 · 天气

- 按天卡片，显示白天/夜间天气与温度、风向风力
- 手机 1 列，且把"日期"作为卡片标题而不是左上角小字

#### M8 结果页 · 操作组

| 动作 | 现状 | 改动 |
|---|---|---|
| 编辑行程 / 保存 / 取消 | 有 | 取消时若已修改需二次确认 |
| 复制分享链接 | 有，失败时把 URL 塞进 toast | 改为 **Modal + 只读输入框 + 复制按钮**（P1-6 同类问题） |
| 导出行程 → 图片 / PDF | 有，代码重复 130 行 | 抽出 `usePlanExport()` composable，两条路径共用采集与预处理逻辑 |
| 删除行程 | **仅历史页有** | 结果页也提供，带 `a-popconfirm` |

#### M9 历史页

- 聚合统计 4 卡
- 列表（表格 / 卡片列表双形态）
- 刷新、新建
- **新增**：搜索框（按城市/标题，前端在已取数据内过滤，见 9.5）、状态筛选、排序（默认按创建时间倒序）
- **修正**：分页与 `limit` 联动（P2-7）—— 列表接口支持 `offset`，改为服务端分页，`total` 用 `stats.total`

#### M10 404

- 居中，说明 + "返回首页" + "查看历史行程"

---

## 6. 组件结构划分

### 6.1 目标目录树

```
frontend/src/
├── styles/
│   ├── tokens.css              # 设计令牌（第 3 节）
│   ├── base.css                # reset + 全局排版 + 焦点样式
│   ├── antd-overrides.css      # antd 组件令牌对齐
│   └── transitions.css         # 过渡/动画 keyframes
├── components/
│   ├── layout/
│   │   ├── AppHeader.vue       # 头部导航（含移动端菜单）
│   │   ├── AppFooter.vue
│   │   └── PageShell.vue       # 容器宽度 + 内边距 + 页面进场动画
│   ├── common/
│   │   ├── StatePanel.vue      # ★ 四态统一出口 loading/empty/error/success
│   │   ├── SectionCard.vue     # 统一卡片（标题 + 动作 + 内容）
│   │   ├── CopyLinkModal.vue   # 复制分享链接
│   │   ├── ConfirmDialog.vue   # 二次确认（取消编辑 / 删除）
│   │   └── AnimatedNumber.vue  # 金额/统计数字滚动
│   ├── trip/
│   │   ├── TripForm.vue        # 表单编排 + 提交
│   │   ├── TripFormBasics.vue  # 城市/日期/天数
│   │   ├── TripFormPrefs.vue   # 交通/住宿/偏好标签
│   │   ├── TripFormExtra.vue   # 额外要求 + 提交
│   │   ├── PreferenceTags.vue  # 胶囊多选
│   │   └── GeneratingPanel.vue # ★ 生成中状态
│   ├── plan/
│   │   ├── PlanSideNav.vue     # 桌面侧栏 / 移动 Pill 条（同一组件两种形态）
│   │   ├── PlanOverview.vue
│   │   ├── BudgetCard.vue
│   │   ├── AttractionMap.vue
│   │   ├── AttractionCard.vue
│   │   ├── DayPlanPanel.vue
│   │   ├── HotelCard.vue
│   │   ├── MealList.vue
│   │   ├── WeatherStrip.vue
│   │   └── PlanSkeleton.vue    # ★ 结果页骨架屏
│   └── history/
│       ├── PlanStatsRow.vue
│       ├── PlanTable.vue
│       ├── PlanCardList.vue    # 移动端形态
│       └── PlanStatusTag.vue   # 状态 tag（全站唯一定义）
├── composables/
│   ├── useTripForm.ts          # 表单状态 + 校验 + 日期联动
│   ├── usePlanCache.ts         # ★ sessionStorage 契约唯一入口
│   ├── useAttractionPhotos.ts  # 配图批量获取 + 缓存 + 并发限流
│   ├── usePlanExport.ts        # 导出图片/PDF 共用逻辑
│   ├── useUnsavedGuard.ts      # 编辑未保存离开保护
│   └── useScreen.ts            # 断点响应式（isMobile / isTablet）
├── constants/
│   ├── storage.ts              # STORAGE_KEYS（消除 P2-2）
│   └── tripOptions.ts          # 交通/住宿/偏好选项（消除 P2-3）
├── services/api.ts             # 保留，可按域拆成 trip.ts / poi.ts
├── types/index.ts              # 保留
└── views/
    ├── Home.vue                # 薄壳：只做编排
    ├── Result.vue              # 薄壳：只做编排
    ├── History.vue             # 薄壳
    └── NotFound.vue
```

### 6.2 关键组件契约

#### `StatePanel.vue` — 四态统一出口

| Prop | 类型 | 必填 | 说明 |
|---|---|---|---|
| `state` | `'loading' \| 'empty' \| 'error' \| 'success'` | 是 | 只允许这四种 |
| `title` | `string` | 否 | 主文案 |
| `description` | `string` | 否 | 补充说明 |
| `errorKind` | `'network' \| 'server' \| 'notFound' \| 'business'` | 否 | `error` 时决定文案与恢复动作 |
| `onRetry` | `() => void` | 否 | 传了才渲染"重试"按钮 |
| `skeleton` | `'plan' \| 'list' \| 'table'` | 否 | `loading` 时的骨架形态 |

**Slot**：`extra`（额外动作）、`default`（`success` 时的内容）

**这个组件存在的意义**：P1-2 那个 bug（加载中显示"暂无数据"）之所以会发生，是因为"四态"没有统一出口，`v-if/v-else` 随手写就会把 loading 和 empty 混在一起。强制走一个组件后，`state='loading'` 就不可能渲染出空态文案。

#### `PlanCache`（`usePlanCache.ts`）— 消除隐式 sessionStorage 契约

```ts
// constants/storage.ts
export const STORAGE_KEYS = {
  TRIP_PLAN: 'tripPlan',
} as const;

// composables/usePlanCache.ts
export function usePlanCache() {
  return {
    save(plan: TripPlan): void,
    /** 读取并做一次结构校验；解析失败时自动清理脏数据并返回 null */
    load(): TripPlan | null,
    clear(): void,
  };
}
```

替代 `Home.vue:300` 与 `Result.vue:395,463` 的直接调用。顺带解决现有 `Result.vue:401-409` 的坏数据处理（逻辑是对的，但散在组件里）。

#### `PlanSideNav.vue` — 一组件两形态

| Prop | 类型 | 说明 |
|---|---|---|
| `sections` | `Array<{key, label, level?}>` | 由行程数据生成 |
| `activeKey` | `string` | 当前锚点 |
| `mode` | `'sidebar' \| 'pills'` | `lg` 及以上由父级传 `sidebar`，否则 `pills` |

**Emit**：`navigate(key)`

内部用 `useScreen()` 判断还是由父级传入？**由父级传入 `mode`** —— 组件不该自己判断视口，否则 SSR/测试都会变复杂。`useScreen()` 只在壳层视图里用。

#### `AttractionCard.vue`

| Prop | 类型 | 说明 |
|---|---|---|
| `attraction` | `Attraction` | 景点数据 |
| `index` | `number` | 序号（展示 + 地图标记编号） |
| `dayIndex` | `number` | 所属天 |
| `editable` | `boolean` | 是否编辑模式 |
| `photo` | `string \| undefined` | 由父级统一获取后传入，**组件内不发请求** |

**Emit**：`update(partial)`、`remove()`、`move(dir)`、`locate()`（在地图查看）
**Slot**：`actions`（编辑按钮组）

`photo` 由父级传入很关键：现在每个卡片自己发请求会造成 P2-6 的问题，收敛到 `useAttractionPhotos` 后可以做限流（并发 ≤ 4）、去重、失败降级到渐变占位图。

### 6.3 拆分优先级

| 顺序 | 组件 | 理由 |
|---|---|---|
| 1 | `StatePanel` + `PlanSkeleton` | 立刻修掉 P1-2，且是后续所有页面的公共依赖 |
| 2 | `usePlanExport` | 消除 130 行重复（P2-1），纯抽取，零行为变更，风险最低 |
| 3 | `PlanTable` / `PlanCardList` / `PlanStatusTag` | History 最小，先拆完验证拆分手法 |
| 4 | `Result.vue` 拆 `plan/*` | 1499 → 目标 200 行以内 |
| 5 | `Home.vue` 拆 `trip/*` | 656 → 目标 150 行以内 |
| 6 | `PlanSideNav` / `AppHeader` | 需要新断点，放在响应式批次 |

---

## 7. 交互细节规范

### 7.1 表单校验

#### 校验规则表

| 字段 | 规则 | 后端约束 | 对齐说明 |
|---|---|---|---|
| `city` | 必填、trim 后非空、≤ 20 字符 | `str` 无长度限制 | 前端加长度上限做保护，避免超长城市名拖垮 prompt |
| `start_date` | 必填、**不早于今天** | 无校验 | 新增，现在可以生成昨天的行程 |
| `end_date` | 必填、≥ `start_date`、跨度 ≤ 30 天 | `travel_days: ge=1, le=30` | 严格对齐后端的 30 天上限 |
| `travel_days` | **只读派生** = `end - start + 1` | `ge=1, le=30` | 保持一致；绝不允许手改，否则前端算的和后端校验的会打架 |
| `transportation` | 必填，枚举 | 无枚举约束 | 用 `a-select` 先天限制 |
| `accommodation` | 必填，枚举 | 无枚举约束 | 同上 |
| `preferences` | 可选，**最多 3 个** | `List[str]` 无上限 | 新增上限：标签过多会让规划 prompt 发散，也与"偏好"语义不符 |
| `free_text_input` | 可选、≤ 200 字符 | 无长度限制 | 前端加计数提示 `已输入 42/200` |

#### 日期联动（修改 P1-6）

**保护优于报错**：给两个 picker 都设 `disabled-date`：

```ts
// 开始日期：不能早于今天，不能晚于 (end - 30天) 或 end
const disabledStart = (d: Dayjs) =>
  d.isBefore(dayjs().startOf('day')) ||
  (form.end_date ? d.isAfter(form.end_date) : false);

// 结束日期：不能早于 start，不能晚于 start + 29 天
const disabledEnd = (d: Dayjs) =>
  d.isBefore(form.start_date ?? dayjs().startOf('day')) ||
  (form.start_date ? d.isAfter(form.start_date.add(29, 'day')) : false);
```

这样非法的日期区间**根本选不出来**，从源头消灭"选完再报错再清空"的体验。

若用户先选结束日期、再改开始日期导致区间超限，则**收缩**而非清空：

| 情况 | 现在的行为 | 改为 |
|---|---|---|
| 区间 > 30 天 | 清空 `end_date` + toast | `end_date = start + 29 天`，并在结束日期下方显示 `--error` 色说明："已自动调整为 30 天上限" |
| `end < start` | 清空 `end_date` + toast | 同 `disabled-date` 拦截，理论上不会发生 |

#### 校验时机

| 时机 | 行为 |
|---|---|
| `blur` | 校验该字段 |
| `change` | 已出过错的字段重新校验；未出过错的字段不打扰 |
| `submit` | 全量校验；首个错误字段 `scrollIntoView({block:'center'})` + `focus()` |
| 校验中 | 提交按钮 `loading`，**禁用二次提交** |

#### 错误展示

- 位置：**字段下方**，`--error` 色，12px，带 4px 上间距
- 图标：错误字段加 `a-form-item` 的 `validate-status="error"`，边框变红
- 汇总：手机端若错误字段不在视口内，顶部弹一条 `a-alert` 汇总"N 个字段需要修改"
- 无障碍：错误容器 `role="alert"`，`aria-live="polite"`

#### 提交反馈

| 结果 | 反馈 |
|---|---|
| `success=true` | 按钮变成功态 → 跳转结果页（**跳转前不弹 toast**，页面切换本身就是反馈） |
| `success=false`（降级） | `a-modal` 展示 `response.message`（含真实失败原因）+ "查看历史记录"按钮。**不用 toast** —— 原因文案较长，toast 会一闪而过 |
| HTTP 500 | `a-alert` 内联在表单上方，显示后端 `detail` + 重试按钮 |
| 网络超时 | 同上，文案区分"网络"与"服务端错误" |

> 现有 `Home.vue:310` 用 `message.error(response.message)` 展示降级原因。后端在 `trip.py:121` 特意把**失败原因**拼进了 `message`（注释里说明这是用户唯一能看到的地方），用 3 秒消失的 toast 承接这段精心写的文案是浪费。

### 7.2 四态强制要求

**每个数据区域都必须显式实现四种状态，不允许省略。**

| 态 | 视觉 | 文案范例 | 恢复动作 |
|---|---|---|---|
| loading | 骨架屏（形状贴近真实内容），`aria-busy="true"` | 无文字或"正在加载行程…" | — |
| empty | 插画/图标 + 一句说明 + 主操作 | "还没有任何行程" / "去生成第一个行程" | 主按钮 |
| error | `a-alert` 或 `StatePanel` ，`--error` 色 | 按 `errorKind` 分四种（见下） | 重试按钮 |
| success | 内容 | — | — |

**错误必须区分类型**（这是现有代码做得不够的地方）：

| errorKind | 触发 | 文案 | 动作 |
|---|---|---|---|
| `network` | 无响应 / 超时 | "连不上服务，请检查后端是否已启动" | 重试 |
| `server` | HTTP 5xx | "服务端出错了" + `detail` | 重试 |
| `notFound` | HTTP 404 | "该行程不存在或已被删除" | 返回首页 / 看历史 |
| `business` | HTTP 200 但 `success=false` | 直接用后端 `message`（含降级原因） | 看历史 / 重新生成 |

现有 `History.vue:39-46` 已经做了 `network` 级别的区分（值得保留），但结果页没有。

### 7.3 生成中状态设计（**需决策，见 9.1**）

现状：`setInterval` 每 500ms 加 10%，封顶 90%。这是**编造的进度**。

后端事实：`POST /api/trip/plan` 同步返回，耗时 30–60 秒（4 次 LLM + MCP 工具），前端拿不到任何中间信号。

三个方案：

**方案 A — 不确定进度（推荐，成本最低）**

```
┌──────────────────────────────────────┐
│  ⟳  正在为你规划北京 3 天行程         │
│                                      │
│  ▓▓▓▓▓▓░░░░░░░░░░░░  ← 循环流动动画   │
│  不显示百分比                          │
│                                      │
│  ⏱ 已用 24 秒 · 通常需要 30–60 秒      │
│  请勿关闭或刷新页面                    │
└──────────────────────────────────────┘
```

- 进度条改为 antd `a-progress` 的 `status="active"` 但**不传 `percent`**，用流动光带表示"正在进行"，不表示"完成 40%"。
- 计时器每秒更新，让用户确信页面还活着（这是假进度条想解决的真问题，但用诚实的方式解决）。
- 超过 90 秒追加一行："比平时慢一些，后端可能正在重试工具调用"。

**方案 B — 推测阶段文案（不显示百分比）**

保留现在 4 段阶段文案的轮播，但**去掉百分比**，改为循环轮播 + "进行中"的脉冲动效。

```ts
// 仅作示意：文案是推测，不是真实阶段
const STAGES = ['正在搜索景点…', '正在查询天气…', '正在推荐酒店…', '正在整合行程…'];
```

- 优点：保留了"系统在忙什么"的信息感。
- 风险：文案是猜的。若实际卡在第一步，用户会看到"正在推荐酒店"但什么都没发生 —— 仍是温和的误导。

**方案 C — 真阶段（需改后端）**

后端已有 `observer` 记录各阶段耗时与降级信息（`trip.py:69-73`），但只在生成结束后落库，不外发。新增：

- `POST /api/trip/plan` 增加可选 `stream=true`，或新增 `GET /api/trip/plan/{task_id}/events`（SSE）
- 前端用 `EventSource` 接收阶段事件，展示**真实**阶段 + 真实耗时

- 成本：后端要引入任务队列或 SSE 响应，`plan_trip()` 现在是同步阻塞的线程池函数（`trip.py:28` 的注释解释了为什么必须是同步 `def`）。改造需谨慎，属于"能不动就不动"的区域。
- 建议：**作为 P3 后续增强，不进本次范围。**

**我的建议：方案 A。** 理由：它用最低成本解决了假进度条的核心痛点（用户无法判断页面是否还活着），且不引入任何新的不诚实。

### 7.4 反馈矩阵

| 动作 | 反馈形式 | 时长 | 可撤销 |
|---|---|---|---|
| 表单字段校验失败 | 字段下方内联错误 | 持续到修正 | — |
| 生成成功 | 页面跳转 | — | — |
| 生成降级 | Modal（含原因） | 手动关闭 | — |
| 复制分享链接成功 | `message.success` | 2s | — |
| 复制失败（无剪贴板权限） | **Modal + 输入框**（改） | 手动关闭 | — |
| 保存编辑成功 | `message.success` | 2s | — |
| 保存编辑失败 | `a-alert` 常驻在编辑区顶部 + 保持编辑态 | 持续 | — |
| 删除景点 | `message.success` + **撤销按钮**（改） | 5s | **是** |
| 删除行程 | `a-popconfirm` → `message.success` | 2s | 否（删除即永久，文案已警示） |
| 地图加载失败 | 卡片内 `a-alert`（改：不再全局弹） | 持续 | 重试 |
| 配图加载失败 | 静默降级为渐变占位图 | — | — |
| 网络请求进行中（列表） | 表格/卡片 `loading` | — | — |
| 编辑未保存时离开 | `a-modal` 二次确认（新） | 手动 | — |

**禁止**：进入页面即弹 toast（P1-3）；用 toast 承载超过一行或需要用户阅读的文案（P1-6）。

### 7.5 动效规范

| 场景 | 属性 | 时长 | 缓动 |
|---|---|---|---|
| 页面切换 | `opacity` + `translateY(8px→0)` | 240ms | `--ease-out` |
| 卡片列表进场 | `opacity` + `translateY(12px→0)`，`delay = min(index,8) × 30ms` | 320ms | `--ease-out` |
| 折叠面板展开 | 用 antd 内建（`height` 由 antd 处理，不自己动） | 240ms | `--ease-inout` |
| 按钮按下 | `scale(0.98)` | 100ms | `--ease-out` |
| 卡片 hover | `box-shadow` + `border-color`；`translateY(-2px)` 仅桌面 | 150ms | `--ease-out` |
| 骨架屏 | `background-position` 流光 | 1.4s 循环 | `linear` |
| 数字滚动 | `textContent` 插值（非 CSS） | 600ms | `--ease-out` |
| 地图标记出现 | antd 不适用，用高德自带 | — | — |
| **焦点环** | `outline: 2px solid var(--brand-500); outline-offset: 2px` | 0ms | — |

**规则**：
- 页面级过渡用 `<router-view v-slot="{ Component }">` + `<Transition>`，**不要**用 KeepAlive（结果页持有地图实例，缓存会累积内存）
- 列表进场动画只在**首次渲染**播放，筛选/翻页后不重播（否则每次点"下一页"都闪一遍）
- 禁止：列表项 hover 整行 `scale()`（P3-4）；`transition: all`（现有代码大量使用 `transition: all 0.3s ease`，改为显式属性列表，`all` 会导致无关属性（如 `color`）意外参与过渡，且无法被浏览器优化）
- 焦点环**不得**用 `box-shadow` 模拟（会被 `overflow: hidden` 裁掉），用 `outline`

### 7.6 无障碍（WCAG 2.1 AA）

| 项 | 要求 | 现状 |
|---|---|---|
| 对比度 | 正文 ≥ 4.5:1，大字 ≥ 3:1 | ❌ `.muted` 12px + 0.45 仅 3.36:1 |
| 非文本对比度 | 输入框边框、图标 ≥ 3:1 | ✅ antd 默认满足 |
| 颜色非唯一信息载体 | 状态 tag 有文字，不只靠颜色 | ✅ History 的 tag 同时有文字 |
| 键盘可达 | 所有操作可 Tab 到 + Enter/Space 触发 | ❌ 侧栏锚点、折叠面板头部需验证 |
| 焦点可见 | 每个可聚焦元素有可见焦点环 | ⚠️ antd 默认有，但被 `outline: none` 覆写处需检查 |
| 表单标签关联 | `label[for]` ↔ `input[id]` | ⚠️ Home 用了 `<template #label>` 自定义 span，需验证 `for/id` 仍正确生成 |
| 图标语义 | 装饰性图标 `aria-hidden="true"`；功能性图标配 `aria-label` | ❌ emoji 全裸露 |
| 图片替代文本 | 有意义图片给 `alt`；纯装饰给 `alt=""` | ⚠️ `Result.vue:203` 的 `:alt="item.name"` 是对的 |
| 动态内容播报 | 加载/错误区域 `aria-live` | ❌ 无 |
| 地图等价文本 | 图形内容提供文本替代 | ❌ 无 |
| 动效偏好 | 尊重 `prefers-reduced-motion` | ❌ 无 |
| 缩放 | 200% 缩放不丢内容 | ⚠️ 固定宽度布局在 200% 下会溢出 |
| 触摸目标 | ≥ 44×44px | ❌ 小图标按钮未达标 |

**emoji 图标的处理建议**（对应 9.2）：

| 类型 | 例子 | 建议 |
|---|---|---|
| 功能性图标（按钮、菜单、导航） | `Result.vue:11-46` 的 ✏️💾🔗📥 | **改用 `@ant-design/icons-vue`**（已安装）。理由：可继承文字色、可设 `aria-label`、跨平台渲染一致 |
| 状态/插画性图标（空态、404） | `Result.vue:305` 的 🗺️ | 保留大型 emoji 或用 antd icon 放大，配 `aria-hidden` |
| 品牌装饰 | `App.vue:5` 的 🌍 | 保留，配 `aria-hidden="true"` |
| 选项前缀 | `Home.vue:106-123` 的 🚇🚗 | 移除（下拉选项文字已足够清晰），或用 icon 组件 |

---

## 8. 状态管理与数据流

### 8.1 决策：**不引入 Pinia**

理由：

1. **真正的问题不是"缺少 store"**，而是隐式的 `sessionStorage` 契约（P2-2）和巨石组件。引入 Pinia 后这两个问题都还在。
2. 项目规模：4 个页面，唯一跨页面共享的状态是"刚生成的行程"（走 `sessionStorage`）和"历史列表"（一次性拉取，不需要共享）。
3. 项目一贯哲学是"不引不必要的库"（README 明确说明不引 ORM、不引 LangChain 的编排、检索层自己写）。在这个项目里塞一个 Pinia 与整体风格不符。
4. `Result.vue` 的编辑态是**局部**状态，用 Pinia 反而会让"取消编辑"的回滚逻辑更绕。

**替代方案**：`composables/` + 显式 props/emit。

- `usePlanCache()` —— 唯一的数据持久化入口，替代裸 `sessionStorage` 调用
- `useAttractionPhotos()` —— 配图的共享缓存（同一景点在概览和每日行程里都要用，避免重复请求）
- 其余状态留在组件内

**若后续出现第三个消费方**（例如新增"行程对比"页面），再评估引入 Pinia。届时改造成本很低，因为状态已经被 composable 收口了。

### 8.2 数据流

```
Home.vue
  └─ TripForm ──提交──> api.generateTripPlan()
        │
        ├─ 成功 ──> usePlanCache().save(data)
        │            └─ router.push(`/result/${plan_id}`)
        └─ 降级 ──> Modal(message) ──> /history

Result.vue
  ├─ 有 :id ──> api.getPlan(id) ──> renderPlan(plan)
  └─ 无 :id ──> usePlanCache().load() ──> renderPlan(plan)
                   │
                   └─ null ──> 引导回首页（不再白屏 / 不再闪空态）

useAttractionPhotos(plan)
  └─ 批量取图（并发 ≤ 4，失败降级占位）──> AttractionCard(photo)
```

**plan_id 是唯一真相来源**：无 id 的 `/result` 路径（sessionStorage 回落）仅作兼容保留，新流程一律带 id 跳转。`Result.vue` 的三种数据来源（CLAUDE.md:204 有记录）保持不变，但路径 2 的优先级降低。

---

## 9. 待确认决策点

以下 5 项需要你拍板，我按"我的建议"给出默认方案。**若不回复，我按建议方案执行。**

### 9.1 生成中状态用哪个方案？

| 选项 | 成本 | 诚实度 |
|---|---|---|
| **A. 不确定进度 + 计时器**（建议） | 低 | 高 |
| B. 推测阶段文案，去掉百分比 | 低 | 中 |
| C. 后端加 SSE 推真阶段 | 高（需改后端） | 最高 |

→ 详见 7.3。**我建议 A。**

### 9.2 emoji 是否统一替换为图标库？

| 选项 | 效果 |
|---|---|
| **A. 功能性图标改 `@ant-design/icons-vue`，装饰性 emoji 保留**（建议） | 一致性 + 可访问性 + 跨平台稳定，视觉改动可控 |
| B. 全部 emoji 替换为图标 | 最统一，但首页 hero 的 ✈️ 等品牌装饰会失去活泼感 |
| C. 保持现状，仅补 `aria-hidden` | 零视觉改动，但跨平台渲染差异和对比度问题仍在 |

→ 详见 7.6。**我建议 A。**

### 9.3 是否引入 Pinia？

**我建议不引**，理由见 8.1。若你希望为后续扩展留出空间，也可以引，但需要你确认这是有意的投入。

### 9.4 视觉方向是否按第 2.3 节收敛？

要点：页面背景改为中性浅灰，品牌紫从"大面积背景"收敛为"仅强调色"，首页保留一段 ≤280px 的渐变 hero。

| 选项 | 效果 |
|---|---|
| **A. 按 2.3 节收敛**（建议） | 更有产品感，视觉层级清晰，去掉模板感 |
| B. 保留现有大面积紫色渐变 | 视觉改动最小，但 P1-8 不解决 |

### 9.5 历史页是否需要搜索/筛选？

| 选项 | 成本 |
|---|---|
| **A. 前端在已取数据内过滤**（建议） | 低。前提是把 `limit` 提到 200（接口上限），并做服务端分页 |
| B. 后端加 `city` / `status` / `q` 查询参数 | 需改后端 |
| C. 不做，只修分页缺陷 | 最低 |

→ 注意：**无论选哪个，P2-7（第 51 条之后的行程看不到）都必须修**，这是 bug 不是特性。

---

## 10. 实施计划

按"风险从低到高、每批都可独立验收"排序。**每批结束后跑 `npm run build`（含 `vue-tsc` 类型检查）确保不退化。**

### 批次 1 — 地基（不改业务逻辑）

- 新建 `styles/tokens.css`、`base.css`、`antd-overrides.css`
- 全局引入；把现有硬编码颜色/圆角替换为令牌（纯替换，视觉应基本不变）
- `constants/storage.ts`、`constants/tripOptions.ts`
- 新增 `usePlanCache`，替换 3 处 `sessionStorage` 直接调用
- `App.vue`：`100vh` → `100dvh`，头部导航加移动端适配，emoji 补 `aria-hidden`
- 路由补 `scrollBehavior` 与 `meta.title`

**验收**：`npm run build` 通过；页面视觉与改动前一致（截图对比）；`grep -rn "667eea" src/views` 应为空。

### 批次 2 — 组件拆分（纯重构）

- `usePlanExport` 抽取，消除 130 行重复
- 拆 `history/*`（最小，先验证手法）
- 拆 `plan/*`、`trip/*`
- `StatePanel` + `PlanSkeleton`

**验收**：`Result.vue` ≤ 250 行，`Home.vue` ≤ 200 行；所有既有功能行为不变（手动过一遍：生成 → 查看 → 编辑 → 保存 → 导出 → 分享 → 历史 → 删除）；构建通过。

### 批次 3 — 响应式与状态

- 全部 `:span` 改阶梯式
- `PlanSideNav` 双形态（侧栏 / Pill 条）
- Home 表单栅格重排 + 手机吸底按钮
- History 卡片列表形态
- 结果页：地图/景点/天气列数与高度断点
- 四态补全（尤其修 P1-2 的加载闪空态）
- 表单校验规则落地（`disabled-date`、30 天收缩、字符计数）

**验收**：在 320 / 375 / 768 / 1024 / 1440 五个宽度下逐页检查，**无横向滚动条**；表单所有非法输入在 UI 上无法产生。

### 批次 4 — 体验与合规

- 动效令牌落地，替换 `transition: all`
- 撤销删除、未保存离开保护、复制链接 Modal
- 降级原因 Modal 替代 toast
- 对比度修正、焦点环、`aria-live`、触摸目标 ≥44px
- `prefers-reduced-motion` 全局降级
- 结果页地图与列表双向联动；地图首屏中心点改为行程坐标
- 配图并发限流

**验收**：Lighthouse 无障碍 ≥ 90；键盘走完全流程（Tab 到每个操作）；开启系统"减少动态效果"后无动画。

### 批次 5（可选）— 后端配合

- 若选 9.1 方案 C：SSE 阶段推送
- 若选 9.5 方案 B：列表查询参数

---

## 11. 验收清单

### 响应式
- [ ] 320 / 375 / 414 / 768 / 1024 / 1440 / 1920 七个宽度无横向滚动
- [ ] 手机端所有可点元素 ≥ 44×44px
- [ ] 手机端输入框字号 ≥ 16px（iOS 不缩放）
- [ ] `100dvh` 替代 `100vh`，移动端地址栏伸缩不跳动
- [ ] 横竖屏切换布局正确
- [ ] 200% 浏览器缩放不丢内容

### 交互与反馈
- [ ] 加载态、空态、错误态在**每个**数据区域都显式实现
- [ ] 加载中**绝不**出现"暂无数据"类文案
- [ ] 错误按 `network`/`server`/`notFound`/`business` 四类区分文案与动作
- [ ] 破坏性操作可撤销或有二次确认
- [ ] 编辑未保存时离开有拦截
- [ ] 无"进页面即弹 toast"的情况
- [ ] 长文案（降级原因、分享链接）用 Modal 而非 toast

### 表单
- [ ] 每条前端规则与后端约束核对一致（尤其 30 天上限）
- [ ] 非法日期在 picker 上**选不出来**
- [ ] 首个错误字段自动滚动 + 聚焦
- [ ] 提交中按钮 disabled，无法二次提交
- [ ] `travel_days` 不可手改

### 无障碍
- [ ] 正文对比度 ≥ 4.5:1，大字 ≥ 3:1
- [ ] 装饰性图标 `aria-hidden`，功能性图标有 `aria-label`
- [ ] 键盘可完成全流程（含锚点跳转、折叠面板）
- [ ] 焦点环可见且未被裁切
- [ ] 动态区域 `aria-live`
- [ ] 手风琴/标签页遵循 WAI-ARIA 模式
- [ ] `prefers-reduced-motion` 生效

### 工程
- [ ] `npm run build`（含 `vue-tsc`）零错误、零警告
- [ ] 无 `transition: all`
- [ ] 无硬编码颜色（品牌色只出现在 `tokens.css`）
- [ ] 无 `sessionStorage` 直接调用（统一走 `usePlanCache`）
- [ ] `Result.vue` ≤ 250 行、`Home.vue` ≤ 200 行
- [ ] 生产环境零 console 错误
- [ ] 首屏包体积未因改动增大（结果页的 html2canvas/jspdf 保持懒加载）

---

## 附：与项目既有约定的兼容性说明

以下几点是项目已有的、**明确经过权衡的决策，本设计不推翻**：

| 既有决策 | 出处 | 本设计的态度 |
|---|---|---|
| Home 同步加载、其余路由懒加载 | `main.ts:8-10` | 保留，并在验收清单里防退化 |
| 前端发相对路径，由 vite proxy 转发 | `api.ts:13-27` | 保留 |
| 地址一律 `127.0.0.1`，不用 `localhost` | `vite.config.ts:15-20` | 保留，前端不再引入其他写死地址 |
| `/result/:id?` 与 `/share/:id` 两条独立路由 | `main.ts:26-35` | 保留 |
| 分享模式隐藏（而非禁用）编辑按钮 | `Result.vue:9-11` | 保留这个判断，它是对的 |
| 后端以降级 + `success=false` 表达"生成失败但有记录" | `trip.py:94-124` | 保留语义，只改前端承接方式（Modal 替代 toast） |
| `plan_trip()` 必须是同步 `def` | `trip.py:32-43` | 保留，故 9.1 的方案 C 需谨慎评估 |
