# Requirements Document
## SPX 0DTE Strike Survival Probability Model

**Version:** 0.1 (Draft)
**Date:** June 2026
**Status:** For review

---

## 1. Purpose

Produce a model that, given a specific SPX strike level and the current market state at any intraday 5-minute bar, outputs the probability that the strike "survives" to 4:00 PM ET settlement without triggering a stop-out, for use in selling 0DTE directional credit spreads on SPX.

The model serves two query modes with a single architecture:

1. **Pre-entry:** queried at a candidate short strike (typically the 0.10 delta level) to decide whether and which side to sell.
2. **Post-entry (monitoring):** queried repeatedly at the trader's fixed, already-sold strike as the day progresses, producing a live risk reading.

The model's value proposition is **not** to re-derive option delta. Delta is the market's own survival estimate. The model's output is only useful insofar as it deviates, in a calibrated and persistent way, from the market-implied probability — by conditioning on intraday path, realized-vs-implied volatility, day structure, and calendar state that delta cannot see. The primary success metric is therefore defined relative to the market-implied baseline (Section 8).

## 2. Scope

**In scope**
- SPX index, 0DTE (PM-settled), regular trading hours 9:30 AM–4:00 PM ET, 1-minute bar resolution.
- Both put-side and call-side strike survival, modeled jointly with a side indicator (or as two heads).
- Probability output for arbitrary strikes within the trained delta range (~0.05 to ~0.35 delta at query time).
- Historical strike reconstruction and labeling without full options chain history, via skew-calibrated Black-Scholes using VIX1D.

**Hard data constraint:** no options data is purchased, ever. All option pricing is derived from SPX 1-min bars plus VIX1D. The only real chain data in the project is the trader's own recorded snapshots, used exclusively to calibrate the skew multipliers and validate the derived pricing.

**Out of scope (v1)**
- Direction/price-target prediction.
- Multi-day expirations, other underlyings, 0DTE on futures.
- Spread width, credit, or P&L estimation (the model outputs probability only; sizing and EV logic are a downstream consumer).
- Execution, order routing, automation of trading decisions.
- Raw-sequence/deep-learning approaches (insufficient data; revisit only if GBT residual analysis suggests unexploited temporal structure).

## 3. Definitions

