# SPX 0DTE Strike Survival Probability Model

A conditional probability model for selling 0DTE directional credit spreads on SPX.

Given a `(timestamp, strike, side)` query and the current intraday market state, the model
outputs the calibrated probability that an SPX short strike **survives** to 4:00 PM ET
settlement — survival means **price never touches the strike** (settle-beyond kept as a
secondary label). Decided at Gate S (2026-06-11); labels are pure price facts and the
pipeline contains no option pricing.

## The point of this project

Option delta is already the market's own survival estimate. A model that merely reproduces
delta is a **failure**. The product of this project is the **edge residual**:

```
edge = p_model − p_mkt
```

where `p_mkt` is the market-implied no-touch probability (analytic barrier formula,
chain-validated; `quant.pmkt.p_mkt()`). The model is only useful insofar as `p_model`
deviates from `p_mkt` in a calibrated, persistent way — by conditioning on intraday path,
realized-vs-implied volatility, day structure, and calendar state that the market's own
estimate cannot see.

A **pre-registered kill criterion** governs the outcome: if the edge residual is
statistically indistinguishable from zero in the band of record after honest evaluation,
the conclusion is that the market's estimate was sufficient and the project stops at a
documented negative result. Tuning until something "works" is not an acceptable outcome.

## Relationship to the trading system (eleuthera)

The model exists to give the trader's rules-based automated strategy
(`../eleuthera`) an edge — specifically against **false breakouts / whipsaw
stop-outs** (measured cost: ~$78k over 301 backtested days, ≈57% of strategy
P&L) and by enabling **early-session entries** before the strategy's 30-min
opening range exists. It must therefore remain an *independent second
opinion*, which is enforced as a one-way rule:

> **The strategy may learn from the model; the model never learns from the
> strategy.**

- The model is a **stateless oracle**: `(timestamp, strike, side) →
  {p_model, p_mkt, residual}`. It knows nothing about opening ranges, RSI
  heat/pressure signals, positions, or rules — by design (correlated second
  opinions are worthless; this is why OR features are deliberately absent).
- **Labels** are pure price facts; **features** are market state only. The
  trader's chain recordings (`eleuthera/events/`, market prices — not
  behavior) calibrate the baseline. The trade log is used *outside* the
  learning loop only: exit-rule characterization, label spot-validation,
  and read-only overlay economics after the model is frozen.
- The only sanctioned influence of the trader's profile is **where the model
  is graded** (band of record, early-session / near-strike / post-breakout
  slices) — choosing the exam, never feeding answers. The kill criterion is
  judged against the market baseline, not against strategy P&L.
- **Planned integration points** (next-gen strategy, in build order):
  (1) early-session entry gate — from 9:31 ET, enter when `p_model` is high
  and the residual positive; OR-breakout entries remain as a parallel
  trigger; (2) exit override — when `risk_off_reversal` / `sr_inner_breach`
  fires, suppress the exit only on a strong conditional model signal (a
  naive p_mkt threshold captures most of the historical prize but eats the
  catastrophic tail — see `docs/calibration.md`, task 1.6); (3) later:
  re-entry after stops, sizing by confidence.
- Because the model never saw the rules, the strategy can evolve freely
  without invalidating it. Candidate rules are evaluated by replaying the
  recorded archive with model queries injected — using validation-period
  outputs only, never the locked test period. Expect *correlation* between
  model output and trading outcomes (both watch the same market); what is
  excluded is the trader's decisions ever becoming training signal.

## How it works

- **Strike encoding** — strikes enter the model as their normalized distance
  `(S−K)/(σ√T·S)` plus side and time-to-settle. Never as raw price; never as delta.
- **Strike grid (historical training rows)** — strikes placed at fixed normalized-distance
  anchors (≈0.05–0.30Δ-equivalent region), snapped to listed 5-pt increments. No
  options-chain history is needed or purchased. The trader's own recorded chains
  (`../eleuthera/events/`, 303+ days) validate the `p_mkt` baseline and provide ablation
  features — never a pipeline dependency.
- **Labels** — for each day × sample-stride bar × side × grid anchor, scan forward at
  1-minute resolution to settlement against bar highs/lows. Failure = price touches the
  strike; survival otherwise. Settle-beyond and closest-approach are also emitted. Labels
  are pure price facts: no option pricing, σ, or greeks anywhere in the label pipeline.
- **Model** — gradient-boosted trees (LightGBM/XGBoost) with a **monotone constraint** on
  normalized distance (survival non-decreasing in distance), plus a post-hoc calibration
  pass (isotonic/Platt).
