"""LLM 用量计量

背景
----
框架的 HelloAgentsLLM.invoke() 最后一行是:

    return response.choices[0].message.content

也就是说 API 返回的 response.usage 被【直接丢掉了】,外部拿不到。
所以这里用子类重写 invoke(),在 return 之前把 usage 接住。

为什么不用全局变量累计
----------------------
P8 阶段要上 ThreadPoolExecutor 让三个 agent 并发跑。全局累加器在并发下
会把不同 agent 的 token 算混。这里用 contextvars —— 每个线程/上下文
各持一份,互不干扰。

为什么不用改 think()
--------------------
那个方法里有个坑:框架写的是 chunk.choices[0].delta.content,而开启
stream_options 后服务端会多发一个 usage-only chunk(choices 为空数组),
会直接 IndexError。本项目没做流式,用不上,索性不碰。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

from hello_agents import HelloAgentsLLM
from hello_agents.core.exceptions import HelloAgentsException

from .pricing import compute_cost_cny, is_priced

# 没有 usage 时的兜底估算系数。
# 中文大致 1 token ≈ 1.5 字,即每字约 0.67 token;取 0.6 偏保守。
_EST_TOKENS_PER_CHAR = 0.6

# 区分「真的花了 0 元」和「没拿到数据只能估」
SOURCE_API = "api"
SOURCE_ESTIMATED = "estimated"


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数。仅在拿不到 usage 时使用。"""
    return int(len(text or "") * _EST_TOKENS_PER_CHAR)


@dataclass
class CallUsage:
    """一次 LLM 调用的用量。"""

    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_cny: float
    source: str  # 'api' | 'estimated'
    cache_hit_tokens: int | None = None
    at: str = ""
    ms: int = 0
    raw: dict[str, Any] | None = None


@dataclass
class UsageCollector:
    """一次运行内的所有 LLM 调用用量。"""

    calls: list[CallUsage] = field(default_factory=list)

    def add(self, call: CallUsage) -> None:
        self.calls.append(call)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_cny(self) -> float:
        return round(sum(c.cost_cny for c in self.calls), 6)

    @property
    def llm_calls(self) -> int:
        return len(self.calls)

    @property
    def usage_source(self) -> str:
        """api / estimated / mixed / none"""
        srcs = {c.source for c in self.calls}
        if not srcs:
            return "none"
        if len(srcs) == 1:
            return srcs.pop()
        return "mixed"

    @property
    def priced(self) -> bool:
        """是否配了价格(用于区分「花费 0」和「没配价」)。"""
        return any(is_priced(c.model) for c in self.calls)

    def summary(self) -> dict[str, Any]:
        return {
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_cny": self.cost_cny,
            "usage_source": self.usage_source,
            "priced": self.priced,
        }


_current: ContextVar[UsageCollector | None] = ContextVar("usage_collector", default=None)


def get_collector() -> UsageCollector | None:
    return _current.get()


@contextmanager
def collect_usage(collector: UsageCollector | None = None) -> Iterator[UsageCollector]:
    """在这个上下文里发生的 LLM 调用都会被计入同一个 collector。

    用法::

        with collect_usage() as usage:
            plan = agent.plan_trip(request)
        print(usage.summary())
    """
    c = collector if collector is not None else UsageCollector()
    token = _current.set(c)
    try:
        yield c
    finally:
        _current.reset(token)


class MeteredLLM(HelloAgentsLLM):
    """在框架的 LLM 客户端上加了用量计量。

    行为与原类完全一致,只是把原本被丢掉的 response.usage 接住了。
    """

    def invoke(self, messages: list[dict[str, str]], **kwargs) -> str:
        import time

        t0 = time.perf_counter()
        try:
            # 与父类 HelloAgentsLLM.invoke() 保持一致地发起请求。
            # 这里必须自己调 create(),不能调 super().invoke() ——
            # 父类把 response.usage 丢掉了,拿不到。
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", self.temperature),
                max_tokens=kwargs.get("max_tokens", self.max_tokens),
                **{
                    k: v
                    for k, v in kwargs.items()
                    if k not in ["temperature", "max_tokens"]
                },
            )
        except Exception as e:
            raise HelloAgentsException(f"LLM调用失败: {str(e)}")

        content = response.choices[0].message.content
        self._record(response, messages, content, int((time.perf_counter() - t0) * 1000))
        return content

    def _record(
        self,
        response: Any,
        messages: list[dict[str, str]],
        content: str,
        ms: int,
    ) -> None:
        """把用量记进当前 collector。任何异常都不影响主流程。"""
        collector = _current.get()
        if collector is None:
            return
        try:
            raw = getattr(response, "usage", None)
            raw_dict = (
                raw.model_dump() if hasattr(raw, "model_dump") else (dict(raw) if raw else None)
            )

            if raw_dict:
                prompt_tokens = int(raw_dict.get("prompt_tokens") or 0)
                completion_tokens = int(raw_dict.get("completion_tokens") or 0)
                # DeepSeek 特有字段:缓存命中/未命中
                cache_hit = raw_dict.get("prompt_cache_hit_tokens")
                if cache_hit is None:
                    details = raw_dict.get("prompt_tokens_details") or {}
                    cache_hit = details.get("cached_tokens")
                source = SOURCE_API
            else:
                # 兜底:按字符估算。宁可偏保守(算贵),不低报。
                prompt_tokens = estimate_tokens(
                    "".join(str(m.get("content") or "") for m in messages)
                )
                completion_tokens = estimate_tokens(content)
                cache_hit = None
                source = SOURCE_ESTIMATED

            collector.add(
                CallUsage(
                    model=self.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_cny=compute_cost_cny(
                        prompt_tokens, completion_tokens, self.model, cache_hit
                    ),
                    source=source,
                    cache_hit_tokens=int(cache_hit) if cache_hit is not None else None,
                    at=datetime.now().isoformat(timespec="seconds"),
                    ms=ms,
                    raw=raw_dict,
                )
            )
        except Exception as e:  # 计量失败绝不能影响业务
            print(f"⚠️  用量计量失败(不影响主流程): {type(e).__name__}: {e}")
