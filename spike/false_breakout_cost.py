#!/usr/bin/env python3
"""False-breakout cost: the Gate S economic question, measured on 294 real
backtested trades from the trader's rules-based system (eleuthera).

For every EARLY-EXITED spread (risk_off_reversal / sr_inner_breach), compute
the counterfactual: what if the position had been held to settlement?

  held P&L = credit - intrinsic(settle) * 100 * qty      (no exit commission)

and whether SPX ever touched the short strike after the early exit.
Settlement price and touch checks come from the project's audited SPX 1-min
data (data/loader.py). Eleuthera log times are US/Pacific; loader is Eastern.

Run: .venv/bin/python spike/false_breakout_cost.py
"""

import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pandas as pd  # noqa: E402
from data.loader import load_bars  # noqa: E402

LOG_GLOB = "/Users/michael/GitHub/eleuthera/analyzer/logs/*.log"
EARLY = {"risk_off_reversal", "sr_inner_breach"}


def spread_intrinsic(side, upper, lower, settle):
    if side == "bear":            # short call upper, long call lower-premium leg
        k_short, k_long = upper, lower
        return max(0.0, settle - k_short) - max(0.0, settle - k_long)
    k_short, k_long = upper, lower  # bull: short put upper, long put lower
    return max(0.0, k_short - settle) - max(0.0, k_long - settle)


def main():
    trades = []
    for path in sorted(glob.glob(LOG_GLOB)):
        entries = {}
        for line in open(path):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e["event"] == "entry_filled":
                entries[e["spreadId"]] = e
            elif e["event"] == "spread_exited":
                ent = entries.get(e["spreadId"])
                if ent:
                    trades.append((ent, e))

    # sanctioned full-span reader (FR-5.3 trade-log analysis; Phase S record)
    spx = load_bars("SPX", _unlocked_full_span=True)
    spx["day"] = spx["ts"].dt.strftime("%Y-%m-%d")
    by_day = dict(tuple(spx.groupby("day")))

    rows = []
    skipped = 0
    for ent, ex in trades:
        if ex["reason"] not in EARLY:
            continue
        day = ent["time"][:10]
        bars = by_day.get(day)
        if bars is None:
            skipped += 1
            continue
        settle = bars["close"].iloc[-1]
        intrinsic = spread_intrinsic(ent["side"], ent["upperStrike"],
                                     ent["lowerStrike"], settle)
        held = ent["credit"] - intrinsic * 100 * ent["quantity"]
        # touch check after the early exit (PT log time + 3h = ET)
        exit_et = pd.Timestamp(ex["time"][:19]) + pd.Timedelta(hours=3)
        after = bars[bars["ts"] > exit_et]
        k = ent["upperStrike"]
        touched = bool(((after["high"] >= k) if ent["side"] == "bear"
                        else (after["low"] <= k)).any())
        rows.append({
            "day": day, "side": ent["side"], "reason": ex["reason"],
            "short": k, "qty": ent["quantity"], "credit": ent["credit"],
            "actual": ex["profit"], "held": round(held, 2),
            "delta_pnl": round(held - ex["profit"], 2),
            "touched_after_exit": int(touched),
            "settle": settle,
        })

    df = pd.DataFrame(rows)
    out = os.path.join(os.path.dirname(__file__), "false_breakout_cost.csv")
    df.to_csv(out, index=False)

    print(f"early-exited trades analyzed: {len(df)} (skipped {skipped}, "
          f"no SPX data)")
    for reason, g in df.groupby("reason"):
        flips = (g["held"] > 0) & (g["actual"] <= 0)
        print(f"\n--- {reason} (n={len(g)}) ---")
        print(f"  actual P&L:            {g['actual'].sum():>12,.0f}")
        print(f"  held-to-settle P&L:    {g['held'].sum():>12,.0f}")
        print(f"  cost of exiting early: {g['delta_pnl'].sum():>12,.0f}")
        print(f"  losses that would have been wins: {int(flips.sum())}/"
              f"{int((g['actual'] <= 0).sum())}")
        print(f"  touched short strike after exit: "
              f"{int(g['touched_after_exit'].sum())} ({g['touched_after_exit'].mean():.0%})")
        print(f"  held-P&L worst single trade: {g['held'].min():>12,.0f}")
    print(f"\n--- ALL EARLY EXITS (n={len(df)}) ---")
    print(f"  actual P&L:            {df['actual'].sum():>12,.0f}")
    print(f"  held-to-settle P&L:    {df['held'].sum():>12,.0f}")
    print(f"  cost of exiting early: {df['delta_pnl'].sum():>12,.0f}")
    print(f"  trades where holding was better: "
          f"{int((df['delta_pnl'] > 0).sum())}/{len(df)}")
    print(f"\ndetail -> {out}")


if __name__ == "__main__":
    main()
