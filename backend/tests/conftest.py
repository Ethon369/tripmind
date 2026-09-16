"""pytest 的公共配置与测试数据构造器

这里做两件事:

**1. 让 `import app` 能成功。**

pytest 默认只把「测试文件所在目录」塞进 `sys.path`,也就是 `backend/tests/`。
但被测代码在 `backend/app/`,而 `backend/` 才是包的上一级目录 ——
不处理的话 `from app.eval.metrics import ...` 会直接报 ModuleNotFoundError。
下面那行把 `backend/` 显式加进去。

为什么不用 `pip install -e .`:本项目没有打包配置,为了跑测试专门加一个
`pyproject.toml` 会多一份要维护的东西。一行 sys.path 更直白。

**2. 提供构造行程的辅助函数。**

指标函数都是 `(EvalContext) -> Metric` 的纯函数,测起来本来就该很轻:
构造一个 dict 就能验证算得对不对,不用起服务、不用调 LLM。
但 TripPlan 的字段不少(见 models/schemas.py),每个测试手写一遍
又长又容易写错。所以这里给几个带默认值的构造器。

**默认造出来的是一份「健康的」行程**:日期连续、每天有景点有餐、
预算算术自洽。测试想验证某个问题能被抓到,就只改那一处 ——
这样「测试为什么失败」指向明确。
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# 把 backend/ 加入模块搜索路径,使 `import app` 生效。
# parents[1]:conftest.py → tests/ → backend/
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# ===========================================================================
# 构造器
# ===========================================================================

# 默认用北京 —— 它在 geo.CITY_BBOX 里,且默认坐标 (116.4, 39.9) 确实落在范围内。
DEFAULT_CITY = "北京"
DEFAULT_START = "2026-06-01"


def make_attraction(
    name: str = "景点A",
    lon: float | None = 116.4,
    lat: float | None = 39.9,
    ticket_price: int = 0,
) -> dict[str, Any]:
    """造一个景点。

    lon / lat 传 None 表示「这个景点没有坐标」——
    指标里对应的分支是「坐标缺失」,不等于「坐标算错了」。
    """
    location = None if lon is None or lat is None else {"longitude": lon, "latitude": lat}
    return {
        "name": name,
        "address": f"{name}的地址",
        "location": location,
        "visit_duration": 120,
        "description": f"{name}的描述",
        "category": "景点",
        "ticket_price": ticket_price,
    }


def make_meal(meal_type: str = "lunch", cost: int = 50) -> dict[str, Any]:
    return {
        "type": meal_type,
        "name": f"{meal_type}的地方",
        "estimated_cost": cost,
    }


def make_day(
    date_str: str,
    day_index: int = 0,
    attractions: list[dict[str, Any]] | None = None,
    meals: list[dict[str, Any]] | None = None,
    hotel_cost: int | None = 250,
) -> dict[str, Any]:
    """造一天。attractions/meals 传 None 时会得到一份默认的非空内容。

    想造「这天没排景点」,传 `attractions=[]` —— 显式空列表,
    与「忘了传」区分开。
    """
    if attractions is None:
        attractions = [make_attraction(f"景点{day_index}")]
    if meals is None:
        meals = [make_meal(t, 50) for t in ("breakfast", "lunch", "dinner")]
    hotel = None if hotel_cost is None else {"name": "某酒店", "estimated_cost": hotel_cost}
    return {
        "date": date_str,
        "day_index": day_index,
        "description": f"第 {day_index} 天",
        "transportation": "公共交通",
        "accommodation": "经济型酒店",
        "hotel": hotel,
        "attractions": attractions,
        "meals": meals,
    }


def make_plan(
    days: list[dict[str, Any]] | None = None,
    city: str = DEFAULT_CITY,
    start_date: str = DEFAULT_START,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """造一份完整的 TripPlan(dict 形式)。

    days 传 None 时默认造 3 天,日期从 start_date 开始逐日连续。

    budget 传 None 时**按行程内容现算一份自洽的预算** ——
    门票 = Σ ticket_price,餐饮 = Σ estimated_cost,酒店 = Σ hotel.estimated_cost,
    total = 四项之和。这样默认产物能通过 `budget_arithmetic_ok`;
    要测「算术对不上」,显式传一个 budget 进去。
    """
    if days is None:
        base = date.fromisoformat(start_date)
        days = [
            make_day((base + timedelta(days=i)).isoformat(), day_index=i)
            for i in range(3)
        ]

    if budget is None:
        attractions_sum = sum(
            int(a.get("ticket_price") or 0) for d in days for a in (d.get("attractions") or [])
        )
        meals_sum = sum(
            int(m.get("estimated_cost") or 0) for d in days for m in (d.get("meals") or [])
        )
        hotels_sum = sum(
            int((d.get("hotel") or {}).get("estimated_cost") or 0) for d in days
        )
        transport = 100
        budget = {
            "total_attractions": attractions_sum,
            "total_hotels": hotels_sum,
            "total_meals": meals_sum,
            "total_transportation": transport,
            "total": attractions_sum + hotels_sum + meals_sum + transport,
        }

    end_date = days[-1]["date"] if days else start_date
    return {
        "city": city,
        "start_date": start_date,
        "end_date": end_date,
        "days": days,
        "weather_info": [],
        "overall_suggestions": "总体建议",
        "budget": budget,
    }


def make_request(
    city: str = DEFAULT_CITY,
    start_date: str = DEFAULT_START,
    travel_days: int = 3,
    end_date: str | None = None,
) -> dict[str, Any]:
    """造一份 TripRequest(dict 形式)。end_date 默认按天数推算。"""
    if end_date is None:
        base = date.fromisoformat(start_date)
        end_date = (base + timedelta(days=travel_days - 1)).isoformat()
    return {
        "city": city,
        "start_date": start_date,
        "end_date": end_date,
        "travel_days": travel_days,
        "transportation": "公共交通",
        "accommodation": "经济型酒店",
        "preferences": [],
        "free_text_input": "",
    }
