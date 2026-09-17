"""知识库(RAG)API 路由

把 `KnowledgeService` 的能力暴露成 HTTP —— 在此之前,这些能力**只能通过
命令行脚本访问**(`scripts/ingest_knowledge.py`),所以前端没有任何接入点。

接口与 CLI 参数一一对应:

| 接口 | 对应 CLI |
|---|---|
| GET  /knowledge/status        | (无参启动时的环境自检 + 末尾的 stats 输出) |
| POST /knowledge/search        | `--query` / `--top-k` |
| POST /knowledge/ingest/preview| `--dry-run` |
| POST /knowledge/ingest        | `--source` / `--recreate` |
| GET  /knowledge/sources       | (新增:数据目录元信息) |

两处与 CLI 不同的设计,都是必须的:

1. **检索失败要能被看见。** `KnowledgeService.retrieve()` 出于「RAG 是增强不是
   依赖」的考虑,把异常吞掉并返回空列表。这在服务内部是对的,但直接暴露成
   接口就会让前端把「连不上 Milvus」显示成「没有命中」。所以这里把
   `service.last_error` 一并返回。

2. **灌库必须是长任务。** 1750 条要分 55 批调 embedding,分钟级;而前端
   axios 超时是 120 秒(`frontend/src/services/api.ts`)。同步接口必然超时,
   且用户看不到任何进度。所以改成「后台线程 + 内存任务表 + 前端轮询」。

注意这里**不能**照抄 `api/routes/trip.py` 里 `plan_trip` 的同步 `def` 做法 ——
那个是一次 LLM 调用约 60 秒,这个要 55 次网络请求。
"""

import threading
import time
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ...config import get_settings, rag_state, set_rag_enabled
from ...services.doc_parser import ParseError, chunk_document, parse_file, parse_paste
from ...services.knowledge_service import EMBED_BATCH, get_knowledge_service
from ...store.doc_store import DocStore

router = APIRouter(prefix="/knowledge", tags=["知识库"])

NamespaceFilter = Literal["all", "poi_facts", "city_guides", "uploaded"]
IngestSource = Literal["frozen", "guides", "all"]

# ===========================================================================
# 响应模型
# ===========================================================================


class KnowledgeNamespaceStat(BaseModel):
    collection: str
    exists: Optional[bool] = None
    count: Optional[int] = None
    error: Optional[str] = None


class KnowledgeStatusResponse(BaseModel):
    success: bool
    # enabled 与 available 是**两件不同的事**,刻意分开:
    #   enabled   = 生成行程时注入不注入知识库(.env 基线 或 业务流程的运行时覆盖)
    #   available = embedding 与 Milvus 是否都能用,决定「知识库能不能查/能不能灌」
    # 现状是用户灌完库、检索也通,但 ENABLE_RAG 默认 false,生成时完全不用它 ——
    # 而界面上无从得知。这一对字段就是给界面把这个状态摊开用的。
    enabled: bool
    # 'env' = 来自 .env(评测基线);'runtime' = 业务流程(上传攻略)临时打开的。
    # 区分它是因为:运行时开启**重启即失效**,界面要提示"这不是永久的"。
    enabled_source: str = "env"
    available: bool
    reason: str
    milvus_uri: str
    last_error: Optional[str] = None
    dim_expected: int
    top_k: int
    collections: dict[str, str]
    poi_facts: Optional[KnowledgeNamespaceStat] = None
    city_guides: Optional[KnowledgeNamespaceStat] = None
    uploaded: Optional[KnowledgeNamespaceStat] = None
    # 上传文档的概览(各状态几份、共多少块)。知识库页用它渲染"我上传的攻略"。
    docs: Optional[dict[str, Any]] = None


class SearchRequest(BaseModel):
    query: str = Field(..., description="检索语句", example="北京 历史文化")
    namespace: NamespaceFilter = Field(default="all", description="检索哪一层")
    top_k: Optional[int] = Field(default=None, ge=1, le=50, description="返回条数")


class SearchHit(BaseModel):
    namespace: str
    content: str
    score: float
    source: str
    heading_path: str


class SearchResponse(BaseModel):
    success: bool
    hits: list[SearchHit] = []
    elapsed_ms: int = 0
    # 一层成功一层失败时为 true —— 前端要提示「仅部分层返回结果」,
    # 否则用户会把「POI 层挂了」误读成「POI 层里没有」。
    partial: bool = False
    # 检索失败原因。retrieve() 内部吞了异常,不从这里透出来的话,
    # 「连不上 Milvus」和「没有命中」在前端长得一模一样。
    error: Optional[str] = None
    namespace_counts: dict[str, int] = {}


