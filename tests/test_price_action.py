from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.core.signals.price_action import PriceActionParams, compute_price_action
from src.core.strategy_engine import _strategy_codes_for_candidate
from src.web.models import EntryCandidate


def _bars(count: int = 75) -> list[dict]:
    start = date(2025, 1, 1)
    rows: list[dict] = []
    for i in range(count):
        close = 100 + i * 0.2
        rows.append(
            {
                "date": (start + timedelta(days=i)).isoformat(),
                "open": close - 0.2,
                "close": close,
                "high": close + 0.5,
                "low": close - 0.5,
                "volume": 100.0,
            }
        )
    return rows


def _make_breakout(rows: list[dict], index: int) -> float:
    level = max(float(x["high"]) for x in rows[index - 20 : index])
    rows[index].update(
        {
            "open": level - 0.1,
            "close": level + 1.0,
            "high": level + 1.4,
            "low": level - 0.2,
            "volume": 180.0,
        }
    )
    return level


def test_insufficient_klines_is_invalid() -> None:
    result = compute_price_action(_bars(30))

    assert result.valid is False
    assert result.error == "insufficient_klines"


def test_breakout_uses_prior_volume_and_prior_high() -> None:
    rows = _bars(70)
    _make_breakout(rows, len(rows) - 1)

    result = compute_price_action(rows)

    assert result.valid is True
    assert result.signals["breakout"] is True
    assert "pa_breakout" in result.tags
    assert result.events[-1].type == "breakout"


def test_new_high_without_volume_is_not_breakout() -> None:
    rows = _bars(70)
    level = _make_breakout(rows, len(rows) - 1)
    rows[-1]["volume"] = 100.0

    result = compute_price_action(rows)

    assert result.levels["resistance"] == pytest.approx(level)
    assert result.signals["new_high"] is True
    assert result.signals["breakout"] is False


def test_breakout_day_is_not_also_pullback() -> None:
    rows = _bars(70)
    _make_breakout(rows, len(rows) - 1)

    result = compute_price_action(rows)

    assert result.signals["breakout"] is True
    assert result.signals["pullback_confirm"] is False


def test_prior_breakout_can_confirm_on_later_pullback() -> None:
    rows = _bars(75)
    level = _make_breakout(rows, 70)
    for index in (71, 72, 73):
        rows[index].update(
            {
                "open": level + 0.5,
                "close": level + 0.7,
                "high": level + 0.9,
                "low": level + 0.3,
                "volume": 90.0,
            }
        )
    rows[74].update(
        {
            "open": level - 0.2,
            "close": level + 0.6,
            "high": level + 0.8,
            "low": level * 0.985,
            "volume": 95.0,
        }
    )

    result = compute_price_action(rows)

    assert result.signals["breakout"] is False
    assert result.signals["pullback_confirm"] is True
    assert result.levels["recent_breakout_level"] == pytest.approx(level)
    assert result.events[-1].type == "pullback_confirm"


def test_rows_are_sorted_and_duplicate_dates_use_last_value() -> None:
    rows = _bars(70)
    level = _make_breakout(rows, len(rows) - 1)
    duplicate = dict(rows[-1])
    shuffled = list(reversed(rows)) + [duplicate]

    result = compute_price_action(shuffled)

    assert result.valid is True
    assert result.signals["breakout"] is True
    assert result.levels["resistance"] == pytest.approx(level)


def test_risk_limit_can_reject_an_otherwise_valid_breakout() -> None:
    rows = _bars(75)
    for row in rows[-15:-1]:
        close = float(row["close"])
        row["high"] = close + 8
        row["low"] = close - 8
        row["open"] = close - 1
    _make_breakout(rows, len(rows) - 1)

    result = compute_price_action(
        rows,
        params=PriceActionParams(max_stop_pct=0.03),
    )

    assert result.signals["breakout"] is True
    assert result.signals["risk_acceptable"] is False


def test_historical_events_do_not_change_when_future_bars_are_added() -> None:
    rows = _bars(75)
    _make_breakout(rows, 65)

    prefix = compute_price_action(rows[:66])
    full = compute_price_action(rows)
    event = next(x for x in full.events if x.date == rows[65]["date"])

    assert prefix.events[-1] == event


def test_price_action_subsignals_do_not_create_duplicate_strategy_codes() -> None:
    row = EntryCandidate(
        strategy_tags=["price_action"],
        candidate_source="watchlist",
        meta={"price_action": {"tags": ["pa_breakout", "pa_trend_up"]}},
    )

    assert _strategy_codes_for_candidate(row) == ["price_action"]
