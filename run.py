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
import shutil
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Load your personal settings from config.py
from config import SLACK_WEBHOOK_URL, FINNHUB_API_KEY, SCORE_WEIGHTS, \
                   STOP_LOSS_PCT, TARGET_RETURN_PCT, TOP_N_PICKS, HOLD_MONTHS, \
                   GMAIL_ADDRESS, GMAIL_APP_PASSWORD, SEND_EMAIL, ANTHROPIC_API_KEY

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

# Run label injected by GitHub Actions (e.g. "5am Early Signal" or "7am Final Signal")
# Falls back to a plain label when running locally
RUN_LABEL = os.environ.get('RUN_LABEL', 'Manual Run')
RUN_EMOJI = os.environ.get('RUN_EMOJI', '🔧')


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
        upgrades = 0
        downgrades = 0
        detail_parts = []

        # --- Yahoo Finance: upgrade/downgrade history (yfinance 1.x) ---
        try:
            ud = ticker_obj.upgrades_downgrades   # correct attribute in yfinance 1.x
            if ud is not None and not ud.empty:
                # Index is timezone-aware — convert cleanly
                ud.index = pd.to_datetime(ud.index).tz_localize(None) \
                           if ud.index.tzinfo is None \
                           else pd.to_datetime(ud.index).tz_convert(None)
                cutoff = datetime.now() - timedelta(days=7)
                recent = ud[ud.index >= cutoff]
                if not recent.empty:
                    # GradeChange column: 'up', 'down', 'init', 'main' (maintain/reiterate)
                    col = 'Action' if 'Action' in recent.columns else \
                          'GradeChange' if 'GradeChange' in recent.columns else None
                    if col:
                        upgrades   = recent[recent[col].str.lower().isin(['up', 'init', 'main', 'reit'])].shape[0]
                        downgrades = recent[recent[col].str.lower() == 'down'].shape[0]
                        if upgrades > 0 or downgrades > 0:
                            detail_parts.append(f"{upgrades} upgrade(s), {downgrades} downgrade(s) in last 7d")
        except Exception:
            pass   # fall through to Finnhub

        # --- Finnhub: consensus bullish % across all analysts ---
        finnhub_score = 0
        if FINNHUB_API_KEY:
            try:
                url = f"https://finnhub.io/api/v1/stock/recommendation?symbol={symbol}&token={FINNHUB_API_KEY}"
                resp = requests.get(url, timeout=8)
                data = resp.json()
                if data and isinstance(data, list):
                    latest      = data[0]
                    strong_buy  = latest.get('strongBuy', 0)
                    buy         = latest.get('buy', 0)
                    hold        = latest.get('hold', 0)
                    sell        = latest.get('sell', 0)
                    strong_sell = latest.get('strongSell', 0)
                    total       = strong_buy + buy + hold + sell + strong_sell
                    if total > 0:
                        bullish_pct  = ((strong_buy + buy) / total) * 100
                        # Score: 70%+ bullish = 60 pts, 50% = 30 pts, etc.
                        finnhub_score = max(0, min(60, (bullish_pct - 30) * 1.5))
                        detail_parts.append(f"{bullish_pct:.0f}% of {total} analysts bullish")
            except Exception:
                pass

        # --- Composite analyst score ---
        # Upgrade/downgrade actions (last 7d): each net upgrade = +30 pts
        # Finnhub consensus: up to 60 pts baseline
        # Combined and clamped to 0–100
        action_score = max(-100, min(100, (upgrades - downgrades) * 30))
        score = max(0, min(100, finnhub_score + action_score))

        detail = " | ".join(detail_parts) if detail_parts else "No recent upgrade/downgrade activity"
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
# SIGNAL 5: Filing Sentiment (SEC 8-K via EDGAR + Claude)
# Source: SEC EDGAR free API + Anthropic Claude Haiku (low cost)
# Score: 0 to 100 (guidance tone)
# Cost:  ~$0 most days (cached); under $0.50/month total
# ============================================================

_EDGAR_HEADERS  = {"User-Agent": "stockrecommender karthik.bia@gmail.com"}
_CIK_CACHE_PATH = "data/cik_map.json"
_SENT_CACHE_PATH = "data/sentiment_cache.json"

# Tickers that don't file 8-Ks with SEC EDGAR — skip lookup, return neutral
_NO_EDGAR = {'REA', 'NOK'}   # REA = ASX-listed; NOK = Finnish FPI, files 6-K not 8-K


