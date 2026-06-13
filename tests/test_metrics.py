"""Eval-harness sanity tests (NFR-2)."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from eval import metrics  # noqa: E402


def test_brier_perfect_and_worst():
    y = np.array([1, 0, 1, 0])
    assert metrics.brier(y, y.astype(float)) == 0.0
    assert metrics.brier(y, (1 - y).astype(float)) == 1.0


def test_brier_constant_equals_variance():
    rng = np.random.default_rng(0)
    y = (rng.random(10000) < 0.7).astype(int)
    p = np.full(len(y), y.mean())
    # Brier of the base-rate constant = p(1-p)
    assert abs(metrics.brier(y, p) - y.mean() * (1 - y.mean())) < 1e-9


def test_calibration_zero_when_perfectly_calibrated():
    rng = np.random.default_rng(1)
    p = rng.random(50000)
    y = (rng.random(50000) < p).astype(int)
    # perfectly calibrated by construction -> small max gap
    assert metrics.calibration_error(y, p, kind="max") < 0.03
    assert metrics.calibration_error(y, p, kind="ece") < 0.01


def test_calibration_sparse_bin_excluded_from_max():
    # 10000 well-calibrated points at p=0.5, plus 5 points at p=0.99 that are
    # all failures (a wild gap in a sparse bin). cal_max must ignore the
    # sparse bin; cal_ece (count-weighted) must stay small.
    p = np.concatenate([np.full(10000, 0.5), np.full(5, 0.99)])
    y = np.concatenate([(np.arange(10000) % 2), np.zeros(5)]).astype(int)
    assert metrics.calibration_error(y, p, kind="max") < 0.05
    assert metrics.calibration_error(y, p, kind="ece") < 0.05


def test_auc_single_class_is_nan():
    assert np.isnan(metrics.auc(np.ones(10), np.linspace(0, 1, 10)))


def test_slices_are_defined():
    assert set(metrics.SLICES) == {"early_session", "near_strike",
                                   "breakout_retest"}
