"""v2 Phase 2 — feature library + event-bar table builder (OR-breakout conviction).

WORK IN PROGRESS: blocks 1-2 (breakout-state, volume/VWAP) are implemented; the
tape and multi-day/regime blocks, parquet persistence + load_master + a manifest
entry, and the signed spec sheet (docs/v2-feature-spec.md) land at Phase-2 close.
build() currently returns an in-memory DataFrame.

Each event is scored at its DECISION BAR = the close of the breakout bar (first
1-min close outside the OR). Information set at the decision bar: completed bars
0..start_idx of today (incl. the breakout bar) + completed prior days + day-
constant context. Features are strictly prefix-only; the truncation harness in
tests/test_breakout_features.py recomputes on physically truncated bars/days and
asserts exact equality (inviolable rule #1).

NORMALIZATION (v2 decision, 2026-06-14): price distances are normalized by ATR
(prior-completed 14-day ATR, a price fact available for ALL SPY history from
2008) or by OR width / ratios — NOT by the VIX1D implied move (VIX1D only exists
2023-04-26+, and normalizing by it would discard 15 years of the data advantage
that motivated SPY). VIX1D/VIX vol-regime features are recent-era extras (NaN
before 2023), never the core. Labels remain pure price facts (data/breakouts.py).

Hard exclusions (v2-plan §5): no eleuthera gate outputs (pressure/RSI/risk/5-bar
count), no option pricing/greeks, no raw price levels, no lookahead.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakouts import build_events, v2_split, _mod  # noqa: E402
from data.loader import load_bars  # noqa: E402

ATR_LOOKBACK_DAYS = 14   # prior-completed 14-day ATR
TRAIL_DAYS = 20          # trailing window for the same-time-of-day volume baseline
ATTEMPT_CAP = 4          # attempt# capped (4 = "4th or later"); rare tail
# The split embargo MUST cover the longest feature lookback so a CALIB/VALID
# day's window can't reach into TRAIN. Derived here (not hardcoded in the split)
# so it cannot drift out of sync as features are added; +5-day buffer for holidays.
MAX_FEATURE_LOOKBACK_DAYS = max(ATR_LOOKBACK_DAYS, TRAIL_DAYS)
EMBARGO_DAYS = MAX_FEATURE_LOOKBACK_DAYS + 5

# block 1 - breakout state (NEW); block 2 - volume/VWAP (NEW, SPY).
# Tape + multi-day/regime blocks are wired in a later step (tasks 19).
FEATURES = (
    # block 1 - breakout state
    "ext_atr", "ext_orw", "or_width_atr", "attempt_n", "minutes_since_open",
    "side_down",
    # block 2 - volume / participation
    "rel_vol_tod", "vol_surge", "vwap_dist_atr", "vol_trend",
)
KEYS = ("day", "side", "attempt", "start_mod")
TARGETS = ("reversed", "held_to_eod", "max_adverse_orw")


def _atr14_by_day(spy_daily):
    """prior-completed 14-day ATR per day (shift(1) = completed days only)."""
    d = spy_daily.reset_index(drop=True)
    tr = np.maximum(d["high"] - d["low"],
                    np.maximum((d["high"] - d["close"].shift()).abs(),
                               (d["low"] - d["close"].shift()).abs()))
    atr = tr.rolling(14).mean().shift(1)
    return dict(zip(d["ts"].dt.strftime("%Y-%m-%d"), atr))


def _tod_volume_baseline(vol_wide):
    """Trailing same-time-of-day median volume. vol_wide: DataFrame indexed by
    day, columns = minute-of-day, values = that bar's volume. Returns a same-
    shaped frame of the trailing TRAIL_DAYS median at each minute, using only
    PRIOR days (shift(1)) - secular-trend-safe (Phase-0 QA)."""
    return vol_wide.shift(1).rolling(TRAIL_DAYS, min_periods=5).median()


def breakout_state(ev, atr):
    """Block 1 - breakout-state features at the decision bar. ev: an event dict
    (data/breakouts). atr: today's prior-completed ATR (price)."""
    up = ev["side"] == "up"
    boundary = ev["or_high"] if up else ev["or_low"]
    ext = (ev["start_close"] - boundary) if up else (boundary - ev["start_close"])
    return {
        "ext_atr": ext / atr if atr and atr > 0 else np.nan,
        "ext_orw": ext / ev["or_width"] if ev["or_width"] > 0 else np.nan,
        "or_width_atr": ev["or_width"] / atr if atr and atr > 0 else np.nan,
        "attempt_n": float(min(ev["attempt"], ATTEMPT_CAP)),
        "minutes_since_open": float(ev["start_mod"]),
        "side_down": int(not up),
    }


