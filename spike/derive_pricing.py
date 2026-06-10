#!/usr/bin/env python3
"""Phase S spike: can we derive 0DTE option pricing (strike <-> delta) from
SPX 1-min bars + a vol input, validated against one day of recorded chain data?

Stdlib only (no pandas) so it runs on a bare system Python.

Data (data/raw/, all timestamps US/Pacific):
  2026-06-01-spx-chart.csv          1-min SPX OHLC, multiple days
  2026-06-01-vix-chart.csv          1-min VIX OHLC (NOTE: VIX, not VIX1D)
  2026-06-01-<strike>-{c,p}.csv     every chain update for that contract:
      ts, bid, ask, last, delta, gamma, vega, theta, iv, mark, ?
      (1.7976931348623157e+308 = missing; bid/last of -1 = none)

For each snapshot time t and target delta in {0.05,0.10,0.15,0.20,0.30}:
  actual strike  = interpolated from the chain's recorded per-strike deltas
  derived strike = closed-form BS inversion from S(t), T(t), and a vol input:
      vix    : sigma = VIX(t)/100, raw
      vixadj : sigma = c * VIX(t)/100, c = single day-level constant
               (median ATM_IV/VIX across snapshots - the "level multiplier")
      atmiv  : sigma = chain ATM IV at t (oracle; isolates skew-only error)

Outputs spike/results.csv (long table) and prints summary tables for the memo.
"""

import csv
import math
import os
import sys
from datetime import datetime, timedelta
from statistics import NormalDist, median

RAW = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
DAY = "2026-06-01"
SETTLE = datetime(2026, 6, 1, 13, 0, 0)  # 4:00 PM ET settlement, in PT
YEAR_SECONDS = 365.0 * 24 * 3600          # calendar-time convention (matches
                                          # the platform's vega, verified by hand)
MISSING = 1e308                           # anything this big is the DBL_MAX sentinel
TARGET_DELTAS = [0.05, 0.10, 0.15, 0.20, 0.30]
SNAPSHOT_TIMES = [  # PT; first bar after the open churn, then every 30 min
    "06:35", "07:00", "07:30", "08:00", "08:30", "09:00", "09:30",
    "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
]
STRIKE_MIN, STRIKE_MAX = 7200, 8000       # spot ran 7560-7610; grid deltas live here
STALE_MAX = timedelta(minutes=10)         # ignore chain quotes older than this
N = NormalDist()


def parse_ts(s):
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def load_bars(path, day):
    """{datetime: open} for 1-min bars of the given day."""
    out = {}
    with open(path) as f:
        for row in csv.reader(f):
            if row[0].startswith(day):
                out[parse_ts(row[0])] = float(row[1])
    return out


def bs_strike(side, delta, S, sigma, T):
    """Closed-form BS strike for a target |delta| (rates/divs = 0).
    call: delta = N(d1);  put: |delta| = N(-d1);  d1 = [ln(S/K)+sigma^2 T/2]/(sigma sqrt(T))
    """
    d1 = N.inv_cdf(delta) if side == "c" else -N.inv_cdf(delta)
    return S * math.exp(sigma * sigma * T / 2 - sigma * math.sqrt(T) * d1)


def bs_delta(side, K, S, sigma, T):
    d1 = (math.log(S / K) + sigma * sigma * T / 2) / (sigma * math.sqrt(T))
    return N.cdf(d1) if side == "c" else -N.cdf(-d1)


def chain_files():
    for name in sorted(os.listdir(RAW)):
        parts = name[:-4].split("-")          # [2026, 06, 01, strike, side]
        if len(parts) != 5 or not name.startswith(DAY):
            continue
        strike, side = parts[3], parts[4]
        if side in ("c", "p") and strike.isdigit():
            k = int(strike)
            if STRIKE_MIN <= k <= STRIKE_MAX:
                yield k, side, os.path.join(RAW, name)


def load_chain_asof(snapshots):
    """For each (strike, side), the last valid update at/before each snapshot.
    Returns {(strike, side): {snap_dt: (delta, iv, ts)}}."""
    out = {}
    for k, side, path in chain_files():
        rows = []
        with open(path) as f:
            for row in csv.reader(f):
                try:
                    delta, iv = float(row[4]), float(row[8])
                except (ValueError, IndexError):
                    continue
                if abs(delta) >= MISSING or iv >= MISSING or iv <= 0:
                    continue
                rows.append((parse_ts(row[0]), delta, iv))
        if not rows:
            continue
        rows.sort(key=lambda r: r[0])
        asof, i = {}, 0
        for snap in snapshots:
            while i < len(rows) and rows[i][0] <= snap:
                i += 1
            if i > 0 and snap - rows[i - 1][0] <= STALE_MAX:
                ts, delta, iv = rows[i - 1]
                asof[snap] = (delta, iv, ts)
        if asof:
            out[(k, side)] = asof
    return out


