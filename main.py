"""
main.py
Entry point.

Modes:
  python main.py                               # live trading (one cycle)
  python main.py --backtest --date 2026-05-27  # backtest one date (fetches real Fyers data)
  python main.py --backtest --date 2026-05-27 --atm 24000  # override ATM
  python main.py --test-date 2026-05-25 --tick 5           # single tick test (uses hardcoded snapshots)
"""

import argparse
import logging
import os
import sys
from datetime import date


def _setup_logging(log_to_file: bool = True, label: str = "") -> None:
    from config import LOG_DIR
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt  = logging.Formatter(
        "%(asctime)s,%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        root.addHandler(ch)
    if log_to_file:
        suffix = f"_{label}" if label else ""
        log_file = os.path.join(LOG_DIR, f"{date.today()}{suffix}.log")
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Nifty Options Bot")
    p.add_argument("--backtest",   action="store_true",
                   help="Run full backtest using real Fyers historical data")
    p.add_argument("--date",       metavar="YYYY-MM-DD",
                   help="Date for backtest (required with --backtest)")
    p.add_argument("--atm",        type=int, default=None,
                   help="Override ATM strike (optional with --backtest)")
    p.add_argument("--test-date",  metavar="YYYY-MM-DD",
                   help="Run single-tick test using hardcoded snapshots")
    p.add_argument("--tick",       type=int, default=0,
                   help="Tick index for --test-date mode")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    # ── Backtest mode (real Fyers data) ─────────────────────────────────────
    if args.backtest:
        if not args.date:
            print("ERROR: --backtest requires --date YYYY-MM-DD")
            sys.exit(1)
        _setup_logging(log_to_file=True, label=f"backtest_{args.date}")
        log = logging.getLogger("main")
        log.info(f"▶ BACKTEST MODE — date={args.date} atm={args.atm or 'auto'}")
        try:
            from backtest_engine import run_backtest_date, print_summary_table
            result = run_backtest_date(args.date, atm=args.atm)
            print_summary_table([result])
        except Exception as e:
            logging.getLogger("main").exception(f"Backtest error: {e}")
            sys.exit(1)
        return

    # ── Single-tick test mode (hardcoded snapshots) ──────────────────────────
    if args.test_date:
        _setup_logging(log_to_file=False)
        log = logging.getLogger("main")
        log.info(f"▶ TEST MODE — date={args.test_date} tick={args.tick}")
        try:
            import test_mode
            from backtest_data import HISTORICAL_DATA
            test_mode.activate_test_mode(args.test_date, tick_index=args.tick)
            snaps = HISTORICAL_DATA.get(args.test_date, [])
            if args.tick < len(snaps):
                snap = snaps[args.tick]
                if snap.get("mtm_actual") is not None:
                    test_mode.set_injected_mtm(snap["mtm_actual"])
            import strategy
            strategy.run_cycle()
            log.info("✓ Test cycle complete")
        except Exception as e:
            logging.getLogger("main").exception(f"Test mode error: {e}")
            sys.exit(1)
        return

    # ── Live trading mode ────────────────────────────────────────────────────
    _setup_logging(log_to_file=True)
    log = logging.getLogger("main")
    log.info("▶ main.py started")
    try:
        import strategy
        strategy.run_cycle()
    except Exception as e:
        log.exception(f"Unhandled exception: {e}")
        try:
            import notifier
            notifier.alert_error("main.py", str(e))
        except Exception:
            pass
        sys.exit(1)
    log.info("✓ main.py finished")


if __name__ == "__main__":
    main()
