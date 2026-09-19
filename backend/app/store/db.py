"""SQLite 连接、建表与迁移

四个设计决定
------------
1. **每请求一个连接,不用全局连接。**
   FastAPI 的同步端点跑在线程池里,每个请求自带线程。用全局连接就必须
   开 `check_same_thread=False` 并自己做加锁;每请求新建连接反而更简单、
   更安全。SQLite 开连接的开销极小,且开了 WAL 之后并发读不互斥。

2. **时间一律存 ISO-8601 字符串。**
   Python 标准库的 datetime 适配器自 3.12 起已废弃,直接塞 datetime
   对象会告警,将来会移除。存字符串最稳。

3. **plans / runs 互相引用,插入顺序有讲究。**
   plans.run_id → runs.id,而 runs.plan_id → plans.id。两边都插就会踩
   FOREIGN KEY constraint failed。正确顺序见 plan_store.create_running():
   先生成两个 uuid → 插 plans(run_id 留空)→ 插 runs(plan_id 有值)
   → UPDATE plans SET run_id=?。

4. **加表靠 SCHEMA,加列靠 `_migrate()`。**
   `CREATE TABLE IF NOT EXISTS` 对**已存在**的表是空操作,所以它只能加表、
   不能加列 —— 这也是 `plan_knowledge` 当初选择独立成表而不是给 plans 加字段的
   原因。但 `plans.user_id` 没法回避:它必须和 plans 同行,否则每次查归属都要
   多一次 JOIN。所以这里补了一个最小的迁移函数,见 `_migrate()`。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# backend/ 目录(本文件在 backend/app/store/ 下,上溯三层)
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

SCHEMA = """
-- 一次生成 = 一条行程记录。这是历史列表与分享链接的数据源。
CREATE TABLE IF NOT EXISTS plans (
    id                TEXT PRIMARY KEY,   -- uuid4().hex,就是分享链接里的 id
    created_at        TEXT NOT NULL,      -- ISO-8601
    updated_at        TEXT NOT NULL,
    status            TEXT NOT NULL,      -- 'running' | 'ok' | 'fallback' | 'error'

    -- 请求参数(去规范化,历史列表直接展示,不用解析 JSON)
    city              TEXT NOT NULL,
    start_date        TEXT NOT NULL,
    end_date          TEXT NOT NULL,
    travel_days       INTEGER NOT NULL,
    title             TEXT,               -- 展示用:"北京 3日游"
    request_json      TEXT NOT NULL,      -- 原始 TripRequest,支持"以此重规划"

    -- 结果
    plan_json         TEXT,               -- 完整 TripPlan
    warnings_json     TEXT,               -- 降级原因,前端直接显示

    -- 成本(去规范化,历史列表要能直接排序/展示)
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_cny          REAL    NOT NULL DEFAULT 0,
    usage_source      TEXT,               -- 'api' | 'estimated'
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    llm_calls         INTEGER NOT NULL DEFAULT 0,

    run_id            TEXT,               -- 关联 runs.id(见文件头说明)
    tag               TEXT,               -- 'manual' | 'baseline' | 'after' ...

    -- 归属账号。**允许为空**,而且是两种不同情况:
    --   · 建库时还没有账号体系,历史记录没有归属(老库迁移过来的)
    --   · 管理员账号被删除时,刻意保留其行程并把归属置空(见 user_store.delete_user)
    -- 为空表示"无主",列表接口对所有普通用户都不可见,只有管理员看得到。
    -- 不设 NOT NULL 就是为了让这两种情况都能表达。
    --
    -- ⚠️ **刻意不加 `REFERENCES users(id)`。** 理由是新老库两条路径必须等价:
    --    新库由上面的 CREATE TABLE 建出这一列,老库由 `_migrate()` 的
    --    ALTER TABLE ADD COLUMN 补上 —— 而 SQLite 的 ALTER 对带 REFERENCES
    --    的列有一串额外限制,两边很容易写出行为不同的表。归属的清理改成
    --    显式做:`user_store.delete_user()` 里先 `UPDATE plans SET user_id=NULL`。
    --    显式版还能被测试直接验证,比依赖 FK 级联更可控。
    user_id           TEXT                -- 关联 users.id(无外键,见上)
);
CREATE INDEX IF NOT EXISTS idx_plans_created ON plans(created_at DESC);
--
-- ⚠️ `plans(user_id)` 的索引**不能写在这里**,必须放在 `_migrate()` 里。
--    原因是执行顺序:老库里 `plans` 已经存在(且没有 user_id 列),
--    所以上面那条 `CREATE TABLE IF NOT EXISTS` 是**空操作**,而
--    `executescript` 会继续往下执行 —— 此时 `CREATE INDEX ... (user_id)`
--    会在 ALTER TABLE 给它加列**之前**执行,直接抛
--        sqlite3.OperationalError: no such column: user_id
--    而且是在 `init_db()` 里抛,也就是**启动即失败**,连服务都起不来。
--    (这不是理论推演:本地 `data/tripmind.db` 是 21 列的老表,
--     把索引放这里的第一次尝试就是这么挂的。)

