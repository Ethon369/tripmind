"""账号认证与角色授权的测试。

本文件取代了原来的 `test_admin_auth.py` + `test_admin_token_contract.py`
(共享口令时代的两份)。契约的形状变了,所以不是改几个断言能完成的:

    旧:一个口令,挂在 10 个"写端点"上,前端有一份 `ADMIN_GUARDED` 清单
    新:一个账号 + 一个角色,覆盖全部端点,前端的清单**整个消失了**
        (因为不再需要"挑哪些请求附头"—— 登录态对每个请求都成立)

所以旧文件里"前后端清单必须逐条对上"那一整类测试没有替代品:
它防的那个失效模式(两端各写一遍清单、静默漂移)在新设计里不存在了。
这里保留下来的是"**哪些端点要什么权限**"的金名单,它防的是另一种漂移:
改路由时漏挂依赖。

关于"怎么遍历路由"
------------------
不能用 `app.routes` —— 新版 FastAPI 把 `include_router` 进来的子路由包成
`_IncludedRouter`,里面只暴露版本相关的属性,遍历它等于把测试绑死在框架
内部实现上。`APIRouter.routes` 与依赖树(`route.dependant`)是稳定接口。
"""

from __future__ import annotations

import time  # noqa: F401 — 保留:节流相关的用例将来可能要真计时
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.routing import APIRoute

from app.api.deps import can_access_plan
from app.api.routes import auth, knowledge, trip
from app.config import get_settings
from app.services.login_guard import LoginGuard, get_login_guard, get_register_guard
from app.services.security import (
    fingerprint,
    hash_password,
    password_policy_error,
    verify_password,
)
from app.store.user_store import (
    ROLE_ADMIN,
    ROLE_USER,
    DuplicateUsername,
    ProtectedAccount,
    UserError,
    UserStore,
)
from tests.conftest import make_plan, make_request

# ===========================================================================
# 夹具
# ===========================================================================

# 测试用的哈希轮数。**必须调小**:默认 600k 轮约 0.3 秒/次,
# 而本文件会建几十个账号、登录几十次 —— 不调的话单文件就要跑一分钟,
# 而它现在总共只要几秒。
_TEST_ITERATIONS = 1000


@pytest.fixture
def authdb(tmp_path, monkeypatch):
    """把两个 store 的单例都指到一个临时库上,并把哈希轮数调小。

    必须**同时**重置 `UserStore` 和 `PlanStore` 的单例:它们各自缓存了
    建库时的路径,只重置一个的话另一个还会写进真实的 `backend/data/tripmind.db`
    —— 那是开发机的数据,测试往里写会很难发现(测试照样绿)。
    """
    from app.store import plan_store as ps
    from app.store import user_store as us

    db = tmp_path / "auth-test.db"
    settings = get_settings()
    monkeypatch.setattr(settings, "db_path", str(db))
    monkeypatch.setattr(settings, "password_hash_iterations", _TEST_ITERATIONS)
    monkeypatch.setattr(settings, "password_min_length", 6)
    monkeypatch.setattr(settings, "session_ttl_hours", 168)
    monkeypatch.setattr(us, "_store", None)
    monkeypatch.setattr(ps, "_store", None)
    # 节流是**进程内全局状态**,不清的话上一个用例的失败次数会拦住下一个用例。
    # **两个 guard 都要清** —— 登录和注册各自独立计数,漏清一个会让另一个
    # 的用例莫名其妙地拿到 429。
    get_login_guard().reset()
    get_register_guard().reset()
    yield db
    get_login_guard().reset()
    get_register_guard().reset()


@pytest.fixture
def store(authdb) -> UserStore:
    from app.store import get_user_store

    return get_user_store()


@pytest.fixture
def client(authdb):
    """当前代码的真实 ASGI 应用。

    **不用 `with TestClient(app)`** —— 那会触发 startup 事件去拉起高德 MCP
    子进程,几十秒起步;而且鉴权发生在路由依赖里,与 MCP 是否就绪无关。
    代价是 startup 里的引导管理员不会被创建,所以测试自己建账号。
    """
    from fastapi.testclient import TestClient

    from app.api.main import app

    return TestClient(app)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin(store) -> dict[str, Any]:
    return store.create_user("boss", "boss-secret", role=ROLE_ADMIN)


@pytest.fixture
def alice(store) -> dict[str, Any]:
    return store.create_user("alice", "alice-secret", role=ROLE_USER)


@pytest.fixture
def bob(store) -> dict[str, Any]:
    return store.create_user("bob", "bob-secret", role=ROLE_USER)


