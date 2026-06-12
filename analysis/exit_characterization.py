#!/usr/bin/env python3
"""Task 1.6 (FR-5.2): characterize the trading system's exits vs the labels.

For all 294 logged spreads (eleuthera backtest replay, 301 days): at entry
and at exit, compute the short strike's normalized distance D and the frozen
p_mkt baseline; then the label-side facts (touch after exit? settle-beyond?
held-to-settle P&L). Aggregated per exit reason x VIX1D regime tercile.

This is the bridge between model output and overlay value: it documents
WHERE in the model's coordinate the system's exits fire, and what the
market-implied survival was at those moments.

Run:  .venv/bin/python analysis/exit_characterization.py
Output: analysis/exit_characterization.csv + printed tables for docs/calibration.md

Settlement caveat: "settle" is the 15:59 ET bar close (13:00-session close on
half days) as a proxy for the official SPXW PM settlement print. The two can
differ by a few tenths of a point; immaterial for these statistics.
"""

import csv
import glob
import json
import os
import sys
from datetime import datetime, timedelta
from statistics import mean, median

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.loader import load_bars, load_calendar  # noqa: E402
from quant.conventions import SigmaAnchor, time_to_settle  # noqa: E402
from quant.distance import normalized_distance  # noqa: E402
from quant.pmkt import p_mkt  # noqa: E402

LOG_GLOB = os.environ.get(
    "OSAKA_TRADE_LOGS",
    os.path.join(os.path.dirname(__file__), "..", "..", "eleuthera",
                 "analyzer", "logs", "*.log"))
OUT = os.path.join(os.path.dirname(__file__), "exit_characterization.csv")
PT_TO_ET = timedelta(hours=3)
TERCILES = (11.0, 14.7)  # frozen full-history VIX1D cutoffs (docs/calibration.md)
SIDE_MAP = {"bull": "p", "bear": "c"}  # bull put spread / bear call spread


def tercile(vix1d):
    return "calm" if vix1d < TERCILES[0] else (
        "mid" if vix1d < TERCILES[1] else "elevated")


def load_trades():
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
            elif e["event"] == "spread_exited" and e["spreadId"] in entries:
                trades.append((entries[e["spreadId"]], e))
    return trades


def main():
    anchor = SigmaAnchor()
    cal = load_calendar()
    half = set(cal[cal["is_half_day"]]["date"].dt.strftime("%Y-%m-%d"))
    spx = load_bars("SPX", start="2024-12-01")
    spx["day"] = spx["ts"].dt.strftime("%Y-%m-%d")
    by_day = dict(tuple(spx.groupby("day")))

    def state_at(day, ts_et, K, side, sigma):
        """(D, p_mkt, S) for strike K at an ET timestamp."""
        bars = by_day[day]
        bar = bars[bars["ts"] <= ts_et]
        S = bar["close"].iloc[-1] if len(bar) else bars["open"].iloc[0]
        T = time_to_settle(ts_et.to_pydatetime() if hasattr(ts_et, "to_pydatetime")
                           else ts_et, is_half_day=day in half)
        m = (ts_et - ts_et.replace(hour=9, minute=30)).total_seconds() / 60
        D = normalized_distance(K, S, sigma, T, side)
        return D, p_mkt(K, S, sigma, T, side, m), S

    rows = []
    skipped = 0
    for ent, ex in load_trades():
        day = ent["time"][:10]
        side = SIDE_MAP[ent["side"]]
        K = float(ent["upperStrike"])
        try:
            sigma = anchor.sigma(day)
        except KeyError:
            skipped += 1
            continue
        bars = by_day.get(day)
        if bars is None:
            skipped += 1
            continue
        ent_et = datetime.strptime(ent["time"][:19], "%Y-%m-%d %H:%M:%S") + PT_TO_ET
        ex_et = datetime.strptime(ex["time"][:19], "%Y-%m-%d %H:%M:%S") + PT_TO_ET
        is_expiry = ex["reason"] == "expiration"
        try:
            d_in, p_in, _ = state_at(day, ent_et, K, side, sigma)
            if is_expiry:
                d_out = p_out = ""
            else:
                d_out, p_out, _ = state_at(day, ex_et, K, side, sigma)
        except ValueError:
            skipped += 1
            continue

        settle = bars["close"].iloc[-1]
        after = bars[bars["ts"] > ex_et]
        touched = bool(((after["high"] >= K) if side == "c"
                        else (after["low"] <= K)).any()) if not is_expiry else False
        if side == "p":
            intrinsic = max(0.0, K - settle) - max(0.0, float(ent["lowerStrike"]) - settle)
            settle_ok = settle > K
        else:
            intrinsic = max(0.0, settle - K) - max(0.0, settle - float(ent["lowerStrike"]))
            settle_ok = settle < K
        held = ent["credit"] - intrinsic * 100 * ent["quantity"]

        rows.append({
            "day": day, "side": side, "reason": ex["reason"],
            "vix1d": round(sigma * 100, 2), "regime": tercile(sigma * 100),
            "entry_et": ent_et.strftime("%H:%M"), "exit_et": ex_et.strftime("%H:%M"),
            "short": K, "D_entry": round(d_in, 3), "pmkt_entry": round(p_in, 4),
            "D_exit": round(d_out, 3) if d_out != "" else "",
            "pmkt_exit": round(p_out, 4) if p_out != "" else "",
            "actual_pnl": ex["profit"], "held_pnl": round(held, 2),
            "touched_after_exit": int(touched), "settle_beyond": int(settle_ok),
        })

    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"analyzed {len(rows)} trades (skipped {skipped}) -> {OUT}\n")

    # ---- summary tables ----
    early = [r for r in rows if r["reason"] != "expiration"]
    print("=== entry state (all trades) ===")
    print(f"  median D at entry: {median(r['D_entry'] for r in rows):.2f}   "
          f"median p_mkt at entry: {median(r['pmkt_entry'] for r in rows):.3f}")

    print("\n=== exits by reason x regime ===")
    print(f"{'reason':>18} {'regime':>9} {'n':>4} {'D_exit':>7} {'pmkt_exit':>9} "
          f"{'touch%':>7} {'settle_ok%':>10} {'exit cost':>10}")
    for reason in ("risk_off_reversal", "sr_inner_breach"):
        for reg in ("calm", "mid", "elevated"):
            sel = [r for r in early if r["reason"] == reason and r["regime"] == reg]
            if not sel:
                continue
            cost = sum(r["held_pnl"] - r["actual_pnl"] for r in sel)
            print(f"{reason:>18} {reg:>9} {len(sel):4d} "
                  f"{median(r['D_exit'] for r in sel):7.2f} "
                  f"{median(r['pmkt_exit'] for r in sel):9.3f} "
                  f"{mean(r['touched_after_exit'] for r in sel):7.0%} "
                  f"{mean(r['settle_beyond'] for r in sel):10.0%} "
                  f"{cost:+10,.0f}")

    print("\n=== the discrimination question: p_mkt at exit vs outcome ===")
    for lo, hi in ((0.0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 1.01)):
        sel = [r for r in early if lo <= float(r["pmkt_exit"]) < hi]
        if not sel:
            continue
        ok = mean(r["settle_beyond"] for r in sel)
        cost = sum(r["held_pnl"] - r["actual_pnl"] for r in sel)
        print(f"  p_mkt(exit) in [{lo:.2f},{hi:.2f}): n={len(sel):3d}  "
              f"actually settled safe: {ok:4.0%}  cost of exiting: {cost:+10,.0f}")


if __name__ == "__main__":
    main()