-- 一次「运行」= 一次生成 或 一次评测。产品与评测共用这张表。
CREATE TABLE IF NOT EXISTS runs (
    id             TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    kind           TEXT NOT NULL,        -- 'manual' | 'eval'
    tag            TEXT,                 -- run_eval --tag 的值
    mode           TEXT,                 -- 'pipeline' | 'supervisor' | 'parallel'
    rag_enabled    INTEGER NOT NULL DEFAULT 0,
    repeat_index   INTEGER,
    request_json   TEXT NOT NULL,
    plan_id        TEXT REFERENCES plans(id),
    ok             INTEGER NOT NULL DEFAULT 0,
    fallback_used  INTEGER NOT NULL DEFAULT 0,
    error          TEXT,

    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_cny          REAL    NOT NULL DEFAULT 0,
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    llm_calls         INTEGER NOT NULL DEFAULT 0,

    -- 指标存 JSON:harness 将来加指标不需要改表结构
    metrics_json   TEXT,
    stages_json    TEXT,
    trace_path     TEXT                  -- data/runs/{日期}.jsonl 的相对路径
);
CREATE INDEX IF NOT EXISTS idx_runs_tag ON runs(tag, created_at);

-- 逐次 LLM 调用明细
CREATE TABLE IF NOT EXISTS llm_calls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT NOT NULL REFERENCES runs(id),
    seq               INTEGER NOT NULL,
    model             TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    cache_hit_tokens  INTEGER,
    usage_source      TEXT,
    raw_usage_json    TEXT,              -- 厂商原始的 usage 字段原样留存(换模型后仍能核对计费口径)
    cost_cny          REAL,
    ms                INTEGER,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_run ON llm_calls(run_id, seq);

