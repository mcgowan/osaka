#!/usr/bin/env python3
"""Task 1.5 (FR-4.3): grid coverage acceptance.

For every sampled (day, snapshot, side) observation from task 1.3, check
that the FR-4 grid (placed with the frozen anchors at that bar's S, sigma
anchor, T) BRACKETS the chain's actual 0.05-0.30 delta strike range in D
units. Acceptance: covered on >= ~95% of observations per side.

Run:  .venv/bin/python analysis/grid_coverage.py
Reads analysis/delta_mapping.csv (task 1.3 output).
"""

import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.loader import load_calendar  # noqa: E402
from quant.conventions import time_to_settle  # noqa: E402
from quant.distance import place_grid  # noqa: E402

SRC = os.path.join(os.path.dirname(__file__), "delta_mapping.csv")
PT_TO_ET = timedelta(hours=3)


def main():
    cal = load_calendar()
    half = set(cal[cal["is_half_day"]]["date"].dt.strftime("%Y-%m-%d"))

    # observed D(0.05) and D(0.30) per (day, snap, side), plus S and sigma
    obs = defaultdict(dict)
    for r in csv.DictReader(open(SRC)):
        key = (r["day"], r["snap_pt"], r["side"])
        obs[key]["spot"] = float(r["spot"])
        obs[key]["sigma"] = float(r["vix1d_anchor"]) / 100.0
        if r["target_delta"] == "0.05":
            obs[key]["d05"] = float(r["D"])
        elif r["target_delta"] == "0.3":
            obs[key]["d30"] = float(r["D"])

    stats = defaultdict(lambda: [0, 0, 0, 0])  # side -> [n, ok, miss_out, miss_in]
    for (day, snap_pt, side), o in sorted(obs.items()):
        if "d05" not in o or "d30" not in o:
            continue
        snap_et = datetime.strptime(f"{day} {snap_pt}",
                                    "%Y-%m-%d %H:%M") + PT_TO_ET
        T = time_to_settle(snap_et, is_half_day=day in half)
        grid = place_grid(o["spot"], o["sigma"], T, side)
        ds = [d for _, _, d in grid]
        s = stats[side]
        s[0] += 1
        out_ok = max(ds) >= o["d05"]
        in_ok = min(ds) <= o["d30"]
        if out_ok and in_ok:
            s[1] += 1
        if not out_ok:
            s[2] += 1
        if not in_ok:
            s[3] += 1

    print("=== FR-4.3 grid coverage (grid brackets actual 0.05-0.30 delta range) ===")
    ok = True
    for side, (n, good, miss_out, miss_in) in sorted(stats.items()):
        pct = good / n
        print(f"  {side}: {good}/{n} = {pct:.1%} covered "
              f"(missed far wing: {miss_out}, missed near edge: {miss_in})")
        ok &= pct >= 0.95
    print("ACCEPTANCE:", "PASS (>=95% both sides)" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
