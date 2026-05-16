# ============================================================
# STOCK RECOMMENDATION ENGINE — run.py
# ============================================================
# Reads your 20 tickers, scores each one daily, outputs a
# ranked pick list. Run this once a day after market close.
#
# Command: python run.py
# ============================================================

import yfinance as yf
import requests
import pandas as pd
from datetime import datetime, timedelta
import os
import json
import time
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Load your personal settings from config.py
from config import SLACK_WEBHOOK_URL, FINNHUB_API_KEY, SCORE_WEIGHTS, \
                   STOP_LOSS_PCT, TARGET_RETURN_PCT, TOP_N_PICKS, \
                   GMAIL_ADDRESS, GMAIL_APP_PASSWORD, SEND_EMAIL


# ============================================================
# HELPER: Load tickers from tickers.txt
# ============================================================
def load_tickers(file_path="tickers.txt"):
    with open(file_path, "r") as f:
        tickers = [
            line.strip().upper()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]
    print(f"  Loaded {len(tickers)} tickers: {', '.join(tickers)}")
    return tickers


# ============================================================
# SIGNAL 1: Analyst Activity (last 7 days)
# Source: Yahoo Finance via yfinance + optional Finnhub
# Score: -100 to 100 (upgrades positive, downgrades negative)
# ============================================================
def get_analyst_score(ticker_obj, symbol):
    try:
        # --- Yahoo Finance analyst recommendations ---
        recs = ticker_obj.recommendations
        upgrades = 0
        downgrades = 0
        detail_parts = []

        if recs is not None and not recs.empty:
            recs.index = pd.to_datetime(recs.index, utc=True).tz_localize(None)
            cutoff = datetime.now() - timedelta(days=7)
            recent = recs[recs.index >= cutoff]

            if not recent.empty:
                # Actions: 'up'=upgrade, 'init'=new coverage, 'reit'=reiterate, 'down'=downgrade
                upgrades   = recent[recent['Action'].isin(['up', 'init', 'reit'])].shape[0]
                downgrades = recent[recent['Action'] == 'down'].shape[0]
                detail_parts.append(f"{upgrades} upgrade(s), {downgrades} downgrade(s) in last 7d")

        # --- Optional: Finnhub for richer analyst data ---
        if FINNHUB_API_KEY:
            try:
                url = f"https://finnhub.io/api/v1/stock/recommendation?symbol={symbol}&token={FINNHUB_API_KEY}"
                resp = requests.get(url, timeout=8)
                data = resp.json()
                if data and isinstance(data, list):
                    latest = data[0]   # Most recent month
                    strong_buy  = latest.get('strongBuy', 0)
                    buy         = latest.get('buy', 0)
                    hold        = latest.get('hold', 0)
                    sell        = latest.get('sell', 0)
                    strong_sell = latest.get('strongSell', 0)
                    total_analysts = strong_buy + buy + hold + sell + strong_sell
                    if total_analysts > 0:
                        bullish_pct = round(((strong_buy + buy) / total_analysts) * 100)
                        detail_parts.append(f"{bullish_pct}% of {total_analysts} analysts bullish (Finnhub)")
            except Exception:
                pass  # Finnhub is optional, skip silently

        # Calculate score
        net = upgrades - downgrades
        score = max(-100, min(100, net * 25))   # Each net upgrade = +25 pts
        detail = " | ".join(detail_parts) if detail_parts else "No recent analyst activity"
        return score, detail

    except Exception as e:
        return 0, f"Analyst data unavailable"


