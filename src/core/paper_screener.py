"""Run selected screener formulas and publish their fresh paper-trading signals."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, time as daytime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.config import Settings
from src.core.screener.providers import normalize_universe_config
from src.core.strategy_pool import get_paper_strategy_selection, register_screener_strategy
from src.core.trade_rules import get_trade_rules
from src.web.database import SessionLocal
from src.web.models import (
    AppSettings,
    PaperTradingAccount,
    StockScreenerFormula,
    StockScreenerResult,
    StockScreenerRun,
    StrategyCatalog,
    StrategySignalRun,
)

logger = logging.getLogger(__name__)


def _local_now() -> datetime:
    try:
        zone = ZoneInfo(Settings().app_timezone or "Asia/Shanghai")
    except Exception:
        zone = ZoneInfo("Asia/Shanghai")
    return datetime.now(zone)


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _rule_float(rules: dict | None, path: str, default: float) -> float:
    value: Any = rules or {}
    try:
        for part in path.split("."):
            value = value[part]
        return float(value)
    except (KeyError, TypeError, ValueError):
        return default


def _published_key(formula_id: int) -> str:
    return f"paper_screener_published_run_{formula_id}"


def selected_paper_formulas(db: Session) -> list[StockScreenerFormula]:
    """Only formulas explicitly selected in custom mode are run automatically."""
    account = db.query(PaperTradingAccount).first()
    if account is not None and not account.enabled:
        return []
    selection = get_paper_strategy_selection(db)
    if selection["mode"] != "custom":
        return []
    ids = {
        int(code.removeprefix("screener:"))
        for code in selection["strategy_codes"]
        if code.startswith("screener:") and code.removeprefix("screener:").isdigit()
    }
    if not ids:
        return []
    disabled_codes = {
        row.code
        for row in db.query(StrategyCatalog)
        .filter(StrategyCatalog.code.in_([f"screener:{id_}" for id_ in ids]))
        .all()
        if not row.enabled
    }
    return [
        row
        for row in db.query(StockScreenerFormula)
        .filter(StockScreenerFormula.id.in_(ids), StockScreenerFormula.enabled.is_(True))
        .order_by(StockScreenerFormula.id.asc())
        .all()
        if f"screener:{row.id}" not in disabled_codes
    ]


def publish_screener_run(
    db: Session,
    run: StockScreenerRun,
    *,
    max_results: int = 20,
    min_change_pct: float | None = None,
) -> dict:
    """Replace a formula's active buy signals with one successful, recent run."""
    if run.status != "success":
        raise ValueError("只能使用成功完成的选股结果生成模拟盘信号")
    finished = _as_utc(run.finished_at)
    if finished is None or datetime.now(timezone.utc) - finished > timedelta(hours=36):
        raise ValueError("选股结果已过期，请重新运行选股后生成模拟盘信号")
    if not run.formula_id:
        raise ValueError("请先保存选股公式，再用于模拟盘")
    if run.formula is None or run.formula.formula != run.formula_snapshot:
        raise ValueError("公式已修改，请重新运行最新公式后再生成模拟盘信号")
    if normalize_universe_config(run.formula.universe_config) != normalize_universe_config(run.universe_config):
        raise ValueError("股票池配置已修改，请保存后重新运行公式")

    marker_key = _published_key(int(run.formula_id))
    marker = db.query(AppSettings).filter(AppSettings.key == marker_key).first()
    strategy_code = f"screener:{run.formula_id}"
    publication_id = json.dumps({
        "run_id": run.id,
        "max_results": max_results,
        "min_change_pct": min_change_pct,
    }, sort_keys=True)
    if marker and marker.value == publication_id:
        return {
            "run_id": run.id,
            "strategy_code": strategy_code,
            "strategy_name": f"选股策略: {run.formula.name}" if run.formula else strategy_code,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "matched": int(run.matched_count or 0),
            "already_published": True,
        }

    strategy = register_screener_strategy(int(run.formula_id), run_config={"max_results": max_results})
    strategy_name = strategy["name"]
    query = db.query(StockScreenerResult).filter(
        StockScreenerResult.run_id == run.id,
        StockScreenerResult.matched.is_(True),
    )
    if min_change_pct is not None:
        query = query.filter(StockScreenerResult.change_pct >= float(min_change_pct))
    results = query.order_by(
        StockScreenerResult.change_pct.desc(), StockScreenerResult.id.asc()
    ).limit(max_results).all()

    # A successful zero-match run must retire yesterday's signals as well.
    db.query(StrategySignalRun).filter(
        StrategySignalRun.strategy_code == strategy_code,
        StrategySignalRun.source_pool == "screener",
        StrategySignalRun.status == "active",
        StrategySignalRun.action == "buy",
    ).update({"status": "inactive"}, synchronize_session=False)

    rules = get_trade_rules(db)
    entry_band_pct = _rule_float(rules, "risk.entry_band_pct", 0.01)
    stop_loss_pct = _rule_float(rules, "risk.paper_fallback_stop_loss_pct", 0.08)
    target_profit_pct = _rule_float(rules, "risk.paper_fallback_target_profit_pct", 0.15)
    snapshot = _local_now().date().isoformat()
    created = updated = skipped = 0

    for index, item in enumerate(results):
        price = _number(item.last_close)
        if price is None or price <= 0:
            skipped += 1
            continue
        change = _number(item.change_pct) or 0.0
        score = max(45.0, min(95.0, 72.0 + change))
        indicators = item.indicators if isinstance(item.indicators, dict) else {}
        row = db.query(StrategySignalRun).filter(
            StrategySignalRun.snapshot_date == snapshot,
            StrategySignalRun.stock_symbol == item.symbol,
            StrategySignalRun.stock_market == item.market,
            StrategySignalRun.strategy_code == strategy_code,
            StrategySignalRun.source_pool == "screener",
        ).first()
        if row is None:
            row = StrategySignalRun(
                snapshot_date=snapshot,
                stock_symbol=item.symbol,
                stock_market=item.market,
                strategy_code=strategy_code,
                source_candidate_id=None,
            )
            db.add(row)
            created += 1
        else:
            updated += 1
        row.stock_name = item.name or item.symbol
        row.strategy_name = strategy_name
        row.strategy_version = "screener-v1"
        row.risk_level = "medium"
        row.source_pool = "screener"
        row.score = score
        row.rank_score = score + max(0.0, (len(results) - index) * 0.01)
        row.confidence = round(score / 100.0, 3)
        row.status = "active"
        row.action = "buy"
        row.action_label = "自定义策略建仓"
        row.signal = "选股公式命中"
        row.reason = item.reason or "Formula matched on the latest trading day"
        row.evidence = [f"选股公式命中: {strategy_name}", f"最近收盘价 {price:.2f}"]
        if item.board_name:
            row.evidence.append(f"来源板块: {item.board_name}")
        row.holding_days = 3
        row.entry_low = round(price * (1 - entry_band_pct), 4)
        row.entry_high = round(price * (1 + entry_band_pct), 4)
        row.stop_loss = round(price * (1 - stop_loss_pct), 4)
        row.target_price = round(price * (1 + target_profit_pct), 4)
        row.invalidation = "选股条件失效或触发模拟盘止损/止盈"
        row.plan_quality = 100
        row.source_agent = "screener"
        row.source_suggestion_id = None
        row.trace_id = f"screener-run:{run.id}"
        row.is_holding_snapshot = False
        row.context_quality_score = None
        row.payload = {
            "source": "screener_strategy",
            "screener_run_id": run.id,
            "screener_formula_id": run.formula_id,
            "formula_snapshot": run.formula_snapshot,
            "board_code": item.board_code or "",
            "board_name": item.board_name or "",
            "indicators": indicators,
            "change_pct": item.change_pct,
        }
        row.updated_at = datetime.now(timezone.utc)

    if marker is None:
        marker = AppSettings(key=marker_key, value=publication_id, description="模拟盘选股公式最近发布的运行")
        db.add(marker)
    else:
        marker.value = publication_id
    db.commit()
    return {
        "run_id": run.id,
        "strategy_code": strategy_code,
        "strategy_name": strategy_name,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "matched": int(run.matched_count or 0),
        "already_published": False,
    }