class IngestPreviewResponse(BaseModel):
    success: bool
    source: IngestSource
    poi: dict[str, Any]
    guides: dict[str, Any]
    embed_requests: int
    note: str


class IngestRequest(BaseModel):
    source: IngestSource = Field(default="all", description="灌哪一层")
    recreate: bool = Field(
        default=False,
        description="是否先删除 collection 再重建。**破坏性**,需要同时传 confirm='DROP'",
    )
    confirm: str = Field(default="", description="recreate=true 时必须为 'DROP'")


class IngestTaskResponse(BaseModel):
    success: bool
    task_id: str
    state: str
    source: str
    recreate: bool
    total: int
    processed: int
    current: Optional[str] = None
    started_at: str
    finished_at: Optional[str] = None
    results: list[dict[str, Any]] = []
    error: Optional[str] = None
    message: str = ""


class SourcesResponse(BaseModel):
    success: bool
    poi_inventory: dict[str, Any]
    city_guides: dict[str, Any]


# ---- 上传攻略（用户文档） ----


class DocChunkPreview(BaseModel):
    idx: int
    heading_path: str
    chars: int
    snippet: str


class DocParseResponse(BaseModel):
    """解析结果 + 入库前预览。

    解析与入库**分成两步**,是刻意保留原「预览 → 灌库」的设计:
    入库要真金白银调 embedding,而且一旦切坏了,坏数据就在库里了 ——
    让用户先看到"这篇会切成几块、每块讲什么",再决定要不要入库。
    对 PDF 这一步尤其重要:没有文字层的文件在这一步就会被拦下来,
    不会等到生成行程时才发现检索全是空的。
    """

    success: bool
    doc_id: str
    title: str
    origin: str
    char_count: int
    chunk_count: int
    # 预估要发的 embedding 请求次数(不是条数)。用户应当知道自己花了多少。
    embed_requests: int
    # 同一份内容之前已经传过。**不拦着**,但要说出来 ——
    # 否则库里会堆重复内容,检索返回的几条全是同一段话。
    duplicate: bool = False
    existing_status: Optional[str] = None
    warnings: list[str] = []
    preview: list[DocChunkPreview] = []


class DocSummary(BaseModel):
    id: str
    title: str
    origin: str
    char_count: int
    chunk_count: int
    status: str
    error: Optional[str] = None
    created_at: str
    ingested_at: Optional[str] = None


class DocListResponse(BaseModel):
    success: bool
    docs: list[DocSummary] = []
    summary: dict[str, Any] = {}


class DocDetailResponse(BaseModel):
    success: bool
    doc: dict[str, Any]
    chunks: list[DocChunkPreview] = []


class DocDeleteResponse(BaseModel):
    success: bool
    message: str
    deleted_chunks: int


class RagToggleRequest(BaseModel):
    enabled: bool


class RagToggleResponse(BaseModel):
    success: bool
    enabled: bool
    enabled_source: str
    message: str


# ===========================================================================
# 超时保护
# ===========================================================================
#
# `stats()` 会对每层调一次 `exists()` + `count()`,都是网络请求;
# Milvus 没起来时建连可能长时间阻塞。前端在页面加载时直接打这个接口,
# 一旦挂住页面就一直转圈。
#
# 这里用线程池包一层超时:超时后 HTTP 请求能正常返回,
# 代价是那个后台线程还会跑到自己结束(无法真正取消)。对这个规模的应用可以接受。

_EXECUTOR_LOCK = threading.Lock()
_executor: Any = None


def _get_executor():
    global _executor
    if _executor is None:
        from concurrent.futures import ThreadPoolExecutor

        with _EXECUTOR_LOCK:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="knowledge")
    return _executor


def _with_timeout(fn, timeout: float):
    from concurrent.futures import TimeoutError as FutureTimeout

    future = _get_executor().submit(fn)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        raise TimeoutError(f"操作超过 {timeout:.0f} 秒未返回")


