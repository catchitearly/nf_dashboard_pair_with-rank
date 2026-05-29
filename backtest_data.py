"""
backtest_data.py
Historical market snapshots extracted from actual trading logs.

Each snapshot has:
  time_ist  : "HH:MM" string
  score     : float  (as seen in logs)
  label     : str
  spot      : float
  upper_above: int  (how many of 4 upper straddles are above their VWAP)
  lower_above: int  (how many of 4 lower straddles are above their VWAP)
  mtm_actual : float | None  (actual system MTM at that point, for reference)

upper_above + upper_below = 4 always
lower_above + lower_below = 4 always

These values are reverse-computed from the log lines:
  "upper_below=X upper_above=Y lower_above=A lower_below=B"
"""

HISTORICAL_DATA: dict[str, list[dict]] = {

    "2026-05-25": [
        # ATM fixed at 24000 (9:15 candle close was 23934 → rounded to 24000? check logs)
        # Actually ATM=24000 was set on 2026-05-26 from 9:15 close 24012.55
        # May 25: spot ~23930-24031, ATM likely 24000
        {"time": "09:54", "score": -10.5, "label": "very_bearish", "spot": 23934.05, "upper_above": 4, "lower_above": 0, "mtm_actual": None},
        {"time": "09:55", "score": -10.5, "label": "very_bearish", "spot": 23930.95, "upper_above": 4, "lower_above": 0, "mtm_actual": None},
        {"time": "10:00", "score": -10.5, "label": "very_bearish", "spot": 23937.80, "upper_above": 4, "lower_above": 0, "mtm_actual": None},
        {"time": "10:05", "score":  -3.5, "label": "bearish",      "spot": 23943.75, "upper_above": 2, "lower_above": 0, "mtm_actual": None},
        {"time": "10:10", "score":  +4.5, "label": "bullish",      "spot": 23953.95, "upper_above": 0, "lower_above": 1, "mtm_actual": None},
        {"time": "10:15", "score":  +7.5, "label": "very_bullish", "spot": 23958.35, "upper_above": 0, "lower_above": 2, "mtm_actual": None},
        {"time": "10:20", "score":  +7.5, "label": "very_bullish", "spot": 23970.75, "upper_above": 0, "lower_above": 2, "mtm_actual": None},  # confirmed
        {"time": "10:25", "score":  +0.5, "label": "neutral",      "spot": 23961.55, "upper_above": 0, "lower_above": 0, "mtm_actual": None},
        {"time": "10:30", "score":  +7.5, "label": "very_bullish", "spot": 23966.45, "upper_above": 0, "lower_above": 2, "mtm_actual": None},  # entry fail
        {"time": "10:35", "score":  +7.5, "label": "very_bullish", "spot": 23974.10, "upper_above": 0, "lower_above": 2, "mtm_actual": None},  # entry fail
        {"time": "10:40", "score":  +9.5, "label": "very_bullish", "spot": 23990.80, "upper_above": 0, "lower_above": 3, "mtm_actual":    -8},  # ENTRY
        {"time": "10:46", "score":  +9.5, "label": "very_bullish", "spot": 23992.70, "upper_above": 0, "lower_above": 3, "mtm_actual":  -120},
        {"time": "10:50", "score":  +7.5, "label": "very_bullish", "spot": 23985.30, "upper_above": 0, "lower_above": 2, "mtm_actual":  -180},
        {"time": "10:55", "score":  +7.5, "label": "very_bullish", "spot": 23985.10, "upper_above": 0, "lower_above": 2, "mtm_actual":  -187},
        {"time": "11:00", "score":  +7.5, "label": "very_bullish", "spot": 23978.85, "upper_above": 0, "lower_above": 2, "mtm_actual":  -128},
        {"time": "11:05", "score":  +9.5, "label": "very_bullish", "spot": 23984.05, "upper_above": 0, "lower_above": 3, "mtm_actual":   +37},
        {"time": "11:10", "score":  +4.5, "label": "bullish",      "spot": 23977.95, "upper_above": 0, "lower_above": 1, "mtm_actual":  -105},
        {"time": "11:15", "score":  +7.5, "label": "very_bullish", "spot": 23986.15, "upper_above": 0, "lower_above": 2, "mtm_actual":   +45},
        {"time": "11:20", "score":  +4.5, "label": "bullish",      "spot": 23977.15, "upper_above": 0, "lower_above": 1, "mtm_actual":  -135},
        {"time": "11:25", "score":  +4.5, "label": "bullish",      "spot": 23979.20, "upper_above": 0, "lower_above": 1, "mtm_actual":   -75},  # confirmed bullish
        {"time": "11:30", "score":  +4.5, "label": "bullish",      "spot": 23975.70, "upper_above": 0, "lower_above": 1, "mtm_actual":   -53},
        {"time": "11:35", "score":  +0.5, "label": "neutral",      "spot": 23964.10, "upper_above": 0, "lower_above": 0, "mtm_actual":  -128},
        {"time": "11:40", "score":  +7.5, "label": "very_bullish", "spot": 23985.70, "upper_above": 0, "lower_above": 2, "mtm_actual":  +375},
        {"time": "11:45", "score":  +7.5, "label": "very_bullish", "spot": 23980.95, "upper_above": 0, "lower_above": 2, "mtm_actual":  +188},  # confirmed very_bullish
        {"time": "11:50", "score":  +7.5, "label": "very_bullish", "spot": 23983.95, "upper_above": 0, "lower_above": 2, "mtm_actual":  +195},
        {"time": "11:55", "score":  +9.5, "label": "very_bullish", "spot": 23990.45, "upper_above": 0, "lower_above": 3, "mtm_actual":  +225},
        {"time": "12:00", "score":  +7.5, "label": "very_bullish", "spot": 23980.15, "upper_above": 0, "lower_above": 2, "mtm_actual":  +120},  # 12:00 add
        {"time": "12:05", "score":  +0.5, "label": "neutral",      "spot": 23967.05, "upper_above": 0, "lower_above": 0, "mtm_actual":  -135},
        {"time": "12:10", "score":  -1.5, "label": "neutral",      "spot": 23965.75, "upper_above": 1, "lower_above": 0, "mtm_actual":   -75},  # confirmed neutral → adj1
        {"time": "12:15", "score":  -3.5, "label": "bearish",      "spot": 23962.00, "upper_above": 2, "lower_above": 0, "mtm_actual":  -143},
        {"time": "12:20", "score":  -3.5, "label": "bearish",      "spot": 23964.65, "upper_above": 2, "lower_above": 0, "mtm_actual":   -68},  # confirmed bearish → adj2
        {"time": "12:25", "score":  -1.5, "label": "neutral",      "spot": 23962.15, "upper_above": 1, "lower_above": 0, "mtm_actual":  -465},
        {"time": "12:30", "score":  -6.5, "label": "bearish",      "spot": 23954.50, "upper_above": 3, "lower_above": 0, "mtm_actual":  -367},
        {"time": "12:35", "score":  -1.5, "label": "neutral",      "spot": 23964.25, "upper_above": 1, "lower_above": 0, "mtm_actual":  -337},
        {"time": "12:40", "score":  -6.5, "label": "bearish",      "spot": 23958.90, "upper_above": 3, "lower_above": 0, "mtm_actual":  -330},
        {"time": "12:45", "score":  -6.5, "label": "bearish",      "spot": 23957.35, "upper_above": 3, "lower_above": 0, "mtm_actual":  -315},
        {"time": "12:50", "score": -10.5, "label": "very_bearish", "spot": 23937.95, "upper_above": 4, "lower_above": 0, "mtm_actual":  -135},
        {"time": "12:56", "score": -10.5, "label": "very_bearish", "spot": 23945.35, "upper_above": 4, "lower_above": 0, "mtm_actual":  -203},  # confirmed very_bearish
        {"time": "13:00", "score":  -6.5, "label": "bearish",      "spot": 23949.95, "upper_above": 3, "lower_above": 0, "mtm_actual":  -157},
        {"time": "13:06", "score":  +0.5, "label": "neutral",      "spot": 23976.25, "upper_above": 0, "lower_above": 0, "mtm_actual":  -150},
        {"time": "13:10", "score":  +4.5, "label": "bullish",      "spot": 23978.50, "upper_above": 0, "lower_above": 1, "mtm_actual":  -210},
        {"time": "13:15", "score":  +0.5, "label": "neutral",      "spot": 23977.15, "upper_above": 0, "lower_above": 0, "mtm_actual":  -120},
        {"time": "13:20", "score":  +4.5, "label": "bullish",      "spot": 23980.60, "upper_above": 0, "lower_above": 1, "mtm_actual":  -232},
        {"time": "13:25", "score":  +4.5, "label": "bullish",      "spot": 23981.75, "upper_above": 0, "lower_above": 1, "mtm_actual":  -105},  # confirmed bullish
        {"time": "13:30", "score":  +4.5, "label": "bullish",      "spot": 23981.75, "upper_above": 0, "lower_above": 1, "mtm_actual":   -90},
        {"time": "13:35", "score":  +4.5, "label": "bullish",      "spot": 23985.85, "upper_above": 0, "lower_above": 1, "mtm_actual":   -97},
        {"time": "13:40", "score":  -1.5, "label": "neutral",      "spot": 23978.75, "upper_above": 1, "lower_above": 0, "mtm_actual":   -30},
        {"time": "13:45", "score":  +0.5, "label": "neutral",      "spot": 23968.25, "upper_above": 0, "lower_above": 0, "mtm_actual":  +247},
        {"time": "13:50", "score":  +0.5, "label": "neutral",      "spot": 23973.15, "upper_above": 0, "lower_above": 0, "mtm_actual":  +195},  # confirmed neutral → adj3
        {"time": "13:55", "score":  -1.5, "label": "neutral",      "spot": 23971.85, "upper_above": 1, "lower_above": 0, "mtm_actual":  +615},
        {"time": "14:00", "score":  -3.5, "label": "bearish",      "spot": 23964.75, "upper_above": 2, "lower_above": 0, "mtm_actual":  +742},
        {"time": "14:05", "score":  +4.5, "label": "bullish",      "spot": 23976.45, "upper_above": 0, "lower_above": 1, "mtm_actual":  +285},
        {"time": "14:10", "score":  -1.5, "label": "neutral",      "spot": 23965.30, "upper_above": 1, "lower_above": 0, "mtm_actual":  +810},
        {"time": "14:15", "score":  -1.5, "label": "neutral",      "spot": 23959.15, "upper_above": 1, "lower_above": 0, "mtm_actual":  +915},
        {"time": "14:20", "score":  -1.5, "label": "neutral",      "spot": 23960.95, "upper_above": 1, "lower_above": 0, "mtm_actual":  +997},
        {"time": "14:25", "score":  +0.5, "label": "neutral",      "spot": 23967.80, "upper_above": 0, "lower_above": 0, "mtm_actual":  +833},
        {"time": "14:30", "score":  +4.5, "label": "bullish",      "spot": 23977.40, "upper_above": 0, "lower_above": 1, "mtm_actual":  +300},
        {"time": "14:40", "score":  +9.5, "label": "very_bullish", "spot": 23995.50, "upper_above": 0, "lower_above": 3, "mtm_actual":  -742},
        {"time": "14:45", "score":  +9.5, "label": "very_bullish", "spot": 24014.10, "upper_above": 0, "lower_above": 3, "mtm_actual": -1860},
        {"time": "14:50", "score": +11.5, "label": "very_bullish", "spot": 24020.30, "upper_above": 0, "lower_above": 4, "mtm_actual": -2542},
        {"time": "14:55", "score": +11.5, "label": "very_bullish", "spot": 24029.75, "upper_above": 0, "lower_above": 4, "mtm_actual": -2820},
        {"time": "15:00", "score":   None,"label": "EXIT",         "spot": 24031.05, "upper_above": 0, "lower_above": 0, "mtm_actual": -2377},
    ],

    "2026-05-26": [
        # ATM = 24000 (9:15 close 24012.55 → rounded to 24000)
        {"time": "11:26", "score":  +0.5, "label": "neutral",      "spot": 24057.00, "upper_above": 0, "lower_above": 0, "mtm_actual":   -67},  # fresh start entry
        {"time": "11:30", "score":  -3.5, "label": "bearish",      "spot": 24033.45, "upper_above": 2, "lower_above": 0, "mtm_actual":  -158},
        {"time": "11:35", "score":  +0.5, "label": "neutral",      "spot": 24049.25, "upper_above": 0, "lower_above": 0, "mtm_actual":  +157},
        {"time": "11:40", "score":  +7.5, "label": "very_bullish", "spot": 24067.30, "upper_above": 0, "lower_above": 2, "mtm_actual":  +315},
        {"time": "11:45", "score":  +0.5, "label": "neutral",      "spot": 24063.05, "upper_above": 0, "lower_above": 0, "mtm_actual":  +450},
        {"time": "11:50", "score":  +0.5, "label": "neutral",      "spot": 24059.35, "upper_above": 0, "lower_above": 0, "mtm_actual":  +405},
        {"time": "11:55", "score":  +0.5, "label": "neutral",      "spot": 24055.40, "upper_above": 0, "lower_above": 0, "mtm_actual":  +360},
        {"time": "12:00", "score":  +0.5, "label": "neutral",      "spot": 24037.20, "upper_above": 0, "lower_above": 0, "mtm_actual":  +112},  # 12:00 add
        {"time": "12:05", "score":  +0.5, "label": "neutral",      "spot": 24034.65, "upper_above": 0, "lower_above": 0, "mtm_actual":  +405},
        {"time": "12:10", "score":  +0.5, "label": "neutral",      "spot": 24029.65, "upper_above": 0, "lower_above": 0, "mtm_actual":  +435},
        {"time": "12:15", "score":  -1.5, "label": "neutral",      "spot": 24022.60, "upper_above": 1, "lower_above": 0, "mtm_actual":  +173},
        {"time": "12:20", "score":  +0.5, "label": "neutral",      "spot": 24038.80, "upper_above": 0, "lower_above": 0, "mtm_actual":  +593},
        {"time": "12:25", "score":  +0.5, "label": "neutral",      "spot": 24029.00, "upper_above": 0, "lower_above": 0, "mtm_actual":  +435},
        {"time": "12:30", "score":  +0.5, "label": "neutral",      "spot": 24027.55, "upper_above": 0, "lower_above": 0, "mtm_actual":  +390},
        {"time": "12:35", "score":  -3.5, "label": "bearish",      "spot": 24016.70, "upper_above": 2, "lower_above": 0, "mtm_actual":  +420},
        {"time": "12:40", "score":  -1.5, "label": "neutral",      "spot": 24019.40, "upper_above": 1, "lower_above": 0, "mtm_actual":  +255},
        {"time": "12:45", "score":  -6.5, "label": "bearish",      "spot": 24005.70, "upper_above": 3, "lower_above": 0, "mtm_actual":   +90},
        {"time": "12:50", "score": -10.5, "label": "very_bearish", "spot": 23987.00, "upper_above": 4, "lower_above": 0, "mtm_actual":  -330},
        {"time": "12:55", "score": -10.5, "label": "very_bearish", "spot": 23998.25, "upper_above": 4, "lower_above": 0, "mtm_actual":  -270},  # confirmed very_bearish
        {"time": "13:00", "score": -10.5, "label": "very_bearish", "spot": 23991.00, "upper_above": 4, "lower_above": 0, "mtm_actual":  -682},
        {"time": "13:05", "score": -10.5, "label": "very_bearish", "spot": 23976.05, "upper_above": 4, "lower_above": 0, "mtm_actual":  -502},
    ],
}

# Convenience lookup
AVAILABLE_DATES = sorted(HISTORICAL_DATA.keys())
ATM_BY_DATE = {
    "2026-05-25": 24000,
    "2026-05-26": 24000,
}
