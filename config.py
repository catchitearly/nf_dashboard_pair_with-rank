"""
config.py
All constants and environment variables.
Update EXPIRY_DATE + EXPIRY_STR each expiry week.
"""

import os

# ── Fyers credentials ──────────────────────────────────────────────────────
FYERS_CLIENT_ID    = os.environ.get("FYERS_CLIENT_ID", "")
FYERS_ACCESS_TOKEN = os.environ.get("FYERS_ACCESS_TOKEN", "")

# ── Telegram ───────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Instrument ─────────────────────────────────────────────────────────────
INDEX_SYMBOL  = "NSE:NIFTY50-INDEX"
EXPIRY_DATE   = "02-06-2026"
EXPIRY_STR    = "26602"        # Format used in Fyers option symbols
INTERVAL      = "5"            # 5-minute candles
STRIKE_STEP   = 100
LOT_SIZE      = 75             # Nifty lot size

# ── View engine ────────────────────────────────────────────────────────────
# How many strikes above and below ATM to watch
N_UPPER_STRIKES = 4            # ATM+100 … ATM+400
N_LOWER_STRIKES = 4            # ATM-100 … ATM-400

# Score weights (reverse-engineered from logs — exact fit verified)
# Upper strikes counted farthest-first when above VWAP (bearish signal)
UPPER_WEIGHTS   = [2, 2, 3, 4]      # farthest→nearest weight when above VWAP
# Lower strikes counted nearest-first when above VWAP (bullish signal)
LOWER_WEIGHTS   = [4, 3, 2, 2]      # nearest→farthest weight when above VWAP
SCORE_BASE      = 0.5

# Label thresholds
LABEL_THRESHOLDS = [
    ("very_bullish", 7.5,  float("inf")),
    ("bullish",      3.5,  7.5),
    ("neutral",     -1.5,  3.5),
    ("bearish",     -6.5, -1.5),
    ("very_bearish", float("-inf"), -6.5),
]

# ── Strategy timing ────────────────────────────────────────────────────────
ENTRY_START_IST     = (9, 45)    # (hour, minute)
ENTRY_CUTOFF_IST    = (13, 0)
FORCE_EXIT_IST      = (14, 30)
ADD_POSITION_IST    = (12, 0)    # 12:00 add trigger

# ── Position sizing (lots per side) ───────────────────────────────────────
# sell_ce, sell_pe, hedge_ce_buy, hedge_pe_buy
POSITION_SIZING = {
    "very_bullish": {"sell_ce": 4,  "sell_pe": 8,  "hedge_ce": 1, "hedge_pe": 2},
    "bullish":      {"sell_ce": 4,  "sell_pe": 6,  "hedge_ce": 1, "hedge_pe": 2},
    "neutral":      {"sell_ce": 6,  "sell_pe": 6,  "hedge_ce": 2, "hedge_pe": 2},
    "bearish":      {"sell_ce": 6,  "sell_pe": 4,  "hedge_ce": 2, "hedge_pe": 1},
    "very_bearish": {"sell_ce": 8,  "sell_pe": 4,  "hedge_ce": 2, "hedge_pe": 1},
}
MAX_LOTS_PER_SIDE   = 20
MAX_TRADES_PER_DAY  = 4
ADD_MAX_FRACTION    = 0.5        # Add at most 50% of original size

# ── Profit lock floors ─────────────────────────────────────────────────────
PROFIT_LOCK_TIERS = [
    (1500, 0.75),
    (1000, 0.65),
    (500,  0.50),
    (300,  0.30),
]   # (peak_threshold, floor_fraction) — checked highest first

# ── Risk limits ────────────────────────────────────────────────────────────
DAILY_LOSS_LIMIT    = -1000      # Stop trading if MTM hits this
DELTA_SELL_MIN      = 0.10
DELTA_SELL_MAX      = 0.15
HEDGE_STRIKE_OFFSET = 300        # pts further OTM for hedge buy

# ── Trading mode ───────────────────────────────────────────────────────────
# Set PAPER_TRADING = True  → no real orders, uses live market prices for MTM
# Set PAPER_TRADING = False → live trading (real orders placed via Fyers)
PAPER_TRADING = True

# ── State / log paths ──────────────────────────────────────────────────────
STATE_DIR   = "state"
STATE_FILE  = "state/trading_state.json"
LOG_DIR     = "logs"
