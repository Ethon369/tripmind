"""amap_parsing.py 的单元测试 —— 高德返回值的解析

这两个函数看着简单,但坑很实在:
- 剥壳时用贪婪正则会在**嵌套 JSON** 和**后面还跟着花括号**时取过头
- 坐标是 `"经度,纬度"` 字符串,顺序和常见的 "lat,lng" **相反** —— 搞反了
  坐标会跑到地球另一边,而且还是个"合法"坐标,不报错

这类错误不会抛异常,只会静默产出错数据。所以测试要从几个方向都堵一遍。
"""

from __future__ import annotations

import pytest

from app.services.amap_parsing import (
    format_location,
    parse_location,
    unwrap_mcp_result,
)

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
