"""RAG 环境自检

在写任何 RAG 业务代码之前,先跑这个脚本确认前置条件全部成立。

用法(在 backend/ 目录下):
    venv\\Scripts\\python.exe scripts/check_rag_env.py

为什么需要它:框架的 embedding 回退链会【静默吞掉异常】逐级降级
(dashscope -> local -> tfidf),而 get_dimension() 也会吞异常返回默认 384。
结果是:用 384 维建好一个空 collection,之后所有检索静默返回 [],
不报任何错。这个脚本就是用来提前抓住这种情况的。

第 [4/5] 项是**真的搜一次**,专门对付这类"前面全绿、检索却废了"的故障 ——
`has_collection` / `get_collection_stats` 都不需要 load,所以光看连通性和
计数是验证不出检索是否可用的。相关背景见 docs/DEPLOYMENT.md 8.3
「重启后的静默失效」。

⚠️ 用 Milvus Lite(方式 A)时,本脚本是**独立进程**,而 Lite 对数据目录加
独占锁 —— 后端容器还开着的话会报 DataDirLockedError。要么先停后端,
要么直接走 HTTP 接口验证:
    curl -s -X POST http://127.0.0.1:8080/api/knowledge/search \\
         -H 'Content-Type: application/json' \\
         -d '{"query":"杭州 西湖","namespace":"all","top_k":3}'
"""

import io
import sys
from pathlib import Path

# 让脚本无论从哪个目录执行都能 import 到 app 包
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Windows 控制台默认 GBK,强制 UTF-8 以免中文输出乱码
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

EXPECTED_DIMENSION = 1024  # BAAI/bge-m3 的维度

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))


