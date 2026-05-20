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

Every weekday at **5am, 7am, and 12pm Sydney time**, an automated system:
1. Reads your personal list of ~47 US stocks
2. Pulls analyst ratings, price momentum, company fundamentals, social media sentiment, and SEC filing sentiment for each stock
3. Scores every stock from 0–100 and ranks them
4. Emails you the full ranked list with buy/sell signals, price targets, and stop-loss levels
5. Saves every run to a JSON history file — one record per day, overwriting if the engine re-runs

You manage your stock list from your iPhone using a web app. Your Mac does not need to be switched on. The whole thing costs $0/month to run.

The **dashboard PWA** shows each day's results as a tab. The first tab (📋 Today) always shows the latest run's current buy/sell recommendation with cross-run comparison tables and an auto-generated analytical write-up. Current-month days appear as individual tabs labelled `21-May`, `20-May`… At the start of a new month, previous-month tabs collapse to a single archive tab labelled `May-2026`, `Apr-2026`… Every card shows a 1M/3M sparkline trend chart, a filter panel, a 📊 Brief summary modal with image download, and analyst news deep links.

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
├── tickers.txt                     ← Your stock watchlist. Edit via Watchlist app.
├── requirements.txt                ← Python libraries needed.
├── .gitignore                      ← Keeps config.py and picks/ off GitHub.
├── SETUP.md                        ← Local setup guide.
├── GITHUB_SETUP.md                 ← GitHub setup guide.
├── stock-recommender.md            ← This document.
├── data/
│   ├── history.json                ← One record per day. Same-day re-runs overwrite. 1st-of-month collapses prior month.
│   └── backups/                    ← Monthly snapshots created on 1st of month.
├── picks/                          ← Daily markdown reports (local only).
├── app/
│   ├── auth.js                     ← Shared GitHub-linked PAT authentication. Included by both apps.
│   ├── index.html                  ← Watchlist manager PWA (add/remove tickers).
│   ├── dashboard.html              ← Picks Dashboard PWA (📋 Today tab, day tabs, archive, Dictionary tab, Brief modal, news links, image download).
│   ├── family-guide.html           ← Standalone shareable reference guide (light theme, NVDA card example).
│   ├── manifest.json               ← PWA manifest for Watchlist app.
│   ├── dashboard-manifest.json     ← PWA manifest for Dashboard app.
│   └── icon.svg                    ← Shared app icon.
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