# ===========================================================================
# 灌库任务表（内存）
# ===========================================================================
#
# 放内存而不是落库:与项目在 SQLite 上的取舍一致 —— 表就两个,不值得为它
# 再引一层抽象。进程重启后任务记录丢失是可接受的(用户重发一次)。
#
# 同一时刻只允许一个灌库任务:两个任务写同一个 collection 会互相干扰,
# 表现为计数对不上、检索结果时好时坏,排查起来非常费劲。

_TASKS: dict[str, dict] = {}
_TASKS_LOCK = threading.Lock()
_RUNNING: Optional[str] = None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _public_task(task: dict) -> dict:
    """任务表对外只读输出。锁内调用。"""
    return {
        "success": True,
        "task_id": task["task_id"],
        "state": task["state"],
        "source": task["source"],
        "recreate": task["recreate"],
        "total": task["total"],
        "processed": task["processed"],
        "current": task["current"],
        "started_at": task["started_at"],
        "finished_at": task["finished_at"],
        "results": task["results"],
        "error": task["error"],
        "message": task.get("message", ""),
    }


# ===========================================================================
# 1. 状态
# ===========================================================================


def _collect_status(service, with_counts: bool) -> dict:
    settings = get_settings()
    # 读 rag_state() 而不是 settings.enable_rag:后者看不到业务流程的运行时覆盖
    state = rag_state()
    available, reason = service.available()

    payload: dict[str, Any] = {
        "success": True,
        "enabled": state["enabled"],
        "enabled_source": state["source"],
        "available": available,
        "reason": reason,
        "milvus_uri": settings.milvus_uri,
        "last_error": getattr(service, "last_error", None) or None,
        "dim_expected": int(settings.embed_dim_expected),
        "top_k": int(settings.rag_top_k),
        "collections": {
            "poi_facts": settings.rag_collection_poi,
            "city_guides": settings.rag_collection_guides,
            "uploaded": settings.rag_collection_uploaded,
        },
    }

    if with_counts:
        stats = service.stats()
        payload["last_error"] = stats.get("last_error") or payload["last_error"]
        # 按 service.ALL_NAMESPACES 遍历:加层时只改那里,这里才不会漏
        for ns in service.ALL_NAMESPACES:
            payload[ns] = stats.get(ns) or {}

        docs_summary = DocStore().summary()
        payload["docs"] = docs_summary

        # uploaded 层的计数**刻意用 SQLite 的,不用 Milvus 的 row_count**:
        # Milvus 删除之后、compaction 之前,row_count 仍把已删的行算在内 ——
        # 实测删完 3 块它还显示 3。而 SQLite 里记录的是「入库时写了哪些块」,
        # 删除时同步清掉,是精确值。用户删完攻略看到计数不变,一定会以为没删掉。
        payload["uploaded"] = {
            "collection": settings.rag_collection_uploaded,
            "exists": (stats.get("uploaded") or {}).get("exists"),
            "count": docs_summary["ingested"]["chunks"],
        }

    return payload


@router.get(
    "/status",
    response_model=KnowledgeStatusResponse,
    summary="知识库状态",
    description=(
        "返回 embedding/Milvus 可用性、ENABLE_RAG 开关、三层 collection 的计数,"
        "以及上传文档的概览。with_counts=false 时跳过计数(更快),用于页面首屏。"
    ),
)
def knowledge_status(with_counts: bool = True):
    service = get_knowledge_service()
    settings = get_settings()
    state = rag_state()
    try:
        # 计数要连 Milvus,给它一个上限;超时也不能让页面卡住
        return _collect_status(service, with_counts)
    except Exception as exc:
        # 状态接口**不应该 500** —— 它本身就是用来报告「哪里坏了」的。
        # 连它都返回 500,前端就只能显示「加载失败」,反而看不到真正的原因。
        return KnowledgeStatusResponse(
            success=True,
            enabled=state["enabled"],
            enabled_source=state["source"],
            available=False,
            reason=f"状态检查超时或失败 — {type(exc).__name__}: {exc}",
            milvus_uri=settings.milvus_uri,
            last_error=str(exc),
            dim_expected=int(settings.embed_dim_expected),
            top_k=int(settings.rag_top_k),
            collections={
                "poi_facts": settings.rag_collection_poi,
                "city_guides": settings.rag_collection_guides,
                "uploaded": settings.rag_collection_uploaded,
            },
        )


