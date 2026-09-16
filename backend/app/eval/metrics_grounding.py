"""需要真实数据做 ground truth 的指标 —— 「接地性」

它回答的问题
------------
A 类指标(`metrics.py`)只能查行程**自己**自不自洽:天数对不对、预算加得对不对。
它们抓不到最要命的那种错误 —— **景点根本不存在、坐标是编的**。
一个编造的行程在 A 类指标上可以完全合格。

这两条指标就是来抓那个的:

- `poi_support_rate`:行程里的景点名,有多少能在**真实的 POI 库存**里找到
- `coord_mae_km`:匹配上的景点,行程给的坐标离真实坐标平均差多少公里

ground truth 从哪来
-------------------
`data/frozen/amap/poi_inventory.json`,由 `scripts/recorder.py` 用高德 MCP
工具(`maps_text_search` + `maps_search_detail`)录下来并**冻结**。

为什么要冻结而不是每次现查:高德的数据每天都在变(新店开张、老店关闭、改名)。
如果今天拿今天的行程比今天的高德,下个月拿新行程比新高德,两次数字不一样时
**分不清是代码改好了还是高德那边变了**。冻结之后,输入恒定,差异只可能来自
代码 —— 这是 harness 能被称为"可复现"的前提。

⚠️ 必须记住的局限(报告里要带上)
--------------------------------
**库存再全也只可能不完整,不可能错。** 所以:

    poi_support_rate 是一个**下限** —— 真实值 ≥ 报出来的数

反过来说,**没匹配上不等于编的**,也可能是真实景点但没被库存收录。
库存里每个城市的 POI 有几百个,靠十几个关键词搜出来的,长尾景点必然漏掉。

这条局限让指标的**用法**变明确了:
- 它掉到 0.3 时,可以确信"大量景点名查无此地"
- 它涨到 0.95 时,说明"几乎每个景点名都有据可查"
- 它在 0.6 附近时,要结合 `coord_coverage` 一起看,别急着下结论

### 名字匹配的误差率(实测,别假装它不存在)

在 41 个知名景点上量过,`best_match` 约 **88% 正确**。剩下 12% 分两类:

| 类型 | 例子 | 后果 |
|---|---|---|
| **漏**(该匹配上却没匹配上) | 「鸟巢」「迪士尼」「灵隐寺」「宋城」「断桥」—— 20 个关键词一个都没搜到 | 真景点被判成**编造** |
| **错**(匹配到了别的) | 「南京路」→「外滩」(因为外滩有个别名"南京路外滩",更短) | `coord_mae_km` 拿去比坐标,**诬告坐标是编的** |

试过用更多规则去修,但**每修一个就弄坏另一个**:
加"前缀优先"会把「东方明珠」从广播电视塔改成东方明珠公园,
加"正式名优先于别名"会把「钟楼」改成「钟楼小区美食街」。

根子上的问题:**一个短俗称在几千个 POI 里本来就没有唯一的字符串答案。**
堆规则是在跟这个事实较劲,所以这里刻意停在最简的
「相似度优先、同分取短」,并把误差率**量出来写在这里**。

**真正的解法是换个 oracle**,而不是在本地继续猜字面 —— 换成什么、以及
为什么**不是** `maps_geo`,见模块文档结尾。结论是 `poi_exists_rate` 那条。

三态设计在这里的用法
--------------------
`coord_mae_km` 在匹配上的景点太少时返回 `ok=None`(不作判定)而不是硬判。
20 个景点里只匹配上 1 个,那个"平均偏差"是在 1 个样本上算的,
拿它判通过/不通过没有意义 —— 把「不知道」和「不合格」分开。

第二步(5c):换一个 oracle —— 但**不是** `maps_geo`
--------------------------------------------------
上面那 88% 是**本地字符串匹配**的准确率,它只回答了"我这个库存里有没有"。
要回答"这个名字到底真不真",得换一个不受我采样限制的 oracle。

⚠️ **第一版用了 `maps_geo`(地理编码),是错的。** 拿 `国子监` 试了一下返回
`level=兴趣点`,以为能用。跑完 74 个名字才发现:

    外滩      → level=住宅区          南京路步行街 → level=道路
    豫园      → level=乡镇            东方明珠     → **返回空**

**东方明珠是上海最著名的地标,`maps_geo` 返回空。** 因为它做的是**地址→坐标**
的解析,把输入当地址看 —— POI 名不是地址,所以地标名解析不出来;而"外滩"这种
区域名会被解析成行政区划。

正确工具是 `maps_text_search`(**POI 关键词搜索**,就是录库存用的那个)。
它天生处理俗称和别名,而且**不受我采样了哪些关键词的限制**。判别实测:

    东方明珠  → 东方明珠广播电视塔          ✅
    外滩      → 外滩                        ✅
    宽窄巷子  → 宽窄巷子景区                ✅
    紫金幻梦星际主题乐园 → 泡泡玛特城市乐园   ❌(编的名字,返回不相干的)
    火星大饭店北京分店   → 南京大饭店         ❌

⚠️ 判据是「**搜到的像不像**」,不是「有没有搜到」:高德对编造的名字**不会返回空**,
总会硬凑一堆相关 POI 给你。光看返回非空会把编造的**全部放行**,而且看不出来。

结果冻在 `data/frozen/amap/poi_name_check.json`(`scripts/check_poi_names.py` 生成),
指标是 `poi_exists_rate`。判定逻辑只有一处:`checked_verdict()`。

`poi_support_rate` **保留** —— 它测的是另一个东西(库存覆盖率),
两条摆在一起看,差值本身就是结论。`scripts/audit_unmatched.py` 专门做这个对比。
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from . import config as cfg
from .geo import haversine_km
from .metrics import EvalContext, Metric

# 纯类别词 —— 它们描述的是**一类地方**,不是**一个地方**。
#
# 为什么需要这个表:包含关系判定(见 name_similarity)会把"公园"匹配到
# "北海公园",但"公园"显然不等于北海公园。这类词单独出现时不能享受
# 包含关系的优待,只能退回去比字面相似度。
#
# 加词的门槛:它必须**单独出现时不指代任何具体地点**。
# 比如"外滩"不能进来(它就是具体地点),"古镇"可以(哪儿的古镇都行)。
_GENERIC_WORDS = frozenset({
    "景点", "公园", "广场", "博物馆", "博物院", "美术馆", "艺术馆",
    "寺庙", "教堂", "古镇", "老街", "步行街", "美食街", "小吃街",
    "动物园", "植物园", "游乐园", "水族馆", "观景台", "地标",
    "夜市", "商圈", "商业街", "风景区", "景区", "遗址", "故居",
})

# 名字里要去掉的噪音:
# - 括号内容:高德的 POI 名常带 "(东门)""(需预约)""(地铁2号线)" 这类后缀,
#   不剥掉会把"故宫"和"故宫(午门)"算成两个不同的写法
_NOISE_PAIRS = (("（", "）"), ("(", ")"))


def normalize_name(name: Any) -> str:
    """把 POI 名归一化成可比较的形式:去掉括号内容和空白。"""
    s = str(name or "").strip()
    for left, right in _NOISE_PAIRS:
        while left in s and right in s:
            i, j = s.find(left), s.find(right, s.find(left) + 1)
            if j == -1:
                break
            s = s[:i] + s[j + 1:]
    # 全角空格、各种空白一并去掉。名字内部一般不含空格,
    # 带空格的多半是高德那边排版留下的。
    return "".join(s.split())


def name_similarity(a: Any, b: Any) -> float:
    """两个景点名的相似度,0~1。命中判定见 config.POI_NAME_MATCH_RATIO。

    判定顺序:

    1. **完全相同** → 1.0
    2. **包含关系** → 1.0。短的名字完整出现在长的里面,如
       "故宫" ⊂ "故宫博物院"、"外滩" ⊂ "外滩观光平台"。
       这一步不能省:光靠下面的 ratio,"故宫"vs"故宫博物院"只有约 0.57,
       会被判成不匹配 —— 而它恰恰是**最该匹配上**的一对。

       但**纯类别词不享受这条**:见 `_GENERIC_WORDS` 的说明。
    3. **字面相似度** `difflib.SequenceMatcher.ratio()`
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    if short in long and short not in _GENERIC_WORDS:
        return 1.0

    return SequenceMatcher(None, na, nb).ratio()


