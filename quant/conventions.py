"""Shared conventions: the one sigma source and the one time convention.

Frozen Phase-1 config values live here (CLAUDE.md: "frozen Phase-1 config").
Changing any of these invalidates buckets, grid, and p_mkt together - they
are deliberately in one place so they cannot drift apart.
"""

import os
import sys
from datetime import datetime, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# --- time convention -------------------------------------------------------
# Calendar-time year fraction. Matches the convention IB's own greeks use
# (verified against recorded vega in the Phase S spike).
YEAR_SECONDS = 365.0 * 24 * 3600

SETTLE_FULL = time(16, 0)   # ET
SETTLE_HALF = time(13, 0)   # ET (half-day sessions)

# --- grid anchors (FR-4.1, frozen by task 1.3) ------------------------------
# Per-side normalized-distance sampling targets at the empirical median D of
# the {0.30, 0.20, 0.15, 0.10, 0.05} delta levels, measured from 62
# VIX1D-stratified recorded-chain days (analysis/delta_mapping.py;
# docs/calibration.md). Side-asymmetric because put skew pushes equal-delta
# strikes much farther out in implied-move units. NOTE: D values are bound
# to THIS project's sigma convention (prior-close VIX1D + calendar-time
# sqrt(T)) - they are not comparable to textbook d-values.
GRID_ANCHORS = {
    "p": (0.80, 1.35, 1.75, 2.30, 3.35),
    "c": (0.75, 1.15, 1.40, 1.75, 2.40),
}
STRIKE_INCREMENT = 5.0

# --- distance buckets (NFR-2.1, frozen by task 1.3) --------------------------
# Per-side bucket edges: median D at the {0.05, 0.10, 0.15, 0.25} delta
# levels. Band of record = the "10-15" bucket. Regime/time-of-day drift of
# the mapping (~10-18% IQR) is documented bucket-assignment noise; edges are
# deliberately FIXED (docs/calibration.md).
BUCKET_EDGES_D = {  # side -> (D at 0.05, 0.10, 0.15, 0.25 delta)
    "p": (3.363, 2.306, 1.729, 1.051),
    "c": (2.380, 1.754, 1.407, 0.927),
}
BUCKET_LABELS = ("lt05", "05-10", "10-15", "15-25", "gt25")
BAND_OF_RECORD = "10-15"


def delta_bucket(D, side):
    """Delta-equivalent bucket label for a normalized distance.
    For ANALYSIS/EVALUATION slicing only - never a model feature."""
    e05, e10, e15, e25 = BUCKET_EDGES_D[side]
    if D > e05:
        return "lt05"
    if D > e10:
        return "05-10"
    if D > e15:
        return "10-15"
    if D > e25:
        return "15-25"
    return "gt25"

# --- sigma anchor (FR-5.1 / task 1.4 decides final mode) -------------------
# "prior_close" is the strictly point-in-time-safe default: available at
# every bar including 09:30. "day_open" (first VIX1D print, ~09:31) is the
# task 1.7 sensitivity variant.
SIGMA_ANCHOR_MODE = "prior_close"


def time_to_settle(ts, is_half_day=False):
    """Year-fraction from bar timestamp ts (naive ET datetime) to settlement.
    Raises if ts is at/after settlement - querying a settled option is a bug."""
    settle = datetime.combine(ts.date(),
                              SETTLE_HALF if is_half_day else SETTLE_FULL)
    seconds = (settle - ts).total_seconds()
    if seconds <= 0:
        raise ValueError(f"{ts} is at/after settlement {settle}")
    return seconds / YEAR_SECONDS


class SigmaAnchor:
    """Per-day sigma from the VIX1D daily series via the load API.

    mode="prior_close": yesterday's VIX1D close (available all session).
    mode="day_open":    today's first print (sensitivity variant; NOT safe
                        for the 09:30 bar - caller beware, task 1.7).
    Returns decimal vol (VIX1D 12.3 -> 0.123).
    """

    def __init__(self, mode=SIGMA_ANCHOR_MODE):
        from data.loader import load_bars
        if mode not in ("prior_close", "day_open"):
            raise ValueError(f"unknown sigma anchor mode {mode}")
        self.mode = mode
        daily = load_bars("VIX1D", freq="1day")
        days = daily["ts"].dt.strftime("%Y-%m-%d").tolist()
        if mode == "prior_close":
            # value for day i = close of day i-1
            self._by_day = dict(zip(days[1:], daily["close"].tolist()[:-1]))
        else:
            self._by_day = dict(zip(days, daily["open"].tolist()))

    def sigma(self, day):
        """day: 'YYYY-MM-DD'. Raises KeyError outside the VIX1D universe."""
        return self._by_day[day] / 100.0
