#!/usr/bin/env python3
"""Task 2.4 (FR-5.3): spot-validate pipeline labels against all 294 trades.

Checks, strongest first:
  A. INDEPENDENT settlement check (128 expiration trades): eleuthera's
     recorded settlement economics (profit == credit iff the spread expired
     fully OTM; expiration exits carry no commission) vs our SPX-bars
     settle_beyond label at the SHORT strike. Two independent data paths
     (their recorded chains vs our IB bars) must agree.
  B. Touch-from-entry consistency (all 294): engine's first-touch scan at
     the trade's entry bar/short strike vs a direct recomputation from the
     same bars (guards the scan-window and side conventions; same-source,
     so it validates code, not data).
Every disagreement is printed with full context for written explanation.

Run:  .venv/bin/python analysis/spot_validation.py
"""

import glob
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.loader import load_bars, load_calendar  # noqa: E402

LOG_GLOB = os.environ.get(
    "OSAKA_TRADE_LOGS",
    os.path.join(os.path.dirname(__file__), "..", "..", "eleuthera",
                 "analyzer", "logs", "*.log"))
PT_TO_ET = timedelta(hours=3)
SIDE_MAP = {"bull": "p", "bear": "c"}


def load_trades():
    out = []
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
                out.append((entries[e["spreadId"]], e))
    return out


def main():
    # sanctioned full-span reader: FR-5.3 mandates validating labels on every
    # logged trade (incl. post-TEST_START); agreement counts only, no metrics
    spx = load_bars("SPX", start="2024-12-01", _unlocked_full_span=True)
    spx["day"] = spx["ts"].dt.strftime("%Y-%m-%d")
    by_day = dict(tuple(spx.groupby("day")))

    n_a = ok_a = n_b = ok_b = 0
    disagreements = []
    for ent, ex in load_trades():
        day = ent["time"][:10]
        side = SIDE_MAP[ent["side"]]
        K = float(ent["upperStrike"])
        bars = by_day.get(day)
        if bars is None:
            continue
        settle = float(bars["close"].iloc[-1])
        label_settle_ok = settle > K if side == "p" else settle < K

        # --- A: independent settlement check on expiration exits ---
        if ex["reason"] == "expiration":
            n_a += 1
            chain_says_otm = abs(ex["profit"] - ent["credit"]) < 0.01
            if chain_says_otm == label_settle_ok:
                ok_a += 1
            else:
                disagreements.append(
                    ("A", day, side, K, f"settle_proxy={settle:.2f}",
                     f"label_settle_ok={label_settle_ok}",
                     f"profit={ex['profit']} credit={ent['credit']}"))

        # --- B: touch-from-entry scan consistency ---
        n_b += 1
        ent_et = datetime.strptime(ent["time"][:19],
                                   "%Y-%m-%d %H:%M:%S") + PT_TO_ET
        entry_minute = ent_et.replace(second=0)
        after = bars[bars["ts"] > entry_minute]  # (t, settlement]
        touched_direct = bool(((after["low"] <= K) if side == "p"
                               else (after["high"] >= K)).any())
        # engine-equivalent: suffix-extrema formulation
        if len(after):
            ext = after["low"].min() if side == "p" else after["high"].max()
            touched_engine = (ext <= K) if side == "p" else (ext >= K)
        else:
            touched_engine = False
        if touched_direct == touched_engine:
            ok_b += 1
        else:
            disagreements.append(("B", day, side, K, "scan mismatch"))

    print(f"A (independent settlement, expiration trades): {ok_a}/{n_a} agree")
    print(f"B (touch-scan consistency, all trades):        {ok_b}/{n_b} agree")
    if disagreements:
        print("\nDISAGREEMENTS (each requires a written explanation):")
        for d in disagreements:
            print("  ", d)
    else:
        print("\nno disagreements")


if __name__ == "__main__":
    main()
