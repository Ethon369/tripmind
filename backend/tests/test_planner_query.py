"""`_build_planner_query` 的单元测试 —— 重点是 RAG 注入**不能改变别的东西**

为什么单独测这一个方法:

P7 的验收产物是「RAG 开 / 关 两组数字并列」。这个对比要成立,前提是
**两组之间唯一的差别就是那段知识**。如果关掉 RAG 时 prompt 里还留着别的变化
(多一个空段落、少一行说明、顺序变了),那两组数字的差异就说不清是 RAG 带来的
还是 prompt 结构变带来的 —— 那种对比是假的,不如不做。

这个测试守的就是这条:**关掉 RAG 时,prompt 必须与加 RAG 之前逐字相同。**

怎么在不构造整个 agent 的前提下测它:
`MultiAgentTripPlanner.__init__` 会建 LLM + MCP 工具(要真起子进程),
但 `_build_planner_query` **只用到了参数、没碰 self**。所以用
`__new__` 跳过 `__init__`、直接调方法即可 —— 不构造任何东西。
(`app/agents/fallback.py` 当初从类方法抽成纯函数,解决的是同一类问题。)
"""

from __future__ import annotations

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import TripRequest

REQUEST = TripRequest(
    city="北京",
    start_date="2026-06-01",
    end_date="2026-06-03",
    travel_days=3,
    transportation="公共交通",
    accommodation="经济型酒店",
    preferences=["历史文化"],
    free_text_input="",
)

ATTRACTIONS = "故宫博物院、天安门广场"
WEATHER = "6月1日 晴 25℃"
HOTELS = "北京某酒店"

KNOWLEDGE = (
    "【外部知识库检索结果】以下内容来自本地知识库:\n"
    "[1] 来源: 北京.md > 北京 > 必须提前预约的\n"
    "故宫博物院需官网实名预约,周一闭馆。"
)


def _build(**kwargs) -> str:
    """不构造 agent,直接调这个不依赖 self 的方法。"""
    planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
    return planner._build_planner_query(
        REQUEST, ATTRACTIONS, WEATHER, HOTELS, **kwargs
    )


class TestRagBlock:
    def test_没有知识时_prompt_里不出现知识库段落(self):
        query = _build(knowledge="")
        assert "知识库" not in query
        assert "外部" not in query

    def test_不传_knowledge_参数时结果与传空串相同(self):
        """默认值必须是空串,不然调用方漏传就会静默多出一段。"""
        assert _build() == _build(knowledge="")

    def test_有知识时内容进了_prompt(self):
        query = _build(knowledge=KNOWLEDGE)
        assert "外部知识库" in query
        assert "故宫博物院需官网实名预约" in query
        assert "北京.md" in query  # 出处也要进去

    def test_知识是追加的而不是插在中间(self):
        """知识块必须加在「酒店信息」之后、「要求」之前 —— 顺序变了模型
        的注意力分配也会变,那就不是干净的 A/B 了。"""
        plain = _build(knowledge="")
        with_kb = _build(knowledge=KNOWLEDGE)

        head_plain, tail_plain = plain.split("**要求:**", 1)
        head_with, tail_with = with_kb.split("**要求:**", 1)

        # 要求那一段一字未改
        assert tail_plain == tail_with
        # 前半段是无知识版本 + 追加的知识块
        assert head_with.startswith(head_plain)
        added = head_with[len(head_plain):]
        assert "外部知识库" in added
        assert "故宫博物院需官网实名预约" in added

    def test_关掉_RAG_时_prompt_与加_RAG_之前逐字相同(self):
        """**这是本文件存在的理由。**

        断言的是「不加知识时的 prompt 完全等于一段写死的期望文本」。
        以后谁往这个 f-string 里顺手加一行(哪怕是无害的说明),这里立刻变红
        —— 提醒他:你正在破坏 RAG 的对照组。
        """
        query = _build(knowledge="")
        expected = (
            "请根据以下信息生成北京的3天旅行计划:\n"
            "\n"
            "**基本信息:**\n"
            "- 城市: 北京\n"
            "- 日期: 2026-06-01 至 2026-06-03\n"
            "- 天数: 3天\n"
            "- 交通方式: 公共交通\n"
            "- 住宿: 经济型酒店\n"
            "- 偏好: 历史文化\n"
            "\n"
            "**景点信息:**\n"
            f"{ATTRACTIONS}\n"
            "\n"
            "**天气信息:**\n"
            f"{WEATHER}\n"
            "\n"
            "**酒店信息:**\n"
            f"{HOTELS}\n"
            "\n"
            "**要求:**\n"
            "1. 每天安排2-3个景点\n"
            "2. 每天必须包含早中晚三餐\n"
            "3. 每天推荐一个具体的酒店(从酒店信息中选择)\n"
            "4. 考虑景点之间的距离和交通方式\n"
            "5. 返回完整的JSON格式数据\n"
            "6. 景点的经纬度坐标要真实准确\n"
        )
        assert query == expected

    def test_额外要求仍然只在有自由输入时出现(self):
        plain = _build(knowledge="")
        assert "额外要求" not in plain  # REQUEST 的 free_text_input 是空串
