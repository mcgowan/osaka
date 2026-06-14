"""Ablations + feature-importance review (plan task 4.6).

Everything is evaluated on TRAIN walk-forward OOF (review item 1: VALID is
one-shot). Each ablation uses the SAME frozen heavy-reg hyperparameters
(isolates the feature/architecture effect, not a re-tune). Decision-relevant
readouts: band-of-record OOF Brier skill vs p_mkt (day-clustered) and the
near-strike + OR-retest slice skills (the use-case populations).

Ablations:
  full          - 29 features, side indicator (reference)
  no_pmkt       - drop pmkt (the first-class without-pmkt variant)
  no_priorday   - drop the whole prior-day block (incl. dist_pdh/pdl)
  no_pd_levels  - drop only dist_pdh/dist_pdl (trader: "I don't use prior-day
                  levels" - is the magnet/barrier feature dead weight?)
  two_head      - separate put/call models, side_c dropped (FR-6.3; subsumes
                  the threat-frame sign question per spec resolution #1 -
                  per-side models encode the threat direction implicitly)

Plus gain-importance from the full model to spot dead weight for pruning back
toward the 20-30 budget.

Run:  .venv/bin/python models/ablations.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.features import load_master  # noqa: E402
from eval import metrics  # noqa: E402
from eval.slices import attach_or_retest  # noqa: E402
from models import gbt  # noqa: E402
from models.hpsearch import PARAMS_PATH  # noqa: E402
from models.oof import walk_forward_oof  # noqa: E402
from models.splits import make_split  # noqa: E402
from quant.conventions import BAND_OF_RECORD  # noqa: E402

PRIORDAY = ("yest_close_pos", "open_vs_yest_range", "mom3d_atr",
            "yest_range_vs_implied", "dist_pdh", "dist_pdl")


def _params():
    c = json.load(open(PARAMS_PATH))["with_pmkt"]
    return c["params"], c["num_rounds"]


def _drop_fp(drop, include_pmkt):
    params, num_rounds = _params()
    feats = [f for f in gbt.feature_list(include_pmkt) if f not in drop]

    def fp(fit, val):
        b, _ = gbt.train(fit, params=params, num_rounds=num_rounds,
                         features=feats)
        return gbt.predict(b, val, feats)
    return fp, len(feats)


def _two_head_fp():
    params, num_rounds = _params()
    feats = [f for f in gbt.feature_list(True) if f != "side_c"]

    def fp(fit, val):
        out = np.empty(len(val), float)
        vside = val["side"].to_numpy()
        for side in ("p", "c"):
            vm = vside == side
            if not vm.any():
                continue
            b, _ = gbt.train(fit[fit["side"] == side], params=params,
                             num_rounds=num_rounds, features=feats)
            out[vm] = gbt.predict(b, val[vm], feats)
        return out
    return fp, len(feats)


ABLATIONS = {
    "full":         lambda: _drop_fp((), True),
    "no_pmkt":      lambda: _drop_fp((), False),
    "no_priorday":  lambda: _drop_fp(PRIORDAY, True),
    "no_pd_levels": lambda: _drop_fp(("dist_pdh", "dist_pdl"), True),
    "two_head":     _two_head_fp,
}


def _skill(df, label):
    return metrics.day_bootstrap_brier_skill(df, label, n_boot=1000)


def main():
    master = load_master()
    split = make_split(sorted(master["day"].unique()))
    print(f"{split}\n(all metrics = TRAIN walk-forward OOF vs p_mkt; "
          f"VALID untouched)\n")

    # or_retest computed once on the (fold-stable) OOF row set, mapped to each
    or_map = None
    rows = []
    for name, make in ABLATIONS.items():
        fp, nfeat = make()
        oof = walk_forward_oof(master, split, fp, label="pred")
        if or_map is None:
            oof = attach_or_retest(oof)
            or_map = dict(zip(zip(oof["day"], oof["minute"]), oof["or_retest"]))
        else:
            oof["or_retest"] = [or_map[(d, m)]
                                for d, m in zip(oof["day"], oof["minute"])]
        band = oof[oof["bucket"] == BAND_OF_RECORD]
        near = oof[metrics.near_strike(oof).values]
        retest = oof[metrics.breakout_retest(oof).values]
        b, n, r = _skill(band, "pred"), _skill(near, "pred"), _skill(retest, "pred")
        rows.append({
            "ablation": name, "n_feat": nfeat,
            "band_skill": b["point"], "band_P>0": b["p_gt_0"],
            "near_skill": n["point"], "near_P>0": n["p_gt_0"],
            "retest_skill": r["point"], "retest_P>0": r["p_gt_0"],
            "pooled_brier": round(metrics.brier(oof["survive"], oof["pred"]), 5),
        })
        print(f"  {name:13s} done ({nfeat} feat)")

    print("\n=== ablations (TRAIN OOF) ===")
    with pd.option_context("display.width", 200, "display.max_columns", None):
        print(pd.DataFrame(rows).to_string(index=False))

    # feature importance (gain) from the full model on TRAIN
    params, num_rounds = _params()
    train = master[master["day"].isin(split.train_days)]
    booster, feats = gbt.train(train, params=params, num_rounds=num_rounds,
                               include_pmkt=True)
    imp = pd.DataFrame({
        "feature": feats,
        "gain": booster.feature_importance(importance_type="gain"),
    }).sort_values("gain", ascending=False)
    imp["gain_pct"] = (100 * imp["gain"] / imp["gain"].sum()).round(2)
    print("\n=== full-model gain importance (TRAIN) ===")
    with pd.option_context("display.max_rows", None):
        print(imp[["feature", "gain_pct"]].to_string(index=False))


if __name__ == "__main__":
    main()
