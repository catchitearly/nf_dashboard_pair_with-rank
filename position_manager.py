"""
position_manager.py
Handles all position entry, exit, rebalancing and MTM computation.

Design:
  - All Fyers order calls wrapped in try/except
  - Dry-run mode when FYERS credentials missing (logs instead of placing)
  - Each action returns success bool + details dict
  - No state mutation without explicit save_state() call after
"""

import logging
from datetime import datetime, date
from typing import Optional

from config import (
    POSITION_SIZING, MAX_LOTS_PER_SIDE, LOT_SIZE,
    DELTA_SELL_MIN, DELTA_SELL_MAX, HEDGE_STRIKE_OFFSET,
    STRIKE_STEP, DAILY_LOSS_LIMIT, ADD_MAX_FRACTION,
)
import fyers_data as fd
import state_manager as sm

log = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _days_to_expiry() -> float:
    """Approximate days to expiry from today."""
    from config import EXPIRY_DATE
    try:
        expiry = datetime.strptime(EXPIRY_DATE, "%d-%m-%Y").date()
        delta  = (expiry - date.today()).days
        return max(delta, 0.5)
    except Exception:
        return 7.0


def _place_order(
    symbol: str,
    side: int,       # 1 = buy, -1 = sell
    qty: int,
    order_type: int = 2,   # 2 = market
) -> dict | None:
    """
    Place a Fyers order. Returns order response dict or None.
    In dry-run (no credentials) → logs and returns mock response.
    """
    from config import FYERS_CLIENT_ID, FYERS_ACCESS_TOKEN
    dry_run = not FYERS_CLIENT_ID or not FYERS_ACCESS_TOKEN

    payload = {
        "symbol":      symbol,
        "qty":         qty,
        "type":        order_type,
        "side":        side,
        "productType": "INTRADAY",
        "limitPrice":  0,
        "stopPrice":   0,
        "validity":    "DAY",
        "disclosedQty": 0,
        "offlineOrder": False,
    }

    if dry_run:
        log.info(f"[DRY-RUN] Order: {symbol} side={'BUY' if side==1 else 'SELL'} qty={qty}")
        return {"s": "ok", "id": "DRY-RUN", "dry_run": True}

    try:
        fyers = fd.get_fyers()
        if fyers is None:
            log.error("Cannot place order — Fyers not authenticated")
            return None
        resp = fyers.place_order(data=payload)
        if resp.get("s") != "ok":
            log.error(f"Order failed: {resp}")
            return None
        log.info(f"Order placed: {symbol} {'BUY' if side==1 else 'SELL'} {qty} → {resp.get('id')}")
        return resp
    except Exception as e:
        log.error(f"_place_order error ({symbol}): {e}")
        return None


def _get_ltp(symbol: str) -> float:
    """Get current LTP for a symbol. Returns 0.0 on failure."""
    try:
        batch = fd.get_quotes_batch([symbol])
        if batch and symbol in batch:
            return batch[symbol].get("ltp", 0.0)
    except Exception as e:
        log.error(f"_get_ltp error ({symbol}): {e}")
    return 0.0


# ── Entry ────────────────────────────────────────────────────────────────────

