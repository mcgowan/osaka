#!/usr/bin/env python3
"""v2 — ONE-SHOT economic gate (pre-registered, locked 2026-06-15).

The frozen model (data/processed/breakout-model/) acts as an EXTRA gate on the
trader's actual logged chain-era trades: skip a trade if calibrated P(reversed)
> operating_threshold, else keep. Measured in REAL logged P&L (actual_pnl) - no
simulation. Held-out chain era (Dec-2024->); model never saw it.

PASS (both): (1) kept-trades total P&L >= actual total P&L (skipping net-accretive);
(2) skipped set is net-NEGATIVE and over-represents risk_off_reversal losers.
Verdict at the frozen threshold; curve reported for context only.

Run ONCE.  .venv/bin/python analysis/economic_gate.py
"""

import csv
import json
import os
import pickle
import sys

import lightgbm as lgb
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakout_features import build  # noqa: E402

BUNDLE = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "breakout-model")
DIR = {"p": "up", "c": "down"}


def _emod(s):
    h, m = s.split(":"); return int(h) * 60 + int(m) - 570


def main():
    booster = lgb.Booster(model_file=os.path.join(BUNDLE, "booster.txt"))
    iso = pickle.load(open(os.path.join(BUNDLE, "isotonic.pkl"), "rb"))
    cfg = json.load(open(os.path.join(BUNDLE, "model.json")))
    thr, feats = cfg["operating_threshold"], cfg["features"]

    # chain-era breakout features (sanctioned final-eval), warm-up from Sep-2024
    df = build(start="2024-09-01", _unlocked_full_span=True)
    df["p"] = iso.predict(booster.predict(df[feats]))
    by_day = {}
    for r in df.itertuples():
        by_day.setdefault((r.day, r.side), []).append((r.start_mod, r.p))

    trades = list(csv.DictReader(open(os.path.join(os.path.dirname(__file__),
                  "exit_characterization.csv"))))
    for t in trades:
        t["pnl"] = float(t["actual_pnl"]) if t["actual_pnl"] else None
        em = _emod(t["entry_et"])
        cands = [(sm, p) for sm, p in by_day.get((t["day"], DIR[t["side"]]), []) if sm <= em]
        t["p"] = max(cands, key=lambda x: x[0])[1] if cands else None

    m = [t for t in trades if t["p"] is not None and t["pnl"] is not None
         and t["day"] >= "2024-12-01"]
    print(f"matched chain-era trades: {len(m)} of {len([t for t in trades if t['day']>='2024-12-01'])} "
          f"({min(t['day'] for t in m)}..{max(t['day'] for t in m)})\n")

    def stats(label, sub):
        n = len(sub); pnl = sum(t["pnl"] for t in sub)
        wins = sum(t["pnl"] > 0 for t in sub)
        ro = [t for t in sub if t["reason"] == "risk_off_reversal"]
        ro_pnl = sum(t["pnl"] for t in ro)
        print(f"  {label:14s} n={n:3d}  total ${pnl:+9,.0f}  win {wins/n:5.1%}  "
              f"risk_off n={len(ro):3d} ${ro_pnl:+9,.0f}")
        return pnl

    kept = [t for t in m if t["p"] <= thr]
    skip = [t for t in m if t["p"] > thr]
    print(f"=== ONE-SHOT GATE (threshold {thr:.3f}) ===")
    actual = stats("ACTUAL(all)", m)
    keptp = stats("model KEEP", kept)
    skipp = stats("model SKIP", skip)

    print(f"\nverdict:")
    print(f"  (1) kept >= actual?   ${keptp:+,.0f} vs ${actual:+,.0f}  -> "
          f"{'PASS' if keptp >= actual else 'FAIL'}")
    ro_skip = [t for t in skip if t["reason"] == "risk_off_reversal"]
    ro_all = [t for t in m if t["reason"] == "risk_off_reversal"]
    share = len(ro_skip) / len(skip) if skip else 0
    base = len(ro_all) / len(m)
    print(f"  (2) skipped net-neg?  ${skipp:+,.0f}  -> {'PASS' if skipp < 0 else 'FAIL'}")
    print(f"      risk_off over-represented in skips? {share:.0%} of skips vs {base:.0%} base "
          f"-> {'PASS' if share > base else 'FAIL'}")

    print("\nP&L-vs-selectivity curve (context only):")
    ps = np.array([t["p"] for t in m])
    for q in (0.3, 0.4, 0.5, 0.6, 0.7, 1.0):
        th = np.quantile(ps, q)
        k = [t for t in m if t["p"] <= th]
        print(f"  keep lowest-P {q:.0%} (thr {th:.2f}): n={len(k):3d}  "
              f"${sum(t['pnl'] for t in k):+9,.0f}  win {sum(t['pnl']>0 for t in k)/len(k):.0%}")


if __name__ == "__main__":
    main()
