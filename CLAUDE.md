# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这个项目是什么

途灵 TripMind —— 用户填一份旅行需求(城市/日期/天数/偏好),系统生成完整行程
(每日景点、天气、酒店、餐饮、预算)。后端 FastAPI + HelloAgents 框架 + 高德地图 MCP,
前端 Vue 3 + ant-design-vue。

**这是作者的简历项目**,目标岗位是 AI / 大模型应用开发。所以除了"能跑",
还要能讲清楚技术选型和量化效果 —— 见下面的「升级路线」。

## 运行

### 后端

```bash
cd backend
./venv/Scripts/python.exe run.py            # 直接指解释器,不需要先 activate
```

Windows PowerShell 下:
```powershell
cd D:\devlop\helloagents-trip-planner\backend
.\venv\Scripts\python.exe run.py
```

> 以前这里必须写 `export PATH="$PWD/venv/Scripts:$PATH"`,否则高德 MCP 会**静默**
> 变成 0 个工具。那个坑已经在**代码里**修掉了(`app/services/mcp_launcher.py`),
> 不再依赖调用者记着激活 venv。见「必须知道的坑」第 1 条。

启动后:`http://127.0.0.1:8001/docs`

### 前端

```bash
cd frontend
npm.cmd run dev        # 用 npm.cmd 不用 npm —— PowerShell 执行策略会挡 npm.ps1
```

启动后:`http://127.0.0.1:5173`。前端发相对路径请求,由 vite proxy 转发到后端
(见 `frontend/vite.config.ts`),所以**后端换端口只改那一处**。

### 其他常用命令

```bash
# RAG 环境自检(Milvus 可连 / embedding 维度 1024 / markitdown 可导入)
cd backend && ./venv/Scripts/python.exe scripts/check_rag_env.py

# RAG 灌库 —— ⚠️ 会调 embedding 接口(花额度,约 ¥0.004/轮)
cd backend && ./venv/Scripts/python.exe scripts/ingest_knowledge.py --dry-run   # 先看多少条,不花钱
cd backend && ./venv/Scripts/python.exe scripts/ingest_knowledge.py --source=all
# 顺带做检索冒烟测试(可以指定查询)
cd backend && ./venv/Scripts/python.exe scripts/ingest_knowledge.py --source=all --query "北京 历史文化"

# 录制高德响应 —— 接地性指标的 ground truth(⚠️ 会调用高德 API,用你的 key)
cd backend && ./venv/Scripts/python.exe scripts/recorder.py --dry-run   # 先看要发多少请求
cd backend && ./venv/Scripts/python.exe scripts/recorder.py             # 真录

# 逐名核对景点是否真实存在 —— poi_exists_rate 的 ground truth(⚠️ 调用高德,额度内免费)
cd backend && ./venv/Scripts/python.exe scripts/check_poi_names.py --dry-run
cd backend && ./venv/Scripts/python.exe scripts/check_poi_names.py      # 已查过的会跳过

# 审计 poi_support_rate 里未命中的名字:是"库存不全"还是"真编造"?
cd backend && ./venv/Scripts/python.exe scripts/audit_unmatched.py --tag baseline

# 评测 —— 录制是**唯一花钱的一步**(10 条约 ¥1.2、20 分钟)
cd backend && ./venv/Scripts/python.exe -m app.eval.run_eval --mode=record --tag=baseline --dry-run  # 先看要花多少,不花钱
cd backend && ./venv/Scripts/python.exe -m app.eval.run_eval --mode=record --tag=baseline            # 真跑(无 --force 会拒绝覆盖)
# 出报告 —— 免费、不碰网络,想跑多少次跑多少次
cd backend && ./venv/Scripts/python.exe -m app.eval.run_eval --mode=replay --tag=baseline

# RAG 的 A/B 对比:同一个 tag 跑两遍,只有 --rag 不同(⚠️ 两遍都花钱)
cd backend && ./venv/Scripts/python.exe -m app.eval.run_eval --mode=record --tag=rag-off --rag=off
cd backend && ./venv/Scripts/python.exe -m app.eval.run_eval --mode=record --tag=rag-on  --rag=on
cd backend && ./venv/Scripts/python.exe -m app.eval.run_eval --mode=replay --tag=rag-off  # 不花钱
cd backend && ./venv/Scripts/python.exe -m app.eval.run_eval --mode=replay --tag=rag-on   # 不花钱
# 然后 diff data/eval/rag-off.md data/eval/rag-on.md

# 单元测试(首次先装测试依赖:./venv/Scripts/python.exe -m pip install -r requirements-dev.txt)
cd backend && ./venv/Scripts/python.exe -m pytest tests/ -q

# 变异测试 —— 故意改坏实现,确认测试会红(证明测试真的在起作用,不是走过场)
cd backend && ./venv/Scripts/python.exe scripts/mutation_check.py

# 前端构建(会跑 vue-tsc 类型检查;noUnusedLocals 开着,留个没用的 import 就构建失败)
cd frontend && npm.cmd run build
```

