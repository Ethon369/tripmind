"""amap_parsing.py 的单元测试 —— 高德返回值的解析

这些函数看着简单,但坑很实在:
- 剥壳时用贪婪正则会在**嵌套 JSON** 和**后面还跟着花括号**时取过头
- 坐标是 `"经度,纬度"` 字符串,顺序和常见的 "lat,lng" **相反** —— 搞反了
  坐标会跑到地球另一边,而且还是个"合法"坐标,不报错
- `alias` / `rating` 的类型**不一致**(同一字段有时是字符串、有时是列表)

这类错误不会抛异常,只会静默产出错数据。所以测试要从几个方向都堵一遍。

文件末尾还有一组**读真实录制数据**的回归(TestAgainstReal*),
那些才能抓住「形状假设错了」——前面的构造用例只能验证「我以为的世界」。
"""

from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.services.amap_parsing import (
    extract_geocode,
    extract_poi_detail,
    extract_pois,
    extract_route,
    extract_weather,
    first_str,
    format_distance,
    format_duration,
    format_location,
    mcp_error_message,
    normalize_str_list,
    parse_location,
    unwrap_mcp_result,
)
from app.services.knowledge_service import resolve_dir

# MCPTool.run() 真实返回的外壳长这样(见 hello_agents 的 protocol_tools.py)
WRAPPER = "工具 'maps_text_search' 执行结果:\n"


# ===========================================================================
# unwrap_mcp_result
# ===========================================================================


class TestUnwrap:
    def test_剥掉外壳(self):
        raw = WRAPPER + '{"pois": [{"name": "故宫"}]}'
        assert unwrap_mcp_result(raw) == {"pois": [{"name": "故宫"}]}

    def test_嵌套对象不会取过头(self):
        """**贪婪正则会在这里出错。**

        真实的高德返回就是嵌套的:
        `{"suggestion": {"cities": [{"name": "北京"}]}, "pois": [...]}`
        """
        payload = {
            "suggestion": {"keywords": "景点", "cities": [{"name": "北京"}, {"name": "上海"}]},
            "pois": [{"id": "B1", "name": "故宫", "address": "景山前街4号", "typecode": "110101"}],
        }
        import json

        got = unwrap_mcp_result(WRAPPER + json.dumps(payload, ensure_ascii=False))
        assert got == payload
        assert got["pois"][0]["name"] == "故宫"

    def test_后面还跟着花括号时不会取过头(self):
        """这是"找最后一个 }"那种写法(以及贪婪正则)会错的地方:
        JSON 之后还有别的内容,其中也带花括号。"""
        raw = WRAPPER + '{"a": {"b": 1}}\n(附加说明 {不是 JSON})'
        assert unwrap_mcp_result(raw) == {"a": {"b": 1}}

    def test_没有外壳的纯JSON也能解析(self):
        assert unwrap_mcp_result('{"pois": []}') == {"pois": []}

    def test_数组按_value返回(self):
        """顶层是数组时不硬塞进 dict,而是包一层 —— 不丢数据。"""
        assert unwrap_mcp_result(WRAPPER + "[1, 2, 3]") == {"_value": [1, 2, 3]}

    def test_解析不了时保留原文(self):
        """解析不了的多半是**错误信息**。丢掉的话排查时就没证据了。"""
        raw = WRAPPER + "Traceback: 连接超时,请稍后重试"
        got = unwrap_mcp_result(raw)
        assert "_raw" in got
        assert "连接超时" in got["_raw"]
        # 外壳必须已经剥掉 —— 否则 _raw 里混着"工具 'xxx' 执行结果:"这行噪音,
        # 排查时得先自己删一遍。这条同时把"剥壳"这一步钉住:
        # 少了它,下面的 raw_decode 兜底也能把 JSON 找出来,
        # 于是"剥壳被删掉"这个改动在其它用例上看不出来。
        assert "执行结果" not in got["_raw"]

    def test_空输入返回空字典(self):
        assert unwrap_mcp_result("") == {}
        assert unwrap_mcp_result(None) == {}
        assert unwrap_mcp_result(WRAPPER) == {}

    def test_永远返回字典不抛异常(self):
        """调用方靠"拿到空 dict"判断没结果,不该还要自己套 try。"""
        for raw in ["", None, "乱码", "{不完整", "[]", "null", "123", WRAPPER + "x"]:
            assert isinstance(unwrap_mcp_result(raw), dict)

    def test_高德的error字段能读出来(self):
        """高德出错时返回的是 {"error": "..."} —— 要能识别,而不是当成正常结果。"""
        raw = WRAPPER + '{"error": "Text Search failed: INVALID_USER_KEY"}'
        assert unwrap_mcp_result(raw).get("error") == "Text Search failed: INVALID_USER_KEY"


