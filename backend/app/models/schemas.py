"""数据模型定义"""

from typing import List, Optional, Union
from pydantic import BaseModel, Field, field_validator
from datetime import date


# ============ 请求模型 ============

class TripRequest(BaseModel):
    """旅行规划请求"""
    city: str = Field(..., description="目的地城市", example="北京")
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD", example="2025-06-01")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD", example="2025-06-03")
    travel_days: int = Field(..., description="旅行天数", ge=1, le=30, example=3)
    transportation: str = Field(..., description="交通方式", example="公共交通")
    accommodation: str = Field(..., description="住宿偏好", example="经济型酒店")
    preferences: List[str] = Field(default=[], description="旅行偏好标签", example=["历史文化", "美食"])
    free_text_input: Optional[str] = Field(default="", description="额外要求", example="希望多安排一些博物馆")
    
    class Config:
        json_schema_extra = {
            "example": {
                "city": "北京",
                "start_date": "2025-06-01",
                "end_date": "2025-06-03",
                "travel_days": 3,
                "transportation": "公共交通",
                "accommodation": "经济型酒店",
                "preferences": ["历史文化", "美食"],
                "free_text_input": "希望多安排一些博物馆"
            }
        }


class POISearchRequest(BaseModel):
    """POI搜索请求"""
    keywords: str = Field(..., description="搜索关键词", example="故宫")
    city: str = Field(..., description="城市", example="北京")
    citylimit: bool = Field(default=True, description="是否限制在城市范围内")


class RouteRequest(BaseModel):
    """路线规划请求"""
    origin_address: str = Field(..., description="起点地址", example="北京市朝阳区阜通东大街6号")
    destination_address: str = Field(..., description="终点地址", example="北京市海淀区上地十街10号")
    origin_city: Optional[str] = Field(default=None, description="起点城市")
    destination_city: Optional[str] = Field(default=None, description="终点城市")
    route_type: str = Field(default="walking", description="路线类型: walking/driving/transit")


# ============ 响应模型 ============

class Location(BaseModel):
    """地理位置"""
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")


class Attraction(BaseModel):
    """景点信息"""
    name: str = Field(..., description="景点名称")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    visit_duration: int = Field(..., description="建议游览时间(分钟)")
    description: str = Field(..., description="景点描述")
    category: Optional[str] = Field(default="景点", description="景点类别")
    rating: Optional[float] = Field(default=None, description="评分")
    photos: Optional[List[str]] = Field(default_factory=list, description="景点图片URL列表")
    poi_id: Optional[str] = Field(default="", description="POI ID")
    image_url: Optional[str] = Field(default=None, description="图片URL")
    ticket_price: int = Field(default=0, description="门票价格(元)")


class Meal(BaseModel):
    """餐饮信息"""
    type: str = Field(..., description="餐饮类型: breakfast/lunch/dinner/snack")
    name: str = Field(..., description="餐饮名称")
    address: Optional[str] = Field(default=None, description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度坐标")
    description: Optional[str] = Field(default=None, description="描述")
    estimated_cost: int = Field(default=0, description="预估费用(元)")


class Hotel(BaseModel):
    """酒店信息"""
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="", description="酒店地址")
    location: Optional[Location] = Field(default=None, description="酒店位置")
    price_range: str = Field(default="", description="价格范围")
    rating: str = Field(default="", description="评分")
    distance: str = Field(default="", description="距离景点距离")
    type: str = Field(default="", description="酒店类型")
    estimated_cost: int = Field(default=0, description="预估费用(元/晚)")


class DayPlan(BaseModel):
    """单日行程"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_index: int = Field(..., description="第几天(从0开始)")
    description: str = Field(..., description="当日行程描述")
    transportation: str = Field(..., description="交通方式")
    accommodation: str = Field(..., description="住宿")
    hotel: Optional[Hotel] = Field(default=None, description="推荐酒店")
    attractions: List[Attraction] = Field(default=[], description="景点列表")
    meals: List[Meal] = Field(default=[], description="餐饮列表")


class WeatherInfo(BaseModel):
    """天气信息"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_weather: str = Field(default="", description="白天天气")
    night_weather: str = Field(default="", description="夜间天气")
    day_temp: Union[int, str] = Field(default=0, description="白天温度")
    night_temp: Union[int, str] = Field(default=0, description="夜间温度")
    wind_direction: str = Field(default="", description="风向")
    wind_power: str = Field(default="", description="风力")

    @field_validator('day_temp', 'night_temp', mode='before')
    @classmethod
    def parse_temperature(cls, v):
        """解析温度,移除°C等单位"""
        if isinstance(v, str):
            # 移除°C, ℃等单位符号
            v = v.replace('°C', '').replace('℃', '').replace('°', '').strip()
            try:
                return int(v)
            except ValueError:
                return 0
        return v


class Budget(BaseModel):
    """预算信息"""
    total_attractions: int = Field(default=0, description="景点门票总费用")
    total_hotels: int = Field(default=0, description="酒店总费用")
    total_meals: int = Field(default=0, description="餐饮总费用")
    total_transportation: int = Field(default=0, description="交通总费用")
    total: int = Field(default=0, description="总费用")


