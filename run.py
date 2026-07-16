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
from datetime import datetime, timedelta, timezone
import os
import json
import time
import shutil
import smtplib
import xml.etree.ElementTree as ET
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
# SHADOW SCORING (v2)
# Corrected signal set running in parallel with v1. The email and
# report still rank on v1; v2 accumulates in history.json so the two
# models can be compared on real forward returns before cutting over.
# Weights are normalised by their sum in code, so adding a future
# signal (e.g. insider) only requires a new entry here.
# ============================================================
V2_WEIGHTS = {
    'analyst':      0.25,   # direction of last 5 actions, not raw counts
    'momentum':     0.20,
    'fundamentals': 0.20,   # median target + dispersion + forward P/E
    'revisions':    0.10,   # forward EPS estimate trend
    'filing':       0.10,
    'social':       0.05,
    'insider':      0.10,   # net Form 4 open-market buy/sell, last 30 days
}


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
    """
    Returns (v1_score, v2_score, detail).
      v1 — legacy: counts init/main/reit as upgrades. Kept unchanged so
           historical scores stay comparable during the shadow period.
      v2 — direction of the last 5 analyst actions within 90 days,
           recency-weighted: 'up' = +1, 'down' = -1, 'init' = +0.3,
           maintain/reiterate = 0. A wall of "Maintained" notes no
           longer scores like a wall of upgrades.
    """
    try:
        upgrades = 0
        downgrades = 0
        detail_parts = []
        v2_direction = None

        # --- Yahoo Finance: upgrade/downgrade history (yfinance 1.x) ---
        try:
            ud = ticker_obj.upgrades_downgrades   # correct attribute in yfinance 1.x
            if ud is not None and not ud.empty:
                # Index is timezone-aware — convert cleanly
                ud.index = pd.to_datetime(ud.index).tz_localize(None) \
                           if ud.index.tzinfo is None \
                           else pd.to_datetime(ud.index).tz_convert(None)
                # GradeChange column: 'up', 'down', 'init', 'main' (maintain/reiterate)
                col = 'Action' if 'Action' in ud.columns else \
                      'GradeChange' if 'GradeChange' in ud.columns else None

                # v1 (legacy) — last 7 days, init/main/reit counted as upgrades
                cutoff = datetime.now() - timedelta(days=7)
                recent = ud[ud.index >= cutoff]
                if not recent.empty and col:
                    upgrades   = recent[recent[col].str.lower().isin(['up', 'init', 'main', 'reit'])].shape[0]
                    downgrades = recent[recent[col].str.lower() == 'down'].shape[0]
                    if upgrades > 0 or downgrades > 0:
                        detail_parts.append(f"{upgrades} upgrade(s), {downgrades} downgrade(s) in last 7d")

                # v2 — direction of the last 5 actions within 90 days
                if col:
                    cutoff90 = datetime.now() - timedelta(days=90)
                    last5 = ud[ud.index >= cutoff90].sort_index(ascending=False).head(5)
                    if not last5.empty:
                        ACTION_VALUE = {'up': 1.0, 'down': -1.0, 'init': 0.3}
                        vals    = [ACTION_VALUE.get(str(a).lower(), 0.0) for a in last5[col]]
                        weights = list(range(len(vals), 0, -1))          # newest weighted heaviest
                        v2_direction = sum(v * w for v, w in zip(vals, weights)) / sum(weights)
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

        # --- v1 composite (legacy math, unchanged) ---
        # Upgrade/downgrade actions (last 7d): each net upgrade = +30 pts
        # Finnhub consensus: up to 60 pts baseline
        # Combined and clamped to 0–100
        action_score = max(-100, min(100, (upgrades - downgrades) * 30))
        score_v1 = max(0, min(100, finnhub_score + action_score))

        # --- v2 composite: consensus baseline + direction of travel (±40) ---
        if v2_direction is not None:
            score_v2 = max(0, min(100, finnhub_score + v2_direction * 40))
        else:
            score_v2 = max(0, min(100, finnhub_score))   # no actions in 90d — consensus only

        detail = " | ".join(detail_parts) if detail_parts else "No recent upgrade/downgrade activity"
        return score_v1, score_v2, detail

    except Exception as e:
        return 0, 0, "Analyst data unavailable"


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
    """
    Returns (v1_score, v2_score, detail).
      v1 — legacy: mean-target upside 60 pts + growth 25 + margin 15.
      v2 — median-target upside 50 pts (halved when the high/low target
           spread exceeds 3x — a consensus nobody agrees on is noise),
           growth 20, margin 10, forward-vs-trailing P/E 20. A forward
           P/E at a deep discount to trailing is treated as a possible
           peak-cycle-earnings warning, not a bargain.
    """
    try:
        info = ticker_obj.info

        current_price   = info.get('currentPrice') or info.get('regularMarketPrice', 0)
        target_price    = info.get('targetMeanPrice', 0)
        revenue_growth  = (info.get('revenueGrowth') or 0) * 100       # e.g. 0.25 → 25%
        gross_margin    = (info.get('grossMargins') or 0) * 100         # e.g. 0.70 → 70%
        forward_pe      = info.get('forwardPE', 0) or 0
        trailing_pe     = info.get('trailingPE', 0) or 0
        median_target   = info.get('targetMedianPrice', 0) or 0
        high_target     = info.get('targetHighPrice', 0) or 0
        low_target      = info.get('targetLowPrice', 0) or 0

        # Upside to analyst consensus target (v1: mean)
        upside = 0
        if current_price > 0 and target_price > 0:
            upside = ((target_price - current_price) / current_price) * 100

        # --- v1 (legacy, unchanged): upside 60 pts, growth 25, margin 15 ---
        upside_score  = max(0, min(60, upside * 1.5))         # 40% upside = 60 pts
        growth_score  = max(0, min(25, revenue_growth * 0.6)) # 40% growth = 24 pts
        margin_score  = max(0, min(15, gross_margin * 0.2))   # 75% margin = 15 pts
        score_v1 = min(100, upside_score + growth_score + margin_score)

        # --- v2: median-target upside 50, growth 20, margin 10, P/E 20 ---
        upside_med = 0
        if current_price > 0 and median_target > 0:
            upside_med = ((median_target - current_price) / current_price) * 100
        upside_score2 = max(0, min(50, upside_med * 1.25))    # 40% upside = 50 pts

        spread = (high_target / low_target) if low_target > 0 else 0
        low_confidence = spread > 3
        if low_confidence:
            upside_score2 *= 0.5   # wide dispersion — target is low-confidence

        growth_score2 = max(0, min(20, revenue_growth * 0.5))
        margin_score2 = max(0, min(10, gross_margin * 0.133))

        pe_note = ""
        if forward_pe > 0 and trailing_pe > 0:
            if forward_pe < 8 and forward_pe < trailing_pe * 0.5:
                pe_score2 = 5    # cyclical trap: market pricing peak earnings
                pe_note   = "⚠ fwd P/E deep-discount — possible peak-cycle earnings"
            elif forward_pe < trailing_pe * 0.75 and revenue_growth > 0:
                pe_score2 = 20   # earnings expected to grow into the valuation
            elif forward_pe < trailing_pe:
                pe_score2 = 13
            else:
                pe_score2 = 5    # earnings expected flat or contracting
        else:
            pe_score2 = 8        # neutral when either P/E is missing/negative

        score_v2 = min(100, upside_score2 + growth_score2 + margin_score2 + pe_score2)

        target_str = f"${target_price:.0f} ({upside:+.0f}% upside)" if target_price else "no target"
        detail = (f"Target: {target_str} | "
                  f"Revenue growth: {revenue_growth:.0f}% | "
                  f"Gross margin: {gross_margin:.0f}%")
        if median_target:
            detail += f" | Median tgt: ${median_target:.0f}"
        if low_confidence:
            detail += f" | ⚠ target spread {spread:.1f}x"
        if pe_note:
            detail += f" | {pe_note}"
        return score_v1, score_v2, detail

    except Exception as e:
        return 0, 0, "Fundamentals data unavailable"


