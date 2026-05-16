# Stock Recommender — Full Development Log

**Owner:** Karthik Palanivel  
**Location:** Sydney, Australia  
**Personal email:** karthik.bia@gmail.com  
**GitHub:** karthikpalsg  
**Repository:** github.com/karthikpalsg/stock-recommender  
**Date built:** May 2026  

---

## What this document is

A complete record of every decision, every file created, and every problem solved when building the Stock Recommender from scratch. Written so that you — or any agent — can replicate the full system or understand any part of it without needing the original conversation.

---

## What the system does (in plain English)

Every weekday at **5am and 7am Sydney time**, an automated system:
1. Reads your personal list of 29 US stocks
2. Pulls analyst ratings, price momentum, company fundamentals, and social media sentiment for each stock
3. Scores every stock from 0–100 and ranks them
4. Emails you the full ranked list with buy/sell signals, price targets, and stop-loss levels
5. Saves every run to a growing history file for future analysis

You manage your stock list from your iPhone using a web app. Your Mac does not need to be switched on. The whole thing costs $0/month to run.

---

## System overview

```
Your iPhone (PWA app)
      ↓ add/remove stocks
GitHub repo (tickers.txt)
      ↓ read at every run
GitHub Actions (cloud scheduler)
      ↓ 5am and 7am Sydney time, Mon–Fri
Python engine (run.py)
      ↓ fetches data from 4 sources
Yahoo Finance + Finnhub + Apewisdom + Reddit
      ↓ scores and ranks all stocks
Gmail (karthik.bia@gmail.com)
      ↓ HTML email with full ranked table
JSON history file (data/history.json)
      ↓ backed up on 1st of every month
data/backups/ folder
```

---

## File structure

```
stock-recommender/
├── run.py                          ← Main engine. Do not edit.
├── config.py                       ← Your settings and API keys. Edit this.
├── tickers.txt                     ← Your stock watchlist. Edit via iPhone app.
├── requirements.txt                ← Python libraries needed.
├── .gitignore                      ← Keeps config.py and picks/ off GitHub.
├── SETUP.md                        ← Local setup guide.
├── GITHUB_SETUP.md                 ← GitHub setup guide.
├── stock-recommender.md            ← This document.
├── data/
│   ├── history.json                ← Grows after every run. Never overwritten.
│   └── backups/                    ← Monthly snapshots created on 1st of month.
├── picks/                          ← Daily markdown reports (local only).
├── app/
│   ├── index.html                  ← iPhone/iPad web app (PWA).
│   ├── manifest.json               ← Makes it installable on home screen.
│   └── icon.svg                    ← App icon.
└── .github/
    └── workflows/
        └── daily-picks.yml         ← GitHub Actions schedule and steps.
```

---

## Quick reference — commands

**Run the engine manually (on your Mac):**
```bash
cd ~/karthik-claude/stock-recommender
python3 run.py
```

**Install libraries (first time only):**
```bash
python3 -m pip install -r requirements.txt
```

**Push a change to GitHub:**
```bash
git add <filename>
git commit -m "Your description"
git push https://karthikpalsg:<token>@github.com/karthikpalsg/stock-recommender.git main
```

**iPhone app URL:**
```
https://karthikpalsg.github.io/stock-recommender/app/
```

---

## Data sources

| Source | What it provides | Cost | API key needed |
|---|---|---|---|
| Yahoo Finance (yfinance) | Prices, fundamentals, analyst upgrades/downgrades | Free | No |
| Finnhub | Analyst consensus (% bullish across all analysts) | Free tier | Yes — finnhub.io |
| Apewisdom | Reddit/social mention counts and sentiment | Free | No |
| StockTwits | Social sentiment per ticker | Free | No |

---

## Scoring model

Each stock is scored 0–100 on four signals, then combined:

| Signal | Weight | What it measures |
|---|---|---|
| Analyst | 35% | Upgrades/downgrades last 7 days + % of analysts bullish (Finnhub) |
| Momentum | 25% | Price vs 50-day moving average, 4-week return, volume |
| Fundamentals | 25% | Gap to analyst price target, revenue growth, gross margin |
| Social | 15% | Reddit mention count and direction vs yesterday |

