"""策略池总览排名的确定性排序键测试（§8.2 平手裁决链）。"""

from __future__ import annotations

from src.web.api.strategy_analysis import _rank_sort_key


def _order(tag_list: list[dict]) -> list[str]:
    ranked = sorted(tag_list, key=lambda t: _rank_sort_key(t))
    return [t["id"] for t in ranked]


def test_总分高者优先():
    """总分降序是第一排序因子。"""
    tags = [
        {"id": "a", "score": 80},
        {"id": "b", "score": 92},
        {"id": "c", "score": 85},
    ]
    assert _order(tags) == ["b", "c", "a"]


def test_平手先比事件年龄再比D0量比再比蓝筹():
    """总分相同：事件年龄小者优先 → D0量比高者优先 → 沪深300优先。"""
    same = 90
    tags = [
        {"id": "老", "score": same, "event_age": 8, "d0_vol_ratio": 2.0, "blue_chip": True},
        {"id": "新", "score": same, "event_age": 2, "d0_vol_ratio": 1.4, "blue_chip": False},
    ]
    assert _order(tags) == ["新", "老"]  # 年龄小的先

    tags2 = [
        {"id": "小量", "score": same, "event_age": 3, "d0_vol_ratio": 1.4},
        {"id": "大量", "score": same, "event_age": 3, "d0_vol_ratio": 2.6},
    ]
    assert _order(tags2) == ["大量", "小量"]  # 年龄相同，量比大的先

    tags3 = [
        {"id": "非蓝筹", "score": same, "event_age": 3, "d0_vol_ratio": 1.5, "blue_chip": False},
        {"id": "蓝筹", "score": same, "event_age": 3, "d0_vol_ratio": 1.5, "blue_chip": True},
    ]
    assert _order(tags3) == ["蓝筹", "非蓝筹"]


def test_缺失裁决因子不报错且排到同档末尾():
    """event_age / d0_vol_ratio 缺失时不崩溃，且排在有值者之后。"""
    tags = [
        {"id": "有年龄", "score": 88, "event_age": 4},
        {"id": "无年龄", "score": 88},
    ]
    assert _order(tags) == ["有年龄", "无年龄"]
