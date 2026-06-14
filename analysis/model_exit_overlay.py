#!/usr/bin/env python3
"""False-breakout overlay (FR-5.2/5.3 decision support): does the frozen v1
model's near-strike "hold" signal save money at the trader's ACTUAL exit
moments?

For each logged EARLY exit (exit rule fired; not an expiration), consult the
frozen v1 model at the exit state. Headline rule (parameter-free): hold to
settlement instead of exiting iff p_model > p_mkt (the documented near-strike
residual). Counterfactual P&L = held_pnl when the override fires, else
actual_pnl. Net delta = Sum(counterfactual) - Sum(actual).

HARD CONSTRAINTS (this file is decision support, NOT model selection):
1. Pre-TEST_START trades only (day < 2025-12-01). The locked period is never
   loaded, predicted on, or summarized here - it stays unspent for Phase 5.
2. Frozen v1 model: 29 features, gbt_params.json, trained on split.train_days.
   Read once; no retraining, no feature/threshold tuning against this.
3. Deterministic; no sampling.

Feature reconstruction at the exit bar: the strike-dependent features (D, pmkt)
are the EXACT exit-strike values from exit_characterization (D_exit, pmkt_exit);
all other 27 features are snapped to the master's floor stride bar at or before
the exit minute (point-in-time; <= 5-min staleness, per the task's allowance).
Era split: TRAIN-era trades are in-sample for the model (optimistic ceiling);
HELD-OUT-era (CALIB+VALID+embargo, all out-of-training-sample) is the honest
read.

Prereq: analysis/exit_characterization.csv (run exit_characterization.py).
Run:    .venv/bin/python analysis/model_exit_overlay.py
"""

import csv
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.features import load_master  # noqa: E402
from data.loader import TEST_START  # noqa: E402
from models import gbt  # noqa: E402
from models.splits import make_split  # noqa: E402

CSV = os.path.join(os.path.dirname(__file__), "exit_characterization.csv")
STRIDE = 5


def _exit_minute(exit_et):
    h, m = map(int, exit_et.split(":"))
    return (h * 60 + m) - (9 * 60 + 30)


def load_early_exits():
    """Pre-TEST_START early-exit trades from the FR-5.3 characterization CSV."""
    if not os.path.exists(CSV):
        sys.exit(f"{CSV} missing - run analysis/exit_characterization.py first")
    out = []
    for r in csv.DictReader(open(CSV)):
        if r["reason"] == "expiration" or r["D_exit"] == "":
            continue
        if r["day"] >= TEST_START:                 # rule #1: locked stays unspent
            continue
        out.append(r)
    return out


def build_features(trades, master):
    """Assemble the 29-feature matrix for the exit states (snap non-strike
    features to the floor stride bar; override D/pmkt with exact exit values).
    Returns (DataFrame[features], list of trades kept)."""
    feats = gbt.feature_list(True)
    nonstrike = [f for f in feats if f not in ("D", "pmkt")]
    # one representative row per (day, minute, side) - non-strike features are
    # identical across the strike grid at a bar
    m = master.drop_duplicates(["day", "minute", "side"]).set_index(
        ["day", "minute", "side"]).sort_index()
    by_daysidemin = {}
    for (day, minute, side) in m.index:
        by_daysidemin.setdefault((day, side), []).append(minute)
    for k in by_daysidemin:
        by_daysidemin[k].sort()

    rows, kept = [], []
    for t in trades:
        day, side = t["day"], t["side"]
        em = _exit_minute(t["exit_et"])
        mins = by_daysidemin.get((day, side))
        if not mins:
            continue
        prior = [x for x in mins if x <= em]
        snap = prior[-1] if prior else mins[0]      # floor stride bar <= exit
        base = m.loc[(day, snap, side)]
        row = {f: base[f] for f in nonstrike}
        row["D"] = float(t["D_exit"])
        row["pmkt"] = float(t["pmkt_exit"])
        rows.append(row)
        kept.append(t)
    X = pd.DataFrame(rows)[feats]
    return X, kept


