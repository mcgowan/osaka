#!/usr/bin/env python3
"""Task 1.3: empirical distance<->delta mapping from the recorded chains.

For a VIX1D-stratified sample of recorded days x several PT snapshot times
x both sides: collect (D, |recorded delta|) pairs near the money, then
interpolate the normalized distance D at target deltas. Aggregate to fix
the per-side bucket edges (incl. the band of record) in D units.

Run:   .venv/bin/python analysis/delta_mapping.py            # extract + summarize
       .venv/bin/python analysis/delta_mapping.py --summarize  # reuse CSV

Output: analysis/delta_mapping.csv (one row per day/snap/side/target delta)
plus printed aggregation tables for docs/calibration.md.
"""

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data import chains  # noqa: E402
from data.loader import load_bars, load_calendar  # noqa: E402
from quant.conventions import SigmaAnchor, time_to_settle  # noqa: E402
from quant.distance import normalized_distance  # noqa: E402

SNAP_TIMES_PT = ["06:35", "08:00", "10:00", "12:00"]
TARGET_DELTAS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
N_SAMPLE_DAYS = 60
OUT = os.path.join(os.path.dirname(__file__), "delta_mapping.csv")
PT_TO_ET = timedelta(hours=3)


def sample_days(anchor):
    """VIX1D-stratified sample: sort archive days by prior-close VIX1D and
    take every k-th, always keeping both extremes."""
    days = []
    for d in chains.list_days():
        try:
            days.append((anchor.sigma(d) * 100, d))
        except KeyError:
            continue
    days.sort()
    k = max(1, len(days) // N_SAMPLE_DAYS)
    picked = days[::k]
    if days[-1] not in picked:
        picked.append(days[-1])
    return [d for _, d in picked]


def d_at_target_deltas(pairs, targets):
    """pairs: [(D, |delta|)] OTM side. Interpolate D at each target delta.
    |delta| must decrease as D increases; violators (stale quotes) dropped."""
    pairs = sorted(p for p in pairs if 0.005 < p[1] < 0.65)
    clean = []
    for D, d in pairs:
        if not clean or d < clean[-1][1]:
            clean.append((D, d))
    out = {}
    for tgt in targets:
        for (D0, d0), (D1, d1) in zip(clean, clean[1:]):
            lo, hi = min(d0, d1), max(d0, d1)
            if lo <= tgt <= hi:
                w = (tgt - d0) / (d1 - d0)
                out[tgt] = D0 + w * (D1 - D0)
                break
    return out


def extract():
    anchor = SigmaAnchor()  # prior_close, the frozen default
    cal = load_calendar()
    half = set(cal[cal["is_half_day"]]["date"].dt.strftime("%Y-%m-%d"))
    spx = load_bars("SPX", start="2024-12-01")
    spx_open = {ts.strftime("%Y-%m-%d %H:%M"): o
                for ts, o in zip(spx["ts"], spx["open"])}

    days = sample_days(anchor)
    print(f"sampling {len(days)} of {len(chains.list_days())} archive days",
          file=sys.stderr)
    rows = []
    for n, day in enumerate(days):
        sigma = anchor.sigma(day)
        is_half = day in half
        snaps = [datetime.strptime(f"{day} {t}", "%Y-%m-%d %H:%M")
                 for t in SNAP_TIMES_PT]
        if is_half:
            snaps = [s for s in snaps if s.hour + 3 < 13]
        spots = {}
        for s in snaps:
            o = spx_open.get((s + PT_TO_ET).strftime("%Y-%m-%d %H:%M"))
            if o:
                spots[s] = o
        if not spots:
            continue
        smin = min(spots.values())
        smax = max(spots.values())
        view = chains.chain_asof(day, list(spots), int(smin * 0.975),
                                 int(smax * 1.025))
        for snap, S in spots.items():
            T = time_to_settle(snap + PT_TO_ET, is_half_day=is_half)
            for side in ("p", "c"):
                pairs = []
                for (k, s), (delta, iv, ts) in view[snap].items():
                    if s != side:
                        continue
                    if (side == "p" and k >= S) or (side == "c" and k <= S):
                        continue
                    D = normalized_distance(k, S, sigma, T, side)
                    if D > 0:
                        pairs.append((D, abs(delta)))
                for tgt, D in d_at_target_deltas(pairs, TARGET_DELTAS).items():
                    rows.append({
                        "day": day, "vix1d_anchor": round(sigma * 100, 2),
                        "snap_pt": snap.strftime("%H:%M"), "side": side,
                        "target_delta": tgt, "D": round(D, 4),
                        "spot": S, "half_day": int(is_half),
                    })
        if (n + 1) % 10 == 0:
            print(f"  {n + 1}/{len(days)} days", file=sys.stderr)

    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {OUT}", file=sys.stderr)
    return rows


def summarize(rows):
    from statistics import median, quantiles

    def med_iqr(vals):
        if len(vals) < 4:
            return f"n={len(vals)} (too few)"
        q = quantiles(vals, n=4)
        return f"{median(vals):5.3f}  [{q[0]:5.3f}..{q[2]:5.3f}]  n={len(vals)}"

    # regime terciles from the sampled days' anchor values
    anchors = sorted({(r["day"], float(r["vix1d_anchor"])) for r in rows})
    vals = sorted(v for _, v in anchors)
    t1, t2 = vals[len(vals) // 3], vals[2 * len(vals) // 3]
    print(f"\nVIX1D anchor terciles within sample: <{t1:.1f} / "
          f"{t1:.1f}-{t2:.1f} / >{t2:.1f}")

    def tercile(v):
        return "calm" if v < t1 else ("mid" if v < t2 else "elevated")

    print("\n=== D at target delta: median [IQR] ===")
    print(f"{'side':>4} {'delta':>6} {'ALL':>26} | {'calm':>10} {'mid':>7} "
          f"{'elev':>7} | {'06:35':>7} {'10:00':>7}")
    for side in ("p", "c"):
        for tgt in TARGET_DELTAS:
            sel = [r for r in rows
                   if r["side"] == side and float(r["target_delta"]) == tgt]
            if not sel:
                continue
            all_d = [float(r["D"]) for r in sel]
            by_t = {t: [float(r["D"]) for r in sel
                        if tercile(float(r["vix1d_anchor"])) == t]
                    for t in ("calm", "mid", "elevated")}
            by_s = {s: [float(r["D"]) for r in sel if r["snap_pt"] == s]
                    for s in ("06:35", "10:00")}
            from statistics import median as md
            cells = [f"{md(v):7.3f}" if len(v) >= 4 else "      -"
                     for v in (by_t["calm"], by_t["mid"], by_t["elevated"],
                               by_s["06:35"], by_s["10:00"])]
            print(f"{side:>4} {tgt:6.2f} {med_iqr(all_d):>26} |"
                  f"{cells[0]} {cells[1]} {cells[2]} |{cells[3]} {cells[4]}")

    print("\n=== proposed frozen bucket edges (median D at delta edges) ===")
    from statistics import median as md
    for side in ("p", "c"):
        edges = {}
        for tgt in (0.05, 0.10, 0.15, 0.25):
            sel = [float(r["D"]) for r in rows
                   if r["side"] == side and float(r["target_delta"]) == tgt]
            if sel:
                edges[tgt] = md(sel)
        print(f"  {side}: " + "  ".join(
            f"D({d:.2f}Δ)={v:.3f}" for d, v in edges.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summarize", action="store_true",
                    help="summarize existing CSV without re-extracting")
    args = ap.parse_args()
    if args.summarize and os.path.exists(OUT):
        rows = list(csv.DictReader(open(OUT)))
    else:
        rows = extract()
        rows = list(csv.DictReader(open(OUT)))
    summarize(rows)


if __name__ == "__main__":
    main()
