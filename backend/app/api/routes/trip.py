"""旅行规划API路由"""

from fastapi import APIRouter, HTTPException
from ...models.schemas import (
    TripRequest,
    TripPlanResponse,
    ErrorResponse
)
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...observability import collect_usage, make_observer

router = APIRouter(prefix="/trip", tags=["旅行规划"])


@router.post(
    "/plan",
    response_model=TripPlanResponse,
    summary="生成旅行计划",
    description="根据用户输入的旅行需求,生成详细的旅行计划"
)
def plan_trip(request: TripRequest):
    """
    生成旅行计划

    注意:这里刻意用同步的 def 而不是 async def。

    plan_trip() 内部是一次要跑几十秒的阻塞调用(4 次 LLM + MCP 工具)。
    如果写成 async def,FastAPI 会在事件循环里直接执行它 —— 在这几十秒内
    整个服务被卡死,/health、/api/poi/photo、历史列表全部无响应。
    写成 def,FastAPI 会自动把它丢进线程池,不阻塞事件循环。

    Args:
        request: 旅行请求参数

    Returns:
        旅行计划响应
    """
    try:
        print(f"\n{'='*60}")
        print(f"📥 收到旅行规划请求:")
        print(f"   城市: {request.city}")
        print(f"   日期: {request.start_date} - {request.end_date}")
        print(f"   天数: {request.travel_days}")
        print(f"{'='*60}\n")

        # 获取Agent实例
        print("🔄 获取多智能体系统实例...")
        agent = get_trip_planner_agent()

        # 生成旅行计划(observer 负责记录各阶段耗时与是否降级)
        # collect_usage() 把这次运行里所有 LLM 调用计入同一个 collector,
        # run_end() 落盘时会把用量一并写进日志。
        print("🚀 开始生成旅行计划...")
        observer = make_observer(enabled=True)
        with collect_usage() as usage:
            trip_plan = agent.plan_trip(request, observer=observer)

        print(
            f"💰 本次用量: {usage.total_tokens} tokens "
            f"(入 {usage.prompt_tokens} / 出 {usage.completion_tokens}), "
            f"花费 {usage.cost_cny} 元, {usage.llm_calls} 次调用 [{usage.usage_source}]"
        )

        print("✅ 旅行计划生成成功,准备返回响应\n")

        return TripPlanResponse(
            success=True,
            message="旅行计划生成成功",
            data=trip_plan
        )

    except Exception as e:
        print(f"❌ 生成旅行计划失败: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"生成旅行计划失败: {str(e)}"
        )


@router.get(
    "/health",
    summary="健康检查",
    description="检查旅行规划服务是否正常"
)
def health_check():
    """健康检查

    同样使用同步 def —— get_trip_planner_agent() 首次调用会构建整个
    多智能体系统(含 MCP 工具连接),是阻塞操作。
    """
    try:
        # 检查Agent是否可用
        agent = get_trip_planner_agent()

        # 注意:MultiAgentTripPlanner 没有 .agent 属性。
        # 之前写的 agent.agent.name 会抛 AttributeError,被 except 捕获后
        # 恒返回 503 —— 这个端点其实一直是坏的。
        agents = {
            "attraction": agent.attraction_agent,
            "weather": agent.weather_agent,
            "hotel": agent.hotel_agent,
            "planner": agent.planner_agent,
        }

        return {
            "status": "healthy",
            "service": "trip-planner",
            "agents": {name: a.name for name, a in agents.items()},
            # SimpleAgent.list_tools() 是公开 API,返回该 agent 注册的工具
            "agent_tools": {name: len(a.list_tools()) for name, a in agents.items()},
            # MCPTool 没有公开的 list_tools();_available_tools 是它在 __init__
            # 里做完工具发现后填充的私有属性。用 getattr 兜底,免得将来框架
            # 内部改名时连健康检查本身都挂掉。
            "mcp_tools_count": len(getattr(agent.amap_tool, "_available_tools", None) or []),
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"服务不可用: {str(e)}"
        )

