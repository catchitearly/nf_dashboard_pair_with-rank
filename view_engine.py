"""
view_engine.py
Computes market view (score + label) from straddle candle VWAP analysis.

Score formula (exact fit from log data):
  For each upper strike above VWAP → bearish signal (farthest has highest weight)
  For each lower strike above VWAP → bullish signal (nearest has highest weight)
  score = BASE + sum(upper_weights[:ua]) negated + sum(lower_weights[:la])

  BASE         = +0.5
  lower_weights = [4, 3, 2, 2]  (nearest ATM-100 first)
  upper_weights = [2, 2, 3, 4]  (farthest ATM+400 first → reversed for counting)

Label requires 2 consecutive identical readings to confirm (prevents whipsaw).
"""

import logging
from typing import Optional

from config import (
    SCORE_BASE, UPPER_WEIGHTS, LOWER_WEIGHTS,
    LABEL_THRESHOLDS, N_UPPER_STRIKES, N_LOWER_STRIKES,
    STRIKE_STEP,
)

log = logging.getLogger(__name__)


# ── VWAP ────────────────────────────────────────────────────────────────────

def compute_vwap(candles: list[dict]) -> float:
    """
    Volume-weighted average price from candle list.
    Uses typical price = (high + low + close) / 3.
    Returns simple close average if volume is all zero.
    """
    total_vol = sum(c.get("volume", 0) for c in candles)
    if total_vol == 0:
        return sum(c["close"] for c in candles) / len(candles)
    num = sum(
        ((c["high"] + c["low"] + c["close"]) / 3) * c.get("volume", 0)
        for c in candles
    )
    return num / total_vol


# ── Score formula ────────────────────────────────────────────────────────────

def _score_from_counts(upper_above: int, lower_above: int) -> float:
    """
    Exact score formula reverse-engineered from log data.
    upper_above: how many of 4 upper straddles have close > VWAP
    lower_above: how many of 4 lower straddles have close > VWAP
    """
    # Upper counted farthest-first (most impactful when all are above)
    upper_contrib = -sum(UPPER_WEIGHTS[:upper_above])
    # Lower counted nearest-first (most impactful when nearest is above)
    lower_contrib =  sum(LOWER_WEIGHTS[:lower_above])
    return SCORE_BASE + upper_contrib + lower_contrib


def _label_from_score(score: float) -> str:
    for label, lo, hi in LABEL_THRESHOLDS:
        if lo <= score < hi:
            return label
    # Fallback
    if score >= LABEL_THRESHOLDS[0][1]:
        return LABEL_THRESHOLDS[0][0]
    return LABEL_THRESHOLDS[-1][0]


# ── Main view check ──────────────────────────────────────────────────────────