# ===========================================================================
# parse_location
# ===========================================================================


class TestParseLocation:
    def test_拆分高德的坐标字符串(self):
        assert parse_location("116.397,39.918") == [116.397, 39.918]

    def test_第一个是经度不是纬度(self):
        """**顺序搞反了坐标会跑到地球另一边**,而且不报错。

        北京是 (东经 116, 北纬 39)。如果实现把两个数对调,
        (39.9, 116.4) 会落在蒙古国境内 —— 仍然是个"合法"坐标。
        这条测试把顺序钉死。
        """
        lon, lat = parse_location("116.397,39.918")
        assert 115 < lon < 118, f"第一个数应是经度(约 116),实际 {lon}"
        assert 39 < lat < 41, f"第二个数应是纬度(约 39),实际 {lat}"

    def test_接受已拆好的列表(self):
        """重复调用要安全 —— 缓存里的坐标可能已经被转成列表了。"""
        assert parse_location([116.397, 39.918]) == [116.397, 39.918]
        assert parse_location((116.397, 39.918)) == [116.397, 39.918]

    @pytest.mark.parametrize(
        "bad",
        [
            "116.397",          # 只有一个数
            "116.397,39.918,1", # 三个数
            "abc,def",
            "",
            "  ",
            None,
            123,
            {},
            [],
            [1.0],              # 长度不对
            ["a", "b"],
        ],
    )
    def test_解析不出来返回None(self, bad):
        """**不能返回 [0.0, 0.0]。** 那是个合法的坐标(几内亚湾海上),
        会被当成真数据一路用下去 —— 比明确地"没有"糟得多。"""
        assert parse_location(bad) is None

    def test_整数也能处理(self):
        assert parse_location("116,39") == [116.0, 39.0]


class TestFormatLocation:
    def test_拼回高德要的格式(self):
        assert format_location(116.397, 39.918) == "116.397,39.918"

    def test_和parse互为逆运算(self):
        s = format_location(116.397, 39.918)
        assert parse_location(s) == [116.397, 39.918]


# ===========================================================================
# 幂等:已经解析过的 dict 再传进来
# ===========================================================================


class TestUnwrapIdempotent:
    def test_dict直接原样返回(self):
        """**这条不是防呆。**

        调用链上经常先 `unwrap_mcp_result(raw)` 拿到 dict,再交给
        `extract_xxx()` —— 而 extract_xxx() 内部还会再剥一次壳。
        第二次拿到的是 Python 的 `repr`(单引号、True/None),不是 JSON,
        精确解析和 raw_decode 都会失败,最后落进 `_raw` 分支 ——
        表现为"数据明明在,却解析不出来"。
        """
        payload = {"pois": [{"id": "B1", "name": "故宫"}]}
        assert unwrap_mcp_result(payload) is payload
        assert unwrap_mcp_result(payload) == payload

    def test_所有提取函数都能吃已解析的dict(self):
        """把上面那条契约在**每个** extract_* 上钉一遍 ——
        漏掉一个,那个函数就会静默返回空。"""
        assert extract_pois({"pois": [{"id": "B1", "name": "故宫", "address": "x"}]})
        assert extract_poi_detail({"id": "B1", "name": "故宫"})["name"] == "故宫"
        assert extract_geocode({"geocodes": [{"location": "116.4,39.9"}]}) == [116.4, 39.9]
        assert extract_route({"route": {"paths": [{"distance": "10", "duration": "20"}]}})
        assert extract_weather({"lives": [{"weather": "晴"}]})


# ===========================================================================
# normalize_str_list / first_str
# ===========================================================================


