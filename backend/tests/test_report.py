"""report.py —— 聚合与渲染

**这组测试在保护什么**

报告是**给人看的**,而且是给没读过代码的人看的(同事、三个月后的自己)。
它的危险不在于崩溃,而在于**看起来正常但把数读错了**:

- "算不出"被算成"不合格" → 报告显得比实际差
- "不判定"(成本类)被算进通过率 → 报告显得比实际好
- 某个指标算不出时静默显示成 0 → 和"真的是 0"分不清

所以下面重点测这三类,而不是"函数有没有返回值"。
"""

from __future__ import annotations

import pytest

from app.eval import config as cfg
from app.eval.metrics import Metric
from app.eval.report import RequestResult, aggregate, render_report


def _m(name: str, value: float | None, ok: bool | None, detail: str = "") -> Metric:
    return Metric(name=name, value=value, ok=ok, detail=detail)


def _res(rid: str, metrics: list[Metric], city: str = "北京", days: int = 3) -> RequestResult:
    return RequestResult(request_id=rid, city=city, travel_days=days, metrics=metrics)


# ===========================================================================
# 聚合
# ===========================================================================


class Test判定类:
    def test_通过率按判定数算不是按请求数(self):
        """ok is None 的那些(算不出/不判定)**不能进分母**。"""
        results = [
            _res("a", [_m("fallback_used", 0.0, True)]),
            _res("b", [_m("fallback_used", 1.0, False, "已降级")]),
            # 这一条压根算不出来 —— 不该拉低通过率
            _res("c", [_m("fallback_used", None, None, "无数据")]),
        ]
        s = aggregate(results)["metrics"]["fallback_used"]
        assert s["judged"] == 2
        assert s["passed"] == 1
        assert s["uncomputable"] == 1

    def test_不通过的带请求id和原文(self):
        results = [
            _res("bj-5d-family", [_m("poi_support_rate", 0.3, False, "3/10 个景点名有据可查")]),
            _res("bj-1d", [_m("poi_support_rate", 1.0, True)]),
        ]
        s = aggregate(results)["metrics"]["poi_support_rate"]
        assert len(s["failed"]) == 1
        assert s["failed"][0]["request_id"] == "bj-5d-family"
        # detail 必须完整带过来 —— 报告里那一行就是靠它说清"差在哪"
        assert "3/10" in s["failed"][0]["detail"]

    def test_均值最小最大(self):
        results = [
            _res("a", [_m("coord_mae_km", 1.0, True)]),
            _res("b", [_m("coord_mae_km", 3.0, False)]),
            _res("c", [_m("coord_mae_km", 2.0, True)]),
        ]
        s = aggregate(results)["metrics"]["coord_mae_km"]
        assert s["mean"] == 2.0
        assert s["min"] == 1.0
        assert s["max"] == 3.0


class Test参考类:
    def test_没有及格线的指标不算通过率(self):
        """成本类 ok 永远是 None。它们只该报数值,不能混进"通过/判定"。"""
        results = [
            _res("a", [_m("cost_cny", 0.12, None)]),
            _res("b", [_m("cost_cny", 0.15, None)]),
        ]
        s = aggregate(results)["metrics"]["cost_cny"]
        assert s["kind"] == "informational"
        assert s["judged"] == 0
        assert s["passed"] == 0
        assert s["failed"] == []
        assert s["mean"] == pytest.approx(0.135)


class Test算不出:
    def test_全算不出的指标单独归类且不算失败(self):
        results = [
            _res("a", [_m("coord_mae_km", None, None, "样本不足")]),
            _res("b", [_m("coord_mae_km", None, None, "样本不足")]),
        ]
        s = aggregate(results)["metrics"]["coord_mae_km"]
        assert s["kind"] == "uncomputable"
        assert s["uncomputable"] == 2
        assert s["failed"] == []

    def test_算不出与判定是两个独立的轴可以重叠(self):
        """行程里一个景点都没有:比例算不出来(分母 0),同时这本身就是失败。

        两者都要计入。**不能**为了"好加"就把其中一个抹掉。
        """
        results = [_res("a", [_m("poi_support_rate", None, False, "行程里没有景点,无从判断")])]
        s = aggregate(results)["metrics"]["poi_support_rate"]
        assert s["uncomputable"] == 1, "值算不出来,该计入算不出"
        assert s["judged"] == 1, "但确实下了判定,该计入判定数"
        assert s["passed"] == 0

    def test_一项指标都没有时不崩(self):
        agg = aggregate([_res("a", [])])
        assert agg["metrics"] == {}
        assert agg["overall"]["judged"] == 0


