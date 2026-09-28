from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core import paper_screener, strategy_catalog, strategy_pool
from src.web.api import paper_trading, screener
from src.web.database import Base
from src.web.models import (
    PaperTradingAccount,
    StockScreenerFormula,
    StockScreenerResult,
    StockScreenerRun,
    StrategySignalRun,
)


def _session_factory(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'paper_screener.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(paper_screener, "SessionLocal", factory)
    monkeypatch.setattr(strategy_pool, "SessionLocal", factory)
    monkeypatch.setattr(strategy_catalog, "SessionLocal", factory)
    return factory


def _add_run(db, formula, *, matched: bool) -> StockScreenerRun:
    run = StockScreenerRun(
        formula_id=formula.id,
        formula_snapshot=formula.formula,
        universe_config=formula.universe_config,
        status="success",
        total_count=1,
        matched_count=int(matched),
        created_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    if matched:
        db.add(StockScreenerResult(
            run_id=run.id,
            symbol="600001",
            market="CN",
            name="样本股",
            matched=True,
            last_close=10.0,
            change_pct=1.0,
            reason="命中",
            indicators={},
        ))
    db.commit()
    return run


def test_zero_match_run_retires_old_paper_signals(tmp_path, monkeypatch):
    factory = _session_factory(tmp_path, monkeypatch)
    db = factory()
    try:
        formula = StockScreenerFormula(
            name="突破回踩", formula="C > O", universe_config={}, enabled=True,
        )
        db.add(formula)
        db.commit()
        successful = _add_run(db, formula, matched=True)

        first = paper_screener.publish_screener_run(db, successful)
        assert first["created"] == 1
        assert db.query(StrategySignalRun).filter(
            StrategySignalRun.strategy_code == f"screener:{formula.id}",
            StrategySignalRun.status == "active",
        ).count() == 1

        empty = _add_run(db, formula, matched=False)
        second = paper_screener.publish_screener_run(db, empty)
        assert second["matched"] == 0
        assert db.query(StrategySignalRun).filter(
            StrategySignalRun.strategy_code == f"screener:{formula.id}",
            StrategySignalRun.status == "active",
        ).count() == 0
        assert paper_screener.publish_screener_run(db, empty)["already_published"] is True
    finally:
        db.close()


def test_selected_formula_runs_once_per_local_day(tmp_path, monkeypatch):
    factory = _session_factory(tmp_path, monkeypatch)
    db = factory()
    try:
        formula = StockScreenerFormula(
            name="突破回踩", formula="C > O", universe_config={}, enabled=True,
        )
        db.add_all([
            formula,
            PaperTradingAccount(enabled=True, initial_capital=100000, current_capital=100000),
        ])
        db.commit()
        formula_id = formula.id
        strategy_pool.save_paper_strategy_selection({
            "mode": "custom", "strategy_codes": [f"screener:{formula_id}"], "top_n": 5,
        }, db)
    finally:
        db.close()


    calls = []

    def fake_run(run_id: int):
        calls.append(run_id)
        session = factory()
        try:
            run = session.query(StockScreenerRun).filter_by(id=run_id).one()
            run.status = "success"
            run.total_count = 1
            run.matched_count = 1
            run.finished_at = datetime.now(timezone.utc)
            session.add(StockScreenerResult(
                run_id=run.id, symbol="600001", market="CN", name="样本股",
                matched=True, last_close=10.0, change_pct=1.0,
                reason="命中", indicators={},
            ))
            session.commit()
        finally:
            session.close()

    monkeypatch.setattr(screener, "_run_screener_job", fake_run)
    first = paper_screener.run_selected_paper_formulas()
    second = paper_screener.run_selected_paper_formulas()
    assert first["selected"] == second["selected"] == 1
    assert len(calls) == 1
    assert second["runs"][0]["already_published"] is True

    db = factory()
    try:
        assert db.query(StrategySignalRun).filter(
            StrategySignalRun.strategy_code == f"screener:{formula_id}",
            StrategySignalRun.status == "active",
        ).count() == 1
    finally:
        db.close()


def test_saved_formula_is_selectable_and_publishing_adds_custom_code(tmp_path, monkeypatch):
    factory = _session_factory(tmp_path, monkeypatch)
    db = factory()
    try:
        formula = StockScreenerFormula(
            name="突破回踩", formula="C > O", universe_config={}, enabled=True,
        )
        db.add(formula)
        db.commit()
        formula_id = formula.id
        selection = paper_trading.get_strategy_selection(db)
        assert f"screener:{formula_id}" in {
            item["code"] for item in selection["strategy_pool"]
        }

        strategy_pool.save_paper_strategy_selection({
            "mode": "custom", "strategy_codes": [], "top_n": 5,
        }, db)
        run = _add_run(db, formula, matched=True)
        result = asyncio.run(paper_trading.create_signals_from_screener_strategy(
            paper_trading.ScreenerStrategyBody(run_id=run.id, trigger_scan=False), db,
        ))
        assert result["created"] == 1
        assert f"screener:{formula_id}" in strategy_pool.get_paper_strategy_selection(db)["strategy_codes"]
    finally:
        db.close()