**Signal thresholds:**
- 🟢 STRONG BUY: score ≥ 65
- 🟡 BUY: score 50–64
- ⚪ WATCH: score 35–49
- 🔴 AVOID: score < 35

**Risk settings:**
- Stop-loss: −8% from entry price
- Target return: +20% (buffer above the 15% goal)
- Hold period: 6 months

---

---

# Development Log — Step by Step

---

## Step 1 — Initial concept

**What you asked:**
Build a stock recommendation skill that recommends Data & AI stocks with high positive analyst reviews in the last 7 days. US stocks only. Strategy: buy and sell within 6 months for minimum 15% profit. Also need Twitter/social sentiment.

**What was decided:**
A daily-run scoring engine that ingests analyst actions, fundamentals, momentum signals, and social sentiment. Scored 0–100. Top 5–10 picks output daily with entry price, stop-loss, and 6-month target.

**Market data sources recommended:**
- Benzinga Pro API (~$200–500/mo) for real-time analyst ratings
- Financial Modeling Prep ($30–70/mo) for fundamentals
- Polygon.io ($30–200/mo) for prices
- Quiver Quantitative ($10–50/mo) for alt data
- X/Twitter API ($200–5,000/mo) for social — later replaced with free alternatives

**Trading strategy framing:**
- Entry: breakout from base + analyst upgrade in prior 7 days + relative strength > 80
- Stop-loss: −8% from entry, no exceptions
- Exit target: +20–25%
- Time stop: exit at 4 months if neither stop nor target hit

---

## Step 2 — POC planning (cost-effective, no Snowflake)

**What you asked:**
This is a personal project. No Snowflake subscription. How do I build a cost-effective POC for a couple of weeks before investing more?

**What was decided:**
Build the minimum that proves the signal works. Total cost: $0–$15/month.

**POC stack chosen:**
| Layer | Tool | Cost |
|---|---|---|
| Storage | DuckDB (file-based, no server) — later simplified to just pandas | $0 |
| Language | Python | $0 |
| Scheduler | GitHub Actions | $0 |
| Output | Markdown file + email | $0 |
| Social data | Apewisdom (free, no key) | $0 |
| Analyst data | Finnhub free tier | $0 |

**Free data sources selected:**
- yfinance — prices, fundamentals, analyst recommendations
- Finnhub free — analyst consensus
- Apewisdom — Reddit/social mentions
- FMP free tier — analyst price targets (later replaced by yfinance)

**POC success criteria defined:**
Does the scoring engine pick stocks that outperform the benchmark over a rolling 5–10 day window?

---

## Step 3 — No-code approach

**What you asked:**
I do not have Python skill to code. Is a no-code solution possible?

**Three options presented:**

**Option 1 — Existing tools (zero build, $0):**
- Finviz screener for analyst upgrades filter
- TradingView for technicals
- StockTwits for sentiment
- Google Sheets to track manually
- 20 minutes daily, fully manual

**Option 2 — Claude builds everything, you run one command:**
- Every line of code written by Claude
- You only: install Python, paste API keys, run `python3 run.py`
- Recommended for this project

**Option 3 — n8n visual workflow:**
- Drag-and-drop workflow builder
- Still requires 2–3 hours setup
- Better for Phase 2

**Decision:** Option 1 to start immediately + Option 2 built this session.

---

## Step 4 — IBKR watchlist integration

**What you asked:**
Do you have integration with IBKR to pick the stocks I am watching?

**What was found:**
No native IBKR integration exists. MCP registry search attempted but connection failed.

**Three options for exporting IBKR watchlist:**

**Option 1 — TWS Desktop export (easiest):**
Open IBKR Trader Workstation → right-click watchlist → Export to Excel/CSV

**Option 2 — Manual text file (recommended for POC):**
Open IBKR Mobile → note down your tickers → type into `tickers.txt`, one per line

**Option 3 — IBKR Client Portal API:**
REST endpoint `GET /v1/api/iserver/watchlists` — requires running a local Java gateway. Too complex for POC.

**Decision:** Manual tickers.txt. You said you have 20 stocks.

