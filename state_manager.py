"""
state_manager.py
Persists all strategy state to a JSON file between GitHub Actions runs.
On load error → returns fresh empty state (safe restart).
On save error → logs but never raises (crash-safe).
"""

import json
import logging
import os
from datetime import date
from typing import Any

from config import STATE_DIR, STATE_FILE

log = logging.getLogger(__name__)

# ── Default fresh state ─────────────────────────────────────────────────────

def _fresh_state() -> dict:
    return {
        "date":           str(date.today()),
        "atm":            None,            # fixed at entry
        "entry_label":    None,            # confirmed label at entry
        "entry_time":     None,            # IST string "HH:MM"
        "trade_count":    0,               # trades used today
        "add_done":       False,           # 12:00 add already executed
        "pending_label":  None,            # label waiting for confirmation
        "pending_count":  0,               # how many consecutive times seen
        "current_label":  None,            # last confirmed label
        "peak_mtm":       0.0,             # highest MTM seen today
        "profit_floor":   None,            # current profit lock floor
        "daily_stopped":  False,           # daily loss limit hit
        "positions":      [],              # list of leg dicts
        "closed_pnl":     0.0,             # realised P&L from closed legs
        "adj_count":      0,               # rebalance count today
        "last_adj_time":  None,            # IST string of last rebalance
    }


# ── Public API ──────────────────────────────────────────────────────────────

def load_state() -> dict:
    """Load state from file. Returns fresh state on any error."""
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        if not os.path.exists(STATE_FILE):
            log.info("No state file found — fresh start.")
            return _fresh_state()

        with open(STATE_FILE, "r") as f:
            raw = f.read().strip()

        if not raw:
            log.info("Empty state file — fresh start.")
            return _fresh_state()

        state = json.loads(raw)

        # If it's a new trading day, reset
        today = str(date.today())
        if state.get("date") != today:
            log.info(f"New trading day: {today}")
            return _fresh_state()

        log.info("State loaded successfully.")
        return state

    except json.JSONDecodeError as e:
        log.error(f"load_state JSON error: {e} – fresh start.")
        return _fresh_state()
    except Exception as e:
        log.error(f"load_state error: {e} – fresh start.")
        return _fresh_state()


def save_state(state: dict) -> bool:
    """Save state to file. Returns True on success, False on failure."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        # Write to tmp then rename for atomicity
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, STATE_FILE)
        return True
    except Exception as e:
        log.error(f"save_state error: {e}")
        return False


def reset_state() -> dict:
    """Force a fresh state and save it."""
    state = _fresh_state()
    save_state(state)
    log.info("State reset to fresh.")
    return state


# ── Position helpers ────────────────────────────────────────────────────────

def add_leg(state: dict, leg: dict) -> None:
    """
    Add a position leg.
    leg = {
        "symbol":   "NSE:NIFTY2660224100CE",
        "strike":   24100,
        "opt_type": "CE",
        "side":     "sell" | "buy",
        "lots":     4,
        "entry_ltp": 25.0,
        "role":     "sell" | "hedge",
    }
    """
    state["positions"].append(leg)


def remove_legs(state: dict, legs: list[dict]) -> float:
    """
    Remove legs from state, compute realised P&L contribution.
    Returns total realised P&L of removed legs.
    Modifies state in place.
    """
    symbols_to_remove = {id(l) for l in legs}
    pnl = 0.0
    new_positions = []
    for leg in state["positions"]:
        if id(leg) in symbols_to_remove:
            pnl += _leg_realised_pnl(leg)
        else:
            new_positions.append(leg)
    state["positions"] = new_positions
    state["closed_pnl"] = state.get("closed_pnl", 0.0) + pnl
    return pnl


def _leg_realised_pnl(leg: dict) -> float:
    """Estimate realised P&L for a closed leg using exit_ltp if set."""
    entry  = leg.get("entry_ltp", 0.0)
    exit_  = leg.get("exit_ltp", entry)   # if not set, assume flat
    lots   = leg.get("lots", 0)
    side   = leg.get("side", "sell")
    from config import LOT_SIZE
    if side == "sell":
        return (entry - exit_) * lots * LOT_SIZE
    else:  # buy/hedge
        return (exit_ - entry) * lots * LOT_SIZE


def get_live_lots(state: dict) -> dict[str, int]:
    """Return {CE_sell, PE_sell, CE_hedge, PE_hedge} lot counts from open positions."""
    counts = {"CE_sell": 0, "PE_sell": 0, "CE_hedge": 0, "PE_hedge": 0}
    for leg in state.get("positions", []):
        otype = leg.get("opt_type", "")
        role  = leg.get("role", "sell")
        key   = f"{otype}_{role}"
        if key in counts:
            counts[key] += leg.get("lots", 0)
    return counts


def update_peak_and_floor(state: dict, current_mtm: float) -> None:
    """Update peak_mtm and profit_floor based on current MTM."""
    from config import PROFIT_LOCK_TIERS
    if current_mtm > state.get("peak_mtm", 0.0):
        state["peak_mtm"] = current_mtm

    peak = state["peak_mtm"]
    floor = None
    for threshold, fraction in PROFIT_LOCK_TIERS:
        if peak >= threshold:
            floor = peak * fraction
            break

    if floor is not None:
        existing = state.get("profit_floor")
        if existing is None or floor > existing:
            state["profit_floor"] = floor


def is_profit_floor_breached(state: dict, current_mtm: float) -> bool:
    """True if current MTM has fallen below the profit lock floor."""
    floor = state.get("profit_floor")
    if floor is None:
        return False
    return current_mtm < floor
