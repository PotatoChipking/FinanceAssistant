"""Relative-strength annotations for the breakout observation pool."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean


MIN_INDUSTRY_STOCKS = 10
INDUSTRY_MAX_AGE_DAYS = 7


@dataclass(frozen=True)
class BreakoutStrength:
    strong: bool
    industry: str
    industry_as_of: str
    price_as_of: str
    industry_rank: int
    industry_count: int
    stock_rank: int
    peer_count: int
    industry_return_pct: float
    stock_return_pct: float


def rank_breakout_strength(
    stocks: list[dict],
    closes_by_symbol: dict[str, dict[str, float]],
    candidate_dates: dict[str, str],
    *,
    snapshot_date: str,
) -> dict[str, BreakoutStrength]:
    """Rank twenty-session returns through D0−1 using the scan's industry snapshot."""
    by_breakout = defaultdict(list)
    for symbol, breakout in candidate_dates.items():
        by_breakout[breakout].append(symbol)
    if not by_breakout:
        return {}

    scan_date = date.fromisoformat(snapshot_date)
    industries: dict[str, tuple[str, str]] = {}
    for stock in stocks:
        label, stamp = stock.get("industry"), stock.get("industry_as_of")
        if not label or not stamp:
            continue
        try:
            age = (scan_date - date.fromisoformat(stamp)).days
        except (TypeError, ValueError):
            continue
        if 0 <= age <= INDUSTRY_MAX_AGE_DAYS:
            industries[stock["symbol"]] = label, stamp
    if not industries:
        return {}

    # Use shared session endpoints so a suspended stock cannot compare
    # twenty of its own bars with a shorter interval for its peers.
    dates = sorted({day for closes in closes_by_symbol.values() for day in closes if day <= snapshot_date})
    sessions = {day: index for index, day in enumerate(dates)}
    strengths = {}
    for breakout, symbols in by_breakout.items():
        end = sessions.get(breakout, 0)
        if end < 21:
            continue
        start_day, last_day = dates[end - 21], dates[end - 1]
        groups: dict[str, dict[str, float]] = defaultdict(dict)
        for symbol, (industry, _) in industries.items():
            closes = closes_by_symbol.get(symbol, {})
            start, last = closes.get(start_day), closes.get(last_day)
            if start is not None and last is not None:
                groups[industry][symbol] = 100 * (last / start - 1)
        averages = {industry: mean(returns.values()) for industry, returns in groups.items()
                    if len(returns) >= MIN_INDUSTRY_STOCKS}
        for symbol in symbols:
            industry, stamp = industries.get(symbol, ("", ""))
            if industry not in averages or symbol not in groups[industry]:
                continue
            peers = groups[industry]
            stock_return = peers[symbol]
            peer_return = mean(value for code, value in peers.items() if code != symbol)
            industry_rank = 1 + sum(value > peer_return for name, value in averages.items()
                                    if name != industry)
            stock_rank = 1 + sum(value > stock_return for value in peers.values())
            strengths[symbol] = BreakoutStrength(
                strong=3 * industry_rank <= len(averages) and 3 * stock_rank <= len(peers),
                industry=industry,
                industry_as_of=stamp,
                price_as_of=last_day,
                industry_rank=industry_rank,
                industry_count=len(averages),
                stock_rank=stock_rank,
                peer_count=len(peers),
                industry_return_pct=round(peer_return, 4),
                stock_return_pct=round(stock_return, 4),
            )
    return strengths
