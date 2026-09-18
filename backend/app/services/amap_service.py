"""高德地图MCP服务封装

这一层负责三件事:起 MCP 工具、调工具、把返回值转成项目的强类型模型。
**解析本身不在这里** —— 剥壳、拆坐标、归一化类型不一致的字段都在
`amap_parsing.py`,那里是纯函数、有单测、并且和 recorder.py 共用。
这样"怎么解析高德的返回"只有一个实现,不会两处各踩一遍同一个坑。

⚠️ 失败为什么一律往外抛
---------------------
以前每个方法都 `except Exception: print(...); return []`。后果是调用方
分不清"真的没搜到"和"调用失败了",于是接口恒定回 `success=true, 0 条`,
而真实原因(key 失效、MCP 起不来)被吞在 print 里 —— 与「MCP 工具静默
变成 0 个」是同一类故障。

现在:调用失败 → 抛异常 → 由路由层的统一错误边界
(`app/api/errors.py`)记完整堆栈并回一句规范化的 500。
**"调用成功但没有结果"才返回空列表** —— 那是正常的业务结果。
"""

from typing import List, Optional

from hello_agents.tools import MCPTool

from ..config import get_settings
from ..models.schemas import Location, POIInfo, RouteInfo, WeatherInfo
from . import amap_parsing as ap
from .mcp_launcher import resolve_uvx_command

# 全局MCP工具实例
_amap_mcp_tool = None


def get_amap_mcp_tool() -> MCPTool:
    """
    获取高德地图MCP工具实例(单例模式)

    Returns:
        MCPTool实例
    """
    global _amap_mcp_tool

    if _amap_mcp_tool is None:
        settings = get_settings()

        if not settings.amap_api_key:
            raise ValueError("高德地图API Key未配置,请在.env文件中设置AMAP_API_KEY")

        # 创建MCP工具
        _amap_mcp_tool = MCPTool(
            name="amap",
            description="高德地图服务,支持POI搜索、路线规划、天气查询等功能",
            # 绝对路径找 uvx —— 不靠 PATH,所以没激活 venv 也能起来。
            # 详见 mcp_launcher.py 的说明。
            server_command=resolve_uvx_command(),
            env={"AMAP_MAPS_API_KEY": settings.amap_api_key},
            auto_expand=True  # 自动展开为独立工具
        )

        tool_count = len(_amap_mcp_tool._available_tools)
        print(f"✅ 高德地图MCP工具初始化成功")
        print(f"   工具数量: {tool_count}")
        # 数量为 0 是**静默失败**的典型信号:服务照常起、接口照常 200,
        # 但 agent 会转而编造景点。这里明确喊一声,别让人只看"✅"就放心。
        if tool_count == 0:
            print("   ⚠️  工具数量为 0 —— 高德 MCP 没有展开成功,请检查 AMAP_API_KEY 与 uvx")

        # 打印可用工具列表
        if _amap_mcp_tool._available_tools:
            print("   可用工具:")
            for tool in _amap_mcp_tool._available_tools[:5]:  # 只打印前5个
                print(f"     - {tool.get('name', 'unknown')}")
            if tool_count > 5:
                print(f"     ... 还有 {tool_count - 5} 个工具")

    return _amap_mcp_tool


