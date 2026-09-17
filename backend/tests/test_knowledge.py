"""knowledge_service.py 里**纯函数**的单元测试 —— 分块、编码形态、引用格式

为什么这些函数值得单独测:

它们**几乎都不抛异常**。文本切错了、别名丢了、向量少升了一维,大多数情况下
不会报错,只会安静地让检索变差 —— 或者让写入直接失败。「检索质量下降」
是最难发现的一类 bug:指标掉了,但没人知道是哪一步掉的。

RAG 的**检索质量**没法在单测里验证(那要真调 embedding 接口,花钱且不稳定),
但「输入是怎么被加工成入库文本的」完全可以 —— 而这一层出错的概率,
比 embedding 模型本身高得多。

不测的部分(需要真服务,归 `scripts/ingest_knowledge.py` 的冒烟验证管):
`MilvusKnowledgeStore` 的连接与读写、`KnowledgeService.ingest_*`。
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.config import get_settings
from app.models.schemas import TripRequest
from app.services.knowledge_service import (
    KnowledgeHit,
    build_retrieval_query,
    fit_utf8,
    format_context,
    normalize_aliases,
    normalize_vectors,
    poi_heading_path,
    poi_to_text,
    primary_types,
    resolve_dir,
    split_markdown_sections,
)

# real 的高德 type 字段长这样(分号分层级、竖线并列)
AMAP_TYPE = "风景名胜;风景名胜;红色景区|风景名胜;公园广场;城市广场"


# ===========================================================================
# fit_utf8 —— 按字节截断
# ===========================================================================


class TestFitUtf8:
    def test_短文本原样返回(self):
        assert fit_utf8("故宫", 100) == "故宫"

    def test_正好等长时也算没过界(self):
        text = "中" * 10  # 30 字节
        assert fit_utf8(text, 30) == text

    def test_按字节而不是字符截断(self):
        """这是它存在的原因:Milvus 的 VARCHAR(max_length) 按**字节**算。

        100 个汉字 = 300 字节。上限给了 100 字节,所以只能留下 33 个字
        (99 字节)。如果按字符数截断会留下 100 个字 = 300 字节,**超限 3 倍**,
        Milvus 写入时直接报错。
        """
        result = fit_utf8("中" * 100, 100)
        assert len(result) == 33
        assert len(result.encode("utf-8")) <= 100

    def test_切在汉字中间时不抛异常(self):
        """101 不是 3 的倍数 → 第 34 个字只截到 2 个字节。

        直接 `raw[:101].decode()` 会抛 UnicodeDecodeError ——
        一个「文本太长」的小问题会变成写入失败。errors="ignore" 兜住它。
        """
        result = fit_utf8("中" * 100, 101)  # 不应抛异常
        assert len(result) == 33
        assert result.encode("utf-8").decode("utf-8") == result  # 仍是合法 UTF-8

    def test_截断结果能再编码回去(self):
        result = fit_utf8("北京故宫博物院" * 50, 200)
        assert len(result.encode("utf-8")) <= 200


# ===========================================================================
# normalize_vectors —— 统一 embedder 的两种返回形态
# ===========================================================================


class TestNormalizeVectors:
    def test_一维向量会升成二维(self):
        """**真踩过的坑。**

        框架的 `encode("单个字符串")` 返回 1 维 `(1024,)`,`encode([...])` 返回
        2 维 `(n, 1024)`。直接把 1 维塞给 Milvus 的 `data=` 会报:

            ParamError: `search_data` value [-0.0112 0.0108 ...] is illegal

        因为 Milvus 要的是「一批向量」,不是一个向量。
        """
        result = normalize_vectors(np.zeros(1024, dtype=np.float32))
        assert len(result) == 1
        assert len(result[0]) == 1024

    def test_二维保持形状(self):
        result = normalize_vectors([np.zeros(1024), np.ones(1024)])
        assert len(result) == 2
        assert len(result[0]) == 1024

    def test_列表套列表也认(self):
        result = normalize_vectors([[0.1, 0.2, 0.3]])
        assert len(result) == 1
        assert len(result[0]) == 3

    def test_返回的是普通列表而不是_numpy_数组(self):
        """必须是纯 Python 类型 —— numpy 标量没法直接 json 序列化,
        而检索结果要写进 JSONL 日志和评测报告。"""
        result = normalize_vectors(np.zeros(4, dtype=np.float32))
        json.dumps(result)  # 能序列化就说明转换干净了
        assert isinstance(result[0][0], float)


# ===========================================================================
# primary_types —— 收敛高德的类型字段
# ===========================================================================


class TestPrimaryTypes:
    def test_两组首段相同时去重成一个(self):
        """**这是最常见的情况**(库存 1750 条里 1106 条长这样)。

        两组的首段都是"风景名胜",第二组只是换了条子分类路径
        (`公园广场`/`城市广场` 本来就是 `风景名胜` 的下级)。
        所以结果只有一个词 —— 这正是我们想要的:取**最粗**的类别。
        """
        assert primary_types(AMAP_TYPE) == "风景名胜"

    def test_两组首段不同时都保留(self):
        """故宫博物院的真实 type:跨了两个大类。"""
        real = "风景名胜;风景名胜;世界遗产|科教文化服务;博物馆;博物馆"
        assert primary_types(real) == "风景名胜 / 科教文化服务"

    def test_去重(self):
        assert primary_types("风景名胜;A|风景名胜;B") == "风景名胜"

    def test_限制条数(self):
        real = "风景名胜;风景名胜;世界遗产|科教文化服务;博物馆;博物馆"
        assert primary_types(real, limit=1) == "风景名胜"

    def test_没有竖线也能处理(self):
        assert primary_types("风景名胜;公园广场;城市广场") == "风景名胜"

    @pytest.mark.parametrize("raw", [None, "", "   ", ";;", "|"])
    def test_空值返回空串(self, raw):
        assert primary_types(raw) == ""


# ===========================================================================
# poi_heading_path / poi_to_text
# ===========================================================================


class TestPoiHeadingPath:
    def test_城市加类别(self):
        assert poi_heading_path({"city": "北京市", "type": AMAP_TYPE}) == "北京市 > 风景名胜"

    def test_只有城市(self):
        assert poi_heading_path({"city": "北京市"}) == "北京市"

    def test_都没有返回空串(self):
        assert poi_heading_path({}) == ""


class TestNormalizeAliases:
    """**这个类是从一次真事故里长出来的。**

    第一版的 `poi_to_text` 写的是 `isinstance(aliases, (list, tuple))` ——
    看着很合理,而且**单测全绿**(因为测试里手写的是 `["紫禁城"]`)。
    但真实数据里,有别名的那 240 条**全部是字符串**,列表**全部是空的**:

        240 条  alias = "紫禁城"      ← 有数据的
       1510 条  alias = []            ← 没数据的

    于是守卫条件对**恰好有数据的那批恒为假**,别名一行都没进库,
    **而且不报任何错**。最后是靠「搜『紫禁城』能不能搜到故宫」这条
    端到端验证才发现的。

    教训:测试要按**数据的真实形状**写,不是按你**以为**的。
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # ---- 真实数据里的形态 ----
            ("紫禁城", ["紫禁城"]),
            ("中国历史博物馆|北京历史博物馆", ["中国历史博物馆", "北京历史博物馆"]),
            ([], []),
            # ---- 防御性形态 ----
            (None, []),
            ("", []),
            ("  ", []),
            ("  紫禁城  ", ["紫禁城"]),
            (["a", "b"], ["a", "b"]),
            (["a|b", "c"], ["a", "b", "c"]),
            (["", "  ", "真别名"], ["真别名"]),
            (("a",), ["a"]),
            (123, []),  # 完全不认识的类型,返回空而不是抛
        ],
    )
    def test_各种形态都归一成字符串列表(self, raw, expected):
        assert normalize_aliases(raw) == expected

    def test_字符串形态必须被认(self):
        """**这一条就是那次事故的回归测试。** 只要有人再把它改回
        「只认 list/tuple」,这里立刻变红。"""
        assert normalize_aliases("紫禁城") == ["紫禁城"]


