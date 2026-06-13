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


def _logit_predict(model, df):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return model.predict_proba(df[list(LOGIT5_FEATURES)].values)[:, 1]


def predict_all_oof(split, master):
    """Development evaluation: base_rate + logit5 as TRAIN walk-forward OOF
    predictions (pmkt is already a per-row column). One pass over the folds so
    all three live on the same rows. Never touches VALID (review item 1)."""
    from models.splits import walk_forward_folds
    parts = []
    for fit_days, val_days in walk_forward_folds(sorted(split.train_days)):
        fit = master[master["day"].isin(fit_days)]
        val = master[master["day"].isin(val_days)].copy()
        val["base_rate"] = float(fit["survive"].mean())
        val["logit5"] = _logit_predict(fit_logit5(fit), val)
        parts.append(val)
    return pd.concat(parts, ignore_index=True)


def predict_all_gate4(split, master, _gate4_reason=None):
    """GATE-4 ONLY: the baselines on the frozen VALID region (the one-shot
    Gate-4 comparison). Reads VALID through the one-shot gate."""
    from models.splits import gate4_valid_days
    valid = master[master["day"].isin(
        gate4_valid_days(split, _gate4_reason=_gate4_reason))].copy()
    train = master[master["day"].isin(split.train_days)]
    valid["base_rate"] = float(train["survive"].mean())
    valid["logit5"] = _logit_predict(fit_logit5(train), valid)
    return valid


def report(valid, pred_cols=("base_rate", "pmkt", "logit5"), where="OOF"):
    print("\n" + "=" * 72)
    print("BASELINES on %s  (n_days=%d, n_rows=%d, base_rate=%.4f)"
          % (where, valid["day"].nunique(), len(valid), valid["survive"].mean()))
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
    print(f"{split}\n(evaluation = TRAIN walk-forward OOF; VALID reserved for "
          f"Gate 4)")
    oof = predict_all_oof(split, master)
    report(oof, where="TRAIN-OOF")


if __name__ == "__main__":
    main()