### 测试

测试在 `backend/tests/`,只覆盖**纯函数** —— 目前是 `app/eval/`(指标、地理计算)
和 `app/services/`(高德返回值解析、uvx 定位)。这些函数不碰网络、不碰数据库、
不调 LLM,构造一个 `EvalContext` 或几个临时文件就能验证,所以跑一轮不到 1 秒。

`tests/conftest.py` 里有构造行程的辅助函数(`make_plan` / `make_day` / ...),
默认造一份**健康的**行程;测试想验证某个问题能被抓到,就只改坏那一处。

**测试全绿不等于测试有效。** 一个 `assert True` 也能全绿。所以改完指标函数要跑
`scripts/mutation_check.py` —— 它故意把实现改坏,确认对应的测试会红。
改坏了却没人报错,说明那段逻辑没被测到。

## 架构大图

### 这是个 Workflow,不是多智能体协作

`MultiAgentTripPlanner.plan_trip()` 是**硬编码的 4 步管线**,顺序固定:

```
景点搜索 → 天气查询 → 酒店推荐 → 行程规划(汇总前三步)
```

4 个 Agent **互不通信**,也不自主决策下一步做什么 —— 叫"多智能体"只是因为
拆了 4 个各带提示词的 `SimpleAgent`。第 4 步的提示词里内嵌完整 JSON Schema,
把前 3 步的自由文本收敛成强类型的 `TripPlan`。

这一点要说准,不要把项目描述成"Agent 自主协作"。

HelloAgents 框架**没有**多智能体编排能力(没有 AgentTeam / Orchestrator / 共享内存)。
上面的调度顺序、上下文传递、失败降级都是本项目自己写的,在 `agents/trip_planner_agent.py`。

### 数据流

```
POST /api/trip/plan
  → plan_store.create_running()      先落一条 status='running' 的记录(拿到 plan_id)
  → collect_usage()                  开一个用量收集器(contextvars,为将来并发准备)
  → agent.plan_trip(request, observer)
      4 个阶段各调一次 observer.stage_start/end
  → plan_store.finish_plan()         回填结果 + 成本 + 降级原因
  → TripPlanResponse(... plan_id=...) 前端靠 plan_id 跳 /result/{id} 和拼分享链接
```

三个存储/观测子系统都通过**窄接口**接入,改它们不影响业务逻辑:

| 目录 | 职责 |
|---|---|
| `app/observability/` | `run_logger.py` 记 JSONL 事件流;`metering.py` 计量 token;`pricing.py` 峰谷定价 |
| `app/store/` | stdlib `sqlite3` 手写 SQL,3 张表(plans / runs / llm_calls) |
| `app/eval/` | 评测 harness:`geo`/`config`/`metrics`(自洽性+成本)/`metrics_grounding`(接地性)/`run_eval`(录制回放 CLI)/`report`(出 markdown) |
| `app/agents/fallback.py` | 降级行程:保留请求的结构,**一个内容都不编**。纯函数,单测覆盖(降级路径 baseline 触发不到,只能靠单测) |
| `app/services/amap_parsing.py` | 高德返回值解析(剥 MCP 外壳、拆 `"经度,纬度"`)。纯函数,`recorder.py` 与 P6 共用 |
| `app/services/mcp_launcher.py` | 拼启动高德 MCP 的命令,用绝对路径找 `uvx`。纯函数,两个 `MCPTool` 创建点共用 |
| `app/services/knowledge_service.py` | RAG 双层知识库,**直接建在 Milvus 上**(框架不支持 Milvus)。分块/归一化/引用格式是纯函数,单测覆盖 |