class TripPlan(BaseModel):
    """旅行计划"""
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    days: List[DayPlan] = Field(..., description="每日行程")
    weather_info: List[WeatherInfo] = Field(default=[], description="天气信息")
    overall_suggestions: str = Field(..., description="总体建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息")


class TripPlanResponse(BaseModel):
    """旅行计划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[TripPlan] = Field(default=None, description="旅行计划数据")
    # 前端靠它跳 /result/{plan_id}、拼分享链接、以及之后回写编辑。
    # 没有这个字段的话前端只能跳 /result 并靠 sessionStorage 传数据,
    # 那样一刷新、一换标签页就丢,也做不出分享。
    plan_id: Optional[str] = Field(default=None, description="行程 ID,也是分享链接里的 id")


class POIInfo(BaseModel):
    """POI信息"""
    id: str = Field(..., description="POI ID")
    name: str = Field(..., description="名称")
    type: str = Field(..., description="类型")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    tel: Optional[str] = Field(default=None, description="电话")


class POISearchResponse(BaseModel):
    """POI搜索响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[POIInfo] = Field(default=[], description="POI列表")


class RouteInfo(BaseModel):
    """路线信息"""
    distance: float = Field(..., description="距离(米)")
    duration: int = Field(..., description="时间(秒)")
    route_type: str = Field(..., description="路线类型")
    description: str = Field(..., description="路线描述")


class RouteResponse(BaseModel):
    """路线规划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[RouteInfo] = Field(default=None, description="路线信息")


class WeatherResponse(BaseModel):
    """天气查询响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[WeatherInfo] = Field(default=[], description="天气信息")


# ============ 持久化 / 历史行程 ============

class PlanSummary(BaseModel):
    """历史列表里的一条。

    刻意【不含】完整行程(plan_json 可能几十 KB),只带列表要显示的信息
    和两个从行程里算出来的数字。
    """

    id: str = Field(..., description="行程 ID,也是分享链接里的 id")
    title: Optional[str] = Field(default=None, description="展示标题,如「北京 3日游」")
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    travel_days: int = Field(..., description="天数")
    status: str = Field(..., description="running/ok/fallback/error")
    created_at: str = Field(..., description="创建时间 ISO8601")
    updated_at: str = Field(..., description="更新时间 ISO8601")

    # 成本
    total_tokens: int = Field(default=0, description="总 token 数")
    cost_cny: float = Field(default=0.0, description="花费(元)")
    latency_ms: int = Field(default=0, description="生成耗时(毫秒)")
    llm_calls: int = Field(default=0, description="LLM 调用次数")
    usage_source: Optional[str] = Field(default=None, description="api / estimated")

    # 从行程里算出来的摘要
    warnings: List[str] = Field(default_factory=list, description="降级原因")
    attractions: int = Field(default=0, description="景点总数")
    total_budget: int = Field(default=0, description="行程总预算(元)")


class PlanStats(BaseModel):
    """历史页顶部的聚合数字。"""

    total: int = Field(default=0, description="行程总数")
    cost_cny: float = Field(default=0.0, description="累计花费(元)")
    total_tokens: int = Field(default=0, description="累计 token")
    llm_calls: int = Field(default=0, description="累计 LLM 调用次数")


class PlanListResponse(BaseModel):
    """历史列表响应。"""

    success: bool = Field(default=True)
    message: str = Field(default="")
    data: List[PlanSummary] = Field(default_factory=list)
    stats: Optional[PlanStats] = Field(default=None, description="聚合统计")


class KnowledgeSource(BaseModel):
    """生成这份行程时,知识库给出的一条参考出处。

    存在的意义:**让「AI 引用了外部知识」这件事变得可见**。
    没有它,用户看到「故宫需要提前 7 天预约」时无法判断
    这是模型编的还是知识库里确有其事 —— 而这恰恰是 RAG 全部的价值所在。
    """

    namespace: str = Field(default="", description="poi_facts | city_guides")
    source: str = Field(default="", description="文件名 / poi_inventory.json")
    heading_path: str = Field(default="", description="章节路径,如「门票与预约」")
    score: float = Field(default=0.0, description="COSINE 相似度")
    snippet: str = Field(default="", description="内容片段(已截断)")


class PlanDetailResponse(BaseModel):
    """单个行程的详情。

    data 直接是 TripPlan —— 前端可以复用渲染逻辑,不必为历史详情另写一套。
    meta 装 id / 时间 / 成本这些"行程本身之外"的信息。
    knowledge 装这次生成用到的知识出处。
    """

    success: bool = Field(default=True)
    message: str = Field(default="")
    data: Optional[TripPlan] = Field(default=None)
    meta: Optional[PlanSummary] = Field(default=None)
    knowledge: List[KnowledgeSource] = Field(
        default_factory=list,
        description="这次生成检索到的知识出处(按相关度降序)。没开 RAG 或没命中时为空数组",
    )


# ============ 错误响应 ============

class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = Field(default=False, description="是否成功")
    message: str = Field(..., description="错误消息")
    error_code: Optional[str] = Field(default=None, description="错误代码")

