"""resolve_uvx_command() —— 用绝对路径找 uvx,绕开 PATH

**这组测试在保护什么**

`MCPTool` 用 subprocess 起 `uvx amap-mcp-server`,而裸写的 `"uvx"` 要靠 PATH
解析。venv 没激活 → `backend/venv/Scripts` 不在 PATH 上 → 高德工具从 **16 个
变成 0 个**,但服务照常启动、接口照常返回 200、**不报任何错**,agent 转而
编造景点和坐标。

`resolve_uvx_command()` 改用 `sys.executable` 定位 uvx(它必定和解释器同目录,
因为 `uv` 是 requirements.txt 的依赖),从此不依赖 PATH。

下面把这些行为钉住:谁要是把它改回裸命令、或者路径推导写错一层,这里会红。
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from app.services.mcp_launcher import AMAP_MCP_PACKAGE, resolve_uvx_command


def _fake_interpreter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把 sys.executable 指向一个假目录,并返回那个目录。"""
    fake_python = tmp_path / "python.exe"
    fake_python.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(fake_python))
    return tmp_path


class Test找得到时:
    def test_Windows布局_解释器旁边有_uvx_exe(self, monkeypatch, tmp_path):
        exe_dir = _fake_interpreter(monkeypatch, tmp_path)
        (exe_dir / "uvx.exe").write_bytes(b"")

        cmd = resolve_uvx_command()

        assert cmd == [str(exe_dir / "uvx.exe"), AMAP_MCP_PACKAGE]

    def test_Linux布局_解释器旁边是无后缀的_uvx(self, monkeypatch, tmp_path):
        exe_dir = _fake_interpreter(monkeypatch, tmp_path)
        (exe_dir / "uvx").write_bytes(b"")

        cmd = resolve_uvx_command()

        assert cmd == [str(exe_dir / "uvx"), AMAP_MCP_PACKAGE]

    def test_返回的是绝对路径而不是裸命令(self, monkeypatch, tmp_path):
        """这条是整个修复的**要害** —— 裸命令会退回去查 PATH,等于没改。"""
        exe_dir = _fake_interpreter(monkeypatch, tmp_path)
        (exe_dir / "uvx.exe").write_bytes(b"")

        cmd = resolve_uvx_command()

        assert Path(cmd[0]).is_absolute(), f"{cmd[0]} 不是绝对路径,PATH 又成了依赖"
        assert Path(cmd[0]).parent == exe_dir, "uvx 必须从解释器所在目录取"


class Test找不到时:
    def test_退回裸命令而不是抛异常(self, monkeypatch, tmp_path):
        """兜底要和改动前的行为**完全一致** —— 宁可退回旧行为,也不要更差。"""
        _fake_interpreter(monkeypatch, tmp_path)  # 目录是空的

        cmd = resolve_uvx_command()

        assert cmd == ["uvx", AMAP_MCP_PACKAGE]

    def test_解释器路径本身有问题也不抛(self, monkeypatch, tmp_path):
        """sys.executable 理论上总存在,但冻结环境/嵌入式解释器下可能是空串。

        chdir 到空目录是必须的:``Path("").parent`` 是**当前工作目录**,
        不换目录的话这条测试会意外依赖"backend/ 下恰好没有 uvx"。
        """
        monkeypatch.setattr(sys, "executable", "")
        monkeypatch.chdir(tmp_path)

        cmd = resolve_uvx_command()

        assert cmd == ["uvx", AMAP_MCP_PACKAGE]


class Test包名:
    def test_包名是_amap_mcp_server(self):
        """包名写错 = 服务起不来 = 工具数 0 且不报错,和原来的坑一模一样。"""
        assert AMAP_MCP_PACKAGE == "amap-mcp-server"

    def test_命令只有两项(self, monkeypatch, tmp_path):
        exe_dir = _fake_interpreter(monkeypatch, tmp_path)
        (exe_dir / "uvx.exe").write_bytes(b"")

        assert len(resolve_uvx_command()) == 2


class Test当前环境:
    """不 mock,直接问**这个 venv** 里到底有没有 uvx。

    这条和上面几条性质不同:上面在测逻辑,这条在测**环境**。
    它失败说明依赖没装全 —— 那正是"工具数静默变 0"的前兆,
    所以要在这里就红,而不是等到跑出编造的行程才发现。
    """

    def test_当前解释器旁边有_uvx(self):
        exe_dir = Path(sys.executable).parent
        found = [n for n in ("uvx.exe", "uvx") if (exe_dir / n).exists()]
        assert found, (
            f"{exe_dir} 下没有 uvx —— 高德 MCP 工具会静默变成 0 个。"
            f" 跑 `{sys.executable} -m pip install -r requirements.txt` 装上 uv"
        )