# ============================================================
# SIGNAL 2: Price Momentum
# Source: Yahoo Finance (yfinance)
# Score: 0 to 100
# ============================================================
def get_momentum_score(ticker_obj, symbol):
    try:
        hist = ticker_obj.history(period="6mo")
        if hist.empty or len(hist) < 30:
            return 0, "Insufficient price history"

        current_price  = hist['Close'].iloc[-1]
        ma50           = hist['Close'].rolling(window=min(50, len(hist))).mean().iloc[-1]

        # 4-week return (approx 20 trading days)
        lookback       = min(20, len(hist) - 1)
        price_4wk_ago  = hist['Close'].iloc[-lookback]
        return_4wk     = ((current_price - price_4wk_ago) / price_4wk_ago) * 100

        # 1-week return (approx 5 trading days)
        price_1wk_ago  = hist['Close'].iloc[max(-5, -len(hist))]
        return_1wk     = ((current_price - price_1wk_ago) / price_1wk_ago) * 100

        # Volume: recent 5-day avg vs 20-day avg
        avg_vol_20d    = hist['Volume'].rolling(window=20).mean().iloc[-1]
        avg_vol_5d     = hist['Volume'].iloc[-5:].mean()
        vol_ratio      = avg_vol_5d / avg_vol_20d if avg_vol_20d > 0 else 1.0

        # Score components
        above_ma       = current_price > ma50
        ma_score       = 40 if above_ma else 0
        return_score   = max(-40, min(40, return_4wk * 2))   # ±20% 4-wk return → ±40 pts
        vol_bonus      = 15 if vol_ratio >= 1.3 else (8 if vol_ratio >= 1.1 else 0)
        momentum_1wk   = 5 if return_1wk > 0 else 0

        score = max(0, min(100, ma_score + return_score + vol_bonus + momentum_1wk))

        ma_label = "above" if above_ma else "below"
        detail = (f"${current_price:.2f} | 4-wk return: {return_4wk:+.1f}% | "
                  f"{ma_label} 50-day MA | Volume ratio: {vol_ratio:.1f}x")
        return score, detail

    except Exception as e:
        return 0, "Momentum data unavailable"


# ============================================================
# SIGNAL 3: Fundamentals
# Source: Yahoo Finance (yfinance)
# Score: 0 to 100
# ============================================================
def get_fundamentals_score(ticker_obj, symbol):
    try:
        info = ticker_obj.info

        current_price   = info.get('currentPrice') or info.get('regularMarketPrice', 0)
        target_price    = info.get('targetMeanPrice', 0)
        revenue_growth  = (info.get('revenueGrowth') or 0) * 100       # e.g. 0.25 → 25%
        gross_margin    = (info.get('grossMargins') or 0) * 100         # e.g. 0.70 → 70%
        forward_pe      = info.get('forwardPE', 0) or 0

        # Upside to analyst consensus target
        upside = 0
        if current_price > 0 and target_price > 0:
            upside = ((target_price - current_price) / current_price) * 100

        # Score: upside drives 60 pts, growth 25 pts, margin 15 pts
        upside_score  = max(0, min(60, upside * 1.5))         # 40% upside = 60 pts
        growth_score  = max(0, min(25, revenue_growth * 0.6)) # 40% growth = 24 pts
        margin_score  = max(0, min(15, gross_margin * 0.2))   # 75% margin = 15 pts

        score = min(100, upside_score + growth_score + margin_score)

        target_str = f"${target_price:.0f} ({upside:+.0f}% upside)" if target_price else "no target"
        detail = (f"Target: {target_str} | "
                  f"Revenue growth: {revenue_growth:.0f}% | "
                  f"Gross margin: {gross_margin:.0f}%")
        return score, detail

    except Exception as e:
        return 0, "Fundamentals data unavailable"


# ============================================================
# SIGNAL 4: Social Sentiment
# Source: Apewisdom (free, no API key) — Reddit & social
# Score: 0 to 100
# ============================================================
def fetch_apewisdom():
    """One API call fetches top 50 mentioned stocks from Reddit/social. Free."""
    try:
        url  = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/1"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        results = data.get('results', [])
        print(f"  Apewisdom: {len(results)} stocks tracked on social media")
        return results
    except Exception:
        print("  Apewisdom unavailable — social scores set to neutral")
        return []


def get_social_score(symbol, apewisdom_data):
    try:
        entry = next((x for x in apewisdom_data if x.get('ticker') == symbol), None)

        if not entry:
            return 30, "Not in top social mentions (neutral score)"

        rank             = entry.get('rank', 999)
        mentions         = entry.get('mentions', 0)
        mentions_24h_ago = entry.get('mentions_24h_ago', mentions)
        mention_delta    = mentions - mentions_24h_ago

        # Base score from rank
        if rank <= 5:
            rank_score = 70
        elif rank <= 15:
            rank_score = 55
        elif rank <= 30:
            rank_score = 40
        else:
            rank_score = 25

        # Trend bonus: rising mentions get rewarded
        trend_bonus = 20 if mention_delta > 20 else (12 if mention_delta > 5 else (5 if mention_delta > 0 else 0))

        score  = min(100, rank_score + trend_bonus)
        trend  = "↑ rising" if mention_delta > 0 else ("→ flat" if mention_delta == 0 else "↓ falling")
        detail = f"Rank #{rank} on social | {mentions} mentions | {trend} ({mention_delta:+d} vs yesterday)"
        return score, detail

    except Exception:
        return 30, "Social data unavailable (neutral)"


