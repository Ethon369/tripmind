# RAG 知识库前端入口 —— 设计补充文档

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | **已实施** —— 见 `docs/IMPLEMENTATION_REPORT.md` |
| 关系 | `docs/FRONTEND_DESIGN.md` 的补充件，复用其令牌体系、组件规范与响应式矩阵 |
| 范围 | 知识库的浏览、检索调试、环境状态、灌库运维；**含必需的后端接口新增** |
| 不在范围内 | 检索算法本身、评测链路、embedding 模型更换 |

> **一句话结论：这是「后端补接口 + 前端做页面」的活儿，不是纯前端。**
> 后端 `KnowledgeService` 的 5 项能力（状态检查 / 检索 / 灌库 / 计数 / 预览）**全部只在命令行脚本里可达**，
> `backend/app/api/` 下 grep `knowledge|rag|milvus` **零命中**。前端无论怎么写都接不上。

---

## 1. 后端能力盘点：有什么、缺什么

### 1.1 已经具备的能力（`app/services/knowledge_service.py`）

| 能力 | 方法 | 位置 | 返回契约 |
|---|---|---|---|
| 环境自检 | `available()` | `:735` | `(bool, str)`，原因形如 `embedding 不可用 — ...` / `Milvus 不可用 — ...` |
| 双库统计 | `stats()` | `:752` | `{milvus_uri, last_error, poi_facts:{collection,exists,count}, city_guides:{...}}` |
| 单层检索 | `retrieve(query, namespace, top_k)` | `:571` | `list[KnowledgeHit]` |
| 灌 POI 层 | `ingest_poi_facts(recreate)` | `:629` | `{namespace, collection, count, cities}` |
| 灌攻略层 | `ingest_city_guides(recreate)` | `:689` | `{namespace, collection, count, files}` |

`KnowledgeHit.to_dict()`（`:335`）给出 `{content, score, source, heading_path, namespace}` —— 字段足够前端展示出处。

**这些能力的形态与 CLI 完全一一对应**（`scripts/ingest_knowledge.py`）：

| CLI 参数 | 对应能力 |
|---|---|
| `--dry-run` | 只统计条数，不调 embedding |
| `--source=frozen\|guides\|all` | 选择灌哪一层 |
| `--recreate` | 先 drop collection 再建 |
| `--query` / `--top-k` | 检索冒烟测试 |
| （无参数时的自检） | `available()` + `stats()` |

**所以前端入口要做的事，本质就是把这 5 个 CLI 能力搬到网页上。**

### 1.2 数据规模（实测）

| 层 | 数据源 | 规模 |
|---|---|---|
| `poi_facts` | `data/frozen/amap/poi_inventory.json`（733 KB） | **1750 条**，5 城：北京 354 / 上海 348 / 成都 349 / 西安 341 / 杭州 358 |
| `city_guides` | `data/knowledge_base/city_guides/*.md` | **5 篇**：北京、上海、成都、西安、杭州（每篇约 4.3–4.8 KB） |

### 1.3 缺失的部分

| 缺什么 | 后果 |
|---|---|
| **0 个 HTTP 接口** | 前端无任何接入点，这是根本阻塞 |
| 灌库无进度反馈 | 1750 条要分 55 批调 embedding（`_EMBED_BATCH = 32`），耗时几分钟，HTTP 同步请求必超时 |
| 无任务状态查询 | 前端发起灌库后无法知道进行到哪、成功没有 |
| 数据目录元信息无接口 | 前端无法列出"有哪些攻略文件、各切成多少块" |

---

## 2. 四个必须先处理的约束

这四条是读代码读出来的，**不处理的话前端做出来就是错的**。

### 2.1 `retrieve()` 吞掉所有异常 —— 前端分不清"没命中"和"连不上"

```python
# knowledge_service.py:571-598
def retrieve(self, query, namespace, top_k=None) -> list[KnowledgeHit]:
    try:
        ...
    except Exception as exc:
        self.last_error = f"检索失败({namespace}): {type(exc).__name__}: {exc}"
        print(f"⚠️  {self.last_error} —— 本次跳过知识库,不影响行程生成")
        return []          # ← 异常在这里被吞成空列表
```

设计上这是**对的**（RAG 是增强不是依赖，Milvus 挂了不能让行程生成也挂），但直接暴露成 API 会让前端看到"0 条结果"而无法区分两种情况：

| 真实情况 | 前端如果只看 `hits` | 应该显示 |
|---|---|---|
| Milvus 没起 | "没有命中" | **"检索失败：Milvus 不可用"** + 修复指引 |
| collection 没灌 | "没有命中" | **"知识库是空的，请先灌库"** |
| 确实无相关内容 | "没有命中" | "没有命中" |

**处理方式**：检索接口必须同时返回 `last_error` 和两层的 `count`。前端据此三分支判断。
（`last_error` 是 service 的实例属性，多次调用会互相覆盖 —— 接口里要**在调用前后各读一次**，或者按 2.4 的方式改为返回值携带。）