class TestPoiToText:
    def test_字符串形态的别名会进文本(self):
        """按**真实数据的形状**(字符串)测,不是 `["紫禁城"]`。"""
        text = poi_to_text({"name": "故宫博物院", "alias": "紫禁城"})
        assert "紫禁城" in text
        assert "故宫博物院" in text

    def test_竖线分隔的多个别名都会进文本(self):
        text = poi_to_text({"name": "秦始皇帝陵博物院", "alias": "西安兵马俑|秦始皇兵马俑博物馆"})
        assert "西安兵马俑" in text
        assert "秦始皇兵马俑博物馆" in text

    def test_列表形态也仍然支持(self):
        text = poi_to_text({"name": "故宫博物院", "alias": ["紫禁城", "故宫"]})
        assert "紫禁城" in text

    def test_没有别名时不出现别名段(self):
        text = poi_to_text({"name": "天安门广场", "alias": []})
        assert "别名" not in text

    def test_没有别名段时不留下孤零零的分隔符(self):
        for empty in ([], "", None, ["", "  "]):
            text = poi_to_text({"name": "某景点", "alias": empty})
            assert "别名" not in text

    def test_带坐标(self):
        text = poi_to_text({"name": "天安门广场", "location": [116.397755, 39.903182]})
        assert "116.397755" in text
        assert "39.903182" in text

    def test_坐标形态不对时不抛异常也不编造(self):
        """location 可能是 None、空列表、或只有一个数。**不能编一个坐标出来**
        —— 这正是这个项目一直在修的那类问题。"""
        for bad in (None, [], [116.4], "116.4,39.9"):
            text = poi_to_text({"name": "某景点", "location": bad})
            assert "坐标" not in text

    def test_空字典返回空串(self):
        assert poi_to_text({}) == ""

    def test_完整字段拼成一行(self):
        text = poi_to_text(
            {
                "name": "天安门广场",
                "alias": ["天安门"],
                "address": "东长安街",
                "location": [116.397755, 39.903182],
                "type": AMAP_TYPE,
                "city": "北京市",
            }
        )
        assert text.count(" | ") == 4
        assert text.startswith("天安门广场")


