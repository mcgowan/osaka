#!/usr/bin/env python3
"""Task 1.7: sensitivity of the frozen Phase-1 machinery to its choices.

Checks (labels are sigma-free by construction and not touched here):
  A. Sigma anchor mode: prior_close (frozen) vs day_open - effect on D,
     bucket assignment, and p_mkt at the grid strikes.
  B. f(t) knots perturbed +/-5% (fit noise) - effect on band-of-record p_mkt.
  C. Bucket fidelity: D-bucket vs recorded-delta bucket agreement
     (how often is a "band of record" row truly 0.10-0.15 delta).

Reads analysis/baseline_validation.csv. Prints the memo numbers.
"""

import csv
import os
import sys
from datetime import datetime, timedelta
from statistics import mean, median

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.loader import load_calendar  # noqa: E402
from quant.conventions import SigmaAnchor, delta_bucket, time_to_settle  # noqa: E402
from quant.pmkt import no_touch_prob, p_mkt  # noqa: E402

SRC = os.path.join(os.path.dirname(__file__), "baseline_validation.csv")
PT_TO_ET = timedelta(hours=3)
SNAP_MIN = {"06:35": 5, "08:00": 90, "10:00": 210, "12:00": 330}


def main():
    rows = []
    for r in csv.DictReader(open(SRC)):
        for k in ("D", "spot", "strike", "vix1d_anchor", "delta_rec",
                  "p_anchor", "p_chainiv"):
            r[k] = float(r[k])
        rows.append(r)
    cal = load_calendar()
    half = set(cal[cal["is_half_day"]]["date"].dt.strftime("%Y-%m-%d"))

    def T_of(r):
        snap_et = datetime.strptime(f"{r['day']} {r['snap_pt']}",
                                    "%Y-%m-%d %H:%M") + PT_TO_ET
        return time_to_settle(snap_et, is_half_day=r["day"] in half)

    # --- A. anchor mode swap --------------------------------------------
    do = SigmaAnchor("day_open")
    moved, p_shift, d_rel = 0, [], []
    n = 0
    for r in rows:
        try:
            s_do = do.sigma(r["day"])
        except KeyError:
            continue
        s_pc = r["vix1d_anchor"] / 100.0
        n += 1
        D_new = r["D"] * s_pc / s_do
        d_rel.append(abs(D_new - r["D"]) / r["D"])
        if delta_bucket(D_new, r["side"]) != r["bucket"]:
            moved += 1
        T = T_of(r)
        p_new = p_mkt(r["strike"], r["spot"], s_do, T, r["side"],
                      SNAP_MIN[r["snap_pt"]])
        p_old = p_mkt(r["strike"], r["spot"], s_pc, T, r["side"],
                      SNAP_MIN[r["snap_pt"]])
        p_shift.append(abs(p_new - p_old))
    print("=== A. sigma anchor: prior_close -> day_open ===")
    print(f"  n={n}  median |dD|/D: {median(d_rel):.1%}  "
          f"bucket reassigned: {moved / n:.1%}  "
          f"median |d p_mkt|: {median(p_shift):.4f}  mean: {mean(p_shift):.4f}")

    # --- B. f(t) +/-5% ----------------------------------------------------
    print("\n=== B. f(t) knots +/-5% (band of record rows) ===")
    for scale in (0.95, 1.05):
        shifts = []
        for r in rows:
            if r["bucket"] != "10-15":
                continue
            T = T_of(r)
            s_pc = r["vix1d_anchor"] / 100.0
            base = p_mkt(r["strike"], r["spot"], s_pc, T, r["side"],
                         SNAP_MIN[r["snap_pt"]])
            pert = no_touch_prob(r["strike"], r["spot"],
                                 scale * _f(r) * s_pc, T, r["side"])
            shifts.append(abs(pert - base))
        print(f"  scale {scale}: median |d p_mkt| {median(shifts):.4f}  "
              f"mean {mean(shifts):.4f}  (n={len(shifts)})")

    # --- C. bucket fidelity vs recorded delta ------------------------------
    print("\n=== C. D-bucket vs recorded-delta bucket ===")
    edges = (0.05, 0.10, 0.15, 0.25)

    def delta_bucket_true(d):
        if d < edges[0]:
            return "lt05"
        if d < edges[1]:
            return "05-10"
        if d < edges[2]:
            return "10-15"
        if d < edges[3]:
            return "15-25"
        return "gt25"

    for bucket in ("05-10", "10-15", "15-25"):
        sel = [r for r in rows if r["bucket"] == bucket]
        agree = mean(delta_bucket_true(r["delta_rec"]) == bucket for r in sel)
        adjacent = mean(
            abs("lt05 05-10 10-15 15-25 gt25".split().index(
                delta_bucket_true(r["delta_rec"]))
                - "lt05 05-10 10-15 15-25 gt25".split().index(bucket)) <= 1
            for r in sel)
        deltas = [r["delta_rec"] for r in sel]
        print(f"  {bucket}: exact agreement {agree:.0%}, within-one-bucket "
              f"{adjacent:.0%}, recorded-delta IQR "
              f"[{sorted(deltas)[len(deltas)//4]:.3f}.."
              f"{sorted(deltas)[3*len(deltas)//4]:.3f}] (n={len(sel)})")


def _f(r):
    from quant.conventions import f_correction
    return f_correction(r["side"], SNAP_MIN[r["snap_pt"]])


if __name__ == "__main__":
    main()
