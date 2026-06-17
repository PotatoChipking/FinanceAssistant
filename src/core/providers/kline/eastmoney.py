"""东方财富 K 线 Provider."""

from __future__ import annotations

import asyncio

from src.collectors.kline_collector import (
    _fetch_eastmoney_intraday_klines,
    _fetch_eastmoney_klines,
)
from src.core.providers.base import (
    KlineProvider,
    ProviderRequest,
    ProviderResponse,
    is_intraday_interval,
)
from src.models.market import MarketCode


def _extra(req: ProviderRequest, key: str, default):
    for k, v in req.extra:
        if k == key:
            return v
    return default


class EastmoneyKlineProvider(KlineProvider):
    name = "eastmoney"
    supports_markets = {"CN", "HK", "US"}
    supports_intraday = True  # 分时/五分(CN/HK)

    async def fetch(self, req: ProviderRequest) -> ProviderResponse:
        if not req.symbols:
            return ProviderResponse(success=True, data=[])
        if len(req.symbols) > 1:
            return ProviderResponse(success=False, error="EastmoneyKlineProvider 仅支持单 symbol")
        try:
            market = MarketCode(req.market)
        except ValueError:
            return ProviderResponse(success=False, error=f"unsupported market: {req.market}")

        symbol = req.symbols[0]
        days = int(_extra(req, "days", 60) or 60)
        interval = str(_extra(req, "interval", "") or "").lower()
        try:
            if is_intraday_interval(interval):
                if market not in (MarketCode.CN, MarketCode.HK):
                    return ProviderResponse(success=False, error="eastmoney intraday only supports CN/HK")
                data = await asyncio.to_thread(
                    _fetch_eastmoney_intraday_klines,
                    symbol,
                    market,
                    interval,
                    max(1, days),
                )
            else:
                data = await asyncio.to_thread(
                    _fetch_eastmoney_klines,
                    symbol,
                    market,
                    max(1, days),
                )
        except Exception as e:
            return ProviderResponse(success=False, error=str(e))

        return ProviderResponse(success=True, data=data)