def open_entry(state: dict, label: str, spot: float, atm: int) -> bool:
    """
    Open a new strangle position for the given label.
    Returns True on success.
    """
    try:
        sizing = POSITION_SIZING[label]
        sell_ce_lots = sizing["sell_ce"]
        sell_pe_lots = sizing["sell_pe"]
        hedge_ce_lots = sizing["hedge_ce"]
        hedge_pe_lots = sizing["hedge_pe"]

        dte    = _days_to_expiry()
        target = (DELTA_SELL_MIN + DELTA_SELL_MAX) / 2   # ~0.125

        # Find sell strikes
        ce_sell_strike = fd.find_strike_by_delta(spot, "CE", target, dte, atm)
        pe_sell_strike = fd.find_strike_by_delta(spot, "PE", target, dte, atm)

        if ce_sell_strike is None or pe_sell_strike is None:
            log.error("Entry: strike selection failed")
            return False

        # Hedge strikes (further OTM)
        ce_hedge_strike = ce_sell_strike + HEDGE_STRIKE_OFFSET
        pe_hedge_strike = pe_sell_strike - HEDGE_STRIKE_OFFSET

        legs_to_open = [
            (ce_sell_strike,  "CE", "sell", sell_ce_lots,  -1),
            (pe_sell_strike,  "PE", "sell", sell_pe_lots,  -1),
            (ce_hedge_strike, "CE", "hedge", hedge_ce_lots, 1),
            (pe_hedge_strike, "PE", "hedge", hedge_pe_lots, 1),
        ]

        opened = []
        for strike, opt_type, role, lots, side in legs_to_open:
            sym = fd.option_symbol(strike, opt_type)
            ltp = _get_ltp(sym)
            qty = lots * LOT_SIZE
            resp = _place_order(sym, side, qty)
            if resp is None:
                log.error(f"Entry leg failed: {sym} — rolling back")
                # Attempt to close already-opened legs
                for prev_leg in opened:
                    _close_leg(prev_leg, reason="rollback")
                return False
            leg = {
                "symbol":    sym,
                "strike":    strike,
                "opt_type":  opt_type,
                "side":      "sell" if side == -1 else "buy",
                "lots":      lots,
                "entry_ltp": ltp,
                "role":      role,
            }
            sm.add_leg(state, leg)
            opened.append(leg)

        lots_info = state.get("positions", [])
        ce_lots = sum(l["lots"] for l in lots_info if l["opt_type"]=="CE" and l["role"]=="sell")
        pe_lots = sum(l["lots"] for l in lots_info if l["opt_type"]=="PE" and l["role"]=="sell")
        log.info(
            f"Entry | label={label} "
            f"CE {ce_sell_strike}x{sell_ce_lots} d={target:.3f} "
            f"PE {pe_sell_strike}x{sell_pe_lots} d={target:.3f}"
        )
        return True

    except Exception as e:
        log.error(f"open_entry error: {e}")
        return False


# ── MTM ──────────────────────────────────────────────────────────────────────

def compute_mtm(state: dict) -> float:
    """
    Compute live MTM P&L for all open positions.
    MTM = sum over sell legs: (entry_ltp - current_ltp) * lots * LOT_SIZE
        + sum over hedge legs: (current_ltp - entry_ltp) * lots * LOT_SIZE
    In test/backtest mode: returns injected MTM directly (real observed values).
    Returns 0.0 if no positions.
    """
    try:
        # ── Test mode: use injected actual MTM from logs ─────────────────
        try:
            import test_mode
            if test_mode.is_active():
                injected = test_mode.get_injected_mtm()
                if injected is not None and state.get("positions"):
                    total = injected + state.get("closed_pnl", 0.0)
                    log.info(f"MTM P&L: ₹{total:,.0f}")
                    return round(total, 2)
        except ImportError:
            pass

        positions = state.get("positions", [])
        if not positions:
            return 0.0

        symbols = [p["symbol"] for p in positions]
        quotes  = fd.get_quotes_batch(symbols)
        if not quotes:
            log.warning("MTM: batch quotes failed — returning 0")
            return 0.0

        total = 0.0
        for leg in positions:
            sym     = leg["symbol"]
            q       = quotes.get(sym, {})
            cur_ltp = q.get("ltp", leg.get("entry_ltp", 0.0))
            entry   = leg.get("entry_ltp", 0.0)
            lots    = leg.get("lots", 0)
            if leg.get("side") == "sell":
                total += (entry - cur_ltp) * lots * LOT_SIZE
            else:  # hedge buy
                total += (cur_ltp - entry) * lots * LOT_SIZE

        total += state.get("closed_pnl", 0.0)
        log.info(f"MTM P&L: ₹{total:,.0f}")
        return round(total, 2)

    except Exception as e:
        log.error(f"compute_mtm error: {e}")
        return 0.0


