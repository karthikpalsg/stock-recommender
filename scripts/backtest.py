# ============================================================
# BACKTEST — scripts/backtest.py
# ============================================================
# Grades the engine's signals and Dip Radar's alerts against what
# the price actually did afterwards. Adapted from the day-trader
# pack's Time Machine, on daily bars instead of 5-minute bars.
#
#   history  Replays every past signal: from the entry, walk each
#            daily bar and ask which came first, the stop or the
#            +20% target, within the hold period. A bar that
#            touches both counts as the stop. A gap past either
#            level fills at the open, not at the level.
#   odds     For today's picks, 10,000 simulated hold periods drawn
#            from the stock's own last year of daily bars (seed 19).
#
# Stops use the current rule for every event (3x daily volatility,
# 8% floor, 25% cap), so old and new picks are graded the same way.
#
# Command: python3 scripts/backtest.py history
#          python3 scripts/backtest.py odds
# Standard library only. Free Yahoo data, unofficial, can lag.
# ============================================================

import argparse
import csv
import json
import os
import random
import statistics as st
import time
import urllib.request
from datetime import date, datetime, timezone

ROOT          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_PATH  = os.path.join(ROOT, "data", "history.json")
DIP_LOG_PATH  = os.path.expanduser("~/Dip-radar/data/alerts_log.csv")
CACHE_DIR     = os.path.expanduser("~/.cache/stock-backtest")
REPORT_DIR    = os.path.join(ROOT, "picks")     # gitignored

TARGET_PCT    = 20.0    # TARGET_RETURN_PCT in config.py
HOLD_DAYS     = 126     # 6 months of trading days (HOLD_MONTHS)
STOP_FLOOR    = 8.0     # STOP_LOSS_PCT
STOP_CAP      = 25.0
STOP_VOL_MULT = 3.0
MIN_GROUP     = 20      # fewer events than this get a small-sample warning


# ============================================================
# PRICE DATA: Yahoo daily bars, cached once per day
# ============================================================
def get_bars(symbol):
    """Returns a list of (date_str, open, high, low, close), oldest first."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{symbol}_{date.today().isoformat()}.json")
    if os.path.exists(path):
        return json.load(open(path))
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range=2y&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(2):
        try:
            res = json.load(urllib.request.urlopen(req, timeout=20))["chart"]["result"][0]
            break
        except Exception:
            if attempt == 1:
                return []
            time.sleep(5)
    q = res["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(res.get("timestamp") or []):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        d = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        bars.append((d, o, h, l, c))
    json.dump(bars, open(path, "w"))
    time.sleep(0.3)   # polite pause
    return bars


def vol_stop_pct(closes):
    """Same rule as run.py volatility_stop_pct and Dip Radar's vol_stop."""
    if len(closes) < 21:
        return STOP_FLOOR
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
    daily_vol = st.stdev(rets) * 100
    return round(max(STOP_FLOOR, min(STOP_CAP, daily_vol * STOP_VOL_MULT)), 1)


# ============================================================
# EVENTS: one row per signal worth grading
# ============================================================
def engine_events():
    """A new event each time a ticker enters a signal it was not in on its
    previous run, plus each day's Top 5 under v1 and v2 (first entry only)."""
    runs = json.load(open(HISTORY_PATH))["runs"]
    by_date = {}
    for r in runs:                       # last run of each day wins
        by_date[r["run_date"]] = r
    events, prev_signal, prev_top = [], {}, {"v1": set(), "v2": set()}
    for d, r in sorted(by_date.items()):
        stocks = [s for s in r["stocks"] if s.get("price")]
        for s in stocks:
            t = s["ticker"]
            if s.get("instrument_type") == "ETF":
                continue
            if prev_signal.get(t) != s["signal"]:
                events.append({"ticker": t, "date": d, "price": s["price"],
                               "group": f"Signal: {s['signal']}"})
            prev_signal[t] = s["signal"]
        eligible = [s for s in stocks if s.get("instrument_type") != "ETF"]
        for model, key in (("v1", "composite_score"), ("v2", "composite_score_v2")):
            if not all(s.get(key) is not None for s in eligible):
                continue
            top = {s["ticker"]: s for s in sorted(eligible, key=lambda s: -s[key])[:5]}
            for t in top.keys() - prev_top[model]:
                events.append({"ticker": t, "date": d, "price": top[t]["price"],
                               "group": f"Top 5 ({model})"})
            prev_top[model] = set(top)
    return events


