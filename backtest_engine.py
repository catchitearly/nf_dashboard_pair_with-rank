"""
backtest_engine.py
Replays historical market data through the full strategy logic tick by tick.

How it works:
  1. Loads historical snapshots for a given date.
  2. Activates test_mode to patch fyers_data.
  3. For each snapshot (5-min tick):
     a. Sets tick_index so patches return that snapshot's data.
     b. Overrides datetime.now() to return snapshot's IST time.
     c. Runs strategy.run_cycle() — the SAME code as live trading.
     d. After the cycle, injects actual observed MTM into state
        (because our synthetic quotes cannot reproduce real option prices;
        we use actual log MTM to simulate P&L faithfully).
     e. Applies strategy exit rules against the injected MTM.
  4. Records all trades and computes final P&L.
  5. Prints a detailed report with comparison to actual system performance.

MTM injection logic:
  The actual MTM from logs already accounts for real option prices.
  We use it as ground truth. The strategy's exit decisions (profit floor,
  force exit) are applied to these real MTM values — exactly what the
  strategy WOULD have done if it had been live on that day.
"""

import logging
import sys
import copy
from datetime import datetime, date, timezone, timedelta
from typing import Optional

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


# ── Time override ─────────────────────────────────────────────────────────────

_mocked_time: Optional[datetime] = None


def _set_mocked_time(date_str: str, time_str: str) -> None:
    global _mocked_time
    h, m = map(int, time_str.split(":"))
    d     = datetime.strptime(date_str, "%Y-%m-%d").date()
    _mocked_time = datetime(d.year, d.month, d.day, h, m, 0, tzinfo=IST)


def _patch_strategy_time() -> None:
    """Monkey-patch strategy._ist_now() to return our mocked time."""
    import strategy
    strategy._ist_now = lambda: _mocked_time


def _unpatch_strategy_time() -> None:
    import strategy
    strategy._ist_now = lambda: datetime.now(IST)


# ── Trade record ──────────────────────────────────────────────────────────────

class TradeEvent:
    def __init__(self, time: str, event_type: str, details: str, mtm: float):
        self.time       = time
        self.event_type = event_type   # ENTRY, EXIT, ADD, REBALANCE, FLOOR_SET, etc.
        self.details    = details
        self.mtm        = mtm

    def __repr__(self):
        return f"[{self.time}] {self.event_type:12s} | MTM={self.mtm:+8.0f} | {self.details}"


# ── Result object ─────────────────────────────────────────────────────────────

class BacktestResult:
    def __init__(self, date_str: str):
        self.date          = date_str
        self.events:  list[TradeEvent] = []
        self.final_pnl     = 0.0
        self.peak_mtm      = 0.0
        self.actual_pnl    = None   # from logs
        self.entry_time    = None
        self.exit_time     = None
        self.exit_reason   = None
        self.entry_label   = None
        self.ticks_run     = 0

    def add_event(self, time, etype, details, mtm):
        self.events.append(TradeEvent(time, etype, details, mtm))

    def summary(self) -> str:
        lines = [
            f"\n{'='*60}",
            f"  BACKTEST RESULT — {self.date}",
            f"{'='*60}",
            f"  Entry:        {self.entry_time or 'none'} ({self.entry_label or '-'})",
            f"  Exit:         {self.exit_time or 'none'} ({self.exit_reason or '-'})",
            f"  Peak MTM:     ₹{self.peak_mtm:,.0f}",
            f"  Strategy P&L: ₹{self.final_pnl:,.0f}",
        ]
        if self.actual_pnl is not None:
            diff = self.final_pnl - self.actual_pnl
            sign = "+" if diff >= 0 else ""
            lines.append(f"  Actual P&L:   ₹{self.actual_pnl:,.0f}  (strategy {sign}₹{diff:,.0f})")
        lines.append(f"\n  Events:")
        for e in self.events:
            lines.append(f"    {e}")
        lines.append(f"{'='*60}")
        return "\n".join(lines)


# ── Core backtest runner ──────────────────────────────────────────────────────

