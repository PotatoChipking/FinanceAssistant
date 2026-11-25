"""Close-confirmed exits for positions that reached their original target."""

from __future__ import annotations

from statistics import mean

from src.core.breakout_retest_study import Bar


def ma5_exit(bars: list[Bar], day: int, target_day: int) -> str:
    """Exit after the close falls below the trailing five-session mean."""
    closes = [bar.close for bar in bars[day - 4:day + 1]]
    if len(closes) == 5 and bars[day].close < mean(closes):
        return "close_below_ma5"
    return ""


def _high_volume(bars: list[Bar], day: int) -> bool:
    window = bars[day - 5:day]
    volumes = [bar.volume for bar in window if bar.volume is not None and bar.volume > 0]
    current = bars[day].volume
    return bool(len(volumes) == 5 and current is not None and current >= mean(volumes) * 1.5)


def volume_doji_exit(bars: list[Bar], day: int, target_day: int) -> str:
    """Require the close after a high-volume doji to break the doji's low."""
    if day <= target_day or bars[day].close >= bars[day - 1].low:
        return ""
    doji = bars[day - 1]
    span = doji.high - doji.low
    if span > 0 and abs(doji.close - doji.open) <= span * 0.2 and _high_volume(bars, day - 1):
        return "volume_doji_break"
    return ""


def volume_stall_exit(bars: list[Bar], day: int, target_day: int) -> str:
    """Flag three closes within 2%, ending flat or down on elevated volume."""
    if day - target_day < 2 or not _high_volume(bars, day):
        return ""
    closes = [bar.close for bar in bars[day - 2:day + 1]]
    if closes[-1] <= closes[0] and max(closes) <= min(closes) * 1.02:
        return "volume_stall"
    return ""
