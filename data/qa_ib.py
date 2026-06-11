#!/usr/bin/env python3
"""QA audit for data/ib/*.csv (plan task 0.1/0.2 acceptance, feeds Gate 0).

Stdlib-only. For each 1-min file: span, trading-day count, duplicate rows,
per-day RTH bar-count histogram, days with suspicious counts, missing
weekdays, and OHLC sanity. Daily files get a lighter pass.

RTH window: 09:30-15:59 ET bar labels (390/day; half days 09:30-12:59 = 210).
Bars outside the window (VIX GTH session) are counted separately, not flagged.
"""

import csv
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

IB_DIR = os.path.join(os.path.dirname(__file__), "ib")

# Per-symbol accepted RTH bar-count ranges (see data/ib/README.md):
# vol indices first print 09:31 and drop the odd minute (380-389 is normal
# dissemination jitter); half days variously end 12:59-13:15 (209-225 bars).
ACCEPT = {  # symbol -> ((full_lo, full_hi), (half_lo, half_hi))
    "SPX": ((390, 390), (210, 225)),
    "VIX": ((380, 390), (209, 225)),
    "VIX1D": ((380, 390), (209, 225)),
}
# Sparse early era: a few missing minutes per day is accepted (outside the
# v1 training universe; only feeds long-history regime features).
SPARSE_ERA_END = "2008-01-01"
SPARSE_ERA_MIN = 350  # 350..full pre-2008 = accepted sparse day

# documented IB-side holes / junk (data/ib/README.md) - reported, not failures
KNOWN_HOLES = {
    "VIX": {f"2006-05-{d:02d}" for d in range(1, 20)} | {
        # deeply partial days confirmed on IB's HMDS (mostly pre-2011 + COVID)
        "2005-12-20", "2006-04-03", "2006-09-05", "2006-09-26", "2007-01-04",
        "2007-03-22", "2007-07-16", "2007-07-31", "2007-08-20", "2007-10-22",
        "2008-02-12", "2008-05-05", "2008-07-03", "2008-08-25", "2010-08-16",
        "2010-10-08", "2011-05-27", "2011-07-28", "2020-03-16", "2020-03-18",
        "2020-12-07",
    },
    "SPX": {
        "2011-05-27",  # afternoon-only hole (shared with VIX)
        "2004-04-09", "2004-12-24", "2006-04-14",  # junk bars on closed days
        "2004-05-31",  # stale flat prints on Memorial Day (closed)
        "2006-10-17",  # 303-bar partial, sparse era
        "2008-02-22", "2008-07-01", "2008-07-03", "2008-07-31",  # 270-384 partials
        "2011-07-28",  # 238-bar partial
        "2020-12-07",  # 374 bars, 16 min missing (shared with VIX)
    },
}


def audit_minute(path, symbol):
    per_day = Counter()          # day -> RTH bar count
    gth = Counter()              # day -> non-RTH bar count
    dup = 0
    seen_ts = set()
    ohlc_bad = 0
    lo, hi = float("inf"), 0.0
    rows = 0
    with open(path) as f:
        for row in csv.reader(f):
            rows += 1
            ts = row[0]
            day, hhmm = ts[:10], ts[11:16]
            if ts in seen_ts:
                dup += 1
                continue
            seen_ts.add(ts)
            o, h, l, c = (float(x) for x in row[1:5])
            if not (l <= o <= h and l <= c <= h and l > 0):
                ohlc_bad += 1
            lo, hi = min(lo, l), max(hi, h)
            if "09:30" <= hhmm <= "15:59":
                per_day[day] += 1
            else:
                gth[day] += 1

    days = sorted(per_day)
    first, last = days[0], days[-1]
    hist = Counter(per_day.values())

    # weekdays in span with zero RTH bars (holidays + real gaps)
    d0 = date.fromisoformat(first)
    d1 = date.fromisoformat(last)
    missing = []
    d = d0
    while d <= d1:
        if d.weekday() < 5 and d.isoformat() not in per_day:
            missing.append(d.isoformat())
        d += timedelta(days=1)

    (flo, fhi), (hlo, hhi) = ACCEPT[symbol]
    odd, sparse = {}, 0
    for d, n in per_day.items():
        if flo <= n <= fhi or hlo <= n <= hhi \
                or d in KNOWN_HOLES.get(symbol, set()):
            continue
        if d < SPARSE_ERA_END and SPARSE_ERA_MIN <= n < flo:
            sparse += 1
            continue
        odd[d] = n

    print(f"\n=== {symbol} 1-min ===")
    print(f"rows {rows:,} | unique ts {len(seen_ts):,} | dup rows {dup:,}")
    print(f"span {first} .. {last} | trading days {len(days):,}")
    print(f"price range [{lo}, {hi}] | OHLC violations {ohlc_bad}")
    top = ", ".join(f"{n} bars x {c} days" for n, c in hist.most_common(6))
    print(f"RTH bar-count histogram: {top}")
    if gth:
        print(f"non-RTH bars present on {len(gth):,} days "
              f"(loader drops; expected for VIX GTH)")
    print(f"zero-bar weekdays in span: {len(missing)} "
          f"(holidays + documented holes)")
    known = KNOWN_HOLES.get(symbol, set())
    if known:
        miss_known = sorted(set(missing) & known)
        print(f"  of which documented IB holes: {len(miss_known)}")
    if sparse:
        print(f"accepted sparse-era days (pre-2008, 350-389 bars): {sparse}")
    print(f"UNEXPLAINED odd RTH counts (audit fails if > 0): {len(odd)}")
    for d in sorted(odd)[:15]:
        print(f"  {d}: {odd[d]} bars")
    if len(odd) > 15:
        print(f"  ... and {len(odd) - 15} more")
    return missing, odd


def audit_daily(path, symbol):
    rows, dup, bad = 0, 0, 0
    seen = set()
    first = last = None
    with open(path) as f:
        for row in csv.reader(f):
            rows += 1
            d = row[0][:10]
            if d in seen:
                dup += 1
            seen.add(d)
            o, h, l, c = (float(x) for x in row[1:5])
            if not (l <= o <= h and l <= c <= h and l > 0):
                bad += 1
            first = min(first or d, d)
            last = max(last or d, d)
    print(f"\n=== {symbol} 1-day ===")
    print(f"rows {rows:,} | dup days {dup} | OHLC violations {bad} | "
          f"span {first} .. {last}")


def main():
    for symbol in ("SPX", "VIX", "VIX1D"):
        p = os.path.join(IB_DIR, f"{symbol}-1min.csv")
        if os.path.exists(p):
            audit_minute(p, symbol)
        else:
            print(f"\n=== {symbol} 1-min === MISSING FILE")
        pd = os.path.join(IB_DIR, f"{symbol}-1day.csv")
        if os.path.exists(pd):
            audit_daily(pd, symbol)


if __name__ == "__main__":
    main()
