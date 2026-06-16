"""v2 — walk-forward model factory: 7 expanding-window quarterly versions for
the eleuthera backtest / forward deployment.

Each version `V` is trained on ALL data from 2008 through the END of its cutoff
month, calibrated on the most recent ~3 months (held out, with a feature-lookback
embargo), and deployed in the backtest for the FOLLOWING quarter (a model never
scores a bar it trained on). Trained on SPX itself (the traded instrument;
osaka's SPX intraday reaches back to 2004, deeper than SPY) so there is NO
SPY->SPX transfer gap - eleuthera computes the same 20 SPX-PRICE features from its
own bars. SPX has no volume, so the 4 volume features are absent (not in the
20-feature production set); VIX1D is dropped too.

Each version is exported BOTH as a Python bundle (booster.txt) and a
self-contained JSON artifact (trees + isotonic + threshold + ordered features +
init offset + golden vectors) that a pure-JS evaluator reproduces bit-exactly.

Run:  .venv/bin/python models/walk_forward_train.py
Out:  data/processed/breakout-models-v2/<label>.json  (+ booster.txt)
"""

import json
import math
import os
import sys

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakout_features import build, FEATURES, EMBARGO_DAYS  # noqa: E402
from data.loader import OUT_DIR  # noqa: E402
from models.breakout_gbt import predict, train  # noqa: E402

DROP = {"rel_vol_tod", "vol_surge", "vwap_dist_atr", "vol_trend",
        "vix1d_anchor", "vix1d_chg"}
PROD_FEATURES = [f for f in FEATURES if f not in DROP]      # 20 SPX-price features
CALIB_MONTHS = 3
OUT = os.path.join(OUT_DIR, "breakout-models-v2")

# (label, cutoff = last training day) — deployed the following quarter.
CUTOFFS = [("sep-2024", "2024-09-30"), ("dec-2024", "2024-12-31"),
           ("mar-2025", "2025-03-31"), ("jun-2025", "2025-06-30"),
           ("sep-2025", "2025-09-30"), ("dec-2025", "2025-12-31"),
           ("mar-2026", "2026-03-31")]


def _leaf(node, x):
    while "leaf_value" not in node:
        v = x[node["split_feature"]]
        left = node["default_left"] if (v is None or v != v) else (v <= node["threshold"])
        node = node["left_child"] if left else node["right_child"]
    return node["leaf_value"]


def _margin(trees, x):
    return sum(_leaf(t["tree_structure"], x) for t in trees)


def build_version(master, label, cutoff):
    w = master[master["day"] <= cutoff]
    days = sorted(w["day"].unique())
    calib_start = (pd.Timestamp(cutoff) - pd.DateOffset(months=CALIB_MONTHS)).strftime("%Y-%m-%d")
    calib_days = [d for d in days if d > calib_start]
    pre = [d for d in days if d <= calib_start]
    booster_days = pre[:-EMBARGO_DAYS]                      # feature-lookback embargo
    tr = w[w["day"].isin(booster_days)]
    ca = w[w["day"].isin(calib_days)].copy()

    booster, feats = train(tr, features=PROD_FEATURES)
    ca["raw"] = predict(booster, ca, feats)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(ca["raw"].to_numpy(), ca["reversed"].to_numpy())
    ca["cal"] = iso.predict(ca["raw"].to_numpy())
    cat = ca[ca["tradeable"] == 1]
    gf = float(cat["confirmed_5bar"].mean())
    thr = float(np.quantile(cat["cal"], gf))

    # JS-evaluator artifact + golden-vector self-check (pure-python tree eval ==
    # booster.predict, so the JS port has an exact reference).
    dm = booster.dump_model()
    trees = dm["tree_info"]
    Xc = ca[feats].to_numpy()
    raw_margin = booster.predict(ca[feats], raw_score=True)
    offset = float(np.mean(raw_margin - np.array([_margin(trees, Xc[i]) for i in range(len(Xc))])))
    chk = np.array([1 / (1 + math.exp(-(_margin(trees, Xc[i]) + offset))) for i in range(min(50, len(Xc)))])
    assert np.allclose(chk, ca["raw"].to_numpy()[:len(chk)], atol=1e-9), f"{label}: tree-eval mismatch"

    gi = np.linspace(0, len(Xc) - 1, 5).astype(int)
    golden = [{"x": {f: (None if Xc[i][j] != Xc[i][j] else float(Xc[i][j]))
                     for j, f in enumerate(feats)},
               "raw": float(ca["raw"].to_numpy()[i]),
               "prob": float(ca["cal"].to_numpy()[i])} for i in gi]

    os.makedirs(OUT, exist_ok=True)
    booster.save_model(os.path.join(OUT, f"{label}.booster.txt"))
    json.dump({
        "version": label, "trained_through": cutoff,
        "features": feats, "init_offset": offset,
        "trees": [t["tree_structure"] for t in trees],
        "isotonic": {"x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()},
        "operating_threshold": thr, "calib_greenlight_fraction": gf,
        "n_booster_days": len(booster_days), "n_calib_days": len(calib_days),
        "golden": golden,
    }, open(os.path.join(OUT, f"{label}.json"), "w"))
    return dict(label=label, train=f"{booster_days[0]}..{booster_days[-1]}",
                bdays=len(booster_days), cdays=len(calib_days), thr=thr, gf=gf)


def main():
    master = build(start="2008-01-01", end="2026-03-31", _unlocked_full_span=True,
                   symbol="SPX")           # SPX = the traded instrument; no transfer gap
    print(f"master {len(master):,} events (SPX); {len(PROD_FEATURES)} prod features\n")
    print(f"{'version':9} {'booster train span':26} {'bdays':>6} {'cdays':>6} "
          f"{'thr':>6} {'gf':>6}")
    for label, cutoff in CUTOFFS:
        r = build_version(master, label, cutoff)
        print(f"{r['label']:9} {r['train']:26} {r['bdays']:>6} {r['cdays']:>6} "
              f"{r['thr']:>6.3f} {r['gf']:>6.0%}")
    print(f"\n7 versions -> {OUT}/  (<label>.json for JS, <label>.booster.txt for Python)")


if __name__ == "__main__":
    main()
