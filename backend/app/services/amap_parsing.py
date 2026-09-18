"""高德 MCP 返回值的解析 —— 纯函数,不依赖 MCP 连接

为什么单独一个模块
------------------
这些事**很容易写错**,而且错得不明显:

1. **剥壳。** `MCPTool.run()` 返回的不是 JSON,是一段带前缀的文本:
   `工具 'maps_text_search' 执行结果:\\n{...}`
2. **拆坐标。** 高德的经纬度是 `"116.397,39.918"` 这样的**字符串**,
   不是数字对。
3. **归一化类型不一致的字段。** `alias` / `rating` 在不同条目里可能是
   字符串也可能是列表,见 `normalize_str_list` 的说明。

它们同时被三处需要:`scripts/recorder.py`(录 ground truth)、
`app/services/amap_service.py`(给 map/poi 路由用)和评测的接地性指标。
写成纯函数放这里,三方共用一份**被测过的**实现 —— 而不是各写一遍、
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
    # 已经是 dict 就直接返回(幂等)。
    #
    # 这一条不是防呆,是防一个**真实会踩的坑**:调用链上常常先
    # `unwrap_mcp_result(raw)` 拿到 dict,再把它交给 extract_xxx() ——
    # 而 extract_xxx() 内部还会再剥一次壳。第二次拿到的是 Python 的
    # `repr`(单引号、True/None),不是 JSON,精确解析与 raw_decode 都会失败,
    # 最后落进 `{"_raw": ...}` 分支 —— 表现为"数据明明在,却解析不出来"。
    if isinstance(raw, dict):
        return raw

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


# ===========================================================================
# 归一化
# ===========================================================================


def normalize_str_list(raw: Any, sep: str = "|") -> list[str]:
    """把"有时是字符串、有时是列表"的字段统一成 `list[str]`。

    ⚠️ 这不是过度防御,是本项目**实测**出来的。冻结库存 1751 条
    `maps_search_detail` 响应里,同一个 `alias` 字段:

        240 条是字符串   "紫禁城" / "中国历史博物馆|北京历史博物馆"
       1510 条是列表     []
         1 条是 None

    **有别名的那 240 条全部是字符串,而列表全部是空的。** 所以只写
    `isinstance(raw, (list, tuple))` 的话,守卫条件对「恰好有数据的那批」
    **恒为假** —— 别名一条也读不出来,而且**不报任何错**。

    ⚠️ 别把这个规律套到别的字段上。同一个 `rating` 字段的实测形态是
    `1693 条字符串 / 57 条空列表 / 1 条 None` —— **不存在非空列表**。
    也就是说两个字段的"类型不一致"表现**方向是相反的**:
    alias 的数据在字符串里,rating 的数据也在字符串里、但列表那边是空的。
    写 `isinstance` 守卫的方向,得按字段各自看真实数据,不能类推。

    多个值在字符串里用 `|` 分隔(见 `中国历史博物馆|北京历史博物馆`)。
    列表元素也按同一规则再拆一次,因为列表里同样可能装着 `"a|b"`。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(sep) if item.strip()]
    if isinstance(raw, (list, tuple, set)):
        out: list[str] = []
        for item in raw:
            out.extend(part.strip() for part in str(item).split(sep) if part.strip())
        return out
    # 完全不认识的类型(如数字)返回空,而不是硬转成字符串 ——
    # 这个契约由 tests/test_knowledge.py 的 `(123, [])` 用例固定下来。
    # 数字标量确实需要保留时用 `first_str`,它单独处理了这种情况。
    return []


def first_str(raw: Any, default: str = "") -> str:
    """取"字符串,或列表里的第一个"。

    用于 `rating` / `type` / `open_time` 这类字段。

    ⚠️ 关于 `rating`,别学上面 `alias` 的结论。实测 1751 条里它是
    `1693 条字符串 / 57 条空列表 / 1 条 None` —— **数据全在字符串那边**,
    列表那边是空的。所以这里真正起作用的是字符串分支,
    列表分支属于"将来万一"的防御。注释里把两个字段说成同一种坑是错的。
    """
    values = normalize_str_list(raw)
    if values:
        return values[0]
    # 标量数字(万一哪天 rating 变成 4.9)不该被丢掉 ——
    # normalize_str_list 对它返回空是刻意的契约,这里单独兜一下。
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return str(raw)
    return default


