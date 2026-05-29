"""
main.py
Entry point for the trading bot.

Modes:
  python main.py                          # live trading
  python main.py --test-date 2026-05-25  # single-date test run (one tick at index 0)
  python main.py --backtest               # full backtest on all historical dates
  python main.py --backtest --date 2026-05-25  # backtest single date
"""

import argparse
import logging
import os
import sys
from datetime import date


def _setup_logging(log_to_file: bool = True) -> None:
    from config import LOG_DIR
    os.makedirs(LOG_DIR, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s,%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    if log_to_file:
        log_file = os.path.join(LOG_DIR, f"{date.today()}.log")
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Nifty Options Trading Bot")
    p.add_argument(
        "--test-date",
        metavar="YYYY-MM-DD",
        help="Run a single test cycle using historical data for this date",
    )
    p.add_argument(
        "--backtest",
        action="store_true",
        help="Run full backtest on all available historical dates",
    )
    p.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="With --backtest: restrict to a single date",
    )
    p.add_argument(
        "--tick",
        type=int,
        default=0,
        help="With --test-date: which tick index to run (default=0)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    # ── Backtest mode ────────────────────────────────────────────────────────
    if args.backtest:
        _setup_logging(log_to_file=False)
        log = logging.getLogger("main")
        log.info("▶ BACKTEST MODE")
        try:
            from backtest_engine import run_backtest_date, run_backtest_all, print_summary_table
            if args.date:
                results = [run_backtest_date(args.date)]
            else:
                results = run_backtest_all()
            print_summary_table(results)
        except Exception as e:
            logging.getLogger("main").exception(f"Backtest error: {e}")
            sys.exit(1)
        return

    # ── Test-date mode ───────────────────────────────────────────────────────
    if args.test_date:
        _setup_logging(log_to_file=False)
        log = logging.getLogger("main")
        log.info(f"▶ TEST MODE — date={args.test_date} tick={args.tick}")
        try:
            import test_mode
            import state_manager as sm
            import strategy

            test_mode.activate_test_mode(args.test_date, tick_index=args.tick)

            from backtest_data import HISTORICAL_DATA
            snaps = HISTORICAL_DATA.get(args.test_date, [])
            if args.tick < len(snaps):
                snap = snaps[args.tick]
                if snap.get("mtm_actual") is not None:
                    test_mode.set_injected_mtm(snap["mtm_actual"])

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
        log.exception(f"Unhandled exception in run_cycle: {e}")
        try:
            import notifier
            notifier.alert_error("main.py", str(e))
        except Exception:
            pass
        sys.exit(1)

    log.info("✓ main.py finished")


if __name__ == "__main__":
    main()
