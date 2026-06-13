"""Walk-forward hyperparameter search (plan task 4.4; NFR-2.3).

Selection happens ENTIRELY within TRAIN, on the expanding-window walk-forward
folds from models.splits.walk_forward_folds. CALIB and VALID are never read
here - touching them would contaminate selection (NFR-2.4 / Gate-4 honesty).

Effective N is trading DAYS (~450 in TRAIN), not the ~300k rows, so the grid
is small and the priors are heavily regularized (large min_data_in_leaf,
shallow leaves, L2). We are choosing among a handful of regularized configs,
not fine-tuning - a big grid would just overfit the fold noise.

Selection metric: pooled out-of-fold Brier in the BAND OF RECORD (the project
objective). Each fold's val block is distinct, so pooling the per-fold val
predictions gives one honest out-of-sample prediction per TRAIN val-week. We
fix num_rounds per config (no early stopping) so the stopping set and the
scoring set never overlap - the fold-val Brier is a clean ranking signal.

Both model variants are tuned independently: the with-pmkt model and the
FIRST-CLASS without-pmkt variant (trader condition #25) may prefer different
regularization. Selected params + round count are frozen to gbt_params.json
for the calibration / ablation / residual steps to load deterministically.

Run:  .venv/bin/python models/hpsearch.py   (writes models/gbt_params.json)
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
from models.splits import make_split, walk_forward_folds  # noqa: E402
from quant.conventions import BAND_OF_RECORD  # noqa: E402

PARAMS_PATH = os.path.join(os.path.dirname(__file__), "gbt_params.json")

# Heavy-regularization grid (effective N = days). Each entry = LightGBM params
# overrides + a fixed num_rounds. lambda/feature/bagging fractions stay at the
# regularized DEFAULT_PARAMS unless overridden here.
GRID = [
    {"num_leaves": nl, "min_data_in_leaf": md, "learning_rate": lr,
     "num_rounds": nr}
    for nl in (15, 31, 63)
    for md in (1000, 3000)
    for lr in (0.03,)
    for nr in (400, 800)
]


def _split_params(cfg):
    cfg = dict(cfg)
    num_rounds = cfg.pop("num_rounds")
    return cfg, num_rounds


def search_variant(master, folds, include_pmkt):
    """Return a list of result dicts (one per grid config), sorted best-first
    by pooled out-of-fold band-of-record Brier."""
    # cache fold frames once
    cached = [(master[master["day"].isin(f)], master[master["day"].isin(v)])
              for f, v in folds]
    results = []
    for cfg in GRID:
        params, num_rounds = _split_params(cfg)
        oof = []                      # (val_df_with_pred) per fold
        for fit_df, val_df in cached:
            booster, feats = gbt.train(fit_df, params=params,
                                       num_rounds=num_rounds,
                                       include_pmkt=include_pmkt)
            vp = val_df.copy()
            vp["pred"] = gbt.predict(booster, vp, feats)
            oof.append(vp)
        pool = pd.concat(oof, ignore_index=True)
        band = pool[pool["bucket"] == BAND_OF_RECORD]
        results.append({
            **cfg,
            "band_brier": round(metrics.brier(band["survive"], band["pred"]), 5),
            "band_brier_pmkt": round(
                metrics.brier(band["survive"], band["pmkt"]), 5),
            "band_skill": round(
                metrics.brier(band["survive"], band["pmkt"])
                - metrics.brier(band["survive"], band["pred"]), 5),
            "pooled_brier": round(metrics.brier(pool["survive"], pool["pred"]), 5),
            "band_n": len(band),
        })
    results.sort(key=lambda r: r["band_brier"])
    return results


def main():
    master = load_master()
    split = make_split(sorted(master["day"].unique()))
    folds = walk_forward_folds(sorted(split.train_days))
    print(f"{split}\n{len(folds)} walk-forward folds, "
          f"{len(GRID)} configs/variant\n")

    out = {}
    for include_pmkt in (True, False):
        variant = "with_pmkt" if include_pmkt else "without_pmkt"
        res = search_variant(master, folds, include_pmkt)
        print(f"=== {variant} (out-of-fold band-of-record Brier) ===")
        with pd.option_context("display.width", 160, "display.max_columns", None):
            print(pd.DataFrame(res).to_string(index=False))
        best = res[0]
        params, num_rounds = _split_params(best)
        out[variant] = {
            "params": params,
            "num_rounds": num_rounds,
            "cv_band_brier": best["band_brier"],
            "cv_band_skill": best["band_skill"],
        }
        print(f"-> best: {params}, num_rounds={num_rounds}, "
              f"cv band Brier {best['band_brier']} "
              f"(skill vs p_mkt {best['band_skill']:+.5f})\n")

    out["_meta"] = {
        "selection": "pooled out-of-fold band-of-record Brier, walk-forward "
                     "folds within TRAIN only (NFR-2.3)",
        "band_of_record": BAND_OF_RECORD,
        "n_folds": len(folds),
        "grid_size": len(GRID),
    }
    with open(PARAMS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"frozen tuned params -> {PARAMS_PATH}")


if __name__ == "__main__":
    main()
