"""Monotonicity verification (plan task 4.7; FR-6.2/6.4).

The inviolable constraint: survival probability is non-decreasing in the
normalized distance D (further OTM => never lower survival). LightGBM enforces
it natively via the +1 monotone constraint on D (models.gbt.monotone_constraints);
this script VERIFIES it numerically by strike sweeps, as FR-6.4 requires.

For each sampled (day, bar) state we hold every other feature fixed and sweep
D upward across a grid, predict, and assert the prediction never decreases
(within float tolerance). This directly tests the constrained input. Both
model variants are checked; trained on TRAIN only (no VALID read). A realistic
"move the strike farther out" sweep would also raise pmkt, but FR-6.2 binds the
D feature specifically, so the clean test holds all else fixed.

Run:  .venv/bin/python models/monotonicity.py
"""

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.features import load_master  # noqa: E402
from models import gbt  # noqa: E402
from models.hpsearch import PARAMS_PATH  # noqa: E402
from models.splits import make_split  # noqa: E402

TOL = 1e-9
D_GRID = np.round(np.arange(0.1, 6.01, 0.1), 2)   # sweep range covers the grid
N_STATES = 500
SEED = 0


def verify(booster, feats, base_states):
    """Sweep D over D_GRID for each base state; return (n_states, n_violations,
    worst_drop). A violation is any step where prediction decreases > TOL."""
    d_idx = feats.index("D")
    X = base_states[feats].to_numpy(float, copy=True)
    n_viol = 0
    worst = 0.0
    for row in X:
        grid = np.tile(row, (len(D_GRID), 1))
        grid[:, d_idx] = D_GRID
        p = booster.predict(grid)
        diffs = np.diff(p)
        drops = diffs[diffs < -TOL]
        if drops.size:
            n_viol += 1
            worst = min(worst, float(drops.min()))
    return len(X), n_viol, worst


def main():
    master = load_master()
    split = make_split(sorted(master["day"].unique()))
    train = master[master["day"].isin(split.train_days)]
    states = train.sample(n=min(N_STATES, len(train)), random_state=SEED)
    cfg = json.load(open(PARAMS_PATH))
    print(f"{split}\nstrike sweeps: {len(D_GRID)} D points x {len(states)} "
          f"sampled TRAIN states\n")
    ok = True
    for variant, include_pmkt in (("with_pmkt", True), ("without_pmkt", False)):
        c = cfg[variant]
        booster, feats = gbt.train(train, params=c["params"],
                                   num_rounds=c["num_rounds"],
                                   include_pmkt=include_pmkt)
        n, nv, worst = verify(booster, feats, states)
        status = "PASS" if nv == 0 else "FAIL"
        ok = ok and nv == 0
        print(f"  [{variant:13s}] {n} states, {nv} violations  -> {status}"
              + ("" if nv == 0 else f" (worst drop {worst:.2e})"))
    print("\nMONOTONICITY:", "VERIFIED (zero violations)" if ok else "VIOLATED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
