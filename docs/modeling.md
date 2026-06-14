# Phase 4 — Modeling record (tasks 4.1–4.8)

Companion to `docs/plan.md`. All figures are reproducible from `models/` and
`analysis/` against the hash-verified master table. **Evaluation discipline
(review item 1, 2026-06-13):** all development evaluation is on TRAIN
walk-forward out-of-fold (OOF) predictions; CALIB is a held-out check; VALID is
the Gate-4 validation region (read deliberately, not iterated against); the
locked test (≥ 2025-12-01) is untouched for Phase 5.

## Setup (4.1)

Week-grained chronological split (`models/splits.py`): TRAIN 448d
(2023-04-27→2025-02-07) / CALIB 95d (2025-02-18→2025-07-03) / VALID 98d
(2025-07-14→2025-11-28), embargo ≥ 5 real trading days between regions
(holiday-proof; asserted on real day positions). Walk-forward CV = 5
expanding folds within TRAIN. Effective N = trading days; all CIs are
day-clustered bootstraps (resample days, not rows).

## Baselines (4.2) — the bar

Fit on TRAIN, reported on TRAIN OOF (band of record = 0.10–0.15Δ bucket):
base-rate Brier 0.18 (floor); **p_mkt 0.176 (the bar)**; logit5 ≈ p_mkt
(band skill +0.0016, P=0.74 — not significant). p_mkt is hard to beat.

## Model + tuning (4.3 / 4.4)

LightGBM, monotone +1 on D, side indicator, explicit `FEATURES` select,
first-class with/without-pmkt variants. Walk-forward search
(`models/hpsearch.py`, selection on OOF band Brier, TRAIN only) chose the
**most-regularized** config — `num_leaves=15, min_data_in_leaf=1000, lr=0.03,
400 rounds` — for both variants (frozen in `models/gbt_params.json`). 800
rounds / larger leaves overfit (CV band skill monotonically worse). Tuned CV
band skill **−0.0021 (with-pmkt) / −0.0016 (without)** — i.e. on the TRAIN
folds the GBT does NOT beat p_mkt at the entry band.

## Monotonicity (4.7) — VERIFIED

`models/monotonicity.py`: 500 sampled states × 60 D-points, both variants,
**zero violations**. CI test in `tests/test_gbt.py`.

## Ablations (4.6)

On TRAIN OOF, same frozen params (isolates the effect, not a re-tune):

| ablation | n_feat | band skill (P>0) | near-strike skill (P>0) | OR-retest skill (P>0) |
|---|---|---|---|---|
| full | 29 | −0.0021 (0.22) | **+0.0020 (0.98)** | +0.0011 (0.69) |
| no_pmkt | 28 | −0.0016 (0.29) | **+0.0018 (0.97)** | +0.0007 (0.61) |
| no_priorday | 23 | −0.0025 (0.18) | +0.0008 (0.76) | −0.0019 (0.21) |
| no_pd_levels | 27 | −0.0025 (0.18) | **+0.0021 (0.97)** | +0.0007 (0.61) |
| two_head | 28 | **−0.0305 (0.00)** | −0.0052 (0.00) | −0.0110 (0.02) |

