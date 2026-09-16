"""scripts/recorder.py 里纯函数的单元测试

只测**不联网**的部分:限额内挑哪些 POI 去取详情。整个录制流程要靠真调高德,
没法在单测里跑,所以这里只钉住容易错的那段逻辑。
"""

from __future__ import annotations

import time

import pytest

from scripts.recorder import (
    CATEGORY_KEYWORDS,
    CallTimeout,
    call_with_timeout,
    pick_for_detail,
)


def make_found(spec: dict[str, int]) -> dict[str, dict]:
    """按 {关键词: 几个} 造一份 found(键是 poi id,值是带 source_keyword 的信息)。"""
    out: dict[str, dict] = {}
    for kw, n in spec.items():
        for i in range(n):
            out[f"{kw}-{i}"] = {"id": f"{kw}-{i}", "name": f"{kw}地点{i}", "source_keyword": kw}
    return out


class TestPickForDetail:
    def test_不限时全部返回(self):
        found = make_found({"景点": 5, "公园": 3})
        assert pick_for_detail(found, ["景点", "公园"], 0) == found

    def test_没超上限时原样返回(self):
        found = make_found({"景点": 2, "公园": 2})
        assert pick_for_detail(found, ["景点", "公园"], 10) == found

    def test_每个关键词都要有份(self):
        """**这是核心。**

        旧逻辑是"取前 N 个",而 found 按关键词顺序插入 —— 于是排在后面的
        关键词**整个被丢掉**。实测过:北京 355 个、上限 200 时,
        观景台/历史建筑/游乐园/地标/古镇/教堂/纪念馆/塔/湖 全灭。

        后果很隐蔽:库存里没有"塔"类的 POI,真实存在的白塔寺就会被
        poi_support_rate 判成"查无此物" —— **假警报比指标偏低更糟**,
        它会让人以为模型在编造。
        """
        spec = {"景点": 20, "公园": 20, "博物馆": 20, "寺庙": 20}
        found = make_found(spec)
        picked = pick_for_detail(found, list(spec), 20)

        per_kw = {kw: sum(1 for v in picked.values() if v["source_keyword"] == kw) for kw in spec}
        assert all(n > 0 for n in per_kw.values()), f"有关键词一个都没进: {per_kw}"
        assert len(picked) == 20

    def test_轮转是均匀的(self):
        """4 个关键词各 20 个、限额 20 → 应该各出 5 个,不多不少。"""
        spec = {"a": 20, "b": 20, "c": 20, "d": 20}
        picked = pick_for_detail(make_found(spec), list(spec), 20)
        per_kw = {kw: sum(1 for v in picked.values() if v["source_keyword"] == kw) for kw in spec}
        assert per_kw == {"a": 5, "b": 5, "c": 5, "d": 5}

    def test_某个关键词不够时由其他补上(self):
        """轮转不能在"某个桶空了"时卡住 —— 剩下的名额要继续分配出去。"""
        spec = {"a": 1, "b": 20, "c": 20}
        picked = pick_for_detail(make_found(spec), list(spec), 10)
        assert len(picked) == 10
        assert sum(1 for v in picked.values() if v["source_keyword"] == "a") == 1
        # 剩下 9 个由 b、c 平分
        assert sum(1 for v in picked.values() if v["source_keyword"] == "b") == 5
        assert sum(1 for v in picked.values() if v["source_keyword"] == "c") == 4

    def test_限额大于总数时全部返回(self):
        spec = {"a": 3, "b": 4}
        found = make_found(spec)
        assert len(pick_for_detail(found, list(spec), 100)) == 7

    def test_顺序不会漏掉任何POI或重复(self):
        spec = {"a": 20, "b": 20, "c": 20}
        found = make_found(spec)
        picked = pick_for_detail(found, list(spec), 30)
        assert len(picked) == 30
        assert set(picked) <= set(found)          # 不能凭空多出来
        assert len(set(picked)) == len(picked)    # 不能重复

    def test_实际的关键词表也能均匀覆盖(self):
        """用真实的关键词表跑一遍 —— 数量多的时候轮转容易出边界问题。"""
        spec = {kw: 20 for kw in CATEGORY_KEYWORDS}
        found = make_found(spec)
        limited = 200
        picked = pick_for_detail(found, CATEGORY_KEYWORDS, limited)

        covered = {v["source_keyword"] for v in picked.values()}
        assert covered == set(CATEGORY_KEYWORDS), f"这些类别没覆盖到: {set(CATEGORY_KEYWORDS) - covered}"
        assert len(picked) == limited

    def test_没有来源关键词时不会崩(self):
        """理论上不会发生,但真发生了不该整轮录制挂掉。"""
        found = {"x": {"id": "x", "name": "x"}}   # 没有 source_keyword
        picked = pick_for_detail(found, ["景点"], 5)
        assert len(picked) == 1


class TestCallWithTimeout:
    """超时保护。

    **为什么需要它(真被卡过)**:`amap-mcp-server` 内部是 `requests.get(...)`
    且没有设 timeout,`MCPTool.run()` 也没有。只要高德不回应,调用就永久挂住
    —— 进程还活着、日志不再增长,整轮录制悄无声息地停在那里。
    实测卡了 17 分钟没动静,而代码里没有任何机制能把它救回来。
    """

    def test_正常返回(self):
        assert call_with_timeout(lambda: 42, 5.0) == 42

    def test_超时会抛CallTimeout(self):
        def slow():
            time.sleep(2.0)
            return "永远也到不了"

        start = time.monotonic()
        with pytest.raises(CallTimeout):
            call_with_timeout(slow, 0.1)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"应该立刻放弃,实际等了 {elapsed:.2f} 秒"

    def test_超时后主流程能继续(self):
        """这是超时保护的**全部意义**:一次卡住不能拖垮整轮。"""
        def slow():
            time.sleep(2.0)

        results = []
        for fn in (slow, lambda: "正常"):
            try:
                results.append(call_with_timeout(fn, 0.1))
            except CallTimeout:
                results.append("超时")
        assert results == ["超时", "正常"]

    def test_异常原样抛回来(self):
        """不能把真实错误吞成超时 —— 那会让排查方向完全错掉。"""
        def boom():
            raise ValueError("高德返回了错误码")

        with pytest.raises(ValueError, match="错误码"):
            call_with_timeout(boom, 5.0)

    def test_可以返回None(self):
        assert call_with_timeout(lambda: None, 5.0) is None

    def test_返回假值不会被误判成超时(self):
        """`box.get("value")` 这种写法容易把 falsy 值当成"没拿到"。
        0 / "" / [] 都是合法返回值。"""
        for falsy in (0, "", [], False):
            assert call_with_timeout(lambda f=falsy: f, 5.0) == falsy
