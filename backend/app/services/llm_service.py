"""LLM服务模块"""

import base64
import re

from hello_agents import HelloAgentsLLM
from ..config import get_settings
from ..observability import MeteredLLM

# 全局LLM实例
_llm_instance = None


def get_llm() -> HelloAgentsLLM:
    """
    获取LLM实例(单例模式)

    返回的是 MeteredLLM —— 继承自框架的 HelloAgentsLLM,行为完全一致,
    只是把框架丢掉的 response.usage 接住,用于统计 token 与成本。
    计量只在 collect_usage() 上下文里生效,否则与原生类无差别。

    Returns:
        HelloAgentsLLM实例(实际类型 MeteredLLM)
    """
    global _llm_instance

    if _llm_instance is None:
        settings = get_settings()

        # 会自动从环境变量读取配置(LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_ID)
        _llm_instance = MeteredLLM()

        print(f"✅ LLM服务初始化成功")
        print(f"   提供商: {_llm_instance.provider}")
        print(f"   模型: {_llm_instance.model}")

    return _llm_instance


def reset_llm():
    """重置LLM实例(用于测试或重新配置)"""
    global _llm_instance
    _llm_instance = None


# ===========================================================================
# 图片 → 文字(视觉模型)
# ===========================================================================

# 图片里没有文字时,让模型回这个标记,而不是自由发挥。
# 用它而不是"看返回的文本长不长"来判断:模型很爱在图没文字时道歉或
# 描述画面(「这张图片似乎是一张风景照,没有可提取的文字」),
# 那种句子**是文字**,但没有任何检索价值,还会被切块入库污染检索结果。
NO_TEXT_MARKER = "NO_TEXT_FOUND"

_VISION_SYSTEM_PROMPT = """你是一个「图片转文字」的提取器。唯一任务:把图片里**看得见的文字**如实抄下来。

规则:
1. 只抄写。不要总结、不要翻译、不要改写、不要补充图片里没有的信息。
2. 保留层级:标题用 markdown 的 # / ## / ### 表示,列表用 - 开头。
3. 表格改写成 markdown 表格;若表格太乱,就按「字段: 值」逐行写。
4. 数字、价格、时间、预约规则、电话号码要格外仔细 —— 它们最容易被抄错,
   而这恰恰是读者真正要用的信息。
5. 如果图片里**没有任何文字**(例如纯风景照、纯菜单图),只回复这六个字符:
   NO_TEXT_FOUND
6. 直接输出提取到的内容本身。不要写「以下是图片中的文字」这类前后缀,
   也不要用代码块把内容包起来。
"""

# 模型偶尔仍会把整段内容塞进 ``` 围栏里,兜底剥掉。
# 只匹配"整段被围栏包住"的情况 —— 正文里本来就有的局部代码块不动。
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*)\n```\s*$", re.DOTALL)


def extract_text_from_image(
    data: bytes,
    mime: str,
    *,
    hint: str = "",
    max_tokens: int = 2000,
) -> str:
    """让视觉模型把图片里的文字读出来。

    走的是 OpenAI 兼容的多模态格式:`content` 不是字符串,而是一个
    **数组**,里面分别是文字片段和图片片段。所以这个函数的 messages
    不能复用纯文本那条路径的类型标注(见 metering.py 的说明)。

    Args:
        data: 图片原始字节(调用方应先压缩,见 doc_parser._prepare_image)。
        mime: 如 `image/png`。用于拼 base64 的 data URL。
        hint: 文件名之类的提示,只帮模型判断上下文(例如城市),
            **不作为结果的一部分**。
        max_tokens: 上限。攻略截图一屏文字通常几百 token,
            2000 足够;给太大会让被截断的风险变成花冤枉钱的风险。

    Returns:
        提取到的文字;图里没有文字时返回 `NO_TEXT_MARKER`。

    Raises:
        Exception: 模型不可用、或**当前模型不支持图像**(文本模型会报错)。
            调用方负责翻成一句人话 —— 这两件事对用户的含义完全不同。
    """
    llm = get_llm()

    user_text = "请提取这张图片里的文字。"
    if hint:
        user_text += f"\n(文件名提示:{hint} —— 只供你判断上下文,不要写进结果)"

    messages = [
        {"role": "system", "content": _VISION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"},
                },
            ],
        },
    ]

    raw = llm.invoke(messages, temperature=0, max_tokens=max_tokens) or ""
    text = raw.strip()

    fence = _FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()

    # 大小写和标点都容错:模型可能回 "no_text_found" 或加了句号
    if text.upper().replace(".", "").replace("。", "").strip() == NO_TEXT_MARKER:
        return NO_TEXT_MARKER
    return text


def looks_like_no_text(text: str) -> bool:
    """判断提取结果是否等于「图里没文字」。

    单独一个函数是因为这里的宽松匹配本身就是一条**规则**,
    散在调用处容易只改一半。
    """
    return text.strip().upper().replace(".", "").replace("。", "").strip() == NO_TEXT_MARKER

