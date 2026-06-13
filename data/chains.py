"""Validation-data access: the trader's recorded chain archive.

Source: ../eleuthera/events/<YYYY-MM-DD>/<date>-<strike>-{c,p}.csv
(written continuously by the trader's live recorder; 303+ days).
Row format: ts, bid, ask, last, delta, gamma, vega, theta, iv, mark, _
Timestamps are US/Pacific. DBL_MAX (~1.8e308) = IB "unset".

ROLE BOUNDARY (CLAUDE.md): chains are validation/calibration/ablation data
ONLY. Nothing in the label pipeline or core feature set may import this
module — the leakage-redteam agent checks for exactly that.

NO TEST_START GUARD: this module is NOT truncated at the locked test
boundary (see data/loader.py lock docstring). Locked-period chain data is
readable here; do not assume otherwise when writing analyses.
"""

import bisect
import csv
import os
from datetime import timedelta

ARCHIVE = os.environ.get(
    "OSAKA_CHAIN_ARCHIVE",
    os.path.join(os.path.dirname(__file__), "..", "..", "eleuthera", "events"))
MISSING = 1e308


def list_days():
    """Sorted recorded days ('YYYY-MM-DD') available in the archive."""
    if not os.path.isdir(ARCHIVE):
        raise FileNotFoundError(
            f"chain archive not found at {ARCHIVE} (set OSAKA_CHAIN_ARCHIVE)")
    return sorted(d for d in os.listdir(ARCHIVE)
                  if len(d) == 10 and d[4] == d[7] == "-")


def day_strikes(day, side=None, lo=None, hi=None):
    """Available strikes for a day (optionally one side / strike window)."""
    out = set()
    for name in os.listdir(os.path.join(ARCHIVE, day)):
        parts = name[:-4].split("-")
        if len(parts) != 5 or not parts[3].isdigit():
            continue
        k, s = int(parts[3]), parts[4]
        if side and s != side:
            continue
        if (lo is None or k >= lo) and (hi is None or k <= hi):
            out.add((k, s))
    return sorted(out)


def contract_asof(day, strike, side, snaps_pt, max_stale=timedelta(minutes=10)):
    """Last valid (delta, iv, ts) at/before each PT snapshot for one contract.

    Returns {snap_datetime: (abs_delta_signed_raw, iv, ts)} - delta kept as
    recorded (negative for puts). Rows with unset delta/iv are skipped.
    """
    from datetime import datetime

    path = os.path.join(ARCHIVE, day, f"{day}-{strike}-{side}.csv")
    if not os.path.exists(path):
        return {}
    rows = []
    with open(path) as f:
        for row in csv.reader(f):
            try:
                delta, iv = float(row[4]), float(row[8])
            except (ValueError, IndexError):
                continue
            if abs(delta) >= MISSING or iv >= MISSING or iv <= 0:
                continue
            rows.append((datetime.strptime(row[0][:19], "%Y-%m-%d %H:%M:%S"),
                         delta, iv))
    rows.sort(key=lambda r: r[0])
    ts_list = [r[0] for r in rows]
    out = {}
    for snap in snaps_pt:
        i = bisect.bisect_right(ts_list, snap)
        if i > 0 and snap - rows[i - 1][0] <= max_stale:
            out[snap] = (rows[i - 1][1], rows[i - 1][2], rows[i - 1][0])
    return out


def contract_quote_asof(day, strike, side, snaps_pt,
                        max_stale=timedelta(minutes=10)):
    """Last valid (bid, ask, mark, ts) at/before each PT snapshot for one
    contract. Row cols: 0=ts,1=bid,2=ask,3=last,...,9=mark. Rows with an unset
    or non-positive bid/ask are skipped (can't form a mid)."""
    from datetime import datetime

    path = os.path.join(ARCHIVE, day, f"{day}-{strike}-{side}.csv")
    if not os.path.exists(path):
        return {}
    rows = []
    with open(path) as f:
        for row in csv.reader(f):
            try:
                bid, ask, mark = float(row[1]), float(row[2]), float(row[9])
            except (ValueError, IndexError):
                continue
            if bid >= MISSING or ask >= MISSING or bid <= 0 or ask <= 0:
                continue
            rows.append((datetime.strptime(row[0][:19], "%Y-%m-%d %H:%M:%S"),
                         bid, ask, mark))
    rows.sort(key=lambda r: r[0])
    ts_list = [r[0] for r in rows]
    out = {}
    for snap in snaps_pt:
        i = bisect.bisect_right(ts_list, snap)
        if i > 0 and snap - rows[i - 1][0] <= max_stale:
            _, b, a, mk = rows[i - 1]
            out[snap] = (b, a, mk, rows[i - 1][0])
    return out


def chain_asof(day, snaps_pt, lo, hi, max_stale=timedelta(minutes=10)):
    """As-of view of all contracts in [lo, hi] at each PT snapshot.

    Returns {snap: {(strike, side): (delta, iv, ts)}}.
    """
    out = {snap: {} for snap in snaps_pt}
    for k, s in day_strikes(day, lo=lo, hi=hi):
        for snap, rec in contract_asof(day, k, s, snaps_pt, max_stale).items():
            out[snap][(k, s)] = rec
    return out
