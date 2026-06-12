"""Feature library + master-table builder (Phase 3, tasks 3.2/3.5/3.6).

THE SPEC IS docs/feature-spec.md (SIGNED v1.1). 28 features; the FEATURES
tuple below must match the spec sheet exactly - drift is a bug and
tests/test_features.py asserts the column set.

Information set at query bar t (global convention): completed bars 0..t-1
of today + open(t) + completed prior days + day-constant anchors. Minute
features are computed by minute_features(), which receives the day's bar
arrays and index i and BY CONSTRUCTION only reads [:i] plus open[i]; the
truncation harness (tests/test_lookahead.py) recomputes on physically
truncated arrays and asserts exact equality.

Rule #4: no raw levels. The master table drops the labels parquet's raw
spot/strike/closest_pts; the auxiliary target closest_approach is
closest_pts normalized to remaining-implied-move units at assembly
(FR-3.3, task 3.5 watch-item).

Build:  .venv/bin/python data/features.py build [--stride 5]
        -> data/processed/master-<stride>min.parquet (+ entry in
           labels-manifest.json with provenance hashes)
Load:   from data.features import load_master
"""

import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.labels import LABELS_MANIFEST, _sha256, load_labels  # noqa: E402
from data.loader import OUT_DIR, load_bars, load_calendar, load_manifest  # noqa: E402
from quant.conventions import YEAR_SECONDS, SigmaAnchor  # noqa: E402
from quant.pmkt import p_mkt  # noqa: E402

SPEC_VERSION = "1.1"
ANNUALIZE_1MIN = math.sqrt(YEAR_SECONDS / 60.0)

FEATURES = (
    # block 1 - strike encoding
    "D", "side_c", "minutes_to_settle",
    # block 2 - clock & calendar
    "minutes_since_open", "day_of_week", "is_fomc", "is_cpi_nfp",
    "is_opex", "is_half_day",
    # block 3 - volatility state
    "vix1d_anchor", "vix1d_chg", "rv_ratio_today", "rv5d_ratio",
    "vix_term_ratio",
    # block 4 - today's tape
    "range_ratio", "range_pos", "gap_atr", "gap_filled", "persist_count",
    "efficiency_ratio", "open_drive",
    # block 5 - prior-day context (+ pmkt per task 3.6)
    "yest_close_pos", "open_vs_yest_range", "mom3d_atr",
    "yest_range_vs_implied", "dist_pdh", "dist_pdl", "pmkt",
)

KEYS = ("day", "minute", "side", "anchor", "bucket")
TARGETS = ("survive", "settle_beyond", "touch_min", "closest_approach")


def minute_features(o, h, l, c, i, m0, close_y, sigma):
    """Intraday tape + vol features at bar index i.

    PREFIX-ONLY CONTRACT: reads o[i] and slices [:i] of each array; never
    indexes beyond i. (o, h, l, c = the day's 1-min open/high/low/close
    numpy arrays; m0 = implied session move; close_y = yesterday's close;
    sigma = the day's anchor, decimal.)
    """
    open_i = float(o[i])
    out = {}

    # rv_ratio_today (#12): >=15 completed bars
    if i >= 15:
        r = np.diff(np.log(c[:i]))
        out["rv_ratio_today"] = float(r.std(ddof=1)) * ANNUALIZE_1MIN / sigma
    else:
        out["rv_ratio_today"] = np.nan

    # range features (#14, #15): extrema over completed bars + open(t)
    if i >= 1:
        hi = max(float(h[:i].max()), open_i)
        lo = min(float(l[:i].min()), open_i)
        rng = hi - lo
        out["range_ratio"] = rng / m0
        out["range_pos"] = (open_i - lo) / rng if rng >= 0.05 * m0 else np.nan
    else:
        out["range_ratio"] = np.nan
        out["range_pos"] = np.nan

    # gap_filled (#17): has the path touched yesterday's close
    gap = float(o[0]) - close_y
    if gap > 0:
        lo_all = min(float(l[:i].min()), open_i) if i >= 1 else open_i
        out["gap_filled"] = int(lo_all <= close_y)
    elif gap < 0:
        hi_all = max(float(h[:i].max()), open_i) if i >= 1 else open_i
        out["gap_filled"] = int(hi_all >= close_y)
    else:
        out["gap_filled"] = 1

    # 5-min rollup features (#18, #19) on completed bars only
    n5 = i // 5
    closes5 = c[4 : 5 * n5 : 5] if n5 >= 1 else np.array([])
    if n5 >= 2:
        d = np.diff(closes5)
        last = d[-1]
        if last == 0:
            out["persist_count"] = 0.0
        else:
            sign = np.sign(last)
            run = 0
            for x in d[::-1]:
                if np.sign(x) == sign:
                    run += 1
                else:
                    break
            out["persist_count"] = float(sign * run)
    else:
        out["persist_count"] = np.nan
    if n5 >= 3:
        moves = np.diff(np.concatenate(([float(o[0])], closes5)))
        path = float(np.abs(moves).sum())
        out["efficiency_ratio"] = (
            (float(closes5[-1]) - float(o[0])) / path if path > 0 else np.nan)
    else:
        out["efficiency_ratio"] = np.nan

    # open_drive (#20)
    out["open_drive"] = (open_i - float(o[0])) / m0
    return out


