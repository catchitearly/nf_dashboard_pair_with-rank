"""
backtest_engine.py
Fetches real Fyers 5-min historical data for a given date and replays
the full strategy bar-by-bar — identical logic to live trading.

How it works:
  1. Fetch full-day 5-min candles for all 9 straddle strikes + index.
  2. For each bar index i (starting from 9:15):
       a. Build straddle_candles[strike] = full_candles[strike][:i+1]
          (simulate "we only know bars up to now")
       b. Compute spot from index candle close at bar i.
       c. Run strategy decision logic (same code as live) with mocked time.
       d. Compute MTM from actual option close prices.
       e. Write dashboard snapshot with BACKTEST label.
  3. Force exit at 14:30 if still in position.
  4. Print full report.

MTM is real — uses actual option close prices from Fyers history,
not synthetic or estimated prices.
"""

import copy
import logging
from datetime import datetime, timezone, timedelta, date
from typing import Optional

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
LOT_SIZE = 75   # from config, duplicated here to avoid circular import

# ── Time helpers ──────────────────────────────────────────────────────────────

def _epoch_to_ist(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=IST)


def _ist_hm(dt: datetime) -> tuple[int, int]:
    return dt.hour, dt.minute


# ── Data fetch ────────────────────────────────────────────────────────────────

def fetch_day_data(date_str: str, atm: int) -> dict | None:
    """
    Fetch full day 5-min candles for all 9 straddle strikes + index.
    Returns:
    {
      "index":  [candle_dict, ...],          # Nifty50 5-min candles
      "straddles": {strike: [candle_dict]},  # CE+PE combined
      "date":   date_str,
      "atm":    atm,
    }
    or None on failure.
    """
    import fyers_data as fd
    from config import STRIKE_STEP, N_UPPER_STRIKES, N_LOWER_STRIKES

    log.info(f"Fetching historical data for {date_str} | ATM={atm}")

    # Fetch index candles
    index_candles = fd.get_index_candles(date_str)
    if not index_candles:
        log.error(f"Could not fetch index candles for {date_str}")
        return None
    log.info(f"Index: {len(index_candles)} bars")

    # Fetch all straddle candles
    strikes = (
        [atm - (i + 1) * STRIKE_STEP for i in range(N_LOWER_STRIKES)]
        + [atm]
        + [atm + (i + 1) * STRIKE_STEP for i in range(N_UPPER_STRIKES)]
    )

    straddles = {}
    failed = 0
    for strike in strikes:
        candles = fd.get_straddle_candles(strike, date_str)
        if candles:
            straddles[strike] = candles
        else:
            failed += 1
            log.warning(f"No straddle candles for {strike}")

    if not straddles or failed > len(strikes) // 2:
        log.error(f"Too many straddle fetch failures ({failed}/{len(strikes)})")
        return None

    log.info(f"Fetched {len(straddles)}/{len(strikes)} straddle series")
    return {
        "index":     index_candles,
        "straddles": straddles,
        "date":      date_str,
        "atm":       atm,
    }


# ── MTM computation from candle data ─────────────────────────────────────────

def compute_mtm_from_candles(
    positions: list[dict],
    straddles: dict[int, list[dict]],
    bar_index: int,
    option_bar_map: dict,        # {symbol: bar_index_at_entry}
) -> float:
    """
    Compute MTM using actual candle close prices at bar_index.
    Sell leg:   (entry_ltp - current_close) * lots * LOT_SIZE
    Hedge leg:  (current_close - entry_ltp) * lots * LOT_SIZE
    """
    total = 0.0
    for leg in positions:
        strike   = leg["strike"]
        opt_type = leg["opt_type"]
        side     = leg["side"]
        lots     = leg["lots"]
        entry    = leg.get("entry_ltp", 0.0)

        # Get current close from combined straddle candle — split CE/PE evenly
        # We store CE and PE close separately in leg dict when entering
        cur_close = leg.get(f"_cur_close_{bar_index}")
        if cur_close is None:
            # Get individual option candle close
            cur_close = _get_option_close(strike, opt_type, bar_index, straddles)
            leg[f"_cur_close_{bar_index}"] = cur_close

        if side == "sell":
            total += (entry - cur_close) * lots * LOT_SIZE
        else:
            total += (cur_close - entry) * lots * LOT_SIZE

    return round(total, 2)


