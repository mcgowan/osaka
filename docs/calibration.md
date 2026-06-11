# Phase 1 Calibration Record

## Task 1.3 — distance↔delta mapping (2026-06-11)

**Method:** 62 of 303 recorded-chain days (`eleuthera/events/`), stratified
by prior-close VIX1D (range ≈ 9–33, terciles within sample <11.3 / 11.3–16.1
/ >16.1), × 4 PT snapshot times (06:35, 08:00, 10:00, 12:00) × both sides.
At each (day, snap, side): recorded chain deltas near the money, normalized
distance D computed with the frozen σ convention (prior-close VIX1D,
calendar-time √T), D interpolated at target deltas. 2,932 mapping points.
Code: `analysis/delta_mapping.py`; raw points: `analysis/delta_mapping.csv`.

**Result — median D [IQR] at each delta level:**

| Δ level | puts | calls |
|---|---|---|
| 0.05 | 3.36 [2.85..3.76] | 2.38 [2.00..2.83] |
| 0.10 | 2.31 [1.96..2.62] | 1.75 [1.46..2.12] |
| 0.15 | 1.73 [1.48..2.00] | 1.41 [1.17..1.68] |
| 0.20 | 1.35 [1.13..1.56] | 1.14 [0.96..1.39] |
| 0.25 | 1.05 [0.88..1.23] | 0.93 [0.78..1.12] |
| 0.30 | 0.79 [0.67..0.92] | 0.73 [0.61..0.89] |

**Why D ≈ 2.3 for a 0.10Δ put (not the textbook ~1.3):** D is bound to this
project's σ convention. Prior-close VIX1D + calendar-time √T understates the
market's effective remaining-session vol (annualization convention +
intraday seasonality), and put skew adds more. The mapping absorbs all of
it — by design. D values are internal coordinates, not comparable across
conventions.

**Drift (documented bucket-assignment noise; edges stay fixed):**
- By regime: equal-delta puts sit *closer* in D when VIX1D is elevated
  (0.05Δ put: calm 3.60 → elevated 2.95, ~−18%; calls much flatter, ~−8%).
- By time of day: morning D higher than midday (0.10Δ put 06:35: 2.52 vs
  10:00: 2.28, ~−10%) — the f(t) intraday-vol effect from the Phase S spike.
- IQRs ≈ ±15%. Consequence: a strike labeled "band of record" by frozen
  edges is sometimes truly 0.08Δ or 0.18Δ. Acceptable: buckets are
  *evaluation slices*, not model inputs; the model sees exact D.

**Frozen values (now in `quant/conventions.py`):**
- `GRID_ANCHORS` — per-side, at the median D of the 0.30/0.20/0.15/0.10/0.05Δ
  levels: puts (0.80, 1.35, 1.75, 2.30, 3.35), calls (0.75, 1.15, 1.40,
  1.75, 2.40). *(Amends FR-4.1's original single-sided draft anchors
  {0.8, 1.0, 1.3, 1.6, 2.1}, which empirically covered only ≈0.12–0.30Δ on
  the put side and would have failed the task 1.5 coverage test.)*
- `BUCKET_EDGES_D` — per-side edges at the 0.05/0.10/0.15/0.25Δ levels (see
  table). Band of record = the "10-15" bucket.
- Regime terciles used in analyses: VIX1D prior-close <11.0 / 11.0–14.7 /
  >14.7 (full-history daily quantiles). For analysis slicing ONLY — any
  model *feature* using vol percentiles must use trailing windows
  (point-in-time rule), never these full-history constants.

**Open items feeding later tasks:** the regime/time drift above is the
empirical case for evaluating an f(t)/regime correction to the p_mkt anchor
in task 1.4; bucket noise quantified here feeds the task 1.7 sensitivity
memo.