**iPhone app URLs:**
```
Watchlist manager:  https://karthikpalsg.github.io/stock-recommender/app/
Picks dashboard:    https://karthikpalsg.github.io/stock-recommender/app/dashboard.html
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

Each stock is scored 0–100 on five signals, then combined:

| Signal | Weight | What it measures |
|---|---|---|
| Analyst | 30% | Upgrades/downgrades last 7 days + % of analysts bullish (Finnhub) |
| Momentum | 25% | Price vs 50-day moving average, 4-week return, volume |
| Fundamentals | 25% | Gap to analyst price target, revenue growth, gross margin |
| Social | 10% | Reddit mention count and direction vs yesterday |
| Filing | 10% | SEC filing sentiment (Claude AI — requires Anthropic API key; defaults to 50/100 neutral if not configured) |

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

## Step 24 — Data Glimpse Stocks Dashboard PWA

**What you asked:**
Build a hosted app that shows the final output of the engine with date in the top-right corner (bold), auto-refreshes after every run, shows current week's runs in separate tabs, and archives past months — all addable to iPhone home screen.

**Files created:**

### `app/dashboard.html`
Full PWA that reads directly from `data/history.json` on GitHub (public, no token required to read). Key features:

**Tab system:**
- Current month: one tab per day, labelled `17-May`, `16-May`… The engine runs at 5am and 7am but the 7am result overwrites the 5am one (same for any manual run). Only one tab per day is ever shown. Most recent tab is selected on open.
- Monthly archive: one tab per past month labelled `May-2026`, `Apr-2026`… Appears after a thin divider. On the 1st of each month, the previous month's day tabs collapse to a single archive tab showing the last day's data.
- Switching tabs updates the bold date in the top-right header.

**Header date (top right, bold):**
- On week tab: shows full day + date (e.g. `Friday 16 May`) in bold green, with `5am Early Signal` or `7am Final Signal` below it in muted text.
- On archive tab: shows `May 2026` in bold green with `Monthly Archive` below.

**Auto-refresh:** Polls `raw.githubusercontent.com` every 5 minutes. If `last_run` timestamp has changed, rebuilds all tabs and content silently. Shows "Updated X min ago" in the subtitle.

**Sticky layout:** Header and tab bar stay fixed at the top. Tab bar `top` offset is calculated dynamically in JS using `ResizeObserver` so it works correctly on all iPhone models including Dynamic Island and notch devices.

### `app/dashboard-manifest.json`
Separate PWA manifest for the dashboard — allows it to be installed as its own home screen icon, independent of the Watchlist app.

**To add to iPhone home screen:**
Open the dashboard URL in Safari → Share → Add to Home Screen → "DG Stocks"

---

## Step 25 — Grid layout with external source links

**What you asked:**
Convert the layout to 3 columns and link each stock card to external pages (financial data, news, analyst reviews) opening in a new tab.

**Layout change (initial):**
Stock list redesigned from a single-column list to a `CSS grid` with `repeat(3, 1fr)`. Later changed to 2-column (`repeat(2, 1fr)`) in Step 29 for better readability on iPhone.

**Card design (per stock):**
- Coloured top border matching signal (green / amber / dark / red) — instant visual scan
- Rank, ticker, signal label, composite score, score bar, upside %, current price → analyst target, stop loss
- Three source-link pills at the bottom of every card

**Three source links per card — each opens in a new tab:**

| Pill | Destination | What it shows |
|---|---|---|
| **YF** | `finance.yahoo.com/quote/{TICKER}` | Live price, news, analyst ratings, financial statements |
| **FV** | `finviz.com/quote.ashx?t={TICKER}` | Chart, ratios, insider trading, news snapshot — all on one page |
| **SA** | `seekingalpha.com/symbol/{TICKER}` | Analyst articles, buy/sell ratings, earnings forecasts |

**Tap behaviour:**
- Tap anywhere on the card body → opens Yahoo Finance
- Tap YF / FV / SA pills specifically → opens that source (uses `stopPropagation` so card click doesn't also fire)

**Signal legend strip** added below the run meta bar.

---

## Step 26 — App security: PIN gate (superseded by Step 27)

**What you asked:**
The apps were open to anyone with the URL. Add a one-time login that, once set up, covers all apps at the same GitHub Pages origin without re-asking.

**What was built (initial version):**
A client-side PIN gate in `app/auth.js`, included as the first `<script>` in both `index.html` and `dashboard.html`.

**How it worked:**
- Body hidden immediately via injected `<style>` tag — zero flash of unprotected content
- First visit: "Create your 6-digit PIN" screen with a native number pad and 6 dot indicators
- Correct setup: PIN hashed with SHA-256 + site-specific salt, stored in `localStorage`
- Subsequent visits: enter PIN → hash compared → unlock
- 🔒 button injected into the header to lock the session

**Limitation:** PIN was not linked to any external identity. Anyone who obtained the PIN could access the app. This led to Step 27.

---

## Step 27 — GitHub-linked authentication (replaced PIN gate)

**What you asked:**
Security should be linked to your GitHub account — require GitHub approval or SMS on any new device setup. Once set up, all apps at the same origin should work.

**Why pure GitHub OAuth can't work on a static site:**
A proper "click Approve on GitHub" OAuth flow requires a backend server to hold the `client_secret` and handle the OAuth callback. GitHub Pages is static-only — no server. So traditional OAuth is not possible without adding a backend (e.g. Cloudflare Workers).

**What was built instead — GitHub PAT authentication:**
The `auth.js` file was completely rewritten to use GitHub Personal Access Tokens as the authentication credential. This achieves the requirement because:

- Creating or retrieving a GitHub PAT requires your **GitHub password + your 2FA method (SMS or authenticator)**. Your GitHub SMS code IS the "SMS sent to mobile" gate.
- The token is validated **live against the GitHub API** on every new device and re-checked every 24 hours on existing devices.
- Only tokens belonging to `@karthikpalsg` are accepted — any other account is rejected.
- **Revoking the token on github.com instantly locks every device** on the next 24-hour validation cycle.

**Authentication flow (new device):**

```
Open app → "Connect to GitHub" screen
      ↓
Tap "↗ Open GitHub token page" (built into the screen)
      ↓
GitHub asks for: password + SMS/authenticator code  ← 2FA gate
      ↓
Generate token with read:user scope → copy it
      ↓
Paste into app → tap "Connect to GitHub"
      ↓
App calls GET https://api.github.com/user
      ↓
Confirms login === 'karthikpalsg' → access granted
      ↓
Token + timestamp stored in localStorage
```

**Subsequent visits (same device):**
- If validated within the last 24 hours: app opens instantly, no network call
- If 24 hours have elapsed: silent background re-check 2 seconds after content loads
- If token revoked mid-session: red banner shown, full lock on next open

**Network offline:** App opens normally using the cached token — graceful offline support.

**Security controls:**

| Action | Where | Effect |
|---|---|---|
| Open on a new device | App | Must provide GitHub PAT (requires GitHub password + 2FA) |
| Background re-check | Automatic, every 24h | Confirms token is still valid on GitHub |
| Revoke access on all devices | github.com/settings/tokens | App locks on next validation cycle |
| Lock this device manually | 🔒 button in header | Clears token, requires re-entry on next open |

**Key code in `app/auth.js`:**
```javascript
// Called on every new device and every 24 hours thereafter
validateToken(token)
  .then(function(user) {
    if (!user)                    // 401 from GitHub — revoked
    if (user.login !== 'karthikpalsg')  // wrong account — rejected
    localStorage.setItem(VALID_KEY, Date.now())  // mark as validated
  })
