"""路由层的统一错误边界 —— 唯一的"异常 → HTTP 响应"出口。

为什么要有这个模块
------------------
之前每个 handler 都自己抄一遍这个模板(7 处):

    try:
        ...
    except Exception as e:
        print(f"❌ XXX失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"XXX失败: {str(e)}")

它有两个问题,第二个比第一个严重得多:

1. **重复。** 7 份几乎一样的代码,改一处文案要改 7 个地方。
2. **把内部细节透给客户端。** `str(e)` 里常常带着文件绝对路径、
   依赖库的报错原文、SQL 片段甚至连接串。这些一旦回给浏览器,
   就等于把服务器内部结构交出去了 —— 而且这些东西对用户毫无用处,
   他既看不懂也做不了什么。

现在收在这一个文件里:完整堆栈留在服务端日志,**客户端只拿到一句
规范化的文案 + 一个错误编号**。用户把编号报过来,日志里一搜就能定位。

两个刻意的设计
--------------
- **`HTTPException` 原样放行。** 403/404/400 这些是"已经想清楚要回什么",
  不是意外。包装器碰到它们不再处理 —— 否则会把一个正确的 404 改成 500。
- **`DEBUG=true` 时才带上原始异常。** 本地调试需要看到真正的原因,
  生产环境不需要。用配置项控制比"改代码再上线"可靠。
"""

from __future__ import annotations

import functools
import inspect
import logging
import uuid
from typing import Any, Callable, TypeVar

from fastapi import HTTPException

from ..config import get_settings

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def to_http_error(action: str, exc: Exception) -> HTTPException:
    """把一个未预期的异常转成对外的 HTTPException。

    完整堆栈写进日志(带错误编号),返回给客户端的是规范化文案。

    Args:
        action: 人类可读的动作名,如"POI搜索"。会出现在文案与日志里。
        exc: 捕获到的异常。

    Returns:
        一个 500 的 HTTPException。**不返回、也不吞掉 `exc`** ——
        调用方应当 `raise ... from exc`,以便本地调试时仍能看到原始调用链。
    """
    # 已经决定好要回什么了,原样放行
    if isinstance(exc, HTTPException):
        return exc

    error_id = uuid.uuid4().hex[:8]
    # exc_info=True 会带上完整 traceback。错误编号是给日志用的关联键 ——
    # 没有它,用户报"保存失败"时只能靠时间戳在日志里翻。
    logger.exception("[%s] %s 失败: %s", error_id, action, exc)

    detail = f"{action}失败(错误编号 {error_id}),请稍后重试"
    # 本地调试时把真实原因也带上:开发阶段"请稍后重试"这种话只会浪费时间。
    if get_settings().debug:
        detail = f"{detail}｜原始错误: {type(exc).__name__}: {exc}"
    return HTTPException(status_code=500, detail=detail)


def handle_service_errors(action: str) -> Callable[[F], F]:
    """路由端点的错误边界装饰器。

    用在 `@router.*` **下方**(先被包装的应是函数本身):

        @router.get("/poi")
        @handle_service_errors("POI搜索")
        async def search_poi(...):
            ...

    行为:
    - 正常返回:原样透传
    - `HTTPException`:原样透传(403/404/400 等由业务自己决定的响应)
    - 其他异常:记完整堆栈 → 返回 500 + 规范化文案

    ⚠️ 装饰器**保持函数的同步/异步属性**。这不是细节:trip.py 里
    `plan_trip` 刻意写成同步 `def`,让 FastAPI 把它丢进线程池,
    否则一次几十秒的生成会把整个事件循环卡死。包装后如果变成
    async,那个保护就悄悄失效了。

    需要额外清理动作的端点(例如把库里那条 `running` 记录标成
    `error`)不必用这个装饰器 —— 自己 `except` 做清理,然后
    `raise to_http_error(...) from e` 即可,文案与日志仍然收在一处。
    """

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await func(*args, **kwargs)
                except HTTPException:
                    raise
                except Exception as exc:  # noqa: BLE001 —— 这就是本模块的职责
                    raise to_http_error(action, exc) from exc

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                raise to_http_error(action, exc) from exc

        return sync_wrapper  # type: ignore[return-value]

    return decorator  # type: ignore[return-value]