- **Evaluation** — Brier score and calibration curves, model vs. `p_mkt` baseline (analytic
  no-touch probability, chain-validated), reported **per distance bucket** labeled by
  delta-equivalents. The **≈0.10–0.15Δ-equivalent band is the band of record**, plus named
  slices (early session, near-strike stress, post-breakout retest); pooled metrics alone
  are never sufficient. Accuracy is not a metric.

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
5. **Internal analytic consistency.** Grid placement, distance encoding, bucket boundaries,
   and `p_mkt` all use the same σ source and √T convention. Labels use no σ at all.
6. **Human gates.** Phase gates are signed off by the trader, not by an agent.

## Conventions

| Item | Convention |
|---|---|
| **Bars** | 1-minute, RTH (9:30–16:00 ET), 390/day. Touch detection, label scans, and live monitoring always run at 1-min. |
| **Sample stride** | Training rows generated every 5 minutes (configurable). Labels still use 1-min forward scans. |
| **Vol input** | VIX1D (anchor for normalization, features, `p_mkt`). Never VIX. History before VIX1D availability is excluded, not approximated. Labels use no vol input. |
| **No purchased options data** | The pipeline needs none. Real chains = the trader's own recordings (`../eleuthera/events/`), for baseline validation and ablations only. |
| **Grid anchors** | Fixed normalized-distance anchors (≈0.05–0.30Δ-equivalent) are *sampling targets*: snap to nearest listed 5-pt strike, record the snapped strike's actual distance as the feature. |
| **v2 (shelved)** | The original delta-breach/δ\* label design — documented in `docs/requirements.md` §6.3, buildable from recorded chain deltas if post-v1 analysis justifies it. |
| **Primary metrics** | Brier score, calibration curves vs. `p_mkt`, per distance bucket + named slices. AUC secondary. |

## Project structure

```
docs/
  requirements.md   # Requirements: purpose, FRs/NFRs, success criteria, kill criterion
  plan.md           # Gated phases with task IDs and live status table
  calibration.md    # Phase 1 record: frozen values, evidence, sensitivity memo
quant/              # Frozen conventions + distance/grid machinery + p_mkt baseline
data/
  loader.py         # THE load API (parquet + hashed manifest); loads bars + calendar
  chains.py         # Validation-only access to the trader's recorded chain archive
  ib_download.py    # IB TWS API downloader for SPX/VIX/VIX1D history ($0 source)
  qa_ib.py          # Standing raw-data audit (Gate 0)
analysis/           # Reproducible calibration/validation studies (script + CSV each)
spike/              # Phase S derived-pricing spike + memo (decided Gate S)
tests/              # pytest suite incl. Monte Carlo verification of p_mkt
.claude/agents/     # Specialized review agents (see below)
CLAUDE.md           # Working instructions for AI agents on this repo
```

The pipeline, feature library, model, and evaluation code are built out phase by phase per
the plan. All data access goes through a versioned load API (`data/loader.py`); no module
reads raw vendor files directly. Datasets are parquet + a hashed manifest.

## Phases

Work proceeds strictly phase by phase; a phase does not start until the prior gate is signed
off by the trader. Roughly half the total effort lives in Phases 0–2 — most quant projects
die from bad labels and leakage, not bad models. Live status lives in the table at the top
of `docs/plan.md` (as of 2026-06-11: Phase S passed, Phase 0 complete, Phase 1 complete and
awaiting Gate 1 trader review).

0. **Data Foundation** — acquire, clean, version, and expose all raw inputs via the load API.
1. **Grid, Baseline & Validation Framework** — distance/grid machinery; analytic `p_mkt` validated against recorded chains; buckets frozen; exit-rule characterization.
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
- **`math-verifier`** — independent verification of the quantitative machinery (distance/grid
  math, the `p_mkt` barrier formula, bucket mapping) against real chains, Monte Carlo, and
  textbook values.
- **`feature-implementer`** — template for per-block feature work against the locked spec sheet.

## Working on this repo

- Read `docs/requirements.md` and `docs/plan.md` before any task. When a task references an
  FR, NFR, or task ID, the document text is the spec.
- Run `tests/` (including the lookahead harness) before declaring any task complete.
- Math-bearing code requires math-verifier sign-off; feature/label PRs require
  leakage-redteam sign-off.
- When uncertain about trading-domain intent (stop conventions, strike-selection rules, what
  counts as a plausible label), ask the trader — do not guess and proceed.