---

## Step 5 — Building the engine (all files created)

**What you asked:**
Implied — proceed with building the full engine.

**Files created:**

### `tickers.txt`
```
# Replace with your 20 IBKR favourite stocks
NVDA
MSFT
... (placeholder stocks)
```

### `config.py`
Settings file — the only file you need to edit:
- `SLACK_WEBHOOK_URL` — optional Slack DM
- `FINNHUB_API_KEY` — free key from finnhub.io
- `GMAIL_ADDRESS` — your Gmail
- `GMAIL_APP_PASSWORD` — 16-char app password from Google
- `SEND_EMAIL = True`
- `SCORE_WEIGHTS` — analyst 35%, momentum 25%, fundamentals 25%, social 15%
- `STOP_LOSS_PCT = 8`
- `TARGET_RETURN_PCT = 20`
- `HOLD_MONTHS = 6`
- `TOP_N_PICKS = 5`

### `requirements.txt`
```
yfinance>=0.2.28
requests>=2.31.0
pandas>=2.0.0
```

### `run.py` (456 lines)
Main engine with 6 sections:
1. `load_tickers()` — reads tickers.txt
2. `get_analyst_score()` — yfinance + Finnhub
3. `get_momentum_score()` — price vs 50-day MA, 4-week return, volume
4. `get_fundamentals_score()` — analyst target upside, revenue growth, gross margin
5. `fetch_apewisdom()` + `get_social_score()` — Reddit mentions
6. `score_all()` → `generate_report()` → `send_email()` → `send_slack()`

### `SETUP.md`
Plain-English setup guide:
- Install Python
- Open terminal in project folder
- `pip3 install -r requirements.txt`
- Edit tickers.txt
- Get Finnhub key
- Set up Slack (optional)
- Run `python3 run.py`
- Schedule with cron (later replaced by GitHub Actions)

---

## Step 6 — Loading your actual IBKR stocks

**What you asked:**
ADD THIS STOCK LIST to my tickers.txt: OKE, LITE, LUNR, COHR, IPGP, SNOW, IRON, MU, NBIS, NVDA, PLTR, ASTS, RKLB, ONDS, AMD, SOFI, NOK, PATH, SNDK, NVT, IRM, GNRC, INOD, FIVN, QUBT, QBTS, RGTI

**What changed:**
`tickers.txt` replaced with your 27 actual IBKR stocks (placeholders removed).

**Notes flagged:**
- QUBT, QBTS, RGTI — quantum computing, high volatility, thin analyst coverage
- LUNR, ASTS, RKLB, ONDS — space/deep tech, momentum and social scores carry more weight
- SNDK — recently re-listed, yfinance may have limited history
- IRON — confirmed as Ironnet Cybersecurity (bankruptcy history), advised to double-check

---

## Step 7 — First run and fixing the library error

**What you asked:**
`python3 run.py` throwing: `ModuleNotFoundError: No module named 'yfinance'`

**Root cause:**
Libraries not installed yet.

**Fix applied:**
```bash
python3 -m pip install -r requirements.txt
```
(Using `python3 -m pip` instead of `pip3` ensures packages install into the exact Python interpreter being used)

**Libraries installed:** yfinance 1.2.0, pandas 2.3.3, requests 2.32.5, and 18 dependencies.

**First successful run output:**
```
Top 5 picks — all showing ⚪ WATCH
#1 NVDA   45.2/100
#2 QUBT   44.2/100
#3 NBIS   43.1/100
```

---

## Step 8 — Why all stocks showed WATCH

**What you observed:**
All 27 stocks showing ⚪ WATCH. No BUY or STRONG BUY signals.

**Root cause 1 — It was Saturday:**
Analyst upgrades/downgrades happen Monday–Friday only. Every analyst score was 0 because there was no analyst activity that weekend.

**Root cause 2 — Wrong yfinance attribute:**
The code used `ticker.recommendations` which in yfinance 1.x returns consensus summary data, not individual analyst actions. The correct attribute is `ticker.upgrades_downgrades`.

