# 服务器部署指南

把途灵 TripMind 部署到一台 Linux 云服务器，并通过公网域名访问。

本指南基于项目当前的实际代码与配置撰写，涉及的端口、路径、端点均与仓库一致。

---

## 0. 部署架构

```
                    Internet
                       │
                  :80 / :443
                       │
              ┌────────▼────────┐
              │      Nginx      │  ← 唯一对外暴露的入口
              └───┬─────────┬───┘
                  │         │
   /              │         │  /api/*
   ▼              │         ▼
前端静态产物       │    ┌─────────────────────────────┐
(packages)        │    │  FastAPI  uvicorn           │
                  │    │  127.0.0.1:8001（仅本机）    │
                  │    └──┬───────────────┬──────────┘
                  │       │               │
                  │       ▼               ▼
                  │   SQLite 文件      uvx amap-mcp-server
                  │   data/tripmind.db  （每次调用起一个子进程，
                  │                      访问高德地图 API）
                  │
                  └─ 可选：Milvus standalone（Docker，3 个容器）
                     127.0.0.1:19530
```

**端口规划**

| 端口 | 服务 | 是否对外 |
|---|---|---|
| 80 / 443 | Nginx | ✅ 对公网开放 |
| 8001 | FastAPI 后端 | ❌ 只监听回环，由 Nginx 反代 |
| 19530 / 9091 | Milvus（可选） | ❌ 只监听回环 |
| 22 | SSH | ✅ 建议限制来源 IP |

> 后端不需要对外暴露。让 Nginx 统一入口，既省掉前端跨域配置，也避免把 `/docs` 直接暴露到公网。

---

## 1. 前置准备

### 1.1 服务器规格

| 项 | 建议 |
|---|---|
| 系统 | Ubuntu 22.04 / 24.04 LTS（命令按此编写，CentOS 需换 `yum`） |
| 配置 | 2 核 2G 起步；若要跑 Milvus 建议 4G 以上 |
| 磁盘 | 20G 起步（项目本体约 9MB，体积主要来自 Python 依赖） |

### 1.2 安全组 / 防火墙

在云控制台的安全组中放行 **80**、**443**；**不要**放行 8001。

### 1.3 域名（可选但强烈建议）

准备一个域名并解析到服务器公网 IP。用域名才能配 HTTPS，也才能给高德 JS API Key 配白名单。

> **HTTP 环境下高德地图可能无法加载**，且部分浏览器会限制定位等能力。建议直接配好域名 + HTTPS 再测试地图。

### 1.4 需要准备的三把 Key

| Key | 用途 | 申请位置 |
|---|---|---|
| 高德 **Web 服务** Key | 后端调用 MCP 工具 | 高德控制台 |
| 高德 **Web 端（JS API）** Key + 安全密钥 | 前端加载地图 | 高德控制台 |
| LLM API Key | 生成行程 | DeepSeek / OpenAI 等 |

---

## 2. 上传代码

### 2.1 方式 A：Git 克隆（推荐）

```bash
sudo mkdir -p /opt/tripmind && sudo chown $USER /opt/tripmind
git clone <你的仓库地址> /opt/tripmind
cd /opt/tripmind
```

### 2.2 方式 B：本地上传

项目本体（排除 `venv`、`node_modules`、`dist`）约 9MB，直接打包上传即可：

```bash
# 本地（Git Bash）
cd /d/devlop/helloagents-trip-planner
tar --exclude=venv --exclude=node_modules --exclude=dist --exclude=.git \
    -czf /tmp/tripmind.tar.gz .

# 上传
scp /tmp/tripmind.tar.gz user@<服务器IP>:/tmp/

# 服务器
sudo mkdir -p /opt/tripmind && sudo chown $USER /opt/tripmind
tar -xzf /tmp/tripmind.tar.gz -C /opt/tripmind
```

> ⚠️ **不要上传本机的 `backend/.env`**。生产环境的密钥应当只在服务器上填写。同理不要上传 `venv`（平台不兼容，且里面是 Windows 的可执行文件）。

### 2.3 确认这些目录跟着一起上传

`backend/data/frozen/` 与 `backend/data/knowledge_base/` 是知识库与评测的输入数据（已在 Git 中跟踪）。缺少它们时，知识库溯源功能不可用。