def candidate_names(poi: dict[str, Any]) -> list[str]:
    """一个 POI 所有可能被叫到的名字:正式名 + 别名。

    **别名不能省。** 高德给一部分 POI 返回 `alias`(多个用 `|` 分隔),
    里面装的正是"俗称 vs 官方名"对不上的那批 ——
    而它们往往是最出名的景点:

        秦始皇帝陵博物院  → 西安兵马俑|秦始皇兵马俑博物馆
        华清宫          → 华清池
        故宫博物院       → 紫禁城
        西安钟楼        → 钟楼

    不带上别名的话,行程里写"兵马俑"会被判成查无此物 ——
    而它就在库存里,只是叫另一个名字。
    """
    out = [str(poi.get("name") or "")]
    alias = poi.get("alias")
    if alias:
        out.extend(str(alias).split("|"))
    return [n for n in (normalize_name(x) for x in out) if n]


def best_match(
    name: Any,
    pois: list[dict[str, Any]],
    threshold: float | None = None,
) -> tuple[dict[str, Any] | None, float]:
    """在库存里找最像的那个 POI。正式名和别名都参与比较。

    Returns:
        (命中的 POI 或 None, 最高相似度)。**没命中时也返回相似度**,
        这样报告里能看出"差一点"还是"完全不沾边"。
    """
    if threshold is None:
        threshold = cfg.POI_NAME_MATCH_RATIO

    best_poi: dict[str, Any] | None = None
    best_key: tuple[float, int] | None = None

    for poi in pois:
        for cand in candidate_names(poi):
            score = name_similarity(name, cand)
            # 排序键:相似度优先,同分取名字**更短**的。
            #
            # ⚠️ 这里刻意保持**简单**。试过加更多规则(前缀匹配优先、
            # 正式名优先于别名),每修好一个就弄坏另一个,得不偿失:
            #
            #   加前缀优先 → 「东方明珠」从"东方明珠广播电视塔"
            #                变成了"东方明珠公园"(更短且同为前缀)
            #   加正式名优先 → 「钟楼」从"西安钟楼"(别名是"钟楼")
            #                变成了"钟楼小区美食街"(名字里带钟楼)
            #
            # 根子上的问题:**一个短俗称,在几千个 POI 里本来就没有
            # 唯一的字符串答案**。「南京路」既像「南京路步行街」,
            # 也像「外滩」的别名「南京路外滩」。
            # 继续堆规则是在跟这个事实较劲。
            #
            # 已知的残余误差(在 41 个知名景点上实测,约 88% 正确):
            #   对:「南京路」这种两边都成立的,会挑错(选到「外滩」)
            #   漏:「鸟巢」「迪士尼」这类没被关键词搜到的,判成查无此物
            #
            # **真正的解法是让高德自己解析名字**(`maps_geo`),而不是
            # 在本地猜。见模块开头「已知局限」那一段。
            key = (score, -len(cand))
            if best_key is None or key > best_key:
                best_key, best_poi = key, poi

    best_score = best_key[0] if best_key else 0.0
    if best_score >= threshold:
        return best_poi, best_score
    return None, best_score