# ===========================================================================
# split_markdown_sections —— 按标题切攻略
# ===========================================================================


class TestSplitMarkdownSections:
    def test_按标题切分(self):
        # `# 北京` 下面也写了内容,所以它自己也算一节
        md = "# 北京\n\n北京是座适合慢慢走的城市。\n\n## 门票\n\n故宫 60 元\n\n## 交通\n\n地铁 1 号线\n"
        sections = split_markdown_sections(md, source="北京.md")
        paths = [p for p, _ in sections]
        assert paths == ["北京", "北京 > 门票", "北京 > 交通"]

    def test_顶层标题没有正文时不留空块(self):
        """`# 北京` 后面直接就是 `## 门票` —— 这一节没有内容可索引,
        留着只会多花一次 embedding 调用。"""
        md = "# 北京\n\n## 门票\n\n故宫 60 元\n\n## 交通\n\n地铁 1 号线\n"
        sections = split_markdown_sections(md, source="北京.md")
        assert [p for p, _ in sections] == ["北京 > 门票", "北京 > 交通"]

    def test_标题路径反映层级(self):
        md = "# 北京\n\n## 门票与预约\n\n故宫要预约\n"
        sections = split_markdown_sections(md)
        assert sections[-1][0] == "北京 > 门票与预约"

    def test_层级回退时出栈(self):
        """**这道题能抓到真 bug。**

        `## 交通` 和 `## 门票` 是**同级**,所以处理「交通」时必须先把「门票」
        弹出栈。忘了弹的话路径会变成 "北京 > 门票 > 交通" —— 一个根本不存在的
        章节层级,而且引用的出处就错了。
        """
        md = "# 北京\n\n## 门票\n\n内容A\n\n## 交通\n\n内容B\n"
        sections = split_markdown_sections(md)
        paths = [p for p, _ in sections]
        assert "北京 > 交通" in paths
        assert "北京 > 门票 > 交通" not in paths

    def test_深层标题能继承上层(self):
        md = "# 北京\n\n## 故宫\n\n### 门票\n\n60 元\n"
        sections = split_markdown_sections(md)
        assert sections[-1][0] == "北京 > 故宫 > 门票"

    def test_只有标题没有正文的小节被跳过(self):
        """空小节索引进库也检索不出东西,白白占一次 embedding 调用。"""
        md = "# 北京\n\n## 空的\n\n## 有内容的\n\n正文\n"
        sections = split_markdown_sections(md)
        assert all("空的" not in path for path, _ in sections)

    def test_标题之前的前言保留且路径为空(self):
        md = "这是写在最前面的引言。\n\n# 北京\n\n正文\n"
        sections = split_markdown_sections(md)
        assert sections[0][0] == ""
        assert "引言" in sections[0][1]

    def test_分块文本里带着标题(self):
        """正文本身往往看不出在说哪个城市(「门票 60 元」)。
        把标题拼进被索引的文本里,向量才知道这段属于「北京 > 故宫」。"""
        md = "# 北京\n\n## 故宫\n\n门票 60 元\n"
        sections = split_markdown_sections(md)
        assert sections[-1][1].startswith("北京 > 故宫")

    def test_空文本返回空列表(self):
        assert split_markdown_sections("") == []
        assert split_markdown_sections("\n\n  \n") == []

    def test_井号在正文中间不算标题(self):
        md = "# 北京\n\n票价 #1 贵得要命\n"
        sections = split_markdown_sections(md)
        assert len(sections) == 1