### 2.2 运行日志只留 800 字符，且 `run_id` 不与 `plan_id` 关联

```python
# observability/run_logger.py:178-183
def stage_response(self, stage: str, text: str) -> None:
    # 截断保存:完整响应可能有几十 KB,这里只留头部用于排查
    self._record["responses"][stage] = {
        "chars": _safe_len(text),
        "head": (text or "")[:800],
    }
```

`trip_planner_agent.py:376` 把 RAG 检索结果 `obs.stage_response("retrieval", context)` 记进了 observer，
但：① 只留前 800 字符（1750 条库的完整命中明细会超）；② observer 的 `run_id` 与 `plan_store` 的 `plan_id` 各生成各的，**没有任何字段把它们关联起来**。

**处理方式**：如果要做"行程详情页展示本次参考了哪些知识"（第 6 节的批次 3），必须改后端存储 —— 把这批 hits 结构化写进 `plan_store`，而不是指望从 JSONL 日志里捞。

### 2.3 dry-run 的统计函数在脚本里，不在 service 里

`scripts/ingest_knowledge.py:47` 的 `_count_poi()` 和 `:61` 的 `_count_guides()` 是**脚本私有函数**。
脚本自己的注释（`:62-64`）说得很清楚：

> 复用入库时同一套切分函数 —— 如果这里另写一份统计逻辑，数字和实际入库的就不会一致，那种"预估"没有意义。

**处理方式**：做成 API 前，把这两个函数**下沉到 `KnowledgeService`**（如 `preview_poi_facts()` / `preview_city_guides()`），CLI 与 API 共用同一份。否则就会出现第二份统计逻辑 —— 正是脚本作者当初刻意避免的事。

### 2.4 `stats()` 会真连 Milvus，且没有超时

```python
# knowledge_service.py:757-766
for ns in (self.NAMESPACE_POI, self.NAMESPACE_GUIDES):
    store = self.store(ns)
    try:
        out[ns] = {"collection": ..., "exists": store.exists(), "count": store.count()}
```

`exists()` + `count()` 各是一次网络调用。Milvus 没起时，`MilvusClient(uri=...)` 的建连重试可能让请求挂住很久。
前端如果在页面加载时直接打这个接口，**页面会一直转圈**。

**处理方式**：
- 状态接口内部加超时（`concurrent.futures` 包一层，或给 MilvusClient 配超时）
- 前端**分两级加载**：先返回 `available()` 的快速结果，计数单独一个请求、允许失败、失败不阻塞页面渲染
- 前端设置比后端更短的 axios timeout，超时后显示"环境检查超时"而不是无限等待

---

## 3. 后端接口设计（前置工作）

新增 `backend/app/api/routes/knowledge.py`，`APIRouter(prefix="/knowledge", tags=["知识库"])`，在 `app/api/main.py` 注册。

### 3.1 接口清单

| # | 方法 | 路径 | 镜像自 | 阻塞性 |
|---|---|---|---|---|
| 1 | GET | `/api/knowledge/status` | `available()` + `stats()` | 快（可用性）+ 慢（计数，独立超时） |
| 2 | POST | `/api/knowledge/search` | `retrieve()` | 需调 embedding，约 1–3 秒 |
| 3 | POST | `/api/knowledge/ingest/preview` | `--dry-run` | 快（只读文件） |
| 4 | POST | `/api/knowledge/ingest` | `--source` / `--recreate` | **长任务（分钟级）** |
| 5 | GET | `/api/knowledge/ingest/{task_id}` | 新 | 快 |
| 6 | GET | `/api/knowledge/sources` | 新 | 快（读目录） |

### 3.2 接口 1：状态

```
GET /api/knowledge/status?with_counts=true
```

```json
{
  "success": true,
  "enabled": false,
  "available": true,
  "reason": "ok",
  "milvus_uri": "http://localhost:19530",
  "last_error": null,
  "dim_expected": 1024,
  "top_k": 4,
  "collections": { "poi_facts": "trip_poi_facts_1024", "city_guides": "trip_city_guides_1024" },
  "poi_facts":   { "collection": "trip_poi_facts_1024",   "exists": true, "count": 1750 },
  "city_guides": { "collection": "trip_city_guides_1024", "exists": true, "count": 30 }
}
```

**`enabled` 与 `available` 是两件不同的事，必须拆开返回**（这是本设计的核心判断之一）：

| 字段 | 来源 | 含义 | 为 false 时 |
|---|---|---|---|
| `enabled` | `settings.enable_rag`（`config.py:84`，默认 `False`） | 生成行程时**是否注入**知识库 | 知识库能查、能灌，只是生成时不用 |
| `available` | `available()` | 知识库本身**能不能用**（embedding + Milvus 都通） | 什么都干不了，先修环境 |

