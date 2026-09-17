"""上传攻略文档的持久化。

## 为什么存正文而不是只存元信息

存一份归一化后的全文有三个直接好处:

1. 「重新入库」不用用户再传一次 —— Milvus 挂了/换 embedding 模型重灌时,
   直接从库里读正文重新切块即可。
2. 「查看来源」能直接展示原文片段,不必碰文件系统。
3. 删除时能按**自己记录的 chunk_ids** 精确删 Milvus 里的块,
   而不是靠 Milvus 的前缀匹配去猜 —— 自己写了什么、删什么,最可靠。

代价是 SQLite 里会存几百 KB 的正文,对这个规模的应用无所谓。

## status 三态

    pending   解析完成、还没入库(Milvus 里没有它) —— 用户上传后先停在这
    ingested  已写入 Milvus,检索能用
    failed    入库失败,error 里留具体原因(embedding 不可用 / Milvus 连不上)

入库是**异步任务**,pending → ingested 之间用户可能刷新页面,
所以状态必须落库而不是放在内存里。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from .db import connect, init_db


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _row_to_doc(row: sqlite3.Row, *, with_content: bool) -> dict[str, Any]:
    """列表页**不带 content**(可能几百 KB,一次取十条就把响应撑大了)。"""
    out: dict[str, Any] = {
        "id": row["id"],
        "title": row["title"],
        "origin": row["origin"],
        "char_count": row["char_count"],
        "chunk_count": row["chunk_count"],
        "status": row["status"],
        "error": row["error"],
        "created_at": row["created_at"],
        "ingested_at": row["ingested_at"],
        "chunk_ids": _loads(row["chunk_ids"]) or [],
    }
    if with_content:
        out["content"] = row["content"]
        out["content_hash"] = row["content_hash"]
    return out


class DocStore:
    """上传文档的增删改查。同步阻塞,调用方应在线程池里用。"""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        # 建表幂等,构造时确保一次。老库会自动补上这张表。
        init_db(db_path)

    # ---------- 写 ----------

    def create_pending(self, doc: dict[str, Any]) -> bool:
        """插入一条 pending 记录;已存在时**什么都不做**,返回是否真的插入了。

        用 `INSERT OR IGNORE` 而不是 `INSERT OR REPLACE`:doc_id 由正文哈希得来,
        已存在的记录可能是 **ingested** 状态、带着入库时记录的 chunk_ids。
        用 REPLACE 会把它重置回 pending 并清掉 chunk_ids ——
        那条记录从此删不掉向量库里的块(症状:删了,检索照样搜得到)。

        同一份内容 → 同一个 doc_id → 同一批 chunk id,
        所以"已存在就保持原样"不会造成重复入库。
        """
        with connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO knowledge_docs
                    (id, title, origin, content, content_hash, char_count,
                     chunk_count, chunk_ids, status, error, created_at, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, NULL)
                """,
                (
                    doc["id"],
                    doc["title"],
                    doc["origin"],
                    doc["content"],
                    doc["content_hash"],
                    doc.get("char_count", len(doc["content"])),
                    doc.get("chunk_count", 0),
                    json.dumps(doc.get("chunk_ids") or [], ensure_ascii=False),
                    _now(),
                ),
            )
            return cur.rowcount > 0

    def mark_ingested(self, doc_id: str, chunk_ids: list[str], chunk_count: int) -> bool:
        """入库成功。chunk_ids 是**这次实际写进 Milvus 的全部主键**。"""
        with connect(self.db_path) as conn:
            cur = conn.execute(
                """
                UPDATE knowledge_docs
                   SET status = 'ingested', chunk_ids = ?, chunk_count = ?,
                       error = NULL, ingested_at = ?
                 WHERE id = ?
                """,
                (json.dumps(chunk_ids, ensure_ascii=False), chunk_count, _now(), doc_id),
            )
            return cur.rowcount > 0

    def mark_failed(self, doc_id: str, error: str) -> bool:
        """入库失败。**error 要写具体原因**,笼统的"失败"没法排错。"""
        with connect(self.db_path) as conn:
            cur = conn.execute(
                """
                UPDATE knowledge_docs
                   SET status = 'failed', error = ?, ingested_at = ?
                 WHERE id = ?
                """,
                (error[:500], _now(), doc_id),
            )
            return cur.rowcount > 0

    # ---------- 读 ----------

    def get(self, doc_id: str, *, with_content: bool = True) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM knowledge_docs WHERE id = ?", (doc_id,)
            ).fetchone()
            return _row_to_doc(row, with_content=with_content) if row else None

    def list_docs(self, limit: int = 100) -> list[dict[str, Any]]:
        """列表,新的在前。不带 content。"""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM knowledge_docs
                 ORDER BY created_at DESC, id DESC
                 LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [_row_to_doc(r, with_content=False) for r in rows]

    def summary(self) -> dict[str, Any]:
        """各状态的数量与已入库的总块数,给「进阶」区的概览用。"""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS n, COALESCE(SUM(chunk_count), 0) AS chunks
                  FROM knowledge_docs
                 GROUP BY status
                """
            ).fetchall()
            by_status = {r["status"]: {"docs": r["n"], "chunks": r["chunks"]} for r in rows}
        return {
            "docs": sum(v["docs"] for v in by_status.values()),
            "chunks": sum(v["chunks"] for v in by_status.values()),
            "ingested": by_status.get("ingested", {"docs": 0, "chunks": 0}),
            "pending": by_status.get("pending", {"docs": 0, "chunks": 0}),
            "failed": by_status.get("failed", {"docs": 0, "chunks": 0}),
        }

    # ---------- 删 ----------

    def delete(self, doc_id: str) -> bool:
        """删记录。**调用方必须先拿 chunk_ids 把 Milvus 里的块删掉** ——
        顺序反了的话,库里的块就成了没有任何记录指向的孤儿,
        而且检索照样能搜到它,症状是"删了还在"。
        """
        with connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM knowledge_docs WHERE id = ?", (doc_id,))
            return cur.rowcount > 0
