"""
test_mode.py
Patches fyers_data module to replay historical snapshots.

Usage:
    from test_mode import activate_test_mode
    activate_test_mode("2026-05-25", tick_index=5)
    # Now all fyers_data calls return data from the 5th snapshot of that date.

The patch replaces:
  - get_spot_price()          → returns snapshot spot
  - get_all_straddle_candles()→ returns synthetic candles matching ua/la counts
  - get_quotes_batch()        → returns synthetic LTP for MTM computation
  - get_915_candle_close()    → returns first snapshot spot

Synthetic MTM:
  During backtest we use the actual observed MTM from logs directly.
  The test mode injects a synthetic MTM via a module-level variable
  that position_manager.compute_mtm() reads when in test mode.
"""

import logging
import sys
from typing import Optional

log = logging.getLogger(__name__)

# ── Test mode state ──────────────────────────────────────────────────────────
_test_date:     Optional[str]  = None
_tick_index:    int            = 0
_injected_mtm:  Optional[float] = None   # set externally to override MTM
_snapshots:     list[dict]     = []


def is_active() -> bool:
    return _test_date is not None


def get_current_snapshot() -> Optional[dict]:
    if not _snapshots or _tick_index >= len(_snapshots):
        return None
    return _snapshots[_tick_index]


def set_tick(index: int) -> None:
    global _tick_index
    _tick_index = index


def set_injected_mtm(mtm: float) -> None:
    global _injected_mtm
    _injected_mtm = mtm


def get_injected_mtm() -> Optional[float]:
    return _injected_mtm


# ── Synthetic candle builder ─────────────────────────────────────────────────

