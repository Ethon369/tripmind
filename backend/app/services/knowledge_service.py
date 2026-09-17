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
_EMBED_BATCH = 32


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
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [a.strip() for a in raw.split("|") if a.strip()]
    if isinstance(raw, (list, tuple, set)):
        out: list[str] = []
        for item in raw:
            # 列表里也可能装着 `|` 分隔的字符串,统一按同一个规则拆
            out.extend(a.strip() for a in str(item).split("|") if a.strip())
        return out
    return []


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

    @property
    def client(self) -> Any:
        if self._client is None:
            from pymilvus import MilvusClient  # 延迟导入:不检索就不需要它

            self._client = MilvusClient(uri=self.uri)
        return self._client

    def exists(self) -> bool:
        return bool(self.client.has_collection(self.collection))

    def drop(self) -> None:
        if self.exists():
            self.client.drop_collection(self.collection)

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

    def search(self, vector: list[float], top_k: int) -> list[dict]:
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
    """双层知识库的统一入口。

    **检索失败绝不能让行程规划挂掉。** RAG 是增强,不是依赖:Milvus 没起来、
    embedding 接口超时、collection 还没建 —— 这些情况下一律返回空结果,
    让管线照常往下走。所以这里每个对外方法都自己吞异常并记录原因,
    由 `available()` 把原因报出来给健康检查和日志看。
    """

    NAMESPACE_POI = "poi_facts"
    NAMESPACE_GUIDES = "city_guides"

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
        key = self.NAMESPACE_POI if namespace == self.NAMESPACE_POI else self.NAMESPACE_GUIDES
        if key not in self._stores:
            collection = (
                self.settings.rag_collection_poi
                if key == self.NAMESPACE_POI
                else self.settings.rag_collection_guides
            )
            self._stores[key] = MilvusKnowledgeStore(
                uri=self.settings.milvus_uri,
                collection=collection,
                dimension=int(self.settings.embed_dim_expected),
            )
        return self._stores[key]

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

    def retrieve_for_request(self, request: Any, top_k: int | None = None) -> list[KnowledgeHit]:
        """两层一起检索,按分数归并。

        两层用**同一个 embedder、同一个度量(COSINE)**,所以分数**可以直接比**。
        这点值得写清楚:如果两层用了不同的 embedding 模型,分数就不可比,
        归并就会变成"哪层分数虚高哪层霸榜"。
        """
        k = int(top_k or self.settings.rag_top_k)
        query = build_retrieval_query(request)
        hits = self.retrieve(query, self.NAMESPACE_GUIDES, k) + self.retrieve(
            query, self.NAMESPACE_POI, k
        )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits

    def build_context(self, request: Any, top_k: int | None = None) -> str:
        """给 planner 用的、可直接拼进 prompt 的引用块。检索不到时返回空串。"""
        return format_context(self.retrieve_for_request(request, top_k))

    # ---------------- 灌库 ----------------

    def _embed_in_batches(self, texts: list[str]) -> list[list[float]]:
        """分批编码。一次发太多会超接口单请求上限;一次发一条则请求数爆炸。"""
        vectors: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH):
            batch = texts[i : i + _EMBED_BATCH]
            vectors.extend(self.encode(batch))
        return vectors

    def ingest_poi_facts(self, inventory_path: Path | str | None = None, recreate: bool = False) -> dict:
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

        vectors = self._embed_in_batches(texts)
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

    def ingest_city_guides(self, guides_dir: Path | str | None = None, recreate: bool = False) -> dict:
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

        vectors = self._embed_in_batches(texts)
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
        for ns in (self.NAMESPACE_POI, self.NAMESPACE_GUIDES):
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
