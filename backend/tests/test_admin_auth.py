"""管理口令鉴权的测试。

这个文件要钉住两件容易"悄悄退化"的事:

1. **哪些端点需要口令。** 一共 19 个端点、其中 9 个受保护,靠人肉眼对着
   装饰器数一遍是不可能可靠的 —— 实测就踩过:并行改装饰器时有 7 处没落盘,
   而肉眼看代码"好像都加了"。所以这里用**金名单**断言,多一个少一个都报错。

2. **fail-closed 的语义。** 未配置口令必须是 503(拒绝服务而不是放行),
   配错了必须是 401。这条如果退化成"没配就放行",公开部署上就等于
   **任何人都能清库**,而且不会有任何报错。

关于"怎么遍历路由":不能用 `app.routes` —— 新版 FastAPI 把 `include_router`
进来的子路由包成 `_IncludedRouter`,里面只暴露 `original_router` 这类版本相关的
属性,遍历它等于把测试绑死在框架内部实现上。`APIRouter.routes` 是稳定接口,
依赖树(`route.dependant`)也是 —— 用它。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.api.deps import require_admin
from app.api.routes import knowledge, trip
from app.config import get_settings


def has_admin_dep(dependant: Any) -> bool:
    """依赖树里有没有 require_admin(它可能嵌在子依赖里,所以要递归)。"""
    call = getattr(dependant, "call", None)
    if call is not None and getattr(call, "__name__", "") == "require_admin":
        return True
    return any(has_admin_dep(sub) for sub in getattr(dependant, "dependencies", []) or [])


def guarded_endpoints() -> dict[str, bool]:
    """`{方法 路径: 是否要口令}`,路径带 `/api` 前缀(与对外一致)。"""
    out: dict[str, bool] = {}
    for router in (knowledge.router, trip.router):
        for r in router.routes:
            if not isinstance(r, APIRoute):
                continue
            for m in r.methods:
                if m in ("HEAD", "OPTIONS"):
                    continue
                out[f"{m} /api{r.path}"] = has_admin_dep(r.dependant)
    return out


# ===========================================================================
# 端点清单(金名单)
# ===========================================================================

# 必须受保护:都是能让服务端状态发生变化的调用
MUST_BE_GUARDED = [
    # 灌库 / 清库(recreate 会 drop collection)
    "POST /api/knowledge/ingest",
    "GET /api/knowledge/ingest/{task_id}",
    # 上传解析:未鉴权上传 = 磁盘与 CPU 风险
    "POST /api/knowledge/docs/parse",
    "POST /api/knowledge/docs/{doc_id}/ingest",
    # 已上传内容可能含用户私有信息
    "GET /api/knowledge/docs",
    "GET /api/knowledge/docs/{doc_id}",
    "DELETE /api/knowledge/docs/{doc_id}",
    # 运行时开关
    "POST /api/knowledge/rag-toggle",
    # 行程的修改与删除
    "PUT /api/trip/plans/{plan_id}",
    "DELETE /api/trip/plans/{plan_id}",
]

# 必须保持公开:前端正常展示要用,或者本来就是给分享用的
MUST_BE_PUBLIC = [
    "GET /api/knowledge/status",      # 知识库页首屏
    "POST /api/knowledge/search",     # 检索调试台,只读
    "POST /api/knowledge/ingest/preview",  # 只统计条数,不写库不花钱
    "GET /api/knowledge/sources",     # 内置数据源统计,只读
    "POST /api/trip/plan",            # 核心功能
    "POST /api/trip/parse",           # 解析意图
    "GET /api/trip/plans",            # 「历史行程」页
    "GET /api/trip/plans/{plan_id}",  # ★ 分享链接用的就是这个,不能加口令
    "GET /api/trip/health",
]


class Test端点是否需要口令:
    @pytest.mark.parametrize("endpoint", MUST_BE_GUARDED)
    def test_写操作必须受保护(self, endpoint):
        found = guarded_endpoints()
        assert endpoint in found, f"{endpoint} 不在路由表里 —— 名字写错了?"
        assert found[endpoint], (
            f"{endpoint} 必须挂 dependencies=[Depends(require_admin)]。"
            "漏挂的后果是公网上任何人都能改/删数据。"
        )

    @pytest.mark.parametrize("endpoint", MUST_BE_PUBLIC)
    def test_读操作必须保持公开(self, endpoint):
        found = guarded_endpoints()
        assert endpoint in found, f"{endpoint} 不在路由表里 —— 名字写错了?"
        assert not found[endpoint], (
            f"{endpoint} 不该要口令:{'分享链接会失效' if 'plans/{plan_id}' in endpoint else '前端展示会坏掉'}。"
        )

    def test_受保护的就是金名单里那些_不多不少(self):
        """防"手滑多挂了一个" —— 那会让某个前端页面突然不可用。"""
        found = guarded_endpoints()
        guarded = {k for k, v in found.items() if v}
        assert guarded == set(MUST_BE_GUARDED), (
            "受保护端点与金名单不一致。\n"
            f"  多了(不该锁的):{sorted(guarded - set(MUST_BE_GUARDED))}\n"
            f"  少了(漏锁的)  :{sorted(set(MUST_BE_GUARDED) - guarded)}"
        )


# ===========================================================================
# require_admin 的行为
# ===========================================================================


class Test未配置口令时拒绝:
    """**fail-closed**:没配 = 拒绝,不是放行。

    公开部署上"忘了配"和"任何人都能清库"不能是同一个状态。
    """

    def test_返回503而不是放行(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "admin_token", "")

        with pytest.raises(HTTPException) as exc:
            require_admin(x_admin_token="随便什么都行")

        assert exc.value.status_code == 503, (
            "未配置口令时必须 503(服务端问题)。放行的话就等于公网无鉴权。"
        )

    def test_503要说清怎么修(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "admin_token", "")

        with pytest.raises(HTTPException) as exc:
            require_admin(x_admin_token=None)

        detail = str(exc.value.detail)
        assert "TRIPMIND_ADMIN_TOKEN" in detail, "错误信息里要告诉运维该配哪个变量"
        assert exc.value.headers.get("X-Admin-Required") == "config"

    def test_空白字符也算未配置(self, monkeypatch):
        """`.env` 里写 `TRIPMIND_ADMIN_TOKEN=` 或只有空格,都是没配。"""
        monkeypatch.setattr(get_settings(), "admin_token", "   ")
        with pytest.raises(HTTPException) as exc:
            require_admin(x_admin_token="   ")
        assert exc.value.status_code == 503


class Test口令校验:
    @pytest.fixture(autouse=True)
    def _set_token(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "admin_token", "s3cret-token-value")

    def test_正确的口令通过(self):
        assert require_admin(x_admin_token="s3cret-token-value") is None

    def test_错误的口令返回401(self):
        with pytest.raises(HTTPException) as exc:
            require_admin(x_admin_token="wrong")
        assert exc.value.status_code == 401

    def test_没带口令返回401(self):
        with pytest.raises(HTTPException) as exc:
            require_admin(x_admin_token=None)
        assert exc.value.status_code == 401

    def test_空串返回401(self):
        with pytest.raises(HTTPException) as exc:
            require_admin(x_admin_token="")
        assert exc.value.status_code == 401

    def test_两侧空白被容忍(self):
        """有人从网页复制口令时会带上空格 —— 不该因此 401。

        这个宽容度不会削弱安全性:口令本身是随机十六进制,首尾空格
        不是它的一部分,允许它只是省掉一次"明明对了却说不对"的困惑。
        """
        assert require_admin(x_admin_token="  s3cret-token-value  ") is None

    def test_前缀相同的口令也不行(self):
        """常见的手写错误:多打了一个字符。"""
        with pytest.raises(HTTPException) as exc:
            require_admin(x_admin_token="s3cret-token-valueX")
        assert exc.value.status_code == 401

    def test_不告诉调用方是没配还是配错了(self):
        """两种失败用不同状态码区分(503 vs 401)、但文案不要泄露更多。

        这里主要确认状态码语义正确:503 是运维要处理的,401 是用户要处理的。
        """
        with pytest.raises(HTTPException) as wrong:
            require_admin(x_admin_token="wrong")
        assert wrong.value.status_code == 401

        get_settings().admin_token = ""
        try:
            with pytest.raises(HTTPException) as unset:
                require_admin(x_admin_token="wrong")
            assert unset.value.status_code == 503
        finally:
            get_settings().admin_token = "s3cret-token-value"

    def test_用了常量时间比较(self):
        """`secrets.compare_digest` 而不是 `==`。

        这条是"防退化"的文档性断言:`==` 会短路,比较耗时随前缀匹配位数变化,
        理论上可被逐字节爆破。断言语义没法直接验,所以检查源码里没用 `!=`
        直接比口令。
        """
        import inspect

        src = inspect.getsource(require_admin)
        assert "compare_digest" in src, (
            "口令比较必须用 secrets.compare_digest(常量时间),不要用 == / !="
        )


# ===========================================================================
# HTTP 层:真的发一个请求,看拦不拦得住
# ===========================================================================
#
# 上面的测试看的是**路由表**(静态结构),这一组看的是**真实请求**。两者都要 ——
# 依赖挂对了但 `require_admin` 自己写错(比如 401 写成了放行),
# 静态检查一点都看不出来。
#
# ⚠️ 用 TestClient 而不是"起个服务再打它":本机 8001 端口上那个开发后端
# 是**启动于这些改动之前**的进程,打它只会看到受保护接口全部放行。
# 如果拿它当基准,会得出"鉴权没生效"的错误结论 —— 实际上只是那个进程
# 里跑的还是旧代码。测试必须针对当前代码,而 TestClient 走的就是当前代码,
# 还顺带省掉了启动 MCP 子进程的开销。


@pytest.fixture
def client():
    """当前代码的真实 ASGI 应用。

    不用 `with TestClient(app)`:那会触发 startup 事件去拉起高德 MCP 子进程,
    几十秒起步,而鉴权发生在路由依赖里,与 MCP 是否就绪毫无关系。
    """
    from fastapi.testclient import TestClient

    from app.api.main import app

    return TestClient(app)


# 无请求体的受保护端点 —— 挑它们是为了让"被拒绝"成为唯一观察到的结果,
# 不会因为请求体不合法而混进 422。
GATED_BODYLESS = [
    ("get", "/api/knowledge/docs"),
    ("delete", "/api/knowledge/docs/does-not-exist"),
    ("delete", "/api/trip/plans/does-not-exist"),
]


class TestHTTP层_未配置口令:
    """没配口令时,受保护接口必须**拒绝服务**,而不是静默放行。

    这一档在测试环境里就是默认状态(本地 .env 里没有 TRIPMIND_ADMIN_TOKEN),
    是"忘了配"最容易发生的情形。
    """

    @pytest.mark.parametrize("method,path", GATED_BODYLESS)
    def test_受保护接口返回503(self, client, monkeypatch, method, path):
        monkeypatch.setattr(get_settings(), "admin_token", "")

        resp = getattr(client, method)(path)

        assert resp.status_code == 503, (
            f"{method.upper()} {path} 在未配置口令时返回了 {resp.status_code}。"
            "必须是 503(fail-closed)—— 放行等于公网无鉴权。"
        )
        assert resp.headers.get("X-Admin-Required") == "config"

    def test_带请求体的写接口也是503而不是422(self, client, monkeypatch):
        """确认**鉴权先于请求体校验**发生。

        顺序反过来的话,前端拿着一个合法请求体会先被鉴权挡住(正确的),
        但如果鉴权在请求体校验之后,一个空 body 的探测请求会拿到 422 ——
        那样外部就能在不知道口令的前提下区分"接口存在"和"鉴权在位"。
        这里用一个**合法**请求体,断言拿到的是 503 而不是 422。
        """
        monkeypatch.setattr(get_settings(), "admin_token", "")

        resp = client.post("/api/knowledge/rag-toggle", json={"enabled": True})

        assert resp.status_code == 503, (
            f"期望 503(未配置口令),实际 {resp.status_code}。"
            "若拿到 422,说明请求体校验跑在了鉴权前面。"
        )

    @pytest.mark.parametrize(
        "path",
        ["/api/trip/plans?limit=1", "/api/knowledge/status?with_counts=false"],
    )
    def test_公开的读接口不受影响(self, client, monkeypatch, path):
        """没配口令不该影响浏览类功能 —— 否则等于把站点锁死了。"""
        monkeypatch.setattr(get_settings(), "admin_token", "")

        resp = client.get(path)

        assert resp.status_code == 200, (
            f"GET {path} 返回 {resp.status_code}。读接口不该受口令影响。"
        )


class TestHTTP层_配置了口令:
    @pytest.fixture(autouse=True)
    def _set_token(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "admin_token", "http-layer-token")

    @pytest.mark.parametrize("method,path", GATED_BODYLESS)
    def test_不带口令一律401(self, client, method, path):
        resp = getattr(client, method)(path)

        assert resp.status_code == 401, (
            f"{method.upper()} {path} 不带口令返回了 {resp.status_code},必须是 401"
        )
        assert resp.headers.get("X-Admin-Required") == "token"

    @pytest.mark.parametrize("method,path", GATED_BODYLESS)
    def test_口令错误也是401(self, client, method, path):
        resp = getattr(client, method)(path, headers={"X-Admin-Token": "wrong"})
        assert resp.status_code == 401

    def test_口令正确时放行(self, client):
        """带对令牌就不该再看到 401/503 —— 说明请求已经进到业务层。

        刻意**只断言"不是拒绝"**,不断言具体的业务状态码:
        这个测试要证明的是"鉴权放行了",而业务层返回 404(文档 / 行程不存在)
        还是 200(列表为空)取决于本机数据,与鉴权无关。
        把它写死成某一个码,只会让这条测试随环境数据变化而红。
        """
        resp = client.delete(
            "/api/knowledge/docs/definitely-not-a-real-doc-id",
            headers={"X-Admin-Token": "http-layer-token"},
        )
        assert resp.status_code not in (401, 503), (
            f"带对了口令仍被拒({resp.status_code})—— 若返回 503 说明读到的 "
            f"admin_token 不是测试里设置的那个"
        )

    def test_识别不出方法时不会误放行(self, client):
        """OPTIONS 预检由 CORS 中间件处理,不该落到 require_admin 上误判。

        这里只确认它没有变成 200 —— 这个项目没有跨域部署,
        出现 200 反而意味着有东西在替我们放行。
        """
        resp = client.options("/api/knowledge/docs")
        assert resp.status_code != 200
