from collections.abc import Iterator
from sqlite3 import Connection

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.web.api.accounts import router
from src.web.database import Base, get_db
from src.web.models import Account, Position, Stock, TMonitorState, TSignalEvent


def test_delete_position_returns_success_after_commit() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Connection, _record: object) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        account = Account(name="测试账户")
        stock = Stock(name="测试股票", symbol="600519", market="CN")
        position = Position(account=account, stock=stock, cost_price=10, quantity=100)
        db.add(position)
        db.flush()
        position_id = position.id
        state = TMonitorState(position_id=position_id, trade_date="2026-11-10")
        db.add(state)
        db.flush()
        db.add(TSignalEvent(
            state_id=state.id, position_id=position_id, signal_id="test",
            trade_date="2026-11-10", action="buy_t",
        ))
        db.commit()

    def get_test_db() -> Iterator[Session]:
        with Session(engine) as db:
            yield db

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = get_test_db
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.delete(f"/api/positions/{position_id}")
        assert response.status_code == 200
        assert response.json() == {"success": True}
        assert client.delete(f"/api/positions/{position_id}").status_code == 404

    with Session(engine) as db:
        assert db.get(Position, position_id) is None
        assert db.query(TMonitorState).count() == 0
        assert db.query(TSignalEvent).count() == 0
        assert db.query(Account).count() == 1
        assert db.query(Stock).count() == 1
    engine.dispose()
