# 实施报告：前端改造 + RAG 知识库入口

| 项 | 值 |
|---|---|
| 日期 | 2026-09-17 |
| 依据 | `docs/FRONTEND_DESIGN.md`、`docs/RAG_CONSOLE_DESIGN.md`（两份决策均已按建议方案执行）|
| 范围 | `frontend/` 全面改造 + `backend/` 新增知识库接口 |
| 状态 | **代码完成；前端构建通过；后端 6 个接口已全部实机验证** |

---

## 1. 一句话摘要

前端从「移动端不可用、四态缺失、巨石组件」改造成了**令牌驱动 + 响应式 + 四态齐全**的结构；
RAG 知识库从「只有命令行入口」变成了**有完整界面入口**——为此在后端补了 6 个接口，
因为在此之前 `backend/app/api/` 里没有任何 RAG 的接入点。

**6 个接口已全部实机验证通过**（Milvus 运行中）。检索按 README 的验收口径命中了
`紫禁城 → 故宫博物院`、`兵马俑 → 秦始皇帝陵博物院`，灌库长任务的
`202 → 409 → 轮询 → done` 全链路跑通。验证过程中发现并修复了 1 个自己引入的缺陷。

---

## 2. 完成了什么

### 2.1 设计令牌与地基（批次 1）

| 新增 | 说明 |
|---|---|
| `src/styles/tokens.css` | 颜色/间距/圆角/阴影/字阶/动效令牌，全站唯一来源 |
| `src/styles/base.css` | reset 补丁、焦点可见性、`prefers-reduced-motion` 降级、工具类 |
| `src/styles/transitions.css` | 页面过渡、错峰进场、骨架流光（只动 GPU 属性） |
| `src/styles/antd-overrides.css` | 用令牌覆写 antd 圆角/阴影/触摸目标/移动端字号 |
| `src/constants/storage.ts` | 消除散落的 `'tripPlan'` 字符串 |
| `src/constants/tripOptions.ts` | 表单选项、状态映射、餐饮标签、校验边界（含与后端约束的对照注释）|
| `src/composables/usePlanCache.ts` | sessionStorage 唯一入口 + 结构校验 + 脏数据自动清理 |
| `src/composables/useScreen.ts` | 断点响应式（md/lg），只在壳层用 |

`App.vue` 改造：顶栏由深色改浅色并加细边框、`100vh` → `100dvh`、
emoji 图标换 `@ant-design/icons-vue`（品牌装饰性的 🌍 保留并标 `aria-hidden`）、
导航加「知识库」第三项、路由过渡、手机端压缩到 56px 高、
`main.ts` 加 `scrollBehavior` 与 `meta.title`（标签页标题）。

### 2.2 修掉的 P0/P1 问题

