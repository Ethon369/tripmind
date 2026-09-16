"""metrics.py 的 B 类(接地性)/ C 类(成本)指标,以及 evaluate / summarize

B 类里 `m_coord_coverage` 是**本项目最重要的一条回归测试**:
`_create_fallback_plan()` 用 `116.4 + i*0.01`(北京)现算坐标,生成上海行程时
所有坐标都落在上海范围外。下面 `TestCoordCoverage.test_上海行程配北京坐标`
就是把那个 bug 钉住 —— 以后谁把它改回来,这条会立刻红。
"""

from __future__ import annotations

import pytest

from app.eval import config as cfg
from app.eval import metrics as M
from app.eval.geo import haversine_km
from app.eval.metrics import (
    EvalContext,
    Metric,
    evaluate,
    m_coord_coverage,
    m_cost_cny,
    m_intraday_travel_km_p95,
    m_latency_s,
    m_llm_calls,
    summarize,
)
from tests.conftest import DEFAULT_START, make_attraction, make_day, make_plan, make_request


# ===========================================================================
# m_coord_coverage —— 抓「整片坐标用错城市」
# ===========================================================================


class TestCoordCoverage:
    def test_坐标都在城市范围内(self):
        ctx = EvalContext(request=make_request(), plan=make_plan(), run={})
        m = m_coord_coverage(ctx)
        assert m.value == 1.0
        assert m.ok is True
        assert "3/3" in m.detail

    def test_上海行程配北京坐标(self):
        """**核心回归测试。**

        请求去上海,但坐标全是北京的(116.4, 39.9)——
        这正是 `_create_fallback_plan()` 的现有行为。
        指标必须掉到 0,而且 detail 里要能看出是哪些景点越界。
        """
        ctx = EvalContext(
            request=make_request(city="上海"),
            plan=make_plan(city="上海"),      # 默认景点坐标是北京
            run={},
        )
        m = m_coord_coverage(ctx)
        assert m.value == 0.0
        assert m.ok is False
        assert "上海" in m.detail
        assert "越界示例" in m.detail

    def test_部分越界返回比例(self):
        days = [
            make_day(
                DEFAULT_START,
                day_index=0,
                attractions=[
                    make_attraction("在城内", lon=116.4, lat=39.9),
                    make_attraction("跑到上海", lon=121.47, lat=31.23),
                ],
            )
        ]
        ctx = EvalContext(
            request=make_request(travel_days=1),
            plan=make_plan(days=days),
            run={},
        )
        m = m_coord_coverage(ctx)
        assert m.value == pytest.approx(0.5)
        assert m.ok is False

    def test_缺坐标算作不在范围内(self):
        """「没有坐标」和「坐标是错的」对用户是一回事:地图上标不出来。

        注意这份行程其实连 schema 都不合法(location 是必填),
        但指标之间互不依赖,这里只测 coord_coverage 自己的行为。
        """
        days = [
            make_day(
                DEFAULT_START,
                day_index=0,
                attractions=[
                    make_attraction("有坐标", lon=116.4, lat=39.9),
                    make_attraction("没坐标", lon=None),
                ],
            )
        ]
        ctx = EvalContext(
            request=make_request(travel_days=1),
            plan=make_plan(days=days),
            run={},
        )
        m = m_coord_coverage(ctx)
        assert m.value == pytest.approx(0.5)
        assert "1/2" in m.detail

    def test_未收录城市不可计算(self):
        """返回 None 而不是 0 —— 「我们没录这个城市」不等于
        「坐标全是假的」。报告里这两种情况的含义完全不同。"""
        ctx = EvalContext(
            request=make_request(city="火星"),
            plan=make_plan(city="火星"),
            run={},
        )
        m = m_coord_coverage(ctx)
        assert m.value is None
        assert m.ok is None
        assert "CITY_BBOX" in m.detail

    def test_边界上的坐标算在范围内(self):
        """和 geo.in_city 的闭区间约定保持一致。"""
        from app.eval.geo import CITY_BBOX

        min_lon, min_lat, _, _ = CITY_BBOX["北京"]
        days = [
            make_day(
                DEFAULT_START,
                day_index=0,
                attractions=[make_attraction("贴边", lon=min_lon, lat=min_lat)],
            )
        ]
        ctx = EvalContext(
            request=make_request(travel_days=1),
            plan=make_plan(days=days),
            run={},
        )
        assert m_coord_coverage(ctx).value == 1.0

    def test_没有景点时不可计算(self):
        days = [make_day(DEFAULT_START, day_index=0, attractions=[])]
        ctx = EvalContext(
            request=make_request(travel_days=1),
            plan=make_plan(days=days),
            run={},
        )
        assert m_coord_coverage(ctx).value is None


