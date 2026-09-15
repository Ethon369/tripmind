"""LLM 定价表

⚠️ DeepSeek 自 2026-09-10 起改为【峰谷定价】—— 同一个模型在不同时段
价格相差 2 倍。所以成本计算必须带上时间维度,不能只乘一个固定单价。

⚠️ 三个字段而不是两个:
DeepSeek 的「缓存命中输入」与「缓存未命中输入」价格相差 50 倍
(空闲时段 0.02 vs 1.0)。忽略缓存命中会让成本算错一个数量级。

单位:人民币元 / 每百万 token
来源:DeepSeek 官方 2026-09-10 调价公告
"""

from __future__ import annotations

from datetime import datetime

# 改了价目表就改这里 —— 评测报告里会带上这个版本号,保证历史数据可比
PRICE_TABLE_VERSION = "2026-09-15"

# 元 / 百万 token
_PEAK = {"cache_hit_input": 0.04, "cache_miss_input": 2.0, "output": 8.0}
_OFFPEAK = {"cache_hit_input": 0.02, "cache_miss_input": 1.0, "output": 4.0}

# 模型名 → 价目。用前缀匹配,兼容 deepseek-flash / deepseek-v4-flash 等写法
_MODEL_PREFIXES = ("deepseek-flash", "deepseek-v4-flash")

# 未在表里的模型:返回全 0,并在报告里显式标注「价格未配置」,
# 而不是假装知道价钱
UNKNOWN_PRICE = {"cache_hit_input": 0.0, "cache_miss_input": 0.0, "output": 0.0}


def is_peak(dt: datetime | None = None) -> bool:
    """是否处于高峰时段。

    高峰:周一至周五 9:00-12:00 与 14:00-18:00(北京时间)
    其余时间(含周末全天)均为空闲时段。

    注意:这里用的是本机本地时间。本项目按中国时区部署/使用,
    若将来部署到其他时区,应显式换算出北京时间再判断。
    """
    dt = dt or datetime.now()
    if dt.weekday() >= 5:  # 周六=5, 周日=6
        return False
    return (9 <= dt.hour < 12) or (14 <= dt.hour < 18)


def get_price(model: str | None, dt: datetime | None = None) -> dict[str, float]:
    """取某模型在某一时刻的单价(元/百万 token)。

    表里没有的模型返回全 0,调用方应据此把 usage_source 标成未配置。
    """
    if not model:
        return dict(UNKNOWN_PRICE)
    name = model.strip().lower()
    if not any(name.startswith(p) for p in _MODEL_PREFIXES):
        return dict(UNKNOWN_PRICE)
    return dict(_PEAK if is_peak(dt) else _OFFPEAK)


def is_priced(model: str | None) -> bool:
    """该模型是否配了价格(用于区分「花费 0 元」和「没配价格」)。"""
    return any(x > 0 for x in get_price(model).values())


def compute_cost_cny(
    prompt_tokens: int,
    completion_tokens: int,
    model: str | None,
    cache_hit_tokens: int | None = None,
    dt: datetime | None = None,
) -> float:
    """算一次调用的花费。

    cache_hit_tokens 为 None 时(服务端没返回缓存信息),按全部未命中计算
    —— 这是【偏保守】的估法(算出来的比实际贵),不会低报成本。
    """
    price = get_price(model, dt)
    total_prompt = max(0, prompt_tokens or 0)
    completion = max(0, completion_tokens or 0)

    if cache_hit_tokens is None:
        miss = total_prompt
        hit = 0
    else:
        hit = max(0, min(cache_hit_tokens, total_prompt))
        miss = total_prompt - hit

    cost = (
        hit / 1_000_000 * price["cache_hit_input"]
        + miss / 1_000_000 * price["cache_miss_input"]
        + completion / 1_000_000 * price["output"]
    )
    return round(cost, 6)
