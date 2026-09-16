"""把评测指标渲染成 markdown 报告

**它只做两件事,两件都是纯函数**

1. `aggregate()` —— 把「N 条请求 × M 个指标」压成每个指标的汇总
2. `render_report()` —— 把汇总渲染成 markdown

不读文件、不碰网络、不调 LLM。所以报告长什么样可以**单独测**,
不用等一次真跑(那要花 20 分钟和真钱)。

聚合的三条规则
--------------
直接对应 `Metric` 的三态设计,最要紧的是**别把"算不出"算成"不合格"**:

| 情况 | 怎么算 | 为什么 |
|---|---|---|
| `ok is not None` | 通过数 / 判定数 | 这是有及格线的那类 |
| `ok is None` | 均值 / 最小 / 最大 | 成本类没有及格线,只有高低 |
| `value is None` | 单独计数,**不算失败** | 「不知道」和「不合格」必须分开 |

⚠️ **"判定"和"算不出"是两个独立的轴,可以重叠。**
一个指标可能既算不出数值、又被判为不合格 —— 比如行程里一个景点都没有:
`poi_support_rate` 算不出来(分母是 0),同时这件事本身就是失败。
所以报告里这两个数字**不能相加**,表头下面专门写了这句话。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Sequence

from . import config as cfg
from .metrics import Metric


# ===========================================================================
# 输入结构
# ===========================================================================


@dataclass(frozen=True)
class RequestResult:
    """一条请求的评测结果。

    带上 city / travel_days,是为了"逐条明细"表能一眼看出哪条最差 ——
    没有这两列,读者得回头翻 requests.json 才知道 `bj-5d-family` 是什么。
    """

    request_id: str
    city: str
    travel_days: int
    metrics: list[Metric]


# ===========================================================================
# 指标的「人话」说明
# ===========================================================================


# 报告是给**没读过代码的人**看的(面试官、三个月后的自己)。光有一列
# `coord_mae_km = 7.55` 说明不了任何事,得说清这个数意味着什么。
METRIC_NOTES: dict[str, str] = {
    "fallback_used": "走了降级(返回现造的兜底行程)的比例。越低越好",
    "schema_valid": "返回的行程符合 TripPlan 数据契约",
    "day_count_ok": "天数与请求一致",
    "date_continuity_ok": "日期从起始日**逐日连续**排下去",
    "attraction_count_ok": "每天至少排了 1 个景点",
    "meal_slots_ok": "每天排满 3 餐",
    "budget_arithmetic_ok": "预算总额 = 四项之和,且各项与明细相符",
    "coord_coverage": "坐标落在目标城市范围内的比例。**门槛低**,抓的是整片跑错城市",
    "poi_support_rate": "景点名能在**我录的 POI 库存**里找到支撑的比例。⚠️ 库存是 20 个关键词的**样本**,所以它测的是**库存覆盖率**,不是编造率 —— 偏低时先看 `poi_exists_rate`",
    "poi_exists_rate": "拿景点名去**高德搜一次**,搜得到自己的比例。**这才是判断「编造」的那条**",
    "coord_mae_km": "匹配上的景点,行程坐标与真实坐标的平均偏差(km)。抓「名字对了但位置是编的」",
    "intraday_travel_km_p95": "单日景点间移动距离的 95 分位(km)。参考值,不判定",
    "latency_s": "端到端耗时(秒)",
    "llm_calls": "LLM 调用次数",
    "cost_cny": "花费(人民币元)",
}

# 逐条明细表展示哪几列。
#
# 为什么不把 14 个指标全铺开:表会宽到读不下去。这几列是"一眼看出哪条最差"
# 所需的最小集合,完整数值在总览表和冻结文件里都有。
#
# `poi_support_rate` 和 `poi_exists_rate` **必须挨着** —— 两条的差值
# (我的库存覆盖了多少 vs 名字到底真不真)本身就是结论,拆开就看不出来了。
_DETAIL_COLUMNS = [
    "fallback_used",
    "schema_valid",
    "poi_support_rate",
    "poi_exists_rate",
    "coord_coverage",
    "coord_mae_km",
    "budget_arithmetic_ok",
    "latency_s",
    "cost_cny",
]

# 报告头部「运行环境」的显示名。认不出的键原样显示,不丢信息。
_PROVENANCE_LABELS: dict[str, str] = {
    "recorded_at": "录制时间",
    "git_sha": "git 提交",
    "git_dirty": "录制时工作区有未提交改动",
    "agent_mode": "编排模式",
    "rag_enabled": "RAG",
    "model": "模型",
    "temperature": "temperature",
    "metrics_version": "指标版本",
    "source": "数据来源",
}


# ===========================================================================
# 聚合
# ===========================================================================


def _stats_for(pairs: Sequence[tuple[str, Metric]]) -> dict[str, Any]:
    """把一个指标的 N 条结果压成一组统计量。

    Args:
        pairs: (request_id, Metric) 列表
    """
    metrics = [m for _, m in pairs]
    values = [m.value for m in metrics if m.value is not None]
    judged = [m for m in metrics if m.ok is not None]

    if not values:
        kind = "uncomputable"
    elif judged:
        kind = "judged"
    else:
        # 有数值但从不判定 —— 成本类就是这样,它没有及格线
        kind = "informational"

    return {
        "kind": kind,
        "mean": round(statistics.mean(values), 4) if values else None,
        "min": round(min(values), 4) if values else None,
        "max": round(max(values), 4) if values else None,
        "judged": len(judged),
        "passed": sum(1 for m in judged if m.ok),
        "uncomputable": len(metrics) - len(values),
        # 只收集**明确判为不合格**的。ok is None(没及格线)不算失败
        "failed": [
            {"request_id": rid, "detail": m.detail}
            for rid, m in pairs
            if m.ok is False
        ],
    }


def aggregate(results: Sequence[RequestResult]) -> dict[str, Any]:
    """把 N 条请求的指标压成每个指标的汇总。

    指标顺序按 `config.METRIC_ORDER`(重要性),不在这张表里的排在后面 ——
    和 `evaluate()` 的排序规则保持一致,免得两处顺序不一样。
    """
    names: list[str] = []
    for r in results:
        for m in r.metrics:
            if m.name not in names:
                names.append(m.name)

    order = {n: i for i, n in enumerate(cfg.METRIC_ORDER)}
    names.sort(key=lambda n: (order.get(n, len(order)), n))

    metrics: dict[str, Any] = {}
    for name in names:
        pairs = [(r.request_id, m) for r in results for m in r.metrics if m.name == name]
        metrics[name] = {"note": METRIC_NOTES.get(name, ""), **_stats_for(pairs)}

    judged = [s for s in metrics.values()]
    return {
        "requests": len(results),
        "metrics": metrics,
        "overall": {
            "passed": sum(s["passed"] for s in judged),
            "judged": sum(s["judged"] for s in judged),
            "uncomputable": sum(s["uncomputable"] for s in judged),
        },
    }


# ===========================================================================
# 渲染
# ===========================================================================


def _fmt(value: float | None, name: str = "") -> str:
    """按指标类型选小数位。None 显示成「—」而不是 0 —— 两者含义完全不同。"""
    if value is None:
        return "—"
    if name == "cost_cny":
        return f"{value:.4f}"
    if name == "latency_s":
        return f"{value:.1f}"
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |"]
    out.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return out


# 报告必须自带的局限声明。
#
# 为什么要写死在这里而不是让调用方拼:这几条是**跟着口径走的** ——
# 换了指标口径就该改这里。放在一处,改的时候不会漏。
#
# ⚠️ 标题里**不要再用 `**`**,渲染时会整体加粗,嵌套就写坏了
# (会渲染成 `**...是**下限**,...**`)。强调用文字,别用标记。
_LIMITATIONS: list[tuple[str, str]] = [
    (
        "`poi_support_rate` 测的是「我的库存覆盖了多少」,不是「模型编没编」",
        "库存是 20 个关键词搜出来的**样本**(每城 340~360 个),不是普查。"
        "所以**没匹配上 ≠ 编造** —— 真实景点没被关键词命中就会被判成查无此物。"
        "baseline 实测:41 个「未命中」里逐个核对后**没有一个**是编造。"
        "要判断真伪请看 `poi_exists_rate`。",
    ),
    (
        "`poi_exists_rate` 的判据是「搜到的像不像」,不是「有没有搜到」",
        "高德对**编造**的名字不会返回空 —— 它总会硬凑一堆相关 POI 给你"
        "(搜「紫金幻梦星际主题乐园」会返回「泡泡玛特城市乐园」)。"
        "所以必须比名字相似度。另外它只覆盖**高德找得到**的地方:"
        "偏远地区的小众景点可能真实存在但搜不到,那会被算成存疑。",
    ),
    (
        "`coord_coverage` 门槛低,单看它会漏掉一大类问题",
        "假坐标只要落在目标城市范围内它就放行。要抓「名字对了但坐标是编的」"
        "必须看 `coord_mae_km`。",
    ),
    (
        "字符串名字匹配本身有误差(实测约 88% 正确)",
        "`coord_mae_km` 靠它找对应物。「匹配到别的 POI」那一类会拿错误实体的"
        "坐标去比,**诬告坐标是编的**。误差分析见 `metrics_grounding.py` 模块文档。",
    ),
    (
        "temperature=0 是评测配置,不是线上配置",
        "线上服务用框架默认温度。所以这份报告衡量的是「代码改动带来的差异」,"
        "不是「用户实际拿到什么」。",
    ),
    (
        "每条请求只跑 1 次,不报标准差",
        "单次样本的方差是未知的。想比较两组配置时,差异小的指标不要下结论。",
    ),
]


def render_report(
    results: Sequence[RequestResult],
    *,
    tag: str,
    provenance: dict[str, Any] | None = None,
    generated_at: str = "",
) -> str:
    """渲染完整报告。

    Args:
        results: 每条请求的评测结果
        tag: 本次评测的名字(和冻结目录、报告文件名一致)
        provenance: 录制时的环境信息,见 run_eval 的 `_provenance()`
        generated_at: 生成时间字符串;留空则不显示
    """
    agg = aggregate(results)
    lines: list[str] = []

    # ---- 标题 ----
    lines.append(f"# 评测报告 · {tag}")
    lines.append("")
    lines.append(
        f"> 由 `app/eval/run_eval.py --mode=replay` 生成"
        + (f" · {generated_at}" if generated_at else "")
    )
    lines.append(
        f"> 数据:`data/frozen/plans/{tag}/` 里冻结的 **{agg['requests']}** 条行程"
        f"(回放,不调用任何网络)"
    )
    lines.append("")

    # ---- 一、这份报告回答什么 ----
    lines.append("## 一、这份报告回答什么")
    lines.append("")
    lines.append(
        "「改了这个之后,行程质量到底变好了还是变差了?」—— 这个问题没有数字就是没回答。"
    )
    lines.append("")
    lines.append(
        "所以下面的指标**全部客观可复算**:要么从行程自身重算(算术、日期、结构),"
        "要么和冻结的真实数据比对(坐标、POI 名)。**没有任何一条是让 LLM 给自己打分** ——"
        "同一个模型既生成又评分是循环论证,换个模型分数就变,没法跨版本比较。"
    )
    lines.append("")

    # ---- 二、运行环境 ----
    lines.append("## 二、运行环境(可追溯)")
    lines.append("")
    lines.append(
        "没有这一段,翻出一份旧报告时你不知道它是哪个提交、什么配置下测的 —— 那样的对比是假的。"
    )
    lines.append("")
    if provenance:
        rows = []
        for key, value in provenance.items():
            label = _PROVENANCE_LABELS.get(key, key)
            if isinstance(value, bool):
                value = "是" if value else "否"
            rows.append([label, f"`{value}`"])
        lines.extend(_table(["项", "值"], rows))
    else:
        lines.append("(未记录 —— 这份报告不是由 `run_eval.py` 生成的)")
    lines.append("")

    # ---- 三、阈值 ----
    lines.append("## 三、阈值(这次的及格线)")
    lines.append("")
    lines.append(
        "阈值是主观判断,不是物理定律 —— 写在这里是为了让「两份报告可比」。"
        f"当前的指标版本是 `{cfg.METRICS_VERSION}`;两次跑的数字对不上时,先看版本号是否一致。"
    )
    lines.append("")
    lines.extend(
        _table(
            ["阈值", "值"],
            [[f"`{k}`", f"`{v}`"] for k, v in cfg.thresholds_snapshot().items()],
        )
    )
    lines.append("")

    # ---- 四、总览 ----
    lines.append("## 四、总览")
    lines.append("")
    lines.append(
        "**「判定」和「算不出」是两个独立的轴,不能相加。** 一个指标可能既算不出数值、"
        "又被判为不合格 —— 比如行程里一个景点都没有:`poi_support_rate` 算不出来(分母是 0),"
        "而这件事本身就是失败。"
    )
    lines.append("")
    rows = []
    for name, s in agg["metrics"].items():
        if s["kind"] == "judged":
            verdict = f"{s['passed']}/{s['judged']}"
        elif s["kind"] == "informational":
            verdict = "不判定"
        else:
            verdict = "—"
        rows.append(
            [
                f"`{name}`",
                s["note"],
                _fmt(s["mean"], name),
                verdict,
                str(s["uncomputable"]) if s["uncomputable"] else "—",
            ]
        )
    lines.extend(_table(["指标", "含义", "均值", "判定(通过/判定)", "算不出"], rows))
    lines.append("")
    o = agg["overall"]
    lines.append(
        f"**合计:判定 {o['judged']} 项,通过 {o['passed']} 项"
        f",不通过 {o['judged'] - o['passed']} 项;另有 {o['uncomputable']} 项算不出数值。**"
    )
    lines.append("")

    # ---- 五、逐条明细 ----
    lines.append("## 五、逐条明细")
    lines.append("")
    lines.append("一眼看出哪条最差。完整数值在总览表里,原始数据在冻结文件里。")
    lines.append("")
    rows = []
    for r in results:
        by_name = {m.name: m for m in r.metrics}
        # 降级是最重要的一条,单独给个醒目的标记
        fb = by_name.get("fallback_used")
        fb_cell = "**是**" if (fb and fb.value) else "否"
        row = [f"`{r.request_id}`", r.city, f"{r.travel_days}d", fb_cell]
        for col in _DETAIL_COLUMNS:
            if col == "fallback_used":
                continue
            m = by_name.get(col)
            row.append(_fmt(m.value if m else None, col))
        rows.append(row)
    headers = ["请求", "城市", "天数", "降级"] + [
        c for c in _DETAIL_COLUMNS if c != "fallback_used"
    ]
    lines.extend(_table(headers, rows))
    lines.append("")

    # ---- 六、不通过的条目 ----
    lines.append("## 六、不通过的条目")
    lines.append("")
    failed_any = False
    for name, s in agg["metrics"].items():
        if not s["failed"]:
            continue
        failed_any = True
        lines.append(f"### `{name}` —— {len(s['failed'])} 条不通过")
        lines.append("")
        for f in s["failed"]:
            lines.append(f"- `{f['request_id']}`: {f['detail']}")
        lines.append("")
    if not failed_any:
        lines.append("(全部通过 —— 如果这让你意外,先检查是不是**根本没有可比的数据**:"
                     "总览表里「算不出」一列如果全是数字,说明指标压根没算出来。)")
        lines.append("")

    # ---- 七、局限 ----
    lines.append("## 七、这份报告的局限(必读)")
    lines.append("")
    lines.append("不写这一段,上面的数字就会被读错。")
    lines.append("")
    for i, (title, body) in enumerate(_LIMITATIONS, 1):
        lines.append(f"{i}. **{title}** —— {body}")
    lines.append("")

    return "\n".join(lines)