现有系统里这两件事都"藏在" `.env` 和 Docker 里，用户完全看不到。页面的第一屏职责就是把它们显式摊开。

`with_counts=false` 时跳过 `count()` 调用，只返回 `exists`，用于快速首屏。

### 3.3 接口 2：检索

```
POST /api/knowledge/search
{ "query": "北京 历史文化", "namespace": "all", "top_k": 4 }
```

```json
{
  "success": true,
  "hits": [
    { "namespace": "city_guides", "content": "...", "score": 0.7831,
      "source": "北京.md", "heading_path": "门票与预约" },
    { "namespace": "poi_facts", "content": "...", "score": 0.7412,
      "source": "poi_inventory.json", "heading_path": "北京 > 历史文化 > 故宫博物院" }
  ],
  "elapsed_ms": 843,
  "partial": false,
  "error": null,
  "namespace_counts": { "poi_facts": 1750, "city_guides": 30 }
}
```

- `namespace`: `"all"` 时两层都查并按 `score` 降序归并 —— 归并逻辑抽成 `KnowledgeService.retrieve_merged(query, top_k)`，
  与 `ingest_knowledge.py:122-125` 的 CLI 排序逻辑共用一份（同 2.3 的理由）
- `error`：把 service 的 `last_error` 透出来（见 2.1）。**这是前端区分"没命中 / 连不上"的唯一依据**
- `partial`：一层成功一层失败时为 `true`，前端要标"仅部分层返回结果"，不能让用户误以为另一层没查到
- `elapsed_ms`：让用户对检索性能有直观感受（也是 embedding 接口慢不慢的信号）

> **`score` 的可比性前提要写进 UI 提示**：两层用的是同一个 embedder（bge-m3）和同一个度量（COSINE），
> 所以分数可直接比较（`knowledge_service.py:600-606` 专门解释了这点）。但**跨 embedding 模型的分数不可比** ——
> 页面要在分数旁注明"COSINE 相似度"，避免用户拿这个数字跟别的系统对比。

### 3.4 接口 3：灌库预览

```
POST /api/knowledge/ingest/preview
{ "source": "all" }
```

```json
{
  "success": true,
  "poi":    { "total": 1750, "per_city": { "北京": 354, "上海": 348, "成都": 349, "西安": 341, "杭州": 358 } },
  "guides": { "total": 30,   "per_file": { "北京.md": 6, "上海.md": 6, "成都.md": 6, "西安.md": 6, "杭州.md": 6 } },
  "embed_requests": 56,
  "note": "共 1780 条将被编码,约 56 次批量请求(每批 32 条)。执行会调用 embedding 接口并产生费用。"
}
```

`embed_requests` 用 `-(-total // 32)` 计算，与 CLI `:93` 一致。

> ⚠️ 这里的 `guides.total` 是我按"每篇 6 块"估的示意值，**实际数字必须由接口算**，不能硬编码。
> 落地时以 `split_markdown_sections()` 的真实输出为准。

### 3.5 接口 4/5：灌库（长任务）

**为什么不能照抄 `plan_trip()` 的写法**：`trip.py:28` 把 `plan_trip` 写成同步 `def`，靠 FastAPI 丢进线程池 ≈ 60 秒可接受。
但灌库要 56 次 embedding 请求，**分钟级**，而且前端 axios 超时是 120 秒（`api.ts:31`）—— 同步接口必然超时，且用户看不到任何进度。

**方案：后台线程 + 内存任务表 + 前端轮询。**

```
POST /api/knowledge/ingest
{ "source": "all", "recreate": false, "confirm": "" }
→ 202 { "success": true, "task_id": "ing-20260917-164200-a3f1", "state": "pending" }

GET /api/knowledge/ingest/{task_id}
→ {
    "task_id": "ing-...", "state": "running",
    "source": "all", "recreate": false,
    "total": 1780, "processed": 896,
    "current": "poi_facts",
    "started_at": "...", "finished_at": null,
    "results": [], "error": null
  }
```

- `state`: `pending` / `running` / `done` / `error`
- 任务表放内存（`dict` + 锁），**不落库** —— 与项目"表就两个不值得引 ORM"的取舍一致；进程重启丢失任务记录是可接受的（用户重发一次即可）
- 同一时刻只允许一个灌库任务（`409` 拒绝并发），因为两个任务写同一个 collection 会互相干扰
- 进度上报：`_embed_in_batches()` 每批 32 条，每批完成后更新 `processed`（需要给它加一个可选回调，或在 `ingest_*` 里加 `progress_cb` 参数）

**安全设计（`recreate` 是破坏性操作）**：

```python
# 伪代码：接口层的守卫
if body.recreate and body.confirm != "DROP":
    raise HTTPException(400, "重建会清空 collection 内全部数据，需显式确认")
```

