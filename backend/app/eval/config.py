"""评测的阈值与版本号

为什么阈值要单独放一个文件、还要带版本号
----------------------------------------
1. **阈值是主观判断,不该散落在代码里。** "坐标覆盖率到 0.95 算过关"是我的选择,
   不是物理定律。集中放在这里,换一套标准只改这一个文件。
2. **报告要能横向比较。** 两份 baseline 只有用同一套阈值才有可比性。报告头部会
   写上 `METRICS_VERSION`,如果两次跑的数字对不上,先看版本号是否一致 ——
   这能防住"阈值偷偷改了却说指标退步了"。

改这个文件时要做的两件事
------------------------
- **`METRICS_VERSION` 跟着改**(日期形式),否则旧报告会显得和新报告可比,实际不可比
- **在下面写清为什么改**,别只改数字

关于 `direction` 的约定
-----------------------
`lower_is_better=True` 的指标,`Metric.ok` 的含义是"这个值算不算问题"。
比如 `fallback_used` 越低越好,所以 `ok = (value == 0)`。
"""

from __future__ import annotations

from typing import Any

# 指标定义与阈值的版本。**改了任何阈值都要改这里。**
METRICS_VERSION = "2026-09-16"

# ===========================================================================
# A 类 · 自洽性 —— 只看行程自身,不需要任何外部数据
# ===========================================================================

# 每天至少要有几个景点才算"排了行程"。1 是底线,不是质量线。
MIN_ATTRACTIONS_PER_DAY = 1

# 每天至少要有几餐。3 = 早/午/晚都在。
MIN_MEALS_PER_DAY = 3

# 预算算术的容差。
# ±1 元:总额与四项之和应该严丝合缝,允许 1 元的浮点/取整误差。
BUDGET_TOTAL_TOLERANCE_CNY = 1
# ±5%:各项与明细之和(门票 Σ、餐饮 Σ、酒店均价×晚数)的偏差容忍度。
# 比总额松,因为明细本身可能是 LLM 估的,精确对上不现实。
BUDGET_DETAIL_TOLERANCE_PCT = 5.0

# ===========================================================================
# B 类 · 接地性 —— 需要真实数据做 ground truth
# ===========================================================================

# 坐标落在目标城市范围内的比例。低于这个值说明坐标明显跑偏了。
#
# 注意:这个指标门槛很低 —— 它抓的是"整片坐标用错城市"(比如给上海行程
# 套了北京的坐标),抓不到"编了一个本城市的假坐标"。详见 geo.py 的说明。
COORD_COVERAGE_MIN = 0.95

# 景点名与真实 POI 名的相似度阈值(difflib.SequenceMatcher ratio)。
# 0.75 是经验值:够容"故宫"vs"故宫博物院"这类别名,又能排除完全不同的景点。
POI_NAME_MATCH_RATIO = 0.75

# 坐标与真实 POI 坐标的平均偏差上限(公里)。
# 超过这个数说明"名字对了但位置是编的"。
COORD_MAE_KM_MAX = 2.0

# ===========================================================================
# 报告用
# ===========================================================================

# 报告中列出的指标顺序(按重要性,不是字母序)。
# 不在这个列表里的指标仍然会计算,只是排在后面。
METRIC_ORDER = [
    "fallback_used",
    "schema_valid",
    "day_count_ok",
    "date_continuity_ok",
    "attraction_count_ok",
    "meal_slots_ok",
    "budget_arithmetic_ok",
    "coord_coverage",
    "poi_support_rate",
    "coord_mae_km",
    "intraday_travel_km_p95",
    "latency_s",
    "llm_calls",
    "cost_cny",
]

# 只在报告里展示数值、不判定通过与否的指标(成本类、分布类)。
# 理由:它们没有"及格线",只有"高不高"。
INFORMATIONAL_METRICS = {
    "intraday_travel_km_p95",
    "latency_s",
    "llm_calls",
    "cost_cny",
    "coord_mae_km",
}


def thresholds_snapshot() -> dict[str, Any]:
    """当前阈值快照,写进报告头部。

    有了它,翻出一份旧报告时能立刻看出"当时的及格线是多少" ——
    否则半年后看到 `coord_coverage = 0.96 ok` 根本不知道标准是什么。
    """
    return {
        "metrics_version": METRICS_VERSION,
        "min_attractions_per_day": MIN_ATTRACTIONS_PER_DAY,
        "min_meals_per_day": MIN_MEALS_PER_DAY,
        "budget_total_tolerance_cny": BUDGET_TOTAL_TOLERANCE_CNY,
        "budget_detail_tolerance_pct": BUDGET_DETAIL_TOLERANCE_PCT,
        "coord_coverage_min": COORD_COVERAGE_MIN,
        "poi_name_match_ratio": POI_NAME_MATCH_RATIO,
        "coord_mae_km_max": COORD_MAE_KM_MAX,
    }
