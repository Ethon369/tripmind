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
    keys = row.keys()
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
        # 归属。**能不能给前端看由路由决定**(只有管理员才该看到"这条是谁的"),
        # 存储层负责如实返回 —— 在这里按角色过滤会让 store 需要知道"当前是谁",
        # 那是 API 层的事。
        #
        # 用 keys() 判断而不是 try:调用方可能不 JOIN users(比如自己拼的查询),
        # 缺列时静默给 None,而不是在读取列表时抛 KeyError 把整页打挂。
        "user_id": row["user_id"] if "user_id" in keys else None,
        "owner_username": row["owner_username"] if "owner_username" in keys else None,
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

    def create_running(
        self,
        request: Any,
        run_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """在调用 LLM 之前先落一条 status='running' 的记录。

        Args:
            user_id: 归属账号。**在这里就写进去**,而不是等生成完再回填 ——
                生成过程中进程崩掉时,那条 running 记录也已经带着归属,
                管理员能在列表里看到"谁的这次生成中断了"。留空表示无主
                (账号体系之前的数据,或未启用登录时)。

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
                    request_json, run_id, user_id
                ) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
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
                    user_id,
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
            # plan_knowledge 不需要手动删 —— 它的外键带 ON DELETE CASCADE,
            # 且连接里开了 PRAGMA foreign_keys=ON,删 plans 时会自动带走。
            return cur.rowcount > 0

    # ---------- 知识出处 ----------

    def save_knowledge(self, plan_id: str, hits: list[dict[str, Any]]) -> int:
        """保存这次生成检索到的知识出处。

        先删后插:虽然一份行程只会生成一次,但接口允许重新生成,
        不先清的话旧记录会和新的混在一起(主键是 plan_id+idx,会冲突)。

        `content` 只留前 300 字符 —— 完整分块可能有上千字,
        而详情页只需要让用户看出"参考了什么",不是把攻略全文再抄一遍。
        """
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM plan_knowledge WHERE plan_id = ?", (plan_id,))
            if not hits:
                return 0
            conn.executemany(
                """
                INSERT INTO plan_knowledge
                    (plan_id, idx, namespace, source, heading_path, score, snippet)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        plan_id,
                        i,
                        str(h.get("namespace") or ""),
                        str(h.get("source") or ""),
                        str(h.get("heading_path") or ""),
                        float(h.get("score") or 0.0),
                        str(h.get("content") or "")[:300],
                    )
                    for i, h in enumerate(hits)
                ],
            )
        return len(hits)

    def get_knowledge(self, plan_id: str) -> list[dict[str, Any]]:
        """读取一次生成的知识出处。

        按 idx 升序 —— 它保的就是当初 **分数降序** 的顺序,
        所以前端拿到的第一条就是最相关的那条。
        """
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT idx, namespace, source, heading_path, score, snippet
                FROM plan_knowledge WHERE plan_id = ? ORDER BY idx
                """,
                (plan_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 读 ----------

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        """取一条行程。**不在这里判权限** —— 调用方拿到 `user_id` 后自己判
        (见 `api/deps.py` 的 `can_access_plan`)。

        用 LEFT JOIN 而不是 JOIN:无主行程(user_id IS NULL)也必须是可见的,
        INNER JOIN 会让它们从详情页凭空消失。
        """
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT p.*, u.username AS owner_username
                FROM plans p
                LEFT JOIN users u ON u.id = p.user_id
                WHERE p.id = ?
                """,
                (plan_id,),
            ).fetchone()
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
            "user_id": row["user_id"],
            "owner_username": row["owner_username"],
        }

    def list_plans(
        self,
        limit: int = 50,
        offset: int = 0,
        include_running: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """历史列表,按创建时间倒序。

        Args:
            user_id: 只列这个账号的行程。**None 表示不过滤(全部)** ——
                所以"看全部"这个权限必须在调用方把关,不能靠这里。
                路由层把它实现成"管理员才允许传 None",见 `routes/trip.py`。
                用一个显式的布尔开关(`mine_only`)会让"无主的也要算进来吗"
                这种问题没法表达,而 user_id 语义只有一个。
        """
        sql = "SELECT p.*, u.username AS owner_username FROM plans p " \
              "LEFT JOIN users u ON u.id = p.user_id"
        params: list[Any] = []

        conditions: list[str] = []
        if not include_running:
            placeholders = ",".join("?" for _ in _VISIBLE_STATUSES)
            conditions.append(f"p.status IN ({placeholders})")
            params.extend(_VISIBLE_STATUSES)
        if user_id is not None:
            conditions.append("p.user_id = ?")
            params.append(user_id)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        sql += " ORDER BY p.created_at DESC, p.id DESC LIMIT ? OFFSET ?"
        params += [int(limit), int(offset)]

        with connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_summary(r) for r in rows]

    def count_plans(self, include_running: bool = False, user_id: str | None = None) -> int:
        conditions: list[str] = []
        params: list[Any] = []
        if not include_running:
            placeholders = ",".join("?" for _ in _VISIBLE_STATUSES)
            conditions.append(f"status IN ({placeholders})")
            params.extend(_VISIBLE_STATUSES)
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)

        sql = "SELECT COUNT(*) AS n FROM plans"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        with connect(self.db_path) as conn:
            return int(conn.execute(sql, params).fetchone()["n"])

    def stats(self, user_id: str | None = None) -> dict[str, Any]:
        """给历史页顶部用的聚合数字。

        ⚠️ 这个数字会**跟着角色变**:管理员看到的是全站成本,普通用户看到的
        是自己那部分。这是有意的 —— 顶部那块写的是"累计花费",如果普通用户
        看到的是全站数字,而下面的列表只有自己的几条,两者对不上会让人以为
        列表漏了数据。
        """
        placeholders = ",".join("?" for _ in _VISIBLE_STATUSES)
        sql = f"""
            SELECT COUNT(*)              AS total,
                   COALESCE(SUM(cost_cny), 0)   AS cost,
                   COALESCE(SUM(total_tokens), 0) AS tokens,
                   COALESCE(SUM(llm_calls), 0)  AS calls
            FROM plans
            WHERE status IN ({placeholders})
        """
        params: list[Any] = list(_VISIBLE_STATUSES)
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)

        with connect(self.db_path) as conn:
            row = conn.execute(sql, params).fetchone()
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