# ============================================================
# SCORING ENGINE — combines all 4 signals
# ============================================================
def score_ticker(symbol, apewisdom_data):
    """Returns a dict with all scores and details for one ticker."""
    try:
        ticker = yf.Ticker(symbol)
        info   = ticker.info

        current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)
        target_price  = info.get('targetMeanPrice', 0)
        company_name  = info.get('shortName', symbol)

        analyst_score,     analyst_detail     = get_analyst_score(ticker, symbol)
        momentum_score,    momentum_detail    = get_momentum_score(ticker, symbol)
        fundamental_score, fundamental_detail = get_fundamentals_score(ticker, symbol)
        social_score,      social_detail      = get_social_score(symbol, apewisdom_data)

        composite = (
            analyst_score     * SCORE_WEIGHTS['analyst']      +
            momentum_score    * SCORE_WEIGHTS['momentum']     +
            fundamental_score * SCORE_WEIGHTS['fundamentals'] +
            social_score      * SCORE_WEIGHTS['social']
        )

        upside = 0
        if current_price > 0 and target_price > 0:
            upside = ((target_price - current_price) / current_price) * 100

        stop_loss = round(current_price * (1 - STOP_LOSS_PCT / 100), 2) if current_price else 0

        return {
            'Ticker':              symbol,
            'Company':             company_name,
            'Price':               round(current_price, 2),
            'Target':              round(target_price, 2),
            'Upside %':            round(upside, 1),
            'Stop Loss':           stop_loss,
            'Score':               round(composite, 1),
            'Analyst Score':       round(analyst_score, 1),
            'Momentum Score':      round(momentum_score, 1),
            'Fundamental Score':   round(fundamental_score, 1),
            'Social Score':        round(social_score, 1),
            'Analyst Detail':      analyst_detail,
            'Momentum Detail':     momentum_detail,
            'Fundamental Detail':  fundamental_detail,
            'Social Detail':       social_detail,
        }

    except Exception as e:
        return {
            'Ticker': symbol, 'Company': symbol,
            'Price': 0, 'Target': 0, 'Upside %': 0, 'Stop Loss': 0,
            'Score': 0, 'Analyst Score': 0, 'Momentum Score': 0,
            'Fundamental Score': 0, 'Social Score': 0,
            'Analyst Detail': 'Error', 'Momentum Detail': 'Error',
            'Fundamental Detail': 'Error', 'Social Detail': f'Error: {str(e)[:60]}',
        }


def score_all(tickers, apewisdom_data):
    results = []
    for i, symbol in enumerate(tickers):
        print(f"  [{i+1}/{len(tickers)}] Analyzing {symbol}...", end=" ", flush=True)
        row = score_ticker(symbol, apewisdom_data)
        print(f"Score: {row['Score']}/100")
        results.append(row)
        time.sleep(0.3)   # Small delay to avoid rate limits on Yahoo Finance

    df = pd.DataFrame(results)
    df = df.sort_values('Score', ascending=False).reset_index(drop=True)
    df.index += 1   # Rank starts at 1
    return df


# ============================================================
# REPORT GENERATOR — saves Markdown to picks/ folder
# ============================================================
def action_label(score):
    if score >= 72:   return "🟢 STRONG BUY"
    elif score >= 55: return "🟡 BUY"
    elif score >= 40: return "⚪ WATCH"
    else:             return "🔴 AVOID"


