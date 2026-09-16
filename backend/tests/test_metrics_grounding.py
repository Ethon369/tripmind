"""metrics_grounding.py 的单元测试 —— 接地性指标

只覆盖**纯函数**:名字相似度、库存查找、两条指标的计算。
读库存文件的部分用临时目录测,不去碰真实的 `data/frozen/`。

不联网 —— ground truth 在测试里现造,真实库存由 scripts/recorder.py 产出。
"""

from __future__ import annotations

import json

import pytest

from app.eval import config as cfg
from app.eval.metrics import EvalContext
from app.eval.metrics_grounding import (
    best_match,
    candidate_names,
    checked_verdict,
    ground_truth_for,
    load_inventory,
    load_name_check,
    m_coord_mae_km,
    m_poi_exists_rate,
    m_poi_support_rate,
    name_check_for,
    name_similarity,
    normalize_name,
)
from tests.conftest import make_attraction, make_day, make_plan, make_request


# ===========================================================================
# 测试数据
# ===========================================================================

# 故宫 / 天坛 / 颐和园 的真实坐标(取自高德,量级正确即可)
REAL = {
    "故宫博物院": (116.397, 39.918),
    "天坛公园": (116.410, 39.882),
    "颐和园": (116.275, 39.999),
}


def make_gt(names: list[str] | None = None) -> dict:
    """造一份 ground_truth(某个城市的库存切片)。"""
    names = names if names is not None else list(REAL)
    return {
        "city": "北京",
        "pois": [
            {"id": f"B{i:04d}", "name": n, "location": list(REAL[n]), "type": "风景名胜"}
            for i, n in enumerate(names)
        ],
    }


def one_day_plan(attractions: list[dict]) -> dict:
    return make_plan(days=[make_day("2026-06-01", day_index=0, attractions=attractions)])


def ctx_with(attractions: list[dict], ground_truth: dict | None) -> EvalContext:
    return EvalContext(
        request=make_request(travel_days=1),
        plan=one_day_plan(attractions),
        run={},
        ground_truth=ground_truth,
    )


# ===========================================================================
# normalize_name
# ===========================================================================


class TestNormalizeName:
    def test_去掉括号内容(self):
        assert normalize_name("故宫(午门)") == "故宫"
        assert normalize_name("故宫（午门）") == "故宫"

    def test_去掉空白(self):
        assert normalize_name("  故宫  ") == "故宫"
        assert normalize_name("故 宫") == "故宫"

    def test_没有括号时原样返回(self):
        assert normalize_name("故宫博物院") == "故宫博物院"

    def test_None和空串返回空串(self):
        assert normalize_name(None) == ""
        assert normalize_name("") == ""

    def test_括号不配对的不要乱切(self):
        """只有左右括号都在才剥。否则会把名字切坏。"""
        assert normalize_name("故宫(午门") == "故宫(午门"


# ===========================================================================
# name_similarity —— 命中判定的核心
# ===========================================================================


