#!/usr/bin/env python3
"""Download 1-min (and daily) index history from Interactive Brokers.

Targets: SPX, VIX, VIX1D (CBOE indices) for Phase 0 data acquisition
(plan tasks 0.1 / 0.2). Requires TWS or IB Gateway running with API enabled
(Configure > API > Settings: "Enable ActiveX and Socket Clients").

Setup (one-time):
    python3 -m venv .venv
    .venv/bin/pip install ib_insync   # (ib_async on Python >= 3.10)

Usage:
    .venv/bin/python data/ib_download.py --check
        Connect, qualify the three index contracts, and print the earliest
        available timestamp for each (verifies VIX1D exists on IB and how
        deep the history goes). Read-only, fast.

    .venv/bin/python data/ib_download.py --symbol VIX1D
    .venv/bin/python data/ib_download.py --symbol SPX --start 2008-01-01
        Download 1-min RTH bars, walking backward from today to --start (or
        the IB head timestamp, whichever is later). Resumable: progress is
        appended per chunk to data/ib/<SYMBOL>-1min.csv and re-runs skip
        already-fetched ranges.

    .venv/bin/python data/ib_download.py --symbol SPX --daily
        Full daily history (cheap, one request) -> data/ib/<SYMBOL>-1day.csv.

    .venv/bin/python data/ib_download.py --symbol VIX --fill-gaps
        Scan data/ib/<SYMBOL>-1min.csv for days with anomalously few bars
        (1..199, i.e. partial chunks left by an interrupted run before the
        SIGINT hardening landed) and refetch each. Written rows duplicate
        existing data; the Phase 0 loader sorts and dedupes.

Known IB-side data holes (not bugs in this script):
    VIX 1-min has no data from 2006-05-01 through 2006-05-19. See
    data/ib/README.md for details and how the loader handles it.

Output format matches the existing recordings: "YYYY-MM-DD HH:MM:SS,o,h,l,c"
Timestamps are US/Eastern (exchange convention; note the trader's own
recordings are US/Pacific - the Phase 0 loader normalizes).

Pacing: IB allows ~60 historical requests per 10 min. We request 1-week
chunks of 1-min bars with a conservative sleep; a full 2008->now SPX pull is
~960 chunks, i.e. an overnight run. VIX1D (2023->) is ~160 chunks, ~40 min.
"""

import argparse
import csv
import io
import os
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    from ib_async import IB, Index  # community fork, Python >= 3.10
except ImportError:
    try:
        from ib_insync import IB, Index  # predecessor, works on Python 3.9
    except ImportError:
        sys.exit("run: .venv/bin/pip install ib_insync")

ET = ZoneInfo("America/New_York")
OUT_DIR = os.path.join(os.path.dirname(__file__), "ib")
SYMBOLS = {"SPX": "CBOE", "VIX": "CBOE", "VIX1D": "CBOE"}
CHUNK = "1 W"            # duration per 1-min request; drop to "1 D" if IB rejects
SLEEP_S = 11             # ~55 requests / 10 min, under the 60-cap
RTH_ONLY = True
GAP_THRESHOLD_BARS = 200 # days with 1..199 bars are suspected partial chunks
                         # (legit half-days have ~210 RTH bars; full days ~389+)

# Days where IB itself has no/partial 1-min data; --fill-gaps must skip
# them or it will refetch the same useless 29 rows forever. See
# data/ib/README.md for confirmation queries and context.
KNOWN_IB_HOLES = {
    "VIX": {"2011-05-27"},  # only 15:31-15:59 ET exist on IB
    "SPX": set(),
    "VIX1D": set(),
}

# Deferred-stop SIGINT handler so Ctrl-C never truncates a chunk mid-write —
# resume only refills timestamps earlier than the file's earliest, so a
# partial chunk in the middle becomes a permanent hole otherwise. Second
# Ctrl-C still force-exits.
_STOP = False


def _install_sigint_handler():
    def _on_sigint(signum, frame):
        global _STOP
        if _STOP:
            raise KeyboardInterrupt
        _STOP = True
        print("\n[Ctrl-C — finishing current chunk then stopping; "
              "press again to force-quit]")
    signal.signal(signal.SIGINT, _on_sigint)


