"""Recent-impulse/velocity candidate feature (plan task 4.6 item).

The spec DECLINED a recent-impulse feature for budget but logged it as a
Phase-4 candidate "specifically for the near-strike-stress (use-case-b)
discrimination question". Near-strike is the one population with a stable
edge, so the test is: does adding a recent-impulse feature improve the
near-strike OOF skill?

impulse(t) = |close(t-1) - close(t-1-K)| / M(t), K=10 min, M(t)=sigma*sqrt(T)*S
- normalized to implied-move units (rule #4: no raw points), point-in-time
(reads only bars < t plus open(t), same contract as the spec minute features).
This is an EXPERIMENT outside the locked spec/master; if it earns its place it
would be promoted via the feature pipeline + lookahead harness.

Evaluated on TRAIN walk-forward OOF (VALID untouched).

Run:  .venv/bin/python models/impulse_candidate.py
"""

import json
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.features import load_master  # noqa: E402
from data.loader import load_bars, load_calendar  # noqa: E402
from eval import metrics  # noqa: E402
from models import gbt  # noqa: E402
from models.hpsearch import PARAMS_PATH  # noqa: E402
from models.oof import walk_forward_oof  # noqa: E402
from models.splits import make_split  # noqa: E402
from quant.conventions import SigmaAnchor, time_to_settle  # noqa: E402

K = 10   # minutes


def impulse_map(days_needed):
    """(day, minute) -> recent-impulse value, point-in-time, for the stride
    minutes present in the master."""
    anchor = SigmaAnchor()
    cal = load_calendar()
    halfs = set(cal[cal["is_half_day"]]["date"].dt.strftime("%Y-%m-%d"))
    bars = load_bars("SPX")
    bars = bars[bars["ts"].dt.strftime("%Y-%m-%d").isin(days_needed)]
    out = {}
    for day, g in bars.groupby(bars["ts"].dt.strftime("%Y-%m-%d")):
        o = g["open"].to_numpy(float)
        c = g["close"].to_numpy(float)
        ts = g["ts"].tolist()
        sigma = anchor.sigma(day)
        half = day in halfs
        n = len(g)
        for m in range(0, n, 5):                      # stride minutes
            if m < K + 1:
                out[(day, m)] = np.nan
                continue
            T = time_to_settle(ts[m], is_half_day=half)
            M = sigma * math.sqrt(T) * o[m]
            out[(day, m)] = abs(c[m - 1] - c[m - 1 - K]) / M
    return out


def main():
    master = load_master()
    split = make_split(sorted(master["day"].unique()))
    days = sorted(master["day"].unique())
    print(f"{split}\nadding impulse(K={K}min) ... ", flush=True)

    imap = impulse_map(set(days))
    master = master.copy()
    master["impulse"] = [imap.get((d, int(m)), np.nan)
                         for d, m in zip(master["day"], master["minute"])]
    params = json.load(open(PARAMS_PATH))["with_pmkt"]
    p, nr = params["params"], params["num_rounds"]

    def fp(feats):
        def _fp(fit, val):
            b, _ = gbt.train(fit, params=p, num_rounds=nr, features=feats)
            return gbt.predict(b, val, feats)
        return _fp

    base_feats = gbt.feature_list(True)
    for name, feats in (("full", base_feats),
                        ("full+impulse", base_feats + ["impulse"])):
        oof = walk_forward_oof(master, split, fp(feats), label="pred")
        near = oof[metrics.near_strike(oof).values]
        band = oof[oof["bucket"] == metrics.BAND_OF_RECORD]
        ns = metrics.day_bootstrap_brier_skill(near, "pred", n_boot=1000)
        bs = metrics.day_bootstrap_brier_skill(band, "pred", n_boot=1000)
        print(f"  {name:13s} near-strike skill {ns['point']:+.5f} "
              f"(P>0={ns['p_gt_0']:.3f})   band skill {bs['point']:+.5f} "
              f"(P>0={bs['p_gt_0']:.3f})")


if __name__ == "__main__":
    main()