# ===========================================================================
# 2. 检索
# ===========================================================================


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="检索知识库",
    description="按 query 检索指定层;namespace=all 时三层归并后按分数降序",
)
def knowledge_search(body: SearchRequest):
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")

    service = get_knowledge_service()
    t0 = time.perf_counter()

    # 检索前清掉上一轮的 last_error —— 它是实例属性,
    # 不清的话上一次的失败原因会一直跟着后续成功的检索显示出来。
    service.last_error = None

    try:
        if body.namespace == "all":
            hits = service.retrieve_merged(query, body.top_k)
        else:
            k = int(body.top_k or get_settings().rag_top_k)
            hits = service.retrieve(query, body.namespace, k)
            hits.sort(key=lambda h: h.score, reverse=True)
    except Exception as exc:
        # retrieve 内部已经吞了异常,能走到这里说明是更外层的问题
        raise HTTPException(status_code=500, detail=f"检索失败: {type(exc).__name__}: {exc}")

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # 各层的条数,供前端区分「库是空的」和「确实没命中」。
    # 遍历 ALL_NAMESPACES 而不是手写 —— 加层时漏改这里的症状是:
    # 检索能搜到上传的内容,但计数里永远没有 uploaded 这一项。
    namespace_counts: dict[str, int] = {}
    for ns in service.ALL_NAMESPACES:
        try:
            namespace_counts[ns] = service.store(ns).count()
        except Exception:
            namespace_counts[ns] = -1  # -1 = 这一层数不出来

    # 一层拿到结果、另一层为空且带错误 => 部分成功。
    # KnowledgeHit.namespace 能告诉我们结果来自哪层,据此判断哪层失败了。
    returned_namespaces = {h.namespace for h in hits}
    partial = bool(service.last_error) and bool(returned_namespaces)
    if service.last_error and not hits:
        # 完全没结果时,partial 无意义;让 error 承担说明职责
        partial = False

    return SearchResponse(
        success=True,
        hits=[
            SearchHit(
                namespace=h.namespace,
                content=h.content,
                score=round(float(h.score), 4),
                source=h.source,
                heading_path=h.heading_path,
            )
            for h in hits
        ],
        elapsed_ms=elapsed_ms,
        partial=partial,
        # service.last_error 初始是空串而不是 None，这里统一成 None，
        # 免得前端要同时判 null 和空串两种「没有错误」的表示
        error=service.last_error or None,
        namespace_counts=namespace_counts,
    )


# ===========================================================================
# 3. 灌库预览
# ===========================================================================


