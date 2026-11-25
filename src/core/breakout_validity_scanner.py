"""突破有效性策略:A股主板全市场扫描 + 模拟盘持仓失效监控。

买点:状态=valid_active 当日写入 buy 信号(模拟盘按入场区间自动建仓);
卖点:止损价=max(G_SUPPORT,L0)、止盈价=G0×1.15 由模拟盘引擎盯价;
     动态失效(连续3日<G0/放量破位/跌停破位)由每日收盘后的持仓复评写 sell 信号强平。
扫描范围:沪深主板(60/000/001/002/003 开头),剔除创业板/科创板/北交所/ST/退市。
"""

from __future__ import annotations

import logging
import math
import time
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, time as clock_time, timedelta
from typing import Any, Callable

from sqlalchemy import or_

from src.core.breakout_strength import BreakoutStrength, rank_breakout_strength
from src.core.cn_symbol import MAIN_BOARD_PREFIXES
from src.core.kline_service import fetch_klines_sync
from src.core.signals.breakout_validity import (
    BREAKOUT_ENTRY_BAND,
    BREAKOUT_HOLDING_DAYS,
    BreakoutParams,
    BreakoutValidityResult,
    compute_breakout_validity,
)
from src.core.strong_b_paper import (
    CN_TZ, STRONG_B_CODE, STRONG_B_HOLDING_SESSIONS, STRONG_B_NAME, cn_sessions, holding_deadline,
)
from src.web.database import SessionLocal
from src.web.models import PaperTradingPosition, StrategySignalRun
from src.web.stock_list import get_stock_list

logger = logging.getLogger(__name__)

STRATEGY_CODE = "breakout_validity"
STRATEGY_NAME = "突破有效性"

KLINE_DAYS = 200          # 60日前高回看 + 均线/量能余量
SCAN_MAX_WORKERS = 8
KLINE_CACHE_TTL = 3600.0


@dataclass(frozen=True)
class ScanEvaluation:
    result: BreakoutValidityResult
    recent_closes: dict[str, float]
    first_b_date: str | None = None


def _cn_now() -> datetime:
    return datetime.now(CN_TZ)


def _today() -> str:
    return _cn_now().date().isoformat()


def _bar_date(bar: Any) -> str:
    return str(bar.get("date") if isinstance(bar, dict) else bar.date)[:10]


def build_universe() -> list[dict]:
    """沪深主板股票清单(剔除双创/北交所在前缀层面天然排除,再剔除 ST/退市)。"""
    out = []
    for s in get_stock_list():
        symbol = str(s.get("symbol") or "")
        name = str(s.get("name") or "")
        if s.get("market") not in (None, "CN", "cn"):
            continue
        if not symbol.startswith(MAIN_BOARD_PREFIXES):
            continue
        if "ST" in name.upper() or "退" in name:
            continue
        out.append({
            "symbol": symbol,
            "name": name,
            "industry": s.get("industry"),
            "industry_as_of": s.get("industry_as_of"),
        })
    return out


