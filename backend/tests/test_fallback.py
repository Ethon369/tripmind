"""fallback.py —— 降级行程不能编造

**这组测试为什么非有不可**

P5 的 baseline 实测:10 条请求 `fallback_used` **全是 0** —— 降级路径一次都没被
触发过。所以改完这条路径**不能靠重跑 baseline 证明它对了**,重跑只会得到
一模一样的数字,不管你改没改对。

只能靠单测。而这条路径又**恰好在最坏的时刻执行**:LLM 输出解析失败、
管线出异常 —— 用户本来就拿不到正常结果,再给他一份编造的,他更看不出来。

所以下面的断言刻意写得**宽**:不看"景点列表是不是空的"(那太容易满足),
而是把整份行程序列化成 JSON,**在里面搜不该出现的东西**。
这样即使将来有人换个地方重新引入编造,也会被抓住。
"""

from __future__ import annotations

import json

import pytest

from app.agents.fallback import build_empty_plan
from app.models.schemas import TripPlan, TripRequest
from tests.conftest import make_request

# 旧实现里写死的坐标基准 —— 生成**任何**城市都会得到它
OLD_FAKE_LAT, OLD_FAKE_LON = 39.9, 116.4


def req(**over) -> TripRequest:
    return TripRequest(**{**make_request(), **over})


def as_json(plan: TripPlan) -> str:
    return json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)


# ===========================================================================
# 编造 —— 这两条是这组测试存在的原因
# ===========================================================================


class Test不编造:
    def test_上海的计划里不能出现北京的坐标(self):
        """**这正是原来的 bug。** 旧实现给任何城市都发 116.4/39.9。

        断言写成"整份 JSON 里搜不到",而不是"景点列表是空的" ——
        前者能抓住任何地方重新引入的假坐标,后者只能抓住"没清空景点"。
        """
        js = as_json(build_empty_plan(req(city="上海")))
        assert str(OLD_FAKE_LON) not in js, f"出现了写死的经度 {OLD_FAKE_LON}"
        assert str(OLD_FAKE_LAT) not in js, f"出现了写死的纬度 {OLD_FAKE_LAT}"

    def test_不能有北京景点1这种假名字(self):
        js = as_json(build_empty_plan(req(city="北京", travel_days=3)))
        assert "景点1" not in js
        assert "景点2" not in js

    def test_景点和餐饮都是空的(self):
        """不是"造得少",是**一个都不造**。"""
        plan = build_empty_plan(req(travel_days=3))
        assert len(plan.days) == 3
        for d in plan.days:
            assert d.attractions == [], f"第 {d.day_index} 天有景点: {d.attractions}"
            assert d.meals == [], f"第 {d.day_index} 天有餐饮: {d.meals}"
            assert d.hotel is None, "不该推荐酒店 —— 那是编的"

    def test_不能有编造的预算(self):
        """没有行程就没有预算。给个 0 元的假预算也是编造。"""
        assert build_empty_plan(req()).budget is None

    def test_天气是空的(self):
        assert build_empty_plan(req()).weather_info == []


# ===========================================================================
# 保留的部分 —— 结构来自用户的请求,是事实
# ===========================================================================


class Test保留结构:
    def test_天数和请求一致(self):
        assert len(build_empty_plan(req(travel_days=5)).days) == 5

    def test_日期从起始日逐日连续(self):
        plan = build_empty_plan(req(start_date="2026-10-01", travel_days=3))
        assert [d.date for d in plan.days] == ["2026-10-01", "2026-10-02", "2026-10-03"]
        assert [d.day_index for d in plan.days] == [0, 1, 2]

    def test_交通和住宿照抄请求(self):
        plan = build_empty_plan(req(transportation="自驾", accommodation="民宿"))
        assert plan.days[0].transportation == "自驾"
        assert plan.days[0].accommodation == "民宿"

    def test_城市和日期区间照抄(self):
        plan = build_empty_plan(req(city="成都", start_date="2026-10-03", end_date="2026-10-05"))
        assert plan.city == "成都"
        assert plan.start_date == "2026-10-03"
        assert plan.end_date == "2026-10-05"

    def test_产物仍然符合_TripPlan_契约(self):
        """降级产物也得能被 schema 校验 —— 否则前端 / 落库那一步会炸。"""
        TripPlan.model_validate(build_empty_plan(req()).model_dump(mode="json"))


# ===========================================================================
# 失败说明 —— 用户唯一能看到的解释
# ===========================================================================


class Test失败说明:
    def test_原因写进去了(self):
        plan = build_empty_plan(req(), reason="模型返回的内容不是合法行程")
        assert "模型返回的内容不是合法行程" in plan.overall_suggestions

    def test_明说这不是用户的行程(self):
        s = build_empty_plan(req(), reason="x").overall_suggestions
        assert "请勿据此出行" in s, "必须明确劝阻用户按它出行"

    def test_不能装作规划好了(self):
        """旧实现写的是「这是为您规划的…行程」—— 那是谎话。"""
        s = build_empty_plan(req(), reason="x").overall_suggestions
        assert "这是为您规划的" not in s
        assert "未能生成" in s

    def test_没有原因时也能用(self):
        s = build_empty_plan(req()).overall_suggestions
        assert "未能生成" in s

    def test_不换行(self):
        """前端是 `{{ tripPlan.overall_suggestions }}` 直接插值,换行会被折叠。"""
        s = build_empty_plan(req(), reason="x").overall_suggestions
        assert "\n" not in s


# ===========================================================================
# 不许抛异常 —— 它是整条链路最后的兜底
# ===========================================================================


class Test绝不抛异常:
    """降级函数是整条链路最后的兜底,它自己再抛就等于 500。

    ⚠️ 这里**只测够得着的输入**。一开始还写了「天数超范围」「天数是负数」两条,
    结果发现 `TripRequest.travel_days` 上有 `ge=1, le=30` —— 那种请求在构造
    pydantic 模型那一步就被拒了,**根本传不到 build_empty_plan**。
    那两条测试实际测的是 pydantic,不是被测函数,所以删了;
    对应的 `min(travel_days, 30)` 防线也一并从实现里删掉(不可达的代码)。

    `start_date` 不一样:它只是普通字符串,没有格式约束,**空串是合法输入**,
    而 `date.fromisoformat("")` 会抛 —— 所以这几条是够得着的。
    """

    def test_起始日期是空串(self):
        plan = build_empty_plan(req(start_date=""))
        assert plan.days == [], "解析不出来就不排天,而不是崩"

    def test_起始日期是乱码(self):
        assert build_empty_plan(req(start_date="不是日期")).days == []

    def test_起始日期带时间后缀也能解析(self):
        """高德/前端有时会带上时间,取前 10 位就够。"""
        assert len(build_empty_plan(req(start_date="2026-10-01T00:00:00")).days) == 3
