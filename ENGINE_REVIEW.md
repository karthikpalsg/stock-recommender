# Engine Review & Upgrade Recommendation

*Reviewed: July 2026. Compared against the data pipelines in /stock-news and /value-dip-scanner.*

---

## 1. What the engine does today

Five signals, weighted composite, 0 to 100:

| Signal | Weight | Source | Coverage across the 91 tickers |
|---|---|---|---|
| Analyst | 30% | yfinance `upgrades_downgrades` + Finnhub consensus | Good |
| Momentum | 25% | yfinance 6mo history (50-day MA, 4-wk return, volume) | Good |
| Fundamentals | 25% | yfinance `info` (mean target upside, revenue growth, gross margin) | Weak for ETFs and pre-revenue names |
| Social | 10% | Apewisdom top-50 list | Poor: ~85 of 91 tickers fall outside the top 50 and get a flat neutral 30 |
| Filing | 10% | SEC EDGAR 8-K + Claude Haiku | Good, cached, unique to this engine |

Strengths worth keeping: $0 cost, API-grade sources that work unattended in GitHub Actions, the cached Claude filing-sentiment signal (neither of the other skills has anything like it), DST-proof scheduling, and a history file that already stores every input needed for self-evaluation.

---

## 2. Data source comparison across your three tools

| Source | Engine | /stock-news | /value-dip-scanner | Notes |
|---|---|---|---|---|
| yfinance API | ✅ | ❌ | ❌ | Engine-only. Underused: forward P/E is fetched but never scored; median target, short float, EPS revision trend all sit in the same API unused |
| Finnhub | ✅ | ❌ | ❌ | Consensus counts only |
| Apewisdom | ✅ | ❌ | ❌ | Top-50 only; near-useless for a 91-ticker watchlist |
| SEC EDGAR + Claude | ✅ | ❌ | ❌ | Engine's unique edge. CIK plumbing already built |
| stockanalysis.com | ❌ | ✅ | ✅ | Median/high/low targets, forward estimate revisions, 5Y P/E baseline |
| Finviz | ❌ | ✅ | ✅ | Dated analyst actions, insider transactions, short float, earnings date, perf columns |
| StockTwits JSON API | ❌ | ✅ | ⚠️ (uses the broken web page) | stock-news proved the API endpoint is reliable; per-ticker sentiment for every symbol |
| Benzinga | ❌ | ✅ | ❌ | Technicals only |

The architectural difference matters: the engine runs unattended in CI, so it needs API-grade sources. The skills scrape web pages interactively. The right move is not to bolt Finviz scraping onto the engine, it is to pull the *concepts* the skills proved out and implement them with API sources the engine can rely on at 5am with nobody watching.

---

## 3. Accuracy problems found in the code review

Ranked by impact.

### P1. Maintains and reiterations count as upgrades
`get_analyst_score()` line 79 treats `'up', 'init', 'main', 'reit'` all as upgrades. A stock with five "Maintained Hold" actions scores +150 action points and clamps to a near-perfect analyst score. This single line inflates the 30%-weighted signal across the whole board and is likely why most of the list clusters in BUY territory. /stock-news scores the *direction of the last 5 actions* instead, which is the correct model.

### P2. The stale-target trap
Fundamentals hands out up to 60 of its 100 points for upside to the analyst mean target. When a stock crashes, targets go stale, computed "upside" balloons, and the engine rewards falling knives. /value-dip-scanner solved exactly this with two checks the engine lacks: forward estimate revisions ("price down while estimates hold" vs "price following estimates down"), and whether any analyst action *since* the move confirms the target is still believed.

### P3. Mean target, no dispersion check
The engine uses `targetMeanPrice`. One outlier target skews it badly. /stock-news uses the median and flags a high/low spread wider than 3x as "the street has no consensus", which is itself a signal. yfinance already returns `targetMedianPrice`, `targetHighPrice`, `targetLowPrice`, `numberOfAnalystOpinions` in the same `info` call. Zero extra cost.