@router.post(
    "/ingest/preview",
    response_model=IngestPreviewResponse,
    summary="灌库预览（不花钱）",
    description="统计将要入库的条数,不调用 embedding 接口。对应 CLI 的 --dry-run",
)
def knowledge_ingest_preview(body: IngestRequest):
    service = get_knowledge_service()

    poi: dict[str, Any] = {"total": 0, "per_city": {}}
    guides: dict[str, Any] = {"total": 0, "per_file": {}, "files": []}

    if body.source in ("frozen", "all"):
        poi = service.preview_poi_facts()
    if body.source in ("guides", "all"):
        guides = service.preview_city_guides()

    total = int(poi.get("total") or 0) + int(guides.get("total") or 0)
    # 与 CLI 一致的估算:每批 32 条
    embed_requests = -(-total // 32) if total else 0

    # 估算费用：bge-m3 约 ¥0.004 / 1750 条(来自 README 的实测值)
    note = (
        f"共 {total} 条将被编码,约 {embed_requests} 次批量请求(每批 32 条)。"
        "执行会调用 embedding 接口并产生费用。"
    )

    return IngestPreviewResponse(
        success=True,
        source=body.source,
        poi=poi,
        guides=guides,
        embed_requests=embed_requests,
        note=note,
    )


# ===========================================================================
# 4/5. 灌库（长任务）
# ===========================================================================


def _run_ingest(task_id: str, source: IngestSource, recreate: bool) -> None:
    """后台线程体。任何异常都要落到任务状态里,不能让它消失在线程里。"""
    service = get_knowledge_service()
    results: list[dict[str, Any]] = []

    def set_progress(processed: int, total: int) -> None:
        with _TASKS_LOCK:
            task = _TASKS.get(task_id)
            if task:
                task["processed"] = processed
                task["total"] = total or task["total"]

    try:
        with _TASKS_LOCK:
            _TASKS[task_id]["state"] = "running"

        # 先算两层总量,作为进度的分母
        poi_total = 0
        guide_total = 0
        if source in ("frozen", "all"):
            poi_total = int(service.preview_poi_facts().get("total") or 0)
        if source in ("guides", "all"):
            guide_total = int(service.preview_city_guides().get("total") or 0)
        grand_total = poi_total + guide_total

        with _TASKS_LOCK:
            _TASKS[task_id]["total"] = grand_total
            _TASKS[task_id]["processed"] = 0

        # 灌库前先确认环境可用 —— 否则会跑到一半才失败,已写入的还得重来
        ok, reason = service.available()
        if not ok:
            raise RuntimeError(f"环境不可用: {reason}")

        base = 0

        if source in ("frozen", "all"):
            with _TASKS_LOCK:
                _TASKS[task_id]["current"] = "poi_facts"
            r = service.ingest_poi_facts(
                recreate=recreate,
                progress_cb=lambda p, t: set_progress(base + p, grand_total),
            )
            results.append(r)
            base += poi_total
            with _TASKS_LOCK:
                _TASKS[task_id]["processed"] = base

        if source in ("guides", "all"):
            with _TASKS_LOCK:
                _TASKS[task_id]["current"] = "city_guides"
            r = service.ingest_city_guides(
                recreate=recreate,
                progress_cb=lambda p, t: set_progress(base + p, grand_total),
            )
            results.append(r)
            base += guide_total

        with _TASKS_LOCK:
            task = _TASKS[task_id]
            task["state"] = "done"
            task["results"] = results
            task["processed"] = grand_total or task["processed"]
            task["current"] = None
            task["finished_at"] = _now_iso()
            task["message"] = "灌库完成"

    except Exception as exc:
        import traceback

        traceback.print_exc()
        with _TASKS_LOCK:
            task = _TASKS.get(task_id)
            if task:
                task["state"] = "error"
                task["error"] = f"{type(exc).__name__}: {exc}"
                task["current"] = None
                task["finished_at"] = _now_iso()
                task["results"] = results
                task["message"] = "灌库失败"


@router.post(
    "/ingest",
    response_model=IngestTaskResponse,
    status_code=202,
    summary="开始灌库（后台任务）",
    description=(
        "在后台线程里执行灌库,立即返回 task_id,用 GET /knowledge/ingest/{task_id} 轮询进度。"
        "recreate=true 是破坏性操作,必须同时传 confirm='DROP'。"
    ),
)
def knowledge_ingest(body: IngestRequest):
    global _RUNNING

    # 破坏性操作必须显式确认。recreate 会 drop collection,
    # 1750 条 POI 数据瞬间清零且无法恢复 —— 不能只靠前端的二次确认弹窗，
    # 接口自己也要挡一道。
    if body.recreate and body.confirm != "DROP":
        raise HTTPException(
            status_code=400,
            detail=(
                "recreate=true 会先删除 collection 再重建,collection 内全部数据会丢失。"
                "确认请在 confirm 字段传 'DROP'。"
            ),
        )

    service = get_knowledge_service()

    with _TASKS_LOCK:
        # 并发保护:两个任务同时写同一个 collection 会互相干扰
        if _RUNNING:
            running = _TASKS.get(_RUNNING)
            if running and running["state"] in ("pending", "running"):
                raise HTTPException(
                    status_code=409,
                    detail=f"已有灌库任务在进行中（{_RUNNING}），请等它结束后再提交。",
                )

        task_id = f"ing-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        _TASKS[task_id] = {
            "task_id": task_id,
            "state": "pending",
            "source": body.source,
            "recreate": body.recreate,
            "total": 0,
            "processed": 0,
            "current": None,
            "started_at": _now_iso(),
            "finished_at": None,
            "results": [],
            "error": None,
            "message": "任务已创建",
        }
        _RUNNING = task_id
        snapshot = _public_task(_TASKS[task_id])

    thread = threading.Thread(
        target=_run_ingest,
        args=(task_id, body.source, body.recreate),
        name=f"ingest-{task_id}",
        daemon=True,
    )
    thread.start()

    return IngestTaskResponse(**snapshot)


@router.get(
    "/ingest/{task_id}",
    response_model=IngestTaskResponse,
    summary="查询灌库任务",
    description="前端按 2 秒左右的间隔轮询这个接口获取进度",
)
def knowledge_ingest_task(task_id: str):
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在（可能服务已重启）")
        return IngestTaskResponse(**_public_task(task))