# ===========================================================================
# 从 EvalContext 里取输入
# ===========================================================================


def _plan_attractions(ctx: EvalContext) -> list[dict[str, Any]]:
    """行程里的所有景点。plan 缺失或格式不对时返回空列表。"""
    if not ctx.plan:
        return []
    days = ctx.plan.get("days")
    if not isinstance(days, list):
        return []
    out: list[dict[str, Any]] = []
    for d in days:
        if isinstance(d, dict):
            out.extend(a for a in (d.get("attractions") or []) if isinstance(a, dict))
    return out


def _inventory(ctx: EvalContext) -> list[dict[str, Any]] | None:
    """取该城市的冻结 POI 库存。

    `ctx.ground_truth` 的形状:
        {"city": "北京", "pois": [{"name": ..., "location": [lon, lat], ...}, ...]}

    返回 None 表示**没有 ground truth**(不是"库存是空的")——
    这两者在报告里含义完全不同,所以要分开。
    """
    gt = ctx.ground_truth
    if not isinstance(gt, dict):
        return None
    pois = gt.get("pois")
    if not isinstance(pois, list) or not pois:
        return None
    return [p for p in pois if isinstance(p, dict)]


def _no_ground_truth(name: str) -> Metric:
    return Metric(
        name=name,
        value=None,
        ok=None,
        detail="没有该城市的冻结 POI 库存,无法判定(先跑 scripts/recorder.py)",
    )


# ===========================================================================
# 指标
# ===========================================================================


