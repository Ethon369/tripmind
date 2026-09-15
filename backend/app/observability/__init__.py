"""可观测性:运行日志、埋点与用量计量

对外暴露:
- NullObserver / JsonlObserver / make_observer  运行日志
- MeteredLLM / collect_usage / UsageCollector  LLM 用量与成本
"""

from .metering import CallUsage, MeteredLLM, UsageCollector, collect_usage, get_collector
from .pricing import PRICE_TABLE_VERSION, compute_cost_cny, get_price, is_peak, is_priced
from .run_logger import JsonlObserver, NullObserver, make_observer

__all__ = [
    # 运行日志
    "NullObserver",
    "JsonlObserver",
    "make_observer",
    # 用量计量
    "MeteredLLM",
    "UsageCollector",
    "CallUsage",
    "collect_usage",
    "get_collector",
    # 定价
    "PRICE_TABLE_VERSION",
    "compute_cost_cny",
    "get_price",
    "is_peak",
    "is_priced",
]
