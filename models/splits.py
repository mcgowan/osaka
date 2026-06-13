"""Split scheme for Phase 4 modeling (plan task 4.1; NFR-1.2).

INVIOLABLE RULE #2 (split discipline) lives here. Every train/validation
partition in Phase 4 goes through this module - no row-level or random
splits anywhere, including "quick experiments". Effective N is trading
days, not rows: adjacent 1-min samples and the strike grid at one bar all
share the same forward path, so they are near-duplicates and must never
straddle a split boundary.

THE UNIT IS A WEEK. Days are grouped by ISO (year, week); regions are cut
on week boundaries so a week is never split. The locked test set
(>= TEST_START, inviolable rule #3) is never produced here - this module
only ever sees pre-TEST_START data (load_master truncates by default and we
do NOT pass _unlocked_full_span).

Three chronological regions, oldest -> newest, embargo gaps between:

    [ TRAIN ........ ] (emb) [ CALIB ] (emb) [ VALID ]  | TEST_START | locked
      model fitting +         post-hoc        Gate-4 honest
      walk-forward CV         calibrator       validation +
      (hyperparam search,     fit (4.5)        residual analysis (4.8)
       ablations 4.4/4.6)                      - touched once, no selection

  - TRAIN: the only region model selection ever sees. walk_forward_folds()
    carves expanding-window CV folds *inside* TRAIN for 4.4/4.6.
  - CALIB: fits the isotonic/Platt calibrator (4.5). Never used to choose a
    model, hyperparameter, or feature set.
  - VALID: the most recent pre-test span (closest in regime to the locked
    test). Read once, for Gate-4 metrics and the 4.8 residual write-up.
    Never used for selection or calibrator fitting.

EMBARGO WIDTH. The longest feature lookback in the spec is rv5d_ratio
(trailing 5 trading days); mom3d_atr reads 3. One ISO week = 5 trading
days, so embargo_weeks=1 is the minimum that guarantees a later region's
features never read an earlier region's bars (labels are intraday and never
cross days, so they need no embargo of their own). Default is 1; raise it,
never lower it.
"""

import sys
import os

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.loader import TEST_START  # noqa: E402

EMBARGO_WEEKS = 1          # >= max feature lookback (rv5d = 5 trading days)
CALIB_WEEKS_FRAC = 0.15    # of pre-test weeks
VALID_WEEKS_FRAC = 0.15


def _week_id(day):
    """ISO (year, week) for a 'YYYY-MM-DD' string. Sorts chronologically
    as a tuple (iso_year increases across the calendar-year boundary, so the
    week-52->week-1 wrap orders correctly)."""
    iso = pd.Timestamp(day).isocalendar()
    return (int(iso.year), int(iso.week))


def assign_weeks(days):
    """days -> list of (day, week_id) and the chronological week ordering.
    Returns (day_to_week dict, ordered_weeks list)."""
    day_to_week = {d: _week_id(d) for d in days}
    ordered = sorted(set(day_to_week.values()))
    return day_to_week, ordered


class Split:
    """The frozen TRAIN/CALIB/VALID day partition (sets of 'YYYY-MM-DD')."""

    def __init__(self, train_days, calib_days, valid_days, embargo_days, meta):
        self.train_days = frozenset(train_days)
        self.calib_days = frozenset(calib_days)
        self.valid_days = frozenset(valid_days)
        self.embargo_days = frozenset(embargo_days)
        self.meta = meta
        # disjointness is the whole point - assert it loudly
        assert not (self.train_days & self.calib_days)
        assert not (self.train_days & self.valid_days)
        assert not (self.calib_days & self.valid_days)
        assert not (self.embargo_days & (self.train_days | self.calib_days
                                         | self.valid_days))

    def mask(self, day_series, region):
        """Boolean mask selecting `region` rows from a master 'day' column."""
        days = getattr(self, f"{region}_days")
        return day_series.isin(days)

    def __repr__(self):
        m = self.meta
        return (f"Split(train={len(self.train_days)}d "
                f"[{m['train_span'][0]}..{m['train_span'][1]}], "
                f"calib={len(self.calib_days)}d "
                f"[{m['calib_span'][0]}..{m['calib_span'][1]}], "
                f"valid={len(self.valid_days)}d "
                f"[{m['valid_span'][0]}..{m['valid_span'][1]}], "
                f"embargo={len(self.embargo_days)}d, "
                f"emb_weeks={m['embargo_weeks']})")