# ── Close helpers ────────────────────────────────────────────────────────────

def _close_leg(leg: dict, reason: str = "") -> float:
    """Close a single leg. Returns realised P&L contribution."""
    try:
        sym  = leg["symbol"]
        qty  = leg["lots"] * LOT_SIZE
        side = 1 if leg["side"] == "sell" else -1   # reverse side to close

        ltp  = _get_ltp(sym)
        leg["exit_ltp"] = ltp

        resp = _place_order(sym, side, qty)
        if resp is None:
            log.error(f"Failed to close leg {sym}")
            return 0.0

        pnl = sm._leg_realised_pnl(leg)
        log.info(f"Closed leg {sym} {reason} | lots={leg['lots']} pnl=₹{pnl:.0f}")
        return pnl

    except Exception as e:
        log.error(f"_close_leg error ({leg.get('symbol', '?')}): {e}")
        return 0.0


def close_all_positions(state: dict, reason: str = "exit") -> float:
    """Close all open positions. Returns total realised P&L."""
    try:
        positions = list(state.get("positions", []))
        if not positions:
            return state.get("closed_pnl", 0.0)

        total_pnl = 0.0
        for leg in positions:
            total_pnl += _close_leg(leg, reason=reason)

        state["positions"] = []
        state["closed_pnl"] = state.get("closed_pnl", 0.0) + total_pnl
        log.info(f"Closed all | reason={reason} | P&L: ₹{state['closed_pnl']:,.0f}")
        return state["closed_pnl"]

    except Exception as e:
        log.error(f"close_all_positions error: {e}")
        return 0.0


def close_risky_leg(state: dict, entry_label: str) -> float:
    """
    Close the risky (directional) leg on emergency exit.
    For bullish entry → close CE sell legs + CE hedges.
    For bearish entry → close PE sell legs + PE hedges.
    Returns P&L contribution.
    """
    try:
        bullish_side = {"bullish", "very_bullish"}
        if entry_label in bullish_side:
            risky_type = "CE"
        else:
            risky_type = "PE"

        to_close = [l for l in state.get("positions", []) if l.get("opt_type") == risky_type]
        if not to_close:
            return 0.0

        total_pnl = 0.0
        for leg in to_close:
            total_pnl += _close_leg(leg, reason=f"emergency_{risky_type}_exit")

        state["positions"] = [l for l in state["positions"] if l.get("opt_type") != risky_type]
        state["closed_pnl"] = state.get("closed_pnl", 0.0) + total_pnl
        log.info(f"Risky leg closed | type={risky_type} pnl=₹{total_pnl:.0f}")
        return total_pnl

    except Exception as e:
        log.error(f"close_risky_leg error: {e}")
        return 0.0


# ── 12:00 Add ────────────────────────────────────────────────────────────────