def _fmt(x):
    return f"${x:+,.0f}"


def main():
    master = load_master()
    split = make_split(sorted(master["day"].unique()))
    train_days = set(split.train_days)

    # frozen v1 model (trained on TRAIN, frozen params) - read once
    import json
    cfg = json.load(open(os.path.join(os.path.dirname(__file__), "..",
                                      "models", "gbt_params.json")))["with_pmkt"]
    booster, fl = gbt.train(master[master["day"].isin(split.train_days)],
                            params=cfg["params"], num_rounds=cfg["num_rounds"],
                            include_pmkt=True)

    trades = load_early_exits()
    X, kept = build_features(trades, master)
    p_model = gbt.predict(booster, X, fl)

    # per-trade frame
    df = pd.DataFrame({
        "day": [t["day"] for t in kept],
        "side": [t["side"] for t in kept],
        "reason": [t["reason"] for t in kept],
        "D_exit": [float(t["D_exit"]) for t in kept],
        "pmkt": [float(t["pmkt_exit"]) for t in kept],
        "p_model": p_model,
        "actual": [float(t["actual_pnl"]) for t in kept],
        "held": [float(t["held_pnl"]) for t in kept],
        "settle_beyond": [int(t["settle_beyond"]) for t in kept],
    })
    df["era"] = np.where(df["day"].isin(train_days), "TRAIN(in-sample)",
                         "HELD-OUT(calib+valid)")
    df["delta_if_override"] = df["held"] - df["actual"]

    def report_rule(mask, name):
        ov = df[mask]
        delta = ov["delta_if_override"].sum()
        n = len(ov)
        per = delta / n if n else 0.0
        return name, n, delta, per

    print("=" * 78)
    print("FALSE-BREAKOUT OVERLAY — model-gated holds at actual early exits")
    print(f"(pre-TEST_START only; frozen v1 29-feature model; {len(df)} early "
          f"exits)")
    print("=" * 78)

    # ---- headline parameter-free rule: hold iff p_model > p_mkt ----
    print("\n--- HEADLINE rule: hold iff p_model > p_mkt (parameter-free) ---")
    print(f"{'era':>22} {'#exits':>7} {'#override':>10} {'netΔ $':>12} "
          f"{'$/override':>12}")
    for era in ("TRAIN(in-sample)", "HELD-OUT(calib+valid)"):
        e = df[df["era"] == era]
        ov = e[e["p_model"] > e["pmkt"]]
        per = (ov["delta_if_override"].sum() / len(ov)) if len(ov) else 0.0
        print(f"{era:>22} {len(e):7d} {len(ov):10d} "
              f"{_fmt(ov['delta_if_override'].sum()):>12} {_fmt(per):>12}")
    ovall = df[df["p_model"] > df["pmkt"]]
    print(f"{'ALL':>22} {len(df):7d} {len(ovall):10d} "
          f"{_fmt(ovall['delta_if_override'].sum()):>12} "
          f"{_fmt(ovall['delta_if_override'].sum()/max(len(ovall),1)):>12}")

    # ---- override ledger (headline rule, all eras + held-out) ----
    for scope, d in (("ALL", df), ("HELD-OUT only", df[df["era"].str.startswith("HELD")])):
        ov = d[d["p_model"] > d["pmkt"]]
        saved = ov[ov["settle_beyond"] == 1]
        back = ov[ov["settle_beyond"] == 0]
        print(f"\n--- override ledger [{scope}] (hold iff p_model > p_mkt) ---")
        print(f"  overrides: {len(ov)}   saved (held→safe): {len(saved)} "
              f"({_fmt(saved['delta_if_override'].sum())})   "
              f"backfired (held→ITM): {len(back)} "
              f"({_fmt(back['delta_if_override'].sum())})")
        if len(back):
            sv = saved["delta_if_override"].sum()
            ratio = abs(back["delta_if_override"].sum()) / sv if sv > 0 else float("inf")
            print(f"  worst single backfire: "
                  f"{_fmt(back['delta_if_override'].min())}   "
                  f"backfired$ / saved$ = {ratio:.2f}")

    # ---- regime reality check: where do exits fire in D? ----
    print("\n--- regime reality check: D at exit (validated near-strike = D<=0.5) ---")
    for lab, seg in (("D<=0.5 (near-strike, validated)", df[df["D_exit"] <= 0.5]),
                     ("D>0.5 (outside validated edge)", df[df["D_exit"] > 0.5])):
        ov = seg[seg["p_model"] > seg["pmkt"]]
        print(f"  {lab:36s} #exits={len(seg):3d}  #override={len(ov):3d}  "
              f"netΔ={_fmt(ov['delta_if_override'].sum())}")
    # held-out near-strike specifically
    ho = df[df["era"].str.startswith("HELD")]
    near_ho = ho[ho["D_exit"] <= 0.5]
    ov = near_ho[near_ho["p_model"] > near_ho["pmkt"]]
    print(f"  [HELD-OUT ∩ D<=0.5]                  #exits={len(near_ho):3d}  "
          f"#override={len(ov):3d}  netΔ={_fmt(ov['delta_if_override'].sum())}")

    # ---- attribution: does the MODEL add value vs naively always-holding? ----
    # task 1.6 already showed the trader's early exits are mostly unnecessary
    # ($ recovered by holding). So a hold-biased signal makes money regardless
    # of any edge. Compare model-gated vs always-hold, and inspect what the
    # model DECLINED (p_model <= p_mkt) - if its declines didn't avoid
    # backfires, the model is just a noisy hold proxy, not discrimination.
    print("\n--- attribution: model-gated vs always-hold (HELD-OUT) ---")
    ho = df[df["era"].str.startswith("HELD")]
    always = ho["delta_if_override"].sum()
    gated = ho[ho["p_model"] > ho["pmkt"]]["delta_if_override"].sum()
    decl = ho[ho["p_model"] <= ho["pmkt"]]
    print(f"  always-hold ALL {len(ho)} early exits: netΔ {_fmt(always)}")
    print(f"  model-gated (hold {int((ho['p_model']>ho['pmkt']).sum())}): "
          f"netΔ {_fmt(gated)}")
    print(f"  -> model's selectivity adds {_fmt(gated - always)} vs always-hold")
    if len(decl):
        safe = (decl["settle_beyond"] == 1).mean()
        print(f"  declined (p_model<=p_mkt): {len(decl)}  would-settle-safe "
              f"{safe:.0%}  (their hold-Δ left on table {_fmt(decl['delta_if_override'].sum())})")

    # ---- sensitivity sweep (shape only; NOT for picking a winner) ----
    print("\n--- sensitivity (shape only; verdict is on the parameter-free rule) ---")
    print(f"{'rule':>24} {'#override':>10} {'netΔ(ALL)':>12} {'netΔ(HELD-OUT)':>15}")
    rules = [("p_model > p_mkt+0.05", df["p_model"] > df["pmkt"] + 0.05),
             ("p_model >= 0.70", df["p_model"] >= 0.70),
             ("p_model >= 0.80", df["p_model"] >= 0.80),
             ("p_model >= 0.90", df["p_model"] >= 0.90)]
    for name, mask in rules:
        ov = df[mask]
        ovh = df[mask & df["era"].str.startswith("HELD")]
        print(f"{name:>24} {len(ov):10d} "
              f"{_fmt(ov['delta_if_override'].sum()):>12} "
              f"{_fmt(ovh['delta_if_override'].sum()):>15}")

    df.to_csv(os.path.join(os.path.dirname(__file__),
                           "model_exit_overlay.csv"), index=False)


if __name__ == "__main__":
    main()
