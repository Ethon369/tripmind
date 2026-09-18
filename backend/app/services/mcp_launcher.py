"""启动高德 MCP 服务的命令 —— 用绝对路径找 uvx,不依赖 PATH

**它解决什么问题**

`MCPTool` 是靠 subprocess 起 `uvx amap-mcp-server` 的,而 `"uvx"` 是**裸命令**,
要靠 PATH 解析。venv 没激活时 `backend/venv/Scripts` 不在 PATH 上,于是:

    高德工具从 16 个 → 0 个,但服务照常启动、接口照常返回 200、**不报任何错**,
    agent 转而编造景点和坐标 —— 而且从外部完全看不出来。

**为什么用绝对路径就够**

`uv` 是 `requirements.txt` 里的依赖,所以 `uvx.exe` 必定和当前 Python 解释器
在同一个目录里。用 `sys.executable` 定位它,就**完全绕开了 PATH** ——
根因消失,不需要"记得先 activate"。

`scripts/recorder.py` 里已经防过同一个坑(`if shutil.which("uvx") is None`),
但那只能在**已经出问题**时报警;这里是从根上不让它发生。

**兜底**

万一解释器旁边真的没有 uvx(系统级 Python、或依赖没装全),退回裸 `"uvx"`
让 PATH 去找 —— 行为和改动前完全一致,**不会比原来更差**。

---

`build_mcp_env()` 解决的是**另一件事**:子进程的环境变量从哪儿来。

`MCPTool` 的 `_merge_env()` 是**从空字典开始拼**的(`result_env = {}`),
它**不继承 `os.environ`** —— 只放自动映射表、`env_keys`,以及我们显式传进去的
`env`。所以:

    容器里 UV_INDEX_URL 设得再对,uvx 子进程也拿不到。

漏掉 UV_* 的症状极具迷惑性:uvx 退回官方源,从 `files.pythonhosted.org`
逐个拉 wheel,国内每个请求要几秒、几十个包要好几分钟。这段时间日志里只有一行

    🔗 连接到 MCP 服务器...

**看起来完全像卡死**,而实际在下包。所以这里主动把 uv 的配置传下去。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# uv 上的包名。uvx 会自己拉取并运行它,所以这里写错的话服务起不来,
# 而且同样是"工具数 0 但不报错"——见 tests/test_mcp_launcher.py 里的断言。
AMAP_MCP_PACKAGE = "amap-mcp-server"

# 要从父进程**继承**给 MCP 子进程的环境变量。
#
# UV_*  :uv 自己的配置(包源 UV_INDEX_URL / UV_DEFAULT_INDEX、缓存目录
#        UV_CACHE_DIR、Python 镜像……)。uvx 定位并下载 amap-mcp-server
#        全靠这几个;漏掉就退回官方源。
# *_PROXY / NO_PROXY:需要代理才能出网的环境(企业网络、部分云主机),
#        少了它 uvx 直接连不上,同样表现成"卡在连接 MCP 服务器"。
#
# 用前缀匹配而不是逐个列举:uv 的配置项还在增加,写死清单迟早会漏。
ENV_PREFIX_PASSTHROUGH = ("UV_",)

ENV_EXACT_PASSTHROUGH = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
)


def build_mcp_env(amap_api_key: str) -> dict[str, str]:
    """拼出传给 amap-mcp-server 子进程的环境变量。

    **只放必需的**,不整个 `os.environ` 倒过去:子进程是第三方包,
    不该顺带拿到 LLM Key、数据库路径这些和它无关的东西。

    Args:
        amap_api_key: 高德 Web 服务 Key,amap-mcp-server 靠它调高德接口。

    Returns:
        可直接作为 ``MCPTool(env=...)`` 的字典。
    """
    env: dict[str, str] = {
        key: value
        for key, value in os.environ.items()
        if key in ENV_EXACT_PASSTHROUGH or key.startswith(ENV_PREFIX_PASSTHROUGH)
    }
    # 显式赋值放最后:即使父进程环境里碰巧有个同名的旧值,也以传入的为准。
    env["AMAP_MAPS_API_KEY"] = amap_api_key
    return env


def resolve_uvx_command() -> list[str]:
    """拼出启动高德 MCP 服务的命令,用**绝对路径**找 uvx。

    这样写之后,``python run.py`` 不管有没有先 activate venv 都能跑通 ——
    而在此之前,"忘了 activate"会让高德工具静默消失。

    Returns:
        形如 ``["C:/.../venv/Scripts/uvx.exe", "amap-mcp-server"]``。
        解释器旁边找不到时退回 ``["uvx", "amap-mcp-server"]``(走 PATH)。
    """
    exe_dir = Path(sys.executable).parent
    # Windows 上是 uvx.exe,Linux/macOS 上是 uvx。两个名字都试,
    # 不判平台 —— 判平台要多一个分支,而多试一个文件名是免费的。
    for name in ("uvx.exe", "uvx"):
        candidate = exe_dir / name
        if candidate.exists():
            return [str(candidate), AMAP_MCP_PACKAGE]
    return ["uvx", AMAP_MCP_PACKAGE]
