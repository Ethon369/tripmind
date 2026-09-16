"""地理计算

两个用途
--------
1. **判断一个坐标是否落在目标城市范围内** —— 这是抓"编造坐标"最直接的手段。
   项目现有的 `_create_fallback_plan()` 用 `116.4 + i*0.01` 现算坐标,生成
   上海行程时会得到北京的坐标,整片落到上海范围之外。
2. **算两个坐标之间的距离** —— 用于 `coord_mae_km`(与真实 POI 的平均偏差)
   和 `intraday_travel_km_p95`(单日行程是否排得太赶)。

关于城市范围的精度
------------------
这里用的是**市级行政区的大致外接矩形**,不是精确边界。判断"在不在这个城市"
够用了,但它有两类已知误差,报告里要写清楚:

- **偏宽松**:紧邻市界的真实景点可能被误判为"在范围内"。
- **抓不到"用错城市的坐标"之外的情况**:如果 LLM 编的坐标恰好落在目标城市的
  范围里(比如给北京行程编了一个北京的假坐标),bbox 判定会放行。
  要抓这种只能靠 `coord_mae_km` —— 那需要冻结的真实 POI 做 ground truth。

所以 `coord_coverage` 是一个**低门槛**指标:它接近 1 只说明"坐标没跑到别的城市去",
不等于"坐标是对的"。
"""

from __future__ import annotations

import math

# 市级行政区外接矩形:(最小经度, 最小纬度, 最大经度, 最大纬度)
#
# 数值来自各市行政区划的公开经纬度范围,取外接矩形。新增城市时加在这里;
# 没收录的城市会让 coord_coverage 返回 None(而不是误报 0),报告里会标"未收录"。
CITY_BBOX: dict[str, tuple[float, float, float, float]] = {
    "北京": (115.42, 39.44, 117.51, 41.06),
    "上海": (120.85, 30.68, 122.12, 31.88),
    "成都": (102.54, 30.05, 104.53, 31.44),
    "西安": (107.40, 33.42, 109.49, 34.45),
    "杭州": (118.35, 29.18, 120.50, 30.55),
    "南京": (118.37, 31.23, 119.23, 32.62),
    "广州": (112.95, 22.55, 114.05, 23.95),
    "深圳": (113.75, 22.40, 114.65, 22.90),
    "重庆": (105.29, 28.16, 110.19, 32.20),
    "武汉": (113.70, 29.97, 115.08, 31.36),
    "长沙": (111.89, 27.85, 114.25, 28.68),
    "青岛": (119.30, 35.58, 121.00, 37.16),
    "厦门": (117.88, 24.40, 118.45, 24.90),
    "昆明": (102.17, 24.39, 103.67, 26.55),
    "三亚": (108.60, 18.15, 110.05, 18.68),
}

# 地球平均半径(公里)
_EARTH_RADIUS_KM = 6371.0088


def bbox_of(city: str) -> tuple[float, float, float, float] | None:
    """取城市外接矩形。没收录则返回 None —— 调用方要处理这种情况,不要当成 0。"""
    return CITY_BBOX.get((city or "").strip())


def in_city(longitude: float, latitude: float, city: str) -> bool | None:
    """判断坐标是否落在城市范围内。

    Returns:
        True/False;城市未收录时返回 None(表示"不知道",不是"不在")
    """
    box = bbox_of(city)
    if box is None:
        return None
    min_lon, min_lat, max_lon, max_lat = box
    return min_lon <= longitude <= max_lon and min_lat <= latitude <= max_lat


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """球面距离(公里)。用于坐标偏差与单日行程跨度。

    用 haversine 而不是平面勾股:后者在跨纬度时不准确,而且本项目全程用
    经纬度而不是投影坐标,没有现成的米制平面可用。
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = p2 - p1
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def path_length_km(points: list[tuple[float, float]]) -> float:
    """一串坐标点按顺序连起来的总长度(公里)。

    points 是 [(longitude, latitude), ...]。
    少于两个点返回 0.0。
    """
    if len(points) < 2:
        return 0.0
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(points, points[1:]):
        total += haversine_km(lon1, lat1, lon2, lat2)
    return total