**Root cause 3 — Thresholds too strict:**
With analyst score at 0 (35% weight), the maximum possible composite was 65. STRONG BUY threshold was 72 — impossible to reach.

**Fixes applied:**
1. Changed `ticker.recommendations` → `ticker.upgrades_downgrades` in `get_analyst_score()`
2. Added Finnhub consensus to base score (up to 60 pts baseline from bullish %)
3. Lowered thresholds: STRONG BUY ≥ 65 (was 72), BUY ≥ 50 (was 55)

**Result after fix:**
```
#1 NVDA   80.2/100  🟢 STRONG BUY
#2 NBIS   78.1/100  🟢 STRONG BUY
#3 MU     73.2/100  🟢 STRONG BUY
#4 RKLB   72.3/100  🟢 STRONG BUY
#5 RGTI   69.7/100  🟢 STRONG BUY
```

---

## Step 9 — Adding Finnhub API key

**What you asked:**
Give me steps to get a free Finnhub key.

**Steps provided:**
1. Go to finnhub.io
2. Click "Get free API key"
3. Sign up with email (no credit card)
4. Verify email
5. Copy API key from dashboard

**Your key added to config.py:**
```python
FINNHUB_API_KEY = "d83uqr1r01qkm5c9ku1gd83uqr1r01qkm5c9ku20"
```

**Free tier limits:** 60 API calls/minute — more than enough for 29 stocks (one call per ticker = 29 calls per run).

---

## Step 10 — WhatsApp notifications (attempted, failed)

**What you asked:**
How do I get output in WhatsApp?

**Approach tried:**
CallMeBot — free WhatsApp notification service.

**Steps given:**
1. Save contact: +34 644 80 03 61
2. Send WhatsApp: "I allow callmebot to send me messages"
3. Receive API key in return

**Problem:**
No response from CallMeBot — unreliable free service with no SLA.

**Decision:**
Abandoned CallMeBot. Moved to Gmail (more reliable, already have account).

---

## Step 11 — Gmail email notifications

**What you asked:**
Suggested email or Telegram as alternatives to WhatsApp.

**Gmail chosen. Setup steps:**
1. Go to myaccount.google.com
2. Search "App passwords"
3. Generate a 16-character app password
4. Add to config.py

**Your Gmail app password:** `snnv zvft bkuj wpbf`  
**Your Gmail (personal):** karthik.bia@gmail.com  
*(Note: Initially tried work email kpalanivel@meritholdings.com — failed because app password was from personal Gmail)*

**`send_email()` function added to run.py:**
- Sends HTML-formatted email
- Top 5 picks with signal breakdown
- From and To: karthik.bia@gmail.com (sends to yourself)
- Uses Gmail SMTP (smtp.gmail.com, port 587)

**First successful email:** Sent and confirmed.

---

## Step 12 — Show all 27 stocks in email (not just top 5)

**What you asked:**
Why am I only seeing 5 stocks from the 27? I want to see all 27 sorted by score.

**Root cause:**
Email function used `df.head(TOP_N_PICKS)` — limited to 5.

**Fix applied:**
Email restructured into two sections:
1. **Top 5 — full signal breakdown** (analyst detail, momentum detail, fundamentals, social)
2. **All 27 stocks — complete ranked table** sorted highest to lowest score, showing all scores per column

**Email subject line updated:**
`📈 [Run Label] Stock Picks [date] — #1 NVDA 80.2/100 | All 27 stocks`

---

## Step 13 — Location update (Singapore → Sydney)

**What you told me:**
"I am based out of Sydney, Australia."

**Changes made:**
1. `CLAUDE.md` (personal profile): Updated from "Based in Singapore" to "Based in Sydney, Australia (AEST/AEDT)"
2. `SETUP.md`: Fixed cron schedule comment — changed SGT references to AEST, updated cron from `1-5` to `2-6` (Tue–Sat) to reflect that US Monday close = Tuesday morning Sydney

---

## Step 14 — Write-up (non-technical)

**What you asked:**
Write a document about the stock recommender that can be understood by non-technical people.

**Produced:** A 600-word narrative explaining:
- The problem it solves
- The four data sources and what each one answers
- How the scoring model works
- What the output means
- The infrastructure (GitHub Actions, free)
- What the system does NOT do (execute trades, predict future)

