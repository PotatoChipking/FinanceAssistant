"""突破有效性状态机测试:合成K线覆盖各状态与路径。"""

import pytest

from src.core.breakout_exit_rules import ma5_exit, volume_doji_exit, volume_stall_exit
from src.core.breakout_retest_study import (
    Bar, Event, Execution, _simulate, run_path_study, run_study, summarize,
)
from src.core.signals.breakout_validity import BreakoutParams, compute_breakout_validity


def _bar(date: str, o: float, h: float, low: float, c: float, v: float = 1000.0) -> dict:
    return {"date": date, "open": o, "high": h, "low": low, "close": c, "volume": v}


def _date(i: int) -> str:
    # 生成递增的伪交易日(仅用于标识,算法不解析日期间隔)
    return f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"


def _uptrend_base(n: int = 70, start: float = 10.0, step: float = 0.02) -> list[dict]:
    """缓慢爬升的底仓K线:满足均线多头,末端留出前高。"""
    rows = []
    price = start
    for i in range(n):
        o = price
        c = price + step
        rows.append(_bar(_date(i), o, c + 0.02, o - 0.03, c, 1000.0))
        price = c
    return rows


def _with_peak_and_pullback(rows: list[dict], peak: float, pull_days: int = 6) -> list[dict]:
    """在底仓尾部造一个前高(peak),随后横盘微升整理,使前高年龄≥5且均线保持多头。"""
    i = len(rows)
    rows = rows + [_bar(_date(i), peak - 0.1, peak, peak - 0.2, peak - 0.05, 1500.0)]
    base = peak - 0.35
    for j in range(pull_days):
        i += 1
        c = base + 0.015 * j  # 微升横盘,收盘始终低于 peak×1.005
        rows.append(_bar(_date(i), c - 0.02, c + 0.03, c - 0.05, c, 800.0))
    return rows


def _breakout_day(rows: list[dict], g0: float, *, vol: float, close_mult: float = 1.02) -> list[dict]:
    i = len(rows)
    c = g0 * close_mult
    return rows + [_bar(_date(i), rows[-1]["close"], c + 0.02, rows[-1]["close"] - 0.02, c, vol)]


def _make_event(vol: float = 2000.0) -> tuple[list[dict], float]:
    """构造标准事件:上升趋势+前高12.0+突破日。返回(rows, g0)。"""
    rows = _uptrend_base(70, start=10.0)
    peak = rows[-1]["close"] + 0.6  # 明显高于其后的整理区
    rows = _with_peak_and_pullback(rows, peak=peak)
    rows = _breakout_day(rows, peak, vol=vol)
    return rows, peak


def _hold_days(rows: list[dict], g0: float, days: int, *, vol: float = 900.0, mult: float = 1.025) -> list[dict]:
    for _ in range(days):
        i = len(rows)
        c = g0 * mult
        rows = rows + [_bar(_date(i), c - 0.02, c + 0.03, c - 0.05, c, vol)]
        mult += 0.002
    return rows


def test_observed_on_d0():
    """突破日当天(无后续K线)状态为 observed"""
    rows, g0 = _make_event(vol=2000.0)
    r = compute_breakout_validity(rows)
    assert r.state == "observed"
    assert abs(r.g0 - round(g0, 4)) < 0.01
    assert r.event_age == 0


def test_pending_before_confirm_window():
    """突破后1~2天未过最小确认时间,状态为 pending"""
    rows, g0 = _make_event(vol=2000.0)
    rows = _hold_days(rows, g0, 2)
    r = compute_breakout_validity(rows)
    assert r.state == "pending"
    assert "最小确认时间" in r.reason


def test_valid_active_path_a():
    """放量突破(路径A)+站稳3日 → valid_active,止损=max(G_SUPPORT,L0)"""
    rows, g0 = _make_event(vol=2000.0)  # V0=2000 ≥ V_BASE×1.3
    rows = _hold_days(rows, g0, 3)
    r = compute_breakout_validity(rows)
    assert r.state == "valid_active"
    assert r.paths["A"] is True
    assert r.stop_loss == round(max(r.g_support, r.l0), 4)
    assert r.target_price == round(r.g0 * 1.15, 4)


