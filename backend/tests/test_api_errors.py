"""errors.py 的单元测试 —— 路由层的统一错误边界

这里要钉住的是**行为契约**,尤其是两条容易被"顺手改坏"的:

1. **敏感信息不出门。** 以前 7 处 handler 都写
   `detail=f"... {str(e)}"`,会把文件路径、库的报错原文回给客户端。
   现在的契约是:客户端只拿到规范化文案 + 错误编号,完整堆栈只在日志里。

2. **HTTPException 原样放行。** 包装器如果把一个正确的 404 改成 500,
   前端"行程不存在"的分支就永远走不到了 —— 而那个分支是产品功能。

还有一条不那么显眼但很关键:`plan_trip` 刻意写成同步 `def`,
好让 FastAPI 把它丢进线程池(否则一次几十秒的生成会卡死整个事件循环)。
装饰器必须**保持函数的同步/异步属性**,否则那个保护会悄悄失效。
"""

from __future__ import annotations

import inspect
import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.errors import handle_service_errors, to_http_error
from app.config import get_settings

# 只在测试里出现的"内部细节" —— 它绝不该出现在响应体里
SECRET = "/srv/app/backend/.env 里的 AMAP_API_KEY 是 abc123"


# ===========================================================================
# to_http_error
# ===========================================================================


class TestToHttpError:
    def test_返回500(self):
        err = to_http_error("搜索POI", RuntimeError("boom"))
        assert isinstance(err, HTTPException)
        assert err.status_code == 500

    def test_不泄漏原始异常内容(self):
        err = to_http_error("搜索POI", RuntimeError(SECRET))
        assert SECRET not in str(err.detail)
        assert "abc123" not in str(err.detail)
        assert "/srv/app" not in str(err.detail)

    def test_带上错误编号(self):
        """用户报"保存失败"时,编号是在日志里定位那次错误的唯一线索。"""
        err = to_http_error("搜索POI", RuntimeError("boom"))
        assert "错误编号" in err.detail
        # 编号是 8 位十六进制
        import re

        assert re.search(r"[0-9a-f]{8}", err.detail)

    def test_编号每次都不同(self):
        ids = {
            str(to_http_error("x", RuntimeError("y")).detail) for _ in range(20)
        }
        assert len(ids) == 20

    def test_保留动作名(self):
        """文案要能说清"什么失败了",否则用户只知道出错了。"""
        err = to_http_error("删除行程", RuntimeError("boom"))
        assert "删除行程失败" in err.detail

    def test_HTTPException原样放行(self):
        """404/403/400 是已经决定好的响应,不能被改成 500。"""
        original = HTTPException(status_code=404, detail="行程不存在或已被删除")
        assert to_http_error("取行程", original) is original

    def test_日志里有完整堆栈(self, caplog):
        """客户端看不到细节,但服务端必须看得到 —— 否则就只是"藏起来"了。"""
        with caplog.at_level(logging.ERROR, logger="app.api.errors"):
            try:
                raise ValueError(SECRET)
            except ValueError as exc:
                to_http_error("搜索POI", exc)

        assert caplog.records, "没有写出任何日志"
        record = caplog.records[-1]
        assert record.exc_info is not None, "没带 traceback"
        assert SECRET in record.getMessage(), "日志里也应该有原因"

    def test_DEBUG时带上原因(self, monkeypatch):
        """本地调试需要真实原因;"请稍后重试"在开发时只会浪费时间。"""
        monkeypatch.setattr(get_settings(), "debug", True)
        err = to_http_error("搜索POI", RuntimeError("boom"))
        assert "boom" in err.detail
        assert "ValueError" in err.detail or "RuntimeError" in err.detail

    def test_非DEBUG时不带原因(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "debug", False)
        err = to_http_error("搜索POI", RuntimeError("boom"))
        assert "boom" not in err.detail


# ===========================================================================
# 装饰器:同步/异步属性必须保持
# ===========================================================================


class TestKeepsSyncness:
    def test_同步函数包装后仍是同步(self):
        """**这条对应 trip.py 的线程池保护。**

        `plan_trip` 刻意写成同步 def,让 FastAPI 把它丢进线程池 ——
        否则一次几十秒的 4 次 LLM 调用会把整个事件循环卡死,
        期间 /health、历史列表全部无响应。包装后变成 async 的话,
        这个保护就没了,而且**不会有任何报错**。
        """

        @handle_service_errors("x")
        def sync_endpoint():
            return 1

        assert not inspect.iscoroutinefunction(sync_endpoint)

    def test_异步函数包装后仍是异步(self):

        @handle_service_errors("x")
        async def async_endpoint():
            return 1

        assert inspect.iscoroutinefunction(async_endpoint)

    def test_签名与文档被保留(self):
        """FastAPI 靠签名解析参数,靠 __name__ 生成 operationId。"""

        @handle_service_errors("x")
        def endpoint(keywords: str, city: str = "北京"):
            """文档字符串。"""
            return {}

        sig = inspect.signature(endpoint)
        assert list(sig.parameters) == ["keywords", "city"]
        assert sig.parameters["city"].default == "北京"
        assert endpoint.__name__ == "endpoint"
        assert endpoint.__doc__ == "文档字符串。"


