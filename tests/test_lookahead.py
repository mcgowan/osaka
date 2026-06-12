"""Truncation harness (task 3.3, NFR-1.1) - THE lookahead gate.

For sampled (day, minute) rows of the master table: recompute every feature
from PHYSICALLY TRUNCATED data (bars sliced to [:i+1]; daily frames sliced
to days strictly before d) and assert exact equality with the stored value.
Any feature that can see past its information set fails here.

Runs in CI on every change (workflow rule). Seeded; ~60 sampled rows across
the pre-boundary universe + dedicated day-edge cases.

Run: .venv/bin/python -m pytest tests/test_lookahead.py -q
"""

import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.features import FEATURES, load_master, minute_features  # noqa: E402
from data.labels import load_labels  # noqa: E402
from data.loader import load_bars, load_calendar  # noqa: E402
from quant.conventions import YEAR_SECONDS  # noqa: E402
from quant.distance import normalized_distance  # noqa: E402
from quant.pmkt import p_mkt  # noqa: E402

N_SAMPLE = 60
SEED = 23

MINUTE_FEATS = ("rv_ratio_today", "range_ratio", "range_pos", "gap_filled",
                "persist_count", "efficiency_ratio", "open_drive")


@pytest.fixture(scope="module")
def world():
    master = load_master(stride=5)
    labels = load_labels(stride=5)
    spx = load_bars("SPX", start="2023-01-01")
    spx["day"] = spx["ts"].dt.strftime("%Y-%m-%d")
    spx_daily = load_bars("SPX", freq="1day")
    spx_daily["day"] = spx_daily["ts"].dt.strftime("%Y-%m-%d")
    vix1d_daily = load_bars("VIX1D", freq="1day")
    vix1d_daily["day"] = vix1d_daily["ts"].dt.strftime("%Y-%m-%d")
    vix_daily = load_bars("VIX", freq="1day")
    vix_daily["day"] = vix_daily["ts"].dt.strftime("%Y-%m-%d")
    sample = master.sample(n=N_SAMPLE, random_state=SEED)
    # force day-edge coverage: earliest minutes carry the NaN policies
    edges = master[master["minute"] <= 10].sample(n=8, random_state=SEED)
    sample = pd.concat([sample, edges])
    return dict(master=master, labels=labels, spx=spx, spx_daily=spx_daily,
                vix1d_daily=vix1d_daily, vix_daily=vix_daily, sample=sample)


def eq(a, b, tol=1e-9):
    if isinstance(a, float) and np.isnan(a):
        return isinstance(b, float) and np.isnan(b) or pd.isna(b)
    if pd.isna(a):
        return pd.isna(b)
    return abs(float(a) - float(b)) <= tol


def test_minute_features_on_truncated_bars(world):
    """Exact equality between stored minute features and a recompute on
    bars physically cut at the query bar."""
    for r in world["sample"].itertuples():
        day_bars = world["spx"][world["spx"]["day"] == r.day]
        i = int(r.minute)
        trunc = day_bars.iloc[: i + 1]  # bar i present only for its open
        o = trunc["open"].to_numpy()
        h = trunc["high"].to_numpy()
        l = trunc["low"].to_numpy()
        c = trunc["close"].to_numpy()
        sigma = r.vix1d_anchor / 100.0
        session_min = 210 if r.is_half_day else 390
        t0 = session_min * 60 / YEAR_SECONDS
        m0 = sigma * math.sqrt(t0) * float(o[0])
        dd = world["spx_daily"]
        close_y = float(dd[dd["day"] < r.day]["close"].iloc[-1])
        mf = minute_features(o, h, l, c, i, m0, close_y, sigma)
        for k in MINUTE_FEATS:
            assert eq(mf[k], getattr(r, k)), (r.day, i, k, mf[k], getattr(r, k))


