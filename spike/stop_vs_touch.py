#!/usr/bin/env python3
"""Gate S decision input: how different are the three failure definitions?

For hypothetical short-strike entries on the recorded chain day (2026-06-01),
measure — from the chain's own recorded deltas and the SPX bars — whether and
when each of these fired:

  A. delta-breach: the strike's recorded |delta| reaches delta* (0.30)
     [the current requirements' label = proxy for a 2-3x credit stop]
  B. touch: SPX trades at/through the strike before settlement
     [the proposed price-barrier label]
  C. settle-through: SPX settles beyond the strike at 13:00 PT
     [the settlement-only label]

Theory says P(A) >= P(B) >= P(C) per entry (A fires earliest). The size of
the A-vs-B gap on real data is the cost of switching to price-barrier labels.

Entries: every 30 min from 06:35 to 12:00 PT, both sides, at the chain's
actual 0.10 / 0.15 / 0.20 delta strikes (interpolated, snapped to listed).
Stdlib only. All timestamps US/Pacific (the recording's native timezone).
"""

import bisect
import csv
import os
from datetime import datetime, timedelta

RAW = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
DAY = "2026-06-01"
SETTLE = datetime(2026, 6, 1, 13, 0)
DELTA_STAR = 0.30
TARGETS = [0.10, 0.15, 0.20]
ENTRY_TIMES = ["06:35", "07:00", "07:30", "08:00", "08:30", "09:00",
               "09:30", "10:00", "10:30", "11:00", "11:30", "12:00"]
MISSING = 1e308
STRIKE_MIN, STRIKE_MAX = 7200, 8000


def parse_ts(s):
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def load_spx():
    bars = {}
    with open(os.path.join(RAW, f"{DAY}-spx-chart.csv")) as f:
        for row in csv.reader(f):
            if row[0].startswith(DAY):
                bars[parse_ts(row[0])] = tuple(float(x) for x in row[1:5])
    return bars


def load_strike_series(strike, side):
    """[(ts, |delta|)] for one contract, valid rows only, chronological."""
    path = os.path.join(RAW, f"{DAY}-{strike}-{side}.csv")
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for row in csv.reader(f):
            try:
                d = float(row[4])
            except (ValueError, IndexError):
                continue
            if abs(d) < MISSING:
                out.append((parse_ts(row[0]), abs(d)))
    out.sort(key=lambda r: r[0])
    return out


SERIES_CACHE = {}


def strike_series(strike, side):
    key = (strike, side)
    if key not in SERIES_CACHE:
        SERIES_CACHE[key] = load_strike_series(strike, side)
    return SERIES_CACHE[key]


def delta_asof(strike, side, t, max_stale_min=10):
    s = strike_series(strike, side)
    ts_list = [r[0] for r in s]
    i = bisect.bisect_right(ts_list, t)
    if i == 0:
        return None
    ts, d = s[i - 1]
    if (t - ts) > timedelta(minutes=max_stale_min):
        return None
    return d


def actual_strike_at_delta(side, t, target, spot):
    """Interpolated chain strike at |delta|==target, snapped to listed 5s."""
    pts = []
    for k in range(STRIKE_MIN, STRIKE_MAX + 5, 5):
        if (side == "p" and k >= spot) or (side == "c" and k <= spot):
            continue
        d = delta_asof(k, side, t, max_stale_min=5)
        if d is not None and 0.01 < d < 0.6:
            pts.append((k, d))
    pts.sort()
    clean = []
    for k, d in pts:
        if not clean or (d > clean[-1][1] if side == "p" else d < clean[-1][1]):
            clean.append((k, d))
    for (k0, d0), (k1, d1) in zip(clean, clean[1:]):
        lo, hi = min(d0, d1), max(d0, d1)
        if lo <= target <= hi:
            raw = k0 + (target - d0) / (d1 - d0) * (k1 - k0)
            return int(round(raw / 5) * 5)
    return None


def first_breach(strike, side, t0):
    """First minute after t0 where the strike's recorded |delta| >= delta*."""
    t = t0 + timedelta(minutes=1)
    while t <= SETTLE:
        d = delta_asof(strike, side, t)
        if d is not None and d >= DELTA_STAR:
            return t
        t += timedelta(minutes=1)
    return None


def first_touch(strike, side, t0, spx):
    t = t0 + timedelta(minutes=1)
    while t <= SETTLE - timedelta(minutes=1):
        bar = spx.get(t)
        if bar:
            o, h, l, c = bar
            if (side == "p" and l <= strike) or (side == "c" and h >= strike):
                return t
        t += timedelta(minutes=1)
    return None


def main():
    spx = load_spx()
    settle_px = spx[max(t for t in spx if t < SETTLE)][3]
    rows = []
    for hhmm in ENTRY_TIMES:
        t0 = datetime.strptime(f"{DAY} {hhmm}", "%Y-%m-%d %H:%M")
        spot = spx[t0][0]
        for side in ("p", "c"):
            for tgt in TARGETS:
                k = actual_strike_at_delta(side, t0, tgt, spot)
                if k is None:
                    continue
                breach = first_breach(k, side, t0)
                touch = first_touch(k, side, t0, spx)
                settle_thru = (settle_px < k) if side == "p" else (settle_px > k)
                rows.append({
                    "entry": hhmm, "side": side, "target": tgt, "strike": k,
                    "spot": spot,
                    "breach": breach.strftime("%H:%M") if breach else "",
                    "touch": touch.strftime("%H:%M") if touch else "",
                    "settle_through": int(settle_thru),
                })

    out = os.path.join(os.path.dirname(__file__), "stop_vs_touch.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    n = len(rows)
    a = sum(1 for r in rows if r["breach"])
    b = sum(1 for r in rows if r["touch"])
    c = sum(1 for r in rows if r["settle_through"])
    ab = sum(1 for r in rows if r["breach"] and not r["touch"])
    print(f"entries: {n}  (settle px {settle_px})")
    print(f"A delta-breach (>= {DELTA_STAR}): {a}  ({a/n:.0%})")
    print(f"B touched strike:               {b}  ({b/n:.0%})")
    print(f"C settled through:              {c}  ({c/n:.0%})")
    print(f"breached but NEVER touched (the A-B gap): {ab}  ({ab/n:.0%})")
    print(f"\nper-entry detail -> {out}")
    print(f"{'entry':>6} {'side':>4} {'tgt':>5} {'strike':>7} "
          f"{'breach':>7} {'touch':>6} {'settle?':>8}")
    for r in rows:
        if r["breach"] or r["touch"] or r["settle_through"]:
            print(f"{r['entry']:>6} {r['side']:>4} {r['target']:>5} "
                  f"{r['strike']:>7} {r['breach'] or '-':>7} "
                  f"{r['touch'] or '-':>6} {r['settle_through']:>8}")


if __name__ == "__main__":
    main()