# ============================================================
# SIGNAL 6 (v2 shadow): Forward EPS Estimate Revisions
# Source: Yahoo Finance (yfinance eps_trend)
# Score: 0 to 100
# Rising estimates during a price fall = the market de-rated the
# stock but the business held — the strongest dip evidence.
# Falling estimates while price rises = multiple expansion only.
# ============================================================
def get_revisions_score(ticker_obj, symbol):
    try:
        et = ticker_obj.eps_trend
        if et is None or len(et) == 0:
            return 50, "No estimate trend data — neutral"

        # Rows indexed '0q','+1q','0y','+1y'; columns: current, 7daysAgo,
        # 30daysAgo, 60daysAgo, 90daysAgo. Use the fiscal-year rows.
        changes = []
        for period in ('0y', '+1y'):
            if period in et.index:
                row  = et.loc[period]
                cur  = row.get('current')
                base = row.get('90daysAgo')
                if base is None or pd.isna(base):
                    base = row.get('60daysAgo')
                if (cur is not None and base is not None
                        and not pd.isna(cur) and not pd.isna(base)
                        and abs(base) > 0.01):
                    changes.append((cur - base) / abs(base) * 100)

        if not changes:
            return 50, "Estimate history incomplete — neutral"

        avg = sum(changes) / len(changes)
        if   avg >= 5:  score = 90
        elif avg >= 1:  score = 72
        elif avg > -1:  score = 50
        elif avg > -5:  score = 28
        else:           score = 10

        arrow = "↑" if avg >= 1 else ("→" if avg > -1 else "↓")
        return score, f"Fwd EPS estimates {arrow} {avg:+.1f}% vs 90d ago"

    except Exception:
        return 50, "Estimate data unavailable — neutral"


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


