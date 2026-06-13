"""v1 survival GBT (plan task 4.3; FR-6).

LightGBM, binary survival target, with the INVIOLABLE monotone constraint:
survival probability is non-decreasing in normalized distance D (further OTM
=> never lower survival). D is the OTM-signed distance (positive for both
sides), so the constraint is +1 on D and 0 on everything else; verified by
strike sweeps in task 4.7.

Two design rules carried from the Gate-3 review:
  - guardrail #8: features are selected EXPLICITLY from FEATURES, never
    "all columns minus targets" - the master deliberately carries string
    join keys (side, anchor, bucket) a wildcard would leak into training.
  - condition #10/#25: the WITHOUT-pmkt model is a first-class variant
    (build(include_pmkt=False)), carried through residual analysis (4.8).

Side enters as the indicator side_c (FR-6.3, independently queryable);
the two-head variant is an ablation in 4.6. day_of_week is categorical.

Heavy-regularization priors: effective N is ~trading DAYS (~450 in TRAIN),
not the ~300k rows - adjacent samples and the strike grid at one bar share
one forward path. Hyperparameters are searched walk-forward in 4.4; the
DEFAULT_PARAMS here are a sane, regularized starting point so 4.3 "just
trains correctly".

Deterministic: fixed seed, deterministic LightGBM flags.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.features import FEATURES  # noqa: E402

SEED = 0
TARGET = "survive"
CATEGORICAL = ("day_of_week",)

# without-pmkt first-class variant (trader condition #25)
FEATURES_NO_PMKT = tuple(f for f in FEATURES if f != "pmkt")

DEFAULT_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "boosting_type": "gbdt",
    "learning_rate": 0.03,
    "num_leaves": 31,
    "min_data_in_leaf": 2000,    # large: effective N is days, not rows
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l1": 1.0,
    "lambda_l2": 5.0,
    "max_depth": -1,
    "verbosity": -1,
    "seed": SEED,
    "deterministic": True,
    "force_row_wise": True,
}
DEFAULT_NUM_ROUNDS = 600


def feature_list(include_pmkt=True):
    return list(FEATURES if include_pmkt else FEATURES_NO_PMKT)


def monotone_constraints(features):
    """+1 on D (survival non-decreasing in distance), 0 elsewhere."""
    return [1 if f == "D" else 0 for f in features]


def _dataset(df, features, reference=None):
    import lightgbm as lgb
    cats = [f for f in CATEGORICAL if f in features]
    return lgb.Dataset(
        df[features], label=df[TARGET].values,
        categorical_feature=cats, reference=reference,
        free_raw_data=False)


def train(train_df, params=None, num_rounds=DEFAULT_NUM_ROUNDS,
          include_pmkt=True, valid_df=None, early_stopping=None):
    """Train one booster on train_df. If valid_df + early_stopping given,
    stops on validation logloss (used by 4.4). Returns (booster, features)."""
    import lightgbm as lgb
    features = feature_list(include_pmkt)
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    p["monotone_constraints"] = monotone_constraints(features)
    dtrain = _dataset(train_df, features)
    valid_sets, callbacks = None, [lgb.log_evaluation(0)]
    if valid_df is not None:
        dvalid = _dataset(valid_df, features, reference=dtrain)
        valid_sets = [dvalid]
        if early_stopping:
            callbacks.append(lgb.early_stopping(early_stopping, verbose=False))
    booster = lgb.train(p, dtrain, num_boost_round=num_rounds,
                        valid_sets=valid_sets, callbacks=callbacks)
    return booster, features


def predict(booster, df, features):
    return booster.predict(df[features],
                           num_iteration=booster.best_iteration or None)


def build(split, master, include_pmkt=True, params=None,
          num_rounds=DEFAULT_NUM_ROUNDS):
    """Fit on TRAIN, attach predictions to a copy of the VALID frame.
    Returns (valid_with_preds, booster, features). Calibration (4.5) is a
    separate step on the CALIB region."""
    train_df = master[master["day"].isin(split.train_days)]
    valid = master[master["day"].isin(split.valid_days)].copy()
    booster, features = train(train_df, params=params, num_rounds=num_rounds,
                              include_pmkt=include_pmkt)
    col = "gbt" if include_pmkt else "gbt_nopmkt"
    valid[col] = predict(booster, valid, features)
    return valid, booster, features


def main():
    from data.features import load_master
    from models.splits import make_split
    from eval import metrics
    import pandas as pd

    master = load_master()
    split = make_split(sorted(master["day"].unique()))
    print(split)

    for include_pmkt in (True, False):
        valid, booster, feats = build(split, master, include_pmkt=include_pmkt)
        col = "gbt" if include_pmkt else "gbt_nopmkt"
        print(f"\n{'='*72}\nv1 GBT [{col}]  ({len(feats)} features, "
              f"{booster.num_trees()} trees)\n{'='*72}")
        with pd.option_context("display.width", 160, "display.max_columns", None):
            print(metrics.bucket_report(valid, col).to_string(index=False))
        band = valid[valid["bucket"] == metrics.BAND_OF_RECORD]
        bs = metrics.day_bootstrap_brier_skill(band, col)
        print(f"\nband-of-record Brier skill vs p_mkt: {bs['point']:+.5f}  "
              f"95% CI [{bs['lo95']:+.5f}, {bs['hi95']:+.5f}]  "
              f"P(skill>0)={bs['p_gt_0']:.3f}")
        print("NFR-2.1b slices:")
        with pd.option_context("display.width", 160, "display.max_columns", None):
            print(metrics.slice_report(valid, col).to_string(index=False))


if __name__ == "__main__":
    main()
