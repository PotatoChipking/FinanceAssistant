"""Backtest breakout confirmation paths using historical A-share daily bars."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import asdict, dataclass, fields
from datetime import date
from pathlib import Path
from statistics import mean, median

from src.core.cn_symbol import MAIN_BOARD_PREFIXES
from src.core.signals.breakout_validity import (
    BREAKOUT_ENTRY_BAND,
    BREAKOUT_HOLDING_DAYS,
    BreakoutParams,
    BreakoutValidityResult,
    compute_breakout_validity,
)


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    eligible_for_entry: bool = True


@dataclass(frozen=True)
class Execution:
    commission_bps: float = 3.0
    sell_tax_bps: float = 5.0
    slippage_bps: float = 10.0
    holding_days: int = BREAKOUT_HOLDING_DAYS

    def buy_cost(self, price: float) -> float:
        return price * (1 + self.commission_bps / 10_000)

    def sell_proceeds(self, price: float) -> float:
        return price * (1 - (self.commission_bps + self.sell_tax_bps) / 10_000)

    def entry_reward_risk(self, entry_price: float, stop: float, target: float) -> float | None:
        """Compare the planned net gain and loss per share at the quoted exit levels."""
        cost = self.buy_cost(entry_price)
        sale_slippage = 1 - self.slippage_bps / 10_000
        risk = cost - self.sell_proceeds(stop * sale_slippage)
        if risk <= 0:
            return None
        return (self.sell_proceeds(target * sale_slippage) - cost) / risk


@dataclass(frozen=True)
class Trade:
    status: str
    signal_date: str = ""
    entry_date: str = ""
    entry_price: float | None = None
    exit_date: str = ""
    exit_price: float | None = None
    exit_reason: str = ""
    net_return_pct: float | None = None
    worst_observed_move_pct: float | None = None


@dataclass(frozen=True)
class Event:
    symbol: str
    breakout_date: str
    breakout_level: float
    support: float
    stop_loss: float
    target_price: float
    observation_complete: bool
    first_paths: str
    path_c_paths: str
    first: Trade
    path_c: Trade


@dataclass(frozen=True)
class PathTrade:
    symbol: str
    breakout_date: str
    breakout_level: float
    path: str
    confirmed_paths: str
    trade: Trade


def _evaluate(
    bars: list[Bar],
    d0: int,
    day: int,
    warmup: int,
    params: BreakoutParams,
) -> BreakoutValidityResult:
    return compute_breakout_validity(
        bars[d0 - warmup:day + 1],
        params=params,
        anchor_d0_date=bars[d0].date,
    )


def _observation(
    bars: list[Bar],
    d0: int,
    sessions: dict[str, int],
    warmup: int,
    params: BreakoutParams,
    *,
    stop_at_c: bool = True,
) -> tuple[list[tuple[int, BreakoutValidityResult]], bool]:
    confirmations: list[tuple[int, BreakoutValidityResult]] = []
    for offset in range(1, params.max_observe + 1):
        day = d0 + offset
        if day >= len(bars) or sessions[bars[day].date] - sessions[bars[d0].date] != offset:
            return confirmations, False
        if not bars[day].eligible_for_entry:
            break
        if not bars[day].volume:
            return confirmations, False
        result = _evaluate(bars, d0, day, warmup, params)
        if result.state in ("failed", "invalidated"):
            break
        if result.state == "valid_active":
            confirmations.append((day, result))
            if stop_at_c and result.paths.get("C"):
                break
    return confirmations, True


def _can_trade(bar: Bar, previous_close: float, *, buy: bool) -> bool:
    if bar.volume is None or bar.volume <= 0:
        return False
    if bar.high != bar.low:
        return True
    return bar.open <= previous_close if buy else bar.open >= previous_close


def _simulate(
    bars: list[Bar],
    d0: int,
    confirmation: tuple[int, BreakoutValidityResult] | None,
    observation_complete: bool,
    sessions: dict[str, int],
    warmup: int,
    params: BreakoutParams,
    execution: Execution,
    *,
    trend_exit: Callable[[list[Bar], int, int], str] | None = None,
    min_reward_risk: float | None = None,
) -> Trade:
    if confirmation is None:
        return Trade("no_signal" if observation_complete else "observation_incomplete")

    signal, result = confirmation
    signal_date = bars[signal].date
    entry_idx = signal + 1
    if entry_idx >= len(bars) or sessions[bars[entry_idx].date] - sessions[signal_date] != 1:
        return Trade("entry_bar_unavailable", signal_date)

    signal_close = bars[signal].close
    entry = bars[entry_idx]
    if not entry.eligible_for_entry:
        return Trade("entry_ineligible", signal_date)
    if not _can_trade(entry, signal_close, buy=True):
        return Trade("entry_untradeable", signal_date)

    lower, upper = (round(signal_close * ratio, 2) for ratio in BREAKOUT_ENTRY_BAND)
    if not lower <= entry.open <= upper:
        return Trade("outside_entry_band", signal_date)
    assert result.stop_loss is not None and result.target_price is not None
    if not result.stop_loss < entry.open < result.target_price:
        return Trade("outside_stop_target", signal_date)

    slippage = execution.slippage_bps / 10_000
    entry_price = entry.open * (1 + slippage)
    if min_reward_risk is not None:
        reward_risk = execution.entry_reward_risk(entry_price, result.stop_loss, result.target_price)
        if reward_risk is None or reward_risk < min_reward_risk:
            return Trade("below_min_reward_risk", signal_date)

    lowest_held_price = entry.low
    pending_exit = ""
    target_day = None
    if _evaluate(bars, d0, entry_idx, warmup, params).state in ("failed", "invalidated"):
        pending_exit = "structure_invalidated"

    for day in range(entry_idx + 1, len(bars)):
        bar, previous = bars[day], bars[day - 1]
        if sessions[bar.date] - sessions[previous.date] != 1:
            return Trade("exit_bar_missing", signal_date, entry.date, round(entry_price, 6))
        if not bar.volume:
            if not pending_exit and day - entry_idx >= execution.holding_days:
                pending_exit = "max_holding_days_delayed"
            continue

        can_sell = _can_trade(bar, previous.close, buy=False)
        exit_reason, exit_price = "", bar.open
        if can_sell:
            if pending_exit:
                exit_reason = pending_exit
            elif bar.open <= result.stop_loss:
                exit_reason = "stop_gap"
            elif trend_exit is None and bar.open >= result.target_price:
                exit_reason = "target_gap"
            elif bar.low <= result.stop_loss:
                exit_reason, exit_price = "stop_loss", result.stop_loss
            elif trend_exit is None and bar.high >= result.target_price:
                exit_reason, exit_price = "target_price", result.target_price
            elif day - entry_idx >= execution.holding_days:
                exit_reason, exit_price = "max_holding_days", bar.close
                lowest_held_price = min(lowest_held_price, bar.low)
        elif not pending_exit and bar.low <= result.stop_loss:
            pending_exit = "stop_delayed"

        if exit_reason:
            lowest_held_price = min(lowest_held_price, bar.open, exit_price)
            exit_price *= 1 - slippage
            buy_cost = execution.buy_cost(entry_price)
            sell_proceeds = execution.sell_proceeds(exit_price)
            return Trade(
                "closed", signal_date, entry.date, round(entry_price, 6),
                bar.date, round(exit_price, 6), exit_reason,
                round((sell_proceeds / buy_cost - 1) * 100, 6),
                round(min(0.0, lowest_held_price / entry_price - 1) * 100, 6),
            )

        lowest_held_price = min(lowest_held_price, bar.low)
        if not pending_exit:
            if _evaluate(bars, d0, day, warmup, params).state in ("failed", "invalidated"):
                pending_exit = "structure_invalidated"
            elif day - entry_idx >= execution.holding_days:
                pending_exit = "max_holding_days_delayed"
            elif trend_exit is not None:
                if target_day is None and can_sell and bar.high >= result.target_price:
                    target_day = day
                if target_day is not None:
                    pending_exit = trend_exit(bars, day, target_day)

    return Trade("unclosed_at_data_end", signal_date, entry.date, round(entry_price, 6))


def _breakout_windows(
    histories: dict[str, list[Bar]],
    calendar: dict[str, int],
    start: str,
    end: str,
    warmup: int,
    params: BreakoutParams,
) -> Iterator[tuple[str, list[Bar], int, BreakoutValidityResult]]:
    for symbol, history in histories.items():
        bars = [bar for bar in history if bar.date <= end]
        for d0 in range(warmup, len(bars)):
            if bars[d0].date < start or not bars[d0].volume or not bars[d0].eligible_for_entry:
                continue
            if calendar[bars[d0].date] - calendar[bars[d0 - warmup].date] != warmup:
                continue
            breakout = _evaluate(bars, d0, d0, warmup, params)
            if breakout.state == "observed":
                yield symbol, bars, d0, breakout


def run_study(
    histories: dict[str, list[Bar]],
    sessions: list[str],
    start: str,
    end: str,
    execution: Execution,
    params: BreakoutParams,
) -> list[Event]:
    """Use the same dated breakout events and execution assumptions for both groups."""
    calendar = {day: i for i, day in enumerate(sessions) if day <= end}
    events: list[Event] = []
    # 70 prior bars also lets the evaluator check MA60 ten sessions back.
    warmup = max(70, params.lookback, params.vbase_window)
    for symbol, bars, d0, breakout in _breakout_windows(
        histories, calendar, start, end, warmup, params,
    ):
        assert breakout.g0 is not None and breakout.g_support is not None
        assert breakout.stop_loss is not None and breakout.target_price is not None
        confirmations, complete = _observation(bars, d0, calendar, warmup, params)
        first = confirmations[0] if confirmations else None
        path_c = next((item for item in confirmations if item[1].paths.get("C")), None)
        events.append(Event(
            symbol, bars[d0].date, breakout.g0, breakout.g_support,
            breakout.stop_loss, breakout.target_price, complete,
            "+".join(key for key, hit in first[1].paths.items() if hit) if first else "",
            "+".join(key for key, hit in path_c[1].paths.items() if hit) if path_c else "",
            _simulate(bars, d0, first, complete, calendar, warmup, params, execution),
            _simulate(bars, d0, path_c, complete, calendar, warmup, params, execution),
        ))
    return sorted(events, key=lambda event: (event.breakout_date, event.symbol))


def run_path_study(
    histories: dict[str, list[Bar]],
    sessions: list[str],
    start: str,
    end: str,
    execution: Execution,
    params: BreakoutParams,
    *,
    min_reward_risk: float | None = None,
) -> list[PathTrade]:
    """Simulate the first qualifying signal of each path for each breakout."""
    calendar = {day: i for i, day in enumerate(sessions) if day <= end}
    warmup = max(70, params.lookback, params.vbase_window)
    trades: list[PathTrade] = []
    for symbol, bars, d0, breakout in _breakout_windows(
        histories, calendar, start, end, warmup, params,
    ):
        assert breakout.g0 is not None
        confirmations, complete = _observation(
            bars, d0, calendar, warmup, params, stop_at_c=False,
        )
        seen: set[str] = set()
        for day, result in confirmations:
            matched = {path for path, active in result.paths.items() if active}
            new_paths = matched - seen
            if not new_paths:
                continue
            trade = _simulate(
                bars, d0, (day, result), complete, calendar, warmup, params, execution,
                min_reward_risk=min_reward_risk,
            )
            confirmed_paths = "+".join(sorted(matched))
            trades.extend(
                PathTrade(symbol, bars[d0].date, breakout.g0, path, confirmed_paths, trade)
                for path in sorted(new_paths)
            )
            seen.update(new_paths)
    return sorted(trades, key=lambda item: (item.breakout_date, item.symbol, item.path))


def _trade_summary(trades: list[Trade]) -> dict:
    returns = [trade.net_return_pct for trade in trades if trade.net_return_pct is not None]
    adverse = [
        trade.worst_observed_move_pct
        for trade in trades if trade.worst_observed_move_pct is not None
    ]
    signaled = sum(bool(trade.signal_date) for trade in trades)
    entered = sum(bool(trade.entry_date) for trade in trades)
    return {
        "signals": signaled,
        "entries": entered,
        "closed_trades": len(returns),
        "entry_rate_pct": round(entered / signaled * 100, 4) if signaled else None,
        "win_rate_pct": round(sum(value > 0 for value in returns) / len(returns) * 100, 4)
        if returns else None,
        "mean_net_return_pct": round(mean(returns), 4) if returns else None,
        "median_net_return_pct": round(median(returns), 4) if returns else None,
        "mean_worst_observed_move_pct": round(mean(adverse), 4) if adverse else None,
        "statuses": dict(Counter(trade.status for trade in trades)),
        "exits": dict(Counter(trade.exit_reason for trade in trades if trade.exit_reason)),
    }


def summarize(events: list[Event]) -> dict:
    paired = []
    delayed_paired = []
    for event in events:
        first, path_c = event.first.net_return_pct, event.path_c.net_return_pct
        if first is not None and path_c is not None:
            paired.append(path_c - first)
            if event.path_c.signal_date > event.first.signal_date:
                delayed_paired.append(path_c - first)
    return {
        "breakouts": len(events),
        "observations_incomplete": sum(not event.observation_complete for event in events),
        "path_c_on_first_signal": sum(
            bool(event.path_c.signal_date) and event.path_c.signal_date == event.first.signal_date
            for event in events
        ),
        "path_c_only_at_first_signal": sum(event.first_paths == "C" for event in events),
        "path_c_after_first_signal": sum(
            bool(event.path_c.signal_date) and event.path_c.signal_date > event.first.signal_date
            for event in events
        ),
        "first": _trade_summary([event.first for event in events]),
        "path_c": _trade_summary([event.path_c for event in events]),
        "first_closed_without_path_c_signal": _trade_summary([
            event.first for event in events
            if event.observation_complete and event.path_c.status == "no_signal"
            and event.first.status == "closed"
        ]),
        "paired_closed_trades": len(paired),
        "paired_mean_path_c_minus_first_pp": round(mean(paired), 4) if paired else None,
        "paired_delayed_closed_trades": len(delayed_paired),
        "paired_delayed_mean_path_c_minus_first_pp": round(mean(delayed_paired), 4)
        if delayed_paired else None,
    }


def _load_cache(
    path: Path, end: str,
) -> tuple[dict[str, list[Bar]], list[str], str, int]:
    histories: dict[str, list[Bar]] = defaultdict(list)
    sessions = set()
    invalid = 0
    digest = hashlib.sha256()
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for symbol, *values in db.execute(
            "SELECT symbol, date, open, high, low, close, volume "
            "FROM stock_kline_cache WHERE market = 'CN' AND date <= ? ORDER BY symbol, date",
            (end,),
        ):
            bar = Bar(*values)
            sessions.add(bar.date)
            if not symbol.startswith(MAIN_BOARD_PREFIXES):
                continue
            prices = (bar.open, bar.high, bar.low, bar.close)
            if any(not math.isfinite(price) or price <= 0 for price in prices):
                invalid += 1
                continue
            if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
                invalid += 1
                continue
            if bar.volume is not None and (not math.isfinite(bar.volume) or bar.volume < 0):
                invalid += 1
                continue
            histories[symbol].append(bar)
            digest.update(json.dumps([symbol, *values], separators=(",", ":")).encode())
            digest.update(b"\n")
    calendar = sorted(sessions)
    digest.update(json.dumps(calendar, separators=(",", ":")).encode())
    return histories, calendar, digest.hexdigest(), invalid


def _write_events(path: Path, events: list[Event]) -> None:
    columns = [field.name for field in fields(Event) if field.name not in ("first", "path_c")]
    columns += [f"{group}_{field.name}" for group in ("first", "path_c") for field in fields(Trade)]
    with path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for event in events:
            row = asdict(event)
            for group in ("first", "path_c"):
                row.update({f"{group}_{key}": value for key, value in row.pop(group).items()})
            writer.writerow(row)


def _write_path_trades(path: Path, trades: list[PathTrade]) -> None:
    columns = [field.name for field in fields(PathTrade) if field.name != "trade"]
    columns += [field.name for field in fields(Trade)]
    with path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for trade in trades:
            row = asdict(trade)
            row.update(row.pop("trade"))
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/panwatch.db"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, help="first breakout date (inclusive)")
    parser.add_argument("--end", type=date.fromisoformat, help="last allowed data date (inclusive)")
    parser.add_argument("--by-path", action="store_true", help="report A, B, C and D independently")
    parser.add_argument("--min-reward-risk", type=float, help="minimum net entry reward/risk for --by-path")
    parser.add_argument("--commission-bps", type=float, default=Execution.commission_bps)
    parser.add_argument("--sell-tax-bps", type=float, default=Execution.sell_tax_bps)
    parser.add_argument("--slippage-bps", type=float, default=Execution.slippage_bps)
    args = parser.parse_args()
    if args.min_reward_risk is not None and (
        not args.by_path or not math.isfinite(args.min_reward_risk) or args.min_reward_risk <= 0
    ):
        parser.error("--min-reward-risk needs --by-path and a positive finite ratio")
    rates = (args.commission_bps, args.sell_tax_bps, args.slippage_bps)
    if any(not math.isfinite(rate) or not 0 <= rate < 10_000 for rate in rates):
        parser.error("costs must be finite and between 0 and 10,000 bps")
    if args.commission_bps + args.sell_tax_bps >= 10_000:
        parser.error("combined sell costs must be below 10,000 bps")
    if args.output_dir.exists():
        parser.error("--output-dir must be a new directory")

    try:
        histories, sessions, digest, invalid = _load_cache(args.db, (args.end or date.max).isoformat())
    except sqlite3.Error as error:
        parser.error(f"cannot read daily bars from {args.db}: {error}")
    if not histories:
        parser.error("no cached main-board daily bars in the requested range")
    start = args.start.isoformat() if args.start else min(bars[0].date for bars in histories.values())
    end = args.end.isoformat() if args.end else sessions[-1]
    if start > end:
        parser.error("--start must be on or before --end")

    execution = Execution(*rates)
    params = BreakoutParams()
    summary = {
        "source": str(args.db.resolve()),
        "daily_bars_sha256": digest,
        "requested_start": start,
        "requested_end": end,
        "latest_observed_session": sessions[-1],
        "main_board_prefix_symbols": len(histories),
        "valid_main_board_bars": sum(map(len, histories.values())),
        "invalid_main_board_bars": invalid,
        "strategy_params": asdict(params),
        "execution": asdict(execution),
    }
    args.output_dir.mkdir(parents=True)
    if args.by_path:
        trades = run_path_study(
            histories, sessions, start, end, execution, params, min_reward_risk=args.min_reward_risk,
        )
        if args.min_reward_risk is not None:
            summary["minimum_entry_reward_risk"] = args.min_reward_risk
        by_path: dict[str, list[PathTrade]] = defaultdict(list)
        for trade in trades:
            by_path[trade.path].append(trade)
        summary["paths"] = {
            path: {
                **_trade_summary([row.trade for row in by_path[path]]),
                "sole_path_at_confirmation": _trade_summary([
                    row.trade for row in by_path[path] if row.confirmed_paths == path
                ]),
            }
            for path in "ABCD"
        }
        _write_path_trades(args.output_dir / "path-trades.csv", trades)
    else:
        events = run_study(histories, sessions, start, end, execution, params)
        summary.update(summarize(events))
        _write_events(args.output_dir / "events.csv", events)
    output = json.dumps(summary, indent=2, ensure_ascii=False)
    (args.output_dir / "summary.json").write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
