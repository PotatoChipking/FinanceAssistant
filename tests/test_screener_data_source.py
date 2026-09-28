import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import server
from src.core.data_collector import DataCollectorManager
from src.core.providers.base import ProviderResponse
from src.core.providers.discovery.eastmoney import EastmoneyDiscoveryProvider
from src.web.database import Base
from src.web.models import DataSource


def test_seed_adds_discovery_to_existing_database_and_preserves_user_settings(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sources.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(server, "SessionLocal", Session)

    server.seed_data_sources()
    db = Session()
    try:
        discovery = db.query(DataSource).filter_by(type="discovery", provider="eastmoney").one()
        assert discovery.enabled is True
        discovery_id = discovery.id
        discovery.enabled = False
        discovery.config = {"timeout": 15}
        db.commit()

        server.seed_data_sources()
        db.expire_all()
        rows = db.query(DataSource).filter_by(type="discovery", provider="eastmoney").all()
        assert len(rows) == 1
        assert rows[0].id == discovery_id
        assert rows[0].enabled is False
        assert rows[0].config == {"timeout": 15}
    finally:
        db.close()


def test_discovery_source_test_checks_board_members(monkeypatch):
    requested_kinds = []

    async def fake_fetch(self, req):
        extra = dict(req.extra)
        requested_kinds.append(extra["kind"])
        if extra["kind"] == "boards":
            return ProviderResponse(success=True, data=[SimpleNamespace(code="BK1")])
        assert extra["board_code"] == "BK1"
        return ProviderResponse(
            success=True,
            data=[SimpleNamespace(symbol="600519", name="测试股票")],
        )

    monkeypatch.setattr(EastmoneyDiscoveryProvider, "fetch", fake_fetch)
    source = DataSource(name="东方财富股票发现", type="discovery", provider="eastmoney", config={})
    result = asyncio.run(DataCollectorManager()._test_source_impl(source, []))
    assert result.success is True
    assert result.count == 1
    assert result.data[0]["symbol"] == "600519"
    assert requested_kinds == ["boards", "board_stocks"]