def dip_events(path):
    """First alert per ticker from Dip Radar, bucketed by the fields that
    split the results in the November review."""
    if not os.path.exists(path):
        return []
    events, seen = [], set()
    for row in csv.DictReader(open(path)):
        t = row["ticker"]
        if t in seen:
            continue
        seen.add(t)
        base = {"ticker": t, "date": row["date"], "price": float(row["price"])}
        off_low, chg = float(row["off_low"]), float(row["chg_1m"])
        groups = ["Dip: all first alerts",
                  "Dip: off 21d low < 5%" if off_low < 5 else "Dip: off 21d low >= 5%",
                  "Dip: 1M drop <= -15%" if chg <= -15 else "Dip: 1M drop > -15%",
                  "Dip: composite >= 80" if float(row["composite"]) >= 80 else "Dip: composite < 80"]
        if row.get("fresh_turn") == "True":
            groups.append("Dip: fresh analyst turn")
        for g in groups:
            events.append(dict(base, group=g))
    return events


# ============================================================
# REPLAY: walk the daily bars after one entry
# ============================================================
def replay(event, bars, spy):
    """Returns the outcome dict, or None when there is no data after the entry."""
    # Both logs date a price by its US session, and runs can land mid-session,
    # so the walk starts on the next session.
    idx = [i for i, b in enumerate(bars) if b[0] > event["date"]]
    if not idx or idx[0] < 21:
        return None
    first = idx[0]
    entry = event["price"]
    stop_pct = vol_stop_pct([b[4] for b in bars[:first]])
    stop, target = entry * (1 - stop_pct / 100), entry * (1 + TARGET_PCT / 100)
    risk = entry - stop
    outcome, exit_px, exit_i = "open", None, None
    for i in range(first, min(len(bars), first + HOLD_DAYS)):
        d, o, h, l, c = bars[i]
        if l <= stop:                                # stop checked first
            outcome, exit_px, exit_i = "stop", min(o, stop), i
            break
        if h >= target:
            outcome, exit_px, exit_i = "target", max(o, target), i
            break
    if exit_i is None:
        exit_i = min(len(bars), first + HOLD_DAYS) - 1
        exit_px = bars[exit_i][4]
        if exit_i - first + 1 >= HOLD_DAYS:
            outcome = "expired"                      # full hold, neither level hit
    ret = (exit_px / entry - 1) * 100
    spy_ret = None
    spy_dates = {b[0]: b[4] for b in spy}
    entry_day, exit_day = bars[first - 1][0], bars[exit_i][0]
    if entry_day in spy_dates and exit_day in spy_dates:
        spy_ret = (spy_dates[exit_day] / spy_dates[entry_day] - 1) * 100
    return {"outcome": outcome, "r": (exit_px - entry) / risk, "ret": ret,
            "excess": None if spy_ret is None else ret - spy_ret,
            "stop_pct": stop_pct, "days": exit_i - first + 1}


# ============================================================
# VERDICT: Time Machine's break-even rule, applied to a group
# ============================================================
def breakeven_rate(stop_pcts):
    """Share of resolved trades the target must win to break even:
    risk / (risk + reward), averaged over the group's stops."""
    avg_stop = st.mean(stop_pcts)
    return avg_stop / (avg_stop + TARGET_PCT)


def verdict(win_rate, need, n_resolved):
    if n_resolved == 0:
        return "Nothing resolved yet: no stop or target has been hit."
    gap = (win_rate - need) * 100
    if abs(gap) < 5:
        line = "Close to break-even"
    elif abs(gap) < 15:
        line = "Leans toward the target" if gap > 0 else "Leans toward the stop"
    else:
        line = "Strongly toward the target" if gap > 0 else "Strongly toward the stop"
    return f"{line}: target won {win_rate:.0%} of resolved, needs {need:.0%}."