```

Both `dashboard.html` and `index.html` include `<script src="auth.js"></script>` as their first script. Because they share the same `karthikpalsg.github.io` origin, one token entry covers both apps.

---

## Step 28 — Analyst review section on dashboard cards

**What you asked:**
Show analyst upgrade/downgrade activity and overall analyst sentiment on each card in the dashboard.

**What was built:**
An analyst summary section added to the bottom of every card, parsed from the existing `signal_details.analyst` string already stored in `history.json` — no additional API calls required.

**Data string format (example):**
```
"5 upgrade(s), 0 downgrade(s) in last 7d | 93% of 71 analysts bullish"
```

**Parsed with regex in `parseAnalyst(detail)`:**
- `↑5` and `↓0` — upgrade and downgrade counts in last 7 days (larger bold text, green/red)
- Bullish % bar — fills to 93%, colour-coded: green ≥ 60%, amber 40–59%, red < 40%
- Analyst count — "71 analysts" (plain text, dark, readable)
- Bullish label — "93% bullish" (bold, coloured by threshold)

**Card visual layout (analyst section, below stop loss):**
```
↑5        LAST 7D       ↓0
━━━━━━━━━━━━━━━━━━━━━━━━━━━ (93% full green bar)
71 analysts              93% bullish
```

---

## Step 29 — Dashboard redesign: light grey theme, 2-column grid, new name

**What you asked:**
Make the background light grey, improve visibility of the number of analysts / current-to-estimated price / stop value, change name from StockPicks to Data Glimpse Stock Recommender, and move to a 2-column layout for readability.

**Theme change — dark → light:**

| CSS variable | Before | After |
|---|---|---|
| `--bg` | `#0a0a0a` (black) | `#f0f2f5` (light grey) |
| `--surface` | `#161616` | `#e2e5ea` |
| `--card` | `#1e1e1e` | `#ffffff` (white) |
| `--border` | `#2a2a2a` | `#cbd0d8` |
| `--green` | `#00c896` | `#00a876` (deeper for light bg) |
| `--amber` | `#f59e0b` | `#d97706` |
| `--red` | `#ff4d4d` | `#dc2626` |
| `--muted` | `#555` | `#6b7280` |
| `--text` | `#f0f0f0` | `#111827` (dark) |

**Grid:** `repeat(3, 1fr)` → `repeat(2, 1fr)` with `gap: 9px`

**Font visibility improvements:**

| Element | Before | After |
|---|---|---|
| Price row (`$225 → $273`) | 8.5px, muted colour | 11px, bold, dark text |
| Stop loss | same as price | bold red `.stop-value` span |
| Analyst count | 7.5px, muted | 10px, font-weight 600, dark text |
| Bullish % | 7.5px | 11px, font-weight 800 |
| Upgrade/downgrade arrows | 10px | 13px |

**Name change:**
- `<title>` and `<meta>` updated to `Data Glimpse Stocks`
- Header `<h1>` updated to `📈 Data Glimpse Stocks`
- PWA short name updated to `DG Stocks`
- `SIG_HEX` map updated to match new colour values

Cards also received `box-shadow: 0 1px 3px rgba(0,0,0,0.07)` for depth on the light background.

---

## Step 30 — Signal label duplication bug fixed

**Problem observed:**
Dashboard cards showed `🟢 STRONG BUY STRONG BUY` — signal label doubled.

**Root cause:**
The `signal_emoji` field in `history.json` was being populated by the engine with the full label text (e.g. `"🟢 STRONG BUY"`) rather than just the emoji `"🟢"`. The dashboard template was then concatenating it with `s.signal`:
```javascript
// Bug: signal_emoji = "🟢 STRONG BUY", s.signal = "STRONG BUY"
`${s.signal_emoji ?? ''} ${s.signal}`  →  "🟢 STRONG BUY STRONG BUY"
```

**Fix:**
Added a local `SIG_EMOJI` map keyed by signal name. Template now uses `SIG_EMOJI[s.signal]` instead of `s.signal_emoji` — fully independent of what the engine stores in the data field:
```javascript
const SIG_EMOJI = {
  'STRONG BUY': '🟢',
  'BUY':        '🟡',
  'WATCH':      '⚪',
  'AVOID':      '🔴',
};
// Fixed template:
`${SIG_EMOJI[s.signal] ?? ''} ${s.signal}`  →  "🟢 STRONG BUY"
```

---

## Step 31 — Dictionary tab + standalone family guide

