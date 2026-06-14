"""Post-hoc probability calibration (plan task 4.5; FR-6.2 / NFR-2.4).

The calibrator is fit on the CALIB region ONLY - a fold never used for model
selection (hyperparameter search lives entirely in TRAIN, task 4.4). This is
the whole reason CALIB exists as a separate chronological block: fitting the
calibrator on data that also drove model choice would flatter the calibration
curve at Gate 4.

Pipeline per variant (with_pmkt / without_pmkt, using the params frozen by
hpsearch in gbt_params.json):
  1. train the GBT on TRAIN
  2. predict CALIB -> fit the calibrator (raw p -> calibrated p)
  3. predict VALID -> apply the calibrator -> report calibration before/after

ISOTONIC by default (free-form monotone fit; we have enough CALIB rows).
Platt (sigmoid) available as a 2-parameter fallback for thin slices. Both are
MONOTONE maps of the probability, so they preserve the monotone-in-distance
constraint (FR-6.2): if raw p is non-decreasing in D, so is calibrated p -
task 4.7 stays valid after calibration.

Run:  .venv/bin/python models/calibrate.py   (after hpsearch)
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
from models.splits import make_split  # noqa: E402
from quant.conventions import BAND_OF_RECORD, BUCKET_LABELS  # noqa: E402


def load_tuned(variant):
    """Return (params, num_rounds) frozen by hpsearch for a variant."""
    if not os.path.exists(PARAMS_PATH):
        raise FileNotFoundError(
            f"{PARAMS_PATH} missing - run models/hpsearch.py (task 4.4) first")
    cfg = json.load(open(PARAMS_PATH))[variant]
    return cfg["params"], cfg["num_rounds"]


def fit_calibrator(raw_p, y, method="isotonic"):
    """Fit a monotone calibrator mapping raw model prob -> calibrated prob.
    Fit on CALIB only. Returns a callable p -> p_calibrated."""
    raw_p = np.asarray(raw_p, float)
    y = np.asarray(y, float)
    if method == "isotonic":
        from sklearn.isotonic import IsotonicRegression
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(raw_p, y)
        return lambda p: iso.predict(np.asarray(p, float))
    if method == "platt":
        from sklearn.linear_model import LogisticRegression
        eps = 1e-6
        z = np.log(np.clip(raw_p, eps, 1 - eps)
                   / (1 - np.clip(raw_p, eps, 1 - eps)))  # logit
        lr = LogisticRegression(C=1e6, solver="lbfgs")
        lr.fit(z.reshape(-1, 1), y)
        return lambda p: lr.predict_proba(
            np.log(np.clip(np.asarray(p, float), eps, 1 - eps)
                   / (1 - np.clip(np.asarray(p, float), eps, 1 - eps)))
            .reshape(-1, 1))[:, 1]
    raise ValueError(method)


def build_calibrated(split, master, variant="with_pmkt", method="isotonic",
                     _gate4_reason=None):
    """GATE-4 ONLY: train on TRAIN, fit calibrator on CALIB, apply to VALID.
    The VALID assessment is one-shot (reads VALID through the gate); fitting
    the calibrator on CALIB is always allowed. Returns (valid_df with
    raw+calibrated pred cols, booster, calibrator, (raw_col, cal_col))."""
    from models.splits import gate4_valid_days
    valid_days = gate4_valid_days(split, _gate4_reason=_gate4_reason)
    include_pmkt = variant == "with_pmkt"
    params, num_rounds = load_tuned(variant)
    train = master[master["day"].isin(split.train_days)]
    calib = master[master["day"].isin(split.calib_days)].copy()
    valid = master[master["day"].isin(valid_days)].copy()

    booster, feats = gbt.train(train, params=params, num_rounds=num_rounds,
                               include_pmkt=include_pmkt)
    calib_raw = gbt.predict(booster, calib, feats)
    calibrator = fit_calibrator(calib_raw, calib["survive"].values, method)

    raw_col = f"{variant}_raw"
    cal_col = f"{variant}_cal"
    valid[raw_col] = gbt.predict(booster, valid, feats)
    valid[cal_col] = calibrator(valid[raw_col].values)
    return valid, booster, calibrator, (raw_col, cal_col)


def _per_bucket_cal(valid, col):
    rows = []
    for b in BUCKET_LABELS:
        sub = valid[valid["bucket"] == b]
        if len(sub) == 0:
            continue
        rows.append({"bucket": b, "n": len(sub),
                     "cal_max": round(metrics.calibration_error(
                         sub["survive"], sub[col], kind="max"), 4),
                     "brier": round(metrics.brier(sub["survive"], sub[col]), 5)})
    return pd.DataFrame(rows)


def main():
    master = load_master()
    split = make_split(sorted(master["day"].unique()))
    print(split)
    GATE4 = "Phase-4 Gate-4 calibration assessment (deliberate VALID read)"
    for variant in ("with_pmkt", "without_pmkt"):
        valid, booster, cal, (raw_col, cal_col) = build_calibrated(
            split, master, variant=variant, _gate4_reason=GATE4)
        print(f"\n{'='*72}\n{variant}: calibration on VALID, raw vs isotonic "
              f"(fit on CALIB)\n{'='*72}")
        merged = (_per_bucket_cal(valid, raw_col)
                  .merge(_per_bucket_cal(valid, cal_col),
                         on=["bucket", "n"], suffixes=("_raw", "_cal")))
        with pd.option_context("display.width", 160, "display.max_columns", None):
            print(merged.to_string(index=False))
        band = valid[valid["bucket"] == BAND_OF_RECORD]
        print(f"band of record ({BAND_OF_RECORD}): "
              f"cal_max {metrics.calibration_error(band['survive'], band[raw_col], kind='max'):.4f}"
              f" -> {metrics.calibration_error(band['survive'], band[cal_col], kind='max'):.4f}, "
              f"Brier {metrics.brier(band['survive'], band[raw_col]):.5f}"
              f" -> {metrics.brier(band['survive'], band[cal_col]):.5f} "
              f"(p_mkt {metrics.brier(band['survive'], band['pmkt']):.5f})")


if __name__ == "__main__":
    main()
