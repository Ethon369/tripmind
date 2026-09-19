"""诊断:为什么生成行程失败。

只读,不改任何东西。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "tripmind.db"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("=" * 78)
print("最近的行程记录(status / warnings / token / 耗时)")
print("=" * 78)
rows = conn.execute(
    """
    SELECT id, created_at, status, city, travel_days,
           warnings_json, total_tokens, llm_calls, latency_ms, cost_cny, user_id
    FROM plans ORDER BY created_at DESC LIMIT 10
    """
).fetchall()

for r in rows:
    print(f"\n[{r['created_at']}] {r['city']} {r['travel_days']}日  status={r['status']}")
    print(f"  plan_id={r['id']}  user_id={r['user_id']}")
    print(f"  tokens={r['total_tokens']}  llm_calls={r['llm_calls']}  "
          f"latency={r['latency_ms']}ms  cost=¥{r['cost_cny']}")
    warns = json.loads(r["warnings_json"] or "[]")
    for w in warns:
        print(f"  ⚠️  {w}")

print()
print("=" * 78)
print("runs 表(看 fallback 发生在哪个阶段)")
print("=" * 78)
try:
    runs = conn.execute(
        "SELECT id, created_at, kind, mode, ok, fallback_used, error, stages_json "
        "FROM runs ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    for r in runs:
        print(f"\n[{r['created_at']}] kind={r['kind']} ok={r['ok']} "
              f"fallback={r['fallback_used']}")
        print(f"  error={r['error']}")
        stages = json.loads(r["stages_json"] or "[]")
        for s in stages if isinstance(stages, list) else []:
            print(f"    · {s}")
except sqlite3.OperationalError as e:
    print("  (runs 表查询失败:", e, ")")

conn.close()