class TestNormalizeStrList:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            # ---- 真实数据里的形态(1751 条 detail 响应实测) ----
            ("紫禁城", ["紫禁城"]),
            ("中国历史博物馆|北京历史博物馆", ["中国历史博物馆", "北京历史博物馆"]),
            ([], []),
            (None, []),
            # ---- 防御性形态 ----
            ("", []),
            ("  ", []),
            ("  紫禁城  ", ["紫禁城"]),
            (["a", "b"], ["a", "b"]),
            (["a|b", "c"], ["a", "b", "c"]),
            (123, []),  # 不认识的类型返回空,而不是硬转
        ],
    )
    def test_各种形态(self, raw, expected):
        assert normalize_str_list(raw) == expected

    def test_字符串形态必须被认(self):
        """这个坑上次让「灌库」整整一批别名没进去,而且**单测全绿** ——
        因为当时测的是 `["紫禁城"]`(我以为的形状),不是真实的 `"紫禁城"`。
        这条钉住真实形态。"""
        assert normalize_str_list("紫禁城") == ["紫禁城"]
        assert normalize_str_list("故宫博物院") == ["故宫博物院"]


class TestFirstStr:
    def test_取列表里的第一个(self):
        assert first_str(["4.9", "4.8"]) == "4.9"

    def test_字符串原样返回(self):
        assert first_str("4.9") == "4.9"

    def test_数字标量不丢(self):
        """`normalize_str_list` 对数字返回空是刻意的契约,
        但 rating 万一变成 4.9 时不该被丢掉。"""
        assert first_str(4.9) == "4.9"

    def test_空值走默认(self):
        assert first_str(None) == ""
        assert first_str([], "N/A") == "N/A"
        assert first_str("", "N/A") == "N/A"


# ===========================================================================
# mcp_error_message —— 「失败但返回一切正常」的那一类
# ===========================================================================


class TestMcpErrorMessage:
    def test_error字段(self):
        raw = WRAPPER + '{"error": "Text Search failed: INVALID_USER_KEY"}'
        assert "INVALID_USER_KEY" in mcp_error_message(raw)

    def test_高德自己的失败形态(self):
        """高德原生的失败是 `status=0` + `info`,**不带 error 字段**。
        只查 error 的话这一种会一路走通,变成「success=true, 0 条」。"""
        raw = WRAPPER + '{"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"}'
        assert "INVALID_USER_KEY" in mcp_error_message(raw)

    def test_成功时返回空串(self):
        raw = WRAPPER + '{"status": "1", "info": "OK", "pois": []}'
        assert mcp_error_message(raw) == ""

    def test_没有结果不等于失败(self):
        """**这是关键区分。**「搜到 0 条」是正常业务结果,
        不能判成失败 —— 否则一个没景点的小城会报 500。"""
        assert mcp_error_message(WRAPPER + '{"pois": [], "suggestion": {}}') == ""

    def test_解析不了的纯文本不算失败(self):
        """报错文本解析不出结构,交给上层按"没结果"处理;
        这里不猜。"""
        assert mcp_error_message(WRAPPER + "连接超时") == ""


# ===========================================================================
# extract_pois
# ===========================================================================


class TestExtractPois:
    def test_取出基本字段(self):
        raw = WRAPPER + json.dumps(
            {
                "suggestion": {"keywords": [], "cities": []},
                "pois": [
                    {"id": "B1", "name": "天安门广场", "address": "东长安街", "typecode": "110210"},
                    {"id": "B2", "name": "故宫博物院", "address": "景山前街4号", "typecode": "110201"},
                ],
            },
            ensure_ascii=False,
        )
        got = extract_pois(raw)
        assert len(got) == 2
        assert got[0]["name"] == "天安门广场"
        assert got[1]["typecode"] == "110201"

    def test_关键词搜索不返回坐标(self):
        """真实数据 1924 条 POI 的字段固定是 {id,name,address,typecode} ——
        没有 location。这里必须如实给 None,**不能补 [0,0]**
        (那是个在几内亚湾海上的合法坐标,比没有更糟)。"""
        raw = WRAPPER + '{"pois": [{"id": "B1", "name": "故宫", "address": "x", "typecode": "1"}]}'
        got = extract_pois(raw)
        assert got[0]["location"] is None

    def test_有坐标时也能解析(self):
        raw = WRAPPER + '{"pois": [{"id": "B1", "name": "故宫", "location": "116.397,39.918"}]}'
        assert extract_pois(raw)[0]["location"] == [116.397, 39.918]

    def test_没有结果返回空列表而不是None(self):
        """调用方要能直接 `for p in ...`,不必再判 None。"""
        assert extract_pois(WRAPPER + '{"pois": []}') == []
        assert extract_pois("") == []
        assert extract_pois(None) == []

    def test_pois不是列表时不炸(self):
        assert extract_pois(WRAPPER + '{"pois": "坏数据"}') == []
        assert extract_pois(WRAPPER + '{"pois": [1, 2, "x"]}') == []


