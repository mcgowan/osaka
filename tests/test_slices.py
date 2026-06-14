"""OR post-breakout-retest slice (NFR-2.1b-c): correctness + point-in-time.

The slice claims to be 'as of bar t'. The truncation test recomputes
membership at (day, minute) from bars PHYSICALLY truncated at that minute and
asserts the flag is unchanged - i.e. it never reads a bar after t (an OR
breakout that happens later, or a later price path, cannot flip a bar into
the slice retroactively)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.loader import load_bars, load_calendar  # noqa: E402
from quant.conventions import SigmaAnchor  # noqa: E402
from eval.slices import _day_membership, attach_or_retest, OR_BARS  # noqa: E402


def _sample_days(n=6):
    from data.loader import TEST_START
    cal = load_calendar()
    ds = cal["date"].dt.strftime("%Y-%m-%d")
    # VIX1D universe (sigma anchor defined) and pre-test, full days only
    mask = (~cal["is_half_day"]) & (ds >= "2023-04-27") & (ds < TEST_START)
    days = ds[mask].tolist()
    idx = np.linspace(0, len(days) - 1, n).astype(int)
    return [days[i] for i in idx]


def test_membership_is_point_in_time():
    """Full-data membership == membership recomputed on bars truncated at t."""
    anchor = SigmaAnchor()
    halfs = set(load_calendar().query("is_half_day")["date"]
                .dt.strftime("%Y-%m-%d"))
    for day in _sample_days():
        sigma = anchor.sigma(day)
        n = len(load_bars("SPX", start=day, end=day))
        minutes = list(range(OR_BARS, n, 5))
        full = _day_membership(day, minutes, sigma, day in halfs)
        # recompute each minute against a physically truncated bar series
        import eval.slices as S
        real_load = S.load_bars
        for m in minutes[::7]:                      # subsample for speed
            def truncated_load(symbol, start=None, end=None, _real=real_load,
                               _m=m, **kw):
                b = _real(symbol, start=start, end=end, **kw)
                return b.iloc[: _m + 1]              # bars 0..m only
            S.load_bars = truncated_load
            try:
                got = S._day_membership(day, [m], sigma, day in halfs)[m]
            finally:
                S.load_bars = real_load
            assert got == full[m], f"{day} minute {m}: {got} != {full[m]}"


def test_pre_or_bars_never_in_slice():
    anchor = SigmaAnchor()
    halfs = set(load_calendar().query("is_half_day")["date"]
                .dt.strftime("%Y-%m-%d"))
    day = _sample_days(1)[0]
    sigma = anchor.sigma(day)
    minutes = list(range(0, OR_BARS, 5))            # all within the OR window
    mem = _day_membership(day, minutes, sigma, day in halfs)
    assert not any(mem.values())


def test_attach_is_idempotent_and_boolean():
    day = _sample_days(1)[0]
    df = pd.DataFrame({"day": [day, day], "minute": [60, 120]})
    out = attach_or_retest(df)
    assert out["or_retest"].dtype == bool
    # second attach is a no-op (column already present)
    again = attach_or_retest(out)
    assert (again["or_retest"] == out["or_retest"]).all()
