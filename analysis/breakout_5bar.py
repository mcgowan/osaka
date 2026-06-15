#!/usr/bin/env python3
"""v2 Phase 1 — characterize the 5-bar rule vs the adverse-reversal target.

The model must beat the crude "5 consecutive closes outside the OR" confirmation
at flagging breakouts that REVERSE (give back >= REVERSAL_K OR-widths before EOD
= would be stopped at a loss; see breakout_label_calib.py). This reports the
reversal base rates over the day-clustered split regions and by side/attempt/time
on SPY, in the tradeable window [10:00, 15:00) ET, and the key number: how weak a
reversal-filter the 5-bar rule is (the room the model has). The locked test
(>= V2_TEST_START) is reserved for the Phase-4 judge and is NOT touched here.

Run:  .venv/bin/python analysis/breakout_5bar.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakouts import build_events, v2_split, V2_TEST_START, REVERSAL_K  # noqa: E402


def _line(label, df):
    if len(df) == 0:
        print(f"  {label:>14} (no events)"); return
    print(f"  {label:>14}: n={len(df):6d}  reversed={df['reversed'].mean():6.1%}"
          f"  held_to_eod={df['held_to_eod'].mean():5.1%}")


def main():
    df = build_events()
    sp = v2_split()
    trad = df[df["tradeable"] == 1]
    dev = trad[trad["day"] < V2_TEST_START]
    print(f"events: {len(df):,} over {df['day'].nunique():,} days; target = reversed "
          f"(max_adverse_orw >= {REVERSAL_K})\n{sp}\n")

    a = dev["max_adverse_orw"].to_numpy()
    print("max_adverse_orw distribution (OR-width units): "
          + "  ".join(f"p{p}={np.percentile(a, p):.2f}" for p in (25, 50, 75, 90)))

    print("\n=== reversal rate by split region (tradeable window) ===")
    for region in ("train", "calib", "valid"):
        _line(region, trad[trad["day"].isin(getattr(sp, f"{region}_days"))])

    print("\n=== the bar to beat: does 5-bar confirmation avoid reversals? ===")
    for c, g in dev.groupby("confirmed_5bar"):
        _line("5-bar CONFIRMED" if c else "unconfirmed", g)

    print("\n=== reversal rate by side / attempt / start-hour ===")
    for side in ("up", "down"):
        _line(side, dev[dev["side"] == side])
    _line("1st attempt", dev[dev["attempt"] == 1])
    _line("2nd+ attempt", dev[dev["attempt"] >= 2])
    d = dev.copy(); d["hr"] = (d["start_mod"] + 570) // 60
    for hr in range(10, 15):
        _line(f"start {hr:02d}:00", d[d["hr"] == hr])


if __name__ == "__main__":
    main()
