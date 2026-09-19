# ===========================================================================
# `_parse_response` 从模型输出里抠 JSON
# ===========================================================================
#
# 这里集中钉住一类**模型完全遵守了指令、却仍然被判失败**的情况。
#
# 实测到的真实案例:planner 输出了 3603 字符的合法 JSON(3 天行程),
# 末尾多写了一个收尾的 ``` —— 它以为自己在关闭一个代码块。
# 结果整个行程被判成"解析失败"降级成空白页,而行程内容其实是好的。
#
# 这类失败**不会**出现在日志里当成错误:它走的是 fallback 分支,
# `ok=True / fallback_used=True`,只有去翻 `warnings` 才知道。

import pytest

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import TripRequest


def _valid_plan_json() -> str:
    """一个能通过 TripPlan 校验的最小行程。"""
    return (
        '{"city":"北京","start_date":"2026-10-01","end_date":"2026-10-03",'
        '"days":[{"date":"2026-10-01","day_index":0,"description":"故宫",'
        '"transportation":"地铁","accommodation":"经济型",'
        '"hotel":{"name":"某酒店","address":"某地址",'
        '"location":{"longitude":116.39,"latitude":39.91},'
        '"price_range":"300-500元","rating":"4.5","distance":"2公里",'
        '"type":"经济型酒店","estimated_cost":400},'
        '"attractions":[{"name":"故宫","address":"景山前街4号",'
        '"location":{"longitude":116.39,"latitude":39.91},'
        '"visit_duration":120,"description":"明清皇宫","category":"古迹",'
        '"ticket_price":60}],'
        '"meals":[{"type":"breakfast","name":"早餐","description":"包子",'
        '"estimated_cost":30}]}],'
        '"weather_info":[{"date":"2026-10-01","day_weather":"晴",'
        '"night_weather":"多云","day_temp":25,"night_temp":15,'
        '"wind_direction":"南风","wind_power":"1-3级"}],'
        '"overall_suggestions":"注意保暖","budget":{"total_attractions":60,'
        '"total_hotels":400,"total_meals":30,"total_transportation":20,'
        '"total":510}}'
    )


def _request() -> TripRequest:
    return TripRequest(city="北京", start_date="2026-10-01",
                       end_date="2026-10-03", travel_days=3,
                       transportation="公共交通", accommodation="经济型酒店")


def _parse(text: str):
    """`_parse_response` 只用 self 做方法查找,不需要构造 agent。

    `__init__` 会连 MCP、起 uvx 子进程(实测 20 秒以上),单测里不能碰。
    """
    agent = object.__new__(MultiAgentTripPlanner)
    return agent._parse_response(text, _request())


class Test抠JSON的容错:
    def test_裸JSON(self):
        plan = _parse(_valid_plan_json())
        assert plan.city == "北京"
        assert len(plan.days) == 1
        assert plan.days[0].attractions[0].name == "故宫"

    def test_json代码块包裹(self):
        plan = _parse("好的,这是行程:\n```json\n" + _valid_plan_json() + "\n```")
        assert len(plan.days) == 1
        assert plan.days[0].attractions[0].name == "故宫"

    def test_无语言标记的代码块(self):
        plan = _parse("```\n" + _valid_plan_json() + "\n```")
        assert len(plan.days) == 1

    def test_JSON后面多一个收尾的反引号(self):
        """**这是实测踩到的那个。**

        模型输出了完整合法的 JSON,末尾多写了一个收尾的 ```。
        旧实现里 `"```" in response` 命中、然后在它之后找收尾找不到,
        切出来是空串 → `JSONDecodeError: Expecting value: char 0` → 降级。
        """
        plan = _parse(_valid_plan_json() + "\n```")
        assert len(plan.days) == 1, "JSON 是好的,不该因为末尾多了个 ``` 就降级"
        assert plan.days[0].attractions[0].name == "故宫"

    def test_前后都有解释性文字(self):
        plan = _parse("行程如下:\n" + _valid_plan_json() + "\n希望你喜欢!")
        assert len(plan.days) == 1

    def test_JSON前面有一句开场白(self):
        plan = _parse("好的,为您规划:\n" + _valid_plan_json())
        assert len(plan.days) == 1

    def test_真的没有JSON时仍然降级(self):
        """容错不能变成"什么都吃" —— 确实不是 JSON 时必须走降级,
        而不是抛异常炸掉整个生成流程。"""
        plan = _parse("抱歉,我无法完成这个请求。")
        # 降级方案是"按 travel_days 铺空的几天",不是抛异常。
        # 判据是 overall_suggestions 里带失败原因 —— 那是给用户看的。
        assert plan.city == "北京"
        assert "不是合法行程" in (plan.overall_suggestions or "")

    def test_空字符串不炸(self):
        plan = _parse("")
        assert "不是合法行程" in (plan.overall_suggestions or "")

    def test_JSON合法但字段不合法时降级(self):
        """结构对、语义不对(比如 days 是字符串)—— 不能 500。"""
        plan = _parse('{"city":"北京","days":"不是数组"}')
        assert "不是合法行程" in (plan.overall_suggestions or "")


class Test确认降级被标记:
    def test_解析失败会标记fallback(self):
        """降级必须在 observer 上留痕 —— 否则这次失败在日志里看不出来
        (`ok` 仍然是 True,只有 `fallback_used` 能区分)。"""
        from app.observability import NullObserver

        class _Rec(NullObserver):
            def __init__(self):
                self.reasons: list[str] = []

            def mark_fallback(self, reason: str) -> None:
                self.reasons.append(reason)

        obs = _Rec()
        agent = object.__new__(MultiAgentTripPlanner)
        agent._parse_response("完全不是 JSON", _request(), obs)
        assert obs.reasons, "降级了却没标记,日志里就查不到"
