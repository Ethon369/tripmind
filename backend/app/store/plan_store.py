"""行程的持久化

关键设计:先落库,再生成
----------------------
`create_running()` 在调用 LLM **之前**就先插一条 status='running' 的记录,
生成完再 UPDATE 回填结果。这样做有两个好处:

1. **不存在"生成完了但没存住"的窗口。** 如果等生成完再插库,而这中间
   进程崩了/请求超时了,那份行程就凭空消失了。
2. **plan_id 提前确定**,可以直接作为分享链接的地址返回给前端。

代价是库里会残留 status='running' 的记录(进程崩溃时)。
`list_plans()` 默认把它们标出来,前端可以显示"生成中断"。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from .db import connect, init_db

# 历史列表默认只返回有结果的记录
_VISIBLE_STATUSES = ("ok", "fallback", "error")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dumps(obj: Any) -> str | None:
    if obj is None:
        return None
    if hasattr(obj, "model_dump_json"):
        return obj.model_dump_json()
    return json.dumps(obj, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _row_to_summary(row: sqlite3.Row) -> dict[str, Any]:
    """历史列表用的精简结构 —— 不带完整的 plan_json(可能很大)。"""
    return {
        "id": row["id"],
        "title": row["title"],
        "city": row["city"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "travel_days": row["travel_days"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "total_tokens": row["total_tokens"],
        "cost_cny": row["cost_cny"],
        "latency_ms": row["latency_ms"],
        "llm_calls": row["llm_calls"],
        "usage_source": row["usage_source"],
        "warnings": _loads(row["warnings_json"]) or [],
        # 从 plan_json 里取摘要,避免前端为了列表再去解析整份行程
        "attractions": _count_attractions(row["plan_json"]),
        "total_budget": _total_budget(row["plan_json"]),
    }


def _count_attractions(plan_json: str | None) -> int:
    plan = _loads(plan_json)
    if not plan:
        return 0
    return sum(len(d.get("attractions") or []) for d in plan.get("days") or [])


def _total_budget(plan_json: str | None) -> int:
    plan = _loads(plan_json)
    if not plan:
        return 0
    budget = plan.get("budget") or {}
    return int(budget.get("total") or 0)


class PlanStore:
    """行程的增删改查。所有方法都是同步阻塞的 —— 调用方应在线程池里用。"""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        # 建表是幂等的,构造时确保一次
        init_db(db_path)

    # ---------- 写 ----------

    def create_running(self, request: Any, run_id: str | None = None) -> str:
        """在调用 LLM 之前先落一条 status='running' 的记录。

        Returns:
            plan_id(uuid4().hex),同时就是分享链接里的 id
        """
        plan_id = uuid.uuid4().hex
        now = _now()
        city = getattr(request, "city", "") or ""
        days = int(getattr(request, "travel_days", 0) or 0)

        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO plans (
                    id, created_at, updated_at, status,
                    city, start_date, end_date, travel_days, title,
                    request_json, run_id
                ) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id, now, now,
                    city,
                    getattr(request, "start_date", "") or "",
                    getattr(request, "end_date", "") or "",
                    days,
                    f"{city} {days}日游" if city else None,
                    _dumps(request),
                    run_id,
                ),
            )
        return plan_id

    def finish_plan(
        self,
        plan_id: str,
        plan: Any = None,
        *,
        status: str = "ok",
        warnings: list[str] | None = None,
        usage: dict[str, Any] | None = None,
        latency_ms: int = 0,
    ) -> None:
        """回填生成结果。

        Args:
            status: 'ok' | 'fallback' | 'error'
            usage: UsageCollector.summary() 的结果
        """
        usage = usage or {}
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE plans SET
                    updated_at        = ?,
                    status            = ?,
                    plan_json         = ?,
                    warnings_json     = ?,
                    prompt_tokens     = ?,
                    completion_tokens = ?,
                    total_tokens      = ?,
                    cost_cny          = ?,
                    usage_source      = ?,
                    latency_ms        = ?,
                    llm_calls         = ?
                WHERE id = ?
                """,
                (
                    _now(),
                    status,
                    _dumps(plan),
                    json.dumps(warnings or [], ensure_ascii=False),
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    int(usage.get("total_tokens") or 0),
                    float(usage.get("cost_cny") or 0.0),
                    usage.get("usage_source"),
                    int(latency_ms),
                    int(usage.get("llm_calls") or 0),
                    plan_id,
                ),
            )

    def update_plan_json(self, plan_id: str, plan: Any) -> bool:
        """前端编辑行程后回写(只改内容,不动成本统计)。"""
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE plans SET plan_json = ?, updated_at = ? WHERE id = ?",
                (_dumps(plan), _now(), plan_id),
            )
            return cur.rowcount > 0

    def delete_plan(self, plan_id: str) -> bool:
        with connect(self.db_path) as conn:
            conn.execute("UPDATE runs SET plan_id = NULL WHERE plan_id = ?", (plan_id,))
            cur = conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
            return cur.rowcount > 0

    # ---------- 读 ----------

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "status": row["status"],
            "title": row["title"],
            "city": row["city"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "travel_days": row["travel_days"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "plan": _loads(row["plan_json"]),
            "request": _loads(row["request_json"]),
            "warnings": _loads(row["warnings_json"]) or [],
            "total_tokens": row["total_tokens"],
            "cost_cny": row["cost_cny"],
            "usage_source": row["usage_source"],
            "latency_ms": row["latency_ms"],
            "llm_calls": row["llm_calls"],
        }

    def list_plans(
        self,
        limit: int = 50,
        offset: int = 0,
        include_running: bool = False,
    ) -> list[dict[str, Any]]:
        """历史列表,按创建时间倒序。"""
        placeholders = ",".join("?" for _ in _VISIBLE_STATUSES)
        sql = f"SELECT * FROM plans WHERE status IN ({placeholders})"
        params: list[Any] = list(_VISIBLE_STATUSES)
        if include_running:
            sql = "SELECT * FROM plans WHERE 1=1"
            params = []
        sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params += [int(limit), int(offset)]

        with connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_summary(r) for r in rows]

    def count_plans(self, include_running: bool = False) -> int:
        if include_running:
            sql, params = "SELECT COUNT(*) AS n FROM plans", ()
        else:
            placeholders = ",".join("?" for _ in _VISIBLE_STATUSES)
            sql = f"SELECT COUNT(*) AS n FROM plans WHERE status IN ({placeholders})"
            params = _VISIBLE_STATUSES
        with connect(self.db_path) as conn:
            return int(conn.execute(sql, params).fetchone()["n"])

    def stats(self) -> dict[str, Any]:
        """给历史页顶部用的聚合数字。"""
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)              AS total,
                       COALESCE(SUM(cost_cny), 0)   AS cost,
                       COALESCE(SUM(total_tokens), 0) AS tokens,
                       COALESCE(SUM(llm_calls), 0)  AS calls
                FROM plans
                WHERE status IN ('ok', 'fallback', 'error')
                """
            ).fetchone()
        return {
            "total": int(row["total"]),
            "cost_cny": round(float(row["cost"]), 6),
            "total_tokens": int(row["tokens"]),
            "llm_calls": int(row["calls"]),
        }


_store: PlanStore | None = None


def get_plan_store() -> PlanStore:
    """单例(沿用项目里 get_llm / get_amap_service 的风格)。"""
    global _store
    if _store is None:
        _store = PlanStore()
    return _store
