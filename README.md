# 途灵 TripMind 🌍✈️

输入目的地、日期和偏好,生成一份包含景点、天气、酒店、餐饮和预算的多日行程。

## ✨ 功能特点

- 🤖 **多 Agent 分工协作**: 景点搜索、天气查询、酒店推荐、行程整合由 4 个专职 Agent 分别完成
- 🗺️ **高德地图集成**: 通过MCP协议接入高德地图服务,支持景点搜索、路线规划、天气查询
- 🧠 **智能工具调用**: Agent自动调用高德地图MCP工具,获取实时POI、路线和天气信息
- 🎨 **现代化前端**: Vue3 + TypeScript + Vite,响应式设计,流畅的用户体验
- 📱 **完整功能**: 包含住宿、交通、餐饮和景点游览时间推荐

## 🏗️ 技术栈

### 后端
- **框架**: HelloAgents (基于SimpleAgent)
- **API**: FastAPI
- **MCP工具**: amap-mcp-server (高德地图)
- **LLM**: 支持多种LLM提供商(OpenAI, DeepSeek等)

### 前端
- **框架**: Vue 3 + TypeScript
- **构建工具**: Vite
- **UI组件库**: Ant Design Vue
- **地图服务**: 高德地图 JavaScript API
- **HTTP客户端**: Axios

## 🧭 技术选型

### 为什么选 HelloAgents

Agent 编排层有三条常见路线:

| 方案 | 优点 | 未选原因 |
|---|---|---|
| 手写 ReAct 循环 | 完全可控、零额外依赖 | 需自行实现工具调用解析、多轮循环与上下文管理 |
| LangChain / LangGraph | 生态成熟、组件丰富 | 抽象层较厚;本项目只需要 Agent 循环与 MCP 工具两块能力,引入成本大于收益 |
| **HelloAgents** | 原生 MCP 支持、内置多种 Agent 范式 | — |

选它主要省下两块工作:

- **MCPTool** 可直接对接 `amap-mcp-server`,无需自己实现 MCP 协议层
- **SimpleAgent** 内置工具调用循环(`max_tool_iterations`),无需自己实现多轮 function calling

### 框架之外自己实现的部分

HelloAgents 未内置多智能体编排能力——没有 `AgentTeam` / `Orchestrator` / 共享内存,Agent 之间也不能直接互相调用。因此以下部分由本项目自行实现:

- **多 Agent 编排**: 4 个 Agent 的调度顺序、上下文传递,以及单个 Agent 失败时的降级策略(见 `agents/trip_planner_agent.py`)
- **提示词工程**: 5 套系统提示词分别约束各 Agent 的工具使用方式与输出格式;其中行程规划 Agent 的提示词内嵌完整 JSON Schema,用于约束输出结构
- **输出解析与容错**: `_parse_response()` 处理 LLM 返回中 JSON 代码块、裸 JSON、格式异常三种情况
- **数据契约**: 用 Pydantic 模型(`TripPlan` / `DayPlan` / `Attraction` / `Budget` 等)定义 Agent 之间传递的结构,把 LLM 的自由文本输出收敛为强类型对象

## 📁 项目结构

```
tripmind/
├── backend/                    # 后端服务
│   ├── app/
│   │   ├── agents/            # Agent实现
│   │   │   └── trip_planner_agent.py
│   │   ├── api/               # FastAPI路由
│   │   │   ├── main.py
│   │   │   └── routes/
│   │   │       ├── trip.py
│   │   │       └── map.py
│   │   ├── services/          # 服务层
│   │   │   ├── amap_service.py
│   │   │   └── llm_service.py
│   │   ├── models/            # 数据模型
│   │   │   └── schemas.py
│   │   └── config.py          # 配置管理
│   ├── requirements.txt
│   ├── requirements.lock.txt
│   └── .gitignore
├── frontend/                   # 前端应用
│   ├── src/
│   │   ├── components/        # Vue组件
│   │   ├── services/          # API服务
│   │   ├── types/             # TypeScript类型
│   │   └── views/             # 页面视图
│   ├── package.json
│   └── vite.config.ts
└── README.md
```

