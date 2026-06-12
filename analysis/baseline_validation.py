#!/usr/bin/env python3
"""Task 1.4 (FR-5.1): validate the analytic p_mkt baseline against the market.

Reference truth: the same first-passage formula evaluated with each strike's
RECORDED implied vol from the chain archive (IB's IV uses the same
calendar-time convention as ours - verified in the Phase S spike). Our
baseline: the formula with the frozen prior-close VIX1D anchor.

    bias = p_anchor - p_chainIV          (per grid strike)

Evaluated at the actual FR-4 grid strikes over the same 62-day stratified
sample as task 1.3. Also fits a time-of-day correction f(t) on odd-indexed
days (median IV/anchor ratio per snapshot, per side) and reports held-out
(even-day) bias reduction, to decide raw-anchor vs f(t)-corrected baseline.

FROZEN PRE-LOCK CALIBRATION (redteam F6, trader-ratified): F_T_KNOTS was
fit on the full 2024-12->2026-06 chain span, which straddles TEST_START.
Documented rather than refit (would reopen Gate 1; f(t) contamination
hardens the baseline, i.e. is conservative for the edge claim). This
script reads FULL-SPAN data via _unlocked_full_span=True so re-running
reproduces the frozen values exactly. Do NOT refit from a truncated run.

Run:  .venv/bin/python analysis/baseline_validation.py [--summarize]
Output: analysis/baseline_validation.csv + printed tables for docs/calibration.md
"""

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta
from statistics import mean, median

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from analysis.delta_mapping import SNAP_TIMES_PT, sample_days  # noqa: E402
from data import chains  # noqa: E402
from data.loader import load_bars, load_calendar  # noqa: E402
from quant.conventions import SigmaAnchor, delta_bucket, time_to_settle  # noqa: E402
from quant.distance import place_grid  # noqa: E402
from quant.pmkt import no_touch_prob  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "baseline_validation.csv")
PT_TO_ET = timedelta(hours=3)


def extract():
    # full-span: reproduces the frozen pre-lock Phase-1 fit (see header)
    anchor = SigmaAnchor(_unlocked_full_span=True)
    cal = load_calendar()
    half = set(cal[cal["is_half_day"]]["date"].dt.strftime("%Y-%m-%d"))
    spx = load_bars("SPX", start="2024-12-01", _unlocked_full_span=True)
    spx_open = {ts.strftime("%Y-%m-%d %H:%M"): o
                for ts, o in zip(spx["ts"], spx["open"])}

    days = sample_days(anchor)
    rows = []
    for n, day in enumerate(days):
        sigma_a = anchor.sigma(day)
        is_half = day in half
        for hhmm in SNAP_TIMES_PT:
            snap = datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M")
            if is_half and snap.hour + 3 >= 13:
                continue
            S = spx_open.get((snap + PT_TO_ET).strftime("%Y-%m-%d %H:%M"))
            if not S:
                continue
            T = time_to_settle(snap + PT_TO_ET, is_half_day=is_half)
            for side in ("p", "c"):
                for a, K, D in place_grid(S, sigma_a, T, side):
                    rec = chains.contract_asof(day, int(K), side, [snap])
                    if snap not in rec:
                        continue
                    delta_rec, iv, _ = rec[snap]
                    if not (0.001 < abs(delta_rec) < 0.9) or not (0.01 < iv < 3):
                        continue
                    rows.append({
                        "day": day, "snap_pt": hhmm, "side": side,
                        "anchor": a, "strike": K, "spot": S, "D": round(D, 4),
                        "bucket": delta_bucket(D, side),
                        "vix1d_anchor": round(sigma_a * 100, 2),
                        "delta_rec": round(abs(delta_rec), 4),
                        "iv": round(iv, 4),
                        "ratio": round(iv / sigma_a, 4),
                        "p_anchor": round(no_touch_prob(K, S, sigma_a, T, side), 4),
                        "p_chainiv": round(no_touch_prob(K, S, iv, T, side), 4),
                    })
        if (n + 1) % 10 == 0:
            print(f"  {n + 1}/{len(days)} days", file=sys.stderr)

    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {OUT}", file=sys.stderr)


