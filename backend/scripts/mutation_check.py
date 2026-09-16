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
    originals = {path: _read(path) for path in {GEO, METRICS}}
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
