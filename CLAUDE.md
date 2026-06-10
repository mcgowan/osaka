# CLAUDE.md — SPX 0DTE Strike Survival Probability Model

## What this project is

A conditional probability model: given (timestamp, strike, side) and current market state, output the calibrated probability that an SPX 0DTE short strike survives to 4:00 PM ET settlement without a delta-stop breach. Used to select and monitor directional credit spreads.

The model's product is the **edge residual**: p_model − p_mkt, where p_mkt is the market-implied survival probability derived from the strike's delta. A model that merely reproduces delta is a failure. The pre-registered kill criterion in the requirements doc is real — a documented negative result is an acceptable project outcome; tuning until something "works" is not.

## Authoritative documents (read before any task)

- `docs/requirements.md` — requirements, definitions, FR/NFR numbering used in tasks
- `docs/plan.md` — gated phases with task IDs (e.g., task 1.7)

When a task references an FR, NFR, or task ID, the document text is the spec. If implementation needs to deviate, stop and flag it — do not silently reinterpret.

## Inviolable rules

These are never relaxed, by any agent, for any reason:

1. **Point-in-time discipline.** Every feature at bar t uses only data available at t. Daily-regime features use completed days only (as of yesterday's close). All feature code must pass the truncation harness (`tests/test_lookahead.py`) — recompute on data truncated at t, assert exact equality.
2. **Split discipline.** Splits are chronological, by week, with an embargo gap. Row-level or random splits are prohibited everywhere, including quick experiments. Effective N ≈ trading days, not rows; report metrics accordingly.
3. **The locked test set is untouchable.** The final test period is read by exactly one script (`eval/final_eval.py`), run once, in Phase 5. No other code path, notebook, or "sanity check" reads it. A deny hook enforces this; do not work around the hook.
4. **No raw points, no raw levels.** All features are normalized (ATR units, implied-move units, ratios, percentile ranks). Raw SPX price or raw point distances never enter the feature table.
5. **Internal BS consistency.** Strike placement, forward delta walks, δ*, and p_mkt all use the same skew-adjusted Black-Scholes framework with the same σ-update rule. Never mix in an external pricer for one component.
6. **Human gates.** Phase gates are signed off by the trader, not by an agent. Do not mark a gate task complete; mark it ready-for-review.

## Core conventions and definitions

- **Bars:** 1-minute, RTH (9:30–16:00 ET), 390/day. Breach detection, forward delta walks, and live monitoring always run at 1-min.
- **Sample stride:** training rows generated every 5 minutes (configurable). Labels are still computed with 1-min forward walks.
- **Strike encoding:** strikes enter the model as current BS delta (primary) + normalized distance (S−K)/(σ√T·S) (secondary). Never raw strike price.
- **Strike generation:** invert BS for the target-delta strike, **snap to nearest listed 5-pt increment**, record the snapped strike's actual delta as the feature. Grid anchors {0.05, 0.10, 0.15, 0.20, 0.30} are sampling targets, not data values.
- **Label:** failure = strike's delta ≥ δ* at any 1-min bar before settlement; survival otherwise. δ* comes from task 1.7 calibration (config value, currently TBD, expected 0.20–0.35). Also emit: breach time, max delta reached, settlement-beyond-strike secondary label.
- **Vol input:** VIX1D. Never substitute VIX. History before VIX1D availability is excluded, not approximated.
- **No purchased options data.** All option pricing (strikes, deltas, p_mkt) is derived from SPX 1-min bars + VIX1D via the skew-adjusted BS framework. Real chains are limited to the trader's own recorded snapshots, used only to calibrate and validate the derived pricing — never as a pipeline input, and never bought from a vendor.
- **Skew multipliers:** m_put, m_call (possibly VIX1D-tercile bucketed) are frozen config values produced by Phase 1 calibration. Do not recompute ad hoc.
- **Band of record:** all go/no-go evaluation centers on the 0.10–0.15Δ bucket. Pooled metrics alone are never sufficient.
- **Primary metrics:** Brier score and calibration curves, model vs. p_mkt baseline, per delta bucket. AUC secondary. Accuracy is not a metric.

## Architecture rules

- All data access goes through the versioned load API (`data/loader.py`). No module reads raw vendor files directly. Datasets are parquet + hashed manifest.
- One function per feature, registered in the feature config with: name, formula, lookback, normalization, point-in-time rule. The spec sheet (`docs/feature-spec.md`) must match the code; drift is a bug.
- Model: LightGBM/XGBoost with a **monotone constraint on strike delta** (survival probability non-increasing in delta). Post-hoc calibration (isotonic/Platt) fit on a fold never used for model selection.
- Deterministic everything: fixed seeds, config-driven runs, no hidden notebook state in deliverables.

## Workflow

- Work proceeds phase by phase per the project plan; do not start tasks from a later phase before the prior gate is marked passed by the trader.
- Every feature or label PR requires review by the leakage red-team agent before merge.
- Math-bearing code (BS solver, delta engine, strike inversion) requires verification by the math-verifier agent against independent computation.
- Run `tests/` (including the lookahead harness) before declaring any task complete.
- When uncertain about trading-domain intent (stop conventions, strike-selection rules, what counts as a plausible label), ask the trader — do not guess and proceed.

## Agent roles

Defined in `.claude/agents/`:
- `leakage-redteam` — adversarial, read-only reviewer for lookahead and split violations
- `math-verifier` — independent verification of pricing/delta math against chains and textbook values
- `feature-implementer` — template for per-block feature work against the locked spec sheet

## Things agents get wrong on this project — don't

- Computing "today's range position" or any tape feature using the full day's data (lookahead).
- Evaluating with row-level cross-validation because "it's just a quick check."
- Reporting pooled metrics as success while the 0.10–0.15Δ band fails.
- Treating delta grid anchors as exact values instead of snap-to-strike targets.
- Backfilling pre-2022 history with VIX-derived approximations.
- "Improving" the BS framework's absolute accuracy in one component, breaking internal consistency.
- Touching, summarizing, or plotting the locked test period for any reason before Phase 5.
