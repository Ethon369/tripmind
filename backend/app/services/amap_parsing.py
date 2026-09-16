"""高德 MCP 返回值的解析 —— 纯函数,不依赖 MCP 连接

为什么单独一个模块
------------------
这两件事**很容易写错**,而且错得不明显:

1. **剥壳。** `MCPTool.run()` 返回的不是 JSON,是一段带前缀的文本:
   `工具 'maps_text_search' 执行结果:\\n{...}`
2. **拆坐标。** 高德的经纬度是 `"116.397,39.918"` 这样的**字符串**,
   不是数字对。

它们同时被两处需要:`scripts/recorder.py`(录 ground truth)和
`app/services/amap_service.py`(P6 要让 agent 用上真实坐标)。
写成纯函数放这里,两边共用一份**被测过的**实现 —— 而不是各写一遍、
各自踩一遍同一个坑。

⚠️ 已知的坑
-----------
用 `re.search(r'\\{.*\\}')` 去抽 JSON 是**贪婪**匹配,它在两种情况下会取过头:
嵌套对象里、以及 JSON 后面还跟着别的花括号时。取过头的后果是解析失败,
而报的错跟真正的原因看不出关系,很容易查错方向。

这里改成:先按固定前缀切壳 → 精确 `json.loads` → 失败才退到
`JSONDecoder.raw_decode`(它在**配对的**收尾处停下,天然不会过头)。
"""

from __future__ import annotations

import json
from typing import Any

# MCPTool.run() 里拼前缀的那句话(见 hello_agents/tools/builtin/protocol_tools.py)
_RESULT_MARKER = "执行结果:\n"


def unwrap_mcp_result(raw: Any) -> dict[str, Any]:
    """剥掉 MCP 工具的文本外壳,拿到里面的 dict。

    三级降级,每一级都保留证据:

    1. 按固定前缀切掉外壳,精确 `json.loads`
    2. 失败则取最外层的一对 `{}` 再试(前后可能挂着别的话)
    3. 都不行就把原文放进 `{"_raw": ...}` **原样留着**

    第 3 步很重要:解析不了的多半是**错误信息**(比如
    `{"error": "Text Search failed: ..."}` 之外的纯文本报错),
    丢掉的话排查时就没有证据了。

    Returns:
        解析出的 dict。永远返回 dict,不返回 None、不抛异常 ——
        调用方拿到空 dict 就知道"这次没结果",不必再套一层 try。
    """
    text = str(raw if raw is not None else "")
    idx = text.find(_RESULT_MARKER)
    if idx != -1:
        text = text[idx + len(_RESULT_MARKER):]
    text = text.strip()

    if not text:
        return {}

    # 快路径:整段就是 JSON
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {"_value": obj}
    except json.JSONDecodeError:
        pass

    # 退化路径:前后还挂着别的话。
    #
    # 用 `JSONDecoder.raw_decode` 而不是"找第一个 { 和最后一个 }"——
    # 后者(以及贪婪正则)在下面这种情况下会取到**过多的内容**:
    #
    #     工具 'x' 执行结果:
    #     {"a": {"b": 1}}
    #     (附加说明 {不是 JSON})
    #
    # "最后一个 }"在附加说明里,于是切出来的字符串两头不搭,解析失败。
    # raw_decode 从第一个 { 开始,在**配对的**收尾处就停下,天然不会过头。
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if starts:
        try:
            obj, _end = json.JSONDecoder().raw_decode(text[min(starts):])
            return obj if isinstance(obj, dict) else {"_value": obj}
        except json.JSONDecodeError:
            pass

    return {"_raw": text[:2000]}


def parse_location(value: Any) -> list[float] | None:
    """把高德的 location 转成 `[经度, 纬度]`。

    高德返回的是 `"经度,纬度"` 这样的**字符串**(注意顺序:先经度后纬度,
    和常见的 "lat,lng" 相反 —— 搞反了坐标会跑到地球另一边)。

    兼容已经拆好的 `[lon, lat]` 列表,所以重复调用是安全的。

    Returns:
        `[经度, 纬度]`;解析不出来返回 None(**不是** `[0.0, 0.0]` ——
        那会变成一个"在几内亚湾海上"的合法坐标,比没有更糟)。
    """
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return [float(value[0]), float(value[1])]
        except (TypeError, ValueError):
            return None

    if not isinstance(value, str):
        return None

    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        return [float(parts[0]), float(parts[1])]
    except ValueError:
        return None


def format_location(lon: float, lat: float) -> str:
    """反过来:把数字对拼成高德要的 `"经度,纬度"` 字符串。

    `maps_around_search`、`maps_distance` 这类工具的参数是这个格式。
    """
    return f"{lon},{lat}"