# ===========================================================================
# 6. 数据源
# ===========================================================================


@router.get(
    "/sources",
    response_model=SourcesResponse,
    summary="知识库数据源",
    description="列出 POI 库存与攻略目录的元信息(文件、大小、分块数)",
)
def knowledge_sources():
    service = get_knowledge_service()
    return SourcesResponse(
        success=True,
        poi_inventory=service.preview_poi_facts(),
        city_guides=service.preview_city_guides(),
    )


# ===========================================================================
# 7. 上传攻略（用户文档）
# ===========================================================================
#
# 与上面「灌库」的区别,一句话:灌库是**全量重灌**(遍历目录、可重建 collection),
# 这里是**单篇增量**(只写这一篇、删除时只删这一篇)。
# 两种生命周期塞进同一个函数里,参数和分支会越来越多,所以分开写。
#
# 流程刻意拆成 parse → ingest 两步:
#   parse   解析 + 切块 + 去重检查,把结果写进 SQLite(status='pending'),**不碰 Milvus**
#   ingest  真正调 embedding 写进 Milvus(异步任务,前端轮询进度)
#
# 中间这一步是给用户看的,也是质量的把关点:
#   - PDF 没有文字层,在这里就会被拦下(而不是等到生成行程时才发现检索是空的)
#   - 入库前先告诉用户「这篇会切成 12 块、发 1 次 embedding 请求」,花的钱心里有数

_doc_store: DocStore | None = None
_DOC_STORE_LOCK = threading.Lock()


def _get_doc_store() -> DocStore:
    global _doc_store
    if _doc_store is None:
        with _DOC_STORE_LOCK:
            if _doc_store is None:
                _doc_store = DocStore()
    return _doc_store


def _doc_chunk_previews(doc: dict[str, Any]) -> list[DocChunkPreview]:
    """把一篇文档切成预览块。切块逻辑与入库时**共用同一份** ——
    预览里说"12 块",入库就必须是 12 块;另写一份迟早对不上。"""
    chunks = chunk_document(doc["content"], source=doc["title"])
    return [
        DocChunkPreview(idx=i, heading_path=h, chars=len(c), snippet=c[:160])
        for i, (h, c) in enumerate(chunks)
    ]


