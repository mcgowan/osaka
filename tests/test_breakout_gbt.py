"""Tests for the v2 GBT model + calibration (Phase 3)."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakout_features import FEATURES, MASTER_MANIFEST, build  # noqa: E402
from models import breakout_gbt as gbt  # noqa: E402


def test_feature_list_matches_spec():
    assert gbt.feature_list() == list(FEATURES)


def test_monotone_constraints_default_unconstrained():
    feats = gbt.feature_list()
    assert gbt.monotone_constraints(feats) == [0] * len(feats)
    # ablation signs map onto the right columns
    mc = gbt.monotone_constraints(feats, {"ext_atr": -1})
    assert mc[feats.index("ext_atr")] == -1 and sum(abs(x) for x in mc) == 1


def test_tuned_defaults_are_frozen():
    # the search-frozen, most-regularized cell (provenance: breakout_hpsearch)
    assert gbt.DEFAULT_PARAMS["num_leaves"] == 7
    assert gbt.DEFAULT_PARAMS["min_data_in_leaf"] == 1600


def test_train_predict_finite():
    df = build(start="2023-06-01", end="2024-03-31")   # small DEV slice
    booster, feats = gbt.train(df, num_rounds=40)
    p = gbt.predict(booster, df, feats)
    assert len(p) == len(df)
    assert np.all(np.isfinite(p)) and p.min() >= 0 and p.max() <= 1


def test_early_stopping_guard_rejects_overlapping_valid():
    df = build(start="2023-06-01", end="2024-03-31")
    with pytest.raises(AssertionError, match="overlaps"):
        gbt.train(df, valid_df=df, early_stopping=10, num_rounds=40)


def test_calibration_reliability_if_master_built():
    if not os.path.exists(MASTER_MANIFEST):
        pytest.skip("master not built (run breakout_features.py build)")
    from models.breakout_calibrate import fit
    booster, iso, thr = fit(persist=False)
    assert 0.0 < thr < 1.0
    # isotonic is monotone non-decreasing
    xs = np.linspace(0.05, 0.95, 19)
    ys = iso.predict(xs)
    assert np.all(np.diff(ys) >= -1e-9)
