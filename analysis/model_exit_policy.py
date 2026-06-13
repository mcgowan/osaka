#!/usr/bin/env python3
"""Model-driven exit POLICY backtest (task-5.4-style indicative economics).

REPLACES the trader's exit rules with a model-driven policy: for each logged
spread, query the frozen v1 model at every stride bar after entry -> p_model
(survival of the short strike to settlement). Exit at the first bar where the
model sees MORE danger than the market implies (p_model < p_mkt); otherwise
hold to settlement. Unlike the veto overlay, this sees the WHOLE path, so it
reaches the near-strike (D<=0.5) states where the model's edge lives - the
false-breakout moments. Partial-credit closes marked with BS-on-VIX1D (approx,
no chains; the task-5.4 economic check).

HEADLINE RULE (parameter-free): exit at first bar with p_model < p_mkt.
SWEEP (shape only, NOT for picking a winner): edge-margin p_model < p_mkt-{.05,.10}
and absolute p_model < {0.6,0.7,0.8}.

Comparisons (per era; TRAIN=in-sample, HELD-OUT=honest): policy vs ALWAYS-HOLD
(settle) vs ACTUAL exits. Actual is shown both as logged (recorded quotes) and
BS-replayed at the logged exit bar, so policy-vs-actual is BS-vs-BS consistent;
the logged figure is reference (BS uses a single flat VIX1D sigma, no skew -
inconsistent with the recorded-quote basis of the logged P&L).

DISCIPLINE: pre-TEST_START trades only (locked period never touched); frozen v1
model read once; deterministic. Conventions: decision + BS mark at stride bars
with S=close(t); D/pmkt recomputed per bar for the actual short strike, all
other features snapped from the master stride row (PIT).
"""

import json
import math
import os
import sys
from datetime import datetime, timedelta
from statistics import NormalDist

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.features import load_master  # noqa: E402
from data.loader import TEST_START, load_bars, load_calendar  # noqa: E402
from models import gbt  # noqa: E402
from models.splits import make_split  # noqa: E402
from quant.conventions import SigmaAnchor, time_to_settle  # noqa: E402
from quant.distance import normalized_distance  # noqa: E402
from quant.pmkt import p_mkt  # noqa: E402
from data import chains  # noqa: E402
from analysis.exit_characterization import load_trades, SIDE_MAP, PT_TO_ET  # noqa: E402

N = NormalDist()


_MID_CACHE = {}


def _mid(day, strike, side, snap_pt):
    key = (day, strike, side, snap_pt)
    if key not in _MID_CACHE:
        q = chains.contract_quote_asof(day, strike, side, [snap_pt])
        _MID_CACHE[key] = (q[snap_pt][0] + q[snap_pt][1]) / 2 if snap_pt in q else None
    return _MID_CACHE[key]


def chain_close_cost(day, minute_et, K, Kl, side):
    """Spread debit (points) to close at an ET stride minute, from recorded
    chain MIDS. Chain ts are PT (ET-3h). Returns None if either leg's quote is
    missing/stale at that time. Capped to [0, width]."""
    base = datetime.strptime(day, "%Y-%m-%d")
    snap_pt = base + timedelta(hours=6, minutes=30 + int(minute_et))  # 9:30 ET - 3h
    mid_s = _mid(day, int(round(K)), side, snap_pt)
    mid_l = _mid(day, int(round(Kl)), side, snap_pt)
    if mid_s is None or mid_l is None:
        return None
    return min(max(mid_s - mid_l, 0.0), abs(K - Kl))


def bs(S, K, sigma, T, kind):
    """Black-Scholes price, r=0. kind 'c'/'p'; sigma decimal, T year-fraction."""
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if kind == "c" else max(0.0, K - S)
    sd = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if kind == "c":
        return S * N.cdf(d1) - K * N.cdf(d2)
    return K * N.cdf(-d2) - S * N.cdf(-d1)


def spread_cost(S, K_short, K_long, sigma, T, side):
    """Debit (points) to close the credit spread; capped to [0, width]."""
    v = bs(S, K_short, sigma, T, side) - bs(S, K_long, sigma, T, side)
    return min(max(v, 0.0), abs(K_short - K_long))


def _d(x):
    return f"${x:+,.0f}"