def make_split(days, calib_frac=CALIB_WEEKS_FRAC, valid_frac=VALID_WEEKS_FRAC,
               embargo_weeks=EMBARGO_WEEKS):
    """Partition pre-test trading `days` chronologically by week into
    TRAIN / (embargo) / CALIB / (embargo) / VALID.

    `days` must be pre-TEST_START already (we assert it). Fractions are of
    the *week* count, taken from the most recent end (VALID newest)."""
    days = sorted(days)
    if days and days[-1] >= TEST_START:
        raise ValueError(
            f"make_split received locked-period day(s) (>= {TEST_START}); "
            f"pass pre-test days only (rule #3)")
    day_to_week, weeks = assign_weeks(days)
    n = len(weeks)
    n_valid = max(1, round(n * valid_frac))
    n_calib = max(1, round(n * calib_frac))
    if n_valid + n_calib + 2 * embargo_weeks >= n:
        raise ValueError("not enough weeks for the requested split")

    # carve from the newest end backward: VALID, embargo, CALIB, embargo, TRAIN
    valid_w = set(weeks[n - n_valid:])
    emb1_w = set(weeks[n - n_valid - embargo_weeks: n - n_valid])
    calib_end = n - n_valid - embargo_weeks
    calib_w = set(weeks[calib_end - n_calib: calib_end])
    emb2_w = set(weeks[calib_end - n_calib - embargo_weeks: calib_end - n_calib])
    train_w = set(weeks[: calib_end - n_calib - embargo_weeks])

    by_week = {}
    for d, w in day_to_week.items():
        by_week.setdefault(w, []).append(d)

    def days_of(ws):
        out = []
        for w in ws:
            out += by_week.get(w, [])
        return sorted(out)

    train_days = days_of(train_w)
    calib_days = days_of(calib_w)
    valid_days = days_of(valid_w)
    embargo_days = days_of(emb1_w | emb2_w)

    meta = {
        "embargo_weeks": embargo_weeks,
        "n_weeks": n,
        "train_span": (train_days[0], train_days[-1]),
        "calib_span": (calib_days[0], calib_days[-1]),
        "valid_span": (valid_days[0], valid_days[-1]),
        "test_start": TEST_START,
    }
    return Split(train_days, calib_days, valid_days, embargo_days, meta)


def walk_forward_folds(train_days, n_folds=5, val_weeks=8,
                       embargo_weeks=EMBARGO_WEEKS):
    """Expanding-window walk-forward CV folds *within TRAIN* for
    hyperparameter search (4.4) and ablation selection (4.6).

    Fold k: fit on the oldest weeks, embargo gap, then a val_weeks block.
    The fit window grows with k (expanding, never peeks past its val block).
    Returns a list of (fit_days, val_days) tuples, oldest fold first.

    Yields exactly the folds that fit; raises if TRAIN is too short for even
    one fold at the requested sizes (caller should shrink the grid, not the
    discipline)."""
    train_days = sorted(train_days)
    day_to_week, weeks = assign_weeks(train_days)
    by_week = {}
    for d, w in day_to_week.items():
        by_week.setdefault(w, []).append(d)
    n = len(weeks)

    # last val block ends at the newest train week; earlier folds step back
    # by val_weeks each. Need a fit window of >= 1 week before each.
    span_per_fold = val_weeks
    min_fit_weeks = 1
    earliest_val_start = min_fit_weeks + embargo_weeks
    folds = []
    for k in range(n_folds):
        val_end = n - k * span_per_fold          # exclusive
        val_start = val_end - val_weeks
        fit_end = val_start - embargo_weeks       # exclusive
        if val_start < earliest_val_start or fit_end < min_fit_weeks:
            break
        fit_w = weeks[:fit_end]
        val_w = weeks[val_start:val_end]
        fit_days = sorted(d for w in fit_w for d in by_week[w])
        val_days = sorted(d for w in val_w for d in by_week[w])
        folds.append((fit_days, val_days))
    if not folds:
        raise ValueError(
            f"TRAIN ({n} weeks) too short for any walk-forward fold at "
            f"val_weeks={val_weeks}, embargo_weeks={embargo_weeks}")
    folds.reverse()   # oldest fold first
    return folds


if __name__ == "__main__":
    from data.features import load_master
    m = load_master()
    days = sorted(m["day"].unique())
    sp = make_split(days)
    print(sp)
    folds = walk_forward_folds(sorted(sp.train_days))
    print(f"\n{len(folds)} walk-forward folds within TRAIN:")
    for i, (fit, val) in enumerate(folds):
        print(f"  fold {i}: fit {len(fit)}d [{fit[0]}..{fit[-1]}]  "
              f"val {len(val)}d [{val[0]}..{val[-1]}]")
