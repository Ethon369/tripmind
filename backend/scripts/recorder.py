"""录制高德响应 —— 为接地性指标造 ground truth

它解决什么问题
--------------
`poi_support_rate`(测「编造率」)和 `coord_mae_km`(测「坐标是不是编的」)需要
一个参照物:**这个城市里真实存在哪些 POI、它们在哪**。

这个参照物不能每次现查高德 —— 高德的数据每天都在变(新店开张、老店关闭、改名)。
今天拿今天的行程比今天的高德,下个月拿新行程比新高德,两次数字不一样时
**分不清是代码改好了还是高德那边变了**。

所以录一次、冻起来。之后所有评测都读这份冻结文件,输入恒定,
指标的变化就只可能来自代码改动。

产出两个文件(都在 data/frozen/amap/,会提交进仓库)
------------------------------------------------
- `raw_cache.json`   —— **原始响应**。每个「工具+参数」对应一段原样保存的高德返回。
  它是一切的源头:库存是从它解析出来的,重跑也优先复用它(所以中断了不用从头再来)。
- `poi_inventory.json` —— **解析好的库存**。指标实际读的就是它。
  形状:`{"cities": {"北京": {"pois": [{"name":..., "location":[lon,lat], ...}]}}}`

为什么分成两份:原始响应是**证据**(可以随时重新解析、加字段、查问题),
库存是**成品**(指标直接读)。只有成品的话,以后想换个解析口径就得重新花钱录一遍。

用法(在 backend/ 目录下)
------------------------
    # 先看要发多少请求,不真的调用
    venv\\Scripts\\python.exe scripts/recorder.py --dry-run

    # 真录(会把 5 个城市全录一遍)
    venv\\Scripts\\python.exe scripts/recorder.py

    # 只录一个城市 / 限制详情请求数
    venv\\Scripts\\python.exe scripts/recorder.py --city 北京 --max-detail 200

    # 只用已有缓存重建库存(不联网)
    venv\\Scripts\\python.exe scripts/recorder.py --rebuild-only

⚠️ 两件要知道的事
----------------
1. 这个脚本会**调用高德 API**(用 .env 里的 AMAP_API_KEY)。免费额度内,
   但仍是你的账号在发请求 —— 所以它不会自动跑,要你自己执行。
2. **它很慢。** `MCPTool.run()` 每调一次工具都会**新起一个
   `uvx amap-mcp-server` 进程**(见 hello_agents 的 protocol_tools.py),
   单次要好几秒。整轮 1000+ 次请求是**按小时计**的。

   所以:每 20 个新请求就自动存一次盘(`--save-every`),中断了直接重跑 ——
   发过的请求会命中缓存,不会重发。跑的时候用 `-u` 让输出实时可见:
   `venv\\Scripts\\python.exe -u scripts/recorder.py`
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# 让脚本无论从哪个目录执行都能 import 到 app 包
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Windows 控制台下重定向时 stdio 默认 GBK,中文/符号会炸。和 app/__init__.py 同一个修法。
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 解析函数放在 app/services/ 下 —— amap_service.py 的 P6 改造要用同一份实现,
# 不在这里重写一遍,避免两份代码各自踩同一个坑。
from app.services.amap_parsing import parse_location, unwrap_mcp_result

FROZEN_DIR = BACKEND_DIR / "data" / "frozen"
AMAP_DIR = FROZEN_DIR / "amap"
CACHE_PATH = AMAP_DIR / "raw_cache.json"
INVENTORY_PATH = AMAP_DIR / "poi_inventory.json"
REQUESTS_PATH = FROZEN_DIR / "requests.json"

# 用哪些关键词去搜。**这是库存覆盖率的关键。**
#
# 选择依据:用**具体的类别词**,不用抽象概念词。理由是实测过的 ——
# 景点 agent 曾用"现代都市"搜上海,高德返回空 POI 列表(抽象概念不是地名,
# 关键词搜索匹配不到)。所以这里全是高德认得的类别词。
#
# 每个关键词取第一页(高德按相关度排序,第一页就是最出名的那些)。
CATEGORY_KEYWORDS = [
    "景点",
    "公园",
    "博物馆",
    "寺庙",
    "古迹",
    "步行街",
    "美食街",
    "广场",
    "动物园",
    "植物园",
    "美术馆",
    "观景台",
    "历史建筑",
    "游乐园",
    "地标",
    "古镇",
    "教堂",
    "纪念馆",
    "塔",
    "湖",
]

# 名字里带这些词的,多半不是景点(高德的关键词搜索会串到别的东西)。
# 不做硬过滤,只用来在统计里提示"库存里混进了多少噪音"。
_NOISE_HINTS = ("酒店", "宾馆", "旅馆", "公寓", "公司", "银行", "营业厅", "店")


# ===========================================================================
# 带超时的调用
# ===========================================================================


class CallTimeout(Exception):
    """单次高德调用超时。"""


def call_with_timeout(fn: Callable[[], Any], seconds: float) -> Any:
    """跑 fn(),超过 seconds 秒就放弃。

    **为什么必须加这个 —— 真被卡过。**

    `amap-mcp-server` 内部是 `requests.get(...)` 且**没有设 timeout**,
    `MCPTool.run()` 也没有。所以只要高德那边不回应,调用就**永久挂住**:
    进程还在、CPU 不动、日志不再增长,整轮录制悄无声息地停在那里。
    实测卡了 17 分钟没动静,而代码里没有任何机制能把它救回来。

    **做不了的事**:Python 没法杀掉一个卡住的线程。所以超时之后那个线程
    会变成孤儿继续挂着(它那个 `uvx` 子进程也是)。用 daemon 线程是为了
    让**主进程能正常退出** —— 非 daemon 线程会让解释器在退出时一直等它。

    超时的调用**不写缓存**,所以下次重跑会重新尝试它,不会留下空洞。
    """
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 —— 要把异常原样带回主线程
            box["error"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(seconds)

    if t.is_alive():
        raise CallTimeout(f"超过 {seconds:.0f} 秒没有响应")
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ===========================================================================
# 录制器
# ===========================================================================


class AmapRecorder:
    """按「工具 + 参数」缓存高德返回。同一个请求只发一次。"""

    def __init__(
        self,
        cache_path: Path = CACHE_PATH,
        offline: bool = False,
        save_every: int = 20,
        timeout: float = 60.0,
    ) -> None:
        self.cache_path = cache_path
        self.entries: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0
        self.offline = offline
        # 每新增多少个请求就存一次盘。
        #
        # 为什么不能只在每个城市录完才存:整轮要发 1000+ 次请求,
        # 每次都要起一个 uvx 子进程,是**按小时计**的活儿。
        # 只在城市边界存盘的话,断在中间就是整座城市白跑。
        # 设成 0 表示关掉自动保存。
        self.save_every = save_every
        # 单次调用的超时秒数。正常一次约 5 秒,给到 60 秒足够宽松,
        # 又能在一分多钟内从"高德不回应"里脱身。
        self.timeout = timeout
        self.timeouts = 0
        self._tool = None
        self._load()

    # ---- 缓存 ----

    def _load(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("entries")
            if isinstance(entries, dict):
                self.entries = entries
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️  缓存读不出来({type(e).__name__}),当成空缓存继续: {self.cache_path}")

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.0",
            "server": "amap-mcp-server (uvx amap-mcp-server)",
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": [
                "高德 MCP 工具的原始返回,按「工具名|参数JSON」存。",
                "这是一切接地性 ground truth 的源头,会提交进仓库 ——",
                "有了它,以后想换解析口径不必重新花额度录一遍。",
            ],
            "entries": self.entries,
        }
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _key(tool: str, args: dict[str, Any]) -> str:
        return f"{tool}|{json.dumps(args, ensure_ascii=False, sort_keys=True)}"

    # ---- 调用 ----

    def _mcp(self):
        if self._tool is None:
            from app.services.amap_service import get_amap_mcp_tool

            self._tool = get_amap_mcp_tool()
        return self._tool

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """调一次高德工具(命中缓存就不发请求),返回解析好的 dict。

        `offline=True`(--rebuild-only)时**缓存未命中就返回空**,
        绝不联网 —— 否则"离线重建"会在你以为没发请求的时候偷偷发。
        """
        key = self._key(tool, args)
        if key in self.entries:
            self.hits += 1
            return unwrap_mcp_result(self.entries[key]["raw"])

        if self.offline:
            self.misses += 1
            return {}

        self.misses += 1
        try:
            raw = call_with_timeout(
                lambda: self._mcp().run(
                    {"action": "call_tool", "tool_name": tool, "arguments": args}
                ),
                self.timeout,
            )
        except CallTimeout as e:
            # 超时**不写缓存** —— 下次重跑会重新尝试这个请求,不会留下空洞。
            self.timeouts += 1
            print(
                f"    ⚠️  超时({e});{tool} 这次跳过,重跑时会再试。"
                f"累计超时 {self.timeouts} 次",
                flush=True,
            )
            return {}
        except Exception as e:  # 单次失败不该中断整轮
            print(f"    ⚠️  {tool} 调用出错({type(e).__name__}: {e});跳过", flush=True)
            return {}
        self.entries[key] = {
            "raw": raw,
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._autosave()
        return unwrap_mcp_result(raw)

    def _autosave(self) -> None:
        """每攒够 save_every 个新请求就落一次盘,让长任务可以断点续跑。"""
        if not self.save_every or self.misses % self.save_every != 0:
            return
        self.save()
        print(f"    …已存盘(缓存 {len(self.entries)} 条,新请求 {self.misses} 次)", flush=True)

    # ---- 两个具体工具 ----

    def text_search(self, keyword: str, city: str) -> list[dict[str, Any]]:
        """关键词搜索。返回的 POI **没有坐标** —— 这正是项目的根本问题。"""
        data = self.call(
            "maps_text_search",
            {"keywords": keyword, "city": city, "citylimit": "true"},
        )
        pois = data.get("pois")
        return [p for p in pois if isinstance(p, dict)] if isinstance(pois, list) else []

    def detail(self, poi_id: str) -> dict[str, Any] | None:
        """POI 详情。**坐标在这里**(`location` 是 "经度,纬度" 字符串)。"""
        if not poi_id:
            return None
        data = self.call("maps_search_detail", {"id": poi_id})
        if not data.get("name"):
            return None
        return data


# ===========================================================================
# 构建库存
# ===========================================================================


def pick_for_detail(
    found: dict[str, dict[str, Any]],
    keywords: list[str],
    limit: int,
) -> dict[str, dict[str, Any]]:
    """在限额内挑哪些 POI 去取详情。

    ⚠️ **不能简单地「取前 N 个」。** `found` 是按关键词顺序插入的,
    取前 N 个等于把排在后面的关键词**整个丢掉**。

    实测过一次(北京,355 个 POI,上限 200):

        观景台  20 个 → 只进 1 个
        历史建筑 13、游乐园 20、地标 11、古镇 18、教堂 20、
        纪念馆 15、塔 19、湖 20  →  一个都不剩

    后果很隐蔽:库存看着有 200 个 POI 挺充实,但"塔/湖/教堂"这些类别
    完全没覆盖 —— 于是真实存在的白塔寺、西湖会被 `poi_support_rate`
    判成"查无此物"。**这是假警报,比指标偏低更糟**,因为它会让人以为
    模型在编造。

    改成**轮转**:每个关键词轮流挑一个,挑完一轮再从头。限额内每个
    关键词都有份,库存的覆盖不会偏向某几个类别 —— 不完整是均匀的,
    而不是"后一半全没了"。

    `limit <= 0` 表示不限,全部取。
    """
    if limit <= 0 or len(found) <= limit:
        return found

    buckets: dict[str, list[str]] = {}
    for pid, info in found.items():
        buckets.setdefault(info.get("source_keyword") or "", []).append(pid)

    picked: dict[str, dict[str, Any]] = {}
    round_no = 0
    while len(picked) < limit:
        added = False
        for kw in keywords:
            bucket = buckets.get(kw) or []
            if round_no < len(bucket):
                pid = bucket[round_no]
                picked[pid] = found[pid]
                added = True
                if len(picked) >= limit:
                    break
        if not added:
            break  # 所有关键词都挑完了
        round_no += 1
    return picked


def build_inventory(
    recorder: AmapRecorder,
    city: str,
    keywords: list[str],
    max_detail: int,
) -> dict[str, Any]:
    """搜关键词 → 去重 → 逐个取详情拿坐标 → 组装成该城市的库存。"""
    # id → 搜索阶段拿到的信息(名字、地址、来自哪个关键词)
    found: dict[str, dict[str, Any]] = {}
    keyword_stats: dict[str, int] = {}

    for kw in keywords:
        pois = recorder.text_search(kw, city)
        keyword_stats[kw] = len(pois)
        for p in pois:
            pid = p.get("id")
            if not pid or pid in found:
                continue
            found[pid] = {
                "id": pid,
                "name": p.get("name"),
                "address": p.get("address"),
                "typecode": p.get("typecode"),
                "source_keyword": kw,
            }
        print(f"  [{city}] 搜「{kw}」→ {len(pois)} 条(累计去重 {len(found)})")

    unique_before_detail = len(found)
    if max_detail > 0 and len(found) > max_detail:
        print(
            f"  [{city}] 去重后 {len(found)} 个,超过上限 {max_detail} ——"
            f"按关键词**轮转**挑 {max_detail} 个取详情(不是取前 N 个)"
        )
        found = pick_for_detail(found, keywords, max_detail)
    elif len(found) > 0:
        print(f"  [{city}] 去重后 {len(found)} 个,全部取详情")

    # 逐个取详情拿坐标
    out: list[dict[str, Any]] = []
    no_location = 0
    for i, (pid, base) in enumerate(found.items(), 1):
        d = recorder.detail(pid)
        if not d:
            continue
        loc = parse_location(d.get("location"))
        if loc is None:
            no_location += 1
            # 没有坐标的也留着 —— 它对 poi_support_rate(比名字)仍然有用,
            # 只是 coord_mae_km 会跳过它。
        out.append({
            "id": d.get("id") or pid,
            "name": d.get("name") or base.get("name"),
            "location": loc,
            "address": d.get("address") or base.get("address"),
            "type": d.get("type"),
            "city": d.get("city"),
            # 别名,**必须存**。
            #
            # 高德会为一部分 POI 返回别名(多个用 `|` 分隔),而别名里恰好
            # 装着那些「俗称 vs 官方名」对不上的情况:
            #   秦始皇帝陵博物院 → 西安兵马俑|秦始皇兵马俑博物馆
            #   华清宫          → 华清池
            #   故宫博物院       → 紫禁城
            #   西安钟楼        → 钟楼
            #
            # 不存的话,行程里写"兵马俑"会被 poi_support_rate 判成查无此物 ——
            # 而它明明就在库存里(只是叫另一个名字)。实测 1130 个详情里
            # 185 个(16%)有别名,踩中的正是最容易出错的那批知名 POI。
            "alias": d.get("alias"),
            "source_keyword": base.get("source_keyword"),
        })
        if i % 10 == 0 or i == len(found):
            print(f"  [{city}] 详情 {i}/{len(found)}", flush=True)

    suspicious = [p["name"] for p in out if any(h in str(p["name"]) for h in _NOISE_HINTS)]
    print(
        f"  [{city}] 完成:{len(out)} 个 POI,"
        f"其中 {len(out) - no_location} 个有坐标、{no_location} 个无坐标;"
        f"名字像商业场所的 {len(suspicious)} 个"
    )

    return {
        "pois": out,
        "stats": {
            "keywords": keywords,
            "per_keyword": keyword_stats,
            "unique_before_detail": unique_before_detail,
            "pois": len(out),
            "with_location": len(out) - no_location,
            "without_location": no_location,
            "suspicious_names": suspicious[:20],
        },
    }


def write_inventory(cities: dict[str, Any]) -> None:
    INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "amap-mcp-server: maps_text_search + maps_search_detail",
        "note": [
            "冻结的真实 POI 库存,给 app/eval/metrics_grounding.py 做 ground truth。",
            "⚠️ 库存再全也只可能不完整,不可能错 —— 所以 poi_support_rate 是下限,",
            "没匹配上不等于编造。详见 metrics_grounding 模块开头的说明。",
        ],
        "cities": cities,
    }
    with open(INVENTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 库存写入 {INVENTORY_PATH}")


# ===========================================================================
# 入口
# ===========================================================================


def cities_from_requests() -> list[str]:
    """从固定输入集里取城市列表 —— 只录**真正会被用到**的城市。

    这样评测请求集一改,库存跟着变,不会录一堆用不上的城市浪费额度。
    """
    if not REQUESTS_PATH.exists():
        return []
    try:
        with open(REQUESTS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    seen: list[str] = []
    for r in data.get("requests") or []:
        c = (r or {}).get("city")
        if c and c not in seen:
            seen.append(c)
    return seen


def main() -> int:
    parser = argparse.ArgumentParser(description="录制高德响应,冻结成评测用的 ground truth")
    parser.add_argument("--city", action="append", help="只录指定城市(可重复);默认录请求集里全部")
    parser.add_argument("--max-detail", type=int, default=200, help="每个城市最多取多少个 POI 详情(默认 200)")
    parser.add_argument("--dry-run", action="store_true", help="只统计要发多少请求,不真的调用")
    parser.add_argument("--rebuild-only", action="store_true", help="只用已有缓存重建库存,不联网")
    parser.add_argument("--save-every", type=int, default=20, help="每多少个新请求存一次盘(默认 20,0=关闭)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="单次高德调用超时秒数(默认 60)。高德的请求没有内置超时,不给上限会永久卡死",
    )
    args = parser.parse_args()

    cities = args.city or cities_from_requests()
    if not cities:
        print(f"❌ 没能从 {REQUESTS_PATH} 读出城市列表")
        return 1

    est_search = len(cities) * len(CATEGORY_KEYWORDS)
    est_detail_max = len(cities) * args.max_detail
    print("=" * 62)
    print("录制高德响应")
    print("=" * 62)
    print(f"城市({len(cities)}): {'、'.join(cities)}")
    print(f"关键词({len(CATEGORY_KEYWORDS)}): {'、'.join(CATEGORY_KEYWORDS)}")
    print(f"关键词搜索请求:{est_search} 次")
    print(f"详情请求:最多 {est_detail_max} 次({len(cities)} 城 × {args.max_detail} 个上限)")
    print(f"合计上限:{est_search + est_detail_max} 次 —— 高德搜索服务免费额度内")
    print()
    print("⚠️  已有缓存的话,发过的请求不会再发(命中缓存直接复用)。")
    print()

    if args.dry_run:
        print("(--dry-run:没有调用高德。)")
        return 0

    if not args.rebuild_only:
        # venv 没激活时 uvx 不在 PATH 上,高德工具会静默变成 0 个 ——
        # 见 CLAUDE.md「必须知道的坑」第 1 条。这里提前拦住,别等问题发生后才查。
        if shutil.which("uvx") is None:
            print("❌ 找不到 uvx —— 高德 MCP 工具起不来,录到的会是空数据。")
            print("   请在 backend/ 下先执行:export PATH=\"$PWD/venv/Scripts:$PATH\"")
            print("   PowerShell: .\\venv\\Scripts\\activate")
            return 1

        # 缓存文件先备份 —— 录坏了还能退回去
        if CACHE_PATH.exists():
            backup = CACHE_PATH.with_suffix(".json.bak")
            shutil.copyfile(CACHE_PATH, backup)
            print(f"已备份旧缓存到 {backup.name}")

    recorder = AmapRecorder(
        offline=args.rebuild_only,
        save_every=args.save_every,
        timeout=args.timeout,
    )
    if args.rebuild_only:
        print(f"(只重建,不联网。现有缓存 {len(recorder.entries)} 条)")

    cities_data: dict[str, Any] = {}
    for city in cities:
        print(f"\n--- {city} ---")
        cities_data[city] = build_inventory(recorder, city, CATEGORY_KEYWORDS, args.max_detail)
        if not args.rebuild_only:
            # 每个城市录完就存一次 —— 中途挂了不用从头再来
            recorder.save()

    write_inventory(cities_data)

    if args.rebuild_only and recorder.misses:
        print(f"\n⚠️  有 {recorder.misses} 个请求在缓存里找不到(离线模式不联网取),"
              f"这部分数据是空的。要补齐就正常跑一次(去掉 --rebuild-only)。")

    print(f"\n缓存:{len(recorder.entries)} 条(本次命中 {recorder.hits}、新请求 {recorder.misses})")
    if recorder.timeouts:
        print(
            f"⚠️  有 {recorder.timeouts} 次调用超时、没写进缓存。"
            f"直接重跑同一条命令即可补上(发过的请求不会重发)。"
        )
    print(f"缓存文件:{CACHE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
