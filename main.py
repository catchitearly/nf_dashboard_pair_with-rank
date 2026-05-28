"""
main.py
Entry point for the trading bot.
Sets up structured logging to both console and daily log file.
Calls strategy.run_cycle() — one complete strategy iteration.
Any uncaught exception is caught here and sent to Telegram.
"""

import logging
import os
import sys
from datetime import date

from config import LOG_DIR


def _setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, f"{date.today()}.log")

    fmt = logging.Formatter(
        "%(asctime)s,%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)


def main() -> None:
    _setup_logging()
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