# ===========================================================================
# 装饰器:端到端行为(用真实的 FastAPI 应用跑一遍)
# ===========================================================================


@pytest.fixture()
def client():
    app = FastAPI()

    @app.get("/ok")
    @handle_service_errors("正常接口")
    async def ok():
        return {"success": True}

    @app.get("/boom")
    @handle_service_errors("查询数据")
    async def boom():
        raise RuntimeError(SECRET)

    @app.get("/missing")
    @handle_service_errors("取行程")
    async def missing():
        raise HTTPException(status_code=404, detail="行程不存在或已被删除")

    @app.get("/bad-request")
    @handle_service_errors("解析意图")
    async def bad_request():
        raise HTTPException(status_code=400, detail="text 不能为空")

    @app.get("/sync-boom")
    @handle_service_errors("同步接口")
    def sync_boom():
        raise RuntimeError("同步炸了")

    return TestClient(app, raise_server_exceptions=False)


class TestDecoratorEndToEnd:
    def test_正常响应原样透传(self, client):
        resp = client.get("/ok")
        assert resp.status_code == 200
        assert resp.json() == {"success": True}

    def test_未预期异常变500(self, client):
        resp = client.get("/boom")
        assert resp.status_code == 500
        assert "查询数据失败" in resp.json()["detail"]

    def test_响应体里没有内部细节(self, client):
        body = client.get("/boom").text
        assert "abc123" not in body
        assert "/srv/app" not in body
        assert "AMAP_API_KEY" not in body

    def test_404不被改成500(self, client):
        """**这条要是坏了,前端「行程不存在」的分支就永远走不到。**"""
        resp = client.get("/missing")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "行程不存在或已被删除"

    def test_400不被改成500(self, client):
        resp = client.get("/bad-request")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "text 不能为空"

    def test_同步端点也能被包装(self, client):
        resp = client.get("/sync-boom")
        assert resp.status_code == 500
        assert "同步接口失败" in resp.json()["detail"]


# ===========================================================================
# 真实应用:路由注册没被装饰器弄坏
# ===========================================================================


class TestRealAppRoutes:
    """装饰器改动了一批端点的定义方式,这里确认路由表没变。

    ⚠️ 不能用 `app.routes` 取:新版 FastAPI 把 `include_router` 进来的
    子路由包成 `_IncludedRouter` 对象,`app.routes` 里看不到具体的 path。
    用 `app.openapi()` —— 它拿到的是真正对外暴露的接口清单,
    与 `/docs` 上看到的完全一致,比翻内部结构更贴近"用户能不能访问到"。
    """

    @pytest.fixture(scope="class")
    def paths(self):
        from app.api.main import app

        return app.openapi()["paths"]

    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/api/trip/plan"),
            ("get", "/api/trip/health"),
            ("post", "/api/trip/parse"),
            ("get", "/api/trip/plans"),
            ("get", "/api/trip/plans/{plan_id}"),
            ("put", "/api/trip/plans/{plan_id}"),
            ("delete", "/api/trip/plans/{plan_id}"),
            ("get", "/api/map/poi"),
            ("get", "/api/map/weather"),
            ("post", "/api/map/route"),
            ("get", "/api/map/health"),
            ("get", "/api/poi/search"),
            ("get", "/api/poi/photo"),
            ("get", "/api/poi/detail/{poi_id}"),
        ],
    )
    def test_端点还在(self, paths, method, path):
        assert path in paths, f"{path} 从接口清单里消失了"
        assert method in paths[path], f"{path} 不再接受 {method.upper()}"

    def test_plan端点仍然是同步函数(self):
        """**这是线程池保护的最后一道哨。**

        `plan_trip` 一旦变成 async,FastAPI 会在事件循环里直接跑它,
        几十秒内整个服务无响应 —— 而接口照样能用,只有并发时才看得出来。
        """
        from app.api.routes import trip as trip_routes

        endpoint = next(r.endpoint for r in trip_routes.router.routes if r.path == "/trip/plan")
        assert not inspect.iscoroutinefunction(endpoint), (
            "plan_trip 变成了 async —— 它会阻塞整个事件循环,"
            "请去掉 async 或改回同步 def"
        )

    def test_map与poi端点都套上了错误边界(self):
        """漏一个,那个端点就又开始把 `str(e)` 回给客户端。

        逐个查 `__wrapped__`(functools.wraps 留下的标记)。

        刻意排除:
        - `/map/health`:它失败时要回 503(服务不可用),不是统一的 500
        - trip 的端点:它们自己 raise HTTPException(400/404),
          或者需要额外的清理动作(plan_trip 要把 running 记录标成 error)
        """
        from app.api.routes import map as map_routes
        from app.api.routes import poi as poi_routes

        unwrapped: list[str] = []
        for module in (map_routes, poi_routes):
            for route in module.router.routes:
                if route.path == "/map/health":
                    continue
                if not hasattr(route.endpoint, "__wrapped__"):
                    unwrapped.append(route.path)

        assert not unwrapped, f"这些端点没有套错误边界:{unwrapped}"