def main():
    master = load_master()
    split = make_split(sorted(master["day"].unique()))
    train_days = set(split.train_days)
    cfg = json.load(open(os.path.join(os.path.dirname(__file__), "..",
                                      "models", "gbt_params.json")))["with_pmkt"]
    booster, fl = gbt.train(master[master["day"].isin(split.train_days)],
                            params=cfg["params"], num_rounds=cfg["num_rounds"],
                            include_pmkt=True)
    nonstrike = [f for f in fl if f not in ("D", "pmkt")]

    anchor = SigmaAnchor(_unlocked_full_span=True)   # FR-5.3 sanctioned reader
    cal = load_calendar()
    half = set(cal[cal["is_half_day"]]["date"].dt.strftime("%Y-%m-%d"))
    spx = load_bars("SPX")                            # default-truncated (pre-test)
    spx["day"] = spx["ts"].dt.strftime("%Y-%m-%d")
    spx["mso"] = ((spx["ts"] - spx["ts"].dt.normalize()
                   - pd.Timedelta(hours=9, minutes=30)).dt.total_seconds()
                  // 60).astype(int)
    spx = spx.drop_duplicates(["day", "mso"])
    spx_by_day = {d: g.set_index("mso")["close"] for d, g in spx.groupby("day")}

    mrep = master.drop_duplicates(["day", "minute", "side"])
    mlook = {}
    for (day, side), g in mrep.groupby(["day", "side"]):
        mlook[(day, side)] = dict(zip(g["minute"].astype(int),
                                      g[nonstrike].to_dict("records")))

    rows, meta, trades = [], [], []
    for ent, ex in load_trades():
        day = ent["time"][:10]
        if day >= TEST_START:                # rule #1: locked period untouched
            continue
        side = SIDE_MAP[ent["side"]]
        K, Kl = float(ent["upperStrike"]), float(ent["lowerStrike"])
        try:
            sigma = anchor.sigma(day)
        except KeyError:
            continue
        if day not in spx_by_day or (day, side) not in mlook:
            continue
        base = datetime.strptime(day, "%Y-%m-%d")
        ent_min = int(((datetime.strptime(ent["time"][:19], "%Y-%m-%d %H:%M:%S")
                        + PT_TO_ET) - base).total_seconds() // 60) - (9 * 60 + 30)
        ex_min = int(((datetime.strptime(ex["time"][:19], "%Y-%m-%d %H:%M:%S")
                       + PT_TO_ET) - base).total_seconds() // 60) - (9 * 60 + 30)
        closes = spx_by_day[day]
        settle = float(closes.iloc[-1])
        if side == "p":
            intr = max(0.0, K - settle) - max(0.0, Kl - settle)
            settle_ok = settle > K
        else:
            intr = max(0.0, settle - K) - max(0.0, settle - Kl)
            settle_ok = settle < K
        tid = len(trades)
        trades.append({
            "tid": tid, "day": day, "side": side, "K": K, "Kl": Kl,
            "credit": ent["credit"], "qty": ent["quantity"], "ex_min": ex_min,
            "actual_pnl": float(ex["profit"]),
            "held_pnl": ent["credit"] - intr * 100 * ent["quantity"],
            "settle_ok": int(settle_ok), "sigma": sigma,
            "era": "TRAIN" if day in train_days else "HELDOUT",
        })
        for m in sorted(mm for mm in mlook[(day, side)] if mm > ent_min):
            if m not in closes.index:
                continue
            S = float(closes.loc[m])
            ts = base.replace(hour=9, minute=30) + timedelta(minutes=int(m))
            try:
                T = time_to_settle(ts, is_half_day=day in half)
            except ValueError:
                continue
            feat = dict(mlook[(day, side)][m])
            feat["D"] = normalized_distance(K, S, sigma, T, side)
            feat["pmkt"] = p_mkt(K, S, sigma, T, side, m)
            rows.append(feat)
            meta.append((tid, int(m), S, T, feat["D"], feat["pmkt"]))

    X = pd.DataFrame(rows)[fl]
    md = pd.DataFrame(meta, columns=["tid", "minute", "S", "T", "D", "pmkt"])
    md["p_model"] = gbt.predict(booster, X, fl)
    tdf = pd.DataFrame(trades).set_index("tid")
    by_tid = {tid: g.sort_values("minute") for tid, g in md.groupby("tid")}

    def pnl_under(tr, rule):
        """P&L for trade tr under an exit rule fn(g)->bool mask (first crossing);
        held to settle if never fires. Returns (pnl, exit_row_or_None)."""
        g = by_tid.get(tr.name)
        if g is None:
            return tr["held_pnl"], None
        hit = g[rule(g)]
        if not len(hit):
            return tr["held_pnl"], None
        e = hit.iloc[0]
        cost = spread_cost(e["S"], tr["K"], tr["Kl"], tr["sigma"], e["T"], tr["side"])
        return tr["credit"] - cost * 100 * tr["qty"], e

    RULES = {
        "headline p<pmkt": lambda g: g["p_model"] < g["pmkt"],
        "p<pmkt-0.05": lambda g: g["p_model"] < g["pmkt"] - 0.05,
        "p<pmkt-0.10": lambda g: g["p_model"] < g["pmkt"] - 0.10,
        "p<0.60": lambda g: g["p_model"] < 0.60,
        "p<0.70": lambda g: g["p_model"] < 0.70,
        "p<0.80": lambda g: g["p_model"] < 0.80,
        # near-strike-scoped: only cut IN the validated edge regime (D<=0.5)
        "nearstrike D<=.5 & p<pmkt":
            lambda g: (g["D"] <= 0.5) & (g["p_model"] < g["pmkt"]),
    }
    pnl = {r: [] for r in RULES}
    rule_exit = {r: {} for r in RULES}
    for tid, tr in tdf.iterrows():
        for r, fn in RULES.items():
            p, e = pnl_under(tr, fn)
            pnl[r].append(p)
            rule_exit[r][tid] = e
    head_exit = rule_exit["headline p<pmkt"]
    tdf["pnl_headline"] = pnl["headline p<pmkt"]

    def chain_pnl(tr, e):
        """Real-chain P&L for trade tr exiting at row e (None=held to settle)."""
        if e is None:
            return tr["held_pnl"]                # settle intrinsic, exact
        cost = chain_close_cost(tr["day"], e["minute"], tr["K"], tr["Kl"], tr["side"])
        return np.nan if cost is None else tr["credit"] - cost * 100 * tr["qty"]

    def actual_bs(tr):
        g = by_tid.get(tr.name)
        if g is None:
            return tr["held_pnl"]
        e = g.iloc[(g["minute"] - tr["ex_min"]).abs().argsort().iloc[0]]
        return tr["credit"] - spread_cost(e["S"], tr["K"], tr["Kl"], tr["sigma"],
                                          e["T"], tr["side"]) * 100 * tr["qty"]
    tdf["pnl_actual_bs"] = [actual_bs(tr) for _, tr in tdf.iterrows()]

    # ---- REAL-CHAIN re-mark of the model exits (the trustworthy valuation) --
    # always-hold = held_pnl (intrinsic at settle, exact); actual = logged P&L
    # (already struck on recorded quotes); only the model exit needs re-marking,
    # at recorded chain mids at the policy's chosen exit minute.
    tdf["pnl_chain"] = [chain_pnl(tr, head_exit.get(tid))
                        for tid, tr in tdf.iterrows()]
    tdf["chain_ok"] = ~tdf["pnl_chain"].isna()

    print("=" * 86)
    print("MODEL-DRIVEN EXIT POLICY — exit at first bar p_model < p_mkt "
          "(BS-on-VIX1D marks)")
    print(f"(pre-TEST_START; frozen v1 model; {len(tdf)} spreads, "
          f"{len(md)} stride-bar queries)")
    print("=" * 86)
    print(f"\n{'era':>8} {'n':>4} {'POLICY':>12} {'always-hold':>13} "
          f"{'actual(BS)':>12} {'actual(logged)':>16}")
    for era in ("TRAIN", "HELDOUT", "ALL"):
        s = tdf if era == "ALL" else tdf[tdf["era"] == era]
        print(f"{era:>8} {len(s):4d} {_d(s['pnl_headline'].sum()):>12} "
              f"{_d(s['held_pnl'].sum()):>13} {_d(s['pnl_actual_bs'].sum()):>12} "
              f"{_d(s['actual_pnl'].sum()):>16}")

    ho = tdf[tdf["era"] == "HELDOUT"]
    print("\n--- policy vs baselines (HELD-OUT, honest, BS marks) ---")
    print(f"  policy − always-hold : {_d(ho['pnl_headline'].sum() - ho['held_pnl'].sum())}")
    print(f"  policy − actual(BS)  : {_d(ho['pnl_headline'].sum() - ho['pnl_actual_bs'].sum())}")
    print(f"  policy − actual(log) : {_d(ho['pnl_headline'].sum() - ho['actual_pnl'].sum())}")

    # ---- the trustworthy comparison: REAL-CHAIN marks, apples-to-apples ----
    print("\n--- REAL-CHAIN re-mark (HELD-OUT; all three on recorded quotes) ---")
    cov = tdf[tdf["era"] == "HELDOUT"]
    ok = cov[cov["chain_ok"]]
    print(f"  chain coverage: {len(ok)}/{len(cov)} held-out trades markable "
          f"({len(ok)/len(cov):.0%})")
    print(f"  {'POLICY(chain)':>16} {'always-hold':>13} {'actual(logged)':>16}")
    print(f"  {_d(ok['pnl_chain'].sum()):>16} {_d(ok['held_pnl'].sum()):>13} "
          f"{_d(ok['actual_pnl'].sum()):>16}  (n={len(ok)})")
    print(f"  policy(chain) − always-hold : {_d(ok['pnl_chain'].sum() - ok['held_pnl'].sum())}")
    print(f"  policy(chain) − actual(log) : {_d(ok['pnl_chain'].sum() - ok['actual_pnl'].sum())}")
    print(f"  [contrast] same trades BS:  policy(BS) "
          f"{_d(ok['pnl_headline'].sum())} vs chain {_d(ok['pnl_chain'].sum())} "
          f"(BS error {_d(ok['pnl_headline'].sum() - ok['pnl_chain'].sum())})")

    print("\n--- regime: distance at the policy's exit (HELD-OUT) ---")
    near = far = held = 0
    for tid in ho.index:
        e = head_exit.get(tid)
        if e is None:
            held += 1
        elif e["D"] <= 0.5:
            near += 1
        else:
            far += 1
    print(f"  exits near-strike D<=0.5 (validated): {near}   D>0.5: {far}   "
          f"held to settle: {held}")
    # reachability: does the near-strike edge regime even occur for these
    # (far-OTM ~0.10delta) spreads, regardless of when the policy exits?
    ho_tids = set(ho.index)
    ho_q = md[md["tid"].isin(ho_tids)]
    reach = ho_q[ho_q["D"] <= 0.5]["tid"].nunique()
    print(f"  near-strike reachability: {reach}/{len(ho_tids)} trades EVER reach "
          f"D<=0.5; {len(ho_q[ho_q['D']<=0.5])}/{len(ho_q)} stride-bars at D<=0.5")

    # ---- ISOLATED near-strike-edge test (the right test): manage ONLY in the
    # validated D<=0.5 zone, on the trades that actually reach it ----
    rname = "nearstrike D<=.5 & p<pmkt"
    reach = [tid for tid in ho.index
             if (by_tid.get(tid) is not None
                 and (by_tid[tid]["D"] <= 0.5).any())]
    print("\n--- ISOLATED near-strike policy (HELD-OUT, real chains) ---")
    print(f"  trades reaching D<=0.5: {len(reach)}")
    nb_pnl = ah_pnl = act_pnl = 0.0
    cut_real = cut_false = nexit = nmiss = 0
    for tid in reach:
        tr = tdf.loc[tid]
        e = rule_exit[rname].get(tid)
        cp = chain_pnl(tr, e)
        if np.isnan(cp):
            nmiss += 1
            continue
        nb_pnl += cp
        ah_pnl += tr["held_pnl"]
        act_pnl += tr["actual_pnl"]
        if e is not None:
            nexit += 1
            # settle_ok==0 => real breakout (cutting it = good); ==1 => false (mistake)
            if tr["settle_ok"] == 0:
                cut_real += 1
            else:
                cut_false += 1
    print(f"  (markable {len(reach) - nmiss}/{len(reach)})  near-strike policy "
          f"{_d(nb_pnl)}   always-hold {_d(ah_pnl)}   actual {_d(act_pnl)}")
    print(f"  near-strike − always-hold: {_d(nb_pnl - ah_pnl)}")
    print(f"  cuts: {nexit} total  -> {cut_real} real breakouts (good), "
          f"{cut_false} false breakouts (cut a winner)")

    print("\n--- sweep on REAL CHAINS (HELD-OUT; shape only, verdict=headline) ---")
    print(f"  baselines: always-hold {_d(ok['held_pnl'].sum())}   "
          f"actual(logged) {_d(ok['actual_pnl'].sum())}   (n={len(ok)} markable)")
    print(f"  {'rule':>16} {'policy(chain)':>14} {'vs always-hold':>15} {'#exit-early':>11}")
    for r in RULES:
        cp = np.array([chain_pnl(tr, rule_exit[r].get(tid))
                       for tid, tr in tdf.iterrows()])
        mask = (tdf["era"] == "HELDOUT").to_numpy() & ~np.isnan(cp)
        nexit = sum(1 for tid in ho.index if rule_exit[r].get(tid) is not None)
        print(f"  {r:>16} {_d(np.nansum(cp[mask])):>14} "
              f"{_d(np.nansum(cp[mask]) - tdf['held_pnl'].to_numpy()[mask].sum()):>15} "
              f"{nexit:>11}")

    tdf.to_csv(os.path.join(os.path.dirname(__file__), "model_exit_policy.csv"))


if __name__ == "__main__":
    main()