---

## 3. 后端部署

### 3.1 安装系统依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git nginx

python3 --version    # 需 >= 3.10（hello-agents 0.2.9 要求 >=3.10）
```

> 若系统自带版本低于 3.10，用 `deadsnakes` PPA 或 `uv python install 3.12` 装一个新版本。

### 3.2 创建虚拟环境并安装依赖

```bash
cd /opt/tripmind/backend
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> **国内服务器建议加镜像源**，否则 `pymilvus`、`markitdown` 这类包可能超时。
>
> `uv` 会随 `requirements.txt` 一起装进 venv，提供 `venv/bin/uvx`。后端启动高德 MCP 时依赖它（通过 `sys.executable` 定位绝对路径，不依赖 PATH）。

### 3.3 配置生产环境变量

```bash
cp .env.example .env
vi .env
```

生产环境必须确认/修改以下几项：

```ini
# 监听所有网卡（由 Nginx 转发，仍不建议把端口开到公网）
HOST=0.0.0.0
PORT=8001

# 密钥
LLM_API_KEY=<你的真实 Key>
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL_ID=<你的模型名>
AMAP_API_KEY=<高德 Web 服务 Key>

# ⚠️ 必改：换成你的域名，否则浏览器请求会被 CORS 拦掉
CORS_ORIGINS=https://your-domain.com

# 日志
LOG_LEVEL=INFO

# 知识库：不用就保持 false
ENABLE_RAG=false

# 本地存储（相对 backend/ 目录）
TRIPMIND_DB=./data/tripmind.db
```

```bash
chmod 600 .env      # 密钥文件收紧权限
```

### 3.4 启动冒烟测试（先用前台方式验证）

```bash
cd /opt/tripmind/backend
./venv/bin/python -m uvicorn app.api.main:app --host 0.0.0.0 --port 8001
```

另开一个终端验证：

```bash
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/api/trip/health
```

**`/api/trip/health` 返回的 `mcp_tools_count` 必须是 `16`。** 高德工具加载失败时服务仍会正常启动、接口仍返回 200，但 Agent 会转而编造景点和坐标，从外部完全看不出来。数字不对时依次检查：

1. `AMAP_API_KEY` 是否填写
2. `venv/bin/uvx` 是否存在（`ls -l venv/bin/uvx`）
3. 服务器能否访问外网（`uvx` 首次运行需要下载 `amap-mcp-server`）

验证通过后 `Ctrl+C` 停掉，改用下面的常驻方式。

> ⚠️ **不要用 `python run.py` 跑生产**：`run.py` 里写死了 `reload=True`，只适合开发。

### 3.5 用 systemd 常驻

创建服务单元：

```bash
sudo vi /etc/systemd/system/tripmind.service
```

```ini
[Unit]
Description=TripMind FastAPI Backend
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/tripmind/backend

# ⚠️ workers 必须为 1，原因见下方说明
ExecStart=/opt/tripmind/backend/venv/bin/uvicorn app.api.main:app \
    --host 0.0.0.0 --port 8001 --workers 1

Restart=always
RestartSec=5

# 日志
StandardOutput=append:/var/log/tripmind/app.log
StandardError=append:/var/log/tripmind/err.log

# 安全加固
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo mkdir -p /var/log/tripmind
sudo chown www-data:www-data /var/log/tripmind /opt/tripmind/backend/data -R
sudo systemctl daemon-reload
sudo systemctl enable --now tripmind
sudo systemctl status tripmind
```

> **为什么 `--workers` 必须是 1**
>
> 项目有两处**进程内状态**，多 worker 会导致行为不一致：
>
> - `app/api/routes/knowledge.py:277` 的 `_TASKS` 是模块级字典，负责记录灌库任务进度。多 worker 时，前端轮询 `GET /api/knowledge/ingest/{task_id}` 可能落到不认识该任务的进程上，表现为进度一直查不到。
> - `app/config.py:126` 的 `_rag_runtime_override` 是内存开关，多 worker 时会出现"页面显示已开启、实际生成时没走知识库"。
>
> 单 worker 下 FastAPI 会把同步阻塞的 `/api/trip/plan` 丢进线程池，不会卡死事件循环，足够支撑个人项目与小规模演示。