def mcp_error_message(raw: Any) -> str:
    """返回体里如果装着"失败",返回它的文案;成功则返回空串。

    为什么必须单独查这个
    --------------------
    高德/ MCP 失败时**不会**抛异常,而是回一个结构完好的 JSON:

        {"error": "Text Search failed: INVALID_USER_KEY"}
        {"status": "0", "info": "INVALID_USER_KEY", ...}   ← 高德自己的失败形态

    如果只看"解析成功没成功",这两种都会一路走通,最后变成
    「success=true, 结果 0 条」—— 用户看到的是"这城市没景点",
    而真实原因是 key 失效。**这与 MCP 工具静默变成 0 个是同一类故障**:
    服务不报错,输出静默变差。

    所以调用方拿到返回体后应当先问一句这个,再决定是不是"真的没结果"。
    """
    obj = unwrap_mcp_result(raw)
    error = obj.get("error")
    if error:
        return str(error)
    if str(obj.get("status")) == "0":
        return str(obj.get("info") or "高德接口返回失败")
    return ""


# ===========================================================================
# 各工具的提取
# ===========================================================================
#
# 覆盖情况(写下来,免得以后误以为全都有数据兜底):
#
#   extract_pois / extract_poi_detail —— **有真实数据回归测试**,
#     对应 data/frozen/amap/raw_cache.json 里的 100 条 maps_text_search
#     与 1751 条 maps_search_detail。
#
#   extract_weather / extract_route / extract_geocode —— 本地**没有**录制样本,
#     结构按高德 Web 服务 API 的公开文档写,并同时兼容 `base`(实况 lives)
#     与 `all`(预报 forecasts)两种返回。想补本地 ground truth 就跑:
#         ./venv/Scripts/python.exe scripts/recorder.py


def _poi_list(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """取出 `pois` 字段并过滤掉非 dict 项。"""
    pois = obj.get("pois")
    if not isinstance(pois, list):
        return []
    return [p for p in pois if isinstance(p, dict)]


def extract_pois(raw: Any) -> list[dict[str, Any]]:
    """从 `maps_text_search` 的返回里取出 POI 列表。

    ⚠️ **关键词搜索不返回坐标。** 真实数据 1924 条 POI,字段固定为
    `{id, name, address, typecode}` —— 没有 `location`。要坐标必须拿 id
    再调一次 `maps_search_detail`。

    这里**如实返回 location=None**,而不是补一个 `[0.0, 0.0]`:
    后者是一个"在几内亚湾海上"的合法坐标,比没有更糟。

    Returns:
        归一化后的 POI 列表;没结果时是空列表(**不是** None)。
        每条形如::

            {"id": ..., "name": ..., "address": ..., "typecode": ..., "location": None}
    """
    obj = unwrap_mcp_result(raw)
    out: list[dict[str, Any]] = []
    for item in _poi_list(obj):
        location = parse_location(item.get("location"))
        out.append(
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "address": str(item.get("address") or ""),
                # 搜索接口给的是分类码(如 "110201|140100"),不是可读类型名
                "typecode": first_str(item.get("typecode")),
                "location": location,
            }
        )
    return out


def extract_poi_detail(raw: Any) -> dict[str, Any]:
    """归一化 `maps_search_detail` 的一条 POI 详情。

    真实响应有 **7 种不同的字段组合**(有的没有 `level`,餐饮类给
    `meal_ordering` 而不是 `ticket_ordering`),所以这里一律用 `.get()`,
    不假设任何字段必然存在。

    归一化的三处:
    - `location`: `"经度,纬度"` 字符串 → `[经度, 纬度]`,解析不出是 None
    - `alias`: 字符串 / 列表 / None → `list[str]`
    - `rating`: 字符串 / 列表 / None → `str`

    Returns:
        归一化后的 dict(**可能为空**,表示这次没拿到数据);
        解析不了原文时会带上 `_raw` 供排查。
    """
    obj = unwrap_mcp_result(raw)
    if not obj:
        return {}
    if "pois" in obj:  # 兼容返回列表的情况:取第一条
        items = _poi_list(obj)
        obj = items[0] if items else {}

    return {
        "id": str(obj.get("id") or ""),
        "name": str(obj.get("name") or ""),
        "address": str(obj.get("address") or ""),
        "location": parse_location(obj.get("location")),
        "city": str(obj.get("city") or ""),
        "business_area": str(obj.get("business_area") or ""),
        "type": first_str(obj.get("type")),
        "typecode": first_str(obj.get("typecode")),
        "aliases": normalize_str_list(obj.get("alias")),
        "rating": first_str(obj.get("rating")),
        "open_time": first_str(obj.get("open_time") or obj.get("opentime2")),
    }