### 评测为什么要「录制 / 回放」两层

改指标口径、改报告排版是天天要做的事,而跑一次真生成要 **¥1.2 和 20 分钟**。
两者绑在一起的结果是:**你再也不会去改指标了**。

| 模式 | 做什么 | 花钱吗 |
|---|---|---|
| `--mode=record` | 跑 agent,把行程冻到 `data/frozen/plans/{tag}/` | ¥0.12/条 |
| `--mode=replay` | 读冻结的行程算指标、出报告 | **不花**,不碰网络 |

冻的**不只是行程**,还有当时的**环境指纹**(git sha、是否脏工作区、模型、
temperature、编排模式)。没有它,翻出一份旧报告时你不知道它是哪个提交测的 ——
那样的前后对比是假的。指纹在报告第二章里。

这和 P2 冻结高德响应是同一个思路:**把「输入」和「被测系统」隔开**,
两次跑的差异才只可能来自代码。

⚠️ `record` 会花真钱**并且覆盖已有数据**,所以不加 `--force` 时直接拒绝执行。

**两个 oracle,测的不是一回事 —— 别只看一个**

| 指标 | oracle | 实际测的是 |
|---|---|---|
| `poi_support_rate` | 我录的 POI 库存(20 个关键词的**样本**) | **库存覆盖率** |
| `poi_exists_rate` | 拿名字去高德搜一次 | **名字是否真实存在** |

baseline 里同一批名字:前者 **0.57**、后者 **0.98**。差值全部是库存覆盖率 ——
库存装不下"有名但没被关键词命中"的地方(国子监、798、上海中心大厦)。
**只看 `poi_support_rate` 会把库存不全读成模型在编**,然后去修一个不存在的 bug。
`scripts/audit_unmatched.py` 专门做这个对比。

### RAG 是两层,而且**检索层是自己写的**

| namespace | 数据源 | 回答什么 | 对指标的贡献 |
|---|---|---|---|
| `poi_facts` | 冻结的高德 POI 库存(1750 条,**带真实坐标**) | 这城市有哪些**真实**景点、在哪、什么类型 | `poi_exists_rate` / `coord_mae_km` |
| `city_guides` | 手写 markdown 攻略(5 城 × 6 块) | 怎么安排才合理(门票、预约、淡旺季、避坑) | 文案质量 / `intraday_travel_km_p95` |

**`city_guides` 才是「为什么要用 RAG」的正当答案**:这些是 LLM 会记错或过时的知识
(门票涨价、闭馆日、预约规则)。`poi_facts` 单看有点同义反复(工具本来就能搜到 POI),
但它补的是**高德搜索不给坐标**那个缺口 —— 见坑 #4。

**为什么不用框架的 RAG**:`hello_agents` 的 RAG(`memory/rag/pipeline.py`,1100+ 行)
**只支持 Qdrant**,`memory/storage/` 下只有 qdrant/neo4j/document 三个,
`grep -ril milvus` 返回空;`create_rag_pipeline()` 的签名里**没有 `store` 参数**,
内部无条件 `new QdrantVectorStore`,也没有 ABC 或注册表可以挂新后端。
所以检索层写在 `app/services/knowledge_service.py` 里(约 470 行,线性的,好懂)。
embedder 仍然复用框架的(`DashScopeEmbedding` → 硅基流动 REST,bge-m3,1024 维)。

**注入方式:步骤 3.5 在 Python 里检索后拼进 planner 的 prompt**,不挂成工具。
理由:时机确定(不会被 `max_tool_iterations` 挤掉)、query/分数/来源可记录、
不占模型的推理轮次。

⚠️ **开关关掉时 prompt 与加 RAG 之前逐字相同** —— `tests/test_planner_query.py`
里有一条盯着这件事的测试。这条不测的话,A/B 两组数字的差异就说不清是 RAG
带来的还是 prompt 结构变了带来的,**那种对比不如不做**。

### 前端的三种数据来源

`Result.vue` 同时服务三条路径,改它的时候要意识到这点:

1. `/result/{id}` —— 从后端取(刷新、换标签页、分享都走这条)
2. `/result`(无 id)—— 回落到 `sessionStorage`,兼容旧路径
3. `/share/{id}` —— 同一个组件,`route.path.startsWith('/share')` 判定只读模式

