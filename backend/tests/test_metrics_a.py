"""metrics.py 的 A 类指标 —— 自洽性(只看行程自身,不需要外部数据)

测试的写法有个共同套路:**先证明「健康的行程能通过」,再只改坏一处,
证明这一处被抓住了。**

只写「坏数据会被抓」是不够的 —— 一个永远返回 `ok=False` 的实现也能通过
那种测试。反过来只写「好数据能通过」也不够 —— 一个永远返回 `ok=True`
的实现同样能通过。两边都有,才说明指标真的在区分。
"""

from __future__ import annotations

import pytest

from app.eval import config as cfg
from app.eval.metrics import (
    EvalContext,
    m_attraction_count_ok,
    m_budget_arithmetic_ok,
    m_date_continuity_ok,
    m_day_count_ok,
    m_fallback_used,
    m_meal_slots_ok,
    m_schema_valid,
)
from tests.conftest import DEFAULT_CITY, DEFAULT_START, make_attraction, make_day, make_meal, make_plan, make_request


# ===========================================================================
# m_fallback_used —— 最重要的一条
# ===========================================================================


class TestFallbackUsed:
    def test_未降级(self):
        m = m_fallback_used(EvalContext(request={}, plan=None, run={"fallback_used": False}))
        assert m.value == 0.0
        assert m.ok is True
        assert "未降级" in m.detail

    def test_已降级(self):
        run = {"fallback_used": True, "warnings": ["LLM 输出解析失败"]}
        m = m_fallback_used(EvalContext(request={}, plan=None, run=run))
        assert m.value == 1.0
        assert m.ok is False
        assert "LLM 输出解析失败" in m.detail

    def test_降级但没记原因也要表明(self):
        """warnings 没落库时不能显示成空白,否则报告上会以为是「正常」。"""
        m = m_fallback_used(EvalContext(request={}, plan=None, run={"fallback_used": True}))
        assert m.value == 1.0
        assert "原因未记录" in m.detail

    def test_run为空时视为未降级(self):
        """run 缺失是「没记录」,不是「降级了」。这里不能倒过来判,
        否则一条没跑完的记录会被算成失败。"""
        m = m_fallback_used(EvalContext(request={}, plan=None, run={}))
        assert m.value == 0.0
        assert m.ok is True


# ===========================================================================
# m_schema_valid
# ===========================================================================


class TestSchemaValid:
    def test_健康行程通过(self):
        ctx = EvalContext(request=make_request(), plan=make_plan(), run={})
        m = m_schema_valid(ctx)
        assert m.value == 1.0
        assert m.ok is True

    def test_没有行程时不可计算(self):
        """plan=None 是「拿不到结果」,不是「结果不合格」——
        两者在报告里要分开,所以 value 是 None 而不是 0。"""
        m = m_schema_valid(EvalContext(request=make_request(), plan=None, run={}))
        assert m.value is None
        assert m.ok is False
        assert "解析失败" in m.detail

    def test_缺必填字段判失败(self):
        plan = make_plan()
        del plan["overall_suggestions"]
        m = m_schema_valid(EvalContext(request=make_request(), plan=plan, run={}))
        assert m.value == 0.0
        assert m.ok is False

    def test_景点缺坐标判失败(self):
        """location 在 Attraction 里是必填。这条同时说明:一份「坐标全丢了」
        的行程连数据契约都不满足,不只是「坐标不准」。"""
        days = [make_day(DEFAULT_START, attractions=[make_attraction("无坐标景点", lon=None)])]
        plan = make_plan(days=days)
        m = m_schema_valid(EvalContext(request=make_request(travel_days=1), plan=plan, run={}))
        assert m.value == 0.0
        assert m.ok is False

    def test_结构合法但运行记录标记失败也要标出来(self):
        """交叉验证:结构没问题,但这次运行本身出过错。只看结构会漏掉。"""
        run = {"ok": False, "error": "MCP 工具不可用"}
        m = m_schema_valid(EvalContext(request=make_request(), plan=make_plan(), run=run))
        assert m.value == 1.0
        assert m.ok is False
        assert "MCP 工具不可用" in m.detail

    def test_走了降级要在detail里提醒(self):
        """降级行程是照着模型现造的,**必然**通过校验。
        所以这里 value 仍是 1.0(结构确实合法),但必须在 detail 里
        说清「这不是 LLM 的原始输出」,否则读报告的人会被误导。"""
        run = {"fallback_used": True}
        m = m_schema_valid(EvalContext(request=make_request(), plan=make_plan(), run=run))
        assert m.value == 1.0
        assert m.ok is True
        assert "降级" in m.detail

    def test_结构不合法优先于运行记录(self):
        plan = make_plan()
        del plan["days"]
        run = {"ok": False, "error": "x"}
        m = m_schema_valid(EvalContext(request=make_request(), plan=plan, run=run))
        assert m.value == 0.0
        assert "不符合 TripPlan 契约" in m.detail

    def test_没有预算的行程也算合法(self):
        """budget 在 TripPlan 里是 Optional —— 拿不到预算不该导致整份行程
        被判为「结构不合法」。"""
        plan = make_plan(budget=None)
        plan["budget"] = None
        m = m_schema_valid(EvalContext(request=make_request(), plan=plan, run={}))
        assert m.ok is True