# ===========================================================================
# extract_poi_detail
# ===========================================================================


class TestExtractPoiDetail:
    def _detail(self, **over) -> dict:
        base = {
            "id": "B000A83C1S",
            "name": "天安门广场",
            "location": "116.397755,39.903182",
            "address": "东长安街",
            "business_area": "天安门广场",
            "city": "北京市",
            "type": "风景名胜;风景名胜;红色景区|风景名胜;公园广场;城市广场",
            "alias": [],
            "cost": [],
            "opentime2": "周一至周日 05:00-22:00",
            "level": [],
            "rating": "4.9",
            "open_time": "05:00-22:00",
            "ticket_ordering": "0",
        }
        base.update(over)
        return base

    def test_归一化坐标(self):
        got = extract_poi_detail(WRAPPER + json.dumps(self._detail(), ensure_ascii=False))
        assert got["location"] == [116.397755, 39.903182]

    def test_别名字符串形态要认(self):
        """真实数据里 240 条有别名,**全部是字符串**;1510 条是空列表。"""
        got = extract_poi_detail(
            WRAPPER + json.dumps(self._detail(alias="紫禁城"), ensure_ascii=False)
        )
        assert got["aliases"] == ["紫禁城"]

    def test_别名列表形态要认(self):
        got = extract_poi_detail(
            WRAPPER + json.dumps(self._detail(alias=["a", "b"]), ensure_ascii=False)
        )
        assert got["aliases"] == ["a", "b"]

    def test_rating是列表时也能取出来(self):
        """防御性用例 —— 注意它**不是**真实数据的形态。

        实测 1751 条里 rating 的列表形态**全是空的**(见下面
        TestAgainstRealPoiDetail 那条被真实数据打脸后改写的测试)。
        这里保留它只是为了钉住"万一哪天列表里真装了值,别取成 `['4.5']`"。
        """
        got = extract_poi_detail(
            WRAPPER + json.dumps(self._detail(rating=["4.5"]), ensure_ascii=False)
        )
        assert got["rating"] == "4.5"

    def test_坐标缺失时是None不是0(self):
        got = extract_poi_detail(
            WRAPPER + json.dumps(self._detail(location=None), ensure_ascii=False)
        )
        assert got["location"] is None

    def test_open_time缺失时退回opentime2(self):
        """真实的 7 种字段组合里有 559 条**没有** open_time,只有 opentime2。"""
        d = self._detail()
        del d["open_time"]
        got = extract_poi_detail(WRAPPER + json.dumps(d, ensure_ascii=False))
        assert got["open_time"] == "周一至周日 05:00-22:00"

    def test_字段缺失不炸(self):
        got = extract_poi_detail(WRAPPER + '{"id": "B1", "name": "只有名字"}')
        assert got["name"] == "只有名字"
        assert got["aliases"] == []
        assert got["location"] is None

    def test_空输入返回空字典(self):
        assert extract_poi_detail("") == {}
        assert extract_poi_detail(WRAPPER) == {}


# ===========================================================================
# extract_weather
# ===========================================================================
#
# ⚠️ 本地**没有**录制天气样本(冻结缓存里只有 text_search 与 search_detail),
# 所以下面用的是高德官方文档里的两种返回结构。这是「按接口契约测」,
# 与上面那几个「按真实录制数据测」不是一回事 —— 别把两者混为一谈。


