"""多智能体旅行规划系统"""

import concurrent.futures as cf
import contextvars
import json
import re
from typing import Dict, Any, List
from hello_agents import SimpleAgent
from hello_agents.tools import MCPTool
from ..services.llm_service import get_llm
from ..services.knowledge_service import format_context, get_knowledge_service
from ..services.mcp_launcher import build_mcp_env, resolve_uvx_command
from ..models.schemas import TripRequest, TripPlan
from ..config import get_settings, rag_enabled
from ..observability import NullObserver
from .fallback import build_empty_plan

# ============ Agent提示词 ============

ATTRACTION_AGENT_PROMPT = """你是景点搜索专家。你的任务是根据城市和用户偏好搜索合适的景点。

**重要提示:**
你必须使用工具来搜索景点!不要自己编造景点信息!

**工具调用格式:**
使用maps_text_search工具时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_text_search:keywords=景点关键词,city=城市名]`

**示例:**
用户: "搜索北京的历史文化景点"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=历史文化,city=北京]

用户: "搜索上海的公园"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=公园,city=上海]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 参数用逗号分隔
"""

WEATHER_AGENT_PROMPT = """你是天气查询专家。你的任务是查询指定城市的天气信息。

**重要提示:**
你必须使用工具来查询天气!不要自己编造天气信息!

**工具调用格式:**
使用maps_weather工具时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_weather:city=城市名]`

**示例:**
用户: "查询北京天气"
你的回复: [TOOL_CALL:amap_maps_weather:city=北京]

用户: "上海的天气怎么样"
你的回复: [TOOL_CALL:amap_maps_weather:city=上海]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
"""

HOTEL_AGENT_PROMPT = """你是酒店推荐专家。你的任务是根据城市和景点位置推荐合适的酒店。

**重要提示:**
你必须使用工具来搜索酒店!不要自己编造酒店信息!

**工具调用格式:**
使用maps_text_search工具搜索酒店时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_text_search:keywords=酒店,city=城市名]`

**示例:**
用户: "搜索北京的酒店"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=酒店,city=北京]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 关键词使用"酒店"或"宾馆"
"""

PLANNER_AGENT_PROMPT = """你是行程规划专家。你的任务是根据景点信息和天气信息,生成详细的旅行计划。

请严格按照以下JSON格式返回旅行计划:
```json
{"city":"城市名称","start_date":"YYYY-MM-DD","end_date":"YYYY-MM-DD","days":[{"date":"YYYY-MM-DD","day_index":0,"description":"第1天行程概述","transportation":"交通方式","accommodation":"住宿类型","hotel":{"name":"酒店名称","address":"酒店地址","location":{"longitude":116.397128,"latitude":39.916527},"price_range":"300-500元","rating":"4.5","distance":"距离景点2公里","type":"经济型酒店","estimated_cost":400},"attractions":[{"name":"景点名称","address":"详细地址","location":{"longitude":116.397128,"latitude":39.916527},"visit_duration":120,"description":"景点描述","category":"景点类别","ticket_price":60}],"meals":[{"type":"breakfast","name":"早餐推荐","description":"早餐描述","estimated_cost":30},{"type":"lunch","name":"午餐推荐","description":"午餐描述","estimated_cost":50},{"type":"dinner","name":"晚餐推荐","description":"晚餐描述","estimated_cost":80}]}],"weather_info":[{"date":"YYYY-MM-DD","day_weather":"晴","night_weather":"多云","day_temp":25,"night_temp":15,"wind_direction":"南风","wind_power":"1-3级"}],"overall_suggestions":"总体建议","budget":{"total_attractions":180,"total_hotels":1200,"total_meals":480,"total_transportation":200,"total":2060}}
```

⚠️ **上面的示例是「压缩格式」,请照这个样子输出 —— 整个 JSON 写在一行里,
不要缩进、不要为了好看而换行。** 这不是风格偏好:缩进和换行会占掉
大量输出 token(实测约 15%),而它们对解析结果毫无影响。
本项目一次生成的输出直接决定用户等多久 —— 输出慢,人就会走。

**重要提示:**
1. weather_info数组必须包含每一天的天气信息
2. 温度必须是纯数字(不要带°C等单位)
3. 每天安排2-3个景点
4. 考虑景点之间的距离和游览时间
5. 每天必须包含早中晚三餐
6. 提供实用的旅行建议
7. **必须包含预算信息**:
   - 景点门票价格(ticket_price)
   - 餐饮预估费用(estimated_cost)
   - 酒店预估费用(estimated_cost)
   - 预算汇总(budget)包含各项总费用
8. **文字要短**:每条 `description` 控制在 30 字以内,`overall_suggestions`
   控制在 80 字以内。用户要看的是"今天去哪、怎么走",不是导游词。
   —— 这些字数同样直接换成用户的等待时间。
"""

