#!/usr/bin/env python3
"""Decides whether a scheduled run should proceed, and which slot it fills.

GitHub starts scheduled runs hours late (Oct 2026: 3-6 hours), so requiring the
exact Sydney hour skipped every run from Oct 24. Each slot is now a window, and
the first arrival in a window takes the slot. Later arrivals skip.

Writes should_run, run_label and run_emoji to $GITHUB_OUTPUT.
"""
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SYDNEY = ZoneInfo("Australia/Sydney")
HISTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "history.json")

# (first hour, last hour inclusive, label, emoji), in Sydney time.
SLOTS = [
    (5, 6, "5am Early Signal", "🌅"),
    (7, 11, "7am Final Signal", "📈"),
    (12, 21, "12pm Midday Signal", "☀️"),
]


def slot_for(hour):
    for first, last, label, emoji in SLOTS:
        if first <= hour <= last:
            return label, emoji
    return None


def already_ran(label, sydney_date, runs):
    """True if this slot has a stored run on this Sydney date."""
    for run in runs:
        stamp = run.get("run_timestamp_utc")
        if run.get("run_label") != label or not stamp:
            continue
        ran_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if ran_at.astimezone(SYDNEY).date() == sydney_date:
            return True
    return False


def decide(now_sydney, event, runs):
    """Returns (should_run, label, emoji, reason)."""
    if event == "workflow_dispatch":
        return True, "Manual Run", "🔧", "manual trigger"
    slot = slot_for(now_sydney.hour)
    if slot is None:
        return False, "skip", "⏭", f"Sydney hour {now_sydney.hour} is outside every slot"
    label, emoji = slot
    if already_ran(label, now_sydney.date(), runs):
        return False, "skip", "⏭", f"{label} already ran today"
    return True, label, emoji, f"first arrival for {label}"


def load_runs(path=HISTORY):
    try:
        with open(path) as f:
            return json.load(f).get("runs", [])
    except (OSError, ValueError):
        return []


def main():
    now = datetime.now(timezone.utc).astimezone(SYDNEY)
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    should_run, label, emoji, reason = decide(now, event, load_runs())
    print(f"Sydney time {now:%Y-%m-%d %H:%M %Z} | {event} | "
          f"{'RUN' if should_run else 'SKIP'}: {reason}")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"should_run={'true' if should_run else 'false'}\n")
        out.write(f"run_label={label}\n")
        out.write(f"run_emoji={emoji}\n")


if __name__ == "__main__":
    main()