| 原问题 | 修法 |
|---|---|
| **P0-1** Home 表单固定 `:span` → 手机上输入框只剩约 100px | 全部改阶梯式 `:xs/:sm/:md/:lg`，天数控件在窄屏改为一行文字说明 |
| **P0-2** 结果页左栏写死 `flex: 0 0 400px` → 375px 必然横向溢出 | 改为 `380px` 且仅在 `≥992px` 生效，991 以下纵向堆叠 |
| **P0-3** 侧栏写死 240px | 抽成 `PlanSideNav`，`≥992px` 常驻侧栏、以下改**顶部横向 Pill 条**（不用抽屉，锚点常驻） |
| **P0-4** 全项目只有 2 处 media query | 五个页面全部补齐 `991 / 767 / 575` 三档断点 |
| **P1-1** 假进度条（每 500ms +10%、封顶 90%） | 换成 `GeneratingPanel`：不确定进度条 + 秒级计时器 + 超 90 秒的说明；同时去掉"当前阶段"的百分比暗示 |
| **P1-2** 加载期间闪出「没有找到旅行计划数据」 | 新增 `StatePanel` 强制四态分离 —— 结构上不可能再让 loading 渲染成 empty |
| **P1-3** 进结果页就弹「地图加载成功」 | 移除，改为卡片内错误提示 |
| **P1-4** 删除景点无撤销 | 加 8 秒撤销条（`undo-bar`） |
| **P1-5** 编辑未保存可静默丢失 | `useUnsavedGuard`：路由守卫弹确认 + `beforeunload` 兜底 |
| **P1-6** 日期超限直接清空用户已选日期 | 改为 `disabled-date` 源头拦截；万一越界则**收缩**到合法值并说明原因 |
| **P1-7** `.muted` 12px + 0.45 → 对比度 3.36:1 不合规 | 统一 `--text-2`（约 7:1），对比度低的 `--text-3` 限制为 ≥14px 使用 |
| **P1-9** emoji 当图标、无 `aria-hidden` | 功能性图标换图标库；`StatePanel`/骨架/警告标记加 `role`/`aria-label`/`aria-busy`/`aria-live` |
| **P1-10** 地图 `calc(100% - 57px)` 魔数、`min-height: 500px` | 改 `flex` 撑满 + 断点高度（420/340/280px），并加手机端「收起地图」 |
| **P2-1** 导出逻辑约 130 行逐字重复 | 抽成 `usePlanExport`，两入口共用采集与预处理 |
| **P2-6** 配图 N+1 并发、无去重、无取消 | 抽成 `useAttractionPhotos`：并发 ≤4、按名去重、代号失效机制 |
| **P2-7** 历史页第 51 条之后永远看不到 | `limit` 取到接口上限 200，并在底部明确写出加载范围 |
| **P2-8** 改了地址但坐标不变，地图标记与地址不符 | 编辑态下不伪装成"地址可改" —— 保留输入但地图标记位置由坐标决定，未误导用户可改坐标 |
| 列表 hover 整行 `scale(1.02)` 导致相邻抖动 | 改为描边 + 阴影变化 |
| `transition: all` 散落各处 | 改为显式属性列表 |
| 分享链接复制失败把 URL 塞进 toast | 改为 Modal + 只读输入框 + 复制按钮 |
| 降级原因用 3 秒 toast 承载 | 改为 Modal（后端特意把失败原因拼进了 message，值得用户读完） |

### 2.3 RAG 知识库（批次 R0 + R1 + R2）

**后端（新增）**：

| 文件 | 改动 |
|---|---|
| `app/services/knowledge_service.py` | 新增 `retrieve_merged()`（CLI 与 API 共用归并逻辑）、`preview_poi_facts()`、`preview_city_guides()`；`_embed_in_batches()` 与两个 `ingest_*` 增加 `progress_cb` 回调 |
| `app/api/routes/knowledge.py` | **新增**，6 个接口：`/status`、`/search`、`/ingest/preview`、`/ingest`、`/ingest/{task_id}`、`/sources` |
| `app/api/main.py` | 注册 knowledge router |
| `scripts/ingest_knowledge.py` | 删掉脚本私有的 `_count_poi`/`_count_guides`，改为调用 service —— 消除"第二份统计逻辑" |

**关键设计落实**：

- `enabled`（ENABLE_RAG，生成时是否注入）与 `available`（Milvus+embedding 能否用）**分开返回**并分开呈现。
  页面明确告诉用户「知识库可用，但生成行程时不会使用它」——这正是原来界面上完全看不出来的事。
- `retrieve()` 吞掉的异常通过 `last_error` 透出来，前端据此把「连不上」与「没命中」分成两个分支。
- 灌库是**后台线程 + 内存任务表 + 前端 2 秒轮询**，用真实 `processed/total` 做进度。
- `recreate=true` 需 `confirm='DROP'`，否则 400；前端另有要求手输确认文字的弹窗，并写明**会删掉多少条**。
- 并发提交返回 409；`stats()` 类慢调用走线程池超时保护；状态接口**不返回 500**（它本身就是用来报告故障的）。
- 知识库页面 5 个区块：环境状态三卡 / 检索调试（三种"无结果"分开显示）/ 数据源（含"试检索"联动）/ 灌库危险区 / 说明。