def connect(port, client_id):
    ib = IB()
    # common ports: 7496 TWS live, 7497 TWS paper, 4001 GW live, 4002 GW paper
    ib.connect("127.0.0.1", port, clientId=client_id, timeout=10)
    return ib


def qualify(ib, symbol):
    contract = Index(symbol, SYMBOLS[symbol])
    ib.qualifyContracts(contract)
    return contract


def check(ib):
    for symbol in SYMBOLS:
        try:
            contract = qualify(ib, symbol)
            head = ib.reqHeadTimeStamp(contract, whatToShow="TRADES", useRTH=RTH_ONLY)
            print(f"{symbol:6s} qualified (conId {contract.conId}), "
                  f"history begins {head}")
        except Exception as e:
            print(f"{symbol:6s} FAILED: {e}")


def out_path(symbol, daily):
    os.makedirs(OUT_DIR, exist_ok=True)
    return os.path.join(OUT_DIR, f"{symbol}-{'1day' if daily else '1min'}.csv")


def existing_earliest(path):
    """Earliest timestamp already on disk (file is written newest-range-last,
    but rows within are chronological; scan is cheap enough either way)."""
    if not os.path.exists(path):
        return None
    earliest = None
    with open(path) as f:
        for row in csv.reader(f):
            ts = row[0]
            if earliest is None or ts < earliest:
                earliest = ts
    return earliest


def write_bars(path, bars):
    """Append a full chunk in a single unbuffered write so SIGINT (or any
    abrupt exit) cannot truncate mid-chunk. Combined with the deferred-stop
    handler this eliminates the partial-chunk-becomes-permanent-hole class
    of failure that left 2011-05-27 with only 29 of its bars on disk."""
    if not bars:
        return
    buf = io.StringIO()
    w = csv.writer(buf)
    for b in bars:
        ts = b.date.astimezone(ET) if hasattr(b.date, "astimezone") else b.date
        w.writerow([str(ts)[:19], b.open, b.high, b.low, b.close])
    payload = buf.getvalue().encode()
    with open(path, "ab", buffering=0) as f:
        f.write(payload)


def download_daily(ib, symbol):
    contract = qualify(ib, symbol)
    bars = ib.reqHistoricalData(
        contract, endDateTime="", durationStr="30 Y", barSizeSetting="1 day",
        whatToShow="TRADES", useRTH=RTH_ONLY, formatDate=2)
    path = out_path(symbol, daily=True)
    if os.path.exists(path):
        os.remove(path)
    write_bars(path, bars)
    print(f"{symbol}: {len(bars)} daily bars -> {path}")


def download_minute(ib, symbol, start):
    contract = qualify(ib, symbol)
    head = ib.reqHeadTimeStamp(contract, whatToShow="TRADES", useRTH=RTH_ONLY)
    head = head.astimezone(ET) if hasattr(head, "astimezone") else head
    floor = max(start, head.replace(tzinfo=None)) if start else head.replace(tzinfo=None)
    path = out_path(symbol, daily=False)

    resume = existing_earliest(path)
    end = datetime.strptime(resume, "%Y-%m-%d %H:%M:%S") if resume \
        else datetime.now(ET).replace(tzinfo=None)
    print(f"{symbol}: fetching 1-min bars from {end:%Y-%m-%d} back to "
          f"{floor:%Y-%m-%d} (IB head: {head})")

    chunk_fails = 0
    while end > floor:
        if _STOP:
            print(f"{symbol}: stopping cleanly after interrupt")
            return
        # TWS 10.20+ requires the new UTC dash format; the legacy
        # "yyyymmdd HH:MM:SS US/Eastern" form is rejected with error 10314.
        end_str = end.replace(tzinfo=ET).astimezone(timezone.utc) \
                     .strftime("%Y%m%d-%H:%M:%S")
        try:
            bars = ib.reqHistoricalData(
                contract, endDateTime=end_str, durationStr=CHUNK,
                barSizeSetting="1 min", whatToShow="TRADES",
                useRTH=RTH_ONLY, formatDate=2)
        except Exception as e:
            chunk_fails += 1
            print(f"  chunk ending {end:%Y-%m-%d} failed ({e}); "
                  f"retry {chunk_fails}/3 after 60s")
            if chunk_fails >= 3:
                print("  giving up on this run; re-run to resume")
                return
            time.sleep(60)
            continue
        chunk_fails = 0
        if bars:
            write_bars(path, bars)
            first = bars[0].date
            first = first.astimezone(ET).replace(tzinfo=None) \
                if hasattr(first, "astimezone") else datetime.combine(first, datetime.min.time())
            print(f"  {len(bars):5d} bars  {first:%Y-%m-%d %H:%M} .. {end:%Y-%m-%d %H:%M}")
            end = first - timedelta(minutes=1)
        else:
            # empty span (holidays / pre-listing) - step back a week
            end -= timedelta(days=7)
        time.sleep(SLEEP_S)
    print(f"{symbol}: done -> {path} (rows are unordered across chunks; "
          f"the Phase 0 loader sorts and dedupes)")


