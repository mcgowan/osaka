#!/usr/bin/env python3
"""Tasks 2.3 + 2.1b: label sanity statistics and stride sensitivity.

2.3: base no-touch rates per bucket vs the p_mkt baseline, monotonicity
across the grid, touch-time distributions, put/call asymmetry, regime
breakdown, trade-log cross-checks.
2.1b: rebuild one sample month at 1-min stride and confirm the 2.3
conclusions are stride-invariant.

Run:  .venv/bin/python analysis/label_stats.py
"""

import os
import sys
from statistics import median

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.labels import day_labels, load_labels  # noqa: E402
from data.loader import load_bars, load_calendar  # noqa: E402
from quant.conventions import SigmaAnchor, time_to_settle  # noqa: E402
from quant.pmkt import p_mkt  # noqa: E402

BUCKETS = ("lt05", "05-10", "10-15", "15-25", "gt25")
SAMPLE_MONTH = "2025-03"


def add_pmkt(df):
    # T via the canonical convention function (rule #5: never reinline)
    base = pd.to_datetime(df["day"]) + pd.Timedelta(hours=9, minutes=30)
    ts = base + pd.to_timedelta(df["minute"], unit="m")
    df = df.assign(T=[
        time_to_settle(t.to_pydatetime(), is_half_day=bool(h))
        for t, h in zip(ts, df["half_day"])
    ])
    df["pmkt"] = [
        p_mkt(r.strike, r.spot, r.sigma_anchor, r.T, r.side, r.minute)
        for r in df.itertuples()
    ]
    return df


def main():
    df = load_labels(stride=5)
    print(f"labels: {len(df):,} rows, {df['day'].nunique()} days, "
          f"pooled survival {df['survive'].mean():.3f}\n")

    # subsample for p_mkt comparison (itertuples over 850k is slow-ish)
    sub = df.sample(n=120_000, random_state=7)
    sub = add_pmkt(sub)

    print("=== survival by bucket: realized vs p_mkt baseline ===")
    print("    (seeded 120k-row subsample, not full-population: bucket means "
          "carry ~±0.01 sampling error; all other tables use the full table)")
    print(f"{'side':>4} {'bucket':>6} {'n':>7} {'realized':>9} {'p_mkt':>7} {'gap':>7}")
    for side in ("p", "c"):
        for b in BUCKETS:
            s = sub[(sub["side"] == side) & (sub["bucket"] == b)]
            if len(s) < 100:
                continue
            print(f"{side:>4} {b:>6} {len(s):7d} {s['survive'].mean():9.3f} "
                  f"{s['pmkt'].mean():7.3f} "
                  f"{s['survive'].mean() - s['pmkt'].mean():+7.3f}")

    print("\n=== monotonicity: survival by anchor (must be non-decreasing) ===")
    viol = 0
    for side in ("p", "c"):
        g = df[df["side"] == side].groupby("anchor")["survive"].mean()
        g = g.sort_index()
        print(f"  {side}: " + "  ".join(f"{a}:{v:.3f}" for a, v in g.items()))
        viol += sum(b < a - 1e-9 for a, b in zip(g.values, g.values[1:]))
    print(f"  violations: {viol}")

    print("\n=== touch timing (failed rows): minutes from entry to touch ===")
    f = df[df["survive"] == 0].assign(tt=lambda x: x["touch_min"] - x["minute"])
    print(f"  median {f['tt'].median():.0f} min, IQR "
          f"[{f['tt'].quantile(.25):.0f}..{f['tt'].quantile(.75):.0f}], "
          f"within 30 min: {(f['tt'] <= 30).mean():.0%}")

    print("\n=== put/call asymmetry (band of record) ===")
    for side in ("p", "c"):
        s = df[(df["side"] == side) & (df["bucket"] == "10-15")]
        print(f"  {side}: survival {s['survive'].mean():.3f}  "
              f"touched-but-recovered {((s['survive'] == 0) & (s['settle_beyond'] == 1)).mean():.3f}")

    print("\n=== regime breakdown (band of record, VIX1D anchor terciles) ===")
    from quant.conventions import regime
    df["vix"] = df["sigma_anchor"] * 100
    df["regime"] = df["vix"].map(regime)
    for name in ("calm", "mid", "elevated"):
        s = df[(df["bucket"] == "10-15") & (df["regime"] == name)]
        print(f"  {name}: n={len(s):6d} survival {s['survive'].mean():.3f}")

    print("\n=== trade-log cross-checks ===")
    sb = df[df["bucket"].isin(("05-10", "10-15"))]
    print(f"  no-touch rate at the trader's entry profile (05-15Δ buckets): "
          f"{sb['survive'].mean():.3f} - log realized ~0.80 at entries "
          f"(median entry p_mkt 0.814, 294 trades)")
    print(f"  settle-beyond rate same buckets: {sb['settle_beyond'].mean():.3f} "
          f"- log: 128/128 held-to-expiry trades settled safe")

    # ---- 2.1b stride sensitivity on one month ----
    print(f"\n=== 2.1b: stride sensitivity ({SAMPLE_MONTH}, 1-min vs 5-min) ===")
    cal = load_calendar()
    half = set(cal[cal["is_half_day"]]["date"].dt.strftime("%Y-%m-%d"))
    anchor = SigmaAnchor()
    spx = load_bars("SPX", start=f"{SAMPLE_MONTH}-01",
                    end=f"{SAMPLE_MONTH}-31")
    spx["day"] = spx["ts"].dt.strftime("%Y-%m-%d")
    rows1 = []
    for day, bars in spx.groupby("day"):
        rows1.extend(day_labels(bars.reset_index(drop=True), day,
                                anchor.sigma(day), day in half, 1))
    s1 = pd.DataFrame(rows1)
    s5 = df[df["day"].str.startswith(SAMPLE_MONTH)]
    print(f"{'side':>4} {'bucket':>6} {'5-min surv':>11} {'1-min surv':>11} {'diff':>7}")
    worst = 0.0
    for side in ("p", "c"):
        for b in BUCKETS:
            a = s5[(s5["side"] == side) & (s5["bucket"] == b)]["survive"].mean()
            o = s1[(s1["side"] == side) & (s1["bucket"] == b)]["survive"].mean()
            if pd.isna(a) or pd.isna(o):
                continue
            worst = max(worst, abs(a - o))
            print(f"{side:>4} {b:>6} {a:11.3f} {o:11.3f} {a - o:+7.3f}")
    print(f"  max abs diff: {worst:.3f} "
          f"({'stride-invariant' if worst < 0.02 else 'INVESTIGATE'})")


if __name__ == "__main__":
    main()
