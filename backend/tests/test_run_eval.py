"""run_eval.py —— 录制/回放的骨架

**这组测试在保护什么**

record 是**唯一花钱的一步**(10 条约 ¥1.2、20 分钟),所以它必须一次就对。
而 replay 免费,坏了随时重跑 —— 但如果它**静默出错**(比如报告里少了一列、
某个指标永远是"算不出"),你花的钱就白花了。

重点测两件事:

1. **`build_run_record()` 拼出来的键,指标函数真的读得懂。**
   这是最容易静默出错的地方:键名一旦和 `metrics.py` 里读的对不上,
   不会报错,指标只会退化成"无法计算",报告上看起来像"这项没数据"。

2. **不花钱的路径真的不花钱。** `--dry-run` / `--force` 守卫 / replay
   都必须能在完全没有网络的情况下跑完。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.eval import config as cfg
from app.eval import run_eval
from app.eval.metrics import EvalContext, evaluate
from app.eval.run_eval import (
    BACKEND_DIR,
    _merge_provenance,
    _write_json,
    build_run_record,
    freeze_dir,
    load_frozen,
    main,
    replay,
    select_requests,
)
from conftest import make_plan, make_request


# ===========================================================================
# 契约:build_run_record 与 metrics.py 之间的接口
# ===========================================================================


class TestRun记录契约:
    """把「记录端」和「读取端」接起来测 —— 光断言键名存在没有意义。"""

    def _run(self, **over):
        kw = dict(
            ok=True,
            error=None,
            fallback_used=False,
            warnings=[],
            elapsed_ms=91_400,
            usage={
                "llm_calls": 7,
                "cost_cny": 0.118,
                "usage_source": "api",
                "total_tokens": 30354,
            },
        )
        kw.update(over)
        return build_run_record(**kw)

    def test_指标函数能读懂拼出来的记录(self):
        ctx = EvalContext(request=make_request(), plan=make_plan(), run=self._run())
        by = {m.name: m for m in evaluate(ctx)}

        assert by["fallback_used"].value == 0.0, "fallback_used 没读出来"
        assert by["latency_s"].value == pytest.approx(91.4)
        assert by["llm_calls"].value == 7.0
        assert by["cost_cny"].value == pytest.approx(0.118)
        # 成本类不该被判定
        assert by["cost_cny"].ok is None
        assert by["cost_cny"].detail == "", "usage_source=api 时不该有额外说明"

    def test_降级被指标抓到(self):
        ctx = EvalContext(
            request=make_request(),
            plan=make_plan(),
            run=self._run(fallback_used=True, warnings=["LLM 输出解析失败"]),
        )
        by = {m.name: m for m in evaluate(ctx)}
        assert by["fallback_used"].value == 1.0
        assert by["fallback_used"].ok is False
        assert "LLM 输出解析失败" in by["fallback_used"].detail

    def test_运行失败时_schema_valid_会标出来(self):
        """结构合法不等于这次运行没问题 —— run.ok=False 必须能传到指标里。"""
        ctx = EvalContext(
            request=make_request(), plan=make_plan(), run=self._run(ok=False, error="Timeout")
        )
        by = {m.name: m for m in evaluate(ctx)}
        assert by["schema_valid"].ok is False
        assert "Timeout" in by["schema_valid"].detail

    def test_估算来源会写进说明(self):
        run = self._run(usage={"llm_calls": 1, "cost_cny": 0.01, "usage_source": "estimated"})
        ctx = EvalContext(request=make_request(), plan=make_plan(), run=run)
        by = {m.name: m for m in evaluate(ctx)}
        assert "估算" in by["cost_cny"].detail

    def test_输入不被就地改动(self):
        warnings = ["a"]
        run = build_run_record(
            ok=True, error=None, fallback_used=False, warnings=warnings,
            elapsed_ms=1, usage={"llm_calls": 1},
        )
        warnings.append("b")
        assert run["warnings"] == ["a"], "应该拷贝一份,不能和调用方共享同一个 list"


# ===========================================================================
# 选请求
# ===========================================================================


class Test选请求:
    REQS = [{"id": "a", "city": "北京"}, {"id": "b", "city": "上海"}, {"id": "c", "city": "成都"}]

    def test_默认全要(self):
        assert len(select_requests(self.REQS)) == 3

    def test_only_选子集(self):
        got = select_requests(self.REQS, only="a,c")
        assert [r["id"] for r in got] == ["a", "c"]

    def test_only_里有空格也认(self):
        assert len(select_requests(self.REQS, only=" a , b ")) == 2

    def test_limit_截前N条(self):
        assert [r["id"] for r in select_requests(self.REQS, limit=2)] == ["a", "b"]

    def test_打错id要报错不能静默跳过(self):
        """不然会得到一份「只有 9 条」的报告而毫无察觉。"""
        with pytest.raises(ValueError) as e:
            select_requests(self.REQS, only="a,typo")
        assert "typo" in str(e.value)
        # 报错信息要给出可用的 id,而不是让人去翻文件
        assert "b" in str(e.value)


# ===========================================================================
# 冻结文件读写
# ===========================================================================


class Test冻结文件:
    def test_路径带tag(self, tmp_path):
        assert freeze_dir(tmp_path, "baseline").name == "baseline"
        assert freeze_dir(tmp_path, "baseline").parent.name == "plans"

    def test_写读往返(self, tmp_path):
        p = tmp_path / "x.json"
        _write_json(p, {"a": "中文", "b": [1, 2]})
        assert load_frozen(p) == {"a": "中文", "b": [1, 2]}

    def test_写出来是_LF_不是_CRLF(self, tmp_path):
        """Windows 上 Path.write_text() 会静默把 \\n 变成 \\r\\n,
        内容一样但 git status 会标成已修改。这是本项目踩过的坑。"""
        p = tmp_path / "x.json"
        _write_json(p, {"a": 1})
        raw = p.read_bytes()
        assert b"\r\n" not in raw, "写成了 CRLF,会污染 git 工作区"
        assert b"\n" in raw

    def test_坏JSON返回None而不是抛(self, tmp_path):
        """一条坏数据不该毁掉整份报告。"""
        p = tmp_path / "bad.json"
        p.write_text("{不是 json", encoding="utf-8")
        assert load_frozen(p) is None

    def test_文件不存在也返回None(self, tmp_path):
        assert load_frozen(tmp_path / "nope.json") is None


# ===========================================================================
# 环境指纹
# ===========================================================================


class Test环境指纹:
    def test_各条一致时原样合并(self):
        got = _merge_provenance([
            {"provenance": {"git_sha": "abc", "recorded_at": "t1"}},
            {"provenance": {"git_sha": "abc", "recorded_at": "t2"}},
        ])
        assert got["git_sha"] == "abc"
        assert "recorded_at" not in got, "录制时间各条不同,不该出现在合并结果里"

    def test_各条不一致时要标出来(self):
        """录到一半改了代码 —— 这批数据是混的,不能拿来做前后对比。"""
        got = _merge_provenance([
            {"provenance": {"git_sha": "abc"}},
            {"provenance": {"git_sha": "def"}},
        ])
        assert any("不一致" in k for k in got)

    def test_没有指纹时返回空(self):
        assert _merge_provenance([{}]) == {}


# ===========================================================================
# replay
# ===========================================================================

FAKE_INVENTORY = {
    "version": "test",
    "cities": {
        "北京": {
            "pois": [
                {"name": "景点0", "location": [116.4, 39.9]},
                {"name": "天安门", "location": [116.397, 39.908]},
            ]
        }
    },
}


def _freeze_one(dirpath: Path, rid: str, city: str = "北京", days: int = 3) -> Path:
    """造一个冻结文件。默认是一条「健康」的行程。"""
    payload = {
        "request_id": rid,
        "provenance": {"git_sha": "abc1234", "model": "test-model", "temperature": 0.0},
        "request": {**make_request(city=city, travel_days=days), "id": rid},
        "plan": make_plan(city=city),
        "run": build_run_record(
            ok=True, error=None, fallback_used=False, warnings=[], elapsed_ms=1000,
            usage={"llm_calls": 7, "cost_cny": 0.1, "usage_source": "api"},
        ),
    }
    path = dirpath / f"{rid}.json"
    _write_json(path, payload)
    return path


class TestReplay:
    def test_读到冻结文件并算出指标(self, tmp_path):
        d = freeze_dir(tmp_path, "t")
        _freeze_one(d, "bj-1d")
        results, prov, warns = replay("t", tmp_path, FAKE_INVENTORY)

        assert len(results) == 1
        assert results[0].request_id == "bj-1d"
        assert results[0].city == "北京"
        assert prov["git_sha"] == "abc1234"
        assert warns == []

    def test_接地性指标真的算出来了(self, tmp_path):
        """3 个景点里只有「景点0」在库存里 —— 支撑率应该是 1/3。"""
        d = freeze_dir(tmp_path, "t")
        _freeze_one(d, "bj-1d")
        results, _, _ = replay("t", tmp_path, FAKE_INVENTORY)
        by = {m.name: m for m in results[0].metrics}
        # 容差放到 1e-4:指标本身会 round(rate, 4),0.3333 就是它的正确输出
        assert by["poi_support_rate"].value == pytest.approx(1 / 3, abs=1e-4)

    def test_没有该城市库存时给警告而不是崩(self, tmp_path):
        d = freeze_dir(tmp_path, "t")
        _freeze_one(d, "xx-1d", city="火星")
        results, _, warns = replay("t", tmp_path, FAKE_INVENTORY)
        by = {m.name: m for m in results[0].metrics}
        assert by["poi_support_rate"].value is None
        assert by["poi_support_rate"].ok is None, "没库存是不作判定,不是不合格"
        assert any("火星" in w for w in warns)

    def test_坏文件被跳过但其余照常(self, tmp_path):
        d = freeze_dir(tmp_path, "t")
        _freeze_one(d, "good")
        (d / "bad.json").write_text("{坏", encoding="utf-8")
        results, _, warns = replay("t", tmp_path, FAKE_INVENTORY)
        assert [r.request_id for r in results] == ["good"]
        assert any("bad.json" in w for w in warns)

    def test_没有冻结文件时明确报错而不是出空报告(self, tmp_path):
        with pytest.raises(FileNotFoundError) as e:
            replay("nope", tmp_path, FAKE_INVENTORY)
        assert "record" in str(e.value), "错误信息要告诉人下一步该干嘛"


# ===========================================================================
# CLI —— 全部走不花钱的路径
# ===========================================================================


class TestCLI不花钱的路径:
    def test_dry_run_不建目录不调模型(self, tmp_path, capsys):
        rc = main(["--mode=record", "--tag=t", "--dry-run", "--frozen-dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "没有花任何钱" in out
        # 关键:dry-run 之后目录不该被建出来
        assert not freeze_dir(tmp_path, "t").exists()

    def test_dry_run_的条数和_only_一致(self, tmp_path, capsys):
        main(["--mode=record", "--tag=t", "--dry-run", "--frozen-dir", str(tmp_path),
              "--only=bj-1d-history,cd-1d-food"])
        out = capsys.readouterr().out
        assert "录 2 条" in out

    def test_已有冻结时拒绝覆盖(self, tmp_path, capsys):
        """record 会花真钱,还覆盖 baseline 数据 —— 默认必须拒绝。"""
        d = freeze_dir(tmp_path, "t")
        _freeze_one(d, "bj-1d")
        rc = main(["--mode=record", "--tag=t", "--frozen-dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == 2
        assert "--force" in out

    def test_replay_没有数据时报错退出码非零(self, tmp_path, capsys):
        rc = main(["--mode=replay", "--tag=t", "--frozen-dir", str(tmp_path)])
        assert rc == 2
        assert "record" in capsys.readouterr().err

    def test_replay_no_write_只打印不落盘(self, tmp_path, capsys, monkeypatch):
        d = freeze_dir(tmp_path, "t")
        _freeze_one(d, "bj-1d")
        monkeypatch.setattr(run_eval, "load_inventory", lambda *a, **k: FAKE_INVENTORY)

        rc = main(["--mode=replay", "--tag=t", "--frozen-dir", str(tmp_path), "--no-write"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "# 评测报告 · t" in out
        assert "`bj-1d`" in out


class TestReplay不依赖agent栈:
    """replay 必须能在**没有 hello_agents** 的情况下跑 —— 它只重算,不生成。

    这不是洁癖:依赖越少,这份报告越容易在别人机器上复现。
    """

    def test_导入_run_eval_不会拖进_hello_agents(self):
        code = (
            "import app.eval.run_eval, sys; "
            "print(int(any(m.split('.')[0] == 'hello_agents' for m in sys.modules)))"
        )
        p = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BACKEND_DIR, capture_output=True, text=True, timeout=60,
        )
        assert p.returncode == 0, p.stderr
        assert p.stdout.strip() == "0", "导入 run_eval 时把 hello_agents 也拖进来了"