**What you asked:**
Add an inline reference guide to the dashboard as a "Dictionary" tab, and create a standalone shareable HTML page with the same content for family members.

### Dictionary tab (inline, inside dashboard.html)

**What was built:**
A permanent `📖 Dictionary` tab (styled in amber) always visible at the far right of the tab bar, separated from the run tabs by a thin divider. Tapping it renders the full reference guide in the content area — no separate page, no new browser tab.

**Tab behaviour:**
- Always appended to `tabMeta` in `buildTabs()` — appears regardless of data state
- Never auto-selected on data refresh (existing run tab is preserved)
- Still available on empty/error state via modified `showEmpty()`
- Header updates to `📖 Dictionary / How to read your picks` when active

**Content rendered by `renderDictionary()`:**
1. NVDA card visual — reuses existing `.stock-card` CSS classes (matches current dashboard theme, real 16 May 2026 data), with 8 numbered callout labels beside it
2. 9-row metric breakdown table — every card element explained with NVDA as the example, colour-coded value chips (`.mv`, `.mv.g`, `.mv.r`, `.mv.a`)
3. 4-signal legend grid — STRONG BUY / BUY / WATCH / AVOID with score thresholds
4. 5am vs 7am run schedule cards
5. 5-step iPhone setup guide with `dict-step-url` styled URL block
6. Disclaimer box

### `app/family-guide.html` (standalone shareable page)

A self-contained HTML page at `https://karthikpalsg.github.io/stock-recommender/app/family-guide.html` with the same content as the Dictionary tab but accessible without authentication — shareable directly with family via link, WhatsApp, or paste into Apple Notes.

Contains: app links, NVDA card visual + 8 numbered callouts, 9-row metric table, 4-signal legend, run schedule, iPhone setup steps, disclaimer.

---

## Step 32 — Negative upside fix + Dictionary updated to explain both cases

**What you observed:**
MU card showed `+-19.4%` in red. MU was trading at **$724.66** against an analyst consensus target of **$584** — the stock had already exceeded where analysts thought it should be.

**Two things addressed:**

### 1. Display bug fixed
Code always prepended `+` to the upside value regardless of sign, producing `+-19.4%` instead of `-19.4%`.

```javascript
// Before (always added '+'):
`+${s.upside_pct.toFixed(1)}%`  →  "+-19.4%"

// After (only adds '+' when positive):
`${s.upside_pct > 0 ? '+' : ''}${s.upside_pct.toFixed(1)}%`  →  "-19.4%"
```

### 2. Dictionary tab updated
The Upside row in the metric breakdown table now explains both cases:

| Colour | Meaning |
|---|---|
| **Green (+)** | Stock is below analyst target — upside potential remains. NVDA: $225 vs target $273 = +21.1% to go. |
| **Red (−)** | Stock has already exceeded analyst target. MU: $724 vs target $584 = −19.4% — trading 19.4% above analyst consensus. Analysts likely haven't updated their targets after a strong run yet. |

**What negative upside means in practice:**
Not automatically a reason to avoid — the stock may have outrun short-term analyst models while the underlying thesis (momentum, earnings, analyst consensus sentiment) remains strong. MU scoring 73 (STRONG BUY) with 92% of 53 analysts bullish despite −19.4% upside is a classic case: the momentum is real, the targets haven't caught up yet. Analysts will either raise their targets (confirming the move) or the stock consolidates back toward $584.

The Price → Target row in the Dictionary was also updated to note that when the right number is lower than the left (e.g. `$724 → $584`), upside will show as negative in red.

---

## Step 33 — Deep Slate redesign (GitHub-dark inspired, mobile-first)

**What you asked:**
Give me a couple of design options that will make the dashboard mobile friendly and sophisticated.

**Two HTML previews built and shown:**
- **Option 1 — Deep Slate:** GitHub-dark palette, left border strip per signal, pill tabs, big score number
- **Option 2 — Signal-colour Headers:** Light background, full-bleed gradient card header per signal

**Chosen: Option 1 — Deep Slate**

**Theme change — light grey → dark slate:**

| CSS variable | Before (Step 29) | After |
|---|---|---|
| `--bg` | `#f0f2f5` | `#0d1117` |
| `--surface` | `#e2e5ea` | `#161b22` |
| `--card` | `#ffffff` | `#1c2128` |
| `--border` | `#cbd0d8` | `#30363d` |
| `--green` | `#00a876` | `#2ea043` + `--green-l: #3fb950` |
| `--amber` | `#d97706` | `#d29922` + `--amber-l: #e3b341` |
| `--red` | `#dc2626` | `#da3633` + `--red-l: #f85149` |
| `--muted` | `#6b7280` | `#8b949e` |
| `--text` | `#111827` | `#e6edf3` |

**Card layout — left border strip:**
- Removed `border-top: 3px solid [signal-colour]`
- Added `border-left: 4px solid [signal-colour]` — wider, more prominent signal cue