# ⚠️ 上面 `PLANNER_AGENT_PROMPT` 里那段 JSON **刻意不换行**。
#    改它的时候不要为了源码好看去格式化它:模型会模仿示例的排版,
#    示例缩进 → 输出缩进 → 约 15% 的输出 token 花在空白上。
#    (Python 的三引号字符串支持隐式拼接,但你拆行就会把换行写进 JSON。)


class MultiAgentTripPlanner:
    """多智能体旅行规划系统"""

    def __init__(self):
        """初始化多智能体系统"""
        print("🔄 开始初始化多智能体旅行规划系统...")

        try:
            settings = get_settings()
            self.llm = get_llm()

            # 创建共享的MCP工具(只创建一次)
            print("  - 创建共享MCP工具...")
            self.amap_tool = MCPTool(
                name="amap",
                description="高德地图服务",
                # 绝对路径找 uvx —— 不靠 PATH,所以没激活 venv 也能起来。
                # 详见 mcp_launcher.py 的说明。
                server_command=resolve_uvx_command(),
                # build_mcp_env 而不是裸 dict:框架**不从 os.environ 继承**,
                # 必须把 UV_* 显式传下去,否则 uvx 退回官方源慢到像卡死。
                # 详见 mcp_launcher.py。
                env=build_mcp_env(settings.amap_api_key),
                auto_expand=True
            )
            self.amap_tool.expandable=True

            # 创建景点搜索Agent
            print("  - 创建景点搜索Agent...")
            self.attraction_agent = SimpleAgent(
                name="景点搜索专家",
                llm=self.llm,
                system_prompt=ATTRACTION_AGENT_PROMPT
            )
            self.attraction_agent.add_tool(self.amap_tool)

            # 创建天气查询Agent
            print("  - 创建天气查询Agent...")
            self.weather_agent = SimpleAgent(
                name="天气查询专家",
                llm=self.llm,
                system_prompt=WEATHER_AGENT_PROMPT
            )
            self.weather_agent.add_tool(self.amap_tool)

            # 创建酒店推荐Agent
            print("  - 创建酒店推荐Agent...")
            self.hotel_agent = SimpleAgent(
                name="酒店推荐专家",
                llm=self.llm,
                system_prompt=HOTEL_AGENT_PROMPT
            )
            self.hotel_agent.add_tool(self.amap_tool)

            # 创建行程规划Agent(不需要工具)
            print("  - 创建行程规划Agent...")
            self.planner_agent = SimpleAgent(
                name="行程规划专家",
                llm=self.llm,
                system_prompt=PLANNER_AGENT_PROMPT
            )

            print(f"✅ 多智能体系统初始化成功")
            print(f"   景点搜索Agent: {len(self.attraction_agent.list_tools())} 个工具")
            print(f"   天气查询Agent: {len(self.weather_agent.list_tools())} 个工具")
            print(f"   酒店推荐Agent: {len(self.hotel_agent.list_tools())} 个工具")

        except Exception as e:
            print(f"❌ 多智能体系统初始化失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise
    
    def plan_trip(
        self,
        request: TripRequest,
        observer: NullObserver | None = None,
        knowledge_sink: list[dict] | None = None,
        user_id: str | None = None,
    ) -> TripPlan:
        """
        使用多智能体协作生成旅行计划

        Args:
            request: 旅行请求
            observer: 可选的运行观察者,用于记录各阶段耗时与 fallback 情况。
                      不传时使用 NullObserver(所有钩子为空操作),行为与
                      加埋点之前完全一致。
            knowledge_sink: 可选的列表。传了的话,这次检索到的知识出处
                      (结构化,含 source / heading_path / score / 内容片段)
                      会被填进去,供调用方落库、在详情页展示。

                      为什么用"调用方传列表"而不是给 TripPlan 加字段或改返回值:
                      - 加到 TripPlan 上会污染数据契约 —— 那是"行程内容",
                        而"它参考了什么"是元信息;
                      - 改返回值会破坏所有调用方(评测脚本也在用)。
                      传列表既显式又不侵入;而且列表是调用方的局部变量,
                      天然线程安全 —— 本类是模块级单例,不能用实例属性存这种东西。

        Returns:
            旅行计划
        """
        obs = observer or NullObserver()
        obs.run_start(request)

        # 清空 4 个 agent 的历史记录。
        # 本类是模块级单例,而 SimpleAgent.run() 每次都会把输入输出追加进
        # _history 且永不清理。不清的话,第 N 次请求的 prompt 里会塞进前
        # N-1 次的内容 —— 结果随调用顺序漂移、不可复现,而且 token 成本虚高。
        for _agent in (self.attraction_agent, self.weather_agent,
                       self.hotel_agent, self.planner_agent):
            _agent.clear_history()

        try:
            print(f"\n{'='*60}")
            print(f"🚀 开始多智能体协作规划旅行...")
            print(f"目的地: {request.city}")
            print(f"日期: {request.start_date} 至 {request.end_date}")
            print(f"天数: {request.travel_days}天")
            print(f"偏好: {', '.join(request.preferences) if request.preferences else '无'}")
            print(f"{'='*60}\n")

            # 步骤1-3: 三个专家 agent **并发**跑
            #
            # 它们之间**没有任何数据依赖**:各自的 query 只由 request 决定,
            # 谁先返回都不影响另外两个的结果。原来串行写只是因为"步骤1、2、3"
            # 读起来顺,不是因为有依赖。实测串行约 15 秒、并发 5 秒。
            #
            # ⚠️ 两个并发前提,都实测过,不是靠读代码推断的:
            #
            # 1. **共享的 `self.amap_tool` 可以并发。** 三个 agent 用的是同一个
            #    MCPTool 实例,而 `MCPTool.run()` 内部是 `async with MCPClient(...)`,
            #    每次调用**新建一个连接**(stdio 就是一个新的 uvx 子进程)、用完即关,
            #    所以实例层面无状态。仍然做了对照实验(`scripts/check_mcp_concurrency.py`):
            #    三个线程同时查北京/上海/成都,每份结果里只出现自己那个城市。
            #    **必须实测** —— 判断错了的症状是"景点张冠李戴",而行程里不会报错。
            #
            # 2. **`collect_usage()` 用 ContextVar,子线程默认继承不到。**
            #    不处理的话三个 agent 的 token 用量会**静默丢失**:历史页的成本
            #    显示 ¥0、llm_calls 少 3 次,而没有任何报错。所以每条任务都带上
            #    主线程上下文的**副本**去跑。
            #    ⚠️ 每份任务必须是**各自的副本**:`ctx.run()` 在同一个 Context
            #    对象上被两个线程同时进入会抛 RuntimeError("already entered")。
            #
            # 失败行为保持不变:任意一个 agent 抛异常,`fut.result()` 会重新抛出,
            # 由外层统一的 except 走降级 —— 不会因为并行而改变错误处理路径。
            print("🚀 步骤1-3: 景点 / 天气 / 酒店 三个专家并发查询...\n")
            responses = self._run_experts_parallel(
                obs,
                [
                    ("attractions", self.attraction_agent,
                     self._build_attraction_query(request)),
                    ("weather", self.weather_agent,
                     f"请查询{request.city}的天气信息"),
                    ("hotels", self.hotel_agent,
                     f"请搜索{request.city}的{request.accommodation}酒店"),
                ],
            )
            attraction_response = responses["attractions"]
            weather_response = responses["weather"]
            hotel_response = responses["hotels"]

            # 步骤3.5: 检索知识库(P7,可用 ENABLE_RAG 开关)
            #
            # 为什么在**这里**检索、而不是给 planner 挂一个 RAG 工具:
            #   - 检索时机确定(每次必发生),不会被 planner 的 max_tool_iterations
            #     预算挤掉 —— 挂成工具的话,模型可能这一轮忘了调
            #   - query / 分数 / 来源都能记进 observer,出问题时一眼看出是
            #     「检索坏了」还是「模型没用好」
            #   - 检索是纯 Python,不占模型的推理轮次
            knowledge_context = ""
            if self._rag_enabled():
                print("📚 步骤3.5: 检索知识库...")
                obs.stage_start("retrieval")
                knowledge_context = self._retrieve_knowledge(
                    request, obs, sink=knowledge_sink, user_id=user_id
                )
                obs.stage_end("retrieval")

            # 步骤4: 行程规划Agent整合信息生成计划
            print("📋 步骤4: 生成行程计划...")
            obs.stage_start("planning")
            planner_query = self._build_planner_query(
                request, attraction_response, weather_response, hotel_response,
                knowledge=knowledge_context,
            )
            planner_response = self.planner_agent.run(planner_query)
            obs.stage_end("planning")
            obs.stage_response("planning", planner_response)
            print(f"行程规划结果: {planner_response[:300]}...\n")

            # 解析最终计划
            trip_plan = self._parse_response(planner_response, request, obs)

            print(f"{'='*60}")
            print(f"✅ 旅行计划生成完成!")
            print(f"{'='*60}\n")

            obs.run_end(trip_plan, ok=True)
            return trip_plan

        except Exception as e:
            print(f"❌ 生成旅行计划失败: {str(e)}")
            import traceback
            traceback.print_exc()
            obs.mark_fallback(f"plan_trip 异常: {type(e).__name__}: {e}")
            # reason 会写进 overall_suggestions 给用户看 —— 所以要写成人话,
            # 不能只给个异常类名
            trip_plan = self._create_fallback_plan(
                request, reason=f"生成过程中出错({type(e).__name__}: {e})"
            )
            obs.run_end(trip_plan, ok=False, error=f"{type(e).__name__}: {e}")
            return trip_plan
    
    def _run_experts_parallel(
        self,
        obs: "NullObserver",
        tasks: list[tuple[str, Any, str]],
    ) -> dict[str, str]:
        """并发跑几个互不依赖的 agent,返回 `{阶段名: 响应}`。

        Args:
            tasks: `[(阶段名, agent, query), ...]`。阶段名同时用于 observer 的
                计时与响应记录 —— 与串行时写进 `data/runs/*.jsonl` 的字段一致,
                所以改并发**不会**让历史数据断掉、也不需要改分析脚本。

        为什么不干脆把所有阶段都并行:只有**彼此没有数据依赖**的才能并。
        planner 要读前三个的输出,知识库检索要给 planner 拼 prompt,
        这两个必须留在后面串行。
        """
        # 主线程上下文的副本 —— 让子线程也能看到 collect_usage() 设的
        # ContextVar,否则 token 计量会静默丢失。每份任务用**各自的副本**:
        # 同一个 Context 对象被两个线程同时 run() 会抛 RuntimeError。
        ctx = contextvars.copy_context()

        def _run_one(name: str, agent: Any, query: str) -> str:
            obs.stage_start(name)
            try:
                resp = agent.run(query)
            finally:
                # stage_end 必须执行 —— 少一次就会让这次运行的 stages 列表
                # 少一项,而 run_end 的 elapsed 与各阶段之和会对不上,
                # 排查时会被误导成"时间花在别的地方"。
                obs.stage_end(name)
            obs.stage_response(name, resp)
            print(f"  ✓ {name} 完成: {resp[:160]}...")
            return resp

        # `max_workers=0` 会直接抛 ValueError("max_workers must be greater
        # than 0") —— 空任务列表是合法输入(由调用方决定是否要跑),不该炸。
        with cf.ThreadPoolExecutor(
            max_workers=max(1, len(tasks)), thread_name_prefix="trip-expert"
        ) as pool:
            futures = {
                name: pool.submit(ctx.copy().run, _run_one, name, agent, query)
                for name, agent, query in tasks
            }
            # result() 会把子线程里的异常**原样重新抛出**,交给 plan_trip 的
            # except 统一走降级 —— 并发不改变失败时的行为。
            return {name: fut.result() for name, fut in futures.items()}

    def _rag_enabled(self) -> bool:
        """RAG 开关。配置里默认关 —— 这样「加了 RAG」和「没加」两组数字
        可以用同一份代码跑出来,而不是靠改代码前后对比(那种对比不可信:
        两次跑的代码都不一样了,差异未必来自 RAG)。

        读 `rag_enabled()` 而不是 `settings.enable_rag`:
        后者看不到业务流程设的运行时覆盖 —— 用户上传攻略时会自动打开开关,
        直接读 settings 的话,界面上明明显示"已开启",
        生成行程时却没用知识库,而且没有任何报错。这种静默不一致最难查。
        """
        return bool(rag_enabled())

    def _retrieve_knowledge(
        self,
        request: TripRequest,
        obs: NullObserver,
        sink: list[dict] | None = None,
        user_id: str | None = None,
    ) -> str:
        """检索知识库并拼成 prompt 片段。**检索不到就返回空串。**

        **失败绝不能让行程规划挂掉。** RAG 是增强,不是依赖 ——
        Milvus 没起来、embedding 接口超时、collection 忘了灌,这些情况一律
        返回空串,管线照常往下走。异常只在这里打印一行,不影响主流程。

        这一层的 `KnowledgeService.retrieve()` 内部也已经吞了异常,
        这里是第二道保险(比如 service 构造本身就出问题)。

        Args:
            sink: 传了的话,把命中的知识**结构化**填进去(用途见 plan_trip 的说明)。
                  prompt 要的是拼好的字符串,而落库与详情页展示要的是结构化数据 ——
                  两者用途不同,所以这里同时留一份,不互相迁就。
        """
        try:
            service = get_knowledge_service()
            # user_id 决定「别人上传的攻略能不能进你的行程」——
            # 传 None 会检索到全站所有上传内容(管理员视角)。
            hits = service.retrieve_for_request(request, user_id=user_id)
        except Exception as exc:
            print(f"⚠️  知识库检索失败,本次跳过: {type(exc).__name__}: {exc}")
            obs.stage_response("retrieval", f"(检索失败: {type(exc).__name__})")
            return ""

        if not hits:
            print("📚 知识库没有检索到内容 —— 本次不带外部知识生成")
            obs.stage_response("retrieval", "(空)")
            return ""

        print(f"📚 知识库检索到 {len(hits)} 条:")
        for h in hits:
            where = f"{h.source} > {h.heading_path}" if h.heading_path else h.source
            print(f"     {h.score:.3f}  {where}")

        # 结构化留一份给调用方落库。顺序就是现在的顺序(按分数降序),
        # 落库时按这个顺序存 idx,详情页拿到的第一条就是最相关的。
        if sink is not None:
            sink.extend(h.to_dict() for h in hits)

        context = format_context(hits)
        # 记进 observer:出问题时能看出「检索到了什么」,而不是只看到最终行程
        obs.stage_response("retrieval", context)
        return context

    def _build_attraction_query(self, request: TripRequest) -> str:
        """构建景点搜索查询 - 直接包含工具调用"""
        keywords = []
        if request.preferences:
            # 只取第一个偏好作为关键词
            keywords = request.preferences[0]
        else:
            keywords = "景点"

        # 直接返回工具调用格式
        query = f"请使用amap_maps_text_search工具搜索{request.city}的{keywords}相关景点。\n[TOOL_CALL:amap_maps_text_search:keywords={keywords},city={request.city}]"
        return query

    def _build_planner_query(self, request: TripRequest, attractions: str, weather: str,
                             hotels: str = "", knowledge: str = "") -> str:
        """构建行程规划查询

        `knowledge` 是 RAG 检索到的引用块(带出处)。**为空时 prompt 与加 RAG
        之前逐字相同** —— 这一点很重要:A/B 对比要测的是「RAG 带来的差异」,
        如果关掉 RAG 时 prompt 里还留着一个空的知识库段落,prompt 长度和结构
        就变了,两组数字的差异就说不清是 RAG 带来的还是段落带来的。
        """
        # 只在真的有内容时才加这一段。空段落会让模型以为"资料给过了但没看到",
        # 而且会白白改变 prompt 的结构
        knowledge_block = ""
        if knowledge:
            knowledge_block = (
                "\n**外部知识库(检索得到,优先采用其中的门票价格、预约规则、"
                f"开放时间、片区串联建议):**\n{knowledge}\n"
            )

        query = f"""请根据以下信息生成{request.city}的{request.travel_days}天旅行计划:

**基本信息:**
- 城市: {request.city}
- 日期: {request.start_date} 至 {request.end_date}
- 天数: {request.travel_days}天
- 交通方式: {request.transportation}
- 住宿: {request.accommodation}
- 偏好: {', '.join(request.preferences) if request.preferences else '无'}

**景点信息:**
{attractions}

**天气信息:**
{weather}

**酒店信息:**
{hotels}
{knowledge_block}
**要求:**
1. 每天安排2-3个景点
2. 每天必须包含早中晚三餐
3. 每天推荐一个具体的酒店(从酒店信息中选择)
4. 考虑景点之间的距离和交通方式
5. 返回完整的JSON格式数据
6. 景点的经纬度坐标要真实准确
"""
        if request.free_text_input:
            query += f"\n**额外要求:** {request.free_text_input}"

        return query
    
    def _parse_response(self, response: str, request: TripRequest,
                        observer: NullObserver | None = None) -> TripPlan:
        """
        解析Agent响应

        Args:
            response: Agent响应文本
            request: 原始请求
            observer: 可选观察者,解析失败时会标记 fallback

        Returns:
            旅行计划
        """
        obs = observer or NullObserver()
        try:
            json_str = _extract_json_object(response)

            # 解析JSON
            data = json.loads(json_str)
            
            # 转换为TripPlan对象
            trip_plan = TripPlan(**data)
            
            return trip_plan
            
        except Exception as e:
            print(f"⚠️  解析响应失败: {str(e)}")
            print(f"   将使用备用方案生成计划")
            obs.mark_fallback(f"响应解析失败,已改用备用方案: {type(e).__name__}: {e}")
            return self._create_fallback_plan(
                request, reason=f"模型返回的内容不是合法行程({type(e).__name__}: {e})"
            )
    
    def _create_fallback_plan(self, request: TripRequest, reason: str = "") -> TripPlan:
        """创建备用计划(当Agent失败时)。

        **这里只是转发** —— 真正的实现在 `app/agents/fallback.py`。

        为什么抽出去:这段逻辑原来是本方法里的 40 行,要构造整个 agent
        (hello_agents + MCPTool + LLM)才能调,所以**没法单测**。
        而 baseline 实测降级路径从没被触发过(`fallback_used` 10 条全是 0),
        改完不能靠重跑 baseline 证明它对了 —— **只能靠单测**。

        原来的实现在这里现造"北京景点1"和写死的北京坐标,详见 fallback.py 的说明。
        """
        return build_empty_plan(request, reason=reason)


def _extract_json_object(text: str) -> str:
    """从模型输出里抠出**第一个** JSON 对象。抠不到就抛 ValueError。

    为什么单独抽一个函数、并且**顺序和旧实现相反**:

    旧实现先判断 "有没有 ```",有就按"开头围栏 → 下一个围栏"切。
    问题是模型经常**只写一个收尾的围栏**(它以为自己在关闭一个代码块,
    而开头那半个它没写)。这时候 `find("```", json_start)` 返回 -1,
    切片 `response[start:-1]` 变成空串,于是 `json.loads("")` 抛
    `JSONDecodeError: Expecting value: line 1 column 1 (char 0)` ——

    **一份完全合法的行程因此被判成"解析失败",降级成空白页。**
    实测到过:planner 输出了 3603 字符的 3 天行程,末尾多了个 ```,
    整趟生成(约 40 秒、7 次 LLM 调用)的结果被丢掉。而它在日志里
    不算错误 —— `ok=True`,只有 `fallback_used=True`。

    所以现在的规则是:**围栏只在成对出现时才剥**,否则一律退回
    "第一个 `{` 到最后一个 `}`"。后者对裸 JSON、前后夹废话、
    以及多写半个围栏这三种情况都成立。
    """
    s = (text or "").strip()
    if not s:
        raise ValueError("响应为空")

    # 成对的代码块才剥 —— 语言标记可选
    fence = re.match(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```\s*$", s, re.S)
    if fence:
        s = fence.group(1).strip()
    else:
        # 也可能是 ```json 开头但没有收尾(被 max_tokens 截断之类),
        # 那也把开头那半个去掉 —— 后面的 `{`...`}` 照样能抠出来。
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)

    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("响应中未找到JSON数据")
    return s[i : j + 1]


# 全局多智能体系统实例
_multi_agent_planner = None


def get_trip_planner_agent() -> MultiAgentTripPlanner:
    """获取多智能体旅行规划系统实例(单例模式)"""
    global _multi_agent_planner

    if _multi_agent_planner is None:
        _multi_agent_planner = MultiAgentTripPlanner()

    return _multi_agent_planner

