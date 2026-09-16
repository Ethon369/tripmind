"""审计 poi_support_rate 里那些「查无此物」的名字

**为什么需要它**

`poi_support_rate` 报一个数字(比如 0.57),报告上就会显示"9 条不通过"。
但**这个数字本身不能告诉你原因**,而两种原因的后果完全相反:

    A. 库存没收录这个真实景点   → 指标偏低,模型没问题
    B. 模型真的编了一个名字     → 指标正确,模型有问题

baseline 实测:41 个未命中里 A 占绝大多数。**不做这个审计就会把 A 读成 B**,
然后去修一个不存在的 bug。

**它怎么分**

对每个未命中的名字,同时问两个 oracle:

1. **我录的库存里最像的那个是什么、有多像** —— 分三档。
   如果大量名字挤在「阈值边缘」,说明阈值定得太松,随时会把 A 地点认成 B 地点 ——
   那比漏判更糟,因为会拿 B 的坐标去算 `coord_mae_km`,**诬告坐标是编的**。

2. **拿名字去高德搜一次,搜得到自己吗** (`data/frozen/amap/poi_name_check.json`,
   由 `check_poi_names.py` 生成)

**两个 oracle 都说不存在的,才是真正的编造嫌疑。** 光看 poi_support_rate
的数字是分不出这三者的 —— 这就是要做审计的原因。

**用法**

    cd backend
    ./venv/Scripts/python.exe scripts/audit_unmatched.py                 # 审所有 tag
    ./venv/Scripts/python.exe scripts/audit_unmatched.py --tag baseline --show 20
"""

from __future__ import annotations

import argparse
import io
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.eval import config as cfg  # noqa: E402
from app.eval.metrics_grounding import (  # noqa: E402
    best_match,
    candidate_names,
    checked_verdict,
    load_inventory,
    load_name_check,
    name_check_for,
    name_similarity,
    normalize_name,
)

PLANS_DIR = BACKEND_DIR / "data" / "frozen" / "plans"


def best_candidate(name: str, pois: list[dict[str, Any]]) -> tuple[float, str, str]:
    """库存里最像的那个:返回 (相似度, 候选名, 官方名)。"""
    best: tuple[float, str, str] = (0.0, "", "")
    for p in pois:
        for c in candidate_names(p):
            s = name_similarity(name, c)
            if s > best[0]:
                best = (s, c, str(p.get("name") or ""))
    return best