def _get_cik_map():
    """
    Downloads ticker→CIK mapping from EDGAR once a month (cached locally).
    Returns a dict: {'NVDA': '0001045810', 'AMD': '0000002488', ...}
    """
    if os.path.exists(_CIK_CACHE_PATH):
        if (time.time() - os.path.getmtime(_CIK_CACHE_PATH)) < 30 * 86400:
            with open(_CIK_CACHE_PATH) as f:
                return json.load(f)
    try:
        url  = "https://www.sec.gov/files/company_tickers.json"
        resp = requests.get(url, headers=_EDGAR_HEADERS, timeout=15)
        raw  = resp.json()
        # Format: {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "..."}, ...}
        cmap = {v["ticker"]: str(v["cik_str"]).zfill(10) for v in raw.values()}
        os.makedirs("data", exist_ok=True)
        with open(_CIK_CACHE_PATH, "w") as f:
            json.dump(cmap, f)
        print(f"  EDGAR CIK map loaded ({len(cmap):,} companies)")
        return cmap
    except Exception as e:
        print(f"  EDGAR CIK map unavailable: {e}")
        return {}


def _load_sentiment_cache():
    """Loads the local filing sentiment cache (keyed by ticker_accession)."""
    if os.path.exists(_SENT_CACHE_PATH):
        with open(_SENT_CACHE_PATH) as f:
            return json.load(f)
    return {}


def _save_sentiment_cache(cache):
    """Persists the sentiment cache back to disk."""
    with open(_SENT_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


def get_filing_sentiment_score(symbol, cik_map, sentiment_cache):
    """
    Fetches the most recent 8-K filing from SEC EDGAR (last 30 days),
    sends the text to Claude Haiku, and returns a guidance tone score 0–100.

    Scoring bands:
      80–100  Major beat + guidance raise / transformational event
      60–79   Beat estimates, positive outlook
      40–59   Neutral / no recent filing (default baseline)
      20–39   Cautious / soft guidance / macro headwinds
       0–19   Guidance cut / earnings miss + lowered outlook

    Each unique filing is scored once and cached — no re-scoring on daily re-runs.
    Tickers without a recent 8-K return neutral (50) — no penalty.
    """
    if symbol in _NO_EDGAR:
        return 50, f"EDGAR not applicable ({symbol} files outside SEC) — neutral"

    if not ANTHROPIC_API_KEY or not _ANTHROPIC_AVAILABLE:
        return 50, "Anthropic API not configured — filing sentiment skipped"

    try:
        cik = cik_map.get(symbol.upper())
        if not cik:
            return 50, "CIK not found in EDGAR — neutral"

        # Fetch the company's recent filing index
        url  = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=_EDGAR_HEADERS, timeout=10)
        if resp.status_code != 200:
            return 50, "EDGAR submissions unavailable — neutral"

        data     = resp.json()
        recent   = data.get("filings", {}).get("recent", {})
        forms    = recent.get("form", [])
        dates    = recent.get("filingDate", [])
        accnums  = recent.get("accessionNumber", [])
        prim_docs = recent.get("primaryDocument", [])
        cutoff   = datetime.now() - timedelta(days=30)

        filing_text = None
        accession   = None
        filing_date = None

        for form, date_str, acc, doc in zip(forms, dates, accnums, prim_docs):
            if form != "8-K":
                continue
            if datetime.strptime(date_str, "%Y-%m-%d") < cutoff:
                break   # EDGAR returns reverse-chronological — nothing older is useful

            cache_key = f"{symbol}_{acc}"
            if cache_key in sentiment_cache:
                c = sentiment_cache[cache_key]
                return c["score"], f"[{date_str}] {c['reason']}"

            # Download the primary document of the filing
            acc_clean = acc.replace("-", "")
            doc_url   = (f"https://www.sec.gov/Archives/edgar/data/"
                         f"{int(cik)}/{acc_clean}/{doc}")
            try:
                dr = requests.get(doc_url, headers=_EDGAR_HEADERS, timeout=15)
                filing_text = dr.text[:8000]   # cap at 8K chars — ~2K tokens
                accession   = acc
                filing_date = date_str
                break
            except Exception:
                continue

        if not filing_text:
            return 50, "No 8-K filed in last 30 days — neutral"

        # Send to Claude Haiku for guidance tone scoring
        client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = (
            f"Analyse this SEC 8-K filing for {symbol}. "
            f"Score the GUIDANCE TONE on a scale of 0–100:\n"
            f"  80–100: Very positive (major beat, large guidance raise, transformational event)\n"
            f"  60–79:  Positive (beat + raise, new contract wins, expansion)\n"
            f"  40–59:  Neutral (meets expectations, no clear direction change)\n"
            f"  20–39:  Cautious (soft language, in-line results, macro headwinds)\n"
            f"  0–19:   Very negative (guidance cut, earnings miss, warning)\n\n"
            f"Filing excerpt:\n{filing_text[:4000]}\n\n"
            f"Return ONLY valid JSON — no markdown, no explanation:\n"
            f'  {{"score": <integer 0-100>, "reason": "<max 12 words>"}}'
        )
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}]
        )
        raw    = response.content[0].text.strip()
        result = json.loads(raw)
        score  = max(0, min(100, int(result["score"])))
        reason = result.get("reason", "guidance scored")

        # Cache — this filing won't be re-scored
        sentiment_cache[f"{symbol}_{accession}"] = {
            "score": score, "reason": reason, "filing_date": filing_date
        }
        _save_sentiment_cache(sentiment_cache)

        return score, f"8-K {filing_date}: {reason}"

    except Exception as e:
        return 50, f"Filing sentiment error — neutral ({str(e)[:40]})"