# ===========================================================================
# build_retrieval_query
# ===========================================================================


class TestBuildRetrievalQuery:
    def _request(self, **overrides) -> TripRequest:
        base = {
            "city": "北京",
            "start_date": "2026-06-01",
            "end_date": "2026-06-03",
            "travel_days": 3,
            "transportation": "公共交通",
            "accommodation": "经济型酒店",
            "preferences": [],
            "free_text_input": "",
        }
        base.update(overrides)
        return TripRequest(**base)

    def test_城市加偏好加自由输入(self):
        q = build_retrieval_query(
            self._request(preferences=["历史文化"], free_text_input="想吃烤鸭")
        )
        assert "北京" in q
        assert "历史文化" in q
        assert "想吃烤鸭" in q

    def test_不含天数和日期(self):
        """**这是个刻意的设计,所以要有测试守着它。**

        「3 天」「2026-06-01」不影响「这个城市有什么值得去的」——
        放进查询里只会稀释语义,把向量推向无关区域。
        """
        q = build_retrieval_query(self._request(travel_days=7, start_date="2026-06-01"))
        assert "7" not in q
        assert "2026" not in q

    def test_不含交通和住宿(self):
        q = build_retrieval_query(self._request(transportation="自驾", accommodation="民宿"))
        assert "自驾" not in q
        assert "民宿" not in q

    def test_只有城市时也能用(self):
        assert build_retrieval_query(self._request()) == "北京"

    def test_不会因为空字段留下多余空格(self):
        q = build_retrieval_query(self._request(preferences=[], free_text_input=""))
        assert q == q.strip()
        assert "  " not in q


# ===========================================================================
# format_context —— 带出处的引用块
# ===========================================================================


class TestFormatContext:
    def test_空结果返回空串(self):
        """调用方靠「是不是空串」来决定要不要往 prompt 里加这一段。
        返回一句「没有检索到内容」会污染 prompt,而且模型可能把它当资料。"""
        assert format_context([]) == ""

    def test_带来源和标题路径(self):
        hits = [
            KnowledgeHit(
                content="故宫需提前 7 天预约",
                score=0.8,
                source="北京.md",
                heading_path="北京 > 门票与预约",
            )
        ]
        text = format_context(hits)
        assert "北京.md" in text
        assert "北京 > 门票与预约" in text
        assert "故宫需提前 7 天预约" in text

    def test_编号从1开始且递增(self):
        hits = [KnowledgeHit(content=f"内容{i}", score=0.5, source="a.md") for i in range(3)]
        text = format_context(hits)
        assert "[1] 来源: a.md" in text
        assert "[3] 来源: a.md" in text

    def test_没有标题路径时只显示来源(self):
        hits = [KnowledgeHit(content="x", score=0.5, source="a.md", heading_path="")]
        assert "[1] 来源: a.md" in format_context(hits)
        assert "a.md > " not in format_context(hits)

    def test_内容原样保留不截断(self):
        long_text = "很长的内容" * 100
        hits = [KnowledgeHit(content=long_text, score=0.5, source="a.md")]
        assert long_text in format_context(hits)

    def test_每条来源各自出现(self):
        hits = [
            KnowledgeHit(content="A", score=0.9, source="北京.md", heading_path="北京 > 门票"),
            KnowledgeHit(content="B", score=0.8, source="poi_inventory.json", heading_path="北京市 > 风景名胜"),
        ]
        text = format_context(hits)
        assert "北京.md > 北京 > 门票" in text
        assert "poi_inventory.json > 北京市 > 风景名胜" in text


