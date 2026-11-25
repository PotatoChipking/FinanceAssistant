"""突破有效性策略 API:手动扫描、单股诊断、信号查询。"""

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.breakout_validity_scanner import STRATEGY_CODE, monitor_positions, scan_market
from src.core.kline_service import fetch_klines_sync
from src.core.signals.breakout_validity import compute_breakout_validity
from src.core.task_manager import task_manager, task_to_dict
from src.web.database import get_db
from src.web.models import StrategySignalRun

router = APIRouter()


@router.post("/scan")
async def trigger_scan(limit: int | None = None) -> dict:
    """手动触发全市场扫描(limit 仅扫前N只,用于验证)。耗时数分钟。"""
    return await asyncio.to_thread(scan_market, limit=limit)


@router.post("/b-scan")
def trigger_b_preview() -> dict:
    """Run the B observation scan without creating or expiring paper signals."""
    task = task_manager.submit(
        type="breakout_b_preview",
        name="B 类全市场预览",
        dedupe_key="breakout_b_preview",
        fn=lambda handle: scan_market(preview=True, on_progress=handle.set_progress),
    )
    return task_to_dict(task)


@router.post("/monitor")
async def trigger_monitor() -> dict:
    """手动触发持仓失效复评。"""
    return await asyncio.to_thread(monitor_positions)


@router.get("/diagnose/{symbol}")
async def diagnose(symbol: str, anchor_d0: str | None = None) -> dict:
    """单股诊断:输出完整状态判定(可传 anchor_d0 锚定历史事件)。"""
    klines = await asyncio.to_thread(
        fetch_klines_sync, symbol, "CN", days=200, interval="1d", cache_ttl_sec=300
    )
    result = compute_breakout_validity(klines, anchor_d0_date=anchor_d0)
    return result.to_dict()


@router.get("/signals")
def list_signals(days: int = 7, db: Session = Depends(get_db)) -> list[dict]:
    """最近的突破有效性信号。"""
    rows = (
        db.query(StrategySignalRun)
        .filter(StrategySignalRun.strategy_code == STRATEGY_CODE)
        .order_by(StrategySignalRun.snapshot_date.desc(), StrategySignalRun.rank_score.desc())
        .limit(max(1, min(days, 30)) * 50)
        .all()
    )
    return [
        {
            "id": r.id,
            "snapshot_date": r.snapshot_date,
            "symbol": r.stock_symbol,
            "name": r.stock_name,
            "action": r.action,
            "status": r.status,
            "score": r.score,
            "reason": r.reason,
            "entry_low": r.entry_low,
            "entry_high": r.entry_high,
            "stop_loss": r.stop_loss,
            "target_price": r.target_price,
            "payload": r.payload or {},
        }
        for r in rows
    ]


@router.get("/b-watchlist")
def b_watchlist(db: Session = Depends(get_db)) -> dict:
    """Read the latest active market-scan snapshot without applying a strength filter."""
    active = db.query(StrategySignalRun).filter(
        StrategySignalRun.strategy_code == STRATEGY_CODE,
        StrategySignalRun.stock_market == "CN",
        StrategySignalRun.source_pool == "market_scan",
        StrategySignalRun.status == "active",
        StrategySignalRun.action == "buy",
    )
    snapshot = active.with_entities(func.max(StrategySignalRun.snapshot_date)).scalar()
    items = []
    if snapshot:
        rows = active.filter(StrategySignalRun.snapshot_date == snapshot).order_by(
            StrategySignalRun.rank_score.desc(), StrategySignalRun.stock_symbol
        )
        for row in rows:
            payload = row.payload if isinstance(row.payload, dict) else {}
            paths = payload.get("paths")
            if not isinstance(paths, dict) or paths.get("B") is not True:
                continue
            strength = payload.get("strength")
            items.append({
                "id": row.id,
                "symbol": row.stock_symbol,
                "name": row.stock_name,
                "breakout_date": payload.get("d0_date"),
                "breakout_level": payload.get("g0"),
                "reason": row.reason,
                "strength": strength if isinstance(strength, dict) else None,
            })
    items.sort(key=lambda item: not ((item["strength"] or {}).get("strong") is True))
    return {"snapshot_date": snapshot, "items": items}