**前端（新增）**：`views/Knowledge.vue` + `components/knowledge/*` 5 个组件 +
`useKnowledgeStatus`（分级加载）、`useIngestTask`（提交与轮询，卸载时清定时器）；
`types/index.ts` 追加 8 个接口类型，`services/api.ts` 追加 6 个方法（各自独立 timeout）。

---

## 3. 验证情况

### 3.1 前端（静态 + 构建）

| 验证 | 结果 |
|---|---|
| `vue-tsc --noEmit`（含 `noUnusedLocals`） | **0 错误** |
| `vite build` | **通过**，3534 模块，28.2s |
| `vite dev` 模块编译 | **14/14** 关键模块（视图、知识库组件、composable、tokens.css）返回 200。dev 用 esbuild 逐模块、build 用 rollup，两条路径单独验证 |
| 代码分割 | Knowledge 独立 chunk（32.7 kB JS / 16.6 kB CSS）、Result 独立 chunk —— 懒加载未被破坏 |
| 后端 `py_compile` | 5 个文件全部通过 |

### 3.2 后端接口（实机运行，Milvus 正在运行）

**环境说明**：当前工作区是 git worktree（`.git` 指向 `D:/devlop/helloagents-trip-planner`），
`venv` 与 `.env` 属于 gitignore 内容、不会被复制进 worktree。已从主仓库取用这两者（`.env` 经
`git check-ignore` 确认不会进入版本控制），用主仓库的 venv 解释器运行 worktree 的代码，
服务跑在 `127.0.0.1:8001`。

| # | 接口 | 结果 |
|---|---|---|
| 1 | `GET /status` | 200 / 5.0s。`available=true`、`reason=ok`、`enabled=**false**`（`ENABLE_RAG` 未配置，默认关）、`dim_expected=1024`、`last_error=null` |
| 1b | `GET /status?with_counts=false` | 200 / 0.0s，`poi_facts` 与 `city_guides` 均为 `null` —— **分级加载生效** |
| 1c | `GET /status`（带计数） | `poi_facts=1750`、`city_guides=30` —— 与设计文档预估**完全一致** |
| 2 | `GET /sources` | 200 / 0.1s。POI 716.2 KB / 5 城（北京354·上海348·成都349·西安341·杭州358）；5 篇攻略各 6 块 |
| 3 | `POST /ingest/preview` | 200 / 0.0s。`1750 + 30 = 1780` 条、`embed_requests=56` —— **与 CLI `--dry-run` 口径一致** |
| 4 | `POST /search`（`namespace=all`） | `紫禁城` → **[1] 0.6356 `故宫博物院 \| 别名:紫禁城`**；`兵马俑` → **[1] 0.5980 `秦始皇帝陵博物院 \| 别名:西安兵马俑`** —— 正是 README 里的验收口径 |
| 4b | `POST /search`（单层） | `namespace=city_guides, top_k=3` → 3 条且只含城市攻略层 |
| 5 | 错误路径 | 空 query → **400**；`recreate=true` 无 `confirm` → **400** + 守卫文案；不存在的 task_id → **404** |
| 6 | `POST /ingest`（`source=guides`） | **202** → 轮询 `running 0/30 → 30/30 → done`，写入 30 条；**立刻重复提交 → 409** + 提示已在跑的任务号 |
| 7 | `POST /ingest`（`recreate=true, confirm=DROP`） | 202 → done，重建后计数回到 30，检索分数与重建前**逐位一致**（0.5980）|

> 灌库验证只跑了 `source=guides`（30 块，embedding 成本可忽略）与一次 `recreate`，
> **没有重灌 1750 条 POI**（那会产生不必要的费用）。`poi_facts` 层仅做了只读校验。

### 3.3 实机验证中发现的两个问题

**（1）`top_k` 语义错误 —— 已修**

首次检索时传 `top_k=4` 却返回了 **8 条**。根因是 `retrieve_merged()` 两层各取 `k` 条后
直接归并返回，没有按 `top_k` 截断 —— 而接口契约与前端 UI（"条数: 4"）的语义是"返回多少条"。
已在 `retrieve_merged()` 内加 `hits[:k]` 截断并补注释说明语义。修复后实测 `top_k=4` → 4 条。

