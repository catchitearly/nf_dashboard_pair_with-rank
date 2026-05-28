"""
notifier.py
Telegram notification layer.
All functions are fire-and-forget — never raise to caller.
"""

import logging
import urllib.request
import urllib.parse
import json

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)


def _send(message: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.debug(f"Telegram not configured. Message: {message}")
        return False
    try:
        url    = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        params = urllib.parse.urlencode({
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=params, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            return body.get("ok", False)
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")
        return False


# ── Formatted alert builders ─────────────────────────────────────────────────

def alert_entry(label: str, ce_strike: int, ce_lots: int, pe_strike: int, pe_lots: int,
                ce_hedge: int, pe_hedge: int, spot: float) -> None:
    msg = (
        f"🟢 <b>ENTRY</b>\n"
        f"Label: <b>{label.upper()}</b> | Spot: {spot:.0f}\n"
        f"Sell CE {ce_strike} × {ce_lots}L | Sell PE {pe_strike} × {pe_lots}L\n"
        f"Hedge CE {ce_strike+300} × {ce_hedge}L | Hedge PE {pe_strike-300} × {pe_hedge}L"
    )
    _send(msg)


def alert_exit(reason: str, pnl: float, peak_mtm: float) -> None:
    emoji = "✅" if pnl >= 0 else "🔴"
    msg   = (
        f"{emoji} <b>EXIT</b> — {reason}\n"
        f"P&amp;L: ₹{pnl:,.0f} | Peak was: ₹{peak_mtm:,.0f}"
    )
    _send(msg)


def alert_profit_floor_set(peak: float, floor: float) -> None:
    msg = (
        f"🔒 <b>Profit Floor Set</b>\n"
        f"Peak: ₹{peak:,.0f} → Floor: ₹{floor:,.0f}"
    )
    _send(msg)


def alert_profit_floor_hit(mtm: float, floor: float) -> None:
    msg = (
        f"⚠️ <b>Profit Floor HIT → Exiting</b>\n"
        f"Current MTM: ₹{mtm:,.0f} | Floor: ₹{floor:,.0f}"
    )
    _send(msg)


def alert_rebalance(old_label: str, new_label: str, score: float) -> None:
    msg = (
        f"♻️ <b>Rebalance</b>\n"
        f"Label: {old_label} → <b>{new_label}</b> (score {score:+.1f})"
    )
    _send(msg)


def alert_daily_loss_limit(mtm: float) -> None:
    msg = (
        f"🛑 <b>Daily Loss Limit Hit</b>\n"
        f"MTM: ₹{mtm:,.0f} — trading stopped for today."
    )
    _send(msg)


def alert_emergency_exit(label: str, score: float, leg_type: str) -> None:
    msg = (
        f"🚨 <b>Emergency Exit</b>\n"
        f"Entry={label} | Score={score:+.1f} | Closing {leg_type} leg"
    )
    _send(msg)


def alert_view(score: float, label: str, mtm: float) -> None:
    emoji = {
        "very_bullish": "🚀", "bullish": "📈",
        "neutral": "➡️", "bearish": "📉", "very_bearish": "💥"
    }.get(label, "❓")
    msg = (
        f"{emoji} Score: {score:+.1f} | {label} | MTM: ₹{mtm:,.0f}"
    )
    _send(msg)


def alert_error(context: str, error: str) -> None:
    msg = f"❌ <b>Error</b> in {context}\n{error[:200]}"
    _send(msg)
