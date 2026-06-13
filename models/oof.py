"""TRAIN walk-forward out-of-fold predictions - the development evaluation
surface (review item 1, 2026-06-13).

VALID is one-shot (Gate-4 only). Everything we look at WHILE iterating -
baseline comparisons, the 4.4 search, 4.6 ablations, sanity smokes - must read
out-of-fold predictions on the TRAIN walk-forward folds, never VALID. A human
who has seen "we already beat p_mkt on VALID" is applying selection pressure
the day-bootstrap cannot measure; the only protection is to not look.

walk_forward_oof pools each fold's val-block predictions into one frame: every
TRAIN val-week appears exactly once, predicted by a model fit only on strictly
earlier (embargoed) weeks. That frame is an honest, leakage-controlled stand-in
for held-out performance during development.
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from models.splits import walk_forward_folds  # noqa: E402


def walk_forward_oof(master, split, fit_predict, label="pred", **fold_kw):
    """Pooled out-of-fold predictions over the TRAIN walk-forward folds.

    fit_predict(fit_df, val_df) -> 1-D array of predictions for val_df. Called
    once per fold; the returned frame is the concatenation of each fold's
    val-block with a `label` prediction column. Only TRAIN days are ever
    touched (walk_forward_folds is TRAIN-internal)."""
    folds = walk_forward_folds(sorted(split.train_days), **fold_kw)
    parts = []
    for fit_days, val_days in folds:
        fit = master[master["day"].isin(fit_days)]
        val = master[master["day"].isin(val_days)].copy()
        val[label] = fit_predict(fit, val)
        parts.append(val)
    return pd.concat(parts, ignore_index=True)