**Then compressed to 200 words** at your request.

---

## Step 15 — GitHub setup for cloud scheduling

**What you asked:**
I do not want my Mac to be switched on. Can it run automatically in the cloud?

**Solution chosen: GitHub Actions**
- Free (2000 minutes/month)
- Runs on GitHub's cloud servers
- No Mac required
- Schedule via cron

**Files created:**

### `.gitignore`
Protects your API keys — prevents `config.py` and `picks/` from uploading to GitHub:
```
config.py
picks/
__pycache__/
*.pyc
.DS_Store
```

### `.github/workflows/daily-picks.yml`
The automation file. Steps on every run:
1. Checkout latest code from GitHub (picks up any watchlist changes)
2. Install Python 3.11
3. Install requirements
4. Write config.py from GitHub Secrets (keys never stored on GitHub)
5. Run `python run.py`
6. Commit updated `data/history.json` back to repo
7. Upload picks report as downloadable artifact (kept 30 days)

### `config.py` updated
Added `import os` and changed all values to read from environment variables first, with local values as fallback:
```python
FINNHUB_API_KEY = os.environ.get('FINNHUB_API_KEY', 'your_key_here')
GMAIL_ADDRESS   = os.environ.get('GMAIL_ADDRESS',   'karthik.bia@gmail.com')
```

---

## Step 16 — Pushing to GitHub

**Your GitHub username:** karthikpalsg  
**Repository created:** stock-recommender (public)

**Steps executed:**
```bash
git init
git add run.py requirements.txt tickers.txt SETUP.md .github
git commit -m "Initial stock recommender setup"
git branch -M main
git remote add origin https://github.com/karthikpalsg/stock-recommender.git
git push https://karthikpalsg:<token>@github.com/karthikpalsg/stock-recommender.git main
```

**Token issue:** First token lacked `workflow` scope — needed to push `.github/workflows/`. Second token had both `repo` + `workflow` scopes and succeeded.

**GitHub Secrets added via API (not manual — Claude did this):**
| Secret | Value |
|---|---|
| `FINNHUB_API_KEY` | Encrypted and uploaded |
| `GMAIL_ADDRESS` | Encrypted and uploaded |
| `GMAIL_APP_PASSWORD` | Encrypted and uploaded |

**First automated run triggered manually and confirmed:**
- Status: completed, conclusion: success
- Email received at karthik.bia@gmail.com

---

## Step 17 — JSON history for agent analysis

**What you asked:**
Store all run data with all parameters into a JSON file, incrementally updated after every run, with timestamps.

**File created:** `data/history.json`

**Structure:**
```json
{
  "total_runs": 1,
  "first_run": "2026-05-16T17:04:49",
  "last_run":  "2026-05-16T17:04:49",
  "runs": [
    {
      "run_id":              "2026-05-16T17:04:49",
      "run_date":            "2026-05-16",
      "run_time":            "17:04:49",
      "run_label":           "Manual Run",
      "run_timestamp_local": "2026-05-16T17:04:49",
      "run_timestamp_utc":   "2026-05-16T07:04:49Z",
      "timezone":            "Australia/Sydney (AEST/AEDT — DST-aware)",
      "total_stocks":        29,
      "score_weights":       { "analyst": 0.35, "momentum": 0.25, ... },
      "strategy":            { "stop_loss_pct": 8, "target_return_pct": 20, "hold_months": 6 },
      "stocks": [
        {
          "rank":                 1,
          "ticker":               "NVDA",
          "company":              "NVIDIA Corporation",
          "signal":               "STRONG BUY",
          "composite_score":      80.2,
          "price":                225.32,
          "analyst_target_price": 272.94,
          "upside_pct":           21.1,
          "stop_loss_price":      207.29,
          "scores": { "analyst": 100.0, "momentum": 68.0, "fundamentals": 70.9, "social": 70.0 },
          "signal_details": {
            "analyst":      "5 upgrade(s), 0 downgrade(s) in last 7d | 93% of 71 analysts bullish",
            "momentum":     "$225.32 | 4-wk return: +11.5% | above 50-day MA | Volume ratio: 1.1x",
            "fundamentals": "Target: $273 (+21% upside) | Revenue growth: 73% | Gross margin: 71%",
            "social":       "Rank #4 on social | 373 mentions | ↓ falling"
          }
        }
      ]
    }
  ]
}
```