## 必须知道的坑

### 1. 虚拟环境在 `backend/venv`,不在仓库根目录

位置深一层,引发过两类表现完全不同的问题,都是同一个根因:

- ~~**MCP 静默失效**~~ **已在代码里修掉**。后端原来用**裸命令**
  `["uvx", "amap-mcp-server"]` 起高德 MCP,`uvx` 要靠 PATH 解析;venv 没激活时
  `venv/Scripts` 不在 PATH 上,高德工具从 **16 个变成 0 个** —— 但服务照常启动、
  接口照常返回 200、**不报任何错**,agent 转而编造景点和坐标。

  现在改用 `app/services/mcp_launcher.py` 的 `resolve_uvx_command()`,用
  `sys.executable` 取**绝对路径**(`uvx` 必定和解释器同目录,因为 `uv` 是
  `requirements.txt` 的依赖),不看 PATH。

  实测(PATH 清到只剩 `C:\Windows\System32`,即 `shutil.which("uvx") is None`):

  | 写法 | 工具数 |
  |---|---|
  | 旧 `server_command=["uvx", ...]` | **0**(且无报错) |
  | 新 `server_command=resolve_uvx_command()` | **16** |

  ⚠️ 但**这不等于"环境随便配都行"**:`resolve_uvx_command()` 找不到时仍会
  静默退回裸 `"uvx"`,行为与改动前一致。所以 `GET /api/trip/health` 的
  `mcp_tools_count` 仍应是 **16**,跑批量任务前值得瞟一眼。让它真的"响"起来
  (启动报警 / health 报 degraded)是**第二步,还没做**。
- **VS Code Pylance 报"无法解析导入 hello_agents"**:Python 扩展只在**工作区根目录**找
  `.venv`/`venv`,找不到就回退到机器上别的 Python(这台机器上 `D:\devlop\Python\python.exe`
  是 3.13.9,没装 hello_agents)。`.vscode/settings.json` 已修好(该文件被 gitignore)。

### 2. 端口是 8001,不是 8000

8000 被这台机器上**另一个项目**的 Docker 容器占着,而且 Docker 会连着 IPv6 的 `[::]:8000`
一起抢。浏览器访问 `localhost:8000` 时 Windows 优先走 IPv6 → 连到 Docker → 那边接了连接
却不返回内容 → `ERR_EMPTY_RESPONSE`,**看起来像后端没启动,实际后端一直好好的**。

同类问题:地址一律写 `127.0.0.1` 而不是 `localhost`;vite 也显式绑了 `127.0.0.1`
(默认 host 在 Windows 上解析成 `::1`,只监听 IPv6)。

### 3. 输出被重定向时,emoji 会炸掉整个应用启动

项目日志里大量用 emoji。输出到**控制台**时 Python 走 UTF-8 没问题,但被**重定向**
(管道 / `> log.txt` / 后台运行 / CI)时 stdio 默认 GBK,`print("🚀 ...")` 抛
`UnicodeEncodeError`。它发生在 FastAPI 的 startup 事件里,后果是**整个应用启动失败**。

已在 `app/__init__.py` 里把 stdio 强制成 UTF-8(放在这里是为了让所有入口都生效)。

### 4. 降级路径不再编造(P6 已修)

**背景(baseline 实测)**:原来 `_create_fallback_plan()` 会现造一份"看起来正常"
的行程 —— 假景点名 `上海景点1`、写死的北京坐标 `116.4 + i*0.01`、
"这是为您规划的…",而且 `success=True`。但 P5 的 baseline 显示它**从没被触发过**:

| 指标 | baseline 实测 |
|---|---|
| `fallback_used` | **0 / 10** |
| `poi_exists_rate` | **0.9789**(74 个景点名 74 个真实) |
| `coord_mae_km` | **1.25 km**(8/9 通过) |

所以"编造率"这个数字没问题 —— 但那颗雷一直在,只等 LLM 哪天输出解析不出来。

**现在改成**(`app/agents/fallback.py`):**宁可空,也不要假**。

