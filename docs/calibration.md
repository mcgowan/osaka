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

## Task 1.4 — p_mkt baseline validation & f(t) freeze (2026-06-11)

**Method:** same 62-day stratified sample, p_mkt evaluated at the actual
FR-4 grid strikes (2,451 points). Reference truth = the same first-passage
formula with each strike's *recorded IV* (IB's IV shares our calendar-time
convention, verified in Phase S). Code: `analysis/baseline_validation.py`;
raw points: `analysis/baseline_validation.csv`.

**Finding — the raw anchor is unusable as a benchmark:** bias
(p_anchor − p_chainIV) is one-sided and large: **+0.21 in the put band of
record** (+0.14 calls), nearly constant across times of day. Cause: the
prior-close VIX1D anchor understates effective remaining-session vol
(annualization convention) and ignores skew. A baseline this optimistic
would let any model "beat the market" for free — the kill criterion would
be meaningless.

**Fix — frozen f(t) correction:** per-side, four time-of-day knots, fitted
as the median recorded-IV/anchor ratio (full sample):

| minutes since open | 5 | 90 | 210 | 330 |
|---|---|---|---|---|
| puts | 1.868 | 1.753 | 1.709 | 1.694 |
| calls | 1.418 | 1.317 | 1.329 | 1.402 |

Piecewise-linear between knots, flat beyond. **Held-out validation** (fit on
odd days, eval on even): band-of-record mean bias **−0.005** both sides
(MAE 0.058 puts / 0.098 calls — symmetric per-day vol noise, acceptable for
a baseline). Regime-stable: ratio drifts only ~5% calm→elevated vs the
~70–87% level it corrects. Frozen in `quant/conventions.py::F_T_KNOTS`;
the baseline callable is `quant.pmkt.p_mkt(...)`.

## Task 1.5 — grid coverage acceptance (2026-06-11)

**Method:** for every (day, snapshot, side) observation in the task 1.3
sample, place the FR-4 grid at that bar's (S, σ_anchor, T) and check it
brackets the chain's actual 0.05–0.30Δ strike range in D units. Code:
`analysis/grid_coverage.py`.

**Iteration:** median-only anchors covered ~50% of the 0.05Δ tail by
construction; first bracketing attempt (p2.5/p97.5 of the edge levels)
reached 95.0% calls / 91.6% puts — snap jitter at the inner edge and the
fat far tail (observed D(0.05Δ) max 6.1) eat the margin. Final anchors add
wider brackets: puts {0.35, …, 5.60}, calls {0.32, …, 5.00}.

**Result: PASS — puts 98.7%, calls 97.9%** (remaining misses are extreme
far-wing days; the 0.05Δ wing is explicitly non-blocking per the Phase S
memo). Cost: 7 anchors/side instead of 5 → training table grows ~40%.

## Task 1.6 — exit-rule characterization (2026-06-11)

**Method:** all 294 logged spreads (eleuthera replay) with the frozen Phase-1
machinery evaluated at entry and at exit: normalized distance D, the p_mkt
baseline, then label-side facts (touch after exit, settle-beyond,
held-to-settle P&L). Code: `analysis/exit_characterization.py` (+ CSV).

**Entry profile:** median D at entry 2.13, median p_mkt at entry 0.814 —
the system enters at ≈0.10–0.12Δ-equivalent strikes, squarely in the band
of record. The evaluation focus is confirmed correct.

**Exit profiles:**
- `risk_off_reversal` (n=130) fires on *vol state*, not price proximity:
  median D at exit ≈ 2.0–2.2 (barely closer than entry), median p_mkt at
  exit 0.80–0.86, only 9–36% ever touch afterward. Cost by regime: calm
  +$33k, mid −$50k (the rule EARNED its keep here — contains the crash-day
  saves), elevated +$81k (worst whipsaw tax: elevated-vol days price wide
  moves that revert).
- `sr_inner_breach` (n=36) is genuinely price-driven: median D at exit
  1.1–1.6, p_mkt 0.53–0.75, touch-after-exit up to 75% in elevated regime.

**The discrimination table (the model's job, quantified):**

| p_mkt at exit | n | actually settled safe | cost of exiting |
|---|---|---|---|
| <0.50 | 5 | 60% | +$11k |
| 0.50–0.70 | 32 | 75% | −$41k |
| 0.70–0.85 | 73 | **96%** | **+$138k** |
| ≥0.85 | 56 | 93% | −$30k |

Two lessons: (a) the bulk of the exit cost sits where the market already
said "fine" (0.70–0.85) — a p_mkt-threshold override alone would have
captured most of the prize on this sample; (b) but the ≥0.85 bucket shows
why that's not enough — its 7% failures were catastrophic (crash days where
the market was still complacent at exit time). The model must beat p_mkt
*conditionally* — that is the edge-residual thesis, now with a dollar sign.

## Task 1.7 — sensitivity memo (2026-06-11)

Code: `analysis/sensitivity.py`. Labels are σ-free by construction and not
affected by anything below.

| Perturbation | Effect | Verdict |
|---|---|---|
| σ anchor prior_close → day_open | median |ΔD|/D **29%**; **58%** of bucket labels reassigned; median |Δp_mkt| 0.086 | **High sensitivity — but a convention, not a fragility.** All Phase-1 calibrations were fitted under prior_close and absorb its level (f(t)'s ~1.7–1.87 includes the overnight VIX1D sawtooth). Mode is frozen with a warning in `conventions.py`: changing it requires re-running 1.3–1.5. |
| f(t) knots ±5% (fit noise) | median |Δp_mkt| ≈ 0.024 in band of record | Small vs the documented baseline MAE (0.06–0.10). Not the dominant error. |
| Bucket fidelity vs recorded delta | band of record: 38% exact, 92% within-one-bucket; recorded-delta IQR [0.094..0.164] | The "10-15" bucket truly captures the 0.10–0.15Δ region at its quartiles. Acceptable: buckets are evaluation slices; the model sees exact D. |

**Conclusion:** nothing fragile in the frozen stack *given* the conventions;
the one genuinely consequential choice (anchor mode) is locked and
documented. Residual baseline noise carries into Phase 4/5 interpretation
as already recorded under task 1.4.

**Scope decision (NFR-3.4 amendment):** f(t) is part of the **p_mkt
definition only**. The distance encoding, grid, and buckets stay on the raw
anchor: their delta-faithfulness was achieved *empirically* in task 1.3
against the same chains, so the two components are market-consistent
without sharing the multiplier — and keeping f(t) out of the feature
coordinate means the model's input space carries no fitted time-of-day
table (time-of-day effects are the model's job to learn, via its clock
features). Documented residual baseline noise (MAE above) is carried into
the task 1.7 sensitivity memo and the Phase 4/5 evaluation interpretation.
