"""Tests for the model layer (review item 3): monotone wiring, build smoke,
determinism, logit5, feature-list discipline, and the one-shot VALID gate."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.features import load_master, FEATURES  # noqa: E402
from models import gbt, baselines  # noqa: E402
from models.splits import make_split, gate4_valid_days  # noqa: E402


@pytest.fixture(scope="module")
def small():
    """A small, fast slice: first ~60 TRAIN days of the master."""
    m = load_master()
    sp = make_split(sorted(m["day"].unique()))
    days = sorted(sp.train_days)[:60]
    return m[m["day"].isin(days)].reset_index(drop=True), sp, m


# --- monotone constraint wiring (the inviolable one) -----------------------

def test_monotone_plus_one_on_distance_only():
    feats = gbt.feature_list(include_pmkt=True)
    mono = gbt.monotone_constraints(feats)
    assert len(mono) == len(feats)
    assert mono[feats.index("D")] == 1
    assert sum(mono) == 1                      # +1 on D, 0 everywhere else
    assert all(c in (0, 1) for c in mono)


def test_feature_lists_differ_by_exactly_pmkt():
    with_p = set(gbt.feature_list(include_pmkt=True))
    without_p = set(gbt.feature_list(include_pmkt=False))
    assert with_p - without_p == {"pmkt"}
    assert without_p - with_p == set()
    assert "pmkt" not in without_p


# --- build / predict smoke + determinism -----------------------------------

def test_train_predict_finite_in_unit_interval(small):
    df, _, _ = small
    booster, feats = gbt.train(df, num_rounds=40)
    p = gbt.predict(booster, df, feats)
    assert len(p) == len(df)
    assert np.all(np.isfinite(p))
    assert p.min() >= 0.0 and p.max() <= 1.0


def test_training_is_deterministic(small):
    df, _, _ = small
    b1, f1 = gbt.train(df, num_rounds=40)
    b2, f2 = gbt.train(df, num_rounds=40)
    p1 = gbt.predict(b1, df, f1)
    p2 = gbt.predict(b2, df, f2)
    assert np.array_equal(p1, p2)              # bit-identical, not just close


def test_monotone_sweep_non_decreasing(small):
    # FR-6.4: prediction must be non-decreasing as D rises, others fixed.
    df, _, _ = small
    booster, feats = gbt.train(df, num_rounds=60)
    d_idx = feats.index("D")
    grid_d = np.arange(0.2, 5.01, 0.2)
    viol = 0
    for row in df[feats].to_numpy(float)[:50]:
        g = np.tile(row, (len(grid_d), 1))
        g[:, d_idx] = grid_d
        p = booster.predict(g)
        if np.any(np.diff(p) < -1e-9):
            viol += 1
    assert viol == 0


def test_early_stopping_rejects_overlapping_valid(small):
    df, _, _ = small
    # passing an overlapping frame as the early-stopping set must trip the guard
    with pytest.raises(AssertionError, match="overlaps"):
        gbt.train(df, num_rounds=20, valid_df=df, early_stopping=5)


# --- logit5 baseline -------------------------------------------------------

def test_logit5_fit_predict_finite(small):
    df, _, _ = small
    model = baselines.fit_logit5(df)
    p = baselines._logit_predict(model, df)
    assert len(p) == len(df)
    assert np.all(np.isfinite(p))
    assert p.min() >= 0.0 and p.max() <= 1.0


# --- the one-shot VALID gate (review item 1) -------------------------------

def test_valid_gate_blocks_without_reason(small):
    _, sp, _ = small
    with pytest.raises(RuntimeError, match="one-shot"):
        gate4_valid_days(sp)


def test_valid_gate_allows_with_reason(small):
    _, sp, _ = small
    days = gate4_valid_days(sp, _gate4_reason="unit test")
    assert set(days) == set(sp.valid_days)


def test_build_requires_gate4_reason(small):
    _, sp, m = small
    with pytest.raises(RuntimeError, match="one-shot"):
        gbt.build(sp, m, num_rounds=10)         # no _gate4_reason -> blocked
