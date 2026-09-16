"""变异测试 —— 证明测试真的能抓到问题

**它回答的问题**

测试全绿,只说明「代码没变坏」,不说明「测试有效」。
一个 `assert True` 也能全绿。

要证明测试有效,得反过来问:**把实现故意改坏,它们会不会红?**

**做法**

每次只改实现里的一处(叫一个「变异」),跑测试,期望**失败**:
- 测试红了 → 这条测试真的在约束那段逻辑
- 测试还是绿的 → 那段逻辑**没被测到**,是个漏洞

改完自动还原。因为要写回源文件,还原的正确性由 `finally` + 末尾校验保证。

**什么时候该跑**

新增指标函数之后。比如 P5 的 5b 要加 `metrics_grounding.py`,
先把新指标的变异加进下面的 `MUTATIONS`,跑一遍确认新测试有效。

**用法**

    cd backend
    ./venv/Scripts/python.exe scripts/mutation_check.py

报告同时写到 `data/eval/mutation_report.txt`(该目录已 gitignore)。

**踩过的坑:行尾符**

第一版用 `Path.write_text()` 写回文件,它在 Windows 上会把 `\\n` 翻译成 `\\r\\n`,
于是每个被变异过的文件跑完都变成 CRLF —— 内容一模一样,但 `git status` 会标成已修改,
提交时容易被误带进去。现在统一用 `newline=""` 读写,不做任何翻译。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# 和 app/__init__.py 里同一个坑、同一个修法:
# 输出被重定向(管道 / > file)时 stdio 默认走 GBK,打印中文/符号会抛
# UnicodeEncodeError 把脚本打断。
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


BACKEND = Path(__file__).resolve().parents[1]
GEO = BACKEND / "app" / "eval" / "geo.py"
METRICS = BACKEND / "app" / "eval" / "metrics.py"
GROUNDING = BACKEND / "app" / "eval" / "metrics_grounding.py"
PARSING = BACKEND / "app" / "services" / "amap_parsing.py"
FALLBACK = BACKEND / "app" / "agents" / "fallback.py"
LAUNCHER = BACKEND / "app" / "services" / "mcp_launcher.py"
EVAL_REPORT = BACKEND / "app" / "eval" / "report.py"
RUN_EVAL = BACKEND / "app" / "eval" / "run_eval.py"
RECORDER = BACKEND / "scripts" / "recorder.py"
REPORT = BACKEND / "data" / "eval" / "mutation_report.txt"


# 每条:(文件, 原文的一小段, 改坏成什么, 说明)
# 说明要写清「改坏之后世界会怎样」,而不是「改了哪一行」——
# 这样测试漏掉时,一眼能看出漏掉的是哪种真实故障。
MUTATIONS: list[tuple[Path, str, str, str]] = [
    (
        GEO,
        "a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lon / 2) ** 2",
        "a = math.sin(d_lat / 2) ** 2 + math.sin(d_lon / 2) ** 2",
        "haversine 去掉 cos(纬度) 修正 —— 高纬度上经度距离会算成两倍",
    ),
    (
        GEO,
        '    """取城市外接矩形。没收录则返回 None —— 调用方要处理这种情况,不要当成 0。"""\n'
        '    return CITY_BBOX.get((city or "").strip())',
        '    """取城市外接矩形。"""\n'
        '    return CITY_BBOX.get((city or "").strip()) or (0.0, 0.0, 0.0, 0.0)',
        "bbox_of 对未收录城市返回 (0,0,0,0) 而不是 None —— 三态退化成两态",
    ),
    (
        GEO,
        '    "西安": (107.67, 33.70, 109.82, 34.75),',
        '    "西安": (107.40, 33.42, 109.49, 34.45),',
        "西安范围框改回「度分当成小数」的错误值 —— 真实 POI 会被判成越界(假警报)",
    ),
    (
        METRICS,
        "        verdict = in_city(float(lon), float(lat), city)",
        "        verdict = True",
        "coord_coverage 不再真的判城市 —— 上海行程配北京坐标也放行(P6 要修的那个 bug)",
    ),
    (
        METRICS,
        "    if abs(total - item_sum) > cfg.BUDGET_TOTAL_TOLERANCE_CNY:",
        "    if False:",
        "budget 不再查「总额 vs 四项之和」",
    ),
    (
        METRICS,
        '    return Metric(name="fallback_used", value=1.0 if used else 0.0, ok=not used, detail=detail)',
        '    return Metric(name="fallback_used", value=1.0 if used else 0.0, ok=True, detail=detail)',
        "fallback_used 永远判通过 —— 降级了也不报警",
    ),
    (
        METRICS,
        "    pos = (len(xs) - 1) * q",
        "    pos = (len(xs) - 1) * 0.5",
        "分位数改成中位数 —— p95 抓不到「有一天特别赶」",
    ),
    (
        METRICS,
        "    judged = [m for m in metrics if m.ok is not None]",
        "    judged = list(metrics)",
        "summarize 把不判定的参考值也算进判定数",
    ),
    (
        METRICS,
        "        if lon is None or lat is None:\n            outside += 1\n            continue",
        "        if lon is None or lat is None:\n            continue",
        "coord_coverage 跳过缺坐标的景点,而不是算作越界",
    ),
    # ---- metrics_grounding.py(5b)----
    (
        GROUNDING,
        "    if short in long and short not in _GENERIC_WORDS:\n        return 1.0",
        "    if False:\n        return 1.0",
        "名字匹配去掉「包含关系」判定 —— 「故宫」匹配不上「故宫博物院」",
    ),
    (
        GROUNDING,
        "    if short in long and short not in _GENERIC_WORDS:",
        "    if short in long:",
        "名字匹配去掉纯类别词豁免 —— 「公园」会被当成「北海公园」",
    ),
    (
        GROUNDING,
        '        ok=rate >= cfg.POI_SUPPORT_RATE_MIN,',
        "        ok=True,",
        "poi_support_rate 永远判通过 —— 编造的名字也不报警",
    ),
    (
        GROUNDING,
        '    if pois is None:\n        return _no_ground_truth("poi_support_rate")',
        '    if pois is None:\n        return Metric(name="poi_support_rate", value=0.0, ok=False, detail="无库存")',
        "poi_support_rate 把「没录库存」当成「全是编的」",
    ),
    (
        GROUNDING,
        "    if n < cfg.MIN_MATCHES_FOR_MAE:",
        "    if False:",
        "coord_mae_km 样本不足时硬判 —— 1 个样本也下结论",
    ),
    (
        GROUNDING,
        "        ok = mae <= cfg.COORD_MAE_KM_MAX",
        "        ok = True",
        "coord_mae_km 永远判通过 —— 坐标编的也不报警",
    ),
    (
        GROUNDING,
        "    except (json.JSONDecodeError, OSError):\n        return {\"version\": None, \"cities\": {}}",
        "    except (json.JSONDecodeError, OSError):\n        raise",
        "库存读到坏 JSON 时直接抛异常,而不是降级成空库存",
    ),
    (
        GROUNDING,
        "    if best_score >= threshold:",
        "    if best_score >= 0.0:",
        "best_match 不看阈值 —— 完全不像的也算命中",
    ),
    (
        GROUNDING,
        "    out = [str(poi.get(\"name\") or \"\")]\n"
        "    alias = poi.get(\"alias\")\n"
        "    if alias:\n"
        "        out.extend(str(alias).split(\"|\"))\n"
        "    return [n for n in (normalize_name(x) for x in out) if n]",
        "    out = [str(poi.get(\"name\") or \"\")]\n"
        "    return [n for n in (normalize_name(x) for x in out) if n]",
        "匹配时忽略别名 —— 「兵马俑」匹配不上官方名「秦始皇帝陵博物院」(假警报)",
    ),
    (
        GROUNDING,
        "            key = (score, -len(cand))",
        "            key = (score, len(cand))",
        "同分时反倒偏爱更长的名字 —— 会匹配到「...-郭宅」而不是「杭州西湖风景名胜区」",
    ),
    (
        GROUNDING,
        "            key = (score, -len(cand))\n"
        "            if best_key is None or key > best_key:\n"
        "                best_key, best_poi = key, poi",
        "            key = (score, 0)\n"
        "            if best_key is None or key > best_key:\n"
        "                best_key, best_poi = key, poi",
        "同分时不比名字长短,变成「谁先出现在库存里谁赢」",
    ),
    # ---- amap_parsing.py ----
    (
        PARSING,
        "    starts = [i for i in (text.find(\"{\"), text.find(\"[\")) if i != -1]\n"
        "    if starts:\n"
        "        try:\n"
        "            obj, _end = json.JSONDecoder().raw_decode(text[min(starts):])",
        "    i, j = text.find(\"{\"), text.rfind(\"}\")\n"
        "    if i != -1 and j > i:\n"
        "        try:\n"
        "            obj = json.loads(text[i:j + 1])",
        "剥壳退化路径改回「找最后一个 }」—— JSON 后面的花括号会让它取过头",
    ),
    (
        PARSING,
        "    idx = text.find(_RESULT_MARKER)\n"
        "    if idx != -1:\n"
        "        text = text[idx + len(_RESULT_MARKER):]\n"
        "    text = text.strip()",
        "    text = text.strip()",
        "剥壳不切前缀 —— 解析失败时 _raw 里混进「工具 'xxx' 执行结果:」噪音",
    ),
    (
        PARSING,
        "    return {\"_raw\": text[:2000]}",
        "    return {}",
        "解析失败时丢掉原文 —— 错误信息一起没了,排查时没有证据",
    ),
    (
        PARSING,
        "            return [float(value[0]), float(value[1])]\n"
        "        except (TypeError, ValueError):\n"
        "            return None\n\n"
        "    if not isinstance(value, str):",
        "            return [float(value[1]), float(value[0])]\n"
        "        except (TypeError, ValueError):\n"
        "            return None\n\n"
        "    if not isinstance(value, str):",
        "坐标列表的经纬度对调 —— 坐标会跑到地球另一边,而且不报错",
    ),
    (
        PARSING,
        "    parts = value.split(\",\")\n"
        "    if len(parts) != 2:\n"
        "        return None\n"
        "    try:\n"
        "        return [float(parts[0]), float(parts[1])]\n"
        "    except ValueError:\n"
        "        return None",
        "    parts = value.split(\",\")\n"
        "    if len(parts) != 2:\n"
        "        return [0.0, 0.0]\n"
        "    try:\n"
        "        return [float(parts[0]), float(parts[1])]\n"
        "    except ValueError:\n"
        "        return [0.0, 0.0]",
        "坐标解析失败时返回 [0,0] 而不是 None —— 变成「几内亚湾海上」的假坐标",
    ),
    # ---- metrics_grounding.py 的第二个 oracle(poi_exists_rate)----
    (
        GROUNDING,
        '    return "weak" if sim >= 0.5 else "missing"',
        '    return "missing"',
        "「有点像」被算成编造 —— 名字只是拼得太长(如「太古里·春熙路商圈」)就被诬告",
    ),
    (
        GROUNDING,
        "    if sim >= thr:\n        return \"found\"",
        "    if sim > 0.99:\n        return \"found\"",
        "命中阈值形同虚设,要求几乎逐字相同 —— 真实景点成片被判成编造",
    ),
    (
        GROUNDING,
        "    if not isinstance(rec, dict):\n"
        "        return \"unknown\"\n"
        "    sim = rec.get(\"best_similarity\")\n"
        "    if sim is None:\n"
        "        return \"unknown\"",
        "    if not isinstance(rec, dict) or rec.get(\"best_similarity\") is None:\n"
        "        return \"missing\"",
        "「没查过」被算成「不存在」—— 补缓存前后同一条行程的数字会自己变",
    ),
    (
        GROUNDING,
        '        if verdict == "found":\n            found += 1\n        elif verdict == "weak":',
        '        if verdict in ("found", "weak"):\n            found += 1\n        elif verdict == "weak":',
        "存疑的被算进分子 —— 编造的名字只要搜到个沾边的就能蒙混过关",
    ),
    (
        GROUNDING,
        '        else:\n            # 没查过 —— 「不知道」,不进分母,也不当成"不存在"\n            unknown += 1',
        "        else:\n            missing += 1",
        "「没查过」被当成「不存在」—— 缓存没覆盖到的名字全变成编造",
    ),
    (
        GROUNDING,
        "    rec = check.get(f\"{(city or '').strip()}|{normalize_name(name)}\")",
        "    rec = check.get(f\"{(city or '').strip()}|{name}\")",
        "查核对缓存时不归一化名字 —— 带括号限定语的景点名一个都查不到",
    ),
    (
        GROUNDING,
        "        sliced = {k: v for k, v in name_check.items() if k.startswith(prefix)}",
        "        sliced = dict(name_check)",
        "核对缓存不按城市切 —— 北京行程会拿上海的同名记录去判",
    ),
    # ---- app/agents/fallback.py ----
    #
    # 这几条是在把**真实的旧实现**改回去。原来的 _create_fallback_plan 就是这个
    # 样子:假景点名 + 写死的北京坐标 + "这是为您规划的…"。谁要是哪天把它改回来,
    # 这几条测试必须全红。
    (
        FALLBACK,
        "                    attractions=[],  # ← 不编造。前端对空列表显示「暂无数据」",
        "                    attractions=[__import__('app.models.schemas', fromlist=['S'])"
        ".Attraction(name=f'{request.city}景点{i + 1}', address='x', location="
        "__import__('app.models.schemas', fromlist=['S']).Location(longitude=116.4, "
        "latitude=39.9), visit_duration=120, description='d')],",
        "退回原来的编造景点 —— 生成上海的计划会得到北京的坐标(真实踩过的 bug)",
    ),
    (
        FALLBACK,
        "                    meals=[],",
        "                    meals=[__import__('app.models.schemas', fromlist=['S'])"
        ".Meal(type='breakfast', name=f'第{i + 1}天早餐')],",
        "退回原来的编造餐饮 —— 「第1天早餐」看起来像推荐,其实是空的",
    ),
    (
        FALLBACK,
        "                    hotel=None,",
        "                    hotel=__import__('app.models.schemas', fromlist=['S'])"
        ".Hotel(name='某酒店'),",
        "退回编造酒店 —— 一份没内容的行程却推荐了具体酒店",
    ),
    (
        FALLBACK,
        "    if reason:\n        parts.append(f\"失败原因:{reason}。\")",
        "    if False:\n        parts.append(f\"失败原因:{reason}。\")",
        "失败原因不写进说明 —— 用户只看到「没生成」,不知道为什么",
    ),
    (
        FALLBACK,
        '        f"本次未能生成{request.city}的行程内容,下面是空的框架。",',
        '        f"这是为您规划的{request.city}行程,请尽情享受。",',
        "降级时谎称规划好了 —— 用户会以为那份空框架就是他的行程",
    ),
    (
        FALLBACK,
        "    try:\n"
        "        return date.fromisoformat(str(value)[:10])\n"
        "    except (TypeError, ValueError):\n"
        "        return None",
        "    return date.fromisoformat(str(value)[:10])",
        "起始日期解析不加保护 —— 空串会让降级路径自己抛异常(兜底反而崩了)",
    ),
    (
        FALLBACK,
        "                    date=(start + timedelta(days=i)).isoformat(),",
        "                    date=(start + timedelta(days=i * 2)).isoformat(),",
        "日期不再是逐日连续 —— 前端排出来的行程会隔天跳",
    ),
    # ---- services/mcp_launcher.py ----
    (
        LAUNCHER,
        '            return [str(candidate), AMAP_MCP_PACKAGE]',
        '            return ["uvx", AMAP_MCP_PACKAGE]',
        "找到绝对路径也不用,退回裸命令 —— 高德工具静默变 0 的坑原样回来",
    ),
    (
        LAUNCHER,
        "    exe_dir = Path(sys.executable).parent",
        "    exe_dir = Path(sys.executable).parent.parent",
        "uvx 从解释器目录的**上一层**找 —— 找不到就静默退回 PATH",
    ),
    (
        LAUNCHER,
        'AMAP_MCP_PACKAGE = "amap-mcp-server"',
        'AMAP_MCP_PACKAGE = "amap-mcp"',
        "包名写错 —— 服务起不来,同样是「工具数 0 但不报错」",
    ),
    # ---- app/eval/report.py ----
    (
        EVAL_REPORT,
        '        "uncomputable": len(metrics) - len(values),',
        '        "uncomputable": 0,',
        "报告不再统计「算不出」—— 「不知道」和「合格」被混成一体",
    ),
    (
        EVAL_REPORT,
        "    judged = [m for m in metrics if m.ok is not None]",
        "    judged = list(metrics)",
        "报告把「不判定」的参考类也塞进判定数 —— 成本指标会拉高通过率",
    ),
    (
        EVAL_REPORT,
        "            if m.ok is False\n",
        "            if not m.ok\n",
        "报告把「不作判定」当成「不合格」—— 算不出的条目被列成故障",
    ),
    (
        EVAL_REPORT,
        '    if value is None:\n        return "—"',
        '    if value is None:\n        return "0"',
        "「算不出」在报告里显示成 0 —— 和「真的是 0」分不清,三态退化成两态",
    ),
    (
        EVAL_REPORT,
        "    for i, (title, body) in enumerate(_LIMITATIONS, 1):",
        "    for i, (title, body) in enumerate([], 1):",
        "报告不再输出局限声明 —— 数字会被读成比实际更确定",
    ),
    # ---- app/eval/run_eval.py ----
    (
        RUN_EVAL,
        '        "fallback_used": fallback_used,',
        '        "fallback": fallback_used,',
        "运行记录的键名和 metrics.py 里读的对不上 —— 降级指标静默变成「无法计算」",
    ),
    (
        RUN_EVAL,
        '        "warnings": list(warnings),',
        '        "warnings": warnings,',
        "运行记录和调用方共享同一个 list —— 调用方之后 append 会污染已录的数据",
    ),
    (
        RUN_EVAL,
        "        missing = [w for w in wanted if w not in known]\n        if missing:",
        "        missing = []\n        if missing:",
        "--only 里打错的 id 被静默跳过 —— 会得到一份「只有 9 条」的报告而毫无察觉",
    ),
    (
        RUN_EVAL,
        '    with open(path, "w", encoding="utf-8", newline="") as f:',
        '    with open(path, "w", encoding="utf-8") as f:',
        "冻结文件写成 CRLF —— 内容一样但 git 工作区被污染(真实踩过的坑)",
    ),
    (
        RUN_EVAL,
        "    if not files:\n        raise FileNotFoundError(",
        "    if not files:\n        return [], {}, []",
        "没有冻结数据时返回空而不是报错 —— 会静默出一份「0 条行程」的假报告",
    ),
    # ---- scripts/recorder.py ----
    (
        RECORDER,
        "    buckets: dict[str, list[str]] = {}\n"
        "    for pid, info in found.items():\n"
        "        buckets.setdefault(info.get(\"source_keyword\") or \"\", []).append(pid)\n\n"
        "    picked: dict[str, dict[str, Any]] = {}\n"
        "    round_no = 0\n"
        "    while len(picked) < limit:\n"
        "        added = False\n"
        "        for kw in keywords:\n"
        "            bucket = buckets.get(kw) or []\n"
        "            if round_no < len(bucket):\n"
        "                pid = bucket[round_no]\n"
        "                picked[pid] = found[pid]\n"
        "                added = True\n"
        "                if len(picked) >= limit:\n"
        "                    break\n"
        "        if not added:\n"
        "            break  # 所有关键词都挑完了\n"
        "        round_no += 1\n"
        "    return picked",
        "    return dict(list(found.items())[:limit])",
        "详情挑选改回「取前 N 个」—— 排在后面的关键词整个被丢掉(真实踩过的 bug)",
    ),
    (
        RECORDER,
        "    if limit <= 0 or len(found) <= limit:\n        return found",
        "    if len(found) <= limit:\n        return found",
        "详情挑选不认「limit<=0 表示不限」—— 会一个都不挑,录出空库存",
    ),
    (
        RECORDER,
        "    t.join(seconds)",
        "    t.join()",
        "调用超时保护失效 —— 高德不回应时永久卡死(真实踩过,卡了 17 分钟)",
    ),
    (
        RECORDER,
        "    if t.is_alive():\n        raise CallTimeout(f\"超过 {seconds:.0f} 秒没有响应\")",
        "    if False:\n        raise CallTimeout(f\"超过 {seconds:.0f} 秒没有响应\")",
        "调用超时后不报错 —— 卡住的调用被当成「成功但没结果」",
    ),
    (
        RECORDER,
        "    return box.get(\"value\")",
        "    return box.get(\"value\") if box.get(\"value\") else None",
        "把 0 / \"\" / [] 这些合法返回值误判成「没拿到」",
    ),
    (
        RECORDER,
        "        except BaseException as e:  # noqa: BLE001 —— 要把异常原样带回主线程\n"
        "            box[\"error\"] = e",
        "        except Exception:  # noqa: BLE001\n            pass",  # noqa
        "调用异常被吞掉 —— 真实错误被当成「没结果」,排查方向全错",
    ),
]


def _read(path: Path) -> str:
    """newline="" —— 不做任何换行符翻译,原样读进来。"""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _write(path: Path, text: str) -> None:
    """newline="" —— 不做翻译,避免把 LF 写成 CRLF 污染 git 工作区。"""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def run_pytest() -> tuple[int, str]:
    # PYTHONIOENCODING=utf-8 不能省:子进程的 stdout 是管道,默认走 GBK,
    # 而 pytest 会回显中文测试名 —— 不指定的话报告里那一行就是乱码。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    originals = {
        path: _read(path)
        for path in {
            GEO, METRICS, GROUNDING, PARSING, FALLBACK,
            LAUNCHER, EVAL_REPORT, RUN_EVAL, RECORDER,
        }
    }
    lines: list[str] = []

    def say(text: str = "") -> None:
        lines.append(text)
        print(text)

    try:
        code, out = run_pytest()
        last = [ln for ln in out.strip().splitlines() if ln.strip()][-1]
        say(f"基线(未变异): exit={code}  {last}")
        if code != 0:
            say("!! 基线就是红的 —— 先让测试全过,再谈变异测试")
            return 1
        say()

        caught = 0
        skipped = 0
        for path, old, new, label in MUTATIONS:
            src = originals[path]
            if old not in src:
                # 原文找不到,通常是被测代码改了措辞。这时**不能当成通过** ——
                # 静默跳过会让「变异数」虚高,给人测试很扎实的错觉。
                say(f"[跳过 !!] 找不到要替换的原文(被测代码可能改过了): {label}")
                skipped += 1
                continue

            _write(path, src.replace(old, new, 1))
            try:
                code, out = run_pytest()
            finally:
                _write(path, src)

            if code != 0:
                caught += 1
                first = next(
                    (ln for ln in out.splitlines() if ln.startswith(("FAILED", "ERROR"))),
                    "",
                )
                say(f"[抓到 OK] {label}")
                say(f"          {first[:130]}")
            else:
                say(f"[漏掉 NG] {label}")
                say("          ^ 改坏了却没人报错,说明这段没被测到")

        say()
        total = len(MUTATIONS)
        say(f"结果: {caught}/{total} 个变异被抓到" + (f",{skipped} 个跳过" if skipped else ""))
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text("\n".join(lines), encoding="utf-8")
        return 0 if caught == total else 1
    finally:
        # 复原。并**校验**复原成功 —— 悄悄改坏源文件比脚本报错严重得多。
        for path, src in originals.items():
            _write(path, src)
            if _read(path) != src:
                print(f"!!! 严重: {path} 未被正确还原,请 git checkout 恢复", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