**`save_json()` function added to run.py:**
- Loads existing history.json if it exists
- Appends new run record
- Updates `total_runs`, `first_run`, `last_run`
- Saves back to file

**GitHub Actions workflow updated:**
Added `permissions: contents: write` and a step to `git commit` + `git push` the updated `data/history.json` after every run — so history accumulates in the cloud across daily runs.

---

## Step 18 — Monthly backup

**What you asked:**
On the first day of every month, back up the JSON file to a separate folder with a timestamp suffix. Should run automatically.

**Function added to run.py:** `backup_json_if_first_of_month()`

**Logic:**
- Checks `datetime.now().day == 1`
- If not 1st, does nothing (silent skip)
- If 1st, copies `data/history.json` → `data/backups/history_backup_YYYY-MM-DD_HHMMSS.json`
- Verifies file sizes match
- Backup happens BEFORE today's data is appended (so it captures previous month's clean state)

**Backup folder:** `data/backups/`  
**Naming example:** `history_backup_2026-06-01_083000.json`

**Called in MAIN:**
```python
backup_json_if_first_of_month()   # runs before save_json()
save_json(results_df)
```

---

## Step 19 — Adding IREN and OKLO

**What you asked:**
Add stocks IREN and OKLO to my stocks to track.

**Change made to tickers.txt:**
Added IREN and OKLO to the bottom of the list.

**New total:** 29 stocks.

**Notes:**
- IREN — Bitcoin mining / AI compute infrastructure. Volatile, thin analyst coverage.
- OKLO — Sam Altman-backed nuclear microreactor company. Pre-revenue, fundamentals score will be low.

---

## Step 20 — iPhone/iPad web app (PWA)

**What you asked:**
How do I maintain and update my stock list on my iPhone or iPad? The updates should add, delete, and modify stocks based on instructions I provide.

**Solution built: Progressive Web App (PWA)**
- Hosted free on GitHub Pages
- No App Store, no installation
- Opens in Safari, can be added to home screen
- Looks and feels like a native app

**App URL:** `https://karthikpalsg.github.io/stock-recommender/app/`

**Files created:**

### `app/index.html`
Full single-page app with three tabs:

**Tab 1 — Watchlist:**
- Shows all current stocks as tappable chips
- Tap × on any chip to mark for removal
- Type a ticker + tap Add to add a new stock
- "Save Changes to GitHub" button — writes directly to tickers.txt via GitHub API
- Changes take effect on next scheduled engine run

**Tab 2 — Bulk Update:**
- Text area for batch instructions
- Supported commands:
  ```
  ADD AAPL          ← adds a stock
  DELETE IRON       ← removes a stock
  REMOVE IRON       ← same as DELETE
  REPLACE LUNR RKLB ← swaps one ticker for another
  # comment         ← ignored
  ```
- "Preview Changes" shows a diff (green = added, red = removed)
- "Apply & Save to GitHub" commits the changes

**Tab 3 — Settings:**
- GitHub token storage (saved in browser localStorage, never sent anywhere except GitHub)
- Token is masked in display for security
- "Clear Token" button

### `app/manifest.json`
Makes the web app installable on iPhone home screen.

### `app/icon.svg`
📈 icon for the home screen.

**GitHub Pages enabled:**
Via GitHub API — repo now serves the app at the Pages URL.

**How to add to iPhone home screen:**
1. Open the URL in Safari
2. Tap Share button (box with arrow)
3. Tap "Add to Home Screen"
4. Tap "Add"
5. App appears on home screen, opens fullscreen

**Security note:**
GitHub token stored in browser localStorage. Token needs `repo` scope. Never shared with any third party.

---

## Step 21 — Scheduled run timing (first adjustment)

**What you asked:**
Can the scheduled run time be set to an earlier time to give me a window to buy/sell before the market closes?

