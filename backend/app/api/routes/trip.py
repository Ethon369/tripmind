"""旅行规划API路由"""

import json
import logging
import re
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from ...models.schemas import (
    TripRequest,
    TripPlan,
    TripPlanResponse,
    ErrorResponse,
    PlanListResponse,
    PlanDetailResponse,
    PlanSummary,
)
from ..errors import handle_service_errors, to_http_error
from ..deps import require_admin
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...observability import collect_usage, make_observer
from ...services.llm_service import get_llm
from ...store import get_plan_store

router = APIRouter(prefix="/trip", tags=["旅行规划"])

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 哪些端点要管理口令(与 knowledge.py 的规则一致:写操作要,读操作不要)
# ---------------------------------------------------------------------------
#
#   要口令:PUT    /plans/{plan_id}   覆盖别人的行程内容
#          DELETE /plans/{plan_id}   删掉别人的行程
#
#   不要口令:POST /plan              核心功能,不加就等于网站不能用了
#            GET  /plans             列表 —— 前端「历史行程」页要用
#            GET  /plans/{plan_id}   详情 —— **分享链接用的就是这个接口**,
#                                    加口令等于分享功能失效(产品设定如此)
#            GET  /health
#
# ⚠️ 这里有个**已知且有意接受**的取舍,写清楚免得以后被当成疏漏:
#
#   `GET /plans` 会把所有行程的 id 列出来。plan_id 本身是 uuid4().hex
#   (32 位随机)猜不到,但这个列表把随机性抹平了 —— 也就是说
#   "拿到列表 → 读/改/删任意行程"这条链是通的,只是后半段现在被口令挡住了。
#
#   为什么不干脆把 `GET /plans` 也加上口令?
#   因为它就是前端「历史行程」页的数据源,加了口令这个页面就废了。
#   而项目**没有用户体系**(行程靠链接分享是产品设定),所以也说不出
#   "只列你自己的" —— 服务端根本不知道你是谁。
#
#   结论:读接口保持公开、写接口全部上锁。要彻底解决就得引入用户体系,
#   那超出这个项目的范围了。


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

        # 收集这次生成用到的知识出处(结构化)。
        # 用**局部列表**而不是 agent 的实例属性 —— agent 是模块级单例,
        # 两个请求并发时实例属性会互相覆盖,拿到别人的检索结果。
        knowledge_hits: list[dict] = []

        t0 = time.perf_counter()
        with collect_usage() as usage:
            trip_plan = agent.plan_trip(
                request, observer=observer, knowledge_sink=knowledge_hits
            )
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
        # 知识出处单独落一张表。放在 finish_plan **之后**、且单独 try ——
        # 它失败不该影响"行程已经存好了"这个事实。
        if knowledge_hits:
            try:
                n = store.save_knowledge(plan_id, knowledge_hits)
                print(f"📚 知识出处已保存: {n} 条")
            except Exception as kn_err:
                print(f"⚠️  知识出处保存失败(不影响行程): {kn_err}")
        print(f"💾 已保存: {plan_id} (status={status})")

        # 降级时 success=False。
        #
        # `success` 的含义是「data 是一份**能用的**行程」——`Result.vue` 的注释
        # 就是这么写的(用它区分 status='running')。降级拿到的是一份**空框架**
        # (见 agents/fallback.py:景点和餐饮都是空的),不是能用的行程,
        # 所以这里必须是 False。
        #
        # 为什么敢直接改:Home.vue 和 Result.vue **两边都已经处理** success=False
        # 的分支(else 里 message.error),所以不需要动前端。用户会看到
        # 「生成失败」而不是「生成成功」+ 一个空页面 —— 后者自相矛盾。
        #
        # ⚠️ 降级**不等于失败**:记录照样落库(status='fallback'),历史页看得到,
        # 只是它明确标着自己没内容。
        if observer.fallback_used:
            # 把**失败原因**也带进 message。
            #
            # 原来只写"请稍后重试" —— 那句话在某些原因下是**错的建议**:
            # LLM 连不上、key 失效、代理配错,重试多少次都一样。
            # 真实踩过:用户看到这句话以为程序出 bug 了,而根因在后端日志里
            # (httpx 把启动那一刻的系统代理记死在客户端里,代理关了就再也连不上)。
            #
            # message 是用户在**弹窗里唯一能看到的东西**(降级时 success=False,
            # 前端不会跳结果页),所以原因必须出现在这儿。
            why = (observer.warnings or ["原因未记录"])[0]
            print("⚠️  本次降级,返回 success=False(空框架,未编造内容)\n")
            return TripPlanResponse(
                success=False,
                message=f"本次未能生成行程内容({why})。已存为空白记录,可在「历史行程」里查看",
                data=trip_plan,
                plan_id=plan_id,
            )

        print("✅ 旅行计划生成成功,准备返回响应\n")
        return TripPlanResponse(
            success=True,
            message="旅行计划生成成功",
            data=trip_plan,
            plan_id=plan_id
        )

    except Exception as e:
        # ⚠️ 这个端点**不能**用 @handle_service_errors:它除了构造错误响应,
        # 还要把库里那条 running 记录标成 error(否则它会永远挂在"生成中")。
        # 所以自己 catch 做清理,只有「错误响应怎么构造」交给统一模块 ——
        # 那是唯一会产生信息泄漏的一步(以前是 detail=f"...{str(e)}")。
        print(f"❌ 生成旅行计划失败: {type(e).__name__}: {e}")
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
        raise to_http_error("生成旅行计划", e) from e


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
        # 503 而不是 500:这个语义是给监控/负载均衡看的("实例不可用"),
        # 与普通业务异常不是一回事。所以这里不用 handle_service_errors。
        #
        # 仍然不回 `str(e)`:它可能是任何库的报错原文。完整堆栈进日志,
        # 对外只给异常类型 —— 异常类型足以区分是配置缺失(ValueError)
        # 还是框架内部问题(AttributeError 等)。
        logger.exception("健康检查失败: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"服务不可用: {type(e).__name__}"
        ) from e


