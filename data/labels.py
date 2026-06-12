"""Label engine + labels table builder (Phase 2, FR-3).

THE LABEL ENGINE IS SIGMA-FREE. Given (bars, entry index, strike, side) it
computes pure price facts:
    survive       1 if SPX never trades at/through K in (t, settlement]
    touch_min     minutes-since-open of the first touching bar (-1 if none)
    closest_pts   signed min distance from the path to K over (t, settlement],
                  in RAW POINTS (puts: min(low)-K; calls: K-max(high);
                  negative = touched/through). Normalization to implied-move
                  units happens at feature-table assembly (Phase 3), where
                  sigma already lives - NOT here (label-purity rule).
    settle_beyond 1 if the settlement proxy is on the safe side of K

Conventions (docs/requirements.md Section 3 + FR-3):
- Scan interval is (t, settlement]: bars STRICTLY AFTER the entry bar, per
  the spec text. Entry is at the open of bar t; an intra-minute touch
  during bar t itself is therefore not a failure by definition. Flagged for
  task 2.4 - if spot-validation disagreements cluster on this boundary,
  revisit via the documented-deviation route.
- Settlement proxy = the session's last 1-min bar close (15:59 ET, or
  12:59 ET on half days), not the official SPXW print (can differ by
  tenths of a point; documented in task 2.4's tolerance).

Strike placement (sigma-dependent, deliberately OUTSIDE the engine) uses
the frozen Phase-1 grid: quant.distance.place_grid with the prior-close
VIX1D anchor.

Build:  .venv/bin/python data/labels.py build [--stride 5]
        -> data/processed/labels-<stride>min.parquet + labels-manifest.json
        (own manifest with provenance hashes of the source parquets; the
        loader's manifest covers raw->clean, this covers clean->labels)

Load:   from data.labels import load_labels; df = load_labels(stride=5)
        Hash-verified against labels-manifest.json on every load.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.loader import OUT_DIR, load_bars, load_calendar, load_manifest  # noqa: E402
from quant.conventions import SigmaAnchor, delta_bucket, time_to_settle  # noqa: E402
from quant.distance import place_grid  # noqa: E402

LABELS_MANIFEST = os.path.join(OUT_DIR, "labels-manifest.json")
MIN_FORWARD_MIN = 5   # entries need at least this many minutes to settlement
ENGINE_VERSION = 1    # bump on ANY day_labels logic change; load_labels checks it


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def day_labels(bars, day, sigma, is_half_day, stride):
    """Label rows for one day. bars: DataFrame (ts, open, high, low, close),
    one session, 1-min, sorted. Returns list of dicts."""
    n = len(bars)
    ts = bars["ts"].to_numpy()
    # session-integrity asserts (redteam F4): the 'minute' column equals
    # minutes-since-09:30 only on gapless sessions starting at the open
    first = pd.Timestamp(ts[0])
    if (first.hour, first.minute) != (9, 30):
        raise ValueError(f"{day}: session starts {first}, not 09:30 ET")
    if not (pd.Series(ts).diff().dropna() == pd.Timedelta(minutes=1)).all():
        raise ValueError(f"{day}: session has missing/duplicate minutes")
    opens = bars["open"].to_numpy()
    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    settle = float(bars["close"].iloc[-1])

    # suffix extrema over bars i..end (computed once, sliced as i+1..)
    suf_min_low = np.minimum.accumulate(lows[::-1])[::-1]
    suf_max_high = np.maximum.accumulate(highs[::-1])[::-1]

    # first-touch lookup: for strike K and entry i, first j > i with
    # lows[j] <= K (puts) / highs[j] >= K (calls); cached per (K, side)
    touch_cache = {}

    def first_touch(K, side, i):
        key = (K, side)
        if key not in touch_cache:
            hit = lows <= K if side == "p" else highs >= K
            touch_cache[key] = np.flatnonzero(hit)
        idx = touch_cache[key]
        pos = np.searchsorted(idx, i + 1)
        return int(idx[pos]) if pos < len(idx) else None

    first_ts = pd.Timestamp(ts[0])
    rows = []
    for i in range(0, n - 1, stride):
        bar_ts = pd.Timestamp(ts[i])
        minutes_to_settle = (n - i)  # bars remaining incl. this one
        if minutes_to_settle < MIN_FORWARD_MIN:
            break
        S = float(opens[i])
        T = time_to_settle(bar_ts.to_pydatetime(), is_half_day=is_half_day)
        m_open = int((bar_ts - first_ts).total_seconds() // 60)
        for side in ("p", "c"):
            for anchor, K, D in place_grid(S, sigma, T, side):
                if side == "p":
                    touched = suf_min_low[i + 1] <= K
                    closest = float(suf_min_low[i + 1] - K)
                    s_beyond = settle > K
                else:
                    touched = suf_max_high[i + 1] >= K
                    closest = float(K - suf_max_high[i + 1])
                    s_beyond = settle < K
                j = first_touch(K, side, i) if touched else None
                rows.append({
                    "day": day, "minute": m_open, "side": side,
                    "anchor": anchor, "strike": K, "D": round(D, 4),
                    "bucket": delta_bucket(D, side),
                    "spot": S, "sigma_anchor": sigma,
                    "survive": int(not touched),
                    "touch_min": int((pd.Timestamp(ts[j]) - first_ts)
                                     .total_seconds() // 60) if j is not None else -1,
                    "closest_pts": round(closest, 2),
                    "settle_beyond": int(s_beyond),
                    "half_day": int(is_half_day),
                })
    return rows


def build(stride=5):
    cal = load_calendar()
    half = set(cal[cal["is_half_day"]]["date"].dt.strftime("%Y-%m-%d"))
    # builder is a sanctioned full-span reader: it WRITES the locked-period
    # labels without analyzing them (rule #3; see loader docstring)
    anchor = SigmaAnchor(_unlocked_full_span=True)
    spx = load_bars("SPX", start="2023-04-26", _unlocked_full_span=True)
    spx["day"] = spx["ts"].dt.strftime("%Y-%m-%d")

    all_rows, skipped = [], {}
    for day, bars in spx.groupby("day"):
        try:
            sigma = anchor.sigma(day)
        except KeyError:
            skipped[day] = "no prior VIX1D close"
            continue
        try:
            all_rows.extend(day_labels(bars.reset_index(drop=True), day,
                                       sigma, day in half, stride))
        except ValueError as e:
            # session-integrity failure (gap / late open): skip-and-log so
            # one bad day cannot abort the whole build
            skipped[day] = str(e)
    if skipped:
        print(f"skipped {len(skipped)} day(s):")
        for d, why in skipped.items():
            print(f"  {d}: {why}")
    df = pd.DataFrame(all_rows)
    out = os.path.join(OUT_DIR, f"labels-{stride}min.parquet")
    df.to_parquet(out, index=False)

    src = load_manifest()["datasets"]
    manifest = {}
    if os.path.exists(LABELS_MANIFEST):
        manifest = json.load(open(LABELS_MANIFEST))
    manifest[f"labels-{stride}min"] = {
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "engine_version": ENGINE_VERSION,
        "parquet_sha256": _sha256(out),
        "rows": len(df),
        "days": int(df["day"].nunique()),
        "stride": stride,
        "skipped_days": skipped,
        "source_spx_sha256": src["SPX-1min"]["parquet_sha256"],
        "source_vix1d_sha256": src["VIX1D-1day"]["parquet_sha256"],
        "source_calendar_sha256": src["calendar"]["parquet_sha256"],
        "first": df["day"].min(), "last": df["day"].max(),
    }
    with open(LABELS_MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"labels-{stride}min: {len(df):,} rows over {df['day'].nunique()} days "
          f"({df['day'].min()}..{df['day'].max()})")
    print(f"survival base rate: {df['survive'].mean():.3f}")


def load_labels(stride=5, _unlocked_full_span=False):
    """Hash- and provenance-verified labels access. Rows on/after the
    locked TEST_START are excluded unless _unlocked_full_span=True
    (sanctioned callers only - see data/loader.py docstring; rule #3)."""
    from data.loader import TEST_START
    meta = json.load(open(LABELS_MANIFEST)).get(f"labels-{stride}min")
    if meta is None:
        raise FileNotFoundError(f"labels-{stride}min not built")
    if meta.get("engine_version") != ENGINE_VERSION:
        raise RuntimeError(
            f"labels built with engine v{meta.get('engine_version')}, "
            f"code is v{ENGINE_VERSION} - rebuild")
    path = os.path.join(OUT_DIR, f"labels-{stride}min.parquet")
    if _sha256(path) != meta["parquet_sha256"]:
        raise RuntimeError("labels parquet does not match manifest - rebuild")
    src = load_manifest()["datasets"]
    for key, mkey in (("SPX-1min", "source_spx_sha256"),
                      ("VIX1D-1day", "source_vix1d_sha256"),
                      ("calendar", "source_calendar_sha256")):
        if src[key]["parquet_sha256"] != meta[mkey]:
            raise RuntimeError(
                f"labels were built from a different {key} dataset - rebuild")
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