### P4. Forward P/E is fetched and thrown away
`forward_pe` is read in `get_fundamentals_score()` and never used. /stock-news treats the trailing-vs-forward P/E gap as a first-class signal, including the cyclical trap: a deep-discount forward P/E on a memory name (MU is on your list and has topped your picks) often means the market is pricing peak earnings, not a bargain.

### P5. Social signal is dead weight for this watchlist
Apewisdom covers the top 50 most-mentioned stocks. Most of your tickers never appear, so 10% of the composite is a constant 30 for ~85 names. The dev log even lists StockTwits as a data source, but the code never calls it. The StockTwits JSON API (`api.stocktwits.com/api/2/streams/symbol/TICKER.json`) returns tagged bullish/bearish counts per ticker and /stock-news confirmed it works reliably where the web page does not.

### P6. No insider signal anywhere
Both other skills weight insiders at 20%, and /stock-news enforces a hard rule: analysts and retail bullish while insiders steadily sell caps the recommendation at HOLD. The engine has zero insider awareness, yet it already has the EDGAR plumbing (CIK map, request headers, caching pattern). Form 4 filings are the same free API.

### P7. No earnings-date awareness
/value-dip-scanner tags "earnings within 7 days" as binary risk and bans those names from top conviction. The engine will happily print STRONG BUY the night before a report. `yf.Ticker(t).calendar` has the date.

### P8. ETFs and dead tickers pollute every run
SMH, SOXX, SOXQ, SKHY score ~8/100 because ETFs have no revenue growth, no 8-Ks, no target from the same fields. SATS and SIVE appear delisted and error out daily. `info['quoteType'] == 'ETF'` distinguishes them; delisted names should be flagged for removal in the email rather than silently scoring 0.

### P9. The engine never grades itself
This is the biggest gap versus /value-dip-scanner, whose Step 0 reads its own scan log every run, compares each prior pick against SPY, and *adjusts its scoring* when a category persistently fails. Your `history.json` already stores 14+ runs with entry prices, scores, and signals for every stock. All the data for a self-grading loop is already accumulating; nothing reads it.

### P10. Momentum chases tops
`return_4wk * 2` means +20% in a month earns the full +40 points with no context of where the stock sits in its own range. Combined with P2, a stock that spiked 20% *and* has a stale target scores high twice. /stock-news scores "trend position" instead: a modest dip inside an intact uptrend scores high, a parabolic extension scores low.

### Minor
- `save_json()` writes only 4 of the 5 weights into run metadata (filing is missing), so the history misstates the model.
- `datetime.utcnow()` deprecation warning on every run.

---

## 4. Recommended changes

### Phase 1 — accuracy fixes, yfinance-only, no new dependencies

| # | Change | Fixes |
|---|---|---|
| 1 | Count only `'up'` as an upgrade; `init` small positive; `main`/`reit` neutral. Score the direction of the last 5 actions, most recent weighted heaviest | P1 |
| 2 | Switch to `targetMedianPrice`; if high/low spread > 3x, halve the upside contribution and print "low-confidence target" in the detail string | P3 |
| 3 | Add forward-vs-trailing P/E to fundamentals: forward well below trailing with revenue growing is a bonus; forward P/E under ~8 on a cyclical (memory, energy) is a warning, not points | P4 |
| 4 | New sub-signal, estimate revisions: `ticker.eps_trend` gives current vs 7/30/60/90-days-ago EPS estimates. Rising during a price fall = strongest buy evidence; falling while price rises = multiple expansion flag | P2 |
| 5 | Short float from `info['shortPercentOfFloat']`: >15% is a penalty on high scorers and a note on the card | Free insight both skills use |
| 6 | Earnings flag: within 7 days, tag ⚠ "earnings imminent" and exclude from Top 5 (still ranked in the full table) | P7 |
| 7 | ETF track: score ETFs on momentum + social only, re-normalised, badge them "ETF" on the dashboard; flag persistent fetch-failures as "delisted? remove" in the email | P8 |
| 8 | Fix the two minor bugs (filing weight in history metadata, `utcnow`) | — |

Proposed re-weighting once revisions and insiders exist:

