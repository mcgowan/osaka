#!/usr/bin/env python3
"""v2 — EXPLORATORY clean instrument test: SPY vs SPX against the trading log,
both as a single dev-trained 20-feature model (so the ONLY difference is the
instrument; controls for the walk-forward confound in spx_log_validation.py).

For each instrument: train on dev TRAIN, calibrate isotonic on CALIB, set the
threshold, then score the chain-era log and run entry-gate + exit-override on
real logged P&L. SECOND-LOOK CAVEAT applies (examined chain era -> exploratory).

Run:  .venv/bin/python analysis/spx_frozen_compare.py
"""

import bisect
import csv
import math
import os
import sys

import numpy as np
from sklearn.isotonic import IsotonicRegression

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakout_features import build  # noqa: E402
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


def fit_and_score(symbol, trades):
    dev = build(start="2008-01-01", end="2025-06-30", symbol=symbol, _unlocked_full_span=True)
    days = sorted(dev[dev["day"] < "2025-07-01"]["day"].unique())
    sp = make_split(days, test_start="2025-07-01", min_gap_days=25)
    booster, feats = train(dev[dev["day"].isin(sp.train_days)], features=PROD_FEATURES)
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
        cands = [(sm, p) for sm, p in by_day.get((t["day"], DIR[t["side"]]), []) if sm <= em]
        out.append(max(cands, key=lambda x: x[0])[1] if cands else None)
    return out, thr


def main():
    trades = list(csv.DictReader(open(os.path.join(os.path.dirname(__file__),
                  "exit_characterization.csv"))))
    for t in trades:
        t["a"], t["h"] = _f(t["actual_pnl"]), _f(t["held_pnl"])

    print("Clean instrument test (single dev-trained 20-feat model, EXPLORATORY)\n")
    print(f"{'metric':32} {'SPY':>12} {'SPX':>12}")
    res = {}
    for sym in ("SPY", "SPX"):
        ps, thr = fit_and_score(sym, trades)
        for t, p in zip(trades, ps):
            t["p"] = p
        m = [t for t in trades if t["day"] >= "2024-12-01" and t["p"] is not None and t["a"] is not None]
        kept = [t for t in m if t["p"] <= thr]
        actual = sum(t["a"] for t in m)
        st = [t for t in m if t["reason"] in STOPS and t["h"] is not None]
        ex_model = sum(t["h"] if t["p"] <= thr else t["a"] for t in st)
        hold = [t for t in st if t["p"] <= thr]; stop = [t for t in st if t["p"] > thr]
        res[sym] = dict(thr=thr, n=len(m), keep_n=len(kept), keep=sum(t["a"] for t in kept),
                        actual=actual, ex_model=ex_model,
                        ex_actual=sum(t["a"] for t in st), ex_hold=sum(t["h"] for t in st),
                        hold_rec=sum(t["h"]-t["a"] for t in hold),
                        stop_rec=sum(t["h"]-t["a"] for t in stop))
    R = res
    def row(lbl, key, fmt="{:+,.0f}"):
        print(f"{lbl:32} {fmt.format(R['SPY'][key]):>12} {fmt.format(R['SPX'][key]):>12}")
    print(f"{'threshold':32} {R['SPY']['thr']:>12.3f} {R['SPX']['thr']:>12.3f}")
    print("--- ENTRY GATE (kept >= actual = win) ---")
    row("actual P&L (all trades)", "actual"); row("model KEEP P&L", "keep")
    print(f"{'  kept >= actual?':32} {'PASS' if R['SPY']['keep']>=R['SPY']['actual'] else 'FAIL':>12} "
          f"{'PASS' if R['SPX']['keep']>=R['SPX']['actual'] else 'FAIL':>12}")
    print("--- EXIT OVERRIDE (model > actual AND >= hold-all = win) ---")
    row("actual (stop all)", "ex_actual"); row("hold-all", "ex_hold"); row("MODEL (selective)", "ex_model")
    row("  HOLD-set recovery (want >0)", "hold_rec"); row("  STOP-set recovery (want <=0)", "stop_rec")


if __name__ == "__main__":
    main()
