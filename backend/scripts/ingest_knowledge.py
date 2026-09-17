"""把知识灌进 Milvus —— `poi_facts` 与 `city_guides` 两层

用法(在 `backend/` 目录下):

    # 先看要发多少次 embedding 请求,不花钱
    venv\\Scripts\\python.exe scripts/ingest_knowledge.py --dry-run

    # 灌 POI 事实层(1750 条,来自冻结的库存,不调高德)
    venv\\Scripts\\python.exe scripts/ingest_knowledge.py --source=frozen
    # 灌城市攻略层(5 篇手写 markdown)
    venv\\Scripts\\python.exe scripts/ingest_knowledge.py --source=guides
    # 两层都灌
    venv\\Scripts\\python.exe scripts/ingest_knowledge.py --source=all

    # 检索冒烟测试 —— **这是 P7 的验收依据**,能看到命中的是真景点还是胡编
    venv\\Scripts\\python.exe scripts/ingest_knowledge.py --query "北京 历史文化" --query "上海 夜景"

⚠️ **会调 embedding 接口**(花你自己的额度)。虽然 bge-m3 很便宜、
1750 条总共约 40k token,但仍然是真花钱的一步 —— 所以 `--dry-run` 默认建议先跑。

⚠️ 重复执行是**幂等**的:行 id 用高德 POI id / 「文件名+分块序号」,
所以 upsert 会覆盖旧行。改完攻略直接重跑,不用先删库。
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Windows 控制台默认 GBK,强制 UTF-8 以免中文输出乱码
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.config import get_settings  # noqa: E402
from app.services.knowledge_service import KnowledgeService  # noqa: E402


def do_dry_run(service: KnowledgeService, settings) -> None:
    print("=" * 66)
    print("演练模式 —— 只统计条数,不调用 embedding 接口,不花钱")
    print("=" * 66)

    # 统计逻辑在 KnowledgeService 里,与 HTTP 接口共用同一份 ——
    # 以前这两个函数是脚本私有的,做成接口时就得复制一遍,
    # 那种「预览说 30 块、实际灌进去 24 块」的漂移迟早会发生。
    print("\n[poi_facts] 数据源: data/frozen/amap/poi_inventory.json")
    poi = service.preview_poi_facts()
    for city, n in (poi.get("per_city") or {}).items():
        print(f"    {city}: {n} 条")
    poi_total = int(poi.get("total") or 0)
    print(f"    合计 {poi_total} 条")

    print("\n[city_guides] 数据源: data/knowledge_base/city_guides/*.md")
    guides = service.preview_city_guides()
    for name, n in (guides.get("per_file") or {}).items():
        print(f"    {name}: {n} 块")
    guide_total = int(guides.get("total") or 0)
    print(f"    合计 {guide_total} 块")

    total = poi_total + guide_total
    print(f"\n总计 {total} 条会被编码,约 {-(-total // 32)} 次批量请求(每批 32 条)")
    print("去掉 --dry-run 即真正执行。")


def do_ingest(service: KnowledgeService, source: str, recreate: bool) -> None:
    results: list[dict] = []
    if source in ("frozen", "all"):
        print("\n▶ 灌 poi_facts ...")
        results.append(service.ingest_poi_facts(recreate=recreate))
    if source in ("guides", "all"):
        print("\n▶ 灌 city_guides ...")
        results.append(service.ingest_city_guides(recreate=recreate))

    print("\n" + "=" * 66)
    print("完成")
    print("=" * 66)
    for r in results:
        print(f"  {r['namespace']:12s} → collection {r['collection']}  {r['count']} 条")
        if "files" in r:
            for name, n in r["files"].items():
                print(f"        {name}: {n} 块")


def do_query(service: KnowledgeService, queries: list[str], top_k: int) -> None:
    print("=" * 66)
    print("检索冒烟测试")
    print("=" * 66)
    for q in queries:
        print(f"\n▼ 查询: {q!r}")
        # 与 HTTP 检索接口共用同一个归并实现
        hits = service.retrieve_merged(q, top_k)
        if not hits:
            print("    (没有命中 —— collection 建了吗?跑过 ingest 了吗?)")
            continue
        for i, h in enumerate(hits[:top_k], 1):
            where = f"{h.source} > {h.heading_path}" if h.heading_path else h.source
            head = h.content.replace("\n", " ")[:110]
            print(f"    [{i}] {h.score:.4f}  {where}")
            print(f"        {head}")


def main() -> int:
    p = argparse.ArgumentParser(description="把知识灌进 Milvus")
    p.add_argument("--source", choices=["frozen", "guides", "all"], default="all")
    p.add_argument("--recreate", action="store_true", help="先删掉 collection 再建(默认 upsert 覆盖)")
    p.add_argument("--dry-run", action="store_true", help="只统计条数,不调用 embedding 接口")
    p.add_argument("--query", action="append", default=[], help="检索测试,可重复传多次")
    p.add_argument("--top-k", type=int, default=3)
    args = p.parse_args()

    settings = get_settings()
    service = KnowledgeService(settings)

    print(f"Milvus: {settings.milvus_uri}")
    print(f"collection: {settings.rag_collection_poi} / {settings.rag_collection_guides}")

    if args.dry_run:
        do_dry_run(service, settings)
        return 0

    # 先确认 embedder 和 Milvus 都能用,再动手 —— 免得跑到一半才发现连不上
    ok, reason = service.available()
    print(f"环境自检: {'通过' if ok else '不通过'} — {reason}")
    if not ok:
        print("\n环境不通,先修这个再灌库。常见原因:")
        print("  - Milvus 没起来:  cd D:/devlop/Milvus && docker compose up -d")
        print("  - EMBED_API_KEY / EMBED_BASE_URL 没配:  检查 backend/.env")
        return 1

    if args.source:
        do_ingest(service, args.source, args.recreate)

    qs = args.query or ["北京 历史文化 一日游"]
    do_query(service, qs, args.top_k)

    print("\n最终状态:")
    import json as _json

    print(_json.dumps(service.stats(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
