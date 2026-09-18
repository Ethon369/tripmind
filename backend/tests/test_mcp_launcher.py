"""resolve_uvx_command() —— 用绝对路径找 uvx,绕开 PATH

**这组测试在保护什么**

`MCPTool` 用 subprocess 起 `uvx amap-mcp-server`,而裸写的 `"uvx"` 要靠 PATH
解析。venv 没激活 → `backend/venv/Scripts` 不在 PATH 上 → 高德工具从 **16 个
变成 0 个**,但服务照常启动、接口照常返回 200、**不报任何错**,agent 转而
编造景点和坐标。

`resolve_uvx_command()` 改用 `sys.executable` 定位 uvx(它必定和解释器同目录,
因为 `uv` 是 requirements.txt 的依赖),从此不依赖 PATH。

`build_mcp_env()` 管的是**另一个**静默故障:子进程环境变量。框架的
`_merge_env()` 从空字典开始拼、不继承 `os.environ`,所以容器里设好的
`UV_INDEX_URL` 传不到 uvx 手上,它会退回官方源(国内极慢)。

下面把这些行为钉住:谁要是把它改回裸命令、路径推导写错一层,或者
把环境变量又收窄回去,这里会红。
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from app.services.mcp_launcher import (
    AMAP_MCP_PACKAGE,
    build_mcp_env,
    resolve_uvx_command,
)


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


class Test传给子进程的环境变量:
    """build_mcp_env() —— 子进程环境**不继承** os.environ,必须显式传。

    在阿里云 ECS 上踩到的真实故障:容器里 `UV_INDEX_URL` 设成了国内镜像,
    但 `MCPTool._merge_env()` 是从空字典开始拼的,uvx 子进程拿不到它,
    于是退回 `files.pythonhosted.org` 逐个拉 wheel。国内每个请求几秒,
    几十个包要好几分钟,而日志里只有一行 `🔗 连接到 MCP 服务器...` ——
    完全像卡死。下面几条钉住"该传的要传、不该传的不传"。
    """

    def test_高德Key一定在里面(self, monkeypatch):
        monkeypatch.delenv("AMAP_MAPS_API_KEY", raising=False)

        env = build_mcp_env("fake-key")

        assert env["AMAP_MAPS_API_KEY"] == "fake-key"

    def test_显式传入的Key覆盖父进程里的同名值(self, monkeypatch):
        """父进程环境里可能有个旧的 AMAP_MAPS_API_KEY,不能让它赢。"""
        monkeypatch.setenv("AMAP_MAPS_API_KEY", "旧值")

        env = build_mcp_env("新值")

        assert env["AMAP_MAPS_API_KEY"] == "新值"

    def test_UV开头的变量全部透传(self, monkeypatch):
        """UV_* 是整个修复的要害 —— 少了 UV_INDEX_URL 就退回慢源。"""
        monkeypatch.setenv("UV_INDEX_URL", "https://pypi.tuna.tsinghua.edu.cn/simple")
        monkeypatch.setenv("UV_DEFAULT_INDEX", "https://pypi.tuna.tsinghua.edu.cn/simple")
        monkeypatch.setenv("UV_CACHE_DIR", "/app/.uv-cache")

        env = build_mcp_env("k")

        assert env["UV_INDEX_URL"] == "https://pypi.tuna.tsinghua.edu.cn/simple"
        assert env["UV_DEFAULT_INDEX"] == "https://pypi.tuna.tsinghua.edu.cn/simple"
        assert env["UV_CACHE_DIR"] == "/app/.uv-cache"

    def test_将来新增的UV配置项也能透传(self, monkeypatch):
        """用前缀而不是写死清单:uv 的配置项还在增加。"""
        monkeypatch.setenv("UV_PYTHON_INSTALL_MIRROR", "https://example.com/python")

        env = build_mcp_env("k")

        assert env["UV_PYTHON_INSTALL_MIRROR"] == "https://example.com/python"

    @pytest.mark.parametrize(
        "name",
        ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
         "http_proxy", "https_proxy", "no_proxy", "all_proxy"],
    )
    def test_代理变量透传(self, monkeypatch, name):
        """需要代理才能出网的环境(企业网络/部分云主机)少了它就是连不上。

        小写那几个在 Linux 上是**独立的**变量,不能只收大写形式。

        ⚠️ 所以断言不能直接 ``env[name]``:Windows 的 `os.environ` 本身
        大小写不敏感,会把键统一转成大写,小写形式读出来是大写。
        这不是缺陷(Windows 上大写一样生效),只是断言得忽略大小写。
        """
        monkeypatch.setenv(name, "http://proxy.internal:3128")

        env = build_mcp_env("k")

        matched = [k for k in env if k.upper() == name.upper()]
        assert matched, f"{name} 没传给子进程 —— 有代理才能出网的环境会直接连不上"
        assert env[matched[0]] == "http://proxy.internal:3128"

    @pytest.mark.parametrize(
        "name",
        ["LLM_API_KEY", "EMBED_API_KEY", "UNSPLASH_SECRET_KEY",
         "TRIPMIND_DB", "RUNS_DIR", "PATH"],
    )
    def test_无关的敏感变量不传过去(self, monkeypatch, name):
        """子进程是第三方包,不该顺带拿到 LLM Key、数据库路径这些。

        PATH 也在列表里:它是"没有前缀也没有被显式列入"的对照组 ——
        顺带说明这个函数**不是**把 os.environ 整个倒过去。
        """
        monkeypatch.setenv(name, "敏感值")

        env = build_mcp_env("k")

        assert name not in env

    def test_返回值全是字符串(self, monkeypatch):
        """subprocess 的 env 只能是 str→str,混进非字符串会直接起不来。"""
        monkeypatch.setenv("UV_SOMETHING", "1")

        env = build_mcp_env("")

        assert all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
