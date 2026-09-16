"""geo.py 的单元测试 —— 地理计算

这里的断言刻意**不写成「和公式一样」**,而是对着**独立已知的事实**验:

- 1 度纬度在任何地方都约等于 111.19 公里(经线是大圆)
- 赤道上 1 度经度也约等于 111.19 公里;到北纬 60° 缩到一半左右
- 北京到上海的直线距离约 1068 公里

如果测试只是把实现里的公式抄一遍,那实现写错了测试也会跟着错 ——
它只能证明「代码没变」,证明不了「代码算得对」。对着外部已知值验,
公式写错时才会真的亮红灯。
"""

from __future__ import annotations

import pytest

from app.eval.geo import CITY_BBOX, bbox_of, haversine_km, in_city, path_length_km

# 1 度纬度对应的公里数(地球平均半径决定,是个常数)
KM_PER_DEGREE = 111.19


class TestBboxOf:
    def test_已收录城市返回四元组(self):
        box = bbox_of("北京")
        assert box is not None
        assert len(box) == 4

    def test_未收录城市返回None(self):
        """必须返回 None,不能是 (0,0,0,0) —— 那会让所有坐标都判成越界。"""
        assert bbox_of("火星") is None

    def test_空字符串返回None(self):
        assert bbox_of("") is None

    def test_两侧空格会被去掉(self):
        """表单里贴进来的城市名常带空格,不该因此判成「未收录」。"""
        assert bbox_of("  北京  ") == bbox_of("北京")


class TestInCity:
    def test_市中心在范围内(self):
        assert in_city(116.4, 39.9, "北京") is True

    def test_上海坐标对北京判为越界(self):
        """这条是 coord_coverage 能抓住「整片坐标用错城市」的根本原因。"""
        assert in_city(121.47, 31.23, "北京") is False

    def test_恰好落在边界上算在范围内(self):
        """实现用的是闭区间 <=。边界归属是主观选择,但要固定下来,
        否则同一份数据在不同实现上会得出不同结论。"""
        min_lon, min_lat, max_lon, max_lat = CITY_BBOX["北京"]
        assert in_city(min_lon, min_lat, "北京") is True
        assert in_city(max_lon, max_lat, "北京") is True

    def test_刚好越界一点点算越界(self):
        min_lon, min_lat, _, _ = CITY_BBOX["北京"]
        assert in_city(min_lon - 0.01, min_lat, "北京") is False

    def test_未收录城市返回None而不是False(self):
        """三态设计的核心:**「不知道」不能伪装成「不在」**。

        返回 False 的话,给一个未收录城市的行程会显示 coord_coverage = 0,
        看起来像「坐标全是假的」,实际只是我们没录这个城市的范围。
        这种报告会误导人,所以必须是 None。
        """
        assert in_city(116.4, 39.9, "火星") is None


class TestHaversine:
    def test_同一个点距离为零(self):
        assert haversine_km(116.4, 39.9, 116.4, 39.9) == pytest.approx(0.0, abs=1e-9)

    def test_一度纬度约等于111公里(self):
        """经线是大圆,所以这个数在全球任何经度都成立。"""
        d = haversine_km(0.0, 0.0, 0.0, 1.0)
        assert d == pytest.approx(KM_PER_DEGREE, rel=0.01)

    def test_赤道上一度经度也约等于111公里(self):
        d = haversine_km(0.0, 0.0, 1.0, 0.0)
        assert d == pytest.approx(KM_PER_DEGREE, rel=0.01)

    def test_高纬度上经度距离会缩短(self):
        """实现里必须有 cos(lat) 这一项。少了它,北纬 60° 的 1 度经度
        会被算成 111 公里(实际约 55.6),单日行程跨度会虚高一倍。"""
        d = haversine_km(0.0, 60.0, 1.0, 60.0)
        assert d == pytest.approx(KM_PER_DEGREE * 0.5, rel=0.02)

    def test_北京到上海约1068公里(self):
        """独立的现实校验:公开数据里这两个城市的直线距离约 1068 km。"""
        d = haversine_km(116.4074, 39.9042, 121.4737, 31.2304)
        assert 1040 < d < 1100, f"算出来 {d:.1f} km,与已知的约 1068 km 不符"

    def test_距离是对称的(self):
        a = haversine_km(116.4, 39.9, 121.47, 31.23)
        b = haversine_km(121.47, 31.23, 116.4, 39.9)
        assert a == pytest.approx(b, rel=1e-12)


class TestPathLength:
    def test_空列表为零(self):
        assert path_length_km([]) == 0.0

    def test_单个点为零(self):
        """一个点没有「路径」可言,返回 0 而不是报错。"""
        assert path_length_km([(116.4, 39.9)]) == 0.0

    def test_两点等于两点间距(self):
        pts = [(116.4, 39.9), (116.5, 39.9)]
        assert path_length_km(pts) == pytest.approx(haversine_km(116.4, 39.9, 116.5, 39.9))

    def test_三点等于两段之和(self):
        pts = [(116.4, 39.9), (116.5, 39.9), (116.6, 39.9)]
        expected = haversine_km(116.4, 39.9, 116.5, 39.9) + haversine_km(116.5, 39.9, 116.6, 39.9)
        assert path_length_km(pts) == pytest.approx(expected)

    def test_顺序不同长度不同(self):
        """路径长度依赖顺序 —— 往返走回头路会算两遍。
        这是「按顺序连起来」的定义决定的,不是 bug。"""
        straight = path_length_km([(116.4, 39.9), (116.5, 39.9), (116.6, 39.9)])
        backtrack = path_length_km([(116.4, 39.9), (116.6, 39.9), (116.5, 39.9)])
        assert backtrack > straight
