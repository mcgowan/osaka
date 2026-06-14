#!/usr/bin/env python3
"""v2 Phase 1 — characterize the 5-bar rule (the incumbent / bar to beat).

The model must beat the crude "5 consecutive closes outside the OR" confirmation
at separating breakouts that HOLD to EOD (no re-entry) from those that fail.
This reports the base rates and the 5-bar hit rate over the day-clustered split
regions and by time of day, on SPY, in the tradeable window [10:00, 15:00) ET.
The locked test (>= V2_TEST_START) is reserved for the Phase-4 economic judge
and is NOT touched here.

Run:  .venv/bin/python analysis/breakout_5bar.py
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakouts import build_events, v2_split, V2_TEST_START  # noqa: E402


def _line(label, df):
    if len(df) == 0:
        print(f"  {label:>14} (no events)"); return
    conf = df[df["confirmed_5bar"] == 1]
    holders = df["held_to_eod"].sum()
    print(f"  {label:>14}: n={len(df):6d}  base held={df['held_to_eod'].mean():6.1%}"
          f"  | 5-bar: confirms {len(conf)/len(df):4.0%}  hit={conf['held_to_eod'].mean():6.1%}"
          f"  catches {conf['held_to_eod'].sum()}/{int(holders)} holders")


def main():
    df = build_events()
    sp = v2_split()
    trad = df[df["tradeable"] == 1]
    print(f"events: {len(df):,} over {df['day'].nunique():,} days "
          f"({df['day'].min()}..{df['day'].max()})\n{sp}\n")

    print(f"=== 5-bar rule by split region (tradeable window [10:00,15:00) ET) ===")
    for region in ("train", "calib", "valid"):
        _line(region, trad[trad["day"].isin(getattr(sp, f"{region}_days"))])

    dev = trad[trad["day"] < V2_TEST_START]
    print(f"\n=== development pooled + by side / attempt / start-hour ===")
    _line("ALL dev", dev)
    for side in ("up", "down"):
        _line(side, dev[dev["side"] == side])
    _line("1st attempt", dev[dev["attempt"] == 1])
    _line("2nd+ attempt", dev[dev["attempt"] >= 2])
    d = dev.copy(); d["hr"] = (d["start_mod"] + 570) // 60
    for hr in range(10, 15):
        _line(f"start {hr:02d}:00", d[d["hr"] == hr])


if __name__ == "__main__":
    main()
