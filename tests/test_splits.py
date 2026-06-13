"""Split-discipline tests (inviolable rule #2, NFR-1.2).

These guard the property that everything downstream in Phase 4 relies on:
no train/validation boundary ever splits a week, regions are disjoint and
chronologically ordered with a real embargo gap, and NO locked-period day
(>= TEST_START) can enter any region.
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.loader import TEST_START  # noqa: E402
from models.splits import (make_split, walk_forward_folds, assign_weeks,  # noqa: E402
                           _week_id)


# a synthetic ~2-year run of weekday "trading days", all pre-TEST_START
@pytest.fixture(scope="module")
def days():
    rng = pd.bdate_range("2023-01-02", "2024-12-31")
    return [d.strftime("%Y-%m-%d") for d in rng]


def test_regions_disjoint_and_chronological(days):
    sp = make_split(days)
    # disjoint (Split.__init__ asserts, but be explicit)
    assert not (sp.train_days & sp.calib_days)
    assert not (sp.train_days & sp.valid_days)
    assert not (sp.calib_days & sp.valid_days)
    # chronological: train < calib < valid with no interleaving
    assert max(sp.train_days) < min(sp.calib_days)
    assert max(sp.calib_days) < min(sp.valid_days)


def test_no_week_straddles_a_boundary(days):
    sp = make_split(days)
    region_of = {}
    for d in sp.train_days:
        region_of[d] = "train"
    for d in sp.calib_days:
        region_of[d] = "calib"
    for d in sp.valid_days:
        region_of[d] = "valid"
    # every week's days all land in one region (embargo weeks are absent)
    by_week = {}
    for d, r in region_of.items():
        by_week.setdefault(_week_id(d), set()).add(r)
    for w, regions in by_week.items():
        assert len(regions) == 1, f"week {w} straddles {regions}"


def test_embargo_gap_present(days):
    sp = make_split(days, embargo_weeks=1)
    assert sp.embargo_days  # non-empty
    # embargo sits strictly between regions, in neither
    assert min(sp.calib_days) > max(sp.train_days)
    emb = sorted(sp.embargo_days)
    # there is at least one embargo week between train and calib
    between = [d for d in emb if max(sp.train_days) < d < min(sp.calib_days)]
    assert between


def test_locked_period_never_enters(days):
    # appending locked-period days must raise, not silently leak
    contaminated = days + ["2026-01-05", "2026-01-06"]
    with pytest.raises(ValueError, match="locked"):
        make_split(contaminated)


def test_all_regions_pre_test_start(days):
    sp = make_split(days)
    for region in (sp.train_days, sp.calib_days, sp.valid_days, sp.embargo_days):
        assert all(d < TEST_START for d in region)


def test_walk_forward_folds_are_clean(days):
    sp = make_split(days)
    folds = walk_forward_folds(sorted(sp.train_days), n_folds=4, val_weeks=6)
    assert folds
    prev_fit_len = -1
    for fit, val in folds:
        fit, val = set(fit), set(val)
        # fit and val disjoint; fit strictly precedes val with an embargo gap
        assert not (fit & val)
        assert max(fit) < min(val)
        # expanding window: fit grows fold over fold
        assert len(fit) >= prev_fit_len
        prev_fit_len = len(fit)
        # all fold days are within TRAIN
        assert fit <= sp.train_days
        assert val <= sp.train_days


def test_walk_forward_embargo_between_fit_and_val(days):
    sp = make_split(days)
    folds = walk_forward_folds(sorted(sp.train_days), n_folds=4, val_weeks=6,
                               embargo_weeks=1)
    for fit, val in folds:
        # the week immediately before the val block must be absent from fit
        val_weeks = {_week_id(d) for d in val}
        fit_weeks = {_week_id(d) for d in fit}
        assert not (val_weeks & fit_weeks)


def test_embargo_must_cover_max_lookback(days):
    # embargo_weeks=0 (or any width < the longest feature lookback) must raise,
    # not silently let a later region's features read an earlier region's bars
    with pytest.raises(ValueError, match="lookback"):
        make_split(days, embargo_weeks=0)


def test_assign_weeks_orders_across_year_boundary():
    # ISO week wrap: late-Dec 2024 and early-Jan 2025 must order correctly
    days = ["2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03"]
    _, ordered = assign_weeks(days)
    assert ordered == sorted(ordered)
