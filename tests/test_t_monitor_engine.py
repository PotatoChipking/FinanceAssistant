import asyncio

from pytest import MonkeyPatch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.collectors.kline_collector import KlineData
from src.core import t_monitor_engine
from src.web.api.t_monitor import list_states
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
    assert row["context"]["minute_at"] == "2026-11-10 10:01"
