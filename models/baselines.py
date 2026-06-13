"""Baselines (plan task 4.2): the floor and the bar the GBT must clear.

  (a) base_rate  - constant predictor = TRAIN survival rate. The trivial
                   floor; Brier here is the variance of the label.
  (b) p_mkt      - the frozen analytic baseline (the `pmkt` column). THE BAR.
                   The whole project is "beat (b) in the band of record".
  (c) logit5     - logistic regression on 5 cheap features. A structured
                   floor: shows what a linear model with no path/calendar
                   richness already extracts, so the GBT's lift is measured
                   against something that already knows distance + vol + T.

All three are FIT on TRAIN only and reported on VALID (Gate-4 region), via
the shared eval harness, per distance bucket + NFR-2.1b slice. Deterministic
(fixed seed, no row-level anything; split is week-based per models.splits).

Run:  .venv/bin/python models/baselines.py
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.features import load_master  # noqa: E402
from eval import metrics  # noqa: E402
from models.splits import make_split  # noqa: E402

# (c) logit5: distance + side + time-to-settle + vol level + a realized-vol
# read. rv_ratio_today is NaN in the first ~minutes (range undefined) - the
# pipeline imputes the TRAIN median (documented; logit needs complete rows,
# the GBT will instead see NaN natively).
LOGIT5_FEATURES = ("D", "side_c", "minutes_to_settle",
                   "vix1d_anchor", "rv_ratio_today")
SEED = 0


def fit_logit5(train, features=LOGIT5_FEATURES):
    import warnings
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.impute import SimpleImputer
    pipe = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("lr", LogisticRegression(max_iter=1000, C=1.0, random_state=SEED)),
    ])
    # benign BLAS overflow warnings during LBFGS line search on this platform;
    # predictions verified finite (no inf/nan in the 5 inputs)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        pipe.fit(train[list(features)].values, train["survive"].values)
    return pipe


def predict_all(split, master):
    """Attach base_rate / logit5 prediction columns to the VALID frame and
    return it (pmkt is already a column). Fits everything on TRAIN."""
    train = master[master["day"].isin(split.train_days)].copy()
    valid = master[master["day"].isin(split.valid_days)].copy()

    base = float(train["survive"].mean())
    valid["base_rate"] = base

    import warnings
    logit = fit_logit5(train)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        valid["logit5"] = logit.predict_proba(
            valid[list(LOGIT5_FEATURES)].values)[:, 1]
    return valid, {"train_base_rate": round(base, 4)}


def report(valid, pred_cols=("base_rate", "pmkt", "logit5")):
    print("\n" + "=" * 72)
    print("BASELINES on VALID  (n_days=%d, n_rows=%d, base_rate=%.4f)"
          % (valid["day"].nunique(), len(valid), valid["survive"].mean()))
    print("=" * 72)
    for col in pred_cols:
        print(f"\n--- [{col}] per distance bucket "
              f"(band of record = {metrics.BAND_OF_RECORD}) ---")
        with pd.option_context("display.width", 160,
                               "display.max_columns", None):
            print(metrics.bucket_report(valid, col).to_string(index=False))
    # band-of-record edge vs p_mkt: bootstrap by day for the GBT's eventual bar
    band = valid[valid["bucket"] == metrics.BAND_OF_RECORD]
    print("\n--- band-of-record Brier skill vs p_mkt (day-clustered "
          "bootstrap) ---")
    for col in pred_cols:
        if col == "pmkt":
            continue
        bs = metrics.day_bootstrap_brier_skill(band, col)
        print(f"  {col:>10}: skill {bs['point']:+.5f}  "
              f"95% CI [{bs['lo95']:+.5f}, {bs['hi95']:+.5f}]  "
              f"P(skill>0)={bs['p_gt_0']:.3f}  (n_days={bs['n_days']})")
    print("\n--- [logit5] NFR-2.1b slices (each slice's full population) ---")
    with pd.option_context("display.width", 160, "display.max_columns", None):
        print(metrics.slice_report(valid, "logit5").to_string(index=False))


def main():
    master = load_master()
    days = sorted(master["day"].unique())
    split = make_split(days)
    print(split)
    valid, info = predict_all(split, master)
    print(info)
    report(valid)


if __name__ == "__main__":
    main()
