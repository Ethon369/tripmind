"""SQLite 连接与建表

三个设计决定
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
    tag               TEXT                -- 'manual' | 'baseline' | 'after' ...
);
CREATE INDEX IF NOT EXISTS idx_plans_created ON plans(created_at DESC);

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
    raw_usage_json    TEXT,              -- DeepSeek 的 cache_hit/miss 等原样留存
    cost_cny          REAL,
    ms                INTEGER,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_run ON llm_calls(run_id, seq);
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


def init_db(db_path: str | Path | None = None) -> Path:
    """建表(幂等)。返回数据库文件的绝对路径。"""
    path = resolve_db_path(db_path)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
    return path
