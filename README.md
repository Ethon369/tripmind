# 途灵 TripMind

输入目的地、日期与偏好，生成一份包含景点、天气、酒店、餐饮和预算的多日行程。

后端 FastAPI + HelloAgents（含高德地图 MCP），前端 Vue 3 + TypeScript。

## 功能

- **多 Agent 分工**：景点搜索、天气查询、酒店推荐、行程整合由 4 个专职 Agent 按固定顺序依次执行，最后一步把前三步的自由文本收敛为强类型的行程结构
- **高德地图集成**：通过 MCP 协议接入高德地图服务，实时获取 POI、天气与路线，而不是让模型凭记忆编造景点
- **完整行程输出**：每日景点时间线、交通方式与耗时、天气、餐饮与住宿推荐、预算拆分
- **结果持久化**：行程落库，支持刷新回看、历史列表与只读分享链接
- **知识库（可选）**：上传攻略文档入库，生成时检索门票、预约、闭馆日、淡旺季等容易过时的信息拼进提示词；不开启也不影响主流程

## 技术栈

**后端**

| 用途 | 选型 |
|---|---|
| Web 框架 | FastAPI + Uvicorn |
| Agent 编排 | HelloAgents（`SimpleAgent` / `MCPTool` / Embedding） |
| 地图工具 | 高德地图 MCP Server（`amap-mcp-server`，经 `uvx` 启动） |
| LLM | OpenAI 兼容接口（DeepSeek / OpenAI 等可换） |
| 向量库 | Milvus（仅知识库使用） |
| Embedding | 硅基流动 `BAAI/bge-m3`（1024 维，REST） |
| 存储 | stdlib `sqlite3`（plans / runs / llm_calls 三张表） |

**前端**

Vue 3 + TypeScript + Vite + Ant Design Vue + 高德地图 JS API + Axios

## 项目结构

```
helloagents-trip-planner/
├── backend/
│   ├── app/
│   │   ├── agents/            # 4 个 Agent 的编排与降级策略
│   │   ├── api/               # FastAPI 入口与路由（trip / map / poi / knowledge）
│   │   ├── eval/              # 行程质量评测（自洽性、接地性、成本）
│   │   ├── models/            # Pydantic 数据契约
│   │   ├── observability/     # 结构化日志、token 计量、峰谷定价
│   │   ├── services/          # 高德解析、MCP 启动、知识库检索、文档解析
│   │   ├── store/             # SQLite 手写 SQL（plan / doc）
│   │   └── config.py          # 配置与校验
│   ├── scripts/               # 环境自检、灌库、录制、评测等运维脚本
│   ├── tests/                 # pytest（纯函数单测）
│   ├── data/                  # 冻结数据、知识库、SQLite（本地产物）
│   ├── requirements.txt
│   └── run.py                 # 启动入口
├── frontend/
│   ├── src/
│   │   ├── views/             # Home / Create / Result / History / Knowledge
│   │   ├── components/        # 业务组件
│   │   ├── composables/       # 组合式逻辑
│   │   ├── services/api.ts    # API 客户端
│   │   └── styles/            # 设计令牌与样式
│   └── vite.config.ts         # 含后端代理配置
└── docs/                      # 设计与实现文档
```

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+
- **两个高德 Key**（用途不同，需在高德控制台分别申请）：
  - **Web 服务** Key → 后端调用 MCP 使用
  - **Web 端（JS API）** Key → 前端加载地图使用
- 一个 LLM API Key（DeepSeek / OpenAI 等）

### 后端

**1. 安装依赖**

```bash
cd backend
python -m venv venv

# Windows
./venv/Scripts/python.exe -m pip install -r requirements.txt
# macOS / Linux
# ./venv/bin/python -m pip install -r requirements.txt
```

> 不需要先 `activate`。下面所有命令都直接指向 venv 里的解释器，效果一样且更不容易出错。

**2. 配置环境变量**

```bash
cp .env.example .env
```

至少填写：

| 变量 | 说明 |
|---|---|
| `LLM_API_KEY` | 你的 LLM Key |
| `LLM_BASE_URL` | 服务地址，例如 `https://api.deepseek.com`（DeepSeek **不带** `/v1`） |
| `LLM_MODEL_ID` | 模型名，在服务商控制台查询 |
| `AMAP_API_KEY` | 高德 **Web 服务** Key |

`UNSPLASH_*`、`EMBED_*`、`MILVUS_*` 可留空（仅影响景点配图与知识库）。`.env` 已在 `.gitignore` 中，不会被提交。

**3. 启动**

```bash
# Windows
./venv/Scripts/python.exe run.py
# macOS / Linux
# ./venv/bin/python run.py
```

启动后访问 API 文档：`http://127.0.0.1:8001/docs`

**4. 确认服务真的可用**

```bash
curl http://127.0.0.1:8001/api/trip/health
```

返回里的 `mcp_tools_count` **应当是 16**。高德工具加载失败时服务仍会正常启动、接口仍返回 200，但 Agent 会转而编造景点与坐标，因此不要仅凭状态码判断。数字不对时，检查 `AMAP_API_KEY` 是否填写、`./venv/Scripts/uvx.exe` 是否存在（`uv` 是 `requirements.txt` 的依赖）。

### 前端