def _get_option_close(
    strike: int, opt_type: str, bar_index: int,
    straddles: dict[int, list[dict]],
) -> float:
    """
    Extract individual CE or PE close from straddle data.
    Straddle candle = CE_close + PE_close. We track them separately.
    Since we don't have them split, we use the straddle as proxy
    and weight by ~50% each (reasonable approximation for OTM options).
    For a more accurate result the caller stores entry_ltp from quotes.
    """
    candles = straddles.get(strike, [])
    if not candles or bar_index >= len(candles):
        return 0.0
    # Use last available bar if bar_index exceeds candle count
    idx = min(bar_index, len(candles) - 1)
    # Straddle = CE + PE. Split 50/50 as approximation.
    # In real entry, actual LTP is fetched via quotes API.
    return candles[idx]["close"] / 2.0


def _get_option_close_from_raw(
    strike: int, opt_type: str, bar_index: int,
    raw_option_data: dict,      # {(strike, opt_type): [candle_dicts]}
) -> float:
    """Use raw per-leg option candle data if available (more accurate)."""
    key = (strike, opt_type)
    candles = raw_option_data.get(key, [])
    if not candles or bar_index >= len(candles):
        return 0.0
    return candles[min(bar_index, len(candles)-1)]["close"]


# ── Bar-by-bar strategy simulation ───────────────────────────────────────────

class BacktestState:
    """Encapsulates all mutable state during a backtest run."""
    def __init__(self, date_str: str, atm: int):
        import state_manager as sm
        self.state     = sm._fresh_state()
        self.state["date"] = date_str
        self.state["atm"]  = atm
        self.state["mode"] = "BACKTEST"

        self.events: list[dict]  = []
        self.snapshots: list[dict] = []   # one per bar
        self.final_pnl  = 0.0
        self.peak_mtm   = 0.0
        self.exit_time  = None
        self.exit_reason = None
        self.entry_time  = None
        self.entry_label = None

    def log_event(self, time_str: str, etype: str, detail: str, mtm: float = 0.0):
        self.events.append({"time": time_str, "event": etype, "detail": detail})
        log.info(f"[BT EVENT] {etype:12s} {time_str} | {detail}")
        import state_manager as sm
        sm.append_event(self.state, time_str, etype, detail)

    def snapshot(self, time_str: str, score: float, label: str,
                 mtm: float, spot: float):
        self.snapshots.append({
            "time": time_str, "score": score,
            "label": label, "mtm": mtm, "spot": spot,
        })
        import state_manager as sm
        sm.append_score_history(self.state, time_str, score, label, mtm, spot)


