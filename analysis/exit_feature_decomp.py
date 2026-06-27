#!/usr/bin/env python3
"""v2 — isolate the two confounded changes on the EXIT-override result: instrument
(SPY->SPX) and feature set (with-volume 26 -> no-volume 20). Identical pipeline
for every cell (same dev train window, calibration, matching, threshold method),
so each row differs from its neighbour by EXACTLY one variable.

Note: SPX+volume is impossible (the index has no volume), so the natural 2x2 is
missing that cell by construction.

EXPLORATORY (small, re-examined chain-era sample). Run:
  .venv/bin/python analysis/exit_feature_decomp.py
"""

import csv
import os
import sys

import numpy as np
from sklearn.isotonic import IsotonicRegression

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakout_features import build, FEATURES  # noqa: E402
from models.breakout_gbt import predict, train  # noqa: E402
from models.splits import make_split  # noqa: E402
from models.walk_forward_train import PROD_FEATURES  # noqa: E402

DIR = {"p": "up", "c": "down"}
STOPS = {"risk_off_reversal", "sr_inner_breach"}


def _emod(s):
    h, m = s.split(":"); return int(h) * 60 + int(m) - 570


def _f(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def fit_and_score(symbol, feats, trades):
    dev = build(start="2008-01-01", end="2025-06-30", symbol=symbol, _unlocked_full_span=True)
    days = sorted(dev[dev["day"] < "2025-07-01"]["day"].unique())
    sp = make_split(days, test_start="2025-07-01", min_gap_days=25)
    booster, _ = train(dev[dev["day"].isin(sp.train_days)], features=feats)
    ca = dev[dev["day"].isin(sp.calib_days)].copy()
    ca["raw"] = predict(booster, ca, feats)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(
        ca["raw"].to_numpy(), ca["reversed"].to_numpy())
    cat = ca[ca["tradeable"] == 1]
    thr = float(np.quantile(iso.predict(cat["raw"].to_numpy()), cat["confirmed_5bar"].mean()))
    chain = build(start="2024-09-01", symbol=symbol, _unlocked_full_span=True)
    chain["p"] = iso.predict(predict(booster, chain, feats))
    by_day = {}
    for r in chain.to_dict("records"):
        by_day.setdefault((r["day"], r["side"]), []).append((r["start_mod"], r["p"]))
    out = []
    for t in trades:
        em = _emod(t["entry_et"])
        c = [(sm, p) for sm, p in by_day.get((t["day"], DIR[t["side"]]), []) if sm <= em]
        out.append((max(c, key=lambda x: x[0])[1] if c else None, thr))
    return out


def main():
    trades = list(csv.DictReader(open(os.path.join(os.path.dirname(__file__),
                  "exit_characterization.csv"))))
    for t in trades:
        t["a"], t["h"] = _f(t["actual_pnl"]), _f(t["held_pnl"])

    cells = [("SPY +volume (26)", "SPY", list(FEATURES)),
             ("SPY no-volume (20)", "SPY", PROD_FEATURES),
             ("SPX no-volume (20)", "SPX", PROD_FEATURES)]
    print("EXIT-OVERRIDE decomposition (identical pipeline; one variable per row)\n")
    print(f"{'config':22} {'exit P&L':>10} {'HOLD-rec':>10} {'STOP-rec':>10} {'thr':>6}")
    prev = None
    for name, sym, feats in cells:
        ps = fit_and_score(sym, feats, trades)
        for t, (p, thr) in zip(trades, ps):
            t["p"], t["thr"] = p, thr
        st = [t for t in trades if t["day"] >= "2024-12-01" and t["reason"] in STOPS
              and t["p"] is not None and t["h"] is not None]
        model = sum(t["h"] if t["p"] <= t["thr"] else t["a"] for t in st)
        hold = [t for t in st if t["p"] <= t["thr"]]; stop = [t for t in st if t["p"] > t["thr"]]
        hr = sum(t["h"] - t["a"] for t in hold); sr = sum(t["h"] - t["a"] for t in stop)
        print(f"{name:22} {model:>+10,.0f} {hr:>+10,.0f} {sr:>+10,.0f} {st[0]['thr']:>6.3f}")
        if prev is not None:
            print(f"  -> change from above ({prev[1]}): {model-prev[0]:+,.0f}")
        prev = (model, name)
    print("\n(small re-examined sample — directional, not a verdict; SPX+volume impossible)")


if __name__ == "__main__":
    main()
