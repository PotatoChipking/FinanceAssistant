from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core import breakout_validity_scanner
from src.core.breakout_strength import rank_breakout_strength
from src.core.signals.breakout_validity import BreakoutValidityResult
from src.core.strong_b_paper import CN_TZ, STRONG_B_CODE
from src.web.api.breakout import b_watchlist
from src.web.database import Base
from src.web.models import StrategySignalRun


def test_scan_reports_missing_market_data_instead_of_zero_matches(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'breakout.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(breakout_validity_scanner, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(breakout_validity_scanner, "build_universe", lambda: [
        {"symbol": "600001", "name": "样本一"},
        {"symbol": "600002", "name": "样本二"},
    ])
    monkeypatch.setattr(breakout_validity_scanner, "_evaluate", lambda symbol, params: None)

    summary = breakout_validity_scanner.scan_market()

    assert summary["status"] == "data_unavailable"
    assert summary["scanned"] == 2
    assert summary["evaluated"] == 0
    assert summary["data_unavailable"] == 2
    assert summary["valid_active"] == 0


def test_scan_to_b_watchlist_includes_every_b_and_keeps_the_existing_action(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'watchlist.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    days = [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(25)]
    d0, snapshot = days[21], days[-1]
    stocks = [
        {"symbol": f"600{i:03}", "name": f"样本{i}", "market": "CN",
         "industry": f"行业{i // 10}", "industry_as_of": snapshot}
        for i in range(32)
    ]
    stocks[30].pop("industry")
    stocks[31].pop("industry")
    paths = {"600000": {"B": True, "C": True}, "600019": {"B": True},
             "600030": {"B": True}, "600031": {"A": True}}
    monkeypatch.setattr(breakout_validity_scanner, "SessionLocal", session_factory)
    monkeypatch.setattr(breakout_validity_scanner, "get_stock_list", lambda: stocks)
    monkeypatch.setattr(breakout_validity_scanner, "_today", lambda: snapshot)
    monkeypatch.setattr(breakout_validity_scanner, "_cn_now",
                        lambda: datetime(2026, 10, 25, 15, 20, tzinfo=CN_TZ))
    calendar = tuple((date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(60))
    monkeypatch.setattr(breakout_validity_scanner, "cn_sessions", lambda day: calendar)

    def fake_klines(symbol, *args, **kwargs):
        index = int(symbol[-3:])
        before = 130 - 13 * (index // 10) - index % 10
        return [
            {"date": day, "close": 100 if i < 20 else (before if i == 20 else 999 - before),
             "volume": 1000, "symbol": symbol}
            for i, day in enumerate(days)
        ]

    def fake_compute(rows, **kwargs):
        checks = paths.get(rows[-1]["symbol"], {})
        return BreakoutValidityResult(
            "valid_active" if checks else "not_in_pool", paths=checks,
            d0_date=d0, d0_index=21, g0=100, close=102, stop_loss=97, target_price=115,
        )

    monkeypatch.setattr(breakout_validity_scanner, "fetch_klines_sync", fake_klines)
    monkeypatch.setattr(breakout_validity_scanner, "compute_breakout_validity", fake_compute)
    summary = breakout_validity_scanner.scan_market()
    assert (summary["valid_active"], summary["b_candidates"], summary["strong_b"]) == (4, 3, 1)
    assert summary["b_strength_evaluated"] == 2
    assert summary["strong_b_paper_signals"] == 1
    with session_factory() as db:
        result = b_watchlist(db)
        assert result["snapshot_date"] == snapshot
        assert [item["symbol"] for item in result["items"]] == ["600000", "600019", "600030"]
        strong, ordinary, unknown = result["items"]
        assert strong["strength"]["strong"] is True
        assert strong["strength"]["price_as_of"] == days[20]
        assert strong["strength"]["stock_return_pct"] == 30
        assert strong["strength"]["industry_return_pct"] == 25
        assert ordinary["strength"]["strong"] is False
        assert unknown["strength"] is None
        assert {r.action for r in db.query(StrategySignalRun).all()} == {"buy"}
        paper = db.query(StrategySignalRun).filter_by(strategy_code=STRONG_B_CODE).one()
        assert paper.stock_symbol == "600000"
        assert paper.target_price is None
        assert paper.stop_loss == 97
        assert paper.holding_days == 20
        assert paper.payload["entry_ceiling"] == 115
        assert paper.payload["paper_entry_date"] == calendar[25]
        assert paper.payload["paper_exit_date"] == calendar[45]

    # A manual sample-only scan cannot replace the already evaluated rank.
    partial = breakout_validity_scanner.scan_market(limit=1)
    assert partial["b_strength_evaluated"] == 0
    with session_factory() as db:
        assert b_watchlist(db) == result

    # Continued active B on the next close is still in the observation pool,
    # but it cannot create a second opening order for the same breakout.
    actual_klines = fake_klines
    monkeypatch.setattr(
        breakout_validity_scanner, "fetch_klines_sync",
        lambda symbol, *args, **kwargs: [
            *actual_klines(symbol), {"date": calendar[25], "close": 102, "volume": 1000, "symbol": symbol},
        ],
    )
    monkeypatch.setattr(breakout_validity_scanner, "_today", lambda: calendar[25])
    monkeypatch.setattr(breakout_validity_scanner, "_cn_now",
                        lambda: datetime(2026, 10, 26, 15, 20, tzinfo=CN_TZ))
    assert breakout_validity_scanner.scan_market()["strong_b_paper_signals"] == 0
    with session_factory() as db:
        assert db.query(StrategySignalRun).filter_by(strategy_code=STRONG_B_CODE).count() == 1


def test_rank_removes_the_candidate_and_requires_shared_session_endpoints():
    dates = [(date(2026, 9, 1) + timedelta(days=i)).isoformat() for i in range(25)]
    stocks = [
        {"symbol": str(i), "industry": str(i // 10), "industry_as_of": dates[-1]}
        for i in range(32)
    ]
    closes = {
        s["symbol"]: dict.fromkeys(dates, 100.0) for s in stocks
    }
    for i in range(30):
        closes[str(i)][dates[20]] = 180 if i == 0 else (110, 113, 105)[i // 10]
    closes["1"].pop(dates[20])
    stocks[31]["industry_as_of"] = dates[0]
    stocks[30]["industry"] = "0"
    closes["30"][dates[20]] = 110

    result = rank_breakout_strength(stocks, closes, {s: dates[21] for s in ("0", "1", "31")},
                                    snapshot_date=dates[-1])
    assert set(result) == {"0"}
    assert result["0"].industry_rank == 2
    assert result["0"].stock_rank == 1
    assert result["0"].strong is False


def test_manual_preview_uses_closed_bars_and_does_not_write_signals(monkeypatch):
    monkeypatch.setattr(breakout_validity_scanner, "_cn_now",
                        lambda: datetime(2026, 11, 25, 11, 0, tzinfo=CN_TZ))
    monkeypatch.setattr(breakout_validity_scanner, "build_universe", lambda: [
        {"symbol": "600001", "name": "有当日盘中线"},
        {"symbol": "600002", "name": "行情滞后"},
    ])

    def klines(symbol, **kwargs):
        dates = ["2026-11-23", "2026-11-24", "2026-11-25"] if symbol == "600001" else ["2026-11-23"]
        return [{"date": day, "close": 10, "volume": 2000} for day in dates]

    def evaluate(rows, **kwargs):
        assert rows[-1]["date"] < "2026-11-25"
        return BreakoutValidityResult("valid_active", paths={"B": True}, d0_date="2026-11-19", g0=10)

    def reject(*args):
        raise AssertionError("预览不应生成模拟盘信号")

    monkeypatch.setattr(breakout_validity_scanner, "fetch_klines_sync", lambda symbol, *_args, **kw: klines(symbol, **kw))
    monkeypatch.setattr(breakout_validity_scanner, "compute_breakout_validity", evaluate)
    monkeypatch.setattr(breakout_validity_scanner, "SessionLocal", reject)
    monkeypatch.setattr(breakout_validity_scanner, "cn_sessions", reject)
    steps = []
    result = breakout_validity_scanner.scan_market(preview=True, on_progress=lambda i, n: steps.append((i, n)))
    assert result["snapshot_date"] == "2026-11-24"
    assert [item["symbol"] for item in result["items"]] == ["600001"]
    assert result["status"] == "ok"
    assert steps == [(0, 2), (2, 2)]
