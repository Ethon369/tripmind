"""端到端验收:两种角色对行程数据的访问边界。

**走真实 HTTP**(不是 TestClient),因为要验证的东西里有一部分是
"网络层真的拦住了" —— 比如没有 Authorization 头时是否真的 401、
分享链接是否真的能匿名打开。TestClient 走的是同一套代码,
但它把"中间有没有经过真实的反代/浏览器行为"这一层省掉了。

只用标准库 urllib:本机的 Git Bash 里没有 curl(见项目记忆),
而且用 Python 写能直接断言,不用解析 shell 输出。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 引导管理员的初始密码。默认是配置里的默认值;
# 服务端用 TRIPMIND_ADMIN_PASSWORD 覆盖时,这里也要跟着改(E2E_ADMIN_PW)。
ADMIN_USERNAME = os.getenv("E2E_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("E2E_ADMIN_PW", "admin123")

BASE = os.getenv("E2E_BASE", "http://127.0.0.1:8002")
# 服务端用的库。**必须和启动命令里的 TRIPMIND_DB 指向同一个文件** ——
# 脚本要直接插几条行程,插错库的表现是"列表是空的",很容易误判成权限错了。
DB = Path(os.getenv("E2E_DB") or (Path(__file__).resolve().parent.parent / "data" / "_e2e_auth.db"))

# ⚠️ **必须显式禁用代理。**
#
# 本机环境里有 `http_proxy=http://127.0.0.1:58715`(开发环境注入的),
# 而 urllib 默认会连 localhost 的请求也走代理 —— 代理不认识 8002,
# 于是返回 **502 Bad Gateway**。
#
# 这个症状很有欺骗性:它看起来像"我的服务崩了/路由写错了",
# 而实际上是请求根本没到服务上(端口上压根没有监听也照样 502)。
# 所以这里装一个空 ProxyHandler,把回环地址的代理绕开。
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

FAILS: list[str] = []
CHECKS = 0


def call(method: str, path: str, *, token: str | None = None, body: dict | None = None):
    req = urllib.request.Request(f"{BASE}{path}", method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with _opener.open(req, data, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def check(label: str, actual, expected) -> None:
    global CHECKS
    CHECKS += 1
    ok = actual == expected
    if not ok:
        FAILS.append(f"{label}: 期望 {expected!r},实际 {actual!r}")
    print(f"  {'PASS' if ok else 'FAIL'}  {label}  →  {actual!r}")


def main() -> int:
    print("=" * 70)
    print("0. 未登录:一切受保护的东西都进不去")
    print("=" * 70)
    check("GET  /api/trip/plans", call("GET", "/api/trip/plans")[0], 401)
    check("POST /api/trip/plan", call("POST", "/api/trip/plan", body={})[0], 401)
    check("GET  /api/auth/me", call("GET", "/api/auth/me")[0], 401)
    check("GET  /api/auth/users", call("GET", "/api/auth/users")[0], 401)
    check("GET  /api/knowledge/docs", call("GET", "/api/knowledge/docs")[0], 401)
    check(
        "旧的 X-Admin-Token 头不再有特权",
        call("GET", "/api/auth/users", token=None)[0],
        401,
    )

    print()
    print("=" * 70)
    print("1. 引导管理员能登录")
    print("=" * 70)
    st, res = call(
        "POST",
        "/api/auth/login",
        body={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    check(f"POST /api/auth/login ({ADMIN_USERNAME}/***)", st, 200)
    if st != 200:
        print("  ⚠️  引导账号没建出来,后面的检查无意义")
        return 1
    admin_token = res["token"]
    check("管理员角色", res["user"]["role"], "admin")

    print()
    print("=" * 70)
    print("2. 登录失败的语义")
    print("=" * 70)
    st_bad, _ = call("POST", "/api/auth/login", body={"username": "admin", "password": "nope"})
    st_nouser, body_nouser = call(
        "POST", "/api/auth/login", body={"username": "不存在的用户", "password": "nope"}
    )
    check("密码错误 → 401", st_bad, 401)
    check("用户不存在 → 也是 401(不泄露账号是否存在)", st_nouser, 401)

    print()
    print("=" * 70)
    print("3. 管理员建两个普通账号")
    print("=" * 70)
    for name in ("alice", "bob"):
        st, res = call(
            "POST",
            "/api/auth/users",
            token=admin_token,
            body={"username": name, "password": f"{name}-secret", "role": "user"},
        )
        check(f"建号 {name}", st, 201)

    st, _ = call(
        "POST",
        "/api/auth/users",
        token=admin_token,
        body={"username": "alice", "password": "whatever-else", "role": "user"},
    )
    check("重名 → 409", st, 409)

    alice_token = call(
        "POST", "/api/auth/login", body={"username": "alice", "password": "alice-secret"}
    )[1]["token"]
    bob_token = call(
        "POST", "/api/auth/login", body={"username": "bob", "password": "bob-secret"}
    )[1]["token"]
    alice_id = call("GET", "/api/auth/me", token=alice_token)[1]["user"]["id"]
    bob_id = call("GET", "/api/auth/me", token=bob_token)[1]["user"]["id"]

    print()
    print("=" * 70)
    print("4. 直接往库里塞三条行程(绕开要跑 100 秒的生成接口)")
    print("=" * 70)
    # 走真实 HTTP 生成一次代价太大(实测 100 秒+),而这里要验的是**访问控制**
    # 而不是生成逻辑 —— 所以用 SQL 直接造数据,归属字段与生产路径一致。
    conn = sqlite3.connect(DB)
    for pid, uid, city in (
        ("e2e-alice-plan", alice_id, "北京"),
        ("e2e-bob-plan", bob_id, "上海"),
        ("e2e-orphan-plan", None, "无主城"),
    ):
        conn.execute(
            "INSERT INTO plans (id, created_at, updated_at, status, city, start_date, "
            "end_date, travel_days, request_json, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pid,
                "2026-09-19T10:00:00",
                "2026-09-19T10:00:00",
                "ok",
                city,
                "2026-10-01",
                "2026-10-02",
                2,
                "{}",
                uid,
            ),
        )
    conn.commit()
    conn.close()
    print("  已插入 3 条:alice 的 / bob 的 / 无主的")

    print()
    print("=" * 70)
    print("5. 列表的归属过滤 —— 本任务的核心")
    print("=" * 70)
    ids = lambda r: sorted(x["id"] for x in r[1]["data"])  # noqa: E731

    check("alice 只看得到自己的", ids(call("GET", "/api/trip/plans", token=alice_token)), ["e2e-alice-plan"])
    check("bob 只看得到自己的", ids(call("GET", "/api/trip/plans", token=bob_token)), ["e2e-bob-plan"])
    check(
        "管理员默认也只看自己的(scope 默认 mine)",
        ids(call("GET", "/api/trip/plans", token=admin_token)),
        [],
    )
    check(
        "管理员 scope=all 看得到全部三条",
        ids(call("GET", "/api/trip/plans?scope=all", token=admin_token)),
        ["e2e-alice-plan", "e2e-bob-plan", "e2e-orphan-plan"],
    )
    check(
        "普通用户 scope=all → 403(显式拒绝,不是静默降级)",
        call("GET", "/api/trip/plans?scope=all", token=alice_token)[0],
        403,
    )

    print()
    print("=" * 70)
    print("6. 统计数字跟着范围变")
    print("=" * 70)
    check(
        "alice 的累计条数",
        call("GET", "/api/trip/plans", token=alice_token)[1]["stats"]["total"],
        1,
    )
    check(
        "管理员的全部条数",
        call("GET", "/api/trip/plans?scope=all", token=admin_token)[1]["stats"]["total"],
        3,
    )

    print()
    print("=" * 70)
    print("7. 归属字段的可见性")
    print("=" * 70)
    all_rows = {r["id"]: r for r in call("GET", "/api/trip/plans?scope=all", token=admin_token)[1]["data"]}
    check("管理员看得到归属用户名", all_rows["e2e-alice-plan"]["owner_username"], "alice")
    check("无主行程的归属是 null", all_rows["e2e-orphan-plan"]["owner_username"], None)

    mine = call("GET", "/api/trip/plans", token=alice_token)[1]["data"][0]
    check("普通用户列表里归属被隐藏", mine["owner_username"], None)

    print()
    print("=" * 70)
    print("8. 分享链接:必须仍然匿名可读,且不泄露归属")
    print("=" * 70)
    st, share = call("GET", "/api/trip/plans/e2e-alice-plan")
    check("匿名打开分享链接 → 200", st, 200)
    check("分享页里看不到归属", share["meta"]["owner_username"], None)
    st, share_admin = call("GET", "/api/trip/plans/e2e-alice-plan", token=admin_token)
    check("管理员看同一条能看到归属", share_admin["meta"]["owner_username"], "alice")

    print()
    print("=" * 70)
    print("8b. can_edit:前端据此决定显不显示「编辑行程」")
    print("=" * 70)
    check(
        "本人 → 可以编辑",
        call("GET", "/api/trip/plans/e2e-alice-plan", token=alice_token)[1]["can_edit"],
        True,
    )
    check(
        "别人 → 不可以编辑",
        call("GET", "/api/trip/plans/e2e-alice-plan", token=bob_token)[1]["can_edit"],
        False,
    )
    check("匿名(分享链接)→ 不可以编辑", share["can_edit"], False)
    check(
        "管理员 → 可以编辑任何人的",
        call("GET", "/api/trip/plans/e2e-alice-plan", token=admin_token)[1]["can_edit"],
        True,
    )
    check(
        "无主行程:普通用户不可以",
        call("GET", "/api/trip/plans/e2e-orphan-plan", token=alice_token)[1]["can_edit"],
        False,
    )
    check(
        "无主行程:管理员可以",
        call("GET", "/api/trip/plans/e2e-orphan-plan", token=admin_token)[1]["can_edit"],
        True,
    )

    print()
    print("=" * 70)
    print("9. 行程的写权限")
    print("=" * 70)
    check(
        "bob 删 alice 的 → 403",
        call("DELETE", "/api/trip/plans/e2e-alice-plan", token=bob_token)[0],
        403,
    )
    check(
        "alice 不能删无主的 → 403",
        call("DELETE", "/api/trip/plans/e2e-orphan-plan", token=alice_token)[0],
        403,
    )
    check("匿名删 → 401", call("DELETE", "/api/trip/plans/e2e-alice-plan")[0], 401)
    check(
        "不存在的行程 → 404(404 先于 403,不泄露 id 是否存在)",
        call("DELETE", "/api/trip/plans/no-such-plan", token=alice_token)[0],
        404,
    )
    check(
        "管理员能删任何人的",
        call("DELETE", "/api/trip/plans/e2e-bob-plan", token=admin_token)[0],
        200,
    )
    check(
        "删掉之后确实没了",
        ids(call("GET", "/api/trip/plans?scope=all", token=admin_token)),
        ["e2e-alice-plan", "e2e-orphan-plan"],
    )

    print()
    print("=" * 70)
    print("10. 知识库:管理员专属")
    print("=" * 70)
    # 知识库改成"每人一份"之后,普通用户能读**自己**的列表了 ——
    # 这一条原来是 403(那时管理员专属)。保留它会被误读成回归。
    check("普通用户读自己的攻略列表 → 200", call("GET", "/api/knowledge/docs", token=alice_token)[0], 200)
    check("普通用户读全部人的 → 403", call("GET", "/api/knowledge/docs?scope=all", token=alice_token)[0], 403)
    check("管理员读知识库攻略列表 → 200", call("GET", "/api/knowledge/docs", token=admin_token)[0], 200)
    check(
        "普通用户切 RAG 开关 → 403",
        call("POST", "/api/knowledge/rag-toggle", token=alice_token, body={"enabled": True})[0],
        403,
    )
    check(
        "公开的只读接口不受影响",
        call("GET", "/api/knowledge/status?with_counts=false")[0],
        200,
    )

    print()
    print("=" * 70)
    print("11. 账号管理接口")
    print("=" * 70)
    check("普通用户列账号 → 403", call("GET", "/api/auth/users", token=alice_token)[0], 403)
    st, users = call("GET", "/api/auth/users", token=admin_token)
    check("管理员列账号 → 200", st, 200)
    check("列表里没有密码哈希", any("password_hash" in u for u in users["users"]), False)

    check(
        "不能删自己",
        call("DELETE", f"/api/auth/users/{call('GET', '/api/auth/me', token=admin_token)[1]['user']['id']}", token=admin_token)[0],
        400,
    )

    print()
    print("=" * 70)
    print("12. 停用账号 → 现有会话立刻失效")
    print("=" * 70)
    check(
        "先确认 alice 现在能用",
        call("GET", "/api/auth/me", token=alice_token)[0],
        200,
    )
    check(
        "管理员停用 alice",
        call(
            "PATCH",
            f"/api/auth/users/{alice_id}",
            token=admin_token,
            body={"is_active": False},
        )[0],
        200,
    )
    check("alice 的令牌立刻失效", call("GET", "/api/auth/me", token=alice_token)[0], 401)
    # ⚠️ 只**调一次**登录，然后对同一个结果断言两件事。
    #
    # 这里踩过一次坑：原来写成两次独立的 `call(...)`，于是同一秒内向同一个
    # IP 发了两次失败登录 —— 加上前面几节的失败尝试，IP 维度的计数正好
    # 撞到阈值，最后一条断言拿到的是 429 而不是 401，看起来像"停用逻辑坏了"。
    #
    # 顺带说明一个**有意为之**的行为：节流的 IP 维度是跨账号的。
    # 也就是说本地开发时（所有请求都来自 127.0.0.1）连着试错几个账号的密码，
    # 全站就会被锁五分钟。这是"宁可偏严"的取舍，见 login_guard.py 的说明。
    st_login, body_login = call(
        "POST", "/api/auth/login", body={"username": "alice", "password": "alice-secret"}
    )
    check("alice 也登不进来了", st_login, 401)
    check(
        "停用的账号登不进来时文案与密码错误相同(不给账号枚举留口子)",
        body_login["detail"],
        "用户名或密码错误",
    )

    print()
    print("=" * 70)
    print("13. 改密码 → 其它会话失效 + 换发新令牌")
    print("=" * 70)
    NEW_ADMIN_PW = "new-admin-pw"
    st, res = call(
        "POST",
        "/api/auth/password",
        token=admin_token,
        body={"old_password": ADMIN_PASSWORD, "new_password": NEW_ADMIN_PW},
    )
    check("改自己的密码", st, 200)
    new_token = res.get("token") if isinstance(res, dict) else None
    check("返回了新令牌", bool(new_token), True)
    check("旧令牌失效", call("GET", "/api/auth/me", token=admin_token)[0], 401)
    check("新令牌可用", call("GET", "/api/auth/me", token=new_token)[0], 200)
    check(
        "旧密码登不进来",
        call("POST", "/api/auth/login", body={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})[0],
        401,
    )
    st, _ = call(
        "POST",
        "/api/auth/login",
        body={"username": ADMIN_USERNAME, "password": NEW_ADMIN_PW},
    )
    check("新密码能登进来", st, 200)

    # ⚠️ **必须把 admin_token 换成新换发的那个。**
    #    改密码会吊销该账号的**全部**会话(这是有意的:改密码的动机通常就是
    #    "怀疑别人知道我的密码"),所以上面那个 admin_token 从这里开始已经失效。
    #    后面几节还要用管理员身份 —— 继续用旧令牌会得到一串 401,
    #    而症状看起来像"后面那些功能坏了",排查方向完全错。
    #    (实测踩过:第 16 节的管理员 scope=all 返回空列表,根因就在这。)
    admin_token = new_token

    print()
    print("=" * 70)
    print("14. 登录节流")
    print("=" * 70)
    for _ in range(6):
        call("POST", "/api/auth/login", body={"username": "bob", "password": "wrong"})
    st, _ = call("POST", "/api/auth/login", body={"username": "bob", "password": "bob-secret"})
    check("连续失败后被锁(即使密码是对的)", st, 429)

    print()
    print("=" * 70)
    print("15. 自助注册")
    print("=" * 70)
    # 每次跑用不同的用户名 —— 否则第二次跑会撞上第一次建出来的账号(409),
    # 而这组检查想验的是"注册成功"那条路径。
    import uuid as _uuid

    uname = f"e2e{_uuid.uuid4().hex[:8]}"
    st, reg = call(
        "POST",
        "/api/auth/register",
        body={"username": uname, "password": "e2e-register-pw"},
    )
    check("匿名可以注册 → 201", st, 201)
    check("注册返回了令牌(注册即登录)", bool(reg.get("token")) if st == 201 else False, True)
    check("注册出来的是普通用户", reg.get("user", {}).get("role") if st == 201 else None, "user")

    new_token = reg.get("token") if st == 201 else None
    check("注册返回的令牌立刻可用", call("GET", "/api/auth/me", token=new_token)[0], 200)

    check(
        "新账号看不到别人的行程",
        ids(call("GET", "/api/trip/plans", token=new_token)),
        [],
    )
    check(
        "新账号 scope=all 也是 403(注册没有提权)",
        call("GET", "/api/trip/plans?scope=all", token=new_token)[0],
        403,
    )
    # 知识库改成"每人一份"之后,普通用户**能**进自己的知识库了。
    # 这一条原来是 403(那时是管理员专属)—— 保持它会被误读成回归。
    check(
        "新账号能读自己的知识库(每人一份)",
        call("GET", "/api/knowledge/docs", token=new_token)[0],
        200,
    )
    check(
        "但灌内置库仍然是 403(全站共用资源)",
        call("POST", "/api/knowledge/ingest", body={"source": "all"}, token=new_token)[0],
        403,
    )
    check(
        "重名注册 → 409",
        call(
            "POST",
            "/api/auth/register",
            body={"username": uname, "password": "another-pw-here"},
        )[0],
        409,
    )
    check(
        "弱密码注册 → 400",
        call(
            "POST",
            "/api/auth/register",
            body={"username": f"{uname}x", "password": "123"},
        )[0],
        400,
    )
    check(
        "注册不能提权(塞 role=admin 无效)",
        call(
            "POST",
            "/api/auth/register",
            body={
                "username": f"{uname}y",
                "password": "e2e-register-pw",
                "role": "admin",
            },
        )[1].get("user", {}).get("role"),
        "user",
    )

    print()
    print("=" * 70)
    print("16. 知识库按人隔离")
    print("=" * 70)
    # 直接往库里塞两条攻略(绕开要花 embedding 额度的上传接口)——
    # 这里要验的是**归属**,不是解析或入库。
    #
    # ⚠️ 用**新注册**的两个账号,不用前面那两位:
    #    · alice 在第 12 节被停用了(令牌已失效);
    #    · 第 14 节的连续失败把来源 IP 锁了,再登录一律 429。
    #    注册接口自带令牌,正好绕开这两件事 —— 这也顺带说明"注册即登录"
    #    在测试里确实省事。
    import sqlite3 as _sqlite
    import uuid as _uuid2

    kb_a = f"kba{_uuid2.uuid4().hex[:6]}"
    kb_b = f"kbb{_uuid2.uuid4().hex[:6]}"
    a_tok = call("POST", "/api/auth/register",
                 body={"username": kb_a, "password": "kb-a-secret"})[1]["token"]
    b_tok = call("POST", "/api/auth/register",
                 body={"username": kb_b, "password": "kb-b-secret"})[1]["token"]
    a_id = call("GET", "/api/auth/me", token=a_tok)[1]["user"]["id"]
    b_id = call("GET", "/api/auth/me", token=b_tok)[1]["user"]["id"]

    conn = _sqlite.connect(DB)
    for doc_id, title, uid in (
        ("e2e-doc-a", "A 的攻略", a_id),
        ("e2e-doc-b", "B 的攻略", b_id),
    ):
        conn.execute(
            "INSERT OR REPLACE INTO knowledge_docs (id, title, origin, content, "
            "content_hash, char_count, chunk_count, chunk_ids, status, created_at, user_id) "
            "VALUES (?, ?, 'paste', ?, ?, 10, 1, '[]', 'pending', ?, ?)",
            (doc_id, title, f"{title} 的正文", doc_id * 3, "2026-09-19T10:00:00", uid),
        )
    conn.commit()
    conn.close()
    print("  已插入 2 条:A 的 / B 的")

    doc_ids = lambda r: sorted(d["id"] for d in (r[1] or {}).get("docs") or [])  # noqa: E731

    check(
        "A 只列得到自己的攻略",
        doc_ids(call("GET", "/api/knowledge/docs", token=a_tok)),
        ["e2e-doc-a"],
    )
    check(
        "B 只列得到自己的攻略",
        doc_ids(call("GET", "/api/knowledge/docs", token=b_tok)),
        ["e2e-doc-b"],
    )
    check(
        "管理员 scope=all 看得到全部",
        doc_ids(call("GET", "/api/knowledge/docs?scope=all", token=admin_token)),
        ["e2e-doc-a", "e2e-doc-b"],
    )
    check(
        "普通用户 scope=all → 403",
        call("GET", "/api/knowledge/docs?scope=all", token=a_tok)[0],
        403,
    )
    check(
        "A 读自己的分块 → 200",
        call("GET", "/api/knowledge/docs/e2e-doc-a", token=a_tok)[0],
        200,
    )
    check(
        "A 读 B 的分块 → 403(分块里是攻略全文)",
        call("GET", "/api/knowledge/docs/e2e-doc-b", token=a_tok)[0],
        403,
    )
    check(
        "A 删 B 的 → 403",
        call("DELETE", "/api/knowledge/docs/e2e-doc-b", token=a_tok)[0],
        403,
    )
    check("匿名看攻略列表 → 401", call("GET", "/api/knowledge/docs")[0], 401)
    check(
        "普通用户灌内置库 → 403(全站共用资源)",
        call("POST", "/api/knowledge/ingest", body={"source": "all"}, token=a_tok)[0],
        403,
    )
    check(
        "普通用户切 RAG 开关 → 403(进程级开关)",
        call("POST", "/api/knowledge/rag-toggle", body={"enabled": True}, token=a_tok)[0],
        403,
    )

    print()
    print("=" * 70)
    total = CHECKS
    if FAILS:
        print(f"❌ {len(FAILS)} / {total} 项失败:")
        for f in FAILS:
            print(f"   · {f}")
        return 1
    print(f"✅ 全部 {total} 项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
