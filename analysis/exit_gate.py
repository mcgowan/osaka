#!/usr/bin/env python3
"""v2 — EXPLORATORY exit test (second look at the chain era; pre-registered
2026-06-15). Can the model's entry conviction guide a hold-vs-stop decision -
ride the shakeouts, keep stopping the genuine reversals?

Population: chain-era trades stopped early (risk_off_reversal / sr_inner_breach).
Policy: when a stop fires, HOLD to expiry (held_pnl) if entry P(reversed) <=
threshold (high conviction), else take the stop (actual_pnl). Real logged P&L.

SECOND LOOK CAVEAT: the chain era was already examined by the entry gate, so this
is exploratory/suggestive, not a clean one-shot. Confirmation needs fresh trades.

Run ONCE.  .venv/bin/python analysis/exit_gate.py
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
STOPS = {"risk_off_reversal", "sr_inner_breach"}


def _emod(s):
    h, m = s.split(":"); return int(h) * 60 + int(m) - 570


def _f(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def main():
    booster = lgb.Booster(model_file=os.path.join(BUNDLE, "booster.txt"))
    iso = pickle.load(open(os.path.join(BUNDLE, "isotonic.pkl"), "rb"))
    cfg = json.load(open(os.path.join(BUNDLE, "model.json")))
    thr, feats = cfg["operating_threshold"], cfg["features"]

    df = build(start="2024-09-01", _unlocked_full_span=True)
    df["p"] = iso.predict(booster.predict(df[feats]))
    by_day = {}
    for r in df.itertuples():
        by_day.setdefault((r.day, r.side), []).append((r.start_mod, r.p))

    trades = list(csv.DictReader(open(os.path.join(os.path.dirname(__file__),
                  "exit_characterization.csv"))))
    for t in trades:
        t["a"], t["h"] = _f(t["actual_pnl"]), _f(t["held_pnl"])
        em = _emod(t["entry_et"])
        cands = [(sm, p) for sm, p in by_day.get((t["day"], DIR[t["side"]]), []) if sm <= em]
        t["p"] = max(cands, key=lambda x: x[0])[1] if cands else None

    st = [t for t in trades if t["day"] >= "2024-12-01" and t["reason"] in STOPS
          and t["p"] is not None and t["a"] is not None and t["h"] is not None]
    print(f"chain-era early-stopped trades: {len(st)} (risk_off + sr_inner)\n")

    actual = sum(t["a"] for t in st)
    holdall = sum(t["h"] for t in st)
    model = sum(t["h"] if t["p"] <= thr else t["a"] for t in st)
    print(f"=== EXIT TEST (threshold {thr:.3f}) — total P&L over stopped trades ===")
    print(f"  (a) actual   (stop every time):  ${actual:+10,.0f}")
    print(f"  (b) hold-all (override all):     ${holdall:+10,.0f}")
    print(f"  (c) MODEL    (hold if conv):     ${model:+10,.0f}")

    print(f"\nverdict (both required):")
    print(f"  model > actual?     ${model:+,.0f} vs ${actual:+,.0f}  -> {'PASS' if model > actual else 'FAIL'}")
    print(f"  model >= hold-all?  ${model:+,.0f} vs ${holdall:+,.0f}  -> {'PASS' if model >= holdall else 'FAIL'}")

    hold = [t for t in st if t["p"] <= thr]
    stop = [t for t in st if t["p"] > thr]
    print(f"\ndiscriminator — recovery (held - actual) by model decision:")
    for lbl, sub in (("HOLD (conv, p<=thr)", hold), ("STOP (p>thr)", stop)):
        if sub:
            rec = [t["h"] - t["a"] for t in sub]
            print(f"  {lbl:22s} n={len(sub):3d}  mean recovery ${np.mean(rec):+8,.0f}  "
                  f"total ${sum(rec):+9,.0f}  (want HOLD>0, STOP<=0)")

    print("\npolicy P&L vs hold-threshold (context):")
    ps = np.array([t["p"] for t in st])
    for q in (0.3, 0.5, 0.7, 1.0):
        th = np.quantile(ps, q)
        v = sum(t["h"] if t["p"] <= th else t["a"] for t in st)
        print(f"  hold lowest-P {q:.0%} (thr {th:.2f}): ${v:+10,.0f}")


if __name__ == "__main__":
    main()