def m_poi_support_rate(ctx: EvalContext) -> Metric:
    """行程里的景点名,有多少能在真实 POI 库存里找到支撑。

    **这是直接测「编造率」的那条**(编造率 ≈ 1 - 它)。

    项目现有的 `_create_fallback_plan()` 会生成"北京景点1"这种名字 ——
    在库存里查无此物,这条指标会掉下去。而 `coord_coverage` 抓不到它
    (名字假但坐标可以落在城市范围内)。

    ⚠️ 结果是**下限**:库存必然不完整,所以真实值 ≥ 报出来的数。
    详见模块开头的说明。
    """
    attractions = _plan_attractions(ctx)
    if not attractions:
        return Metric(
            name="poi_support_rate",
            value=None,
            ok=False,
            detail="行程里没有景点,无从判断",
        )

    pois = _inventory(ctx)
    if pois is None:
        return _no_ground_truth("poi_support_rate")

    matched = 0
    unmatched: list[str] = []
    for a in attractions:
        poi, _score = best_match(a.get("name"), pois)
        if poi is not None:
            matched += 1
        elif len(unmatched) < 5:
            unmatched.append(str(a.get("name") or "(无名)"))

    total = len(attractions)
    rate = matched / total
    detail = f"{matched}/{total} 个景点名在库存({len(pois)} 个 POI)里有据可查"
    if unmatched:
        detail += f";查无此物示例: {', '.join(unmatched)}"
    detail += ";注意这是下限,没匹配上不等于编造(库存可能不全)"

    return Metric(
        name="poi_support_rate",
        value=round(rate, 4),
        ok=rate >= cfg.POI_SUPPORT_RATE_MIN,
        detail=detail,
    )


def m_coord_mae_km(ctx: EvalContext) -> Metric:
    """匹配上的景点,行程坐标与真实坐标的平均偏差(公里)。

    和 `poi_support_rate` 是**互补**的:
    - `poi_support_rate` 抓"名字是编的"
    - 这条抓"**名字对了但位置是编的**"

    后者 `coord_coverage` 抓不到 —— 只要假坐标恰好落在目标城市范围内,
    它就放行。只有和真实坐标比才知道差了多远。

    样本太少时返回 `ok=None`(不作判定),理由见模块开头。
    """
    attractions = _plan_attractions(ctx)
    if not attractions:
        return Metric(name="coord_mae_km", value=None, ok=False, detail="行程里没有景点,无从判断")

    pois = _inventory(ctx)
    if pois is None:
        return _no_ground_truth("coord_mae_km")

    distances: list[float] = []
    worst: tuple[str, float] | None = None
    for a in attractions:
        poi, _score = best_match(a.get("name"), pois)
        if poi is None:
            continue
        loc = poi.get("location")
        if not (isinstance(loc, (list, tuple)) and len(loc) == 2):
            continue
        plan_loc = a.get("location") or {}
        lon, lat = plan_loc.get("longitude"), plan_loc.get("latitude")
        if lon is None or lat is None:
            continue
        try:
            d = haversine_km(float(lon), float(lat), float(loc[0]), float(loc[1]))
        except (TypeError, ValueError):
            continue
        distances.append(d)
        if worst is None or d > worst[1]:
            worst = (str(a.get("name") or "?"), d)

    n = len(distances)
    if n == 0:
        return Metric(
            name="coord_mae_km",
            value=None,
            ok=None,
            detail="没有「名字匹配上且两边都有坐标」的景点,算不出偏差",
        )

    mae = sum(distances) / n
    detail = f"{n} 个景点匹配上;平均偏差 {mae:.2f} km"
    if worst is not None:
        detail += f"(最远 {worst[0]} {worst[1]:.2f} km)"

    if n < cfg.MIN_MATCHES_FOR_MAE:
        # 样本不足 —— 报出数值但**不判定**,避免 1~2 个样本得出误导性结论
        detail += f";样本仅 {n} 个(< {cfg.MIN_MATCHES_FOR_MAE}),不作判定"
        ok: bool | None = None
    else:
        ok = mae <= cfg.COORD_MAE_KM_MAX
        if not ok:
            detail += f";超过阈值 {cfg.COORD_MAE_KM_MAX} km"

    return Metric(name="coord_mae_km", value=round(mae, 3), ok=ok, detail=detail)


def _name_check_slice(ctx: EvalContext) -> dict[str, Any] | None:
    """该城市的逐名核对结果。None 表示**没查过**(不是"都不存在")。"""
    gt = ctx.ground_truth
    if not isinstance(gt, dict):
        return None
    check = gt.get("name_check")
    return check if isinstance(check, dict) and check else None