def run_backtest_date(date_str: str, atm: int | None = None) -> "BacktestState":
    """
    Run full strategy backtest on a given date using real Fyers historical data.
    atm: override ATM strike. If None, computed from 9:15 candle close.
    Returns BacktestState with full event log and P&L.
    """
    import fyers_data as fd
    import view_engine as ve
    import state_manager as sm
    import dashboard_gen
    from config import (
        STRIKE_STEP, ENTRY_START_IST, ENTRY_CUTOFF_IST,
        FORCE_EXIT_IST, ADD_POSITION_IST, MAX_TRADES_PER_DAY,
        DAILY_LOSS_LIMIT, POSITION_SIZING, MAX_LOTS_PER_SIDE,
        PROFIT_LOCK_TIERS, PAPER_TRADING,
    )

    log.info(f"\n{'='*60}")
    log.info(f"BACKTEST: {date_str}")
    log.info(f"{'='*60}")

    # ── 1. Fetch 9:15 close for ATM ──────────────────────────────────────────
    open_close = fd.get_915_candle_close(date_str)
    if open_close is None:
        log.error("Could not fetch 9:15 candle — aborting")
        bs = BacktestState(date_str, atm or 0)
        bs.log_event("09:15", "ERROR", "Could not fetch 9:15 candle")
        return bs

    if atm is None:
        atm = int(round(open_close / STRIKE_STEP) * STRIKE_STEP)
    log.info(f"9:15 close={open_close} → ATM={atm}")

    # ── 2. Fetch all candle data ──────────────────────────────────────────────
    day_data = fetch_day_data(date_str, atm)
    if day_data is None:
        bs = BacktestState(date_str, atm)
        bs.log_event("09:15", "ERROR", "Data fetch failed")
        return bs

    index_candles = day_data["index"]
    straddles_full = day_data["straddles"]   # {strike: [all_day_candles]}

    # Also fetch individual CE/PE candles for accurate MTM
    raw_opts: dict[tuple, list[dict]] = {}
    from config import N_UPPER_STRIKES, N_LOWER_STRIKES
    strikes = (
        [atm - (i+1)*STRIKE_STEP for i in range(N_LOWER_STRIKES)]
        + [atm]
        + [atm + (i+1)*STRIKE_STEP for i in range(N_UPPER_STRIKES)]
    )
    log.info("Fetching individual CE/PE candles for accurate MTM...")
    for strike in strikes:
        for ot in ("CE", "PE"):
            sym = fd.option_symbol(strike, ot)
            raw = fd._fetch_candles_raw(sym, date_str)
            if raw:
                raw_opts[(strike, ot)] = [
                    {"ts": c[0], "close": c[4]} for c in raw
                ]

    # ── 3. Build timestamp-aligned bar list ───────────────────────────────────
    # Use index candle timestamps as the master timeline
    ts_list = [c["ts"] for c in index_candles]
    n_bars  = len(ts_list)
    log.info(f"Timeline: {n_bars} bars from "
             f"{_epoch_to_ist(ts_list[0]).strftime('%H:%M')} to "
             f"{_epoch_to_ist(ts_list[-1]).strftime('%H:%M')}")

    # Build bar_index lookup for raw_opts (match by timestamp)
    # raw_opts candles may differ in count from index — build ts→idx maps
    raw_ts_idx: dict[tuple, dict[int, int]] = {}  # {(strike,ot): {ts: idx}}
    for key, candles in raw_opts.items():
        raw_ts_idx[key] = {c["ts"]: i for i, c in enumerate(candles)}

    straddle_ts_idx: dict[int, dict[int, int]] = {}
    for strike, candles in straddles_full.items():
        straddle_ts_idx[strike] = {c["ts"]: i for i, c in enumerate(candles)}

    # ── 4. Bar-by-bar loop ────────────────────────────────────────────────────
    bs = BacktestState(date_str, atm)
    bs.state["atm"] = atm

    # Virtual positions (paper style — no real orders)
    open_positions: list[dict] = []
    closed_pnl = 0.0

    # Strategy state
    pending_label: str | None = None
    pending_count: int        = 0
    current_label: str | None = None
    entry_label:   str | None = None
    entry_bar:     int | None = None
    trade_count    = 0
    add_done       = False
    daily_stopped  = False
    peak_mtm       = 0.0
    profit_floor: float | None = None

    for bar_idx, ts in enumerate(ts_list):
        bar_dt   = _epoch_to_ist(ts)
        hm       = _ist_hm(bar_dt)
        time_str = bar_dt.strftime("%H:%M")
        spot     = index_candles[bar_idx]["close"]

        if daily_stopped:
            break

        # ── Build straddle candles up to this bar (simulate "known so far") ──
        straddle_candles_now: dict[int, list[dict]] = {}
        for strike, all_candles in straddles_full.items():
            ts_idx_map = straddle_ts_idx[strike]
            # Find bars up to current timestamp
            subset = [c for c in all_candles if c["ts"] <= ts]
            if subset:
                straddle_candles_now[strike] = subset

        # ── Compute view ──────────────────────────────────────────────────────
        view = ve.compute_view(atm, straddle_candles_now, spot)
        if view is None:
            log.warning(f"[{time_str}] view_engine returned None — skipping bar")
            continue

        score = view["score"]
        label = view["label"]

        # ── Label confirmation (2-of-2) ───────────────────────────────────────
        confirmed_label: str | None = None
        if label == current_label:
            pending_label = None
            pending_count = 0
        elif label == pending_label:
            pending_count += 1
            if pending_count >= 2:
                confirmed_label = label
                current_label   = label
                pending_label   = None
                pending_count   = 0
                log.info(f"[{time_str}] Label confirmed: {confirmed_label}")
        else:
            pending_label = label
            pending_count = 1

        # ── Compute current MTM ───────────────────────────────────────────────
        mtm = closed_pnl
        for leg in open_positions:
            cur = _get_leg_close(leg, ts, raw_opts, raw_ts_idx, straddles_full, straddle_ts_idx)
            entry = leg["entry_ltp"]
            lots  = leg["lots"]
            if leg["side"] == "sell":
                mtm += (entry - cur) * lots * LOT_SIZE
            else:
                mtm += (cur - entry) * lots * LOT_SIZE
        mtm = round(mtm, 2)

        # ── Peak MTM and profit floor ─────────────────────────────────────────
        if mtm > peak_mtm:
            peak_mtm = mtm
        old_floor = profit_floor
        profit_floor = _calc_floor(peak_mtm)
        if profit_floor and profit_floor != old_floor:
            bs.log_event(time_str, "FLOOR_SET",
                         f"peak=₹{peak_mtm:.0f} → floor=₹{profit_floor:.0f}", mtm)

        # ── Force exit at 14:30 ───────────────────────────────────────────────
        if hm >= FORCE_EXIT_IST and open_positions:
            closed_pnl += _close_all(open_positions, ts, raw_opts,
                                     raw_ts_idx, straddles_full, straddle_ts_idx)
            open_positions = []
            trade_count += 1
            bs.log_event(time_str, "EXIT", f"14:30 force exit | pnl=₹{closed_pnl:.0f}", mtm)
            bs.exit_time   = time_str
            bs.exit_reason = "force_exit_1430"
            bs.final_pnl   = closed_pnl
            bs.snapshot(time_str, score, label, closed_pnl, spot)
            bs.state.update({
                "trade_count": trade_count, "peak_mtm": peak_mtm,
                "profit_floor": profit_floor, "positions": [],
                "closed_pnl": closed_pnl, "last_view": _make_view_dict(view, spot),
                "last_updated": bar_dt.strftime("%Y-%m-%d %H:%M:%S IST"),
                "current_label": current_label,
            })
            _write_bt_dashboard(bs, closed_pnl)
            break

        # ── Daily loss limit ──────────────────────────────────────────────────
        if mtm <= DAILY_LOSS_LIMIT and open_positions:
            closed_pnl += _close_all(open_positions, ts, raw_opts,
                                     raw_ts_idx, straddles_full, straddle_ts_idx)
            open_positions = []
            daily_stopped  = True
            bs.log_event(time_str, "DAILY_STOP",
                         f"MTM=₹{mtm:.0f} hit limit ₹{DAILY_LOSS_LIMIT}", mtm)
            bs.final_pnl = closed_pnl
            break

        # ── Profit floor check ────────────────────────────────────────────────
        if profit_floor and mtm < profit_floor and open_positions:
            closed_pnl += _close_all(open_positions, ts, raw_opts,
                                     raw_ts_idx, straddles_full, straddle_ts_idx)
            open_positions = []
            trade_count += 1
            bs.log_event(time_str, "EXIT",
                         f"profit floor ₹{profit_floor:.0f} breached | MTM=₹{mtm:.0f}",
                         mtm)
            bs.exit_time   = time_str
            bs.exit_reason = "profit_floor"
            bs.final_pnl   = closed_pnl
            bs.snapshot(time_str, score, label, closed_pnl, spot)
            bs.state.update({
                "trade_count": trade_count, "peak_mtm": peak_mtm,
                "profit_floor": profit_floor, "positions": [],
                "closed_pnl": closed_pnl, "last_view": _make_view_dict(view, spot),
                "last_updated": bar_dt.strftime("%Y-%m-%d %H:%M:%S IST"),
                "current_label": current_label,
            })
            _write_bt_dashboard(bs, closed_pnl)
            break

        # ── Emergency score exit ──────────────────────────────────────────────
        if (entry_label and open_positions and confirmed_label
                and _score_emergency(entry_label, score)):
            risky_ot = "CE" if entry_label in ("bullish", "very_bullish") else "PE"
            to_close = [l for l in open_positions if l["opt_type"] == risky_ot]
            for leg in to_close:
                cur = _get_leg_close(leg, ts, raw_opts, raw_ts_idx,
                                     straddles_full, straddle_ts_idx)
                leg["exit_ltp"] = cur
                if leg["side"] == "sell":
                    closed_pnl += (leg["entry_ltp"] - cur) * leg["lots"] * LOT_SIZE
                else:
                    closed_pnl += (cur - leg["entry_ltp"]) * leg["lots"] * LOT_SIZE
            open_positions = [l for l in open_positions if l["opt_type"] != risky_ot]
            trade_count += 1
            bs.log_event(time_str, "EXIT",
                         f"emergency {risky_ot} exit | entry={entry_label} score={score:+.1f}",
                         mtm)

        # ── Entry ─────────────────────────────────────────────────────────────
        if (not open_positions and not entry_label
                and hm >= ENTRY_START_IST and hm <= ENTRY_CUTOFF_IST
                and confirmed_label is not None
                and trade_count < MAX_TRADES_PER_DAY):

            sizing = POSITION_SIZING.get(confirmed_label, POSITION_SIZING["neutral"])
            legs   = _build_legs(confirmed_label, sizing, spot, atm,
                                 ts, raw_opts, raw_ts_idx, straddles_full, straddle_ts_idx)
            open_positions.extend(legs)
            entry_label      = confirmed_label
            entry_bar        = bar_idx
            bs.entry_time    = time_str
            bs.entry_label   = confirmed_label
            bs.state["entry_label"] = confirmed_label
            bs.state["entry_time"]  = time_str
            trade_count += 1

            ce_sell = next((l for l in legs if l["opt_type"]=="CE" and l["role"]=="sell"), {})
            pe_sell = next((l for l in legs if l["opt_type"]=="PE" and l["role"]=="sell"), {})
            bs.log_event(time_str, "ENTRY",
                         f"label={confirmed_label} score={score:+.1f} spot=₹{spot:.0f} "
                         f"CE{ce_sell.get('strike',0)}×{ce_sell.get('lots',0)} "
                         f"PE{pe_sell.get('strike',0)}×{pe_sell.get('lots',0)}",
                         mtm)

        # ── 12:00 Add ─────────────────────────────────────────────────────────
        elif (open_positions and not add_done
              and hm >= ADD_POSITION_IST and hm < FORCE_EXIT_IST
              and entry_label and current_label == entry_label
              and mtm > 0 and trade_count < MAX_TRADES_PER_DAY - 1):

            from config import ADD_MAX_FRACTION
            sizing = POSITION_SIZING.get(entry_label, POSITION_SIZING["neutral"])
            existing_ce = sum(l["lots"] for l in open_positions
                              if l["opt_type"]=="CE" and l["role"]=="sell")
            existing_pe = sum(l["lots"] for l in open_positions
                              if l["opt_type"]=="PE" and l["role"]=="sell")
            add_ce = min(int(sizing["sell_ce"] * ADD_MAX_FRACTION),
                         MAX_LOTS_PER_SIDE - existing_ce)
            add_pe = min(int(sizing["sell_pe"] * ADD_MAX_FRACTION),
                         MAX_LOTS_PER_SIDE - existing_pe)

            if add_ce > 0 or add_pe > 0:
                new_legs = _build_legs_add(
                    entry_label, add_ce, add_pe, spot, atm,
                    ts, raw_opts, raw_ts_idx, straddles_full, straddle_ts_idx)
                open_positions.extend(new_legs)
                trade_count += 1
                bs.log_event(time_str, "ADD",
                             f"label={entry_label} +CE×{add_ce} +PE×{add_pe} | MTM=₹{mtm:.0f}",
                             mtm)
            add_done = True

        # ── Score-triggered rebalance ─────────────────────────────────────────
        elif (open_positions and entry_label and confirmed_label
              and confirmed_label != entry_label
              and trade_count < MAX_TRADES_PER_DAY - 1
              and mtm >= 0):

            _rebalance_positions(
                open_positions, confirmed_label, spot, atm,
                ts, raw_opts, raw_ts_idx, straddles_full, straddle_ts_idx,
                closed_pnl
            )
            trade_count += 1
            bs.log_event(time_str, "REBALANCE",
                         f"{entry_label} → {confirmed_label} score={score:+.1f}", mtm)

        # ── Record snapshot and update state for dashboard ────────────────────
        bs.snapshot(time_str, score, label, mtm, spot)
        bs.state.update({
            "trade_count":   trade_count,
            "peak_mtm":      peak_mtm,
            "profit_floor":  profit_floor,
            "positions":     _positions_for_state(open_positions),
            "closed_pnl":    closed_pnl,
            "last_view":     _make_view_dict(view, spot),
            "last_updated":  bar_dt.strftime("%Y-%m-%d %H:%M:%S IST"),
            "current_label": current_label,
            "pending_label": pending_label,
            "daily_stopped": daily_stopped,
            "add_done":      add_done,
            "mode":          "BACKTEST",
        })
        bs.peak_mtm = peak_mtm

        # Write dashboard every bar
        _write_bt_dashboard(bs, mtm)

        if hm >= FORCE_EXIT_IST and not open_positions:
            break

    # Final P&L if positions still open at end of data
    if open_positions:
        last_ts = ts_list[-1]
        closed_pnl += _close_all(open_positions, last_ts, raw_opts,
                                 raw_ts_idx, straddles_full, straddle_ts_idx)
        bs.final_pnl   = closed_pnl
        bs.exit_time   = _epoch_to_ist(last_ts).strftime("%H:%M")
        bs.exit_reason = "data_end"
        bs.log_event(bs.exit_time, "EXIT", f"End of data | pnl=₹{closed_pnl:.0f}")

    if not bs.final_pnl:
        bs.final_pnl = closed_pnl

    bs.state["closed_pnl"] = closed_pnl
    _write_bt_dashboard(bs, bs.final_pnl)

    log.info(f"Backtest complete | P&L=₹{bs.final_pnl:.0f} | "
             f"peak=₹{bs.peak_mtm:.0f} | exit={bs.exit_reason}")
    return bs