**Header:** Solid surface colour → `rgba(22,27,34,0.88)` with `backdrop-filter: blur(20px)` (frosted glass)

**Tab bar — pill style:**
- Underline tabs (`border-bottom: 2px solid`) → pill tabs (`border-radius: 20px; background: [signal-color]`)
- Active tab: filled green pill. Dictionary tab: amber pill.

**Score display:**
- Score font: `20px` → `32px bold` with `letter-spacing: -1.5px`
- Added `/ 100` label (`.card-score-label`) beside the score number
- Score bar moved above the score row

**Upside badge:**
- Upside % moved from inline beside score → small coloured badge in card top-right
- Green badge for positive upside, red badge for negative upside (stock exceeded analyst target)

**Signal hex colours updated (`SIG_HEX`):**

| Signal | Before | After |
|---|---|---|
| STRONG BUY | `#00a876` | `#3fb950` |
| BUY | `#d97706` | `#e3b341` |
| WATCH | `#9ca3af` | `#8b949e` |
| AVOID | `#dc2626` | `#f85149` |

Dictionary, analyst section, and legend inline colours all updated to match dark-theme variants. Source buttons changed from `rgba(0,0,0,0.04)` → `rgba(255,255,255,0.04)` for dark glass effect.

---

## Step 34 — Score + current price on same row

**What you asked:**
The current stock price needs to be displayed in the same font colour and size as the composite score — in the same row but on the right side. Font size can be reduced a bit to accommodate both.

**What changed in `dashboard.html`:**

**Card score row (before):**
```
[score bar]
80 / 100
$225.32 → $273
Stop $207.29
```

**Card score row (after):**
```
[score bar]
80 / 100              $225.32
→ $273   Stop $207.29
```

**CSS changes:**

| Property | Before | After |
|---|---|---|
| `.card-score-row` | `align-items: flex-end; gap: 6px` | `justify-content: space-between; align-items: flex-end` |
| `.card-score` font-size | `32px` | `24px` |
| `.card-score` letter-spacing | `-1.5px` | `-1px` |
| New `.score-left` wrapper | — | `display: flex; align-items: flex-end; gap: 5px` |
| New `.card-price-now` | — | `font-size: 24px; font-weight: 900; letter-spacing: -1px` |
| `.card-price` (target row) | Full price + arrow + stop on two lines | Muted `→ $target  Stop $x.xx` on one line |
| New `.card-price .tv` | — | `color: var(--text); font-weight: 700` (target value highlight) |

**JS template change:**
- Score row now wraps score + `/ 100` in `.score-left` div, and adds `<span class="card-price-now">` on the right — both coloured with `${color}` (signal colour)
- Price row changed from `` `${price} → ${target}<br>Stop...` `` to `` `→ <span class="tv">${target}</span> &nbsp; Stop...` ``

**Dictionary updated:**
- Callout #3 updated to: "80 / 100 · $225.32 — score (left) and current price (right) share one row, both in signal colour"
- Metric table "Score" row expanded to "Score & Price" — explains both halves of the row

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

Apps will be live at:
- `https://karthikpalsg.github.io/stock-recommender/app/` (Watchlist)
- `https://karthikpalsg.github.io/stock-recommender/app/dashboard.html` (Dashboard)

**Step 10 — Set up GitHub authentication on each device**
1. Open either app URL in Safari
2. The "Connect to GitHub" screen appears
3. Tap "↗ Open GitHub token page" — GitHub will ask for your password + 2FA
4. Create a token with `read:user` scope → copy it
5. Paste into the app → tap "Connect to GitHub"
6. Token is validated live → access granted
7. Both apps are now unlocked (same origin = shared auth)

**Step 11 — Set watchlist write token**
Watchlist app → Settings tab → paste a GitHub PAT with `repo` scope → Save
(This is the write token for modifying tickers.txt — separate from the auth token)

**Step 12 — Add apps to iPhone home screen**
Open each URL in Safari → Share → Add to Home Screen

**Step 13 — Verify automated runs**
GitHub repo → Actions tab → watch runs appear at 5am and 7am Sydney time Tuesday–Saturday

---

---

## Step 35 — Filter panel on dashboard

**What you asked:**
Add filters to the dashboard so you can narrow the card grid without leaving the tab. Initially requested 5 filters; revised to 2: **ticker** (multi-value) and **price range**.

**Behaviour:**
- Non-matching cards are **completely hidden** (not dimmed)
- If nothing matches, a "no results" empty state appears with an inline clear button
- A red **Clear filters** button resets both filters at once

**Multi-ticker input:**
Type one or more tickers separated by comma, space, or both — e.g. `NVDA DDOG, RKLB`. Split regex: `/[\s,]+/`