- **Side indicator ≫ two heads.** Halving data per head is far too costly at
  effective-N ≈ days; two-head is catastrophically worse. The threat-frame
  sign question is subsumed (spec resolution #1) and likewise rejected — keep
  the single side indicator.
- **without-pmkt holds the edge** (near-strike +0.0018 vs +0.0020) — the edge
  is NOT a p_mkt echo (trader condition #25 satisfied).
- **Prior-day levels are largely dead weight** (`dist_pdl` gain 0.33%,
  `dist_pdh` 1.62%; dropping both does not hurt and slightly helps) —
  confirms the trader's intuition. But dropping the *whole* prior-day block
  hurts the slices (`mom3d_atr` is the top non-D/pmkt feature at 2.6%): prune
  the levels, keep the block.
- **Recent-impulse/velocity candidate** (`models/impulse_candidate.py`,
  K=10min, implied-move-normalized, point-in-time): near-strike skill
  +0.0020 → +0.0018 (no improvement). **Declined** — no demonstrated
  importance.
- **Gain importance:** D 57%, pmkt 16%, then a long tail. Near-zero (prunable
  toward the 20–30 budget): `persist_count` 0.00, `is_opex_quarterly` 0.02,
  `is_half_day`/`is_cpi_nfp` 0.03, `is_opex` 0.06, `gap_filled` 0.11, plus
  `dist_pdl` 0.33. Pruning these → ~22 features, no edge change.

**Pruning evaluated, DEFERRED to v2 — the v1 model is the full 29 features.**
The 8 candidates (`dist_pdh, dist_pdl, persist_count, is_opex_quarterly,
is_half_day, is_cpi_nfp, is_opex, gap_filled`; `gbt.PRUNE_V2_CANDIDATE`)
showed no edge change in an OOF spot-check (full-29 vs candidate-21: band
−0.0021→−0.0011, near-strike +0.0020→+0.0018), but the prune is NOT applied in
v1. Reasons: 29 is already within the 20–30 budget; the prune's only claimed
benefit is "no edge change"; and shipping a 21-feature model would break the
validated==shipped guarantee — **all the Gate-4 held-out evidence below
(CALIB/VALID, the regime decomposition, calibration) is the 29-feature
model**, and the VALID-is-spent rule forbids re-reading VALID to give the
21-set a clean held-out number. The 21-set was never run as a unit through the
ablation table or CALIB/VALID, and `dist_pdh` (1.62% gain) is not clearly dead
weight. So v1 ships the validated 29-feature model; the prune is logged as a v2
candidate to be re-validated with its own held-out budget.

## Residual analysis (4.8) — where the edge lives, signal vs noise

`analysis/residual_analysis.py`, TRAIN OOF, tuned with-pmkt model. Mean
residual p_model − p_mkt: pooled +0.007, band +0.005, **near-strike +0.027**
(the model systematically says "survives more often than the market implies"
near the strike — correcting the put-skew insurance premium measured in
Phase 2).

**Band-of-record skill by VIX1D regime:** calm **−0.0116 (P=0.003, worse)** /
mid +0.0010 (0.57) / elevated **+0.0091 (P=0.98)**.
**Near-strike skill by regime:** calm −0.0002 (null) / mid +0.0012 / elevated
**+0.0059 (P=1.00)**.
**Band skill by time-of-day:** open 0–30m **−0.0116 (P=0.000, worst)** /
30–120m −0.0064 / 120–300m −0.0015 / 300m–close +0.0043.
**Near-strike by time-of-day:** concentrated mid-session (120–300m +0.0036,
P=0.995); ~0 at open and into the close.

**Reading: this is signal, not noise.** The edge has a coherent, mechanistic
structure — it lives where the frozen analytic p_mkt is weakest (elevated vol,
where a static VIX1D barrier mis-prices) and vanishes where p_mkt is already
good (calm). It is consistent across the near-strike slice and the
elevated-band, and the residual sign matches the known put-skew premium. The
GBT is conditioning on path/realized-vol/regime to correct the baseline
exactly when that baseline is stressed.

But the **pooled / calm-weighted band-of-record metric fails**: calm days
dominate the sample (OOF n_days calm 82 vs elevated 52), and the model is
significantly *worse* than p_mkt in calm and at the session open — the pre-OR
entry use case (a) is its weakest spot.

## Held-out corroboration (CALIB / VALID)

Tuned with-pmkt, trained on TRAIN:

| population | OOF (dev) | CALIB 2025 H1 | VALID 2025 H2 |
|---|---|---|---|
| band of record | −0.0021 (0.22) | +0.0006 (0.57) | +0.0086 (0.999) |
| near-strike | +0.0020 (0.98) | +0.0036 (0.98) | +0.0086 (1.00) |

The **near-strike false-breakout edge is positive and significant in all
three windows** — the most robust result in the project, and the trader's
actual discrimination use case. The **band-of-record edge is not stable**:
absent/negative on OOF and CALIB, positive only on VALID (2025 H2, a
more-elevated-vol period — consistent with the regime story above).

## Calibration (4.5)

Isotonic fit on CALIB (`models/calibrate.py`) **worsens** every VALID bucket
(band cal_max 0.074 → 0.088): CALIB→VALID calibration drift. Raw band cal_max
≈ 7.4pt already exceeds the Section-8 ±3pt target. Calibration approach needs
rethinking (e.g. calibrate on a window adjacent to deployment, or regime-
conditional) before any go.

**VALID-is-spent rule for calibration rework (binding).** Any rework of the
calibration method (regime-conditional, deployment-adjacent window, Platt vs
isotonic, etc.) MUST be selected without reading VALID — fit on CALIB and
assess via CALIB-internal cross-validation or TRAIN OOF; VALID is read exactly
once, at the Gate-4/Phase-5 verdict, never iterated against. The code enforces
the seam (`build_calibrated`/`gate4_valid_days` require an explicit
`_gate4_reason`), but the discipline is the operator's: choosing a calibrator
by repeatedly checking VALID is selection on VALID and silently spends the
one-shot. This is the single place the reviewer flagged as most likely to slip.

## Gate-4 readiness vs Section-8 criteria

| Section-8 criterion | status |
|---|---|
| Band-of-record Brier beats p_mkt, **stable across sub-periods** | ❌ pooled band edge is calm-/period-dependent, negative in calm & at open |
| Per-bucket calibration within ±3pt in band | ❌ raw ≈7pt; isotonic drifts worse |
| Zero monotonicity violations | ✅ |
| Pooled mean within ~2pt of pooled implied | model runs +0.7pt pooled, +2.7pt near-strike (conditional, by design) |

**Honest summary for the go/no-go.** v1 is *not* a flat failure and *not* a
clean pass. The model carries a real, interpretable, regime-conditional edge —
strongest and stably significant in the **near-strike false-breakout regime
under elevated vol**, which is the trader's core discrimination use case — but
**no robust edge at the moderately-OTM entry band**, where delta (p_mkt) is
largely sufficient, and it is *worse* than p_mkt in calm regimes and at the
session open. The pre-registered primary criterion (stable band-of-record edge)
is not met as written; a conditional/slice-scoped edge is real.

Open decision for the trader (does not move pre-registered goalposts):
whether the conditional near-strike/elevated-vol edge warrants the one-shot
Phase-5 locked-test run (the locked period is Dec 2025+, the most recent /
most-elevated window — a fair test of the regime hypothesis), or whether the
unstable headline band edge triggers the documented-negative-result path.
