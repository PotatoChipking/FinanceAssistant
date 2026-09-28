from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.screener.providers import PanWatchScreenerDataProvider
from src.web.database import Base
from src.web.models import Stock, WatchedBoard


def test_universe_dedupes_watchlist_and_board_stocks(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        db.add(Stock(symbol="600519", market="CN", name="贵州茅台", sort_order=1))
        db.add(WatchedBoard(market="CN", board_code="BK0001", board_name="白酒", sort_order=1, tier="pinned", enabled=True))
        db.add(WatchedBoard(market="CN", board_code="BK0002", board_name="食品", sort_order=2, tier="pool", enabled=True))
        db.commit()

        requested_boards = []

        class FakeDiscoveryOrchestrator:
            def fetch_sync(self, req, **kwargs):
                requested_boards.append(dict(req.extra)["board_code"])
                return SimpleNamespace(
                    success=True,
                    data=[
                        SimpleNamespace(symbol="600519", name="贵州茅台"),
                        SimpleNamespace(symbol="000858", name="五粮液"),
                    ],
                )

        monkeypatch.setattr(
            "src.core.screener.providers.get_discovery_orchestrator",
            lambda: FakeDiscoveryOrchestrator(),
        )

        rows = PanWatchScreenerDataProvider().resolve_universe(
            db,
            {
                "include_watchlist": True,
                "include_watched_boards": True,
                "board_codes": [],
                "max_symbols": 300,
            },
            limit=300,
        )

        keys = [f"{x.market}:{x.symbol}" for x in rows]
        assert keys == ["CN:600519", "CN:000858"]
        assert rows[0].board_code == "BK0001"
        assert requested_boards == ["BK0001", "BK0002"]
    finally:
        db.close()


def test_universe_spreads_small_limit_across_boards(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        for i in range(4):
            db.add(WatchedBoard(market="CN", board_code=f"BK{i}", board_name=f"板块{i}", sort_order=i, tier="pool", enabled=True))
        db.commit()

        class FakeDiscoveryOrchestrator:
            def fetch_sync(self, req, **kwargs):
                extra = dict(req.extra)
                return SimpleNamespace(
                    success=True,
                    data=[SimpleNamespace(symbol=f"60000{extra['board_code'][-1]}", name="测试")]
                    [: extra["limit"]],
                )

        monkeypatch.setattr(
            "src.core.screener.providers.get_discovery_orchestrator",
            lambda: FakeDiscoveryOrchestrator(),
        )
        rows = PanWatchScreenerDataProvider().resolve_universe(
            db,
            {"include_watchlist": False, "include_watched_boards": True},
            limit=4,
        )
        assert [row.board_code for row in rows] == ["BK0", "BK1", "BK2", "BK3"]
    finally:
        db.close()
