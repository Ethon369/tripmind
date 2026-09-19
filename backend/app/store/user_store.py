"""账号与会话的持久化。

为什么单独一个 store 文件而不是塞进 `plan_store.py`
--------------------------------------------------
两者的生命周期完全不同:行程是"生成一次、读很多次、偶尔删",而账号是
"很少变、每个请求都要读一次做鉴权"。混在一个类里会让 `PlanStore` 变成
一个既管行程又管账号的大对象,而它已经被 3 个测试文件引用了。

设计上的三个决定
----------------
1. **对外只吐不含 `password_hash` 的字典。**
   所有返回账号的方法都过一遍 `_public_user()`,而不是让调用方自己记得
   剔除那个字段。后者迟早会漏 —— 而漏的那次是把密码哈希发给了前端。

2. **会话表存令牌摘要,不存原文。** 见 `security.fingerprint()`。

3. **删除账号不连带删行程。**
   把 `plans.user_id` 置空(变成"无主"),而不是删掉那些行程。理由是
   行程里带着真实的 LLM 成本记录,而成本统计是历史页顶部的聚合数字 ——
   删账号顺手把成本记录抹掉,会让"这个月花了多少钱"变成一个会随时间
   缩水的数。这也与 `delete_plan()` 的语义保持一致:只有**明确**要求删
   才删。
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from ..services.security import (
    fingerprint,
    hash_password,
    new_session_token,
    password_policy_error,
    verify_password,
)
from .db import connect, init_db

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_USER, ROLE_ADMIN)

# 登录名白名单。**限 ASCII 是刻意为之**,和 users 表上 `COLLATE NOCASE`
# 的唯一索引配套 —— SQLite 的 NOCASE 只折叠 ASCII 大小写,如果允许中文或
# 别的字符,"唯一"的判定就会和用户以为的不一致(出现"已存在"却搜不到)。
# 允许 `.` `_` `-` 是为了 `zhang.san` 这类习惯写法。
_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")

# 会话"最近活跃"的写入节流窗口(秒)。
# 每个请求都 UPDATE 一次 last_seen_at 会让每次鉴权都变成一次写事务;
# 而它只是个诊断字段,精度到分钟足够。
_TOUCH_INTERVAL_SEC = 60


class UserError(Exception):
    """账号操作的**可展示**错误。路由层据此回 400/409,而不是 500。"""


class DuplicateUsername(UserError):
    """登录名已被占用。"""


class ProtectedAccount(UserError):
    """这个账号不允许被这样操作(自删、删掉最后一个管理员)。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse(ts: str) -> datetime:
    """解析库里存的时间串。

    存的是 `isoformat(timespec="seconds")`,没有时区后缀 —— 全项目统一用
    本地时间(与 plan_store 一致)。解析失败时返回一个"远古时间",
    让过期的会话被当成过期、而不是让请求 500。
    """
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return datetime(1970, 1, 1)


def _public_user(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """转成对外结构。**密码哈希在这里被丢掉** —— 这是唯一的出口。"""
    keys = row.keys() if isinstance(row, sqlite3.Row) else row
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "display_name": row["display_name"],
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_login_at": row["last_login_at"] if "last_login_at" in keys else None,
    }


def normalize_username(username: str) -> str:
    """去空白。大小写**保留原样**(显示用),唯一性由库上的 NOCASE 索引保证。"""
    return (username or "").strip()


def validate_username(username: str) -> str | None:
    """返回错误说明;通过时返回 None。"""
    if not username:
        return "用户名不能为空"
    if not _USERNAME_RE.match(username):
        return "用户名需为 3-32 位,以字母或数字开头,只能包含字母、数字、下划线、点和短横线"
    return None