- `recreate=true` 必须带 `confirm: "DROP"` 字段，缺失直接 400
- 前端对应的二次确认弹窗要**把后果写清楚**："将删除 `trip_poi_facts_1024` 中的 1750 条数据，需重新灌库才能恢复"，
  并要求用户手动输入确认文字（照搬 GitHub 删仓库的做法）
- 默认 `recreate=false`（upsert 幂等覆盖），这条路是安全的

### 3.6 接口 6：数据源清单

```
GET /api/knowledge/sources
→ {
    "success": true,
    "poi_inventory": {
      "path": "data/frozen/amap/poi_inventory.json", "size_kb": 733,
      "cities": ["北京","上海","成都","西安","杭州"], "total": 1750
    },
    "city_guides": {
      "dir": "data/knowledge_base/city_guides",
      "files": [ { "name": "北京.md", "size_kb": 4.2, "sections": 6 }, ... ]
    }
  }
```

`sections` 用 `split_markdown_sections()` 实算，与入库口径一致。

---

## 4. 前端页面设计

### 4.1 路由与导航

| 项 | 值 |
|---|---|
| 路由 | `/knowledge`，`name: 'Knowledge'`，懒加载（与 Result 同策略） |
| 导航位置 | 顶栏第三项：首页 / 历史行程 / **知识库** |
| 页面标题 | 知识库 |
| 副标题 | 行程生成时参考的外部知识：1750 条 POI 事实 + 5 篇城市攻略 |
| 空权限 | 无（单用户项目，不做角色区分） |

**为什么放主导航而不是藏进设置**：这个页面的核心价值是"让 RAG 从黑盒变成可观察的东西"。
藏在设置里，用户还是不知道知识库有没有灌、检索准不准 —— 那和现在的状态没区别。

### 4.2 信息架构

页面自上而下五块，**按"从只读诊断到破坏性操作"排序**，破坏性最强的放最后并被折叠收起：

| # | 区块 | 回答的问题 | 可折叠 |
|---|---|---|---|
| B1 | 环境状态 | Milvus / embedding 通不通？灌了多少？生成时启没启用？ | 否（首屏必须看见） |
| B2 | 检索调试 | 换个问法能搜到什么？分数多少？出处是哪？ | 否（核心功能） |
| B3 | 数据源 | 库里有什么？攻略有哪些篇、各切成几块？ | 是（默认收起） |
| B4 | 灌库 / 重建 | 怎么把改动灌进去？会花多少钱、删多少数据？ | **是（默认收起 + 危险区样式）** |
| B5 | 说明 | `ENABLE_RAG` 怎么开？维度为什么必须是 1024？ | 是（默认收起） |

### 4.3 B1 环境状态区

三张状态卡 + 一行提示，桌面三列、手机单列。

**第一张：知识库可用性**

| 状态 | 视觉 | 文案 |
|---|---|---|
| 可用 | `--success` 圆点 + "正常" | Milvus 连接正常，embedding 服务正常 |
| 不可用 | `--error` 圆点 + "异常" | **原样显示 `reason`**（如 `Milvus 不可用 — ConnectionRefusedError: ...`），并附修复指引 |
| 超时 | `--warning` 圆点 + "检查超时" | 无法确认状态，点「重试」 |

> 修复指引直接复用 `ingest_knowledge.py:159-162` 的两条：
> `cd D:/devlop/Milvus && docker compose up -d`、检查 `backend/.env` 里的 `EMBED_API_KEY` / `EMBED_BASE_URL`。
> 把命令行里的排错经验搬到界面上，是这个页面最实际的价值。

**第二张：向量库计数**

```
poi_facts      1750 条     trip_poi_facts_1024      [已建]
city_guides      30 块     trip_city_guides_1024    [已建]
```

- `count` 为 0 或 `exists=false` 时高亮为 `--warning`，并直接给「去灌库」按钮滚动到 B4
- 这个数字是排错第一现场：**"检索没结果"十有八九是这里显示 0**

**第三张：生成时的启用状态**

| `enabled` | 展示 |
|---|---|
| `true` | `--success` 徽标「已启用」+ "生成行程时会注入知识库内容" |
| `false` | `--warning` 徽标「未启用」+ "知识库本身可用，但生成行程时不会使用它" + 展开的开启说明 |

**这一张是本设计最想解决的问题**。现状是：用户灌了库、检索也通了，但 `ENABLE_RAG=false` 时生成行程**完全不用知识库**，
而界面上没有任何地方能看出这件事。`config.py:80-83` 的注释解释了为什么默认关（为了 A/B 对比），
但那是对开发者有意义的理由 —— 对使用者而言，需要一句话告诉他"你灌的知识现在没起作用"。

**开启方式**：`.env` 改 `ENABLE_RAG=true` 后重启后端。前端展示为只读说明 + 可复制的配置片段，
**不提供在线切换**（改环境变量需要重启进程才生效，做个"开关"按钮反而是欺骗）。