def _make_synthetic_straddle(want_above: bool, base: float = 100.0, n: int = 6) -> list[dict]:
    """
    Return a candle list whose final close is above or below VWAP.
    Uses a sequence of history candles near base, then a final candle
    pushed 5% above or below the history VWAP.
    """
    ts = 1_700_000_000
    candles = []
    for i in range(n):
        p = base + (i - n // 2) * 0.3
        candles.append({
            "ts": ts + i * 300,
            "open": p, "high": p + 0.5,
            "low":  p - 0.5, "close": p,
            "volume": 1000,
        })

    # Compute current VWAP of history
    total_vol = sum(c["volume"] for c in candles)
    vwap = sum(((c["high"]+c["low"]+c["close"])/3)*c["volume"] for c in candles) / total_vol

    final = vwap * 1.06 if want_above else vwap * 0.94
    candles.append({
        "ts": ts + n * 300,
        "open": final, "high": final + 0.3,
        "low":  final - 0.3, "close": final,
        "volume": 1000,
    })
    return candles


def _build_straddle_candles(atm: int, upper_above: int, lower_above: int) -> dict[int, list[dict]]:
    """
    Build synthetic straddle candles matching the desired ua/la counts.
    upper_above: how many of 4 upper strikes (ATM+100..+400) are above VWAP
                 counted farthest-first (ATM+400, +300, +200, +100)
    lower_above: how many of 4 lower strikes (ATM-100..-400) are above VWAP
                 counted nearest-first (ATM-100, -200, -300, -400)
    """
    from config import STRIKE_STEP, N_UPPER_STRIKES, N_LOWER_STRIKES

    # Upper strikes: ATM+100, +200, +300, +400
    upper_strikes = [atm + (i + 1) * STRIKE_STEP for i in range(N_UPPER_STRIKES)]
    # Lower strikes: ATM-100, -200, -300, -400
    lower_strikes = [atm - (i + 1) * STRIKE_STEP for i in range(N_LOWER_STRIKES)]

    # upper_above counted farthest-first: farthest ua strikes are above VWAP
    # So reverse the list to mark farthest first
    upper_above_set = set(upper_strikes[::-1][:upper_above])   # last N in reversed = farthest N
    # lower_above counted nearest-first: nearest la strikes are above VWAP
    lower_above_set = set(lower_strikes[:lower_above])         # first N = nearest N

    result = {}

    for strike in upper_strikes:
        want_above = strike in upper_above_set
        result[strike] = _make_synthetic_straddle(want_above, base=50.0)

    for strike in lower_strikes:
        want_above = strike in lower_above_set
        result[strike] = _make_synthetic_straddle(want_above, base=50.0)

    # ATM straddle — make it below VWAP (atm_below_vwap=True as seen in all logs)
    result[atm] = _make_synthetic_straddle(False, base=200.0)

    return result


# ── Activation ───────────────────────────────────────────────────────────────

def activate_test_mode(date_str: str, tick_index: int = 0) -> None:
    """
    Activate test mode for a specific historical date.
    Patches fyers_data module functions in-place.
    """
    global _test_date, _tick_index, _snapshots

    from backtest_data import HISTORICAL_DATA, ATM_BY_DATE
    if date_str not in HISTORICAL_DATA:
        raise ValueError(
            f"No historical data for {date_str}. "
            f"Available: {list(HISTORICAL_DATA.keys())}"
        )

    _test_date  = date_str
    _tick_index = tick_index
    _snapshots  = HISTORICAL_DATA[date_str]
    atm         = ATM_BY_DATE[date_str]

    log.info(f"[TEST MODE] Activated for {date_str} | tick={tick_index} | atm={atm}")

    # ── Patch fyers_data ─────────────────────────────────────────────────────
    import fyers_data as fd

    def _test_get_spot_price() -> Optional[float]:
        snap = get_current_snapshot()
        if snap is None:
            return None
        log.info(f"[TEST] Spot: {snap['spot']}")
        return snap["spot"]

    def _test_get_915_candle_close() -> Optional[float]:
        if not _snapshots:
            return None
        close = _snapshots[0]["spot"]
        log.info(f"[TEST] 9:15 candle close: {close}")
        return close

    def _test_get_all_straddle_candles(atm_: int) -> Optional[dict]:
        snap = get_current_snapshot()
        if snap is None:
            return None
        ua = snap.get("upper_above", 0)
        la = snap.get("lower_above", 0)
        candles = _build_straddle_candles(atm_, ua, la)
        # Log bar count like real code
        for strike, c in candles.items():
            log.info(f"[TEST] Straddle {strike}: {len(c)} bars")
        return candles

    def _test_get_straddle_candles(strike: int) -> Optional[list]:
        snap = get_current_snapshot()
        if snap is None:
            return None
        # Determine above/below for this specific strike
        atm_ = atm
        ua = snap.get("upper_above", 0)
        la = snap.get("lower_above", 0)
        all_candles = _build_straddle_candles(atm_, ua, la)
        return all_candles.get(strike)

    def _test_get_quotes_batch(symbols: list[str]) -> Optional[dict]:
        """
        Return synthetic quotes.
        MTM is driven by _injected_mtm if set.
        Otherwise return entry price (no P&L change).
        """
        result = {}
        for sym in symbols:
            result[sym] = {
                "ltp":    50.0,
                "bid":    49.5,
                "ask":    50.5,
                "volume": 10000,
                "delta":  0.12,
                "greeks_source": "test",
            }
        return result

    def _test_find_strike_by_delta(spot, opt_type, target_delta, dte, atm_arg, search_range=20):
        """Return a reasonable OTM strike based on spot."""
        from config import STRIKE_STEP
        direction = 1 if opt_type == "CE" else -1
        # ~500 pts OTM
        strike = int(round(spot / STRIKE_STEP) * STRIKE_STEP) + direction * 5 * STRIKE_STEP
        ltp = 30.0
        log.info(f"[TEST] Selected {opt_type} {strike} | delta={target_delta:.3f} ltp={ltp} dist={abs(strike - atm_arg)}pts")
        return strike

    def _test_place_order(symbol, side, qty, order_type=2):
        action = "BUY" if side == 1 else "SELL"
        log.info(f"[TEST ORDER] {action} {symbol} qty={qty}")
        return {"s": "ok", "id": f"TEST-{symbol}", "test": True}

    # Apply patches
    fd.get_spot_price             = _test_get_spot_price
    fd.get_915_candle_close       = _test_get_915_candle_close
    fd.get_all_straddle_candles   = _test_get_all_straddle_candles
    fd.get_straddle_candles       = _test_get_straddle_candles
    fd.get_quotes_batch           = _test_get_quotes_batch
    fd.find_strike_by_delta       = _test_find_strike_by_delta

    # Patch _place_order inside position_manager
    import position_manager as pm
    pm._place_order = _test_place_order

    log.info(f"[TEST MODE] fyers_data patched — {len(_snapshots)} ticks available")


def deactivate_test_mode() -> None:
    """Reset test mode — useful between backtest runs."""
    global _test_date, _tick_index, _snapshots, _injected_mtm
    _test_date    = None
    _tick_index   = 0
    _snapshots    = []
    _injected_mtm = None
    log.info("[TEST MODE] Deactivated")
