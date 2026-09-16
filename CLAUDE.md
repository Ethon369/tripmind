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
# RAG 环境自检(Qdrant 可连 / embedding 维度 1024 / markitdown 可导入)
cd backend && ./venv/Scripts/python.exe scripts/check_rag_env.py

# 录制高德响应 —— 接地性指标的 ground truth(⚠️ 会调用高德 API,用你的 key)
cd backend && ./venv/Scripts/python.exe scripts/recorder.py --dry-run   # 先看要发多少请求
cd backend && ./venv/Scripts/python.exe scripts/recorder.py             # 真录

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
| `app/eval/` | 评测 harness:`geo`/`config`/`metrics`(自洽性+成本)/`metrics_grounding`(接地性) |
| `app/services/amap_parsing.py` | 高德返回值解析(剥 MCP 外壳、拆 `"经度,纬度"`)。纯函数,`recorder.py` 与 P6 共用 |
| `app/services/mcp_launcher.py` | 拼启动高德 MCP 的命令,用绝对路径找 `uvx`。纯函数,两个 `MCPTool` 创建点共用 |

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

### 4. 项目当前会静默编造数据(P6 待修)

`agents/trip_planner_agent.py` 的 `_create_fallback_plan()` 会生成"北京景点1"这种假数据,
坐标用 `116.4 + i*0.01` 现算 —— **生成上海的计划会得到北京的坐标**,而且 `success=True`。

根因不在 LLM:`maps_text_search` 这个高德 MCP 工具**只返回 id/name/address/typecode,
没有坐标**;要 `maps_search_detail(id)` 才有 `location`。提示词却要求"经纬度坐标要真实准确",
LLM 手上没数据只能编。`app/services/amap_service.py` 里 4 个 `TODO` 就是该补的解析。

解析要用 `app/services/amap_parsing.py` 里那两个**被测过**的函数,不要重写:
`unwrap_mcp_result()` 剥 MCP 文本外壳(用贪婪正则会在嵌套 JSON 上取过头)、
`parse_location()` 拆 `"经度,纬度"` 字符串(顺序和常见的 "lat,lng" 相反)。

P5 的 harness 怎么量化这个问题:两个互补的指标 ——
`poi_support_rate` 抓"名字是编的",`coord_mae_km` 抓"名字对了但坐标是编的"。
后者 `coord_coverage` 抓不到(假坐标只要落在城市范围内就放行),
所以**不能只看 coord_coverage**。

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

### 8. Python 3.14,不要碰 torch / sentence-transformers

没有预编译 wheel,Windows 下装必失败。RAG 的 embedding 走硅基流动的 REST 接口
(`BAAI/bge-m3`,1024 维)。这也是不引 ORM、用 stdlib `sqlite3` 的原因。

## 升级路线(P0–P9)

完整方案在 `~/.claude/plans/1-2-rag-subagen-harness-3-piped-spark.md`。当前进度:

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 初版上 GitHub | ✅ |
| P1 | RAG 环境自检 | ✅ |
| P2 | 可观测性 + 回调式管线 + 修 health 端点 | ✅ |
| P3 | token / 成本计量 | ✅ |
| P4 | SQLite 持久化 + 三页面 + 分享链接 | ✅ |
| **P5** | **评测 harness + baseline 报告** | 🔶 **进行中**:5a 指标 ✅ / 单测 ✅ / 5b 接地性 ✅ / 5c CLI+报告 ⬜ |
| P6 | 数据接地,修掉上面的编造问题 | ⬜ |
| P7 | RAG 双层知识库(poi_facts + city_guides) | ⬜ |
| P8 | 并发 + supervisor-worker 编排对比 | ⬜ |
| P9 | 前端去杂乱 + 修 4 个 bug | ⬜ |

**顺序有依赖**:P5 的 baseline 是 P6/P7 的对照组,没有它"我改好了"无法量化。

## 工作方式

作者是编程新手,明确担心"改动太多后理解不了自己的项目"。所以:

- **优先新增文件,少动现有代码**;每个阶段独立可交付、可回滚
- 注释要写**为什么**,不只是**是什么**
- 验证要给出可复现的命令和**真实输出**,不要只说"应该没问题"
- 需要取舍的决策(端口、方案选择)直接问,不要替他定
- **不要自行触发真实的 LLM 生成** —— 那是花他自己的 API 额度