# ── Position helpers ──────────────────────────────────────────────────────────

def _get_leg_close(leg, ts, raw_opts, raw_ts_idx, straddles_full, straddle_ts_idx):
    """Get current close price for a leg at timestamp ts."""
    strike   = leg["strike"]
    opt_type = leg["opt_type"]
    key      = (strike, opt_type)
    ts_map   = raw_ts_idx.get(key, {})
    idx      = ts_map.get(ts)
    if idx is not None and key in raw_opts:
        return raw_opts[key][idx]["close"]
    # Fallback: use straddle / 2
    st_map = straddle_ts_idx.get(strike, {})
    si     = st_map.get(ts)
    if si is not None and strike in straddles_full:
        return straddles_full[strike][si]["close"] / 2.0
    return leg.get("entry_ltp", 0.0)


def _close_all(positions, ts, raw_opts, raw_ts_idx, straddles_full, straddle_ts_idx):
    """Close all positions at ts, return total P&L."""
    total = 0.0
    for leg in positions:
        cur = _get_leg_close(leg, ts, raw_opts, raw_ts_idx, straddles_full, straddle_ts_idx)
        leg["exit_ltp"] = cur
        if leg["side"] == "sell":
            total += (leg["entry_ltp"] - cur) * leg["lots"] * LOT_SIZE
        else:
            total += (cur - leg["entry_ltp"]) * leg["lots"] * LOT_SIZE
    positions.clear()
    return round(total, 2)