| 元素 | 怎么办 |
|---|---|
| 天数、日期、交通、住宿 | **保留** —— 它们来自用户的请求,是事实 |
| 景点 / 餐饮 / 酒店 / 预算 | **清空** —— 编不出真的就不放假的 |
| `overall_suggestions` | **写清失败原因**,并明确"请勿据此出行" |
| `TripPlanResponse.success` | **降级时 `False`**(前端两边都已处理该分支) |

**⚠️ 为什么单独一个模块**:原来它是 `MultiAgentTripPlanner` 的方法,要构造整个
agent 才能调 → **没法单测**。而降级路径在 baseline 里从没被触发过,改完
**不能靠重跑 baseline 证明它对了**(重跑只会得到一模一样的数字)。只能靠单测。

**⚠️ 顺手删了一段不可达的代码**:一开始给 `travel_days` 加了个 `min(…, 30)` 防线,
写测试时才发现 `TripRequest.travel_days` 上有 `ge=1, le=30`,越界请求在构造
pydantic 模型那步就被拒了,根本传不到这里。留一段永远不执行、也测不到的代码,
只会让人以为有保护。

`maps_text_search` 只返回 id/name/address/typecode 没有坐标,要 `maps_search_detail(id)`
才有 `location` —— 这个事实仍然成立,`app/services/amap_service.py` 里 4 个 `TODO` 也还在。
解析要用 `app/services/amap_parsing.py` 里那两个**被测过**的函数,不要重写:
`unwrap_mcp_result()` 剥 MCP 文本外壳(用贪婪正则会在嵌套 JSON 上取过头)、
`parse_location()` 拆 `"经度,纬度"` 字符串(顺序和常见的 "lat,lng" 相反)。

**P5 的 harness 量化这个问题的两条指标**(互补,都要看):
`poi_exists_rate` 抓"名字是编的",`coord_mae_km` 抓"名字对了但坐标是编的"。
后者 `coord_coverage` 抓不到(假坐标只要落在城市范围内就放行)。

### 5. `MCPTool.run()` 没有超时 —— 会永久卡死

每次调用都会新起一个 `uvx amap-mcp-server` 进程,而那个服务内部的
`requests.get(...)` **没有设 timeout**,`MCPTool.run()` 也没有。所以高德
只要不回应,调用就**永久挂住**:进程还在、日志不再增长,整轮任务悄无声息地停住。

实测卡过 17 分钟没动静,代码里没有任何机制能救回来。

跑批量任务(如 `scripts/recorder.py`)必须自己加超时。可复用 `recorder.py` 里的
`call_with_timeout()`;注意要用 **daemon 线程**(非 daemon 会让解释器退出时一直等它)。

### 6. 匹配 POI 名必须带上 `alias` 字段

高德给约 16% 的 POI 返回别名(多个用 `|` 分隔),里面装的正是"俗称 vs 官方名"
对不上的那批,**而且往往是最大牌的景点**:

```
秦始皇帝陵博物院 → 西安兵马俑|秦始皇兵马俑博物馆     ← 说"兵马俑"完全正确
华清宫          → 华清池
故宫博物院       → 紫禁城
西安钟楼        → 钟楼
杭州西湖风景名胜区 → 西湖景区
```

不带别名匹配,行程里写"兵马俑"就会被判成**查无此物** —— 假警报比指标偏低更糟,
它会让人以为模型在编造。见 `app/eval/metrics_grounding.py` 的 `candidate_names()`。

### 7. 城市范围框:官方数据常是**度分**,不是小数

`CITY_BBOX` 里的值踩过这个坑:西安的官方范围写作 `34°45′N`,正确换算是
`34.75`,最初按小数读成了 `34.45` —— 框小了 0.3 度,于是阎良区、高陵区、
蓝田县的 7 个合法 POI 全被判成"坐标越界"。

已有数据驱动的回归测试兜底:`tests/test_geo.py::TestBboxAgainstRealData`
拿录制库存反过来验(搜索时传了 `citylimit=true`,所以落在框外的必然是框画小了)。

### 8. `maps_geo` **不是** POI 存在性 oracle,`maps_text_search` 才是

测"景点名是不是编的"时,第一版用了 `maps_geo`(地理编码)—— 因为拿 `国子监`
试了一下返回 `level=兴趣点`,以为能用。跑完 74 个名字才发现是错的:

```
外滩        → level=住宅区       南京路步行街 → level=道路
豫园        → level=乡镇        东方明珠     → **返回空**(上海最著名的地标)
```

