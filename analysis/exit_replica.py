#!/usr/bin/env python3
"""v2 — approximate reproduction of the eleuthera exit engine, for the stop-aware
economic judge (does the model's breakout selection trigger fewer stop-outs?).

Ported from ../eleuthera/trader/common/directional-credit-spread-strategy.js
($green.js production config). EVALUATION machinery only - never a model feature
or label (the model stays independent of these gates). Approximate by design
(a benchmark doesn't need tick parity); validated against the logged trades.

Components (this file builds + validates the risk-heat core first):
- wilder_rsi(closes, 14)
- risk_level_series: VIX-RSI heat model -> per-bar riskLevel (1-1/(1+heat));
  risk_off_reversal arms when riskLevel >= RISK_EXIT_LEVEL (0.2).
Params from $green.js: upper 70, lower 30, decay 0.8, accumulation 3,
magnitudeCurve 0.75, riskExitLevel 0.2.

Run:  .venv/bin/python analysis/exit_replica.py   (validates vs the trade log)
"""

import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.loader import load_bars  # noqa: E402

RSI_PERIOD = 14
HEAT = dict(upper=70.0, lower=30.0, decay=0.8, accumulation=3.0, magnitudeCurve=0.75)
RISK_EXIT_LEVEL = 0.2


def wilder_rsi(closes, period=RSI_PERIOD):
    closes = np.asarray(closes, float)
    n = len(closes)
    rsi = np.full(n, np.nan)
    if n <= period:
        return rsi
    d = np.diff(closes)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    ag = gain[:period].mean()
    al = loss[:period].mean()
    for i in range(period, n):
        if i > period:
            ag = (ag * (period - 1) + gain[i - 1]) / period
            al = (al * (period - 1) + loss[i - 1]) / period
        rs = ag / al if al > 0 else np.inf
        rsi[i] = 100.0 - 100.0 / (1.0 + rs)
    return rsi


def risk_level_series(vix_closes):
    """Per-bar riskLevel (0..1) from the VIX-RSI heat model. heat resets at 0
    each session and accumulates; riskLevel = 1 - 1/(1+heat)."""
    rsi = wilder_rsi(vix_closes)
    heat = 0.0
    out = np.zeros(len(vix_closes))
    for i, r in enumerate(rsi):
        if not np.isnan(r):
            if r > HEAT["upper"]:
                dev = (r - HEAT["upper"]) / (100 - HEAT["upper"])
            elif r < HEAT["lower"]:
                dev = (HEAT["lower"] - r) / HEAT["lower"]
            else:
                dev = 0.0
            if dev > 0:
                heat += dev ** HEAT["magnitudeCurve"] * HEAT["accumulation"]
            else:
                heat = max(0.0, heat - HEAT["decay"])
        out[i] = 1.0 - 1.0 / (1.0 + heat)
    return out


def _emin(s):  # "HH:MM" ET -> minute-of-day
    h, m = s.split(":"); return int(h) * 60 + int(m)


def validate():
    """Does the reproduced riskLevel light up on the ACTUAL logged
    risk_off_reversal exits (vs sr_inner_breach / expiration as controls)?"""
    rows = [r for r in csv.DictReader(open(os.path.join(os.path.dirname(__file__),
            "exit_characterization.csv"))) if r["day"] < "2025-07-01" and r["exit_et"]]
    days = sorted({r["day"] for r in rows})
    vix = load_bars("VIX", start=days[0], end=days[-1])
    vix["day"] = vix["ts"].dt.strftime("%Y-%m-%d")
    vix["min"] = vix["ts"].dt.hour * 60 + vix["ts"].dt.minute
    rl_by_day = {}
    for d, g in vix.groupby("day"):
        g = g.sort_values("min")
        rl_by_day[d] = dict(zip(g["min"].to_numpy(),
                                risk_level_series(g["close"].to_numpy())))

    def at_exit(r):
        rl = rl_by_day.get(r["day"], {})
        em = _emin(r["exit_et"])
        # nearest VIX bar at/just before the exit minute
        cands = [m for m in rl if m <= em]
        return rl[max(cands)] if cands else np.nan

    by_reason = {}
    for r in rows:
        by_reason.setdefault(r["reason"], []).append(at_exit(r))
    print(f"reproduced riskLevel at logged exit (riskExitLevel={RISK_EXIT_LEVEL}):")
    for reason in ("risk_off_reversal", "sr_inner_breach", "expiration"):
        v = [x for x in by_reason.get(reason, []) if x == x]
        if not v:
            continue
        armed = np.mean([x >= RISK_EXIT_LEVEL for x in v])
        print(f"  {reason:20s} n={len(v):3d}  median riskLevel {np.median(v):.2f}  "
              f"armed(>= {RISK_EXIT_LEVEL}) {armed:.0%}")


if __name__ == "__main__":
    validate()
