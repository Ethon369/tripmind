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

# 消息体的类型。
#
# ⚠️ 不能写成 `dict[str, str]`:`content` 既可以是字符串(纯文本),
# 也可以是**数组**(多模态 —— 数组里分别是 `{"type": "text"}` 与
# `{"type": "image_url", "image_url": {...}}`,见 llm_service.extract_text_from_image)。
# 标成 str 会把图片这条路挡在类型检查之外,逼调用方去写 type: ignore,
# 那还不如把类型写准。
Messages = list[dict[str, Any]]

# 没有 usage 时的兜底估算系数。
# 中文大致 1 token ≈ 1.5 字,即每字约 0.67 token;取 0.6 偏保守。
_EST_TOKENS_PER_CHAR = 0.6

# 区分「真的花了 0 元」和「没拿到数据只能估」
SOURCE_API = "api"
SOURCE_ESTIMATED = "estimated"


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数。仅在拿不到 usage 时使用。"""
    return int(len(text or "") * _EST_TOKENS_PER_CHAR)


def join_message_text(messages: Messages) -> str:
    """把消息体里的**文字**拼起来。

    ⚠️ 不能直接 `str(m["content"])`:多模态消息的 content 是数组,
    `str()` 出来的是 Python 的 **repr**(带引号、花括号、`image_url` 字段名,
    还有 base64 图片那一大串字符),估出来的数字会离谱地大
    —— 而且只在"服务端没返回 usage"这一条分支上生效,平时看不出来。
    """
    parts: list[str] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for piece in content:
                if isinstance(piece, dict) and piece.get("type") == "text":
                    parts.append(str(piece.get("text") or ""))
        else:
            parts.append(str(content or ""))
    return "".join(parts)


# 一张图折算成多少 token —— 仅用于「拿不到 usage」的兜底估算,不代表真实计费。
# 视觉模型按图块计费,算不出准确值;取一个数量级合理的保守值即可。
_IMAGE_EST_TOKENS = 1200


def estimate_messages_tokens(messages: Messages) -> int:
    """估算一次请求的输入 token(含图片)。仅在拿不到 usage 时使用。"""
    images = sum(
        1
        for m in messages
        if isinstance(m.get("content"), list)
        for piece in m["content"]
        if isinstance(piece, dict) and piece.get("type") == "image_url"
    )
    return estimate_tokens(join_message_text(messages)) + images * _IMAGE_EST_TOKENS


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


def thinking_extra_body(model: str) -> dict[str, Any]:
    """按模型名决定要不要显式关掉思维链。

    **这是本项目目前最大的一处性能浪费,而且它是默认值造成的。**

    实测(同一个"生成一份小 JSON"的请求,内容长度几乎一致):

        | 配置                     | 输出 tokens | 耗时    |
        |--------------------------|-------------|---------|
        | 不传参数(默认)           |   3,570     | 47.7s   |
        | enable_thinking=False    |     878     | 12.1s   |
        | enable_thinking=True     |   2,636     | 34.2s   |

    也就是说**约 75% 的输出 token 是看不见的推理过程**,而这类任务
    (按给定事实拼一份 JSON)根本不需要推理链 —— 景点、天气、酒店都是
    前三个 agent 已经查好的,planner 做的是"整理与排版"。

    换算到真实运行:planner 那一步实测输出 6,240 tokens、耗时 84 秒,
    占总耗时 134 秒的 **63%**。关掉思维链后预计降到 20 秒上下。

    ⚠️ 为什么用**模型名前缀**判断、而不是配一个开关:
       `enable_thinking` 是通义/百炼系列特有的参数。对不认它的厂商
       (DeepSeek、OpenAI 官方接口……)传过去会直接 **400** ——
       而那是**每一次 LLM 调用都失败**,整个应用不可用。
       所以判断必须保守:不认识的名字就不传,退回厂商默认行为。
       换厂商时不需要改代码,这条规则会自动放行。

    ⚠️ 换成别的 qwen 模型时如果这个参数不被接受,表现是**所有调用 400**。
       处置办法是把这里的 "qwen" 判断去掉(或者收窄成具体型号)。
       之所以敢用前缀而不是白名单,是因为 qwen3 之后的型号都支持它,
       而白名单会随着型号增加不断漏项。

    Args:
        model: 模型名,如 `qwen3.7-plus`。

    Returns:
        可直接展开进 `create(**kwargs)` 的字典;不支持时返回空字典。
    """
    if not (model or "").lower().startswith("qwen"):
        return {}

    from ..config import get_settings

    return {"extra_body": {"enable_thinking": bool(get_settings().llm_enable_thinking)}}


class MeteredLLM(HelloAgentsLLM):
    """在框架的 LLM 客户端上加了用量计量。

    行为与原类几乎一致,只改了三处,都写在这里免得以后看到 `_create_client`
    的覆写以为是随手加的:

    1. 把原本被丢掉的 `response.usage` 接住(见 `invoke` / `_record`)。
    2. **关掉 OpenAI SDK 的自动重试**(见 `_create_client`)。
    3. **关掉思考模型的思维链**(见 `invoke` 里的 `_thinking_extra_body`)。
       实测这是本项目最大的一处性能浪费,详见那个函数的说明。
    """

    def _create_client(self) -> Any:
        """建客户端,但把 SDK 的自动重试关掉。

        ⚠️ 这不是"顺手优化",是必须的 —— 不关的话超时会被放大 3 倍,
        把整条超时链路顶穿。

        OpenAI SDK 默认 `max_retries=2`,也就是**最多尝试 3 次**。
        而框架 `_create_client()` 只传了 `timeout`,没传 `max_retries`,
        所以这个默认值一直在生效。

        对「生成超时」这类错误,重试是没有意义的:同样的 prompt、同样长的
        输出,第二次一样会超时,只是把总耗时乘以 3。实测到的正是这个形态 ——
        `LLM_TIMEOUT=60` 时一次 planner 超时,客户端连试 3 次,白等 180 秒:

            elapsed_ms = 239783  (≈ 各阶段 58s + 60s×3)
            6 次调用都在 20s 内正常返回,只有 planner 那一次在重试
            错误最终是 "LLM调用失败: Request timed out."

        放大之后会直接突破外层:nginx 的 `proxy_read_timeout` 是 330 秒,
        而 `LLM_TIMEOUT=180` 配 3 次尝试最坏就是 540 秒 —— 用户等不到前端的
        友好提示,只会收到一个 nginx 504,同时白占一个 worker 五分多钟。

        该由外层负责的重试交给外层:前端对 5xx 会重试(最多 3 次),那是
        真正有意义的场景(网络抖动、服务瞬时不可用)。

        不做成参数是因为框架的构造函数没暴露它,而这里手工重建 OpenAI
        客户端会跟框架的凭据解析逻辑重复一份 —— 后者更容易随升级漂移。
        """
        client = super()._create_client()
        client.max_retries = 0
        return client

    def invoke(self, messages: Messages, **kwargs) -> str:
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
                **thinking_extra_body(self.model),
            )
        except Exception as e:
            raise HelloAgentsException(f"LLM调用失败: {str(e)}")

        content = response.choices[0].message.content
        self._record(response, messages, content, int((time.perf_counter() - t0) * 1000))
        return content

    def _record(
        self,
        response: Any,
        messages: Messages,
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
                # 缓存命中字段有**两种写法**,厂商不同:
                #   DeepSeek → prompt_cache_hit_tokens(顶层字段)
                #   百炼等 OpenAI 兼容实现 → prompt_tokens_details.cached_tokens
                # 先查前者再退到后者,换厂商时这一处不用改。
                cache_hit = raw_dict.get("prompt_cache_hit_tokens")
                if cache_hit is None:
                    details = raw_dict.get("prompt_tokens_details") or {}
                    cache_hit = details.get("cached_tokens")
                source = SOURCE_API
            else:
                # 兜底:按字符估算。宁可偏保守(算贵),不低报。
                prompt_tokens = estimate_messages_tokens(messages)
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
