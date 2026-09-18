"""地图服务API路由"""

from fastapi import APIRouter, HTTPException, Query
from ...models.schemas import (
    POISearchResponse,
    RouteRequest,
    RouteResponse,
    WeatherResponse
)
from ..errors import handle_service_errors
from ...services.amap_service import get_amap_service

router = APIRouter(prefix="/map", tags=["地图服务"])


@router.get(
    "/poi",
    response_model=POISearchResponse,
    summary="搜索POI",
    description="根据关键词搜索POI(兴趣点)"
)
@handle_service_errors("POI搜索")
async def search_poi(
    keywords: str = Query(..., description="搜索关键词", example="故宫"),
    city: str = Query(..., description="城市", example="北京"),
    citylimit: bool = Query(True, description="是否限制在城市范围内")
):
    """
    搜索POI

    Args:
        keywords: 搜索关键词
        city: 城市
        citylimit: 是否限制在城市范围内

    Returns:
        POI搜索结果
    """
    service = get_amap_service()
    pois = service.search_poi(keywords, city, citylimit)

    return POISearchResponse(
        success=True,
        message=f"共找到 {len(pois)} 个 POI",
        data=pois
    )


@router.get(
    "/weather",
    response_model=WeatherResponse,
    summary="查询天气",
    description="查询指定城市的天气信息"
)
@handle_service_errors("天气查询")
async def get_weather(
    city: str = Query(..., description="城市名称", example="北京")
):
    """
    查询天气

    Args:
        city: 城市名称

    Returns:
        天气信息
    """
    service = get_amap_service()
    weather_info = service.get_weather(city)

    return WeatherResponse(
        success=True,
        message=f"共 {len(weather_info)} 天预报",
        data=weather_info
    )


@router.post(
    "/route",
    response_model=RouteResponse,
    summary="规划路线",
    description="规划两点之间的路线。查不到路线时返回 success=false,而不是空 data"
)
@handle_service_errors("路线规划")
async def plan_route(request: RouteRequest):
    """
    规划路线

    Args:
        request: 路线规划请求

    Returns:
        路线信息
    """
    service = get_amap_service()
    route_info = service.plan_route(
        origin_address=request.origin_address,
        destination_address=request.destination_address,
        origin_city=request.origin_city,
        destination_city=request.destination_city,
        route_type=request.route_type
    )

    if route_info is None:
        # 高德查不到路线(地址写错、两地无法步行到达)是**业务结果**不是异常。
        # 回 success=false 让调用方能区分"没查到"和"服务坏了"。
        return RouteResponse(
            success=False,
            message="未查到路线,请检查起点与终点地址",
            data=None
        )

    return RouteResponse(
        success=True,
        message="路线规划成功",
        data=route_info
    )


@router.get(
    "/health",
    summary="健康检查",
    description="检查地图服务是否正常"
)
async def health_check():
    """健康检查

    ⚠️ 这个端点刻意**不**用 `handle_service_errors`:
    它失败时应该回 503(服务不可用),而不是统一的 500。
    503 是给负载均衡/监控看的语义,不能和普通业务异常混在一起。
    """
    try:
        service = get_amap_service()
        return {
            "status": "healthy",
            "service": "map-service",
            "mcp_tools_count": len(service.mcp_tool._available_tools)
        }
    except Exception as e:
        # 这里带上原因:健康检查的读者是运维/开发者,不是终端用户
        raise HTTPException(
            status_code=503,
            detail=f"服务不可用: {type(e).__name__}"
        ) from e