# ===========================================================================
# m_day_count_ok
# ===========================================================================


class TestDayCountOk:
    def test_天数吻合(self):
        ctx = EvalContext(request=make_request(travel_days=3), plan=make_plan(), run={})
        m = m_day_count_ok(ctx)
        assert m.value == 1.0
        assert m.ok is True
        assert "请求 3 天,返回 3 天" in m.detail

    def test_少给了天数(self):
        """请求 5 天只排 3 天 —— 用户按 5 天定的假期,这是实打实的错。"""
        ctx = EvalContext(request=make_request(travel_days=5), plan=make_plan(), run={})
        m = m_day_count_ok(ctx)
        assert m.value == 0.0
        assert m.ok is False
        assert "请求 5 天,返回 3 天" in m.detail

    def test_多给了天数也算错(self):
        days = [
            make_day(f"2026-06-0{i}", day_index=i - 1) for i in range(1, 5)
        ]
        ctx = EvalContext(request=make_request(travel_days=2), plan=make_plan(days=days), run={})
        assert m_day_count_ok(ctx).ok is False

    def test_没有天数时不可计算(self):
        ctx = EvalContext(request=make_request(), plan=make_plan(days=[]), run={})
        m = m_day_count_ok(ctx)
        assert m.value is None
        assert m.ok is False


# ===========================================================================
# m_date_continuity_ok
# ===========================================================================


class TestDateContinuityOk:
    def test_连续三天(self):
        ctx = EvalContext(request=make_request(travel_days=3), plan=make_plan(), run={})
        m = m_date_continuity_ok(ctx)
        assert m.value == 1.0
        assert m.ok is True
        assert "连续 3 天" in m.detail

    def test_起始日期与请求不符(self):
        plan = make_plan(start_date="2026-06-02")
        ctx = EvalContext(request=make_request(start_date="2026-06-01"), plan=plan, run={})
        m = m_date_continuity_ok(ctx)
        assert m.value == 0.0
        assert m.ok is False

    def test_中间跳了一天(self):
        """LLM 排着排着丢一天。天数可能仍然对,但日期断了。"""
        days = [
            make_day("2026-06-01", day_index=0),
            make_day("2026-06-03", day_index=1),   # ← 06-02 被跳过
            make_day("2026-06-04", day_index=2),
        ]
        ctx = EvalContext(request=make_request(), plan=make_plan(days=days), run={})
        m = m_date_continuity_ok(ctx)
        assert m.value == 0.0
        assert m.ok is False
        assert "第 1 天起不连续" in m.detail
        assert "2026-06-02" in m.detail

    def test_日期无法解析(self):
        days = [make_day("六月一号", day_index=0)]
        ctx = EvalContext(request=make_request(), plan=make_plan(days=days), run={})
        m = m_date_continuity_ok(ctx)
        assert m.value == 0.0
        assert m.ok is False
        assert "无法解析" in m.detail

    def test_请求里没有合法起始日期时不可计算(self):
        """start_date 不合法时不能判「不连续」—— 没有基准就无从谈起。
        这里 value/ok 都是 None,表示「不知道」。"""
        req = make_request()
        req["start_date"] = ""
        ctx = EvalContext(request=req, plan=make_plan(), run={})
        m = m_date_continuity_ok(ctx)
        assert m.value is None
        assert m.ok is None

    def test_结果日期与end_date不符仍通过但会提示(self):
        """连续性没问题就不该判失败 —— end_date 对不上往往是请求本身
        天数和区间自相矛盾。但要在 detail 里留下线索方便排查。"""
        req = make_request(start_date="2026-06-01", travel_days=3, end_date="2026-06-10")
        ctx = EvalContext(request=req, plan=make_plan(), run={})
        m = m_date_continuity_ok(ctx)
        assert m.ok is True
        assert "注意" in m.detail


# ===========================================================================
# m_meal_slots_ok / m_attraction_count_ok
# ===========================================================================