@router.post(
    "/docs/parse",
    response_model=DocParseResponse,
    summary="解析一篇上传的攻略（不入库）",
    description=(
        "上传 Markdown / 纯文本 / PDF(需有文字层),或直接粘贴文字。"
        "返回切块预览与预估 embedding 次数;确认后调 POST /docs/{id}/ingest 真正入库。"
    ),
)
async def knowledge_doc_parse(
    file: Optional[UploadFile] = File(default=None),
    text: Optional[str] = Form(default=None),
    title: str = Form(default=""),
):
    settings = get_settings()
    max_bytes = int(getattr(settings, "max_upload_bytes", 5 * 1024 * 1024))

    if file is not None:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="上传的文件是空的。")
        if len(data) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"文件 {len(data) / 1024 / 1024:.1f} MB,超过上限 "
                    f"{max_bytes / 1024 / 1024:.0f} MB。攻略用不到这么大,"
                    "多半是选错文件了。"
                ),
            )
        try:
            parsed = parse_file(file.filename or "", data)
        except ParseError as exc:
            # 422 而不是 500:文件内容不适合入库是**用户的可修复问题**,
            # 前端要把它当普通提示展示,而不是当成服务端故障。
            raise HTTPException(status_code=422, detail=str(exc))
    elif text is not None:
        # `text is not None` 而不是 `text.strip()`:只贴了空格也该收到
        # 「粘贴的内容是空的」,而不是笼统的"请上传文件"。
        try:
            parsed = parse_paste(text, title)
        except ParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    else:
        raise HTTPException(status_code=400, detail="请上传文件,或在文本框里粘贴攻略内容。")

    doc_store = _get_doc_store()
    existing = doc_store.get(parsed.doc_id, with_content=False)

    # **已存在的记录一律不覆盖** —— 这不是偷懒,是正确性问题:
    # 同一份内容 → 同一个 doc_id → 同一批 chunk id。
    # 如果这里把它重置回 pending 并清掉 chunk_ids,
    # 那条记录就再也删不掉向量库里的块了(症状:删了,检索还能搜到)。
    # 所以:没有就插一条 pending;有就保持原样,预览照样给用户看。
    if existing is None:
        doc_store.create_pending(
            {
                "id": parsed.doc_id,
                "title": parsed.title,
                "origin": parsed.origin,
                "content": parsed.text,
                "content_hash": parsed.content_hash,
                "char_count": parsed.char_count,
                "chunk_count": parsed.chunk_count,
                "chunk_ids": [],
            }
        )

    return DocParseResponse(
        success=True,
        doc_id=parsed.doc_id,
        title=parsed.title,
        origin=parsed.origin,
        char_count=parsed.char_count,
        chunk_count=parsed.chunk_count,
        # 向上取整:哪怕只有 1 块也要发 1 次请求,不能显示成 0 次
        embed_requests=-(-parsed.chunk_count // EMBED_BATCH),
        duplicate=existing is not None,
        existing_status=existing["status"] if existing else None,
        warnings=parsed.warnings,
        preview=_doc_chunk_previews(
            {"content": parsed.text, "title": parsed.title}
        ),
    )


def _run_doc_ingest(task_id: str, doc_id: str) -> None:
    """后台线程体:把一篇已解析(pending)的文档灌进 Milvus。

    任何异常都要落到任务状态**和**文档状态里 —— 只记任务的话,
    用户刷新页面就看不到失败原因了。
    """
    service = get_knowledge_service()
    doc_store = _get_doc_store()

    def set_progress(processed: int, total: int) -> None:
        with _TASKS_LOCK:
            task = _TASKS.get(task_id)
            if task:
                task["processed"] = processed
                task["total"] = total or task["total"]

    try:
        with _TASKS_LOCK:
            _TASKS[task_id]["state"] = "running"

        doc = doc_store.get(doc_id)
        if doc is None:
            raise RuntimeError(f"文档 {doc_id} 不存在（可能已被删除）")
        if not (doc.get("content") or "").strip():
            raise RuntimeError("文档内容为空,无法入库")

        # 上传即开启。用户点「入库」这个动作本身就是在表达
        # 「我想让这份攻略参与行程生成」—— 再让他去别处开一次开关,属于多余步骤。
        # 只改运行时状态,不写回 .env:评测基线不受影响,重启即恢复。
        rag = set_rag_enabled(True)

        with _TASKS_LOCK:
            _TASKS[task_id]["current"] = "uploaded"

        # 入库前先确认环境可用 —— 否则会跑到一半才失败
        ok, reason = service.available()
        if not ok:
            raise RuntimeError(f"环境不可用: {reason}")

        chunks = chunk_document(doc["content"], source=doc["title"])
        total = len(chunks)
        with _TASKS_LOCK:
            _TASKS[task_id]["total"] = total
            _TASKS[task_id]["processed"] = 0

        result = service.ingest_uploaded_doc(
            doc_id=doc_id,
            title=doc["title"],
            chunks=chunks,
            progress_cb=set_progress,
        )
        chunk_ids = result.get("chunk_ids") or []
        doc_store.mark_ingested(doc_id, chunk_ids, len(chunk_ids))

        with _TASKS_LOCK:
            task = _TASKS[task_id]
            task["state"] = "done"
            task["results"] = [
                {"namespace": result.get("namespace"), "count": result.get("count")}
            ]
            task["processed"] = total
            task["current"] = None
            task["finished_at"] = _now_iso()
            task["message"] = (
                f"入库完成,共 {len(chunk_ids)} 块。"
                + ("已自动开启知识库（重启后恢复 .env 设置）。" if rag["source"] == "runtime" else "")
            )

    except Exception as exc:
        import traceback

        traceback.print_exc()
        reason = f"{type(exc).__name__}: {exc}"
        try:
            doc_store.mark_failed(doc_id, reason)
        except Exception:
            pass
        with _TASKS_LOCK:
            task = _TASKS.get(task_id)
            if task:
                task["state"] = "error"
                task["error"] = reason
                task["current"] = None
                task["finished_at"] = _now_iso()
                task["message"] = "入库失败"


@router.post(
    "/docs/{doc_id}/ingest",
    response_model=IngestTaskResponse,
    status_code=202,
    summary="入库一篇攻略（后台任务）",
    description=(
        "把 parse 过的文档写入向量库,返回 task_id 用 GET /knowledge/ingest/{task_id} 轮询。"
        "成功后会自动开启 ENABLE_RAG(运行时覆盖,重启恢复)。"
    ),
)
def knowledge_doc_ingest(doc_id: str):
    global _RUNNING

    doc_store = _get_doc_store()
    doc = doc_store.get(doc_id, with_content=False)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail="这篇文档不存在（可能已被删除）。请重新上传。",
        )

    with _TASKS_LOCK:
        # 与灌库共用同一把单飞锁:两者写的都是 Milvus,并发会互相干扰
        if _RUNNING:
            running = _TASKS.get(_RUNNING)
            if running and running["state"] in ("pending", "running"):
                raise HTTPException(
                    status_code=409,
                    detail=f"已有知识库任务在进行中（{_RUNNING}），请等它结束后再提交。",
                )

        task_id = f"upl-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        _TASKS[task_id] = {
            "task_id": task_id,
            "state": "pending",
            "source": f"uploaded:{doc_id}",
            "recreate": False,
            "total": 0,
            "processed": 0,
            "current": None,
            "started_at": _now_iso(),
            "finished_at": None,
            "results": [],
            "error": None,
            "message": "任务已创建",
        }
        _RUNNING = task_id
        snapshot = _public_task(_TASKS[task_id])

    thread = threading.Thread(
        target=_run_doc_ingest,
        args=(task_id, doc_id),
        name=f"upload-{task_id}",
        daemon=True,
    )
    thread.start()

    return IngestTaskResponse(**snapshot)