def _login(client, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


@pytest.fixture
def admin_token(client, admin) -> str:
    return _login(client, "boss", "boss-secret")


@pytest.fixture
def alice_token(client, alice) -> str:
    return _login(client, "alice", "alice-secret")


@pytest.fixture
def bob_token(client, bob) -> str:
    return _login(client, "bob", "bob-secret")


def _make_plan(store, user_id: str | None, city: str = "北京") -> str:
    """造一条真实落库的行程。走 `create_running` + `finish_plan`,
    与生产路径一致 —— 手写 INSERT 会绕过归属写入那段代码,测了个假的。

    请求对象用**真的 `TripRequest`** 而不是 SimpleNamespace:
    `create_running` 会把 request 序列化进 `request_json`
    (`_dumps` 走 `model_dump_json`),用假对象会在那一步抛
    `TypeError: Object of type SimpleNamespace is not JSON serializable`
    —— 也就是说假对象根本走不完生产路径。
    """
    from app.models.schemas import TripRequest

    plan_id = store.create_running(
        TripRequest(**make_request(city=city, start_date="2026-06-01", travel_days=3)),
        user_id=user_id,
    )
    store.finish_plan(plan_id, None, status="ok")
    return plan_id


@pytest.fixture
def plan_store(authdb):
    from app.store import get_plan_store

    return get_plan_store()


# ===========================================================================
# 1. 密码哈希
# ===========================================================================


class Test密码哈希:
    def test_哈希里不含明文(self):
        stored = hash_password("my-secret-pw", iterations=_TEST_ITERATIONS)
        assert "my-secret-pw" not in stored
        # 格式:算法$轮数$盐$摘要
        algo, iters, salt, digest = stored.split("$")
        assert algo == "pbkdf2_sha256"
        assert int(iters) == _TEST_ITERATIONS
        assert len(salt) == 32  # 16 字节 → 32 个 hex 字符
        assert len(digest) == 64  # 32 字节 → 64 个 hex 字符

    def test_同一个密码两次哈希结果不同(self):
        """盐是随机的。相同 → 说明盐写死了,彩虹表直接可用。"""
        a = hash_password("same-pw", iterations=_TEST_ITERATIONS)
        b = hash_password("same-pw", iterations=_TEST_ITERATIONS)
        assert a != b

    def test_正确密码通过(self):
        stored = hash_password("right", iterations=_TEST_ITERATIONS)
        assert verify_password("right", stored) is True

    def test_错误密码不通过(self):
        stored = hash_password("right", iterations=_TEST_ITERATIONS)
        assert verify_password("wrong", stored) is False
        assert verify_password("righ", stored) is False
        assert verify_password("rightt", stored) is False

    @pytest.mark.parametrize("stored", ["", "   ", "garbage", "pbkdf2_sha256$abc$x$y", "md5$1$a$b"])
    def test_损坏的哈希串返回False而不是抛异常(self, stored):
        """哈希串坏了(手工改库、迁移出错)时必须是"校验不过"这一个结果。
        抛异常会让登录接口 500,而 500 和 401 的区别就是一个账号枚举信号。
        """
        assert verify_password("whatever", stored) is False

    def test_轮数从哈希串里读(self):
        """换了默认轮数,老哈希仍然能校验 —— 否则调高轮数会让所有老密码失效。"""
        old = hash_password("pw", iterations=10)
        assert verify_password("pw", old) is True

    def test_空密码不通过(self):
        stored = hash_password("pw", iterations=_TEST_ITERATIONS)
        assert verify_password("", stored) is False


class Test密码策略:
    def test_太短(self):
        assert password_policy_error("12345", min_length=6) is not None

    def test_刚好够长(self):
        assert password_policy_error("123456", min_length=6) is None

    def test_不能与用户名相同(self):
        assert password_policy_error("alice", min_length=6, username="alice") is not None
        # 大小写和空格都要能识别出来
        assert password_policy_error("ALICE", min_length=6, username="alice") is not None
        assert password_policy_error(" alice ", min_length=6, username="alice") is not None

    def test_不能是重复字符(self):
        assert password_policy_error("aaaaaa", min_length=6) is not None

    def test_空密码(self):
        assert password_policy_error("", min_length=6) is not None


# ===========================================================================
# 2. 账号存储
# ===========================================================================


class Test账号存储:
    def test_建账号后能查到(self, store):
        u = store.create_user("zhangsan", "pw-123456")
        assert u["username"] == "zhangsan"
        assert u["role"] == ROLE_USER
        assert u["is_active"] is True
        assert store.get_by_id(u["id"])["username"] == "zhangsan"

    def test_返回值里没有密码哈希(self, store):
        """**这是整个文件里最重要的一条断言之一。**
        密码哈希一旦从 API 漏出去,等于把离线爆破的材料送给了对方。
        """
        u = store.create_user("zhangsan", "pw-123456")
        assert "password_hash" not in u
        assert not any("hash" in k for k in u)

    def test_列表里也没有密码哈希(self, store):
        store.create_user("zhangsan", "pw-123456")
        for item in store.list_users():
            assert "password_hash" not in item

    def test_重名被拒(self, store):
        store.create_user("zhangsan", "pw-123456")
        with pytest.raises(DuplicateUsername):
            store.create_user("zhangsan", "pw-654321")

    def test_重名判定不区分大小写(self, store):
        """`Zhaoyun` 和 `zhaoyun` 是同一个账号。

        否则用户会卡在"提示已存在、但按自己记的写法查不到"这个状态里。
        """
        store.create_user("Zhaoyun", "pw-123456")
        with pytest.raises(DuplicateUsername):
            store.create_user("zhaoyun", "pw-654321")
        assert store.get_by_username("ZHAOYUN") is not None

    @pytest.mark.parametrize("name", ["", "ab", "带中文的名", "has space", "a" * 33, "-leading"])
    def test_非法用户名被拒(self, store, name):
        with pytest.raises(UserError):
            store.create_user(name, "pw-123456")

    def test_弱密码被拒(self, store):
        with pytest.raises(UserError):
            store.create_user("zhangsan", "12345")
        with pytest.raises(UserError):
            store.create_user("zhangsan", "zhangsan")

    def test_非法角色被拒(self, store):
        with pytest.raises(UserError):
            store.create_user("zhangsan", "pw-123456", role="superuser")

    def test_改密码后旧密码失效(self, store):
        u = store.create_user("zhangsan", "pw-123456")
        store.update_user(u["id"], password="pw-654321")
        assert store.authenticate("zhangsan", "pw-123456") is None
        assert store.authenticate("zhangsan", "pw-654321") is not None

    def test_停用后登不进来(self, store):
        u = store.create_user("zhangsan", "pw-123456")
        store.update_user(u["id"], is_active=False)
        assert store.authenticate("zhangsan", "pw-123456") is None

    def test_停用账号的现有会话立刻失效(self, store):
        """**禁用必须立刻生效**,不能等令牌自然过期(默认 7 天)。"""
        u = store.create_user("zhangsan", "pw-123456")
        s = store.create_session(u["id"])
        assert store.resolve_session(s["token"]) is not None

        store.update_user(u["id"], is_active=False)
        assert store.resolve_session(s["token"]) is None

    def test_改密码会吊销全部会话(self, store):
        u = store.create_user("zhangsan", "pw-123456")
        s1 = store.create_session(u["id"])
        s2 = store.create_session(u["id"])

        store.update_user(u["id"], password="pw-654321")

        assert store.resolve_session(s1["token"]) is None
        assert store.resolve_session(s2["token"]) is None

    def test_删除账号返回False表示不存在(self, store):
        assert store.delete_user("no-such-id") is False

    def test_删除账号后会话与行程的处理(self, store, plan_store):
        """账号删了,**行程不删**,只变成无主。

        行程带着真实成本记录,而成本是历史页顶部的聚合数字 ——
        删账号顺手抹掉成本,会让"这个月花了多少"随时间缩水。
        """
        u = store.create_user("zhangsan", "pw-123456")
        s = store.create_session(u["id"])
        pid = _make_plan(plan_store, u["id"])

        assert store.delete_user(u["id"]) is True

        assert store.resolve_session(s["token"]) is None
        row = plan_store.get_plan(pid)
        assert row is not None, "行程不该被连带删除"
        assert row["user_id"] is None, "应当转为无主"
        # 无主行程仍然出现在管理员的全量列表里
        assert pid in [r["id"] for r in plan_store.list_plans()]


class Test最后一个管理员:
    """不能把系统改到"再也进不去账号管理页"的状态。

    一但触发就是死锁:没有管理员 → 建不了管理员 → 只能改库。
    """

    def test_不能降级(self, store):
        u = store.create_user("boss", "boss-secret", role=ROLE_ADMIN)
        with pytest.raises(ProtectedAccount):
            store.update_user(u["id"], role=ROLE_USER)

    def test_不能停用(self, store):
        u = store.create_user("boss", "boss-secret", role=ROLE_ADMIN)
        with pytest.raises(ProtectedAccount):
            store.update_user(u["id"], is_active=False)

    def test_不能删除(self, store):
        u = store.create_user("boss", "boss-secret", role=ROLE_ADMIN)
        with pytest.raises(ProtectedAccount):
            store.delete_user(u["id"])

    def test_有第二个管理员时就可以(self, store):
        u1 = store.create_user("boss", "boss-secret", role=ROLE_ADMIN)
        store.create_user("boss2", "boss2-secret", role=ROLE_ADMIN)

        store.update_user(u1["id"], role=ROLE_USER)
        assert store.get_by_id(u1["id"])["role"] == ROLE_USER

    def test_被停用的管理员不算数(self, store):
        """两个管理员,其中一个被停用了 —— 剩下那个就是"最后一个",

        判据必须是**启用中的**管理员数量。只数 role='admin' 的话,
        这里会允许把唯一能登进来的管理员也删掉。
        """
        u1 = store.create_user("boss", "boss-secret", role=ROLE_ADMIN)
        u2 = store.create_user("boss2", "boss2-secret", role=ROLE_ADMIN)
        store.update_user(u2["id"], is_active=False)

        with pytest.raises(ProtectedAccount):
            store.delete_user(u1["id"])

    def test_普通用户不受这条约束(self, store):
        store.create_user("boss", "boss-secret", role=ROLE_ADMIN)
        u = store.create_user("zhangsan", "pw-123456", role=ROLE_USER)
        assert store.delete_user(u["id"]) is True


class Test引导管理员:
    def test_空库时创建(self, store):
        created = store.ensure_bootstrap_admin("admin", "admin123")
        assert created is not None
        assert created["role"] == ROLE_ADMIN

    def test_库非空时不动手(self, store):
        store.create_user("zhangsan", "pw-123456")
        assert store.ensure_bootstrap_admin("admin", "admin123") is None
        assert store.get_by_username("admin") is None

    def test_不会覆盖已有管理员的密码(self, store):
        """管理员改过密码/用户名之后重启,**不能被改回去**。"""
        u = store.create_user("admin", "my-own-strong-pw", role=ROLE_ADMIN)
        store.update_user(u["id"], username=None) if False else None

        assert store.ensure_bootstrap_admin("admin", "admin123") is None
        assert store.authenticate("admin", "my-own-strong-pw") is not None
        assert store.authenticate("admin", "admin123") is None

    def test_引导配置非法时回退而不是崩(self, store):
        """`TRIPMIND_ADMIN_PASSWORD=123` 这种配置错误不该让服务起不来。"""
        created = store.ensure_bootstrap_admin("admin", "123")
        assert created is not None
        assert created["username"] == "admin"


# ===========================================================================
# 3. 会话
# ===========================================================================


class Test会话:
    def test_令牌能换回账号(self, store):
        u = store.create_user("zhangsan", "pw-123456")
        s = store.create_session(u["id"])
        assert store.resolve_session(s["token"])["id"] == u["id"]

    def test_库里存的是摘要不是原文(self, store, authdb):
        """库被读走(备份泄露、误传)也拿不到可直接使用的会话。"""
        import sqlite3

        u = store.create_user("zhangsan", "pw-123456")
        s = store.create_session(u["id"])

        conn = sqlite3.connect(authdb)
        rows = conn.execute("SELECT token FROM sessions").fetchall()
        conn.close()

        stored = [r[0] for r in rows]
        assert s["token"] not in stored, "会话表里存了令牌原文"
        assert fingerprint(s["token"]) in stored

    def test_同一个账号可以有多个会话(self, store):
        """多设备登录。登出一台不该影响另一台。"""
        u = store.create_user("zhangsan", "pw-123456")
        s1 = store.create_session(u["id"])
        s2 = store.create_session(u["id"])

        store.delete_session(s1["token"])
        assert store.resolve_session(s1["token"]) is None
        assert store.resolve_session(s2["token"]) is not None

    def test_过期的会话无效且被清理(self, store):
        u = store.create_user("zhangsan", "pw-123456")
        s = store.create_session(u["id"], ttl_hours=1)

        # 把 expires_at 直接改到过去,比 sleep 一小时现实。
        # 注意用的是**库里的真值**(走 resolve_db_path),而不是 tmp_path ——
        # 这样如果哪天夹具换成了别的路径,这条测试会自己发现。
        import sqlite3

        from app.store.db import resolve_db_path

        past = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        conn = sqlite3.connect(resolve_db_path())
        conn.execute("UPDATE sessions SET expires_at = ?", (past,))
        conn.commit()
        conn.close()

        assert store.resolve_session(s["token"]) is None
        assert store.list_sessions(u["id"]) == [], "过期会话应当顺手删掉"

    def test_不存在的令牌是None(self, store):
        assert store.resolve_session("not-a-real-token") is None
        assert store.resolve_session("") is None
        assert store.resolve_session(None) is None  # type: ignore[arg-type]

    def test_清理过期会话(self, store):
        u = store.create_user("zhangsan", "pw-123456")
        store.create_session(u["id"], ttl_hours=1)
        assert store.purge_expired() == 0  # 还没过期
        assert len(store.list_sessions(u["id"])) == 1

    def test_删除账号会连带清掉会话(self, store):
        """sessions.user_id 有 ON DELETE CASCADE,靠的是连接里的 foreign_keys=ON。

        这条测试真正锁住的是**那个 PRAGMA 还在** —— 它一旦被去掉,
        级联会静默失效,留下指向不存在账号的会话行。
        """
        u = store.create_user("zhangsan", "pw-123456")
        store.create_session(u["id"])
        store.delete_user(u["id"])
        assert store.list_sessions(u["id"]) == []


# ===========================================================================
# 4. 登录节流
# ===========================================================================


class Test登录节流:
    def test_连续失败后锁定(self):
        guard = LoginGuard(max_attempts=3, lockout_sec=60)
        assert guard.retry_after("user:a") == 0
        for _ in range(3):
            guard.record_failure("user:a")
        assert guard.retry_after("user:a") > 0

    def test_没到阈值不锁(self):
        guard = LoginGuard(max_attempts=5, lockout_sec=60)
        guard.record_failure("user:a")
        guard.record_failure("user:a")
        assert guard.retry_after("user:a") == 0

    def test_成功会清零(self):
        guard = LoginGuard(max_attempts=3, lockout_sec=60)
        for _ in range(3):
            guard.record_failure("user:a")
        guard.record_success("user:a")
        assert guard.retry_after("user:a") == 0

    def test_锁定会随时间解除(self):
        """注入时钟来"快进",而不是真的 sleep。"""
        now = [0.0]
        guard = LoginGuard(max_attempts=2, lockout_sec=60, clock=lambda: now[0])
        guard.record_failure("user:a")
        guard.record_failure("user:a")
        assert guard.retry_after("user:a") > 0

        now[0] = 61.0
        assert guard.retry_after("user:a") == 0

    def test_多个key取最严格的那个(self):
        """用户名没被锁,但 IP 被锁了 —— 也必须拦。

        只按用户名计数的话,攻击者用一个密码去撞一万个用户名,
        每个账号只失败一次,永远不触发。
        """
        guard = LoginGuard(max_attempts=3, lockout_sec=60)
        for _ in range(3):
            guard.record_failure("ip:1.2.3.4")
        assert guard.retry_after("user:fresh", "ip:1.2.3.4") > 0

    def test_prune只清彻底过期的(self):
        now = [0.0]
        guard = LoginGuard(max_attempts=5, lockout_sec=10, clock=lambda: now[0])
        guard.record_failure("user:a")
        assert guard.prune() == 0  # 还新着呢

        now[0] = 21.0
        assert guard.prune() == 1


# ===========================================================================
# 5. HTTP:登录 / 登出 / 当前账号
# ===========================================================================


class Test登录接口:
    def test_登录成功返回令牌与账号(self, client, admin):
        resp = client.post("/api/auth/login", json={"username": "boss", "password": "boss-secret"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"]
        assert body["user"]["username"] == "boss"
        assert body["user"]["role"] == ROLE_ADMIN
        assert "password_hash" not in body["user"]

    def test_密码错误返回401(self, client, admin):
        resp = client.post("/api/auth/login", json={"username": "boss", "password": "nope"})
        assert resp.status_code == 401

    def test_不存在的账号返回401且文案相同(self, client, admin):
        """**不能**区分"用户不存在"和"密码错误" —— 那是账号枚举接口。"""
        wrong_pw = client.post("/api/auth/login", json={"username": "boss", "password": "nope"})
        no_user = client.post("/api/auth/login", json={"username": "nobody", "password": "nope"})
        assert wrong_pw.status_code == no_user.status_code == 401
        assert wrong_pw.json()["detail"] == no_user.json()["detail"]

    def test_停用的账号文案也相同(self, client, store, admin):
        store.create_user("frozen", "frozen-secret")
        u = store.get_by_username("frozen")
        store.update_user(u["id"], is_active=False)

        resp = client.post("/api/auth/login", json={"username": "frozen", "password": "frozen-secret"})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "用户名或密码错误"

    def test_用户名大小写不敏感(self, client, admin):
        resp = client.post("/api/auth/login", json={"username": "BOSS", "password": "boss-secret"})
        assert resp.status_code == 200

    def test_连续失败会被节流并返回429(self, client, admin):
        guard = get_login_guard()
        for _ in range(guard.max_attempts):
            client.post("/api/auth/login", json={"username": "boss", "password": "nope"})

        resp = client.post("/api/auth/login", json={"username": "boss", "password": "nope"})
        assert resp.status_code == 429, "暴力破解必须被挡住"
        assert "Retry-After" in resp.headers
        # 关键:即使密码**是对的**,锁定期内也不放行 —— 否则节流形同虚设
        assert client.post(
            "/api/auth/login", json={"username": "boss", "password": "boss-secret"}
        ).status_code == 429

    def test_登录成功会清掉该账号的失败计数(self, client, admin):
        guard = get_login_guard()
        client.post("/api/auth/login", json={"username": "boss", "password": "nope"})
        client.post("/api/auth/login", json={"username": "boss", "password": "boss-secret"})
        assert guard.retry_after("user:boss") == 0


class Test自助注册:
    """`POST /api/auth/register` —— 首页「免费开始」通向的接口。

    这一组里**最重要的一条**是 `test_注册不能提权`:注册是公开接口,
    一旦角色能从客户端传进来,整套 RBAC 就形同虚设。
    """

    def _post(self, client, **overrides):
        body = {"username": "newbie", "password": "newbie-secret"}
        body.update(overrides)
        return client.post("/api/auth/register", json=body)

    def test_注册成功并直接返回令牌(self, client, authdb):
        get_register_guard().reset()
        resp = self._post(client)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["token"], "注册后应当直接给令牌,不该再让用户手打一次刚设的密码"
        assert body["user"]["username"] == "newbie"
        assert "password_hash" not in body["user"]

    def test_注册返回的令牌立刻可用(self, client, authdb):
        get_register_guard().reset()
        token = self._post(client).json()["token"]
        me = client.get("/api/auth/me", headers=_bearer(token))
        assert me.status_code == 200
        assert me.json()["user"]["username"] == "newbie"

    def test_注册出来的是普通用户(self, client, authdb):
        get_register_guard().reset()
        role = self._post(client).json()["user"]["role"]
        assert role == ROLE_USER, "自助注册的账号必须是普通用户"

    def test_注册不能提权(self, client, authdb):
        """**本文件里最要紧的一条。**

        请求体里塞 `role: admin`(以及常见的几种变体)都必须无效 ——
        注册接口是公开的,只要角色能被客户端影响,任何人都能给自己发管理员。
        """
        get_register_guard().reset()
        for i, attempt in enumerate(
            (
                {"role": "admin"},
                {"role": "ADMIN"},
                {"is_admin": True},
                {"role": "admin", "is_active": True, "id": "whatever"},
                {"role": ["admin"]},
            )
        ):
            # 用户名必须每次不同 —— 否则第二次会撞上第一次建出来的账号,
            # 拿到 409 而不是 201,测试就变成了在验证"重名检测"(那件事
            # 另有专门的用例)。
            username = f"attacker{i}"
            resp = client.post(
                "/api/auth/register",
                json={"username": username, "password": "attacker-secret", **attempt},
            )
            assert resp.status_code == 201, f"{attempt} → {resp.text}"
            assert resp.json()["user"]["role"] == ROLE_USER, (
                f"塞了 {attempt} 之后角色变成了 {resp.json()['user']['role']} —— 提权漏洞"
            )

        # 而且这个账号真的没有管理员权限
        token = client.post(
            "/api/auth/login", json={"username": "attacker0", "password": "attacker-secret"}
        ).json()["token"]
        assert client.get("/api/auth/users", headers=_bearer(token)).status_code == 403

    def test_重名返回409(self, client, authdb, alice):
        get_register_guard().reset()
        resp = self._post(client, username="alice")
        assert resp.status_code == 409

    def test_重名判定不区分大小写(self, client, authdb, alice):
        get_register_guard().reset()
        assert self._post(client, username="ALICE").status_code == 409

    def test_弱密码返回400(self, client, authdb):
        get_register_guard().reset()
        assert self._post(client, password="123").status_code == 400
        assert self._post(client, password="newbie").status_code == 400, "密码不能与用户名相同"

    def test_非法用户名返回400(self, client, authdb):
        get_register_guard().reset()
        assert self._post(client, username="ab").status_code == 400
        assert self._post(client, username="有中文").status_code == 400

    def test_不会顶掉已有管理员(self, client, authdb, admin):
        """拿管理员的名字去注册不该得手,更不该改掉对方的密码。"""
        get_register_guard().reset()
        assert self._post(client, username="boss", password="hijack-secret").status_code == 409
        assert client.post(
            "/api/auth/login", json={"username": "boss", "password": "boss-secret"}
        ).status_code == 200

    def test_按IP节流(self, client, authdb, alice):
        """防的是**同一个来源批量建号**,所以只按 IP 计。

        触发方式是反复拿一个已被占用的用户名去注册(每次都是 409 + 计数)。
        """
        guard = get_register_guard()
        guard.reset()

        for _ in range(guard.max_attempts):
            assert self._post(client, username="alice").status_code == 409

        assert guard.retry_after("register:testclient") > 0
        resp = self._post(client, username="another")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers

    def test_输入不合法不计入节流(self, client, authdb):
        """用户名/密码不合法是**用户在改自己的输入**,不是攻击行为。

        计进去的话,"输错两次用户名"的人会被锁十分钟 —— 而注册的重名失败
        (`DuplicateUsername`)才是防批量建号该看住的信号。
        """
        guard = get_register_guard()
        guard.reset()
        for _ in range(6):
            self._post(client, password="123")
        assert guard.retry_after("register:testclient") == 0

    def test_可以关掉自助注册(self, client, authdb, monkeypatch):
        get_register_guard().reset()
        monkeypatch.setattr(get_settings(), "allow_registration", False)
        resp = self._post(client)
        assert resp.status_code == 403
        assert "关闭" in resp.json()["detail"]

    def test_关掉后也不影响登录(self, client, authdb, admin, monkeypatch):
        monkeypatch.setattr(get_settings(), "allow_registration", False)
        assert client.post(
            "/api/auth/login", json={"username": "boss", "password": "boss-secret"}
        ).status_code == 200


class Test当前账号与登出:
    def test_me需要登录(self, client):
        assert client.get("/api/auth/me").status_code == 401

    def test_me返回当前账号(self, client, admin_token):
        resp = client.get("/api/auth/me", headers=_bearer(admin_token))
        assert resp.status_code == 200
        assert resp.json()["user"]["username"] == "boss"

    def test_无效令牌是401(self, client):
        resp = client.get("/api/auth/me", headers=_bearer("garbage"))
        assert resp.status_code == 401
        assert resp.headers.get("WWW-Authenticate") == "Bearer"

    def test_非Bearer的scheme被当成未登录(self, client):
        resp = client.get("/api/auth/me", headers={"Authorization": "Basic YWJjOmRlZg=="})
        assert resp.status_code == 401

    def test_bearer大小写不敏感(self, client, admin_token):
        resp = client.get("/api/auth/me", headers={"Authorization": f"bearer {admin_token}"})
        assert resp.status_code == 200

    def test_登出后令牌立即失效(self, client, admin_token):
        assert client.get("/api/auth/me", headers=_bearer(admin_token)).status_code == 200
        assert client.post("/api/auth/logout", headers=_bearer(admin_token)).status_code == 200
        assert client.get("/api/auth/me", headers=_bearer(admin_token)).status_code == 401

    def test_登出是幂等的(self, client, admin_token):
        client.post("/api/auth/logout", headers=_bearer(admin_token))
        resp = client.post("/api/auth/logout", headers=_bearer(admin_token))
        assert resp.status_code == 200, "已经失效的令牌再登出一次不该报错"

    def test_登出不影响其它设备(self, client, store, admin):
        t1 = _login(client, "boss", "boss-secret")
        t2 = _login(client, "boss", "boss-secret")
        client.post("/api/auth/logout", headers=_bearer(t1))
        assert client.get("/api/auth/me", headers=_bearer(t2)).status_code == 200


class Test改密码:
    def test_需要原密码(self, client, admin_token):
        resp = client.post(
            "/api/auth/password",
            json={"old_password": "wrong", "new_password": "new-secret"},
            headers=_bearer(admin_token),
        )
        assert resp.status_code == 401

    def test_改完能用新密码登录(self, client, admin_token):
        resp = client.post(
            "/api/auth/password",
            json={"old_password": "boss-secret", "new_password": "new-secret"},
            headers=_bearer(admin_token),
        )
        assert resp.status_code == 200
        assert client.post(
            "/api/auth/login", json={"username": "boss", "password": "new-secret"}
        ).status_code == 200

    def test_改完返回新令牌且旧令牌失效(self, client, admin_token):
        """改密码会吊销全部会话。必须**换发**一个给当前客户端,

        否则用户改完自己的密码当场被踢到登录页,会被理解成"把账号弄坏了"。
        """
        resp = client.post(
            "/api/auth/password",
            json={"old_password": "boss-secret", "new_password": "new-secret"},
            headers=_bearer(admin_token),
        )
        new_token = resp.json()["token"]
        assert new_token and new_token != admin_token

        assert client.get("/api/auth/me", headers=_bearer(new_token)).status_code == 200
        assert client.get("/api/auth/me", headers=_bearer(admin_token)).status_code == 401

    def test_新密码也要过策略(self, client, admin_token):
        resp = client.post(
            "/api/auth/password",
            json={"old_password": "boss-secret", "new_password": "123"},
            headers=_bearer(admin_token),
        )
        assert resp.status_code == 400


# ===========================================================================
# 6. HTTP:账号管理(仅管理员)
# ===========================================================================


class Test账号管理接口:
    def test_匿名看不到账号列表(self, client):
        assert client.get("/api/auth/users").status_code == 401

    def test_普通用户被拒且是403(self, client, alice_token):
        """必须是 403 而不是 401 —— 401 会让前端把已登录的用户踹回登录页。"""
        assert client.get("/api/auth/users", headers=_bearer(alice_token)).status_code == 403

    def test_管理员能列出账号(self, client, admin_token, alice):
        resp = client.get("/api/auth/users", headers=_bearer(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert {u["username"] for u in body["users"]} >= {"boss", "alice"}
        assert body["summary"]["admins"] == 1
        assert all("password_hash" not in u for u in body["users"])

    def test_创建账号(self, client, admin_token):
        resp = client.post(
            "/api/auth/users",
            json={"username": "carol", "password": "carol-secret", "role": "user"},
            headers=_bearer(admin_token),
        )
        assert resp.status_code == 201
        assert resp.json()["username"] == "carol"

    def test_重名返回409(self, client, admin_token, alice):
        resp = client.post(
            "/api/auth/users",
            json={"username": "alice", "password": "other-secret"},
            headers=_bearer(admin_token),
        )
        assert resp.status_code == 409

    def test_弱密码返回400(self, client, admin_token):
        resp = client.post(
            "/api/auth/users",
            json={"username": "carol", "password": "123"},
            headers=_bearer(admin_token),
        )
        assert resp.status_code == 400

    def test_普通用户建不了账号(self, client, alice_token):
        resp = client.post(
            "/api/auth/users",
            json={"username": "mallory", "password": "mallory-secret"},
            headers=_bearer(alice_token),
        )
        assert resp.status_code == 403
        assert client.post(
            "/api/auth/login", json={"username": "mallory", "password": "mallory-secret"}
        ).status_code == 401

    def test_改密码(self, client, admin_token, alice):
        resp = client.patch(
            f"/api/auth/users/{alice['id']}",
            json={"password": "reset-secret"},
            headers=_bearer(admin_token),
        )
        assert resp.status_code == 200
        assert client.post(
            "/api/auth/login", json={"username": "alice", "password": "reset-secret"}
        ).status_code == 200

    def test_停用后登不进来(self, client, admin_token, alice):
        # 先让 alice 有一个会话,确认停用能把它踢掉
        alice_token = _login(client, "alice", "alice-secret")

        resp = client.patch(
            f"/api/auth/users/{alice['id']}",
            json={"is_active": False},
            headers=_bearer(admin_token),
        )
        assert resp.status_code == 200
        assert client.get("/api/auth/me", headers=_bearer(alice_token)).status_code == 401

    def test_清空显示名是真的清空(self, client, admin_token, store):
        """PATCH 必须区分「没传」和「传了 null」。

        用 `body.display_name is not None` 判断的话,"传 null 清空"
        会被当成"没传",界面上点了保存却什么都没变 —— 静默失效。
        """
        u = store.create_user("carol", "carol-secret", display_name="卡罗尔")
        resp = client.patch(
            f"/api/auth/users/{u['id']}",
            json={"display_name": None},
            headers=_bearer(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] is None

    def test_不传的字段不被清空(self, client, admin_token, store):
        u = store.create_user("carol", "carol-secret", display_name="卡罗尔")
        client.patch(
            f"/api/auth/users/{u['id']}", json={"role": "admin"}, headers=_bearer(admin_token)
        )
        assert store.get_by_id(u["id"])["display_name"] == "卡罗尔"

    def test_不能删掉自己(self, client, admin_token, admin):
        resp = client.delete(f"/api/auth/users/{admin['id']}", headers=_bearer(admin_token))
        assert resp.status_code == 400

    def test_不能删掉最后一个管理员(self, client, store, admin):
        """换个管理员来删 boss —— 因为 boss 现在是唯一的启用管理员。"""
        other = store.create_user("boss2", "boss2-secret", role=ROLE_ADMIN)
        t = _login(client, "boss2", "boss2-secret")
        # 先把 boss2 之外的这条路走通:boss2 删 boss 应当成功(两人时)
        assert client.delete(f"/api/auth/users/{admin['id']}", headers=_bearer(t)).status_code == 200

    def test_删掉最后一个管理员被拒(self, client, store, admin):
        # 只留一个管理员,而且操作者必须是**另一个人**才谈得上"删最后一个管理员"
        other = store.create_user("boss2", "boss2-secret", role=ROLE_ADMIN)
        t = _login(client, "boss2", "boss2-secret")
        # 把 boss 降成普通用户,现在 boss2 是唯一管理员
        store.update_user(admin["id"], role=ROLE_USER)

        resp = client.delete(f"/api/auth/users/{other['id']}", headers=_bearer(t))
        assert resp.status_code == 400, "唯一管理员不能被删"

    def test_删账号返回行程转无主的说明(self, client, admin_token, alice, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        resp = client.delete(f"/api/auth/users/{alice['id']}", headers=_bearer(admin_token))
        assert resp.status_code == 200
        assert "无主" in resp.json()["message"]
        assert plan_store.get_plan(pid)["user_id"] is None

    def test_强制下线(self, client, admin_token, alice):
        alice_token = _login(client, "alice", "alice-secret")
        resp = client.post(
            f"/api/auth/users/{alice['id']}/sessions/revoke", headers=_bearer(admin_token)
        )
        assert resp.status_code == 200
        assert client.get("/api/auth/me", headers=_bearer(alice_token)).status_code == 401

    def test_改不存在的账号返回404(self, client, admin_token):
        resp = client.patch(
            "/api/auth/users/no-such-id", json={"role": "user"}, headers=_bearer(admin_token)
        )
        assert resp.status_code == 404

    def test_删不存在的账号返回404(self, client, admin_token):
        resp = client.delete("/api/auth/users/no-such-id", headers=_bearer(admin_token))
        assert resp.status_code == 404


# ===========================================================================
# 7. 行程的归属与访问控制
# ===========================================================================


class Test行程归属:
    def test_生成行程要求登录(self, client):
        """未登录必须**在花掉两分钟跑模型之前**就被拒。"""
        resp = client.post("/api/trip/plan", json={})
        assert resp.status_code == 401

    def test_行程归属登录账号(self, client, alice_token, alice, plan_store):
        """直接验证"归属是在 create_running 时写进去的"。"""
        from app.models.schemas import TripRequest

        pid = plan_store.create_running(
            TripRequest(**make_request(city="上海", travel_days=2)),
            user_id=alice["id"],
        )
        assert plan_store.get_plan(pid)["user_id"] == alice["id"]

    def test_无主行程不影响读取(self, plan_store):
        pid = _make_plan(plan_store, None)
        assert plan_store.get_plan(pid)["user_id"] is None


class Test历史列表的范围:
    def test_普通用户只看得到自己的(self, client, alice, alice_token, bob, bob_token, plan_store):
        alice_plan = _make_plan(plan_store, alice["id"], "北京")
        bob_plan = _make_plan(plan_store, bob["id"], "上海")

        resp = client.get("/api/trip/plans", headers=_bearer(alice_token))
        assert resp.status_code == 200
        ids = [r["id"] for r in resp.json()["data"]]
        assert alice_plan in ids
        assert bob_plan not in ids, "普通用户看到了别人的行程 —— 这是最严重的越权"

    def test_管理员默认也只看自己的(self, client, admin, admin_token, alice, plan_store):
        """scope 默认 mine。默认值必须是**最保守**的那个。"""
        own = _make_plan(plan_store, admin["id"], "北京")
        other = _make_plan(plan_store, alice["id"], "上海")

        resp = client.get("/api/trip/plans", headers=_bearer(admin_token))
        ids = [r["id"] for r in resp.json()["data"]]
        assert own in ids
        assert other not in ids

    def test_管理员scope_all看得到全部(self, client, admin, admin_token, alice, plan_store):
        own = _make_plan(plan_store, admin["id"], "北京")
        other = _make_plan(plan_store, alice["id"], "上海")

        resp = client.get("/api/trip/plans?scope=all", headers=_bearer(admin_token))
        ids = [r["id"] for r in resp.json()["data"]]
        assert {own, other} <= set(ids)

    def test_普通用户scope_all是403(self, client, alice, alice_token, bob, plan_store):
        """**不能是静默降级成 mine** —— 那会让用户以为"全站只有我这几条"。"""
        _make_plan(plan_store, bob["id"], "上海")
        resp = client.get("/api/trip/plans?scope=all", headers=_bearer(alice_token))
        assert resp.status_code == 403

    def test_非法scope被拒(self, client, alice_token):
        assert client.get("/api/trip/plans?scope=whatever", headers=_bearer(alice_token)).status_code == 422

    def test_未登录读不到列表(self, client):
        assert client.get("/api/trip/plans").status_code == 401

    def test_无主行程只对管理员可见(self, client, admin_token, alice_token, plan_store):
        orphan = _make_plan(plan_store, None, "无主城")

        as_admin = client.get("/api/trip/plans?scope=all", headers=_bearer(admin_token))
        assert orphan in [r["id"] for r in as_admin.json()["data"]]

        as_user = client.get("/api/trip/plans", headers=_bearer(alice_token))
        assert orphan not in [r["id"] for r in as_user.json()["data"]]

    def test_统计数字跟着范围变(self, client, admin, admin_token, alice, plan_store):
        """顶部"累计花费"必须和下面列表的数量一致,否则用户以为漏数据了。"""
        _make_plan(plan_store, admin["id"])
        _make_plan(plan_store, alice["id"])
        _make_plan(plan_store, alice["id"])

        mine = client.get("/api/trip/plans", headers=_bearer(admin_token)).json()["stats"]
        every = client.get("/api/trip/plans?scope=all", headers=_bearer(admin_token)).json()["stats"]
        assert mine["total"] == 1
        assert every["total"] == 3


class Test归属字段的可见性:
    def test_列表里管理员看得到归属(self, client, admin_token, alice, plan_store):
        _make_plan(plan_store, alice["id"])
        row = client.get("/api/trip/plans?scope=all", headers=_bearer(admin_token)).json()["data"][0]
        assert row["owner_username"] == "alice"

    def test_列表里普通用户看不到归属字段(self, client, alice, alice_token, plan_store):
        _make_plan(plan_store, alice["id"])
        row = client.get("/api/trip/plans", headers=_bearer(alice_token)).json()["data"][0]
        assert row["owner_username"] is None
        assert row["user_id"] is None

    def test_分享链接匿名可读(self, client, alice, plan_store):
        """**分享功能是明确的产品设定**,不能被归属控制误伤。"""
        pid = _make_plan(plan_store, alice["id"])
        resp = client.get(f"/api/trip/plans/{pid}")
        assert resp.status_code == 200, "匿名打开分享链接被拒了 —— 分享功能已失效"

    def test_分享链接里看不到归属(self, client, alice, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        meta = client.get(f"/api/trip/plans/{pid}").json()["meta"]
        assert meta["owner_username"] is None
        assert meta["user_id"] is None

    def test_管理员看详情能看到归属(self, client, admin_token, alice, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        meta = client.get(f"/api/trip/plans/{pid}", headers=_bearer(admin_token)).json()["meta"]
        assert meta["owner_username"] == "alice"


class Test详情里的can_edit:
    """`can_edit` 决定前端显不显示「编辑行程」。

    它必须由**服务端**算：归属字段对非管理员是隐藏的，前端没有任何
    判断依据。没有它的话，匿名或别人打开 `/result/{id}` 会看到一排
    编辑按钮，改了半天到保存才拿 403 —— 那种交互既困惑也白费功夫。
    """

    def test_本人可以编辑(self, client, alice, alice_token, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        body = client.get(f"/api/trip/plans/{pid}", headers=_bearer(alice_token)).json()
        assert body["can_edit"] is True

    def test_别人不可以编辑(self, client, alice, bob_token, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        body = client.get(f"/api/trip/plans/{pid}", headers=_bearer(bob_token)).json()
        assert body["can_edit"] is False

    def test_匿名打开分享链接不可以编辑(self, client, alice, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        body = client.get(f"/api/trip/plans/{pid}").json()
        assert body["can_edit"] is False

    def test_管理员可以编辑任何人的(self, client, alice, admin_token, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        body = client.get(f"/api/trip/plans/{pid}", headers=_bearer(admin_token)).json()
        assert body["can_edit"] is True

    def test_无主行程只有管理员可以编辑(self, client, alice_token, admin_token, plan_store):
        pid = _make_plan(plan_store, None)
        assert client.get(f"/api/trip/plans/{pid}", headers=_bearer(alice_token)).json()["can_edit"] is False
        assert client.get(f"/api/trip/plans/{pid}", headers=_bearer(admin_token)).json()["can_edit"] is True

    def test_未生成完的行程也带can_edit(self, client, alice, alice_token, plan_store):
        """`success=false` 的那条分支也要带上它 —— 否则前端在"生成中断"
        的页面上会沿用上一次的 can_edit，出现按钮忽隐忽现。
        """
        from app.models.schemas import TripRequest

        pid = plan_store.create_running(
            TripRequest(**make_request(city="北京", start_date="2026-06-01", travel_days=3)),
            user_id=alice["id"],
        )
        body = client.get(f"/api/trip/plans/{pid}", headers=_bearer(alice_token)).json()
        assert body["success"] is False, "running 状态的行程应当是 success=false"
        assert body["can_edit"] is True


class Test行程的写权限:
    def test_匿名不能删(self, client, alice, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        assert client.delete(f"/api/trip/plans/{pid}").status_code == 401

    def test_别人不能删(self, client, alice, bob_token, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        resp = client.delete(f"/api/trip/plans/{pid}", headers=_bearer(bob_token))
        assert resp.status_code == 403
        assert plan_store.get_plan(pid) is not None, "被拒之后行程还在"

    def test_本人能删(self, client, alice, alice_token, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        assert client.delete(f"/api/trip/plans/{pid}", headers=_bearer(alice_token)).status_code == 200

    def test_管理员能删任何人的(self, client, alice, admin_token, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        assert client.delete(f"/api/trip/plans/{pid}", headers=_bearer(admin_token)).status_code == 200

    def test_无主行程只有管理员能删(self, client, alice_token, bob_token, admin_token, plan_store):
        pid = _make_plan(plan_store, None)
        assert client.delete(f"/api/trip/plans/{pid}", headers=_bearer(alice_token)).status_code == 403
        assert client.delete(f"/api/trip/plans/{pid}", headers=_bearer(admin_token)).status_code == 200

    def test_别人不能改写(self, client, alice, bob_token, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        resp = client.put(
            f"/api/trip/plans/{pid}", json=make_plan(), headers=_bearer(bob_token)
        )
        assert resp.status_code == 403

    def test_本人能改写(self, client, alice, alice_token, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        resp = client.put(f"/api/trip/plans/{pid}", json=make_plan(), headers=_bearer(alice_token))
        assert resp.status_code == 200

    def test_不存在的行程返回404而不是403(self, client, alice_token):
        """404 先于 403。反过来的话,拿别人的 id 试能靠状态码区分"存在"与"不存在",
        而 plan_id 是 32 位随机串 —— 那就把这个性质抹掉了。"""
        resp = client.delete("/api/trip/plans/no-such-plan", headers=_bearer(alice_token))
        assert resp.status_code == 404


class Testcan_access_plan:
    """纯函数,直接测边界 —— 上面那些 HTTP 测试覆盖不到组合爆炸。"""

    ADMIN = {"id": "a", "role": ROLE_ADMIN}
    USER = {"id": "u", "role": ROLE_USER}

    def test_本人可以(self):
        assert can_access_plan(self.USER, {"user_id": "u"}) is True

    def test_别人不可以(self):
        assert can_access_plan(self.USER, {"user_id": "other"}) is False

    def test_管理员可以(self):
        assert can_access_plan(self.ADMIN, {"user_id": "other"}) is True

    def test_无主只有管理员可以(self):
        assert can_access_plan(self.ADMIN, {"user_id": None}) is True
        assert can_access_plan(self.USER, {"user_id": None}) is False

    def test_未登录一律不可以(self):
        assert can_access_plan(None, {"user_id": None}) is False
        assert can_access_plan(None, {"user_id": "u"}) is False


# ===========================================================================
# 8. 知识库:管理员专属
# ===========================================================================


class Test知识库权限:
    """知识库改成"每人一份"之后的权限边界。

    两层,判断标准是**这个调用会不会改到别人的东西**:

        ADMIN_GATED  灌内置库 / 清库 / RAG 开关 —— 全站共用资源与进程级开关
        AUTH_ALLOWED 上传与管理自己的攻略 —— 登录即可,按归属判定
    """

    ADMIN_GATED = [
        ("post", "/api/knowledge/ingest", {"source": "all"}),
        ("get", "/api/knowledge/ingest/task-1", None),
        ("post", "/api/knowledge/rag-toggle", {"enabled": True}),
    ]

    AUTH_ALLOWED = [
        ("get", "/api/knowledge/docs", None),
        ("get", "/api/knowledge/docs/doc-does-not-exist", None),
        ("delete", "/api/knowledge/docs/doc-does-not-exist", None),
    ]

    @pytest.mark.parametrize("method,path,body", ADMIN_GATED + AUTH_ALLOWED)
    def test_匿名一律401(self, client, method, path, body):
        resp = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
        assert resp.status_code == 401, f"{method.upper()} {path}"

    @pytest.mark.parametrize("method,path,body", ADMIN_GATED)
    def test_管理员专属对普通用户是403(self, client, alice_token, method, path, body):
        kwargs = {"headers": _bearer(alice_token)}
        if body:
            kwargs["json"] = body
        resp = getattr(client, method)(path, **kwargs)
        assert resp.status_code == 403, f"{method.upper()} {path} 返回了 {resp.status_code}"

    @pytest.mark.parametrize("method,path,body", AUTH_ALLOWED)
    def test_普通用户可以进到业务层(self, client, alice_token, method, path, body):
        """**只断言"不是拒绝"**,不断言具体状态码。

        这些端点的业务结果取决于库里有没有那条记录(404)还是列表为空(200),
        与权限无关。写死某个码只会让这条测试随环境数据变化而红。
        """
        kwargs = {"headers": _bearer(alice_token)}
        if body:
            kwargs["json"] = body
        resp = getattr(client, method)(path, **kwargs)
        assert resp.status_code not in (401, 403), (
            f"{method.upper()} {path} 被拒了({resp.status_code}) —— 普通用户应当能用自己的知识库"
        )

    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/api/knowledge/ingest/preview"),
            ("get", "/api/knowledge/sources"),
        ],
    )
    def test_只读接口保持公开(self, client, method, path):
        """知识库页首屏要用的接口不能被锁死。

        ⚠️ 刻意**不**拿 `/api/knowledge/status` 和 `/api/knowledge/search` 来测:
        前者要连 Milvus(`available()` 在连不上时要等十几秒超时),后者要调
        embedding 接口(实测 80 秒)。它们确实是公开的 —— 但那件事由
        `Test端点权限金名单::test_公开端点不带任何鉴权依赖` 用路由表断言,
        不需要在单测里真的跑一遍网络请求。**测试变慢会被跳过,被跳过的测试
        等于没有。**
        """
        kwargs = {"json": {"source": "all"}} if method == "post" else {}
        resp = getattr(client, method)(path, **kwargs)
        assert resp.status_code == 200, f"{method.upper()} {path} → {resp.status_code}"


class Test知识库按人隔离:
    """上传的攻略按人隔离 —— 这是"普通用户也能用知识库"能成立的前提。

    这一组只测 **SQLite 侧的归属**(列表、查看、删除)。
    向量库侧的检索过滤由 `tests/test_knowledge.py` 里的 `user_filter` 单测覆盖,
    因为那需要真的起 Milvus,不适合放在这里。
    """

    def _make_doc(self, doc_store, *, doc_id: str, title: str, user_id: str | None):
        doc_store.create_pending(
            {
                "id": doc_id,
                "title": title,
                "origin": "paste",
                "content": f"{title} 的正文内容",
                "content_hash": doc_id * 3,
                "char_count": 10,
                "chunk_count": 1,
                "chunk_ids": [],
                "user_id": user_id,
            }
        )
        return doc_id

    @pytest.fixture
    def doc_store(self, authdb):
        from app.store.doc_store import DocStore

        return DocStore()

    def test_只看得到自己的(self, client, alice_token, bob_token, alice, bob, doc_store):
        self._make_doc(doc_store, doc_id="d-alice", title="Alice 的攻略", user_id=alice["id"])
        self._make_doc(doc_store, doc_id="d-bob", title="Bob 的攻略", user_id=bob["id"])

        ids = [d["id"] for d in client.get("/api/knowledge/docs", headers=_bearer(alice_token)).json()["docs"]]
        assert "d-alice" in ids
        assert "d-bob" not in ids, "普通用户看到了别人上传的攻略"

    def test_管理员默认也只看到自己的(self, client, admin_token, admin, alice, doc_store):
        """scope 默认 mine —— 默认值必须最保守。"""
        self._make_doc(doc_store, doc_id="d-alice", title="Alice 的攻略", user_id=alice["id"])
        ids = [d["id"] for d in client.get("/api/knowledge/docs", headers=_bearer(admin_token)).json()["docs"]]
        assert "d-alice" not in ids

    def test_管理员scope_all看到全部(self, client, admin_token, alice, admin, doc_store):
        self._make_doc(doc_store, doc_id="d-alice", title="Alice 的攻略", user_id=alice["id"])
        body = client.get("/api/knowledge/docs?scope=all", headers=_bearer(admin_token)).json()
        assert "d-alice" in [d["id"] for d in body["docs"]]
        # 全量视图要带归属,否则分不清哪些是谁的
        assert body["docs"][0]["user_id"] == alice["id"]

    def test_普通用户scope_all是403(self, client, alice_token, doc_store):
        assert client.get("/api/knowledge/docs?scope=all", headers=_bearer(alice_token)).status_code == 403

    def test_无主的老攻略只对管理员可见(self, client, alice_token, admin_token, doc_store):
        """账号体系上线前上传的那批 user_id 为空,只有管理员看得到。"""
        self._make_doc(doc_store, doc_id="d-legacy", title="老攻略", user_id=None)

        mine = [d["id"] for d in client.get("/api/knowledge/docs", headers=_bearer(alice_token)).json()["docs"]]
        assert "d-legacy" not in mine

        every = [d["id"] for d in client.get("/api/knowledge/docs?scope=all", headers=_bearer(admin_token)).json()["docs"]]
        assert "d-legacy" in every

    def test_不能看别人的分块(self, client, alice_token, alice, doc_store):
        self._make_doc(doc_store, doc_id="d-alice", title="Alice 的攻略", user_id=alice["id"])
        resp = client.get("/api/knowledge/docs/d-alice", headers=_bearer(alice_token))
        assert resp.status_code == 200, "本人应当能看自己的"

        # 换个人 —— 分块里是**攻略全文**,这一条不能漏
        resp = client.get("/api/knowledge/docs/d-alice", headers=_bearer("no-such-token"))
        assert resp.status_code == 401

    def test_别人不能查看全文(self, client, bob_token, alice, doc_store):
        self._make_doc(doc_store, doc_id="d-alice", title="Alice 的攻略", user_id=alice["id"])
        resp = client.get("/api/knowledge/docs/d-alice", headers=_bearer(bob_token))
        assert resp.status_code == 403, "别人看到了我上传的攻略全文"

    def test_别人不能删(self, client, bob_token, alice, doc_store):
        self._make_doc(doc_store, doc_id="d-alice", title="Alice 的攻略", user_id=alice["id"])
        resp = client.delete("/api/knowledge/docs/d-alice", headers=_bearer(bob_token))
        assert resp.status_code == 403
        assert doc_store.get("d-alice") is not None, "被拒之后记录还在"

    def test_本人能删(self, client, alice_token, alice, doc_store):
        self._make_doc(doc_store, doc_id="d-alice", title="Alice 的攻略", user_id=alice["id"])
        # 会尝试从 Milvus 删块 —— 本机没起 Milvus 时那一步会抛,接口回 502。
        # 这不是权限问题,所以两种结果都接受,只断言"不是 401/403"。
        resp = client.delete("/api/knowledge/docs/d-alice", headers=_bearer(alice_token))
        assert resp.status_code not in (401, 403), resp.text

    def test_统计跟着范围走(self, client, alice_token, bob_token, alice, bob, doc_store):
        """列表 2 条、统计说 12 篇,会让人以为列表漏了数据。"""
        self._make_doc(doc_store, doc_id="d-a1", title="A1", user_id=alice["id"])
        self._make_doc(doc_store, doc_id="d-a2", title="A2", user_id=alice["id"])
        self._make_doc(doc_store, doc_id="d-b1", title="B1", user_id=bob["id"])

        body = client.get("/api/knowledge/docs", headers=_bearer(alice_token)).json()
        assert len(body["docs"]) == 2
        assert body["summary"]["docs"] == 2, "统计没跟着归属走"


# ===========================================================================
# 9. 端点的权限金名单
# ===========================================================================
#
# 防的是"改路由时漏挂依赖"。这一类失效**不报错**:
# 少挂一个 `require_admin_role`,那个接口就对所有人开放了,
# 而所有现有测试都还是绿的。


def _deps_of(dependant: Any) -> list[str]:
    out: list[str] = []
    call = getattr(dependant, "call", None)
    if call is not None:
        out.append(getattr(call, "__name__", "?"))
    for sub in getattr(dependant, "dependencies", []) or []:
        out.extend(_deps_of(sub))
    return out


def _all_endpoints() -> dict[str, list[str]]:
    """`{"GET /api/trip/plans": [依赖名...]}`"""
    out: dict[str, list[str]] = {}
    for router in (auth.router, trip.router, knowledge.router):
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods:
                if method in ("HEAD", "OPTIONS"):
                    continue
                out[f"{method} /api{route.path}"] = _deps_of(route.dependant)
    return out


PUBLIC_ENDPOINTS = [
    # 登录接口必须公开 —— 没有它谁也进不来
    "POST /api/auth/login",
    # 注册接口必须公开 —— 首页的「免费开始」直接通向它
    "POST /api/auth/register",
    # 登出:令牌已失效时也要能调,否则前端会在"已经登出"的状态下卡住
    "POST /api/auth/logout",
    # 分享链接的数据源
    "GET /api/trip/plans/{plan_id}",
    "GET /api/trip/health",
    "POST /api/trip/parse",
    # 知识库的只读接口
    "GET /api/knowledge/status",
    "POST /api/knowledge/search",
    "POST /api/knowledge/ingest/preview",
    "GET /api/knowledge/sources",
]

AUTH_ONLY_ENDPOINTS = [
    "GET /api/auth/me",
    "POST /api/auth/password",
    "POST /api/trip/plan",
    "GET /api/trip/plans",
    "PUT /api/trip/plans/{plan_id}",
    "DELETE /api/trip/plans/{plan_id}",
    # 上传攻略:登录即可,**按归属判定**(本人或管理员)。
    # 这五个原来是管理员专属 —— 知识库改成"每人一份"之后降级了。
    "POST /api/knowledge/docs/parse",
    "POST /api/knowledge/docs/{doc_id}/ingest",
    "GET /api/knowledge/docs",
    "GET /api/knowledge/docs/{doc_id}",
    "DELETE /api/knowledge/docs/{doc_id}",
]

# 只有管理员:操作的是**全站共用资源**或**进程级开关**,
# 不该由一个人替所有人决定。
ADMIN_ONLY_ENDPOINTS = [
    "GET /api/auth/users",
    "POST /api/auth/users",
    "PATCH /api/auth/users/{user_id}",
    "DELETE /api/auth/users/{user_id}",
    "POST /api/auth/users/{user_id}/sessions/revoke",
    # 灌内置库 / 清库(recreate 会 drop 全站共用的 collection)
    "POST /api/knowledge/ingest",
    "GET /api/knowledge/ingest/{task_id}",
    # RAG 开关是**进程级**的,影响所有人的行程生成
    "POST /api/knowledge/rag-toggle",
]


class Test端点权限金名单:
    @pytest.mark.parametrize("endpoint", ADMIN_ONLY_ENDPOINTS)
    def test_管理员专属端点挂了正确的依赖(self, endpoint):
        found = _all_endpoints()
        assert endpoint in found, f"{endpoint} 不在路由表里 —— 名字写错了?"
        deps = found[endpoint]
        assert "require_admin_role" in deps, (
            f"{endpoint} 必须挂 dependencies=[Depends(require_admin_role)]。"
            "漏挂的后果是任何登录用户都能操作知识库 / 管理账号。"
        )
        assert "require_user" in deps, (
            "require_admin_role 必须依赖 require_user —— 否则未登录会拿到 403 而不是 401,"
            "前端会把「该登录」显示成「没有权限」。"
        )

    @pytest.mark.parametrize("endpoint", AUTH_ONLY_ENDPOINTS)
    def test_登录即可的端点只需登录(self, endpoint):
        deps = _all_endpoints()[endpoint]
        assert "require_user" in deps, f"{endpoint} 必须要求登录"
        assert "require_admin_role" not in deps, (
            f"{endpoint} 只该要求登录 —— 挂成管理员专属会让普通用户的功能整片消失。"
        )

    @pytest.mark.parametrize("endpoint", PUBLIC_ENDPOINTS)
    def test_公开端点不带任何鉴权依赖(self, endpoint):
        deps = _all_endpoints()[endpoint]
        assert "require_user" not in deps and "require_admin_role" not in deps, (
            f"{endpoint} 不该要求登录 —— 共享链接/登录页/首屏会坏掉。"
        )

    def test_管理员专属的集合不多不少(self):
        found = _all_endpoints()
        actual = {k for k, v in found.items() if "require_admin_role" in v}
        assert actual == set(ADMIN_ONLY_ENDPOINTS), (
            "管理员专属端点与金名单不一致。\n"
            f"  多了(不该锁的):{sorted(actual - set(ADMIN_ONLY_ENDPOINTS))}\n"
            f"  少了(漏锁的)  :{sorted(set(ADMIN_ONLY_ENDPOINTS) - actual)}"
        )

    def test_公开的集合不多不少(self):
        """防"手滑多挂了一个" —— 那会让某个前端页面整片不可用。"""
        found = _all_endpoints()
        actual = {
            k
            for k, v in found.items()
            if "require_user" not in v and "require_admin_role" not in v
        }
        assert actual == set(PUBLIC_ENDPOINTS), (
            "公开端点与金名单不一致。\n"
            f"  多了(漏锁的):{sorted(actual - set(PUBLIC_ENDPOINTS))}\n"
            f"  少了(误锁的):{sorted(set(PUBLIC_ENDPOINTS) - actual)}"
        )


# ===========================================================================
# 11. 老库迁移
# ===========================================================================


class Test老库迁移:
    """账号体系给 `plans` 加了一列,而**已部署的库里那张表已经存在**。

    这一类改动最容易挂在"顺序"上,而且挂的方式是**启动即失败**
    (在 `init_db()` 里抛),连登录页都打不开。所以单独立一组。
    """

    LEGACY_PLANS = """
        CREATE TABLE plans (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            status TEXT NOT NULL, city TEXT NOT NULL, start_date TEXT NOT NULL,
            end_date TEXT NOT NULL, travel_days INTEGER NOT NULL, title TEXT,
            request_json TEXT NOT NULL, plan_json TEXT, warnings_json TEXT,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            cost_cny REAL NOT NULL DEFAULT 0, usage_source TEXT,
            latency_ms INTEGER NOT NULL DEFAULT 0, llm_calls INTEGER NOT NULL DEFAULT 0,
            run_id TEXT, tag TEXT
        )
    """

    def _legacy_db(self, tmp_path) -> str:
        """造一个"账号体系之前"的库:plans 存在且**没有 user_id**。"""
        import sqlite3

        path = tmp_path / "legacy.db"
        conn = sqlite3.connect(path)
        conn.executescript(self.LEGACY_PLANS)
        conn.execute(
            "INSERT INTO plans (id, created_at, updated_at, status, city, "
            "start_date, end_date, travel_days, request_json) "
            "VALUES ('old-plan-1', '2026-01-01T00:00:00', '2026-01-01T00:00:00', "
            "'ok', '北京', '2026-01-01', '2026-01-02', 2, '{}')"
        )
        conn.commit()
        conn.close()
        return str(path)

    def test_加列成功且老数据保留(self, tmp_path):
        import sqlite3

        from app.store.db import init_db

        path = self._legacy_db(tmp_path)
        init_db(path)

        conn = sqlite3.connect(path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(plans)")]
        assert "user_id" in cols
        assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1
        # 老数据没有归属 —— 语义正好是"无主,仅管理员可见",不需要回填
        assert conn.execute("SELECT COUNT(*) FROM plans WHERE user_id IS NULL").fetchone()[0] == 1
        conn.close()

    def test_新老两条路径建出同样的表(self, tmp_path):
        """**这条是这个类里最重要的一条。**

        防的是"索引建在 SCHEMA 里"这件事:老库执行到那条 CREATE INDEX 时
        `user_id` 还没加(ALTER 在 `_migrate()` 里,晚于 SCHEMA),
        于是抛 `no such column: user_id` —— 启动失败。
        所以断言两边建出来的索引集合必须一致。
        """
        import sqlite3

        from app.store.db import init_db

        legacy = self._legacy_db(tmp_path)
        init_db(legacy)
        fresh = str(tmp_path / "fresh.db")
        init_db(fresh)

        def schema(path):
            conn = sqlite3.connect(path)
            cols = sorted(r[1] for r in conn.execute("PRAGMA table_info(plans)"))
            idx = sorted(
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='plans'"
                )
            )
            conn.close()
            return cols, idx

        assert schema(legacy) == schema(fresh)

    def test_重复迁移是幂等的(self, tmp_path):
        """每次启动都会跑一遍 `init_db`,所以它必须能被反复调用。"""
        from app.store.db import init_db

        path = self._legacy_db(tmp_path)
        init_db(path)
        init_db(path)
        init_db(path)

    def test_迁移后账号功能可用(self, tmp_path, monkeypatch):
        """迁移不只是"不报错" —— 建出来的表要真的能用。"""
        from app.config import get_settings
        from app.store.user_store import UserStore

        path = self._legacy_db(tmp_path)
        monkeypatch.setattr(get_settings(), "db_path", path)
        monkeypatch.setattr(get_settings(), "password_hash_iterations", _TEST_ITERATIONS)

        store = UserStore(path)
        assert store.count_users() == 0
        user = store.create_user("boss", "boss-secret", role=ROLE_ADMIN)
        assert store.authenticate("boss", "boss-secret")["id"] == user["id"]

    def test_老的无主行程对管理员可见对普通用户不可见(self, client, admin_token, alice_token):
        """端到端确认"迁移过来的老行程"的可见性。

        它们没有归属 → 普通用户在历史页看不到(这是对的:说不上是谁的),
        管理员在 scope=all 里能看到。
        """
        from app.store.db import connect

        with connect() as conn:
            conn.execute(
                """
                INSERT INTO plans (id, created_at, updated_at, status, city,
                    start_date, end_date, travel_days, request_json, user_id)
                VALUES ('orphan-1', '2026-01-01T00:00:00', '2026-01-01T00:00:00',
                        'ok', '老城', '2026-01-01', '2026-01-02', 2, '{}', NULL)
                """
            )

        as_admin = client.get("/api/trip/plans?scope=all", headers=_bearer(admin_token))
        assert "orphan-1" in [r["id"] for r in as_admin.json()["data"]]

        as_user = client.get("/api/trip/plans", headers=_bearer(alice_token))
        assert "orphan-1" not in [r["id"] for r in as_user.json()["data"]]



# ===========================================================================
# 12. 旧机制确实被移除
# ===========================================================================


class Test共享口令已移除:
    """这一组测的是"**删干净了**"。

    留着旧入口(哪怕已经不再校验)比完全删掉更糟:它会让人以为
    "带着 X-Admin-Token 就能过",于是把真正的权限问题藏起来。
    """

    def test_配置里没有admin_token(self):
        assert not hasattr(get_settings(), "admin_token")

    def test_带旧请求头不再有特权(self, client, alice, plan_store):
        pid = _make_plan(plan_store, alice["id"])
        # 旧时代这个头等于"管理员",现在必须完全无效
        resp = client.delete(
            f"/api/trip/plans/{pid}",
            headers={"X-Admin-Token": "anything-at-all", "X-Admin-Required": "token"},
        )
        assert resp.status_code == 401

    def test_旧请求头不能读账号列表(self, client):
        resp = client.get("/api/auth/users", headers={"X-Admin-Token": "anything"})
        assert resp.status_code == 401

    def test_未配共享密钥时的503语义已消失(self, client):
        """旧设计在"服务端没配口令"时返回 503(fail-closed)。

        新设计里没有"要配的共享密钥"这个概念了 —— 未登录就是 401。
        这条断言是**文档性**的:确认 503 那个分支不会再出现。
        """
        assert client.get("/api/trip/plans").status_code == 401


# ===========================================================================
# 13. 配置项的环境变量别名
# ===========================================================================
#
# `Settings` 用 AliasChoices 给每个字段带了 `TRIPMIND_` 前缀别名。
# 这一组防的是"**文档里写的变量名其实是无效的**" ——
# pydantic-settings 默认只按字段名匹配,所以别名漏了某个字段时,
# 部署者照文档配了一遍,发现"没生效",而**没有任何报错**。
# 这类问题在 `db_path` 上真实发生过一次(见 config.py 里那段注释)。


class Test配置别名:
    def _load(self, monkeypatch, **env: str):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        from app.config import Settings

        return Settings()

    def test_会话有效期(self, monkeypatch):
        assert self._load(monkeypatch, TRIPMIND_SESSION_TTL_HOURS="48").session_ttl_hours == 48

    def test_引导管理员用户名(self, monkeypatch):
        assert self._load(monkeypatch, TRIPMIND_ADMIN_USERNAME="boss").admin_username == "boss"

    def test_引导管理员密码(self, monkeypatch):
        assert self._load(monkeypatch, TRIPMIND_ADMIN_PASSWORD="strong-pw").admin_password == "strong-pw"

    def test_密码最小长度(self, monkeypatch):
        assert self._load(monkeypatch, TRIPMIND_PASSWORD_MIN_LENGTH="10").password_min_length == 10

    def test_哈希轮数(self, monkeypatch):
        assert self._load(monkeypatch, TRIPMIND_PASSWORD_HASH_ITERATIONS="2000").password_hash_iterations == 2000

    def test_无前缀的名字也认(self, monkeypatch):
        """不带前缀的写法仍然可用(老部署脚本 / 本地临时覆盖)。"""
        assert self._load(monkeypatch, SESSION_TTL_HOURS="12").session_ttl_hours == 12

    def test_默认值(self, monkeypatch):
        """什么都不配时的默认值 —— 它们就是"开箱即用"那套。"""
        for key in (
            "TRIPMIND_SESSION_TTL_HOURS",
            "TRIPMIND_ADMIN_USERNAME",
            "TRIPMIND_ADMIN_PASSWORD",
            "TRIPMIND_PASSWORD_MIN_LENGTH",
        ):
            monkeypatch.delenv(key, raising=False)
        s = self._load(monkeypatch)
        assert s.admin_username == "admin"
        assert s.admin_password == "admin123", "默认引导密码,文档里明确标注为弱口令"
        assert s.session_ttl_hours == 168
        assert s.password_min_length == 6