**Architecture:**
- `currentRun` holds the active run object
- `applyFilters()` reads filter inputs, filters `currentRun.stocks`, calls `renderGrid()`
- `buildCard(s)` generates card HTML per stock
- Filter panel is toggled by `filterOpen` bool; `.filter-panel.hidden` CSS class controls visibility
- Filters reset (panel closes) when switching tabs

**New CSS classes:** `.legend-row`, `.filter-btn`, `.filter-btn.active`, `.filter-panel`, `.filter-panel.hidden`, `.filter-clear-btn.has-filters`, `.result-bar`, `.result-bar.visible`, `.no-results`

---

## Step 36 — 1M/3M sparkline trend charts on cards

**What you asked:**
Add a 1-month price trend chart to each card. Chose **Option 1 (SVG sparkline)** over Option 2 (% return number only). Added a 1M/3M toggle after seeing both previews.

**Data (run.py changes):**
In `score_ticker()`, after stop_loss calculation:
```python
hist3m = ticker.history(period='3mo', interval='1d')['Close']
prices_3m = [round(float(p), 2) for p in hist3m.tolist() if not pd.isna(p)]
prices_1m = prices_3m[-22:] if len(prices_3m) >= 22 else prices_3m
```
Added to stock record in `save_json()`:
```python
"prices_1m": row["Prices 1M"],   # last 22 trading days
"prices_3m": row["Prices 3M"],   # ~63 trading days
```

**Rendering (dashboard.html):**
`buildSparkline(prices)` — pure inline SVG, no external libraries:
- Maps price array to `<polyline>` coordinates normalised to `viewBox="0 0 200 28"`
- `preserveAspectRatio="none"` stretches full card width
- Green (`#3fb950`) if `prices[last] >= prices[0]`, red (`#f85149`) if down
- Filled `<path>` area in matching colour at 8% opacity
- Endpoint `<circle r="2.5">` highlights latest price
- Header row shows period label and % return

**1M/3M toggle:**
- `trendMode` state var (`'1m'` | `'3m'`)
- `setTrend(mode)` updates button classes and calls `applyFilters()` to re-render all cards
- Toggle pill sits in the legend row between the legend items and Filter button

---

## Step 37 — One tab per day (overwrite on same-day refresh)

**What you asked:**
Keep only one tab per day. If the engine runs multiple times in a day, overwrite rather than add a new tab.

**run.py change (`save_json()`):**
Before appending, check if a run for today's date already exists:
```python
existing_idx = next((i for i, r in enumerate(history["runs"]) if r["run_date"] == today_date), None)
if existing_idx is not None:
    history["runs"][existing_idx] = run_record   # overwrite
else:
    history["runs"].append(run_record)           # new day
```

**dashboard.html changes:**
- `buildUI()` deduplicates `monthRuns` by date (keeps latest `run_id` per date) — handles any legacy history with multiple runs per day
- Tab ID changed from `'w-' + run.run_id` (timestamp) to `'w-' + run.run_date` (e.g. `'w-2026-05-17'`) — so the active tab survives a data refresh
- Tab label changed from `day + ' ' + time` to just `day` (since there is only one run per day)

---

## Step 38 — Month-boundary tab logic

**What you asked:**
On the first day of the month: rename the last day tab of the previous month to "Month Year", delete all other previous month day tabs, then create the new day tab.

**run.py change (`save_json()`):**
After same-day dedup, if `now.day == 1`:
```python
prev_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
prev_runs  = [r for r in history["runs"] if r["run_date"].startswith(prev_month)]
if len(prev_runs) > 1:
    last_day = max(prev_runs, key=lambda r: r["run_date"])["run_date"]
    history["runs"] = [r for r in history["runs"]
                       if not r["run_date"].startswith(prev_month) or r["run_date"] == last_day]
```
This fires after today's run is already in place, so the cleanup and new-day creation happen atomically.

**dashboard.html change:**
Replaced `getMondayDateStr()` (week boundary) with `getMonthStartDateStr()` (returns `'YYYY-MM-01'` in Sydney time):
- Runs with `run_date >= monthStart` → individual day tabs (`17-May`, `16-May`…)
- Older runs → one archive tab per month (`May-2026`, `Apr-2026`…)

Result: during a month you see daily tabs; at the turn of a month the previous month's tabs collapse automatically to a single archive entry.

---

---

## Step 39 — Tab label format: day-Month and Month-Year

**What you asked:**
Tabs were showing weekday names ("Sun", "Sat") because manual runs on weekends used `dayName()` which just returns the weekday. Labels should be date-based, not weekday-based. Confirmed that multiple runs on the same day overwrite the single tab — no duplicates.

**New label formats:**

| Tab type | Old label | New label |
|---|---|---|
| Current month day | `Mon`, `Sun` | `17-May`, `16-May` |
| Previous month archive | `Apr '26` | `May-2026`, `Apr-2026` |

