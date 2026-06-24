"""底仓 VWAP 做 T 盯盘 API。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.core.t_monitor_engine import ENGINE
from src.web.database import get_db
from src.web.models import Position, Stock, TMonitorState, TSignalEvent

router = APIRouter()


def _state_dict(row: TMonitorState, position: Position, stock: Stock) -> dict:
    return {
        "id": row.id,
        "position_id": row.position_id,
        "trade_date": row.trade_date,
        "state": row.state,
        "cycle_count": row.cycle_count,
        "score": row.score,
        "recommended_quantity": row.recommended_quantity,
        "entry_price": row.entry_price,
        "current_price": row.current_price,
        "vwap": row.vwap,
        "support_price": row.support_price,
        "stop_loss_price": row.stop_loss_price,
        "target_price": row.target_price,
        "signal_expires_at": row.signal_expires_at,
        "context": row.context or {},
        "stock_symbol": stock.symbol,
        "stock_name": stock.name,
        "sellable_quantity": position.sellable_quantity,
        "updated_at": row.updated_at,
    }


@router.get("/states")
def list_states(db: Session = Depends(get_db)) -> list[dict]:
    rows = (
        db.query(TMonitorState, Position, Stock)
        .join(Position, TMonitorState.position_id == Position.id)
        .join(Stock, Position.stock_id == Stock.id)
        .order_by(TMonitorState.trade_date.desc(), TMonitorState.score.desc())
        .limit(200)
        .all()
    )
    latest: dict[int, dict] = {}
    for state, position, stock in rows:
        latest.setdefault(position.id, _state_dict(state, position, stock))
    return list(latest.values())


@router.get("/events")
def list_events(position_id: int | None = None, limit: int = 50, db: Session = Depends(get_db)) -> list[dict]:
    query = db.query(TSignalEvent)
    if position_id is not None:
        query = query.filter(TSignalEvent.position_id == position_id)
    rows = query.order_by(TSignalEvent.created_at.desc()).limit(min(max(limit, 1), 200)).all()
    return [
        {
            "id": row.id,
            "position_id": row.position_id,
            "signal_id": row.signal_id,
            "trade_date": row.trade_date,
            "action": row.action,
            "score": row.score,
            "current_price": row.current_price,
            "vwap": row.vwap,
            "support_price": row.support_price,
            "stop_loss_price": row.stop_loss_price,
            "target_price": row.target_price,
            "recommended_quantity": row.recommended_quantity,
            "reason": row.reason,
            "notify_success": row.notify_success,
            "notify_error": row.notify_error,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/scan")
async def scan(position_id: int | None = None) -> dict:
    return await ENGINE.scan_once(position_id=position_id, bypass_market_hours=True)


@router.post("/states/{state_id}/manual")
async def manual_action(state_id: int, action: str) -> dict:
    """手动驱动状态机:mark_long_open / mark_short_open / mark_done / reset。"""
    result = await ENGINE.manual_action(state_id, action)
    if not result.get("success"):
        raise HTTPException(400, result.get("error") or "操作失败")
    return result


class ExecuteLegRequest(BaseModel):
    action: str  # long_open / short_open / long_close / short_close
    price: float
    quantity: int = 0


@router.post("/states/{state_id}/execute")
async def execute_leg(state_id: int, payload: ExecuteLegRequest) -> dict:
    """记录一腿实际成交(价+量);平仓时摊低持仓成本。"""
    result = await ENGINE.execute_leg(state_id, payload.action, payload.price, payload.quantity)
    if not result.get("success"):
        raise HTTPException(400, result.get("error") or "操作失败")
    return result


@router.post("/states/{state_id}/confirm-buy")
def confirm_buy(state_id: int, db: Session = Depends(get_db)) -> dict:
    state = db.query(TMonitorState).filter(TMonitorState.id == state_id).first()
    if not state:
        raise HTTPException(404, "做T状态不存在")
    from src.core.t_monitor_engine import _now

    if state.state == "buy_t_notified":
        # 正T:确认低吸买入,进入等待卖出
        if state.signal_expires_at and state.signal_expires_at < _now():
            state.state = "invalidated"
            db.commit()
            raise HTTPException(400, "低吸信号已过期，请等待下一次有效信号")
        state.state = "waiting_exit"
        db.commit()
        return {"success": True, "state": state.state}
    if state.state == "buy_back_notified":
        # 倒T:确认买回平仓,本轮完成
        state.state = "completed"
        state.cycle_count += 1
        db.commit()
        return {"success": True, "state": state.state, "cycle_count": state.cycle_count}
    raise HTTPException(400, "当前状态不能确认买入")


@router.post("/states/{state_id}/confirm-sell")
def confirm_sell(state_id: int, db: Session = Depends(get_db)) -> dict:
    state = db.query(TMonitorState).filter(TMonitorState.id == state_id).first()
    if not state:
        raise HTTPException(404, "做T状态不存在")
    from src.core.t_monitor_engine import _now

    if state.state == "sell_t_notified":
        # 正T:确认止盈卖出,本轮完成
        state.state = "completed"
        state.cycle_count += 1
        db.commit()
        return {"success": True, "state": state.state, "cycle_count": state.cycle_count}
    if state.state == "sell_open_notified":
        # 倒T:确认高抛开仓,进入等待买回
        if state.signal_expires_at and state.signal_expires_at < _now():
            state.state = "invalidated"
            db.commit()
            raise HTTPException(400, "高抛信号已过期，请等待下一次有效信号")
        state.state = "waiting_buyback"
        db.commit()
        return {"success": True, "state": state.state}
    raise HTTPException(400, "当前状态不能确认卖出")
