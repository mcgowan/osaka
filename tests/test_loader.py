"""Tests for the versioned load API (plan task 0.7).

Run: .venv/bin/python -m pytest tests/test_loader.py -q
Requires `data/loader.py build` to have been run (tests are read-only).
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.loader import (  # noqa: E402
    KNOWN_CLOSED_JUNK, load_bars, load_manifest, trading_days,
)


def test_manifest_lists_all_built_datasets():
    m = load_manifest()
    for key in ("SPX-1min", "VIX-1min", "VIX1D-1min",
                "SPX-1day", "VIX-1day", "VIX1D-1day"):
        assert key in m["datasets"], f"{key} missing from manifest"
        assert m["datasets"][key]["rows"] > 0


@pytest.mark.parametrize("symbol", ["SPX", "VIX", "VIX1D"])
def test_minute_bars_sorted_unique_rth(symbol):
    df = load_bars(symbol, start="2025-06-02", end="2025-06-06")
    assert df["ts"].is_monotonic_increasing
    assert not df["ts"].duplicated().any()
    hhmm = df["ts"].dt.strftime("%H:%M")
    assert (hhmm >= "09:30").all() and (hhmm <= "15:59").all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1)).all()
    assert (df["high"] >= df[["open", "close"]].max(axis=1)).all()


def test_spx_full_day_has_390_bars():
    df = load_bars("SPX", start="2025-06-02", end="2025-06-02")
    assert len(df) == 390
    assert df["ts"].iloc[0].strftime("%H:%M") == "09:30"
    assert df["ts"].iloc[-1].strftime("%H:%M") == "15:59"


def test_half_days_truncated_at_1259():
    # 2024-07-03 was a 13:00 ET close
    for symbol in ("SPX", "VIX", "VIX1D"):
        df = load_bars(symbol, start="2024-07-03", end="2024-07-03")
        assert len(df) > 0, f"{symbol} missing the 2024-07-03 half day"
        assert df["ts"].iloc[-1].strftime("%H:%M") <= "12:59", \
            f"{symbol} half day not truncated"


def test_closed_day_junk_dropped():
    for symbol, days in KNOWN_CLOSED_JUNK.items():
        for day in days:
            df = load_bars(symbol, start=day, end=day)
            assert len(df) == 0, f"{symbol} junk bars on closed day {day}"


def test_vix1d_universe_start():
    days = trading_days("VIX1D")
    assert days[0] == "2023-04-26"
    assert len(days) > 640  # pre-TEST_START span


def test_locked_test_period_guard():
    from data.loader import TEST_START
    # default loads stop strictly before the boundary
    df = load_bars("SPX")
    assert df["ts"].max() < pd.Timestamp(TEST_START)
    # even an explicit request for locked dates returns nothing
    df = load_bars("SPX", start="2026-01-05", end="2026-01-09")
    assert len(df) == 0
    # the sanctioned escape hatch serves the full span
    df = load_bars("SPX", start="2026-01-05", end="2026-01-09",
                   _unlocked_full_span=True)
    assert len(df) > 0


def test_labels_locked_guard():
    from data.labels import load_labels
    from data.loader import TEST_START
    df = load_labels(stride=5)
    assert df["day"].max() < TEST_START
    full = load_labels(stride=5, _unlocked_full_span=True)
    assert full["day"].max() > TEST_START
    assert len(full) > len(df)


def test_date_bounds_inclusive():
    df = load_bars("SPX", start="2025-06-02", end="2025-06-03")
    got = sorted(df["ts"].dt.strftime("%Y-%m-%d").unique())
    assert got == ["2025-06-02", "2025-06-03"]


def test_unknown_dataset_rejected():
    with pytest.raises(ValueError):
        load_bars("QQQ")        # not a configured symbol (SPY now is, for v2)
    with pytest.raises(ValueError):
        load_bars("SPX", freq="5min")


def test_calendar_flags():
    from data.loader import load_calendar
    cal = load_calendar().set_index(
        pd.to_datetime(load_calendar()["date"]).dt.strftime("%Y-%m-%d"))
    # 2024-06-12: famous CPI + FOMC double day
    assert bool(cal.loc["2024-06-12", "is_cpi"])
    assert bool(cal.loc["2024-06-12", "is_fomc"])
    # 2024-11-07: Thursday FOMC (election week)
    assert bool(cal.loc["2024-11-07", "is_fomc"])
    # half day + quarterly OPEX
    assert bool(cal.loc["2024-07-03", "is_half_day"])
    assert bool(cal.loc["2024-06-21", "is_opex_quarterly"])
    assert bool(cal.loc["2024-06-21", "is_opex_monthly"])
    assert not bool(cal.loc["2024-06-14", "is_opex_monthly"])
    # pre-2023 days exist but carry no event flags (scope: v1 universe)
    early = cal[cal.index < "2023-04-01"]
    assert len(early) > 4000
    assert not early[["is_fomc", "is_cpi", "is_nfp"]].any().any()
    # event counts match the compiled file (only events on days the data
    # already covers get rows; e.g. a future FOMC has no trading day yet)
    assert int(cal["is_fomc"].sum()) == 25  # 26 compiled, 2026-06-17 is future
    assert int(cal["is_cpi"].sum()) == 38
    # 38 compiled, but 2023-04-07 and 2026-04-03 were Good Friday releases
    # (market closed; BLS still publishes at 08:30 ET) - no trading-day row
    assert int(cal["is_nfp"].sum()) == 36


def test_hash_verification_catches_tampering(tmp_path, monkeypatch):
    import data.loader as L
    # point the loader at a copy whose parquet got modified post-manifest
    src = os.path.join(L.OUT_DIR, "VIX1D-1min.parquet")
    df = pd.read_parquet(src)
    df.iloc[0, 1] = 99999.0
    tampered_dir = tmp_path / "processed"
    tampered_dir.mkdir()
    df.to_parquet(tampered_dir / "VIX1D-1min.parquet", index=False)
    import shutil
    shutil.copy(L.MANIFEST, tampered_dir / "manifest.json")
    monkeypatch.setattr(L, "OUT_DIR", str(tampered_dir))
    monkeypatch.setattr(L, "MANIFEST", str(tampered_dir / "manifest.json"))
    with pytest.raises(RuntimeError, match="does not match the manifest"):
        L.load_bars("VIX1D")