**dashboard.html changes:**
- Removed `dayName(dateStr)` (returned weekday name — broken for weekend manual runs)
- Removed `runTimeLabel(run)` (no longer needed — time is not shown in tab label)
- Added `dayTabLabel(dateStr)` — splits `'2026-05-17'` into day `17` + month abbr `May` → `'17-May'`
- Added `archiveTabLabel(monthKey)` — splits `'2026-05'` into month abbr `May` + year `2026` → `'May-2026'`
- Updated `buildTabs()` to call the new helpers

**Algorithm (confirmed):**

Running through May into June:
```
During May:    17-May  16-May  15-May  ...  |  📖 Dictionary
June 1 run:   1-Jun  |  May-2026  |  📖 Dictionary
Mid-June:    17-Jun  16-Jun  ...  1-Jun  |  May-2026  |  Apr-2026  |  📖 Dictionary
```

Same-day re-runs (auto or manual) overwrite the existing tab — one tab per day, always.

---

## Step 40 — Three daily runs: adding 12pm midday signal

**What changed:**
A third scheduled run was added at 12pm Sydney time to capture mid-session analyst moves and price action after the US morning session.

**Workflow changes (`.github/workflows/daily-picks.yml`):**
Two new cron lines added — one for AEST, one for AEDT:
```yaml
- cron: '0  2 * * 2-6'   # 12pm AEST (Australian winter)
- cron: '0  1 * * 2-6'   # 12pm AEDT (Australian summer)
```

**Time guard updated:**
```bash
elif [ "$SYDNEY_HOUR" = "12" ]; then
  echo "should_run=true"             >> $GITHUB_OUTPUT
  echo "run_label=12pm Midday Signal" >> $GITHUB_OUTPUT
  echo "run_emoji=☀️"                >> $GITHUB_OUTPUT
```

**Context for each run:**
| Time (Sydney) | Label | Context |
|---|---|---|
| 5:00am | 🌅 5am Early Signal | Pre-market — overnight analyst revisions, futures |
| 7:00am | 📈 7am Final Signal | US close confirmed, first-hour trading captured |
| 12:00pm | ☀️ 12pm Midday Signal | Mid-session check — intraday analyst moves, price gaps |

All three runs on the same day overwrite to one tab — the latest run always wins.

---

## Step 41 — Analyst news deep links (📰+ / 📰–)

**What you asked:**
Provide links to the latest positive analyst articles for buys and latest negative articles for sells, directly from each card.

**What was built:**
Two helper functions added to `dashboard.html`:

