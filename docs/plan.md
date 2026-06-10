# Project Plan & Task List
## SPX 0DTE Strike Survival Probability Model

**Companion to:** Requirements Document v0.1
**Structure:** A de-risking spike (Phase S) followed by 7 gated phases. Each phase has a gate — do not start the next phase until the gate passes. Phases 0–2 are deliberately front-loaded: most quant projects die from bad labels and leakage, not bad models, so the unglamorous work comes first.

**Standing data constraint:** no options data is purchased. All option pricing is derived from SPX 1-min bars + VIX1D; the only real chains are the trader's own recordings.

---

## Phase S — Derived-Pricing Proof of Concept (the spike)

**Goal:** Prove, on one day of real data, that we can derive option pricing well enough to recover which strike corresponds to a given delta — before building anything else. This is FR-5.0 and the project's first deliverable.

- [x] **S.1** Ingest the spike inputs: one day of SPX 1-min bars and the trader's recorded 0DTE chain for that same day. Document the chain's format (snapshot times, fields available: bid/ask/mid, delta, IV). *(Done — 2026-06-01; format documented in `spike/memo.md`. Timestamps are US/Pacific.)*
- [x] **S.2** Establish the vol input for that day and document what was used. *(Done — the recording is VIX 1-min, not VIX1D; substitution documented and analyzed in the memo.)*
- [x] **S.3** Implement the raw BS strike solver K(delta, S, σ, T) for puts and calls (pulls task 1.1 forward); unit-test against textbook values. *(Done — `spike/derive_pricing.py`; verified against the platform's own recorded greeks.)*
- [x] **S.4** At each chain snapshot time: derive the 0.05/0.10/0.15/0.20/0.30Δ strikes per side from bars + vol, and extract the actual strikes at those deltas from the recorded chain. *(Done — 13 snapshots × 2 sides × 5 deltas × 3 σ variants → `spike/results.csv`.)*
- [x] **S.5** Compare: error in strike points and in strike increments, per side, per delta level, per time of day. Compute the implied skew multiplier m = actual distance ÷ BS distance at each point — is it stable enough within the day to look calibratable? *(Done — m_put ≈ 1.30 @0.10Δ, m_call ≈ 1.05, both stable; vol level identified as the dominant error source.)*
- [x] **S.6** Write the spike memo: error tables, whether raw BS alone or BS+single-m gets the 0.10Δ strike within one 5-pt increment, and what the result implies about FR-4.3 feasibility. *(Done — `spike/memo.md`. Finding: feasible with the right vol level (92% within one strike at 0.10Δ); requires adding a time-of-day vol multiplier curve f(t) to the calibration framework.)*

**Gate S:** Trader reviews the memo and decides the derived-pricing approach is viable (possibly with regime-bucketed m to be calibrated later). If derived pricing cannot plausibly hit the FR-4.3 tolerance even on one in-sample day, stop and rethink before Phase 0. **Decision items for the trader in `spike/memo.md` §Recommendations: (1) adopt f(t) vol-curve amendment, (2) VIX vs VIX1D convention, (3) ongoing chain-recording plan.**

---

## Phase 0 — Data Foundation

**Goal:** All raw inputs acquired, cleaned, versioned, and queryable.

- [ ] **0.1** Acquire SPX 1-min OHLC, ≥4 years, RTH only (SPX is a calculated index — no volume exists). **Source decided: IB TWS API via `data/ib_download.py` ($0; trader's own brokerage). Cross-check vendor bars against the trader's own 2026-06-01 recording (timezone: IB=ET, recordings=PT).** Verify: 390 bars/day, correct holiday/half-day handling, no duplicate or missing bars; document any vendor quirks (index print vs. futures-derived). Note: 1-min index data has more vendor-specific gaps/anomalies than 5-min — budget extra QA time here.
- [ ] **0.2** Acquire VIX1D history (daily at minimum; intraday if available). Document exact availability start date — this sets the training-universe start (index launched 2023-04-23). **Source: IB TWS API (`data/ib_download.py --check` verifies VIX1D listing + history depth); CBOE's free daily CSV as fallback/cross-check. Also pull VIX 1-min + deep daily SPX/VIX for regime-percentile features.**
- [ ] **0.3** Build the economic calendar table: FOMC, CPI, NFP, monthly/quarterly OPEX, half-days, for the full span.
- [ ] **0.4** Inventory the trader's existing 0DTE chain snapshots: list days, times, VIX1D level on each. Identify regime gaps (target ≥3 VIX1D regimes × ≥2 times of day per FR-5.1).
- [ ] **0.5** If gaps exist, set up an ongoing recording habit/process so the trader captures chain snapshots on days in the missing regime cells (no purchases — coverage grows only through recording). Document which cells remain open at Gate 0.
- [ ] **0.6** Digitize the trade log: entry time, side, strike, stop/exit time and reason, credit, for every past 0DTE trade available.
- [ ] **0.7** Stand up versioned storage (even just parquet + a manifest with hashes) and a single load API all later code uses. No notebook reads raw vendor files directly.

**Gate 0:** A script reproduces every dataset from raw → clean with one command; bar-count and calendar audits pass; chain coverage matrix documented (empty cells flagged as risks, with a recording plan — they do not block the gate, since coverage can only grow over calendar time).

---

## Phase 1 — Strike Reconstruction & Calibration

**Goal:** Skew-adjusted BS machinery that places historical 0.10Δ (and grid) strikes within tolerance, plus a calibrated stop threshold.

- [ ] **1.1** Promote the spike's BS strike solver (S.3) to production: move behind the load API, full unit tests against textbook values.
- [ ] **1.2** From each calibration chain: extract the actual 0.05/0.10/0.15/0.20/0.30Δ strikes per side; compute m = actual distance ÷ BS distance for each.
- [ ] **1.3** Analyze m stability: by side, by VIX1D regime, by time of day, by delta level. Decide: single m per side vs. VIX1D-tercile buckets (FR-5.1).
- [ ] **1.4** Lock m values; implement the adjusted strike function and freeze it behind the load API.
- [ ] **1.5** Acceptance test (FR-4.3): on held-out chain snapshots (not used to fit m), reconstructed 0.10Δ strike within 5 pts of actual ≥ ~80% of the time, no systematic one-sided bias by regime. If failing: record additional chain days, revisit bucketing, re-test.
- [ ] **1.6** Implement forward delta computation: delta of a fixed K at any later bar given updated S, T, σ; define the σ-update rule (intraday VIX1D, or hold-at-entry fallback).
- [ ] **1.7** Calibrate δ\* (FR-5.2): replay logged trades through the delta engine; find the delta level that best reproduces actual stop-outs from the 2–3× credit rule. Record chosen δ\* and the evidence.
- [ ] **1.8** Sensitivity memo: how labels shift if σ-update rule changes (intraday vs. static) and if δ\* moves ±0.05. If labels are fragile to these, flag before proceeding.

**Gate 1:** 1.5 acceptance passes; δ\* documented and reproduces real stop behavior on the trade log; sensitivity memo reviewed.

---

## Phase 2 — Label Generation

**Goal:** The full training table's target columns, validated.

- [ ] **2.1** Implement the per-bar label engine (FR-3): for every day × sample-stride bar × side × grid delta {0.05, 0.10, 0.15, 0.20, 0.30}, place the strike, walk forward **at 1-min resolution**, emit: binary survival label, breach time (if any), max delta reached, settlement-beyond-strike secondary label. Sample stride configurable (default 5 min); breach detection always 1-min.
- [ ] **2.1b** Stride sensitivity check: regenerate labels for a sample month at 1-min stride and confirm conclusions in 2.3 are stride-invariant before committing the default.
- [ ] **2.2** Run over full history; persist as the labels table keyed by (date, stride bar, side, grid_delta).
- [ ] **2.3** Sanity statistics: base survival rates per delta bucket vs. theory (0.10Δ touch-style failure should land in a plausible ~15–25% zone, monotone across the grid); failure-time distributions; put/call asymmetry; regime breakdowns. Investigate anything implausible before continuing.
- [ ] **2.4** Spot-validation (FR-5.3): for every day in the trade log, compare pipeline labels at the trader's actual entry bar/strike against what really happened. Every disagreement gets a written explanation (acceptable noise vs. pipeline bug).
- [ ] **2.5** Visual audit: plot ~15 randomly sampled days (price path, strikes, breach markers) and have the trader eyeball them — cheap and catches bugs statistics miss.

**Gate 2:** 2.3 statistics plausible; 2.4 disagreements all explained; visual audit signed off.

---

## Phase 3 — Feature Engineering

**Goal:** The 20–30 feature columns, point-in-time-safe, each specified.

- [ ] **3.1** Write the feature spec sheet first (name, formula, lookback, normalization, point-in-time rule) covering the FR-1 list: strike encoding, clock/calendar, vol state, today's tape, prior-day context. Trader reviews the spec before code.
- [ ] **3.2** Implement features against the load API. One function per feature, config-registered.
- [ ] **3.3** Build the lookahead test harness (NFR-1.1): recompute every feature at bar t using data truncated at t; assert exact equality with the full-data computation, across many random (day, bar) pairs. This runs in CI on every change.
- [ ] **3.4** Feature QA: distributions, NaN handling at day-open edges (several features are undefined in the first bars — define and document the policy), correlation matrix to spot redundancies.
- [ ] **3.5** Assemble the master training table: features ⋈ labels, one row per (date, stride bar, side, grid_delta). Persist versioned.
- [ ] **3.6** Implement p_mkt: implied survival baseline per row from strike delta, consistent with the labeling framework. Store as a column — it is both a feature input and the evaluation benchmark.

**Gate 3:** Lookahead harness green; spec sheet matches code; master table builds reproducibly end-to-end from raw data.

---

## Phase 4 — Modeling

**Goal:** A trained, calibrated, monotone GBT that beats nothing yet — just trains correctly.

- [ ] **4.1** Implement the split scheme (NFR-1.2): chronological splits by week with an embargo gap; final test period (most recent ~15–20% of days) carved out and **locked** — no code path reads it except the Phase 5 final run.
- [ ] **4.2** Baselines first: (a) constant base-rate predictor, (b) p_mkt alone, (c) logistic regression on 5 features. These define the floor and the bar.
- [ ] **4.3** Train v1 GBT (LightGBM/XGBoost) with the monotone constraint on strike delta; side as indicator (compare against two-head variant in 4.6).
- [ ] **4.4** Hyperparameter search via the walk-forward validation folds only — small grid, heavy regularization priors given effective N ≈ days, not rows.
- [ ] **4.5** Post-hoc calibration (isotonic or Platt) fit on a validation fold never used for model selection.
- [ ] **4.6** Ablations on validation folds: side-indicator vs. two heads; with/without prior-day block; with/without p_mkt as input feature; feature-importance review and pruning of dead weight back toward the 20–30 budget.
- [ ] **4.7** Monotonicity verification: strike sweeps at sampled (day, bar) states; zero violations required.
- [ ] **4.8** Residual analysis on validation: where does p_model − p_mkt concentrate (time of day, regime, day-type ingredients)? Does it look like signal or noise? Written up before touching the test set.

**Gate 4:** Model beats baselines (b) and (c) on validation Brier in the 0.10–0.15Δ band; calibration curves acceptable per-bucket; monotonicity clean; residual write-up reviewed.

---

## Phase 5 — Final Evaluation (one shot)

**Goal:** The honest answer, against the pre-registered criteria.

- [ ] **5.1** Freeze: model artifact, calibrator, feature code, m values, δ\* — all hashed and recorded.
- [ ] **5.2** Single run on the locked test period. No iteration. (If something is broken, fix the bug, document it, and the re-run is itself documented — but no tuning against test results.)
- [ ] **5.3** Evaluate Section-8 success criteria: per-bucket calibration (±3 pts in 0.10–0.15Δ); Brier vs. p_mkt baseline with sub-period stability; pooled-mean agreement; monotonicity.
- [ ] **5.4** Rough economic check: approximate spread P&L simulation (BS on spot + VIX1D, no chains needed) comparing "trade everything" vs. "filter/side-select by model probability." This is indicative, not a backtest of record.
- [ ] **5.5** Go/no-go decision against the kill criterion. If killed: write the negative-result memo (what was tested, why delta proved sufficient, what would change the answer) and stop.

**Gate 5:** Documented go or documented no-go. Both are successful project outcomes.

---

## Phase 6 — Shadow Deployment (only on "go")

**Goal:** Live-condition validation without risk, then a defined promotion path.

- [ ] **6.1** Build the live query interface: (timestamp, strike, side) → {p_model, p_mkt, residual}; wire to live/delayed SPX and VIX1D feeds; latency check (< 1 s).
- [ ] **6.2** Shadow log: every 1-min bar of every session, log model output at the would-be 0.10Δ strikes both sides, plus at any strike the trader actually holds.
- [ ] **6.3** Run shadow for a minimum of 2–3 months / ≥40 sessions alongside real (model-blind) trading.
- [ ] **6.4** Weekly drift checks: live calibration vs. test-period calibration; feature distributions vs. training distributions; alert thresholds defined in advance.
- [ ] **6.5** End-of-shadow review: does live edge residual match test-period behavior? Decide promotion to decision-support (model informs but does not gate trades) with explicit rules for what the trader does with the number.
- [ ] **6.6** Retraining policy: scheduled (e.g., quarterly walk-forward refresh) + triggered (drift alert), with the same gates re-applied each refresh.
- [ ] **6.7** Update the requirements doc to as-built; archive the project record.

**Gate 6:** Shadow metrics consistent with test metrics; promotion decision and usage rules written down.

---

## Sequencing & Effort Notes

- **Critical path:** S → 0 → 1 → 2 → 3 → 4 → 5. Phase S needs only one day of bars + one recorded chain, so it starts immediately. Phase 3 spec-writing (3.1) can start during Phase 2 runs; calendar table (0.3) and trade-log digitization (0.6) are parallelizable from day one.
- **Expected effort distribution:** roughly half the total work lives in Phases 0–2. If modeling (Phase 4) is consuming most of the calendar, something upstream was rushed.
- **Standing rules for every phase:** all code against the versioned load API; lookahead harness in CI from Phase 3 onward; the locked test set is read by exactly one script, run in exactly one phase.
- **Single most dangerous failure mode:** correlated-sample leakage making validation look great. Treat NFR-1 as inviolable; when in doubt, split coarser (week → month) and accept noisier estimates over biased ones.
