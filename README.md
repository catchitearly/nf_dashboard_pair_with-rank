# Nifty Options Bot — Scientific Short Strangle Strategy

Automated options selling bot for Nifty with delta-neutral hedging, profit lock trailing stop, and 4-trade daily budget. Runs on GitHub Actions every 5 minutes during market hours.

---

## File Structure

```
.
├── main.py               # Entry point — sets up logging, calls strategy
├── strategy.py           # Orchestration — one run cycle per invocation
├── view_engine.py        # Score + label computation from straddle VWAP
├── position_manager.py   # Entry, exit, rebalance, MTM calculation
├── state_manager.py      # JSON state persistence between runs
├── fyers_data.py         # Fyers API data layer (candles, quotes, orders)
├── notifier.py           # Telegram alerts (fire-and-forget)
├── config.py             # All constants — update EXPIRY_STR each week
├── requirements.txt
├── state/                # Persisted JSON state (committed back to git)
├── logs/                 # Daily log files (committed as artifact)
└── .github/
    └── workflows/
        └── trading_bot.yml
```

---

## Setup

### 1. Fork / clone this repo to your GitHub account

### 2. Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**

| Secret name          | Value                                      |
|----------------------|--------------------------------------------|
| `FYERS_CLIENT_ID`    | Your Fyers app client ID                   |
| `FYERS_ACCESS_TOKEN` | Daily access token (see refresh note below)|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather                  |
| `TELEGRAM_CHAT_ID`   | Your chat/channel ID                       |

### 3. Update expiry each week

In `config.py`, update before Monday open:

```python
EXPIRY_DATE = "09-06-2026"   # DD-MM-YYYY
EXPIRY_STR  = "26609"        # Fyers format — verify from symbol master
```

### 4. Enable Actions

Go to **Actions** tab → Enable workflows if prompted.

### 5. Manual test run

Go to **Actions → Trading Bot → Run workflow** to trigger a single cycle manually.

---

## Fyers Access Token — Daily Refresh

Fyers access tokens expire daily. You need to refresh `FYERS_ACCESS_TOKEN` each morning before 9:15 IST.

**Option A — Manual:** Generate via Fyers API login flow, paste into GitHub Secrets.

**Option B — Automated:** Add a separate workflow that calls your token-refresh script and updates the secret using the GitHub API. Example:

```python
# refresh_token.py (run separately before market open)
import requests, os

new_token = generate_fyers_token()   # your auth flow

headers = {
    "Authorization": f"token {os.environ['PAT_TOKEN']}",  # GitHub PAT with secrets:write
    "Accept": "application/vnd.github+json",
}
# Encrypt + update secret via GitHub API
# See: https://docs.github.com/en/rest/actions/secrets
```

---

## Strategy Logic (Quick Reference)

### Entry
- After **09:45 IST**, before **13:00 IST**
- Requires label **confirmed** (2 consecutive same-label readings)
- Score absolute value ≥ 0.5 (any non-zero confirmation)
- Opens sell strangle + hedge buys per sizing table

### Position Sizing

| Label         | CE Sell | PE Sell | CE Hedge | PE Hedge |
|---------------|---------|---------|----------|----------|
| very_bullish  | 4       | 8       | 1        | 2        |
| bullish       | 4       | 6       | 1        | 2        |
| neutral       | 6       | 6       | 2        | 2        |
| bearish       | 6       | 4       | 2        | 1        |
| very_bearish  | 8       | 4       | 2        | 1        |

### Profit Lock (most important rule)

| Peak MTM     | Floor    |
|--------------|----------|
| ≥ ₹300       | 30%      |
| ≥ ₹500       | 50%      |
| ≥ ₹1,000     | 65%      |
| ≥ ₹1,500     | 75%      |

If current MTM drops below floor → **immediate full exit**.

### Exits
1. **Profit floor breached** → exit all
2. **14:30 IST force exit** → exit all  
3. **Daily loss -₹1,000** → exit all, stop for day
4. **Emergency score reversal** → exit risky leg only (CE for bullish entry, PE for bearish)

---

## Score Formula

Derived from VWAP analysis of 9 straddle strikes (ATM ± 100..400):

```
score = 0.5
      + lower_weights for each lower straddle close > VWAP  (nearest first: [4,3,2,2])
      - upper_weights for each upper straddle close > VWAP  (farthest first: [2,2,3,4])

Labels:
  very_bullish  score ≥  7.5
  bullish       score ≥  3.5
  neutral       score ≥ -1.5
  bearish       score ≥ -6.5
  very_bearish  score  < -6.5
```

---

## State JSON (state/trading_state.json)

```json
{
  "date": "2026-06-02",
  "atm": 24000,
  "entry_label": "neutral",
  "entry_time": "10:40",
  "trade_count": 2,
  "add_done": false,
  "pending_label": "bullish",
  "pending_count": 1,
  "current_label": "neutral",
  "peak_mtm": 593.0,
  "profit_floor": 296.5,
  "daily_stopped": false,
  "positions": [...],
  "closed_pnl": 0.0,
  "adj_count": 0,
  "last_adj_time": null
}
```

State is **git-committed** after every run so it persists across GitHub Actions jobs.

---

## Monitoring

All key events sent to Telegram:
- 🟢 Entry details (strikes, lots, label)
- 🔒 Profit floor set / updated
- ⚠️ Profit floor hit → exiting
- 🚨 Emergency exit (risky leg closed)
- ♻️ Rebalance triggered
- 🛑 Daily loss limit
- ✅ / 🔴 Exit with P&L

---

## Updating for Next Expiry

1. Check Fyers symbol master for new expiry string
2. Update `config.py`:
   ```python
   EXPIRY_DATE = "09-06-2026"
   EXPIRY_STR  = "26609"
   ```
3. Commit and push before Sunday midnight

---

## Dry Run Mode

If `FYERS_CLIENT_ID` or `FYERS_ACCESS_TOKEN` are not set (or empty), the bot runs in **dry-run mode** — all logic executes normally but orders are logged instead of placed. Useful for testing the strategy flow without live trading.