`maps_geo` 做的是**地址 → 坐标**的解析,它把输入当地址看。POI 名不是地址,
所以地标名(东方明珠)解析不出来,而"外滩"这种区域名会被解析成行政区划。

正确工具是 `maps_text_search`(**POI 关键词搜索**,就是建库存用的那个)。

⚠️ **判据是「搜到的像不像」,不是「有没有搜到」。** 高德对编造的名字**不会返回空**,
总会硬凑一堆相关 POI 给你:

```
搜「紫金幻梦星际主题乐园」→ 返回「泡泡玛特城市乐园」「咘隆家族主题乐园」
搜「火星大饭店北京分店」  → 返回「南京大饭店」「北京饭店大堂」
```

光看"返回非空"会把编造的名字**全部放行**,而且看不出来。必须比名字相似度。
见 `scripts/check_poi_names.py`,判定逻辑集中在 `metrics_grounding.checked_verdict()`。

### 9. httpx 会把「启动那一刻的系统代理」记死在客户端里

后端报 `WinError 10061 由于目标计算机积极拒绝` / `openai.APIConnectionError`,
但**同一台机器上 `curl` 同一个地址却通** —— 大概率是这个:

- httpx 的 `trust_env` 会调 `urllib.request.getproxies()`,它在 **Windows 上会
  回退去读系统注册表**里的代理设置(VPN / Clash 这类工具写在那儿)
- 而代理是在**客户端构造那一刻**解析的。`get_llm()` 是单例,所以那个 httpx
  客户端只在**后端启动时**读一次代理
- **之后你把 VPN 关掉,httpx 还在往那个已经没人监听的端口发请求**

三条症状同时出现就是它:①`curl` 通、后端不通(curl 只读环境变量,不读注册表);
②重启后端就好了;③日志 traceback 里有一行 `httpcore/_sync/http_proxy.py`。

```bash
# 排查:当前实际生效的代理
cd backend && ./venv/Scripts/python.exe -c "from urllib.request import getproxies; print(getproxies())"
# 返回 {} 说明现在没有代理,重启后端即可恢复
```

表现是"点生成,弹『本次未能生成行程内容』" —— 那是降级路径**正常工作**
(LLM 连不上 → 返回空框架,而不是编一份假的)。弹窗里现在会带上原因。

> ⚠️ 别用 `reg query` 查代理 —— Git Bash 里**没有 `reg` 命令**,它会以
> `command not found` 失败。如果你只 grep 关键字,看到的是一片空白,
> **极易误读成"注册表里没有代理"**(这个误判真发生过)。

### 10. Python 3.14,不要碰 torch / sentence-transformers

没有预编译 wheel,Windows 下装必失败。RAG 的 embedding 走硅基流动的 REST 接口
(`BAAI/bge-m3`,1024 维)。这也是不引 ORM、用 stdlib `sqlite3` 的原因。

### 11. 高德的 `alias` 字段**类型不一致** —— 守卫条件对"恰好有数据的那批"恒为假

这是本项目里最阴的一个坑,踩过。

同一个 `alias` 字段,有时是字符串、有时是列表。实测冻结库存 1750 条:

```
 240 条  alias 是 **字符串**   "紫禁城" / "中国历史博物馆|北京历史博物馆"
1510 条  alias 是 **空列表**   []
```

**有别名的那 240 条全部是字符串,而列表全部是空的。** 所以第一版写的

```python
if isinstance(aliases, (list, tuple)) and aliases:   # ← 对那 240 条恒为假
```

**一行都没生效过**,别名从来没进过库,而且**不报任何错**,检索结果只是悄悄变差。
`scripts/ingest_knowledge.py --query "紫禁城"` 是唯一能看出问题的办法。

修法是 `knowledge_service.normalize_aliases()`,统一按 `|` 拆成 `list[str]`。

**但比这个 bug 本身更值得记住的是它怎么被发现的:第一版单测全绿。**
因为测试里我手写的是 `["紫禁城"]`(**我以为**的形状),不是数据里真实的 `"紫禁城"`。
测试验证的是我的假设,不是现实。

