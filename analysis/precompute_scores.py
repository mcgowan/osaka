#!/usr/bin/env python3
"""v2 — precompute a breakout -> success-probability lookup table for the
eleuthera backtest. No JS feature port, no Python at trade time: osaka runs the
tested feature pipeline + the right walk-forward version for each breakout's
date (point-in-time, no lookahead) and writes one row per breakout.

eleuthera just reads the row for its detected breakout (match by day + side +
start time). p_success = 1 - p_reversed (the model predicts the chance it
REVERSES; success is the complement).

Run:  .venv/bin/python analysis/precompute_scores.py
Out:  data/processed/breakout-scores.csv
"""

import bisect
import csv
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakout_features import build  # noqa: E402
from data.loader import OUT_DIR  # noqa: E402

MODELS = os.path.join(OUT_DIR, "breakout-models-v2")
OUT = os.path.join(OUT_DIR, "breakout-scores.csv")
DEPLOY = [("2024-10-01", "sep-2024"), ("2025-01-01", "dec-2024"),
          ("2025-04-01", "mar-2025"), ("2025-07-01", "jun-2025"),
          ("2025-10-01", "sep-2025"), ("2026-01-01", "dec-2025"),
          ("2026-04-01", "mar-2026")]


def version_for(day):
    v = None
    for start, label in DEPLOY:
        if day >= start:
            v = label
    return v


def p_reversed(art, row):
    x = [row.get(f) for f in art["features"]]

    def leaf(n):
        while "leaf_value" not in n:
            val = x[n["split_feature"]]
            go = n["default_left"] if (val is None or val != val) else (val <= n["threshold"])
            n = n["left_child"] if go else n["right_child"]
        return n["leaf_value"]

    margin = sum(leaf(t) for t in art["trees"]) + art["init_offset"]
    raw = 1 / (1 + math.exp(-margin))
    xs, ys = art["isotonic"]["x"], art["isotonic"]["y"]
    if raw <= xs[0]:
        return ys[0]
    if raw >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_left(xs, raw)
    return ys[i - 1] + (ys[i] - ys[i - 1]) * (raw - xs[i - 1]) / (xs[i] - xs[i - 1])


def clock(start_mod):
    t = 570 + int(start_mod)          # minutes from midnight (09:30 = 570)
    return f"{t // 60:02d}:{t % 60:02d}"


def main():
    arts = {label: json.load(open(os.path.join(MODELS, f"{label}.json")))
            for _, label in DEPLOY}
    df = build(start="2024-09-01", symbol="SPY", _unlocked_full_span=True)
    rows = []
    for r in df.to_dict("records"):
        v = version_for(r["day"])
        if v is None:
            continue                  # before the first deploy quarter
        pr = p_reversed(arts[v], r)
        rows.append({
            "day": r["day"], "side": r["side"], "start_et": clock(r["start_mod"]),
            "start_mod": int(r["start_mod"]), "attempt": int(r["attempt"]),
            "model_version": v,
            "p_success": round(1 - pr, 4),
            "p_reversed": round(pr, 4),
            "reversal_threshold": round(arts[v]["operating_threshold"], 4),
        })
    rows.sort(key=lambda x: (x["day"], x["start_mod"], x["side"]))
    cols = ["day", "side", "start_et", "start_mod", "attempt", "model_version",
            "p_success", "p_reversed", "reversal_threshold"]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    print(f"wrote {len(rows):,} breakout scores -> {OUT}")
    print(f"  span {rows[0]['day']} .. {rows[-1]['day']}, "
          f"p_success {min(r['p_success'] for r in rows):.2f}..{max(r['p_success'] for r in rows):.2f}")
    print("\nsample rows:")
    print("  " + " | ".join(cols))
    for r in rows[:6]:
        print("  " + " | ".join(str(r[c]) for c in cols))


if __name__ == "__main__":
    main()
