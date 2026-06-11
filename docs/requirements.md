# Requirements Document
## SPX 0DTE Strike Survival Probability Model

**Version:** 0.1 (Draft)
**Date:** June 2026
**Status:** For review

---

## 1. Purpose

Produce a model that, given a specific SPX strike level and the current market state at any intraday 5-minute bar, outputs the probability that the strike "survives" to 4:00 PM ET settlement, for use in selling 0DTE directional credit spreads on SPX.

**Survival is a price event** (decided at Gate S, 2026-06-11): the primary label is **no-touch** (SPX never trades at/through the strike before settlement); the secondary label is **settle-beyond** (settlement lands on the safe side). Both are pure price facts computable from bars alone — no option pricing exists anywhere in the label pipeline. The earlier delta-breach label (failure = strike's delta reaching δ\* ≈ 0.30) is **shelved as a documented v2 option**; the trader's actual exits are price-level/risk-state triggers, and the model's purpose is to provide the market counterfactual that judges those exits, not to echo them. (Evidence: `spike/memo.md` addenda — 24% of delta-breaches never touch; early exits cost $77.6k/301 days vs settlement counterfactual.)

The model serves two query modes with a single architecture:

1. **Pre-entry:** queried at a candidate short strike (typically the 0.10 delta level) to decide whether and which side to sell. This explicitly includes the **early session (9:31–10:00 ET), before an opening range exists** — the trader's rules-based system requires a 30-min OR plus confirmation and therefore misses early entries; enabling model-gated pre-OR entries is a primary consumption mode, not an edge case.
2. **Post-entry (monitoring):** queried repeatedly at the trader's fixed, already-sold strike as the day progresses, producing a live risk reading — in particular at the moments the trading system's early-exit rules fire, where the model's number is the false-breakout discriminator (see `spike/memo.md` Addendum 2 for the measured economic case).

The model's value proposition is **not** to re-derive option delta. Delta is the market's own survival estimate. The model's output is only useful insofar as it deviates, in a calibrated and persistent way, from the market-implied probability — by conditioning on intraday path, realized-vs-implied volatility, day structure, and calendar state that delta cannot see. The primary success metric is therefore defined relative to the market-implied baseline (Section 8).

## 2. Scope

**In scope**
- SPX index, 0DTE (PM-settled), regular trading hours 9:30 AM–4:00 PM ET, 1-minute bar resolution.
- Both put-side and call-side strike survival, modeled jointly with a side indicator (or as two heads).
- Probability output for arbitrary strikes within the trained delta range (~0.05 to ~0.35 delta at query time).
- Historical strike-grid placement and labeling without any options chain history: labels are price facts from SPX 1-min bars; the strike grid and the implied baseline use VIX1D via a simple analytic framework.

**Hard data constraint:** no options data is purchased, ever. The only real chain data in the project is the trader's own recordings (`eleuthera/events/`, 303 days and growing) — used exclusively to validate the analytic baseline, as candidate features (recorded deltas), and for the shelved v2 delta-breach option. Never as a pipeline dependency.

**Out of scope (v1)**
- Direction/price-target prediction.
- Multi-day expirations, other underlyings, 0DTE on futures.
- Spread width, credit, or P&L estimation (the model outputs probability only; sizing and EV logic are a downstream consumer).
- Execution, order routing, automation of trading decisions.
- Raw-sequence/deep-learning approaches (insufficient data; revisit only if GBT residual analysis suggests unexploited temporal structure).

## 3. Definitions

| Term | Definition |
|---|---|
| **Bar** | A 1-minute OHLC interval, RTH only (390 bars/day). SPX is a calculated index — bars carry no volume, and no feature requires it. Touch detection, the forward label scan, and live monitoring all operate at this resolution. |
| **Sample stride** | The interval at which training rows are generated (configurable; default every 5 minutes). Adjacent 1-min rows add volume but almost no independent information, so labels/monitoring run at 1-min while the training table samples at a coarser stride. |
| **Query time t** | The timestamp of a bar at which the model is evaluated. |
| **T(t)** | Time remaining from t to 4:00 PM ET settlement, in year fraction. |
| **Strike K** | The short strike under evaluation. Input to the model, never baked into it. |
| **Strike encoding** | K is fed to the model as its **normalized distance** D = (S − K)/(σ√T·S) (primary; sign convention: positive = OTM), plus side and T. Never raw price. Recorded chain deltas are a candidate *ablation* feature for days that have them — not a v1 input (missing for ~40% of the universe). |
| **σ(t)** | Volatility input: VIX1D (daily anchor; intraday series as it accumulates; sensitivity-tested). Used for normalization, features, and the analytic baseline — label computation does not use σ at all. |
| **Touch / failure (primary label)** | SPX trades at or through K at any 1-min bar in (t, settlement]: bar low ≤ K for puts, bar high ≥ K for calls. |
| **Survival / success (primary label)** | Price never touches K through settlement. |
| **Settle-beyond (secondary label)** | Settlement price is on the safe side of K (above K for puts, below for calls). |
| **Closest approach (auxiliary target)** | min over (t, settlement] of the distance from price to K, in remaining-implied-move units (σ√T·S). The continuous analog of "how close did it get" — supports a future quantile variant. |
| **Implied baseline p_mkt** | The market-implied no-touch probability for strike K at time t, from the analytic barrier formula under the same σ convention (reflection-principle transform of the normalized distance). Validated against recorded chains (FR-5). |
| **Edge residual** | Model probability minus implied baseline, p_model − p_mkt. The actual product of this project. |
| **δ\* / delta-breach (v2, shelved)** | The original failure definition (strike's delta reaches δ\* ≈ 0.30). Retained in this doc only as the documented v2 option; see Section 6 decision 3. |

## 4. Functional Requirements

### FR-1: Model inputs (per query)
1. Strike encoding: normalized distance in σ√T units; side (put/call). (Recorded chain delta: Phase 4 ablation candidate only, days where available.)
2. Clock/calendar: minutes since open; minutes to settlement; day of week; event flags (FOMC, CPI, NFP, OPEX, half-days).
3. Volatility state: VIX1D level; VIX1D change vs. prior day; realized intraday vol (annualized from 1-min returns, open→t; if microstructure noise inflates the estimator, a subsampled variant — e.g., 5-min-spaced returns on the 1-min grid — may be substituted, decided in feature QA) ÷ implied (VIX1D at open); trailing 5-day realized vol.
4. Today's tape (all point-in-time as of t, all normalized — no raw points): range-so-far ÷ implied expected range; price position within today's range [0,1]; gap size in ATR units; gap-filled flag; directional-close persistence count (computed on a 5-min rollup of the 1-min bars to avoid noise-dominated counts); efficiency ratio (net move ÷ path length, same rollup); open-drive distance.
5. Prior-day context: yesterday's close location in its range; today's open vs. yesterday's range (inside/above/below + distance); 3-day momentum in ATR units; prior-day range vs. its own implied move.

Initial feature budget: **20–30 features.** Additions require demonstrated importance and no degradation in per-bucket calibration.

### FR-2: Model output
- A probability in [0,1] of survival to settlement for the queried (strike, side, state).
- Output must be **calibrated** (post-hoc calibration pass required, e.g. isotonic or Platt on a held-out fold).
- Alongside the probability, the system shall report p_mkt and the edge residual for the same query.

### FR-3: Label generation pipeline
1. For each historical day, each sample-stride bar t, each side, and each strike on the candidate grid (FR-4):
   - Scan forward **at 1-minute resolution** from t to settlement against the bar highs/lows.
   - Primary label = 1 (survive) if price never touches K; 0 (failure) at first touch, with touch time recorded.
2. Secondary label: settlement beyond strike. The (no-touch, settle-beyond) pair distinguishes "touched but recovered" — kept for analysis of stop-vs-hold economics.
3. Auxiliary continuous target: **closest approach** in remaining-implied-move units over (t, settlement] — recorded for every sample to support a future quantile-regression variant.
4. No option pricing, σ path, or greek computation anywhere in this pipeline — labels depend only on the SPX bar series and K.

### FR-4: Strike grid placement (historical training rows)
1. Training strikes are placed at fixed **normalized-distance anchors** from spot: D ∈ {0.8, 1.0, 1.3, 1.6, 2.1} remaining-implied-move units (σ√T·S, σ = VIX1D-based), i.e. K = S ∓ D·σ√T·S, snapped to the listed 5-pt increment. These anchors approximate the 0.30/0.20/0.15/0.10/0.05Δ region the trader actually quotes (exact mapping documented in Phase 1).
2. The snapped strike's actual normalized distance is what enters the feature row — anchors are sampling targets, not data values.
3. Acceptance: on recorded-chain days, the grid's span must cover the chain's actual 0.05–0.30Δ strikes per side ≥ ~95% of (day × stride-bar) observations — i.e. the training distribution covers where live queries will land. (Pricing-accuracy acceptance is obsolete: nothing in the pipeline claims to price options.)

### FR-5: Validation against recorded chains and the trading system (no purchased data)
0. ~~Pricing proof-of-concept~~ *(completed as Phase S — see `spike/memo.md`; outcome led to the Gate S price-barrier decision).*
1. **Baseline validation:** the analytic p_mkt (no-touch baseline) must be compared against market-implied touch probabilities from the recorded chains (`eleuthera/events/`) across VIX1D regimes and times of day. p_mkt does not need to be perfect — it needs documented, stable bias characteristics, because beating it is the success criterion. If a calibrated correction (e.g., time-of-day vol curve f(t) on the VIX1D anchor) materially improves baseline honesty, apply it to the baseline and freeze it.
2. **Exit-rule characterization (replaces δ\* calibration):** from the eleuthera backtest logs, document where the system's actual exits (`risk_off_reversal`, `sr_inner_breach`) sit relative to the labels — exit-to-touch frequency and timing, per regime. This is the bridge between model output and overlay value, not a label parameter.
3. **Label spot-validation:** on all 294 logged trades, pipeline labels at the actual entry bar/strike must agree with what happened (touch/settle outcomes verifiable directly). Every disagreement gets a written explanation.

### FR-6: Model class and constraints
1. v1 model: gradient-boosted trees (LightGBM or XGBoost).
2. **Monotonicity constraint enforced** on the normalized-distance input: survival probability must be non-decreasing in distance from the strike (further OTM ⇒ never lower survival probability), using native monotone-constraint support. Verified by sweep tests post-training.
3. Two-head or side-indicator design such that put-side and call-side probabilities are independently queryable.

### FR-7: Data requirements
| Dataset | Resolution | Span | Source notes |
|---|---|---|---|
| SPX OHLC | 1-min, RTH | ≥4 years (≥ ~1,000 days) | Index prints (no volume — calculated index); verify bar alignment & holidays |
| VIX1D | Daily minimum; intraday preferred | Full available history (≈2022→) | CBOE; document fallback when intraday unavailable |
| SPX 0DTE option chains | Every quote/greek change | `eleuthera/events/`: 303 days (2024-12-17→), growing daily; never purchased | Baseline validation, ablation features, v2 option — not a pipeline dependency |
| Economic calendar | Daily flags | Same span | FOMC/CPI/NFP/OPEX/half-days |
| Trading-system log | Per trade + per-minute signal state | `eleuthera/analyzer/logs`: 294 spreads over 301 days (rules replay) | Exit-rule characterization (FR-5.2), label spot-validation (FR-5.3), overlay economics |

History predating VIX1D availability is excluded from v1 rather than approximated with VIX.

## 5. Non-Functional Requirements

### NFR-1: Leakage controls (hard requirements)
1. **Point-in-time discipline:** every feature at bar t computed strictly from data available at t. Enforced by an automated test that recomputes features on truncated data and asserts equality.
2. **Split discipline:** train/validation/test splits by **day at minimum, preferably by week, with an embargo gap** between train and test periods. Random row-level splits are prohibited. Rationale: labels within a day (and across the strike grid at a single bar) share the same forward path and are massively correlated — adjacent 1-minute samples are near-duplicates — so row-level splits produce inflated, fake performance. The 1-min resolution increases row count ~5x over a 5-min design while adding essentially zero independent information; effective N remains ≈ trading days.
3. Final test period is chronologically last and touched once.

### NFR-2: Evaluation honesty
1. Metrics reported **per strike-distance bucket**, labeled by their approximate delta equivalents (e.g., ≈0.05–0.10Δ, ≈0.10–0.15Δ, ≈0.15–0.25Δ), as well as pooled. The **band of record is the ≈0.10–0.15Δ-equivalent bucket** (boundaries fixed in implied-move units during Phase 1 and cross-checked against recorded chain deltas on days that have them), since pooled metrics are flattered by easy far-OTM samples.
1b. Metrics additionally reported for **named use-case slices**, pre-registered here: (a) **early session** — queries at bars 9:31–10:00 ET (pre-OR entry mode; expected hardest slice — day-open features are undefined/sparse, premium and vol seasonality richest); (b) **near-strike stress** — bars where price has closed to within ~0.5 remaining-implied-move units of the queried strike (recorded delta ≥ ~0.20 on chain days; the false-breakout discrimination mode); (c) **post-breakout retest** — bars following a breakout of the session's prior high/low. A model that wins pooled but fails the slices fails the project's purpose.
2. Day-level aggregation acknowledged in all statistics (effective N ≈ trading days, not rows).
3. Primary metrics: **Brier score and calibration curves**, model vs. implied baseline. AUC reported secondarily. Raw accuracy is not a metric (a constant "survive" prediction scores ~80%+).

### NFR-3: Reproducibility & ops
1. Deterministic pipeline: fixed seeds, versioned data snapshots, config-driven feature definitions.
2. Every feature documented as: name, formula, lookback, normalization, point-in-time rule (the feature spec doubles as the pipeline requirements).
3. Live-query latency target: < 1 second per bar for both sides at one strike (trivial for GBT; stated to preclude architecture creep).
4. Internal consistency rule: grid placement, the normalized-distance encoding, bucket boundaries, and p_mkt must all use the **same** σ source and √T convention. Model-relative consistency outranks absolute accuracy of any component.

## 6. Explicit Design Decisions (settled in discovery)

1. Strike is a **model input**, not embedded in the label — supports both pre-entry and post-entry (fixed strike, shrinking T) queries with one model.
2. Strike encoded as **normalized distance** (implied-move units), not price — makes input space stationary across years and regimes; time and vol remain separate features because conditional dynamics differ at equal distance. *(Amended at Gate S from "current delta": distance is computable for every bar of history with no options machinery, and is the same quantity delta proxies.)*
3. Failure = **strike touch** (primary), settle-through (secondary) — REVERSED at Gate S (2026-06-11) from the original delta-threshold breach. Rationale: (a) the trader's real exits proved to be price-level/risk-state triggers, not credit multiples, so δ\* never matched actual behavior; (b) the model's purpose is to *override* early exits on false breakouts, which requires the market counterfactual ("did the level actually fail?"), not an echo of the panic; (c) price labels need no option pricing, removing the entire derived-pricing risk surface from v1. The delta-breach design is shelved as v2, buildable from `eleuthera/events/` recorded deltas (2024-12→) if post-v1 analysis shows the managed-exit gap carries signal the touch label misses.
4. Opening-range logic **removed entirely**; model is a general per-bar conditional survival model. Any OR-related signal can return later as ordinary features.
5. Prior-day information enters as **engineered summary features**, not raw sequences. Expected to matter primarily through vol/regime channels, not directional carry.
6. No hand-labeled trend/chop day classifier; the model composes its own from ingredient features (efficiency ratio, persistence counts, range expansion), optimized against actual touch outcomes.

## 7. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Analytic p_mkt baseline mis-calibrated | Fake "edge" vs a soft baseline, or real edge masked | FR-5.1 validation against 303 recorded-chain days; documented bias characteristics; frozen correction if needed |
| Touch label diverges from managed-exit reality | Model probability ≠ trader's realized P(win) under live exit rules | FR-5.2 exit-rule characterization; overlay economics reported separately (rules+model vs rules); v2 delta-breach option held in reserve |
| Correlated-sample overfitting | Fake research performance, live failure | NFR-1 split discipline; week splits + embargo; day-level effective-N reporting |
| Lookahead bugs in features | Silently inflated results | Automated truncation test (NFR-1.1); code review checklist |
| Model merely reproduces delta | No edge; wasted effort | Edge-residual evaluation is primary (Sec. 8); pre-registered kill criterion |
| VIX1D history short (≈2022→) | Limited regimes seen | Accept reduced span in v1; monitor for regime novelty live; do not backfill with VIX |
| Miscalibrated confidence | Bad sizing decisions downstream | Mandatory post-hoc calibration; per-bucket calibration curves as gating |

## 8. Success Criteria (v1 go/no-go)

The model ships to paper/shadow use only if, on the untouched final test period:

1. **Calibration:** per-bucket calibration error in the band of record (≈0.10–0.15Δ-equivalent) within ±3 percentage points across the probability range actually produced.
2. **Edge over market:** Brier score strictly better than the implied-baseline Brier in the band of record, with the improvement stable across test sub-periods (not driven by one regime). Reported additionally per NFR-2.1b slice.
3. **Sanity:** zero monotonicity violations in strike sweeps; pooled mean prediction within ~2 points of pooled implied mean (deviations conditional, not systematic).
4. **Label fidelity:** FR-5.3 spot-validation passes on the trader's real trade days.

**Kill criterion (pre-registered):** if the edge residual is statistically indistinguishable from zero in the band of record after honest evaluation, the conclusion is that delta was sufficient — the project stops at a documented negative result rather than proceeding to threshold-tuning until something "works."

## 9. Deliverables

1. Data pipeline (ingestion, strike reconstruction, labeling) — versioned, tested.
2. Feature library with spec sheet per feature.
3. Trained, calibrated GBT model artifact + monotonicity verification report.
4. Evaluation report: per-bucket calibration, Brier vs. baseline, residual analysis, regime breakdown.
5. Query interface: (timestamp, strike, side) → {p_model, p_mkt, residual} for both live monitoring and research replay.
6. This document, updated to "as-built" at v1 close.