class TestExtractWeather:
    def test_base实况形态(self):
        raw = WRAPPER + json.dumps(
            {
                "status": "1",
                "count": "1",
                "lives": [
                    {
                        "province": "北京",
                        "city": "北京市",
                        "weather": "晴",
                        "temperature": "25",
                        "winddirection": "西北",
                        "windpower": "≤3",
                        "humidity": "40",
                        "reporttime": "2025-06-01 15:00:00",
                    }
                ],
            },
            ensure_ascii=False,
        )
        got = extract_weather(raw)
        assert len(got) == 1
        assert got[0]["date"] == "2025-06-01"
        assert got[0]["day_weather"] == "晴"
        assert got[0]["day_temp"] == "25"
        assert got[0]["wind_direction"] == "西北"

    def test_all预报形态(self):
        raw = WRAPPER + json.dumps(
            {
                "forecasts": [
                    {
                        "city": "北京市",
                        "casts": [
                            {
                                "date": "2025-06-01",
                                "dayweather": "晴",
                                "nightweather": "多云",
                                "daytemp": "28",
                                "nighttemp": "18",
                                "daywind": "北",
                                "daypower": "1-3",
                            },
                            {
                                "date": "2025-06-02",
                                "dayweather": "阴",
                                "nightweather": "小雨",
                                "daytemp": "26",
                                "nighttemp": "17",
                                "daywind": "东",
                                "daypower": "3-4",
                            },
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )
        got = extract_weather(raw)
        assert [d["date"] for d in got] == ["2025-06-01", "2025-06-02"]
        assert got[1]["night_weather"] == "小雨"

    def test_没数据返回空列表(self):
        assert extract_weather(WRAPPER + '{"status": "0"}') == []
        assert extract_weather("") == []

    def test_结果能直接喂给WeatherInfo(self):
        """提取出的字段名必须和 models/schemas.py 的 WeatherInfo 对得上 ——
        对不上会在构造模型时才炸,那时候已经离出错点很远了。"""
        from app.models.schemas import WeatherInfo

        raw = WRAPPER + json.dumps(
            {"lives": [{"weather": "晴", "temperature": "25°C", "reporttime": "2025-06-01 12:00:00"}]},
            ensure_ascii=False,
        )
        info = WeatherInfo(**extract_weather(raw)[0])
        assert info.day_temp == 25  # 校验器会把 °C 剥掉
        assert info.date == "2025-06-01"


# ===========================================================================
# extract_route(同样:按官方文档的结构)
# ===========================================================================


class TestExtractRoute:
    def test_步行驾车形态_paths(self):
        raw = WRAPPER + json.dumps(
            {
                "status": "1",
                "route": {
                    "origin": "116.4,39.9",
                    "destination": "116.5,39.95",
                    "paths": [{"distance": "1234", "duration": "900", "steps": [{}, {}]}],
                },
            }
        )
        got = extract_route(raw)
        assert got["distance"] == 1234.0
        assert got["duration"] == 900
        assert got["steps"] == 2

    def test_公交形态_transits(self):
        """公交返回的是 `route.transits[]`,**结构不同**;而且 distance 在
        route 层、不在 transits[0] 里。只认 paths 的话公交会恒定"查不到"。"""
        raw = WRAPPER + json.dumps(
            {
                "route": {
                    "distance": "8000",
                    "transits": [{"duration": "2400", "walking_distance": "700"}],
                }
            }
        )
        got = extract_route(raw)
        assert got["distance"] == 8000.0
        assert got["duration"] == 2400

    def test_查不到路线返回None(self):
        """**不能返回一条全 0 的假路线** —— 那会被当成"两地相距 0 米"。"""
        assert extract_route(WRAPPER + '{"status": "0", "info": "no route"}') is None
        assert extract_route(WRAPPER + '{"route": {}}') is None
        assert extract_route(WRAPPER + '{"route": {"paths": []}}') is None
        assert extract_route("") is None

    def test_数值是脏数据时不炸(self):
        raw = WRAPPER + '{"route": {"paths": [{"distance": "abc", "duration": null}]}}'
        got = extract_route(raw)
        assert got["distance"] == 0.0
        assert got["duration"] == 0


# ===========================================================================
# extract_geocode(同样:按官方文档的结构)
# ===========================================================================


class TestExtractGeocode:
    def test_取出第一条坐标(self):
        raw = WRAPPER + json.dumps(
            {"status": "1", "geocodes": [{"formatted_address": "北京市", "location": "116.4,39.9"}]}
        )
        assert extract_geocode(raw) == [116.4, 39.9]

    def test_跳过没有坐标的条目(self):
        raw = WRAPPER + '{"geocodes": [{"location": ""}, {"location": "116.4,39.9"}]}'
        assert extract_geocode(raw) == [116.4, 39.9]

    def test_解析不出返回None(self):
        assert extract_geocode(WRAPPER + '{"geocodes": []}') is None
        assert extract_geocode(WRAPPER + '{"geocodes": [{"location": "乱码"}]}') is None
        assert extract_geocode("") is None


# ===========================================================================
# 文案格式化
# ===========================================================================


class TestFormatHelpers:
    @pytest.mark.parametrize(
        "meters,expected",
        [(0, "0 米"), (500, "500 米"), (999, "999 米"), (1000, "1.0 公里"), (8500, "8.5 公里")],
    )
    def test_距离(self, meters, expected):
        assert format_distance(meters) == expected

    @pytest.mark.parametrize(
        "seconds,expected",
        [(0, "1 分钟"), (60, "1 分钟"), (900, "15 分钟"), (3600, "1 小时 0 分钟"), (5400, "1 小时 30 分钟")],
    )
    def test_耗时(self, seconds, expected):
        assert format_duration(seconds) == expected


# ===========================================================================
# 拿**真实录制的高德响应**反过来验
# ===========================================================================
#
# 与 `tests/test_knowledge.py::TestAliasesAgainstRealData` 同一个思路:
# 上面那些构造出来的用例只能验证「我以为是这个形状」;
# 只有读 `data/frozen/amap/raw_cache.json` 里真实录下来的响应,
# 才能验证「实际是这个形状」。
#
# 这是被两个事故教出来的做法:
#   - alias 的字符串/列表不一致(构造测试全绿,一碰真数据就露馅)
#   - 城市范围框把度分当小数读(同上)


def _raw_cache() -> dict:
    path = resolve_dir(get_settings().frozen_dir) / "amap" / "raw_cache.json"
    if not path.exists():
        pytest.skip(f"没有冻结缓存: {path}")
    return json.loads(path.read_text(encoding="utf-8")).get("entries") or {}


@pytest.fixture(scope="module")
def real_text_search():
    return {k: v["raw"] for k, v in _raw_cache().items() if k.startswith("maps_text_search|")}


@pytest.fixture(scope="module")
def real_details():
    return {k: v["raw"] for k, v in _raw_cache().items() if k.startswith("maps_search_detail|")}


class TestAgainstRealTextSearch:
    """100 条真实关键词搜索响应,共 1900+ 个 POI。"""

    def test_每条响应都能取出POI(self, real_text_search):
        """**这一条如果失败,说明我对响应结构的假设是错的。**
        之前那种「解析不出来就返回空列表」的实现,在这里会全量返回 0 条,
        而接口仍然回 success=true —— 用户看到的是"这城市没景点"。"""
        assert real_text_search, "冻结缓存里没有 text_search 响应"
        empty = [k for k, raw in real_text_search.items() if not extract_pois(raw)]
        assert not empty, f"有 {len(empty)} 条响应解析不出任何 POI,例如:{empty[:3]}"

    def test_每个POI都有id和name(self, real_text_search):
        missing = [
            item
            for raw in real_text_search.values()
            for item in extract_pois(raw)
            if not item["id"] or not item["name"]
        ]
        assert not missing, f"有 {len(missing)} 个 POI 缺 id/name:{missing[:3]}"

    def test_关键词搜索确实不带坐标(self, real_text_search):
        """把「搜索不给坐标」这个事实钉住。哪天高德改了、或者换了工具,
        这条会红 —— 那时 amap_service.search_poi 里 `location=None` 的
        说明和 POIInfo 的可选字段就该重新审一遍了。"""
        total = 0
        with_location = 0
        for raw in real_text_search.values():
            for item in extract_pois(raw):
                total += 1
                if item["location"] is not None:
                    with_location += 1
        assert total > 1000, f"样本太少({total}),不足以支撑这个结论"
        assert with_location == 0, f"{total} 个 POI 里有 {with_location} 个带了坐标,假设已失效"


class TestAgainstRealPoiDetail:
    """1751 条真实 POI 详情响应。"""

    def test_每条都能解析出名字(self, real_details):
        """⚠️ 1751 条里有 **1 条本身是失败的响应**,不是数据:

            {"error": "Request failed: ('Connection aborted.',
             ConnectionResetError(10054, '远程主机强迫关闭了一个现有的连接。'))"}

        它是真实存在的 —— 采集时那次请求被重置了。所以这里不能断言
        "每条都有 name",而要断言:**凡是正常响应都有 name,
        凡是失败响应都能被 mcp_error_message 认出来**。

        这恰好是 `mcp_error_message` 存在的理由:少了它,上面那条会被
        当成"一个没有名字的 POI"悄悄混进结果里。
        """
        assert real_details, "冻结缓存里没有 detail 响应"

        error_entries = [k for k, raw in real_details.items() if mcp_error_message(raw)]
        ok_entries = [k for k, raw in real_details.items() if not mcp_error_message(raw)]

        assert ok_entries, "样本里居然没有一条正常响应,采样逻辑有问题"

        failed = [k for k in ok_entries if not extract_poi_detail(real_details[k]).get("name")]
        assert not failed, f"有 {len(failed)} 条正常响应解析不出 name,例如:{failed[:3]}"

        # 失败的那条必须被识别出来,而且不该被当成一条正常数据
        for key in error_entries:
            assert extract_poi_detail(real_details[key]).get("name") in (None, ""), (
                "失败响应不该解析出 name"
            )

    def test_有坐标的条目坐标都能解析出来(self, real_details):
        """**这条是接地性的核心。** 坐标解析不出来不会报错,
        只会让景点没有位置 —— 静默降级。"""
        broken: list[str] = []
        checked = 0
        for key, raw in real_details.items():
            raw_obj = unwrap_mcp_result(raw)
            if not raw_obj.get("location"):
                continue
            checked += 1
            if extract_poi_detail(raw)["location"] is None:
                broken.append(key)
        assert checked > 1500, f"带坐标的样本太少({checked})"
        assert not broken, f"有 {len(broken)} 条坐标解析失败,例如:{broken[:3]}"

    def test_字符串形态的别名一条都没漏(self, real_details):
        """**这条直接对应那次 alias 事故。**

        真实数据里带别名的条目**全部是字符串形态**(列表形态全是空的)。
        所以「只认 list/tuple」的实现在这里会**全量失败** ——
        而它在上面的构造用例里是全绿的。
        """
        expected_alias_total = 0
        missing: list[str] = []
        for key, raw in real_details.items():
            raw_obj = unwrap_mcp_result(raw)
            raw_alias = raw_obj.get("alias")
            if isinstance(raw_alias, str) and raw_alias.strip():
                expected_alias_total += 1
                if not extract_poi_detail(raw)["aliases"]:
                    missing.append(key)
        assert expected_alias_total > 100, (
            f"字符串别名只有 {expected_alias_total} 条,预期上百条 —— "
            "数据变了,这条测试的前提需要重看"
        )
        assert not missing, f"有 {len(missing)} 条字符串别名被漏掉,例如:{missing[:3]}"

    def test_别名内容真的带出来了(self, real_details):
        """拿具体景点验一次内容,而不是只看"非空"。"""
        by_name = {
            unwrap_mcp_result(raw).get("name"): extract_poi_detail(raw)
            for raw in real_details.values()
        }
        gugong = by_name.get("故宫博物院")
        assert gugong is not None, "样本里应该有故宫博物院"
        assert "紫禁城" in gugong["aliases"], f"故宫的别名没带出来:{gugong['aliases']}"

    def test_rating的列表形态全是空的_数据在字符串那边(self, real_details):
        """**这条是先写了错断言、被真实数据打脸之后改过来的。**

        我原本以为 rating 和 alias 一样是"有数据的在列表、空的在字符串",
        所以断言"存在非空列表形态的 rating"。跑下来 `list_rating == 0`。

        实测真相:1693 条字符串 / 57 条**空列表** / 1 条 None ——
        **不存在非空列表**,数据全在字符串那边。

        于是这条测试真正该钉住的是两件事:
        1. 所有非空 rating 都是字符串,且都被完整带出(不能丢)
        2. 空列表归一成空串,而**不是** `"[]"` 这种字符串化垃圾
        """
        nonempty_str = 0
        empty_list_seen = 0
        for key, raw in real_details.items():
            raw_rating = unwrap_mcp_result(raw).get("rating")
            got = extract_poi_detail(real_details[key])["rating"]

            if isinstance(raw_rating, str) and raw_rating.strip():
                nonempty_str += 1
                assert got == raw_rating, f"{key} 的 rating 被改写了:{got!r} != {raw_rating!r}"
            elif isinstance(raw_rating, list):
                empty_list_seen += 1
                assert raw_rating == [], f"{key}:列表形态的 rating 居然非空({raw_rating!r}),假设已失效"
                assert got == "", f"{key} 的空列表 rating 变成了 {got!r}"

        assert nonempty_str > 1000, f"样本里只有 {nonempty_str} 条非空 rating,采样有问题"
        assert empty_list_seen > 0, "样本里没有空列表 rating —— 这条测试的前提需要重看"
