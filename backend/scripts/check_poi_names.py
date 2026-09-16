"""检查行程里的景点名「到底是不是真的存在」—— 用高德的 POI 搜索当 oracle

**它解决什么问题**

`poi_support_rate` 拿景点名去**冻结的 POI 库存**里做字符串匹配。但那个库存是
20 个关键词搜出来的**样本**(每城 340~360 个),不是普查 —— 于是「国子监」
「798艺术中心」「上海中心大厦」这类有名但没被关键词命中的地方全被判成
"查无此物"。baseline 实测:41 个"未命中"里**没有一个**是编造的名字。

那个指标测的其实是"我的库存覆盖了多少",不是"模型编没编"。
要测后者,得找一个**不受我的采样限制**的 oracle。

⚠️ 踩过的坑:`maps_geo` 看起来像那个 oracle,**但它不是**
------------------------------------------------------
第一版用了 `maps_geo`(地理编码),因为试 `国子监` 返回了 `level=兴趣点` 就以为能用。
结果 74 个名字跑下来:

    外滩     → level=住宅区        南京路步行街 → level=道路
    豫园     → level=乡镇          东方明珠     → **返回空**
    宽窄巷子  → level=道路          苏堤        → level=道路

**东方明珠是上海最著名的地标,`maps_geo` 返回空。** 原因是 `maps_geo` 做的是
**地址→坐标**的解析,它把输入当地址看。POI 名不是地址,所以地标名解析不出来;
而"外滩"这种区域名会被解析成"住宅区"这种行政区划。

**正确做法是 `maps_text_search`(POI 关键词搜索)** —— 就是录库存用的那个工具。
实测判别很干净:

    东方明珠          → 东方明珠广播电视塔        ✅
    外滩              → 外滩                      ✅
    宽窄巷子          → 宽窄巷子景区              ✅
    紫金幻梦星际主题乐园 → 泡泡玛特城市乐园 …      ❌(编的名字,返回不相干的)
    火星大饭店北京分店   → 南京大饭店 …            ❌
    孙庆海历史博物馆    → 孙庆海历史博物馆         ✅(是真的,库存里没有而已)

编造的名字会拿回一堆**不相干的** POI,名字相似度自然低。

**缓存里存的是「事实」不是「判定」**

只记 `best_similarity`(搜到的 POI 名里跟查询名最像的那个有多像),**不记**
"是否命中"。因为命中阈值是 `config.POI_NAME_MATCH_RATIO`,它随时可能调整 ——
存判定的话,一改阈值整份缓存就作废了。存原始相似度,改阈值只是重算。

**用法**

    cd backend
    ./venv/Scripts/python.exe scripts/check_poi_names.py --dry-run   # 先看要发多少请求
    ./venv/Scripts/python.exe scripts/check_poi_names.py             # 真查(高德额度内免费)

结果冻结到 `data/frozen/amap/poi_name_check.json`,要提交进仓库。
已查过的 (城市, 名字) 会跳过,所以重跑很便宜。
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
# 复用 recorder.py 的超时保护(坑 #5:高德不回应时 MCPTool.run() 会永久卡死,实测卡过 17 分钟)
sys.path.insert(0, str(Path(__file__).resolve().parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.eval.metrics_grounding import name_similarity, normalize_name  # noqa: E402
from app.services.amap_parsing import unwrap_mcp_result  # noqa: E402
from recorder import call_with_timeout  # noqa: E402

FROZEN_DIR = BACKEND_DIR / "data" / "frozen"
AMAP_DIR = FROZEN_DIR / "amap"
CHECK_PATH = AMAP_DIR / "poi_name_check.json"
PLANS_DIR = FROZEN_DIR / "plans"

# 每次查询要发多少次搜索。高德按相关度排序,第一页就够 ——
# 真实景点必然排在前几个,编造的名字则一个都排不上。
_SEARCH_LIMIT = 20
# 缓存里留几个候选名,方便事后审计("它当时搜到了什么")
_KEEP_TOP = 5


def collect_names(tags: list[str] | None = None) -> list[tuple[str, str, str]]:
    """遍历 `data/frozen/plans/*/`,收集所有 (城市, 查询名, 原始名) 并去重。

    查询名用 `normalize_name` 处理 —— 和字符串匹配用**同一套归一化**,两个指标
    比的才是同一个东西。括号里通常是模型加的限定语(`曲院风荷（荷花区）`),
    拿它去搜会因为奇怪的理由失败。
    """
    if not PLANS_DIR.exists():
        return []
    tag_dirs = sorted(d for d in PLANS_DIR.iterdir() if d.is_dir())
    if tags:
        tag_dirs = [d for d in tag_dirs if d.name in set(tags)]

    seen: dict[tuple[str, str], str] = {}
    for d in tag_dirs:
        for f in sorted(d.glob("*.json")):
            try:
                with open(f, encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue
            city = str((payload.get("request") or {}).get("city") or "").strip()
            days = (payload.get("plan") or {}).get("days") or []
            if not city or not isinstance(days, list):
                continue
            for day in days:
                for a in (day.get("attractions") or []):
                    raw = str((a or {}).get("name") or "").strip()
                    q = normalize_name(raw)
                    if q:
                        seen.setdefault((city, q), raw)

    return [(city, q, raw) for (city, q), raw in sorted(seen.items())]


def load_cache() -> dict[str, Any]:
    if not CHECK_PATH.exists():
        return {"version": "1.0", "entries": {}}
    try:
        with open(CHECK_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"⚠️  {CHECK_PATH} 读不出来,当成空缓存")
        return {"version": "1.0", "entries": {}}
    data.setdefault("entries", {})
    return data


def save_cache(cache: dict[str, Any]) -> None:
    """newline="" —— 不把 LF 翻译成 CRLF(Windows 上 Path.write_text 会,踩过)。"""
    AMAP_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHECK_PATH, "w", encoding="utf-8", newline="") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _key(city: str, name: str) -> str:
    return f"{city}|{name}"


def query_one(tool: Any, city: str, name: str, timeout: float) -> dict[str, Any]:
    """搜一次,返回要写进缓存的那条**事实**记录。"""
    raw = call_with_timeout(
        lambda: tool.run({
            "action": "call_tool",
            "tool_name": "maps_text_search",
            "arguments": {"keywords": name, "city": city, "citylimit": "true"},
        }),
        timeout,
    )
    data = unwrap_mcp_result(raw)
    pois = (data or {}).get("pois") if isinstance(data, dict) else None
    pois = [p for p in (pois or []) if isinstance(p, dict)][:_SEARCH_LIMIT]

    names = [str(p.get("name") or "") for p in pois]
    best_name, best_sim = "", 0.0
    for n in names:
        s = name_similarity(name, n)
        if s > best_sim:
            best_sim, best_name = s, n

    return {
        "query": name,
        "city": city,
        "poi_count": len(pois),
        "best_similarity": round(best_sim, 4),
        "best_name": best_name,
        "top_names": names[:_KEEP_TOP],
    }


def _report(entries: dict[str, Any]) -> int:
    from app.eval import config as cfg

    if not entries:
        print("缓存是空的。")
        return 0

    thr = cfg.POI_NAME_MATCH_RATIO
    total = len(entries)
    buckets = {"命中(>=阈值)": 0, "有点像(0.5~阈值)": 0, "完全不像(<0.5)": 0}
    for v in entries.values():
        s = v.get("best_similarity") or 0.0
        if s >= thr:
            buckets["命中(>=阈值)"] += 1
        elif s >= 0.5:
            buckets["有点像(0.5~阈值)"] += 1
        else:
            buckets["完全不像(<0.5)"] += 1

    print()
    print("=" * 70)
    print(f"缓存共 {total} 条 (城市, 景点名)   命中阈值 = {thr}")
    print("=" * 70)
    for k, n in buckets.items():
        print(f"  {k:16} {n:5}  {100 * n / total:5.1f}%")
    print()
    print("  「完全不像」这一档才是编造嫌疑 —— 高德搜出来一堆不相干的 POI。")
    low = sorted((v.get("best_similarity") or 0.0, k, v) for k, v in entries.items())
    for s, k, v in low[:12]:
        print(f"    {s:.2f}  {v['query']:28} 搜到的是 {v.get('best_name') or '(空)'!r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="用 maps_text_search 检查景点名是否真实存在")
    ap.add_argument("--dry-run", action="store_true", help="只列要查什么,不发请求")
    ap.add_argument("--tag", action="append", help="只处理某个冻结 tag(可多次)")
    ap.add_argument("--limit", type=int, default=None, help="最多查几个(调试用)")
    ap.add_argument("--timeout", type=float, default=30.0, help="单次调用超时秒数")
    ap.add_argument("--save-every", type=int, default=20, help="每查多少个存一次盘")
    ap.add_argument("--report", action="store_true", help="只统计现有缓存,不发请求")
    args = ap.parse_args()

    cache = load_cache()
    entries: dict[str, Any] = cache["entries"]

    if args.report:
        return _report(entries)

    wanted = collect_names(args.tag)
    todo = [(c, n, r) for c, n, r in wanted if _key(c, n) not in entries]
    if args.limit is not None:
        todo = todo[: args.limit]

    print(f"冻结行程里共 {len(wanted)} 个不重复的 (城市, 景点名);本次要查 {len(todo)} 个")

    if args.dry_run:
        for c, n, r in todo[:30]:
            print(f"   {c:4} {n}" + (f"   (原始名 {r})" if r != n else ""))
        if len(todo) > 30:
            print(f"   ... 还有 {len(todo) - 30} 个")
        print(f"[dry-run] 会发 {len(todo)} 次 maps_text_search(**高德额度内免费**)。没发任何请求")
        return 0

    if not todo:
        print("没有新名字要查。")
        return _report(entries)

    from app.services.amap_service import get_amap_mcp_tool

    tool = get_amap_mcp_tool()

    failed = 0
    for i, (city, name, raw) in enumerate(todo, 1):
        try:
            rec = query_one(tool, city, name, args.timeout)
        except Exception as e:  # noqa: BLE001 —— 单个失败不该中断整批
            failed += 1
            print(f"[{i}/{len(todo)}] ⚠️  {city} {name}: {type(e).__name__}: {e}")
            continue
        rec["raw_name"] = raw
        entries[_key(city, name)] = rec

        # **每 save_every 个存一次盘**,不是最后统一存。
        # 第一次写这个脚本时是最后才写的 —— 中途挂掉 74 次查询全丢。
        # recorder.py 也是每 20 条存一次,同一个道理。
        if i % args.save_every == 0:
            _stamp(cache)
            save_cache(cache)
            print(f"   ... {i}/{len(todo)}  (已存盘)")

    _stamp(cache)
    save_cache(cache)

    print(f"\n查到 {len(todo) - failed} 个,失败 {failed} 个")
    print(f"缓存已更新: {CHECK_PATH}(共 {len(entries)} 条)")
    return _report(entries)


def _stamp(cache: dict[str, Any]) -> None:
    cache.update({
        "version": "1.0",
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": [
            "maps_text_search 的结果,按「城市|归一化后的景点名」存。",
            "存的是**事实**(best_similarity)不是判定 —— 阈值改了只需重算,缓存不作废。",
            "best_similarity 是搜到的 POI 名里跟查询名最像的那个的相似度;",
            "编造的名字会搜出一堆不相干的 POI,这个值自然低。",
            "这是 poi_exists_rate 的 ground truth,会提交进仓库。",
        ],
    })


if __name__ == "__main__":
    raise SystemExit(main())