def summarise(group, results):
    resolved = [r for r in results if r["outcome"] in ("stop", "target")]
    wins = sum(r["outcome"] == "target" for r in resolved)
    win_rate = wins / len(resolved) if resolved else 0.0
    need = breakeven_rate([r["stop_pct"] for r in results])
    ex = [r["excess"] for r in results if r["excess"] is not None]
    return {
        "group": group, "n": len(results),
        "target": wins, "stop": len(resolved) - wins,
        "open": sum(r["outcome"] in ("open", "expired") for r in results),
        "early_stop": sum(r["outcome"] == "stop" and r["days"] <= 21 for r in results),
        "win_rate": win_rate, "need": need,
        "avg_r": st.mean(r["r"] for r in results),
        "avg_excess": st.mean(ex) if ex else None,
        "beat_spy": sum(e > 0 for e in ex) / len(ex) if ex else None,
        "verdict": verdict(win_rate, need, len(resolved)),
    }


# ============================================================
# ODDS: Time Machine-style simulation for today's picks
# ============================================================
# With the trend removed, a random walk hits the target first at exactly
# the break-even rate, so "volatility only" odds carry no information about
# the outcome. What they do measure is how often normal noise reaches the
# stop before the thesis has had time to work. The outcome odds come from
# last year's trend and are labelled as such.
def simulate(bars, entry, stop_pct, replays, seed, demean):
    """Each simulated day is a random past day's open/high/low/close, as ratios to
    the previous close, scaled to the current price. demean strips last year's
    average daily drift. Returns (target first, stop first, stop within 21 days)."""
    hist = bars[-253:]
    ratios = [(b[1] / a[4], b[2] / a[4], b[3] / a[4], b[4] / a[4])
              for a, b in zip(hist, hist[1:])]
    if demean:
        drift = st.mean(r[3] for r in ratios)
        ratios = [tuple(x / drift for x in r) for r in ratios]
    rng = random.Random(seed)
    stop, target = entry * (1 - stop_pct / 100), entry * (1 + TARGET_PCT / 100)
    hit_t = hit_s = early_s = 0
    for _ in range(replays):
        px = entry
        for day in range(HOLD_DAYS):
            o, h, l, c = rng.choice(ratios)
            if px * l <= stop:
                hit_s += 1
                early_s += day < 21
                break
            if px * h >= target:
                hit_t += 1
                break
            px *= c
    return hit_t / replays, hit_s / replays, early_s / replays


def run_odds(signals, replays, seed):
    runs = json.load(open(HISTORY_PATH))["runs"]
    last = runs[-1]
    picks = [s for s in last["stocks"]
             if s["signal"] in signals and s.get("instrument_type") != "ETF" and s.get("price")]
    picks.sort(key=lambda s: -s["composite_score"])
    lines = [f"## Odds for today's picks",
             f"*{last['run_date']} {last['run_label']} · {replays:,} simulated {HOLD_DAYS}-day holds "
             f"per name · seed {seed} · target +{TARGET_PCT:.0f}% · stop 3x daily vol (8-25%)*", "",
             "| Ticker | Signal | Stop | Needs | Noise stop in 1M | Target first | Stop first "
             "| Read (if last year's trend repeats) |",
             "|---|---|---|---|---|---|---|---|"]
    for s in picks:
        bars = get_bars(s["ticker"])
        if len(bars) < 253:
            lines.append(f"| {s['ticker']} | {s['signal']} | | | under a year of bars, skipped | | | |")
            continue
        stop_pct = vol_stop_pct([b[4] for b in bars])
        need = stop_pct / (stop_pct + TARGET_PCT)
        _, _, noise = simulate(bars, s["price"], stop_pct, replays, seed, demean=True)
        t1, s1, _ = simulate(bars, s["price"], stop_pct, replays, seed, demean=False)
        read = verdict(t1 / (t1 + s1), need, 1).split(":")[0] if t1 + s1 else "Mostly unresolved"
        lines.append(f"| {s['ticker']} | {s['signal']} | -{stop_pct:.1f}% | {need:.0%} | "
                     f"{noise:.0%} | {t1:.0%} | {s1:.0%} | {read} |")
    lines += ["",
              "*Noise stop in 1M* removes last year's trend and counts holds stopped out inside 21 "
              "trading days: how often normal swings alone end the trade before the thesis can work. "
              "*Target first / Stop first* replay last year's actual days, trend included, which flatters "
              "recent winners and punishes recent losers. Neither is a prediction. Earnings, news and "
              "regime changes can do things the last year never did."]
    return "\n".join(lines)