### 4.4 B2 检索调试区

```
┌─────────────────────────────────────────────────────────┐
│ [ 北京 历史文化 一日游              ]  [检索]            │
│ 知识层: (全部) (城市攻略) (POI 事实)     条数: [4 ▾]     │
└─────────────────────────────────────────────────────────┘
```

- `a-input-search`，回车即搜；检索中按钮 `loading`、结果区骨架屏
- 知识层用 `a-radio-group` 按钮组（`all` / `city_guides` / `poi_facts`）
- `top_k` 用 `a-select`：3 / 4 / 6 / 10
- 预置 4 个示例查询（点击填入）：`北京 历史文化 一日游`、`紫禁城`、`兵马俑`、`成都 美食`
  —— 其中 `紫禁城` / `兵马俑` 来自 README 的验收说明（别名检索是这套 RAG 最关键的能力）

**结果列表**（每条一张卡）：

```
┌──────────────────────────────────────────────────────┐
│ 0.783  ████████████████░░░░   city_guides            │
│ 北京.md > 门票与预约                                  │
│ ──────────────────────────────────────────────────── │
│ 故宫需提前 7 天在官网预约，周一闭馆（法定节假日除外）…  │
│                                             [展开]    │
└──────────────────────────────────────────────────────┘
```

- **分数条形图**：`score` 是 COSINE 值域 `[-1, 1]`，实际命中集中在 `[0.5, 0.9]`。条形按 `(score) × 100%` 宽度渲染，
  并在旁注明"COSINE 相似度"。**不做颜色分级**（"大于 0.7 就是好结果"这种判断没有依据，会误导）
- **出处**：`source > heading_path`，这是判断"命中来自哪一层"的直接依据，用等宽字体便于对比
- **namespace 徽标**：`city_guides` 用品牌紫、`poi_facts` 用中性灰 —— 两层在视觉上要能一眼分开
- **内容**：默认 3 行截断，`[展开]` 查看全文（`content` 可能上千字符）
- **排序**：按 `score` 降序（接口已排好），页面不再排序

**三种"没有结果"必须分开显示**（见 2.1）：

| 情况 | 判断依据 | 展示 |
|---|---|---|
| 检索失败 | `error` 非空 | `--error` 提示：原样显示 `error` + 修复指引 + 重试 |
| 知识库为空 | `namespace_counts` 全为 0 | `--warning` 提示："知识库还没有数据" + 「去灌库」按钮 |
| 确实无命中 | `error` 为空且计数正常 | 中性提示："没有检索到相关内容" + 建议换关键词（可点示例） |

**部分成功**：`partial=true` 时在结果区顶部加一条 `--warning` 提示："POI 事实层检索失败，以下仅为城市攻略层结果"。

### 4.5 B3 数据源区

两栏（桌面并排，手机堆叠）：

**左：POI 事实层**
- 数据源路径 + 文件大小
- 按城市分组的条形（北京 354 / 上海 348 / …），条形长度按最大值归一
- 底部：合计 1750 条

**右：城市攻略层**
- `a-list` 列出 5 个 md 文件：文件名、大小、**分块数**
- 每项一个「试检索」按钮：点击后带着该城市名（去掉 `.md`）填入 B2 并立即检索

> 这个按钮是页面的"顺藤摸瓜"路径：看到北京攻略 → 想验证它能不能被搜到 → 一键检索。
> 比让用户自己回上面输入框敲字顺手得多。

### 4.6 B4 灌库区（危险区）

折叠面板，标题带警示色。展开后：

**步骤 1 · 预览**

```
[预览将要入库的内容]
  POI 事实层        1750 条（北京 354 / 上海 348 / 成都 349 / 西安 341 / 杭州 358）
  城市攻略层          30 块（北京.md 6 / 上海.md 6 / 成都.md 6 / 西安.md 6 / 杭州.md 6）
  ──────────────────────────────────────────────
  合计 1780 条将被编码，约 56 次批量请求（每批 32 条）
  ⚠️ 执行会调用 embedding 接口并产生费用（约 ¥0.004）
```

**步骤 2 · 选择范围与方式**

| 控件 | 选项 | 默认 |
|---|---|---|
| 灌哪一层 | 全部 / 仅 POI 事实 / 仅城市攻略 | 全部 |
| 重建 collection | 开关（危险，默认关） | 关 |

重建开关旁必须有说明：`关闭 = upsert 幂等覆盖（安全，推荐）；开启 = 先删除 collection 再建，数据全部丢失`。

**步骤 3 · 执行**

- `recreate` 关闭时：`a-popconfirm` 确认 → 直接执行
- `recreate` 开启时：**`a-modal` + 输入确认文字**（要我输出 `DROP` 才可点确认），文案写明会删除多少条现存数据（引用 B1 的计数）