**（2）Milvus 的 `count()` 会随灌库次数虚高 —— 未修，属 Milvus 固有行为**

灌了一次攻略层后，`city_guides` 计数从 30 变成 **60**。深查确认：

- `top_k=40` 检索只返回 **30 条、零重复** → **数据没有重复，upsert 正确地覆盖了**
- 但 `count()` 稳定返回 60（等 12 秒 compaction 仍是 60）

原因：Milvus 的 upsert 实现是 delete + insert，被标记删除的旧版本在 compaction 完成前
仍计入 `num_entities`。所以 README 说的"重复执行是幂等的"**对数据成立**，
但 `count` 这个数字不幂等。

影响与处理：
- 界面上的条数会随灌库次数虚高（30 → 60 → 90 …），**但检索结果与"库是否为空"的判断不受影响**
- 用 `recreate=true` 重建后可恢复准确（已实测：回到 30）
- **本次没有改代码**：这是 Milvus 的固有行为，改成"拉全量 id 去重计数"会牺牲性能换一个
  仅用于展示的数字，不划算。已在此记录，供后续决定是否在界面上加说明文案

（本次验证造成的计数虚高已用 `recreate` 修复，`city_guides` 现为准确的 30。）

### 3.4 仍未验证

| 项 | 原因 |
|---|---|
| **后端单元测试（pytest）** | 未跑。`knowledge_service.py` 加了方法、`scripts/ingest_knowledge.py` 改了调用，建议补跑 `tests/test_knowledge.py`（516 行）确认无回归 |
| **浏览器内逐宽度目视走查** | 响应式是按断点书写并结构化核对的，**没有在 320/375/768/1024/1440 下真机走查** |
| **页面运行时行为** | 前后端服务都已启动（5173 / 8001）、`/knowledge` 可达、所有模块编译通过 —— 但**没有在浏览器里实际点过**：数据绑定是否正确渲染、三种"无结果"分支是否按预期出现、灌库进度条是否推进，都还没被真人验证 |
| **无障碍实测** | 未用屏幕阅读器验证；对比度是按公式计算，未用工具实测 |
| **Lighthouse** | 未跑 |

---

## 4. 与设计文档的偏差

### 4.1 未达成的目标：组件拆分不彻底

设计目标是 `Result.vue ≤ 250 行`、`Home.vue ≤ 200 行`。**没有达到**：

| 文件 | 改造前 | 改造后 |
|---|---:|---:|
| `Result.vue` | 1499 | **1342** |
| `Home.vue` | 656 | **739** |

实际抽出去的：`GeneratingPanel`(236)、`PlanSideNav`(194)、`StatePanel`(215)、`PlanSkeleton`(119)、
`PlanCardList`(128)、`PlanStatusTag`(60)、`usePlanExport`(190)、`useAttractionPhotos`(135)。
但 `AttractionCard`、`DayPlanPanel`、`WeatherStrip`、`BudgetCard` 仍内联在 `Result.vue` 里，
`Home.vue` 的三个表单分区也仍在一个文件中。

**`Home.vue` 反而变长了**（656 → 739）—— 因为它新增了原本不存在的响应式断点样式和
说明性注释，同时把生成中面板抽成了独立组件。**净效果是逻辑更分散、注释更完整，
但"单文件过长"这个问题没有解决。**

### 4.2 其他偏离

| 设计 | 实际 | 理由 |
|---|---|---|
| B1 拆成 3 个组件（可用性/计数/启用状态） | 合并为 `KnowledgeStatusStrip.vue`（内部仍是三张卡） | 三者共享同一个 `status` 对象，拆开要把同一份 props 传三遍 |
| 批次 4 无单独阶段 | 与批次 3 合并实施 | 两者改的是同一批文件，分开反而要重复打开 |

---

## 5. 运行注意事项

### 5.1 我在沙箱里装依赖时遇到的问题（你本地不受影响）

本环境的 npm 执行 postinstall 调 `cmd.exe` 被拦，第一次 `npm install` **失败并回滚**。
我用 `npm install --ignore-scripts` 完成了安装，构建验证通过。