| Signal | Now | Proposed |
|---|---|---|
| Analyst (direction-corrected) | 30% | 25% |
| Momentum (trend-position aware) | 25% | 20% |
| Fundamentals (median target + fwd P/E) | 25% | 20% |
| Estimate revisions (new) | — | 10% |
| Insider (new, Phase 2) | — | 10% |
| Filing (unchanged) | 10% | 10% |
| Social (StockTwits-backed) | 10% | 5% |

### Phase 2 — two new signals from proven-reliable sources

**Status: done.** Item 10 shipped as scoped. Item 9 was cut after verification — see below.

| # | Change | Detail | Status |
|---|---|---|---|
| 9 | ~~StockTwits JSON API per ticker~~ | **Dropped.** Verified live: `api.stocktwits.com/api/2/streams/symbol/{TICKER}.json` returns a Cloudflare JS challenge page to a plain `requests.get()` call — not JSON. `/stock-news` gets this endpoint via Claude's interactive `WebFetch` tool, a different fetch path than a headless script in GitHub Actions; it does not transfer to an unattended engine, and solving the Cloudflare challenge would be bot-detection bypass, which is off the table regardless. Apewisdom stays the only automated social source; StockTwits sentiment remains available on demand via `/stock-news TICKER` | ❌ Cut |
| 10 | EDGAR Form 4 insider signal | Shipped. Reuses the CIK map and a shared `submissions.json` fetch already made for the 8-K signal — zero extra top-level SEC requests per ticker. Parses raw Form 4 XML directly (`xml.etree.ElementTree`, no Claude call, no new dependency): nets open-market buys (code `P`) against sales (code `S`) over the last 30 days, weighting officer/director/10%-owner transactions 1.5x. Verified on live data — MU showed 0 buys vs 68 sells and correctly triggered cluster selling. Hard rule from /stock-news adopted: 2+ insiders net-selling caps the v2 composite at 64.9 (BUY, never STRONG BUY), flagged on the card as "⚠ insider cluster selling" | ✅ Shipped |

### Phase 3 — insight features (the "better insights" half)

| # | Feature | Why |
|---|---|---|
| 11 | **Self-grading scorecard** | Each run, compute the forward return of every past Top-5 pick vs SPY at 1wk/1m/3m from `history.json` entry prices. New email section + dashboard "📊 Performance" tab: hit rate, average return vs benchmark, best/worst call. This is /value-dip-scanner's Step 0 applied to data you are already collecting. It also becomes the evidence base for tuning the weights above, instead of guessing |
| 12 | **Movers section** | "What changed today": biggest composite-score movers vs the previous run and every signal transition (WATCH→BUY, STRONG BUY→BUY). The daily email currently re-ranks 91 mostly-unchanged rows; the delta is the actionable part |
| 13 | **Theme concentration flag** | `groups.json` already maps every ticker to a theme. When 3+ of the Top 5 share one group, badge them "⚠ same trade: AI & Compute". Seven AI-infrastructure names are one bet, not seven, which is /value-dip-scanner's concentration rule applied to data the repo already has |
| 14 | **Dip / rebound tags** | Port the two cheapest /value-dip-scanner concepts: "🟢 stabilizing dip" (1M down, 1wk flat-or-up, estimates holding) and "rebound in progress" (20%+ off a low within a month, upside remaining). Both computable from the 3-month price series the engine already stores for sparklines |
| 15 | **Deep-dive handoff** | When a stock newly enters STRONG BUY or crashes out of it, the email links a one-tap prompt: "run /stock-news TICKER". The engine screens 91 wide; /stock-news validates one deep. That is the correct division of labour between your tools |

### Rollout discipline

Run the new scoring as a shadow column for two to four weeks: `history.json` records both `composite_score` and `composite_score_v2` per stock, the email keeps ranking on v1. Then let the Phase 3 scorecard judge which version's Top 5 actually beat SPY, and cut over on evidence. The speed comes from shipping Phase 1 in a day. The judgment comes from letting the scorecard prove it.

---

*Not financial advice. This reviews the engine's signal quality, not any individual stock.*
