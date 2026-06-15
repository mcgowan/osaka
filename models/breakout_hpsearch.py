"""v2 Phase 3 — hyperparameter search (provenance for the frozen params).

Grid over the regularization knobs that matter when effective N = trading days,
selected on TRAIN walk-forward OOF logloss (a proper scoring rule - never the
operational ratio, which is the eval target). VALID is never touched. The winner
is frozen into models/breakout_gbt.DEFAULT_PARAMS.

Result (2026-06-14): num_leaves=7, min_data_in_leaf=1600 won (logloss 0.6256);
the most-regularized cell, consistent with effective N = days.

Run:  .venv/bin/python models/breakout_hpsearch.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from models.breakout_gbt import train_oof  # noqa: E402

GRID_NUM_LEAVES = (7, 15, 31)
GRID_MIN_DATA = (400, 800, 1600)


def _oof_logloss(oof):
    t = oof[oof["tradeable"] == 1]
    y = t["reversed"].to_numpy()
    p = np.clip(t["pred"].to_numpy(), 1e-6, 1 - 1e-6)
    return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))


def search():
    rows = []
    print(f"{'num_leaves':>10} {'min_data':>9} {'OOF logloss':>12}")
    for nl in GRID_NUM_LEAVES:
        for md in GRID_MIN_DATA:
            ll = _oof_logloss(train_oof(params={"num_leaves": nl,
                                                "min_data_in_leaf": md}))
            rows.append((ll, nl, md))
            print(f"{nl:>10} {md:>9} {ll:>12.4f}")
    best = min(rows)
    print(f"\nbest by OOF logloss: num_leaves={best[1]} min_data_in_leaf={best[2]} "
          f"(logloss {best[0]:.4f})")
    return best


if __name__ == "__main__":
    search()
