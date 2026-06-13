"""Evaluation metrics for the survival model (NFR-2, Section 8).

PRIMARY metric is Brier + calibration vs the implied baseline p_mkt, reported
PER DISTANCE BUCKET (band of record = the 10-15 bucket) and PER NFR-2.1b
SLICE. AUC is secondary. Accuracy is NOT a metric (a constant "survive" call
scores ~68% here).

Honesty rules baked in (NFR-2.2): effective N is trading DAYS, not rows.
Brier-skill confidence intervals are bootstrapped by RESAMPLING DAYS (a
day's rows share one forward path; resampling rows would fake significance).
Pooled numbers are always reported alongside, never instead of, the bucket
and slice breakdowns - a model that wins pooled but loses the band of record
fails the project (Section 8).

The band of record and the named slices are pre-registered (NFR-2.1/2.1b);
they are defined here once so every report - baselines, ablations, residual
analysis, the Phase-5 final run - slices identically.
"""

import numpy as np
import pandas as pd

from quant.conventions import BAND_OF_RECORD, BUCKET_LABELS

# --- NFR-2.1b slice definitions (pre-registered) ---------------------------
EARLY_SESSION_MAX_MIN = 30      # bars 9:31-10:00 ET (pre-OR entry mode)
NEAR_STRIKE_D = 0.5            # |distance| <= 0.5 remaining-implied-move units
# post-breakout-retest (c) is an OR-based slice computed in eval.slices
# (needs SPX bars); its band lives there as RETEST_BAND_D.


def brier(y, p):
    """Mean squared error of probabilistic survival forecasts."""
    y = np.asarray(y, float)
    p = np.asarray(p, float)
    return float(np.mean((p - y) ** 2))


def auc(y, p):
    """ROC-AUC (secondary metric). Returns nan if only one class present."""
    from sklearn.metrics import roc_auc_score
    y = np.asarray(y, int)
    if y.min() == y.max():
        return float("nan")
    return float(roc_auc_score(y, np.asarray(p, float)))


# A reliability bin must hold at least this share of the sample (and an
# absolute floor) before its gap counts toward the max-calibration test - a
# 3-point bin in a sparse probability region must not masquerade as a
# calibration failure ("across the probability range actually produced",
# Section 8). cal_ece is count-weighted and already robust to this.
CAL_MIN_BIN_FRAC = 0.01
CAL_MIN_BIN_N = 30


