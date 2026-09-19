# ===========================================================================
# 三个专家 agent 的并发执行
# ===========================================================================
#
# 这个改动只有**两个**会静默失效的点,两个都必须钉住:
#
#   1. 子线程继承不到 `collect_usage()` 设的 ContextVar → token 计量丢失,
#      表现为历史页成本 ¥0、llm_calls 少 3 次,**没有任何报错**。
#   2. `ctx.run()` 在同一个 Context 对象上被两个线程同时进入会抛 RuntimeError
#      ("already entered") —— 所以每条任务必须用**各自的副本**。
#
# 不测"真的更快了多少":那依赖网络,是 `scripts/check_generation_speed.py`
# 的事(它跑真实生成并对比阶段耗时)。这里只测**结构**:并发、计量、异常。

import time

import pytest

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.observability import NullObserver
from app.observability.metering import CallUsage, UsageCollector, collect_usage, get_collector


class _FakeAgent:
    """替身 agent:`run()` 睡一会儿,并像 MeteredLLM 那样登记一次用量。"""

    def __init__(self, seconds: float, record_usage: bool = True):
        self.seconds = seconds
        self.record_usage = record_usage

    def run(self, query: str) -> str:
        time.sleep(self.seconds)
        if self.record_usage:
            collector = get_collector()
            if collector is not None:
                collector.add(
                    CallUsage(
                        model="fake-model",
                        prompt_tokens=10,
                        completion_tokens=5,
                        cost_cny=0.001,
                        source="api",
                        at="",
                        ms=int(self.seconds * 1000),
                    )
                )
        return f"result: {query}"


class _RecordingObserver(NullObserver):
    """把 stage_start/stage_end 记下来 —— 要断言每个阶段都被闭合了。"""

    def __init__(self):
        self.started: list[str] = []
        self.ended: list[str] = []
        self.responses: dict[str, str] = {}

    def stage_start(self, name: str) -> None:
        self.started.append(name)

    def stage_end(self, name: str) -> None:
        self.ended.append(name)

    def stage_response(self, stage: str, text: str) -> None:
        self.responses[stage] = text


def _agent_without_init() -> MultiAgentTripPlanner:
    """拿到一个**没跑 __init__ 的实例**。

    `MultiAgentTripPlanner.__init__` 会连 MCP、起 uvx 子进程(实测 20 秒以上),
    单测里不能碰。而 `_run_experts_parallel` 只用到 `self` 做方法查找,
    不需要任何实例状态 —— 所以绕开构造是安全的。
    """
    return object.__new__(MultiAgentTripPlanner)


class Test三个专家并发:
    def test_总耗时取最大值而不是求和(self):
        """串行的判据:三个各 0.4s 的 agent,并发后总时长应接近 0.4s 而不是 1.2s。

        阈值放得很宽(0.9s)是有意的 —— CI 上线程调度抖动很大,
        但**远小于**串行的 1.2s 才能说明确实并发了。
        """
        agent = _agent_without_init()
        obs = _RecordingObserver()
        tasks = [("attractions", _FakeAgent(0.4), "q1"),
                 ("weather", _FakeAgent(0.4), "q2"),
                 ("hotels", _FakeAgent(0.4), "q3")]

        t0 = time.perf_counter()
        out = agent._run_experts_parallel(obs, tasks)
        elapsed = time.perf_counter() - t0

        assert set(out) == {"attractions", "weather", "hotels"}
        assert out["attractions"] == "result: q1"
        assert elapsed < 0.9, f"三个 0.4s 的任务花了 {elapsed:.2f}s —— 是串行"
        assert elapsed >= 0.4, f"只花了 {elapsed:.2f}s,比单个任务还短,不可能"

    def test_子线程里的用量不会丢(self):
        """**这是本次改动最容易静默失效的地方。**

        `collect_usage()` 用 `ContextVar`,而子线程默认**继承不到**父线程
        的 ContextVar。不显式传上下文副本的话,三个 agent 的 token 用量
        全部丢失 —— 历史页成本显示 ¥0、调用次数少 3 次,而且没有任何报错。
        """
        agent = _agent_without_init()
        obs = _RecordingObserver()
        tasks = [("attractions", _FakeAgent(0.05), "q1"),
                 ("weather", _FakeAgent(0.05), "q2"),
                 ("hotels", _FakeAgent(0.05), "q3")]

        with collect_usage(UsageCollector()) as usage:
            agent._run_experts_parallel(obs, tasks)

        assert usage.llm_calls == 3, (
            f"只记到 {usage.llm_calls} 次 —— 子线程没拿到 ContextVar,计量丢了"
        )
        assert usage.total_tokens == 45

    def test_没有collect_usage上下文时不报错(self):
        """没包 `collect_usage()` 时(比如命令行脚本)不该崩。"""
        agent = _agent_without_init()
        out = agent._run_experts_parallel(
            _RecordingObserver(), [("attractions", _FakeAgent(0.01), "q")]
        )
        assert out["attractions"] == "result: q"

    def test_每个阶段都被闭合(self):
        """stage_end 少一次,这次运行的 stages 就少一项 ——
        `run_end` 的总耗时与各阶段之和对不上,排查时会被误导。"""
        agent = _agent_without_init()
        obs = _RecordingObserver()
        agent._run_experts_parallel(
            obs, [("a", _FakeAgent(0.01), "q1"), ("b", _FakeAgent(0.01), "q2")]
        )
        assert sorted(obs.started) == ["a", "b"]
        assert sorted(obs.ended) == ["a", "b"]
        assert set(obs.responses) == {"a", "b"}

    def test_某个agent抛异常会原样抛出且其它阶段仍被闭合(self):
        """并发**不改变失败时的行为**:异常要能从 `fut.result()` 重新抛出,
        交给 `plan_trip` 的 except 统一降级。

        同时 stage_end 必须照常执行 —— 否则失败的那次运行会在日志里留下
        一个没有闭合的阶段,而失败的运行恰恰是最需要看日志的。
        """
        class _Boom:
            def run(self, query: str) -> str:
                raise ValueError("模拟 agent 失败")

        agent = _agent_without_init()
        obs = _RecordingObserver()

        with pytest.raises(ValueError, match="模拟 agent 失败"):
            agent._run_experts_parallel(
                obs, [("attractions", _Boom(), "q1"), ("weather", _FakeAgent(0.01), "q2")]
            )

        assert "attractions" in obs.ended, "失败的阶段也必须 stage_end"

    def test_空任务列表不炸(self):
        agent = _agent_without_init()
        assert agent._run_experts_parallel(_RecordingObserver(), []) == {}

    def test_单任务也走同一条路径(self):
        """只有一个任务时不该退化成别的分支 —— 两条路径意味着两套行为。"""
        agent = _agent_without_init()
        obs = _RecordingObserver()
        out = agent._run_experts_parallel(obs, [("only", _FakeAgent(0.01), "q")])
        assert out == {"only": "result: q"}
        assert obs.started == ["only"] and obs.ended == ["only"]
