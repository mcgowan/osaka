"""Residual analysis (plan task 4.8): where does the edge p_model - p_mkt
concentrate, and is it signal or noise?

Evaluated on TRAIN walk-forward OOF (review item 1: VALID is one-shot, read
once at Gate 4). The edge is judged in the band of record and the NFR-2.1b
slices, never pooled (pmkt-as-input can collapse the model onto the baseline
and pooled Brier hides it). Decomposes the band and near-strike skill by
time-of-day and VIX1D regime to see whether the edge is a broad property or a
thin pocket. Run for the written 4.8 record.

Run:  .venv/bin/python analysis/residual_analysis.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.features import load_master  # noqa: E402
from eval import metrics  # noqa: E402
from models import gbt  # noqa: E402
from models.hpsearch import PARAMS_PATH  # noqa: E402
from models.oof import walk_forward_oof  # noqa: E402
from models.splits import make_split  # noqa: E402
from quant.conventions import BAND_OF_RECORD, regime  # noqa: E402

TOD_BINS = [(0, 30, "open 0-30m"), (30, 120, "30-120m"),
            (120, 300, "120-300m"), (300, 391, "300m-close")]


def _skill(df):
    if len(df) < 200:
        return None
    return metrics.day_bootstrap_brier_skill(df, "pred", n_boot=1000)


def _line(label, df):
    s = _skill(df)
    if s is None:
        print(f"    {label:18s} n={len(df):6d}  (thin)")
        return
    print(f"    {label:18s} n={len(df):6d} n_d={s['n_days']:3d}  "
          f"skill {s['point']:+.5f}  P>0={s['p_gt_0']:.3f}")


def main():
    master = load_master()
    split = make_split(sorted(master["day"].unique()))
    c = json.load(open(PARAMS_PATH))["with_pmkt"]

    feats = gbt.feature_list(True)

    def fp(fit, val):
        b, _ = gbt.train(fit, params=c["params"], num_rounds=c["num_rounds"],
                         include_pmkt=True)
        return gbt.predict(b, val, feats)

    oof = walk_forward_oof(master, split, fp, label="pred")
    oof["resid"] = oof["pred"] - oof["pmkt"]
    oof["reg"] = oof["vix1d_anchor"].map(regime)
    band = oof[oof["bucket"] == BAND_OF_RECORD]
    near = oof[metrics.near_strike(oof).values]

    print("=== mean residual p_model - p_mkt (OOF) ===")
    print(f"  pooled {oof['resid'].mean():+.4f}   band {band['resid'].mean():+.4f}"
          f"   near-strike {near['resid'].mean():+.4f}")

    print("\n=== BAND-OF-RECORD skill by time-of-day (OOF) ===")
    for lo, hi, lab in TOD_BINS:
        _line(lab, band[(band["minutes_since_open"] >= lo)
                        & (band["minutes_since_open"] < hi)])
    print("=== BAND-OF-RECORD skill by VIX1D regime (OOF) ===")
    for r in ("calm", "mid", "elevated"):
        _line(r, band[band["reg"] == r])

    print("\n=== NEAR-STRIKE skill by time-of-day (OOF) ===")
    for lo, hi, lab in TOD_BINS:
        _line(lab, near[(near["minutes_since_open"] >= lo)
                        & (near["minutes_since_open"] < hi)])
    print("=== NEAR-STRIKE skill by VIX1D regime (OOF) ===")
    for r in ("calm", "mid", "elevated"):
        _line(r, near[near["reg"] == r])


if __name__ == "__main__":
    main()
