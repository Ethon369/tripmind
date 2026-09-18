"""pricing.py 的单元测试 —— 成本计算

**这个文件之前是零覆盖的**,而它算出来的数字会直接显示在
历史页的「花费」和评测报告里 —— 算错了没人会发现问题,
只会让所有成本结论静默失真。

被真实数据教过一次的做法在这里也适用:**别只测"函数有返回值",
要测容易被改坏的那几处**:

1. 峰谷切换(DeepSeek)—— 同一个模型两个时段差 2 倍,判错时段就错一倍
2. **厂商分支** —— 千问是平坦价,不能跟着时段走。这条最容易在
   "以后再加一个厂商"时被顺手改坏
3. 未知模型返回全 0,且 `is_priced` 为 False —— 区分「花费 0 元」与
   「没配价格」。这是本项目刻意保留的语义,别退化成"反正都是 0"
4. 缓存命中/未命中的加权 —— 两者单价差 50 倍(DeepSeek),漏掉一项就错一个数量级
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.observability.pricing import (
    UNKNOWN_PRICE,
    PRICE_TABLE_VERSION,
    compute_cost_cny,
    get_price,
    is_peak,
    is_priced,
)

# 2026-09-18 是周五,10:00 落在高峰时段(9-12)
FRIDAY_PEAK = datetime(2026, 9, 18, 10, 0)
# 周五 13:00 在两个高峰之间的空档
FRIDAY_GAP = datetime(2026, 9, 18, 13, 0)
# 周六全天算空闲
SATURDAY = datetime(2026, 9, 19, 10, 0)


# ===========================================================================
# 峰谷判定
# ===========================================================================


class TestIsPeak:
    @pytest.mark.parametrize("hour", [9, 10, 11, 14, 15, 17])
    def test_工作日高峰时段(self, hour):
        assert is_peak(datetime(2026, 9, 18, hour, 0)) is True

    @pytest.mark.parametrize("hour", [0, 8, 12, 13, 18, 23])
    def test_工作日非高峰时段(self, hour):
        """⚠️ 12:00 和 13:00 是**空档**,不是高峰 —— 午休那一小时。
        边界写成 `<= 12` 就会多算一小时的高峰价。"""
        assert is_peak(datetime(2026, 9, 18, hour, 0)) is False

    def test_周末全天都是空闲(self):
        assert is_peak(SATURDAY) is False
        assert is_peak(datetime(2026, 9, 20, 10, 0)) is False  # 周日


# ===========================================================================
# 取价:先按厂商分支,再看时段
# ===========================================================================


class TestGetPrice:
    def test_deepseek高峰比空闲贵一倍(self):
        peak = get_price("deepseek-flash", FRIDAY_PEAK)
        off = get_price("deepseek-flash", FRIDAY_GAP)
        assert off["cache_miss_input"] * 2 == peak["cache_miss_input"]
        assert off["output"] * 2 == peak["output"]

    def test_deepseek前缀匹配日期版本(self):
        """deepseek-flash / deepseek-v4-flash 这类写法都要认。"""
        assert is_priced("deepseek-flash")
        assert is_priced("deepseek-v4-flash")

    def test_千问是平坦价_不随时段变化(self):
        """**这条是切到阿里云百炼之后的关键契约。**

        千问没有峰谷。如果有人图省事把 get_price 改成"一律走峰谷",
        这条会红 —— 否则成本会在不同时段悄悄差 2 倍,而且完全看不出来。
        """
        at_peak = get_price("qwen3.7-plus", FRIDAY_PEAK)
        at_gap = get_price("qwen3.7-plus", FRIDAY_GAP)
        at_weekend = get_price("qwen3.7-plus", SATURDAY)
        assert at_peak == at_gap == at_weekend
        assert at_peak["cache_miss_input"] > 0

    def test_千问按官方价目(self):
        """输入 ¥2 / 输出 ¥8 每百万 token(中国内地,≤256K 输入档)。"""
        price = get_price("qwen3.7-plus")
        assert price["cache_miss_input"] == 2.0
        assert price["output"] == 8.0

    def test_千问带日期后缀也能认(self):
        assert is_priced("qwen3.7-plus-2026-05-26")

    @pytest.mark.parametrize("model", [None, "", "gpt-4", "某个没配价的模型"])
    def test_没配价的模型返回全零(self, model):
        assert get_price(model) == UNKNOWN_PRICE

    def test_大小写与空格不影响(self):
        assert get_price("  QWEN3.7-PLUS  ") == get_price("qwen3.7-plus")


# ===========================================================================
# is_priced —— 区分「花费 0 元」与「没配价格」
# ===========================================================================


class TestIsPriced:
    def test_已配价的模型(self):
        assert is_priced("qwen3.7-plus") is True
        assert is_priced("deepseek-flash") is True

    def test_未配价的模型为假(self):
        """报告靠它把"这次真的没花钱"和"我不知道价"分开。
        退化成恒 True/恒 False 都会让报告说假话。"""
        assert is_priced("gpt-4") is False
        assert is_priced(None) is False


# ===========================================================================
# compute_cost_cny
# ===========================================================================


class TestComputeCost:
    def test_纯输入(self):
        # 100 万输入 token × ¥2/百万 = ¥2
        assert compute_cost_cny(1_000_000, 0, "qwen3.7-plus") == 2.0

    def test_纯输出(self):
        assert compute_cost_cny(0, 1_000_000, "qwen3.7-plus") == 8.0

    def test_输入输出相加(self):
        assert compute_cost_cny(500_000, 500_000, "qwen3.7-plus") == pytest.approx(1.0 + 4.0)

    def test_缓存命中打折(self):
        """100 万输入全部命中缓存,应比全部未命中便宜。"""
        hit = compute_cost_cny(1_000_000, 0, "qwen3.7-plus", cache_hit_tokens=1_000_000)
        miss = compute_cost_cny(1_000_000, 0, "qwen3.7-plus", cache_hit_tokens=0)
        assert hit < miss
        assert hit == 0.4

    def test_缓存信息缺失时按全部未命中算(self):
        """**偏保守**:算出来比实际贵,不低报成本。"""
        assert compute_cost_cny(1_000_000, 0, "qwen3.7-plus", cache_hit_tokens=None) == 2.0

    def test_命中数超过输入数时被夹住(self):
        """脏数据不该算出负数的未命中量。"""
        weird = compute_cost_cny(100, 0, "qwen3.7-plus", cache_hit_tokens=999_999)
        assert weird >= 0

    def test_没配价时是0而不是报错(self):
        assert compute_cost_cny(1_000_000, 1_000_000, "gpt-4") == 0.0

    def test_负数与空值不炸(self):
        assert compute_cost_cny(-5, -5, "qwen3.7-plus") == 0.0
        assert compute_cost_cny(None, None, "qwen3.7-plus") == 0.0  # type: ignore[arg-type]


class TestPriceTableVersion:
    def test_版本号存在且非空(self):
        """评测报告会带上它 —— 没有它,历史报告之间没法比。"""
        assert isinstance(PRICE_TABLE_VERSION, str)
        assert PRICE_TABLE_VERSION.strip()
