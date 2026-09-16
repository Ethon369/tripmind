"""评测 harness

这个包回答一个问题:**改动之后,行程质量到底变好了还是变差了?**

为什么需要它
------------
"我加了 RAG,行程更准了" 这句话在面试里是不可验证的 —— 没有数字,
就是没说过。这个包把"更准了"变成可复现的量化对比。

模块
----
- `geo.py`                地理计算(城市范围判定、球面距离)
- `config.py`             阈值与版本号
- `metrics.py`            A 类(自洽性)+ C 类(成本)指标,每个都是纯函数
- `metrics_grounding.py`  B 类:需要冻结的真实 POI 做 ground truth
- `run_eval.py`           CLI:录制(--mode=record)/ 回放(--mode=replay)
- `report.py`             把指标聚合、渲染成 markdown(纯函数)

两层冻结,把「输入」和「被测系统」隔开
--------------------------------------
- `data/frozen/amap/`   高德响应的 ground truth,由 `scripts/recorder.py` 录
- `data/frozen/plans/`  行程的录制结果,由 `run_eval.py --mode=record` 录

**分开是因为钱。** 录一次要花真钱(10 条约 ¥1.2),而改指标口径、改报告排版
是天天要做的事。冻住之后 replay 完全不碰网络,改多少次都免费 ——
不这样的话,你就不会去改指标了。

两条设计原则
------------
**不含任何 LLM 打分。** 让同一个模型既生成又评价是循环论证,而且换模型
分数就变,没法跨版本比较。所有指标要么从行程内部重算(算术、日期、结构),
要么和外部真实数据比对(坐标、POI 名)。

**可复现靠冻结,不靠运气。** 固定输入集在 `data/frozen/requests.json`,
高德响应冻结在 `data/frozen/amap/`。这样两次跑出来的差异只可能来自
代码改动,不会来自"今天高德返回了别的"。
"""

from .config import METRICS_VERSION
from .metrics import EvalContext, Metric, evaluate, summarize
from .metrics_grounding import ground_truth_for, load_inventory
from .report import RequestResult, aggregate, render_report

__all__ = [
    "METRICS_VERSION",
    "EvalContext",
    "Metric",
    "evaluate",
    "summarize",
    "ground_truth_for",
    "load_inventory",
    "RequestResult",
    "aggregate",
    "render_report",
]
