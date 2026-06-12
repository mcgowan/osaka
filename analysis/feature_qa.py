#!/usr/bin/env python3
"""Task 3.4: feature QA - distributions, NaN policy audit, correlation
matrix, and the FR-1.3 subsampled-RV decision.

Run:  .venv/bin/python analysis/feature_qa.py
Prints the QA report; the RV decision gets recorded in docs/feature-spec.md.
"""

import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.features import FEATURES, load_master  # noqa: E402
from data.loader import load_bars  # noqa: E402
from quant.conventions import YEAR_SECONDS  # noqa: E402

ANNUALIZE_1MIN = math.sqrt(YEAR_SECONDS / 60.0)
ANNUALIZE_5MIN = math.sqrt(YEAR_SECONDS / 300.0)


def main():
    df = load_master(stride=5)
    print(f"master (pre-boundary): {len(df):,} rows, {df['day'].nunique()} days\n")

    # ---- distributions ----
    print("=== feature distributions ===")
    print(f"{'feature':>22} {'nan%':>6} {'mean':>9} {'std':>8} "
          f"{'p1':>8} {'p50':>8} {'p99':>9} {'flag':>5}")
    for f in FEATURES:
        s = df[f]
        nanpct = s.isna().mean()
        sv = s.dropna()
        flag = ""
        if np.isinf(sv).any():
            flag = "INF!"
        elif sv.std() == 0:
            flag = "CONST"
        print(f"{f:>22} {nanpct:6.1%} {sv.mean():9.3f} {sv.std():8.3f} "
              f"{sv.quantile(.01):8.3f} {sv.quantile(.50):8.3f} "
              f"{sv.quantile(.99):9.3f} {flag:>5}")

    # ---- NaN policy audit (beyond the harness's per-minute assertions) ----
    print("\n=== NaN sources ===")
    for f in FEATURES:
        s = df[f]
        if s.isna().any():
            by_min = df[s.isna()]["minute"]
            print(f"  {f}: {s.isna().sum():,} NaN "
                  f"(minutes {by_min.min()}..{by_min.max()}) - day-open policy")

    # ---- correlation matrix: redundancy candidates ----
    print("\n=== |Spearman rho| > 0.8 pairs (sampled 80k rows) ===")
    num = [f for f in FEATURES if df[f].dtype != object
           and df[f].nunique() > 2]
    sub = df[num].sample(n=80_000, random_state=11)
    corr = sub.corr(method="spearman")
    seen = set()
    for a in num:
        for b in num:
            if a < b and abs(corr.loc[a, b]) > 0.8:
                seen.add((a, b, round(corr.loc[a, b], 3)))
    for a, b, r in sorted(seen, key=lambda x: -abs(x[2])):
        print(f"  {a:>22} ~ {b:<22} rho={r:+.3f}")

    # ---- FR-1.3: subsampled RV variant decision ----
    print("\n=== RV estimator: 1-min vs 5-min-subsampled (60 sampled days) ===")
    spx = load_bars("SPX", start="2023-05-01")
    spx["day"] = spx["ts"].dt.strftime("%Y-%m-%d")
    days = sorted(spx["day"].unique())
    rng = np.random.default_rng(5)
    ratios = []
    for day in rng.choice(days, size=60, replace=False):
        c = spx[spx["day"] == day]["close"].to_numpy()
        if len(c) < 390:
            continue
        rv1 = np.diff(np.log(c)).std(ddof=1) * ANNUALIZE_1MIN
        rv5 = np.diff(np.log(c[::5])).std(ddof=1) * ANNUALIZE_5MIN
        ratios.append(rv1 / rv5)
    ratios = np.array(ratios)
    print(f"  RV_1min / RV_5min: median {np.median(ratios):.3f}, "
          f"IQR [{np.quantile(ratios, .25):.3f}..{np.quantile(ratios, .75):.3f}]")
    if abs(np.median(ratios) - 1) < 0.05:
        print("  -> no systematic inflation (SPX is a calculated index: no "
              "bid-ask bounce). DECISION: keep the 1-min estimator.")
    else:
        print("  -> systematic bias detected; escalate the FR-1.3 decision.")


if __name__ == "__main__":
    main()