def run_selected_paper_formulas() -> dict:
    """Run each selected formula once per local day, then publish its result."""
    from src.web.api.screener import _run_screener_job

    now = _local_now()
    day_start = _utc_naive(datetime.combine(now.date(), daytime.min, now.tzinfo))
    day_end = _utc_naive(datetime.combine(now.date() + timedelta(days=1), daytime.min, now.tzinfo))
    db = SessionLocal()
    try:
        selected = [
            (row.id, row.name, row.formula, normalize_universe_config(row.universe_config or {}))
            for row in selected_paper_formulas(db)
        ]
    finally:
        db.close()

    summaries: list[dict] = []
    for formula_id, name, formula_text, universe_config in selected:
        db = SessionLocal()
        try:
            recent = (
                db.query(StockScreenerRun)
                .filter(
                    StockScreenerRun.formula_id == formula_id,
                    StockScreenerRun.formula_snapshot == formula_text,
                    StockScreenerRun.created_at >= day_start,
                    StockScreenerRun.created_at < day_end,
                )
                .order_by(StockScreenerRun.id.desc())
                .all()
            )
            existing = next(
                (
                    run for run in recent
                    if normalize_universe_config(run.universe_config or {}) == universe_config
                    and run.status in {"queued", "running", "success"}
                ),
                None,
            )
            if existing is None:
                run = StockScreenerRun(
                    formula_id=formula_id,
                    formula_snapshot=formula_text,
                    universe_config=universe_config,
                    status="queued",
                    created_at=datetime.now(timezone.utc),
                )
                db.add(run)
                db.commit()
                run_id = int(run.id)
                own_run = True
            else:
                run_id = int(existing.id)
                own_run = False
        finally:
            db.close()

        if own_run:
            try:
                _run_screener_job(run_id)
            except Exception as exc:
                logger.warning("[模拟盘选股] %s 运行失败: %s", name, exc)
        else:
            # A manual run may already be in progress. Give it time to finish.
            for _ in range(300):
                db = SessionLocal()
                try:
                    status = db.query(StockScreenerRun.status).filter(StockScreenerRun.id == run_id).scalar()
                finally:
                    db.close()
                if status not in {"queued", "running"}:
                    break
                time.sleep(2)

        db = SessionLocal()
        try:
            run = db.query(StockScreenerRun).filter(StockScreenerRun.id == run_id).first()
            if run is None or run.status != "success":
                summaries.append({"formula_id": formula_id, "name": name, "run_id": run_id, "status": run.status if run else "missing", "error": run.error if run else "运行记录不存在"})
                continue
            try:
                published = publish_screener_run(db, run)
                summaries.append({"formula_id": formula_id, "name": name, "status": "success", **published})
            except (KeyError, ValueError) as exc:
                summaries.append({"formula_id": formula_id, "name": name, "run_id": run_id, "status": "failed", "error": str(exc)})
        finally:
            db.close()

    return {"selected": len(selected), "runs": summaries}