# ===========================================================================
# m_intraday_travel_km_p95
# ===========================================================================


class TestIntradayTravel:
    def test_单日两点路径长度(self):
        days = [
            make_day(
                DEFAULT_START,
                day_index=0,
                attractions=[
                    make_attraction("A", lon=116.4, lat=39.9),
                    make_attraction("B", lon=116.5, lat=39.9),
                ],
            )
        ]
        ctx = EvalContext(
            request=make_request(travel_days=1),
            plan=make_plan(days=days),
            run={},
        )
        m = m_intraday_travel_km_p95(ctx)
        expected = haversine_km(116.4, 39.9, 116.5, 39.9)
        assert m.value == pytest.approx(round(expected, 2))
        assert m.ok is None      # 参考值,不判定

    def test_只有单点的一天不计入(self):
        """一个点没有「移动距离」可言。若把它当 0 计入,会把分位数拉低,
        让「某天排得极赶」更难被发现。"""
        days = [
            make_day(
                DEFAULT_START,
                day_index=0,
                attractions=[make_attraction("孤零零", lon=116.4, lat=39.9)],
            )
        ]
        ctx = EvalContext(
            request=make_request(travel_days=1),
            plan=make_plan(days=days),
            run={},
        )
        m = m_intraday_travel_km_p95(ctx)
        assert m.value is None
        assert "没有可计算的单日路径" in m.detail

    def test_p95能暴露个别排得极赶的日子(self):
        """这是选 p95 而不是平均值的理由。

        10 天里 9 天都很松(约 0.85 km),第 10 天要跑约 42 km。
        平均值会被前 9 天稀释掉;p95 必须明显抬起来。
        """
        days = []
        for i in range(9):
            days.append(
                make_day(
                    f"2026-06-{i + 1:02d}",
                    day_index=i,
                    attractions=[
                        make_attraction("近1", lon=116.40, lat=39.90),
                        make_attraction("近2", lon=116.41, lat=39.90),  # 约 0.85 km
                    ],
                )
            )
        days.append(
            make_day(
                "2026-06-10",
                day_index=9,
                attractions=[
                    make_attraction("远1", lon=116.40, lat=39.90),
                    make_attraction("远2", lon=116.90, lat=39.90),      # 约 42 km
                ],
            )
        )
        ctx = EvalContext(
            request=make_request(travel_days=10),
            plan=make_plan(days=days),
            run={},
        )
        m = m_intraday_travel_km_p95(ctx)

        assert m.value > 20, f"p95 只算到 {m.value} km,没能反映那个 42 km 的极端值"
        assert "中位" in m.detail
        # detail 里同时给出中位数,读报告时能看出「中位很低但 p95 很高」= 有一天特别赶
        assert "最大" in m.detail

    def test_没有天数时不可计算(self):
        ctx = EvalContext(request=make_request(), plan=make_plan(days=[]), run={})
        assert m_intraday_travel_km_p95(ctx).value is None


# ===========================================================================
# C 类 · 成本(只报告,不判定)
# ===========================================================================