def reliability_table(y, p, n_bins=10):
    """Calibration / reliability table. Bins predictions into n_bins equal-
    width [0,1] bins; returns per-bin mean prediction, observed survival
    rate, count, and signed gap (pred - obs). Empty bins are dropped."""
    y = np.asarray(y, float)
    p = np.asarray(p, float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        m = idx == b
        if not m.any():
            continue
        rows.append({
            "bin": b,
            "p_mean": float(p[m].mean()),
            "y_mean": float(y[m].mean()),
            "n": int(m.sum()),
            "gap": float(p[m].mean() - y[m].mean()),
        })
    return pd.DataFrame(rows)


def calibration_error(y, p, n_bins=10, kind="max"):
    """Calibration error in probability points (0-1 scale).

    kind="max": largest |gap| over occupied bins that clear the minimum-
                occupancy floor (the Section-8 ±3pt test reads on this -
                'within ±3 points across the probability range actually
                produced'; sparse-bin gaps are excluded as not part of the
                range actually produced in volume).
    kind="ece": count-weighted mean |gap| (expected calibration error).
    Returns nan if n < 50 (too thin to read a calibration curve honestly)."""
    if len(y) < 50:
        return float("nan")
    tbl = reliability_table(y, p, n_bins)
    if tbl.empty:
        return float("nan")
    if kind == "max":
        floor = max(CAL_MIN_BIN_N, CAL_MIN_BIN_FRAC * len(y))
        dense = tbl[tbl["n"] >= floor]
        if dense.empty:
            return float("nan")
        return float(dense["gap"].abs().max())
    if kind == "ece":
        w = tbl["n"] / tbl["n"].sum()
        return float((w * tbl["gap"].abs()).sum())
    raise ValueError(kind)


def _n_days(df):
    return int(df["day"].nunique())


def bucket_report(df, p_col, ref_col="pmkt", y_col="survive",
                  buckets=BUCKET_LABELS):
    """Per distance-bucket metrics for prediction column `p_col` against the
    implied baseline `ref_col`. One row per bucket + a pooled row.

    Columns: n_rows, n_days, base_rate, brier, brier_ref, brier_skill
    (= brier_ref - brier, positive = model beats baseline), cal_max
    (max calibration gap, pts), cal_ece, auc."""
    out = []

    def one(sub, label):
        if len(sub) == 0:
            return
        y = sub[y_col].values
        p = sub[p_col].values
        r = sub[ref_col].values
        out.append({
            "bucket": label,
            "n_rows": len(sub),
            "n_days": _n_days(sub),
            "base_rate": round(float(np.mean(y)), 4),
            "brier": round(brier(y, p), 5),
            "brier_ref": round(brier(y, r), 5),
            "brier_skill": round(brier(y, r) - brier(y, p), 5),
            "cal_max": round(calibration_error(y, p, kind="max"), 4),
            "cal_ece": round(calibration_error(y, p, kind="ece"), 4),
            "auc": round(auc(y, p), 4),
        })

    for b in buckets:
        one(df[df["bucket"] == b], b)
    one(df, "POOLED")
    res = pd.DataFrame(out)
    if not res.empty:
        res.attrs["band_of_record"] = BAND_OF_RECORD
    return res


# --- slices (NFR-2.1b) ------------------------------------------------------

def early_session(df):
    """(a) Pre-OR entry mode: bars 9:31-10:00 ET."""
    return df["minutes_since_open"] <= EARLY_SESSION_MAX_MIN


def near_strike(df):
    """(b) False-breakout discrimination mode: price within ~0.5 remaining-
    implied-move units of the queried strike (|D| <= 0.5). D is signed
    (positive = OTM), and survival queries are OTM, so this is D <= 0.5."""
    return df["D"] <= NEAR_STRIKE_D


def breakout_retest(df):
    """(c) Post-breakout retest of the OPENING RANGE (trader definition,
    2026-06-12): an OR breakout already occurred today and price has pulled
    back to within 0.5 implied-move units of the broken OR level - the
    trader's false-breakout-of-OR scenario.

    Membership is precomputed by eval.slices.attach_or_retest (it needs SPX
    bars, not just master columns). Reads the boolean `or_retest` column;
    slice_report auto-attaches it. Raises if absent and unattached."""
    if "or_retest" not in df.columns:
        raise KeyError("breakout_retest needs the 'or_retest' column - call "
                       "eval.slices.attach_or_retest(df) first "
                       "(slice_report does this automatically)")
    return df["or_retest"].astype(bool)


SLICES = {
    "early_session": early_session,
    "near_strike": near_strike,
    "breakout_retest": breakout_retest,   # PROVISIONAL
}


def slice_report(df, p_col, ref_col="pmkt", y_col="survive",
                 band_only=False):
    """Per-slice metrics over each slice's natural population (NFR-2.1b).

    The slices are distance-SPANNING use-case populations, not subsets of the
    band of record - near_strike (D<=0.5) is definitionally OUTSIDE the band
    (D in ~1.7-2.3), so intersecting them is empty. Default band_only=False
    reports each slice over all buckets, which is what NFR-2.1b asks for.
    band_only=True is available for the (early_session, breakout_retest)
    slices, which do overlap the band, but is meaningless for near_strike."""
    if "or_retest" not in df.columns:
        from eval.slices import attach_or_retest
        df = attach_or_retest(df)
    base = df[df["bucket"] == BAND_OF_RECORD] if band_only else df
    out = []
    for name, fn in SLICES.items():
        sub = base[fn(base).values]
        if len(sub) == 0:
            out.append({"slice": name, "n_rows": 0, "n_days": 0})
            continue
        y, p, r = sub[y_col].values, sub[p_col].values, sub[ref_col].values
        out.append({
            "slice": name,
            "n_rows": len(sub),
            "n_days": _n_days(sub),
            "base_rate": round(float(np.mean(y)), 4),
            "brier": round(brier(y, p), 5),
            "brier_ref": round(brier(y, r), 5),
            "brier_skill": round(brier(y, r) - brier(y, p), 5),
            "cal_max": round(calibration_error(y, p, kind="max"), 4),
            "auc": round(auc(y, p), 4),
        })
    res = pd.DataFrame(out)
    res.attrs["band_only"] = band_only
    return res


def day_bootstrap_brier_skill(df, p_col, ref_col="pmkt", y_col="survive",
                              n_boot=2000, seed=0):
    """Day-clustered bootstrap CI for Brier skill (brier_ref - brier_model).

    Resamples DAYS with replacement (effective N = days, NFR-2.2), recomputes
    pooled Brier skill each draw. Returns (point, lo95, hi95, p_gt_0) where
    p_gt_0 is the bootstrap fraction with positive skill (model beats
    baseline). The Section-8 'strictly better, stable across sub-periods'
    test reads on the band-of-record subset passed in here."""
    rng = np.random.default_rng(seed)
    g = df.groupby("day")
    # precompute per-day arrays once
    days = list(g.groups.keys())
    per_day = {d: (sub[y_col].values, sub[p_col].values, sub[ref_col].values)
               for d, sub in g}

    def skill_from(daylist):
        ys = np.concatenate([per_day[d][0] for d in daylist])
        ps = np.concatenate([per_day[d][1] for d in daylist])
        rs = np.concatenate([per_day[d][2] for d in daylist])
        return brier(ys, rs) - brier(ys, ps)

    point = skill_from(days)
    nd = len(days)
    draws = np.empty(n_boot)
    days_arr = np.array(days, dtype=object)
    for b in range(n_boot):
        pick = days_arr[rng.integers(0, nd, nd)]
        draws[b] = skill_from(list(pick))
    return {
        "point": round(float(point), 5),
        "lo95": round(float(np.percentile(draws, 2.5)), 5),
        "hi95": round(float(np.percentile(draws, 97.5)), 5),
        "p_gt_0": round(float(np.mean(draws > 0)), 4),
        "n_days": nd,
    }