class TestNameSimilarity:
    def test_完全相同(self):
        assert name_similarity("故宫", "故宫") == 1.0

    def test_包含关系算命中(self):
        """**这条不能少。**

        光靠 difflib 的 ratio,"故宫"vs"故宫博物院"只有约 0.57,会被判成
        不匹配 —— 而它恰恰是最该匹配上的一对。这是配置里 0.75 那个阈值
        单独用会踩的坑。
        """
        assert name_similarity("故宫", "故宫博物院") == 1.0
        assert name_similarity("外滩", "外滩观光平台") == 1.0
        assert name_similarity("西湖", "西湖风景名胜区") == 1.0

    def test_包含关系与顺序无关(self):
        assert name_similarity("故宫", "故宫博物院") == name_similarity("故宫博物院", "故宫")

    def test_纯类别词不享受包含关系(self):
        """「公园」出现在「北海公园」里,但它们是两回事 ——
        前者是一类地方,后者是一个具体地点。

        注意这里**不是**要求返回 0,而是要求退回字面相似度(约 0.67),
        低于 0.75 的阈值,所以最终判不命中。
        """
        s = name_similarity("公园", "北海公园")
        assert s < cfg.POI_NAME_MATCH_RATIO, f"类别词被误判为命中: {s}"

    def test_剥掉括号后相同算命中(self):
        assert name_similarity("故宫(午门)", "故宫") == 1.0

    def test_完全不同的景点不命中(self):
        assert name_similarity("故宫", "颐和园") < cfg.POI_NAME_MATCH_RATIO

    def test_空值返回零(self):
        assert name_similarity(None, "故宫") == 0.0
        assert name_similarity("故宫", "") == 0.0
        assert name_similarity("", "") == 0.0

    def test_相似度在0到1之间(self):
        for a, b in [("故宫", "颐和园"), ("天安门", "天安门广场"), ("外滩", "外滩源")]:
            assert 0.0 <= name_similarity(a, b) <= 1.0


# ===========================================================================
# best_match
# ===========================================================================


class TestBestMatch:
    def test_找到命中的那个(self):
        pois = make_gt()["pois"]
        poi, score = best_match("故宫", pois)
        assert poi is not None
        assert poi["name"] == "故宫博物院"
        assert score == 1.0

    def test_取分最高的而不是第一个(self):
        pois = [
            {"name": "北京故宫文创店", "location": [1.0, 1.0]},
            {"name": "故宫博物院", "location": [2.0, 2.0]},
        ]
        poi, _ = best_match("故宫博物院", pois)
        assert poi["name"] == "故宫博物院"

    def test_不命中时返回None但给出相似度(self):
        """相似度要一起返回 —— 报告里能看出是「差一点」还是「完全不沾边」。"""
        poi, score = best_match("火星度假村", make_gt()["pois"])
        assert poi is None
        assert 0.0 <= score < cfg.POI_NAME_MATCH_RATIO

    def test_有点像但不够像时必须判不命中(self):
        """**阈值本身要被测到。**

        「天坛南门」和库存里的「天坛公园」共享"天坛"两个字,相似度约 0.5 ——
        有点像,但不是同一个地方,必须判不命中。

        这条是变异测试逼出来的:原先只有"火星度假村"那种完全不沾边的用例,
        它和库存里每个名字的相似度都恰好是 0.0,于是"把阈值判定整个删掉"
        这个改动观察不到 —— 删了它测试照样全绿。加这条之后才真的被约束住。
        """
        poi, score = best_match("天坛南门", make_gt()["pois"])
        assert 0.0 < score < cfg.POI_NAME_MATCH_RATIO, f"这条例子的相似度应落在两者之间,实际 {score}"
        assert poi is None, "有点像但不够像,不该判命中"

    def test_空库存返回None和零(self):
        poi, score = best_match("故宫", [])
        assert poi is None
        assert score == 0.0


# ===========================================================================
# m_poi_support_rate —— 测「编造率」的那条
# ===========================================================================


