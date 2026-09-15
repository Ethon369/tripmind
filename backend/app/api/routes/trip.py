"""旅行规划API路由"""

import time

from fastapi import APIRouter, HTTPException
from ...models.schemas import (
    TripRequest,
    TripPlan,
    TripPlanResponse,
    ErrorResponse,
    PlanListResponse,
    PlanDetailResponse,
    PlanSummary,
)
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...observability import collect_usage, make_observer
from ...store import get_plan_store

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
        store = get_plan_store()

        # 先落一条 status='running' 的记录再开始生成。
        # 这样 plan_id 提前确定,而且不存在"生成完了但没存住"的窗口
        # —— 中途崩了库里也留得下痕迹,而不是凭空消失。
        plan_id = store.create_running(request)
        print(f"🆔 plan_id: {plan_id}")

        print("🚀 开始生成旅行计划...")
        observer = make_observer(enabled=True)
        t0 = time.perf_counter()
        with collect_usage() as usage:
            trip_plan = agent.plan_trip(request, observer=observer)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        print(
            f"💰 本次用量: {usage.total_tokens} tokens "
            f"(入 {usage.prompt_tokens} / 出 {usage.completion_tokens}), "
            f"花费 {usage.cost_cny} 元, {usage.llm_calls} 次调用 [{usage.usage_source}]"
        )

        # 回填结果。降级过就标 fallback —— 这条记录会决定前端是否显示
        # "此行程数据不完整"的提示。
        status = "fallback" if observer.fallback_used else "ok"
        store.finish_plan(
            plan_id,
            trip_plan,
            status=status,
            warnings=observer.warnings,
            usage=usage.summary(),
            latency_ms=latency_ms,
        )
        print(f"💾 已保存: {plan_id} (status={status})")
        print("✅ 旅行计划生成成功,准备返回响应\n")

        return TripPlanResponse(
            success=True,
            message="旅行计划生成成功",
            data=trip_plan,
            plan_id=plan_id
        )

    except Exception as e:
        print(f"❌ 生成旅行计划失败: {str(e)}")
        import traceback
        traceback.print_exc()
        # 把库里那条 running 记录标成失败,免得它永远挂在"生成中"
        try:
            if "plan_id" in locals():
                store.finish_plan(
                    plan_id, None, status="error",
                    warnings=[f"{type(e).__name__}: {e}"],
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
        except Exception as save_err:
            print(f"⚠️ 标记失败状态时出错(不影响报错): {save_err}")
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


# ===========================================================================
# 历史行程
# ===========================================================================
# 这几个端点都是同步 def —— 里面是阻塞的 sqlite3 调用,
# FastAPI 会把它们丢进线程池,不占用事件循环。


@router.get(
    "/plans",
    response_model=PlanListResponse,
    summary="历史行程列表",
    description="按创建时间倒序返回已生成的行程,附带累计用量统计"
)
def list_plans(limit: int = 50, offset: int = 0):
    """历史行程列表。

    Args:
        limit: 返回条数上限(1-200)
        offset: 偏移量,用于分页
    """
    try:
        store = get_plan_store()
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))

        rows = store.list_plans(limit=limit, offset=offset)
        return PlanListResponse(
            success=True,
            message=f"共 {len(rows)} 条",
            data=[PlanSummary(**r) for r in rows],
            stats=store.stats(),
        )
    except Exception as e:
        print(f"❌ 读取历史行程失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取历史行程失败: {e}")


@router.get(
    "/plans/{plan_id}",
    response_model=PlanDetailResponse,
    summary="行程详情",
    description="按 id 取单个行程的完整内容。分享链接用的就是这个接口。"
)
def get_plan_detail(plan_id: str):
    """行程详情。

    注意 status='running' 的记录也会返回 —— 前端可以据此显示
    "该行程生成中断"。这与列表接口不同(列表默认隐藏 running)。
    """
    store = get_plan_store()
    row = store.get_plan(plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail="行程不存在或已被删除")

    plan = TripPlan(**row["plan"]) if row.get("plan") else None
    meta = PlanSummary(
        id=row["id"],
        title=row["title"],
        city=row["city"],
        start_date=row["start_date"],
        end_date=row["end_date"],
        travel_days=row["travel_days"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        total_tokens=row["total_tokens"],
        cost_cny=row["cost_cny"],
        latency_ms=row["latency_ms"],
        llm_calls=row["llm_calls"],
        usage_source=row["usage_source"],
        warnings=row["warnings"] or [],
    )

    if plan is None:
        return PlanDetailResponse(
            success=False,
            message=f"该行程尚未生成完成(状态: {row['status']})",
            data=None,
            meta=meta,
        )

    return PlanDetailResponse(success=True, message="获取成功", data=plan, meta=meta)


@router.put(
    "/plans/{plan_id}",
    response_model=PlanDetailResponse,
    summary="更新行程内容",
    description="保存前端编辑后的行程(增删景点、调整顺序)。不改动成本统计。"
)
def update_plan(plan_id: str, plan: TripPlan):
    """回写编辑后的行程。"""
    store = get_plan_store()
    if not store.update_plan_json(plan_id, plan):
        raise HTTPException(status_code=404, detail="行程不存在或已被删除")

    row = store.get_plan(plan_id)
    return PlanDetailResponse(
        success=True,
        message="已保存",
        data=TripPlan(**row["plan"]),
    )


@router.delete(
    "/plans/{plan_id}",
    summary="删除行程",
    description="从历史记录中永久删除一个行程"
)
def delete_plan(plan_id: str):
    """删除行程。"""
    if not get_plan_store().delete_plan(plan_id):
        raise HTTPException(status_code=404, detail="行程不存在或已被删除")
    return {"success": True, "message": "已删除", "data": {"id": plan_id}}

