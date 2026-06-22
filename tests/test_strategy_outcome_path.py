from datetime import date

from src.collectors.kline_collector import KlineData
from src.core.strategy_engine import _evaluate_price_path


def _bar(day: str, *, high: float, low: float) -> KlineData:
    return KlineData(
        date=day,
        open=(high + low) / 2,
        close=(high + low) / 2,
        high=high,
        low=low,
        volume=100,
    )


def test_price_path_uses_first_touched_level() -> None:
    bars = [
        _bar("2026-01-02", high=10.5, low=9.8),
        _bar("2026-01-03", high=12.2, low=10.1),
        _bar("2026-01-04", high=12.0, low=8.5),
    ]

    status, price = _evaluate_price_path(
        bars,
        start=date(2026, 1, 1),
        target=date(2026, 1, 5),
        stop_loss=9.0,
        target_price=12.0,
    )

    assert status == "hit_target"
    assert price == 12.0


def test_price_path_is_conservative_when_both_hit_same_day() -> None:
    bars = [_bar("2026-01-02", high=12.2, low=8.8)]

    status, price = _evaluate_price_path(
        bars,
        start=date(2026, 1, 1),
        target=date(2026, 1, 5),
        stop_loss=9.0,
        target_price=12.0,
    )

    assert status == "hit_stop"
    assert price == 9.0