def add_position_1200(state: dict, current_label: str, spot: float, atm: int, mtm: float) -> bool:
    """
    Execute 12:00 IST position add if conditions are met.
    Returns True if add was executed.
    """
    try:
        if state.get("add_done"):
            return False
        if mtm <= 0:
            log.info("12:00 add skipped: MTM not positive")
            return False
        if state.get("trade_count", 0) >= 3:
            log.info("12:00 add skipped: trade count ≥ 3")
            return False

        entry_label = state.get("entry_label", "neutral")
        if current_label != entry_label:
            log.info(f"12:00 add skipped: label changed {entry_label}→{current_label}")
            return False

        # Check lot cap
        live  = sm.get_live_lots(state)
        cur_ce = live["CE_sell"]
        cur_pe = live["PE_sell"]

        sizing = POSITION_SIZING[entry_label]
        add_ce = min(int(sizing["sell_ce"] * ADD_MAX_FRACTION), MAX_LOTS_PER_SIDE - cur_ce)
        add_pe = min(int(sizing["sell_pe"] * ADD_MAX_FRACTION), MAX_LOTS_PER_SIDE - cur_pe)
        add_ce_h = min(int(sizing["hedge_ce"] * ADD_MAX_FRACTION), MAX_LOTS_PER_SIDE - live["CE_hedge"])
        add_pe_h = min(int(sizing["hedge_pe"] * ADD_MAX_FRACTION), MAX_LOTS_PER_SIDE - live["PE_hedge"])

        if add_ce <= 0 and add_pe <= 0:
            log.info(f"12:00 add | key=('{entry_label}','{current_label}') ce=0 pe=0")
            log.info("12:00 add: nothing to add.")
            state["add_done"] = True
            return False

        log.info(f"12:00 add | key=('{entry_label}','{current_label}') ce={add_ce} pe={add_pe}")

        dte    = _days_to_expiry()
        target = (DELTA_SELL_MIN + DELTA_SELL_MAX) / 2

        legs_to_add = []
        if add_ce > 0:
            ce_s = fd.find_strike_by_delta(spot, "CE", target, dte, atm)
            if ce_s:
                legs_to_add.append((ce_s, "CE", "sell", add_ce, -1))
                if add_ce_h > 0:
                    legs_to_add.append((ce_s + HEDGE_STRIKE_OFFSET, "CE", "hedge", add_ce_h, 1))

        if add_pe > 0:
            pe_s = fd.find_strike_by_delta(spot, "PE", target, dte, atm)
            if pe_s:
                legs_to_add.append((pe_s, "PE", "sell", add_pe, -1))
                if add_pe_h > 0:
                    legs_to_add.append((pe_s - HEDGE_STRIKE_OFFSET, "PE", "hedge", add_pe_h, 1))

        opened_legs = 0
        for strike, opt_type, role, lots, side in legs_to_add:
            sym  = fd.option_symbol(strike, opt_type)
            ltp  = _get_ltp(sym)
            qty  = lots * LOT_SIZE
            resp = _place_order(sym, side, qty)
            if resp:
                sm.add_leg(state, {
                    "symbol": sym, "strike": strike, "opt_type": opt_type,
                    "side": "sell" if side == -1 else "buy",
                    "lots": lots, "entry_ltp": ltp, "role": role,
                })
                opened_legs += 1

        state["add_done"]    = True
        state["trade_count"] = state.get("trade_count", 0) + 1
        log.info(f"12:00 add done: {opened_legs} legs | adj={state.get('adj_count', 0) + 1}")
        return True

    except Exception as e:
        log.error(f"add_position_1200 error: {e}")
        return False


# ── Rebalance on label change ────────────────────────────────────────────────

