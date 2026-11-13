import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src.collectors.discovery_collector import EastMoneyDiscoveryCollector, HotStock, BoardKline
from src.web.api import discovery


def test_rapid_field_and_missing_values():
    collector = EastMoneyDiscoveryCollector()
    collector._get_json = AsyncMock(return_value={"data": {"diff": [
        {"f12": "000001", "f11": "2.5"}, {"f12": "000002", "f11": "-"},
    ]}})
    rows = asyncio.run(collector.fetch_hot_stocks(mode="rapid"))
    assert collector._get_json.call_args.kwargs["params"]["fid"] == "f11"
    assert rows[0].change_5m_pct == 2.5
    assert rows[1].change_5m_pct is None


def test_rapid_sorted_positive_without_snapshot():
    collector = Mock()
    collector.fetch_hot_stocks = AsyncMock(return_value=[
        HotStock(str(i), "CN", "test", 10, 1, 100, 10, change)
        for i, change in enumerate([None, -1, 0, 2, 4])
    ])
    db = Mock()
    rows = asyncio.run(discovery._hot_stocks_live_or_snapshot(collector=collector, db=db, market="CN", mode="rapid", limit=20))
    assert [row["change_5m_pct"] for row in rows] == [4, 2]
    db.query.assert_not_called()
    collector.fetch_hot_stocks.side_effect = RuntimeError("offline")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(discovery._hot_stocks_live_or_snapshot(collector=collector, db=db, market="CN", mode="rapid", limit=20))
    assert exc.value.status_code == 503
    db.query.assert_not_called()


def test_board_trend_and_synthetic_unavailable(monkeypatch):
    discovery._cache.clear()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    yesterday = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=1)).strftime("%Y-%m-%d")
    mock = AsyncMock(return_value=[
        BoardKline(f"{yesterday} 15:00", 9, 9, 9, 9, None, None),
        BoardKline(f"{today} 09:31", 10, 10, 10, 10, None, None),
        BoardKline(f"{today} 09:32", 11, 11, 11, 11, None, None),
    ])
    monkeypatch.setattr(EastMoneyDiscoveryCollector, "fetch_board_intraday", mock)
    monkeypatch.setattr(discovery, "_resolve_proxy", lambda: "")
    monkeypatch.setattr(EastMoneyDiscoveryCollector, "fetch_board_klines", AsyncMock(return_value=[BoardKline(yesterday, 9, 9, 9, 9, None, None)]))
    result = asyncio.run(discovery.get_board_trend("BK0001"))
    assert result["prev_close"] == 9
    assert result["change_pct"] is None
    assert all(point["date"].startswith(today) for point in result["points"])
    assert len(result["points"]) == 2
    assert asyncio.run(discovery.get_board_trend("HK_hot"))["points"] == []
    mock.assert_awaited_once()
    discovery._cache.clear()


def test_board_intraday_uses_minute_bars_and_skips_bad_prices():
    collector = EastMoneyDiscoveryCollector()
    collector._get_json = AsyncMock(return_value={"data": {"klines": [
        "2026-11-13 09:31,10,11,12,9,100,1000",
        "2026-11-13 09:32,10,-,12,9,100,1000",
        "bad",
    ]}})
    bars = asyncio.run(collector.fetch_board_intraday(board_code="BK0001"))
    assert collector._get_json.call_args.kwargs["params"]["klt"] == "1"
    assert len(bars) == 1
    assert bars[0].close == 11


def test_board_intraday_does_not_show_previous_day(monkeypatch):
    discovery._cache.clear()
    mock = AsyncMock(return_value=[BoardKline("2000-01-01 15:00", 10, 10, 10, 10, None, None)])
    monkeypatch.setattr(EastMoneyDiscoveryCollector, "fetch_board_intraday", mock)
    monkeypatch.setattr(discovery, "_resolve_proxy", lambda: "")
    assert asyncio.run(discovery.get_board_trend("BK0001"))["points"] == []