# ===========================================================================
# 意图解析：从用户的一句话里提取结构化信息
# ===========================================================================

# 与前端 constants/tripOptions.ts 保持一致。模型给出的选项必须落在白名单里 ——
# 否则它会自由发挥出"商务酒店""地铁"这类后端不认的值。
_ALLOWED_PREFS = ["历史文化", "自然风光", "美食", "购物", "艺术", "休闲"]
_ALLOWED_TRANSPORT = ["公共交通", "自驾", "步行", "混合"]
_ALLOWED_STAY = ["经济型酒店", "舒适型酒店", "豪华酒店", "民宿"]


class ParseIntentRequest(BaseModel):
    text: str = Field(..., description="用户的一句话", example="我想去重庆玩五天")


class ParsedIntent(BaseModel):
    city: str = Field(default="", description="目的地；没识别出来就是空串")
    days: Optional[int] = Field(default=None, description="天数 1-30；没识别出来就是 null")
    preferences: List[str] = Field(default_factory=list)
    transportation: Optional[str] = None
    accommodation: Optional[str] = None
    greeting: str = Field(default="", description="一句自然语言的回应")


class ParseIntentResponse(BaseModel):
    success: bool
    # llm = 模型解析成功；unavailable = 模型不可用，调用方应回退到自己的规则解析
    source: str
    message: str = ""
    data: ParsedIntent


_PARSE_PROMPT = """你是旅行规划助手。请从用户的一句话里提取结构化的出行信息。

用户说：{text}

只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。字段如下：
{{
  "city": "目的地城市或地区名(只写地名，2-6 个字)；没提到就填空字符串",
  "days": 出行天数(1-30 的整数)；没提到就填 null,
  "preferences": 从 ["历史文化","自然风光","美食","购物","艺术","休闲"] 里选出用户提到的；没提到就填空数组,
  "transportation": 从 ["公共交通","自驾","步行","混合"] 里选；没提到就填 null,
  "accommodation": 从 ["经济型酒店","舒适型酒店","豪华酒店","民宿"] 里选；没提到就填 null,
  "greeting": "一句 25-45 字的回应:先复述你理解到的需求,再点出这座城市的一个特色。语气自然、像朋友说话,不要用 emoji"
}}

注意:
- 用户可能用中文数字(比如「五天」),请转成阿拉伯数字
- 不要臆造用户没提到的信息;不确定的字段就留空
"""


