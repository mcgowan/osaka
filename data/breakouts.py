"""v2 Phase 1 — opening-range breakout event-builder + no-reentry labels.

Builds the v2 training universe: every OR breakout EVENT (per side, multiple
per day), labeled by whether it HELD (no close back inside the OR) to EOD. This
is decoupled from whether the trader would have entered (entry needs his other
gates — not the model's concern). The crude 5-bar rule is reconstructed here as
the benchmark to beat.

DEFINITIONS (frozen, docs/v2-plan.md §4):
- Opening range (OR): high/low of the first 30 1-min bars (09:30-09:59 ET);
  fixed once 10:00 passes.
- Event (per side): starts at the first bar that CLOSES outside the OR boundary
  (up: close > OR_high; down: close < OR_low) after OR-finalized; stays alive
  while it keeps closing outside; TERMINATES + re-arms at the first bar that
  closes back at/inside the boundary. (A close through to the other side also
  terminates — it is no longer "above/below the OR".)
- PRIMARY label (adverse-reversal, price fact): max_adverse_orw = the largest
  adverse excursion AFTER the breakout, from the breakout close (entry proxy) to
  the lowest low (up) / highest high (down) through EOD, normalized by the OR
  width. A breakout `reversed` iff max_adverse_orw >= REVERSAL_K. K=1.0 is FROZEN
  (trader-ratified 2026-06-14): calibrated against the 103 pre-locked logged
  trades, where it flags 66% of real losers vs only 16% of winners, and cleanly
  separates real winners (max_adverse_orw med ~0.3) from losers (~1.7). This is
  the target the model predicts: weak / violently-reversing breakouts to avoid
  entering (it maps to the OR-puncture `risk_off_reversal` exit, the -$96k pool;
  the strike-relative `sr_inner_breach` exit is downstream strike construction,
  out of scope). NO sigma/greeks - pure price, OR-width units (labels-are-price-
  facts discipline carried from v1). See analysis/breakout_label_calib.py.
- SECONDARY: held_to_eod = the event never closed back inside the OR before the
  close (the old no-reentry signal; kept as a feature/diagnostic, not the target).
- 5-bar rule (incumbent benchmark): an event is "confirmed" iff it strings >= 5
  consecutive closes outside before terminating. confirmed <=> n_closes_outside
  >= 5 (consecutive-closes-outside by construction).

POINT-IN-TIME: the OR uses bars[:30]; labels (held_to_eod, max_adverse_orw) scan
the rest of the day - they are OUTCOMES and may see the future. Per-bar FEATURES
(Phase 2) are strictly prefix-only and harness-checked; nothing here is a feature.

Instrument: SPY (has volume). Tradeable window for evaluation: events starting
in [10:00, 15:00) ET (OR-finalized -> cutoff). Train broad (all-day), judge narrow.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.loader import load_bars, V2_TEST_START  # noqa: E402,F401  (re-export)

OR_BARS = 30            # first 30 min form the OR; OR finalized at minute 30
CUTOFF_MIN = 330        # 15:00 ET — end of the trader's tradeable entry window
CONFIRM_BARS = 5        # the 5-bar incumbent
MIN_OR_BARS = 25        # need this many of the first 30 bars to trust the OR
REVERSAL_K = 1.0        # FROZEN (trader-ratified 2026-06-14): a breakout
                        # `reversed` iff its adverse give-back >= 1.0 OR-width

# V2_TEST_START (the v2 locked-test boundary) is defined in data.loader, where
# the STRUCTURAL truncation lives (load_bars/trading_days exclude it by default).
# RATIFIED 2026-06-14: days >= it are sealed for the Phase-4 chain judge (191
# logged trades) and never enter training; the 103 earlier chain-era trades
# calibrated the label. Re-exported above for callers that import it from here.

SIDES = ("up", "down")


def _mod(ts):
    """Minute-of-day from 09:30 ET (09:30 -> 0, 10:00 -> 30, 16:00 -> 390)."""
    return ts.dt.hour * 60 + ts.dt.minute - (9 * 60 + 30)


def opening_range(day_df):
    """(OR_high, OR_low) from the first OR_BARS bars, or None if too few."""
    orb = day_df[day_df["mod"] < OR_BARS]
    if len(orb) < MIN_OR_BARS:
        return None
    return float(orb["high"].max()), float(orb["low"].min())


def _adverse_orw(up, st_close, st_idx, suf_min_low, suf_max_high, or_width):
    """Max adverse excursion AFTER entry (bar st_idx's close), in OR-width units.
    suf_min_low[i]/suf_max_high[i] are the extreme low/high over bars strictly
    after i (entry is the close of bar i, so the adverse path starts next bar).

    INTENTIONAL: the scan runs to EOD, NOT to event termination - this models
    "would I have been stopped after entering here," so a failed event and a
    later re-break on the same side have overlapping adverse windows by design.
    Do not "fix" this to stop at the episode boundary."""
    ext = suf_min_low[st_idx] if up else suf_max_high[st_idx]
    if not np.isfinite(ext):                  # entry was the last bar of the day
        return 0.0
    adv = (st_close - ext) if up else (ext - st_close)
    return max(0.0, float(adv)) / or_width


def day_events(day, day_df):
    """All OR-breakout events for one day (both sides). day_df: that day's SPY
    1-min bars with a 'mod' column, ascending."""
    rng = opening_range(day_df)
    if rng is None:
        return []
    or_high, or_low = rng
    or_width = or_high - or_low
    post = day_df[day_df["mod"] >= OR_BARS]
    n = len(post)
    if n == 0 or or_width <= 0:
        return []
    mods = post["mod"].to_numpy()
    closes = post["close"].to_numpy()
    lows = post["low"].to_numpy()
    highs = post["high"].to_numpy()
    vols = post["volume"].to_numpy()
    # suffix extremes over bars strictly after index i (for the adverse scan)
    suf_min_low = np.full(n, np.inf)
    suf_max_high = np.full(n, -np.inf)
    for i in range(n - 2, -1, -1):
        suf_min_low[i] = min(lows[i + 1], suf_min_low[i + 1])
        suf_max_high[i] = max(highs[i + 1], suf_max_high[i + 1])
    last_mod = int(mods[-1])
    events = []
    for side in SIDES:
        up = side == "up"
        attempt = 0
        in_ep = False
        st_idx = st_mod = st_close = st_vol = n_out = None
        for i in range(n):
            outside = (closes[i] > or_high) if up else (closes[i] < or_low)
            if outside:
                if not in_ep:
                    in_ep = True
                    attempt += 1
                    st_idx, st_mod = i, int(mods[i])
                    st_close, st_vol = float(closes[i]), float(vols[i])
                    n_out = 1
                else:
                    n_out += 1
            elif in_ep:                       # closed back inside/through -> FAIL
                events.append(_ev(day, side, attempt, st_mod, int(mods[i]),
                                  n_out, False, or_high, or_low, or_width,
                                  st_close, st_vol,
                                  _adverse_orw(up, st_close, st_idx, suf_min_low,
                                               suf_max_high, or_width)))
                in_ep = False
        if in_ep:                             # never terminated -> held to EOD
            events.append(_ev(day, side, attempt, st_mod, last_mod, n_out,
                              True, or_high, or_low, or_width, st_close, st_vol,
                              _adverse_orw(up, st_close, st_idx, suf_min_low,
                                           suf_max_high, or_width)))
    return events


def _ev(day, side, attempt, start_mod, end_mod, n_out, held, oh, ol, ow,
        st_close, st_vol, max_adverse_orw):
    return {
        "day": day, "side": side, "attempt": attempt,
        "start_mod": start_mod, "end_mod": end_mod,
        "n_closes_outside": n_out, "held_to_eod": int(held),
        "max_adverse_orw": max_adverse_orw,
        "reversed": int(max_adverse_orw >= REVERSAL_K),
        "confirmed_5bar": int(n_out >= CONFIRM_BARS),
        "tradeable": int(OR_BARS <= start_mod < CUTOFF_MIN),
        "or_high": oh, "or_low": ol, "or_width": ow,
        "start_close": st_close, "start_volume": st_vol,
    }


def build_events(start=None, end=None, _unlocked_full_span=False):
    """Build the event table over SPY. By default the loader truncates SPY at
    V2_TEST_START (structural lock, rule #3), so this returns DEV-only events;
    the Phase-4 chain judge passes _unlocked_full_span=True (sanctioned)."""
    spy = load_bars("SPY", start=start, end=end,
                    _unlocked_full_span=_unlocked_full_span)
    spy["mod"] = _mod(spy["ts"])
    spy["day"] = spy["ts"].dt.strftime("%Y-%m-%d")
    rows = []
    for day, g in spy.groupby("day", sort=True):
        rows.extend(day_events(day, g))
    return pd.DataFrame(rows)


def v2_split(min_gap_days=20, **kw):
    """Day-clustered TRAIN/CALIB/VALID split over the v2 (SPY) dev universe,
    using the same week-grained/embargo machinery as v1 but with the v2 locked
    boundary. Days >= V2_TEST_START are the reserved locked test (the Phase-4
    economic judge runs there); they never enter the split (and trading_days now
    truncates them out anyway). min_gap_days MUST be >= the longest feature
    lookback; feature-aware callers use breakout_features.split(), which derives
    it (EMBARGO_DAYS) so it can't drift below a feature's reach. _assert_no_leak
    enforces the gap but cannot know a feature's true lookback - only this can."""
    from data.loader import trading_days
    from models.splits import make_split
    days = [d for d in trading_days("SPY") if d < V2_TEST_START]
    return make_split(days, test_start=V2_TEST_START, min_gap_days=min_gap_days,
                      **kw)


if __name__ == "__main__":
    df = build_events()
    print(f"{len(df):,} events over {df['day'].nunique():,} days "
          f"({df['day'].min()}..{df['day'].max()})")
    sp = v2_split()
    print(sp)