**你在自己终端正常执行 `npm install` 即可**，不需要 `--ignore-scripts`。

### 5.2 环境说明与启动顺序

本工作区是 git **worktree**（主仓库在 `D:/devlop/helloagents-trip-planner`）。
`backend/venv` 与两个 `.env` 属于 gitignore 内容，**不会被复制进 worktree** ——
我已从主仓库取用（`.env` 经 `git check-ignore` 确认不会进入版本控制，`git status` 中也不出现）。

**当前状态**：后端服务正跑在 `127.0.0.1:8001`（主仓库的 venv 解释器 + worktree 的代码），
这是本次接口验证用的。**它还开着**，你可以直接拿它去跑前端页面。要停掉它：

```powershell
# 找到占用 8001 的进程并结束（这是停服务，不是删文件）
netstat -ano | findstr :8001
taskkill /PID <上面查到的PID> /F
```

自己重新起服务的命令：

```bash
# 后端（worktree 用主仓库的 venv）
cd backend
"D:/devlop/helloagents-trip-planner/backend/venv/Scripts/python.exe" -m uvicorn app.api.main:app --host 127.0.0.1 --port 8001

# 前端（.env 已就位，含 VITE_AMAP_WEB_JS_KEY）
cd frontend
npm install
npm run dev        # 打开 http://127.0.0.1:5173/knowledge
```

`ENABLE_RAG` 当前**未配置**（即默认关闭）。想看"已启用"状态的展示效果，
在 `backend/.env` 里加 `ENABLE_RAG=true` 再重启后端。

### 5.3 知识库页面打开后会看到什么

- 没有 Milvus → 第一张卡显示「不可用」并原样列出原因（如 `ConnectionRefusedError`）+ 修复指引
- 没有 `EMBED_API_KEY` → 原因显示为 `embedding 不可用 — ...`
- 两项都正常但没灌库 → 计数显示 0 并提示「去灌库」
- 灌过库但 `ENABLE_RAG=false`（默认） → 第三张卡显示「未启用」并说明生成时不会使用

**这些都是预期表现**，是这个页面刻意要摊开的状态，不是故障。

### 5.4 构建产物

`frontend/dist/` 已生成（上面那次构建的产物）。它在 `.gitignore` 里，不影响仓库。

---

## 6. 后续建议（按性价比排序）

1. **把前端 dev server 起起来点一遍知识库页面**。接口已经逐一验证过，但页面本身
   （数据绑定、三种"无结果"分支的渲染、灌库进度条的推进）**没有在浏览器里实际点过**。
   这是目前最大的不确定性，也是最容易发现问题的一步。
2. **补跑后端 pytest**：`./venv/Scripts/python.exe -m pytest`。
   `knowledge_service.py` 新增了方法、`ingest_knowledge.py` 改了调用方式，
   需要确认既有 516 行的 `tests/test_knowledge.py` 无回归。
3. **浏览器逐宽度走查 + 无障碍实测 + Lighthouse**：320/375/768/1024/1440 五个宽度，
   键盘走完全流程，`prefers-reduced-motion` 开启后确认无动画。
4. **决定 Milvus 计数虚高要不要在界面上加说明**（见 3.3 第 2 条）。
   当前行为是"灌库几次就翻几倍"，虽然不影响检索，但用户看到 60 条而实际只有 30 块会困惑。
5. **继续组件拆分**：`AttractionCard` / `DayPlanPanel` / `WeatherStrip` / `BudgetCard` 从 `Result.vue` 抽出，
   `Home.vue` 的三个表单分区抽出。纯重构、行为不变，风险低。
6. **主包体积**：`index.js` 为 1.58 MB（gzip 495 kB），主要来自 `app.use(Antd)` 全量注册。
   这是既有状况（不是本次引入），但改成按需引入能显著改善首屏。
7. **批次 R3（行程内的知识出处展示）**：需要改 `trip.py` / `agents/` / `plan_store` 主链路，风险高于新增接口，当时决定先不做。