def generate_report(df, output_dir="picks"):
    os.makedirs(output_dir, exist_ok=True)
    today    = datetime.now().strftime("%Y-%m-%d")
    run_time = datetime.now().strftime("%H:%M")
    filename = os.path.join(output_dir, f"picks_{today}.md")

    top = df.head(TOP_N_PICKS)
    lines = []

    # --- Header ---
    lines += [
        f"# 📈 Stock Picks — {today}",
        f"*Data & AI Recommendation Engine | Run at {run_time}*\n",
        "---\n",
    ]

    # --- Top picks summary table ---
    lines += [
        f"## 🏆 Top {TOP_N_PICKS} Picks\n",
        "| Rank | Ticker | Company | Price | Target | Upside | Stop Loss | Score | Signal |",
        "|------|--------|---------|-------|--------|--------|-----------|-------|--------|",
    ]
    for rank, row in top.iterrows():
        lines.append(
            f"| #{rank} | **{row['Ticker']}** | {row['Company']} | "
            f"${row['Price']:.2f} | ${row['Target']:.0f} | "
            f"{row['Upside %']:+.0f}% | ${row['Stop Loss']:.2f} | "
            f"**{row['Score']}/100** | {action_label(row['Score'])} |"
        )

    # --- Detailed breakdown per pick ---
    lines += ["\n---\n", "## 📊 Signal Breakdown\n"]
    for rank, row in top.iterrows():
        lines += [
            f"### #{rank} — {row['Ticker']} ({row['Company']})",
            f"**Composite Score: {row['Score']}/100** | {action_label(row['Score'])}",
            f"- Entry: **${row['Price']:.2f}** → Target: **${row['Target']:.0f}** "
            f"({row['Upside %']:+.0f}% upside) | Stop-loss: **${row['Stop Loss']:.2f}** (-{STOP_LOSS_PCT}%)",
            f"",
            f"| Signal | Score | Detail |",
            f"|--------|-------|--------|",
            f"| 🔬 Analyst (35%)     | {row['Analyst Score']}/100    | {row['Analyst Detail']} |",
            f"| 📈 Momentum (25%)    | {row['Momentum Score']}/100   | {row['Momentum Detail']} |",
            f"| 💰 Fundamentals (25%)| {row['Fundamental Score']}/100 | {row['Fundamental Detail']} |",
            f"| 💬 Social (15%)      | {row['Social Score']}/100     | {row['Social Detail']} |",
            f"",
        ]

    # --- Full watchlist scorecard ---
    lines += [
        "---\n",
        "## 📋 Full Watchlist Scorecard\n",
        "| Rank | Ticker | Score | Analyst | Momentum | Fundamentals | Social | Signal |",
        "|------|--------|-------|---------|----------|--------------|--------|--------|",
    ]
    for rank, row in df.iterrows():
        lines.append(
            f"| #{rank} | {row['Ticker']} | {row['Score']} | "
            f"{row['Analyst Score']} | {row['Momentum Score']} | "
            f"{row['Fundamental Score']} | {row['Social Score']} | "
            f"{action_label(row['Score'])} |"
        )

    # --- Footer ---
    lines += [
        "\n---",
        f"*Weights: Analyst {int(SCORE_WEIGHTS['analyst']*100)}% | "
        f"Momentum {int(SCORE_WEIGHTS['momentum']*100)}% | "
        f"Fundamentals {int(SCORE_WEIGHTS['fundamentals']*100)}% | "
        f"Social {int(SCORE_WEIGHTS['social']*100)}%*",
        f"*Stop-loss: -{STOP_LOSS_PCT}% | Target return: +{TARGET_RETURN_PCT}% | Hold: 6 months*",
        f"*Data sources: Yahoo Finance · Finnhub (optional) · Apewisdom*",
    ]

    with open(filename, "w") as f:
        f.write("\n".join(lines))

    print(f"  Report saved → {filename}")
    return filename


