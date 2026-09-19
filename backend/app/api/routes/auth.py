"""账号与登录的 HTTP 接口。

| 接口 | 权限 | 说明 |
|---|---|---|
| POST   /auth/login            | 公开   | 换取令牌 |
| POST   /auth/register         | 公开   | 自助注册,**只能是普通用户**;成功即登录 |
| POST   /auth/logout           | 登录   | 吊销**当前这一个**会话 |
| GET    /auth/me               | 登录   | 当前账号(前端刷新时用它恢复登录态) |
| POST   /auth/password         | 登录   | 改自己的密码,踢掉其它会话后换发新令牌 |
| GET    /auth/users            | 管理员 | 账号列表 |
| POST   /auth/users            | 管理员 | 新建账号(**唯一能建管理员的入口**) |
| PATCH  /auth/users/{id}       | 管理员 | 改密码 / 改角色 / 停用 / 改显示名 |
| DELETE /auth/users/{id}       | 管理员 | 删账号(行程保留为无主) |

**两个"公开"接口的差别**:`/login` 与 `/register` 都不需要登录,
但它们的威胁模型不一样 —— 前者防"撞已有账号的密码"(按用户名 + IP 节流),
后者防"同一来源批量建号"(只按 IP 节流)。所以它们各有独立的计数器。

关于"登录接口是公开的"这件事
----------------------------
它必须公开 —— 没有它谁也进不来。但它同时是**唯一一个"未登录也能消耗服务端
资源"的接口**(每次失败都要跑一次 600k 轮 pbkdf2,约 0.3 秒 CPU),
所以它是这个项目里最需要节流的地方。限流见 `services/login_guard.py`。

关于错误信息
------------
登录失败一律返回**同一句话**("用户名或密码错误"),不区分:
  · 用户不存在
  · 密码错误
  · 账号被停用
区分它们等于对外提供一个**账号枚举接口** —— 攻击者可以先确认哪些用户名
存在,再只针对这些账号撞密码。代价是"账号被停用"的用户需要管理员告知,
这个代价可以接受。

唯一例外是**节流**(429):它必须说出来,否则用户只会看到"密码错误"
却怎么也登不进去 —— 那种困惑比泄露"有人在爆破"更糟。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...config import get_settings
from ...services.login_guard import get_login_guard, get_register_guard
from ...store import get_user_store
from ...store.user_store import (
    ROLE_ADMIN,
    ROLE_USER,
    DuplicateUsername,
    ProtectedAccount,
    UserError,
)
from ..deps import get_current_token, get_current_user, require_admin_role, require_user
from ..errors import handle_service_errors

router = APIRouter(prefix="/auth", tags=["账号"])

# 登录失败统一文案。**不要**在这里加"或账号已停用"这类补充 ——
# 那句话本身就是账号枚举的答案。
_BAD_CREDENTIALS = "用户名或密码错误"


# ===========================================================================
# 响应 / 请求模型
# ===========================================================================


class UserInfo(BaseModel):
    id: str
    username: str
    role: str = Field(description="admin | user")
    display_name: Optional[str] = None
    is_active: bool = True
    created_at: str
    last_login_at: Optional[str] = None
    # 只出现在列表里(账号管理页要显示"这个人有多少行程")
    plan_count: Optional[int] = None


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    """自助注册的入参。

    ⚠️ **刻意没有 `role` 字段。** 不是"忘了加" —— 见 `register()` 里的说明:
    一旦角色能从客户端传进来,管理员权限就成了一个注册参数。
    """

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)
    display_name: Optional[str] = Field(default=None, max_length=64)


class LoginResponse(BaseModel):
    success: bool = True
    message: str = "登录成功"
    token: str = Field(description="后续请求放在 Authorization: Bearer <token>")
    expires_at: str
    user: UserInfo


class MeResponse(BaseModel):
    success: bool = True
    user: UserInfo


class SimpleOk(BaseModel):
    success: bool = True
    message: str = ""


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)
    role: str = Field(default=ROLE_USER, description="admin | user")
    display_name: Optional[str] = Field(default=None, max_length=64)


class UpdateUserRequest(BaseModel):
    """改账号。**所有字段都是可选的** —— 传哪几个就改哪几个。

    这样一个 PATCH 既能"只停用"、也能"只改密码",不需要为每种操作
    各开一个端点。代价是"传了 null"和"没传"必须区分开,而 pydantic 的
    `Optional[...] = None` 恰好把两者混在一起了 —— 所以下面用
    显式的哨兵判断(`model_fields_set`),见 `update_user_endpoint`。
    """

    password: Optional[str] = Field(default=None, min_length=1, max_length=256)
    role: Optional[str] = None
    is_active: Optional[bool] = None
    display_name: Optional[str] = Field(default=None, max_length=64)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=1, max_length=256)


class ChangePasswordResponse(BaseModel):
    success: bool = True
    message: str = ""
    # 改密码会吊销全部会话,所以必须**换发一个新令牌**给当前这个客户端,
    # 否则用户改完密码自己就被登出了。前端拿到新令牌直接覆盖本地那份。
    token: str = ""
    expires_at: str = ""


class UserListResponse(BaseModel):
    success: bool = True
    users: list[UserInfo] = []
    summary: dict[str, Any] = {}


# ===========================================================================
# 工具
# ===========================================================================


def _to_user_info(user: dict[str, Any]) -> UserInfo:
    return UserInfo(**user)


def _client_ip(request: Request) -> str:
    """取来源 IP。

    ⚠️ **不看 `X-Forwarded-For`**:这个头是客户端可以随便写的,
    拿它做限流的键等于把限流交给攻击者控制(每次请求换一个假 IP,
    限流直接失效)。生产部署里后端只绑宿主机回环、由 nginx 转发
    (见 docs/DEPLOYMENT.md),所以这里拿到的一直是 nginx 的地址 ——
    也就是说**限流的 IP 维度在生产上退化成"同一个 nginx"**,
    粒度只到"整个部署"。

    这是有意的取舍:宁可限流偏严(所有用户共享一个 IP 键),
    也不要一个可以被绕过的键。真正的每用户 IP 限流应该在 nginx 层用
    `limit_req` 做 —— 那一层能看到真实连接地址。账号维度(下面的
    `user:` 键)不受这个影响,它才是防"盯着一个账号爆破"的主力。
    """
    return request.client.host if request.client else "unknown"


def _login_keys(username: str, ip: str) -> tuple[str, str]:
    return f"user:{(username or '').strip().lower()}", f"ip:{ip}"


# ===========================================================================
# 1. 登录 / 登出 / 当前账号
# ===========================================================================


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="登录",
    description="用账号密码换取一个 Bearer 令牌。连续失败会被节流(429)。",
)
def login(body: LoginRequest, request: Request):
    guard = get_login_guard()
    store = get_user_store()
    keys = _login_keys(body.username, _client_ip(request))

    # 节流检查放在**查库之前**:它挡的正是"大量无效请求"这件事,
    # 放在后面就还得先付出一次数据库查询的代价。
    wait = guard.retry_after(*keys)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=f"登录失败次数过多,请 {wait} 秒后重试。",
            headers={"Retry-After": str(wait)},
        )

    user = store.authenticate(body.username, body.password)
    if user is None:
        guard.record_failure(*keys)
        # 顺手清理过期记录。放在失败路径而不是成功路径上:失败是高频事件,
        # 清理机会更多;而成功路径越短越好。
        guard.prune()
        raise HTTPException(status_code=401, detail=_BAD_CREDENTIALS)

    # 用户名维度清零。IP 维度**不清**(理由见 login_guard.record_success)。
    guard.record_success(keys[0])

    session = store.create_session(user["id"])
    store.touch_login(user["id"])
    user["last_login_at"] = user.get("last_login_at")

    return LoginResponse(
        token=session["token"],
        expires_at=session["expires_at"],
        user=_to_user_info(user),
    )


@router.post(
    "/register",
    response_model=LoginResponse,
    status_code=201,
    summary="注册",
    description=(
        "自助注册一个**普通用户**账号,成功后直接返回登录令牌(不用再登一次)。"
        "可用 `TRIPMIND_ALLOW_REGISTRATION=false` 关闭。"
    ),
)
def register(body: RegisterRequest, request: Request):
    """注册。

    三条写死在代码里的规矩(都不是配置项):

    1. **角色固定是 `user`。** 请求体里没有 `role`,这里也不读它的任何变体。
       管理员只能由已有管理员在「账号管理」里创建 —— 这是整套 RBAC 的地基,
       一旦注册能选角色,权限模型就没有意义了。
    2. **注册即登录**(直接返回令牌)。让用户注册完再手打一次刚设的密码,
       是把一个纯损失步骤塞给他。
    3. **只按 IP 节流,不按用户名。** 按用户名节流在这里没有意义 ——
       注册的失败原因是"用户名被占用",而那个限制的公开性和"用户是否存在"
       是一回事(注册接口本来就会告诉你重名)。真正要防的是**同一个来源批量建号**,
       那只有 IP 维度能挡。
    """
    settings = get_settings()
    if not settings.allow_registration:
        raise HTTPException(
            status_code=403,
            detail="本站已关闭自助注册。如需账号请联系管理员开通。",
        )

    guard = get_register_guard()
    ip = _client_ip(request)
    key = f"register:{ip}"

    wait = guard.retry_after(key)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=f"注册请求过于频繁,请 {wait} 秒后重试。",
            headers={"Retry-After": str(wait)},
        )

    store = get_user_store()
    try:
        user = store.create_user(
            body.username,
            body.password,
            role=ROLE_USER,  # ← 写死。见 docstring 第 1 条。
            display_name=body.display_name,
        )
    except DuplicateUsername as exc:
        # 409 而不是 400:请求本身没问题,是"此刻的状态不允许"。
        # 前端据此把错误挂在用户名这一项上,而不是笼统的"提交失败"。
        guard.record_failure(key)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UserError as exc:
        # 用户名/密码不合法**不算**节流里的失败 —— 那是用户在改自己的输入,
        # 不是攻击行为。把它计进去会让"输错两次用户名"的人被锁十分钟。
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session = store.create_session(user["id"])
    store.touch_login(user["id"])
    guard.prune()
    return LoginResponse(
        message="注册成功",
        token=session["token"],
        expires_at=session["expires_at"],
        user=_to_user_info(user),
    )


@router.post(
    "/logout",
    response_model=SimpleOk,
    summary="登出",
    description="吊销当前这一个会话。其它设备上的登录不受影响。",
)
def logout(token: str | None = Depends(get_current_token)):
    # 幂等:令牌已经无效时也返回成功。登出失败没有可执行的后续动作,
    # 报错只会让前端在"已经登出了"的情况下卡住。
    if token:
        get_user_store().delete_session(token)
    return SimpleOk(message="已登出")


@router.get(
    "/me",
    response_model=MeResponse,
    summary="当前账号",
    description="前端在页面刷新时用它恢复登录态、并判断要不要显示管理员入口。",
)
def me(user: dict[str, Any] = Depends(require_user)):
    return MeResponse(user=_to_user_info(user))


@router.post(
    "/password",
    response_model=ChangePasswordResponse,
    summary="修改自己的密码",
    description=(
        "需要提供原密码。成功后**吊销该账号的全部会话**并换发一个新令牌 —— "
        "改密码的动机通常是「怀疑别人知道我的密码」,旧会话若还能用,这个动作等于没做。"
    ),
)
def change_password(
    body: ChangePasswordRequest,
    token: str | None = Depends(get_current_token),
    user: dict[str, Any] = Depends(require_user),
):
    store = get_user_store()

    # 必须验原密码。否则一个被盗用的令牌就能永久接管账号 ——
    # 那就把"令牌泄露"升级成了"账号丢失"。
    if store.authenticate(user["username"], body.old_password) is None:
        raise HTTPException(status_code=401, detail="原密码不正确")

    try:
        store.update_user(user["id"], password=body.new_password)
    except UserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # update_user 已经吊销了全部会话,这里给当前客户端补发一个,
    # 让"改完密码继续用"成为可能 —— 否则用户改完自己的密码就被踢去登录页,
    # 会被理解成"改密码把账号弄坏了"。
    session = store.create_session(user["id"])
    return ChangePasswordResponse(
        message="密码已修改,其它设备上的登录已失效",
        token=session["token"],
        expires_at=session["expires_at"],
    )


# ===========================================================================
# 2. 账号管理(仅管理员)
# ===========================================================================
#
# 为什么这些接口要管理员而不是"本人也能改":
# 改自己的密码走上面的 /auth/password(需要原密码);而角色、启用状态、
# 别人的账号 —— 这些都是**管理动作**,没有"本人操作"的语义。


@router.get(
    "/users",
    response_model=UserListResponse,
    dependencies=[Depends(require_admin_role)],
    summary="账号列表",
)
def list_users():
    store = get_user_store()
    users = [_to_user_info(u) for u in store.list_users()]
    return UserListResponse(
        users=users,
        summary={
            "total": len(users),
            "admins": sum(1 for u in users if u.role == ROLE_ADMIN),
            "active": sum(1 for u in users if u.is_active),
            "disabled": sum(1 for u in users if not u.is_active),
        },
    )


@router.post(
    "/users",
    response_model=UserInfo,
    status_code=201,
    dependencies=[Depends(require_admin_role)],
    summary="新建账号",
)
def create_user(body: CreateUserRequest):
    try:
        return _to_user_info(
            get_user_store().create_user(
                body.username,
                body.password,
                role=body.role,
                display_name=body.display_name,
            )
        )
    except DuplicateUsername as exc:
        # 409 而不是 400:请求本身没问题,是"此刻的状态不允许"。
        # 前端据此可以把错误挂在"用户名"这一项上,而不是笼统的"提交失败"。
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch(
    "/users/{user_id}",
    response_model=UserInfo,
    dependencies=[Depends(require_admin_role)],
    summary="修改账号(密码/角色/启用状态/显示名)",
)
def update_user_endpoint(user_id: str, body: UpdateUserRequest):
    store = get_user_store()
    if store.get_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    # 用 model_fields_set 区分"没传"和"传了 null":
    #   {"display_name": null}  → 显式清空显示名
    #   {}                      → 不动显示名
    # 直接用 `body.display_name is not None` 判断的话,第一种情况会被当成
    # "没传",于是**清空显示名的操作会静默失败** —— 界面上点了保存却什么都没变。
    provided = body.model_fields_set
    kwargs: dict[str, Any] = {}
    if "password" in provided and body.password:
        kwargs["password"] = body.password
    if "role" in provided and body.role is not None:
        kwargs["role"] = body.role
    if "is_active" in provided and body.is_active is not None:
        kwargs["is_active"] = body.is_active
    if "display_name" in provided:
        kwargs["display_name"] = body.display_name or ""

    try:
        updated = store.update_user(user_id, **kwargs)
    except ProtectedAccount as exc:
        # 400 而不是 403:请求者**确实**是管理员,被拒的原因是
        # "这个操作会让系统失去最后一个管理员",属于请求内容不合法。
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if updated is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _to_user_info(updated)


@router.delete(
    "/users/{user_id}",
    response_model=SimpleOk,
    dependencies=[Depends(require_admin_role)],
    summary="删除账号",
    description="该账号名下的行程**不会被删除**,只变成无主(仅管理员可见)。",
)
def delete_user(user_id: str, me: dict[str, Any] = Depends(get_current_user)):
    store = get_user_store()
    if store.get_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    # 不许删自己。技术上删得掉(有另一个管理员时),但结果是管理员
    # 删完自己就掉线了,而界面上那个"删除"按钮通常不会提醒这一点。
    # 与其依赖用户想清楚,不如直接挡掉 —— 想删自己的账号,先用另一个
    # 管理员账号操作。
    if me and me.get("id") == user_id:
        raise HTTPException(
            status_code=400,
            detail="不能删除当前登录的账号。请用另一个管理员账号操作。",
        )

    try:
        store.delete_user(user_id)
    except ProtectedAccount as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SimpleOk(message="已删除该账号,其行程已转为无主(仅管理员可见)")


@router.post(
    "/users/{user_id}/sessions/revoke",
    response_model=SimpleOk,
    dependencies=[Depends(require_admin_role)],
    summary="强制该账号下线",
    description="吊销某账号的全部会话,但保留账号本身。用于「怀疑某人的令牌泄露了」。",

)
@handle_service_errors("吊销会话")
def revoke_user_sessions(user_id: str):
    store = get_user_store()
    if store.get_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    n = store.delete_sessions_for_user(user_id)
    return SimpleOk(message=f"已吊销 {n} 个会话")