**步骤 4 · 进度**

```
正在灌入 poi_facts …                          896 / 1780
▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░  50.3%
已用 42 秒 · 请勿关闭页面
```

- 轮询间隔 2 秒；`state` 变为 `done` / `error` 时停止
- 进度条用**真实** `processed / total`（这是后端能给出的真实数字，不像行程生成那样只能做不确定进度）
- 完成后展示结果表：`namespace / collection / 写入条数`，并自动刷新 B1 的计数
- 失败时展示 `error` 原文 + 重试

> 与 `docs/FRONTEND_DESIGN.md` 第 7.3 节的对比：那边因为后端只有一次性响应，只能做"不确定进度"；
> 这边后端提供真实进度，就应该用真实进度。**不给假进度，也不浪费真进度。**

### 4.7 B5 说明区

折叠的 `a-collapse`，三条：

1. **两层知识库各干什么**（表：namespace / 数据源 / 回答什么问题）—— 摘自 README
2. **为什么维度必须是 1024**：collection 名字里带维度，换模型没重建库时 Milvus 不报错、结果全乱。
   `EMBED_DIM_EXPECTED` 对不上时页面要显式警告
3. **`ENABLE_RAG` 怎么开**：可复制的 `.env` 片段 + "改完需重启后端"

### 4.8 响应式

沿用 `docs/FRONTEND_DESIGN.md` 第 4 节的断点与令牌。

| 元素 | xs <576 | md ≥768 | lg ≥992 |
|---|---|---|---|
| B1 状态卡 | 1 列 | 3 列 | 3 列 |
| B2 检索栏 | 输入框与筛选器上下堆叠 | 同行 | 同行 |
| B2 namespace 选择 | 横向滚动的按钮组 | 同行 | 同行 |
| B3 两栏 | 纵向堆叠 | 2 列 | 2 列 |
| B4 表单 | 纵向 | 2 列 | 2 列 |
| 结果卡片 | 1 列 | 1 列（内容宽，不宜分栏） | 1 列 |
| 进度数字 | 独占一行 | 右上角 | 右上角 |

**触摸目标**：所有按钮 ≥ 44×44px；「试检索」这类小按钮在手机上改为整行可点。

### 4.9 组件划分

复用 `FRONTEND_DESIGN.md` 第 6 节的 `StatePanel` / `SectionCard` / `AnimatedNumber`，新增：

```
src/
├── views/
│   └── Knowledge.vue                 # 薄壳，只做编排
├── components/knowledge/
│   ├── KnowledgeStatusCard.vue       # B1 可用性
│   ├── KnowledgeCountCard.vue        # B1 两层计数
│   ├── RagEnabledCard.vue            # B1 生成时启用状态
│   ├── SearchPanel.vue               # B2 检索栏
│   ├── SearchResultList.vue          # B2 结果列表
│   ├── SearchResultCard.vue          # B2 单条结果（分数条 + 出处）
│   ├── ScoreBar.vue                  # 分数可视化
│   ├── SourceInventory.vue           # B3 数据源
│   ├── IngestPanel.vue               # B4 灌库（含预览/确认/进度）
│   ├── RecreateConfirmModal.vue      # B4 重建二次确认
│   └── KnowledgeDocPanel.vue         # B5 说明
├── composables/
│   ├── useKnowledgeStatus.ts         # 状态轮询 + 分级加载
│   └── useIngestTask.ts              # 灌库任务提交与轮询
└── services/
    └── api.ts                        # 追加 knowledge 相关方法
```

**关键契约**：

`useIngestTask.ts`

| 导出 | 类型 | 说明 |
|---|---|---|
| `submit(source, recreate)` | `Promise<void>` | 提交任务；`recreate` 时需先经确认弹窗 |
| `task` | `Ref<KnowledgeIngestTask \| null>` | 当前任务 |
| `progress` | `ComputedRef<number>` | `processed / total`，`total` 为 0 时返回 0 |
| `polling` | `Ref<boolean>` | 轮询中 |
| `stop()` | `() => void` | **组件卸载时必须调用**（清 `setInterval`） |

`SearchResultCard.vue`

| Prop | 类型 | 说明 |
|---|---|---|
| `hit` | `KnowledgeHit` | 单条命中 |
| `rank` | `number` | 序号（1 起） |

### 4.10 状态管理的边界

沿用 `FRONTEND_DESIGN.md` 8.1 的结论：**不引入 Pinia**。

- B1 状态、B2 检索结果、B4 任务都是**本页局部状态**，没有跨页面消费者
- 唯一需要跨页面的是"知识库是否启用"—— 但它是后端配置，不是前端状态，每次进页面重新拉即可
- 灌库任务的 `task_id` 只在页面内存活；刷新页面后任务仍在后端跑，但前端失去 `task_id`。
  **这是可接受的**：页面提供「检查是否有任务在跑」的兜底（重新进页面时若后端有 running 任务则接管），
  或更简单 —— 刷新即视为放弃跟踪，提示"灌库可能仍在后台进行，稍后刷新计数查看结果"。**建议后者**，成本低且不误导