-- 一份行程检索到的知识出处(RAG 的"它参考了什么")。
--
-- 为什么单独一张表而不是给 plans 加一列:
--   CREATE TABLE IF NOT EXISTS 对**已有**的库也会执行,所以老库能自动补上这张表;
--   而给 plans 加列必须写 ALTER TABLE 迁移。项目一共就这几张表,不值得为迁移引工具。
--
-- 为什么需要它:生成行程时模型会引用城市攻略与 POI 事实,但引用是**看不见的** ——
-- 用户看到"故宫需要提前预约"时,无法判断这是模型编的还是知识库里确有其事。
-- 存下来之后,行程详情页就能把出处摊开。
CREATE TABLE IF NOT EXISTS plan_knowledge (
    plan_id      TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL,      -- 保序:接口返回时按分数降序
    namespace    TEXT NOT NULL,         -- 'poi_facts' | 'city_guides'
    source       TEXT NOT NULL,         -- 文件名 / poi_inventory.json
    heading_path TEXT,                  -- 章节路径,如「门票与预约」
    score        REAL NOT NULL,         -- COSINE 相似度
    snippet      TEXT,                  -- 内容摘要(截断),供前端展示
    PRIMARY KEY (plan_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_plan_knowledge_plan ON plan_knowledge(plan_id);

-- 用户上传的攻略文档。
--
-- 存的是**归一化后的正文**,不是原文件 —— 这样「重新入库」不需要用户再传一次,
-- 「查看来源」也能直接从库里读正文,不必碰文件系统。图片(OCR)刻意不支持,
-- 原因见 app/services/doc_parser.py 顶部注释。
--
-- status 的取值:
--   pending   解析完了、还没入库(Milvus 里还没有它)
--   ingested  已写入 Milvus,检索能用
--   failed    入库失败,error 里留原因(通常是 embedding 或 Milvus 不可用)
--
-- chunk_ids 存的是**这次写入 Milvus 的全部主键**(JSON 数组)。删除时按它精确删,
-- 而不是靠前缀匹配 —— Milvus 的 like 前缀匹配在动态字段上行为有坑,
-- 「自己记录自己写了什么,删的时候就删什么」是最可靠的。
--
-- user_id: 上传者。**允许为空**,语义与 plans.user_id 一致:
--   · 为空 = 「无主」(账号体系上线前上传的那批),只有管理员看得到/管得到;
--   · 非空 = 本人的,只有他和管理员看得到 —— 检索时也只能检索到自己的。
--
-- ⚠️ 有了这一列之后,**doc_id 必须把上传者算进去**(见 routes/knowledge.py)。
--    原来的 doc_id 是正文哈希的前 12 位,而"同一份内容 → 同一个 id"在
--    多人场景下是个 bug:两个用户传同一个文件会撞成同一条记录,
--    后传的人静默地什么都得不到(INSERT OR IGNORE),但他以为传成功了。
--    把 owner 混进哈希之后,幂等性变成"**同一个人**传同一份内容不会重复",
--    这才是原本想要的那条性质。
CREATE TABLE IF NOT EXISTS knowledge_docs (
    id           TEXT PRIMARY KEY,         -- sha256(上传者 + 正文) 的前 12 位
    title        TEXT NOT NULL,
    origin       TEXT NOT NULL,            -- 'md' | 'txt' | 'pdf' | 'paste'
    content      TEXT NOT NULL,            -- 归一化后的全文
    content_hash TEXT NOT NULL,            -- SHA-256 全文,64 位
    char_count   INTEGER NOT NULL DEFAULT 0,
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    chunk_ids    TEXT NOT NULL DEFAULT '[]',
    status       TEXT NOT NULL DEFAULT 'pending',
    error        TEXT,
    created_at   TEXT NOT NULL,
    ingested_at  TEXT,
    user_id      TEXT                      -- 上传者(无外键,理由同 plans.user_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_docs_status ON knowledge_docs(status);
-- ⚠️ user_id 的索引只能建在 `_migrate()` 里,不能写在这里 ——
--    理由与 plans.user_id 完全相同:老库的 knowledge_docs 已存在,
--    CREATE TABLE 是空操作,而这条 CREATE INDEX 会在 ALTER 之前执行。

-- 账号。两种角色:
--   admin  可管理账号、可看所有人的行程、可用知识库的写操作
--   user   只看得到自己的行程
--
-- password_hash 是 `pbkdf2_sha256$轮数$盐$摘要`(见 app/services/security.py),
-- **绝不要**在这一列里放明文或可逆加密 —— 它会被导出、备份、贴进日志。
--
-- username 的唯一性刻意用 COLLATE NOCASE:登录名"Zhaoyun"和"zhaoyun"
-- 是同一个账号更符合直觉,否则用户会看到一个"用户名已存在"却查不到自己的
-- 诡异状态。COLLATE NOCASE 在 SQLite 里只对 ASCII 生效,注册时用字符白名单
-- 把范围限在 ASCII,这两件事是配套的(见 user_store.validate_username)。
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,        -- uuid4().hex
    username      TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    display_name  TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_login_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username COLLATE NOCASE);

-- 登录会话。
--
-- token 列存的是**令牌的 SHA-256**,不是令牌原文 —— 库被读走(备份泄露、
-- 误传)也拿不到可直接用的会话。校验时对请求带来的令牌算一次摘要再查,
-- 所以这一列等于令牌本身,只是不可逆。
--
-- 用"服务端会话表"而不是无状态 JWT:JWT 一旦签发就无法作废,
-- 而账号管理里"禁用/删除账号应当立刻生效"是明确需求 ——
-- 那需要一张能被删行的表。代价是每个请求多一次本地 SQLite 查询,
-- 在这个规模下是微秒级。
CREATE TABLE IF NOT EXISTS sessions (
    token        TEXT PRIMARY KEY,         -- sha256(令牌)
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,            -- ISO-8601,比较用字符串序即可
    last_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
"""


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """把配置里的相对路径解析成绝对路径。

    相对路径是相对 backend/ 而不是当前工作目录 —— 否则从不同目录启动
    服务会生成不同的数据库文件。
    """
    if db_path is None:
        from ..config import get_settings

        db_path = get_settings().db_path

    path = Path(db_path)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path


@contextmanager
def connect(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """打开一个连接。正常退出自动提交,异常自动回滚。

    用法::

        with connect() as conn:
            conn.execute("INSERT ...")
    """
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL:读写不互斥,适合"一个请求在写、另一个在读列表"的场景
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """给**已存在**的库补上 SCHEMA 加不出来的东西。

    `CREATE TABLE IF NOT EXISTS` 只能建新表,对已存在的表是空操作 ——
    所以凡是"给老表加一列"的改动都必须写在这里。

    为什么不用 Alembic 之类的迁移工具:项目总共 6 张表、一次性的列新增,
    引一套工具要带 migration 目录、版本表、降级脚本,维护成本远大于收益。
    这里的判断标准很简单:**幂等** + **可重复执行**。
    加了什么就写在下面,顺序即执行顺序。
    """
    # plans 加 user_id(账号体系上线时补的)。
    # 老库里已有若干条无主行程,加完这一列它们就是 user_id IS NULL ——
    # 语义正好是"无主,仅管理员可见",不需要额外回填。
    plan_columns = {row["name"] for row in conn.execute("PRAGMA table_info(plans)")}
    if "user_id" not in plan_columns:
        conn.execute("ALTER TABLE plans ADD COLUMN user_id TEXT")

    # ⚠️ 索引必须建在 ALTER 之后:老库里根本没这一列,先建索引会报
    #    `no such column: user_id`,而且是**启动即失败**,整个服务起不来。
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plans_user ON plans(user_id, created_at DESC)"
    )

    # knowledge_docs 加 user_id(知识库按人隔离时补的)。
    # 老库里那批上传的攻略会变成 user_id IS NULL = "无主",
    # 语义正好是"只有管理员看得到",不需要回填。
    doc_columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_docs)")}
    if "user_id" not in doc_columns:
        conn.execute("ALTER TABLE knowledge_docs ADD COLUMN user_id TEXT")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_docs_user "
        "ON knowledge_docs(user_id, created_at DESC)"
    )


def init_db(db_path: str | Path | None = None) -> Path:
    """建表 + 迁移(幂等)。返回数据库文件的绝对路径。"""
    path = resolve_db_path(db_path)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
    return path