def test_valid_active_path_b_late_volume():
    """D0缩量突破,后置正向放量日确认(路径B)→ valid_active"""
    rows, g0 = _make_event(vol=900.0)  # D0 未放量
    rows = _hold_days(rows, g0, 2, vol=900.0)
    # 第3天正向放量推进:放量+上涨+收盘强
    i = len(rows)
    c = g0 * 1.045
    rows = rows + [_bar(_date(i), g0 * 1.03, c + 0.02, g0 * 1.025, c, 2200.0)]
    r = compute_breakout_validity(rows)
    assert r.state == "valid_active"
    assert r.paths["B"] is True


def test_failed_close_below_support():
    """收盘跌破 G_SUPPORT(3a)→ failed 且永久失效"""
    rows, g0 = _make_event(vol=2000.0)
    i = len(rows)
    c = g0 * 0.96  # < 0.97×G0
    rows = rows + [_bar(_date(i), g0, g0 * 1.01, c - 0.05, c, 1000.0)]
    r = compute_breakout_validity(rows)
    assert r.state == "failed"
    assert "3a" in r.fail_reason


def test_failed_big_volume_breakdown():
    """放量收回 G0 下方(3d 放量破位)→ failed,即使跌幅未及支撑区"""
    rows, g0 = _make_event(vol=2000.0)
    i = len(rows)
    c = g0 * 0.985  # 在 [G_SUPPORT, G0) 区间,但放量
    rows = rows + [_bar(_date(i), g0 * 1.01, g0 * 1.015, c - 0.02, c, 2500.0)]
    r = compute_breakout_validity(rows)
    assert r.state == "failed"
    assert "3d" in r.fail_reason


def test_failed_three_consecutive_below():
    """连续3日收盘<G0(3c)→ failed(缩量阴跌也算持续拒绝)"""
    rows, g0 = _make_event(vol=2000.0)
    for k in range(3):
        i = len(rows)
        c = g0 * (0.995 - 0.002 * k)  # 略低于G0但高于支撑区
        rows = rows + [_bar(_date(i), c + 0.01, c + 0.02, c - 0.02, c, 700.0)]
    r = compute_breakout_validity(rows)
    assert r.state == "failed"
    assert "3c" in r.fail_reason


def test_invalidated_after_valid():
    """先达成Valid再破位 → invalidated(不改写曾经有效)"""
    rows, g0 = _make_event(vol=2000.0)
    rows = _hold_days(rows, g0, 4)  # 已达成 valid(路径A)
    i = len(rows)
    c = g0 * 0.96
    rows = rows + [_bar(_date(i), g0, g0 * 1.01, c - 0.05, c, 1000.0)]
    r = compute_breakout_validity(rows)
    assert r.state == "invalidated"
    assert r.ever_valid is True


def test_extended_when_overextended():
    """C/G0>1.15 → extended,不作为买入候选"""
    rows, g0 = _make_event(vol=2000.0)
    rows = _hold_days(rows, g0, 3, mult=1.16)  # 收盘越过 1.15×G0
    r = compute_breakout_validity(rows)
    assert r.state == "extended"
    assert r.extension > 1.15


def test_not_in_pool_without_breakout():
    """无突破事件 → not_in_pool"""
    rows = _uptrend_base(70)
    r = compute_breakout_validity(rows)
    assert r.state == "not_in_pool"


def test_anchor_replay_keeps_event():
    """锚点冻结:传入 anchor_d0_date 后即使事件超出观察窗口也能复评"""
    rows, g0 = _make_event(vol=2000.0)
    d0_date = rows[-1]["date"]
    rows = _hold_days(rows, g0, 8)  # 年龄8 > 观察期5
    r = compute_breakout_validity(rows, anchor_d0_date=d0_date)
    assert r.d0_date == d0_date
    assert r.state in ("valid_active", "extended")


def test_insufficient_data():
    """数据不足10根 → insufficient"""
    r = compute_breakout_validity([_bar(_date(i), 10, 10.1, 9.9, 10, 100) for i in range(5)])
    assert r.state == "insufficient"


def _study(
    rows: list[dict],
    d0: str,
    *,
    sessions: list[str] | None = None,
    end: str | None = None,
    holding_days: int = 2,
) -> list[Event]:
    return run_study(
        {"600001": [Bar(**row) for row in rows]},
        sessions or [row["date"] for row in rows],
        d0,
        end or rows[-1]["date"],
        Execution(commission_bps=0, sell_tax_bps=0, slippage_bps=0, holding_days=holding_days),
        BreakoutParams(),
    )


