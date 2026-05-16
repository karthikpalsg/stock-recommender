# Stock Recommendation Engine — Setup Guide

No coding needed. Follow these steps in order. Takes about 20 minutes the first time.

---

## Step 1 — Install Python (skip if already installed)

1. Go to: **https://www.python.org/downloads/**
2. Click the big yellow "Download Python" button
3. Open the downloaded file and click through the installer
4. ✅ On the first screen, tick **"Add Python to PATH"** before clicking Install

**Verify it worked**: Open Terminal (press `Cmd + Space`, type "Terminal", hit Enter)
Type this and press Enter:
```
python3 --version
```
You should see something like `Python 3.12.x`. If you do, move to Step 2.

---

## Step 2 — Open Terminal in the project folder

In Terminal, type this and press Enter:
```
cd ~/karthik-claude/stock-recommender
```

---

## Step 3 — Install the required libraries (one time only)

Copy and paste this into Terminal, press Enter:
```
pip3 install -r requirements.txt
```
Wait for it to finish. You'll see a lot of text — that's normal.

---

## Step 4 — Add your IBKR stocks

1. Open the file **`tickers.txt`** (in the stock-recommender folder)
2. Replace the example tickers with your 20 IBKR favourites
3. One ticker per line, e.g.:
```
NVDA
MSFT
AAPL
```
4. Save the file

---

## Step 5 — Get your free Finnhub API key (optional but recommended)

Finnhub adds better analyst upgrade/downgrade data. It's free.

1. Go to: **https://finnhub.io** → click Sign Up (takes 1 minute)
2. After signup, your API key is shown on the dashboard
3. Open **`config.py`** in a text editor
4. Find the line: `FINNHUB_API_KEY = ""`
5. Add your key inside the quotes: `FINNHUB_API_KEY = "your_key_here"`
6. Save the file

---

## Step 6 — Set up Slack notifications (optional)

Skip this if you don't need Slack alerts. Picks still save to the `picks/` folder.

1. Go to: **https://api.slack.com/apps**
2. Click **"Create New App"** → **"From Scratch"** → give it any name → choose your Merit Holdings workspace
3. Under **"Features"** → click **"Incoming Webhooks"** → toggle it ON
4. Click **"Add New Webhook to Workspace"** → choose **your own DM** as the channel
5. Copy the webhook URL (starts with `https://hooks.slack.com/...`)
6. Open **`config.py`** → paste it into `SLACK_WEBHOOK_URL = ""`
7. Save the file

---

## Step 7 — Run the engine

In Terminal (make sure you're in the stock-recommender folder):
```
python3 run.py
```

It will take about 1–2 minutes to analyse all 20 stocks.
When done, you'll see your top picks printed in the Terminal.
The full report is saved in the **`picks/`** folder as a Markdown file.

---

## Running it daily (automated, no Terminal needed)

To run automatically every weekday at 4:30pm (after US market close):

1. Open Terminal
2. Type `crontab -e` and press Enter
3. Press `i` to enter edit mode
4. Paste this line (replace YOUR_USERNAME with your Mac username):
```
30 8 * * 2-6 cd /Users/YOUR_USERNAME/karthik-claude/stock-recommender && python3 run.py
```
*(Note: 8:30am AEST Sydney ≈ 4:30pm US Eastern the previous day. Runs Tue–Sat to capture Mon–Fri US market close. Adjust to 6:30am in Australian summer when US is on EDT.)*
5. Press `Esc`, then type `:wq` and press Enter to save

The engine will now run automatically every weekday morning (your time).

---

## Understanding your report

Each pick shows:

| Field | What it means |
|-------|---------------|
| **Score** | 0–100 composite. Above 55 = worth buying |
| **Target** | Analyst consensus price target |
| **Upside %** | How far the stock could run to hit the target |
| **Stop Loss** | Sell immediately if price hits this level (protects your capital) |
| 🟢 STRONG BUY | Score ≥ 72 — multiple signals aligned |
| 🟡 BUY | Score 55–71 — solid signal |
| ⚪ WATCH | Score 40–54 — wait for a better entry |
| 🔴 AVOID | Score < 40 — signals weak or negative |

---

## Files in this project

| File | Purpose |
|------|---------|
| `run.py` | Main engine — do not edit |
| `config.py` | Your settings — only file you need to edit |
| `tickers.txt` | Your 20 IBKR stock tickers |
| `requirements.txt` | Libraries list — do not edit |
| `picks/` | Folder where daily reports are saved |

---

## Problems?

**"Module not found" error** → Run `pip3 install -r requirements.txt` again

**"No such file or directory"** → Make sure you ran `cd ~/karthik-claude/stock-recommender` first

**All scores are 0** → Yahoo Finance may be rate-limiting. Wait 5 minutes and try again.

**Slack not working** → Double-check the webhook URL in config.py. Make sure there are no spaces around it.
