"""
dashboard_gen.py
Generates docs/index.html — a self-contained, auto-refreshing dashboard
served by GitHub Pages.  Called at the end of every strategy cycle.

The HTML has zero external dependencies (no CDN), so it loads instantly.
Charts are drawn with inline SVG.  Page auto-refreshes every 5 minutes.
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

log = logging.getLogger(__name__)

DOCS_DIR  = "docs"
HTML_FILE = os.path.join(DOCS_DIR, "index.html")
IST       = timezone(timedelta(hours=5, minutes=30))

# Label → colour
LABEL_COLOURS = {
    "very_bullish": "#3fb950",
    "bullish":      "#2ea043",
    "neutral":      "#8b949e",
    "bearish":      "#d29922",
    "very_bearish": "#f85149",
}

LABEL_BG = {
    "very_bullish": "#0f2a1a",
    "bullish":      "#0f2216",
    "neutral":      "#1a1f28",
    "bearish":      "#2a1506",
    "very_bearish": "#2a0a0a",
}


# ── HTML helpers ─────────────────────────────────────────────────────────────

def _esc(s: Any) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_pnl(v: float) -> str:
    sign  = "+" if v >= 0 else ""
    colour = "#3fb950" if v >= 0 else "#f85149"
    return f'<span style="color:{colour};font-weight:700">{sign}₹{v:,.0f}</span>'


def _label_badge(label: str) -> str:
    if not label:
        return '<span style="color:#8b949e">—</span>'
    c  = LABEL_COLOURS.get(label, "#8b949e")
    bg = LABEL_BG.get(label, "#1a1f28")
    return (
        f'<span style="background:{bg};color:{c};border:1px solid {c};'
        f'padding:2px 10px;border-radius:20px;font-size:12px;font-weight:600">'
        f'{_esc(label)}</span>'
    )


# ── SVG sparklines ────────────────────────────────────────────────────────────

def _score_sparkline(history: list[dict], width: int = 700, height: int = 120) -> str:
    """SVG line chart for score history."""
    if not history:
        return f'<svg width="{width}" height="{height}"><text x="50%" y="50%" fill="#8b949e" text-anchor="middle" font-size="13">No data yet</text></svg>'

    scores = [h["score"] for h in history]
    times  = [h["time"]  for h in history]
    n      = len(scores)

    ymin, ymax = -12, 13
    pad_l, pad_r, pad_t, pad_b = 40, 10, 10, 28
    cw = width - pad_l - pad_r
    ch = height - pad_t - pad_b

    def xp(i):   return pad_l + i * cw / max(n - 1, 1)
    def yp(v):   return pad_t + ch - (v - ymin) / (ymax - ymin) * ch

    # Grid lines
    grid = ""
    for yv in [-10.5, -3.5, 0.5, 4.5, 9.5]:
        yc = yp(yv)
        grid += f'<line x1="{pad_l}" y1="{yc:.1f}" x2="{pad_l+cw}" y2="{yc:.1f}" stroke="#21262d" stroke-width="1"/>'
        label_map = {-10.5: "v.bear", -3.5: "bear", 0.5: "neut", 4.5: "bull", 9.5: "v.bull"}
        lbl = label_map.get(yv, "")
        grid += f'<text x="{pad_l-3}" y="{yc+3:.1f}" fill="#8b949e" font-size="9" text-anchor="end">{lbl}</text>'

    # Zero line
    y0 = yp(0)
    grid += f'<line x1="{pad_l}" y1="{y0:.1f}" x2="{pad_l+cw}" y2="{y0:.1f}" stroke="#30363d" stroke-width="1.5" stroke-dasharray="4,3"/>'

    # Coloured polyline segments
    segs = ""
    for i in range(1, n):
        x1, y1 = xp(i-1), yp(scores[i-1])
        x2, y2 = xp(i),   yp(scores[i])
        c = LABEL_COLOURS.get(history[i]["label"], "#8b949e")
        segs += f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{c}" stroke-width="2"/>'

    # Dots
    dots = ""
    for i, (s, h) in enumerate(zip(scores, history)):
        c = LABEL_COLOURS.get(h["label"], "#8b949e")
        dots += f'<circle cx="{xp(i):.1f}" cy="{yp(s):.1f}" r="3" fill="{c}"/>'

    # X-axis labels (every ~8 ticks)
    xlbls = ""
    step  = max(1, n // 8)
    for i in range(0, n, step):
        xlbls += f'<text x="{xp(i):.1f}" y="{height-4}" fill="#8b949e" font-size="9" text-anchor="middle">{times[i]}</text>'

    return (
        f'<svg width="100%" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'{grid}{segs}{dots}{xlbls}'
        f'</svg>'
    )


def _mtm_sparkline(history: list[dict], width: int = 700, height: int = 100) -> str:
    """SVG area chart for MTM history."""
    mtms = [h.get("mtm", 0) for h in history]
    if not mtms or all(m == 0 for m in mtms):
        return f'<svg width="{width}" height="{height}"><text x="50%" y="50%" fill="#8b949e" text-anchor="middle" font-size="13">No P&amp;L data yet</text></svg>'

    n    = len(mtms)
    ymin = min(mtms) * 1.1 if min(mtms) < 0 else -abs(max(mtms)) * 0.1
    ymax = max(mtms) * 1.1 if max(mtms) > 0 else abs(min(mtms)) * 0.1
    if ymin == ymax: ymax = ymin + 1

    pad_l, pad_r, pad_t, pad_b = 55, 10, 10, 25
    cw = width - pad_l - pad_r
    ch = height - pad_t - pad_b

    def xp(i): return pad_l + i * cw / max(n-1, 1)
    def yp(v): return pad_t + ch - (v - ymin) / (ymax - ymin) * ch

    # zero line
    y0   = yp(0)
    zero = f'<line x1="{pad_l}" y1="{y0:.1f}" x2="{pad_l+cw}" y2="{y0:.1f}" stroke="#30363d" stroke-width="1.5" stroke-dasharray="4,3"/>'

    # area fill
    pts  = " ".join(f"{xp(i):.1f},{yp(v):.1f}" for i, v in enumerate(mtms))
    area = (
        f'<polyline points="{pts}" fill="none" stroke="#58a6ff" stroke-width="2"/>'
        f'<polygon points="{pts} {xp(n-1):.1f},{y0:.1f} {xp(0):.1f},{y0:.1f}" '
        f'fill="rgba(88,166,255,0.07)"/>'
    )

    # Y labels
    ylbls = ""
    for yv in [ymin, (ymin+ymax)/2, ymax]:
        yc = yp(yv)
        sign = "+" if yv >= 0 else ""
        ylbls += f'<text x="{pad_l-4}" y="{yc+4:.1f}" fill="#8b949e" font-size="9" text-anchor="end">{sign}₹{yv:,.0f}</text>'

    # X labels
    times  = [h.get("time","") for h in history]
    step   = max(1, n // 6)
    xlbls  = ""
    for i in range(0, n, step):
        xlbls += f'<text x="{xp(i):.1f}" y="{height-4}" fill="#8b949e" font-size="9" text-anchor="middle">{times[i]}</text>'

    return (
        f'<svg width="100%" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
        f'{zero}{area}{ylbls}{xlbls}</svg>'
    )


# ── Positions table ───────────────────────────────────────────────────────────

def _positions_table(positions: list[dict], quotes: dict) -> str:
    if not positions:
        return '<p style="color:#8b949e;text-align:center;padding:20px">No open positions</p>'

    rows = ""
    for leg in positions:
        sym    = leg.get("symbol", "")
        short  = sym.split(":")[-1] if ":" in sym else sym
        side   = leg.get("side", "")
        role   = leg.get("role", "")
        lots   = leg.get("lots", 0)
        entry  = leg.get("entry_ltp", 0.0)
        cur    = quotes.get(sym, {}).get("ltp", entry)
        otype  = leg.get("opt_type", "")

        if side == "sell":
            leg_pnl = (entry - cur) * lots * 75
        else:
            leg_pnl = (cur - entry) * lots * 75

        pnl_colour = "#3fb950" if leg_pnl >= 0 else "#f85149"
        sign_pnl   = "+" if leg_pnl >= 0 else ""
        role_badge = (
            '<span style="background:#0a1929;color:#58a6ff;border:1px solid #1f6feb;'
            'padding:1px 6px;border-radius:10px;font-size:10px">hedge</span>'
            if role == "hedge" else
            '<span style="background:#1c2410;color:#3fb950;border:1px solid #238636;'
            'padding:1px 6px;border-radius:10px;font-size:10px">sell</span>'
        )
        side_col = "#f85149" if side == "sell" else "#3fb950"
        otype_col = "#58a6ff" if otype == "CE" else "#d29922"

        rows += f"""
        <tr>
          <td style="font-family:monospace;font-size:12px">{_esc(short)}</td>
          <td style="color:{otype_col};font-weight:600">{_esc(otype)}</td>
          <td style="color:{side_col}">{_esc(side.upper())}</td>
          <td>{role_badge}</td>
          <td style="text-align:right">{lots}</td>
          <td style="text-align:right;font-family:monospace">₹{entry:.2f}</td>
          <td style="text-align:right;font-family:monospace">₹{cur:.2f}</td>
          <td style="text-align:right;color:{pnl_colour};font-weight:600">{sign_pnl}₹{leg_pnl:,.0f}</td>
        </tr>"""

    return f"""
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead>
        <tr style="color:#8b949e;font-size:11px;text-transform:uppercase;border-bottom:1px solid #30363d">
          <th style="padding:8px;text-align:left">Symbol</th>
          <th style="padding:8px;text-align:left">Type</th>
          <th style="padding:8px;text-align:left">Side</th>
          <th style="padding:8px;text-align:left">Role</th>
          <th style="padding:8px;text-align:right">Lots</th>
          <th style="padding:8px;text-align:right">Entry</th>
          <th style="padding:8px;text-align:right">LTP</th>
          <th style="padding:8px;text-align:right">Leg P&amp;L</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