class TestPoiSupportRate:
    def test_名字全都有据可查(self):
        attrs = [make_attraction("故宫"), make_attraction("天坛公园"), make_attraction("颐和园")]
        m = m_poi_support_rate(ctx_with(attrs, make_gt()))
        assert m.value == 1.0
        assert m.ok is True
        assert "3/3" in m.detail

    def test_编造的名字被抓到(self):
        """`_create_fallback_plan()` 造的就是这种名字 —— 查无此物。"""
        attrs = [make_attraction("北京景点1"), make_attraction("北京景点2")]
        m = m_poi_support_rate(ctx_with(attrs, make_gt()))
        assert m.value == 0.0
        assert m.ok is False
        assert "北京景点1" in m.detail

    def test_真假混杂返回比例(self):
        attrs = [
            make_attraction("故宫"),
            make_attraction("天坛公园"),
            make_attraction("北京景点3"),
            make_attraction("北京景点4"),
        ]
        m = m_poi_support_rate(ctx_with(attrs, make_gt()))
        assert m.value == pytest.approx(0.5)
        assert m.ok is False
        assert "2/4" in m.detail

    def test_别名也算命中(self):
        """"故宫"和库存里的"故宫博物院"是同一个地方,不能算编造。"""
        m = m_poi_support_rate(ctx_with([make_attraction("故宫")], make_gt()))
        assert m.value == 1.0

    def test_没有库存时不可计算(self):
        """**没录库存 ≠ 全是编的。** 返回 None/None,不能返回 0/False ——
        否则第一次跑 harness 会看到"编造率 100%"这种假警报。"""
        m = m_poi_support_rate(ctx_with([make_attraction("故宫")], None))
        assert m.value is None
        assert m.ok is None
        assert "没有该城市的冻结 POI 库存" in m.detail

    def test_库存是空列表时同样不可计算(self):
        m = m_poi_support_rate(ctx_with([make_attraction("故宫")], {"city": "北京", "pois": []}))
        assert m.value is None
        assert m.ok is None

    def test_没有景点时不通过但不崩(self):
        m = m_poi_support_rate(ctx_with([], make_gt()))
        assert m.value is None
        assert m.ok is False

    def test_没有名字的景点算未匹配(self):
        attrs = [make_attraction("故宫"), make_attraction(None)]
        m = m_poi_support_rate(ctx_with(attrs, make_gt()))
        assert m.value == pytest.approx(0.5)

    def test_查无此物的示例最多列5个(self):
        """详情是一行文本,列太多会把报告撑爆。"""
        attrs = [make_attraction(f"假景点{i}") for i in range(9)]
        m = m_poi_support_rate(ctx_with(attrs, make_gt()))
        assert m.detail.count("假景点") == 5

    def test_详情里必须带上这是下限的提醒(self):
        """读报告的人必须知道:没匹配上不等于编造。"""
        m = m_poi_support_rate(ctx_with([make_attraction("故宫")], make_gt()))
        assert "下限" in m.detail


# ===========================================================================
# m_coord_mae_km —— 抓「名字对了但位置是编的」
# ===========================================================================