def _fetch_submissions(symbol, cik_map):
    """
    Fetches the EDGAR 'recent filings' block once per ticker and shares it
    between the 8-K filing-sentiment signal and the Form 4 insider signal,
    so a ticker never costs two top-level SEC requests per run.
    Returns (cik, recent_dict). Either element may be None on failure —
    callers distinguish "no CIK" from "CIK found but fetch failed" by
    checking cik first, then recent.
    """
    if symbol in _NO_EDGAR:
        return None, None
    cik = cik_map.get(symbol.upper())
    if not cik:
        return None, None
    try:
        url  = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=_EDGAR_HEADERS, timeout=10)
        if resp.status_code != 200:
            return cik, None
        return cik, resp.json().get("filings", {}).get("recent", {})
    except Exception:
        return cik, None


def get_filing_sentiment_score(symbol, cik, recent, sentiment_cache):
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

    if not cik:
        return 50, "CIK not found in EDGAR — neutral"

    if not recent:
        return 50, "EDGAR submissions unavailable — neutral"

    try:
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

            # Download the filing and extract meaningful text.
            # 8-Ks often put the earnings press release in Exhibit 99.1
            # (e.g. ex991pressrelease.htm) rather than the primary doc.
            # Strategy: try primary doc; if thin, scan folder for ex99 file.
            import re as _re
            acc_clean  = acc.replace("-", "")
            base_url   = (f"https://www.sec.gov/Archives/edgar/data/"
                          f"{int(cik)}/{acc_clean}/")

            def _fetch_clean(url):
                """Fetch URL and return stripped plain text, or '' on failure."""
                try:
                    r = requests.get(url, headers=_EDGAR_HEADERS, timeout=15)
                    if r.status_code != 200:
                        return ''
                    txt = _re.sub(r'<[^>]+>', ' ', r.text)
                    txt = _re.sub(r'\s+', ' ', txt).strip()
                    return txt
                except Exception:
                    return ''

            try:
                # Strategy: prefer Exhibit 99.1 (earnings press release) over
                # primary doc, which is often an XBRL wrapper with no guidance text.
                # 1. Scan folder for ex99 / pressrelease files first.
                # 2. Fall back to primary doc if no exhibit found.
                text_only   = ''
                folder_html = requests.get(base_url, headers=_EDGAR_HEADERS,
                                           timeout=10).text
                ex99_files  = _re.findall(
                    r'href="(/[^"]*(?:ex.?99|pressrelease|exhibit.?99|ex\.99|99\.1)[^"]*\.htm[l]?)"',
                    folder_html, _re.IGNORECASE
                )
                for ex_path in ex99_files[:3]:
                    ex_url  = f"https://www.sec.gov{ex_path}"
                    ex_text = _fetch_clean(ex_url)
                    if len(ex_text) > 500:
                        text_only = ex_text
                        break

                # Fall back to primary document if no usable exhibit found
                if len(text_only) < 500:
                    text_only = _fetch_clean(base_url + doc)

                if len(text_only) < 200:
                    continue

                filing_text = text_only[:12000]   # ~3K tokens of clean text
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
        # Strip markdown fences if Claude wraps the JSON (e.g. ```json ... ```)
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
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
# SIGNAL 7 (v2 shadow): Insider Activity (SEC Form 4 via EDGAR)
# Source: SEC EDGAR free API — no Claude call, structured XML only
# Score: 0 to 100
# Nets open-market buys (code 'P') against sales (code 'S') in the last
# 30 days; officer/director/10%-owner transactions count 1.5x a routine
# holder's. Gifts, grants, and option exercises (G/A/M/F) are not market
# signals and are ignored. Cluster selling by 2+ insiders caps the v2
# composite at BUY, never STRONG BUY — the same hard rule /stock-news
# applies: the street can be bullish, but insiders selling into it wins.
# ============================================================
_INSIDER_CACHE_PATH = "data/insider_cache.json"