class UserStore:
    """账号与会话的增删改查。同步阻塞 —— 与 `PlanStore` 同样的约定。"""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        init_db(db_path)

    # ------------------------------------------------------------------
    # 账号:读
    # ------------------------------------------------------------------

    def get_by_id(self, user_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _public_user(row) if row else None

    def get_by_username(self, username: str) -> dict[str, Any] | None:
        """按登录名查。用 `COLLATE NOCASE` 与唯一索引的排序规则对齐。"""
        name = normalize_username(username)
        if not name:
            return None
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (name,)
            ).fetchone()
        return _public_user(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        """全部账号,管理员在前、再按创建时间。"""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM users
                ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END,
                         created_at ASC
                """
            ).fetchall()
        # 列表额外带上行程数与当前会话数 —— 账号管理页要显示,
        # 否则管理员删账号前完全不知道那个人有多少数据。
        plan_counts = self._plan_counts()
        out = []
        for row in rows:
            item = _public_user(row)
            item["plan_count"] = plan_counts.get(row["id"], 0)
            out.append(item)
        return out

    def count_users(self) -> int:
        with connect(self.db_path) as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])

    def count_admins(self, *, include_inactive: bool = True) -> int:
        sql = "SELECT COUNT(*) AS n FROM users WHERE role = ?"
        if not include_inactive:
            sql += " AND is_active = 1"
        with connect(self.db_path) as conn:
            return int(conn.execute(sql, (ROLE_ADMIN,)).fetchone()["n"])

    def _plan_counts(self) -> dict[str, int]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT user_id, COUNT(*) AS n FROM plans "
                "WHERE user_id IS NOT NULL GROUP BY user_id"
            ).fetchall()
        return {row["user_id"]: int(row["n"]) for row in rows}

    # ------------------------------------------------------------------
    # 账号:写
    # ------------------------------------------------------------------

    def create_user(
        self,
        username: str,
        password: str,
        *,
        role: str = ROLE_USER,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """建账号。校验不通过或重名时抛 `UserError` 子类。

        与 `PlanStore` 一致的风格:校验放在 store 而不是路由 —— 因为
        "什么样的账号名合法"是数据层的约束,放到路由会让 CLI 脚本
        (`scripts/manage_users.py`)绕开它。
        """
        from ..config import get_settings

        name = normalize_username(username)
        if (err := validate_username(name)):
            raise UserError(err)
        if role not in ROLES:
            raise UserError(f"角色必须是 {' 或 '.join(ROLES)}")
        if (err := password_policy_error(
            password,
            min_length=int(get_settings().password_min_length),
            username=name,
        )):
            raise UserError(err)

        user_id = uuid.uuid4().hex
        now = _now()
        try:
            with connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO users
                        (id, username, password_hash, role, display_name,
                         is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        user_id,
                        name,
                        hash_password(password),
                        role,
                        (display_name or "").strip() or None,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            # 唯一索引拦下来的重名。**靠库的约束而不是先 SELECT 再 INSERT**
            # —— 后者在并发下会漏(两个请求同时查到"没有",然后都插进去)。
            raise DuplicateUsername(f"用户名「{name}」已被占用") from exc

        created = self.get_by_id(user_id)
        assert created is not None  # 刚插进去的,不可能查不到
        return created

    def update_user(
        self,
        user_id: str,
        *,
        password: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
        display_name: str | None = None,
    ) -> dict[str, Any] | None:
        """改账号。只改传入的字段。账号不存在返回 None。

        两条守卫,都在这里(而不是路由)实现,因为"最后一个管理员不能降级"
        是数据一致性约束:

        1. **不能把最后一个管理员降级成普通用户或停用** ——
           那会让整个系统再也进不去账号管理页,且只能靠改库恢复。
        2. **改密码会吊销该账号的所有会话。** 这是有意的:改密码的常见
           动机是"怀疑别人知道我的密码",如果旧会话还能用,这个动作等于没做。
           代价是被改的人(包括管理员自己)需要重新登录,这是对的。
        """
        current = self.get_by_id(user_id)
        if current is None:
            return None

        sets: list[str] = []
        params: list[Any] = []

        if password is not None:
            from ..config import get_settings

            if (err := password_policy_error(
                password,
                min_length=int(get_settings().password_min_length),
                username=current["username"],
            )):
                raise UserError(err)
            sets.append("password_hash = ?")
            params.append(hash_password(password))

        if role is not None:
            if role not in ROLES:
                raise UserError(f"角色必须是 {' 或 '.join(ROLES)}")
            if role != ROLE_ADMIN and self._is_last_active_admin(user_id):
                raise ProtectedAccount(
                    "这是最后一个启用中的管理员账号,不能降级。"
                    "请先创建另一个管理员账号。"
                )
            sets.append("role = ?")
            params.append(role)

        if is_active is not None:
            if not is_active and self._is_last_active_admin(user_id):
                raise ProtectedAccount(
                    "这是最后一个启用中的管理员账号,不能停用。"
                    "请先创建另一个管理员账号。"
                )
            sets.append("is_active = ?")
            params.append(1 if is_active else 0)

        if display_name is not None:
            sets.append("display_name = ?")
            params.append((display_name or "").strip() or None)

        if sets:
            sets.append("updated_at = ?")
            params.append(_now())
            params.append(user_id)
            with connect(self.db_path) as conn:
                conn.execute(
                    f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params
                )

        if password is not None:
            self.delete_sessions_for_user(user_id)
        elif is_active is False:
            # 停用也要踢掉现有会话。`resolve_session()` 里也检查 is_active,
            # 所以这不是唯一防线 —— 但主动清理能让"停用"立刻腾出会话表,
            # 而且不依赖"那个请求恰好在检查时读过 is_active"。
            self.delete_sessions_for_user(user_id)

        return self.get_by_id(user_id)

    def delete_user(self, user_id: str) -> bool:
        """删账号。行程**保留**但变成无主(见模块文档)。"""
        current = self.get_by_id(user_id)
        if current is None:
            return False
        if self._is_last_active_admin(user_id):
            raise ProtectedAccount(
                "这是最后一个启用中的管理员账号,不能删除。请先创建另一个管理员账号。"
            )

        with connect(self.db_path) as conn:
            # 先解绑行程,再删账号。顺序反过来的话中间会存在一个
            # "plans.user_id 指向不存在的账号"的瞬间 —— 本项目的读取路径
            # 用的是 LEFT JOIN,不会崩,但没必要留下这种中间态。
            conn.execute("UPDATE plans SET user_id = NULL WHERE user_id = ?", (user_id,))
            # sessions 有 ON DELETE CASCADE,删 users 时会一起走
            # (connect() 里开了 PRAGMA foreign_keys=ON)。
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return True

    def touch_login(self, user_id: str) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), user_id)
            )

    def _is_last_active_admin(self, user_id: str) -> bool:
        """这个账号是不是"唯一还能用的管理员"。

        判据是"删除/降级它之后,还剩几个**启用中的**管理员"。
        只数 role='admin' 不够 —— 一个被停用的管理员登不进来,
        拿它当"还有别的管理员"是错的。
        """
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT role, is_active FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None or row["role"] != ROLE_ADMIN or not row["is_active"]:
            # 它本来就不是"启用中的管理员",谈不上"最后一个"
            return False
        return self.count_admins(include_inactive=False) <= 1

    # ------------------------------------------------------------------
    # 认证:登录
    # ------------------------------------------------------------------

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        """校验账号密码。成功返回账号字典,失败返回 None。

        **所有失败原因都返回 None**,由调用方统一回一句"用户名或密码错误"。
        区分"用户不存在"和"密码错误"等于给了一个账号枚举接口。
        """
        name = normalize_username(username)
        if not name or not password:
            return None

        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (name,)
            ).fetchone()
        if row is None:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        if not row["is_active"]:
            return None
        return _public_user(row)

    # ------------------------------------------------------------------
    # 会话
    # ------------------------------------------------------------------

    def create_session(self, user_id: str, ttl_hours: int | None = None) -> dict[str, Any]:
        """签发一个会话。返回 `{token, expires_at}` —— **token 只在这里出现一次**。"""
        if ttl_hours is None:
            from ..config import get_settings

            ttl_hours = int(get_settings().session_ttl_hours)

        token = new_session_token()
        now = datetime.now()
        expires = now + timedelta(hours=max(1, ttl_hours))

        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sessions (token, user_id, created_at, expires_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    fingerprint(token),
                    user_id,
                    now.isoformat(timespec="seconds"),
                    expires.isoformat(timespec="seconds"),
                    now.isoformat(timespec="seconds"),
                ),
            )
        return {
            "token": token,
            "expires_at": expires.isoformat(timespec="seconds"),
            "ttl_hours": max(1, ttl_hours),
        }

    def resolve_session(self, token: str) -> dict[str, Any] | None:
        """令牌 → 账号。任何异常都返回 None(未登录),不抛。

        返回 None 的四种情况都**顺手清理掉那行会话**:令牌不存在、
        已过期、账号被停用、账号已删除。最后两种是"禁用立刻生效"的实现点 ——
        如果只查 sessions 表,禁用账号要等令牌自然过期才生效。
        """
        if not token:
            return None

        digest = fingerprint(token)
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT s.token        AS s_token,
                       s.expires_at   AS s_expires,
                       s.last_seen_at AS s_last_seen,
                       u.*
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token = ?
                """,
                (digest,),
            ).fetchone()

        if row is None:
            return None

        if _parse(row["s_expires"]) <= datetime.now():
            self.delete_session(token)
            return None

        if not row["is_active"]:
            self.delete_session(token)
            return None

        self._touch_session(digest, row["s_last_seen"])
        return _public_user(row)

    def _touch_session(self, digest: str, last_seen: str | None) -> None:
        """节流更新 last_seen_at。失败无所谓 —— 它只是个诊断字段。"""
        try:
            if last_seen and (datetime.now() - _parse(last_seen)).total_seconds() < _TOUCH_INTERVAL_SEC:
                return
            with connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE token = ?",
                    (_now(), digest),
                )
        except Exception:
            pass

    def delete_session(self, token: str) -> bool:
        # rowcount 必须在连接还开着的时候读 —— connect() 用的是
        # try/finally:close(),出了 with 再碰 cursor 会抛 ProgrammingError。
        with connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM sessions WHERE token = ?", (fingerprint(token),))
            return cur.rowcount > 0

    def delete_sessions_for_user(self, user_id: str) -> int:
        """吊销某账号的全部会话。改密码、停用、删除时调它。"""
        with connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            return int(cur.rowcount)

    def list_sessions(self, user_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sessions"
        params: list[Any] = []
        if user_id:
            sql += " WHERE user_id = ?"
            params.append(user_id)
        sql += " ORDER BY created_at DESC"
        with connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        # 令牌列是摘要,对调用方没有意义 —— 只给一个前缀方便排查"有几个会话"
        return [
            {
                "token_prefix": row["token"][:12],
                "user_id": row["user_id"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "last_seen_at": row["last_seen_at"],
            }
            for row in rows
        ]

    def purge_expired(self) -> int:
        """清掉过期会话。启动时跑一次即可,不需要定时任务。"""
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM sessions WHERE expires_at <= ?", (_now(),)
            )
            return int(cur.rowcount)


    # ------------------------------------------------------------------
    # 引导
    # ------------------------------------------------------------------

    def ensure_bootstrap_admin(self, username: str, password: str) -> dict[str, Any] | None:
        """库里一个账号都没有时,建出引导管理员。

        **只在完全空库时执行**,所以它不会覆盖任何已有账号 ——
        哪怕管理员后来把密码改了、把用户名改了,重启也不会被改回去。

        返回新建的账号;库非空时返回 None(表示"没做事")。
        """
        if self.count_users() > 0:
            return None
        try:
            return self.create_user(
                username, password, role=ROLE_ADMIN, display_name="系统管理员"
            )
        except UserError:
            # 配置里的引导账号本身不合法(比如 `TRIPMIND_ADMIN_PASSWORD=123`)。
            # **不能让启动挂掉** —— 那会让服务在"配置文件里有个错别字"时
            # 完全起不来。改成退回一个必然合法的组合,并把原因打到日志里,
            # 让部署者能登进去自己改。
            print(
                f"⚠️  引导管理员配置不合法(用户名={username!r}),"
                f"已回退为 admin / admin123,请登录后立即修改密码"
            )
            return self.create_user(
                "admin", "admin123", role=ROLE_ADMIN, display_name="系统管理员"
            )


_store: UserStore | None = None


def get_user_store() -> UserStore:
    """单例(沿用项目里 get_plan_store / get_llm 的风格)。"""
    global _store
    if _store is None:
        _store = UserStore()
    return _store


def reset_user_store() -> None:
    """丢掉单例缓存。

    测试要在临时库上建账号,而单例会把第一个库粘住 —— 于是第二组测试
    会拿上一个库的数据。加这个函数而不是让测试自己碰 `_store`:
    改私有变量名时(比如将来重构)只有这里需要跟着动。
    """
    global _store
    _store = None