def m_poi_exists_rate(ctx: EvalContext) -> Metric:
    """行程里的景点名,有多少是**真的存在**的。

    **这才是判断「编造」的那条**,它和 `poi_support_rate` 测的不是一回事:

    | 指标 | 拿什么当 oracle | 实际测的是 |
    |---|---|---|
    | `poi_support_rate` | 我录的 POI 库存(20 个关键词的**样本**) | **库存覆盖率** |
    | `poi_exists_rate` | 拿名字去高德搜一次 | **名字是否真实存在** |

    baseline 实测:被 `poi_support_rate` 判成"查无此物"的 41 个名字,逐个拿去高德搜,
    **绝大多数都真实存在**(国子监、798艺术中心、上海中心大厦…)—— 它们只是没被
    那 20 个关键词搜到,是**库存不全**,不是模型在编。

    ⚠️ 判据是「**搜到的像不像**」,不是「有没有搜到」。
    高德对编造的名字**不会返回空** —— 它总会硬凑一堆相关 POI 给你:

        搜「紫金幻梦星际主题乐园」→ 返回「泡泡玛特城市乐园」「咘隆家族主题乐园」
        搜「火星大饭店北京分店」  → 返回「南京大饭店」「北京饭店大堂」

    所以必须比名字相似度,光看返回非空会把编造的名字全放行。
    详见 `scripts/check_poi_names.py` 的说明。

    `weak`(有点像但不够像)单独报,不直接判为编造 —— 它可能是名字拼得太长
    (`太古里·春熙路商圈`),也可能是编的。分开报,读者自己判断。
    """
    attractions = _plan_attractions(ctx)
    if not attractions:
        return Metric(
            name="poi_exists_rate",
            value=None,
            ok=False,
            detail="行程里没有景点,无从判断",
        )

    check = _name_check_slice(ctx)
    if check is None:
        return Metric(
            name="poi_exists_rate",
            value=None,
            ok=None,
            detail="没有该城市的逐名核对缓存,无法判定(先跑 scripts/check_poi_names.py)",
        )

    city = str(ctx.request.get("city") or "")
    found = weak = missing = unknown = 0
    missing_names: list[str] = []
    weak_names: list[str] = []

    for a in attractions:
        verdict = checked_verdict(name_check_for(city, a.get("name"), check))
        if verdict == "found":
            found += 1
        elif verdict == "weak":
            weak += 1
            if len(weak_names) < 5:
                weak_names.append(str(a.get("name") or "(无名)"))
        elif verdict == "missing":
            missing += 1
            if len(missing_names) < 5:
                missing_names.append(str(a.get("name") or "(无名)"))
        else:
            # 没查过 —— 「不知道」,不进分母,也不当成"不存在"
            unknown += 1

    judged = found + weak + missing
    if judged == 0:
        return Metric(
            name="poi_exists_rate",
            value=None,
            ok=None,
            detail=f"行程里 {unknown} 个景点名都不在缓存里,一个都判不了",
        )

    rate = found / judged
    detail = f"{found}/{judged} 个景点名在高德那里搜得到自己"
    if weak:
        detail += f";{weak} 个只搜到有点像的(存疑,不算存在): {', '.join(weak_names)}"
    if missing:
        detail += f";{missing} 个搜出来全是**不相干的** POI(编造嫌疑): {', '.join(missing_names)}"
    if unknown:
        detail += f";另有 {unknown} 个没查过,未计入(跑 check_poi_names.py 可补上)"

    return Metric(
        name="poi_exists_rate",
        value=round(rate, 4),
        ok=rate >= cfg.POI_EXISTS_RATE_MIN,
        detail=detail,
    )


# ===========================================================================
# 库存的读写(给 scripts/recorder.py 和 run_eval.py 共用)
# ===========================================================================


def default_inventory_path() -> Path:
    """库存文件位置。放在 frozen/ 下 —— 它是要提交进仓库的 ground truth。"""
    from ..config import get_settings

    return Path(get_settings().frozen_dir) / "amap" / "poi_inventory.json"


def load_inventory(path: Path | str | None = None) -> dict[str, Any]:
    """读库存文件。文件不存在时返回空结构,不抛异常。

    为什么文件不存在也不抛:第一次跑 harness 时库存还没录,
    这时应该得到一份"所有接地性指标都不可计算"的报告,而不是崩溃。
    """
    p = Path(path) if path else default_inventory_path()
    if not p.exists():
        return {"version": None, "cities": {}}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"version": None, "cities": {}}
    if not isinstance(data, dict):
        return {"version": None, "cities": {}}
    data.setdefault("cities", {})
    return data


