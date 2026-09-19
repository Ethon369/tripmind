"""账号管理命令行工具。

为什么在有了 /api/auth/users 之后还需要它
-----------------------------------------
HTTP 接口需要**先用一个管理员账号登进去**。而这个工具要解决的正是
"登不进去"那几种情况:

  · 忘了管理员密码,而且没有第二个管理员可以重置它
  · 想在没有前端的机器上(容器里、服务器上)建第一个账号
  · 需要脚本化地批量建账号(CI、演示环境初始化)

用法(在 backend/ 目录下)::

    # 列出所有账号
    python scripts/manage_users.py list

    # 建账号(不指定 --role 就是普通用户)
    python scripts/manage_users.py add zhangsan --password secret123
    python scripts/manage_users.py add boss --password secret123 --role admin

    # 重置密码 / 改角色 / 停用
    python scripts/manage_users.py passwd zhangsan --password newsecret
    python scripts/manage_users.py role zhangsan admin
    python scripts/manage_users.py disable zhangsan
    python scripts/manage_users.py enable zhangsan

    # 删账号(行程保留为无主)
    python scripts/manage_users.py delete zhangsan

⚠️ `--password` 会出现在 shell 历史和 `ps` 输出里。生产环境请用
   `--password-stdin`(从标准输入读一行),或者建完之后立刻用
   `passwd` 改掉。这是本工具唯一一处"方便与安全二选一"的地方,
   所以它有两个开关而不是一个好用的默认值。

⚠️ 它**直接写 SQLite**,不走 HTTP。也就是说必须和运行中的服务指向
   **同一个库文件** —— 在容器里跑的话要进容器(或用同样的
   TRIPMIND_DB 挂载)。库不对时表现是"建好了但登录页说密码错",
   而不是报错。
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# 让 `import app` 生效(与 tests/conftest.py 同样的做法)
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.store import get_user_store  # noqa: E402
from app.store.db import resolve_db_path  # noqa: E402
from app.store.user_store import (  # noqa: E402
    ROLE_ADMIN,
    ROLE_USER,
    ProtectedAccount,
    UserError,
)


def _print_db_banner() -> None:
    """先告诉使用者"在操作哪个库"。

    这个打印不是装饰:工具写的是文件而不是服务,而"改错了库"是这个工具
    最容易犯、最难发现的错(界面表现是"明明改了却登不上")。
    把路径印在每次输出前面,是对这个失效模式的直接对策。
    """
    print(f"📂 数据库: {resolve_db_path()}")
    print(f"   (TRIPMIND_DB = {get_settings().db_path!r})")
    print("-" * 60)


def _read_password(args) -> str:
    if args.password_stdin:
        line = sys.stdin.readline().strip()
        if not line:
            print("❌ 标准输入里没有读到密码")
            raise SystemExit(2)
        return line
    if args.password:
        return args.password
    # 交互式输入。不指定任何来源时**不猜、不生成**:
    # 生成一个随机密码然后打屏,在脚本里会变成"密码在日志里"。
    first = getpass.getpass("新密码: ")
    again = getpass.getpass("再输一次: ")
    if first != again:
        print("❌ 两次输入不一致")
        raise SystemExit(2)
    return first


def _find(store, username: str) -> dict:
    user = store.get_by_username(username)
    if user is None:
        print(f"❌ 账号「{username}」不存在")
        raise SystemExit(1)
    return user


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------


def _pad(text: str, width: int) -> str:
    """按**显示宽度**补空格。

    `str.ljust` 数的是字符数,而中文在等宽字体里占两格 —— 直接用 ljust
    会让含中文的那几列错位。这里只处理"宽字符占 2"这一条规则
    (本项目的中文都在 CJK 区),不引 wcwidth。
    """
    shown = sum(2 if _is_wide(ch) else 1 for ch in text)
    return text + " " * max(0, width - shown)


def _is_wide(ch: str) -> bool:
    code = ord(ch)
    return (
        0x1100 <= code <= 0x115F
        or 0x2E80 <= code <= 0xA4CF
        or 0xAC00 <= code <= 0xD7A3
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE6F
        or 0xFF00 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
    )


def cmd_list(store, args) -> int:
    users = store.list_users()
    if not users:
        print("(还没有任何账号。跑 `add` 建一个,或者启动服务让引导管理员生效)")
        return 0

    # 手写对齐而不是用 tabulate:为了一个格式化多一个依赖不划算,
    # 而这张表的列是固定的。
    header = (
        f"{_pad('用户名', 16)} {_pad('角色', 7)} {_pad('状态', 6)} "
        f"{_pad('行程', 6)} 最近登录"
    )
    rule = "-" * 72
    print(header)
    print(rule)
    for u in users:
        print(
            f"{_pad(u['username'], 16)} "
            f"{_pad(u['role'], 7)} "
            f"{_pad('启用' if u['is_active'] else '停用', 6)} "
            f"{_pad(str(u.get('plan_count', 0)), 6)} "
            f"{u.get('last_login_at') or '从未'}"
        )
    print(rule)
    admins = sum(1 for u in users if u["role"] == ROLE_ADMIN)
    active = sum(1 for u in users if u["is_active"])
    print(f"共 {len(users)} 个账号(管理员 {admins},启用 {active})")
    return 0


def cmd_add(store, args) -> int:
    password = _read_password(args)
    try:
        user = store.create_user(
            args.username,
            password,
            role=args.role,
            display_name=args.display_name,
        )
    except UserError as exc:
        print(f"❌ {exc}")
        return 1
    print(f"✅ 已创建 {user['role']} 账号「{user['username']}」(id={user['id']})")
    return 0


def cmd_passwd(store, args) -> int:
    user = _find(store, args.username)
    password = _read_password(args)
    try:
        store.update_user(user["id"], password=password)
    except UserError as exc:
        print(f"❌ {exc}")
        return 1
    # 说出来 —— 否则"改了密码但另一台设备还登着"会让人以为改密没生效。
    print(f"✅ 已重置「{user['username']}」的密码,该账号的其它登录已全部失效")
    return 0


def cmd_role(store, args) -> int:
    user = _find(store, args.username)
    try:
        updated = store.update_user(user["id"], role=args.role)
    except ProtectedAccount as exc:
        print(f"❌ {exc}")
        return 1
    except UserError as exc:
        print(f"❌ {exc}")
        return 1
    print(f"✅ 「{updated['username']}」的角色已改为 {updated['role']}")
    return 0


def _set_active(store, username: str, active: bool) -> int:
    user = _find(store, username)
    try:
        store.update_user(user["id"], is_active=active)
    except ProtectedAccount as exc:
        print(f"❌ {exc}")
        return 1
    state = "启用" if active else "停用"
    extra = "该账号的登录已全部失效" if not active else ""
    print(f"✅ 「{user['username']}」已{state}{('(' + extra + ')') if extra else ''}")
    return 0


def cmd_enable(store, args) -> int:
    return _set_active(store, args.username, True)


def cmd_disable(store, args) -> int:
    return _set_active(store, args.username, False)


def cmd_delete(store, args) -> int:
    user = _find(store, args.username)
    if not args.yes:
        print(
            f"将删除账号「{user['username']}」。"
            "TA 名下的行程**不会被删除**,只会变成无主(仅管理员可见)。"
        )
        if input("确认删除?输入 yes 继续: ").strip().lower() != "yes":
            print("已取消")
            return 0
    try:
        store.delete_user(user["id"])
    except ProtectedAccount as exc:
        print(f"❌ {exc}")
        return 1
    print(f"✅ 已删除「{user['username']}」,其行程已转为无主")
    return 0


def cmd_revoke(store, args) -> int:
    user = _find(store, args.username)
    n = store.delete_sessions_for_user(user["id"])
    print(f"✅ 已吊销「{user['username']}」的 {n} 个会话")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage_users.py",
        description="途灵 TripMind 账号管理(直接写 SQLite)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_password_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--password",
            help="密码。⚠️ 会进 shell 历史和 ps 输出,生产环境请改用 --password-stdin",
        )
        p.add_argument(
            "--password-stdin",
            action="store_true",
            help="从标准输入读一行作为密码(脚本化时用这个)",
        )

    sub.add_parser("list", help="列出所有账号").set_defaults(func=cmd_list)

    p = sub.add_parser("add", help="新建账号")
    p.add_argument("username")
    p.add_argument("--role", choices=[ROLE_USER, ROLE_ADMIN], default=ROLE_USER)
    p.add_argument("--display-name", default=None)
    add_password_args(p)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("passwd", help="重置密码(会吊销该账号的全部登录)")
    p.add_argument("username")
    add_password_args(p)
    p.set_defaults(func=cmd_passwd)

    p = sub.add_parser("role", help="改角色")
    p.add_argument("username")
    p.add_argument("role", choices=[ROLE_USER, ROLE_ADMIN])
    p.set_defaults(func=cmd_role)

    # enable / disable 共用一条实现,只是布尔值不同 —— 拆成两个子命令是因为
    # 命令行上 `enable zhangsan` 比 `active zhangsan --on` 好记,也更好读。
    sub.add_parser("enable", help="启用账号").add_argument("username")
    sub.choices["enable"].set_defaults(func=cmd_enable)

    sub.add_parser("disable", help="停用账号(会吊销其全部登录)").add_argument("username")
    sub.choices["disable"].set_defaults(func=cmd_disable)

    p = sub.add_parser("delete", help="删除账号(行程保留为无主)")
    p.add_argument("username")
    p.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    p.set_defaults(func=cmd_delete)

    sub.add_parser("revoke", help="强制某账号下线(保留账号)").add_argument("username")
    sub.choices["revoke"].set_defaults(func=cmd_revoke)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    _print_db_banner()
    try:
        store = get_user_store()
    except Exception as exc:
        print(f"❌ 打开账号库失败: {type(exc).__name__}: {exc}")
        return 2

    return args.func(store, args)


if __name__ == "__main__":
    raise SystemExit(main())