class TestCoordMaeKm:
    def test_坐标准确时偏差为零(self):
        attrs = [make_attraction(n, lon, lat) for n, (lon, lat) in REAL.items()]
        m = m_coord_mae_km(ctx_with(attrs, make_gt()))
        assert m.value == pytest.approx(0.0, abs=0.01)
        assert m.ok is True

    def test_坐标是编的但仍在城市内(self):
        """**这是 coord_coverage 抓不到、只有本指标能抓的情况。**

        把真实坐标整体平移约 7.5 km —— 仍然落在北京市范围内,
        coord_coverage 会给满分,而这里必须报出来。
        """
        attrs = [make_attraction(n, lon + 0.06, lat + 0.05) for n, (lon, lat) in REAL.items()]
        m = m_coord_mae_km(ctx_with(attrs, make_gt()))
        assert m.value is not None
        assert m.value > cfg.COORD_MAE_KM_MAX
        assert m.ok is False
        assert "超过阈值" in m.detail

    def test_样本不足时不作判定(self):
        """1~2 个样本算出来的平均值不足以判通过/不通过 ——
        返回 ok=None(不知道),而不是硬判。"""
        assert cfg.MIN_MATCHES_FOR_MAE >= 2
        attrs = [make_attraction("故宫", 116.397, 39.918)]
        m = m_coord_mae_km(ctx_with(attrs, make_gt()))
        assert m.value is not None        # 值照给
        assert m.ok is None               # 但不判定
        assert "不作判定" in m.detail

    def test_样本够了才判定(self):
        attrs = [make_attraction(n, lon, lat) for n, (lon, lat) in REAL.items()]
        assert len(attrs) >= cfg.MIN_MATCHES_FOR_MAE
        assert m_coord_mae_km(ctx_with(attrs, make_gt())).ok is not None

    def test_一个都没匹配上时算不出(self):
        m = m_coord_mae_km(ctx_with([make_attraction("北京景点1")], make_gt()))
        assert m.value is None
        assert m.ok is None
        assert "算不出偏差" in m.detail

    def test_没有库存时不可计算(self):
        m = m_coord_mae_km(ctx_with([make_attraction("故宫")], None))
        assert m.value is None
        assert m.ok is None

    def test_行程里景点没坐标时跳过(self):
        attrs = [
            make_attraction("故宫", lon=None),          # 没坐标,跳过
            make_attraction("天坛公园", 116.410, 39.882),
        ]
        m = m_coord_mae_km(ctx_with(attrs, make_gt()))
        assert m.value == pytest.approx(0.0, abs=0.01)
        assert "1 个景点匹配上" in m.detail

    def test_库存里没有坐标时跳过(self):
        gt = {"city": "北京", "pois": [{"id": "B1", "name": "故宫", "location": None}]}
        m = m_coord_mae_km(ctx_with([make_attraction("故宫")], gt))
        assert m.value is None

    def test_详情里给出最远的那一个(self):
        """平均偏差会被多数准确的点稀释掉,最远的那条往往才是问题所在。"""
        attrs = [
            make_attraction("故宫", 116.397, 39.918),
            make_attraction("天坛公园", 116.410, 39.882),
            make_attraction("颐和园", 116.275 + 0.2, 39.999),   # ← 明显偏了
        ]
        m = m_coord_mae_km(ctx_with(attrs, make_gt()))
        assert "最远" in m.detail
        assert "颐和园" in m.detail


# ===========================================================================
# 库存的读写
# ===========================================================================


