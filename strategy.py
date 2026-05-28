"""
strategy.py
Main strategy orchestration — runs ONE cycle per invocation.
Each GitHub Actions run calls main.py → strategy.run_cycle().

Flow per cycle:
  1. Load state
  2. Check force-exit conditions (time, daily loss)
  3. Fetch spot price + straddle candles
  4. Compute view (score + label)
  5. Update label confirmation
  6. If no positions: check entry conditions
  7. If positions open: compute MTM, check profit lock, rebalance if needed
  8. 12:00 add trigger
  9. Save state
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import fyers_data as fd
import state_manager as sm
import view_engine as ve
import position_manager as pm
import notifier

from config import (
    ENTRY_START_IST, ENTRY_CUTOFF_IST, FORCE_EXIT_IST,
    ADD_POSITION_IST, MAX_TRADES_PER_DAY, DAILY_LOSS_LIMIT,
    STRIKE_STEP,
)

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def _ist_now() -> datetime:
    return datetime.now(IST)


def _ist_hm(dt: datetime) -> tuple[int, int]:
    return dt.hour, dt.minute


def _hm_gte(hm: tuple, ref: tuple) -> bool:
    return hm >= ref


def _hm_lte(hm: tuple, ref: tuple) -> bool:
    return hm <= ref


def _round_atm(spot: float) -> int:
    """Round spot to nearest STRIKE_STEP."""
    return int(round(spot / STRIKE_STEP) * STRIKE_STEP)


# ── Main cycle ───────────────────────────────────────────────────────────────

def run_cycle() -> None:
    now     = _ist_now()
    hm      = _ist_hm(now)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S IST")

    log.info("=" * 60)
    log.info(f"Run at {now_str}")

    state = sm.load_state()

    # ── 1. Daily stopped ────────────────────────────────────────────────────
    if state.get("daily_stopped"):
        log.info("Daily loss limit was hit — no trading today.")
        sm.save_state(state)
        return

    # ── 2. Force exit (time-based) ──────────────────────────────────────────
    if _hm_gte(hm, FORCE_EXIT_IST) and state.get("positions"):
        log.info(f"Force exit at {now.strftime('%H:%M')} IST")
        mtm = pm.compute_mtm(state)
        pnl = pm.close_all_positions(state, reason="force_exit_1430")
        notifier.alert_exit("14:30 Force Exit", pnl, state.get("peak_mtm", 0))
        state["trade_count"] = state.get("trade_count", 0) + 1
        sm.save_state(state)
        log.info(f"Run complete.")
        return

    if _hm_gte(hm, FORCE_EXIT_IST):
        log.info("Past 14:30 — no positions, nothing to do.")
        sm.save_state(state)
        return

    # ── 3. Fetch spot ───────────────────────────────────────────────────────
    spot = fd.get_spot_price()
    if spot is None:
        log.error("Could not fetch spot price — aborting cycle.")
        notifier.alert_error("strategy.run_cycle", "Spot price fetch failed")
        sm.save_state(state)
        return

    # ── 4. Determine ATM ────────────────────────────────────────────────────
    if state.get("atm") is None:
        state["atm"] = _round_atm(spot)
        # Fetch and store 9:15 close for reference
        open_close = fd.get_915_candle_close()
        if open_close:
            state["atm"] = _round_atm(open_close)
        log.info(f"ATM fixed at {state['atm']}")

    atm = int(state["atm"])

    # ── 5. Fetch straddle candles ────────────────────────────────────────────
    straddle_candles = fd.get_all_straddle_candles(atm)
    if straddle_candles is None:
        log.error("Straddle candles fetch failed — aborting cycle.")
        sm.save_state(state)
        return

    # ── 6. Compute view ──────────────────────────────────────────────────────
    view = ve.compute_view(atm, straddle_candles, spot)
    if view is None:
        log.error("View computation failed — aborting cycle.")
        sm.save_state(state)
        return

    score = view["score"]
    label = view["label"]

    # ── 7. Update label confirmation ─────────────────────────────────────────
    confirmed_label = ve.update_label_confirmation(state, label)

    # Initialise current_label on first run
    if state.get("current_label") is None and confirmed_label is None:
        # Not yet confirmed — skip entry
        sm.save_state(state)
        return

    current_label = state.get("current_label", label)

    # ── 8. Compute MTM if positions exist ────────────────────────────────────
    mtm = 0.0
    if state.get("positions"):
        mtm = pm.compute_mtm(state)
        old_floor = state.get("profit_floor")
        sm.update_peak_and_floor(state, mtm)
        new_floor = state.get("profit_floor")

        if new_floor and new_floor != old_floor:
            notifier.alert_profit_floor_set(state["peak_mtm"], new_floor)
            log.info(f"Profit floor updated: ₹{new_floor:,.0f}")

    # ── 9. Daily loss limit ──────────────────────────────────────────────────
    if mtm <= DAILY_LOSS_LIMIT and state.get("positions"):
        log.warning(f"Daily loss limit hit: MTM={mtm:.0f}")
        pnl = pm.close_all_positions(state, reason="daily_loss_limit")
        state["daily_stopped"] = True
        notifier.alert_daily_loss_limit(mtm)
        sm.save_state(state)
        return

    # ── 10. Profit floor check ───────────────────────────────────────────────
    if state.get("positions") and sm.is_profit_floor_breached(state, mtm):
        floor = state.get("profit_floor")
        log.info(f"Profit floor breached: MTM={mtm:.0f} < floor={floor:.0f} → EXIT")
        notifier.alert_profit_floor_hit(mtm, floor)
        pnl = pm.close_all_positions(state, reason="profit_floor_exit")
        state["trade_count"] = state.get("trade_count", 0) + 1
        sm.save_state(state)
        log.info("Run complete.")
        return

    # ── 11. Emergency score exit ─────────────────────────────────────────────
    entry_label = state.get("entry_label")
    if (
        entry_label
        and state.get("positions")
        and confirmed_label  # only act on newly confirmed labels
        and ve.score_emergency_exit(entry_label, score)
    ):
        risky_type = "CE" if entry_label in ("bullish", "very_bullish") else "PE"
        log.warning(f"Emergency exit: entry={entry_label} score={score:+.1f}")
        notifier.alert_emergency_exit(entry_label, score, risky_type)
        pm.close_risky_leg(state, entry_label)
        state["trade_count"] = state.get("trade_count", 0) + 1
        sm.save_state(state)
        return

    # ── 12. Entry logic ──────────────────────────────────────────────────────
    if (
        not state.get("positions")
        and not state.get("entry_label")           # not entered yet today
        and _hm_gte(hm, ENTRY_START_IST)
        and _hm_lte(hm, ENTRY_CUTOFF_IST)
        and confirmed_label is not None            # freshly confirmed
        and state.get("trade_count", 0) < MAX_TRADES_PER_DAY
    ):
        log.info(f"Entry signal: label={confirmed_label} score={score:+.1f}")
        success = pm.open_entry(state, confirmed_label, spot, atm)
        if success:
            state["entry_label"]  = confirmed_label
            state["entry_time"]   = now.strftime("%H:%M")
            state["trade_count"]  = state.get("trade_count", 0) + 1
            # Notify entry
            positions = state.get("positions", [])
            ce_sell = next((l for l in positions if l["opt_type"]=="CE" and l["role"]=="sell"), {})
            pe_sell = next((l for l in positions if l["opt_type"]=="PE" and l["role"]=="sell"), {})
            ce_hedge = next((l for l in positions if l["opt_type"]=="CE" and l["role"]=="hedge"), {})
            pe_hedge = next((l for l in positions if l["opt_type"]=="PE" and l["role"]=="hedge"), {})
            notifier.alert_entry(
                confirmed_label,
                ce_sell.get("strike", 0), ce_sell.get("lots", 0),
                pe_sell.get("strike", 0), pe_sell.get("lots", 0),
                ce_hedge.get("lots", 0), pe_hedge.get("lots", 0),
                spot,
            )
            log.info(f"Entry done: label={confirmed_label} legs={len(positions)}")
        else:
            log.error("Entry failed.")

    # ── 13. 12:00 Add ────────────────────────────────────────────────────────
    elif (
        state.get("positions")
        and not state.get("add_done")
        and hm >= ADD_POSITION_IST
        and hm < FORCE_EXIT_IST
    ):
        added = pm.add_position_1200(state, current_label, spot, atm, mtm)
        if added:
            notifier.alert_rebalance(entry_label or "?", current_label, score)

    # ── 14. Score-triggered rebalance ────────────────────────────────────────
    elif (
        state.get("positions")
        and entry_label
        and confirmed_label is not None             # freshly confirmed new label
        and confirmed_label != entry_label
        and state.get("trade_count", 0) < MAX_TRADES_PER_DAY - 1  # reserve 1 for exit
        and mtm >= 0                                # only rebalance from profit
    ):
        log.info(f"Score adj trigger | entry={entry_label} score={score:+.1f} → rebalance to {confirmed_label}")
        pm.rebalance_position(state, confirmed_label, spot, atm, mtm)
        notifier.alert_rebalance(entry_label, confirmed_label, score)

    # ── Final: log MTM ───────────────────────────────────────────────────────
    if state.get("positions"):
        log.info(f"MTM P&L: ₹{mtm:,.0f}")
        notifier.alert_view(score, current_label, mtm)

    sm.save_state(state)
    log.info("Run complete.")
