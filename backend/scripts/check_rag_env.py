"""RAG 环境自检

在写任何 RAG 业务代码之前,先跑这个脚本确认前置条件全部成立。

用法(在 backend/ 目录下):
    venv\\Scripts\\python.exe scripts/check_rag_env.py

为什么需要它:框架的 embedding 回退链会【静默吞掉异常】逐级降级
(dashscope -> local -> tfidf),而 get_dimension() 也会吞异常返回默认 384。
结果是:用 384 维建好一个空 collection,之后所有检索静默返回 [],
不报任何错。这个脚本就是用来提前抓住这种情况的。
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
    print("\n[0/4] 加载配置")
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
    print("\n[1/4] Milvus 向量库")
    try:
        import requests

        # Milvus 的 9091 端口有 healthz,比连 gRPC 更轻,失败了也不会挂住
        url = (settings.milvus_uri or "http://localhost:19530").rstrip("/")
        host = url.split("//")[-1].split(":")[0]
        r = requests.get(f"http://{host}:9091/healthz", timeout=5)
        if r.status_code == 200:
            # 健康检查过了再真连一次 pymilvus,并数一数 collection ——
            # healthz 只能说明进程活着,不代表 gRPC 能连上
            from pymilvus import MilvusClient

            client = MilvusClient(uri=url)
            cols = client.list_collections()
            record("Milvus 可连接", True, f"{url}  ({len(cols)} 个 collection)")
        else:
            record("Milvus 可连接", False, f"healthz HTTP {r.status_code}")
    except Exception as e:
        record(
            "Milvus 可连接",
            False,
            f"{type(e).__name__} — 容器起了吗?试: cd D:/devlop/Milvus && docker compose up -d",
        )

    # ---------- 2. Embedding 后端 ----------
    print("\n[2/4] Embedding 模型")
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
    print("\n[3/4] 向量维度")
    try:
        from hello_agents.memory.embedding import get_dimension, get_text_embedder

        dimension = get_dimension(EXPECTED_DIMENSION)
        vec = get_text_embedder().encode("测试")
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

    # ---------- 4. 文档解析依赖 ----------
    print("\n[4/4] 文档解析")
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
