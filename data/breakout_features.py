"""v2 Phase 2 — feature library + event-bar table builder (OR-breakout conviction).

Four feature blocks (26 features): breakout-state, volume/VWAP, today's tape,
multi-day/regime - see docs/v2-feature-spec.md. build() assembles the DEV table;
save_master/load_master persist it (hashed manifest). The signed spec sign-off
and the leakage-redteam pass are the Phase-2 gate items.

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

import json
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakouts import build_events, v2_split, _mod  # noqa: E402
from data.loader import MANIFEST, OUT_DIR, _sha256, load_bars  # noqa: E402
from quant.conventions import YEAR_SECONDS  # noqa: E402

SPEC_VERSION = "2.0"
MASTER_PATH = os.path.join(OUT_DIR, "breakout-master.parquet")
MASTER_MANIFEST = os.path.join(OUT_DIR, "breakout-master-manifest.json")

ATR_LOOKBACK_DAYS = 14   # prior-completed 14-day ATR
TRAIL_DAYS = 20          # trailing window for the same-time-of-day volume baseline
RV_TRAIL_DAYS = 20       # trailing window for the realized-vol baseline
NDAY_LEVELS = (5, 20)    # prior N-day high/low "key levels" (completed days)
ATTEMPT_CAP = 4          # attempt# capped (4 = "4th or later"); rare tail
ANNUALIZE_1MIN = math.sqrt(YEAR_SECONDS / 60.0)
# The split embargo MUST cover the longest feature lookback so a CALIB/VALID
# day's window can't reach into TRAIN. Derived here (not hardcoded in the split)
# so it cannot drift out of sync as features are added; +5-day buffer for holidays.
MAX_FEATURE_LOOKBACK_DAYS = max(ATR_LOOKBACK_DAYS, TRAIL_DAYS, RV_TRAIL_DAYS,
                                max(NDAY_LEVELS))
EMBARGO_DAYS = MAX_FEATURE_LOOKBACK_DAYS + 5

# block 1 - breakout state (NEW); block 2 - volume/VWAP (NEW, SPY).
# Tape + multi-day/regime blocks are wired in a later step (tasks 19).
FEATURES = (
    # block 1 - breakout state
    "ext_atr", "ext_orw", "or_width_atr", "attempt_n", "minutes_since_open",
    "side_down",
    # block 2 - volume / participation
    "rel_vol_tod", "vol_surge", "vwap_dist_atr", "vol_trend",
    # block 3 - today's tape (ATR-normalized)
    "range_atr", "range_pos", "open_drive", "efficiency_ratio", "persist_count",
    "rv_ratio", "gap_filled",
    # block 4 - multi-day / regime / key levels
    "gap_atr", "dist_pdh", "dist_pdl", "dist_hi20", "dist_lo20", "mom3d_atr",
    "yest_close_pos", "vix1d_anchor", "vix1d_chg",
)
KEYS = ("day", "side", "attempt", "start_mod")
TARGETS = ("reversed", "held_to_eod", "max_adverse_orw")


def _series_atr14(daily):
    """prior-completed N-day ATR as a Series aligned to daily's reset index
    (shift(1) = completed days only)."""
    d = daily.reset_index(drop=True)
    tr = np.maximum(d["high"] - d["low"],
                    np.maximum((d["high"] - d["close"].shift()).abs(),
                               (d["low"] - d["close"].shift()).abs()))
    return tr.rolling(ATR_LOOKBACK_DAYS).mean().shift(1)


def _atr14_by_day(spy_daily):
    """prior-completed 14-day ATR per day, as {day: atr}."""
    d = spy_daily.reset_index(drop=True)
    return dict(zip(d["ts"].dt.strftime("%Y-%m-%d"), _series_atr14(d)))


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


def tape_features(o, h, l, c, si, atr, close_y, rv_base):
    """Block 3 - today's tape at the decision bar (close of breakout bar si).
    PREFIX-ONLY: reads bars [:si+1] (through the breakout bar). ATR-normalized
    (price fact, all-history) - adapted from v1 minute_features which used the
    VIX1D implied move. rv_base = trailing realized-vol baseline (decimal)."""
    px = float(c[si]); day_open = float(o[0])
    hi = float(np.max(h[:si + 1])); lo = float(np.min(l[:si + 1]))
    rng = hi - lo
    out = {
        "range_atr": rng / atr if atr and atr > 0 else np.nan,
        "range_pos": (px - lo) / rng if rng > 1e-9 else np.nan,
        "open_drive": (px - day_open) / atr if atr and atr > 0 else np.nan,
    }
    if si >= 2:
        d = np.diff(c[:si + 1])
        path = float(np.abs(d).sum())
        out["efficiency_ratio"] = (px - day_open) / path if path > 0 else np.nan
        last = d[-1]
        if last == 0:
            out["persist_count"] = 0.0
        else:
            sgn = np.sign(last); run = 0
            for x in d[::-1]:
                if np.sign(x) == sgn:
                    run += 1
                else:
                    break
            out["persist_count"] = float(sgn * run)
    else:
        out["efficiency_ratio"] = np.nan
        out["persist_count"] = np.nan
    if si >= 15:
        r = np.diff(np.log(c[:si + 1]))
        rv = float(r.std(ddof=1)) * ANNUALIZE_1MIN
        out["rv_ratio"] = rv / rv_base if rv_base and rv_base > 0 else np.nan
    else:
        out["rv_ratio"] = np.nan
    gap = day_open - close_y
    if gap > 0:
        out["gap_filled"] = int(lo <= close_y)
    elif gap < 0:
        out["gap_filled"] = int(hi >= close_y)
    else:
        out["gap_filled"] = 1
    return out


def _multiday_context(spy_daily, vix1d_daily, vix_daily):
    """Per-day completed-prior-day context (all values for day d use data
    through d-1 only). ATR/level distances normalized downstream by ATR. VIX1D
    regime is recent-era (NaN before the VIX1D universe, ~2023-04)."""
    d = spy_daily.reset_index(drop=True)
    day = d["ts"].dt.strftime("%Y-%m-%d")
    atr = _series_atr14(d)
    hi_n = {n: d["high"].rolling(n).max().shift(1) for n in NDAY_LEVELS}
    lo_n = {n: d["low"].rolling(n).min().shift(1) for n in NDAY_LEVELS}
    v1 = dict(zip(vix1d_daily["ts"].dt.strftime("%Y-%m-%d"), vix1d_daily["close"]))
    vx = dict(zip(vix_daily["ts"].dt.strftime("%Y-%m-%d"), vix_daily["close"]))
    ctx = {}
    for j in range(1, len(d)):
        dd = day.iloc[j]; prev = d.iloc[j - 1]; pday = day.iloc[j - 1]
        ctx[dd] = {
            "close_y": float(prev["close"]), "high_y": float(prev["high"]),
            "low_y": float(prev["low"]),
            "close_y3": float(d["close"].iloc[j - 4]) if j >= 4 else np.nan,
            "atr14": float(atr.iloc[j]) if not np.isnan(atr.iloc[j]) else np.nan,
            "hi20": float(hi_n[20].iloc[j]), "lo20": float(lo_n[20].iloc[j]),
            "vix1d_prior": v1.get(pday, np.nan),
            "vix1d_2back": (v1.get(day.iloc[j - 2]) if j >= 2 else np.nan),
            "vix_prior": vx.get(pday, np.nan),
        }
    return ctx


def multiday_features(px, day_open, ctx):
    """Block 4 - multi-day / regime at the decision price px (the breakout close)
    on day with completed-prior context ctx. ATR-normalized; NaN-safe."""
    atr = ctx["atr14"]
    inv = (1.0 / atr) if atr and atr > 0 and not np.isnan(atr) else np.nan
    yr = ctx["high_y"] - ctx["low_y"]
    v1p, v2b = ctx["vix1d_prior"], ctx["vix1d_2back"]
    return {
        "gap_atr": (day_open - ctx["close_y"]) * inv,
        "dist_pdh": (ctx["high_y"] - px) * inv,
        "dist_pdl": (px - ctx["low_y"]) * inv,
        "dist_hi20": (ctx["hi20"] - px) * inv,
        "dist_lo20": (px - ctx["lo20"]) * inv,
        "mom3d_atr": ((ctx["close_y"] - ctx["close_y3"]) * inv
                      if not np.isnan(ctx["close_y3"]) else np.nan),
        "yest_close_pos": (ctx["close_y"] - ctx["low_y"]) / yr if yr > 1e-9 else np.nan,
        "vix1d_anchor": v1p if v1p and not np.isnan(v1p) else np.nan,
        "vix1d_chg": (math.log(v1p / v2b)
                      if v1p and v2b and not np.isnan(v1p) and not np.isnan(v2b)
                      else np.nan),
    }


def _realized_vol_baseline(spy):
    """Trailing RV_TRAIL_DAYS median of each day's intraday realized vol
    (annualized, decimal), prior-day only. Returns {day: rv_base}."""
    rv = spy.groupby("day")["close"].apply(
        lambda s: float(np.diff(np.log(s.to_numpy())).std(ddof=1)) * ANNUALIZE_1MIN
        if len(s) > 2 else np.nan)
    base = rv.shift(1).rolling(RV_TRAIL_DAYS, min_periods=5).median()
    return base.to_dict()


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
    vix1d_daily = load_bars("VIX1D", freq="1day",
                            _unlocked_full_span=_unlocked_full_span)
    vix_daily = load_bars("VIX", freq="1day",
                          _unlocked_full_span=_unlocked_full_span)
    mctx = _multiday_context(spy_daily, vix1d_daily, vix_daily)
    rv_base = _realized_vol_baseline(spy)

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
        ctx = mctx.get(day)
        if ctx is None:
            continue                          # first trading day - no prior context
        day_open = float(o[0])
        rvb = rv_base.get(day, np.nan)
        for ev in ev_day.to_dict("records"):
            si = int(np.searchsorted(mods, ev["start_mod"]))
            if si >= len(mods) or mods[si] != ev["start_mod"]:
                continue                      # breakout bar not found (gap) - skip
            tb = (tod_base.at[day, ev["start_mod"]]
                  if ev["start_mod"] in tod_base.columns else np.nan)
            row = {"day": day, "side": ev["side"], "attempt": ev["attempt"],
                   "start_mod": ev["start_mod"], "tradeable": ev["tradeable"],
                   "reversed": ev["reversed"], "held_to_eod": ev["held_to_eod"],
                   "max_adverse_orw": ev["max_adverse_orw"],
                   # benchmark only (NOT a feature): the 5-bar rule's greenlight
                   "confirmed_5bar": ev["confirmed_5bar"]}
            row.update(breakout_state(ev, atr))
            row.update(volume_vwap(o, h, l, c, v, si, atr, tb))
            row.update(tape_features(o, h, l, c, si, atr, ctx["close_y"], rvb))
            row.update(multiday_features(float(c[si]), day_open, ctx))
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


def walk_forward_oof(master, fit_predict, label="pred", split_obj=None, **fold_kw):
    """v2 walk-forward OOF over the TRAIN folds, with the fold embargo DERIVED
    from the feature lookbacks (EMBARGO_DAYS) so it CANNOT inherit the smaller
    v1 default in models.oof/splits (leakage-redteam MEDIUM, 2026-06-14: a 5-day
    fold gap would let a val day's 20-day feature window read into the fit block).
    Use THIS for v2 modeling, never the raw models.oof helper."""
    from models import oof as _oof
    fold_kw.setdefault("min_gap_days", EMBARGO_DAYS)
    return _oof.walk_forward_oof(master, split_obj or split(), fit_predict,
                                 label=label, **fold_kw)


def save_master(df):
    """Persist the DEV master table + a hashed manifest (provenance: the loader
    manifest hash, spec version, feature list, row count)."""
    df.to_parquet(MASTER_PATH, index=False)
    man = {
        "spec_version": SPEC_VERSION,
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "loader_manifest_sha256": _sha256(MANIFEST),
        "n_rows": int(len(df)), "n_features": len(FEATURES),
        "n_days": int(df["day"].nunique()), "features": list(FEATURES),
        "day_span": [df["day"].min(), df["day"].max()],
        "parquet_sha256": _sha256(MASTER_PATH),
    }
    json.dump(man, open(MASTER_MANIFEST, "w"), indent=2)
    return man


def load_master():
    """Load the persisted DEV master, hash-verified against its manifest."""
    if not os.path.exists(MASTER_MANIFEST):
        raise FileNotFoundError(
            "breakout-master not built - run 'breakout_features.py build'")
    man = json.load(open(MASTER_MANIFEST))
    if _sha256(MASTER_PATH) != man["parquet_sha256"]:
        raise RuntimeError(
            "breakout-master.parquet does not match its manifest hash - rebuild")
    return pd.read_parquet(MASTER_PATH)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="sample",
                    help="build = full DEV table -> parquet+manifest; "
                         "sample = print a date-bounded summary")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=None)
    a = ap.parse_args()
    if a.cmd == "build":
        df = build()                          # full DEV span (loader self-locks)
        man = save_master(df)
        print(f"breakout-master: {man['n_rows']:,} rows x {man['n_features']} "
              f"features over {man['n_days']:,} days {man['day_span']}")
    else:
        df = build(start=a.start, end=a.end)
        print(f"{len(df):,} event-rows x {len(FEATURES)} features "
              f"({df['day'].min()}..{df['day'].max()})")
        print(df[list(FEATURES)].describe().T[["mean", "std", "min", "max"]].round(3))
