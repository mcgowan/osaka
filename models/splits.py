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

# The embargo must cover the longest feature lookback so a later region's
# features never read an earlier region's bars. rv5d_ratio (5 trading days)
# is the longest in the v1 spec; 1 ISO week = 5 trading days covers it
# EXACTLY. If a feature with a longer lookback is ever added, raise
# EMBARGO_WEEKS or this guard fails loudly (leakage-redteam style finding,
# 2026-06-13: make the coupling programmatic, not just a comment).
TRADING_DAYS_PER_WEEK = 5
MAX_FEATURE_LOOKBACK_DAYS = 5


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


def _by_week(days):
    """day-list -> (ordered weeks, {week: sorted day list})."""
    day_to_week, weeks = assign_weeks(days)
    bw = {}
    for d, w in day_to_week.items():
        bw.setdefault(w, []).append(d)
    for w in bw:
        bw[w].sort()
    return weeks, bw


def _carve_embargo(weeks, bw, hi, embargo_weeks, min_gap_days):
    """Return lo such that weeks[lo:hi] is the embargo region holding AT LEAST
    `embargo_weeks` weeks AND `min_gap_days` real TRADING days. Widening past
    embargo_weeks is what makes the gap holiday-proof: a holiday week has only
    4 trading days, so one week (the nominal embargo) leaves a 4-day gap and a
    5-day-lookback feature on the next region's first day would bleed one day
    across the boundary - so we pull in another week until the trading-day
    count actually clears min_gap_days."""
    lo = hi
    while lo > 0:
        lo -= 1
        n_weeks = hi - lo
        n_days = sum(len(bw[w]) for w in weeks[lo:hi])
        if n_weeks >= embargo_weeks and n_days >= min_gap_days:
            return lo
    return lo  # ran out of weeks; caller validates sufficiency


def _assert_no_leak(prev_days, next_days, all_pos, min_gap, label):
    """Defense-in-depth: the first day of `next_days` must sit at least
    min_gap TRADING days after the last day of `prev_days`, so a feature with
    up to min_gap-day lookback on that first day never reads a prev-region
    bar. Checks real positions in the full ordered day list, so holidays
    can't fake the gap."""
    if not prev_days or not next_days:
        return
    gap = all_pos[next_days[0]] - all_pos[prev_days[-1]] - 1
    if gap < min_gap:
        raise AssertionError(
            f"{label}: only {gap} trading days between {prev_days[-1]} and "
            f"{next_days[0]} (need >= {min_gap}); embargo failed to widen")


def make_split(days, calib_frac=CALIB_WEEKS_FRAC, valid_frac=VALID_WEEKS_FRAC,
               embargo_weeks=EMBARGO_WEEKS,
               min_gap_days=MAX_FEATURE_LOOKBACK_DAYS):
    """Partition pre-test trading `days` chronologically by week into
    TRAIN / (embargo) / CALIB / (embargo) / VALID.

    `days` must be pre-TEST_START already (we assert it). Fractions are of the
    *week* count, taken from the most recent end (VALID newest). The embargo
    between regions holds at least `embargo_weeks` weeks AND `min_gap_days`
    real trading days - the latter is the no-leak guarantee (it widens past
    one week when a holiday shrinks the gap), asserted on real day positions
    at the end."""
    days = sorted(days)
    if days and days[-1] >= TEST_START:
        raise ValueError(
            f"make_split received locked-period day(s) (>= {TEST_START}); "
            f"pass pre-test days only (rule #3)")
    weeks, bw = _by_week(days)
    n = len(weeks)
    n_valid = max(1, round(n * valid_frac))
    n_calib = max(1, round(n * calib_frac))

    valid_lo = n - n_valid
    emb1_lo = _carve_embargo(weeks, bw, valid_lo, embargo_weeks, min_gap_days)
    calib_hi = emb1_lo
    calib_lo = calib_hi - n_calib
    if calib_lo <= 0:
        raise ValueError("not enough weeks for the requested split")
    emb2_lo = _carve_embargo(weeks, bw, calib_lo, embargo_weeks, min_gap_days)
    train_hi = emb2_lo
    if train_hi < 1:
        raise ValueError("not enough weeks for the requested split")

    def days_of(lo, hi):
        return sorted(d for w in weeks[lo:hi] for d in bw[w])

    train_days = days_of(0, train_hi)
    calib_days = days_of(calib_lo, calib_hi)
    valid_days = days_of(valid_lo, n)
    embargo_days = days_of(emb2_lo, calib_lo) + days_of(emb1_lo, valid_lo)
    embargo_days.sort()

    # programmatic no-leak check on REAL day positions (holiday-proof)
    pos = {d: i for i, d in enumerate(days)}
    _assert_no_leak(train_days, calib_days, pos, min_gap_days, "train->calib")
    _assert_no_leak(calib_days, valid_days, pos, min_gap_days, "calib->valid")

    meta = {
        "embargo_weeks": embargo_weeks,
        "min_gap_days": min_gap_days,
        "n_weeks": n,
        "train_span": (train_days[0], train_days[-1]),
        "calib_span": (calib_days[0], calib_days[-1]),
        "valid_span": (valid_days[0], valid_days[-1]),
        "test_start": TEST_START,
    }
    return Split(train_days, calib_days, valid_days, embargo_days, meta)