def extract_weather(raw: Any) -> list[dict[str, Any]]:
    """归一化 `maps_weather` 的返回。

    高德天气接口有两种返回,取决于 `extensions` 参数,所以两种都要认:

    - `base`  → `lives[]`,实况(每个城市一条,只有当天)
    - `all`   → `forecasts[0].casts[]`,逐日预报

    统一映射到 `WeatherInfo` 的字段名(见 models/schemas.py)。

    ⚠️ 本地没有录制样本,结构按官方文档;两种形态都兼容,任一为空时返回空列表。
    """
    obj = unwrap_mcp_result(raw)
    out: list[dict[str, Any]] = []

    # --- base:实况 ---
    lives = obj.get("lives")
    if isinstance(lives, list):
        for live in lives:
            if not isinstance(live, dict):
                continue
            # reporttime 形如 "2025-06-01 15:00:00",取日期部分
            report_time = str(live.get("reporttime") or "")
            out.append(
                {
                    "date": report_time.split(" ")[0],
                    "day_weather": str(live.get("weather") or ""),
                    "night_weather": "",
                    "day_temp": live.get("temperature") or 0,
                    "night_temp": 0,
                    "wind_direction": str(live.get("winddirection") or ""),
                    "wind_power": str(live.get("windpower") or ""),
                }
            )

    # --- all:预报 ---
    forecasts = obj.get("forecasts")
    if isinstance(forecasts, list):
        for forecast in forecasts:
            if not isinstance(forecast, dict):
                continue
            casts = forecast.get("casts")
            if not isinstance(casts, list):
                continue
            for cast in casts:
                if not isinstance(cast, dict):
                    continue
                out.append(
                    {
                        "date": str(cast.get("date") or ""),
                        "day_weather": str(cast.get("dayweather") or ""),
                        "night_weather": str(cast.get("nightweather") or ""),
                        "day_temp": cast.get("daytemp") or 0,
                        "night_temp": cast.get("nighttemp") or 0,
                        "wind_direction": str(
                            cast.get("daywind") or cast.get("nightwind") or ""
                        ),
                        "wind_power": str(cast.get("daypower") or cast.get("nightpower") or ""),
                    }
                )

    return out


def extract_route(raw: Any) -> dict[str, Any] | None:
    """归一化路线规划的返回,取第一条方案。

    驾车/步行返回 `route.paths[]`,公交返回 `route.transits[]` ——
    **两者结构不同**,所以两条路径都要认。

    Returns:
        `{"distance": 米, "duration": 秒}`;查不到路线时返回 **None**
        (调用方据此回 `success=false`,而不是回一个全 0 的假路线)。
    """
    obj = unwrap_mcp_result(raw)
    route = obj.get("route")
    if not isinstance(route, dict):
        return None

    candidate: dict[str, Any] | None = None
    for key in ("paths", "transits"):
        items = route.get(key)
        if isinstance(items, list) and items and isinstance(items[0], dict):
            candidate = items[0]
            break

    if candidate is None:
        return None

    def _num(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    # 公交方案的 distance 在 route 层,不在 transits[0] 里
    distance = candidate.get("distance")
    if distance in (None, ""):
        distance = route.get("distance")

    return {
        "distance": _num(distance),
        "duration": int(_num(candidate.get("duration"))),
        "strategy": str(candidate.get("strategy") or ""),
        "steps": len(candidate.get("steps") or []),
    }


def extract_geocode(raw: Any) -> list[float] | None:
    """归一化地理编码(`maps_geo`)的返回,取第一条结果。

    Returns:
        `[经度, 纬度]`;解析不出来返回 None。
    """
    obj = unwrap_mcp_result(raw)
    geocodes = obj.get("geocodes")
    if not isinstance(geocodes, list):
        return None
    for item in geocodes:
        if not isinstance(item, dict):
            continue
        location = parse_location(item.get("location"))
        if location is not None:
            return location
    return None


def format_distance(meters: float) -> str:
    """米 → 人读的距离文案。"""
    if meters >= 1000:
        return f"{meters / 1000:.1f} 公里"
    return f"{int(meters)} 米"


def format_duration(seconds: int) -> str:
    """秒 → 人读的耗时文案。"""
    if seconds >= 3600:
        hours, rest = divmod(seconds, 3600)
        return f"{hours} 小时 {rest // 60} 分钟"
    return f"{max(1, seconds // 60)} 分钟"