def compute_view(
    atm: int,
    straddle_candles: dict[int, list[dict]],
    spot: float,
) -> Optional[dict]:
    """
    Compute score and raw label from straddle VWAP analysis.

    Args:
        atm: ATM strike (fixed at entry).
        straddle_candles: {strike: candles} from fyers_data.get_all_straddle_candles().
        spot: current spot price (used for ATM below/above VWAP check).

    Returns:
        {
            "score": float,
            "label": str,
            "upper_below": int,
            "upper_above": int,
            "lower_below": int,
            "lower_above": int,
            "atm_below_vwap": bool,
        }
        or None on failure.
    """
    try:
        upper_strikes = [atm + (i + 1) * STRIKE_STEP for i in range(N_UPPER_STRIKES)]
        lower_strikes = [atm - (i + 1) * STRIKE_STEP for i in range(N_LOWER_STRIKES)]

        # Count upper straddles above/below their own VWAP
        upper_above = 0
        upper_below = 0
        missing_upper = 0
        for strike in upper_strikes:
            candles = straddle_candles.get(strike)
            if not candles:
                missing_upper += 1
                continue
            vwap  = compute_vwap(candles)
            close = candles[-1]["close"]
            if close > vwap:
                upper_above += 1
            else:
                upper_below += 1

        # Count lower straddles above/below their own VWAP
        lower_above = 0
        lower_below = 0
        missing_lower = 0
        for strike in lower_strikes:
            candles = straddle_candles.get(strike)
            if not candles:
                missing_lower += 1
                continue
            vwap  = compute_vwap(candles)
            close = candles[-1]["close"]
            if close > vwap:
                lower_above += 1
            else:
                lower_below += 1

        if missing_upper + missing_lower > 2:
            log.warning(
                f"Too many missing straddles: "
                f"upper_miss={missing_upper} lower_miss={missing_lower}"
            )
            return None

        # ATM straddle vs its own VWAP
        atm_candles   = straddle_candles.get(atm)
        atm_below_vwap = True
        if atm_candles:
            atm_vwap      = compute_vwap(atm_candles)
            atm_below_vwap = atm_candles[-1]["close"] <= atm_vwap

        score = _score_from_counts(upper_above, lower_above)
        label = _label_from_score(score)

        sign = "+" if score >= 0 else ""
        log.info(
            f"View check | ATM below_vwap={atm_below_vwap} | "
            f"upper_below={upper_below} upper_above={upper_above} | "
            f"lower_above={lower_above} lower_below={lower_below} | "
            f"score={sign}{score} label={label}"
        )

        return {
            "score":        score,
            "label":        label,
            "upper_below":  upper_below,
            "upper_above":  upper_above,
            "lower_below":  lower_below,
            "lower_above":  lower_above,
            "atm_below_vwap": atm_below_vwap,
        }

    except Exception as e:
        log.error(f"compute_view error: {e}")
        return None


# ── Label confirmation (2-of-2) ──────────────────────────────────────────────

def update_label_confirmation(state: dict, new_label: str) -> Optional[str]:
    """
    Implements 2-consecutive-same-label confirmation logic.
    Mutates state["pending_label"], state["pending_count"], state["current_label"].
    Returns the confirmed label if just confirmed, else None.
    """
    try:
        current  = state.get("current_label")
        pending  = state.get("pending_label")
        count    = state.get("pending_count", 0)

        if new_label == current:
            # No change — reset pending
            state["pending_label"] = None
            state["pending_count"] = 0
            return None

        if new_label == pending:
            count += 1
            state["pending_count"] = count
            if count >= 2:
                # Confirmed!
                log.info(f"Label confirmed: {current} → {new_label}")
                state["current_label"] = new_label
                state["pending_label"] = None
                state["pending_count"] = 0
                return new_label
            else:
                log.info(f"Label pending ({count}/2): current={current} pending={new_label}")
                return None
        else:
            # New different pending label
            state["pending_label"] = new_label
            state["pending_count"] = 1
            log.info(f"Label pending (1/2): current={current} pending={new_label}")
            return None

    except Exception as e:
        log.error(f"update_label_confirmation error: {e}")
        return None


def is_opposite_extreme(entry_label: str, current_label: str) -> bool:
    """
    True if current label is an 'opposite extreme' vs entry label.
    Used for emergency exit trigger.
    """
    bearish_side  = {"bearish", "very_bearish"}
    bullish_side  = {"bullish", "very_bullish"}
    if entry_label in bullish_side and current_label in bearish_side:
        return True
    if entry_label in bearish_side and current_label in bullish_side:
        return True
    return False


def score_emergency_exit(entry_label: str, current_score: float) -> bool:
    """
    True if score has reversed enough from entry to trigger emergency leg closure.
    very_bullish entry + score ≤ -3.5 confirmed → exit risky (CE) leg
    very_bearish entry + score ≥ +3.5 confirmed → exit risky (PE) leg
    """
    if entry_label in ("very_bullish", "bullish") and current_score <= -3.5:
        return True
    if entry_label in ("very_bearish", "bearish") and current_score >= 3.5:
        return True
    return False