---

## 5. TS 类型契约

追加到 `src/types/index.ts`：

```ts
// ============ 知识库 ============

export type KnowledgeNamespace = 'poi_facts' | 'city_guides';
export type KnowledgeNamespaceFilter = KnowledgeNamespace | 'all';

export interface KnowledgeHit {
  namespace: string;
  content: string;
  /** COSINE 相似度，值域 [-1,1]，两层共用同一 embedder 故可直接比较 */
  score: number;
  source: string;
  heading_path: string;
}

export interface KnowledgeNamespaceStat {
  collection: string;
  exists?: boolean;
  count?: number;
  /** 该层统计失败时的原因（Milvus 不可用时出现） */
  error?: string;
}

export interface KnowledgeStatus {
  success: boolean;
  /** 生成行程时是否注入知识库（= 后端 ENABLE_RAG） */
  enabled: boolean;
  /** 知识库本身能不能用（embedding + Milvus） */
  available: boolean;
  /** available 的原因，不可用时原样展示给用户 */
  reason: string;
  milvus_uri: string;
  last_error: string | null;
  dim_expected: number;
  top_k: number;
  collections: { poi_facts: string; city_guides: string };
  poi_facts: KnowledgeNamespaceStat;
  city_guides: KnowledgeNamespaceStat;
}

export interface KnowledgeSearchRequest {
  query: string;
  namespace: KnowledgeNamespaceFilter;
  top_k?: number;
}

export interface KnowledgeSearchResponse {
  success: boolean;
  hits: KnowledgeHit[];
  elapsed_ms: number;
  /** 一层成功一层失败 —— 前端需提示"仅部分层返回结果" */
  partial: boolean;
  /** 检索失败原因（异常被 service 吞掉，只能从这里拿到） */
  error: string | null;
  namespace_counts: Record<string, number>;
}

export interface KnowledgeIngestPreview {
  success: boolean;
  poi: { total: number; per_city: Record<string, number> };
  guides: { total: number; per_file: Record<string, number> };
  embed_requests: number;
  note: string;
}

export type IngestState = 'pending' | 'running' | 'done' | 'error';

export interface KnowledgeIngestTask {
  task_id: string;
  state: IngestState;
  source: 'frozen' | 'guides' | 'all';
  recreate: boolean;
  total: number;
  processed: number;
  current: string | null;
  started_at: string;
  finished_at: string | null;
  results: Array<{
    namespace: string;
    collection: string;
    count: number;
    cities?: string[];
    files?: Record<string, number>;
  }>;
  error: string | null;
}

export interface KnowledgeSources {
  success: boolean;
  poi_inventory: {
    path: string;
    size_kb: number;
    cities: string[];
    total: number;
  };
  city_guides: {
    dir: string;
    files: Array<{ name: string; size_kb: number; sections: number }>;
  };
}
```

---

## 6. 实施批次

### 批次 R0 — 后端接口（**阻塞项，必须最先做**）

1. `_count_poi` / `_count_guides` 从脚本下沉到 `KnowledgeService`，CLI 改为调用 service（消除 2.3 的第二份逻辑）
2. 新增 `retrieve_merged(query, top_k)`，CLI 与 API 共用（消除排序逻辑重复）
3. `_embed_in_batches()` 增加可选 `progress_cb`，支撑灌库进度
4. 新增 `app/api/routes/knowledge.py` 六个接口，`main.py` 注册
5. 长任务：内存任务表 + 线程执行 + `409` 并发保护

**验收**：六个接口用 `curl` 全部跑通；Milvus 关闭时 `/status` 返回 `available=false` 且 `reason` 可读（不 500）；
`/search` 在 Milvus 关闭时返回 `error` 非空而非空 hits；灌库任务能轮询到进度到 100%。

### 批次 R1 — 前端只读部分

- 路由 `/knowledge` + 导航项
- B1 状态区（三卡，含"未启用"提示）
- B2 检索调试（含三种"无结果"的分支）
- B3 数据源区
- B5 说明区

**验收**：Milvus 关 / 开两种情况下页面表现正确；`紫禁城`、`兵马俑` 能搜到故宫与秦始皇帝陵博物院（对齐 README 的验收口径）。

### 批次 R2 — 前端灌库运维

- B4 灌库区（预览 → 选择 → 确认 → 进度 → 结果）
- `recreate` 二次确认弹窗
- 完成后自动刷新 B1 计数

**验收**：`--dry-run` 数字与 CLI 输出一致；灌库进度真实推进；`recreate` 缺 `confirm` 时后端 400、前端有明确提示。

### 批次 R3（可选）— 行程详情页的出处展示