def test_study_compares_first_confirmation_with_later_path_c() -> None:
    rows, level = _make_event()
    d0 = rows[-1]["date"]
    rows = _hold_days(rows, level, 3)
    first_signal = rows[-1]["date"]
    c_day = len(rows)
    rows.append(_bar(_date(c_day), level * 1.028, level * 1.04, level * 1.01, level * 1.035, 600))
    rows = _hold_days(rows, level, 3, mult=1.04)

    [event] = _study(rows, d0)

    assert event.first.signal_date == first_signal
    assert event.first.entry_date == _date(c_day)
    assert event.path_c.signal_date == _date(c_day)
    assert event.path_c.entry_date == _date(c_day + 1)
    assert "C" in event.path_c_paths.split("+")
    assert event.first.status == event.path_c.status == "closed"
    assert summarize([event])["paired_delayed_closed_trades"] == 1

    [prefix] = _study(rows, d0, end=first_signal)
    assert prefix.first.status == "entry_bar_unavailable"
    assert prefix.path_c.status == "observation_incomplete"


def test_study_avoids_same_day_exit_and_prices_stop_at_next_gap() -> None:
    rows, level = _make_event()
    d0 = rows[-1]["date"]
    rows = _hold_days(rows, level, 3)
    entry_index = len(rows)
    rows.append(_bar(_date(entry_index), level * 1.03, level * 1.05, level * 0.94, level * 1.04))
    gap = level * 0.93
    rows.append(_bar(_date(entry_index + 1), gap, level * 0.96, level * 0.91, level * 0.95))

    [event] = _study(rows, d0)

    assert event.first.exit_reason == "stop_gap"
    assert event.first.exit_date > event.first.entry_date
    assert event.first.exit_price == pytest.approx(gap, abs=1e-6)


def test_study_exits_structure_invalidation_on_following_open() -> None:
    rows, level = _make_event()
    d0 = rows[-1]["date"]
    rows = _hold_days(rows, level, 3)
    entry_index = len(rows)
    rows.append(_bar(_date(entry_index), level * 1.025, level * 1.04, level * 0.995, level * 0.999, 800))
    rows.append(_bar(_date(entry_index + 1), level, level * 1.01, level * 0.996, level * 0.998, 800))
    rows.append(_bar(_date(entry_index + 2), level, level * 1.01, level * 0.996, level * 0.997, 800))
    rows.append(_bar(_date(entry_index + 3), level * 0.995, level, level * 0.99, level * 0.996))

    [event] = _study(rows, d0, holding_days=10)

    assert event.first.exit_date == _date(entry_index + 3)
    assert event.first.exit_reason == "structure_invalidated"


def test_study_marks_missing_entry_session_without_trading_on_later_bar() -> None:
    rows, level = _make_event()
    d0 = rows[-1]["date"]
    rows = _hold_days(rows, level, 5)
    sessions = [row["date"] for row in rows]
    del rows[-2]

    [event] = _study(rows, d0, sessions=sessions)

    assert event.first.status == "entry_bar_unavailable"
    assert event.path_c.status == "observation_incomplete"


def test_study_checks_dated_eligibility_again_at_entry() -> None:
    rows, level = _make_event()
    d0 = rows[-1]["date"]
    rows = _hold_days(rows, level, 4)
    bars = [Bar(**row) for row in rows[:-1]]
    bars.append(Bar(**rows[-1], eligible_for_entry=False))

    [event] = run_study(
        {"600001": bars}, [bar.date for bar in bars], d0, bars[-1].date,
        Execution(), BreakoutParams(),
    )

    assert event.first.signal_date == bars[-2].date
    assert event.first.status == "entry_ineligible"
    assert event.first.entry_date == ""


def test_path_study_counts_overlaps_and_keeps_scanning_after_c() -> None:
    rows, level = _make_event(vol=900)
    d0 = rows[-1]["date"]
    rows = _hold_days(rows, level, 2)
    c_day = len(rows)
    rows.append(_bar(_date(c_day), level * 1.02, level * 1.04, level * 1.01, level * 1.035, 600))
    b_day = len(rows)
    rows.append(_bar(_date(b_day), level * 1.036, level * 1.05, level * 1.03, level * 1.048, 2100))
    rows = _hold_days(rows, level, 2, mult=1.052)

    trades = run_path_study(
        {"600001": [Bar(**row) for row in rows]}, [row["date"] for row in rows],
        d0, rows[-1]["date"], Execution(), BreakoutParams(),
    )
    by_path = {record.path: record for record in trades}

    assert [record.path for record in trades] == ["B", "C", "D"]
    assert by_path["C"].confirmed_paths == by_path["D"].confirmed_paths == "C+D"
    assert by_path["C"].trade == by_path["D"].trade
    assert by_path["C"].trade.signal_date == _date(c_day)
    assert by_path["B"].trade.signal_date == _date(b_day)
    assert by_path["B"].trade.entry_date == _date(b_day + 1)


