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
export PATH="$PWD/venv/Scripts:$PATH"        # ← 不能省,见「必须知道的坑」第 1 条
./venv/Scripts/python.exe run.py
```

Windows PowerShell 下:
```powershell
cd D:\devlop\helloagents-trip-planner\backend
.\venv\Scripts\activate
python run.py
```

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

# 前端构建(会跑 vue-tsc 类型检查;noUnusedLocals 开着,留个没用的 import 就构建失败)
cd frontend && npm.cmd run build
```

目前**没有单元测试**。`app/eval/metrics.py` 的纯函数是第一批该补测试的地方(见升级路线 P5)。

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
| `app/eval/` | **尚未创建**,见升级路线 P5 |

### 前端的三种数据来源

`Result.vue` 同时服务三条路径,改它的时候要意识到这点:

1. `/result/{id}` —— 从后端取(刷新、换标签页、分享都走这条)
2. `/result`(无 id)—— 回落到 `sessionStorage`,兼容旧路径
3. `/share/{id}` —— 同一个组件,`route.path.startsWith('/share')` 判定只读模式

## 必须知道的坑

### 1. 虚拟环境在 `backend/venv`,不在仓库根目录

位置深一层,引发过三类表现完全不同的问题,都是同一个根因:

- **MCP 静默失效**:后端的 MCP 工具靠 `venv/Scripts/uvx.exe` 启动。没激活 venv 时
  `uvx` 不在 PATH 上,高德工具从 **16 个变成 0 个** —— 但服务照常启动、接口照常返回 200,
  **不报任何错**,agent 转而编造景点和坐标。
  → 验证方式:`GET /api/trip/health` 的 `mcp_tools_count` 应为 **16**,不要凭"返回 200"下结论。
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

### 5. Python 3.14,不要碰 torch / sentence-transformers

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
| **P5** | **评测 harness + baseline 报告** | ⬜ **下一步** |
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