def summarize():
    rows = []
    for r in csv.DictReader(open(OUT)):
        for k in ("D", "spot", "strike", "vix1d_anchor", "delta_rec", "iv",
                  "ratio", "p_anchor", "p_chainiv"):
            r[k] = float(r[k])
        rows.append(r)

    # Chronological split with an embargo gap (inviolable rule #2 applies to
    # experiments too): fit = first 60% of sampled days, skip one sampled day
    # (sampled days are ~5 trading days apart, so the gap is ~a calendar
    # week), eval = the remainder. NOTE: an earlier draft used interleaved
    # days[::2], which is mildly optimistic under day-autocorrelated regimes;
    # the f(t) decision survives either split (capacity = 4 medians/side).
    days = sorted({r["day"] for r in rows})
    cut = int(len(days) * 0.6)
    fit_days = set(days[:cut])
    eval_days = set(days[cut + 1:])  # +1 = embargo gap of one sampled day

    # --- raw-anchor bias by side x bucket ---
    print("=== bias = p_anchor - p_chainIV (raw VIX1D anchor) ===")
    print(f"{'side':>4} {'bucket':>6} {'n':>5} {'mean bias':>10} {'MAE':>7} "
          f"{'mean p_chainIV':>14}")
    for side in ("p", "c"):
        for bucket in ("lt05", "05-10", "10-15", "15-25", "gt25"):
            sel = [r for r in rows if r["side"] == side and r["bucket"] == bucket]
            if len(sel) < 10:
                continue
            b = [r["p_anchor"] - r["p_chainiv"] for r in sel]
            print(f"{side:>4} {bucket:>6} {len(sel):5d} {mean(b):+10.4f} "
                  f"{mean(map(abs, b)):7.4f} "
                  f"{mean(r['p_chainiv'] for r in sel):14.4f}")

    # --- bias by snapshot time (band of record only) ---
    print("\n=== band-of-record bias by snapshot (raw anchor) ===")
    for side in ("p", "c"):
        for hhmm in SNAP_TIMES_PT:
            sel = [r for r in rows if r["side"] == side
                   and r["bucket"] == "10-15" and r["snap_pt"] == hhmm]
            if len(sel) < 10:
                continue
            b = [r["p_anchor"] - r["p_chainiv"] for r in sel]
            print(f"  {side} {hhmm}: n={len(sel):4d} mean {mean(b):+.4f} "
                  f"MAE {mean(map(abs, b)):.4f}")

    # --- f(t) correction: fit on fit_days, evaluate on eval_days ---
    f_t = {}
    for side in ("p", "c"):
        for hhmm in SNAP_TIMES_PT:
            sel = [r["ratio"] for r in rows if r["side"] == side
                   and r["snap_pt"] == hhmm and r["day"] in fit_days]
            if sel:
                f_t[(side, hhmm)] = median(sel)
    print(f"\n=== fitted f(t) = median IV/anchor "
          f"(chronological fit: {len(fit_days)} days through {max(fit_days)}; "
          f"eval: {len(eval_days)} days from {min(eval_days)}) ===")
    for (side, hhmm), v in sorted(f_t.items()):
        print(f"  {side} {hhmm}: {v:.3f}")

    print("\n=== held-out (chronological, embargoed) bias by bucket: "
          "raw vs f(t)-corrected ===")
    cal = load_calendar()
    half = set(cal[cal["is_half_day"]]["date"].dt.strftime("%Y-%m-%d"))
    print(f"{'side':>4} {'bucket':>6} {'n':>5} {'raw mean':>9} {'raw MAE':>8} "
          f"{'f(t) mean':>10} {'f(t) MAE':>9}")
    for side in ("p", "c"):
        for bucket in ("05-10", "10-15", "15-25"):
            raw_b, fix_b = [], []
            for r in rows:
                if r["side"] != side or r["bucket"] != bucket \
                        or r["day"] not in eval_days:
                    continue
                key = (side, r["snap_pt"])
                if key not in f_t:
                    continue
                snap_et = datetime.strptime(f"{r['day']} {r['snap_pt']}",
                                            "%Y-%m-%d %H:%M") + PT_TO_ET
                T = time_to_settle(snap_et, is_half_day=r["day"] in half)
                sigma_c = f_t[key] * r["vix1d_anchor"] / 100.0
                p_fix = no_touch_prob(r["strike"], r["spot"], sigma_c, T, side)
                raw_b.append(r["p_anchor"] - r["p_chainiv"])
                fix_b.append(p_fix - r["p_chainiv"])
            if raw_b:
                print(f"{side:>4} {bucket:>6} {len(raw_b):5d} "
                      f"{mean(raw_b):+9.4f} {mean(map(abs, raw_b)):8.4f} "
                      f"{mean(fix_b):+10.4f} {mean(map(abs, fix_b)):9.4f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summarize", action="store_true")
    args = ap.parse_args()
    if not (args.summarize and os.path.exists(OUT)):
        extract()
    summarize()


if __name__ == "__main__":
    main()
