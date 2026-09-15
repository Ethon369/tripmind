"""运行日志:一次规划 = 一条 JSONL 记录

设计要点
--------
1. **NullObserver 是默认值。** plan_trip() 不传 observer 时,所有钩子都是
   空操作,行为与加埋点之前【完全一致】。这样埋点本身不会成为新的故障源。

2. **写日志失败绝不影响主流程。** 所有落盘操作都包了 try/except,磁盘满了
   或者权限不对,只打印一行警告,不能让用户看不到行程。

3. **为什么要记 fallback_used。** 现在 _create_fallback_plan() 会编造
   "北京景点1" 这种假数据,而且 plan_trip() 的宽 except 把失败吞掉、
   照常返回 success=True —— 前端和用户都看不出来。有这个字段才能量化
   "到底有多少比例的请求在骗用户"。
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# backend/ 目录(本文件在 backend/app/observability/ 下,上溯三层)
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_len(text: Any) -> int:
    return len(text) if isinstance(text, str) else 0


class NullObserver:
    """空观察者 —— 所有钩子都是空操作。

    plan_trip() 默认用它,保证"不传 observer"和"改埋点之前"行为一致。
    """

    def __init__(self) -> None:
        self.run_id: str = uuid.uuid4().hex
        self.fallback_used: bool = False
        self.warnings: list[str] = []

    # ---- 运行级 ----
    def run_start(self, request: Any) -> None:
        """一次规划开始。"""

    def run_end(self, plan: Any = None, ok: bool = True, error: str | None = None) -> None:
        """一次规划结束,落盘。"""

    # ---- 阶段级 ----
    def stage_start(self, name: str) -> None:
        """某个阶段开始。"""

    def stage_end(self, name: str) -> None:
        """某个阶段结束。"""

    def stage_response(self, stage: str, text: str) -> None:
        """记录某个阶段 agent 的原始输出(用于事后排查)。"""

    # ---- 事件 ----
    def mark_fallback(self, reason: str) -> None:
        """标记本次运行走了降级路径(编造数据)。"""
        self.fallback_used = True
        self.warnings.append(reason)


class JsonlObserver(NullObserver):
    """把一次运行写成一条 JSONL,追加到 data/runs/{日期}.jsonl。

    用法::

        obs = JsonlObserver()
        plan = agent.plan_trip(request, observer=obs)
        print(obs.run_id, obs.fallback_used)
    """

    def __init__(self, runs_dir: str | Path | None = None) -> None:
        super().__init__()
        if runs_dir is None:
            # 延迟导入,避免 observability 模块反向依赖 app.config
            from ..config import get_settings

            runs_dir = get_settings().runs_dir
        self.runs_dir = Path(runs_dir)
        if not self.runs_dir.is_absolute():
            self.runs_dir = BACKEND_DIR / self.runs_dir

        self._t0 = time.perf_counter()
        self._stage_t0: dict[str, float] = {}
        self._record: dict[str, Any] = {
            "run_id": self.run_id,
            "started_at": _now_iso(),
            "stages": [],
            "responses": {},
            "error": None,
        }

    # ---- 运行级 ----
    def run_start(self, request: Any) -> None:
        try:
            # TripRequest 是 pydantic 模型,用 model_dump 拿纯 dict
            self._record["request"] = (
                request.model_dump() if hasattr(request, "model_dump") else dict(request)
            )
        except Exception:
            self._record["request"] = {"_repr": repr(request)[:500]}

    def run_end(self, plan: Any = None, ok: bool = True, error: str | None = None) -> None:
        elapsed_ms = int((time.perf_counter() - self._t0) * 1000)
        self._record.update(
            {
                "finished_at": _now_iso(),
                "elapsed_ms": elapsed_ms,
                "ok": ok,
                "error": error,
                "fallback_used": self.fallback_used,
                "warnings": self.warnings,
            }
        )

        # 如果调用方用 collect_usage() 包住了本次运行,这里就能读到用量。
        # 用 contextvar 而不是传参,是为了不改 plan_trip() 的签名。
        try:
            from .metering import get_collector

            collector = get_collector()
            if collector is not None and collector.calls:
                self._record["usage"] = collector.summary()
                self._record["llm_calls_detail"] = [
                    {
                        "seq": i,
                        "model": c.model,
                        "prompt_tokens": c.prompt_tokens,
                        "completion_tokens": c.completion_tokens,
                        "cache_hit_tokens": c.cache_hit_tokens,
                        "cost_cny": c.cost_cny,
                        "source": c.source,
                        "ms": c.ms,
                    }
                    for i, c in enumerate(collector.calls, 1)
                ]
        except Exception as e:
            self._record["usage_error"] = f"{type(e).__name__}: {e}"

        # 只记行程的摘要,不记整个对象 —— 日志文件会膨胀得太快
        if plan is not None:
            try:
                days = getattr(plan, "days", None) or []
                self._record["plan_summary"] = {
                    "city": getattr(plan, "city", None),
                    "days": len(days),
                    "attractions": sum(len(getattr(d, "attractions", []) or []) for d in days),
                    "has_weather": bool(getattr(plan, "weather_info", None)),
                }
            except Exception as e:
                self._record["plan_summary"] = {"_error": repr(e)}

        self._flush()

    # ---- 阶段级 ----
    def stage_start(self, name: str) -> None:
        self._stage_t0[name] = time.perf_counter()

    def stage_end(self, name: str) -> None:
        t0 = self._stage_t0.pop(name, None)
        if t0 is None:
            return
        self._record["stages"].append(
            {"name": name, "ms": int((time.perf_counter() - t0) * 1000)}
        )

    def stage_response(self, stage: str, text: str) -> None:
        # 截断保存:完整响应可能有几十 KB,这里只留头部用于排查
        self._record["responses"][stage] = {
            "chars": _safe_len(text),
            "head": (text or "")[:800],
        }

    # ---- 事件 ----
    def mark_fallback(self, reason: str) -> None:
        super().mark_fallback(reason)
        print(f"⚠️  降级: {reason}")

    # ---- 落盘 ----
    def _flush(self) -> None:
        """写一行 JSONL。任何失败都只警告,绝不影响主流程。"""
        try:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            path = self.runs_dir / f"{datetime.now():%Y-%m-%d}.jsonl"
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(self._record, ensure_ascii=False) + "\n")
            print(f"📝 运行日志: {path}")
        except Exception as e:
            print(f"⚠️  运行日志写入失败(不影响主流程): {type(e).__name__}: {e}")


def make_observer(enabled: bool = True, runs_dir: str | Path | None = None) -> NullObserver:
    """按开关创建 observer。写日志出问题时自动退回 NullObserver。"""
    if not enabled:
        return NullObserver()
    try:
        return JsonlObserver(runs_dir=runs_dir)
    except Exception as e:
        print(f"⚠️  JsonlObserver 创建失败,退回 NullObserver: {e}")
        return NullObserver()
