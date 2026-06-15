#!/usr/bin/env python3
"""v2 Phase 1 — calibrate/validate the adverse-reversal label against real $.

Matches the trader's logged trades (analysis/exit_characterization.csv) to their
OR breakouts and checks that the price-fact label `max_adverse_orw` predicts the
real dollar outcomes — the de-risk before building features. RESTRICTED to
pre-V2_TEST_START trades (the locked chain era is sealed for the Phase-4 judge).

Result (frozen REVERSAL_K=1.0, trader-ratified 2026-06-14):
  - winners (pnl>0) max_adverse_orw med ~0.30  vs  losers ~1.67  (~5x separation)
  - expiration/held winners ~0.18; risk_off_reversal (OR-puncture) ~1.16
  - k=1.0 flags 66% of real losers, 16% of winners.

Side mapping (trader-confirmed): put spread -> UP breakout, call spread -> DOWN.

Run:  .venv/bin/python analysis/breakout_label_calib.py
"""

import csv
import os
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakouts import build_events, REVERSAL_K, V2_TEST_START  # noqa: E402

DIR = {"p": "up", "c": "down"}


def _f(s):
    try:
        return float(s)
    except ValueError:
        return None


def _emod(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m) - 570


def load_matched():
    rows = [x for x in csv.DictReader(open(os.path.join(os.path.dirname(__file__),
            "exit_characterization.csv"))) if x["day"] < V2_TEST_START]
    ev = build_events(end="2025-06-30")
    by_day = {}
    for e in ev.to_dict("records"):
        by_day.setdefault((e["day"], e["side"]), []).append(e)
    for x in rows:
        x["pnl"], x["em"] = _f(x["actual_pnl"]), _emod(x["entry_et"])
        cands = [e for e in by_day.get((x["day"], DIR[x["side"]]), [])
                 if e["start_mod"] <= x["em"]]
        x["madv"] = (max(cands, key=lambda e: e["start_mod"])["max_adverse_orw"]
                     if cands else None)
    return [x for x in rows if x["madv"] is not None]


def main():
    rows = load_matched()
    W = [x for x in rows if x["pnl"] and x["pnl"] > 0]
    L = [x for x in rows if x["pnl"] and x["pnl"] < 0]
    print(f"matched {len(rows)} pre-{V2_TEST_START} trades: {len(W)} winners, {len(L)} losers\n")

    def med(sub):
        return st.median([x["madv"] for x in sub]) if sub else float("nan")
    print("max_adverse_orw by exit reason:")
    for r in ("expiration", "risk_off_reversal", "sr_inner_breach"):
        sub = [x for x in rows if x["reason"] == r]
        print(f"  {r:>18}: n={len(sub):3d}  med={med(sub):.2f}")
    print(f"\n  WINNERS med={med(W):.2f}   LOSERS med={med(L):.2f}\n")

    print(f"{'k':>5} {'losers flagged':>15} {'winners flagged':>16}")
    for k in (0.6, 0.8, 1.0, 1.2, 1.5):
        lr = sum(x["madv"] >= k for x in L) / len(L)
        wf = sum(x["madv"] >= k for x in W) / len(W)
        star = "  <- FROZEN REVERSAL_K" if k == REVERSAL_K else ""
        print(f"{k:>5} {lr:>14.0%} {wf:>16.0%}{star}")


if __name__ == "__main__":
    main()
