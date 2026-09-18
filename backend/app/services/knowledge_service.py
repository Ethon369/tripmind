"""RAG 双层知识库 —— 直接建在 Milvus 上

## 为什么检索层是自己写的,而不是用框架的 RAG

hello_agents 有 RAG(`memory/rag/pipeline.py`,1100+ 行),但它**只支持 Qdrant**:

- `memory/storage/` 下只有 `document_store.py` / `neo4j_store.py` / `qdrant_store.py`
- 在框架里 `grep -ril milvus` 返回**空**
- `create_rag_pipeline()` 的签名里**没有 `store` 参数**,内部无条件
  `QdrantVectorStore(...)`,也没有抽象基类或注册表可以挂新后端

所以要用 Milvus 只剩「自己写」一条路。这反而更好懂 —— 下面这 300 行是线性的,
看得到每一步在干什么;框架那个 1100 行的 pipeline 把 embedding、分块、检索、
重排全缠在一起。

**复用框架的部分**:embedder 仍用框架的 `DashScopeEmbedding`
(硅基流动的 OpenAI 兼容 REST,bge-m3,1024 维)。它是唯一知道该往哪个地址、
用什么 payload 发请求的地方,重写没有意义。

## 两层各答什么问题

| namespace | 数据源 | 回答什么 | 对指标的贡献 |
|---|---|---|---|
| `poi_facts` | 冻结的高德 POI 库存(1750 条,**带真实坐标**) | 这城市有哪些**真实**景点、在哪、什么类型 | `poi_exists_rate` / `coord_mae_km` |
| `city_guides` | 手写 markdown 攻略(每城 1 篇) | 怎么安排才合理(门票、预约、淡旺季、避坑) | 文案质量 / `intraday_travel_km_p95` |

`city_guides` 才是「为什么需要 RAG」的正当答案:**这些是 LLM 会记错或过时的知识**
—— 门票涨价、闭馆日、地铁新线、预约规则。而 `poi_facts` 只是把工具本来就拿得到
的事实整理进向量库(单看它确实有点同义反复,但它能补上高德搜索**不给坐标**这个缺口)。

## 注入方式:Python 里检索后拼进 prompt,不给 planner 挂 RAG 工具

理由:检索时机确定(每次必发生,不受 `max_tool_iterations` 预算影响)、
可记录 query/score/来源、而且出问题时能一眼看出是检索坏了还是模型坏了。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from ..config import Settings, get_settings
from .amap_parsing import normalize_str_list

BACKEND_DIR = Path(__file__).resolve().parents[2]


def resolve_dir(value: str | Path) -> Path:
    """把配置里的相对路径解析成**相对 `backend/`** 的绝对路径。

    为什么不能直接拿配置值去 open:配置里写的是 `"./data/frozen"` 这种
    **相对 cwd** 的路径(见 `app/config.py`),从仓库根目录启动就会指到
    别的地方 —— 和 `.env` 找不到是同一个根因。这里统一钉死在 `backend/` 下。
    """
    p = Path(value)
    return p if p.is_absolute() else (BACKEND_DIR / p).resolve()

# Milvus VARCHAR 的 max_length 按 **UTF-8 字节**算,不是字符数。
# 中文一个字 3 字节,所以这里留足:16384 字节 ≈ 5400 个汉字,
# 远超我们单个分块的体量(攻略每节 500~1500 字)。
_MAX_TEXT_BYTES = 16384
_MAX_ID_BYTES = 64

# 每次发给 embedding 接口的条数。太大容易超单请求上限,太小则请求次数爆炸
EMBED_BATCH = 32


# ===========================================================================
# 一、纯函数 —— 不碰网络、不碰数据库,可以单测
# ===========================================================================


def fit_utf8(text: str, max_bytes: int) -> str:
    """按 **UTF-8 字节数**截断,并且保证不把一个汉字切成两半。

    为什么按字节而不是字符:Milvus 的 `VARCHAR(max_length)` 是按字节算的,
    而中文一个字 3 字节。按字符数截断会超限,写入时直接报错。

    `errors="ignore"` 是必须的:字节切在汉字中间时,直接 decode 会抛
    `UnicodeDecodeError` —— 一个"文本太长"的小问题会变成写入失败。
    """
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def normalize_vectors(x: object) -> list[list[float]]:
    """把 embedder 的返回值统一成 `list[list[float]]`。

    ⚠️ 实测过的坑:框架的 `encode()` 有**两种返回形态** —— 传单个字符串时返回
    **1 维**向量(1024,),传列表时返回 **2 维**(n, 1024)。见
    `DashScopeEmbedding.encode()` 里的 `return vecs[0]`。

    直接把 1 维向量塞给 Milvus 的 `data=` 会报:

        ParamError: `search_data` value [-0.0112 0.0108 ...] is illegal

    因为 Milvus 期望的是「一批向量」,不是「一个向量」。所以这里统一升维。
    """
    arr = np.asarray(x, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    return arr.tolist()


def primary_types(raw: object, limit: int = 3) -> str:
    """把高德的 `type` 字段收敛成好读的类别。

    高德的格式是 `"A;B;C|D;E;F"` —— 分号表示**层级**(越来越细),
    竖线表示**并列**。只取每组的**首段**:那是最粗的类别,
    后面的细类("红色景区")对检索和阅读都没什么帮助。

        "风景名胜;风景名胜;红色景区|风景名胜;公园广场;城市广场"
        → "风景名胜"

    注意上面这串其实是**最常见**的形态:两组的首段都是"风景名胜"
    (第二组只是换了条子分类路径),去重之后只剩一个。实测库存 1750 条里
    1106 条都是这个样子。首段**确实不同**的情况才少见:

        "风景名胜;风景名胜;世界遗产|科教文化服务;博物馆;博物馆"
        → "风景名胜 / 科教文化服务"          ← 故宫博物院
    """
    if not raw:
        return ""
    seen: list[str] = []
    for group in str(raw).split("|"):
        head = group.split(";")[0].strip()
        if head and head not in seen:
            seen.append(head)
        if len(seen) >= limit:
            break
    return " / ".join(seen)


def poi_heading_path(poi: dict) -> str:
    """POI 的「属于哪一节」,用来做引用出处,风格与攻略的标题路径保持一致。"""
    city = str(poi.get("city") or "").strip()
    kind = primary_types(poi.get("type"), limit=1)
    if city and kind:
        return f"{city} > {kind}"
    return city or kind


def normalize_aliases(raw: object) -> list[str]:
    """把 `alias` 字段归一成 `list[str]`。

    ⚠️ **这是本项目里最阴的一种坑,值得单独一个函数。**

    高德的 `alias` 字段**类型不一致** —— 同一个字段,有时是字符串、有时是列表:

        实测冻结库存 1750 条:
          240 条  alias 是 **字符串**   "紫禁城" / "中国历史博物馆|北京历史博物馆"
         1510 条  alias 是 **空列表**   []

    **有别名的那 240 条全部是字符串,而列表全部是空的。** 所以如果只写
    `isinstance(raw, (list, tuple))`,守卫条件对「恰好有数据的那批」**恒为假** ——
    别名一行都进不了库,而且**不报任何错**,检索结果只是悄悄变差。

    更值得记住的是它怎么被发现的:第一版代码就是这么写的,而且**单元测试全绿**
    —— 因为测试里我手写的是 `["紫禁城"]`(我**假设**的形状),不是数据里真实的
    `"紫禁城"`。**测试验证的是我的假设,不是现实。** 最后是靠「搜『紫禁城』
    能不能搜到故宫」这条端到端验证才暴露出来的。

    多个别名在字符串里用 `|` 分隔(见 `中国历史博物馆|北京历史博物馆`)。

    实现委托给 `amap_parsing.normalize_str_list` —— 同一个坑只需要一个实现。
    灌库(knowledge_service)和 map/poi 路由(amap_service)读的是同一个
    `alias` 字段,各写一份迟早会只修好一边。
    """
    return normalize_str_list(raw)


def poi_to_text(poi: dict) -> str:
    """把一条 POI 变成一行「可索引、也能直接喂给 LLM」的文本。

    **必须带别名。** 高德给一部分 POI 返回别名(实测冻结库存 1750 条里有 240 条),
    里面装的正是「俗称 vs 官方名」对不上的那批,而且往往是最大牌的景点:

        故宫博物院 → 紫禁城      西安兵马俑 → 秦始皇兵马俑博物馆
        华清宫     → 华清池      杭州西湖风景名胜区 → 西湖景区

    不带别名,用户说「紫禁城」就检索不到故宫。见 CLAUDE.md 坑 #6,
    以及 `normalize_aliases` 里那个「守卫条件对恰好有数据的那批恒为假」的坑。
    """
    parts: list[str] = []
    name = str(poi.get("name") or "").strip()
    if name:
        parts.append(name)

    aliases = normalize_aliases(poi.get("alias"))
    if aliases:
        parts.append("别名:" + "/".join(aliases))

    addr = str(poi.get("address") or "").strip()
    if addr:
        parts.append(addr)

    loc = poi.get("location") or []
    if isinstance(loc, (list, tuple)) and len(loc) == 2:
        parts.append(f"坐标 {loc[0]},{loc[1]}")

    kinds = primary_types(poi.get("type"))
    if kinds:
        parts.append(kinds)

    return " | ".join(parts)


def split_markdown_sections(text: str, source: str = "") -> list[tuple[str, str]]:
    """按 markdown 标题把整篇攻略切成若干块,返回 `[(heading_path, chunk_text), ...]`。

    为什么不复用框架的 `DocumentProcessor.process_document()`:
    它是**按字符数**切的(`chunk_size=1000`),会把「## 门票与预约」这一节从中间
    截断 —— 结果是一块里混着两个主题,而且**没人知道它原本属于哪个标题**。
    攻略的价值恰恰在标题结构:「北京 > 门票与预约」这种**可引用的定位**,
    按长度切是拿不到的。而我们的攻略每节 500~1500 字,按标题切天然合适。

    `heading_path` 用 " > " 连接标题栈。标题之前的前言部分路径为空串。
    `chunk_text` 里**带着标题**,因为光看正文往往看不出这段在说哪个城市 ——
    向量检索会被这个影响。
    """
    sections: list[tuple[str, list[str]]] = []
    stack: list[tuple[int, str]] = []

    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            # 同级或更浅的标题出栈 —— 标题栈要反映"当前在哪一节"
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            sections.append((" > ".join(t for _, t in stack), []))
            continue

        if not sections:
            # 第一个标题之前的前言
            sections.append(("", []))
        sections[-1][1].append(line)

    out: list[tuple[str, str]] = []
    for path, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not body:
            # 光有标题、没有正文的小节,索引进库也检索不出东西
            continue
        head = path or source
        out.append((path, f"{head}\n{body}" if head else body))
    return out


def build_retrieval_query(request: Any) -> str:
    """拿什么去检索?

    只用「城市 + 偏好 + 用户自由输入」。**不放天数、日期、交通、住宿** ——
    这几项不影响「这个城市有什么值得去的、要注意什么」,放进去只会稀释语义,
    把查询向量推向无关区域。
    """
    parts: list[str] = []
    city = str(getattr(request, "city", "") or "").strip()
    if city:
        parts.append(city)
    prefs = getattr(request, "preferences", None) or []
    prefs_txt = " ".join(str(p).strip() for p in prefs if str(p).strip())
    if prefs_txt:
        parts.append(prefs_txt)
    free = str(getattr(request, "free_text_input", "") or "").strip()
    if free:
        parts.append(free)
    return " ".join(parts).strip()


_CONTEXT_HEADER = (
    "【外部知识库检索结果】以下内容来自本地知识库,"
    "在门票价格、开放时间、预约规则这类**容易记错或过时**的信息上,"
    "请优先采用这里的内容:"
)


def format_context(hits: Sequence["KnowledgeHit"]) -> str:
    """把检索结果拼成**带出处**的引用块,直接放进 planner 的 prompt。

    为什么自己拼,不用框架的 `RAGTool.get_relevant_context()`:

    - 它返回**裸拼接文本,没有来源、没有分数** —— LLM 无从判断哪条更可信,
      用户也无从核对。检索到的内容不带出处,就等于无法验证。
    - 它异常时返回 `f"获取上下文失败: {e}"`,这行**错误信息会被当成资料**
      一起注入 prompt。

    检索为空时返回空串(调用方据此决定不往 prompt 里加这一段)。
    """
    if not hits:
        return ""
    lines: list[str] = [_CONTEXT_HEADER]
    for i, h in enumerate(hits, 1):
        where = f"{h.source} > {h.heading_path}" if h.heading_path else h.source
        lines.append(f"[{i}] 来源: {where}")
        lines.append(h.content)
    return "\n".join(lines)


# ===========================================================================
# 二、检索结果的结构
# ===========================================================================


@dataclass
class KnowledgeHit:
    """一条检索结果。

    带上 `source` / `heading_path` 是为了两件事:①拼进 prompt 时给出处,
    让模型和用户都能核对;②评测时能统计「命中来自哪一层」。
    """

    content: str
    score: float
    source: str = ""
    heading_path: str = ""
    namespace: str = ""

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "score": self.score,
            "source": self.source,
            "heading_path": self.heading_path,
            "namespace": self.namespace,
        }


# ===========================================================================
# 三、Milvus 薄封装
# ===========================================================================


class MilvusKnowledgeStore:
    """一个 collection 的极薄封装。

    只用 `MilvusClient`(简单 API),不碰 ORM 那一套 —— 与本项目在 SQLite 上
    的取舍一致:表/集合就两个,不值得引一层抽象。

    连接是**懒建立**的:构造这个对象不会去连 Milvus。这样"Milvus 没起来"
    不会在导入模块或启动后端的时候就炸,而是等到真正检索时才失败,
    由 `KnowledgeService` 兜住(检索失败**不能**让整条行程规划挂掉)。
    """

    def __init__(self, uri: str, collection: str, dimension: int):
        self.uri = uri
        self.collection = collection
        self.dimension = dimension
        self._client: Any = None
        # collection 是否已确认加载到内存。见 `_ensure_loaded()` ——
        # 按实例缓存一次,因为 search 是热路径(一次行程生成最多查 3 遍)。
        self._loaded = False

    @property
    def client(self) -> Any:
        if self._client is None:
            from pymilvus import MilvusClient  # 延迟导入:不检索就不需要它

            self._client = MilvusClient(uri=self.uri)
        return self._client

    def exists(self) -> bool:
        return bool(self.client.has_collection(self.collection))

    def _ensure_loaded(self) -> None:
        """检索前把 collection 加载进内存。**漏掉这一步会让 RAG 静默失效。**

        Milvus 的 collection 必须先 load 才能 search/get/query。麻烦在于
        **新建的 collection 会自动加载**,所以"灌完库马上检索"一切正常 ——
        问题只在**进程重启之后**暴露:从磁盘恢复出来的 collection 是
        `released` 状态,此时检索抛

            code=101 Collection 'xxx' is in state 'released';
                     call load() before search/get/query

        (实测:用两个独立进程分别写和读同一个 Milvus Lite 库复现。)

        为什么这个坑格外难发现,两点叠加:

        1. 它**只在重启后出现**,同一进程里怎么测都是好的;
        2. `has_collection` 和 `get_collection_stats` **不需要 load** ——
           于是知识库状态页会兴高采烈地显示 `count: 1750`、`available: true`,
           而检索其实一条都返回不了。再加上 `KnowledgeService.retrieve()`
           把异常吞掉(那是 RAG 降级的正确设计),最终表现是
           **行程照常生成、只是再也不引用知识库** —— 没有任何报错。

        写操作不受影响(`upsert` / `delete` / `flush` 在 released 上实测可用),
        所以只有这里需要管。

        按实例缓存:load 是幂等的,但没必要每次检索都发一次 RPC。
        """
        if self._loaded:
            return
        if not self.exists():
            # 这层还没建(uploaded 层的常态)。**不缓存**,等它被建出来再加载。
            return
        self.client.load_collection(self.collection)
        self._loaded = True

    def drop(self) -> None:
        if self.exists():
            self.client.drop_collection(self.collection)
        # 删掉之后 `_loaded` 就过期了:下次 ensure_collection 会重建一个
        # 全新的 collection,必须重新走一遍加载判断。
        self._loaded = False

    def ensure_collection(self, recreate: bool = False) -> None:
        """建 collection(带索引)。

        `enable_dynamic_field=True` 让没在 schema 里声明的字段也能存进去 ——
        将来想加个 `city` 字段做过滤,不用改 schema 重建库。
        """
        from pymilvus import DataType

        if recreate:
            self.drop()
        if self.exists():
            return

        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=True)
        schema.add_field("id", DataType.VARCHAR, max_length=_MAX_ID_BYTES, is_primary=True)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dimension)
        schema.add_field("text", DataType.VARCHAR, max_length=_MAX_TEXT_BYTES)
        schema.add_field("source", DataType.VARCHAR, max_length=512)
        schema.add_field("heading_path", DataType.VARCHAR, max_length=512)

        index = self.client.prepare_index_params()
        # AUTOINDEX 让 Milvus 自己挑索引类型;COSINE 与 bge-m3 的用法一致
        index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")

        self.client.create_collection(self.collection, schema=schema, index_params=index)

    def upsert(self, rows: list[dict]) -> int:
        """写入(同 id 覆盖)。

        用 upsert 而不是 insert:重新灌库时不会因为主键重复而失败,
        所以 `ingest` 是**幂等**的 —— 改完攻略重跑一遍就行,不用先删库。
        """
        if not rows:
            return 0
        self.client.upsert(self.collection, rows)
        return len(rows)

    def flush(self) -> None:
        self.client.flush(self.collection)

    def delete(self, ids: list[str]) -> int:
        """按主键精确删除,返回删除的条数。

        用**显式 id 列表**而不是 `like "upload:xxx:%"` 前缀匹配:
        「自己记录自己写了什么,删的时候就删什么」最可靠 ——
        前缀匹配在表达式转义上有坑,而且一旦 id 生成规则变了,
        老数据的清理逻辑就会悄悄失效,症状是"删了还在,检索照样搜出来"。
        """
        if not ids or not self.exists():
            return 0

        deleted = 0
        batch = 500
        for i in range(0, len(ids), batch):
            part = ids[i : i + batch]
            self.client.delete(self.collection, ids=part)
            deleted += len(part)
        self.flush()
        return deleted

    def search(self, vector: list[float], top_k: int) -> list[dict]:
        """检索。**collection 还没建时返回空列表,不抛异常。**

        与 `count()` / `delete()` 一样先查 `exists()` —— 这一点不是可有可无的:

        `uploaded` 层在「还没上传过任何攻略」时 collection 是不存在的,这是
        **完全正常的初始状态**,不是故障。但 Milvus 对不存在的 collection 检索
        会抛 `MilvusException: collection 'xxx' does not exist`,而
        `KnowledgeService.retrieve()` 会把任何异常都记进 `last_error` 并打印告警
        —— 于是三个层里只要有一层是空的,每次检索、每次行程生成都会刷一条
        「检索失败(uploaded)」,检索接口还会返回 `partial: true`。
        真正出故障时反而没人注意到那条告警了。
        """
        if not self.exists():
            return []

        # ⚠️ 进程重启后 collection 是 released 状态,不 load 就检索会抛
        #    code=101。详见 `_ensure_loaded()`。
        self._ensure_loaded()

        hits = self.client.search(
            self.collection,
            data=[vector],
            limit=top_k,
            output_fields=["text", "source", "heading_path"],
        )
        out: list[dict] = []
        for h in (hits[0] if hits else []):
            entity = h.get("entity") or {}
            out.append(
                {
                    "content": entity.get("text", ""),
                    "score": float(h.get("distance", 0.0)),
                    "source": entity.get("source", ""),
                    "heading_path": entity.get("heading_path", ""),
                }
            )
        return out

    def count(self) -> int:
        if not self.exists():
            return 0
        stats = self.client.get_collection_stats(self.collection)
        return int(stats.get("row_count", 0))


# ===========================================================================
# 四、服务
# ===========================================================================


class KnowledgeService:
    """三层知识库的统一入口。

    **检索失败绝不能让行程规划挂掉。** RAG 是增强,不是依赖:Milvus 没起来、
    embedding 接口超时、collection 还没建 —— 这些情况下一律返回空结果,
    让管线照常往下走。所以这里每个对外方法都自己吞异常并记录原因,
    由 `available()` 把原因报出来给健康检查和日志看。

    三层各自一个 collection(不是分区):

        poi_facts      内置 POI 事实(高德库存,1750 条)
        city_guides    内置城市攻略(data/knowledge_base/city_guides/*.md)
        uploaded       用户上传的攻略(解析自 Markdown / 文本 / PDF)

    上传内容**单独一层**而不是混进内置攻略,原因是「重灌内置库」会
    drop 整个 collection —— 混在一起的话,用户传的攻略会被一起清掉。
    三层用同一个 embedder、同一个 COSINE 度量,分数**可直接比较**,
    归并时才能公平地排序。
    """

    NAMESPACE_POI = "poi_facts"
    NAMESPACE_GUIDES = "city_guides"
    NAMESPACE_UPLOAD = "uploaded"

    # 三层一起列,stats / available / retrieve 归并都按这份走。
    # 新加层时只改这里 —— 否则总会漏掉某一处,症状是"检索时好时坏"。
    ALL_NAMESPACES = (NAMESPACE_POI, NAMESPACE_GUIDES, NAMESPACE_UPLOAD)

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._embedder: Any = None
        self._embedder_error: str = ""
        self._stores: dict[str, MilvusKnowledgeStore] = {}
        self.last_error: str = ""

    # ---------------- embedder ----------------

    def _ensure_env_loaded(self) -> None:
        """按**绝对路径**把 `backend/.env` 读进 `os.environ`。

        为什么要显式做这件事,而不是指望别人:

        - `app/config.py` 里的 `load_dotenv()` 是按 **cwd** 找 `.env` 的
          (`find_dotenv()` 从当前工作目录往上找)。从仓库根目录启动 Python
          就找不到,配置全空,**而且不报错**。
        - 更隐蔽的是:`pymilvus/settings.py` 在 **import 时**也调 `load_dotenv()`,
          同样是按 cwd。于是「embedder 能不能用」一度取决于**哪个库先被 import**
          —— 实测:只 `import hello_agents` 时 `EMBED_API_KEY` 为空、
          `get_text_embedder()` 直接抛 `RuntimeError`;而先 `import pymilvus`
          就一切正常。

        依赖第三方库的导入副作用来决定自己的配置,是随时会炸的。所以这里
        用绝对路径显式加载一次,与 cwd 无关。
        """
        env_path = BACKEND_DIR / ".env"
        if not env_path.exists():
            return
        try:
            from dotenv import load_dotenv

            # override=False:已经设好的环境变量优先(CI/容器里注入的值不该被覆盖)
            load_dotenv(env_path, override=False)
        except Exception as exc:  # pragma: no cover - python-dotenv 一定在依赖里
            self.last_error = f"加载 .env 失败: {type(exc).__name__}: {exc}"

    def embedder(self) -> Any:
        """构造 embedder(**显式传参**,不读环境变量)。

        为什么不用框架的 `get_text_embedder()`:它内部走的是
        `create_embedding_model_with_fallback()`,会依次尝试
        dashscope → local → tfidf,**把异常吞掉**。万一路径走到 tfidf,
        我们会拿到一个 384 维的假 embedder,然后建出一个 384 维的 collection
        —— 之后**所有检索静默返回空结果,不报任何错**。

        这里改用不带 fallback 的 `create_embedding_model()`:参数全部来自
        `settings`(显式),失败就**直接抛**,再由 `available()` 把原因说出来。
        宁可响亮地失败,也不要安静地退化成假实现。

        ⚠️ 构造本身会**发一次真实的 embedding 请求**:`DashScopeEmbedding.__init__`
        里用 `self.encode("health_check")` 探测维度。所以 embedder 是按需构造、
        并缓存的,不要在热路径里反复 new。
        """
        if self._embedder is not None:
            return self._embedder
        if self._embedder_error:
            raise RuntimeError(self._embedder_error)

        self._ensure_env_loaded()
        try:
            from hello_agents.memory.embedding import create_embedding_model

            embedder = create_embedding_model(
                self.settings.embed_model_type,
                model_name=self.settings.embed_model_name,
                api_key=self.settings.embed_api_key,
                base_url=self.settings.embed_base_url,
            )
            actual = int(getattr(embedder, "dimension", 0) or 0)
            expected = int(self.settings.embed_dim_expected)
            if actual != expected:
                raise RuntimeError(
                    f"embedding 维度对不上:实测 {actual},配置期望 {expected}。"
                    f"换了模型就必须换 collection 名并重建库,否则写入/检索结果会乱"
                )
        except Exception as exc:
            self._embedder_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(self._embedder_error) from exc

        self._embedder = embedder
        return embedder

    def encode(self, texts: str | list[str]) -> list[list[float]]:
        return normalize_vectors(self.embedder().encode(texts))

    # ---------------- stores ----------------

    def store(self, namespace: str) -> MilvusKnowledgeStore:
        # 用显式映射而不是 if/else 逐层判断:三层之后 if/else 已经开始出错,
        # 早期版本里「非 POI 一律当 guides」这种写法,加第三层时必然串层。
        collections = {
            self.NAMESPACE_POI: self.settings.rag_collection_poi,
            self.NAMESPACE_GUIDES: self.settings.rag_collection_guides,
            self.NAMESPACE_UPLOAD: self.settings.rag_collection_uploaded,
        }
        if namespace not in collections:
            raise ValueError(f"未知的知识库分层: {namespace!r}")

        if namespace not in self._stores:
            self._stores[namespace] = MilvusKnowledgeStore(
                uri=self.settings.milvus_uri,
                collection=collections[namespace],
                dimension=int(self.settings.embed_dim_expected),
            )
        return self._stores[namespace]

    # ---------------- 检索 ----------------

    def retrieve(self, query: str, namespace: str, top_k: int | None = None) -> list[KnowledgeHit]:
        """检索某一层。**任何异常都吞掉并返回空列表。**

        RAG 是增强不是依赖 —— 向量库连不上时,行程照样得能生成。
        """
        query = (query or "").strip()
        if not query:
            return []
        k = int(top_k or self.settings.rag_top_k)
        try:
            store = self.store(namespace)
            vector = self.encode(query)[0]
            rows = store.search(vector, top_k=k)
        except Exception as exc:
            self.last_error = f"检索失败({namespace}): {type(exc).__name__}: {exc}"
            print(f"⚠️  {self.last_error} —— 本次跳过知识库,不影响行程生成")
            return []

        return [
            KnowledgeHit(
                content=r.get("content", ""),
                score=float(r.get("score", 0.0)),
                source=r.get("source", ""),
                heading_path=r.get("heading_path", ""),
                namespace=namespace,
            )
            for r in rows
        ]

    def _retrieve_all_layers(self, query: str, k: int) -> list[KnowledgeHit]:
        """三层一起检索后按分数归并。

        抽成私有方法是因为归并逻辑出现了两次(planner 与调试台) ——
        之前「两层各自取 k 条再归并」的规则是逐字复制粘贴的,
        加第三层时已经漏改过一处。归并规则只写一份。

        每层各取 k 条,所以总数最多 3k;要不要截断由调用方决定:
        `retrieve_for_request` 给 planner 用,不截 —— 多几条参考资料没有坏处;
        `retrieve_merged` 给调试台,必须截到 k,否则接口返回的条数
        是调用方要求的三倍。
        """
        hits: list[KnowledgeHit] = []
        for ns in self.ALL_NAMESPACES:
            hits.extend(self.retrieve(query, ns, k))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits

    def retrieve_for_request(self, request: Any, top_k: int | None = None) -> list[KnowledgeHit]:
        """三层一起检索,按分数归并。

        三层用**同一个 embedder、同一个度量(COSINE)**,所以分数**可以直接比**。
        这点值得写清楚:如果各层用了不同的 embedding 模型,分数就不可比,
        归并就会变成"哪层分数虚高哪层霸榜"。
        """
        k = int(top_k or self.settings.rag_top_k)
        query = build_retrieval_query(request)
        return self._retrieve_all_layers(query, k)

    def retrieve_merged(self, query: str, top_k: int | None = None) -> list[KnowledgeHit]:
        """三层一起检索,归并后返回**最多 top_k 条**。

        与 `retrieve_for_request` 的区别:那个接受 TripRequest、用
        `build_retrieval_query()` 拼 query(给 planner 用);这个直接接受一个字符串
        (给检索调试台用)。

        归并逻辑与 `scripts/ingest_knowledge.py --query` **共用这一份** ——
        另写一份迟早会漂移,那时候「命令行搜得到、界面搜不到」就说不清是谁的错了。

        ⚠️ `top_k` 的语义是「**返回**多少条」,不是「每层取多少条」。
        三层各自取 k 条再归并,必须截断到 k —— 详见 `_retrieve_all_layers`。
        """
        k = int(top_k or self.settings.rag_top_k)
        return self._retrieve_all_layers(query, k)[:k]

    def build_context(self, request: Any, top_k: int | None = None) -> str:
        """给 planner 用的、可直接拼进 prompt 的引用块。检索不到时返回空串。"""
        return format_context(self.retrieve_for_request(request, top_k))

    # ---------------- 灌库预览（不花钱） ----------------
    #
    # 这两个方法原来是 scripts/ingest_knowledge.py 里的私有函数（_count_poi /
    # _count_guides）。做成 HTTP 接口时必须下沉到这里 —— 脚本自己的注释说得很清楚:
    # 「如果这里另写一份统计逻辑,数字和实际入库的就不会一致,那种'预估'没有意义」。
    # 现在 CLI 和 API 共用同一份实现。

    def preview_poi_facts(self, inventory_path: Path | str | None = None) -> dict:
        """统计 `poi_facts` 层将入库多少条。**不调用 embedding,不花钱。**

        过滤条件（id 与 name 都非空）与 `ingest_poi_facts` 逐字相同,
        这样预览的数字和实际入库的数字才对得上。
        """
        path = (
            Path(inventory_path)
            if inventory_path
            else resolve_dir(self.settings.frozen_dir) / "amap" / "poi_inventory.json"
        )
        if not path.exists():
            return {"total": 0, "per_city": {}, "path": str(path), "exists": False}

        data = json.loads(path.read_text(encoding="utf-8"))
        cities = data.get("cities") or {}

        per_city: dict[str, int] = {}
        for city, payload in cities.items():
            per_city[city] = sum(
                1 for p in (payload or {}).get("pois") or [] if p.get("id") and p.get("name")
            )

        return {
            "total": sum(per_city.values()),
            "per_city": per_city,
            "path": str(path),
            "size_kb": round(path.stat().st_size / 1024, 1),
            "exists": True,
        }

    def preview_city_guides(self, guides_dir: Path | str | None = None) -> dict:
        """统计 `city_guides` 层将入库多少块。**不调用 embedding,不花钱。**

        复用入库时同一个 `split_markdown_sections()` —— 切分逻辑改了,
        预览和实际入库会一起改,不会出现「预览说 30 块、实际灌进去 24 块」。
        """
        directory = (
            Path(guides_dir)
            if guides_dir
            else resolve_dir(self.settings.knowledge_dir) / "city_guides"
        )
        if not directory.exists():
            return {"total": 0, "per_file": {}, "files": [], "dir": str(directory), "exists": False}

        files: list[dict] = []
        per_file: dict[str, int] = {}
        for f in sorted(directory.glob("*.md")):
            sections = split_markdown_sections(f.read_text(encoding="utf-8"), source=f.name)
            per_file[f.name] = len(sections)
            files.append(
                {
                    "name": f.name,
                    "sections": len(sections),
                    "size_kb": round(f.stat().st_size / 1024, 1),
                }
            )

        return {
            "total": sum(per_file.values()),
            "per_file": per_file,
            "files": files,
            "dir": str(directory),
            "exists": True,
        }

    # ---------------- 灌库 ----------------

    def _embed_in_batches(self, texts: list[str], progress_cb: Any = None) -> list[list[float]]:
        """分批编码。一次发太多会超接口单请求上限;一次发一条则请求数爆炸。

        Args:
            texts: 待编码文本
            progress_cb: 可选回调 `(processed, total) -> None`,每批完成后调用一次。
                灌 1750 条要 55 批、分钟级,没有进度回调的话前端只能干等。
        """
        vectors: list[list[float]] = []
        total = len(texts)
        for i in range(0, total, EMBED_BATCH):
            batch = texts[i : i + EMBED_BATCH]
            vectors.extend(self.encode(batch))
            if progress_cb is not None:
                progress_cb(len(vectors), total)
        return vectors

    def ingest_poi_facts(
        self,
        inventory_path: Path | str | None = None,
        recreate: bool = False,
        progress_cb: Any = None,
    ) -> dict:
        """把冻结的高德 POI 库存灌进 `poi_facts`。

        数据源是 `data/frozen/amap/poi_inventory.json`(P2 录制的 ground truth)
        —— 用它而不是现调高德,是为了**不花额度、结果可复现**。
        """
        path = (
            Path(inventory_path)
            if inventory_path
            else resolve_dir(self.settings.frozen_dir) / "amap" / "poi_inventory.json"
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        cities = data.get("cities") or {}

        store = self.store(self.NAMESPACE_POI)
        store.ensure_collection(recreate=recreate)

        texts: list[str] = []
        metas: list[dict] = []
        for city, payload in cities.items():
            for poi in (payload or {}).get("pois") or []:
                pid = str(poi.get("id") or "").strip()
                if not pid or not poi.get("name"):
                    continue
                texts.append(fit_utf8(poi_to_text(poi), _MAX_TEXT_BYTES))
                metas.append(
                    {
                        # 用**高德自己的 POI id** 做主键,不用序号。
                        # 序号会随库存顺序变化,重灌时旧行不会被覆盖,只会**越堆越多**,
                        # 而检索时那些孤儿行照样会被搜出来 —— 症状是"删了的数据还在"。
                        # 高德 id 稳定且唯一(实测 1750 行 0 重复),upsert 才是幂等的。
                        "id": f"poi:{pid}",
                        "source": "poi_inventory.json",
                        "heading_path": poi_heading_path(poi),
                    }
                )

        if not texts:
            return {"namespace": self.NAMESPACE_POI, "collection": store.collection, "count": 0}

        vectors = self._embed_in_batches(texts, progress_cb=progress_cb)
        rows = [
            {
                "id": meta["id"],
                "vector": vec,
                "text": txt,
                "source": meta["source"],
                "heading_path": meta["heading_path"],
            }
            for vec, txt, meta in zip(vectors, texts, metas)
        ]
        store.upsert(rows)
        store.flush()
        return {
            "namespace": self.NAMESPACE_POI,
            "collection": store.collection,
            "count": len(rows),
            "cities": list(cities.keys()),
        }

    def ingest_city_guides(
        self,
        guides_dir: Path | str | None = None,
        recreate: bool = False,
        progress_cb: Any = None,
    ) -> dict:
        """把 `data/knowledge_base/city_guides/*.md` 灌进 `city_guides`。"""
        directory = (
            Path(guides_dir)
            if guides_dir
            else resolve_dir(self.settings.knowledge_dir) / "city_guides"
        )
        files = sorted(directory.glob("*.md"))
        if not files:
            raise FileNotFoundError(f"{directory} 下没有 .md 攻略文件")

        store = self.store(self.NAMESPACE_GUIDES)
        store.ensure_collection(recreate=recreate)

        texts: list[str] = []
        metas: list[dict] = []
        per_file: dict[str, int] = {}
        for f in files:
            sections = split_markdown_sections(f.read_text(encoding="utf-8"), source=f.name)
            per_file[f.name] = len(sections)
            for idx, (heading_path, chunk) in enumerate(sections):
                texts.append(fit_utf8(chunk, _MAX_TEXT_BYTES))
                metas.append({"source": f.name, "heading_path": heading_path, "idx": idx})

        vectors = self._embed_in_batches(texts, progress_cb=progress_cb)
        rows = [
            {
                "id": f"guide:{meta['source']}:{meta['idx']}",
                "vector": vec,
                "text": txt,
                "source": meta["source"],
                "heading_path": meta["heading_path"],
            }
            for vec, txt, meta in zip(vectors, texts, metas)
        ]
        store.upsert(rows)
        store.flush()
        return {
            "namespace": self.NAMESPACE_GUIDES,
            "collection": store.collection,
            "count": len(rows),
            "files": per_file,
        }

    # ---------------- 用户上传的攻略 ----------------

    def ingest_uploaded_doc(
        self,
        *,
        doc_id: str,
        title: str,
        chunks: list[tuple[str, str]],
        progress_cb: Any = None,
    ) -> dict:
        """把一篇用户上传的攻略灌进 `uploaded` 层。

        与内置攻略分开灌(而不是复用 `ingest_city_guides`)的原因:
        内置攻略是「全量重灌」语义 —— 遍历目录、把整个 collection 重建;
        上传文档是「单篇增量」语义 —— 只写这一篇、删除时只删这一篇。
        两种生命周期硬塞进一个方法,迟早会互相踩。

        chunk 主键 `upload:{doc_id}:{idx}`:
        doc_id 由正文哈希得来,所以同一份内容重传时 id 完全一致,
        upsert 直接覆盖 —— 这就是为什么重复上传不会在库里越堆越多。
        """
        store = self.store(self.NAMESPACE_UPLOAD)
        store.ensure_collection()

        texts: list[str] = []
        metas: list[dict] = []
        for idx, (heading_path, chunk) in enumerate(chunks):
            texts.append(fit_utf8(chunk, _MAX_TEXT_BYTES))
            metas.append({"heading_path": heading_path, "idx": idx})

        if not texts:
            return {"namespace": self.NAMESPACE_UPLOAD, "count": 0, "chunk_ids": []}

        vectors = self._embed_in_batches(texts, progress_cb=progress_cb)
        rows = [
            {
                "id": f"upload:{doc_id}:{meta['idx']}",
                "vector": vec,
                "text": txt,
                # source 存的是**攻略标题**而不是文件名 ——
                # 行程详情页展示出处时,用户要认的是「成都三日游避坑」,
                # 不是「download(3).md」。
                "source": title,
                "heading_path": meta["heading_path"],
            }
            for vec, txt, meta in zip(vectors, texts, metas)
        ]
        store.upsert(rows)
        store.flush()

        chunk_ids = [r["id"] for r in rows]
        return {
            "namespace": self.NAMESPACE_UPLOAD,
            "collection": store.collection,
            "count": len(rows),
            "chunk_ids": chunk_ids,
        }

    def delete_uploaded_doc(self, chunk_ids: list[str]) -> int:
        """把一篇上传文档从 Milvus 里删掉。

        chunk_ids 来自 SQLite 里 `knowledge_docs.chunk_ids` ——
        入库时记录了写了哪些,删除时就删哪些。
        **先删这里、再删 SQLite 记录**:反过来一旦这里失败,
        库里就剩下一批没有任何记录指向的孤儿块。
        """
        if not chunk_ids:
            return 0
        return self.store(self.NAMESPACE_UPLOAD).delete(chunk_ids)

    # ---------------- 状态 ----------------

    def available(self) -> tuple[bool, str]:
        """`(能不能用, 原因)`。

        先试 embedder 再试 Milvus:两者失败的原因完全不同,分开报才排得动错。
        失败原因会原样透出来(例如 `EMBED_API_KEY` 没配 / Milvus 没起来),
        不要吞成一句"RAG 不可用"。
        """
        try:
            self.embedder()
        except Exception as exc:
            return False, f"embedding 不可用 — {exc}"
        try:
            self.store(self.NAMESPACE_GUIDES).count()
        except Exception as exc:
            return False, f"Milvus 不可用 — {type(exc).__name__}: {exc}"
        return True, "ok"

    def stats(self) -> dict:
        out: dict[str, Any] = {
            "milvus_uri": self.settings.milvus_uri,
            "last_error": self.last_error,
        }
        # 按 ALL_NAMESPACES 遍历,而不是手写两行 —— 加第三层时漏改这里的
        # 症状是:上传成功、状态接口里却没有 uploaded 这一项。
        for ns in self.ALL_NAMESPACES:
            store = self.store(ns)
            try:
                out[ns] = {
                    "collection": store.collection,
                    "exists": store.exists(),
                    "count": store.count(),
                }
            except Exception as exc:
                out[ns] = {"collection": store.collection, "error": f"{type(exc).__name__}: {exc}"}
        return out


# ===========================================================================
# 五、单例
# ===========================================================================

_service: KnowledgeService | None = None


def get_knowledge_service() -> KnowledgeService:
    global _service
    if _service is None:
        _service = KnowledgeService()
    return _service


def reset_knowledge_service() -> None:
    """测试用:丢掉单例,让下次调用重新构造。"""
    global _service
    _service = None