### 3.6 日志轮转（建议）

```bash
sudo vi /etc/logrotate.d/tripmind
```

```
/var/log/tripmind/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
```

---

## 4. 前端部署

### 4.1 安装 Node 并构建

```bash
# 安装 Node 20 LTS
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
node -v

cd /opt/tripmind/frontend
npm config set registry https://registry.npmmirror.com    # 国内加速
npm install
```

### 4.2 配置构建期变量

```bash
cp .env.example .env
vi .env
```

```ini
# 留空即可：前端发相对路径请求，由 Nginx 统一转发到后端
VITE_API_BASE_URL=

# 高德 Web 端（JS API）Key
VITE_AMAP_WEB_JS_KEY=<你的 JS API Key>
VITE_AMAP_SECURITY_CODE=<你的安全密钥>
```

> ⚠️ **`VITE_` 变量是在构建时被编译进产物的**。改完 `.env` 必须重新执行 `npm run build`，否则改动不会生效。
> 这些值会出现在浏览器可见的 JS 里，属于设计如此（高德 JS Key 本身就是公开的），安全性依赖高德控制台的域名白名单，**不要**把后端的高德 Web 服务 Key 或 LLM Key 写在这里。

### 4.3 构建

```bash
npm run build
```

`npm run build` 会先跑 `vue-tsc` 类型检查（开启了 `noUnusedLocals`，残留未使用的 import 会导致构建失败），通过后产物输出到 `frontend/dist/`。

### 4.4 交给 Nginx

```bash
sudo mkdir -p /var/www/tripmind
sudo cp -r /opt/tripmind/frontend/dist/* /var/www/tripmind/
sudo chown -R www-data:www-data /var/www/tripmind
```

---

## 5. Nginx 配置

```bash
sudo vi /etc/nginx/sites-available/tripmind
```

```nginx
server {
    listen 80;
    server_name your-domain.com;

    root /var/www/tripmind;
    index index.html;

    # 上传攻略文件的大小上限（后端限制为 5MB，这里留余量）
    client_max_body_size 10m;

    # ---------- 后端 API ----------
    location /api/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # ⚠️ 关键：生成一次行程需要几十秒到数分钟（4 次 LLM 调用 + MCP 工具），
        #    Nginx 默认 60s 会直接返回 504。
        proxy_connect_timeout 60s;
        proxy_send_timeout    300s;
        proxy_read_timeout    300s;

        # 关闭缓冲，长请求下更稳
        proxy_buffering off;
    }

    # ---------- 前端静态资源（带 hash，可长缓存）----------
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
        try_files $uri =404;
    }

    # ---------- SPA 路由回退 ----------
    # /result/:id、/share/:id、/history、/knowledge 都交给前端路由处理
    location / {
        try_files $uri $uri/ /index.html;
    }

    # 不对外暴露接口文档（需要时再开）
    location = /docs   { return 404; }
    location = /redoc  { return 404; }
}
```

启用并重载：

```bash
sudo ln -s /etc/nginx/sites-available/tripmind /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

此时访问 `http://your-domain.com` 应当能看到首页。

> 前端 Axios 的默认超时是 **120 秒**（`src/services/api.ts:42`），上面对后端设的 300 秒是它的上游余量，保证前端先自己超时并给出提示，而不是收到一个 Nginx 504。

---

## 6. 域名、HTTPS 与高德白名单

### 6.1 配置 HTTPS

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

Certbot 会自动改写 Nginx 配置、签发证书并设置自动续期（`systemctl list-timers | grep certbot` 可确认）。

### 6.2 配置高德域名白名单（必做）

到高德控制台，为 **Web 端（JS API）Key** 添加域名白名单：`your-domain.com`。

**不配的话地图区域会一片空白**，报 `INVALID_USER_SCODE`，现象和"Key 填错了"完全一样，很容易误判。

### 6.3 回填 CORS

确认 `backend/.env` 里已经改成生产域名：

```ini
CORS_ORIGINS=https://your-domain.com
```

```bash
sudo systemctl restart tripmind
```

> 本项目前端与后端同域（都由 Nginx 提供），理论上不会触发跨域。保留 CORS 配置是为了应对前端单独部署到 CDN / 对象存储的场景。

