"""LLM 客户端的**超时与重试**配置测试。

为什么单独建一个文件测这个:

这两件事都不是"代码写错了会编译不过"的类型,而是**配置错了也一切正常、
只在大请求上失败**。实测踩过的坑:

- 框架 `LLM_TIMEOUT` 默认 60 秒,而 planner 一步要生成 7500+ tokens、
  耗时 110 秒 → **2 日以上的行程永远失败**,而前 6 次调用日志全正常,
  极易误判成网络问题。
- OpenAI SDK 默认 `max_retries=2`,框架建客户端时没传这个参数,
  于是超时被**静默放大 3 倍**(180s → 540s),直接顶穿 nginx 的 330 秒。

第二件事尤其不能靠人记住:它藏在第三方库的默认值里,不写测试就会复发。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.observability import MeteredLLM

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_llm(**kwargs) -> MeteredLLM:
    """造一个不连任何真实服务的客户端实例。

    OpenAI 客户端构造时并不发请求,所以假 key/假地址完全够用 ——
    这里要验的是"建出来的客户端被配成什么样",不是它能不能连通。
    """
    opts = {
        "api_key": "fake-key-for-test",
        "base_url": "https://example.invalid/v1",
        "model": "fake-model",
    }
    opts.update(kwargs)
    return MeteredLLM(**opts)


# ===========================================================================
# 重试:必须关掉
# ===========================================================================


class Test自动重试被关掉:
    def test_max_retries必须是0(self):
        """SDK 默认是 2(共 3 次尝试)。开着的话超时会被放大 3 倍。

        这不是"优化"而是正确性问题:`LLM_TIMEOUT=180` 配 3 次尝试最坏
        540 秒,比 nginx 的 330 秒还长 —— 用户等不到前端的友好提示,
        只会收到一个 504,同时白占一个 worker 五分多钟。
        """
        llm = _make_llm()
        assert llm._client.max_retries == 0, (
            "MeteredLLM 必须关掉 SDK 自动重试。若这条失败,说明 "
            "metering.py 里的 _create_client() 覆写被删了。"
        )

    def test_是覆写而不是直接改父类(self):
        """父类自己建的客户端仍是 SDK 默认值 —— 本项目用的是子类。

        这条是"防呆":如果哪天有人把 _create_client 的覆写挪到调用方去
        (比如在 llm_service 里改),这里会提醒他父类没被改到。
        """
        from hello_agents import HelloAgentsLLM

        parent = HelloAgentsLLM(
            api_key="fake-key-for-test",
            base_url="https://example.invalid/v1",
            model="fake-model",
        )
        assert parent._client.max_retries != 0, (
            "父类行为变了?若 SDK 默认值不再是 2,上面的断言与注释都要跟着改。"
        )


# ===========================================================================
# 超时:来自环境变量,且合理
# ===========================================================================


class TestLLM超时读取:
    def test_跟随LLM_TIMEOUT环境变量(self, monkeypatch):
        monkeypatch.setenv("LLM_TIMEOUT", "123")
        assert _make_llm().timeout == 123

    def test_没有环境变量时用框架默认值(self, monkeypatch):
        """记录框架默认值是 60 —— 这个值对本项目**太小**。

        所以测试不是"断言默认值够用",而是把这个事实固定下来:
        一旦有人以为不配也没关系,这条测试会指出 60 秒的来源。
        """
        monkeypatch.delenv("LLM_TIMEOUT", raising=False)
        assert _make_llm().timeout == 60

    def test_项目配置不能小于60秒(self):
        """下限保护:低于 120 秒连"调 7 次 LLM"里的任何一次都撑不住。"""
        found = _llm_timeout_templates()
        if not found:  # pragma: no cover
            pytest.skip("没有 .env.example")
        for name, value in found.items():
            assert value >= 120, (
                f"{name} 的 LLM_TIMEOUT={value} 太小。实测 planner 一步就要 "
                "110 秒(输出 7500+ tokens),2 日行程整体约 160 秒。"
            )

    def test_两份模板的LLM超时一致(self):
        found = _llm_timeout_templates()
        if len(found) < 2:  # pragma: no cover
            pytest.skip("只有一份模板")
        assert len(set(found.values())) == 1, (
            f"两份 .env.example 的 LLM_TIMEOUT 不一致:{found}。"
            "只改一份,另一份会在某个部署方式下把 60 秒带回来。"
        )


# ===========================================================================
# 三层超时链路的顺序 —— LLM < axios < nginx
# ===========================================================================


def _llm_timeout_templates() -> dict[str, int]:
    """读出所有 `.env.example` 里的 LLM_TIMEOUT。

    仓库里有两份模板(根目录给 docker compose、backend/ 给单独跑后端),
    两边都得对 —— 只改一份的话,另一份的 60 秒会在某天被某个部署方式用上。
    """
    found: dict[str, int] = {}
    for rel in (".env.example", "backend/.env.example"):
        p = REPO_ROOT / rel
        if not p.exists():
            continue
        m = re.search(r"^LLM_TIMEOUT=(\d+)", p.read_text(encoding="utf-8"), re.M)
        if m:
            found[rel] = int(m.group(1))
    return found


def _read_llm_timeout() -> int:
    found = _llm_timeout_templates()
    assert found, "两份 .env.example 里都没找到 LLM_TIMEOUT"
    values = set(found.values())
    assert len(values) == 1, f"两份模板的 LLM_TIMEOUT 不一致:{found}"
    return values.pop()


def _read_axios_timeout() -> int:
    """返回**秒**。注意 api.ts 里写的是毫秒,这里统一换算。"""
    text = (REPO_ROOT / "frontend" / "src" / "services" / "api.ts").read_text(encoding="utf-8")
    m = re.search(r"const TRIP_PLAN_TIMEOUT_MS\s*=\s*(\d+)", text)
    assert m, "api.ts 里找不到 TRIP_PLAN_TIMEOUT_MS"
    ms = int(m.group(1))
    assert ms % 1000 == 0, f"TRIP_PLAN_TIMEOUT_MS={ms} 不是整秒,三处对齐时容易算错"
    return ms // 1000


def _read_nginx_timeout() -> int:
    """返回**秒**。"""
    text = (REPO_ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    m = re.search(r"proxy_read_timeout\s+(\d+)s", text)
    assert m, "nginx.conf 里找不到 proxy_read_timeout"
    return int(m.group(1))


class Test三层超时链路有序:
    """三个数字躺在三个不同文件里,靠人肉同步迟早会漂移。

    顺序必须是 `LLM < axios < nginx`,否则会出现最难查的那种症状:
    nginx 先抛 504,前端根本没机会给出可读提示 —— 用户看到"生成失败",
    日志里却是超时,很难联想到是 nginx。
    """

    def test_LLM超时小于前端axios(self):
        assert _read_llm_timeout() < _read_axios_timeout(), (
            "单次 LLM 调用超时应当小于前端 axios 超时 —— 让后端先失败并返回"
            "结构化错误,而不是前端先放弃、后端还在跑。"
        )

    def test_前端axios小于nginx(self):
        assert _read_axios_timeout() < _read_nginx_timeout(), (
            "axios 必须小于 nginx:要的是前端先超时并给出可读文案,"
            "而不是把 nginx 的 504 甩给用户。"
        )

    def test_nginx要留出余量(self):
        """nginx 至少比 axios 多 30 秒 —— 否则两者会"赛跑",谁先到不确定。"""
        gap = _read_nginx_timeout() - _read_axios_timeout()
        assert gap >= 30, (
            f"nginx({_read_nginx_timeout()}s) 与 axios({_read_axios_timeout()}s) "
            f"只差 {gap}s,余量太小,容易变成谁先到谁说了算。"
        )

    def test_重试次数与最坏耗时匹配(self):
        """把"重试放大"这条约束也算进来 —— 它是最容易被忘掉的一环。

        生成一次行程是 4 次**串行**的长 LLM 调用(三个 agent + planner)。
        若开着 SDK 自动重试,最坏耗时是 `LLM_TIMEOUT × 尝试次数 × 4`,
        很容易就顶穿 nginx,表现是"用户等不到前端的友好提示,只见 504"。

        当前设计是 `max_retries=0`,即每次只尝试一次,最坏
        `180 × 1 × 4 = 720s` —— 这已经**超过** nginx 的 330s 了,但那没关系,
        因为"4 次全部超时"意味着服务整体不可用,此时报 504 是正确结果。
        真正要拦住的是"**单次**调用因重试而超过 nginx",那才是误导用户的形态。
        """
        attempts = 1 + _make_llm()._client.max_retries
        single_call_worst = _read_llm_timeout() * attempts

        assert single_call_worst < _read_nginx_timeout(), (
            f"单次调用最坏耗时 {single_call_worst}s"
            f"(LLM_TIMEOUT={_read_llm_timeout()} × {attempts} 次尝试)"
            f" 已超过 nginx 的 {_read_nginx_timeout()}s —— 用户会先收到 504 "
            "而不是前端的可读提示。要么关掉重试,要么提高 nginx。"
        )