```bash
cd frontend
npm install
cp .env.example .env      # 填入高德 Web 端（JS API）Key
npm run dev
```

打开 `http://127.0.0.1:5173`。

Windows PowerShell 下请用 `npm.cmd run dev`（执行策略会拦截 `npm.ps1`）。

> 前端发送相对路径请求，由 `vite.config.ts` 的 `server.proxy` 转发到后端，因此后端换端口只需改这一处。
> `VITE_` 前缀的变量会被打包进前端产物，请勿在此放入需要保密的密钥。

### 运行测试

```bash
cd backend
./venv/Scripts/python.exe -m pip install -r requirements-dev.txt
./venv/Scripts/python.exe -m pytest tests/ -q
```

## 使用说明

1. 在**创建行程**页填写目的地城市、日期与天数、交通方式、住宿偏好和风格标签
2. 点击「生成旅行计划」，系统依次执行：景点搜索 → 天气查询 → 酒店推荐 → 行程整合
3. 生成完成后查看结果页：每日时间线、景点与地图标记、路线规划、天气、餐饮推荐与预算
4. 历史页可回看已生成的行程；结果页可生成只读分享链接

若模型或外部服务不可用，系统会返回一份**保留行程框架但清空内容**的结果并说明失败原因，而不是伪造景点。

## 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/trip/plan` | 生成旅行计划 |
| GET | `/api/trip/health` | 后端与 MCP 工具健康检查 |
| POST | `/api/trip/parse` | 自然语言解析行程需求 |
| GET | `/api/trip/plans` | 历史行程列表 |
| GET | `/api/trip/plans/{id}` | 行程详情 |
| PUT | `/api/trip/plans/{id}` | 保存编辑后的行程 |
| DELETE | `/api/trip/plans/{id}` | 删除行程 |
| GET | `/api/map/poi` | 搜索 POI（关键词搜索不返回坐标） |
| GET | `/api/map/weather` | 查询天气 |
| POST | `/api/map/route` | 规划路线（查不到时返回 `success=false`） |
| GET | `/api/poi/detail/{poi_id}` | POI 详情（含坐标与开放时间） |
| GET | `/api/poi/search` | 关键词搜索 POI |
| GET | `/api/poi/photo` | 获取景点配图 |
| — | `/api/knowledge/*` | 知识库状态、检索、文档上传与入库 |

完整文档见 `http://127.0.0.1:8001/docs`。

### 错误响应

所有端点的错误响应格式一致：

- **业务错误**（404 行程不存在、400 参数非法等）返回对应状态码与一句可照做的说明；
- **未预期的服务端错误**返回 `500`，`detail` 是**规范化文案 + 一个错误编号**，
  例如 `"搜索POI失败(错误编号 3f8a1c02),请稍后重试"`。完整堆栈只写进服务端日志，
  不会随响应外泄（内部路径、依赖库报错原文都不出网）。把编号报过来即可在日志里定位。

## 知识库（可选）

默认关闭，不装也能跑完整流程。启用步骤：

**1. 启动 Milvus**（需要 Docker）

```bash
docker compose -f /path/to/milvus/docker-compose.yml up -d
curl http://127.0.0.1:9091/healthz     # 返回 200 才算就绪，首次启动约需 30~60 秒
```

**2. 环境自检**

```bash
cd backend && ./venv/Scripts/python.exe scripts/check_rag_env.py
```

**3. 灌入内置知识**（会调用 embedding 接口，产生少量费用）

```bash
cd backend && ./venv/Scripts/python.exe scripts/ingest_knowledge.py --dry-run    # 先看条数，不花钱
cd backend && ./venv/Scripts/python.exe scripts/ingest_knowledge.py --source=all
```

重复执行是幂等的（用 POI id / 文件名+序号做主键做 upsert），修改攻略后直接重跑即可。

**4. 开启开关**

在 `backend/.env` 中设置 `ENABLE_RAG=true`，或在**知识库页面**上传攻略后由系统临时开启（重启后恢复 `.env` 的值）。

## 说明与已知限制

- **端口使用 8001 而非 8000**：8000 常被本机其他服务（尤其是 Docker 容器）同时占用 IPv6 的 `[::]:8000`，浏览器访问 `localhost:8000` 可能连到别的服务并返回空响应。项目统一使用 `127.0.0.1` 地址。
- **高德 MCP 每次调用会新起一个 `uvx amap-mcp-server` 进程**，且其内部请求未设置超时；批量任务需自行加超时保护。
- **知识库依赖 Docker 中的 Milvus standalone**（etcd + minio + milvus 三个容器）。Milvus 不可用时检索层会跳过，行程生成不会失败。
- **不引入 torch / sentence-transformers**：目标环境为 Python 3.13+，无预编译 wheel，Embedding 统一走 REST 接口。

## 开源协议

CC BY-NC-SA 4.0

## 致谢

- [HelloAgents](https://github.com/jjyaoao/HelloAgents) - Agent 框架
- [Hello-Agents](https://github.com/datawhalechina/Hello-Agents) - 智能体教程
- [高德地图开放平台](https://lbs.amap.com/) - 地图服务
- [amap-mcp-server](https://github.com/sugarforever/amap-mcp-server) - 高德地图 MCP 服务器

---

**途灵 TripMind** - 让旅行计划变得简单而智能