def _evaluate(symbol: str, params: BreakoutParams, *, as_of: str | None = None) -> ScanEvaluation | None:
    try:
        klines = fetch_klines_sync(
            symbol, "CN", days=KLINE_DAYS, interval="1d",
            cache_ttl_sec=300 if as_of is not None else KLINE_CACHE_TTL,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[突破有效性] %s 拉取K线失败: %s", symbol, exc)
        return None
    if as_of is not None:
        klines = [bar for bar in klines if _bar_date(bar) <= as_of]
    if not klines:
        return None
    try:
        result = compute_breakout_validity(klines, params=params)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[突破有效性] %s 计算异常: %s", symbol, exc)
        return None
    window = klines[-(params.max_observe + 30):]
    closes = {}
    for bar in window:
        # Provider responses are normally KlineData; dictionary bars also
        # pass through the existing evaluator.
        day, close, volume = (
            (bar.get("date"), bar.get("close"), bar.get("volume")) if isinstance(bar, dict)
            else (bar.date, bar.close, bar.volume)
        )
        try:
            price = float(close)
            if day and price > 0 and math.isfinite(price) and float(volume or 0) > 0:
                closes[str(day)[:10]] = price
        except (ValueError, TypeError):
            continue
    first_b_date = None
    if as_of is None and result.state == "valid_active" and result.paths.get("B") and result.d0_index >= 0:
        try:
            first_b_date = _bar_date(klines[-1])
            for index in range(result.d0_index + params.confirm_days, len(klines) - 1):
                previous = compute_breakout_validity(
                    klines[:index + 1], params=params, anchor_d0_date=result.d0_date,
                )
                if previous.state == "valid_active" and previous.paths.get("B"):
                    first_b_date = _bar_date(klines[index])
                    break
        except Exception as exc:
            first_b_date = None
            logger.warning("[强势 B] %s 无法确认首次触发日: %s", symbol, exc)
    return ScanEvaluation(result, closes, first_b_date)


def _signal_score(result: BreakoutValidityResult) -> float:
    hits = sum(1 for v in (result.paths or {}).values() if v)
    score = 70.0 + 5.0 * hits
    if result.extension is not None and result.extension <= 1.05:
        score += 5.0  # 离突破位近,空间占优(评分卡"位置与空间"简化)
    return min(score, 95.0)


def _upsert_signal(
    db, *, symbol: str, name: str, action: str, result: BreakoutValidityResult, snapshot: str,
    strength: BreakoutStrength | None = None,
    strategy_code: str = STRATEGY_CODE, paper_details: dict[str, Any] | None = None,
) -> None:
    row = (
        db.query(StrategySignalRun)
        .filter(
            StrategySignalRun.snapshot_date == snapshot,
            StrategySignalRun.stock_symbol == symbol,
            StrategySignalRun.stock_market == "CN",
            StrategySignalRun.strategy_code == strategy_code,
            StrategySignalRun.source_candidate_id.is_(None),
        )
        .first()
    )
    close = float(result.close or 0)
    strong_b = strategy_code == STRONG_B_CODE
    score = _signal_score(result) if action == "buy" else 0.0
    strength_payload = asdict(strength) if strength is not None else None
    if (strength_payload is None and action == "buy" and result.paths.get("B")
            and row and isinstance(row.payload, dict) and row.payload.get("d0_date") == result.d0_date):
        strength_payload = row.payload.get("strength")
    fields: dict[str, Any] = {
        "stock_name": name,
        "strategy_name": STRONG_B_NAME if strong_b else STRATEGY_NAME,
        "status": "active",
        "action": action,
        "action_label": "买入" if action == "buy" else "卖出",
        "signal": f"breakout_{result.state}",
        "reason": result.reason,
        "evidence": list(result.evidence or []),
        "score": score,
        "rank_score": score,
        "holding_days": STRONG_B_HOLDING_SESSIONS if strong_b else BREAKOUT_HOLDING_DAYS,
        "entry_low": round(close * BREAKOUT_ENTRY_BAND[0], 2) if action == "buy" else None,
        "entry_high": round(close * BREAKOUT_ENTRY_BAND[1], 2) if action == "buy" else None,
        "stop_loss": result.stop_loss if action == "buy" else None,
        "target_price": result.target_price if action == "buy" and not strong_b else None,
        "invalidation": f"收盘<max(G_SUPPORT,L0)={result.stop_loss} 或 连续3日<G0/放量破位/跌停破位",
        "source_pool": "market_scan",
        "payload": {
            "d0_date": result.d0_date,
            "g0": result.g0,
            "g_support": result.g_support,
            "l0": result.l0,
            "state": result.state,
            "paths": result.paths,
            "extension": result.extension,
            "fail_reason": result.fail_reason,
            "strength": strength_payload,
            **(paper_details or {}),
        },
    }
    if row:
        for key, value in fields.items():
            setattr(row, key, value)
    else:
        db.add(StrategySignalRun(
            snapshot_date=snapshot,
            stock_symbol=symbol,
            stock_market="CN",
            strategy_code=strategy_code,
            **fields,
        ))


def scan_market(
    *, limit: int | None = None, params: BreakoutParams | None = None,
    preview: bool = False, on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Scan the main board, with an optional read-only view of completed daily bars."""
    started = time.time()
    p = params or BreakoutParams()
    universe = build_universe()
    truncated = bool(limit and limit < len(universe))
    if limit:
        universe = universe[:limit]
    snapshot = _today()
    local_now = _cn_now()
    after_close = snapshot == local_now.date().isoformat() and local_now.time() >= clock_time(15)
    completed_through = (local_now.date() - timedelta(days=0 if after_close else 1)).isoformat()
    hits: list[tuple[dict, BreakoutValidityResult]] = []
    recent_closes: dict[str, dict[str, float]] = {}
    new_b_symbols = set()
    scanned = 0
    evaluated = 0
    if on_progress:
        on_progress(0, len(universe))
    with ThreadPoolExecutor(max_workers=SCAN_MAX_WORKERS) as pool:
        futures = {
            (pool.submit(_evaluate, s["symbol"], p, as_of=completed_through)
             if preview else pool.submit(_evaluate, s["symbol"], p)): s
            for s in universe
        }
        for future in as_completed(futures):
            stock = futures[future]
            scanned += 1
            if on_progress and (scanned % 25 == 0 or scanned == len(universe)):
                on_progress(scanned, len(universe))
            evaluation = future.result()
            if evaluation is None:
                continue
            evaluated += 1
            if preview or not truncated:
                recent_closes[stock["symbol"]] = evaluation.recent_closes
            if evaluation.result.state == "valid_active":
                hits.append((stock, evaluation.result))
                if evaluation.first_b_date == snapshot:
                    new_b_symbols.add(stock["symbol"])

    preview_date = None
    if preview:
        preview_date = max((max(closes) for closes in recent_closes.values() if closes), default=None)
        hits = [(stock, result) for stock, result in hits
                if preview_date in recent_closes.get(stock["symbol"], {})]
    candidate_dates = {stock["symbol"]: result.d0_date for stock, result in hits if result.paths.get("B")}
    strengths = rank_breakout_strength(
        universe, recent_closes, candidate_dates, snapshot_date=snapshot
    ) if not truncated else {}
    if preview:
        items = []
        for stock, result in hits:
            if not result.paths.get("B"):
                continue
            strength = strengths.get(stock["symbol"])
            items.append({
                "symbol": stock["symbol"],
                "name": stock["name"],
                "breakout_date": result.d0_date,
                "breakout_level": result.g0,
                "reason": result.reason,
                "strength": asdict(strength) if strength is not None else None,
            })
        items.sort(key=lambda item: (not (item["strength"] or {}).get("strong"), item["symbol"]))
        return {
            "snapshot_date": preview_date,
            "items": items,
            "scanned": scanned,
            "evaluated": evaluated,
            "status": "data_unavailable" if not preview_date else ("partial" if evaluated < scanned else "ok"),
        }

    qualified = {symbol for symbol in new_b_symbols if symbol in strengths and strengths[symbol].strong}
    paper_schedule: dict[str, Any] = {}
    if after_close and qualified:
        try:
            sessions = cn_sessions(local_now.date())
            next_index = bisect_right(sessions, snapshot)
            if snapshot in sessions and next_index < len(sessions):
                entry_date = sessions[next_index]
                paper_schedule = {"paper_entry_date": entry_date,
                                  "paper_exit_date": holding_deadline(sessions, entry_date)}
        except Exception as exc:
            logger.warning("[强势 B] 无法确定下一个交易日，暂缓生成模拟信号: %s", exc)

    db = SessionLocal()
    paper_signals = 0
    try:
        # 旧的 buy 信号过期,避免模拟盘按过时价格区间建仓
        db.query(StrategySignalRun).filter(
            StrategySignalRun.strategy_code == STRATEGY_CODE,
            StrategySignalRun.status == "active",
            StrategySignalRun.action == "buy",
            StrategySignalRun.snapshot_date < snapshot,
        ).update({"status": "inactive"}, synchronize_session=False)
        open_strong_b = set()
        if after_close and not truncated and evaluated:
            expired = StrategySignalRun.payload["paper_entry_date"].as_string() <= snapshot
            if evaluated == len(universe) and (not qualified or paper_schedule):
                expired = or_(expired, StrategySignalRun.snapshot_date == snapshot)
            db.query(StrategySignalRun).filter(
                StrategySignalRun.strategy_code == STRONG_B_CODE,
                StrategySignalRun.status == "active",
                StrategySignalRun.action == "buy",
                expired,
            ).update({"status": "inactive"}, synchronize_session=False)
            if paper_schedule:
                open_strong_b = {
                    symbol for (symbol,) in db.query(PaperTradingPosition.stock_symbol).filter(
                        PaperTradingPosition.strategy_code == STRONG_B_CODE,
                        PaperTradingPosition.status == "open",
                    )
                }
        for stock, result in hits:
            symbol = stock["symbol"]
            _upsert_signal(
                db, symbol=symbol, name=stock["name"], action="buy", result=result,
                snapshot=snapshot, strength=strengths.get(symbol),
            )
            if paper_schedule and symbol in qualified and symbol not in open_strong_b:
                _upsert_signal(
                    db, symbol=symbol, name=stock["name"], action="buy", result=result, snapshot=snapshot,
                    strength=strengths[symbol], strategy_code=STRONG_B_CODE,
                    paper_details={**paper_schedule, "entry_ceiling": result.target_price},
                )
                paper_signals += 1
        db.commit()
    finally:
        db.close()

    elapsed = round(time.time() - started, 1)
    summary = {
        "status": "data_unavailable" if not universe or not evaluated else ("partial" if evaluated < scanned else "ok"),
        "scanned": scanned,
        "evaluated": evaluated,
        "data_unavailable": scanned - evaluated,
        "universe": len(universe),
        "valid_active": len(hits),
        "symbols": [s["symbol"] for s, _ in hits],
        "b_candidates": len(candidate_dates),
        "b_strength_evaluated": len(strengths),
        "strong_b": sum(item.strong for item in strengths.values()),
        "strong_b_paper_signals": paper_signals,
        "elapsed_sec": elapsed,
    }
    if summary["status"] == "data_unavailable":
        logger.warning("[突破有效性] 股票池或 K 线不可用: %s", summary)
    else:
        logger.info("[突破有效性] 扫描完成: %s", summary)
    return summary


def monitor_positions(*, params: BreakoutParams | None = None) -> dict:
    """模拟盘持仓复评:锚点冻结重放,failed/invalidated 写 sell 信号触发强平。"""
    p = params or BreakoutParams()
    snapshot = _today()
    local_now = _cn_now()
    db = SessionLocal()
    try:
        positions = (
            db.query(PaperTradingPosition)
            .filter(
                PaperTradingPosition.status == "open",
                PaperTradingPosition.strategy_code.in_((STRATEGY_CODE, STRONG_B_CODE)),
            )
            .all()
        )
        checked, closed_signals = 0, []
        for pos in positions:
            strong_b = pos.strategy_code == STRONG_B_CODE
            if strong_b and local_now.time() < clock_time(15):
                continue
            anchor = None
            signal_payload = {}
            if pos.signal_run_id:
                sig = db.get(StrategySignalRun, pos.signal_run_id)
                if sig and isinstance(sig.payload, dict):
                    signal_payload = sig.payload
                    anchor = signal_payload.get("d0_date")
                    entry_date = signal_payload.get("paper_entry_date")
                    if strong_b and entry_date and not signal_payload.get("paper_exit_date"):
                        try:
                            deadline = holding_deadline(cn_sessions(local_now.date()), entry_date)
                            if deadline:
                                sig.payload = {**signal_payload, "paper_exit_date": deadline}
                        except Exception as exc:
                            logger.warning("[强势 B] %s 的到期日暂无法补齐: %s", pos.stock_symbol, exc)
            if strong_b and not anchor:
                continue
            try:
                klines = fetch_klines_sync(pos.stock_symbol, "CN", days=KLINE_DAYS, interval="1d", cache_ttl_sec=300)
                if strong_b and (not klines or _bar_date(klines[-1]) != snapshot):
                    continue
                result = compute_breakout_validity(klines, params=p, anchor_d0_date=anchor)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[突破有效性] 持仓 %s 复评失败: %s", pos.stock_symbol, exc)
                continue
            checked += 1
            if result.state in ("failed", "invalidated"):
                _upsert_signal(
                    db, symbol=pos.stock_symbol, name=pos.stock_name or "", action="sell",
                    result=result, snapshot=snapshot, strategy_code=pos.strategy_code,
                    paper_details={"paper_position_id": pos.id} if strong_b else None,
                )
                closed_signals.append(f"{pos.stock_symbol}:{result.fail_reason}")
        db.commit()
        summary = {"positions": len(positions), "checked": checked, "sell_signals": closed_signals}
        if closed_signals:
            logger.info("[突破有效性] 持仓失效强平信号: %s", summary)
        return summary
    finally:
        db.close()


def run_daily() -> dict:
    """收盘后例行:先复评持仓失效,再扫全市场新信号。"""
    monitor = monitor_positions()
    scan = scan_market()
    return {"monitor": monitor, "scan": scan}