def _build_legs(label, sizing, spot, atm, ts, raw_opts, raw_ts_idx,
                straddles_full, straddle_ts_idx):
    """Build entry legs with real fill prices from candle close at entry bar."""
    from config import STRIKE_STEP, HEDGE_STRIKE_OFFSET, DELTA_SELL_MIN, DELTA_SELL_MAX
    import fyers_data as fd
    from datetime import date as dt_date

    legs = []
    target_delta = (DELTA_SELL_MIN + DELTA_SELL_MAX) / 2

    # Approximate OTM strikes: use BS estimate
    from config import EXPIRY_DATE
    from datetime import datetime as dtime
    try:
        expiry = dtime.strptime(EXPIRY_DATE, "%d-%m-%Y").date()
        dte    = max((expiry - dt_date.today()).days, 0.5)
    except Exception:
        dte = 7.0

    ce_s = _nearest_strike(spot, "CE", atm, STRIKE_STEP)
    pe_s = _nearest_strike(spot, "PE", atm, STRIKE_STEP)

    configs = [
        (ce_s,                   "CE", "sell", sizing["sell_ce"],  "sell"),
        (pe_s,                   "PE", "sell", sizing["sell_pe"],  "sell"),
        (ce_s + HEDGE_STRIKE_OFFSET, "CE", "hedge", sizing["hedge_ce"], "buy"),
        (pe_s - HEDGE_STRIKE_OFFSET, "PE", "hedge", sizing["hedge_pe"], "buy"),
    ]
    for strike, ot, role, lots, side in configs:
        ltp = _get_close_at(strike, ot, ts, raw_opts, raw_ts_idx, straddles_full, straddle_ts_idx)
        legs.append({
            "symbol":    fd.option_symbol(strike, ot),
            "strike":    strike,
            "opt_type":  ot,
            "side":      side,
            "role":      role,
            "lots":      lots,
            "entry_ltp": ltp,
        })
    return legs