class TestCostMetrics:
    def test_耗时换算成秒(self):
        ctx = EvalContext(request={}, plan=None, run={"elapsed_ms": 137200})
        m = m_latency_s(ctx)
        assert m.value == pytest.approx(137.2)
        assert m.ok is None

    def test_没记耗时则不可计算(self):
        m = m_latency_s(EvalContext(request={}, plan=None, run={}))
        assert m.value is None
        assert m.ok is None

    def test_llm调用次数(self):
        ctx = EvalContext(request={}, plan=None, run={"usage": {"llm_calls": 7}})
        m = m_llm_calls(ctx)
        assert m.value == pytest.approx(7.0)
        assert m.ok is None

    def test_花费取自usage(self):
        run = {"usage": {"cost_cny": 0.1571, "usage_source": "api"}}
        m = m_cost_cny(EvalContext(request={}, plan=None, run=run))
        assert m.value == pytest.approx(0.1571)
        assert m.ok is None
        assert m.detail == ""      # API 实测,不需要额外说明

    def test_估算出来的花费要标注来源(self):
        """估算值和实测值混在一张表里会误导人 —— 报告里必须能区分。"""
        run = {"usage": {"cost_cny": 0.16, "usage_source": "estimated"}}
        m = m_cost_cny(EvalContext(request={}, plan=None, run=run))
        assert "估算" in m.detail
        assert "estimated" in m.detail

    def test_没有usage则不可计算(self):
        ctx = EvalContext(request={}, plan=None, run={})
        assert m_llm_calls(ctx).value is None
        assert m_cost_cny(ctx).value is None


# ===========================================================================
# evaluate()
# ===========================================================================


class TestEvaluate:
    def test_跑出全部指标(self):
        ctx = EvalContext(
            request=make_request(),
            plan=make_plan(),
            run={"fallback_used": False, "elapsed_ms": 1000, "usage": {"llm_calls": 7, "cost_cny": 0.1}},
        )
        results = evaluate(ctx)
        names = [m.name for m in results]
        # METRIC_ORDER 里的名字都该出现;将来 metrics_grounding 加进来会更多
        for expected in ("fallback_used", "schema_valid", "coord_coverage", "cost_cny"):
            assert expected in names, f"缺少指标 {expected}"

    def test_按METRIC_ORDER排序(self):
        ctx = EvalContext(request=make_request(), plan=make_plan(), run={})
        names = [m.name for m in evaluate(ctx)]
        order = {n: i for i, n in enumerate(cfg.METRIC_ORDER)}
        positions = [order[n] for n in names if n in order]
        assert positions == sorted(positions), f"顺序不对: {names}"

    def test_单个指标抛异常不影响其余(self, monkeypatch):
        """跑到一半因为一个指标崩溃就丢掉全部结果,代价太大。"""

        def m_boom(ctx):
            raise RuntimeError("故意炸的")

        monkeypatch.setattr(M, "ALL_METRICS", [m_boom, M.m_day_count_ok])
        results = evaluate(EvalContext(request=make_request(), plan=make_plan(), run={}))

        by_name = {m.name: m for m in results}
        assert len(results) == 2
        assert by_name["boom"].value is None
        assert "指标计算异常" in by_name["boom"].detail
        assert "RuntimeError" in by_name["boom"].detail
        # 另一个指标照常算出结果
        assert by_name["day_count_ok"].ok is True

    def test_指标名去掉m_前缀(self):
        ctx = EvalContext(request=make_request(), plan=make_plan(), run={})
        names = [m.name for m in evaluate(ctx)]
        assert not any(n.startswith("m_") for n in names)


# ===========================================================================
# summarize()
# ===========================================================================


class TestSummarize:
    def test_通过数与判定数分开统计(self):
        s = summarize([
            Metric("a", 1.0, True),
            Metric("b", 0.0, False),
            Metric("c", None, None),    # 不可计算
            Metric("d", 5.0, None),     # 参考值,不判定
        ])
        assert s["passed"] == 1
        assert s["judged"] == 2         # 只有 a 和 b 参与判定
        assert s["failed"] == ["b"]

    def test_不可计算要单独列出(self):
        """否则「3 项无法计算」会被读成「3 项不通过」。"""
        s = summarize([
            Metric("a", 1.0, True),
            Metric("x", None, None),
            Metric("y", None, False),
        ])
        assert s["uncomputable"] == ["x", "y"]
        # y 虽然不可计算,但被判为不通过(没拿到结果 ≠ 没问题),要出现在 failed 里
        assert s["failed"] == ["y"]

    def test_指标值字典齐全(self):
        s = summarize([Metric("a", 1.0, True), Metric("b", None, None)])
        assert s["metrics"] == {"a": 1.0, "b": None}

    def test_全通过(self):
        s = summarize([Metric("a", 1.0, True), Metric("b", 0.99, True)])
        assert s["passed"] == 2
        assert s["failed"] == []

    def test_空列表不炸(self):
        s = summarize([])
        assert s["passed"] == 0
        assert s["judged"] == 0