---

## 7. 验收清单

逐项确认后再对外公布地址：

| # | 检查项 | 命令 / 方法 | 期望结果 |
|---|---|---|---|
| 1 | 后端进程 | `systemctl status tripmind` | `active (running)` |
| 2 | 后端存活 | `curl http://127.0.0.1:8001/health` | `{"status":"healthy"}` |
| 3 | **MCP 工具加载** | `curl http://127.0.0.1:8001/api/trip/health` | **`mcp_tools_count` = 16** |
| 4 | 前端可访问 | 浏览器打开域名 | 首页正常渲染 |
| 5 | 接口连通 | 浏览器 Network 面板看 `/api/*` | 200，无 CORS 报错 |
| 6 | 高德地图 | 打开创建页 | 地图正常显示，非空白 |
| 7 | **端到端生成** | 提交一份 3 天行程 | 返回真实景点名、有坐标、耗时可接受 |
| 8 | 历史与分享 | 生成后刷新 / 打开分享链接 | 数据正常回显 |
| 9 | 知识库（如启用） | `curl http://127.0.0.1:8001/api/knowledge/status` | 返回连接正常的集合信息 |

第 3 与第 7 项是**最容易被忽略、后果最严重**的两项：MCP 静默失效时，服务一切正常但输出全是编造的景点。

---

## 8. 可选：知识库（Milvus）

不开启知识库时跳过本节即可，**主流程完全不受影响**（`ENABLE_RAG=false`）。

Milvus standalone 需要 Docker，且是 **3 个容器**（etcd + minio + milvus），对内存有一定要求。

```bash
# 安装 Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker

# 准备 Milvus 的 compose 文件（从官方仓库获取 standalone 版）
cd /opt && git clone https://github.com/milvus-io/milvus.git
cd /opt/milvus/deployments/docker/standalone
sudo docker compose up -d

# 健康检查：返回 200 才算就绪，首次启动约需 30~60 秒
curl http://127.0.0.1:9091/healthz
```

然后在 `backend/.env` 中：

```ini
ENABLE_RAG=true
MILVUS_URI=http://localhost:19530
EMBED_API_KEY=<硅基流动 Key>
```

灌库（会调用 embedding 接口，产生少量费用）：

```bash
cd /opt/tripmind/backend
./venv/bin/python scripts/check_rag_env.py                  # 环境自检，六项全绿再继续
./venv/bin/python scripts/ingest_knowledge.py --dry-run     # 先看条数，不花钱
./venv/bin/python scripts/ingest_knowledge.py --source=all
```

> 灌库脚本也可通过前端「知识库」页面上传文档来替代。
>
> ⚠️ Milvus 与后端通过 `localhost` 通信，因此两者必须在**同一台机器**上（或改 `MILVUS_URI` 指向独立部署的实例）。

---

## 9. 日常运维

### 9.1 更新代码

```bash
cd /opt/tripmind
git pull

# 后端有依赖变更时
cd backend && ./venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart tripmind

# 前端有变更时
cd ../frontend && npm install && npm run build
sudo cp -r dist/* /var/www/tripmind/
sudo systemctl reload nginx
```

### 9.2 数据备份

项目的持久化数据只有一处：**SQLite 数据库 `backend/data/tripmind.db`**（行程、运行记录、LLM 调用计量三张表）。

```bash
# 建议做成每日定时任务
sqlite3 /opt/tripmind/backend/data/tripmind.db ".backup /backup/tripmind-$(date +%F).db"
```

需要一并备份的还有：`backend/data/frozen/`、`backend/data/knowledge_base/`、`backend/.env`。

### 9.3 查看日志

```bash
sudo tail -f /var/log/tripmind/app.log      # 后端业务日志（含 4 个阶段的执行过程）
sudo journalctl -u tripmind -f              # systemd 层
sudo tail -f /var/log/nginx/error.log
```

### 9.4 本项目特有的运维注意事项

