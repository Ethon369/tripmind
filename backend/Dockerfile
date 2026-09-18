# 途灵 TripMind —— 后端镜像
#
# 为什么是 python:3.12-slim:
#   - hello-agents 要求 >=3.10,3.12 是有 wheel 覆盖最完整的一档
#   - 本机开发用的是 3.14,但 3.14 上部分依赖(pymilvus / markitdown 的
#     PDF 后端)的预编译 wheel 还不齐,构建容易卡在编译上
#   - slim 而不是 alpine:C 扩展(pymilvus、Pillow)在 musl 上要么没有
#     wheel、要么行为有差异,省下的几十 MB 不值得
#
# 这个镜像**自带 uvx**:uv 是 requirements.txt 的依赖,pip 会把它装到
# 和解释器同一个 bin 目录。Agent 靠 `uvx amap-mcp-server` 起高德 MCP,
# 而 app/services/mcp_launcher.py 是用 sys.executable 的目录去定位 uvx 的
# —— 所以容器里这条路径天然成立,不需要额外配置 PATH。

FROM python:3.12-slim

# PYTHONUNBUFFERED:日志立刻出来。没有它,容器里 print 的内容会被缓冲,
#   出问题时 docker logs 看不到最后几行 —— 恰恰是最要紧的那几行。
# PYTHONDONTWRITEBYTECODE:别在只读层里写 .pyc
# UV_CACHE_DIR:uvx 第一次会去 PyPI 下 amap-mcp-server,缓存必须可写。
#   默认放在 ~/.cache/uv,而容器里 HOME 属于 root —— 所以显式挪到 /app 下。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_CACHE_DIR=/app/.uv-cache

# 国内构建默认用清华源;国外环境可以用 --build-arg 覆盖。
# 做成 ARG 而不是写死:这是个公开仓库,别人的网络环境未必在国内。
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

# ca-certificates:uvx 走 HTTPS 拉包要用
# curl:下面 HEALTHCHECK 用(不装的话健康检查直接失败)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# 先只拷依赖清单再装 —— 这样改业务代码不会让依赖层失效,
# 二次构建能直接命中缓存。
COPY requirements.txt ./
RUN pip install --index-url "${PIP_INDEX_URL}" -r requirements.txt

COPY . .

# 建非 root 用户,并把 /app 交给它。
#
# ⚠️ 为什么要顺手把 /data 也建好并 chown:
#   命名卷**第一次创建**时,Docker 会把镜像里该路径的内容与**属主**
#   复制进卷。所以镜像里 /data 属于 appuser,卷也就属于 appuser;
#   否则卷默认归 root,非 root 进程连 SQLite 文件都建不出来
#   —— 报错是 "unable to open database file",很容易误以为是路径写错。
#   绑定挂载(bind mount)不走这套逻辑,需要宿主机自己 chown。
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
 && mkdir -p /app/.uv-cache /data \
 && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8001

# start-period 给足:首次启动要初始化高德 MCP 工具发现(uvx 还要下载包)
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8001/health || exit 1

# ⚠️ --workers 必须是 1。
#   app/api/routes/knowledge.py 的 _TASKS 与 app/config.py 的
#   _rag_runtime_override 都是**进程内状态**:多 worker 会让前端轮询灌库进度
#   时落到不认识的进程上、RAG 开关各 worker 不一致。
#   并发靠 uvicorn 的线程池就够(生成行程那个端点刻意写成同步 def 走线程池)。
#   另:不要用 run.py 启动,它写死了 reload=True(只适合开发)。
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