class TestInventory:
    def test_文件不存在时返回空结构而不抛异常(self, tmp_path):
        """第一次跑 harness 时库存还没录 —— 应该得到一份
        "接地性指标都不可计算"的报告,而不是崩溃。"""
        data = load_inventory(tmp_path / "不存在.json")
        assert data["cities"] == {}
        assert data["version"] is None

    def test_文件内容不是合法JSON时不抛异常(self, tmp_path):
        p = tmp_path / "坏.json"
        p.write_text("{不是 json", encoding="utf-8")
        assert load_inventory(p)["cities"] == {}

    def test_正常读取(self, tmp_path):
        p = tmp_path / "inv.json"
        p.write_text(
            json.dumps({"version": "1.0", "cities": {"北京": {"pois": [{"name": "故宫"}]}}},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        data = load_inventory(p)
        assert data["version"] == "1.0"
        assert "北京" in data["cities"]

    def test_取出某个城市(self):
        inv = {"cities": {"北京": {"pois": [{"name": "故宫"}]}}}
        gt = ground_truth_for("北京", inv)
        assert gt is not None
        assert gt["city"] == "北京"
        assert len(gt["pois"]) == 1

    def test_城市不在库存里返回None(self):
        inv = {"cities": {"北京": {"pois": [{"name": "故宫"}]}}}
        assert ground_truth_for("火星", inv) is None

    def test_库存结构不对时返回None(self):
        assert ground_truth_for("北京", None) is None
        assert ground_truth_for("北京", {}) is None
        assert ground_truth_for("北京", {"cities": {"北京": {}}}) is None
        assert ground_truth_for("北京", {"cities": {"北京": {"pois": []}}}) is None


# ===========================================================================
# 别名匹配 + 同分取短
# ===========================================================================

# 用的都是真实数据(从高德的 alias 字段录下来的)
#
# ⚠️ 顺序是**故意**排的:容易匹配错的候选(终南山的钟楼、西湖里的郭宅)
# 放在正确候选的**前面**。这样"取第一个匹配上的"这种错实现会失败 ——
# 否则测试只能证明"顺序碰巧对了",证明不了同分取短的逻辑真的起作用。
ALIAS_POIS = [
    {"id": "1", "name": "秦始皇帝陵博物院", "alias": "西安兵马俑|秦始皇兵马俑博物馆",
     "location": [109.27, 34.38]},
    {"id": "2", "name": "华清宫", "alias": "华清池", "location": [109.22, 34.36]},
    {"id": "4", "name": "终南山古楼观历史文化景区钟楼", "alias": None, "location": [108.35, 34.09]},
    {"id": "3", "name": "西安钟楼", "alias": "钟楼", "location": [108.94, 34.26]},
    {"id": "6", "name": "杭州西湖风景名胜区-郭宅", "alias": "郭宅", "location": [120.15, 30.25]},
    {"id": "5", "name": "杭州西湖风景名胜区", "alias": "西湖景区", "location": [120.14, 30.24]},
]


class TestCandidateNames:
    def test_正式名加别名(self):
        names = candidate_names({"name": "华清宫", "alias": "华清池"})
        assert names == ["华清宫", "华清池"]

    def test_多个别名按竖线拆开(self):
        names = candidate_names({"name": "秦始皇帝陵博物院", "alias": "西安兵马俑|秦始皇兵马俑博物馆"})
        assert names == ["秦始皇帝陵博物院", "西安兵马俑", "秦始皇兵马俑博物馆"]

    def test_没有别名时只有正式名(self):
        assert candidate_names({"name": "颐和园", "alias": None}) == ["颐和园"]
        assert candidate_names({"name": "颐和园"}) == ["颐和园"]
        assert candidate_names({"name": "颐和园", "alias": ""}) == ["颐和园"]

    def test_会做归一化(self):
        """括号内容和空白要在这一步就剥掉,后面比较才一致。"""
        assert candidate_names({"name": "故宫(午门)", "alias": " 紫禁城 "}) == ["故宫", "紫禁城"]


class TestBestMatchWithAlias:
    """别名匹配 —— 修的是「俗称 vs 官方名」对不上的假警报。

    这类错误最要命:行程里写"兵马俑"是完全正确的,库存里也有它,
    但因为官方名叫「秦始皇帝陵博物院」而判成"查无此物"。
    **假警报比指标偏低更糟**,它会让人以为模型在编造。
    """

    def test_俗称能匹配到官方名(self):
        poi, score = best_match("兵马俑", ALIAS_POIS)
        assert poi is not None, "「兵马俑」应该能通过别名 西安兵马俑 匹配上"
        assert poi["name"] == "秦始皇帝陵博物院"
        assert score == 1.0

    def test_另一个俗称(self):
        poi, _ = best_match("华清池", ALIAS_POIS)
        assert poi is not None and poi["name"] == "华清宫"

    def test_官方名同样能匹配(self):
        poi, _ = best_match("秦始皇帝陵博物院", ALIAS_POIS)
        assert poi is not None and poi["id"] == "1"

    def test_用别名自己的名字也能匹配(self):
        poi, _ = best_match("郭宅", ALIAS_POIS)
        assert poi is not None and poi["id"] == "6"


class TestShortestNameWins:
    """同分时取名字更短的那个 —— 修的是「匹配到错误实体」。

    两个候选都是包含关系、都算 1.0,但只有名字短的那个才是
    用户说的那个地方。取错的后果很严重:会拿错误实体的坐标去比,
    差出好几公里,然后**诬告坐标是编的**。
    """

    def test_钟楼不该匹配到终南山里的那个(self):
        poi, _ = best_match("钟楼", ALIAS_POIS)
        assert poi is not None
        assert poi["name"] == "西安钟楼", f"匹配到了 {poi['name']} —— 那不是市区的钟楼"

    def test_西湖不该匹配到景区里的一栋宅子(self):
        """「西湖」同时包含在「杭州西湖风景名胜区」和「...-郭宅」里。
        取短的那个才是景区本身。"""
        poi, _ = best_match("西湖", ALIAS_POIS)
        assert poi is not None
        assert poi["name"] == "杭州西湖风景名胜区", f"匹配到了 {poi['name']}"

    def test_坐标准确性受影响(self):
        """把后果测出来:匹配错了,算出来的坐标偏差就会很大。"""
        from app.eval.geo import haversine_km

        poi, _ = best_match("西湖", ALIAS_POIS)
        plan_lon, plan_lat = 120.14, 30.24      # 西湖的真实位置
        got_lon, got_lat = poi["location"]
        assert haversine_km(plan_lon, plan_lat, got_lon, got_lat) < 1.0


class TestPoiSupportRateWithAlias:
    def test_只有俗称的行程_不该被判成编造(self):
        """这是端到端的验证:一份全用俗称写的行程,应该拿到满分。"""
        attrs = [make_attraction("兵马俑"), make_attraction("华清池"), make_attraction("钟楼")]
        gt = {"city": "西安", "pois": ALIAS_POIS}
        m = m_poi_support_rate(ctx_with(attrs, gt))
        assert m.value == 1.0, f"被误判了,详情: {m.detail}"

    def test_编造的名字仍然会被抓(self):
        """别名不能松到把假名字也放行。"""
        attrs = [make_attraction("兵马俑"), make_attraction("西安景点1")]
        gt = {"city": "西安", "pois": ALIAS_POIS}
        m = m_poi_support_rate(ctx_with(attrs, gt))
        assert m.value == pytest.approx(0.5)


# ===========================================================================
# 第二个 oracle:拿名字去高德搜一次
# ===========================================================================


def make_check(pairs: dict[str, float]) -> dict:
    """造一份逐名核对缓存。pairs: {景点名: 最高相似度}。"""
    return {
        f"北京|{normalize_name(name)}": {
            "query": normalize_name(name),
            "city": "北京",
            "best_similarity": sim,
            "best_name": name if sim >= 0.75 else "某个不相干的POI",
        }
        for name, sim in pairs.items()
    }


def ctx_with_check(attractions: list[dict], check: dict | None) -> EvalContext:
    gt = make_gt()
    if check is not None:
        gt["name_check"] = check
    return ctx_with(attractions, gt)


class TestCheckedVerdict:
    """判定只在这一处做 —— 别处不要再各自比较相似度。"""

    def test_够像就是存在(self):
        assert checked_verdict({"best_similarity": 1.0}) == "found"
        assert checked_verdict({"best_similarity": cfg.POI_NAME_MATCH_RATIO}) == "found"

    def test_有点像算存疑不算存在(self):
        """名字可能只是拼得太长(「太古里·春熙路商圈」),不直接判为编造。"""
        assert checked_verdict({"best_similarity": 0.6}) == "weak"

    def test_搜出来全不相干才是编造(self):
        assert checked_verdict({"best_similarity": 0.1}) == "missing"

    def test_没查过是不知道而不是不存在(self):
        """三态设计:把「没查过」读成「是编的」就是诬告。"""
        assert checked_verdict(None) == "unknown"
        assert checked_verdict({}) == "unknown"
        assert checked_verdict({"best_similarity": None}) == "unknown"

    def test_阈值可以覆盖(self):
        assert checked_verdict({"best_similarity": 0.6}, threshold=0.5) == "found"


class TestNameCheckFor:
    def test_按城市和归一化名字查(self):
        check = make_check({"故宫博物院": 1.0})
        assert name_check_for("北京", "故宫博物院", check) is not None

    def test_括号里的限定语不影响查找(self):
        """和字符串匹配用同一套归一化,两个指标比的才是同一个东西。"""
        check = make_check({"曲院风荷": 1.0})
        assert name_check_for("北京", "曲院风荷（荷花区）", check) is not None

    def test_别的城市查不到(self):
        check = make_check({"故宫博物院": 1.0})
        assert name_check_for("上海", "故宫博物院", check) is None


class TestLoadNameCheck:
    def test_文件不存在返回空(self, tmp_path):
        assert load_name_check(tmp_path / "nope.json") == {}

    def test_坏JSON返回空而不是抛(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{坏", encoding="utf-8")
        assert load_name_check(p) == {}

    def test_正常读出来(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text(json.dumps({"entries": {"北京|外滩": {"best_similarity": 1.0}}}),
                     encoding="utf-8")
        assert "北京|外滩" in load_name_check(p)


class TestPoiExistsRate:
    def test_全真实就是满分(self):
        attrs = [make_attraction("故宫博物院"), make_attraction("天坛公园")]
        m = m_poi_exists_rate(ctx_with_check(attrs, make_check({"故宫博物院": 1.0, "天坛公园": 1.0})))
        assert m.value == 1.0
        assert m.ok is True

    def test_编造的名字会被抓(self):
        """这是这条指标存在的理由。"""
        attrs = [make_attraction("故宫博物院"), make_attraction("紫金幻梦星际主题乐园")]
        m = m_poi_exists_rate(ctx_with_check(
            attrs, make_check({"故宫博物院": 1.0, "紫金幻梦星际主题乐园": 0.14})))
        assert m.value == pytest.approx(0.5)
        assert m.ok is False
        assert "紫金幻梦星际主题乐园" in m.detail, "编造的名字要出现在详情里"

    def test_存疑的算不进分子(self):
        """「有点像」不是「存在」,不能拉高指标。"""
        attrs = [make_attraction("故宫博物院"), make_attraction("北京环球影城主题公园")]
        m = m_poi_exists_rate(ctx_with_check(
            attrs, make_check({"故宫博物院": 1.0, "北京环球影城主题公园": 0.57})))
        assert m.value == pytest.approx(0.5)
        assert "存疑" in m.detail

    def test_没查过的名字不进分母(self):
        """「没查过」和「不存在」必须分开 —— 否则补缓存前后数字会自己变。"""
        attrs = [make_attraction("故宫博物院"), make_attraction("某个没查过的")]
        m = m_poi_exists_rate(ctx_with_check(attrs, make_check({"故宫博物院": 1.0})))
        assert m.value == 1.0, "只有 1 个有判定,应该是 1/1 而不是 1/2"
        assert "没查过" in m.detail

    def test_没有缓存时返回算不出而不是不合格(self):
        gt = make_gt()  # 没有 name_check 这一层
        m = m_poi_exists_rate(ctx_with([make_attraction("故宫博物院")], gt))
        assert m.value is None
        assert m.ok is None, "没数据是不作判定,不是不合格"
        assert "check_poi_names" in m.detail, "要告诉人下一步该干嘛"

    def test_行程里没景点(self):
        m = m_poi_exists_rate(ctx_with([], make_gt()))
        assert m.ok is False


class TestGroundTruthForWithCheck:
    def test_两个_oracle_都塞进去(self):
        inv = {"cities": {"北京": {"pois": [{"name": "故宫博物院", "location": [116.4, 39.9]}]}}}
        check = {"北京|故宫博物院": {"best_similarity": 1.0},
                 "上海|外滩": {"best_similarity": 1.0}}
        gt = ground_truth_for("北京", inv, check)
        assert gt["pois"]
        assert set(gt["name_check"]) == {"北京|故宫博物院"}, "只该切出本城市的"

    def test_没有核对缓存时只有_pois(self):
        inv = {"cities": {"北京": {"pois": [{"name": "故宫博物院", "location": [116.4, 39.9]}]}}}
        gt = ground_truth_for("北京", inv, {})
        assert "name_check" not in gt