class AmapService:
    """高德地图服务封装类"""

    def __init__(self):
        """初始化服务"""
        self.mcp_tool = get_amap_mcp_tool()

    def _call(self, tool_name: str, arguments: dict) -> dict:
        """调一个 MCP 工具,剥壳 + 查错,返回归一化后的 dict。

        所有方法的公共前两步。抽出来是为了让"调工具"这套协议
        (`action` / `tool_name` / `arguments` 三层结构,以及返回是文本
        而不是 JSON)只出现一次。

        Raises:
            RuntimeError: MCP 返回体里带着 error / status=0 时。
        """
        raw = self.mcp_tool.run(
            {
                "action": "call_tool",
                "tool_name": tool_name,
                "arguments": arguments,
            }
        )
        # 先查错,再解析 —— 顺序反了会把"key 失效"读成"没有结果"
        error = ap.mcp_error_message(raw)
        if error:
            raise RuntimeError(f"高德工具 {tool_name} 返回失败: {error}")
        return ap.unwrap_mcp_result(raw)

    def search_poi(self, keywords: str, city: str, citylimit: bool = True) -> List[POIInfo]:
        """
        搜索POI

        Args:
            keywords: 搜索关键词
            city: 城市
            citylimit: 是否限制在城市范围内

        Returns:
            POI信息列表

        ⚠️ 关键词搜索**不返回坐标**(实测 1924 条样本无一例外),
        所以返回的 `POIInfo.location` 是 `None`。要坐标得拿 id 再调
        `get_poi_detail`。
        """
        obj = self._call(
            "maps_text_search",
            {
                "keywords": keywords,
                "city": city,
                "citylimit": str(citylimit).lower(),
            },
        )

        pois: List[POIInfo] = []
        for item in ap.extract_pois(obj):
            location = item.get("location")
            pois.append(
                POIInfo(
                    id=item["id"],
                    name=item["name"],
                    address=item["address"],
                    typecode=item["typecode"],
                    location=(
                        Location(longitude=location[0], latitude=location[1])
                        if location
                        else None
                    ),
                )
            )
        return pois

    def get_weather(self, city: str) -> List[WeatherInfo]:
        """
        查询天气

        Args:
            city: 城市名称

        Returns:
            天气信息列表
        """
        obj = self._call("maps_weather", {"city": city})
        return [WeatherInfo(**item) for item in ap.extract_weather(obj)]

    def plan_route(
        self,
        origin_address: str,
        destination_address: str,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        route_type: str = "walking"
    ) -> Optional[RouteInfo]:
        """
        规划路线

        Args:
            origin_address: 起点地址
            destination_address: 终点地址
            origin_city: 起点城市
            destination_city: 终点城市
            route_type: 路线类型 (walking/driving/transit)

        Returns:
            路线信息;**查不到路线时返回 None**(不是一条全 0 的假路线)
        """
        # 根据路线类型选择工具
        tool_map = {
            "walking": "maps_direction_walking_by_address",
            "driving": "maps_direction_driving_by_address",
            "transit": "maps_direction_transit_integrated_by_address"
        }
        tool_name = tool_map.get(route_type, "maps_direction_walking_by_address")

        # 城市参数能提高准确度;公共交通**必需**(否则跨城地址解析不出来)
        arguments = {
            "origin_address": origin_address,
            "destination_address": destination_address,
        }
        if origin_city:
            arguments["origin_city"] = origin_city
        if destination_city:
            arguments["destination_city"] = destination_city

        obj = self._call(tool_name, arguments)

        # 复用归一化结果,避免这里再解析一遍(多一处解析就多一处能写错的地方)
        parsed = ap.extract_route(obj)
        if parsed is None:
            return None

        label = {"walking": "步行", "driving": "驾车", "transit": "公共交通"}.get(
            route_type, route_type
        )
        return RouteInfo(
            distance=parsed["distance"],
            duration=parsed["duration"],
            route_type=route_type,
            description=(
                f"{label}约 {ap.format_distance(parsed['distance'])},"
                f"耗时约 {ap.format_duration(parsed['duration'])}"
            ),
        )

    def geocode(self, address: str, city: Optional[str] = None) -> Optional[Location]:
        """
        地理编码(地址转坐标)

        Args:
            address: 地址
            city: 城市

        Returns:
            经纬度坐标;解析不出来返回 None

        ⚠️ 它做的是**地址 → 坐标**的解析,把输入当地址看。
        POI 名不是地址,所以地标名(如"东方明珠")可能解析不出来,
        而"外滩"这种区域名会被解析成行政区划。要判断"景点名是否真实存在"
        请用 `search_poi`,不要用这个。
        """
        arguments: dict = {"address": address}
        if city:
            arguments["city"] = city

        obj = self._call("maps_geo", arguments)
        location = ap.extract_geocode(obj)
        if location is None:
            return None
        return Location(longitude=location[0], latitude=location[1])

    def get_poi_detail(self, poi_id: str) -> dict:
        """
        获取POI详情

        Args:
            poi_id: POI ID

        Returns:
            POI详情信息(已归一化,见 amap_parsing.extract_poi_detail)

        ⚠️ 以前这里用 `re.search(r'\\{.*\\}', result, re.DOTALL)` 抽 JSON ——
        那是**贪婪**匹配,在嵌套对象上会取过头,取过头就解析失败,
        而报的错跟真正的原因看不出关系。现在改用被测过的 unwrap_mcp_result。
        """
        obj = self._call("maps_search_detail", {"id": poi_id})
        return ap.extract_poi_detail(obj)


# 创建全局服务实例
_amap_service = None


def get_amap_service() -> AmapService:
    """获取高德地图服务实例(单例模式)"""
    global _amap_service

    if _amap_service is None:
        _amap_service = AmapService()

    return _amap_service
