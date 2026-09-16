"""评测指标 —— 每个指标一个纯函数

设计原则
--------
**1. 全部客观可复算,不让 LLM 给自己打分。**

让 LLM 评价"这个行程好不好"是循环论证:同一个模型既生成又评分,分数只会反映
它自己的偏好,而且换个模型分数就变,没法跨版本比较。所以这里的每条指标要么
从行程内部重算(算术、日期、结构),要么和外部真实数据比对(坐标、POI 名)。
**没有任何一条依赖模型判断。**

**2. 每个指标是纯函数,输入输出都不带副作用。**

签名统一为 `(ctx: EvalContext) -> Metric`。好处是可以单独测:构造一个
`EvalContext` 就能验证指标算得对不对,不用起服务、不用调 LLM。

**3. 三态而非两态。**

`Metric.value is None` 表示**无法计算**(比如城市没收录),不是"算出来是 0"。
`Metric.ok is None` 表示**不作判定**(成本类指标没有及格线)。
把"不知道"和"不合格"分开,报告才不会骗人。

指标分类
--------
- **A 类 · 自洽性**:只看行程自身。`m_day_count_ok` / `m_date_continuity_ok` /
  `m_meal_slots_ok` / `m_budget_arithmetic_ok` / `m_attraction_count_ok`
- **B 类 · 接地性**:要外部真实数据。`m_coord_coverage` 只需要城市范围;
  `poi_support_rate` / `coord_mae_km` 需要冻结的真实 POI,见 metrics_grounding.py
- **C 类 · 成本**:`m_latency_s` / `m_llm_calls` / `m_cost_cny`,只报告不判定
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from . import config as cfg
from .geo import haversine_km, in_city, path_length_km


# ===========================================================================
# 数据结构
# ===========================================================================


@dataclass(frozen=True)
class Metric:
    """一个指标的计算结果。

    Attributes:
        name: 指标名,与 config.METRIC_ORDER 里的字符串对应
        value: 数值。None = 无法计算(不是 0)
        ok: 是否通过。None = 不作判定(成本类)
        detail: 一行人类可读的说明,报告里直接显示
    """

    name: str
    value: float | None
    ok: bool | None
    detail: str = ""


@dataclass
class EvalContext:
    """跑一次评测所需的一切输入。

    Attributes:
        request: 原始请求(TripRequest 的 dict 形式)
        plan: 生成的行程(TripPlan 的 dict 形式)。**可能为 None** ——
              如果连解析都失败了。所有指标都必须能处理这种情况。
        run: `data/runs/*.jsonl` 里那条运行记录,含 fallback_used / usage / stages
        ground_truth: 冻结的真实 POI 数据,暂缺时为 None(B 类指标会返回"无法计算")
    """

    request: dict[str, Any]
    plan: dict[str, Any] | None
    run: dict[str, Any] = field(default_factory=dict)
    ground_truth: dict[str, Any] | None = None


# ===========================================================================
# 小工具
# ===========================================================================


def _days(ctx: EvalContext) -> list[dict[str, Any]]:
    """取 days 列表,plan 缺失或格式不对时返回空列表。"""
    if not ctx.plan:
        return []
    days = ctx.plan.get("days")
    return days if isinstance(days, list) else []


def _attractions(ctx: EvalContext) -> list[dict[str, Any]]:
    return [a for d in _days(ctx) for a in (d.get("attractions") or [])]


def _meals(ctx: EvalContext) -> list[dict[str, Any]]:
    return [m for d in _days(ctx) for m in (d.get("meals") or [])]


def _parse_date(s: Any) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _percentile(values: list[float], q: float) -> float | None:
    """线性插值的分位数。values 为空的返回 None。

    不用 statistics.quantiles:它要求至少两个数据点,而且插值方式在
    边界上不好控制。这里手写一个,行为明确。
    """
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def _missing_plan(name: str) -> Metric:
    """plan 为 None 时的统一返回。"""
    return Metric(name=name, value=None, ok=False, detail="没有可评测的行程(解析失败)")


# ===========================================================================
# A 类 · 自洽性
# ===========================================================================


def m_schema_valid(ctx: EvalContext) -> Metric:
    """最终返回的行程是否符合 TripPlan 数据契约。

    ⚠️ 读这个数要注意:如果这次运行**走了降级**(`fallback_used=True`),
    返回的是 `_create_fallback_plan()` 现造的行程,它是照着模型构造的,
    **必然通过校验** —— 这时 `schema_valid=True` 不代表 LLM 的输出合格。
    真正的"LLM 输出是否合格"要看 `fallback_used`。

    所以这里额外用 `run.ok` 作为交叉验证:`run.ok=False` 说明这次运行
    出过错,即使行程结构没问题也要标出来。
    """
    if not ctx.plan:
        return _missing_plan("schema_valid")

    # 用真实的 Pydantic 模型校验,而不是手写字段检查 ——
    # 手写的会随 schemas.py 的改动漂移,模型不会。
    from ..models.schemas import TripPlan

    try:
        TripPlan.model_validate(ctx.plan)
    except Exception as e:  # pydantic 的 ValidationError 等
        return Metric(
            name="schema_valid",
            value=0.0,
            ok=False,
            detail=f"不符合 TripPlan 契约: {type(e).__name__}",
        )

    run_ok = ctx.run.get("ok")
    if run_ok is False:
        return Metric(
            name="schema_valid",
            value=1.0,
            ok=False,
            detail=f"结构合法,但运行记录标记为失败(error={ctx.run.get('error')!r})",
        )

    if ctx.run.get("fallback_used"):
        return Metric(
            name="schema_valid",
            value=1.0,
            ok=True,
            detail="结构合法(注意:本次走了降级,该结果不是 LLM 原始输出)",
        )

    return Metric(name="schema_valid", value=1.0, ok=True, detail="结构合法")


def m_fallback_used(ctx: EvalContext) -> Metric:
    """是否走了降级方案。**这是最重要的一条。**

    降级意味着:要么 plan_trip 抛了异常,要么 LLM 的输出解析不出来,
    于是返回一份现造的兜底行程。这份兜底行程**看起来正常**(字段齐全、
    schema 校验通过),但里面的景点和坐标可能是编的 —— 用户看不出区别。

    所以 `fallback_used` 是"有多少比例的用户拿到了假数据"的直接度量。
    """
    used = bool(ctx.run.get("fallback_used"))
    warnings = ctx.run.get("warnings") or []
    detail = "未降级" if not used else f"已降级: {'; '.join(str(w) for w in warnings) or '原因未记录'}"
    return Metric(name="fallback_used", value=1.0 if used else 0.0, ok=not used, detail=detail)


def m_day_count_ok(ctx: EvalContext) -> Metric:
    """行程天数是否等于请求的天数。

    常见错误是少给:请求 5 天只排了 3 天。多给也算错(用户按天数定的假期)。
    """
    days = _days(ctx)
    if not days:
        return _missing_plan("day_count_ok")

    want = int(ctx.request.get("travel_days") or 0)
    got = len(days)
    return Metric(
        name="day_count_ok",
        value=1.0 if got == want else 0.0,
        ok=got == want,
        detail=f"请求 {want} 天,返回 {got} 天",
    )


def m_date_continuity_ok(ctx: EvalContext) -> Metric:
    """日期是否从 start_date 开始、逐日连续、且与 end_date 吻合。

    三种典型错误:
    - 起始日期不是请求里的 start_date
    - 中间跳了一天(LLM 排着排着丢了)
    - 天数对但结束日期和 end_date 对不上
    """
    days = _days(ctx)
    if not days:
        return _missing_plan("date_continuity_ok")

    start = _parse_date(ctx.request.get("start_date"))
    if start is None:
        return Metric(name="date_continuity_ok", value=None, ok=None, detail="请求里没有合法起始日期")

    actual = [_parse_date(d.get("date")) for d in days]
    if any(a is None for a in actual):
        bad = sum(1 for a in actual if a is None)
        return Metric(
            name="date_continuity_ok",
            value=0.0,
            ok=False,
            detail=f"有 {bad} 天的日期无法解析",
        )

    expected = [start + timedelta(days=i) for i in range(len(days))]
    if actual != expected:
        first_bad = next(
            (i for i, (a, e) in enumerate(zip(actual, expected)) if a != e),
            0,
        )
        return Metric(
            name="date_continuity_ok",
            value=0.0,
            ok=False,
            detail=(
                f"第 {first_bad} 天起不连续: 期望 {expected[first_bad]}, 实际 {actual[first_bad]}"
            ),
        )

    # 顺带看结束日期(不单独判定,但写进 detail 方便排查)
    end = _parse_date(ctx.request.get("end_date"))
    tail = "" if end in (None, actual[-1]) else f";注意 end_date={end} 与最后一天 {actual[-1]} 不符"
    return Metric(
        name="date_continuity_ok",
        value=1.0,
        ok=True,
        detail=f"{actual[0]} 起连续 {len(actual)} 天{tail}",
    )


def m_meal_slots_ok(ctx: EvalContext) -> Metric:
    """每天是否都排够了餐次。

    返回**达标天数的比例**,不是一个布尔值 —— 9/10 天达标和 3/10 天达标
    差别很大,退化成 True/False 会丢掉这个信息。
    """
    days = _days(ctx)
    if not days:
        return _missing_plan("meal_slots_ok")

    want = cfg.MIN_MEALS_PER_DAY
    ok_days = sum(1 for d in days if len(d.get("meals") or []) >= want)
    ratio = ok_days / len(days)
    return Metric(
        name="meal_slots_ok",
        value=ratio,
        ok=ok_days == len(days),
        detail=f"{ok_days}/{len(days)} 天达到 {want} 餐",
    )


def m_attraction_count_ok(ctx: EvalContext) -> Metric:
    """每天是否至少排了景点。同 `m_meal_slots_ok`,返回达标比例。"""
    days = _days(ctx)
    if not days:
        return _missing_plan("attraction_count_ok")

    want = cfg.MIN_ATTRACTIONS_PER_DAY
    ok_days = sum(1 for d in days if len(d.get("attractions") or []) >= want)
    ratio = ok_days / len(days)
    return Metric(
        name="attraction_count_ok",
        value=ratio,
        ok=ok_days == len(days),
        detail=f"{ok_days}/{len(days)} 天至少 {want} 个景点",
    )


def m_budget_arithmetic_ok(ctx: EvalContext) -> Metric:
    """预算算术是否自洽 —— 从行程自身重算,完全客观。

    查两层:
    1. `total` == 四项之和(±1 元,容纳取整误差)
    2. 各项 == 对应明细之和(±5%):门票 vs 各景点 `ticket_price`,
       餐饮 vs 各餐 `estimated_cost`,酒店 vs 各天 `hotel.estimated_cost`

    ⚠️ 交通费**没有明细可比** —— `transportation` 只是一个字符串
    ("公共交通"),没有单价可累加。所以 `total_transportation` 只参与第 1
    层检查,不参与第 2 层。报告里要说明这一点,否则会显得"交通那块没查"。

    这条指标的价值:LLM 的算术会漂移。数字各自看着合理,加起来对不上,
    而用户不会去手算 —— 只会觉得"这预算大概不准"。
    """
    plan = ctx.plan
    if not plan:
        return _missing_plan("budget_arithmetic_ok")

    budget = plan.get("budget")
    if not isinstance(budget, dict):
        return Metric(name="budget_arithmetic_ok", value=0.0, ok=False, detail="没有 budget 字段")

    problems: list[str] = []

    # ---- 第 1 层:总额 vs 四项之和 ----
    items = ["total_attractions", "total_hotels", "total_meals", "total_transportation"]
    try:
        item_sum = sum(int(budget.get(k) or 0) for k in items)
        total = int(budget.get("total") or 0)
    except (TypeError, ValueError):
        return Metric(name="budget_arithmetic_ok", value=0.0, ok=False, detail="budget 里有非数值字段")

    if abs(total - item_sum) > cfg.BUDGET_TOTAL_TOLERANCE_CNY:
        problems.append(f"总额 {total} ≠ 四项之和 {item_sum}")

    # ---- 第 2 层:各项 vs 明细 ----
    tol = cfg.BUDGET_DETAIL_TOLERANCE_PCT

    def _cmp(label: str, stated: int, detailed: int) -> None:
        """明细为 0 时跳过 —— 分母是 0,百分比没有意义。"""
        if detailed == 0:
            return
        diff_pct = abs(stated - detailed) / detailed * 100
        if diff_pct > tol:
            problems.append(f"{label} {stated} 与明细之和 {detailed} 差 {diff_pct:.1f}%")

    try:
        _cmp(
            "门票",
            int(budget.get("total_attractions") or 0),
            sum(int(a.get("ticket_price") or 0) for a in _attractions(ctx)),
        )
        _cmp(
            "餐饮",
            int(budget.get("total_meals") or 0),
            sum(int(m.get("estimated_cost") or 0) for m in _meals(ctx)),
        )
        _cmp(
            "酒店",
            int(budget.get("total_hotels") or 0),
            sum(int((d.get("hotel") or {}).get("estimated_cost") or 0) for d in _days(ctx)),
        )
    except (TypeError, ValueError):
        return Metric(name="budget_arithmetic_ok", value=0.0, ok=False, detail="明细里有非数值字段")

    if problems:
        return Metric(
            name="budget_arithmetic_ok",
            value=0.0,
            ok=False,
            detail="; ".join(problems),
        )

    return Metric(
        name="budget_arithmetic_ok",
        value=1.0,
        ok=True,
        detail=f"总额 {total} = 四项之和;各项与明细相符(交通费无明细可比)",
    )


# ===========================================================================
# B 类 · 接地性(不需要冻结数据的那部分)
# ===========================================================================


def m_coord_coverage(ctx: EvalContext) -> Metric:
    """坐标落在目标城市范围内的比例。

    **这条是抓"整片坐标用错城市"的。** 项目现在 `_create_fallback_plan()`
    里写死了 `116.4 + i*0.01`(北京),生成上海行程时所有坐标都会落到上海
    范围之外 —— 这条指标会直接掉到接近 0。

    局限(报告里要写):门槛低。坐标恰好落在正确城市范围内的假坐标抓不到,
    那要靠 `coord_mae_km`。详见 `geo.py`。
    """
    attractions = _attractions(ctx)
    if not attractions:
        return _missing_plan("coord_coverage")

    city = ctx.request.get("city") or ""
    inside = 0
    outside = 0
    unknown_city = False
    bad_examples: list[str] = []

    for a in attractions:
        loc = a.get("location") or {}
        lon, lat = loc.get("longitude"), loc.get("latitude")
        if lon is None or lat is None:
            outside += 1
            continue
        verdict = in_city(float(lon), float(lat), city)
        if verdict is None:
            unknown_city = True
            break
        if verdict:
            inside += 1
        else:
            outside += 1
            if len(bad_examples) < 3:
                bad_examples.append(f"{a.get('name')}({lon},{lat})")

    if unknown_city:
        return Metric(
            name="coord_coverage",
            value=None,
            ok=None,
            detail=f"城市「{city}」不在 geo.CITY_BBOX 里,无法判定;需先补充范围",
        )

    total = inside + outside
    ratio = inside / total if total else 0.0
    detail = f"{inside}/{total} 个景点坐标在{city}范围内"
    if bad_examples:
        detail += f";越界示例: {', '.join(bad_examples)}"

    return Metric(
        name="coord_coverage",
        value=ratio,
        ok=ratio >= cfg.COORD_COVERAGE_MIN,
        detail=detail,
    )


def m_intraday_travel_km_p95(ctx: EvalContext) -> Metric:
    """单日行程里,景点之间移动距离的 95 分位(公里)。

    用 p95 而不是平均值:平均值会被"大部分天排得很松"掩盖掉个别排得极赶的日子,
    而那几天正是体验最差的。p95 能暴露"有一天要跑 80 公里"。

    只看同一天内的景点间移动,不含跨天。这是**参考值**,不作判定 ——
    "多少公里算太赶"取决于交通方式和城市,没有统一及格线。
    """
    days = _days(ctx)
    if not days:
        return _missing_plan("intraday_travel_km_p95")

    per_day: list[float] = []
    for d in days:
        pts = []
        for a in d.get("attractions") or []:
            loc = a.get("location") or {}
            lon, lat = loc.get("longitude"), loc.get("latitude")
            if lon is not None and lat is not None:
                pts.append((float(lon), float(lat)))
        if len(pts) >= 2:
            per_day.append(path_length_km(pts))

    if not per_day:
        return Metric(name="intraday_travel_km_p95", value=None, ok=None, detail="没有可计算的单日路径")

    p95 = _percentile(per_day, 0.95)
    return Metric(
        name="intraday_travel_km_p95",
        value=round(p95, 2) if p95 is not None else None,
        ok=None,
        detail=f"{len(per_day)} 天有路径;中位 {statistics.median(per_day):.1f} km,最大 {max(per_day):.1f} km",
    )


# ===========================================================================
# C 类 · 成本(只报告,不判定)
# ===========================================================================


def m_latency_s(ctx: EvalContext) -> Metric:
    """端到端耗时(秒)。"""
    ms = ctx.run.get("elapsed_ms")
    if ms is None:
        return Metric(name="latency_s", value=None, ok=None, detail="运行记录里没有 elapsed_ms")
    return Metric(name="latency_s", value=round(float(ms) / 1000, 1), ok=None, detail="")


def m_llm_calls(ctx: EvalContext) -> Metric:
    """LLM 调用次数。"""
    usage = ctx.run.get("usage") or {}
    n = usage.get("llm_calls")
    if n is None:
        return Metric(name="llm_calls", value=None, ok=None, detail="")
    return Metric(name="llm_calls", value=float(n), ok=None, detail="")


def m_cost_cny(ctx: EvalContext) -> Metric:
    """花费(元)。单价见 observability/pricing.py,受峰谷时段影响。"""
    usage = ctx.run.get("usage") or {}
    c = usage.get("cost_cny")
    if c is None:
        return Metric(name="cost_cny", value=None, ok=None, detail="")
    src = usage.get("usage_source") or "?"
    note = "" if src == "api" else f"来源={src}(非 API 实测,是估算)"
    return Metric(name="cost_cny", value=round(float(c), 6), ok=None, detail=note)


# ===========================================================================
# 注册表
# ===========================================================================

ALL_METRICS: list[Callable[[EvalContext], Metric]] = [
    m_fallback_used,
    m_schema_valid,
    m_day_count_ok,
    m_date_continuity_ok,
    m_meal_slots_ok,
    m_attraction_count_ok,
    m_budget_arithmetic_ok,
    m_coord_coverage,
    m_intraday_travel_km_p95,
    m_latency_s,
    m_llm_calls,
    m_cost_cny,
]


def evaluate(ctx: EvalContext) -> list[Metric]:
    """跑全部指标,按 config.METRIC_ORDER 排序返回。

    单个指标抛异常不会中断整轮评测 —— 记成一条"计算失败"的指标继续。
    评测跑到一半因为一个指标崩溃而丢掉全部结果,代价太大。
    """
    results: list[Metric] = []
    for fn in ALL_METRICS:
        name = fn.__name__.removeprefix("m_")
        try:
            results.append(fn(ctx))
        except Exception as e:
            results.append(
                Metric(name=name, value=None, ok=None, detail=f"指标计算异常: {type(e).__name__}: {e}")
            )

    order = {n: i for i, n in enumerate(cfg.METRIC_ORDER)}
    results.sort(key=lambda m: (order.get(m.name, len(order)), m.name))
    return results


def summarize(metrics: list[Metric]) -> dict[str, Any]:
    """把指标列表压成可直接落库/进报告的字典。

    判定类的指标汇总成"通过数/总数";不可计算的单独计数 ——
    否则"3 项无法计算"会被算成"3 项不通过"。
    """
    judged = [m for m in metrics if m.ok is not None]
    passed = [m for m in judged if m.ok]
    uncomputable = [m for m in metrics if m.value is None]
    return {
        "metrics": {m.name: m.value for m in metrics},
        "passed": len(passed),
        "judged": len(judged),
        "uncomputable": [m.name for m in uncomputable],
        "failed": [m.name for m in judged if not m.ok],
    }
