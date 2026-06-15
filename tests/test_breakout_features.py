"""Tests for the v2 feature library (Phase 2): value checks + lookahead harness.

The lookahead harness recomputes each prefix-only feature on bar arrays
physically truncated at the decision bar and asserts exact equality - the
no-lookahead guarantee (inviolable rule #1)."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakout_features import (breakout_state, volume_vwap, build,  # noqa: E402
                                     FEATURES, _atr14_by_day, tape_features,
                                     _tod_volume_baseline, _multiday_context,
                                     _realized_vol_baseline)


def _ev(side="up", or_high=100.0, or_low=90.0, start_close=101.0, attempt=1,
        start_mod=40):
    return {"side": side, "or_high": or_high, "or_low": or_low,
            "or_width": or_high - or_low, "start_close": start_close,
            "attempt": attempt, "start_mod": start_mod}


def test_breakout_state_values():
    # up break, close 102 over OR_high 100, width 10, atr 5
    f = breakout_state(_ev(start_close=102.0), atr=5.0)
    assert abs(f["ext_atr"] - (2.0 / 5.0)) < 1e-9       # (102-100)/5
    assert abs(f["ext_orw"] - (2.0 / 10.0)) < 1e-9      # (102-100)/10
    assert abs(f["or_width_atr"] - (10.0 / 5.0)) < 1e-9
    assert f["side_down"] == 0 and f["attempt_n"] == 1.0
    # down break uses the low boundary, side flips
    g = breakout_state(_ev(side="down", start_close=88.0), atr=5.0)
    assert abs(g["ext_atr"] - (2.0 / 5.0)) < 1e-9       # (90-88)/5
    assert g["side_down"] == 1
    # attempt cap
    assert breakout_state(_ev(attempt=9), atr=5.0)["attempt_n"] == 4.0


def _rng(n, seed):
    g = np.random.RandomState(seed)
    c = 100 + np.cumsum(g.normal(0, 0.3, n))
    h = c + np.abs(g.normal(0, 0.1, n)); l = c - np.abs(g.normal(0, 0.1, n))
    o = np.concatenate(([100.0], c[:-1]))
    v = g.randint(500, 5000, n).astype(float)
    return o, h, l, c, v


def test_volume_vwap_is_prefix_only():
    # recompute at the same decision bar si on the FULL arrays vs arrays
    # truncated just past si; a prefix-only function must give identical output.
    o, h, l, c, v = _rng(120, seed=7)
    for si in (9, 30, 75, 119):
        full = volume_vwap(o, h, l, c, v, si, atr=2.0, tod_base=1500.0)
        k = si + 1
        trunc = volume_vwap(o[:k], h[:k], l[:k], c[:k], v[:k], si,
                            atr=2.0, tod_base=1500.0)
        for key in full:
            a, b = full[key], trunc[key]
            assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-12, \
                f"{key} not prefix-only at si={si}: {a} vs {b}"


def test_tape_features_is_prefix_only():
    o, h, l, c, v = _rng(120, seed=11)
    for si in (16, 40, 90, 119):
        full = tape_features(o, h, l, c, si, atr=2.0, close_y=99.5, rv_base=0.15)
        k = si + 1
        trunc = tape_features(o[:k], h[:k], l[:k], c[:k], si,
                              atr=2.0, close_y=99.5, rv_base=0.15)
        for key in full:
            a, b = full[key], trunc[key]
            assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-12, \
                f"{key} not prefix-only at si={si}"


def _synth_daily(n, seed):
    g = np.random.RandomState(seed)
    close = 100 + np.cumsum(g.normal(0, 1, n))
    return pd.DataFrame({"ts": pd.date_range("2020-01-01", periods=n, freq="D"),
                         "open": close + g.normal(0, 0.3, n),
                         "high": close + np.abs(g.normal(0, 0.5, n)),
                         "low": close - np.abs(g.normal(0, 0.5, n)), "close": close})


def test_multiday_context_is_prefix_only_day_level():
    # the N-day high/low levels + ATR for day t must be future-blind
    d = _synth_daily(40, seed=4)
    vix = d[["ts"]].assign(close=15.0)
    full = _multiday_context(d, vix, vix)
    days = d["ts"].dt.strftime("%Y-%m-%d").tolist()
    for cut in (25, 35):
        tr = _multiday_context(d.iloc[:cut], vix.iloc[:cut], vix.iloc[:cut])
        td = days[cut - 1]
        for key in ("hi20", "lo20", "atr14", "close_y", "close_y3"):
            a, b = full[td][key], tr[td][key]
            assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-9, \
                f"multiday {key} lookahead at {td}"


def test_realized_vol_baseline_is_prefix_only_day_level():
    g = np.random.RandomState(8)
    frames = []
    for di, day in enumerate(pd.date_range("2020-01-01", periods=40, freq="D")):
        c = 100 + np.cumsum(g.normal(0, 0.2, 30))
        frames.append(pd.DataFrame({"day": day.strftime("%Y-%m-%d"), "close": c}))
    spy = pd.concat(frames, ignore_index=True)
    full = _realized_vol_baseline(spy)
    days = sorted(spy["day"].unique())
    for cut in (25, 35):
        sub = spy[spy["day"].isin(days[:cut])]
        tr = _realized_vol_baseline(sub)
        td = days[cut - 1]
        a, b = full[td], tr[td]
        assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-12, \
            f"rv-baseline lookahead at {td}"


def test_vwap_distance_sign():
    # constant-volume bars rising monotonically -> close is above VWAP -> +dist
    n = 30
    c = np.linspace(100, 110, n); h = c + 0.1; l = c - 0.1
    o = np.concatenate(([100.0], c[:-1])); v = np.full(n, 1000.0)
    f = volume_vwap(o, h, l, c, v, n - 1, atr=2.0, tod_base=1000.0)
    assert f["vwap_dist_atr"] > 0


def test_atr_is_prefix_only_day_level():
    # the daily ATR for day t must not change when future days are removed
    g = np.random.RandomState(3)
    n = 40
    close = 100 + np.cumsum(g.normal(0, 1, n))
    d = pd.DataFrame({"ts": pd.date_range("2020-01-01", periods=n, freq="D"),
                      "open": close + g.normal(0, 0.3, n),
                      "high": close + np.abs(g.normal(0, 0.5, n)),
                      "low": close - np.abs(g.normal(0, 0.5, n)), "close": close})
    full = _atr14_by_day(d)
    days = d["ts"].dt.strftime("%Y-%m-%d").tolist()
    for cut in (20, 33):
        trunc = _atr14_by_day(d.iloc[:cut])
        td = days[cut - 1]                        # last day of the truncated span
        assert abs(full[td] - trunc[td]) < 1e-12, f"ATR lookahead at {td}"


def test_tod_volume_baseline_is_prefix_only_day_level():
    # the trailing same-time-of-day baseline for day t must be future-blind
    g = np.random.RandomState(5)
    idx = pd.date_range("2020-01-01", periods=40, freq="D").strftime("%Y-%m-%d")
    mins = [30, 60, 90]
    wide = pd.DataFrame(g.randint(100, 1000, (40, len(mins))).astype(float),
                        index=idx, columns=mins)
    full = _tod_volume_baseline(wide)
    for cut in (25, 35):
        trunc = _tod_volume_baseline(wide.iloc[:cut])
        td = idx[cut - 1]
        for m in mins:
            a, b = full.at[td, m], trunc.at[td, m]
            assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-9, \
                f"tod-baseline lookahead at {td}/{m}"


def test_build_smoke_has_features_and_label():
    df = build(start="2024-01-02", end="2024-01-31")
    assert len(df) > 0
    assert set(FEATURES) <= set(df.columns)
    assert df["reversed"].isin([0, 1]).all()
    # rule #4: no raw level columns leaked into the table
    assert not ({"or_high", "or_low", "start_close"} & set(df.columns))


def test_load_master_hash_verified_if_built():
    from data.breakout_features import MASTER_MANIFEST, load_master
    if not os.path.exists(MASTER_MANIFEST):
        pytest.skip("master not built (run breakout_features.py build)")
    df = load_master()                        # raises on hash mismatch
    assert set(FEATURES) <= set(df.columns)
    assert "reversed" in df.columns and len(df) > 0