**US market hours in Sydney time:**
| US Event | US Eastern | Sydney AEST |
|---|---|---|
| Market opens | 9:30am ET | 11:30pm Sydney |
| Market closes | 4:00pm ET | 6:00am Sydney |

**Previous schedule:** 8:30am AEST — 2.5 hours after US close. Too late.

**New schedule:** 6:15am AEST — 15 minutes after US market close.

**Cron changed:**
```yaml
# Before:
- cron: '30 22 * * 1-5'   # 8:30am AEST

# After:
- cron: '15 20 * * 1-5'   # 6:15am AEST
```

---

## Step 22 — Two daily runs with full DST handling

**What you asked:**
Can it run two times — once at 5am (captures 80% of analyst reviews, 1 hour window to act) and once at 7am (final signal, last chance to queue). Must stay at exactly 5am and 7am regardless of daylight saving changes.

**The DST challenge:**
GitHub Actions uses UTC only. Sydney observes:
- AEST (UTC+10): April to October
- AEDT (UTC+11): October to April

To always hit 5am and 7am Sydney time, four UTC cron times are needed:

| UTC Cron | AEST result | AEDT result |
|---|---|---|
| `0 18 * * 1-5` | 4am (wrong) | **5am ✅** |
| `0 19 * * 1-5` | **5am ✅** | 6am (wrong) |
| `0 20 * * 1-5` | 6am (wrong) | **7am ✅** |
| `0 21 * * 1-5` | **7am ✅** | 8am (wrong) |

**Solution: Time guard job**

A preliminary job runs before the main engine and checks the actual Sydney clock using `TZ='Australia/Sydney'`. If it's not 5am or 7am, it sets `should_run=false` and the main job is skipped:

```yaml
jobs:
  check-sydney-time:
    outputs:
      should_run: ${{ steps.check.outputs.should_run }}
      run_label:  ${{ steps.check.outputs.run_label }}
    steps:
      - id: check
        run: |
          SYDNEY_HOUR=$(TZ='Australia/Sydney' date +%H)
          if [ "$SYDNEY_HOUR" = "05" ]; then
            echo "should_run=true" >> $GITHUB_OUTPUT
            echo "run_label=5am Early Signal" >> $GITHUB_OUTPUT
          elif [ "$SYDNEY_HOUR" = "07" ]; then
            echo "should_run=true" >> $GITHUB_OUTPUT
            echo "run_label=7am Final Signal" >> $GITHUB_OUTPUT
          else
            echo "should_run=false" >> $GITHUB_OUTPUT
          fi

  run-stock-picks:
    needs: check-sydney-time
    if: needs.check-sydney-time.outputs.should_run == 'true'
    ...
```

**Run label added throughout:**
- Email subject: `🌅 [5am Early Signal] Stock Picks...` or `📈 [7am Final Signal] Stock Picks...`
- Email header shows which run it is
- JSON history records `"run_label"` field per run
- Terminal output shows run label

**Daily schedule result:**
| Time (Sydney) | Email label | Context |
|---|---|---|
| 5:00am | 🌅 5am Early Signal | US market open ~3.5 hrs, most analyst activity done |
| 7:00am | 📈 7am Final Signal | US market closed 1 hour ago (AEST) or closing now (AEDT) |

---

## Step 23 — iPhone app credential error (fixed)

**What you reported:**
Bad credential error when deleting a stock and saving changes in the iPhone app.

**Root causes identified:**
1. **Stale file SHA** — The app stores the file's SHA when the page first loads. If GitHub Actions committed a data update in the meantime, the SHA is outdated. GitHub rejects writes with a stale SHA.
2. **`token` vs `Bearer` auth format** — The old code used `token <PAT>` in the Authorization header. `Bearer` is more universally accepted by GitHub for both classic PATs and fine-grained tokens.
3. **Hidden whitespace in stored token** — Pasting from iPhone clipboard can include a trailing newline or invisible character. `.trim()` alone doesn't catch all whitespace variants.

**Fixes applied to `app/index.html`:**