# ============================================================
# SCORING ENGINE — combines all 5 signals
# ============================================================
def score_ticker(symbol, apewisdom_data, cik_map=None, sentiment_cache=None):
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
        filing_score,      filing_detail      = get_filing_sentiment_score(
                                                    symbol,
                                                    cik_map or {},
                                                    sentiment_cache if sentiment_cache is not None else {}
                                                )

        composite = (
            analyst_score     * SCORE_WEIGHTS['analyst']            +
            momentum_score    * SCORE_WEIGHTS['momentum']           +
            fundamental_score * SCORE_WEIGHTS['fundamentals']       +
            social_score      * SCORE_WEIGHTS['social']             +
            filing_score      * SCORE_WEIGHTS.get('filing', 0)
        )

        upside = 0
        if current_price > 0 and target_price > 0:
            upside = ((target_price - current_price) / current_price) * 100

        stop_loss = round(current_price * (1 - STOP_LOSS_PCT / 100), 2) if current_price else 0

        # ── 3-month daily close prices for sparkline (dashboard trend charts) ──
        prices_3m = []
        prices_1m = []
        try:
            hist3m = ticker.history(period='3mo', interval='1d')['Close']
            prices_3m = [round(float(p), 2) for p in hist3m.tolist() if not pd.isna(p)]
            prices_1m = prices_3m[-22:] if len(prices_3m) >= 22 else prices_3m
        except Exception:
            pass

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
            'Filing Score':        round(filing_score, 1),
            'Analyst Detail':      analyst_detail,
            'Momentum Detail':     momentum_detail,
            'Fundamental Detail':  fundamental_detail,
            'Social Detail':       social_detail,
            'Filing Detail':       filing_detail,
            'Prices 3M':           prices_3m,
            'Prices 1M':           prices_1m,
        }

    except Exception as e:
        return {
            'Ticker': symbol, 'Company': symbol,
            'Price': 0, 'Target': 0, 'Upside %': 0, 'Stop Loss': 0,
            'Score': 0, 'Analyst Score': 0, 'Momentum Score': 0,
            'Fundamental Score': 0, 'Social Score': 0, 'Filing Score': 50,
            'Analyst Detail': 'Error', 'Momentum Detail': 'Error',
            'Fundamental Detail': 'Error', 'Social Detail': f'Error: {str(e)[:60]}',
            'Filing Detail': 'Error', 'Prices 3M': [], 'Prices 1M': [],
        }