| 事项 | 说明 |
|---|---|
| **高德 MCP 每次调用起一个新进程** | 每次工具调用都会 `uvx amap-mcp-server` 起一个子进程，且其内部 HTTP 请求未设置超时。若高德不响应，调用会永久挂住（进程仍在、日志不再增长）。因此本服务**不适合高并发**，演示场景建议在 Nginx 层加限流。 |
| **单次生成耗时长** | 一次 `/api/trip/plan` 要跑 4 次 LLM 调用，实测单条约 1~2 分钟。请确保 Nginx 的 `proxy_read_timeout` 已调大（见第 5 节）。 |
| **`mcp_tools_count` 是对外服务质量的唯一外部指标** | 建议加一条定时监控（如每分钟请求 `/api/trip/health` 并告警当数值 ≠ 16）。 |
| **`--workers` 保持 1** | 见 3.5 节说明，多 worker 会破坏灌库任务状态与 RAG 开关。 |
| **不要用 `run.py` 启动生产** | 其中写死 `reload=True`。 |

---

## 10. 故障排查

| 症状 | 可能原因 | 处理 |
|---|---|---|
| 页面能开，接口全部 502 | 后端没起来 | `systemctl status tripmind`、看 `/var/log/tripmind/err.log` |
| 提交行程后等 60 秒左右报 504 | Nginx 超时太短 | 按第 5 节把 `proxy_read_timeout` 调到 300s |
| 生成成功但景点名和坐标是编的 | 高德 MCP 未加载 | 检查 `/api/trip/health` 的 `mcp_tools_count` 是否为 16 |
| 地图区域一片空白 | JS API Key 未配域名白名单 / 未配安全密钥 | 按 6.2 节配置 |
| 前端改完 `.env` 没生效 | `VITE_` 变量是构建期注入 | 重新 `npm run build` 并刷新浏览器缓存 |
| 提示"本次未能生成行程内容" | LLM 连不上（Key、额度、网络、代理） | 看 `app.log`；错误原因会随响应一起返回 |
| 灌库进度一直查不到 | 起了多个 worker | 改为 `--workers 1` 并重启 |
| CORS 报错 | `CORS_ORIGINS` 未改成生产域名 | 修改 `backend/.env` 后重启后端 |
| 知识库检索无结果、但不报错 | embedding 维度不匹配或未灌库 | `./venv/bin/python scripts/check_rag_env.py`，确认维度为 1024 |

---

## 附录：如果希望改用 Docker

**当前仓库不包含任何 Docker 配置**（无 `Dockerfile`、`docker-compose.yml`、`.dockerignore`），启动方式是上文的本机 venv + systemd。若希望容器化，可按下面的配置自行添加。

`backend/Dockerfile`：

```dockerfile
FROM python:3.12-slim

# uvx 需要 curl 下载 amap-mcp-server；ca-certificates 用于 HTTPS
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .

# SQLite 与运行时产物的落盘位置，需挂载为卷
VOLUME ["/app/data"]

EXPOSE 8001

# workers 必须为 1：项目依赖进程内状态（见部署指南 3.5 节）
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
```

`frontend/Dockerfile`：

```dockerfile
# 构建阶段
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --registry=https://registry.npmmirror.com
COPY . .
# VITE_* 变量在此阶段注入，改完必须重新构建
RUN npm run build

# 运行阶段
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
```

`docker-compose.yml`（项目根目录）：

```yaml
services:
  backend:
    build: ./backend
    env_file: ./backend/.env
    volumes:
      - ./backend/data:/app/data
    # 只在本机暴露，由 nginx 反代
    ports:
      - "127.0.0.1:8001:8001"
    restart: unless-stopped

  frontend:
    build: ./frontend
    ports:
      - "80:80"
    depends_on:
      - backend
    restart: unless-stopped
```

`.dockerignore`（放在 `backend/` 与 `frontend/` 下）：

```
venv/
node_modules/
dist/
__pycache__/
*.pyc
.pytest_cache/
data/runs/
data/*.db
.env
```

启动：

```bash
docker compose up -d --build
docker compose logs -f backend
```

> ⚠️ 容器化版本的 Nginx 与上面的裸机配置不同（前者由 `frontend` 镜像内的 Nginx 承担），因此 `proxy_read_timeout`、SPA 回退、`/api` 反代这些规则**需要单独写在 `frontend/nginx.conf` 里**并 COPY 进镜像，否则长耗时请求仍会 504、前端路由仍会 404。裸机方案（第 5 节）已包含完整配置，部署起来更直接。
