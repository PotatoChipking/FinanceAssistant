import logging
import math
from dataclasses import dataclass

import httpx


logger = logging.getLogger(__name__)


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class HotStock:
    symbol: str
    market: str
    name: str
    price: float | None
    change_pct: float | None
    turnover: float | None
    volume: float | None
    change_5m_pct: float | None = None


@dataclass(frozen=True)
class HotBoard:
    code: str
    name: str
    change_pct: float | None
    change_amount: float | None
    turnover: float | None


@dataclass(frozen=True)
class BoardKline:
    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float | None
    turnover: float | None


class EastMoneyDiscoveryCollector:
    """Discovery ranks via EastMoney push2 (CN/HK/US)."""

    STOCKS_API = "https://push2.eastmoney.com/api/qt/clist/get"
    BOARDS_API = "https://push2.eastmoney.com/api/qt/clist/get"
    BOARD_KLINE_API = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    def __init__(
        self,
        *,
        timeout_s: float = 10.0,
        verify_ssl: bool = False,
        proxy: str | None = None,
        retries: int = 1,
        backoff_s: float = 0.4,
    ):
        self.timeout_s = float(timeout_s)
        self.verify_ssl = bool(verify_ssl)
        self.proxy = proxy
        self.retries = int(retries)
        self.backoff_s = float(backoff_s)

    async def fetch_hot_stocks(
        self,
        *,
        market: str = "CN",
        mode: str = "turnover",
        limit: int = 20,
    ) -> list[HotStock]:
        market = (market or "CN").upper()

        fid = {"turnover": "f6", "rapid": "f11"}.get(mode, "f3")
        fields = "f12,f14,f2,f3,f6,f5,f11"
        if market == "CN":
            fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"  # A-share
        elif market == "HK":
            fs = "m:128+t:3,m:128+t:4,m:128+t:1,m:128+t:2"  # HK
        elif market == "US":
            fs = "m:105,m:106,m:107"  # US
        else:
            return []

        params = {
            "pn": 1,
            "pz": max(1, min(int(limit), 100)),
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": fid,
            "fs": fs,
            "fields": fields,
        }

        data = await self._get_json(self.STOCKS_API, params=params)
        diff = ((data or {}).get("data") or {}).get("diff") or []
        result: list[HotStock] = []
        for it in diff:
            try:
                result.append(
                    HotStock(
                        symbol=str(it.get("f12") or "").strip(),
                        market=market,
                        name=str(it.get("f14") or "").strip(),
                        price=_finite_number(it.get("f2")),
                        change_pct=_finite_number(it.get("f3")),
                        turnover=_finite_number(it.get("f6")),
                        volume=_finite_number(it.get("f5")),
                        change_5m_pct=_finite_number(it.get("f11")),
                    )
                )
            except Exception:
                continue
        return result

    async def fetch_hot_boards(
        self,
        *,
        market: str = "CN",
        mode: str = "gainers",
        limit: int = 12,
        scope: str = "industry",
    ) -> list[HotBoard]:
        if market != "CN":
            return []

        fid = "f3" if mode in ("gainers", "hot") else "f6"
        fields = "f12,f14,f2,f3,f4,f6"
        scope_key = (scope or "industry").strip().lower()
        fs = "m:90+t:3" if scope_key in ("concept", "concepts") else "m:90+t:2"

        params = {
            "pn": 1,
            "pz": max(1, min(int(limit), 500)),
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": fid,
            "fs": fs,
            "fields": fields,
        }

        data = await self._get_json(self.BOARDS_API, params=params)
        diff = ((data or {}).get("data") or {}).get("diff") or []
        result: list[HotBoard] = []
        for it in diff:
            try:
                result.append(
                    HotBoard(
                        code=str(it.get("f12") or "").strip(),
                        name=str(it.get("f14") or "").strip(),
                        change_pct=it.get("f3"),
                        change_amount=it.get("f4"),
                        turnover=it.get("f6"),
                    )
                )
            except Exception:
                continue
        return result

    async def fetch_board_stocks(
        self,
        *,
        board_code: str,
        mode: str = "gainers",
        limit: int = 20,
    ) -> list[HotStock]:
        code = (board_code or "").strip()
        if not code:
            return []

        fid = "f3" if mode in ("gainers", "hot") else "f6"
        fields = "f12,f14,f2,f3,f6,f5"
        fs = f"b:{code}"

        params = {
            "pn": 1,
            "pz": max(1, min(int(limit), 100)),
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": fid,
            "fs": fs,
            "fields": fields,
        }

        data = await self._get_json(self.STOCKS_API, params=params)
        diff = ((data or {}).get("data") or {}).get("diff") or []
        result: list[HotStock] = []
        for it in diff:
            try:
                result.append(
                    HotStock(
                        symbol=str(it.get("f12") or "").strip(),
                        market="CN",
                        name=str(it.get("f14") or "").strip(),
                        price=it.get("f2"),
                        change_pct=it.get("f3"),
                        turnover=it.get("f6"),
                        volume=it.get("f5"),
                    )
                )
            except Exception:
                continue
        return result

    async def fetch_board_klines(
        self,
        *,
        board_code: str,
        days: int = 120,
    ) -> list[BoardKline]:
        code = (board_code or "").strip().upper()
        if not code:
            return []

        limit = max(1, min(int(days or 120), 1300))
        params = {
            "secid": f"90.{code}",
            "klt": "101",
            "fqt": "1",
            "lmt": str(limit),
            "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }

        data = await self._get_json(self.BOARD_KLINE_API, params=params)
        rows = ((data or {}).get("data") or {}).get("klines") or []
        result: list[BoardKline] = []
        for row in rows:
            parts = str(row).split(",")
            if len(parts) < 6:
                continue
            try:
                result.append(
                    BoardKline(
                        date=parts[0],
                        open=float(parts[1]),
                        close=float(parts[2]),
                        high=float(parts[3]),
                        low=float(parts[4]),
                        volume=float(parts[5]) if parts[5] not in ("", "-") else None,
                        turnover=float(parts[6]) if len(parts) > 6 and parts[6] not in ("", "-") else None,
                    )
                )
            except Exception:
                continue
        return result[-limit:]

    async def fetch_board_intraday(self, *, board_code: str) -> list[BoardKline]:
        """One-minute board bars; the API layer selects today's Shanghai session."""
        params = {
            "secid": f"90.{board_code.strip().upper()}",
            "klt": "1", "fqt": "0", "lmt": "300", "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
        }
        data = await self._get_json(self.BOARD_KLINE_API, params=params)
        rows = ((data or {}).get("data") or {}).get("klines") or []
        result: list[BoardKline] = []
        for row in rows:
            parts = str(row).split(",")
            if len(parts) < 5:
                continue
            close = _finite_number(parts[2])
            if close is None or close <= 0:
                continue
            result.append(BoardKline(
                date=parts[0], open=_finite_number(parts[1]) or close,
                close=close, high=_finite_number(parts[3]) or close,
                low=_finite_number(parts[4]) or close, volume=None, turnover=None,
            ))
        return result

    async def _get_json(self, url: str, *, params: dict) -> dict:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        }

        last_exc: Exception | None = None
        attempts = max(self.retries, 0) + 1

        for attempt in range(attempts):
            try:
                timeout = httpx.Timeout(
                    self.timeout_s, connect=min(self.timeout_s, 6.0)
                )
                async with httpx.AsyncClient(
                    timeout=timeout,
                    verify=self.verify_ssl,
                    follow_redirects=True,
                    trust_env=True,
                    headers=headers,
                    proxy=self.proxy,
                ) as client:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    return resp.json()
            except Exception as e:
                last_exc = e
                if attempt < attempts - 1:
                    try:
                        import asyncio

                        await asyncio.sleep(self.backoff_s * (attempt + 1))
                    except Exception:
                        pass
                    continue

        if last_exc is not None:
            logger.warning(
                f"EastMoney discovery request failed: {type(last_exc).__name__}: {last_exc!r}"
            )
        return {}
