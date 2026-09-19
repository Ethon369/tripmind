"""API 层的鉴权依赖:身份解析、登录校验、角色校验。

这一层为什么从"共享口令"换成了"账号体系"
------------------------------------------
原来只有一件事要挡住 —— **不能让人在公网上随手把数据删了**。那时项目
没有用户体系(行程靠链接分享是产品设定),所以一个共享口令就够,而且
它在注释里写清了自己的弱小之处:无法按人吊销、泄密只能全员重录。

现在出现了一个共享口令**表达不了**的需求:

    「普通用户只能看自己的历史行程,管理员能看所有人的」

这句话里有一个"谁"字。共享口令没有主体,服务端根本不知道请求是谁发的,
所以无法回答"这条行程是不是他的"。于是必须引入账号。

四个组件的分工
--------------
    services/security.py    密码怎么哈希、令牌怎么生成(纯函数,好测)
    store/user_store.py     账号与会话的持久化
    api/deps.py(**本文件**) 把"请求头里的令牌"翻译成"当前账号",再判角色
    api/routes/auth.py      登录/登出/改密/账号管理的 HTTP 接口

三层依赖的形状
--------------
    get_current_user     可选。拿得到账号就给,拿不到给 None,**从不抛**
    require_user         必须有账号,否则 401
    require_admin_role   必须账号 + role == 'admin',否则 403

为什么 `get_current_user` 不抛异常
----------------------------------
因为**同一个接口在不同登录状态下要给出不同结果**,而不是拦掉。最典型的是
`GET /api/trip/plans/{id}`(行程详情):它是分享链接的数据源,必须允许匿名打开;
但如果请求者正好是管理员,响应里应当多带一个"这条行程属于谁"的字段。
如果把鉴权写成"检查失败就 401",这两个分支就没法共存 —— 只能在路由里
再解析一次令牌,等于把解析逻辑抄了第二遍。

401 与 403 的边界
-----------------
    401  没登录 / 令牌过期 / 令牌无效 —— 客户端该做的是**去登录**
    403  登录了,但这个角色不允许做这件事 —— 再登录一次也没用

这个区分对前端是必需的:401 要清掉本地令牌并跳登录页;403 只能弹一句
"没有权限"。混成一个码会让前端在权限不足时把用户踹回登录页,而他刚登录完。
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException

from ..store import get_user_store
from ..store.user_store import ROLE_ADMIN

# 认证失败的响应头。`WWW-Authenticate` 是 HTTP 规定的 401 配套头 ——
# 它让"未认证"这个状态机器可读(浏览器、调试面板、网关都会看它),
# 比只在 detail 里写一句中文更有意义。
_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def parse_bearer(authorization: str | None) -> str | None:
    """从 `Authorization` 头里取出 Bearer 令牌。

    大小写不敏感地匹配 `Bearer` 前缀 —— RFC 7235 规定 scheme 是大小写
    不敏感的,某些客户端会发 `bearer`。为这一个字符的差异回 401 不值得。

    不属于 `Bearer` 的 scheme(比如 `Basic`)一律当成"没带令牌":
    本项目不支持别的 scheme,把它当成解析失败去报 400 只会多一个分支。
    """
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def get_current_user(authorization: str | None = Header(default=None)) -> dict[str, Any] | None:
    """解析当前账号。**不抛异常** —— 未登录返回 None。理由见模块文档。"""
    token = parse_bearer(authorization)
    if token is None:
        return None
    # resolve_session 内部会把过期/已停用/已删除的会话顺手清掉,
    # 所以"账号被停用"能在下一个请求就失效,不用等令牌自然过期。
    return get_user_store().resolve_session(token)


def get_current_token(authorization: str | None = Header(default=None)) -> str | None:
    """当前请求带来的令牌原文。**只有登出/改密这种"要吊销自己"的端点需要它**。

    其余端点一律用 `get_current_user()`。多一层"能拿到令牌原文"的依赖是有
    代价的:它容易被误用到不该用的地方(比如回显给客户端)。所以这里把用途
    写死在名字和文档里,并且它自己不做任何校验。
    """
    return parse_bearer(authorization)


def require_user(user: dict[str, Any] | None = Depends(get_current_user)) -> dict[str, Any]:
    """必须有账号。作为依赖挂在端点上::

        @router.get("/plans", dependencies=[Depends(require_user)])

    或者需要用到账号本身时直接声明参数::

        def list_plans(user: dict = Depends(require_user)): ...
    """
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="请先登录。",
            headers=_BEARER_CHALLENGE,
        )
    return user


def require_admin_role(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """必须是管理员账号。

    依赖 `require_user` 而不是 `get_current_user`:能登进来但角色不够时
    必须是 **401(没登录)→ 403(角色不够)** 这两步,而不是合并成一步 403。
    否则未登录的用户访问管理员接口会拿到 403,前端会显示"没有权限" ——
    而他其实只是需要登录。
    """
    if user.get("role") != ROLE_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="该操作需要管理员权限。",
        )
    return user


def is_admin(user: dict[str, Any] | None) -> bool:
    """给"可选区分角色"的分支用(如行程详情里的归属字段)。"""
    return bool(user) and user.get("role") == ROLE_ADMIN


def can_access_plan(user: dict[str, Any] | None, plan: dict[str, Any]) -> bool:
    """这个账号能不能改写/删除这条行程。

    规则:**本人的,或管理员的**。

    无主行程(`user_id IS NULL`,账号体系上线前留下的那些)**只有管理员能动**。
    这是刻意的:如果判据写成"plan.user_id == user.id or plan.user_id is None",
    那么任何登录用户都能改写那批老数据,而它们恰恰是最早、最真实的那批记录。
    与其猜"它应该算谁的",不如显式要求管理员介入。
    """
    if not user:
        return False
    if is_admin(user):
        return True
    owner = plan.get("user_id")
    return bool(owner) and owner == user.get("id")


__all__ = [
    "parse_bearer",
    "get_current_user",
    "get_current_token",
    "require_user",
    "require_admin_role",
    "is_admin",
    "can_access_plan",
]
