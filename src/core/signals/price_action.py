"""Price Action signal calculation over normalized daily K-lines."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite
from typing import Any


STRATEGY_CODE = "price_action"


@dataclass(frozen=True)
class PriceActionParams:
    breakout_window: int = 20
    pullback_window: int = 10
    volume_ma_window: int = 5
    volume_ratio: float = 1.5
    atr_n: int = 14
    atr_stop_multiple: float = 2.0
    pullback_tolerance: float = 0.03
    reward_risk_ratio: float = 2.0
    max_stop_pct: float = 0.08


def price_action_params_from_dict(raw: dict | None) -> PriceActionParams:
    values = raw if isinstance(raw, dict) else {}
    defaults = PriceActionParams()
    return PriceActionParams(
        breakout_window=max(5, int(values.get("breakout_window", defaults.breakout_window))),
        pullback_window=max(2, int(values.get("pullback_window", defaults.pullback_window))),
        volume_ma_window=max(2, int(values.get("volume_ma_window", defaults.volume_ma_window))),
        volume_ratio=max(0.1, float(values.get("volume_ratio", defaults.volume_ratio))),
        atr_n=max(2, int(values.get("atr_n", defaults.atr_n))),
        atr_stop_multiple=max(0.1, float(values.get("atr_stop_multiple", defaults.atr_stop_multiple))),
        pullback_tolerance=max(0.001, float(values.get("pullback_tolerance", defaults.pullback_tolerance))),
        reward_risk_ratio=max(0.1, float(values.get("reward_risk_ratio", defaults.reward_risk_ratio))),
        max_stop_pct=max(0.001, float(values.get("max_stop_pct", defaults.max_stop_pct))),
    )


@dataclass(frozen=True)
class PriceActionEvent:
    date: str
    type: str
    text: str
    price: float
    level: float | None = None


@dataclass(frozen=True)
class PriceActionResult:
    valid: bool
    score: int = 0
    strategy_code: str = STRATEGY_CODE
    timeframe: str = "1d"
    price_basis: str = "adjusted_provider_series"
    params: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    signals: dict[str, bool] = field(default_factory=dict)
    levels: dict[str, float | None] = field(default_factory=dict)
    events: list[PriceActionEvent] = field(default_factory=list)
    data_as_of: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Bar:
    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if isfinite(parsed) else default


def _normalize(klines: list[Any]) -> list[_Bar]:
    """Sort, de-duplicate and discard unusable OHLC rows."""

    by_date: dict[str, _Bar] = {}
    for row in klines or []:
        date = str(_get(row, "date", "") or "").strip()
        open_price = _float(_get(row, "open"))
        close = _float(_get(row, "close"))
        high = _float(_get(row, "high"))
        low = _float(_get(row, "low"))
        volume = _float(_get(row, "volume"), 0.0)
        if not date or None in (open_price, close, high, low, volume):
            continue
        if min(open_price, close, high, low) <= 0 or volume < 0:
            continue
        if high < max(open_price, close, low) or low > min(open_price, close, high):
            continue
        by_date[date] = _Bar(date, open_price, close, high, low, volume)
    return [by_date[key] for key in sorted(by_date)]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _atr_at(rows: list[_Bar], index: int, n: int) -> float | None:
    if index < n:
        return None
    true_ranges: list[float] = []
    for i in range(index - n + 1, index + 1):
        if i <= 0:
            continue
        bar = rows[i]
        prev_close = rows[i - 1].close
        true_ranges.append(
            max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))
        )
    return _mean(true_ranges) if len(true_ranges) == n else None


def _round(value: float | None) -> float | None:
    return round(value, 3) if value is not None and isfinite(value) else None


def compute_price_action(
    klines: list[Any],
    *,
    params: PriceActionParams | None = None,
) -> PriceActionResult:
    """Compute the latest PA factor and historical breakout/pullback events.

    Only data at or before each event bar is used, so the result can also be
    consumed by backtests without look-ahead leakage.
    """

    cfg = params or PriceActionParams()
    rows = _normalize(klines)
    required = max(60, cfg.breakout_window, cfg.atr_n) + 1
    if len(rows) < required:
        return PriceActionResult(
            valid=False,
            params=asdict(cfg),
            evidence=[f"K线数量不足，至少需要 {required} 根有效日线"],
            error="insufficient_klines",
            data_as_of=rows[-1].date if rows else None,
        )

    breakouts: dict[int, float] = {}
    pullbacks: dict[int, float] = {}
    events: list[PriceActionEvent] = []
    start = max(cfg.breakout_window, cfg.volume_ma_window)

    for i in range(start, len(rows)):
        bar = rows[i]
        resistance = max(x.high for x in rows[i - cfg.breakout_window : i])
        prior_volumes = [x.volume for x in rows[i - cfg.volume_ma_window : i]]
        volume_mean = _mean(prior_volumes) or 0.0
        is_breakout = (
            bar.close > resistance
            and volume_mean > 0
            and bar.volume >= volume_mean * cfg.volume_ratio
        )
        if is_breakout:
            breakouts[i] = resistance
            events.append(
                PriceActionEvent(
                    date=bar.date,
                    type="breakout",
                    text="PA突破",
                    price=round(bar.close, 3),
                    level=round(resistance, 3),
                )
            )

        recent = [
            (idx, level)
            for idx, level in breakouts.items()
            if max(start, i - cfg.pullback_window) <= idx < i
        ]
        if recent:
            _, breakout_level = recent[-1]
            lower = breakout_level * (1 - cfg.pullback_tolerance)
            upper = breakout_level * (1 + cfg.pullback_tolerance)
            is_pullback = (
                lower <= bar.low <= upper
                and bar.close > breakout_level
                and bar.close > bar.open
            )
            if is_pullback:
                pullbacks[i] = breakout_level
                events.append(
                    PriceActionEvent(
                        date=bar.date,
                        type="pullback_confirm",
                        text="PA回踩确认",
                        price=round(bar.close, 3),
                        level=round(breakout_level, 3),
                    )
                )

    index = len(rows) - 1
    bar = rows[index]
    closes = [x.close for x in rows]
    ma20 = _mean(closes[index - 19 : index + 1])
    ma60 = _mean(closes[index - 59 : index + 1])
    trend_up = bool(ma20 is not None and ma60 is not None and bar.close > ma20 > ma60)
    resistance = max(x.high for x in rows[index - cfg.breakout_window : index])
    support = min(x.low for x in rows[index - cfg.breakout_window : index])
    breakout = index in breakouts
    pullback_confirm = index in pullbacks
    new_high = bar.close > resistance

    recent_breakouts = [
        (idx, level)
        for idx, level in breakouts.items()
        if max(start, index - cfg.pullback_window) <= idx <= index
    ]
    recent_breakout_index, recent_breakout_level = (
        recent_breakouts[-1] if recent_breakouts else (None, None)
    )
    structure_invalidated = bool(
        recent_breakout_level is not None
        and bar.close < recent_breakout_level * (1 - cfg.pullback_tolerance)
    )
    atr = _atr_at(rows, index, cfg.atr_n)
    anchor = recent_breakout_level if recent_breakout_level is not None else support
    structural_stop = anchor * (1 - cfg.pullback_tolerance) if anchor else None
    atr_stop = bar.close - atr * cfg.atr_stop_multiple if atr is not None else None
    stop_candidates = [x for x in (structural_stop, atr_stop) if x is not None and x > 0]
    stop_loss = min(stop_candidates) if stop_candidates else None
    risk_pct = (bar.close - stop_loss) / bar.close if stop_loss is not None else None
    risk_acceptable = bool(risk_pct is not None and 0 < risk_pct <= cfg.max_stop_pct)
    target_price = (
        bar.close + (bar.close - stop_loss) * cfg.reward_risk_ratio
        if stop_loss is not None and stop_loss < bar.close
        else None
    )

    tags: list[str] = []
    evidence: list[str] = []
    score = 0
    if trend_up:
        tags.append("pa_trend_up")
        evidence.append("价格处于多头趋势结构：收盘价 > MA20 > MA60")
        score += 25
    if breakout:
        tags.append("pa_breakout")
        evidence.append(f"放量突破 {cfg.breakout_window} 日高点 {resistance:.2f}")
        score += 35
    if pullback_confirm:
        tags.append("pa_pullback_confirm")
        evidence.append(f"突破后回踩确认，突破位约 {pullbacks[index]:.2f}")
        score += 30
    if new_high:
        tags.append("pa_new_high")
        evidence.append("收盘价创阶段新高")
        score += 10
    if stop_loss is not None and not risk_acceptable:
        evidence.append(f"止损距离 {risk_pct * 100:.1f}% 超过 {cfg.max_stop_pct * 100:.1f}% 上限")

    return PriceActionResult(
        valid=True,
        score=min(score, 100),
        params=asdict(cfg),
        tags=tags,
        evidence=evidence,
        signals={
            "breakout": breakout,
            "pullback_confirm": pullback_confirm,
            "trend_up": trend_up,
            "new_high": new_high,
            "risk_acceptable": risk_acceptable,
            "structure_invalidated": structure_invalidated,
        },
        levels={
            "last_close": _round(bar.close),
            "resistance": _round(resistance),
            "support": _round(support),
            "recent_breakout_level": _round(recent_breakout_level),
            "recent_breakout_index": float(recent_breakout_index) if recent_breakout_index is not None else None,
            "atr": _round(atr),
            "stop_loss": _round(stop_loss),
            "target_price": _round(target_price),
            "risk_pct": _round(risk_pct),
        },
        events=events[-40:],
        data_as_of=bar.date,
    )