**需要改后端存储**（见 2.2）：
- `agent._retrieve_knowledge()` 把 hits 结构化返回（现在只返回拼接后的字符串）
- `plan_store` 新增字段存这批 hits（或单独一张关联表）
- `GET /api/trip/plans/{id}` 的 `meta` 里带上 hits 摘要
- 前端在行程详情页加一张「本次参考的知识」卡片，列出 `score + source > heading_path`

**为什么要单独一批**：它碰的是行程生成主链路（`trip.py` / `agents/` / `plan_store`），
风险显著高于新增只读接口。而且前两批做完之后，"知识库是否有效"已经能通过 B2 检索调试回答了，
R3 属于锦上添花 —— 除非你明确想要"每份行程可追溯它用了哪些知识"，否则建议先不做。

---

## 7. 待确认决策点

| # | 决策 | 选项 | 我的建议 |
|---|---|---|---|
| 1 | 页面范围 | A 只读（B1+B2+B3+B5）／ B 含灌库（+B4）／ C 全含（+R3） | **B** —— 灌库是最高频的运维动作，只读页面解决不了"改完攻略怎么生效" |
| 2 | `ENABLE_RAG` 是否支持在线切换 | A 只读展示 + 说明／ B 加接口在线改（需重启才生效） | **A** —— 环境变量改了也要重启，做个假开关是欺骗 |
| 3 | 灌库任务进度 | A 前端轮询（2s）／ B 后端 SSE 推流 | **A** —— 轮询对 2 秒级刷新完全够用，SSE 要多维护一条链路 |
| 4 | 是否做 R3（行程出处） | 做／不做 | **先不做**，理由见批次 R3 |
| 5 | 导航放第几位 | 第三项／收进设置 | **第三项**，理由见 4.1 |

---

## 8. 验收清单

### 后端
- [ ] `/api/knowledge/status` 在 Milvus 关闭时返回 200 且 `available=false`、`reason` 可读（不是 500）
- [ ] `/api/knowledge/search` 在 Milvus 关闭时 `error` 非空（**不是**空 hits 无说明）
- [ ] `namespace=all` 的归并与 CLI `--query` 输出一致
- [ ] `/ingest` 并发提交第二个任务返回 409
- [ ] `/ingest` 带 `recreate=true` 但无 `confirm` 返回 400
- [ ] 灌库进度 `processed` 真实递增，终态为 `done` / `error`
- [ ] `_count_poi` / `_count_guides` 只有一份实现，CLI 与 API 共用

### 前端
- [ ] `enabled=false` 但 `available=true` 时，页面明确提示"知识库可用，生成时不使用"
- [ ] 三种"无结果"分支表现不同：检索失败 / 库为空 / 确实无命中
- [ ] `partial=true` 时有"仅部分层返回"提示
- [ ] 分数旁注明"COSINE 相似度"，无颜色分级暗示
- [ ] `recreate` 需输入确认文字才可提交，文案写明将删除的条数
- [ ] 灌库进度用真实 `processed/total`，不用假进度
- [ ] 离开页面时 `setInterval` 被清除（无内存泄漏）
- [ ] 320 / 375 / 768 / 1024 / 1440 五个宽度无横向滚动
- [ ] 三个"无结果"提示中的恢复动作（重试 / 去灌库）均可键盘触发
- [ ] 空态、加载态、错误态三态齐全，且加载中不出现空态文案

### 工程
- [ ] `npm run build`（含 `vue-tsc`）零错误
- [ ] 新页面懒加载，未进首屏包
- [ ] 无硬编码颜色（沿用 `tokens.css`）
- [ ] 新增组件复用 `StatePanel`，不另造四态实现

---

## 附：本次调研的关键事实来源

| 事实 | 出处 |
|---|---|
| RAG 无任何 HTTP 接口 | `grep -rn "knowledge\|rag\|milvus" backend/app/api/` 零命中 |
| 5 项能力的方法签名与返回契约 | `app/services/knowledge_service.py:571,629,689,735,752` |
| `retrieve()` 吞异常返回空列表 | `knowledge_service.py:584-587` |
| 运行日志截断 800 字符 | `observability/run_logger.py:178-183` |
| `run_id` 与 `plan_id` 无关联 | `run_logger.py:97` vs `api/routes/trip.py:65` |
| 统计函数在脚本里 | `scripts/ingest_knowledge.py:47,61`（及其 `:62-64` 的说明注释） |
| `enable_rag` 默认关 | `app/config.py:84`、`agents/trip_planner_agent.py:344` |
| 灌库分批大小 32 | `knowledge_service.py:71`（`_EMBED_BATCH`） |
| COSINE 度量与分数可比性 | `knowledge_service.py:404`、`:600-606` |
| 数据规模 1750 条 / 5 篇攻略 | 实测 `data/frozen/amap/poi_inventory.json` 与 `data/knowledge_base/city_guides/` |