# ============================================================
# REPORT
# ============================================================
def fmt_pct(x, signed=True):
    return "n/a" if x is None else (f"{x:+.1f}" if signed else f"{x:.0%}")


def run_history(include_dip, dip_path):
    events = engine_events() + (dip_events(dip_path) if include_dip else [])
    spy = get_bars("SPY")
    tickers = sorted({e["ticker"] for e in events})
    bars = {t: get_bars(t) for t in tickers}
    failed = [t for t in tickers if not bars[t]]
    groups = {}
    for e in events:
        res = replay(e, bars[e["ticker"]], spy) if bars[e["ticker"]] else None
        if res:
            groups.setdefault(e["group"], []).append(res)
    order = ["Top 5 (v1)", "Top 5 (v2)", "Signal: STRONG BUY", "Signal: BUY",
             "Signal: WATCH", "Signal: AVOID"]
    keys = [g for g in order if g in groups] + sorted(g for g in groups if g not in order)
    rows = [summarise(g, groups[g]) for g in keys]

    lines = [f"# Signal Backtest — {date.today().isoformat()}",
             f"*{len(events)} events across {len(tickers)} tickers, "
             f"{len(failed)} with no price data{(' (' + ', '.join(failed) + ')') if failed else ''}. "
             f"Target +{TARGET_PCT:.0f}%, stop 3x daily vol (8-25%), hold {HOLD_DAYS} trading days. "
             f"Daily bars, stop checked before target, gaps fill at the open.*", "",
             "| Group | Events | Target first | Stop first | Stopped in 1M | Still open | Win rate "
             "| Needs | Avg R | Avg vs SPY | Beat SPY |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        flag = " ⚠" if r["n"] < MIN_GROUP else ""
        lines.append(f"| {r['group']}{flag} | {r['n']} | {r['target']} | {r['stop']} | {r['early_stop']} | {r['open']} "
                     f"| {r['win_rate']:.0%} | {r['need']:.0%} | {r['avg_r']:+.2f} "
                     f"| {fmt_pct(r['avg_excess'])} | {fmt_pct(r['beat_spy'], signed=False)} |")
    lines += ["", "## Verdicts", ""]
    lines += [f"- **{r['group']}**: {r['verdict']}" for r in rows]
    lines += ["",
              "*Avg R* marks open trades to the last close: +1.0 is a full target at a 1:1 stop, "
              "-1.0 a full stop. *Needs* is the win rate that breaks even at this group's stops. "
              f"⚠ marks groups under {MIN_GROUP} events. Events cluster in time, so they are not "
              "independent: one market phase can carry a whole group. Not financial advice."]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Grade past signals and simulate today's picks.")
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("history", help="replay every past signal and dip alert")
    h.add_argument("--no-dip", action="store_true", help="engine history only")
    h.add_argument("--dip-log", default=DIP_LOG_PATH)
    o = sub.add_parser("odds", help="simulate today's picks")
    o.add_argument("--signals", default="STRONG BUY,BUY")
    o.add_argument("--replays", type=int, default=10000)
    o.add_argument("--seed", type=int, default=19)
    for sp in (h, o):
        sp.add_argument("--write", action="store_true", help=f"save the report in {REPORT_DIR}")
    a = p.parse_args()

    if a.cmd == "history":
        report = run_history(not a.no_dip, a.dip_log)
    else:
        report = run_odds([s.strip() for s in a.signals.split(",")], a.replays, a.seed)
    print(report)
    if a.write:
        os.makedirs(REPORT_DIR, exist_ok=True)
        out = os.path.join(REPORT_DIR, f"backtest_{a.cmd}_{date.today().isoformat()}.md")
        open(out, "w").write(report + "\n")
        print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