1. **Fresh SHA before every write** — `pushTickers()` now does a live GET request immediately before every PUT write to fetch the latest SHA. This makes stale-SHA conflicts impossible:
   ```javascript
   // Always fetch the freshest SHA right before writing
   const freshRes = await fetch(API, { headers: { Authorization: `Bearer ${token}` } });
   fileSHA = freshData.sha;   // always use the live SHA
   ```

2. **Switched to `Bearer` auth** — All API calls updated from `token ${token}` to `Bearer ${token}`.

3. **Specific error messages** — 401 and 409 now give clear, actionable messages:
   - 401: "Invalid token — go to Settings, clear it, and paste it again"
   - 409: "File was just updated elsewhere — please try saving again" (auto-reloads)

4. **Stronger token sanitisation** — On save, strips all whitespace (not just leading/trailing) and validates the token starts with `ghp_` or `github_pat_` before storing.

5. **"Test Connection" button added** — New button in Settings tab. Tapping it pings GitHub, reports whether the token is valid, and refreshes the cached SHA. Green ✅ = working, red message = what to fix.

**To apply on your iPhone:**
Open the app in Safari → hard reload (close and reopen) → Settings tab → tap "Test Connection" → should show ✅ — then delete a stock and save.

---

---

# API keys and credentials reference

> ⚠️ Store your credentials securely — never commit real values to a public repo. Use GitHub Secrets for all sensitive values.

| Item | Where to find it |
|---|---|
| Finnhub API key | finnhub.io → Dashboard → API Key |
| Gmail address | Your personal Gmail used for sending reports |
| Gmail app password | Google Account → Security → 2-Step Verification → App Passwords |
| GitHub username | `karthikpalsg` |
| GitHub repo | `stock-recommender` |
| GitHub PAT | GitHub → Settings → Developer Settings → Personal Access Tokens |
| App URL | `https://karthikpalsg.github.io/stock-recommender/app/` |

---

# Replication guide — build from scratch

If you or an agent needs to rebuild this entire system from zero:

**Step 1 — Create project folder**
```bash
mkdir -p ~/karthik-claude/stock-recommender/data/backups
mkdir -p ~/karthik-claude/stock-recommender/picks
mkdir -p ~/karthik-claude/stock-recommender/app
mkdir -p ~/karthik-claude/stock-recommender/.github/workflows
```

**Step 2 — Create all files**
Recreate every file listed in the File Structure section above using the content documented in each development step.

**Step 3 — Install Python libraries**
```bash
cd ~/karthik-claude/stock-recommender
python3 -m pip install -r requirements.txt
```

**Step 4 — Add API keys to config.py**
Fill in: `FINNHUB_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`

**Step 5 — Test locally**
```bash
python3 run.py
```
Confirm email arrives at karthik.bia@gmail.com.

**Step 6 — Create GitHub repo**
- github.com → New repository → `stock-recommender` → Public
- Generate PAT with `repo` + `workflow` scopes

**Step 7 — Push to GitHub**
```bash
git init
git add run.py requirements.txt tickers.txt .gitignore .github/ app/ data/ stock-recommender.md
git commit -m "Full stock recommender setup"
git branch -M main
git remote add origin https://github.com/karthikpalsg/stock-recommender.git
git push https://karthikpalsg:<PAT>@github.com/karthikpalsg/stock-recommender.git main
```

**Step 8 — Add GitHub Secrets**
In GitHub repo → Settings → Secrets and variables → Actions → add:
- `FINNHUB_API_KEY`
- `GMAIL_ADDRESS`
- `GMAIL_APP_PASSWORD`

**Step 9 — Enable GitHub Pages**
Repo → Settings → Pages → Source: Deploy from branch → main → / (root) → Save
App will be live at `https://karthikpalsg.github.io/stock-recommender/app/`

**Step 10 — Add iPhone app to home screen**
Open app URL in Safari → Share → Add to Home Screen

**Step 11 — Set token in app**
App → Settings tab → paste GitHub PAT → Save

**Step 12 — Verify automated runs**
GitHub repo → Actions tab → watch runs appear at 5am and 7am Sydney time Tuesday–Saturday

---

*Last updated: May 2026*  
*Built in one session with Claude Code*