```javascript
function getNewsLink(ticker, signal) {
  const isBuy  = signal === 'STRONG BUY' || signal === 'BUY';
  const isAvoid = signal === 'AVOID';
  const q = isBuy  ? `${ticker} stock analyst upgrade buy`
          : isAvoid ? `${ticker} stock analyst downgrade sell`
          : `${ticker} stock analyst`;
  return `https://www.google.com/search?q=${encodeURIComponent(q)}&tbm=nws&tbs=qdr:w`;
}
```

**Behaviour:**
- STRONG BUY / BUY: 📰+ badge (green) — opens Google News filtered to positive analyst upgrade news for that ticker, last 7 days
- AVOID: 📰– badge (red) — opens Google News filtered to bearish/downgrade coverage
- WATCH: 📰 (neutral grey)

**Key detail:** Uses Google News's `tbm=nws&tbs=qdr:w` URL parameters — no API key, no backend, works from any device. The news link is added alongside YF / FV / SA source buttons on every card.

---

## Step 42 — Responsive thesis text

**What you asked:**
Full analytical text on desktop, compact card view on mobile.

**What was built:**
`.summary-thesis` and `.rec-thesis` CSS classes hide longer write-up text by default on mobile, revealed at ≥700px:
```css
.summary-thesis { display: none; }
@media (min-width: 700px) { .summary-thesis { display: block; } }
```
This means the card grid stays clean and scannable on iPhone while desktop users see the full analytical paragraph per pick.

---

## Step 43 — 📊 Brief modal + image download

**What you asked:**
A button on every run tab that opens a window showing a summary of the recommendation for the day, with a link to download it as an image.

**What was built:**

### 📊 Brief button
A `📊 Brief` button in the legend bar above each run's card grid. Clicking it opens a full-screen modal sheet (`summary-overlay`) showing:
- Run label and date
- Top picks with composite score, signal, entry price, target, stop-loss
- Signal details (analyst score, momentum, fundamentals, social)
- Disclaimer

### ⬇ Download as Image
Inside the modal: a "⬇ Download as Image" button powered by **html2canvas** (CDN):
```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
```

**Off-screen clone trick** — the modal has `max-height` + `overflow: auto` for scrolling. html2canvas only captures the visible portion unless scroll is removed. The download function:
1. Clones the modal card DOM off-screen
2. Sets `max-height: none; overflow: visible` on the clone
3. Passes the clone to html2canvas
4. Removes the clone after capture

Result: the downloaded PNG contains the full summary, not just the visible scroll window.

---

## Step 44 — 📋 Today tab (current recommendation view)

**What you asked:**
A new first tab called "Current Recommendation" that always shows the latest run's full buy/sell recommendation — separate from the day tabs, always up to date.

**What was built:**
A new `📋 Today` tab always prepended to the tab bar (before the day tabs, separated by a thin divider). Styled with a green pill (`rec-tab` class) to stand out.

**Content rendered by `renderRecommendation()`:**

**Top 3 Buys** — stocks with `signal === 'STRONG BUY'` or `'BUY'`, ranked by composite score, top 3:
- Header: rank, ticker, company, current price, target, upside%, stop-loss
- Cross-run comparison table: shows Rank / Score / Analyst / Momentum / Fundamentals across up to 4 historical runs (oldest → latest). Latest column starred (★), cells colour-coded green (improved vs previous run) / red (declined)
- Auto-generated thesis paragraph: parses `signal_details.*` strings using regex to extract analyst consensus %, upgrade counts, 4-week return, whether above 50-day MA, revenue growth, gross margin

**Top 2 Sells** — stocks with `signal === 'AVOID'` AND `upside_pct < -10` (already trading above analyst target), ranked by score descending:
- Same layout: header, comparison table, sell thesis

**⬇ Download** button at the top of the recommendation view — saves the full Today tab content as a PNG using html2canvas.

**Latest run lookup:**
```javascript
const run = allRuns.find(r => r.run_id === lastRunId) ?? sortedDesc[0];
```
Uses `lastRunId` (set directly from `data.last_run` during fetch) — no sort dependency.

---

## Step 45 — History cleanup (May 16/17 removed)

**What you did:**
Removed the 2026-05-16 and 2026-05-17 run data from `data/history.json` to clean up the tab bar.

**Method:**
Python one-liner to filter runs and recompute metadata:
```python
import json
with open('data/history.json') as f: d = json.load(f)
d['runs'] = [r for r in d['runs'] if r['run_date'] >= '2026-05-18']
d['total_runs'] = len(d['runs'])
d['first_run']  = min(r['run_id'] for r in d['runs'])
d['last_run']   = max(r['run_id'] for r in d['runs'])
with open('data/history.json','w') as f: json.dump(d, f, indent=2)
```

**Result:** 6 runs → 4 runs. Remaining: 2026-05-18, 2026-05-19, 2026-05-20, 2026-05-21.

---

## Step 46 — Today tab sort bug fix (direct lastRunId lookup)

**Problem reported (twice):**
"Today tab does not show latest day data. It still shows the oldest tab data."

**Root cause:**
`allRuns` is stored in file insertion order (oldest first). The sort used arithmetic subtraction on ISO datetime strings:
```javascript
sort((a, b) => b.run_id - a.run_id)   // NaN - NaN = NaN → no reorder
```
`NaN` comparisons leave the array unchanged. `sortedDesc[0]` was always the oldest run (May 18), not the latest (May 21).

**Fix applied:**
Replaced sort-based lookup with a direct find using `lastRunId` — already set from `data.last_run` during `fetchData()`:
```javascript
const run = allRuns.find(r => r.run_id === lastRunId) ?? sortedDesc[0];
```
`lastRunId` is the authoritative `"2026-05-21T07:39:33"` value from the JSON. No sort needed, no array-order dependency.

The same fix was applied in `downloadRecAsImage()` to ensure the download filename also reflects the correct date.

---

## Step 47 — Refresh button: force re-render + mobile tap fix

**Problem reported:**
"The app refresh button does not reflect the latest changes on the mobile screen."

**Two root causes found and fixed:**

### 1. Logic: conditional buildUI() blocked re-render
`fetchData()` only called `buildUI()` when `data.last_run !== lastRunId`. On a manual refresh where data hadn't changed, the condition was false — the UI was never rebuilt, leaving any stale tab content in place.

**Fix:** Added `force` parameter to `fetchData()`:
```javascript
async function fetchData(force = false) {
  ...
  if (force || data.last_run !== lastRunId) {
    lastRunId = data.last_run;
    buildUI();
  }
}
```
`manualRefresh()` now calls `fetchData(true)` — always rebuilds on every tap.

### 2. Mobile tap target too small
The ↻ button had `padding: 0 1px` — effectively a ~14px tap area. Apple's Human Interface Guidelines recommend 44×44px minimum.

**Fix:**
```css
.btn-update {
  min-width: 36px;
  min-height: 36px;
  justify-content: center;
  touch-action: manipulation;   /* removes 300ms iOS double-tap delay */
}
```

---

*Last updated: May 2026 — Steps 1–47 complete*
*Built with Claude Code*
