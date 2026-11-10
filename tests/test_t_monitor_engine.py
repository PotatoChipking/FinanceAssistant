import asyncio
from datetime import date, timedelta

from pytest import MonkeyPatch, approx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.collectors.kline_collector import KlineData
from src.core import t_monitor_engine
from src.web.api.t_monitor import list_states, scan
from src.web.database import Base
from src.web.models import Account, Position, Stock


def test_scan_keeps_latest_minute_price_without_daily_bars(monkeypatch: MonkeyPatch) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)
    with session() as db:
        account = Account(name="测试账户")
        for symbol in ("600519", "000001"):
            stock = Stock(symbol=symbol, name=symbol, market="CN")
            db.add(Position(account=account, stock=stock, cost_price=10, quantity=100))
        db.commit()

    minute_bars = [KlineData("2026-11-10 10:00", 10, 10, 10, 10, 100)]

    def fetch(symbol: str, market: str, *, interval: str, **kwargs: int) -> list[KlineData]:
        return minute_bars if interval == "1min" and symbol == "600519" else []

    monkeypatch.setattr(t_monitor_engine, "SessionLocal", session)
    monkeypatch.setattr(t_monitor_engine, "fetch_klines_sync", fetch)
    monkeypatch.setattr(t_monitor_engine, "get_strategy_profile_map", lambda: {})

    result = asyncio.run(t_monitor_engine.ENGINE.scan_once(bypass_market_hours=True))
    assert result["scanned"] == 2
    skipped = next(row for row in result["results"] if row["symbol"] == "000001")
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "分钟 K 线不可用"

    minute_bars.append(KlineData("2026-11-10 10:01", 10, 10.2, 10.2, 10, 100))
    asyncio.run(t_monitor_engine.ENGINE.scan_once(bypass_market_hours=True))
    with session() as db:
        [row] = list_states(db)
    assert row["current_price"] == 10.2
    assert row["vwap"] == approx((10 + (10.2 + 10 + 10.2) / 3) / 2)
    assert row["context"]["minute_at"] == "2026-11-10 10:01"


def test_manual_scan_recalculates_vwap_and_score_with_new_minutes(monkeypatch: MonkeyPatch) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)
    with session() as db:
        db.add(Position(
            account=Account(name="测试账户"),
            stock=Stock(symbol="600519", name="测试股票", market="CN"),
            cost_price=10,
            quantity=100,
        ))
        db.commit()

    trade_date = date(2026, 11, 10)
    daily = [KlineData(str(trade_date - timedelta(days=i)), 10, 10, 10.2, 9.8, 1000) for i in range(30, 0, -1)]
    minute = [KlineData(f"{trade_date} 09:{i}", 10, 10, 10, 10, 100) for i in range(31, 59)]
    fresh_minute = [*minute, KlineData(f"{trade_date} 09:59", 10, 9.7, 10, 9.7, 100)]

    def fetch(
        symbol: str, market: str, *, interval: str, refresh_intraday: bool = False, **kwargs: int,
    ) -> list[KlineData]:
        if interval == "1d":
            return daily
        return fresh_minute if refresh_intraday else minute

    monkeypatch.setattr(t_monitor_engine, "SessionLocal", session)
    monkeypatch.setattr(t_monitor_engine, "fetch_klines_sync", fetch)
    monkeypatch.setattr(t_monitor_engine, "get_strategy_profile_map", lambda: {
        "base_position_vwap_t": {"params": {"direction": "long"}},
    })

    asyncio.run(t_monitor_engine.ENGINE.scan_once(bypass_market_hours=True))
    with session() as db:
        [previous] = list_states(db)

    assert asyncio.run(scan())["scanned"] == 1
    with session() as db:
        [current] = list_states(db)
    assert current["context"]["minute_at"] == f"{trade_date} 09:59"
    assert current["vwap"] < previous["vwap"]
    assert current["score"] != previous["score"]

    # 之后的数据源若返回旧分钟 K，已有结果应继续保留。
    result = asyncio.run(t_monitor_engine.ENGINE.scan_once(bypass_market_hours=True))
    assert result["results"][0]["status"] == "skipped"
    with session() as db:
        [latest] = list_states(db)
    assert latest["context"]["minute_at"] == current["context"]["minute_at"]