def score_all(tickers, apewisdom_data, cik_map=None, sentiment_cache=None):
    results = []
    for i, symbol in enumerate(tickers):
        print(f"  [{i+1}/{len(tickers)}] Analyzing {symbol}...", end=" ", flush=True)
        row = score_ticker(symbol, apewisdom_data, cik_map, sentiment_cache)
        filing_note = f" | 8-K: {row['Filing Score']}" if row['Filing Score'] != 50 else ""
        print(f"Score: {row['Score']}/100{filing_note}")
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
    if score >= 65:   return "🟢 STRONG BUY"
    elif score >= 50: return "🟡 BUY"
    elif score >= 35: return "⚪ WATCH"
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
            f"| 🔬 Analyst (30%)     | {row['Analyst Score']}/100    | {row['Analyst Detail']} |",
            f"| 📈 Momentum (25%)    | {row['Momentum Score']}/100   | {row['Momentum Detail']} |",
            f"| 💰 Fundamentals (25%)| {row['Fundamental Score']}/100 | {row['Fundamental Detail']} |",
            f"| 💬 Social (10%)      | {row['Social Score']}/100     | {row['Social Detail']} |",
            f"| 📄 Filing (10%)      | {row['Filing Score']}/100     | {row['Filing Detail']} |",
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
        f"Social {int(SCORE_WEIGHTS['social']*100)}% | "
        f"Filing {int(SCORE_WEIGHTS.get('filing', 0)*100)}%*",
        f"*Stop-loss: -{STOP_LOSS_PCT}% | Target return: +{TARGET_RETURN_PCT}% | Hold: 6 months*",
        f"*Data sources: Yahoo Finance · Finnhub (optional) · Apewisdom*",
    ]

    with open(filename, "w") as f:
        f.write("\n".join(lines))

    print(f"  Report saved → {filename}")
    return filename


