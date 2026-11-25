"""Trading schedule and quotation checks for the strong-B paper strategy."""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
import math
from zoneinfo import ZoneInfo

import httpx

from src.models.market import MARKETS, MarketCode


STRONG_B_CODE = "breakout_strong_b"
STRONG_B_NAME = "强势 B（模拟）"
STRONG_B_HOLDING_SESSIONS = 20
CN_TZ = ZoneInfo("Asia/Shanghai")


@lru_cache(maxsize=2)
def cn_sessions(as_of: date) -> tuple[str, ...]:
    """Load the dated exchange calendar once per China calendar date."""
    from akshare.tool.trade_date_hist import hk_js_decode, py_mini_racer

    response = httpx.get(
        "https://finance.sina.com.cn/realstock/company/klc_td_sh.txt", timeout=8, follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.text.split('"', 2)[1]
    with py_mini_racer.MiniRacer() as parser:
        parser.eval(hk_js_decode)
        decoded = parser.call("d", payload)
    days = tuple(sorted({date.fromisoformat(value[:10]).isoformat() for value in decoded}))
    if not days or days[-1] < as_of.isoformat():
        raise ValueError("交易日历未覆盖当前日期")
    return days


def holding_deadline(sessions: tuple[str, ...], entry_day: str) -> str | None:
    i = bisect_right(sessions, entry_day) + STRONG_B_HOLDING_SESSIONS - 1
    return sessions[i] if i < len(sessions) else None


def is_opening_window(now: datetime) -> bool:
    local = now.astimezone(CN_TZ)
    return MARKETS[MarketCode.CN].is_trading_time(local) and local.time() < time(9, 40)


def is_tradable_quote(quote: dict, now: datetime, *, buy: bool) -> bool:
    """Accept fresh A-share quotes and avoid fills at the adverse price limit."""
    if not MARKETS[MarketCode.CN].is_trading_time(now):
        return False
    try:
        quote_time = datetime.fromisoformat(str(quote.get("data_as_of") or ""))
        if quote_time.tzinfo is None:
            quote_time = quote_time.replace(tzinfo=CN_TZ)
        age = (now - quote_time).total_seconds()
        price, previous, volume = (float(quote[key]) for key in ("current_price", "prev_close", "volume"))
        if not -5 <= age <= 120 or not all(math.isfinite(v) and v > 0 for v in (price, previous, volume)):
            return False
    except (KeyError, ValueError, TypeError):
        return False
    factor = Decimal("1.10") if buy else Decimal("0.90")
    limit = float((Decimal(str(previous)) * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return price < limit if buy else price > limit
