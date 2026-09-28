from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core import breakout_validity_scanner
from src.web.database import Base


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
