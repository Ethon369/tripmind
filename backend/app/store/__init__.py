"""本地持久化(SQLite)

对外暴露:
- connect / init_db        连接、建表与迁移
- PlanStore               行程的增删改查
- UserStore               账号与会话的增删改查

为什么用标准库 sqlite3 而不是 ORM
---------------------------------
项目跑在 Python 3.14 上,已经为版本兼容放弃过 torch。SQLAlchemy 虽然
2.0.45+ 提供了 cp314 wheel,但本项目只有 6 张表,手写 SQL 约 300 行就够,
不值得再引入一个会随 Python 版本波动的依赖。

存储逻辑全部收在这个包里,将来要换 ORM 只需要改这一个目录。
"""

from .db import connect, init_db, resolve_db_path
from .plan_store import PlanStore, get_plan_store
from .user_store import (
    ROLE_ADMIN,
    ROLE_USER,
    ROLES,
    DuplicateUsername,
    ProtectedAccount,
    UserError,
    UserStore,
    get_user_store,
    normalize_username,
    reset_user_store,
    validate_username,
)

__all__ = [
    "connect",
    "init_db",
    "resolve_db_path",
    "PlanStore",
    "get_plan_store",
    "UserStore",
    "get_user_store",
    "reset_user_store",
    "UserError",
    "DuplicateUsername",
    "ProtectedAccount",
    "ROLE_ADMIN",
    "ROLE_USER",
    "ROLES",
    "normalize_username",
    "validate_username",
]