def default_name_check_path() -> Path:
    """`maps_text_search` 逐名核对结果的缓存位置。和 POI 库存同目录,同样是**要提交**的 ground truth。"""
    from ..config import get_settings

    return Path(get_settings().frozen_dir) / "amap" / "poi_name_check.json"


def load_name_check(path: Path | str | None = None) -> dict[str, Any]:
    """读 `scripts/check_poi_names.py` 冻结下来的逐名核对结果。

    结构::

        {"城市|归一化后的名字": {"best_similarity": 1.0, "best_name": "外滩", ...}}

    ⚠️ 缓存里存的是**事实**(相似度)不是**判定**(是否命中)—— 阈值改了只需重算,
    缓存不作废。判定在 `checked_verdict()` 里做。

    文件不存在时返回空 —— 和 `load_inventory` 一个道理:第一次跑时应该得到一份
    "这条指标算不出来"的报告,而不是崩溃。
    """
    p = Path(path) if path else default_name_check_path()
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    return entries if isinstance(entries, dict) else {}


def name_check_for(city: str, name: Any, check: dict[str, Any] | None) -> dict[str, Any] | None:
    """某个景点名的核对记录。

    返回 None 表示**没查过**,不等于"不存在"—— 三态设计的要求,
    混起来会把"没查过"读成"是编的"。
    """
    if not isinstance(check, dict):
        return None
    rec = check.get(f"{(city or '').strip()}|{normalize_name(name)}")
    return rec if isinstance(rec, dict) else None


def checked_verdict(rec: dict[str, Any] | None, threshold: float | None = None) -> str:
    """把核对记录归成三态。**判定只在这里做一次**,别处不要再各自比较。

    - `found`   —— 搜到的 POI 里有名字跟它够像的(≥ `POI_NAME_MATCH_RATIO`)
    - `weak`    —— 有点像但不够像。存疑区:可能是限定语拼得太长,也可能是编的
    - `missing` —— 搜出来的全是**不相干**的 POI。这才是编造嫌疑

    编造的名字不会让高德返回空 —— 高德总会硬凑一堆相关 POI 给你。所以判据是
    **"搜到的像不像"**,不是"有没有搜到"。这是这个 oracle 最容易搞错的地方。
    """
    if not isinstance(rec, dict):
        return "unknown"
    sim = rec.get("best_similarity")
    if sim is None:
        return "unknown"
    thr = cfg.POI_NAME_MATCH_RATIO if threshold is None else threshold
    if sim >= thr:
        return "found"
    # 0.5 是个粗界:低于它基本可以认为搜到的和查询名没有关系
    return "weak" if sim >= 0.5 else "missing"


def ground_truth_for(
    city: str,
    inventory: dict[str, Any] | None,
    name_check: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """从整份库存里取出某个城市的那一份,喂给 `EvalContext.ground_truth`。

    两个 oracle 都塞进同一个 dict:
    - `pois`       我录的 POI 库存(20 个关键词的**样本**)—— `poi_support_rate` / `coord_mae_km` 用
    - `name_check` 逐名做 POI 搜索的结果(不受采样限制)—— `poi_exists_rate` 用

    为什么要两个:库存装不下"有名但没被关键词命中"的地方,于是会把真实景点判成
    编造。baseline 实测就是这个问题。见模块文档「两个 oracle」。
    """
    if not isinstance(inventory, dict):
        return None
    cities = inventory.get("cities")
    if not isinstance(cities, dict):
        return None
    entry = cities.get((city or "").strip())
    if not isinstance(entry, dict):
        return None
    pois = entry.get("pois")
    if not isinstance(pois, list) or not pois:
        return None

    gt: dict[str, Any] = {"city": city, "pois": pois}

    # 逐名核对结果按城市切一份 —— 指标里就不用再关心 key 的拼法
    if isinstance(name_check, dict) and name_check:
        prefix = f"{(city or '').strip()}|"
        sliced = {k: v for k, v in name_check.items() if k.startswith(prefix)}
        if sliced:
            gt["name_check"] = sliced

    return gt


# 注册表。evaluate() 会把这份和 metrics.ALL_METRICS 拼起来。
#
# 顺序有讲究:`poi_exists_rate` 紧跟在 `poi_support_rate` 后面,报告里两条挨着显示 ——
# 它们的差值(库存覆盖 vs 真实存在)本身就是结论。
GROUNDING_METRICS = [m_poi_support_rate, m_poi_exists_rate, m_coord_mae_km]