def gate4_valid_days(split, _gate4_reason=None):
    """The ONE-SHOT VALID accessor. VALID is the Gate-4 region: it must be
    read exactly once, on the frozen model, at Gate 4 - never during
    iterative development (every dev read is invisible selection pressure, the
    same risk class as the locked test set). All pre-Gate-4 evaluation goes
    through TRAIN walk-forward OOF (models.oof.walk_forward_oof) instead.

    This raises unless an explicit `_gate4_reason` is given, so VALID cannot be
    pulled into a main() or a quick experiment by accident. The kwarg is named
    to be greppable, mirroring data.loader._unlocked_full_span."""
    if not _gate4_reason:
        raise RuntimeError(
            "VALID is one-shot (Gate-4 only). Use TRAIN walk-forward OOF "
            "(models.oof.walk_forward_oof) for all development evaluation. To "
            "read VALID deliberately at the gate, pass _gate4_reason='...'.")
    return sorted(split.valid_days)


def walk_forward_folds(train_days, n_folds=5, val_weeks=8,
                       embargo_weeks=EMBARGO_WEEKS,
                       min_gap_days=MAX_FEATURE_LOOKBACK_DAYS):
    """Expanding-window walk-forward CV folds *within TRAIN* for
    hyperparameter search (4.4) and ablation selection (4.6).

    Fold k: fit on the oldest weeks, embargo gap, then a val_weeks block.
    The fit window grows with k (expanding, never peeks past its val block).
    The fit->val embargo holds >= embargo_weeks weeks AND >= min_gap_days real
    trading days (same holiday-proof widening as make_split). Returns a list
    of (fit_days, val_days) tuples, oldest fold first.

    Raises if TRAIN is too short for even one fold (shrink the grid, not the
    discipline)."""
    train_days = sorted(train_days)
    weeks, bw = _by_week(train_days)
    n = len(weeks)
    pos = {d: i for i, d in enumerate(train_days)}

    span_per_fold = val_weeks
    folds = []
    for k in range(n_folds):
        val_end = n - k * span_per_fold          # exclusive
        val_start = val_end - val_weeks
        if val_start < 1:
            break
        fit_end = _carve_embargo(weeks, bw, val_start, embargo_weeks,
                                 min_gap_days)   # exclusive week index
        if fit_end < 1:
            break
        fit_days = sorted(d for w in weeks[:fit_end] for d in bw[w])
        val_days = sorted(d for w in weeks[val_start:val_end] for d in bw[w])
        _assert_no_leak(fit_days, val_days, pos, min_gap_days, f"fold{k}")
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
