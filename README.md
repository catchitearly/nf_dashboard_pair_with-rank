# Nifty Options Bot — Scientific Short Strangle Strategy

Automated options selling bot for Nifty. Runs on **GitHub Actions** — no server needed.

---

## Workflows

| File | Trigger | Purpose |
|---|---|---|
| `live_trading.yml` | Every 5 min (schedule) + manual | Runs one strategy cycle per invocation |
| `backtest.yml` | Manual only | Replays historical data through full strategy logic |
| `token_refresh.yml` | Daily 08:45 IST + manual | Generates fresh Fyers access token, stores in GitHub Secret |

---

## One-Time Setup

### Step 1 — Fork & clone this repo

```bash
git clone https://github.com/YOUR_USERNAME/nifty-options-bot
cd nifty-options-bot
```

### Step 2 — Get Fyers tokens (run locally once)

```bash
pip install requests fyers-apiv3
python token_generator.py
```

Follow the prompts. It will print `FYERS_ACCESS_TOKEN` and `FYERS_REFRESH_TOKEN`.

### Step 3 — Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value | Needed by |
|---|---|---|
| `FYERS_CLIENT_ID` | Fyers app client ID | live_trading, token_refresh |
| `FYERS_SECRET_KEY` | Fyers app secret key | token_refresh |
| `FYERS_ACCESS_TOKEN` | Today's access token (from Step 2) | live_trading |
| `FYERS_REFRESH_TOKEN` | Refresh token (from Step 2) | token_refresh |
| `TELEGRAM_BOT_TOKEN` | From @BotFather | all |
| `TELEGRAM_CHAT_ID` | Your chat/channel ID | all |
| `GH_PAT` | GitHub Personal Access Token with `secrets:write` scope | token_refresh |

**Creating GH_PAT:**
1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. Select your repo, give `Secrets` read+write permission
3. Copy token → paste as `GH_PAT` secret

### Step 4 — Update expiry each Thursday evening

In `config.py`:
```python
EXPIRY_DATE = "09-06-2026"   # DD-MM-YYYY of next expiry
EXPIRY_STR  = "26609"        # Fyers symbol suffix — verify from symbol master
```
Commit and push before Friday 9:15 IST.

### Step 5 — Enable Actions

GitHub → Actions tab → Enable workflows (if prompted).

### Step 6 — Test dry run

GitHub → Actions → **Backtest** → Run workflow → date: `all`

Then: Actions → **Live Trading** → Run workflow (with no Fyers secrets set yet = dry-run mode).

---

## Daily Operations

| Time (IST) | What happens automatically |
|---|---|
| 08:45 | `token_refresh.yml` generates fresh Fyers token |
| 09:40 | First scheduled trading cycle starts |
| 09:40–14:35 | Cycles every 5 minutes |
| 14:30 | Force-exit rule fires, all positions closed |
| After 14:35 | No more scheduled runs |

**If token refresh fails:** You get a Telegram alert. Manually run `token_generator.py`,
paste the new token into the `FYERS_ACCESS_TOKEN` secret before 09:40.

---

## Running Workflows Manually

### Backtest
```
Actions → Backtest → Run workflow
  date: 2026-05-25        # single date
  date: all               # all historical dates
  verbose: false          # true for full tick log
```

### Live trading (single manual cycle)
```
Actions → Live Trading → Run workflow
  reason: manual test
```

### Token refresh
```
Actions → Token Refresh → Run workflow
```

---

## File Structure

```
├── main.py               # Entry point (--backtest, --test-date, or live)
├── strategy.py           # Main orchestration — one cycle per run
├── view_engine.py        # Score + label from straddle VWAP
├── position_manager.py   # Orders, MTM, entry/exit/rebalance
├── state_manager.py      # JSON state persistence
├── fyers_data.py         # Fyers API data layer
├── notifier.py           # Telegram alerts
├── config.py             # All constants — update EXPIRY_STR weekly
├── backtest_data.py      # Historical snapshots (add new days here)
├── backtest_engine.py    # Backtester — replays data through strategy
├── test_mode.py          # Patches fyers_data for test/backtest
├── token_generator.py    # One-time local script to get refresh_token
├── requirements.txt
├── state/                # trading_state.json committed after every run
├── logs/                 # Daily .log files
└── .github/workflows/
    ├── live_trading.yml
    ├── backtest.yml
    └── token_refresh.yml
```

---

## Strategy Quick Reference

### Entry conditions
- Label **confirmed** (2 consecutive same readings) between 09:45–13:00 IST
- Opens sell strangle + hedge buys

### Position sizing

| Label | CE Sell | PE Sell | CE Hedge Buy | PE Hedge Buy |
|---|---|---|---|---|
| very_bullish | 4 | 8 | 1 | 2 |
| bullish | 4 | 6 | 1 | 2 |
| neutral | 6 | 6 | 2 | 2 |
| bearish | 6 | 4 | 2 | 1 |
| very_bearish | 8 | 4 | 2 | 1 |

### Profit lock (most important rule)

| Peak MTM | Floor |
|---|---|
| ≥ ₹300 | 30% |
| ≥ ₹500 | 50% |
| ≥ ₹1,000 | 65% |
| ≥ ₹1,500 | 75% |

MTM drops below floor → **immediate full exit**.

### Other exits
- **14:30 IST** force exit (all positions)
- **-₹1,000** daily loss limit (all positions, stop for day)
- **Opposite extreme score confirmed** → close risky leg only

---

## Backtest Results (from actual log data)

| Day | Strategy P&L | Actual P&L | Improvement |
|---|---|---|---|
| 2026-05-25 | -₹135 | -₹2,377 | +₹2,242 |
| 2026-05-26 | +₹255 | -₹502 | +₹757 |
| **Total** | **+₹120** | **-₹2,879** | **+₹2,999** |

---

## Adding Historical Data for Backtest

In `backtest_data.py`, add a new date entry to `HISTORICAL_DATA`:

```python
"2026-06-02": [
    {"time": "09:45", "score": -3.5, "label": "bearish",
     "spot": 24100.0, "upper_above": 2, "lower_above": 0, "mtm_actual": None},
    # ... one dict per 5-min candle
],
```

Get `upper_above` / `lower_above` from the `view_engine` log line:
```
View check | upper_below=2 upper_above=2 | lower_above=0 lower_below=4
#                           ^^^^^^^^^^^                  ^^^^^^^^^^^
#                           upper_above=2                lower_above=0
```

---

## Dry Run Mode

If `FYERS_CLIENT_ID` or `FYERS_ACCESS_TOKEN` are missing/empty, all order calls
log instead of execute. Safe for workflow testing without live trading.