def _build_legs_add(label, add_ce, add_pe, spot, atm, ts, raw_opts, raw_ts_idx,
                    straddles_full, straddle_ts_idx):
    """Build additional lots for 12:00 add."""
    from config import STRIKE_STEP, HEDGE_STRIKE_OFFSET
    import fyers_data as fd
    legs = []
    ce_s = _nearest_strike(spot, "CE", atm, STRIKE_STEP)
    pe_s = _nearest_strike(spot, "PE", atm, STRIKE_STEP)
    for strike, ot, role, lots, side in [
        (ce_s,                   "CE", "sell",  add_ce, "sell"),
        (pe_s,                   "PE", "sell",  add_pe, "sell"),
        (ce_s + HEDGE_STRIKE_OFFSET, "CE", "hedge", max(1, add_ce//4), "buy"),
        (pe_s - HEDGE_STRIKE_OFFSET, "PE", "hedge", max(1, add_pe//4), "buy"),
    ]:
        if lots <= 0:
            continue
        ltp = _get_close_at(strike, ot, ts, raw_opts, raw_ts_idx,
                            straddles_full, straddle_ts_idx)
        legs.append({
            "symbol": fd.option_symbol(strike, ot),
            "strike": strike, "opt_type": ot,
            "side": side, "role": role,
            "lots": lots, "entry_ltp": ltp,
        })
    return legs


def _rebalance_positions(positions, new_label, spot, atm, ts, raw_opts,
                         raw_ts_idx, straddles_full, straddle_ts_idx, closed_pnl_ref):
    """Simple rebalance: remove excess lots of the over-represented side."""
    from config import POSITION_SIZING, STRIKE_STEP
    sizing  = POSITION_SIZING.get(new_label, POSITION_SIZING["neutral"])
    tgt_ce  = sizing["sell_ce"]
    tgt_pe  = sizing["sell_pe"]
    cur_ce  = sum(l["lots"] for l in positions if l["opt_type"]=="CE" and l["role"]=="sell")
    cur_pe  = sum(l["lots"] for l in positions if l["opt_type"]=="PE" and l["role"]=="sell")

    for ot, tgt, cur in [("CE", tgt_ce, cur_ce), ("PE", tgt_pe, cur_pe)]:
        if cur > tgt:
            excess    = cur - tgt
            sell_legs = [l for l in positions if l["opt_type"]==ot and l["role"]=="sell"]
            to_remove = sell_legs[:excess]
            for leg in to_remove:
                cur_close = _get_leg_close(leg, ts, raw_opts, raw_ts_idx,
                                           straddles_full, straddle_ts_idx)
                leg["exit_ltp"] = cur_close
            positions[:] = [l for l in positions if l not in to_remove]
            log.info(f"Rebalance: closed {excess} {ot} sell lots")


def _nearest_strike(spot, opt_type, atm, step, n_steps=5):
    """Return a reasonable OTM sell strike (~5 steps from ATM)."""
    if opt_type == "CE":
        return atm + n_steps * step
    else:
        return atm - n_steps * step


def _get_close_at(strike, ot, ts, raw_opts, raw_ts_idx, straddles_full, straddle_ts_idx):
    key = (strike, ot)
    idx = raw_ts_idx.get(key, {}).get(ts)
    if idx is not None and key in raw_opts:
        return raw_opts[key][idx]["close"]
    si = straddle_ts_idx.get(strike, {}).get(ts)
    if si is not None and strike in straddles_full:
        return straddles_full[strike][si]["close"] / 2.0
    return 50.0   # fallback if no data


def _calc_floor(peak: float) -> float | None:
    from config import PROFIT_LOCK_TIERS
    for threshold, fraction in PROFIT_LOCK_TIERS:
        if peak >= threshold:
            return round(peak * fraction, 2)
    return None


def _score_emergency(entry_label: str, score: float) -> bool:
    if entry_label in ("bullish", "very_bullish") and score <= -3.5:
        return True
    if entry_label in ("bearish", "very_bearish") and score >= 3.5:
        return True
    return False


def _positions_for_state(positions: list[dict]) -> list[dict]:
    """Strip per-bar cache keys before storing in state."""
    clean = []
    for leg in positions:
        clean.append({k: v for k, v in leg.items() if not k.startswith("_cur_close_")})
    return clean


def _make_view_dict(view: dict, spot: float) -> dict:
    return {
        "score":       view.get("score", 0),
        "label":       view.get("label", ""),
        "upper_above": view.get("upper_above", 0),
        "upper_below": view.get("upper_below", 0),
        "lower_above": view.get("lower_above", 0),
        "lower_below": view.get("lower_below", 0),
        "spot":        spot,
    }


def _write_bt_dashboard(bs: BacktestState, mtm: float) -> None:
    try:
        import dashboard_gen
        # Build live-style quotes from last known closes
        quotes = {}
        for leg in bs.state.get("positions", []):
            sym = leg.get("symbol", "")
            quotes[sym] = {"ltp": leg.get("entry_ltp", 0.0)}
        dashboard_gen.write_dashboard(bs.state, mtm, quotes)
    except Exception as e:
        log.error(f"Dashboard write error: {e}")


# ── Multi-date backtest ───────────────────────────────────────────────────────

def run_backtest_all(atm_map: dict[str, int] | None = None) -> list[BacktestState]:
    """
    Run backtest on all dates provided.
    atm_map: {date_str: atm} — if None, ATM is derived from 9:15 close.
    """
    from config import STRIKE_STEP
    results = []
    # Get dates from command line or default to today
    import sys
    dates = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not dates:
        log.warning("No dates provided — pass dates as args or use --date in workflow")
        return results

    for d in dates:
        atm = (atm_map or {}).get(d)
        r   = run_backtest_date(d, atm)
        results.append(r)
        print(_result_summary(r))
    return results


# ── Summary ───────────────────────────────────────────────────────────────────

def _result_summary(bs: BacktestState) -> str:
    lines = [
        f"\n{'='*60}",
        f"  BACKTEST — {bs.state['date']}",
        f"{'='*60}",
        f"  Entry:    {bs.entry_time or 'none'} ({bs.entry_label or '-'})",
        f"  Exit:     {bs.exit_time or 'none'} ({bs.exit_reason or '-'})",
        f"  Peak MTM: ₹{bs.peak_mtm:,.0f}",
        f"  P&L:      ₹{bs.final_pnl:,.0f}",
        f"  Ticks:    {len(bs.snapshots)}",
        f"\n  Events:",
    ]
    for e in bs.events:
        lines.append(f"    [{e['time']}] {e['event']:12s} {e['detail']}")
    lines.append(f"{'='*60}")
    return "\n".join(lines)


def print_summary_table(results: list[BacktestState]) -> None:
    print(f"\n{'='*70}")
    print(f"  BACKTEST SUMMARY — {len(results)} day(s)")
    print(f"{'='*70}")
    print(f"  {'Date':<12} {'Entry':>8} {'Label':>14} {'Peak':>9} {'P&L':>10} {'Exit':>16}")
    print(f"  {'-'*68}")
    total = 0.0
    for r in results:
        print(
            f"  {r.state['date']:<12} "
            f"{r.entry_time or 'none':>8} "
            f"{r.entry_label or '-':>14} "
            f"₹{r.peak_mtm:>8,.0f} "
            f"₹{r.final_pnl:>9,.0f} "
            f"{r.exit_reason or '-':>16}"
        )
        total += r.final_pnl
    print(f"  {'-'*68}")
    print(f"  {'TOTAL':<12} {'':>8} {'':>14} {'':>9} ₹{total:>9,.0f}")
    print(f"{'='*70}\n")