def _daily_context(spx_daily, vix1d_daily, vix_daily):
    """Per-day dict of completed-prior-day context. All values for day d use
    data through d-1 only."""
    sd = spx_daily.reset_index(drop=True)
    sd["day"] = sd["ts"].dt.strftime("%Y-%m-%d")
    tr = np.maximum(
        sd["high"] - sd["low"],
        np.maximum((sd["high"] - sd["close"].shift()).abs(),
                   (sd["low"] - sd["close"].shift()).abs()))
    atr14 = tr.rolling(14).mean().shift(1)  # completed days only

    v1 = {d: c for d, c in zip(
        vix1d_daily["ts"].dt.strftime("%Y-%m-%d"), vix1d_daily["close"])}
    vx = {d: c for d, c in zip(
        vix_daily["ts"].dt.strftime("%Y-%m-%d"), vix_daily["close"])}
    v1_days = sorted(v1)

    ctx = {}
    for j in range(1, len(sd)):
        d = sd["day"].iloc[j]
        prev = sd.iloc[j - 1]
        ctx[d] = {
            "close_y": float(prev["close"]),
            "high_y": float(prev["high"]),
            "low_y": float(prev["low"]),
            "open_y": float(prev["open"]),
            "close_y3": float(sd["close"].iloc[j - 4]) if j >= 4 else np.nan,
            "atr14": float(atr14.iloc[j]) if not np.isnan(atr14.iloc[j]) else np.nan,
            "prev_day": sd["day"].iloc[j - 1],
        }
        # VIX1D anchor of yesterday (= close of the day before yesterday)
        k = np.searchsorted(v1_days, sd["day"].iloc[j - 1])
        ctx[d]["anchor_y"] = (v1[v1_days[k - 1]] / 100.0
                              if 0 < k <= len(v1_days) else np.nan)
        ctx[d]["vix_prior_close"] = vx.get(sd["day"].iloc[j - 1], np.nan)
        ctx[d]["vix1d_prior_close"] = v1.get(sd["day"].iloc[j - 1], np.nan)
        ctx[d]["vix1d_2back"] = (v1[v1_days[k - 1]]
                                 if 0 < k <= len(v1_days) else np.nan)
    return ctx