| Term | Definition |
|---|---|
| **Bar** | A 1-minute OHLC interval, RTH only (390 bars/day). SPX is a calculated index — bars carry no volume, and no feature requires it. Breach detection, the forward delta walk, and live monitoring all operate at this resolution. |
| **Sample stride** | The interval at which training rows are generated (configurable; default every 5 minutes). Adjacent 1-min rows add volume but almost no independent information, so labels/monitoring run at 1-min while the training table samples at a coarser stride. |
| **Query time t** | The timestamp of a bar at which the model is evaluated. |
| **T(t)** | Time remaining from t to 4:00 PM ET settlement, in year fraction. |
| **Strike K** | The short strike under evaluation. Input to the model, never baked into it. |
| **Strike encoding** | K is fed to the model as its **current Black-Scholes delta** (primary) and normalized distance (S − K)/(σ√T·S) (secondary), not raw price. |
| **σ(t)** | Volatility input: VIX1D (intraday series where available; entry-time or daily value as fallback, sensitivity-tested). |
| **Skew multiplier m** | Per-side calibration constant(s) mapping BS-implied 0.xx-delta strike distance to actual market 0.xx-delta strike distance, estimated from real 0DTE chains. Separate m_put and m_call; optionally bucketed by VIX1D regime. |
| **Delta-stop threshold δ\*** | The short-strike delta level treated as a stop-out (initial estimate 0.30; to be calibrated against the trader's actual stop practice in credit-multiple terms, expected range 0.20–0.35). |
| **Breach / failure** | The strike's computed delta meets or exceeds δ\* at any bar in (t, settlement]. |
| **Survival / success** | Delta stays below δ\* through settlement. (Settlement-beyond-strike retained as a secondary sanity label.) |
| **Implied baseline p_mkt** | The market-implied survival probability for strike K at time t, derived analytically from its delta (touch/breach probability ≈ a fixed transform of delta within the BS framework, consistent with the labeling machinery). |
| **Edge residual** | Model probability minus implied baseline, p_model − p_mkt. The actual product of this project. |

## 4. Functional Requirements

### FR-1: Model inputs (per query)
1. Strike encoding: current BS delta of K; normalized distance in σ√T units; side (put/call).
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
1. For each historical day, each sample-stride bar t, each side, and each strike on a candidate grid (the 0.05, 0.10, 0.15, 0.20, 0.30 delta levels at t, computed via skew-adjusted BS):
   - Walk forward **at 1-minute resolution** from t to settlement, recomputing the strike's delta with updated S, T, σ.
   - Label = 1 (survive) if delta < δ\* throughout; 0 (failure) on first breach.
2. Secondary label: settlement beyond strike (expiry-style), retained for sanity analysis of the gap between managed and held-to-settlement outcomes.
3. Auxiliary continuous target: **max delta reached** over (t, settlement] — recorded for every sample to support a future quantile-regression variant.

### FR-4: Strike reconstruction (historical)
1. Raw BS strikes: K_put = S·exp(−σ√T·1.2816 − σ²T/2), K_call mirrored (rates/dividends ignored at 0DTE horizon).
2. Skew correction: K = S ∓ m_side·(S ∓ K_BS), with m_put and m_call calibrated per FR-5.
3. Acceptance: reconstructed 0.10Δ strikes land within **one strike increment (5 pts)** of the actual chain's 0.10Δ strike on ≥ ~80% of calibration-sample observations, with no systematic one-sided bias by vol regime.

### FR-5: Calibration from real chains (trader-recorded snapshots only — no purchased data)
0. **Pricing proof-of-concept (first milestone):** before any other calibration work, demonstrate on one recorded chain day that derived pricing recovers the chain — i.e., for each grid delta, the BS-derived strike (from 1-min bars + vol input) matches the actual chain's strike at that delta within tolerance, across multiple intraday timestamps. This de-risks the entire derived-pricing approach before further investment.
1. Estimate m_put, m_call from the trader's recorded chains. Target coverage: ≥3 VIX1D regimes (calm/mid/elevated) and ≥2 times of day; coverage grows only by recording additional days going forward, not by purchase. If drift across regimes is material, bucket m by VIX1D tercile rather than fitting a curve. If coverage is incomplete at calibration time, fit m on available regimes and document the gap as a live-monitoring risk.
2. Calibrate δ\*: map the trader's actual stop rule (≈2–3× credit) to a short-strike delta level by simulating known past trades; choose the threshold that best reproduces real stop-out decisions.
3. Spot-validate generated labels on days actually traded: pipeline-marked failures must correspond to trades the trader would in fact have stopped.

### FR-6: Model class and constraints
1. v1 model: gradient-boosted trees (LightGBM or XGBoost).
2. **Monotonicity constraint enforced** on the strike-delta input: survival probability must be non-increasing in strike delta (further OTM ⇒ never lower survival probability), using native monotone-constraint support. Verified by sweep tests post-training.
3. Two-head or side-indicator design such that put-side and call-side probabilities are independently queryable.

### FR-7: Data requirements
| Dataset | Resolution | Span | Source notes |
|---|---|---|---|
| SPX OHLC | 1-min, RTH | ≥4 years (≥ ~1,000 days) | Index prints (no volume — calculated index); verify bar alignment & holidays |
| VIX1D | Daily minimum; intraday preferred | Full available history (≈2022→) | CBOE; document fallback when intraday unavailable |
| SPX 0DTE option chains | Snapshots | Trader-recorded days only; coverage expanded by recording new days, never by purchase | Calibration & validation only — not a training dependency |
| Economic calendar | Daily flags | Same span | FOMC/CPI/NFP/OPEX/half-days |
| Trader's trade log | Per trade | All available | δ\* calibration and label spot-validation |

History predating VIX1D availability is excluded from v1 rather than approximated with VIX.

## 5. Non-Functional Requirements

### NFR-1: Leakage controls (hard requirements)
1. **Point-in-time discipline:** every feature at bar t computed strictly from data available at t. Enforced by an automated test that recomputes features on truncated data and asserts equality.
2. **Split discipline:** train/validation/test splits by **day at minimum, preferably by week, with an embargo gap** between train and test periods. Random row-level splits are prohibited. Rationale: labels within a day (and across the strike grid at a single bar) share the same forward path and are massively correlated — adjacent 1-minute samples are near-duplicates — so row-level splits produce inflated, fake performance. The 1-min resolution increases row count ~5x over a 5-min design while adding essentially zero independent information; effective N remains ≈ trading days.
3. Final test period is chronologically last and touched once.

### NFR-2: Evaluation honesty
1. Metrics reported **per delta bucket** (e.g., 0.05–0.10, 0.10–0.15, 0.15–0.25) as well as pooled. The 0.10–0.15 band is the band of record, since pooled metrics are flattered by easy far-OTM samples.
2. Day-level aggregation acknowledged in all statistics (effective N ≈ trading days, not rows).
3. Primary metrics: **Brier score and calibration curves**, model vs. implied baseline. AUC reported secondarily. Raw accuracy is not a metric (a constant "survive" prediction scores ~80%+).

### NFR-3: Reproducibility & ops
1. Deterministic pipeline: fixed seeds, versioned data snapshots, config-driven feature definitions.
2. Every feature documented as: name, formula, lookback, normalization, point-in-time rule (the feature spec doubles as the pipeline requirements).
3. Live-query latency target: < 1 second per bar for both sides at one strike (trivial for GBT; stated to preclude architecture creep).
4. Internal consistency rule: strike placement, delta monitoring, δ\*, and p_mkt must all live in the **same** BS-plus-skew framework. Model-relative consistency outranks absolute option-pricing accuracy.

## 6. Explicit Design Decisions (settled in discovery)

1. Strike is a **model input**, not embedded in the label — supports both pre-entry and post-entry (fixed strike, shrinking T) queries with one model.
2. Strike encoded as **current delta**, not price — makes input space stationary across years and regimes; time and vol remain separate features because conditional dynamics differ at equal delta.
3. Failure = **delta-threshold breach**, not strike touch and not settlement-only — matches actual stop behavior; price re-entering prior ranges early in the day correctly does not label as failure; time-of-day sensitivity arises automatically through √T.
4. Opening-range logic **removed entirely**; model is a general per-bar conditional survival model. Any OR-related signal can return later as ordinary features.
5. Prior-day information enters as **engineered summary features**, not raw sequences. Expected to matter primarily through vol/regime channels, not directional carry.
6. No hand-labeled trend/chop day classifier; the model composes its own from ingredient features (efficiency ratio, persistence counts, range expansion), optimized against actual breach outcomes.

## 7. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Skew multiplier unstable across regimes | Mislabeled strikes → noisy labels | Regime-bucketed m; acceptance test in FR-4.3; record additional chain days if failing (no purchases) |
| Recorded-chain sample too thin for regime coverage | m calibrated on narrow conditions; silent bias in unseen regimes | FR-5.0 proof-of-concept first; document coverage gaps; keep recording chains across regimes during development |
| Correlated-sample overfitting | Fake research performance, live failure | NFR-1 split discipline; week splits + embargo; day-level effective-N reporting |
| Lookahead bugs in features | Silently inflated results | Automated truncation test (NFR-1.1); code review checklist |
| Model merely reproduces delta | No edge; wasted effort | Edge-residual evaluation is primary (Sec. 8); pre-registered kill criterion |
| VIX1D history short (≈2022→) | Limited regimes seen | Accept reduced span in v1; monitor for regime novelty live; do not backfill with VIX |
| Miscalibrated confidence | Bad sizing decisions downstream | Mandatory post-hoc calibration; per-bucket calibration curves as gating |
| δ\* mismatch with real behavior | Labels train against the wrong stop | FR-5.2 calibration against actual trade log; re-derive if stop practice changes |

## 8. Success Criteria (v1 go/no-go)

The model ships to paper/shadow use only if, on the untouched final test period:

1. **Calibration:** per-bucket calibration error in the 0.10–0.15Δ band within ±3 percentage points across the probability range actually produced.
2. **Edge over market:** Brier score strictly better than the implied-baseline Brier in the 0.10–0.15Δ band, with the improvement stable across test sub-periods (not driven by one regime).
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