# ── Event log ─────────────────────────────────────────────────────────────────

def _event_log_html(events: list[dict]) -> str:
    if not events:
        return '<p style="color:#8b949e;text-align:center;padding:16px">No events yet today</p>'

    EVENT_STYLES = {
        "ENTRY":     ("#3fb950", "#0f2a1a"),
        "EXIT":      ("#f85149", "#2a0a0a"),
        "FLOOR_SET": ("#a371f7", "#1a1429"),
        "REBALANCE": ("#58a6ff", "#0a1929"),
        "ADD":       ("#e3b341", "#2a1f06"),
        "ERROR":     ("#f85149", "#2a0a0a"),
        "DAILY_STOP":("#f85149", "#2a0a0a"),
    }

    rows = ""
    for e in reversed(events[-20:]):   # newest first, last 20
        evt   = e.get("event", "INFO")
        col, bg = EVENT_STYLES.get(evt, ("#8b949e", "#1a1f28"))
        badge = (
            f'<span style="background:{bg};color:{col};border:1px solid {col};'
            f'padding:1px 8px;border-radius:10px;font-size:10px;font-weight:600">'
            f'{_esc(evt)}</span>'
        )
        rows += f"""
        <tr style="border-bottom:1px solid #21262d">
          <td style="padding:6px 8px;color:#8b949e;font-size:11px;white-space:nowrap">{_esc(e.get('time',''))}</td>
          <td style="padding:6px 8px">{badge}</td>
          <td style="padding:6px 8px;color:#c9d1d9;font-size:12px">{_esc(e.get('detail',''))}</td>
        </tr>"""

    return f'<table style="width:100%;border-collapse:collapse"><tbody>{rows}</tbody></table>'