def rebalance_position(state: dict, new_label: str, spot: float, atm: int, mtm: float) -> bool:
    """
    Rebalance open sell positions to match new_label sizing.
    Closes excess lots, adds missing lots.
    Returns True if any change was made.
    """
    try:
        from view_engine import score_emergency_exit
        entry_label = state.get("entry_label", "neutral")

        if mtm < -500:
            log.info("Rebalance skipped: MTM deeply negative")
            return False
        if state.get("trade_count", 0) >= 3:
            log.info("Rebalance skipped: trade count ≥ 3")
            return False

        live    = sm.get_live_lots(state)
        cur_ce  = live["CE_sell"]
        cur_pe  = live["PE_sell"]

        target  = POSITION_SIZING[new_label]
        tgt_ce  = min(target["sell_ce"], MAX_LOTS_PER_SIDE)
        tgt_pe  = min(target["sell_pe"], MAX_LOTS_PER_SIDE)

        add_ce  = max(0, tgt_ce - cur_ce)
        add_pe  = max(0, tgt_pe - cur_pe)
        close_ce = max(0, cur_ce - tgt_ce)
        close_pe = max(0, cur_pe - tgt_pe)

        log.info(
            f"Rebalance | label={new_label} | "
            f"cur CE={cur_ce} PE={cur_pe} | "
            f"target CE={tgt_ce} PE={tgt_pe} | "
            f"add CE={add_ce} PE={add_pe} | "
            f"close CE={close_ce} PE={close_pe}"
        )

        if add_ce == 0 and add_pe == 0 and close_ce == 0 and close_pe == 0:
            log.info("Adjustment triggered but no change needed.")
            state["trade_count"] = state.get("trade_count", 0) + 1
            return False

        changed = False

        # Close excess sell lots (take least-profitable first)
        if close_ce > 0:
            ce_sells = [l for l in state["positions"] if l["opt_type"]=="CE" and l["role"]=="sell"]
            ce_sells.sort(key=lambda l: l.get("entry_ltp", 0))   # lowest premium first
            to_close = ce_sells[:close_ce]
            for leg in to_close:
                _close_leg(leg, reason="rebalance_close_ce")
            state["positions"] = [l for l in state["positions"] if l not in to_close]
            state["closed_pnl"] = state.get("closed_pnl", 0.0) + sum(
                sm._leg_realised_pnl(l) for l in to_close
            )
            log.info(f"Closed {close_ce} CE sell lots")
            changed = True

        if close_pe > 0:
            pe_sells = [l for l in state["positions"] if l["opt_type"]=="PE" and l["role"]=="sell"]
            pe_sells.sort(key=lambda l: l.get("entry_ltp", 0))
            to_close = pe_sells[:close_pe]
            for leg in to_close:
                _close_leg(leg, reason="rebalance_close_pe")
            state["positions"] = [l for l in state["positions"] if l not in to_close]
            state["closed_pnl"] = state.get("closed_pnl", 0.0) + sum(
                sm._leg_realised_pnl(l) for l in to_close
            )
            log.info(f"Closed {close_pe} PE sell lots")
            changed = True

        # Add missing lots
        dte    = _days_to_expiry()
        t_delta = (DELTA_SELL_MIN + DELTA_SELL_MAX) / 2

        for opt_type, add_lots, hedge_lots_add in [
            ("CE", add_ce, target["hedge_ce"] - live["CE_hedge"]),
            ("PE", add_pe, target["hedge_pe"] - live["PE_hedge"]),
        ]:
            if add_lots <= 0:
                continue
            direction = 1 if opt_type == "CE" else -1
            s = fd.find_strike_by_delta(spot, opt_type, t_delta, dte, atm)
            if s is None:
                log.warning(f"Rebalance: could not find {opt_type} strike")
                continue
            sym = fd.option_symbol(s, opt_type)
            ltp = _get_ltp(sym)
            resp = _place_order(sym, -1, add_lots * LOT_SIZE)
            if resp:
                sm.add_leg(state, {
                    "symbol": sym, "strike": s, "opt_type": opt_type,
                    "side": "sell", "lots": add_lots,
                    "entry_ltp": ltp, "role": "sell",
                })
                changed = True
                # Add hedge if needed
                hedge_add = max(0, min(hedge_lots_add, MAX_LOTS_PER_SIDE - live.get(f"{opt_type}_hedge", 0)))
                if hedge_add > 0:
                    h_strike = s + direction * HEDGE_STRIKE_OFFSET
                    h_sym    = fd.option_symbol(h_strike, opt_type)
                    h_ltp    = _get_ltp(h_sym)
                    h_resp   = _place_order(h_sym, 1, hedge_add * LOT_SIZE)
                    if h_resp:
                        sm.add_leg(state, {
                            "symbol": h_sym, "strike": h_strike, "opt_type": opt_type,
                            "side": "buy", "lots": hedge_add,
                            "entry_ltp": h_ltp, "role": "hedge",
                        })

        adj = state.get("adj_count", 0) + 1
        state["adj_count"]   = adj
        state["trade_count"] = state.get("trade_count", 0) + 1
        added_legs = add_ce + add_pe
        closed_legs = int(close_ce > 0) + int(close_pe > 0)
        log.info(f"Adjustment done: +{added_legs} legs, {closed_legs} closed | adj={adj}")
        return changed

    except Exception as e:
        log.error(f"rebalance_position error: {e}")
        return False