所以现在 `tests/test_knowledge.py::TestAliasesAgainstRealData` 直接读
`data/frozen/amap/poi_inventory.json`(**真实录制的数据**)反过来验:
每一条有别名的 POI,它的别名都必须出现在 `poi_to_text()` 的输出里。
这与坑 #7 的 `TestBboxAgainstRealData` 是同一个思路 —— **构造的输入只能验证
"我以为的世界",只有真数据能验证"实际的世界"**。

修复前后(能直接写进简历的对比):

| 查询 | 修复前 | 修复后 |
|---|---|---|
| `紫禁城` | 完全搜不到故宫(第1名是"人民公园-紫藤架" 0.5288) | **故宫博物院 第1名 0.6356** |
| `兵马俑` | 秦始皇帝陵博物院 只排第3(0.4696) | **第1名 0.5980** |

### 12. 别拿第三方库的**导入副作用**当自己的配置来源

`pymilvus/settings.py:6` 在 **import 时**调用 `load_dotenv()`(无参数),
而 `find_dotenv()` 是**按 cwd** 找 `.env` 的。于是:

- 在 `backend/` 下 `import pymilvus` → 顺手把 `.env` 灌进了 `os.environ`
- 不 import pymilvus 时,`EMBED_API_KEY` 就是**空的**,
  而 `get_text_embedder()` 会直接抛 `RuntimeError: 所有嵌入模型都不可用`

**「embedder 能不能用」一度取决于哪个库先被 import。** 当时一个探针脚本能跑通
纯属运气(它先 import 了 pymilvus),换成不 import 它的脚本就炸。
同类问题:`app/config.py:11` 的 `load_dotenv()` 也是按 cwd 找的,
从仓库根目录启动就找不到,配置全空**而且不报错**。

对策:`knowledge_service` 里两件事都显式做了 ——
`_ensure_env_loaded()` 用**绝对路径**读 `backend/.env`;
embedder 用不带 fallback 的 `create_embedding_model()` **显式传参**构造,
构造失败就响亮地抛,不静默退化成假实现。

### 13. Milvus 的运维代价(选它之前要知道)

- **Docker Desktop 必须开着。** Milvus 装了 ≠ 随时能用。Docker 守护进程没起时,
  `docker ps` 直接报 `npipe:////./pipe/dockerDesktopLinuxEngine` 连不上。
  每次要用先启动 Docker Desktop,再 `cd D:/devlop/Milvus && docker compose up -d`。
- **standalone 是三个容器**(etcd + minio + milvus),比单容器的 Qdrant 重,
  首次启动要等 30~60 秒 `http://127.0.0.1:9091/healthz` 才返回 200。
- **容器被「重建」后可能报** `InvalidateCollectionMetaCache failed ...
  node not match[expectedNodeID=2][actualNodeID=3]` —— etcd 里记着旧 proxy 的节点 ID。
  解:`docker compose down && docker compose up -d`(数据在 bind mount 里,
  `D:/devlop/Milvus/volumes/milvus`,`down` **不会**删数据)。
- **Milvus Lite 在 Windows 上不可用。** pymilvus 的 `milvus-lite` extra 里写着
  `sys_platform != "win32"`,所以"嵌入式、免 Docker"这个卖点在 Windows 上拿不到。
- 客户端版本要和服务端对齐:服务端 2.5.14 → `pymilvus>=2.5,<2.6`。
  pymilvus 3.x 是给 Milvus 3.x 服务端用的,别升。

### 14. `git push` 报 408 / "unexpected disconnect" —— 是 **git 自己掐断的**,不是服务器

症状特别有误导性:

```
error: RPC failed; HTTP 408 curl 22 The requested URL returned error: 408
send-pack: unexpected disconnect while reading sideband packet
fatal: the remote end hung up unexpectedly
```

`unexpected disconnect` 听着像"服务器挂了"或"网断了",**实际是 git 客户端主动放弃**。

排查顺序(下面每步都实测过,弯路就别再走了):

1. **`git ls-remote origin main` 能通吗?** 这台机器上它**通**(连续三次成功),
   而 `push` 全部失败。**读能通、写不通** → 不是"连不上 GitHub",是传输中被中断。
2. `git config --get http.postBuffer` —— 这台机器上被设成了 500MB
   (说明之前就撞过这个墙),但**调大调小都没用**。
3. `http.version=HTTP/1.1`、`--no-thin` —— **都没用**,不是病因。

