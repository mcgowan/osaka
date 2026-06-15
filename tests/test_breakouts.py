"""Tests for the v2 OR-breakout event-builder (Phase 1).

The close-based start / terminate / re-arm / attempt logic and the
adverse-reversal label (max_adverse_orw) are subtle; these pin them on
synthetic days."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakouts import (day_events, opening_range, OR_BARS, CUTOFF_MIN,  # noqa: E402
                            MIN_OR_BARS, REVERSAL_K)

OR_HIGH, OR_LOW = 100.0, 90.0


def make_day(post_closes, n_or=OR_BARS, start_mod=OR_BARS):
    """Synthetic day: n_or OR bars fixing OR=[90,100], then post bars at the
    given closes (highs/lows bracket the close so OR is unaffected)."""
    rows = [dict(mod=m, high=OR_HIGH, low=OR_LOW, close=95.0, volume=1000.0)
            for m in range(n_or)]
    for i, c in enumerate(post_closes):
        rows.append(dict(mod=start_mod + i, high=max(c, OR_HIGH) + 1,
                         low=min(c, OR_LOW) - 1, close=float(c), volume=1000.0))
    return pd.DataFrame(rows)


def _ev(events, side, attempt):
    return next(e for e in events if e["side"] == side and e["attempt"] == attempt)


def test_opening_range_and_too_few_bars():
    assert opening_range(make_day([105])) == (OR_HIGH, OR_LOW)
    assert opening_range(make_day([105], n_or=MIN_OR_BARS - 1)) is None


def test_single_breakout_holds_to_eod():
    ev = day_events("2020-01-02", make_day([105] * 100))
    ups = [e for e in ev if e["side"] == "up"]
    assert len(ups) == 1
    e = ups[0]
    assert e["attempt"] == 1 and e["held_to_eod"] == 1
    assert e["n_closes_outside"] == 100 and e["confirmed_5bar"] == 1
    assert not any(x["side"] == "down" for x in ev)


def test_fail_then_rearm_second_attempt_holds():
    # break up (3 closes), close back inside (fail), break up again (5, holds)
    ev = day_events("2020-01-02",
                    make_day([105, 105, 105, 95, 95, 105, 105, 105, 105, 105]))
    e1, e2 = _ev(ev, "up", 1), _ev(ev, "up", 2)
    assert e1["held_to_eod"] == 0 and e1["n_closes_outside"] == 3 \
        and e1["confirmed_5bar"] == 0
    assert e2["held_to_eod"] == 1 and e2["n_closes_outside"] == 5 \
        and e2["confirmed_5bar"] == 1


def test_close_through_terminates_up_and_starts_down():
    # up 2 bars, then a close BELOW the OR low (through the range)
    ev = day_events("2020-01-02", make_day([105, 105, 85, 85, 85]))
    up = _ev(ev, "up", 1)
    assert up["held_to_eod"] == 0 and up["n_closes_outside"] == 2
    down = _ev(ev, "down", 1)               # the through-close opens a down event
    assert down["n_closes_outside"] == 3 and down["held_to_eod"] == 1


def test_confirmed_requires_five_closes():
    ev4 = day_events("2020-01-02", make_day([105] * 4 + [95]))   # 4 then fail
    assert _ev(ev4, "up", 1)["confirmed_5bar"] == 0
    ev5 = day_events("2020-01-02", make_day([105] * 5 + [95]))   # 5 then fail
    assert _ev(ev5, "up", 1)["confirmed_5bar"] == 1


def test_max_adverse_excursion():
    # OR=[90,100] (width 10); break up, entry close=105, then dip to low 95,
    # recover. Adverse = entry(105) - min low AFTER entry(95) = 10 -> 1.0 OR-width.
    rows = [dict(mod=m, high=100.0, low=90.0, close=95.0, volume=1e3)
            for m in range(OR_BARS)]
    for i, (c, h, lo) in enumerate([(105, 107, 104), (105, 106, 95), (105, 108, 103)]):
        rows.append(dict(mod=OR_BARS + i, high=float(h), low=float(lo),
                         close=float(c), volume=1e3))
    e = _ev(day_events("2020-01-02", pd.DataFrame(rows)), "up", 1)
    assert abs(e["max_adverse_orw"] - 1.0) < 1e-9 and e["held_to_eod"] == 1
    # max_adverse_orw 1.0 >= REVERSAL_K (1.0) -> labeled reversed
    assert e["reversed"] == int(1.0 >= REVERSAL_K)
    # entry on the last bar of the day -> no forward path -> zero adverse
    last = day_events("2020-01-02", make_day([105], start_mod=380))
    le = _ev(last, "up", 1)
    assert le["max_adverse_orw"] == 0.0 and le["reversed"] == 0


def test_tradeable_flag_respects_cutoff():
    early = day_events("2020-01-02", make_day([105] * 3, start_mod=OR_BARS))
    assert _ev(early, "up", 1)["tradeable"] == 1
    late = day_events("2020-01-02", make_day([105] * 3, start_mod=CUTOFF_MIN + 5))
    assert _ev(late, "up", 1)["tradeable"] == 0
