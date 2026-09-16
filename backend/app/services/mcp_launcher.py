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
"""

from __future__ import annotations

import sys
from pathlib import Path

# uv 上的包名。uvx 会自己拉取并运行它,所以这里写错的话服务起不来,
# 而且同样是"工具数 0 但不报错"——见 tests/test_mcp_launcher.py 里的断言。
AMAP_MCP_PACKAGE = "amap-mcp-server"


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