def test_day_features_from_completed_days_only(world):
    """Recompute day-level features with daily frames truncated strictly
    before the query day; assert equality with stored values."""
    days = world["sample"]["day"].unique()
    dd = world["spx_daily"]
    v1 = world["vix1d_daily"]
    vx = world["vix_daily"]
    for day in days:
        row = world["sample"][world["sample"]["day"] == day].iloc[0]
        past = dd[dd["day"] < day]
        prev = past.iloc[-1]
        tr = np.maximum(
            past["high"] - past["low"],
            np.maximum((past["high"] - past["close"].shift()).abs(),
                       (past["low"] - past["close"].shift()).abs()))
        atr14 = float(tr.iloc[-14:].mean())
        day_bars = world["spx"][world["spx"]["day"] == day]
        open0 = float(day_bars["open"].iloc[0])
        assert eq(row["gap_atr"], (open0 - prev["close"]) / atr14, 1e-6)
        assert eq(row["yest_close_pos"],
                  (prev["close"] - prev["low"]) / (prev["high"] - prev["low"]), 1e-6)
        assert eq(row["open_vs_yest_range"],
                  (open0 - (prev["high"] + prev["low"]) / 2) / atr14, 1e-6)
        v1p = v1[v1["day"] < day]
        vxp = vx[vx["day"] < day]
        assert eq(row["vix1d_anchor"], float(v1p["close"].iloc[-1]), 1e-9)
        assert eq(row["vix_term_ratio"],
                  float(v1p["close"].iloc[-1]) / float(vxp["close"].iloc[-1]), 1e-9)
        assert eq(row["vix1d_chg"],
                  math.log(float(v1p["close"].iloc[-1]) / float(v1p["close"].iloc[-2])),
                  1e-9)


def test_row_features_from_prefix(world):
    """D, pmkt, dist_pdh/pdl recomputed from the strike (labels join) and
    the truncated prefix's open(t)."""
    lab = world["labels"].set_index(["day", "minute", "side", "anchor"])
    for r in world["sample"].itertuples():
        lrow = lab.loc[(r.day, int(r.minute), r.side, r.anchor)]
        if isinstance(lrow, pd.DataFrame):
            lrow = lrow.iloc[0]
        day_bars = world["spx"][world["spx"]["day"] == r.day]
        open_i = float(day_bars["open"].iloc[int(r.minute)])
        assert eq(open_i, lrow["spot"], 1e-9)  # placement used the prefix open
        sigma = r.vix1d_anchor / 100.0
        session_min = 210 if r.is_half_day else 390
        t_rem = (session_min - int(r.minute)) * 60 / YEAR_SECONDS
        K = float(lrow["strike"])
        assert eq(r.D, normalized_distance(K, open_i, sigma, t_rem, r.side), 1e-3)
        assert eq(r.pmkt, p_mkt(K, open_i, sigma, t_rem, r.side, int(r.minute)),
                  1e-9)
        m_t = sigma * math.sqrt(t_rem) * open_i
        dd = world["spx_daily"]
        prev = dd[dd["day"] < r.day].iloc[-1]
        assert eq(r.dist_pdh, (float(prev["high"]) - open_i) / m_t, 1e-6)
        assert eq(r.dist_pdl, (float(prev["low"]) - open_i) / m_t, 1e-6)


def test_nan_policy_day_open_edges(world):
    m = world["master"]
    assert m[m["minute"] < 15]["rv_ratio_today"].isna().all()
    assert m[m["minute"] == 0]["range_ratio"].isna().all()
    assert m[m["minute"] < 10]["persist_count"].isna().all()
    assert m[m["minute"] < 15]["efficiency_ratio"].isna().all()
    # and the features defined-from-minute-0 are never NaN
    for k in ("open_drive", "gap_atr", "dist_pdh", "dist_pdl", "pmkt", "D"):
        assert m[k].notna().all(), k


def test_no_raw_levels_in_master(world):
    forbidden = {"spot", "strike", "closest_pts", "sigma_anchor",
                 "open", "high", "low", "close"}
    assert not (forbidden & set(world["master"].columns))
    assert set(FEATURES) <= set(world["master"].columns)