def main() -> int:
    print("=" * 62)
    print("RAG 环境自检")
    print("=" * 62)

    # ---------- 0. 加载 .env ----------
    print("\n[0/5] 加载配置")
    try:
        from dotenv import load_dotenv

        load_dotenv(BACKEND_DIR / ".env", override=True)
        from app.config import get_settings

        settings = get_settings()
        record("读取 app.config", True, f"db={settings.db_path}")
    except Exception as e:
        record("读取 app.config", False, f"{type(e).__name__}: {e}")
        return report()

    # ---------- 1. Milvus 连通性 ----------
    print("\n[1/5] Milvus 向量库")
    existing: list[str] = []
    uri = (settings.milvus_uri or "http://localhost:19530").strip()
    # 没有 "://" 就是本地 .db 文件 → 嵌入式 Milvus Lite。
    # 两种模式探测方式不同:远端有 9091 的 healthz,Lite 是进程内实例、没有那个端口。
    is_lite = "://" not in uri
    try:
        from pymilvus import MilvusClient

        if not is_lite:
            import requests

            # healthz 比连 gRPC 更轻,失败了也不会挂住。
            # 但它只能说明进程活着、不代表 gRPC 连得上,所以下面还要真连一次。
            host = uri.split("//")[-1].split(":")[0]
            r = requests.get(f"http://{host}:9091/healthz", timeout=5)
            if r.status_code != 200:
                raise RuntimeError(f"healthz 返回 HTTP {r.status_code}")

        client = MilvusClient(uri=uri)

        # ⚠️ 这里**刻意不用** client.list_collections() 数个数:
        #    pymilvus 2.5.x 与 milvus-lite 3.x 的 protobuf 版本差会让**这一个**
        #    方法抛 `ShowCollectionsResponse has no "shards_num" field`
        #    (2.5.18 + 3.2.1 实测;远端模式的 pymilvus 走另一条代码路径,不受影响)。
        #    其余方法 —— create/has/upsert/flush/search/get_collection_stats/
        #    delete/drop —— 在 Lite 上逐个验过都正常,所以不用因噎废食。
        #    而我们只关心自己那三层,逐个 has_collection 问就够了,
        #    还顺带告诉用户哪层还没灌库。
        names = [
            settings.rag_collection_poi,
            settings.rag_collection_guides,
            settings.rag_collection_uploaded,
        ]
        existing = [n for n in names if client.has_collection(n)]
        record(
            "Milvus 可连接",
            True,
            f"{'本地嵌入式(Lite)' if is_lite else '远端'} {uri} —— "
            f"三层里已有 {len(existing)}/{len(names)}"
            + (f": {', '.join(existing)}" if existing else "(还没灌库)"),
        )
    except Exception as e:
        hint = (
            "嵌入式模式不需要额外的 Milvus 服务。两点常见原因:"
            "(1) 后端/其他进程正开着这个 .db —— Lite 是独占锁,"
            "报 DataDirLockedError 就先停掉它;"
            "(2) 报 PermissionError / FileNotFoundError 时,"
            "检查 TRIPMIND_MILVUS_URI 指向的目录是否存在且可写"
            if is_lite
            else "Milvus 服务起了吗?见 docs/DEPLOYMENT.md 第 8 节"
        )
        record("Milvus 可连接", False, f"{type(e).__name__}: {e} — {hint}")

    # ---------- 2. Embedding 后端 ----------
    print("\n[2/5] Embedding 模型")
    embedder_name = "?"
    dimension = -1
    try:
        from hello_agents.memory.embedding import get_text_embedder

        embedder = get_text_embedder()
        embedder_name = type(embedder).__name__

        # TFIDFEmbedding 是假兜底:未 fit() 时 encode() 会直接 raise
        if embedder_name == "TFIDFEmbedding":
            record(
                "Embedding 后端可用",
                False,
                "回退到了 TFIDFEmbedding(假兜底) — 检查 EMBED_API_KEY / EMBED_BASE_URL",
            )
        else:
            record("Embedding 后端可用", True, f"实际使用 {embedder_name}")
    except Exception as e:
        record("Embedding 后端可用", False, f"{type(e).__name__}: {e}")

    # ---------- 3. 维度校验(最关键) ----------
    print("\n[3/5] 向量维度")
    probe_vec: list[float] | None = None
    try:
        from hello_agents.memory.embedding import get_dimension, get_text_embedder

        dimension = get_dimension(EXPECTED_DIMENSION)
        vec = get_text_embedder().encode("测试")
        probe_vec = list(vec)  # 给第 4 步做真实检索用,省一次接口调用
        actual = len(vec)

        record("get_dimension() 返回真实值", dimension == EXPECTED_DIMENSION,
               f"得到 {dimension},期望 {EXPECTED_DIMENSION}")
        record("encode() 长度与维度一致", actual == dimension,
               f"encode 返回 {actual},维度声明 {dimension}")

        if dimension != EXPECTED_DIMENSION:
            print(
                "        ↳ 维度不对会导致 collection 建错,之后检索会【静默】返回空结果"
            )
    except Exception as e:
        record("向量维度校验", False, f"{type(e).__name__}: {e}")

    # ---------- 4. 检索可用性 ----------
    #
    # 这一项专门用来抓「重启后 collection 变成 released」那类**静默故障**。
    # 前面几项全绿也未必能搜出东西:has_collection / get_collection_stats
    # 都不需要 load,所以状态一切正常、检索却一条都返回不了。
    # 唯一的判据就是**真的搜一次**。
    print("\n[4/5] 检索可用性")
    if not existing:
        print("        ↳ 三层 collection 都还没建,无法验证 —— 灌库后再跑一次本脚本")
    elif probe_vec is None:
        print("        ↳ 上一步没拿到向量,跳过(先修好 embedding)")
    else:
        try:
            from pymilvus import MilvusClient as _MC

            _c = _MC(uri=uri)
            target = existing[0]
            # 先 load 再搜。这一句是**必须**的:collection 从磁盘恢复出来是
            # released 状态,不 load 直接 search 会抛 code=101。
            # 本脚本是独立进程,得自己负责这一步(应用里对应
            # MilvusKnowledgeStore._ensure_loaded())。
            _c.load_collection(target)
            record("加载 collection", True, f"{target} 已 load")

            hits = _c.search(target, data=[probe_vec], limit=1)
            n = len(hits[0]) if hits else 0
            record(
                "真实检索有返回",
                n > 0,
                f"{target} 返回 {n} 条"
                + (
                    ""
                    if n > 0
                    else " —— 能连上但搜不出东西:collection 可能是空的(dim 不匹配时"
                    "会是这种表现),建议重灌一次库"
                ),
            )
        except Exception as e:
            record(
                "真实检索有返回",
                False,
                f"{type(e).__name__}: {e} — 若是 code=101 'released',"
                "见 docs/DEPLOYMENT.md 8.3「重启后的静默失效」",
            )

    # ---------- 5. 文档解析依赖 ----------
    print("\n[5/5] 文档解析")
    try:
        import markitdown  # noqa: F401

        record("markitdown 可 import", True)
    except ImportError as e:
        record("markitdown 可 import", False, f"{e} — 试: pip install markitdown")

    return report(collection_hint=dimension if dimension > 0 else None)


def report(collection_hint: int | None = None) -> int:
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    failed = [name for name, ok, _ in results if not ok]

    print("\n" + "=" * 62)
    if failed:
        print(f"结果: {passed}/{total} 通过 —— 有 {len(failed)} 项失败")
        print("\n需要修复:")
        for name in failed:
            print(f"  - {name}")
        print("=" * 62)
        return 1

    print(f"结果: {passed}/{total} 全部通过 ✅")
    if collection_hint:
        print(f"\n提示:collection 名字建议带上维度,例如 trip_kb_{collection_hint}")
        print("      换 embedding 模型时【必须】换 collection 名或删库重建,")
        print("      否则维度不匹配会让检索静默返回空结果。")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
