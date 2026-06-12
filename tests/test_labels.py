"""Label-engine tests (Phase 2, FR-3) on synthetic bars with known outcomes.

Run: .venv/bin/python -m pytest tests/test_labels.py -q
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.labels import day_labels  # noqa: E402

SIGMA = 0.12


def synth_bars(path, start="2026-01-05 09:30"):
    """1-min bars from a list of (open, high, low, close)."""
    ts = pd.date_range(start, periods=len(path), freq="min")
    return pd.DataFrame(
        {"ts": ts, "open": [p[0] for p in path], "high": [p[1] for p in path],
         "low": [p[2] for p in path], "close": [p[3] for p in path]})


def flat(n, px=6000.0):
    return [(px, px, px, px)] * n


def rows_for(bars, stride=5):
    return day_labels(bars, "2026-01-05", SIGMA, False, stride)


def test_flat_day_all_survive():
    rows = rows_for(synth_bars(flat(390)))
    assert rows, "no rows generated"
    assert all(r["survive"] == 1 for r in rows)
    assert all(r["settle_beyond"] == 1 for r in rows)
    assert all(r["touch_min"] == -1 for r in rows)
    # closest approach positive and equals |settle - K| on a flat path
    for r in rows:
        assert r["closest_pts"] > 0
        assert r["closest_pts"] == pytest.approx(abs(6000.0 - r["strike"]), abs=0.01)


def test_put_touch_detected_with_time_and_recovery():
    # flat at 6000, dips to 5947.5 low during minute 100, recovers
    path = flat(390)
    path[100] = (6000.0, 6000.0, 5947.5, 6000.0)
    rows = rows_for(synth_bars(path))
    for r in rows:
        if r["side"] != "p" or r["minute"] >= 100:
            continue
        if r["strike"] >= 5947.5:  # dip reaches at/through this strike
            assert r["survive"] == 0, r
            assert r["touch_min"] == 100
            assert r["closest_pts"] == pytest.approx(5947.5 - r["strike"], abs=0.01)
            assert r["settle_beyond"] == 1  # recovered -> touched-but-recovered
        else:
            assert r["survive"] == 1, r
    # entries after the dip never see it
    late_puts = [r for r in rows if r["side"] == "p" and r["minute"] > 100]
    assert late_puts and all(r["survive"] == 1 for r in late_puts)


def test_call_touch_and_settle_through():
    # grinds up through call strikes and stays there
    path = []
    for i in range(390):
        px = 6000.0 + (0.0 if i < 200 else 40.0)
        path.append((px, px, px, px))
    path[200] = (6000.0, 6040.0, 6000.0, 6040.0)
    rows = rows_for(synth_bars(path))
    for r in rows:
        if r["side"] != "c" or r["minute"] >= 200:
            continue
        if r["strike"] <= 6040.0:
            assert r["survive"] == 0
            assert r["touch_min"] == 200
            assert r["settle_beyond"] == (1 if r["strike"] > 6040.0 else 0)
        else:
            assert r["survive"] == 1
            assert r["settle_beyond"] == 1


def test_entry_bar_excluded_from_scan():
    # spec: scan interval is (t, settlement] - a dip INSIDE the entry bar
    # itself is not a touch. Entry minute 0 bar dips to 5900 then all flat.
    path = flat(390)
    path[0] = (6000.0, 6000.0, 5900.0, 6000.0)
    rows = rows_for(synth_bars(path))
    entry0_puts = [r for r in rows if r["side"] == "p" and r["minute"] == 0]
    assert entry0_puts
    assert all(r["survive"] == 1 for r in entry0_puts)


def test_no_entries_too_close_to_settlement():
    rows = rows_for(synth_bars(flat(390)), stride=5)
    assert max(r["minute"] for r in rows) <= 385
    rows1 = rows_for(synth_bars(flat(12)), stride=5)  # tiny session
    assert all(r["minute"] <= 12 - 5 for r in rows1)


def test_half_day_settlement_handling():
    bars = synth_bars(flat(210))
    rows = day_labels(bars, "2026-07-03", SIGMA, True, 5)
    assert rows
    assert all(r["half_day"] == 1 for r in rows)
    assert max(r["minute"] for r in rows) <= 205


def test_stride_one_supersets_stride_five_entries():
    path = flat(60)
    r5 = rows_for(synth_bars(path), stride=5)
    r1 = rows_for(synth_bars(path), stride=1)
    k5 = {(r["minute"], r["side"], r["strike"]) for r in r5}
    k1 = {(r["minute"], r["side"], r["strike"]) for r in r1}
    assert k5 <= k1
    # identical labels where entries coincide
    by_key1 = {(r["minute"], r["side"], r["strike"]): r["survive"] for r in r1}
    for r in r5:
        assert by_key1[(r["minute"], r["side"], r["strike"])] == r["survive"]
