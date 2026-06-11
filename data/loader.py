#!/usr/bin/env python3
"""Versioned data storage + the single load API (plan task 0.7).

ALL later code loads market data through this module. No pipeline code,
notebook, or experiment reads data/ib/*.csv or data/raw/* directly —
that is an architecture rule (CLAUDE.md), not a convention.

Build (re-run whenever raw CSVs change):
    .venv/bin/python data/loader.py build
        data/ib/*.csv  ->  data/processed/<SYMBOL>-<freq>.parquet
                           data/processed/manifest.json

    Cleaning applied to 1-min bars (documented in data/ib/README.md):
      - sort by timestamp, drop duplicate timestamps (keep first)
      - drop bars outside 09:30-15:59 ET (VIX GTH session etc.)
      - drop junk bars on days the market was closed (KNOWN_CLOSED_JUNK)
      - half days (RTH count 209-225) truncated at 12:59 ET
      - everything else preserved as-is; missing minutes stay missing

Load (the API):
    from data.loader import load_bars, load_manifest, trading_days
    spx = load_bars("SPX")                          # 1-min, full history
    vix = load_bars("VIX", start="2023-04-26")      # date-bounded
    spxd = load_bars("SPX", freq="1day")
    days = trading_days("SPX")                      # sorted list of dates

    load_bars verifies the parquet's sha256 against the manifest on every
    call, so silently regenerated/corrupted datasets fail loudly.

Note on the locked test set (inviolable rule #3): the final test period is
not yet defined (Phase 4 carves it). When it is, the date guard goes HERE,
in load_bars, plus the eval/final_eval.py allowlist — not in callers.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
IB_DIR = os.path.join(DATA_DIR, "ib")
OUT_DIR = os.path.join(DATA_DIR, "processed")
MANIFEST = os.path.join(OUT_DIR, "manifest.json")

LOADER_VERSION = 1
SYMBOLS = ("SPX", "VIX", "VIX1D")
FREQS = ("1min", "1day")
COLS = ["open", "high", "low", "close"]

RTH_START, RTH_END = "09:30", "15:59"     # inclusive bar labels, ET
HALF_DAY_RANGE = (209, 225)               # RTH bar count signature
HALF_DAY_END = "12:59"                    # truncation point for half days

# Days the exchange was closed but IB returns junk prints (see data/ib/README.md)
KNOWN_CLOSED_JUNK = {
    "SPX": {"2004-04-09", "2004-05-31", "2004-12-24", "2006-04-14"},
    "VIX": set(),
    "VIX1D": set(),
}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _read_raw(symbol, freq):
    path = os.path.join(IB_DIR, f"{symbol}-{freq}.csv")
    df = pd.read_csv(path, header=None, names=["ts"] + COLS,
                     parse_dates=["ts"])
    return df, path


def _clean_minute(df, symbol):
    df = df.sort_values("ts").drop_duplicates(subset="ts", keep="first")
    hhmm = df["ts"].dt.strftime("%H:%M")
    df = df[(hhmm >= RTH_START) & (hhmm <= RTH_END)]
    day = df["ts"].dt.strftime("%Y-%m-%d")
    df = df[~day.isin(KNOWN_CLOSED_JUNK[symbol])]

    # half-day truncation: identify by RTH bar-count signature
    day = df["ts"].dt.strftime("%Y-%m-%d")
    counts = day.map(day.value_counts())
    half = (counts >= HALF_DAY_RANGE[0]) & (counts <= HALF_DAY_RANGE[1])
    df = df[~(half & (df["ts"].dt.strftime("%H:%M") > HALF_DAY_END))]
    return df.reset_index(drop=True)


def _clean_daily(df, symbol):
    df = df.sort_values("ts").drop_duplicates(subset="ts", keep="first")
    day = df["ts"].dt.strftime("%Y-%m-%d")
    df = df[~day.isin(KNOWN_CLOSED_JUNK[symbol])]
    return df.reset_index(drop=True)


def build():
    os.makedirs(OUT_DIR, exist_ok=True)
    manifest = {
        "loader_version": LOADER_VERSION,
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "datasets": {},
    }
    for symbol in SYMBOLS:
        for freq in FREQS:
            src = os.path.join(IB_DIR, f"{symbol}-{freq}.csv")
            if not os.path.exists(src):
                if freq == "1day":
                    _build_derived_daily(symbol, manifest)
                else:
                    print(f"skip {symbol}-{freq}: no source file")
                continue
            df, src_path = _read_raw(symbol, freq)
            raw_rows = len(df)
            df = (_clean_minute if freq == "1min" else _clean_daily)(df, symbol)
            out = os.path.join(OUT_DIR, f"{symbol}-{freq}.parquet")
            df.to_parquet(out, index=False)
            manifest["datasets"][f"{symbol}-{freq}"] = {
                "source": os.path.relpath(src_path, DATA_DIR),
                "source_sha256": _sha256(src_path),
                "parquet_sha256": _sha256(out),
                "raw_rows": raw_rows,
                "rows": len(df),
                "first": str(df["ts"].iloc[0]),
                "last": str(df["ts"].iloc[-1]),
            }
            print(f"{symbol}-{freq}: {raw_rows:,} raw -> {len(df):,} clean rows "
                  f"({df['ts'].iloc[0]:%Y-%m-%d} .. {df['ts'].iloc[-1]:%Y-%m-%d})")
    _build_calendar(manifest)
    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"manifest -> {MANIFEST}")


def _third_friday(year, month):
    import calendar as _cal
    fridays = [d for d in range(1, 29)
               if datetime(year, month, d).weekday() == 4]
    return datetime(year, month, fridays[2]).strftime("%Y-%m-%d")


def _build_calendar(manifest):
    """Economic calendar table (plan task 0.3), one row per SPX trading day.

    Data-derived: is_half_day (bar-count signature of the cleaned 1-min SPX).
    Computed: monthly/quarterly OPEX (3rd Friday; quarterly = Mar/Jun/Sep/Dec).
    From data/raw/econ-events.csv ("date,kind" rows, kinds FOMC/CPI/NFP,
    sourced + verified per docs): event flags. Missing file -> flags all 0
    and a loud warning; the manifest records which mode was built.

    Events on non-trading days get no row (e.g. NFP released on Good Friday:
    2023-04-07, 2026-04-03). Deliberate: the flag means "scheduled intraday
    vol event TODAY"; a release while closed is Monday gap risk, a different
    phenomenon, not flagged in v1.
    """
    minute_pq = os.path.join(OUT_DIR, "SPX-1min.parquet")
    if not os.path.exists(minute_pq):
        print("skip calendar: no SPX-1min parquet")
        return
    df = pd.read_parquet(minute_pq)
    counts = df.groupby(df["ts"].dt.normalize()).size()
    cal = pd.DataFrame({"date": counts.index, "bars": counts.values})
    cal["is_half_day"] = cal["bars"] <= 250
    years = range(cal["date"].dt.year.min(), cal["date"].dt.year.max() + 1)
    opex_m = {_third_friday(y, m) for y in years for m in range(1, 13)}
    opex_q = {_third_friday(y, m) for y in years for m in (3, 6, 9, 12)}
    ds = cal["date"].dt.strftime("%Y-%m-%d")
    cal["is_opex_monthly"] = ds.isin(opex_m)
    cal["is_opex_quarterly"] = ds.isin(opex_q)

    events_csv = os.path.join(DATA_DIR, "raw", "econ-events.csv")
    for kind in ("FOMC", "CPI", "NFP"):
        cal[f"is_{kind.lower()}"] = False
    events_present = os.path.exists(events_csv)
    if events_present:
        ev = pd.read_csv(events_csv, header=None, names=["date", "kind"],
                         comment="#")
        for kind in ("FOMC", "CPI", "NFP"):
            days = set(ev[ev["kind"] == kind]["date"])
            cal[f"is_{kind.lower()}"] = ds.isin(days)
    else:
        print("WARNING: data/raw/econ-events.csv missing - "
              "calendar built with FOMC/CPI/NFP flags all false")

    cal = cal.drop(columns=["bars"])
    out = os.path.join(OUT_DIR, "calendar.parquet")
    cal.to_parquet(out, index=False)
    manifest["datasets"]["calendar"] = {
        "source": "derived from SPX-1min"
                  + (" + raw/econ-events.csv" if events_present else
                     " (NO econ-events.csv - event flags empty)"),
        "source_sha256": _sha256(events_csv) if events_present else None,
        "parquet_sha256": _sha256(out),
        "raw_rows": len(cal),
        "rows": len(cal),
        "first": str(cal["date"].iloc[0]),
        "last": str(cal["date"].iloc[-1]),
    }
    print(f"calendar: {len(cal):,} trading days "
          f"({int(cal['is_half_day'].sum())} half days, events "
          f"{'merged' if events_present else 'MISSING'})")


def _build_derived_daily(symbol, manifest):
    """Daily bars aggregated from the already-cleaned 1-min parquet, for
    symbols with no IB daily file (currently VIX1D). Marked derived in the
    manifest so a true IB daily download can replace it transparently."""
    minute_pq = os.path.join(OUT_DIR, f"{symbol}-1min.parquet")
    if not os.path.exists(minute_pq):
        print(f"skip {symbol}-1day: no 1-min parquet to derive from")
        return
    df = pd.read_parquet(minute_pq)
    day = df["ts"].dt.normalize()
    daily = df.groupby(day).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last")).reset_index()
    daily = daily.rename(columns={"ts": "ts"})[["ts"] + COLS]
    out = os.path.join(OUT_DIR, f"{symbol}-1day.parquet")
    daily.to_parquet(out, index=False)
    manifest["datasets"][f"{symbol}-1day"] = {
        "source": f"derived from {symbol}-1min",
        "source_sha256": _sha256(minute_pq),
        "parquet_sha256": _sha256(out),
        "raw_rows": len(df),
        "rows": len(daily),
        "first": str(daily["ts"].iloc[0]),
        "last": str(daily["ts"].iloc[-1]),
    }
    print(f"{symbol}-1day: derived {len(daily):,} daily bars from 1-min")


def load_manifest():
    with open(MANIFEST) as f:
        return json.load(f)


def load_bars(symbol, freq="1min", start=None, end=None):
    """Canonical bar access. Returns a DataFrame with columns
    [ts, open, high, low, close], ts ascending, ET timestamps, RTH only.
    start/end are inclusive 'YYYY-MM-DD' date bounds."""
    if symbol not in SYMBOLS or freq not in FREQS:
        raise ValueError(f"unknown dataset {symbol}-{freq}")
    key = f"{symbol}-{freq}"
    meta = load_manifest()["datasets"].get(key)
    if meta is None:
        raise FileNotFoundError(f"{key} not in manifest - run loader build")
    path = os.path.join(OUT_DIR, f"{key}.parquet")
    if _sha256(path) != meta["parquet_sha256"]:
        raise RuntimeError(
            f"{key}.parquet does not match the manifest hash - "
            f"rebuild via 'loader.py build' (never edit parquet in place)")
    df = pd.read_parquet(path)
    if start is not None:
        df = df[df["ts"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["ts"] < pd.Timestamp(end) + pd.Timedelta(days=1)]
    return df.reset_index(drop=True)


def load_calendar():
    """Per-trading-day calendar: date, is_half_day, is_opex_monthly,
    is_opex_quarterly, is_fomc, is_cpi, is_nfp. Hash-verified."""
    meta = load_manifest()["datasets"].get("calendar")
    if meta is None:
        raise FileNotFoundError("calendar not in manifest - run loader build")
    path = os.path.join(OUT_DIR, "calendar.parquet")
    if _sha256(path) != meta["parquet_sha256"]:
        raise RuntimeError("calendar.parquet does not match manifest hash")
    return pd.read_parquet(path)


def trading_days(symbol, freq="1min"):
    """Sorted list of 'YYYY-MM-DD' strings with data present."""
    df = load_bars(symbol, freq)
    return sorted(df["ts"].dt.strftime("%Y-%m-%d").unique())


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "build":
        build()
    else:
        print(__doc__)