def find_gap_days(path, symbol):
    """Return YYYY-MM-DD strings already on disk whose bar count is in
    (0, GAP_THRESHOLD_BARS), excluding days in KNOWN_IB_HOLES. Zero-bar
    days are IB-side holes or holidays and are intentionally not flagged."""
    if not os.path.exists(path):
        return []
    per_day = defaultdict(int)
    with open(path) as f:
        for row in csv.reader(f):
            per_day[row[0][:10]] += 1
    holes = KNOWN_IB_HOLES.get(symbol, set())
    return sorted(d for d, c in per_day.items()
                  if 0 < c < GAP_THRESHOLD_BARS and d not in holes)


def fill_gaps(ib, symbol):
    """Detect any day on disk with anomalously few bars (e.g. a chunk that
    was interrupted mid-write before the SIGINT hardening landed) and
    refetch it. Writes duplicates that the Phase 0 loader will dedupe."""
    contract = qualify(ib, symbol)
    path = out_path(symbol, daily=False)
    gaps = find_gap_days(path, symbol)
    if not gaps:
        print(f"{symbol}: no partial days detected "
              f"(threshold {GAP_THRESHOLD_BARS} bars)")
        return
    print(f"{symbol}: refetching {len(gaps)} partial day(s): {gaps}")
    for day in gaps:
        if _STOP:
            print(f"{symbol}: stopping gap-fill after interrupt")
            return
        # Request a 1 D chunk ending at 23:00 ET of the target day so the
        # window covers the full RTH session (plus extended hours for VIX).
        end_dt = datetime.strptime(day + " 23:00:00",
                                   "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET)
        end_str = end_dt.astimezone(timezone.utc).strftime("%Y%m%d-%H:%M:%S")
        try:
            bars = ib.reqHistoricalData(
                contract, endDateTime=end_str, durationStr="1 D",
                barSizeSetting="1 min", whatToShow="TRADES",
                useRTH=RTH_ONLY, formatDate=2)
        except Exception as e:
            print(f"  {day}: FAILED ({e})")
            time.sleep(SLEEP_S)
            continue
        if bars:
            write_bars(path, bars)
            print(f"  {day}: fetched {len(bars)} bars")
        else:
            print(f"  {day}: no data returned")
        time.sleep(SLEEP_S)
    print(f"{symbol}: fill complete; rows may now duplicate, loader will dedupe")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true", help="verify contracts + head timestamps")
    p.add_argument("--symbol", choices=SYMBOLS, help="which index to download")
    p.add_argument("--daily", action="store_true", help="daily bars instead of 1-min")
    p.add_argument("--fill-gaps", action="store_true",
                   help="rescan disk for partial days (<200 bars) and refetch them")
    p.add_argument("--start", type=lambda s: datetime.strptime(s, "%Y-%m-%d"),
                   help="earliest date to fetch (default: IB head timestamp)")
    p.add_argument("--port", type=int, default=7496, help="TWS/Gateway API port")
    p.add_argument("--client-id", type=int, default=17)
    args = p.parse_args()

    _install_sigint_handler()
    ib = connect(args.port, args.client_id)
    try:
        if args.check:
            check(ib)
        elif args.symbol and args.fill_gaps:
            fill_gaps(ib, args.symbol)
        elif args.symbol and args.daily:
            download_daily(ib, args.symbol)
        elif args.symbol:
            download_minute(ib, args.symbol, args.start)
        else:
            p.print_help()
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
