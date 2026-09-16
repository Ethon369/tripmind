"""评测 CLI —— 录制 / 回放

**为什么要分成两个模式**

因为录一次要花真钱、真时间(10 条请求约 ¥1.2、20 分钟),而改指标口径、
改报告排版是天天要做的事。如果两者绑在一起,你就不会去改指标了。

    --mode=record   跑 agent,把行程冻结到 data/frozen/plans/{tag}/
                    唯一花钱的一步,一个 tag 只该做一次

    --mode=replay   读冻结的行程,算指标,出报告
                    不碰网络,免费,想跑多少次跑多少次

**冻结什么、为什么**

冻结的**不只是**行程结果,还有当时的运行记录和**环境指纹**(git sha、模型、
temperature、编排模式)。没有后者,翻出一份"改造后"的报告时你不知道它是哪个
提交测的 —— 那样的前后对比是假的。

这和 P2 冻结高德响应是同一个思路:两层冰冻,把「输入」和「被测系统」隔开,
两次跑的差异才只可能来自代码。

**用法**

    cd backend
    # 录制(花钱,只做一次)
    ./venv/Scripts/python.exe -m app.eval.run_eval --mode=record --tag=baseline
    # 出报告(免费)
    ./venv/Scripts/python.exe -m app.eval.run_eval --mode=replay --tag=baseline

    # 调试:只跑一条
    ./venv/Scripts/python.exe -m app.eval.run_eval --mode=record --tag=smoke --only=bj-1d-history
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from . import config as cfg
from .metrics import EvalContext, evaluate
from .metrics_grounding import ground_truth_for, load_inventory
from .report import RequestResult, render_report

# backend/ 目录(本文件在 backend/app/eval/ 下,上溯三层)
BACKEND_DIR = Path(__file__).resolve().parents[2]

# 报告输出目录。**和 frozen/ 分开**:报告是可再生的产物(有冻结数据随时能重出),
# 冻结数据才是源头。混在一起会让人分不清"删掉哪个就真的没了"。
REPORT_DIR = BACKEND_DIR / "data" / "eval"


# ===========================================================================
# 输入:固定请求集
# ===========================================================================


def load_requests(path: Path | str | None = None) -> list[dict[str, Any]]:
    """读固定输入集。结构见 data/frozen/requests.json。"""
    p = Path(path) if path else BACKEND_DIR / "data" / "frozen" / "requests.json"
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    reqs = data.get("requests")
    if not isinstance(reqs, list) or not reqs:
        raise ValueError(f"{p} 里没有 requests 列表")
    return reqs


def select_requests(
    requests: Sequence[dict[str, Any]],
    only: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """按 --only / --limit 过滤。

    `--only` 里写了不存在的 id 时**报错而不是静默跳过** ——
    手滑打错一个字母,不然就会得到一份"只有 9 条"的报告而毫无察觉。
    """
    out = list(requests)

    if only:
        wanted = [x.strip() for x in only.split(",") if x.strip()]
        known = {r.get("id") for r in out}
        missing = [w for w in wanted if w not in known]
        if missing:
            raise ValueError(
                f"--only 里这些 id 不存在: {', '.join(missing)}\n"
                f"可用的 id: {', '.join(sorted(str(k) for k in known))}"
            )
        out = [r for r in out if r.get("id") in set(wanted)]

    if limit is not None:
        out = out[:limit]

    return out


# ===========================================================================
# 运行记录
# ===========================================================================


def build_run_record(
    *,
    ok: bool,
    error: str | None,
    fallback_used: bool,
    warnings: Sequence[str],
    elapsed_ms: int,
    usage: dict[str, Any],
) -> dict[str, Any]:
    """拼出 `EvalContext.run` 要的那个 dict。

    ⚠️ 键名必须和 `metrics.py` 里读的**一一对上**:那边读的是
    `run["fallback_used"]` / `run["ok"]` / `run["error"]` / `run["elapsed_ms"]`
    和 `run["usage"]["cost_cny"]`。

    对不上**不会报错** —— 指标会静默退化成"无法计算",报告上看起来只是
    "这一项没数据"。所以这里单独写成纯函数,好把它钉死。
    """
    return {
        "ok": ok,
        "error": error,
        "fallback_used": fallback_used,
        "warnings": list(warnings),
        "elapsed_ms": elapsed_ms,
        "usage": dict(usage),
    }


# ===========================================================================
# 环境指纹
# ===========================================================================


def _git_state() -> tuple[str, bool]:
    """当前 commit 短 sha,以及工作区是否**脏**(有未提交改动)。

    为什么要记 dirty:在脏工作区上录的数据,和仓库里任何一个 commit 都对不上 ——
    拿它做"改之前 vs 改之后"的对比是作弊,所以必须标出来。
    拿不到 git 信息时返回 ("unknown", True),宁可标脏也不要假装干净。
    """
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=BACKEND_DIR, capture_output=True, text=True, timeout=10,
        )
        if sha.returncode != 0:
            return "unknown", True
        st = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=BACKEND_DIR, capture_output=True, text=True, timeout=10,
        )
        return sha.stdout.strip(), bool(st.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return "unknown", True


def _provenance(temperature: float | None, model: str = "") -> dict[str, Any]:
    """录制时的环境指纹。写进每个冻结文件,replay 时再读出来放进报告。"""
    from ..config import get_settings

    s = get_settings()
    sha, dirty = _git_state()
    return {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "git_sha": sha,
        "git_dirty": dirty,
        "agent_mode": s.agent_mode,
        "rag_enabled": bool(s.enable_rag),
        "model": model or "(未记录)",
        "temperature": temperature if temperature is not None else "框架默认",
        "metrics_version": cfg.METRICS_VERSION,
    }


def _merge_provenance(payloads: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """从冻结文件里取出环境指纹,用于报告头部。

    用**冻结文件里的**而不是当前环境 —— replay 可能发生在另一台机器、
    另一个提交上,报告描述的是"这批数据是怎么录的",不是"现在是什么环境"。

    各条之间不一致时(比如录到一半改了代码)取第一条并标出来。
    """
    seen: dict[str, Any] = {}
    conflicts: list[str] = []
    for p in payloads:
        prov = p.get("provenance") or {}
        for k, v in prov.items():
            if k == "recorded_at":
                continue
            if k not in seen:
                seen[k] = v
            elif seen[k] != v:
                conflicts.append(k)
    if conflicts:
        seen["⚠️ 各条冻结文件不一致"] = ", ".join(sorted(set(conflicts)))
    return seen


# ===========================================================================
# 冻结文件的读写
# ===========================================================================


def freeze_dir(frozen_dir: Path | str, tag: str) -> Path:
    """`data/frozen/plans/{tag}/`。tag 同时决定冻结目录名和报告名。"""
    p = Path(frozen_dir)
    if not p.is_absolute():
        p = BACKEND_DIR / p
    return p / "plans" / tag


def _write_json(path: Path, payload: Any) -> None:
    """newline="" —— 不做换行符翻译,别把 LF 写成 CRLF 污染 git 工作区。

    这是本项目踩过的坑:Windows 上 `Path.write_text()` 会静默把 \\n 变成 \\r\\n,
    内容一模一样但 git status 会标成已修改。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_frozen(path: Path) -> dict[str, Any] | None:
    """读一个冻结文件。坏了就返回 None(而不是抛) —— 一条坏数据不该毁掉整份报告。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


# ===========================================================================
# record —— 唯一花钱的一步
# ===========================================================================


def record_one(
    agent: Any,
    request: dict[str, Any],
    *,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """跑一次规划,打包成冻结文件的内容。

    这里刻意用 `make_observer(enabled=False)`:评测跑几十遍,如果都写进
    `data/runs/`,真实用户的运行记录会被冲掉,也分不清哪些是评测。
    但 `NullObserver` 一样带 `fallback_used` / `warnings`,够用。
    """
    from ..models.schemas import TripRequest
    from ..observability import collect_usage, make_observer

    req = TripRequest(**request)
    observer = make_observer(enabled=False)

    ok, error, plan = True, None, None
    t0 = time.perf_counter()
    # collect_usage() 用 contextvar 收集本次运行的所有 LLM 调用,
    # 写法与 api/routes/trip.py 一致 —— 同一个机制,不另起一套。
    with collect_usage() as usage:
        try:
            plan = agent.plan_trip(req, observer=observer)
        except Exception as e:  # noqa: BLE001 —— 单条失败不该中断整轮评测
            ok, error = False, f"{type(e).__name__}: {e}"
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    return {
        "request_id": request.get("id"),
        "provenance": provenance,
        "request": dict(request),
        "plan": plan.model_dump(mode="json") if plan is not None else None,
        "run": build_run_record(
            ok=ok,
            error=error,
            fallback_used=observer.fallback_used,
            warnings=observer.warnings,
            elapsed_ms=elapsed_ms,
            usage=usage.summary(),
        ),
    }


def do_record(args: argparse.Namespace) -> int:
    requests = select_requests(load_requests(args.requests), args.only, args.limit)
    out_dir = freeze_dir(args.frozen_dir, args.tag)

    # 防手滑:record 会花真钱,而且会覆盖掉已有 baseline 的冻结数据。
    # 默认拒绝,逼你想一下。
    existing = sorted(out_dir.glob("*.json"))
    if existing and not args.force:
        print(f"❌ {out_dir} 里已经有 {len(existing)} 个冻结文件了。")
        print("   重新录制会花真钱(约 ¥0.12/条),而且会覆盖已有的数据。")
        print("   确认要覆盖就加 --force;想保留旧的请换个 --tag。")
        return 2

    if args.dry_run:
        print(f"[dry-run] 会用 tag={args.tag} 录 {len(requests)} 条到 {out_dir}")
        for r in requests:
            print(f"    - {r.get('id')}  {r.get('city')} {r.get('travel_days')}d")
        print(f"[dry-run] 预计花费约 ¥{0.118 * len(requests):.2f},耗时约 "
              f"{1.5 * len(requests):.0f} 分钟。**没有花任何钱**")
        return 0

    # 温度必须在建 agent **之前**设 —— agent 持有的是同一个 LLM 单例。
    # 不传就是框架默认;传了写进 provenance,报告里能看出来。
    from ..services.llm_service import get_llm

    llm = get_llm()
    if args.temperature is not None:
        llm.temperature = float(args.temperature)

    from ..agents.trip_planner_agent import get_trip_planner_agent

    agent = get_trip_planner_agent()
    provenance = _provenance(args.temperature, model=str(getattr(llm, "model", "")))

    print("=" * 70)
    print(f"录制 {len(requests)} 条 → {out_dir}")
    print(f"环境: {provenance['git_sha']}"
          f"{'(脏工作区!)' if provenance['git_dirty'] else ''}"
          f" | {provenance['model']} | temperature={provenance['temperature']}")
    print("=" * 70)

    spent = 0.0
    failed = 0
    for i, req in enumerate(requests, 1):
        rid = req.get("id")
        print(f"\n[{i}/{len(requests)}] {rid}  {req.get('city')} {req.get('travel_days')}d")
        payload = record_one(agent, req, provenance=provenance)
        _write_json(out_dir / f"{rid}.json", payload)

        run = payload["run"]
        spent += float(run["usage"].get("cost_cny") or 0)
        if not run["ok"] or run["fallback_used"]:
            failed += 1
            print(f"    ⚠️  ok={run['ok']} fallback={run['fallback_used']} "
                  f"{run['error'] or run['warnings']}")
        print(f"    {run['elapsed_ms'] / 1000:.1f}s  "
              f"¥{run['usage'].get('cost_cny', 0):.4f}  "
              f"| 累计 ¥{spent:.4f}")

    print("\n" + "=" * 70)
    print(f"录完 {len(requests)} 条,共花 ¥{spent:.4f}"
          f"{f',其中 {failed} 条降级/失败' if failed else ''}")
    print(f"下一步(免费): --mode=replay --tag={args.tag}")
    print("=" * 70)
    return 0


# ===========================================================================
# replay —— 免费
# ===========================================================================


def replay(tag: str, frozen_dir: Path | str, inventory: Any) -> tuple[list[RequestResult], dict, list[str]]:
    """读冻结的行程,算指标。

    Returns:
        (评测结果, 环境指纹, 警告列表)
    """
    out_dir = freeze_dir(frozen_dir, tag)
    files = sorted(out_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(
            f"{out_dir} 里没有冻结文件。\n"
            f"replay 只是**重算**已有的数据,不会去生成新行程。\n"
            f"先跑: --mode=record --tag={tag}"
        )

    results: list[RequestResult] = []
    payloads: list[dict[str, Any]] = []
    warns: list[str] = []

    for path in files:
        payload = load_frozen(path)
        if payload is None:
            warns.append(f"{path.name} 读不出来(JSON 坏了?),已跳过")
            continue
        payloads.append(payload)

        request = payload.get("request") or {}
        city = str(request.get("city") or "")
        gt = ground_truth_for(city, inventory)
        if gt is None:
            warns.append(f"{payload.get('request_id')}: 没有 {city} 的冻结库存,接地性指标会算不出")

        ctx = EvalContext(
            request=request,
            plan=payload.get("plan"),
            run=payload.get("run") or {},
            ground_truth=gt,
        )
        results.append(
            RequestResult(
                request_id=str(payload.get("request_id") or path.stem),
                city=city,
                travel_days=int(request.get("travel_days") or 0),
                metrics=evaluate(ctx),
            )
        )

    return results, _merge_provenance(payloads), warns


def do_replay(args: argparse.Namespace) -> int:
    inventory = load_inventory()
    n_cities = len((inventory.get("cities") or {}))
    results, provenance, warns = replay(args.tag, args.frozen_dir, inventory)

    for w in warns:
        print(f"⚠️  {w}")
    if provenance:
        provenance["source"] = f"data/frozen/plans/{args.tag}/"
        provenance["ground_truth"] = (
            f"data/frozen/amap/poi_inventory.json({n_cities} 个城市)"
        )

    text = render_report(
        results,
        tag=args.tag,
        provenance=provenance,
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )

    if args.no_write:
        print(text)
        return 0

    out = REPORT_DIR / f"{args.tag}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(text)

    print(f"✅ 报告已生成: {out}")
    print(f"   {len(results)} 条行程 · 阈值版本 {cfg.METRICS_VERSION}")
    print(f"   (报告是可再生的:数据没变的话,重跑得到一模一样的结果)")
    return 0


# ===========================================================================
# CLI
# ===========================================================================


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.eval.run_eval",
        description="评测 harness:录制行程 / 回放算指标",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  --mode=record --tag=baseline         录制(花钱,只做一次)\n"
            "  --mode=replay --tag=baseline         出报告(免费)\n"
            "  --mode=record --tag=smoke --only=bj-1d-history\n"
        ),
    )
    p.add_argument("--mode", required=True, choices=["record", "replay"])
    p.add_argument("--tag", default="baseline", help="冻结目录名 + 报告名")
    p.add_argument("--requests", default=None, help="固定输入集,默认 data/frozen/requests.json")
    p.add_argument("--frozen-dir", dest="frozen_dir", default="./data/frozen")
    p.add_argument("--only", default=None, help="逗号分隔的 request id")
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 条")

    g = p.add_argument_group("record 专用")
    g.add_argument("--temperature", type=float, default=0.0,
                   help="评测用的温度。默认 0.0 是为了让两次跑尽量一致;写进 provenance")
    g.add_argument("--force", action="store_true", help="允许覆盖已有冻结(会花钱,想清楚)")
    g.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="只打印要录什么,不花任何钱")

    r = p.add_argument_group("replay 专用")
    r.add_argument("--no-write", dest="no_write", action="store_true", help="只打印,不写文件")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.mode == "record":
            return do_record(args)
        return do_replay(args)
    except (ValueError, FileNotFoundError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