def collect_unmatched(tag: str | None) -> list[tuple[str, str, str]]:
    """(城市, 原始名, request_id)—— 所有没在库存里匹配上的景点名。"""
    inventory = load_inventory()
    out: list[tuple[str, str, str]] = []
    tag_dirs = sorted(d for d in PLANS_DIR.iterdir() if d.is_dir()) if PLANS_DIR.exists() else []
    if tag is not None:
        tag_dirs = [d for d in tag_dirs if d.name == tag]

    for d in tag_dirs:
        for f in sorted(d.glob("*.json")):
            try:
                import json

                with open(f, encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, ValueError):
                continue
            city = str((payload.get("request") or {}).get("city") or "").strip()
            entry = (inventory.get("cities") or {}).get(city)
            if not isinstance(entry, dict):
                continue
            pois = [p for p in (entry.get("pois") or []) if isinstance(p, dict)]
            days = (payload.get("plan") or {}).get("days") or []
            rid = str(payload.get("request_id") or f.stem)
            for day in days:
                for a in (day.get("attractions") or []):
                    name = str((a or {}).get("name") or "").strip()
                    if not name:
                        continue
                    poi, _ = best_match(name, pois)
                    if poi is None:
                        out.append((city, name, rid))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="审计 poi_support_rate 里未命中的名字")
    ap.add_argument("--tag", default=None, help="只看某个冻结 tag(默认全部)")
    ap.add_argument("--show", type=int, default=0, help="逐条列出前 N 个(默认不列)")
    args = ap.parse_args()

    rows = collect_unmatched(args.tag)
    if not rows:
        print("没有未命中的景点名。")
        return 0

    inventory = load_inventory()
    check = load_name_check()

    buckets: dict[str, list[tuple[str, str, tuple[float, str, str]]]] = defaultdict(list)
    verdicts: Counter[str] = Counter()
    uniq = sorted({(c, n) for c, n, _ in rows})

    for city, name in uniq:
        pois = ((inventory.get("cities") or {}).get(city) or {}).get("pois") or []
        best = best_candidate(name, pois)
        s = best[0]
        key = ("阈值边缘(0.6~0.75)" if s >= 0.6
               else "有点像(0.4~0.6)" if s >= 0.4
               else "完全不像(<0.4)")
        buckets[key].append((city, name, best))
        verdicts[checked_verdict(name_check_for(city, name, check))] += 1

    total = len(uniq)
    print("=" * 76)
    print(f"未命中的景点名审计  (tag={args.tag or '全部'})   命中阈值={cfg.POI_NAME_MATCH_RATIO}")
    print("=" * 76)
    print(f"出现 {len(rows)} 次,去重后 {total} 个不同的 (城市, 名字)\n")

    print("【一】我录的库存里,最像的那个有多像")
    print("-" * 76)
    for k in ("阈值边缘(0.6~0.75)", "有点像(0.4~0.6)", "完全不像(<0.4)"):
        n = len(buckets.get(k, []))
        print(f"  {k:20} {n:4} 个  {100 * n / total:5.1f}%")
    edge = buckets.get("阈值边缘(0.6~0.75)", [])
    if edge:
        print()
        print("  ⚠️ 「阈值边缘」是**假阳性风险区** —— 差一点就会被认成别人,")
        print("     那会拿别人的坐标去算 coord_mae_km,诬告坐标是编的。前 8 个:")
        for city, name, (s, cand, official) in edge[:8]:
            print(f"       {name:26} → {cand!r} ({s:.2f}, 官方名 {official!r}, {city})")
    print()

    print("【二】拿名字去高德搜一次,搜得到自己吗")
    print("-" * 76)
    if not check:
        print("  (没有 poi_name_check.json —— 先跑 scripts/check_poi_names.py,否则这一节是空的)")
    else:
        for k, desc in (("found", "搜得到自己 → **库存漏了,模型没问题**"),
                        ("weak", "只搜到有点像的 → 名字可能拼得太长,存疑"),
                        ("missing", "搜出来全是**不相干的** POI → **编造嫌疑**"),
                        ("unknown", "没查过(这个名字是这份行程独有的?)")):
            n = verdicts.get(k, 0)
            if n:
                print(f"  {k:8} {n:4} 个  {100 * n / total:5.1f}%   {desc}")

        both = [(city, name) for city, name, _ in buckets.get("完全不像(<0.4)", [])
                if checked_verdict(name_check_for(city, name, check)) == "missing"]
        print()
        print(f"  **两个 oracle 都说不存在的只有 {len(both)} 个** —— 这才是该去看的清单:")
        for city, name in both[:20]:
            rec = name_check_for(city, name, check) or {}
            print(f"       {city:4} {name:30} 高德搜到的是 {rec.get('best_name') or '(空)'!r}")
    print()

    print("【三】结论怎么读")
    print("-" * 76)
    print("  · 「阈值边缘」占比高     → 阈值太松,存在假阳性风险,该收紧")
    print("  · 「高德搜得到自己」占比高 → 指标偏低是**库存不全**造成的,不是模型在编")
    print("  · 两个 oracle 都说不存在  → 这才是真正的编造嫌疑清单")
    print()

    if args.show:
        print("【附】逐条明细")
        print("-" * 76)
        all_rows = [r for k in buckets for r in buckets[k]]
        for city, name, (s, cand, _) in all_rows[: args.show]:
            v = checked_verdict(name_check_for(city, name, check))
            print(f"  {city:4} {name:30} 库存最像={s:.2f} {cand!r:22} 高德={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