def run_backtest_date(date_str: str) -> BacktestResult:
    """
    Run the full strategy against one day of historical data.
    Returns a BacktestResult.
    """
    from backtest_data import HISTORICAL_DATA, ATM_BY_DATE
    import state_manager as sm
    import test_mode
    import strategy

    result = BacktestResult(date_str)

    snaps = HISTORICAL_DATA.get(date_str)
    if not snaps:
        log.error(f"No data for {date_str}")
        return result

    # Get actual final P&L from last snapshot
    for snap in reversed(snaps):
        if snap["mtm_actual"] is not None:
            result.actual_pnl = snap["mtm_actual"]
            break

    atm = ATM_BY_DATE.get(date_str, 24000)

    # Fresh state for this backtest
    state = sm._fresh_state()
    state["date"] = date_str

    # Activate test mode
    test_mode.activate_test_mode(date_str, tick_index=0)
    _patch_strategy_time()

    # Swap state_manager to use our in-memory state dict
    # (avoid file I/O during backtest)
    _state_store = {"state": state}

    original_load = sm.load_state
    original_save = sm.save_state

    def _bt_load():
        return _state_store["state"]

    def _bt_save(s):
        _state_store["state"] = s
        return True

    sm.load_state = _bt_load
    sm.save_state = _bt_save

    try:
        for idx, snap in enumerate(snaps):
            time_str = snap["time"]

            # Skip "EXIT" marker row
            if snap["label"] == "EXIT":
                break

            # Set mocked time
            _set_mocked_time(date_str, time_str)
            test_mode.set_tick(idx)

            # Get current state before cycle
            state_before = copy.deepcopy(_state_store["state"])
            had_positions = bool(state_before.get("positions"))

            # Inject actual MTM if we have positions
            actual_mtm = snap.get("mtm_actual")
            if actual_mtm is not None:
                test_mode.set_injected_mtm(actual_mtm)

            # Run one strategy cycle
            try:
                strategy.run_cycle()
            except Exception as e:
                log.error(f"Cycle error at {time_str}: {e}")
                result.add_event(time_str, "ERROR", str(e), actual_mtm or 0)
                continue

            state_after = _state_store["state"]
            result.ticks_run += 1

            # ── Detect what happened this tick ───────────────────────────
            mtm_val = actual_mtm if actual_mtm is not None else 0.0

            # Entry
            if not had_positions and state_after.get("positions"):
                result.entry_time  = time_str
                result.entry_label = state_after.get("entry_label", "?")
                result.add_event(
                    time_str, "ENTRY",
                    f"label={result.entry_label} score={snap['score']:+.1f} spot={snap['spot']:.0f}",
                    mtm_val
                )

            # Track peak MTM
            if actual_mtm is not None and actual_mtm > result.peak_mtm:
                result.peak_mtm = actual_mtm

            # Profit floor set
            floor = state_after.get("profit_floor")
            prev_floor = state_before.get("profit_floor")
            if floor and floor != prev_floor:
                result.add_event(
                    time_str, "FLOOR_SET",
                    f"peak={state_after['peak_mtm']:.0f} → floor=₹{floor:.0f}",
                    mtm_val
                )

            # Exit
            if had_positions and not state_after.get("positions"):
                exit_reason = _detect_exit_reason(state_before, state_after, actual_mtm)
                result.exit_time   = time_str
                result.exit_reason = exit_reason
                # In test mode, closed_pnl is synthetic (entry=exit LTP=50).
                # Use the actual observed MTM at the exit tick as the real P&L.
                result.final_pnl   = actual_mtm if actual_mtm is not None else state_after.get("closed_pnl", 0.0)
                result.add_event(
                    time_str, "EXIT",
                    f"reason={exit_reason} pnl=₹{result.final_pnl:,.0f}",
                    mtm_val
                )
                # Done — strategy exited
                break

            # Rebalance
            prev_adj = state_before.get("adj_count", 0)
            curr_adj = state_after.get("adj_count", 0)
            if curr_adj > prev_adj:
                result.add_event(
                    time_str, "REBALANCE",
                    f"label={snap['label']} score={snap['score']:+.1f}",
                    mtm_val
                )

            # 12:00 add
            if not state_before.get("add_done") and state_after.get("add_done"):
                result.add_event(
                    time_str, "ADD",
                    f"label={snap['label']}",
                    mtm_val
                )

        # If positions still open at end of data → use last known MTM
        if _state_store["state"].get("positions"):
            last_snap = snaps[-1]
            result.final_pnl  = last_snap.get("mtm_actual") or 0.0
            result.exit_time  = last_snap["time"]
            result.exit_reason = "data_end"
            result.add_event(
                last_snap["time"], "DATA_END",
                f"positions still open | last MTM=₹{result.final_pnl:.0f}",
                result.final_pnl
            )

    finally:
        # Restore originals
        sm.load_state = original_load
        sm.save_state = original_save
        test_mode.deactivate_test_mode()
        _unpatch_strategy_time()

    return result


def _detect_exit_reason(state_before: dict, state_after: dict, mtm: Optional[float]) -> str:
    floor = state_before.get("profit_floor")
    if floor and mtm is not None and mtm < floor:
        return "profit_floor"
    if state_after.get("daily_stopped"):
        return "daily_loss_limit"
    # Check if forced exit time
    if _mocked_time:
        hm = (_mocked_time.hour, _mocked_time.minute)
        if hm >= (14, 30):
            return "force_exit_1430"
    return "signal_exit"


# ── Multi-date backtest ───────────────────────────────────────────────────────

def run_backtest_all() -> list[BacktestResult]:
    from backtest_data import AVAILABLE_DATES
    results = []
    for d in AVAILABLE_DATES:
        log.info(f"\nRunning backtest for {d}...")
        r = run_backtest_date(d)
        results.append(r)
        print(r.summary())
    return results


def print_summary_table(results: list[BacktestResult]) -> None:
    print(f"\n{'='*70}")
    print(f"  BACKTEST SUMMARY — {len(results)} day(s)")
    print(f"{'='*70}")
    print(f"  {'Date':<12} {'Entry':>8} {'Label':>14} {'Peak':>8} {'Strategy':>10} {'Actual':>10} {'Diff':>10}")
    print(f"  {'-'*68}")

    total_strategy = 0.0
    total_actual   = 0.0
    for r in results:
        actual_str = f"₹{r.actual_pnl:,.0f}" if r.actual_pnl is not None else "    N/A"
        diff_val   = r.final_pnl - (r.actual_pnl or 0)
        diff_str   = f"{'+' if diff_val >= 0 else ''}₹{diff_val:,.0f}"
        print(
            f"  {r.date:<12} {r.entry_time or 'none':>8} "
            f"{r.entry_label or '-':>14} "
            f"₹{r.peak_mtm:>7,.0f} "
            f"₹{r.final_pnl:>9,.0f} "
            f"{actual_str:>10} "
            f"{diff_str:>10}"
        )
        total_strategy += r.final_pnl
        if r.actual_pnl is not None:
            total_actual += r.actual_pnl

    print(f"  {'-'*68}")
    total_diff = total_strategy - total_actual
    print(
        f"  {'TOTAL':<12} {'':>8} {'':>14} {'':>8} "
        f"₹{total_strategy:>9,.0f} "
        f"₹{total_actual:>9,.0f} "
        f"{'+' if total_diff >= 0 else ''}₹{total_diff:,.0f}"
    )
    print(f"{'='*70}\n")
