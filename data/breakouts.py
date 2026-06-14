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
- Label: held_to_eod = the event never terminated before the session close.
  Per the no-reentry decision, a single close back inside = failure, period.
- 5-bar rule (incumbent): an event is "confirmed" iff it strings >= 5
  consecutive closes outside before terminating (it would fire at bar 5). Since
  an event is consecutive-closes-outside by construction, confirmed <=>
  n_closes_outside >= 5.

POINT-IN-TIME: the OR uses bars[:30]; the label uses the rest of the day (labels
may see the future — they are outcomes). Per-bar FEATURES (Phase 2) are strictly
prefix-only and harness-checked; nothing here is fed to the model as a feature.

Instrument: SPY (has volume). Tradeable window for evaluation: events starting
in [10:00, 12:00) (OR-finalized -> cutoff). Train broad (all-day), judge narrow.
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.loader import load_bars  # noqa: E402

OR_BARS = 30            # first 30 min form the OR; OR finalized at minute 30
CUTOFF_MIN = 330        # 15:00 ET — end of the trader's tradeable entry window
CONFIRM_BARS = 5        # the 5-bar incumbent
MIN_OR_BARS = 25        # need this many of the first 30 bars to trust the OR

# PROPOSED v2 locked-test boundary — TRADER TO RATIFY. Chosen so the locked test
# (>= this date) is fully covered by the recorded option chains (Dec 2024->),
# enabling the Phase-4 chain-based economic judge on the untouched test set,
# while leaving ~16 years for development.
V2_TEST_START = "2025-07-01"

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


def day_events(day, day_df):
    """All OR-breakout events for one day (both sides). day_df: that day's SPY
    1-min bars with a 'mod' column, ascending."""
    rng = opening_range(day_df)
    if rng is None:
        return []
    or_high, or_low = rng
    or_width = or_high - or_low
    post = day_df[day_df["mod"] >= OR_BARS]
    if len(post) == 0:
        return []
    last_mod = int(post["mod"].iloc[-1])
    events = []
    for side in SIDES:
        attempt = 0
        in_ep = False
        st_mod = st_close = st_vol = n_out = None
        for b in post.itertuples(index=False):
            outside = (b.close > or_high) if side == "up" else (b.close < or_low)
            if outside:
                if not in_ep:
                    in_ep = True
                    attempt += 1
                    st_mod, st_close = int(b.mod), float(b.close)
                    st_vol = float(b.volume)
                    n_out = 1
                else:
                    n_out += 1
            elif in_ep:                       # closed back inside/through -> FAIL
                events.append(_ev(day, side, attempt, st_mod, int(b.mod),
                                  n_out, False, or_high, or_low, or_width,
                                  st_close, st_vol))
                in_ep = False
        if in_ep:                             # never terminated -> held to EOD
            events.append(_ev(day, side, attempt, st_mod, last_mod, n_out,
                              True, or_high, or_low, or_width, st_close, st_vol))
    return events


def _ev(day, side, attempt, start_mod, end_mod, n_out, held, oh, ol, ow,
        st_close, st_vol):
    return {
        "day": day, "side": side, "attempt": attempt,
        "start_mod": start_mod, "end_mod": end_mod,
        "n_closes_outside": n_out, "held_to_eod": int(held),
        "confirmed_5bar": int(n_out >= CONFIRM_BARS),
        "tradeable": int(OR_BARS <= start_mod < CUTOFF_MIN),
        "or_high": oh, "or_low": ol, "or_width": ow,
        "start_close": st_close, "start_volume": st_vol,
    }


def build_events(start=None, end=None):
    """Build the full event table over SPY (full span unless bounded)."""
    spy = load_bars("SPY", start=start, end=end)
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
    economic judge runs there); they never enter the split. min_gap_days set
    conservatively for the planned multi-day features (finalize at Phase 2;
    the split's _assert_no_leak enforces it)."""
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