class Test顺序:
    def test_按_METRIC_ORDER_排而不是字母序(self):
        """报告的行序应该跟着重要性走,和 evaluate() 的排序规则保持一致。"""
        names = ["cost_cny", "schema_valid", "fallback_used"]
        results = [_res("a", [_m(n, 1.0, None) for n in names])]
        got = list(aggregate(results)["metrics"])
        # METRIC_ORDER 里 fallback_used 在 schema_valid 前,cost_cny 最后
        assert got.index("fallback_used") < got.index("schema_valid") < got.index("cost_cny")


# ===========================================================================
# 渲染
# ===========================================================================


def _render(results, **kw):
    kw.setdefault("tag", "baseline")
    kw.setdefault("provenance", {"git_sha": "abc1234", "model": "deepseek-v4-flash"})
    return render_report(results, **kw)


def _mean_cell(text: str, metric: str) -> str:
    """从「总览」表里取出某个指标的**均值**那一格。

    表格结构:`| 指标 | 含义 | 均值 | 判定 | 算不出 |`
    必须按格取,不能"整行含某个字符就算过" —— 那样写出来的断言是空的。
    """
    row = next(
        ln for ln in text.splitlines()
        if ln.startswith("|") and f"`{metric}`" in ln
    )
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[0] == f"`{metric}`", f"取到了别的行: {row[:60]}"
    return cells[2]


class Test渲染:
    def test_七个章节都在(self):
        text = _render([_res("a", [_m("fallback_used", 0.0, True)])])
        for title in ["## 一、", "## 二、", "## 三、", "## 四、", "## 五、", "## 六、", "## 七、"]:
            assert title in text, f"少了章节 {title}"

    def test_算不出和真的是0必须分得清(self):
        """三态设计的落地:None → 「—」,0.0 → 「0」。

        一开始这条测试写错了 —— 断言的是整行"含有破折号",而判定列本来就是
        破折号,所以不管 `_fmt` 怎么改它都绿。是变异测试把它抓出来的:
        把 `return "—"` 改成 `return "0"`,测试照样通过,说明它什么都没测。
        现在**精确断言均值单元格**。
        """
        gap = _render([_res("a", [_m("coord_mae_km", None, None)])])
        zero = _render([_res("a", [_m("fallback_used", 0.0, True)])])
        assert _mean_cell(gap, "coord_mae_km") == "—", "算不出来必须显示成破折号"
        assert _mean_cell(zero, "fallback_used") == "0", "真的是 0 要显示成 0"

    def test_降级那条被标出来(self):
        text = _render([_res("bad", [_m("fallback_used", 1.0, False)])])
        row = next(ln for ln in text.splitlines() if "`bad`" in ln)
        assert "**是**" in row

    def test_不通过的详情原文进报告(self):
        text = _render(
            [_res("bj-5d", [_m("poi_support_rate", 0.3, False, "3/10 个景点名有据可查")])]
        )
        assert "3/10 个景点名有据可查" in text

    def test_局限声明必须出现(self):
        """不写局限,数字就会被读错。这一段是报告的一部分,不是装饰。"""
        text = _render([_res("a", [_m("fallback_used", 0.0, True)])])
        assert "这份报告的局限" in text
        assert "没匹配上 ≠ 编造" in text     # 库存不全 ≠ 编造(baseline 实测出来的)
        assert "poi_exists_rate" in text     # 必须指向真正判断编造的那条
        assert "88%" in text                 # 字符串匹配的实测误差率
        assert "temperature=0" in text       # 评测配置 ≠ 线上配置

    def test_局限标题里不嵌粗体标记(self):
        """标题渲染时外面会包一层 `**`,标题自己再带 `**` 就写坏了。

        这个 bug 真发生过:渲染出 `**...是**下限**,不是真值**`,
        在 GitHub 上显示成乱码。丑但不崩,所以只能靠测试钉住。
        """
        from app.eval.report import _LIMITATIONS

        for title, _body in _LIMITATIONS:
            assert "**" not in title, f"标题里嵌了粗体标记,渲染会坏: {title!r}"

    def test_阈值快照跟着版本走(self):
        text = _render([_res("a", [])])
        assert f"`{cfg.METRICS_VERSION}`" in text
        assert "poi_support_rate_min" in text

    def test_环境指纹写进头部(self):
        text = _render([_res("a", [])], provenance={"git_sha": "deadbee", "git_dirty": True})
        assert "`deadbee`" in text
        assert "是" in text  # git_dirty=True 渲染成「是」

    def test_空结果不崩(self):
        text = _render([])
        assert "# 评测报告 · baseline" in text
        assert "**0** 条行程" in text

    def test_没有环境指纹时明说(self):
        text = render_report([_res("a", [])], tag="t", provenance=None)
        assert "未记录" in text
