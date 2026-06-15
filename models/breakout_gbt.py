"""v2 Phase 3 — LightGBM for OR-breakout reversal conviction.

Predicts P(reversed) per breakout event (the adverse-reversal label, K=1.0) from
the 26 v2 features (data/breakout_features.FEATURES). Mirrors v1 models/gbt.py
but: TARGET=reversed; the full v2 feature set; NO monotone constraint by default
(monotonicity is a 3.x ablation, not a prior - trader call 2026-06-14); no
categorical columns (v2 features are all numeric/ratio).

Regularized for effective N = trading days (~3,030 TRAIN days, not the ~20k
event-rows): large min_data_in_leaf, shallow leaves, L1/L2. Development
evaluation is TRAIN walk-forward OOF (breakout_features.walk_forward_oof, 25-day
embargo); CALIB is for calibration + the operating threshold; VALID is sealed for
the one-shot Phase-4 statistical gate (§7.1) and is never read here.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakout_features import FEATURES  # noqa: E402

SEED = 0
TARGET = "reversed"

# FROZEN 2026-06-14 by the TRAIN walk-forward OOF grid search
# (models/breakout_hpsearch.py): num_leaves in {7,15,31} x min_data in
# {400,800,1600}, selected on OOF logloss. The most-regularized cell won
# (num_leaves=7, min_data=1600, logloss 0.6256) - consistent with effective
# N = trading days. Heavier regularization also raised the operational edge
# (pooled greenlit-reversal ratio 0.80 -> 0.77) by discarding weak morning noise.
DEFAULT_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "boosting_type": "gbdt",
    "learning_rate": 0.03,
    "num_leaves": 7,
    "min_data_in_leaf": 1600,    # large: effective N is days, not event-rows
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
DEFAULT_NUM_ROUNDS = 400


def feature_list():
    return list(FEATURES)


def monotone_constraints(features, signs=None):
    """Per-feature monotone signs (default all 0 = unconstrained). `signs` is an
    optional {feature: +1/-1} map for the monotonicity ablation; e.g. reversal
    prob non-increasing in ext_atr -> {'ext_atr': -1}."""
    signs = signs or {}
    return [int(signs.get(f, 0)) for f in features]


def _dataset(df, features, reference=None):
    import lightgbm as lgb
    return lgb.Dataset(df[features], label=df[TARGET].values,
                       reference=reference, free_raw_data=False)


def train(train_df, params=None, num_rounds=DEFAULT_NUM_ROUNDS,
          valid_df=None, early_stopping=None, features=None, mono_signs=None):
    """Train one booster on train_df. Returns (booster, features).

    If valid_df + early_stopping are given, stops on its logloss. FOOTGUN GUARD
    (from v1): valid_df MUST be a within-TRAIN walk-forward fold's val block -
    never CALIB or VALID (early stopping is model selection). We assert it shares
    no day with train_df."""
    import lightgbm as lgb
    if features is None:
        features = feature_list()
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    p["monotone_constraints"] = monotone_constraints(features, mono_signs)
    dtrain = _dataset(train_df, features)
    valid_sets, callbacks = None, [lgb.log_evaluation(0)]
    if valid_df is not None:
        overlap = set(train_df["day"]) & set(valid_df["day"])
        assert not overlap, (
            f"early-stopping valid_df overlaps train_df on {len(overlap)} days "
            f"- pass a disjoint walk-forward fold val block, not CALIB/VALID")
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


# Broad multi-regime OOF coverage for v2's ~12-year TRAIN: 20 half-year val
# blocks tile ~2010-2020 (v1's 5x8wk default only covered the last ~year).
OOF_FOLDS = 20
OOF_VAL_WEEKS = 26


def train_oof(master=None, params=None, num_rounds=DEFAULT_NUM_ROUNDS,
              mono_signs=None, label="pred", n_folds=OOF_FOLDS,
              val_weeks=OOF_VAL_WEEKS):
    """Development evaluation: TRAIN walk-forward OOF predictions of P(reversed),
    with the v2 25-day fold embargo (breakout_features.walk_forward_oof). VALID
    is never touched. Returns the TRAIN val-block rows with a `label` column."""
    from data.breakout_features import load_master, walk_forward_oof
    m = master if master is not None else load_master()

    def fp(fit, val):
        booster, feats = train(fit, params=params, num_rounds=num_rounds,
                               mono_signs=mono_signs)
        return predict(booster, val, feats)

    return walk_forward_oof(m, fp, label=label, n_folds=n_folds,
                            val_weeks=val_weeks)
