"""v2 Phase 3 — calibration + operating threshold on CALIB.

The tuned model (models/breakout_gbt.DEFAULT_PARAMS) is trained on TRAIN, then:
  - an ISOTONIC calibrator (monotone, so it preserves ranking / the operating
    point) is fit on CALIB to make P(reversed) trustworthy as a value;
  - the OPERATING THRESHOLD is set on CALIB to greenlight the same fraction of
    breakouts as the 5-bar rule's CALIB selectivity (§7.1).
VALID is NEVER touched here - it is the one-shot Phase-4 gate. CALIB is the
sanctioned development/calibration fold; reading its operational ratio is a
second out-of-sample check (a different regime than the TRAIN-OOF read), not the
gate. Frozen artifacts (booster + calibrator + threshold) are persisted for the
gate run.

Run:  .venv/bin/python models/breakout_calibrate.py
"""

import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.breakout_features import load_master, split  # noqa: E402
from data.loader import OUT_DIR  # noqa: E402
from models.breakout_gbt import DEFAULT_PARAMS, predict, train  # noqa: E402

BUNDLE_DIR = os.path.join(OUT_DIR, "breakout-model")


def _isotonic(raw, y):
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(raw, y)
    return iso


def _greenlit_ratio(df, prob_col, thr=None):
    """(5-bar greenlit reversal, model greenlit reversal) on tradeable rows;
    model greenlights prob<=thr (or matched-fraction quantile if thr is None)."""
    t = df[df["tradeable"] == 1]
    y = t["reversed"].to_numpy(); p = t[prob_col].to_numpy()
    g5 = t["confirmed_5bar"].to_numpy(); gf = g5.mean()
    if thr is None:
        thr = np.quantile(p, gf)
    return y[g5 == 1].mean(), y[p <= thr].mean(), gf, thr


def fit(persist=True):
    master = load_master(); sp = split()
    calib_days = sorted(sp.calib_days)
    train_df = master[master["day"].isin(sp.train_days)]
    calib = master[master["day"].isin(sp.calib_days)].copy()

    booster, feats = train(train_df)                 # tuned DEFAULT_PARAMS
    calib["raw"] = predict(booster, calib, feats)
    iso = _isotonic(calib["raw"].to_numpy(), calib["reversed"].to_numpy())
    calib["cal"] = iso.predict(calib["raw"].to_numpy())

    # operating threshold on CALIB: match the 5-bar greenlight fraction
    rev5, _, gf, thr = _greenlit_ratio(calib, "cal")

    # --- report ---
    t = calib[calib["tradeable"] == 1]
    print(f"CALIB span {calib_days[0]}..{calib_days[-1]}  tradeable rows {len(t):,}")
    yb = t["reversed"].to_numpy()
    print(f"calibration (Brier): raw {np.mean((t['raw']-yb)**2):.4f}  "
          f"isotonic {np.mean((t['cal']-yb)**2):.4f}  base {np.mean((yb.mean()-yb)**2):.4f}")
    print("reliability (calibrated decile -> empirical reversal):")
    q = np.quantile(t["cal"], np.linspace(0, 1, 11))
    for i in range(10):
        m = (t["cal"] >= q[i]) & (t["cal"] <= q[i + 1] if i == 9 else t["cal"] < q[i + 1])
        if m.sum():
            print(f"  pred {t['cal'][m].mean():.2f}  actual {t['reversed'][m].mean():.2f}  (n={m.sum()})")

    _, revm, _, _ = _greenlit_ratio(calib, "cal", thr)
    print(f"\noperating point (threshold {thr:.3f}, greenlight {gf:.0%}):")
    print(f"  5-bar greenlit reversal {rev5:.1%}   model {revm:.1%}   ratio {revm/rev5:.2f}")
    t = t.copy(); t["hr"] = (t["start_mod"] + 570) // 60
    for lbl, hrs in (("morning(10-11)", [10, 11]), ("afternoon(12-14)", [12, 13, 14])):
        s = t[t["hr"].isin(hrs)]
        r5, rm, _, th = _greenlit_ratio(s, "cal")
        print(f"  {lbl:16s} n={len(s):4d}  5-bar {r5:.1%}  model {rm:.1%}  ratio {rm/r5:.2f}")

    if persist:
        os.makedirs(BUNDLE_DIR, exist_ok=True)
        booster.save_model(os.path.join(BUNDLE_DIR, "booster.txt"))
        pickle.dump(iso, open(os.path.join(BUNDLE_DIR, "isotonic.pkl"), "wb"))
        json.dump({"features": feats, "params": DEFAULT_PARAMS,
                   "operating_threshold": float(thr),
                   "calib_greenlight_fraction": float(gf),
                   "calib_span": [calib_days[0], calib_days[-1]]},
                  open(os.path.join(BUNDLE_DIR, "model.json"), "w"), indent=2)
        print(f"\nfrozen bundle -> {BUNDLE_DIR}")
    return booster, iso, thr


if __name__ == "__main__":
    fit()