# ── Main HTML builder ─────────────────────────────────────────────────────────

def generate_dashboard(state: dict, mtm: float = 0.0, quotes: dict | None = None) -> str:
    if quotes is None:
        quotes = {}

    now_ist    = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    mode       = state.get("mode", "PAPER")
    if mode == "BACKTEST":
        mode_colour, mode_bg = "#a371f7", "#1a1429"
    elif mode == "LIVE":
        mode_colour, mode_bg = "#f85149", "#2a0a0a"
    else:
        mode_colour, mode_bg = "#e3b341", "#2a1f06"   # PAPER

    label      = state.get("current_label") or state.get("pending_label") or "—"
    pending    = state.get("pending_label") and not state.get("current_label") == state.get("pending_label")
    label_str  = _label_badge(label)
    if pending:
        label_str += ' <span style="color:#8b949e;font-size:11px">(pending)</span>'

    view       = state.get("last_view", {})
    score      = view.get("score", 0)
    score_col  = LABEL_COLOURS.get(label, "#8b949e")
    spot       = view.get("spot", 0)

    peak       = state.get("peak_mtm", 0.0)
    floor      = state.get("profit_floor")
    pnl_col    = "#3fb950" if mtm >= 0 else "#f85149"
    pnl_sign   = "+" if mtm >= 0 else ""

    history    = state.get("score_history", [])
    events     = state.get("event_log", [])
    positions  = state.get("positions", [])
    trade_cnt  = state.get("trade_count", 0)
    daily_stop = state.get("daily_stopped", False)
    entry_lbl  = state.get("entry_label") or "—"
    entry_time = state.get("entry_time") or "—"
    atm        = state.get("atm") or "—"
    date_str   = state.get("date", "")

    score_svg  = _score_sparkline(history)
    mtm_svg    = _mtm_sparkline(history)
    pos_table  = _positions_table(positions, quotes)
    evt_html   = _event_log_html(events)

    floor_html = (
        f'<span style="color:#a371f7;font-weight:700">₹{floor:,.0f}</span>'
        if floor else
        '<span style="color:#8b949e">—</span>'
    )

    status_banner = ""
    if mode == "BACKTEST":
        status_banner = (
            '<div style="background:#1a1429;border:1px solid #a371f7;border-radius:8px;'
            'padding:12px 18px;margin-bottom:20px;display:flex;align-items:center;gap:12px">'
            '<span style="font-size:20px">🔬</span>'
            f'<div><div style="color:#a371f7;font-weight:700;font-size:14px">BACKTEST MODE</div>'
            f'<div style="color:#8b949e;font-size:12px;margin-top:2px">'
            f'Replaying real Fyers 5-min data · '
            f'ATM {_esc(atm)} · Date {_esc(date_str)} · '
            f'All strategy rules active</div></div></div>'
        )
    elif daily_stop:
        status_banner = '<div style="background:#2a0a0a;border:1px solid #f85149;border-radius:8px;padding:12px;margin-bottom:20px;color:#f85149;font-weight:600;text-align:center">🛑 Daily loss limit hit — trading stopped</div>'
    elif not positions:
        status_banner = '<div style="background:#1a1f28;border:1px solid #30363d;border-radius:8px;padding:12px;margin-bottom:20px;color:#8b949e;text-align:center">⏳ Waiting for entry signal…</div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="300">
  <title>Nifty Options Bot — {_esc(date_str)}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1117;color:#e6edf3;min-height:100vh}}
    .hdr{{background:linear-gradient(135deg,#161b22,#21262d);padding:16px 24px;border-bottom:1px solid #30363d;display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
    .hdr h1{{font-size:17px;font-weight:600;color:#f0f6fc}}
    .hdr .sub{{font-size:12px;color:#8b949e;margin-top:2px}}
    .badge{{padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600;letter-spacing:.3px}}
    .wrap{{max-width:1200px;margin:0 auto;padding:20px}}
    .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:20px}}
    .card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px}}
    .card .lbl{{font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
    .card .val{{font-size:22px;font-weight:700}}
    .card .sub{{font-size:11px;color:#8b949e;margin-top:3px}}
    .box{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;margin-bottom:16px}}
    .box-title{{font-size:13px;font-weight:600;color:#c9d1d9;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #21262d}}
    .two{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
    .grid-3{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:8px}}
    .stat-item{{background:#1c2128;border-radius:6px;padding:10px}}
    .stat-item .k{{font-size:10px;color:#8b949e;text-transform:uppercase}}
    .stat-item .v{{font-size:14px;font-weight:600;margin-top:3px}}
    .upd{{font-size:11px;color:#8b949e;text-align:right;margin-top:8px}}
    @media(max-width:700px){{.two{{grid-template-columns:1fr}}.grid-3{{grid-template-columns:repeat(2,1fr)}}}}
  </style>
</head>
<body>

<div class="hdr">
  <div>
    <h1>⚡ Nifty Options Bot</h1>
    <div class="sub">Short Strangle · Profit Lock · Max 4 trades/day</div>
  </div>
  <span class="badge" style="background:{mode_bg};color:{mode_colour};border:1px solid {mode_colour}">{_esc(mode)} TRADING</span>
  <span class="badge" style="background:#1a1f28;color:#8b949e;border:1px solid #30363d">{_esc(date_str)}</span>
  <span class="badge" style="background:#0a1929;color:#58a6ff;border:1px solid #1f6feb">ATM {_esc(atm)}</span>
  <span class="upd" style="margin-left:auto">Updated: {_esc(now_ist)}</span>
</div>

<div class="wrap">

  {status_banner}

  <!-- KPI cards -->
  <div class="cards">
    <div class="card">
      <div class="lbl">MTM P&amp;L</div>
      <div class="val" style="color:{pnl_col}">{pnl_sign}₹{mtm:,.0f}</div>
      <div class="sub">Live mark-to-market</div>
    </div>
    <div class="card">
      <div class="lbl">Peak MTM</div>
      <div class="val" style="color:#3fb950">₹{peak:,.0f}</div>
      <div class="sub">Profit floor: {floor_html}</div>
    </div>
    <div class="card">
      <div class="lbl">Score</div>
      <div class="val" style="color:{score_col}">{f"+{score}" if score >= 0 else score}</div>
      <div class="sub">{label_str}</div>
    </div>
    <div class="card">
      <div class="lbl">Spot</div>
      <div class="val" style="color:#c9d1d9">₹{spot:,.0f}</div>
      <div class="sub">Nifty50</div>
    </div>
    <div class="card">
      <div class="lbl">Trades Used</div>
      <div class="val" style="color:{'#f85149' if trade_cnt>=4 else '#e3b341' if trade_cnt>=3 else '#3fb950'}">{trade_cnt} / 4</div>
      <div class="sub">Max 4 per day</div>
    </div>
    <div class="card">
      <div class="lbl">Entry</div>
      <div class="val" style="font-size:16px;color:#c9d1d9">{_esc(entry_time)}</div>
      <div class="sub">{_label_badge(entry_lbl) if entry_lbl != '—' else '—'}</div>
    </div>
  </div>

  <!-- View details -->
  <div class="box">
    <div class="box-title">Market View</div>
    <div class="grid-3">
      <div class="stat-item"><div class="k">Upper Above VWAP</div><div class="v" style="color:#f85149">{view.get('upper_above','—')}</div></div>
      <div class="stat-item"><div class="k">Upper Below VWAP</div><div class="v" style="color:#3fb950">{view.get('upper_below','—')}</div></div>
      <div class="stat-item"><div class="k">Lower Above VWAP</div><div class="v" style="color:#3fb950">{view.get('lower_above','—')}</div></div>
      <div class="stat-item"><div class="k">Lower Below VWAP</div><div class="v" style="color:#f85149">{view.get('lower_below','—')}</div></div>
      <div class="stat-item"><div class="k">Pending Label</div><div class="v">{_label_badge(state.get('pending_label') or '—')}</div></div>
      <div class="stat-item"><div class="k">Confirmed Label</div><div class="v">{_label_badge(state.get('current_label') or '—')}</div></div>
    </div>
  </div>

  <!-- Charts -->
  <div class="box">
    <div class="box-title">Score History</div>
    {score_svg}
  </div>

  <div class="box">
    <div class="box-title">MTM P&amp;L History</div>
    {mtm_svg}
  </div>

  <!-- Positions + Events -->
  <div class="two">
    <div class="box">
      <div class="box-title">Open Positions ({len(positions)} legs)</div>
      {pos_table}
    </div>
    <div class="box">
      <div class="box-title">Event Log</div>
      {evt_html}
    </div>
  </div>

  <div class="upd">Auto-refreshes every 5 min · Generated at {_esc(now_ist)}</div>
</div>

</body>
</html>"""

    return html


# ── Write to disk ─────────────────────────────────────────────────────────────

def write_dashboard(state: dict, mtm: float = 0.0, quotes: dict | None = None) -> None:
    """Generate and write docs/index.html. Logs success/failure but never raises."""
    try:
        os.makedirs(DOCS_DIR, exist_ok=True)
        html  = generate_dashboard(state, mtm, quotes or {})
        tmp   = HTML_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp, HTML_FILE)
        log.info(f"Dashboard written → {HTML_FILE} ({len(html):,} bytes)")
    except Exception as e:
        log.error(f"Dashboard write error: {e}")