def test_ma5_exit_replaces_target_and_fills_after_the_close_signal() -> None:
    rows, level = _make_event()
    d0 = len(rows) - 1
    rows = _hold_days(rows, level, 3)
    signal = len(rows) - 1
    rows.append(_bar(_date(signal + 1), level * 1.03, level * 1.045, level * 1.02, level * 1.04))
    rows.append(_bar(_date(signal + 2), level * 1.12, level * 1.16, level * 1.06, level * 1.07))
    rows.append(_bar(_date(signal + 3), level * 1.06, level * 1.07, level, level * 1.01))
    rows.append(_bar(_date(signal + 4), level * 1.02, level * 1.04, level, level * 1.03))
    bars = [Bar(**row) for row in rows]
    sessions = {bar.date: i for i, bar in enumerate(bars)}
    params = BreakoutParams()
    confirmed = compute_breakout_validity(bars[:signal + 1], params=params, anchor_d0_date=bars[d0].date)
    args = (bars, d0, (signal, confirmed), True, sessions, d0, params, Execution())

    fixed = _simulate(*args)
    trailing = _simulate(*args, trend_exit=ma5_exit)

    assert fixed.exit_reason == "target_price"
    assert fixed.exit_date == _date(signal + 2)
    assert trailing.entry_price == fixed.entry_price
    assert trailing.exit_reason == "close_below_ma5"
    assert trailing.exit_date == _date(signal + 4)


def test_doji_and_stall_use_only_bars_after_target_activation() -> None:
    bars = [Bar(_date(i), 100, 102, 99, 100, 100) for i in range(5)]
    bars.append(Bar(_date(5), 100, 103, 99, 100.1, 200))
    bars.append(Bar(_date(6), 99.2, 100, 98, 98.9, 150))
    bars.append(Bar(_date(7), 99, 101, 98, 99, 250))

    assert volume_doji_exit(bars, 5, 5) == ""
    assert volume_doji_exit(bars, 6, 6) == ""
    assert volume_doji_exit(bars, 6, 5) == "volume_doji_break"
    assert volume_stall_exit(bars, 6, 5) == ""
    assert volume_stall_exit(bars, 7, 5) == "volume_stall"


def test_entry_reward_risk_checks_the_open_with_costs_before_entering() -> None:
    rows, level = _make_event()
    d0 = len(rows) - 1
    rows = _hold_days(rows, level, 3)
    signal = len(rows) - 1
    rows.append(_bar(_date(signal + 1), level * 1.045, level * 1.055, level * 1.02, level * 1.04))
    rows = _hold_days(rows, level, 2, mult=1.05)
    bars = [Bar(**row) for row in rows]
    sessions = {bar.date: index for index, bar in enumerate(bars)}
    params = BreakoutParams()
    confirmed = compute_breakout_validity(bars[:signal + 1], params=params, anchor_d0_date=bars[d0].date)
    assert confirmed.stop_loss is not None and confirmed.target_price is not None
    execution = Execution(holding_days=2)
    entry_price = bars[signal + 1].open * (1 + execution.slippage_bps / 10_000)
    ratio = execution.entry_reward_risk(entry_price, confirmed.stop_loss, confirmed.target_price)
    assert ratio is not None
    without_costs = Execution(commission_bps=0, sell_tax_bps=0, slippage_bps=0)
    gross_ratio = without_costs.entry_reward_risk(
        bars[signal + 1].open, confirmed.stop_loss, confirmed.target_price,
    )
    assert gross_ratio is not None and gross_ratio > ratio
    args = (bars, d0, (signal, confirmed), True, sessions, d0, params, execution)

    baseline = _simulate(*args)
    assert baseline.status == "closed"
    assert _simulate(*args, min_reward_risk=ratio) == baseline
    blocked = _simulate(*args, min_reward_risk=gross_ratio)
    assert blocked.status == "below_min_reward_risk"
    assert blocked.entry_date == ""