# ===========================================================================
# 拿**真实录制的库存**反过来验 —— 这一类测试才抓得住「形状假设错了」
# ===========================================================================
#
# 与 `tests/test_geo.py::TestBboxAgainstRealData` 同一个思路:不构造理想输入,
# 而是读 `data/frozen/` 里真实录下来的高德数据。上面那些用字面量构造的测试
# 只能验证「我以为的数据长什么样」;只有读真数据,才能验证「数据实际长什么样」。
#
# 前面那个别名事故就是被这类测试抓住的:构造测试全绿,一碰真数据就露馅。


def _inventory_path():
    return resolve_dir(get_settings().frozen_dir) / "amap" / "poi_inventory.json"


@pytest.fixture(scope="module")
def real_pois():
    path = _inventory_path()
    if not path.exists():
        pytest.skip(f"没有冻结库存: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [p for payload in (data.get("cities") or {}).values() for p in (payload or {}).get("pois") or []]


class TestAliasesAgainstRealData:
    def test_库存里确实有带别名的_POI(self, real_pois):
        """如果这条挂了,说明数据换了或格式变了 —— 下面几条就不再成立,
        需要重新看一眼,而不是当成「测试自己坏了」。"""
        with_alias = [p for p in real_pois if normalize_aliases(p.get("alias"))]
        assert len(with_alias) > 100, f"带别名的只有 {len(with_alias)} 条,预期上百条"

    def test_每一条别名都真的进了索引文本(self, real_pois):
        """**这是那次事故的核心回归测试。**

        断言的是「对每一条有别名的 POI,poi_to_text 的输出里都能找到它的别名」。
        第一版代码在这里会**全量失败** —— 因为 `isinstance(raw, (list, tuple))`
        对真实数据里的字符串恒为假。
        """
        missing: list[str] = []
        for p in real_pois:
            aliases = normalize_aliases(p.get("alias"))
            if not aliases:
                continue
            text = poi_to_text(p)
            for a in aliases:
                if a not in text:
                    missing.append(f"{p.get('name')} 的别名 {a!r} 没进文本")
        assert not missing, "有别名没进索引文本:\n" + "\n".join(missing[:10])

    def test_几个最大牌景点的别名确实带上了(self, real_pois):
        """挑几个「俗称和官方名差最远、最该被搜到」的做定点验证。"""
        interesting = {"故宫博物院", "秦始皇帝陵博物院", "杭州西湖风景名胜区"}
        found = {p.get("name"): poi_to_text(p) for p in real_pois if p.get("name") in interesting}
        assert "故宫博物院" in found, "库存里没有故宫,数据可能变了"
        assert "紫禁城" in found["故宫博物院"]
        if "秦始皇帝陵博物院" in found:
            assert "兵马俑" in found["秦始皇帝陵博物院"]
        if "杭州西湖风景名胜区" in found:
            assert "西湖景区" in found["杭州西湖风景名胜区"]

    def test_每一条有坐标的_POI_坐标都进了文本(self, real_pois):
        """坐标是高德搜索**不给**、必须靠 detail 补的那个字段。
        进不了索引文本的话,planner 就还是拿不到真坐标。"""
        bad = []
        for p in real_pois:
            loc = p.get("location") or []
            if isinstance(loc, (list, tuple)) and len(loc) == 2:
                if f"{loc[0]},{loc[1]}" not in poi_to_text(p):
                    bad.append(p.get("name"))
        assert not bad, f"有坐标但没进文本: {bad[:5]}"