def actual_strike_at_delta(chain, side, snap, target, spot):
    """Interpolate the chain's strike at |delta| == target, OTM side only."""
    pts = []
    for (k, s), asof in chain.items():
        if s != side or snap not in asof:
            continue
        if (side == "p" and k >= spot) or (side == "c" and k <= spot):
            continue
        d = abs(asof[snap][0])
        if 0.005 < d < 0.65:
            pts.append((k, d))
    pts.sort()
    # ascending strike: |delta| must increase for puts, decrease for calls;
    # drop violating points (stale quotes)
    clean = []
    for k, d in pts:
        if not clean or (d > clean[-1][1] if side == "p" else d < clean[-1][1]):
            clean.append((k, d))
    for (k0, d0), (k1, d1) in zip(clean, clean[1:]):
        lo, hi = min(d0, d1), max(d0, d1)
        if lo <= target <= hi:
            w = (target - d0) / (d1 - d0)
            return k0 + w * (k1 - k0)
    return None


def atm_iv(chain, snap, spot):
    """Average IV of the OTM put and OTM call nearest the spot."""
    ivs = []
    for side, pick in (("p", max), ("c", min)):
        ks = [k for (k, s), asof in chain.items()
              if s == side and snap in asof
              and ((side == "p" and k < spot) or (side == "c" and k > spot))]
        if ks:
            k = pick(ks)  # max strike below spot (put) / min above (call)
            ivs.append(chain[(k, side)][snap][1])
    return sum(ivs) / len(ivs) if ivs else None


def main():
    spx = load_bars(os.path.join(RAW, f"{DAY}-spx-chart.csv"), DAY)
    vix = load_bars(os.path.join(RAW, f"{DAY}-vix-chart.csv"), DAY)
    snaps = [datetime.strptime(f"{DAY} {t}", "%Y-%m-%d %H:%M") for t in SNAPSHOT_TIMES]
    chain = load_chain_asof(snaps)
    print(f"chain contracts loaded: {len(chain)}", file=sys.stderr)

    # day-level VIX->0DTE level multiplier from ATM IV (uses the chain once, as
    # a single scalar - the analog of Phase 1's m-calibration, level component)
    ratios = []
    for snap in snaps:
        S, v, a = spx.get(snap), vix.get(snap), atm_iv(chain, snap, spx.get(snap, 0))
        if S and v and a:
            ratios.append(a / (v / 100.0))
    c_level = median(ratios)
    print(f"ATM_IV / VIX ratio: median {c_level:.3f}  "
          f"range {min(ratios):.3f}-{max(ratios):.3f}", file=sys.stderr)

    results = []
    for snap in snaps:
        S, v = spx.get(snap), vix.get(snap)
        if not S or not v:
            print(f"skip {snap}: missing SPX or VIX bar", file=sys.stderr)
            continue
        T = (SETTLE - snap).total_seconds() / YEAR_SECONDS
        a_iv = atm_iv(chain, snap, S)
        sigmas = {"vix": v / 100.0, "vixadj": c_level * v / 100.0}
        if a_iv:
            sigmas["atmiv"] = a_iv
        for side in ("p", "c"):
            for tgt in TARGET_DELTAS:
                actual = actual_strike_at_delta(chain, side, snap, tgt, S)
                if actual is None:
                    continue
                for variant, sigma in sigmas.items():
                    derived = bs_strike(side, tgt, S, sigma, T)
                    m = ((S - actual) / (S - derived)) if side == "p" \
                        else ((actual - S) / (derived - S))
                    results.append({
                        "snap": snap.strftime("%H:%M"), "side": side,
                        "target_delta": tgt, "spot": round(S, 2),
                        "vix": v, "atm_iv": round(a_iv, 4) if a_iv else "",
                        "variant": variant, "sigma": round(sigma, 4),
                        "actual_strike": round(actual, 1),
                        "derived_strike": round(derived, 1),
                        "error_pts": round(derived - actual, 1),
                        "error_increments": round((derived - actual) / 5, 2),
                        "implied_m": round(m, 3),
                    })

    out = os.path.join(os.path.dirname(__file__), "results.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=results[0].keys())
        w.writeheader()
        w.writerows(results)
    print(f"wrote {len(results)} rows -> {out}", file=sys.stderr)

    # ---- summary tables ----
    def fmt(rows):
        errs = [abs(r["error_pts"]) for r in rows]
        within1 = sum(1 for e in errs if e <= 5) / len(errs)
        ms = [r["implied_m"] for r in rows]
        return (f"n={len(errs):3d}  mean|err|={sum(errs)/len(errs):6.1f}pts  "
                f"max|err|={max(errs):6.1f}  within-1-strike={within1:4.0%}  "
                f"m: med={median(ms):.3f} range {min(ms):.3f}-{max(ms):.3f}")

    for variant in ("vix", "vixadj", "atmiv"):
        print(f"\n=== sigma variant: {variant} ===")
        for side in ("p", "c"):
            for tgt in TARGET_DELTAS:
                rows = [r for r in results if r["variant"] == variant
                        and r["side"] == side and r["target_delta"] == tgt]
                if rows:
                    print(f"  {side} {tgt:.2f}d  {fmt(rows)}")


if __name__ == "__main__":
    main()