def _load_insider_cache():
    if os.path.exists(_INSIDER_CACHE_PATH):
        with open(_INSIDER_CACHE_PATH) as f:
            return json.load(f)
    return {}


def _save_insider_cache(cache):
    with open(_INSIDER_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


def _parse_form4_xml(xml_text):
    """Extracts transaction codes + reporting-owner role from a raw Form 4 XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    rel = root.find(".//reportingOwnerRelationship")
    is_officer  = rel is not None and (rel.findtext("isOfficer") or "0") == "1"
    is_director = rel is not None and (rel.findtext("isDirector") or "0") == "1"
    is_ten_pct  = rel is not None and (rel.findtext("isTenPercentOwner") or "0") == "1"
    owner = root.findtext(".//rptOwnerName") or "unknown"

    txs = []
    for node in root.findall(".//nonDerivativeTransaction"):
        code = node.findtext(".//transactionCoding/transactionCode")
        if code:
            txs.append(code)

    return {
        "is_officer": is_officer, "is_director": is_director, "is_ten_pct": is_ten_pct,
        "owner": owner, "transactions": txs,
    }


def get_insider_score(symbol, cik, recent, insider_cache):
    """
    Returns (score, detail, cluster_selling_cap).
    Reuses the same EDGAR 'recent filings' payload the 8-K signal already
    fetched — zero extra top-level SEC requests, just one small XML fetch
    per Form 4 found in the last 30 days (cached by accession number).
    """
    if symbol in _NO_EDGAR:
        return 50, f"EDGAR not applicable ({symbol}) — neutral", False
    if not cik:
        return 50, "CIK not found in EDGAR — neutral", False
    if not recent:
        return 50, "EDGAR submissions unavailable — neutral", False

    try:
        forms     = recent.get("form", [])
        dates     = recent.get("filingDate", [])
        accnums   = recent.get("accessionNumber", [])
        prim_docs = recent.get("primaryDocument", [])
        cutoff    = datetime.now() - timedelta(days=30)

        net_value  = 0.0
        buy_count  = 0
        sell_count = 0
        sellers    = set()

        for form, date_str, acc, doc in zip(forms, dates, accnums, prim_docs):
            if form not in ("4", "4/A"):
                continue
            if datetime.strptime(date_str, "%Y-%m-%d") < cutoff:
                break   # EDGAR returns reverse-chronological — nothing older is useful

            cache_key = f"{symbol}_{acc}"
            if cache_key in insider_cache:
                tx = insider_cache[cache_key]
            else:
                acc_clean = acc.replace("-", "")
                # Raw XML lives at the accession root under its own filename —
                # 'doc' is often an xslF345X06/-prefixed transform path; strip
                # any folder prefix and fetch the raw file directly (verified:
                # no folder-listing scan needed, unlike the 8-K exhibit search).
                xml_url = (f"https://www.sec.gov/Archives/edgar/data/"
                           f"{int(cik)}/{acc_clean}/{doc.split('/')[-1]}")
                try:
                    r = requests.get(xml_url, headers=_EDGAR_HEADERS, timeout=10)
                    tx = _parse_form4_xml(r.text) if r.status_code == 200 else None
                except Exception:
                    tx = None
                insider_cache[cache_key] = tx
                _save_insider_cache(insider_cache)

            if not tx:
                continue

            weight = 1.5 if (tx["is_officer"] or tx["is_director"] or tx["is_ten_pct"]) else 1.0
            for code in tx["transactions"]:
                if code == "P":         # open-market purchase
                    net_value += weight
                    buy_count += 1
                elif code == "S":       # open-market sale
                    net_value -= weight
                    sell_count += 1
                    sellers.add(tx["owner"])
                # G (gift) / A (grant) / M (exercise) / F (tax withholding) —
                # not open-market signals, ignored

        if buy_count == 0 and sell_count == 0:
            return 50, "No insider open-market activity in last 30d — neutral", False

        cluster_selling = len(sellers) >= 2 and sell_count > buy_count

        if   net_value >= 3:  score = 90
        elif net_value >= 1:  score = 70
        elif net_value > -1:  score = 50
        elif net_value > -3:  score = 30
        else:                 score = 10

        detail = f"{buy_count} buy(s), {sell_count} sell(s) in last 30d (net {net_value:+.1f})"
        if cluster_selling:
            detail += " | ⚠ cluster selling"
        return score, detail, cluster_selling

    except Exception as e:
        return 50, f"Insider data error — neutral ({str(e)[:40]})", False


# ============================================================
# SCORING ENGINE — combines all 5 signals
# ============================================================
def score_ticker(symbol, apewisdom_data, cik_map=None, sentiment_cache=None, insider_cache=None):
    """Returns a dict with all scores and details for one ticker."""
    try:
        ticker = yf.Ticker(symbol)
        info   = ticker.info

        current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)
        target_price  = info.get('targetMeanPrice', 0)
        company_name  = info.get('shortName', symbol)
        quote_type    = (info.get('quoteType') or 'EQUITY').upper()
        is_etf        = quote_type == 'ETF'

        # Short float — free field in the same info payload
        short_float = round((info.get('shortPercentOfFloat') or 0) * 100, 1)

        # Next earnings date — from quote data, no extra API call
        earnings_date    = ''
        days_to_earnings = None
        ts = info.get('earningsTimestampStart') or info.get('earningsTimestamp')
        if ts:
            try:
                ed    = datetime.fromtimestamp(ts)
                delta = (ed.date() - datetime.now().date()).days
                if delta >= 0:
                    earnings_date    = ed.strftime('%Y-%m-%d')
                    days_to_earnings = delta
            except Exception:
                pass

        momentum_score, momentum_detail = get_momentum_score(ticker, symbol)
        social_score,   social_detail   = get_social_score(symbol, apewisdom_data)

        if is_etf:
            # ETFs have no analyst actions, fundamentals, or 8-Ks. Score on
            # momentum + social only, re-normalised to each model's weights,
            # instead of letting missing data drag them to ~8/100.
            analyst_score = analyst_v2 = 0
            fundamental_score = fundamental_v2 = 0
            revisions_score = 0
            filing_score = 0
            insider_score = 0
            insider_cap = False
            analyst_detail = fundamental_detail = "n/a — ETF"
            revisions_detail = filing_detail = insider_detail = "n/a — ETF"

            w1 = SCORE_WEIGHTS['momentum'] + SCORE_WEIGHTS['social']
            composite = (momentum_score * SCORE_WEIGHTS['momentum'] +
                         social_score   * SCORE_WEIGHTS['social']) / w1
            w2 = V2_WEIGHTS['momentum'] + V2_WEIGHTS['social']
            composite_v2 = (momentum_score * V2_WEIGHTS['momentum'] +
                            social_score   * V2_WEIGHTS['social']) / w2
        else:
            analyst_score, analyst_v2, analyst_detail          = get_analyst_score(ticker, symbol)
            fundamental_score, fundamental_v2, fundamental_detail = get_fundamentals_score(ticker, symbol)
            revisions_score, revisions_detail                  = get_revisions_score(ticker, symbol)

            # One shared EDGAR submissions fetch feeds both the 8-K filing
            # signal and the Form 4 insider signal — no duplicate SEC request.
            cik, submissions = _fetch_submissions(symbol, cik_map or {})
            filing_score,   filing_detail                      = get_filing_sentiment_score(
                                                                    symbol, cik, submissions,
                                                                    sentiment_cache if sentiment_cache is not None else {}
                                                                )
            insider_score, insider_detail, insider_cap         = get_insider_score(
                                                                    symbol, cik, submissions,
                                                                    insider_cache if insider_cache is not None else {}
                                                                )

            composite = (
                analyst_score     * SCORE_WEIGHTS['analyst']            +
                momentum_score    * SCORE_WEIGHTS['momentum']           +
                fundamental_score * SCORE_WEIGHTS['fundamentals']       +
                social_score      * SCORE_WEIGHTS['social']             +
                filing_score      * SCORE_WEIGHTS.get('filing', 0)
            )

            w2 = sum(V2_WEIGHTS.values())
            composite_v2 = (
                analyst_v2      * V2_WEIGHTS['analyst']      +
                momentum_score  * V2_WEIGHTS['momentum']     +
                fundamental_v2  * V2_WEIGHTS['fundamentals'] +
                revisions_score * V2_WEIGHTS['revisions']    +
                filing_score    * V2_WEIGHTS['filing']       +
                social_score    * V2_WEIGHTS['social']       +
                insider_score   * V2_WEIGHTS['insider']
            ) / w2
            if short_float > 15:
                composite_v2 = max(0, composite_v2 - 5)   # informed money leaning against it
            if insider_cap:
                # Hard rule (matches /stock-news): cluster insider selling
                # caps v2 at BUY regardless of the numeric score — the street
                # can be bullish, but insiders selling into it wins the tie.
                composite_v2 = min(composite_v2, 64.9)

        # Risk flags — warnings on the card, not score changes
        flags = []
        if is_etf:
            flags.append("ETF")
        if days_to_earnings is not None and days_to_earnings <= 7:
            flags.append(f"⚠ earnings in {days_to_earnings}d")
        if short_float > 15:
            flags.append(f"short float {short_float:.0f}%")
        if insider_cap:
            flags.append("⚠ insider cluster selling")
        if not current_price:
            flags.append("⚠ no price — delisted?")

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
            # ── v2 shadow model + risk flags ──
            'Score V2':            round(composite_v2, 1),
            'Analyst V2':          round(analyst_v2, 1),
            'Fundamental V2':      round(fundamental_v2, 1),
            'Revisions Score':     round(revisions_score, 1),
            'Revisions Detail':    revisions_detail,
            'Insider Score':       round(insider_score, 1),
            'Insider Detail':      insider_detail,
            'Short Float %':       short_float,
            'Earnings Date':       earnings_date,
            'Days To Earnings':    days_to_earnings,
            'Type':                'ETF' if is_etf else 'STOCK',
            'Flags':               " | ".join(flags),
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
            'Score V2': 0, 'Analyst V2': 0, 'Fundamental V2': 0,
            'Revisions Score': 50, 'Revisions Detail': 'Error',
            'Insider Score': 50, 'Insider Detail': 'Error',
            'Short Float %': 0, 'Earnings Date': '', 'Days To Earnings': None,
            'Type': 'STOCK', 'Flags': '⚠ scoring error',
        }


def score_all(tickers, apewisdom_data, cik_map=None, sentiment_cache=None, insider_cache=None):
    results = []
    for i, symbol in enumerate(tickers):
        print(f"  [{i+1}/{len(tickers)}] Analyzing {symbol}...", end=" ", flush=True)
        row = score_ticker(symbol, apewisdom_data, cik_map, sentiment_cache, insider_cache)
        filing_note  = f" | 8-K: {row['Filing Score']}" if row['Filing Score'] != 50 else ""
        insider_note = f" | insider: {row['Insider Score']}" if row['Insider Score'] != 50 else ""
        print(f"Score: {row['Score']}/100{filing_note}{insider_note}")
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


def select_top_picks(df, n=None):
    """
    Top picks by v1 score, skipping names that fail the risk gate:
      - earnings within 7 days (binary event risk — a pick the night
        before a report is a coin flip wearing a thesis)
      - ETFs (tracked on the momentum+social side track, not picks)
    Skipped names keep their rank and flags in the full table.
    """
    n = n or TOP_N_PICKS

    def eligible(row):
        if row.get('Type') == 'ETF':
            return False
        d = row.get('Days To Earnings')
        if d is not None and pd.notna(d) and d <= 7:
            return False
        return True

    mask = df.apply(eligible, axis=1)
    return df[mask].head(n)


def generate_report(df, output_dir="picks"):
    os.makedirs(output_dir, exist_ok=True)
    today    = datetime.now().strftime("%Y-%m-%d")
    run_time = datetime.now().strftime("%H:%M")
    filename = os.path.join(output_dir, f"picks_{today}.md")

    top = select_top_picks(df)
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
            f"| 🧮 Revisions (v2)    | {row['Revisions Score']}/100  | {row['Revisions Detail']} |",
            f"| 🕴️ Insider (v2)       | {row['Insider Score']}/100    | {row['Insider Detail']} |",
            f"",
            f"*Shadow score v2: {row['Score V2']}/100"
            + (f" | Flags: {row['Flags']}*" if row['Flags'] else "*"),
            f"",
        ]

    # --- Full watchlist scorecard ---
    lines += [
        "---\n",
        "## 📋 Full Watchlist Scorecard\n",
        "| Rank | Ticker | Score | v2 | Analyst | Momentum | Fundamentals | Social | Signal | Flags |",
        "|------|--------|-------|----|---------|----------|--------------|--------|--------|-------|",
    ]
    for rank, row in df.iterrows():
        lines.append(
            f"| #{rank} | {row['Ticker']} | {row['Score']} | {row['Score V2']} | "
            f"{row['Analyst Score']} | {row['Momentum Score']} | "
            f"{row['Fundamental Score']} | {row['Social Score']} | "
            f"{action_label(row['Score'])} | {row['Flags']} |"
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
        f"*Shadow model v2 (not yet ranking): direction-based analyst, median target + "
        f"dispersion, forward P/E, EPS estimate revisions. Weights: "
        + " / ".join(f"{k} {int(v*100)}%" for k, v in V2_WEIGHTS.items()) + "*",
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
    top   = select_top_picks(df)

    # --- Section 1: Top picks with full signal detail ---
    top_rows_html = ""
    for rank, row in top.iterrows():
        flags_html = (f"📌 <b>Flags:</b> {row['Flags']}<br>" if row['Flags'] else "")
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
            📄 <b>Filing {row['Filing Score']:.0f}/100:</b> {row['Filing Detail']}<br>
            🧮 <b>Revisions {row['Revisions Score']:.0f}/100:</b> {row['Revisions Detail']}<br>
            🕴️ <b>Insider {row['Insider Score']:.0f}/100:</b> {row['Insider Detail']}<br>
            {flags_html}🧪 <b>Shadow v2:</b> {row['Score V2']}/100
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
        row_flags = (f"<br><span style='color:#c0392b;font-size:10px;'>{row['Flags']}</span>"
                     if row['Flags'] else "")
        all_rows_html += f"""
        <tr style="background:{bg};border-bottom:1px solid #eee;">
          <td style="padding:8px 10px;color:#999;">#{rank}</td>
          <td style="padding:8px 10px;font-weight:bold;">{row['Ticker']}</td>
          <td style="padding:8px 10px;font-size:12px;color:#888;">{row['Company']}{row_flags}</td>
          <td style="padding:8px 10px;font-weight:bold;">{row['Score']}/100</td>
          <td style="padding:8px 10px;font-size:11px;color:#666;">{row['Score V2']}</td>
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
              <th style="padding:8px 10px;text-align:left;">v2</th>
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
        Shadow v2 (not yet ranking): direction-based analyst · median target + dispersion ·
        forward P/E · EPS revisions &nbsp;|&nbsp; ⚠-flagged names are excluded from Top {TOP_N_PICKS}<br>
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
    now_utc    = datetime.now(timezone.utc)
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
            "filing":       SCORE_WEIGHTS.get("filing", 0),
        },
        "score_weights_v2": V2_WEIGHTS,
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
                "revisions":    round(float(row.get("Revisions Score", 50) or 0), 2),
                "insider":      round(float(row.get("Insider Score", 50) or 0), 2),
            },
            "signal_details": {
                "analyst":      row["Analyst Detail"],
                "momentum":     row["Momentum Detail"],
                "fundamentals": row["Fundamental Detail"],
                "social":       row["Social Detail"],
                "filing":       row.get("Filing Detail", ""),
                "revisions":    row.get("Revisions Detail", ""),
                "insider":      row.get("Insider Detail", ""),
            },
            "composite_score_v2": round(float(row.get("Score V2", 0) or 0), 2),
            "scores_v2": {
                "analyst":      round(float(row.get("Analyst V2", 0) or 0), 2),
                "fundamentals": round(float(row.get("Fundamental V2", 0) or 0), 2),
            },
            "short_float_pct":  round(float(row.get("Short Float %", 0) or 0), 2),
            "earnings_date":    row.get("Earnings Date", "") or "",
            "days_to_earnings": (int(row["Days To Earnings"])
                                 if row.get("Days To Earnings") is not None
                                 and pd.notna(row.get("Days To Earnings")) else None),
            "instrument_type":  row.get("Type", "STOCK"),
            "flags":            row.get("Flags", "") or "",
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

    # Step 2b: Load EDGAR CIK map + sentiment/insider caches (Signals 5 & 7)
    print("\n[2b/5] Loading EDGAR CIK map, filing sentiment cache, and insider cache...")
    cik_map         = _get_cik_map()
    sentiment_cache = _load_sentiment_cache()
    insider_cache   = _load_insider_cache()
    print(f"  {len(cik_map):,} companies in CIK map | "
          f"{len(sentiment_cache)} filing(s) cached | {len(insider_cache)} Form 4(s) cached")

    # Step 3: Score every ticker
    print("\n[3/5] Analysing and scoring all tickers...")
    results_df = score_all(tickers, apewisdom_data, cik_map, sentiment_cache, insider_cache)

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
    for rank, row in select_top_picks(results_df).iterrows():
        print(f"  #{rank}  {row['Ticker']:<6}  {row['Score']:5.1f}/100  v2:{row['Score V2']:5.1f}  {action_label(row['Score'])}")
    skipped = results_df.head(TOP_N_PICKS).index.difference(select_top_picks(results_df).index)
    for rank in skipped:
        row = results_df.loc[rank]
        print(f"  (#{rank} {row['Ticker']} held out of Top {TOP_N_PICKS}: {row['Flags']})")
    print("=" * 55)
    print(f"\n  Open {report_file} for the full report.\n")
