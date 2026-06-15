#!/usr/bin/env python3
"""v2 Phase 3 — dev-period ECONOMIC proxy: does the model's breakout selection
pay better than the 5-bar rule at the trader's actual strike (10-delta short,
40-wide), not just at the 1-OR-width reversal proxy?

The 10-delta short strike sits ~1.28 implied-moves OTM (~2 OR-widths beyond
entry), so most 1-OR-width "reversals" are shakeouts that still WIN. This prices
the hold-to-EOD spread outcome as a PRICE FACT: strike placed via the VIX implied
move (standard placement; not the unreliable BS magnitude that sank v1), outcome
= where SPY settles vs the strikes. The model-vs-5-bar comparison is
CREDIT-INDEPENDENT (matched selectivity -> ΔP&L = -Δ total settle-through), so the
only approximation is strike placement, and it biases both rules equally.

Assumes hold-to-EOD (0DTE expiry), no stops (the trader's stops need his exit
rules, out of scope). Dev period only (TRAIN-OOF preds, 2010-2020); the real
chains are the §7.2 locked-test judge. Run on SPY; distances scaled to SPX pts.

Run:  .venv/bin/python analysis/breakout_econ_proxy.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakouts import build_events  # noqa: E402
from data.loader import load_bars  # noqa: E402
from models.breakout_gbt import train_oof  # noqa: E402
from quant.conventions import YEAR_SECONDS  # noqa: E402

Z10 = 1.2816          # 10-delta -> ~1.28 sigma OTM
WIDTH_SPX = 40.0      # the trader's spread width (SPX points)
SPY_TO_SPX = 10.0     # SPY ~ SPX/10; width in SPY points = 4.0
CREDIT_SPX = 6.0      # illustrative credit (for net-$ flavor only; the model-vs-
                      # 5-bar comparison is credit-independent)


def main():
    ev = build_events()                          # dev-only (loader self-locks)
    spy = load_bars("SPY")
    spy["day"] = spy["ts"].dt.strftime("%Y-%m-%d")
    eod = spy.groupby("day")["close"].last()

    vix = load_bars("VIX", freq="1day")
    vd = vix["ts"].dt.strftime("%Y-%m-%d").tolist()
    prior_vix = {vd[i]: float(vix["close"].iloc[i - 1]) for i in range(1, len(vd))}

    oof = train_oof()                            # TRAIN-OOF P(reversed)
    key = ["day", "side", "attempt", "start_mod"]
    m = ev.merge(oof[key + ["pred"]], on=key, how="inner")
    m = m[(m["tradeable"] == 1) & m["day"].isin(prior_vix)].copy()
    m = m[m["day"].isin(eod.index)]

    width_spy = WIDTH_SPX / SPY_TO_SPX
    through = np.zeros(len(m))                    # settle-through depth (SPY pts)
    for i, r in enumerate(m.itertuples()):
        S = r.start_close
        sig = prior_vix[r.day] / 100.0
        trem = max((390 - r.start_mod), 1) * 60.0 / YEAR_SECONDS
        dist = Z10 * sig * math.sqrt(trem) * S
        settle = float(eod[r.day])
        if r.side == "up":                        # put spread below
            short = S - dist
            t = max(0.0, short - settle)
        else:                                     # call spread above
            short = S + dist
            t = max(0.0, settle - short)
        through[i] = min(t, width_spy)
    m["through_spx"] = through * SPY_TO_SPX       # in SPX points
    m["loss"] = m["through_spx"] > 0

    def report(label, sub):
        n = len(sub)
        winrate = 1 - sub["loss"].mean()
        loss_pts = sub["through_spx"].sum()
        pnl = n * CREDIT_SPX - loss_pts           # SPX pts (x$100/contract)
        print(f"  {label:18s} n={n:5d}  win {winrate:5.1%}  "
              f"settle-through {loss_pts:8.0f} SPX-pts  net@cr{CREDIT_SPX:.0f} {pnl:+8.0f} pts")

    gf = m["confirmed_5bar"].mean()
    thr = np.quantile(m["pred"], gf)
    sets = {"take-ALL": m, "5-bar": m[m["confirmed_5bar"] == 1],
            "MODEL(matched)": m[m["pred"] <= thr]}
    print(f"TRAIN-OOF dev econ proxy: {len(m):,} breakouts, 10-delta / {WIDTH_SPX:.0f}-wide, "
          f"greenlight {gf:.0%}\n(net is credit-dependent flavor; win-rate & settle-through are price facts)")
    print("\n=== pooled ===")
    for k, v in sets.items():
        report(k, v)
    m["hr"] = (m["start_mod"] + 570) // 60
    for lbl, hrs in (("MORNING (10-11)", [10, 11]), ("AFTERNOON (12-14)", [12, 13, 14])):
        print(f"\n=== {lbl} ===")
        mm = m[m["hr"].isin(hrs)]
        gfm = mm["confirmed_5bar"].mean(); thrm = np.quantile(mm["pred"], gfm)
        report("take-ALL", mm)
        report("5-bar", mm[mm["confirmed_5bar"] == 1])
        report("MODEL(matched)", mm[mm["pred"] <= thrm])


if __name__ == "__main__":
    main()