# ============================================================
# EMAIL NOTIFIER — sends all stocks ranked by score to Gmail
# ============================================================
def send_email(df):
    if not SEND_EMAIL or not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        return

    today = datetime.now().strftime("%d %B %Y")
    top   = df.head(TOP_N_PICKS)

    # --- Section 1: Top picks with full signal detail ---
    top_rows_html = ""
    for rank, row in top.iterrows():
        signal = action_label(row['Score'])
        color  = "#1a7a3f" if "STRONG" in signal else ("#b8860b" if "BUY" in signal else "#666")
        top_rows_html += f"""
        <tr style="border-bottom:1px solid #eee;">
          <td style="padding:10px;font-weight:bold;font-size:15px;color:#333;">#{rank}</td>
          <td style="padding:10px;">
            <span style="font-size:17px;font-weight:bold;">{row['Ticker']}</span><br>
            <span style="color:#888;font-size:12px;">{row['Company']}</span>
          </td>
          <td style="padding:10px;">${row['Price']:.2f}</td>
          <td style="padding:10px;">${row['Target']:.0f}<br>
            <span style="color:#1a7a3f;font-size:12px;">+{row['Upside %']:.0f}%</span>
          </td>
          <td style="padding:10px;color:#c0392b;">${row['Stop Loss']:.2f}</td>
          <td style="padding:10px;font-weight:bold;font-size:16px;">{row['Score']}/100</td>
          <td style="padding:10px;font-weight:bold;color:{color};">{signal}</td>
        </tr>
        <tr style="background:#fafafa;border-bottom:2px solid #ddd;">
          <td></td>
          <td colspan="6" style="padding:5px 10px 10px;font-size:11px;color:#666;">
            🔬 <b>Analyst {row['Analyst Score']:.0f}/100:</b> {row['Analyst Detail']}<br>
            📈 <b>Momentum {row['Momentum Score']:.0f}/100:</b> {row['Momentum Detail']}<br>
            💰 <b>Fundamentals {row['Fundamental Score']:.0f}/100:</b> {row['Fundamental Detail']}<br>
            💬 <b>Social {row['Social Score']:.0f}/100:</b> {row['Social Detail']}<br>
            📄 <b>Filing {row['Filing Score']:.0f}/100:</b> {row['Filing Detail']}
          </td>
        </tr>"""

    # --- Section 2: Full watchlist sorted by score ---
    all_rows_html = ""
    for rank, row in df.iterrows():
        signal = action_label(row['Score'])
        bg     = "#fff" if rank % 2 == 1 else "#f9f9f9"
        color  = "#1a7a3f" if "STRONG" in signal else \
                 "#b8860b" if "BUY" in signal else \
                 "#555"    if "WATCH" in signal else "#c0392b"
        all_rows_html += f"""
        <tr style="background:{bg};border-bottom:1px solid #eee;">
          <td style="padding:8px 10px;color:#999;">#{rank}</td>
          <td style="padding:8px 10px;font-weight:bold;">{row['Ticker']}</td>
          <td style="padding:8px 10px;font-size:12px;color:#888;">{row['Company']}</td>
          <td style="padding:8px 10px;font-weight:bold;">{row['Score']}/100</td>
          <td style="padding:8px 10px;">${row['Price']:.2f}</td>
          <td style="padding:8px 10px;">${row['Target']:.0f}
            <span style="color:#1a7a3f;font-size:11px;">(+{row['Upside %']:.0f}%)</span>
          </td>
          <td style="padding:8px 10px;color:#c0392b;">${row['Stop Loss']:.2f}</td>
          <td style="padding:8px 10px;font-size:11px;">{row['Analyst Score']:.0f}</td>
          <td style="padding:8px 10px;font-size:11px;">{row['Momentum Score']:.0f}</td>
          <td style="padding:8px 10px;font-size:11px;">{row['Fundamental Score']:.0f}</td>
          <td style="padding:8px 10px;font-size:11px;">{row['Social Score']:.0f}</td>
          <td style="padding:8px 10px;font-size:11px;">{row['Filing Score']:.0f}</td>
          <td style="padding:8px 10px;font-weight:bold;color:{color};font-size:12px;">{signal}</td>
        </tr>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:800px;margin:auto;color:#222;">

      <!-- Header -->
      <div style="background:#111;padding:20px;border-radius:8px 8px 0 0;">
        <h2 style="color:#fff;margin:0;">{RUN_EMOJI} {RUN_LABEL} — {today}</h2>
        <p style="color:#aaa;margin:6px 0 0;font-size:13px;">
          Data & AI Recommendation Engine &nbsp;|&nbsp;
          {len(df)} stocks analysed &nbsp;|&nbsp;
          Top pick: {df.iloc[0]['Ticker']} at {df.iloc[0]['Score']}/100
        </p>
      </div>

      <!-- Top 5 detailed -->
      <div style="padding:16px 20px 4px;background:#fff;border:1px solid #ddd;border-top:none;">
        <h3 style="margin:0 0 12px;font-size:15px;color:#333;">🏆 Top {TOP_N_PICKS} Picks — Full Breakdown</h3>
      </div>
      <div style="background:#fff;border:1px solid #ddd;border-top:none;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
          <thead>
            <tr style="background:#f0f0f0;font-size:12px;color:#666;text-transform:uppercase;">
              <th style="padding:8px 10px;text-align:left;">Rank</th>
              <th style="padding:8px 10px;text-align:left;">Ticker</th>
              <th style="padding:8px 10px;text-align:left;">Price</th>
              <th style="padding:8px 10px;text-align:left;">Target</th>
              <th style="padding:8px 10px;text-align:left;">Stop Loss</th>
              <th style="padding:8px 10px;text-align:left;">Score</th>
              <th style="padding:8px 10px;text-align:left;">Signal</th>
            </tr>
          </thead>
          <tbody>{top_rows_html}</tbody>
        </table>
      </div>

      <!-- Full watchlist -->
      <div style="padding:16px 20px 4px;margin-top:24px;background:#fff;border:1px solid #ddd;">
        <h3 style="margin:0 0 4px;font-size:15px;color:#333;">📋 Full Watchlist — All {len(df)} Stocks Ranked</h3>
        <p style="margin:0 0 12px;font-size:12px;color:#888;">Sorted highest to lowest score</p>
      </div>
      <div style="background:#fff;border:1px solid #ddd;border-top:none;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
          <thead>
            <tr style="background:#f0f0f0;font-size:11px;color:#666;text-transform:uppercase;">
              <th style="padding:8px 10px;text-align:left;">#</th>
              <th style="padding:8px 10px;text-align:left;">Ticker</th>
              <th style="padding:8px 10px;text-align:left;">Company</th>
              <th style="padding:8px 10px;text-align:left;">Score</th>
              <th style="padding:8px 10px;text-align:left;">Price</th>
              <th style="padding:8px 10px;text-align:left;">Target</th>
              <th style="padding:8px 10px;text-align:left;">Stop</th>
              <th style="padding:8px 10px;text-align:left;">Analyst</th>
              <th style="padding:8px 10px;text-align:left;">Momentum</th>
              <th style="padding:8px 10px;text-align:left;">Fund.</th>
              <th style="padding:8px 10px;text-align:left;">Social</th>
              <th style="padding:8px 10px;text-align:left;">8-K</th>
              <th style="padding:8px 10px;text-align:left;">Signal</th>
            </tr>
          </thead>
          <tbody>{all_rows_html}</tbody>
        </table>
      </div>

      <!-- Footer -->
      <div style="padding:14px 20px;background:#f9f9f9;border:1px solid #ddd;border-top:none;
                  font-size:11px;color:#999;border-radius:0 0 8px 8px;margin-bottom:20px;">
        Weights: Analyst 30% · Momentum 25% · Fundamentals 25% · Social 10% · Filing 10%<br>
        Stop-loss: -{STOP_LOSS_PCT}% from entry &nbsp;|&nbsp;
        Target return: +{TARGET_RETURN_PCT}% &nbsp;|&nbsp; Hold: {HOLD_MONTHS} months<br>
        <i>Personal research tool — not financial advice.</i>
      </div>

    </body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"{RUN_EMOJI} [{RUN_LABEL}] Stock Picks {today} — #{1} {df.iloc[0]['Ticker']} {df.iloc[0]['Score']}/100 | All {len(df)} stocks"
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
# MONTHLY BACKUP — runs only on the 1st of each month
# ============================================================
def backup_json_if_first_of_month(json_path="data/history.json", backup_dir="data/backups"):
    """
    On the 1st of every month, copies history.json to data/backups/
    with a timestamp suffix before today's data is appended.
    This preserves a clean snapshot of the previous month's full history.
    Skipped silently on all other days.
    """
    today = datetime.now()

    if today.day != 1:
        return   # Not the 1st — nothing to do

    if not os.path.exists(json_path):
        print("  Monthly backup skipped — no history file yet")
        return

    os.makedirs(backup_dir, exist_ok=True)

    # Filename: history_backup_2026-06-01_083000.json
    timestamp   = today.strftime("%Y-%m-%d_%H%M%S")
    backup_name = f"history_backup_{timestamp}.json"
    backup_path = os.path.join(backup_dir, backup_name)

    shutil.copy2(json_path, backup_path)

    # Verify backup size matches original
    original_size = os.path.getsize(json_path)
    backup_size   = os.path.getsize(backup_path)
    status = "✓ verified" if original_size == backup_size else "⚠ size mismatch"

    print(f"  📦 Monthly backup created → {backup_path} ({status}, {backup_size:,} bytes)")


# ============================================================
# JSON HISTORY — incremental log for agent analysis
# ============================================================
def save_json(df, output_dir="data", filename="history.json"):
    """
    Appends this run's full results to data/history.json.
    Each run is a separate record — nothing is ever overwritten.
    Structure is designed to be pulled directly into another agent.
    """
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)

    # --- Build run metadata ---
    now        = datetime.now()
    now_utc    = datetime.utcnow()
    run_record = {
        "run_id":              now.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_date":            now.strftime("%Y-%m-%d"),
        "run_time":            now.strftime("%H:%M:%S"),
        "run_label":           RUN_LABEL,
        "run_timestamp_local": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_timestamp_utc":   now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timezone":            "Australia/Sydney (AEST/AEDT — DST-aware)",
        "total_stocks":        len(df),
        "score_weights": {
            "analyst":      SCORE_WEIGHTS["analyst"],
            "momentum":     SCORE_WEIGHTS["momentum"],
            "fundamentals": SCORE_WEIGHTS["fundamentals"],
            "social":       SCORE_WEIGHTS["social"],
        },
        "strategy": {
            "stop_loss_pct":     STOP_LOSS_PCT,
            "target_return_pct": TARGET_RETURN_PCT,
            "hold_months":       HOLD_MONTHS,
        },
        "stocks": []
    }

    # --- Build per-stock records ---
    for rank, row in df.iterrows():
        stock_record = {
            "rank":                 int(rank),
            "ticker":               row["Ticker"],
            "company":              row["Company"],
            "signal":               action_label(row["Score"]).replace("🟢 ", "").replace("🟡 ", "").replace("⚪ ", "").replace("🔴 ", ""),
            "signal_emoji":         action_label(row["Score"]),
            "composite_score":      round(float(row["Score"]), 2),
            "price":                round(float(row["Price"]), 2),
            "analyst_target_price": round(float(row["Target"]), 2),
            "upside_pct":           round(float(row["Upside %"]), 2),
            "stop_loss_price":      round(float(row["Stop Loss"]), 2),
            "scores": {
                "analyst":      round(float(row["Analyst Score"]), 2),
                "momentum":     round(float(row["Momentum Score"]), 2),
                "fundamentals": round(float(row["Fundamental Score"]), 2),
                "social":       round(float(row["Social Score"]), 2),
                "filing":       round(float(row.get("Filing Score", 50)), 2),
            },
            "signal_details": {
                "analyst":      row["Analyst Detail"],
                "momentum":     row["Momentum Detail"],
                "fundamentals": row["Fundamental Detail"],
                "social":       row["Social Detail"],
                "filing":       row.get("Filing Detail", ""),
            },
            "prices_1m":  row["Prices 1M"] if "Prices 1M" in row and isinstance(row["Prices 1M"], list) else [],
            "prices_3m":  row["Prices 3M"] if "Prices 3M" in row and isinstance(row["Prices 3M"], list) else [],
        }
        run_record["stocks"].append(stock_record)

    # --- Load existing history and append ---
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                history = {"runs": []}
    else:
        history = {"runs": []}

    # Replace existing run for today's date; otherwise append (one run per day)
    today_date   = run_record["run_date"]
    existing_idx = next((i for i, r in enumerate(history["runs"]) if r["run_date"] == today_date), None)
    if existing_idx is not None:
        history["runs"][existing_idx] = run_record
        print(f"  Overwrote existing run for {today_date} with latest results")
    else:
        history["runs"].append(run_record)

    # On the 1st of the month, consolidate the previous month to its last day only.
    # That single entry becomes the "Month Year" archive tab; all earlier days are dropped.
    if now.day == 1:
        prev_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        prev_runs  = [r for r in history["runs"] if r["run_date"].startswith(prev_month)]
        if len(prev_runs) > 1:
            last_day = max(prev_runs, key=lambda r: r["run_date"])["run_date"]
            before   = len(history["runs"])
            history["runs"] = [
                r for r in history["runs"]
                if not r["run_date"].startswith(prev_month) or r["run_date"] == last_day
            ]
            removed = before - len(history["runs"])
            print(f"  1st-of-month: kept {prev_month}/{last_day[-2:]} as archive, removed {removed} earlier day(s)")

    history["total_runs"]    = len(history["runs"])
    history["first_run"]     = history["runs"][0]["run_id"]
    history["last_run"]      = run_record["run_id"]

    with open(filepath, "w") as f:
        json.dump(history, f, indent=2)

    print(f"  JSON history updated → {filepath} ({len(history['runs'])} run(s) stored)")
    return filepath


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
    print(f"  {RUN_EMOJI}  {RUN_LABEL}")
    print("=" * 55)

    # Step 1: Load tickers
    print("\n[1/5] Loading your tickers...")
    tickers = load_tickers("tickers.txt")

    # Step 2: Fetch social data (one call covers all tickers)
    print("\n[2/5] Fetching social sentiment (Reddit/Apewisdom)...")
    apewisdom_data = fetch_apewisdom()

    # Step 2b: Load EDGAR CIK map + sentiment cache (for 8-K Signal 5)
    print("\n[2b/5] Loading EDGAR CIK map and filing sentiment cache...")
    cik_map         = _get_cik_map()
    sentiment_cache = _load_sentiment_cache()
    cached_count    = len(sentiment_cache)
    print(f"  {len(cik_map):,} companies in CIK map | {cached_count} filing(s) already cached")

    # Step 3: Score every ticker
    print("\n[3/5] Analysing and scoring all tickers...")
    results_df = score_all(tickers, apewisdom_data, cik_map, sentiment_cache)

    # Step 4: Save report + notifications
    print("\n[4/5] Generating report and sending notifications...")
    report_file = generate_report(results_df)
    send_slack(results_df)
    send_email(results_df)

    # Step 5: Monthly backup (1st of month only) + append to JSON history
    print("\n[5/5] Saving to JSON history...")
    backup_json_if_first_of_month()
    save_json(results_df)

    # Terminal summary
    print()
    print("=" * 55)
    print(f"  TODAY'S TOP {TOP_N_PICKS} PICKS")
    print("=" * 55)
    for rank, row in results_df.head(TOP_N_PICKS).iterrows():
        print(f"  #{rank}  {row['Ticker']:<6}  {row['Score']:5.1f}/100  {action_label(row['Score'])}")
    print("=" * 55)
    print(f"\n  Open {report_file} for the full report.\n")
