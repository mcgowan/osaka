# SPX 0DTE Strike Survival Probability Model

A conditional probability model for selling 0DTE directional credit spreads on SPX.

Given a `(timestamp, strike, side)` query and the current intraday market state, the model
outputs the calibrated probability that an SPX short strike **survives** to 4:00 PM ET
settlement without triggering a delta-stop breach.

## The point of this project

Option delta is already the market's own survival estimate. A model that merely reproduces
delta is a **failure**. The product of this project is the **edge residual**:

```
edge = p_model − p_mkt
```

where `p_mkt` is the market-implied survival probability derived analytically from the
strike's delta. The model is only useful insofar as `p_model` deviates from `p_mkt` in a
calibrated, persistent way — by conditioning on intraday path, realized-vs-implied
volatility, day structure, and calendar state that delta cannot see.

A **pre-registered kill criterion** governs the outcome: if the edge residual is
statistically indistinguishable from zero in the band of record after honest evaluation,
the conclusion is that delta was sufficient and the project stops at a documented negative
result. Tuning until something "works" is not an acceptable outcome.

## How it works

- **Strike encoding** — strikes enter the model as their current Black-Scholes delta
  (primary) plus normalized distance `(S−K)/(σ√T·S)` (secondary). Never as raw price.
- **Historical strike reconstruction** — there is no full options-chain history, and none is
  purchased. All option pricing (strikes, deltas, `p_mkt`) is derived from SPX 1-min bars +
  VIX1D via the skew-adjusted Black-Scholes framework, with per-side skew multipliers
  (`m_put`, `m_call`). The only real chains used are the trader's own recorded snapshots —
  for calibration and validation of the derived pricing, never as a pipeline input.
- **Labels** — for each day × sample-stride bar × side × grid-delta strike, walk forward at
  1-minute resolution to settlement. Failure = the strike's delta meets or exceeds the
  calibrated stop threshold `δ*` at any bar; survival otherwise.
- **Model** — gradient-boosted trees (LightGBM/XGBoost) with a **monotone constraint** on
  strike delta (survival probability non-increasing in delta), plus a post-hoc calibration
  pass (isotonic/Platt).
- **Evaluation** — Brier score and calibration curves, model vs. `p_mkt` baseline, reported
  **per delta bucket**. The **0.10–0.15Δ band is the band of record**; pooled metrics alone
  are never sufficient (they are flattered by easy far-OTM samples). Accuracy is not a metric.

## Inviolable rules

These are never relaxed, by any contributor or agent, for any reason:

1. **Point-in-time discipline.** Every feature at bar *t* uses only data available at *t*.
   Daily-regime features use completed days only. All feature code must pass the truncation
   harness (`tests/test_lookahead.py`).
2. **Split discipline.** Splits are chronological, by week, with an embargo gap. Row-level or
   random splits are prohibited everywhere — adjacent 1-min samples are near-duplicates, so
   effective N ≈ trading days, not rows.
3. **The locked test set is untouchable.** The final test period is read by exactly one
   script (`eval/final_eval.py`), run once, in Phase 5. A deny hook enforces this.
4. **No raw points, no raw levels.** All features are normalized (ATR units, implied-move
   units, ratios, percentile ranks).
5. **Internal BS consistency.** Strike placement, forward delta walks, `δ*`, and `p_mkt` all
   use the same skew-adjusted Black-Scholes framework with the same σ-update rule.
6. **Human gates.** Phase gates are signed off by the trader, not by an agent.

## Conventions

| Item | Convention |
|---|---|
| **Bars** | 1-minute, RTH (9:30–16:00 ET), 390/day. Breach detection, forward walks, and live monitoring always run at 1-min. |
| **Sample stride** | Training rows generated every 5 minutes (configurable). Labels still use 1-min forward walks. |
| **Vol input** | VIX1D. Never VIX. History before VIX1D availability is excluded, not approximated. |
| **No purchased options data** | All pricing is derived from SPX bars + VIX1D. Real chains are limited to the trader's own snapshots, used for calibration/validation only. |
| **Grid anchors** | {0.05, 0.10, 0.15, 0.20, 0.30}Δ are *sampling targets*. Invert BS, snap to nearest listed 5-pt strike, record the snapped strike's actual delta as the feature. |
| **δ\*** | Delta-stop threshold from Phase 1 calibration (config value, expected 0.20–0.35). |
| **Primary metrics** | Brier score, calibration curves vs. `p_mkt`, per delta bucket. AUC secondary. |

## Project structure

```
docs/
  requirements.md   # Requirements: purpose, FRs/NFRs, success criteria, kill criterion
  plan.md           # 7 gated phases with task IDs (e.g. task 1.7)
.claude/agents/     # Specialized review agents (see below)
CLAUDE.md           # Working instructions for AI agents on this repo
```

The pipeline, feature library, model, and evaluation code are built out phase by phase per
the plan. All data access goes through a versioned load API (`data/loader.py`); no module
reads raw vendor files directly. Datasets are parquet + a hashed manifest.

## Phases

Work proceeds strictly phase by phase; a phase does not start until the prior gate is signed
off by the trader. Roughly half the total effort lives in Phases 0–2 — most quant projects
die from bad labels and leakage, not bad models.

0. **Data Foundation** — acquire, clean, version, and expose all raw inputs via the load API.
1. **Strike Reconstruction & Calibration** — skew-adjusted BS machinery; lock `m` values; calibrate `δ*`.
2. **Label Generation** — the full label table, validated against theory, the trade log, and visual audit.
3. **Feature Engineering** — the 20–30 point-in-time-safe feature columns + the lookahead harness.
4. **Modeling** — trained, calibrated, monotone GBT that beats the baselines on validation.
5. **Final Evaluation** — one shot against the locked test set and the pre-registered criteria.
6. **Shadow Deployment** — live-condition validation without risk (only on a "go").

See `docs/plan.md` for per-task detail and gate criteria.

## Review agents

Defined in `.claude/agents/`:

- **`leakage-redteam`** — adversarial, read-only reviewer for lookahead and split violations.
  Every feature or label PR requires its review before merge.
- **`math-verifier`** — independent verification of pricing/delta math (BS solver, strike
  inversion, forward delta, `δ*`, `p_mkt`) against real chains and textbook values.
- **`feature-implementer`** — template for per-block feature work against the locked spec sheet.

## Working on this repo

- Read `docs/requirements.md` and `docs/plan.md` before any task. When a task references an
  FR, NFR, or task ID, the document text is the spec.
- Run `tests/` (including the lookahead harness) before declaring any task complete.
- Math-bearing code requires math-verifier sign-off; feature/label PRs require
  leakage-redteam sign-off.
- When uncertain about trading-domain intent (stop conventions, strike-selection rules, what
  counts as a plausible label), ask the trader — do not guess and proceed.