## 🚀 快速开始

### 前提条件

- Python 3.10+
- Node.js 16+
- 高德地图API密钥 (Web服务API和Web端(JS API))
- LLM API密钥 (OpenAI/DeepSeek等)

### 后端安装

1. 进入后端目录
```bash
cd backend
```

2. 创建虚拟环境
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

3. 安装依赖
```bash
pip install -r requirements.txt
```

4. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env,把其中的占位符替换成你自己的密钥
# 需要填:LLM_API_KEY、AMAP_API_KEY、UNSPLASH_ACCESS_KEY、UNSPLASH_SECRET_KEY
```

> `.env` 已在 `.gitignore` 中,不会被提交。

5. 启动后端服务
```bash
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
```

### 前端安装

1. 进入前端目录
```bash
cd frontend
```

2. 安装依赖
```bash
npm install
```

3. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env,填入你的高德地图 Web服务 Key 和 Web端 JS API Key
```

> `VITE_` 前缀的变量会被打包进前端产物,请勿在此放入需要保密的密钥。

4. 启动开发服务器
```bash
npm run dev
```

5. 打开浏览器访问 `http://localhost:5173`

## 📝 使用指南

1. 在首页填写旅行信息:
   - 目的地城市
   - 旅行日期和天数
   - 交通方式偏好
   - 住宿偏好
   - 旅行风格标签

2. 点击"生成旅行计划"按钮

3. 系统将:
   - 由 4 个专职 Agent 分工生成初步计划
   - Agent自动调用高德地图MCP工具搜索景点
   - Agent获取天气信息和路线规划
   - 整合所有信息生成完整行程

4. 查看结果:
   - 每日详细行程
   - 景点信息与地图标记
   - 交通路线规划
   - 天气预报
   - 餐饮推荐

## 🔧 核心实现

### HelloAgents Agent集成

```python
from hello_agents import SimpleAgent, HelloAgentsLLM
from hello_agents.tools import MCPTool

# 创建高德地图MCP工具
amap_tool = MCPTool(
    name="amap",
    server_command=["uvx", "amap-mcp-server"],
    env={"AMAP_MAPS_API_KEY": "your_api_key"},
    auto_expand=True
)

# 创建旅行规划Agent
agent = SimpleAgent(
    name="旅行规划助手",
    llm=HelloAgentsLLM(),
    system_prompt="你是一个专业的旅行规划助手..."
)

# 添加工具
agent.add_tool(amap_tool)
```

### MCP工具调用

Agent可以自动调用以下高德地图MCP工具:
- `maps_text_search`: 搜索景点POI
- `maps_weather`: 查询天气
- `maps_direction_walking_by_address`: 步行路线规划
- `maps_direction_driving_by_address`: 驾车路线规划
- `maps_direction_transit_integrated_by_address`: 公共交通路线规划

## 📄 API文档

启动后端服务后,访问 `http://localhost:8000/docs` 查看完整的API文档。

主要端点:
- `POST /api/trip/plan` - 生成旅行计划
- `GET /api/map/poi` - 搜索POI
- `GET /api/map/weather` - 查询天气
- `POST /api/map/route` - 规划路线

## 🤝 贡献指南

欢迎提交Pull Request或Issue!

## 📜 开源协议

CC BY-NC-SA 4.0

## 🙏 致谢

- [HelloAgents](https://github.com/datawhalechina/Hello-Agents) - 智能体教程
- [HelloAgents框架](https://github.com/jjyaoao/HelloAgents) - 智能体框架
- [高德地图开放平台](https://lbs.amap.com/) - 地图服务
- [amap-mcp-server](https://github.com/sugarforever/amap-mcp-server) - 高德地图MCP服务器

---

**途灵 TripMind** - 让旅行计划变得简单而智能 🌈