def volume_vwap(o, h, l, c, v, si, atr, tod_base):
    """Block 2 - volume/VWAP at the decision bar (close of bar si, the breakout
    bar). PREFIX-ONLY: reads bars [:si+1] (through the breakout bar) only.
    tod_base = trailing same-time-of-day median volume for bar si's minute."""
    vol_bar = float(v[si])
    sess = v[:si + 1]
    # rel_vol_tod: breakout-bar volume vs its trailing same-minute baseline
    rel_tod = vol_bar / tod_base if tod_base and tod_base > 0 else np.nan
    # vol_surge: breakout-bar vs today's mean bar volume so far
    mean_sofar = float(sess.mean()) if len(sess) else np.nan
    surge = vol_bar / mean_sofar if mean_sofar and mean_sofar > 0 else np.nan
    # vol_trend: last-5-bar vs prior-session mean volume (rising participation)
    if si + 1 >= 10:
        recent = float(v[si - 4:si + 1].mean())
        earlier = float(v[:si - 4].mean())
        trend = recent / earlier if earlier > 0 else np.nan
    else:
        trend = np.nan
    # VWAP through the breakout bar; distance of the breakout close from it (ATR)
    typ = (h[:si + 1] + l[:si + 1] + c[:si + 1]) / 3.0
    vv = v[:si + 1]
    vwap = float((typ * vv).sum() / vv.sum()) if vv.sum() > 0 else np.nan
    vwap_dist = ((float(c[si]) - vwap) / atr
                 if atr and atr > 0 and not np.isnan(vwap) else np.nan)
    return {"rel_vol_tod": rel_tod, "vol_surge": surge,
            "vwap_dist_atr": vwap_dist, "vol_trend": trend}


def build(start="2008-01-01", end=None, _unlocked_full_span=False):
    """Assemble the event-bar table (one row per breakout event, scored at its
    decision bar) with the v2 features + targets. By default the loader truncates
    SPY at V2_TEST_START, so this is DEV-only; the Phase-4 chain judge passes
    _unlocked_full_span=True (sanctioned, rule #3)."""
    events = build_events(start=start, end=end,
                          _unlocked_full_span=_unlocked_full_span)
    spy = load_bars("SPY", start=start, end=end,
                    _unlocked_full_span=_unlocked_full_span)
    spy["mod"] = _mod(spy["ts"])
    spy["day"] = spy["ts"].dt.strftime("%Y-%m-%d")
    bars_by_day = {d: g for d, g in spy.groupby("day", sort=True)}

    spy_daily = load_bars("SPY", freq="1day",
                          _unlocked_full_span=_unlocked_full_span)
    atr_by_day = _atr14_by_day(spy_daily)

    # trailing same-time-of-day volume baseline (pivot day x minute, prior-day roll)
    vw = spy.pivot_table(index="day", columns="mod", values="volume",
                         aggfunc="first")
    tod_base = _tod_volume_baseline(vw)

    rows = []
    for day, ev_day in events.groupby("day", sort=True):
        bars = bars_by_day[day]
        mods = bars["mod"].to_numpy()
        o = bars["open"].to_numpy(); h = bars["high"].to_numpy()
        l = bars["low"].to_numpy(); c = bars["close"].to_numpy()
        v = bars["volume"].to_numpy()
        atr = atr_by_day.get(day, np.nan)
        for ev in ev_day.to_dict("records"):
            si = int(np.searchsorted(mods, ev["start_mod"]))
            if si >= len(mods) or mods[si] != ev["start_mod"]:
                continue                      # breakout bar not found (gap) - skip
            tb = (tod_base.at[day, ev["start_mod"]]
                  if ev["start_mod"] in tod_base.columns else np.nan)
            row = {"day": day, "side": ev["side"], "attempt": ev["attempt"],
                   "start_mod": ev["start_mod"], "tradeable": ev["tradeable"],
                   "reversed": ev["reversed"], "held_to_eod": ev["held_to_eod"],
                   "max_adverse_orw": ev["max_adverse_orw"]}
            row.update(breakout_state(ev, atr))
            row.update(volume_vwap(o, h, l, c, v, si, atr, tb))
            rows.append(row)

    df = pd.DataFrame(rows)
    forbidden = {"or_high", "or_low", "start_close", "spot", "strike"}
    assert not (forbidden & set(df.columns)), "raw level columns leaked (rule #4)"
    assert set(FEATURES) <= set(df.columns), "spec drift: missing features"
    assert set(KEYS) <= set(df.columns) and set(TARGETS) <= set(df.columns)
    return df


def split():
    """Day-clustered TRAIN/CALIB/VALID split for the v2 feature universe, with
    the embargo DERIVED from the longest feature lookback (EMBARGO_DAYS) so it
    can't drift below a feature's true reach as blocks are added."""
    return v2_split(min_gap_days=EMBARGO_DAYS)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="sample")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=None)
    a = ap.parse_args()
    df = build(start=a.start, end=a.end)
    print(f"{len(df):,} event-rows x {len(FEATURES)} features "
          f"({df['day'].min()}..{df['day'].max()})")
    print(df[list(FEATURES)].describe().T[["mean", "std", "min", "max"]].round(3))