# ============================================================
# EMAIL NOTIFIER — sends top 5 picks to your Gmail
# ============================================================
def send_email(df):
    if not SEND_EMAIL or not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        return

    today = datetime.now().strftime("%d %B %Y")
    top   = df.head(TOP_N_PICKS)

    # --- Build HTML email body ---
    rows_html = ""
    for rank, row in top.iterrows():
        signal = action_label(row['Score'])
        color  = "#1a7a3f" if "STRONG" in signal else ("#b8860b" if "BUY" in signal else "#555")
        rows_html += f"""
        <tr style="border-bottom:1px solid #eee;">
          <td style="padding:10px;font-weight:bold;font-size:16px;">#{rank}</td>
          <td style="padding:10px;">
            <span style="font-size:18px;font-weight:bold;">{row['Ticker']}</span><br>
            <span style="color:#666;font-size:13px;">{row['Company']}</span>
          </td>
          <td style="padding:10px;">${row['Price']:.2f}</td>
          <td style="padding:10px;">${row['Target']:.0f} <span style="color:#1a7a3f;">({row['Upside %']:+.0f}%)</span></td>
          <td style="padding:10px;color:#c0392b;">${row['Stop Loss']:.2f}</td>
          <td style="padding:10px;font-weight:bold;font-size:16px;">{row['Score']}/100</td>
          <td style="padding:10px;color:{color};font-weight:bold;">{signal}</td>
        </tr>
        <tr style="background:#f9f9f9;border-bottom:1px solid #eee;">
          <td></td>
          <td colspan="6" style="padding:6px 10px;font-size:12px;color:#555;">
            🔬 <b>Analyst:</b> {row['Analyst Detail']} &nbsp;|&nbsp;
            📈 <b>Momentum:</b> {row['Momentum Detail']}
          </td>
        </tr>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;color:#222;">
      <div style="background:#111;padding:20px;border-radius:8px 8px 0 0;">
        <h2 style="color:#fff;margin:0;">📈 Daily Stock Picks — {today}</h2>
        <p style="color:#aaa;margin:4px 0 0;">Data & AI Recommendation Engine</p>
      </div>
      <div style="padding:20px;background:#fff;border:1px solid #ddd;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
          <thead>
            <tr style="background:#f0f0f0;font-size:13px;color:#555;">
              <th style="padding:10px;text-align:left;">#</th>
              <th style="padding:10px;text-align:left;">Ticker</th>
              <th style="padding:10px;text-align:left;">Price</th>
              <th style="padding:10px;text-align:left;">Target</th>
              <th style="padding:10px;text-align:left;">Stop Loss</th>
              <th style="padding:10px;text-align:left;">Score</th>
              <th style="padding:10px;text-align:left;">Signal</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
      <div style="padding:16px;background:#f9f9f9;border:1px solid #ddd;border-top:none;font-size:12px;color:#888;border-radius:0 0 8px 8px;">
        Stop-loss: -{STOP_LOSS_PCT}% from entry &nbsp;|&nbsp; Target return: +{TARGET_RETURN_PCT}% &nbsp;|&nbsp; Hold: 6 months<br>
        Weights: Analyst 35% · Momentum 25% · Fundamentals 25% · Social 15%<br>
        <i>This is a personal research tool, not financial advice.</i>
      </div>
    </html></body>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"📈 Stock Picks {today} — Top pick: {df.iloc[0]['Ticker']} ({df.iloc[0]['Score']}/100)"
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = GMAIL_ADDRESS
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())

        print(f"  Email sent → {GMAIL_ADDRESS}")
    except Exception as e:
        print(f"  Email failed: {e}")


# ============================================================
# SLACK NOTIFIER — sends top 3 to your Slack DM
# ============================================================
def send_slack(df):
    if not SLACK_WEBHOOK_URL:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    top3  = df.head(3)

    text = f"*📈 Daily Stock Picks — {today}*\n"
    for rank, row in top3.iterrows():
        text += (
            f"\n*#{rank} {row['Ticker']}* — {row['Company']}\n"
            f"  Score: *{row['Score']}/100* {action_label(row['Score'])}\n"
            f"  Price: ${row['Price']:.2f} → Target: ${row['Target']:.0f} "
            f"({row['Upside %']:+.0f}%) | Stop: ${row['Stop Loss']:.2f}\n"
            f"  _{row['Analyst Detail']}_\n"
        )
    text += "\n_Full report saved in your picks/ folder_"

    try:
        requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
        print("  Slack notification sent")
    except Exception as e:
        print(f"  Slack failed: {e}")


# ============================================================
# MAIN — runs the full pipeline in order
# ============================================================
if __name__ == "__main__":
    print()
    print("=" * 55)
    print("  📈  STOCK RECOMMENDATION ENGINE")
    print(f"  {datetime.now().strftime('%A, %d %B %Y — %H:%M')}")
    print("=" * 55)

    # Step 1: Load tickers
    print("\n[1/4] Loading your tickers...")
    tickers = load_tickers("tickers.txt")

    # Step 2: Fetch social data (one call covers all tickers)
    print("\n[2/4] Fetching social sentiment (Reddit/Apewisdom)...")
    apewisdom_data = fetch_apewisdom()

    # Step 3: Score every ticker
    print("\n[3/4] Analysing and scoring all tickers...")
    results_df = score_all(tickers, apewisdom_data)

    # Step 4: Save report + optional Slack
    print("\n[4/4] Generating report...")
    report_file = generate_report(results_df)
    send_slack(results_df)
    send_email(results_df)

    # Terminal summary
    print()
    print("=" * 55)
    print(f"  TODAY'S TOP {TOP_N_PICKS} PICKS")
    print("=" * 55)
    for rank, row in results_df.head(TOP_N_PICKS).iterrows():
        print(f"  #{rank}  {row['Ticker']:<6}  {row['Score']:5.1f}/100  {action_label(row['Score'])}")
    print("=" * 55)
    print(f"\n  Open {report_file} for the full report.\n")