def build(stride=5):
    labels = load_labels(stride=stride, _unlocked_full_span=True)  # builder
    cal = load_calendar().copy()
    cal["day"] = cal["date"].dt.strftime("%Y-%m-%d")
    cal_by_day = cal.set_index("day")

    spx = load_bars("SPX", start="2023-01-01", _unlocked_full_span=True)
    spx["day"] = spx["ts"].dt.strftime("%Y-%m-%d")
    bars_by_day = {d: g for d, g in spx.groupby("day")}

    spx_daily = load_bars("SPX", freq="1day", _unlocked_full_span=True)
    vix1d_daily = load_bars("VIX1D", freq="1day", _unlocked_full_span=True)
    vix_daily = load_bars("VIX", freq="1day", _unlocked_full_span=True)
    dctx = _daily_context(spx_daily, vix1d_daily, vix_daily)

    # per-day pooled 1-min return stats for rv5d
    day_list = sorted(bars_by_day)
    ret_stats = {}
    for d in day_list:
        r = np.diff(np.log(bars_by_day[d]["close"].to_numpy()))
        ret_stats[d] = (float((r ** 2).sum()), float(r.sum()), len(r))

    rows = []
    grouped = labels.groupby("day", sort=True)
    for day, lab in grouped:
        bars = bars_by_day[day]
        o = bars["open"].to_numpy()
        h = bars["high"].to_numpy()
        l = bars["low"].to_numpy()
        c = bars["close"].to_numpy()
        n = len(bars)
        ctxd = dctx[day]
        crow = cal_by_day.loc[day]
        sigma = float(lab["sigma_anchor"].iloc[0])
        session_min = 210 if bool(lab["half_day"].iloc[0]) else 390
        t0 = session_min * 60 / YEAR_SECONDS
        m0 = sigma * math.sqrt(t0) * float(o[0])

        # rv5d: pooled returns over the 5 completed sessions before today
        idx = day_list.index(day)
        prior5 = day_list[max(0, idx - 5):idx]
        if len(prior5) == 5:
            sq = sum(ret_stats[d][0] for d in prior5)
            s1 = sum(ret_stats[d][1] for d in prior5)
            cnt = sum(ret_stats[d][2] for d in prior5)
            var = (sq - s1 * s1 / cnt) / (cnt - 1)
            rv5d = math.sqrt(var) * ANNUALIZE_1MIN / sigma
        else:
            rv5d = np.nan

        m0_y = (ctxd["anchor_y"] * math.sqrt(t0) * ctxd["open_y"]
                if not np.isnan(ctxd["anchor_y"]) else np.nan)
        day_feats = {
            "day_of_week": pd.Timestamp(day).dayofweek,
            "is_fomc": int(crow["is_fomc"]),
            "is_cpi_nfp": int(crow["is_cpi"] or crow["is_nfp"]),
            "is_opex": int(crow["is_opex_monthly"]),
            "is_half_day": int(bool(lab["half_day"].iloc[0])),
            "vix1d_anchor": sigma * 100.0,
            "vix1d_chg": (math.log(ctxd["vix1d_prior_close"] / ctxd["vix1d_2back"])
                          if ctxd["vix1d_2back"] and not np.isnan(ctxd["vix1d_2back"])
                          else np.nan),
            "rv5d_ratio": rv5d,
            "vix_term_ratio": (ctxd["vix1d_prior_close"] / ctxd["vix_prior_close"]
                               if ctxd["vix_prior_close"] else np.nan),
            "gap_atr": (float(o[0]) - ctxd["close_y"]) / ctxd["atr14"],
            "yest_close_pos": ((ctxd["close_y"] - ctxd["low_y"])
                               / (ctxd["high_y"] - ctxd["low_y"])),
            "open_vs_yest_range": ((float(o[0]) - (ctxd["high_y"] + ctxd["low_y"]) / 2)
                                   / ctxd["atr14"]),
            "mom3d_atr": ((ctxd["close_y"] - ctxd["close_y3"]) / ctxd["atr14"]
                          if not np.isnan(ctxd["close_y3"]) else np.nan),
            "yest_range_vs_implied": ((ctxd["high_y"] - ctxd["low_y"]) / m0_y
                                      if m0_y and not np.isnan(m0_y) else np.nan),
        }

        mf_cache = {}
        for r in lab.itertuples():
            i = r.minute
            if i not in mf_cache:
                mf_cache[i] = minute_features(o, h, l, c, i, m0,
                                              ctxd["close_y"], sigma)
            mf = mf_cache[i]
            t_rem = (session_min - i) * 60 / YEAR_SECONDS
            m_t = sigma * math.sqrt(t_rem) * float(o[i])
            row = {
                "day": day, "minute": i, "side": r.side, "anchor": r.anchor,
                "bucket": r.bucket,
                "D": r.D, "side_c": int(r.side == "c"),
                "minutes_to_settle": session_min - i,
                "minutes_since_open": i,
                "dist_pdh": (ctxd["high_y"] - float(o[i])) / m_t,
                "dist_pdl": (ctxd["low_y"] - float(o[i])) / m_t,
                "pmkt": p_mkt(r.strike, r.spot, sigma, t_rem, r.side, i),
                "survive": r.survive, "settle_beyond": r.settle_beyond,
                "touch_min": r.touch_min,
                "closest_approach": r.closest_pts / m_t,
            }
            row.update(day_feats)
            row.update(mf)
            rows.append(row)

    df = pd.DataFrame(rows)
    # rule #4 assertion: no raw level columns in the master table
    forbidden = {"spot", "strike", "closest_pts", "sigma_anchor"}
    assert not (forbidden & set(df.columns)), "raw level columns leaked"
    assert set(FEATURES) <= set(df.columns), "spec drift: missing features"

    out = os.path.join(OUT_DIR, f"master-{stride}min.parquet")
    df.to_parquet(out, index=False)
    manifest = json.load(open(LABELS_MANIFEST))
    manifest[f"master-{stride}min"] = {
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "spec_version": SPEC_VERSION,
        "parquet_sha256": _sha256(out),
        "rows": len(df),
        "n_features": len(FEATURES),
        "source_labels_sha256": manifest[f"labels-{stride}min"]["parquet_sha256"],
    }
    with open(LABELS_MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"master-{stride}min: {len(df):,} rows x {len(FEATURES)} features")


def load_master(stride=5, _unlocked_full_span=False):
    """Hash-verified master table; pre-TEST_START only by default."""
    from data.loader import TEST_START
    meta = json.load(open(LABELS_MANIFEST)).get(f"master-{stride}min")
    if meta is None:
        raise FileNotFoundError("master table not built")
    if meta.get("spec_version") != SPEC_VERSION:
        raise RuntimeError("master built under a different feature spec - rebuild")
    path = os.path.join(OUT_DIR, f"master-{stride}min.parquet")
    if _sha256(path) != meta["parquet_sha256"]:
        raise RuntimeError("master parquet does not match manifest - rebuild")
    cur_labels = json.load(open(LABELS_MANIFEST))[f"labels-{stride}min"]
    if cur_labels["parquet_sha256"] != meta["source_labels_sha256"]:
        raise RuntimeError("master built from different labels - rebuild")
    df = pd.read_parquet(path)
    if not _unlocked_full_span:
        df = df[df["day"] < TEST_START].reset_index(drop=True)
    return df


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--stride", type=int, default=5)
    args = ap.parse_args()
    build(stride=args.stride)
