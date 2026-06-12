"""Global trade rule configuration.

The defaults mirror the legacy hard-coded scoring and risk controls so an
upgrade does not change recommendations until the user saves custom rules.
"""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from typing import Any

from src.web.database import SessionLocal
from src.web.models import AppSettings


TRADE_RULES_SETTING_KEY = "trade_rules"
TRADE_RULES_VERSION = "v1"


DEFAULT_TRADE_RULES: dict[str, Any] = {
    "version": TRADE_RULES_VERSION,
    "technical": {
        "indicators": {
            "trend": {"enabled": True, "bullish": 2, "bearish": -2},
            "macd": {
                "enabled": True,
                "golden": 2,
                "dead": -2,
                "hist_positive": 1,
                "hist_negative": -1,
            },
            "rsi": {
                "enabled": True,
                "oversold": 1,
                "strong": 1,
                "overbought": -1,
                "weak": -1,
            },
            "kdj": {"enabled": True, "golden": 1, "dead": -1},
            "boll": {"enabled": True, "break_upper": 1, "break_lower": -1},
            "volume": {"enabled": True, "high": 1, "low": -1},
            "support_resistance": {
                "enabled": True,
                "near_support": 1,
                "near_resistance": -1,
                "support_pct": 0.02,
                "resistance_pct": 0.02,
            },
        },
        "thresholds": {
            "unheld_buy": 3,
            "unheld_avoid": -2,
            "held_add": 3,
            "held_hold": 1,
            "held_reduce": -1,
            "held_sell": -3,
        },
    },
    "opportunity": {
        "action_base_scores": {
            "buy": 78,
            "add": 72,
            "hold": 58,
            "watch": 52,
            "alert": 45,
            "reduce": 30,
            "sell": 20,
            "avoid": 15,
        },
        "suggestion": {
            "quote_moderate_abs_min": 0.5,
            "quote_moderate_abs_max": 6,
            "quote_moderate_score": 2,
            "quote_extreme_abs_min": 10,
            "quote_extreme_score": -3,
            "trend_bullish": 8,
            "trend_bearish": -8,
            "macd_golden": 5,
            "macd_dead": -5,
            "rsi_weak_or_oversold": 2,
            "rsi_overbought": -3,
            "kdj_golden": 3,
            "kdj_dead": -3,
            "volume_high_ratio": 2,
            "volume_high_score": 4,
            "volume_mid_ratio": 1.3,
            "volume_mid_score": 2,
            "context_quality_base": 60,
            "context_quality_divisor": 10,
            "context_quality_min_bonus": -3,
            "context_quality_max_bonus": 5,
            "fresh_hours": 6,
            "fresh_score": 3,
            "stale_hours": 48,
            "stale_score": -3,
        },
        "market_scan": {
            "trend_bullish": 2,
            "trend_bearish": -2,
            "macd_golden": 2,
            "macd_dead": -2,
            "volume_high_ratio": 1.8,
            "volume_high_score": 1,
            "volume_low_ratio": 0.7,
            "volume_low_score": -1,
            "momentum_min_pct": 1.5,
            "momentum_max_pct": 8.5,
            "momentum_score": 1,
            "overheat_pct": 10.5,
            "overheat_score": -2,
            "rebound_pct": -5.5,
            "rebound_score": 1,
            "support_proximity_pct": 0.03,
            "support_score": 1,
            "buy_points": 4,
            "add_points": 3,
            "avoid_points": -3,
            "tag_bonus_per_tag": 2,
            "tag_bonus_max": 8,
            "quote_momentum_min_pct": 1,
            "quote_momentum_max_pct": 7,
            "quote_momentum_score": 2,
            "quote_overheat_pct": 10,
            "quote_overheat_score": -3,
            "turnover_high": 3_000_000_000,
            "turnover_high_score": 3,
            "turnover_mid": 1_000_000_000,
            "turnover_mid_score": 1,
            "score_trend_bullish": 5,
            "score_trend_bearish": -6,
        },
        "active": {
            "watchlist_min_score": 55,
            "market_scan_min_score": 62,
            "plan_quality_min": 90,
        },
    },
    "risk": {
        "entry_band_pct": 0.01,
        "buy_stop_support_discount": 0.985,
        "buy_fallback_stop_price_pct": 0.95,
        "hold_stop_support_discount": 0.98,
        "hold_fallback_stop_price_pct": 0.94,
        "target_resistance_discount": 0.99,
        "buy_fallback_target_price_pct": 1.06,
        "hold_fallback_target_price_pct": 1.04,
        "paper_fallback_stop_loss_pct": 0.08,
        "paper_fallback_target_profit_pct": 0.15,
    },
}


