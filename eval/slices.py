"""Opening-range post-breakout-retest slice (NFR-2.1b-c).

DEFINITION (trader, 2026-06-12): the slice is the trader's actual
false-breakout-of-OR scenario - the moment that "causes a LOT of grief".
Opening range = high/low over the first 30 min (bars 0-29, 09:30-09:59 ET),
fixed once 10:00 passes. A bar t (minute >= 30) is in the slice when:

  (1) an OR breakout has ALREADY occurred earlier today - some bar in
      [30, t) traded above OR-high (or below OR-low), AND
  (2) price has pulled back into the retest zone - |S(t) - OR_level| <=
      BAND * M(t), with the broken level (OR-high for an up-break, OR-low
      for a down-break).

BAND = 0.5 implied-move units; NO recency window (any retest is a threat -
trader decision). M(t) = sigma_anchor * sqrt(T(t)) * S(t), the SAME
implied-move unit as D and the near-strike slice (rule #5).

OR is used ONLY to define this EVALUATION slice - it is never a model
feature (the model stays an independent second opinion to the trader's OR
system, feature-spec exclusion #4). The membership is computed point-in-time
(OR from bars[:30], breakout from bars[30:t], proximity from open(t)) - every
input is <= t, so it reflects the real-time scenario and is lookahead-clean;
tests/test_slices.py asserts it via truncation.

This module reads SPX bars through the load API (default-truncated; VALID
days are all pre-TEST_START, so no _unlocked_full_span is needed).
"""

import math

import numpy as np
import pandas as pd

from data.loader import load_bars, load_calendar
from quant.conventions import SigmaAnchor, time_to_settle

OR_BARS = 30          # first 30 one-min bars form the opening range
RETEST_BAND_D = 0.5   # implied-move units (trader)


def _half_days():
    cal = load_calendar()
    return set(cal[cal["is_half_day"]]["date"].dt.strftime("%Y-%m-%d"))


def _day_membership(day, minutes, sigma, is_half_day, band=RETEST_BAND_D):
    """Return {minute: bool} OR-retest membership for the requested stride
    `minutes` of one day. Pure prefix logic at each bar."""
    bars = load_bars("SPX", start=day, end=day)
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    ts = bars["ts"]
    n = len(bars)
    out = {}
    if n <= OR_BARS:          # no session past the OR window (shouldn't happen)
        return {m: False for m in minutes}
    or_high = h[:OR_BARS].max()
    or_low = l[:OR_BARS].min()
    for m in minutes:
        i = int(m)            # master minute key == bar index (minutes_since_open)
        if i < OR_BARS or i >= n:
            out[m] = False
            continue
        broke_up = h[OR_BARS:i].max() > or_high if i > OR_BARS else False
        broke_dn = l[OR_BARS:i].min() < or_low if i > OR_BARS else False
        S = o[i]
        T = time_to_settle(ts.iloc[i], is_half_day=is_half_day)
        M = sigma * math.sqrt(T) * S
        retest_up = broke_up and abs(S - or_high) <= band * M
        retest_dn = broke_dn and abs(S - or_low) <= band * M
        out[m] = bool(retest_up or retest_dn)
    return out


def attach_or_retest(df, band=RETEST_BAND_D):
    """Add a boolean `or_retest` column to a copy of `df` (keyed on its
    day/minute), computed from SPX bars. Idempotent: returns df unchanged if
    the column is already present."""
    if "or_retest" in df.columns:
        return df
    df = df.copy()
    anchor = SigmaAnchor()                 # prior_close, default-truncated
    halfs = _half_days()
    flags = {}
    for day, sub in df.groupby("day"):
        minutes = sub["minute"].unique().tolist()
        sigma = anchor.sigma(day)
        mem = _day_membership(day, minutes, sigma, day in halfs, band=band)
        for m, v in mem.items():
            flags[(day, int(m))] = v
    keys = list(zip(df["day"], df["minute"].astype(int)))
    df["or_retest"] = [flags.get(k, False) for k in keys]
    return df
