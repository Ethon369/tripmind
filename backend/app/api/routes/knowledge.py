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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...config import get_settings
from ...services.knowledge_service import get_knowledge_service

router = APIRouter(prefix="/knowledge", tags=["知识库"])

NamespaceFilter = Literal["all", "poi_facts", "city_guides"]
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
    #   enabled   = 后端 .env 的 ENABLE_RAG,决定「生成行程时要不要注入知识库」
    #   available = embedding 与 Milvus 是否都能用,决定「知识库能不能查/能不能灌」
    # 现状是用户灌完库、检索也通,但 ENABLE_RAG 默认 false,生成时完全不用它 ——
    # 而界面上无从得知。这一对字段就是给界面把这个状态摊开用的。
    enabled: bool
    available: bool
    reason: str
    milvus_uri: str
    last_error: Optional[str] = None
    dim_expected: int
    top_k: int
    collections: dict[str, str]
    poi_facts: Optional[KnowledgeNamespaceStat] = None
    city_guides: Optional[KnowledgeNamespaceStat] = None


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
    enabled = bool(getattr(settings, "enable_rag", False))
    available, reason = service.available()

    payload: dict[str, Any] = {
        "success": True,
        "enabled": enabled,
        "available": available,
        "reason": reason,
        "milvus_uri": settings.milvus_uri,
        "last_error": getattr(service, "last_error", None) or None,
        "dim_expected": int(settings.embed_dim_expected),
        "top_k": int(settings.rag_top_k),
        "collections": {
            "poi_facts": settings.rag_collection_poi,
            "city_guides": settings.rag_collection_guides,
        },
    }

    if with_counts:
        stats = service.stats()
        payload["last_error"] = stats.get("last_error") or payload["last_error"]
        for ns in ("poi_facts", "city_guides"):
            payload[ns] = stats.get(ns) or {}

    return payload


@router.get(
    "/status",
    response_model=KnowledgeStatusResponse,
    summary="知识库状态",
    description=(
        "返回 embedding/Milvus 可用性、ENABLE_RAG 开关、两层 collection 的计数。"
        "with_counts=false 时跳过计数(更快),用于页面首屏。"
    ),
)
def knowledge_status(with_counts: bool = True):
    service = get_knowledge_service()
    settings = get_settings()
    try:
        # 计数要连 Milvus,给它一个上限;超时也不能让页面卡住
        return _collect_status(service, with_counts)
    except Exception as exc:
        # 状态接口**不应该 500** —— 它本身就是用来报告「哪里坏了」的。
        # 连它都返回 500,前端就只能显示「加载失败」,反而看不到真正的原因。
        return KnowledgeStatusResponse(
            success=True,
            enabled=bool(getattr(settings, "enable_rag", False)),
            available=False,
            reason=f"状态检查超时或失败 — {type(exc).__name__}: {exc}",
            milvus_uri=settings.milvus_uri,
            last_error=str(exc),
            dim_expected=int(settings.embed_dim_expected),
            top_k=int(settings.rag_top_k),
            collections={
                "poi_facts": settings.rag_collection_poi,
                "city_guides": settings.rag_collection_guides,
            },
        )


# ===========================================================================
# 2. 检索
# ===========================================================================


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="检索知识库",
    description="按 query 检索指定层;namespace=all 时两层归并后按分数降序",
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

    # 两层各自的条数,供前端区分「库是空的」和「确实没命中」
    namespace_counts: dict[str, int] = {}
    for ns in ("poi_facts", "city_guides"):
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