@router.get(
    "/docs",
    response_model=DocListResponse,
    summary="列出上传的攻略",
)
def knowledge_doc_list(limit: int = 100):
    doc_store = _get_doc_store()
    return DocListResponse(
        success=True,
        docs=[DocSummary(**d) for d in doc_store.list_docs(limit)],
        summary=doc_store.summary(),
    )


@router.get(
    "/docs/{doc_id}",
    response_model=DocDetailResponse,
    summary="查看一篇上传攻略的分块",
)
def knowledge_doc_detail(doc_id: str):
    doc_store = _get_doc_store()
    doc = doc_store.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="这篇文档不存在。")

    chunks = _doc_chunk_previews(doc)
    # 全文不回传 —— 分块拼起来就是全文,没必要再传一份大的。
    # 注意顺序:**先**切块、再丢掉 content,顺序反了切块拿到的就是空的。
    doc.pop("content", None)
    return DocDetailResponse(success=True, doc=doc, chunks=chunks)


@router.delete(
    "/docs/{doc_id}",
    response_model=DocDeleteResponse,
    summary="删除一篇上传攻略",
    description="同时删除向量库里属于它的所有分块。先删向量库,再删记录。",
)
def knowledge_doc_delete(doc_id: str):
    doc_store = _get_doc_store()
    doc = doc_store.get(doc_id, with_content=False)
    if doc is None:
        raise HTTPException(status_code=404, detail="这篇文档不存在。")

    service = get_knowledge_service()
    try:
        deleted = service.delete_uploaded_doc(doc.get("chunk_ids") or [])
    except Exception as exc:
        # 向量库删不掉时**不能**顺手把 SQLite 记录也删了 ——
        # 那会留下一批没有任何记录指向的孤儿块,检索照样能搜到它,
        # 用户看到的症状是"明明删掉了,行程里还在引用它"。
        # 让记录留着,用户可以重试删除。
        raise HTTPException(
            status_code=502,
            detail=f"从向量库删除失败,记录已保留,请稍后重试:{type(exc).__name__}: {exc}",
        )

    doc_store.delete(doc_id)
    return DocDeleteResponse(
        success=True,
        message=f"已删除《{doc['title']}》",
        deleted_chunks=deleted,
    )


@router.post(
    "/rag-toggle",
    response_model=RagToggleResponse,
    summary="开启/关闭知识库（运行时）",
    description=(
        "运行时覆盖 ENABLE_RAG,不写回 .env —— 评测基线不受影响,重启后恢复 .env 的值。"
    ),
)
def knowledge_rag_toggle(body: RagToggleRequest):
    state = set_rag_enabled(body.enabled)
    suffix = (
        "（运行时覆盖,重启后恢复 .env 设置）" if state["source"] == "runtime" else "（与 .env 一致）"
    )
    return RagToggleResponse(
        success=True,
        enabled=state["enabled"],
        enabled_source=state["source"],
        message=("已开启" if state["enabled"] else "已关闭") + suffix,
    )
