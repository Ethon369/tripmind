"""LLM 定价表

⚠️ **两家厂商的定价结构不一样,不能套同一套算法。**

DeepSeek:自 2026-09-10 起改为【峰谷定价】—— 同一个模型在不同时段
价格相差 2 倍。所以成本计算必须带上时间维度,不能只乘一个固定单价。

通义千问(阿里云百炼):**平坦价,没有峰谷**,但有输入长度分档。
本表只配 ≤256K 那一档(见下方说明)。

⚠️ 三个字段而不是两个:
DeepSeek 的「缓存命中输入」与「缓存未命中输入」价格相差 50 倍
(空闲时段 0.02 vs 1.0)。忽略缓存命中会让成本算错一个数量级。

单位:人民币元 / 每百万 token

来源:
- DeepSeek 官方 2026-09-10 调价公告
- 阿里云百炼「模型调用价格」文档(中国内地/华北2 北京地域)
"""

from __future__ import annotations

from datetime import datetime

# 改了价目表就改这里 —— 评测报告里会带上这个版本号,保证历史数据可比
PRICE_TABLE_VERSION = "2026-09-18"

# ===========================================================================
# DeepSeek —— 峰谷两套价,按调用时刻切换
# ===========================================================================

# 元 / 百万 token
_DEEPSEEK_PEAK = {"cache_hit_input": 0.04, "cache_miss_input": 2.0, "output": 8.0}
_DEEPSEEK_OFFPEAK = {"cache_hit_input": 0.02, "cache_miss_input": 1.0, "output": 4.0}

# 用前缀匹配,兼容 deepseek-flash / deepseek-v4-flash 等写法
_DEEPSEEK_PREFIXES = ("deepseek-flash", "deepseek-v4-flash")


# ===========================================================================
# 通义千问(阿里云百炼)—— 平坦价
# ===========================================================================
#
# 官方价目(qwen3.7-plus,中国内地):输入 ¥2 / 百万,输出 ¥8 / 百万。
#
# ⚠️ 两个必须知道的口径限制,别把这里的数字当成精确账单:
#
# 1. **只配了「输入 ≤256K」那一档。** 官方对 256K<输入≤1M 另报 ¥6/¥24。
#    本表不区分输入长度,超长输入的那部分会被**低估**。
#    对本项目无实际影响 —— 一次行程生成的 prompt 只有几千 token,
#    离 256K 差两个数量级。但**别把这张表直接搬去做长上下文场景的成本核算**。
#
# 2. **缓存命中价是按官方公布的折扣比例折算的**(隐式命中 ≈ 输入价的 20%),
#    不是从文档里直接抄的整数。若要做精确对账,以百炼控制台的账单为准。
_QWEN_FLAT = {"cache_hit_input": 0.4, "cache_miss_input": 2.0, "output": 8.0}

_QWEN_PREFIXES = ("qwen3.7-plus",)

# 未在表里的模型:返回全 0,并在报告里显式标注「价格未配置」,
# 而不是假装知道价钱
UNKNOWN_PRICE = {"cache_hit_input": 0.0, "cache_miss_input": 0.0, "output": 0.0}


def is_peak(dt: datetime | None = None) -> bool:
    """是否处于高峰时段。

    ⚠️ **只对 DeepSeek 有意义** —— 通义千问是平坦价,不随时段变化。

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

    先按厂商分支:**千问是平坦价(忽略 dt),DeepSeek 才看时段。**

    表里没有的模型返回全 0,调用方应据此把 usage_source 标成未配置。
    """
    if not model:
        return dict(UNKNOWN_PRICE)
    name = model.strip().lower()

    if any(name.startswith(p) for p in _QWEN_PREFIXES):
        return dict(_QWEN_FLAT)

    if any(name.startswith(p) for p in _DEEPSEEK_PREFIXES):
        return dict(_DEEPSEEK_PEAK if is_peak(dt) else _DEEPSEEK_OFFPEAK)

    return dict(UNKNOWN_PRICE)


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