class TestMealSlotsOk:
    def test_每天都够三餐(self):
        ctx = EvalContext(request=make_request(), plan=make_plan(), run={})
        m = m_meal_slots_ok(ctx)
        assert m.value == 1.0
        assert m.ok is True
        assert "3/3 天" in m.detail

    def test_部分天不达标返回比例(self):
        """返回的是比例不是布尔 ——「9/10 天达标」和「3/10 天达标」
        差别很大,退化成 True/False 会丢掉这个信息。"""
        days = [
            make_day("2026-06-01", day_index=0),
            make_day("2026-06-02", day_index=1, meals=[make_meal("lunch", 50)]),  # ← 只有 1 餐
            make_day("2026-06-03", day_index=2),
        ]
        ctx = EvalContext(request=make_request(), plan=make_plan(days=days), run={})
        m = m_meal_slots_ok(ctx)
        assert m.value == pytest.approx(2 / 3)
        assert m.ok is False
        assert "2/3 天" in m.detail

    def test_一天都没排餐(self):
        days = [make_day("2026-06-01", day_index=0, meals=[])]
        ctx = EvalContext(request=make_request(travel_days=1), plan=make_plan(days=days), run={})
        assert m_meal_slots_ok(ctx).ok is False

    def test_没有天数时不可计算(self):
        ctx = EvalContext(request=make_request(), plan=make_plan(days=[]), run={})
        assert m_meal_slots_ok(ctx).value is None


class TestAttractionCountOk:
    def test_每天都有景点(self):
        ctx = EvalContext(request=make_request(), plan=make_plan(), run={})
        m = m_attraction_count_ok(ctx)
        assert m.value == 1.0
        assert m.ok is True

    def test_有一天空着(self):
        """这条是抓「某天什么都排出来」的 —— 会出现在 LLM 上下文快满时。"""
        days = [
            make_day("2026-06-01", day_index=0),
            make_day("2026-06-02", day_index=1, attractions=[]),  # ← 空的一天
            make_day("2026-06-03", day_index=2),
        ]
        ctx = EvalContext(request=make_request(), plan=make_plan(days=days), run={})
        m = m_attraction_count_ok(ctx)
        assert m.value == pytest.approx(2 / 3)
        assert m.ok is False

    def test_没有天数时不可计算(self):
        ctx = EvalContext(request=make_request(), plan=make_plan(days=[]), run={})
        assert m_attraction_count_ok(ctx).value is None


# ===========================================================================
# m_budget_arithmetic_ok
# ===========================================================================


def _single_day_plan(total_attractions: int, hotel_cost: int = 200) -> dict:
    """造一份只有 1 天的行程:两个景点(门票 60 + 40 = 100),酒店 200,三餐 150。

    `total` 会按四项之和算好,所以**只有「各项 vs 明细」这一层**可能出问题 ——
    测第二层时不会被第一层的报错干扰。
    """
    acts = [make_attraction("A", ticket_price=60), make_attraction("B", ticket_price=40)]
    days = [make_day("2026-06-01", day_index=0, attractions=acts, hotel_cost=hotel_cost)]
    budget = {
        "total_attractions": total_attractions,
        "total_hotels": hotel_cost,
        "total_meals": 150,
        "total_transportation": 100,
        "total": total_attractions + hotel_cost + 150 + 100,
    }
    return make_plan(days=days, budget=budget)


