#!/usr/bin/env python3
"""v2 — EXPLORATORY: is the SPX / no-volume model superior against the trading log?

Scores the chain-era logged trades with the 7 SPX walk-forward versions (each
trade by the version deployed for its quarter - proper point-in-time, no
lookahead), then re-runs the entry gate + exit test and compares to the SPY
single-model baseline.

SECOND/THIRD LOOK CAVEAT: the chain era is already examined. This is exploratory -
it tells us whether SPX looks better against real dollars, NOT a clean verdict.
Forward is the only clean test. Real logged P&L throughout (no simulation).

Run:  .venv/bin/python analysis/spx_log_validation.py
"""

import bisect
import csv
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakout_features import build  # noqa: E402

MODELS = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "breakout-models-v2")
DIR = {"p": "up", "c": "down"}
STOPS = {"risk_off_reversal", "sr_inner_breach"}

# deploy schedule: a trade in quarter Q is scored by the version trained the
# prior quarter (it never saw Q). Keyed by "deploy from this date".
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


def load_artifacts():
    return {label: json.load(open(os.path.join(MODELS, f"{label}.json")))
            for _, label in DEPLOY}


def score(art, xrow):
    x = [xrow.get(f) for f in art["features"]]

    def leaf(n):
        while "leaf_value" not in n:
            v = x[n["split_feature"]]
            go = n["default_left"] if (v is None or v != v) else (v <= n["threshold"])
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


def _emod(s):
    h, m = s.split(":"); return int(h) * 60 + int(m) - 570


def _f(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def main():
    arts = load_artifacts()
    df = build(start="2024-09-01", symbol="SPX", _unlocked_full_span=True)
    # score each breakout with its point-in-time version
    by_day = {}
    for r in df.to_dict("records"):
        v = version_for(r["day"])
        if v is None:
            continue
        p = score(arts[v], r)
        thr = arts[v]["operating_threshold"]
        by_day.setdefault((r["day"], r["side"]), []).append((r["start_mod"], p, thr))

    trades = list(csv.DictReader(open(os.path.join(os.path.dirname(__file__),
                  "exit_characterization.csv"))))
    for t in trades:
        t["a"], t["h"] = _f(t["actual_pnl"]), _f(t["held_pnl"])
        em = _emod(t["entry_et"])
        cands = [(sm, p, thr) for sm, p, thr in by_day.get((t["day"], DIR[t["side"]]), []) if sm <= em]
        t["p"], t["thr"] = (max(cands, key=lambda x: x[0])[1:] if cands else (None, None))

    m = [t for t in trades if t["day"] >= "2024-12-01" and t["p"] is not None and t["a"] is not None]
    print(f"SPX walk-forward scoring of {len(m)} chain-era trades (EXPLORATORY — second look)\n")

    # ENTRY GATE: keep if P <= version threshold
    kept = [t for t in m if t["p"] <= t["thr"]]
    skip = [t for t in m if t["p"] > t["thr"]]
    def tot(s): return sum(t["a"] for t in s)
    print("=== ENTRY GATE (model as filter on actual trades) ===")
    print(f"  ACTUAL(all)  n={len(m):3d}  ${tot(m):+10,.0f}")
    print(f"  model KEEP   n={len(kept):3d}  ${tot(kept):+10,.0f}")
    print(f"  model SKIP   n={len(skip):3d}  ${tot(skip):+10,.0f}  (want net-NEGATIVE)")
    print(f"  SPX kept>=actual? {'PASS' if tot(kept)>=tot(m) else 'FAIL'}   "
          f"SPY baseline: FAIL (kept +$50,812 vs +$136,725)")

    # EXIT OVERRIDE: hold stopped trade to expiry if conviction high (P<=thr)
    st = [t for t in m if t["reason"] in STOPS and t["h"] is not None]
    actual = sum(t["a"] for t in st)
    holdall = sum(t["h"] for t in st)
    model = sum(t["h"] if t["p"] <= t["thr"] else t["a"] for t in st)
    print(f"\n=== EXIT OVERRIDE (hold shakeouts, keep stopping reversals) ===")
    print(f"  stopped trades n={len(st)}")
    print(f"  actual (stop all)     ${actual:+10,.0f}")
    print(f"  hold-all              ${holdall:+10,.0f}")
    print(f"  MODEL (selective)     ${model:+10,.0f}   SPY baseline: +$16,448")
    print(f"  SPX model>actual & >=hold-all? "
          f"{'PASS' if model>actual and model>=holdall else 'PARTIAL/FAIL'}")
    hold = [t for t in st if t["p"] <= t["thr"]]
    stop = [t for t in st if t["p"] > t["thr"]]
    def rec(s): return sum(t["h"] - t["a"] for t in s)
    print(f"  discriminator: HOLD set recovery ${rec(hold):+,.0f} (n={len(hold)}), "
          f"STOP set ${rec(stop):+,.0f} (n={len(stop)})  (want HOLD>0, STOP<=0)")


if __name__ == "__main__":
    main()
