"""降级行程 —— 宁可空,也不要假

**它解决什么问题**

LLM 输出解析失败时,管线得有东西返回 —— 不能抛 500 给用户。原来的做法是
现造一份"看起来正常"的行程(`trip_planner_agent._create_fallback_plan`):

    name=f"{city}景点{j+1}"                  ← 假景点名
    location=116.4 + i*0.01 + j*0.005       ← **写死的北京坐标**
    description=f"这是{city}的著名景点"        ← 假描述
    overall_suggestions="这是为您规划的…"      ← 谎称规划好了

后果**不是"用户拿到一份差行程",而是"用户拿到假行程却看不出来"**:
生成上海的计划会得到北京的坐标,schema 校验通过,前端照常渲染地图、
照常显示"旅行计划生成成功"。用户不会怀疑景点叫"上海景点1"有什么问题 ——
他会以为那就是这个景点的名字。

P5 的 baseline 实测这条路径**一次都没被触发**(10 条请求 `fallback_used` 全是 0),
但它一直都在,只等 LLM 哪天输出解析不出来。

**现在的规矩:结构可以留,内容一律不编**

| 元素 | 留不留 | 为什么 |
|---|---|---|
| 天数、日期 | **留** | 它们来自**用户的请求**,是事实 |
| 交通方式、住宿偏好 | **留** | 同上 |
| 景点 | **清空** | 编不出真的,就不放假的 |
| 餐饮 | **清空** | 「第1天早餐」看起来像**推荐**,不是空位 |
| 天气 | 空 | 本来就是空 |
| `overall_suggestions` | **写清失败** | 用户有权知道发生了什么 |

**为什么单独一个模块**

原来是 `MultiAgentTripPlanner` 的方法,要构造整个 agent(hello_agents +
MCPTool + LLM)才能调 —— 那样这条逻辑**没法单测**。抽成纯函数之后,
`tests/test_fallback.py` 直接构造一个 request 就能验证。

这很重要:因为 baseline 里降级**从没被触发过**,改完不能靠重跑 baseline
证明它好了,**只能靠单测**。
"""

from __future__ import annotations

from datetime import date, timedelta

from ..models.schemas import DayPlan, TripPlan, TripRequest


def _parse_start(value: object) -> date | None:
    """尽力解析起始日期。解析不出来返回 None,不抛。

    降级函数**绝不能再抛异常** —— 它是整条链路最后的兜底。
    这个分支是**够得着的**:`TripRequest.start_date` 只是普通字符串,没有格式约束,
    空串是合法输入,而 `date.fromisoformat("")` 会抛。
    """
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _failure_note(request: TripRequest, reason: str) -> str:
    """写进 `overall_suggestions` 的失败说明。

    这是**用户唯一能看到的解释**,所以要写白话、说清楚"这不是你的行程"。
    不用换行符:前端是 `{{ tripPlan.overall_suggestions }}` 直接插值,
    换行会被 HTML 折叠掉,不如用标点连成一句。
    """
    parts = [
        f"本次未能生成{request.city}的行程内容,下面是空的框架。",
        "这不是您的行程 —— 里面没有任何景点、餐饮或酒店,请勿据此出行。",
    ]
    if reason:
        parts.append(f"失败原因:{reason}。")
    parts.append("建议稍后重试,或换个城市、日期再试一次。")
    return "".join(parts)


def build_empty_plan(request: TripRequest, reason: str = "") -> TripPlan:
    """造一份**不编造任何内容**的降级行程。

    Args:
        request: 原始请求
        reason: 失败原因,会写进 `overall_suggestions` 给用户看

    Returns:
        天数/日期与请求一致、但景点和餐饮全空的 `TripPlan`。
        起始日期解析不出来时返回 `days=[]` 而不是抛异常。
    """
    start = _parse_start(request.start_date)
    days: list[DayPlan] = []

    # 这里**不做** travel_days 的范围钳制。
    #
    # 一开始写了个 `min(travel_days, 30)` 的防线,写测试时才发现它**够不着**:
    # `TripRequest.travel_days` 上有 `ge=1, le=30`,越界的请求在构造 pydantic
    # 模型那一步就被拒了,根本传不到这儿。留一段永远不执行、也测不到的代码,
    # 只会让人以为这里有保护 —— 按本项目的规矩(见 CLAUDE.md「测试全绿不等于
    # 测试有效」),没被覆盖的逻辑等于不存在,所以删掉。
    if start is not None:
        for i in range(request.travel_days):
            days.append(
                DayPlan(
                    date=(start + timedelta(days=i)).isoformat(),
                    day_index=i,
                    # 「第 N 天」是事实(用户要的就是 N 天);「行程」两个字
                    # 会让人以为下面该有内容,所以补一句"未生成"
                    description=f"第 {i + 1} 天(未生成内容)",
                    transportation=request.transportation,
                    accommodation=request.accommodation,
                    attractions=[],  # ← 不编造。前端对空列表显示「暂无数据」
                    meals=[],
                    hotel=None,
                )
            )

    return TripPlan(
        city=request.city,
        start_date=request.start_date,
        end_date=request.end_date,
        days=days,
        weather_info=[],
        budget=None,
        overall_suggestions=_failure_note(request, reason),
    )