class TestBudgetArithmeticOk:
    def test_默认行程的预算自洽(self):
        ctx = EvalContext(request=make_request(), plan=make_plan(), run={})
        m = m_budget_arithmetic_ok(ctx)
        assert m.value == 1.0
        assert m.ok is True

    def test_总额与四项之和对不上(self):
        """LLM 的算术会漂移:每个数字单看都合理,加起来对不上,
        而用户不会去手算。"""
        plan = make_plan()
        plan["budget"]["total"] = 9999
        ctx = EvalContext(request=make_request(), plan=plan, run={})
        m = m_budget_arithmetic_ok(ctx)
        assert m.value == 0.0
        assert m.ok is False
        assert "总额" in m.detail

    def test_总额误差一元以内算通过(self):
        """容差是为了容纳取整,不是为了放过真问题。"""
        plan = make_plan()
        plan["budget"]["total"] += cfg.BUDGET_TOTAL_TOLERANCE_CNY
        ctx = EvalContext(request=make_request(), plan=plan, run={})
        assert m_budget_arithmetic_ok(ctx).ok is True

    def test_总额误差超过容差算失败(self):
        plan = make_plan()
        plan["budget"]["total"] += cfg.BUDGET_TOTAL_TOLERANCE_CNY + 1
        ctx = EvalContext(request=make_request(), plan=plan, run={})
        assert m_budget_arithmetic_ok(ctx).ok is False

    def test_门票与明细之差在容差内(self):
        """明细 Σ = 100,填 104 → 差 4%,在 5% 容差内。"""
        ctx = EvalContext(
            request=make_request(travel_days=1),
            plan=_single_day_plan(total_attractions=104),
            run={},
        )
        assert m_budget_arithmetic_ok(ctx).ok is True

    def test_门票与明细之差超出容差(self):
        """明细 Σ = 100,填 120 → 差 20%,明显对不上。"""
        ctx = EvalContext(
            request=make_request(travel_days=1),
            plan=_single_day_plan(total_attractions=120),
            run={},
        )
        m = m_budget_arithmetic_ok(ctx)
        assert m.value == 0.0
        assert m.ok is False
        assert "门票" in m.detail

    def test_明细为零时不判百分比(self):
        """分母是 0 时百分比没有意义。门票全是免费景点(Σ=0)却又填了
        一个数,这条**故意不判** —— 但总额那一层还是会查。"""
        acts = [make_attraction("免费公园", ticket_price=0)]
        days = [make_day("2026-06-01", day_index=0, attractions=acts, hotel_cost=200)]
        budget = {
            "total_attractions": 500,   # 明细是 0,无从比较
            "total_hotels": 200,
            "total_meals": 150,
            "total_transportation": 100,
            "total": 500 + 200 + 150 + 100,
        }
        plan = make_plan(days=days, budget=budget)
        ctx = EvalContext(request=make_request(travel_days=1), plan=plan, run={})
        assert m_budget_arithmetic_ok(ctx).ok is True

    def test_酒店与明细之差被抓到(self):
        acts = [make_attraction("A", ticket_price=100)]
        days = [make_day("2026-06-01", day_index=0, attractions=acts, hotel_cost=200)]
        budget = {
            "total_attractions": 100,
            "total_hotels": 900,        # ← 明细只有 200
            "total_meals": 150,
            "total_transportation": 100,
            "total": 100 + 900 + 150 + 100,
        }
        plan = make_plan(days=days, budget=budget)
        ctx = EvalContext(request=make_request(travel_days=1), plan=plan, run={})
        m = m_budget_arithmetic_ok(ctx)
        assert m.ok is False
        assert "酒店" in m.detail

    def test_没有预算字段判失败(self):
        plan = make_plan(budget=None)
        plan["budget"] = None
        ctx = EvalContext(request=make_request(), plan=plan, run={})
        m = m_budget_arithmetic_ok(ctx)
        assert m.value == 0.0
        assert m.ok is False
        assert "没有 budget 字段" in m.detail

    def test_预算里有非数值判失败(self):
        plan = make_plan()
        plan["budget"]["total"] = "大概一千块"
        ctx = EvalContext(request=make_request(), plan=plan, run={})
        m = m_budget_arithmetic_ok(ctx)
        assert m.value == 0.0
        assert m.ok is False
        assert "非数值" in m.detail

    def test_交通费不参与明细比对(self):
        """transportation 只是个字符串("公共交通"),没有单价可累加。
        所以交通费只参与总额那一层 —— 这条测试把这个约定固定下来,
        免得以后有人以为「交通那块漏查了」。"""
        plan = make_plan()
        plan["budget"]["total_transportation"] = 100
        # 明细那一层没有交通可比,只要总额仍然等于四项之和就通过
        plan["budget"]["total"] = (
            plan["budget"]["total_attractions"]
            + plan["budget"]["total_hotels"]
            + plan["budget"]["total_meals"]
            + 100
        )
        ctx = EvalContext(request=make_request(), plan=plan, run={})
        m = m_budget_arithmetic_ok(ctx)
        assert m.ok is True
        assert "交通费无明细可比" in m.detail


# ===========================================================================
# plan 缺失时所有 A 类指标都不该崩
# ===========================================================================


@pytest.mark.parametrize(
    "fn",
    [
        m_schema_valid,
        m_day_count_ok,
        m_date_continuity_ok,
        m_meal_slots_ok,
        m_attraction_count_ok,
        m_budget_arithmetic_ok,
    ],
)
def test_plan为None时不抛异常(fn):
    """plan=None 是真实会发生的(解析失败)。指标抛异常会被 evaluate()
    记成「计算失败」,但那是兜底,不该是常态。"""
    m = fn(EvalContext(request=make_request(), plan=None, run={}))
    assert m.value is None