def _deep_merge(default: Any, override: Any) -> Any:
    if isinstance(default, dict):
        out = copy.deepcopy(default)
        if isinstance(override, dict):
            for key, value in override.items():
                if key in out:
                    out[key] = _deep_merge(out[key], value)
        return out
    if isinstance(default, bool):
        return bool(override) if isinstance(override, bool) else default
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        try:
            return float(override)
        except Exception:
            return default
    if isinstance(default, str):
        return str(override) if override is not None else default
    return copy.deepcopy(default)


def _clamp_numbers(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _clamp_numbers(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_clamp_numbers(v) for v in node]
    if isinstance(node, bool):
        return node
    if isinstance(node, (int, float)):
        if node != node:  # NaN
            return 0
        return max(-1_000_000.0, min(1_000_000.0, float(node)))
    return node


def _validate_override(default: Any, override: Any, path: str = "") -> None:
    label = path or "rules"
    if isinstance(default, dict):
        if not isinstance(override, dict):
            raise ValueError(f"{label} 必须是对象")
        unknown = sorted(set(override.keys()) - set(default.keys()))
        if unknown:
            raise ValueError(f"{label} 包含未知字段: {', '.join(unknown[:5])}")
        for key, value in override.items():
            _validate_override(default[key], value, f"{path}.{key}" if path else key)
        return
    if isinstance(default, bool):
        if not isinstance(override, bool):
            raise ValueError(f"{label} 必须是布尔值")
        return
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        if isinstance(override, bool) or not isinstance(override, (int, float)):
            raise ValueError(f"{label} 必须是数字")
        if not math.isfinite(float(override)):
            raise ValueError(f"{label} 必须是有限数字")
        return
    if isinstance(default, str):
        if not isinstance(override, str):
            raise ValueError(f"{label} 必须是字符串")


def normalize_trade_rules(payload: dict | None) -> dict:
    rules = _deep_merge(DEFAULT_TRADE_RULES, payload or {})
    rules["version"] = TRADE_RULES_VERSION
    return _clamp_numbers(rules)


def _parse_rules(raw: str | None) -> dict:
    if not raw:
        return normalize_trade_rules(None)
    try:
        data = json.loads(raw)
    except Exception:
        return normalize_trade_rules(None)
    return normalize_trade_rules(data if isinstance(data, dict) else None)


def get_trade_rules(db=None) -> dict:
    if db is not None:
        row = (
            db.query(AppSettings)
            .filter(AppSettings.key == TRADE_RULES_SETTING_KEY)
            .first()
        )
        return _parse_rules(row.value if row else "")

    session = SessionLocal()
    try:
        return get_trade_rules(session)
    finally:
        session.close()


def get_default_trade_rules() -> dict:
    return normalize_trade_rules(None)


def save_trade_rules(payload: dict, db=None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("rules 必须是对象")
    _validate_override(DEFAULT_TRADE_RULES, payload)
    rules = normalize_trade_rules(payload)
    raw = json.dumps(rules, ensure_ascii=False, sort_keys=True)

    def _save(session) -> dict:
        row = (
            session.query(AppSettings)
            .filter(AppSettings.key == TRADE_RULES_SETTING_KEY)
            .first()
        )
        desc = f"交易规则配置（{datetime.now(timezone.utc).isoformat(timespec='seconds')}）"
        if row:
            row.value = raw
            row.description = desc
        else:
            session.add(
                AppSettings(
                    key=TRADE_RULES_SETTING_KEY,
                    value=raw,
                    description=desc,
                )
            )
        session.commit()
        return rules

    if db is not None:
        return _save(db)

    session = SessionLocal()
    try:
        return _save(session)
    finally:
        session.close()


def reset_trade_rules(db=None) -> dict:
    rules = get_default_trade_rules()

    def _reset(session) -> dict:
        row = (
            session.query(AppSettings)
            .filter(AppSettings.key == TRADE_RULES_SETTING_KEY)
            .first()
        )
        if row:
            session.delete(row)
            session.commit()
        return rules

    if db is not None:
        return _reset(db)

    session = SessionLocal()
    try:
        return _reset(session)
    finally:
        session.close()