def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 返回里抠出 JSON 对象。

    容忍三种情况：裸 JSON、```json 代码块包裹、前后夹着解释性文字。
    """
    s = (text or "").strip()
    if not s:
        return None

    # 去掉 markdown 代码块包装
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"```\s*$", "", s).strip()

    # 取第一个 { 到最后一个 } —— 模型偶尔会在 JSON 前后多写一两句
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None

    try:
        parsed = json.loads(s[i : j + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _clamp_days(value: object) -> Optional[int]:
    """把模型给的天数收进 1-30，与 travel_days 的约束保持一致。

    超范围时宁可当成"没识别出来"，也不要返回一个必然被 422 打回的值。
    """
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 30 else None


def _pick(value: object, allowed: List[str]) -> Optional[str]:
    """只接受白名单内的值，避免模型自由发挥出后端不认的选项。"""
    s = str(value or "").strip()
    return s if s in allowed else None


@router.post(
    "/parse",
    response_model=ParseIntentResponse,
    summary="解析用户意图",
    description=(
        "从用户的一句话里提取目的地、天数、偏好等结构化信息，并生成一句回应。"
        "**这是增强而不是依赖** —— 模型不可用时返回 source='unavailable'，"
        "调用方应回退到自己的规则解析。"
    ),
)
def parse_intent(request: ParseIntentRequest):
    """解析用户的一句话。

    ⚠️ 这里**刻意不包 collect_usage()**。

    `plan_trip` 用它把一次运行里所有 LLM 调用计入同一条用量记录,因为那是一次
    "生成行程"的完整动作。而这里只是一次轻量的字段提取,和生成是两件事 ——
    计进去会让那次生成的成本统计虚高(用户看到"生成一次花 5 分钱",其中一部分
    其实是进创建页时解析意图花的)。

    代价是这次调用的 token 不进统计。它只有几百 token,可以接受。
    """
    text = (request.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")
    if len(text) > 500:
        text = text[:500]

    empty = ParsedIntent()

    try:
        llm = get_llm()
        content = llm.invoke(
            [{"role": "user", "content": _PARSE_PROMPT.format(text=text)}],
            temperature=0,  # 要稳定的结构化输出,不要创造性
            # ⚠️ 这里给到 2000 而不是 500,是实测踩出来的:
            # 用 500 时简单输入(「我想去重庆玩五天」)能出结果,稍复杂的
            # (「国庆想去云南待7天,喜欢自然风光和美食」)返回的是**空字符串** ——
            # token 被用尽,content 就空了。这个模型有思维链开销,
            # 留给"最终答案"的额度必须比答案本身大得多。
            max_tokens=2000,
        )
    except Exception as exc:
        # 模型不可用(没配 key / 超时 / 代理挂了)时**不能让前端跟着挂** ——
        # 前端本来就有规则解析,这里如实报告,由它决定回退。
        print(f"⚠️  意图解析不可用: {type(exc).__name__}: {exc}")
        return ParseIntentResponse(
            success=False,
            source="unavailable",
            message=f"模型不可用: {type(exc).__name__}",
            data=empty,
        )

    raw = _extract_json(content)
    if raw is None:
        print(f"⚠️  意图解析返回的不是 JSON: {content[:200]}")
        return ParseIntentResponse(
            success=False,
            source="unavailable",
            message="模型返回格式异常",
            data=empty,
        )

    raw_prefs = raw.get("preferences")
    prefs = (
        [str(p) for p in raw_prefs if str(p) in _ALLOWED_PREFS]
        if isinstance(raw_prefs, list)
        else []
    )

    data = ParsedIntent(
        city=str(raw.get("city") or "").strip()[:20],
        days=_clamp_days(raw.get("days")),
        preferences=prefs[:3],  # 上限 3,与前端 LIMITS.MAX_PREFERENCES 对齐
        transportation=_pick(raw.get("transportation"), _ALLOWED_TRANSPORT),
        accommodation=_pick(raw.get("accommodation"), _ALLOWED_STAY),
        greeting=str(raw.get("greeting") or "").strip()[:120],
    )

    return ParseIntentResponse(success=True, source="llm", message="解析成功", data=data)


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
@handle_service_errors("读取历史行程")
def list_plans(limit: int = 50, offset: int = 0):
    """历史行程列表。

    Args:
        limit: 返回条数上限(1-200)
        offset: 偏移量,用于分页
    """
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

    # 知识出处一起返回。没开 RAG / 没命中 / 记录不存在时,store 返回空列表 ——
    # 前端据此决定显不显示那张卡片,不需要额外判断。
    knowledge = store.get_knowledge(plan_id)

    if plan is None:
        return PlanDetailResponse(
            success=False,
            message=f"该行程尚未生成完成(状态: {row['status']})",
            data=None,
            meta=meta,
            knowledge=knowledge,
        )

    return PlanDetailResponse(
        success=True, message="获取成功", data=plan, meta=meta, knowledge=knowledge
    )


@router.put(
    "/plans/{plan_id}",
    response_model=PlanDetailResponse,
    summary="更新行程内容",
    description="保存前端编辑后的行程(增删景点、调整顺序)。不改动成本统计。",
    dependencies=[Depends(require_admin)],
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
    description="从历史记录中永久删除一个行程",
    dependencies=[Depends(require_admin)],
)
def delete_plan(plan_id: str):
    """删除行程。"""
    if not get_plan_store().delete_plan(plan_id):
        raise HTTPException(status_code=404, detail="行程不存在或已被删除")
    return {"success": True, "message": "已删除", "data": {"id": plan_id}}

