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
#
# ⚠️ UV_INDEX_URL / UV_DEFAULT_INDEX 是**必须的**,不是优化项。
#   默认源把 wheel 放在 files.pythonhosted.org,国内是"能连上但极慢":
#   实测每个 .metadata 请求要几秒,几十个包装完要好几分钟,而这段时间
#   日志里只有一行 `🔗 连接到 MCP 服务器...`,看起来完全像卡死。
#   跟上面的 PIP_INDEX_URL 同理,做成 ARG 让国外环境能覆盖回官方源。
#   两个名字都设:UV_INDEX_URL 是旧名(仍生效),UV_DEFAULT_INDEX 是新名。
ARG UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_CACHE_DIR=/app/.uv-cache \
    UV_INDEX_URL=${UV_INDEX_URL} \
    UV_DEFAULT_INDEX=${UV_INDEX_URL}

# 国内构建默认用清华源;国外环境可以用 --build-arg 覆盖。
# 做成 ARG 而不是写死:这是个公开仓库,别人的网络环境未必在国内。
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

# ⚠️ 这里**刻意没有 apt-get**。原来装的是 ca-certificates + curl:
#    前者给 uvx 走 HTTPS 拉包用,后者给 HEALTHCHECK 用。
#
#    但在阿里云 ECS 上实测:这层会**卡死**。python:3.12-slim 用的
#    Debian 官方源 deb.debian.org 在国内经常"能连上但下不动",
#    而 apt-get 默认**没有超时** —— 一挂就是好几分钟,日志里
#    停在 `Get:4 ... [9678 kB]` 再也不动,看起来像构建卡了。
#
#    两件事其实都不需要它:
#      - python:slim 基础镜像**自带** /etc/ssl/certs/ca-certificates.crt(实测有)
#      - 健康检查改用 python 自己发请求,不必装 curl
#
#    少一层、少一个国外依赖 ⇒ 构建更稳也更快。
#
#    如果将来换了基础镜像导致证书缺失,症状是 uvx 拉包报 SSL 错误 ——
#    那时再加回 apt,并把 Debian 源换成国内镜像(而不是用默认的)。

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
#
# 用 python 发请求而不是 curl:上面刻意不装 apt,而 python 本来就在镜像里。
# urlopen 遇到非 2xx 会抛异常 → 退出码非 0 → 健康检查失败。
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=4)"

# ⚠️ --workers 必须是 1。
#   app/api/routes/knowledge.py 的 _TASKS 与 app/config.py 的
#   _rag_runtime_override 都是**进程内状态**:多 worker 会让前端轮询灌库进度
#   时落到不认识的进程上、RAG 开关各 worker 不一致。
#   并发靠 uvicorn 的线程池就够(生成行程那个端点刻意写成同步 def 走线程池)。
#   另:不要用 run.py 启动,它写死了 reload=True(只适合开发)。
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