**真凶:`http.lowSpeedLimit`。** git 默认在传输速率掉到 1000 字节/秒以下时
**主动掐断连接**。这台机器走 **Steam++ 加速器**(见下),隧道速度会抖,
一抖就被 git 自己杀掉,报出来却是"remote end hung up"。

**解**(已在本仓库设好,存于 `.git/config`,机器相关、不随仓库提交):

```bash
git config --local http.lowSpeedLimit 0
git config --local http.lowSpeedTime 999999
```

⚠️ **排查时我自己犯的两个错,写下来免得再犯:**

- 用 `netstat -ano | grep "127.0.0.1:443 "` 查加速器在不在 → **查不到**,
  于是我得出「工具没在跑」的**错结论**。实际它监听在 **`0.0.0.0:443`**,
  是我的 grep 写窄了。改用
  `Get-NetTCPConnection -State Listen -LocalPort 443`(带进程名)。
  **这和坑 #9 里用 `reg query` 得出反结论是同一类错误**:
  命令本身用错了,空白的输出被当成了否定证据。
- 用 `git rev-list --objects A..B | git cat-file --batch-check='%(objectsize)'`
  估推送体积,算出 **1.15 GB**,虚惊一场 —— 实际最大对象 0.03 MB、整包约 200 KB。
  **测量命令写错了。** 换成 `'%(objecttype) %(objectsize) %(rest)'` 再排序才对。

**那块 hosts 是谁写的**:`C:\Windows\System32\drivers\etc\hosts` 里有 63 条
`127.0.0.1` 重定向(GitHub / Steam / Google / Docker Hub / Greasyfork),
是 **Steam++(Watt Toolkit)加速模式**生成的 —— 它把域名指到本机,
自己在 `0.0.0.0:443` 做反向代理转发。

**想让 git 直连(比如配合 VPN)时,别手动去删 hosts** ——
先在加速器里关掉对应的加速项,它通常会自己把那些条目清掉;
手动删了它会再加回来,而且容易删漏。

## 升级路线(P0–P9)

完整方案在 `~/.claude/plans/1-2-rag-subagen-harness-3-piped-spark.md`。当前进度:

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 初版上 GitHub | ✅ |
| P1 | RAG 环境自检 | ✅ |
| P2 | 可观测性 + 回调式管线 + 修 health 端点 | ✅ |
| P3 | token / 成本计量 | ✅ |
| P4 | SQLite 持久化 + 三页面 + 分享链接 | ✅ |
| **P5** | **评测 harness + baseline 报告** | ✅ **完成**:`data/eval/baseline.md`,10 条行程,共花 ¥0.465 |
| P6 | 数据接地:让降级路径**不再编造** | ✅ 降级路径已修(`app/agents/fallback.py`);`enrich_pois` 未做(见坑 #4,收益有限) |
| **P7** | **RAG 双层知识库(poi_facts + city_guides)** | ✅ **代码完成**:检索层 `app/services/knowledge_service.py` + 灌库(1750 + 30 条)+ 接进 planner(`--rag` 开关)。**A/B 数字还没跑**(要花 LLM 额度,作者自己挑时间跑) |
| P8 | 并发 + supervisor-worker 编排对比 | ⬜ **作者决定不做** |
| P9 | 前端去杂乱 + 修 4 个 bug | ⬜ |

**顺序有依赖**:P5 的 baseline 是 P6/P7 的对照组,没有它"我改好了"无法量化。

⚠️ **P7 跑 A/B 时不要拿旧的 `baseline` 当对照组。** 它是**旧提交**录的,
会把 P6/P7 的代码改动和 RAG 混在一起。正确做法是同一个 tag 跑两遍
(`--rag=off` / `--rag=on`),两侧用同一份代码,差异才只可能来自那段知识。

## 工作方式

作者是编程新手,明确担心"改动太多后理解不了自己的项目"。所以:

- **优先新增文件,少动现有代码**;每个阶段独立可交付、可回滚
- 注释要写**为什么**,不只是**是什么**
- 验证要给出可复现的命令和**真实输出**,不要只说"应该没问题"
- 需要取舍的决策(端口、方案选择)直接问,不要替他定
- **不要自行触发真实的 LLM 生成** —— 那是花他自己的 API 额度
