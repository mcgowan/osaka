# Project Plan & Task List
## SPX 0DTE Strike Survival Probability Model

**Companion to:** Requirements Document v0.1
**Structure:** A de-risking spike (Phase S) followed by 7 gated phases. Each phase has a gate — do not start the next phase until the gate passes. Phases 0–2 are deliberately front-loaded: most quant projects die from bad labels and leakage, not bad models, so the unglamorous work comes first.

**Standing data constraint:** no options data is purchased. Labels are price facts from SPX 1-min bars (no option pricing in the pipeline, per Gate S); the only real chains are the trader's own recordings (`eleuthera/events/`), used for baseline validation and ablations only.

## Status (as of 2026-06-11)

| Phase | State |
|---|---|
| **S — Derived-Pricing Spike** | Tasks S.1–S.6 ✅ + addenda (stop-vs-touch gap; false-breakout economics). **Gate S ✅ passed 2026-06-11: trader chose the price-barrier restructure.** Labels = price facts; option pricing removed from the pipeline; delta-breach design shelved as v2. |
| **0 — Data Foundation** | **All tasks ✅ (0.1–0.7). Gate 0 ready for trader review.** Late discovery: `eleuthera/events/` = 303 days of recorded chains; `eleuthera/analyzer/logs` = the 294-trade rule-behavior log. False-breakout cost measured: $77.6k over 301 days (`spike/memo.md` Addendum 2) — the project's economic case, quantified. |
| **1 — Grid, Baseline & Validation** | **All tasks ✅, Gate 1 ✅ passed (trader sign-off 2026-06-11, post-review)** — evidence in `docs/calibration.md`. Frozen: σ convention, 7-anchor per-side grid, bucket edges, f(t)-corrected p_mkt baseline. |
| **2 — Label Generation** | **All tasks ✅, Gate 2 ✅ passed (trader sign-off 2026-06-12)**. `TEST_START = 2025-12-01` ratified and enforced in the load APIs. Labels: 847k rows/783 days (default loads serve pre-boundary 704k/651). Engine math-verifier VERIFIED; redteam BLOCK→remediated; spot-validation 128/128 + 294/294. |
| **3 — Feature Engineering** | In progress — 3.1 feature spec drafted, **awaiting trader review before any feature code** (per task 3.1). |
| **4–6** | Not started. |

Data inventory: SPX 1-min 2004→, VIX 1-min 2005→, VIX1D 1-min 2023-04-26→ (all IB, $0, audited via `data/qa_ib.py`); SPX/VIX daily; trader's 2026-06-01 full-chain recording (502 contracts) + SPX/VIX charts in `data/raw/`. Canonical access: `data/loader.py`.

---

## Phase S — Derived-Pricing Proof of Concept (the spike)

**Goal:** Prove, on one day of real data, that we can derive option pricing well enough to recover which strike corresponds to a given delta — before building anything else. This is FR-5.0 and the project's first deliverable.

- [x] **S.1** Ingest the spike inputs: one day of SPX 1-min bars and the trader's recorded 0DTE chain for that same day. Document the chain's format (snapshot times, fields available: bid/ask/mid, delta, IV). *(Done — 2026-06-01; format documented in `spike/memo.md`. Timestamps are US/Pacific.)*
- [x] **S.2** Establish the vol input for that day and document what was used. *(Done — the recording is VIX 1-min, not VIX1D; substitution documented and analyzed in the memo.)*
- [x] **S.3** Implement the raw BS strike solver K(delta, S, σ, T) for puts and calls (pulls task 1.1 forward); unit-test against textbook values. *(Done — `spike/derive_pricing.py`; verified against the platform's own recorded greeks.)*
- [x] **S.4** At each chain snapshot time: derive the 0.05/0.10/0.15/0.20/0.30Δ strikes per side from bars + vol, and extract the actual strikes at those deltas from the recorded chain. *(Done — 13 snapshots × 2 sides × 5 deltas × 3 σ variants → `spike/results.csv`.)*
- [x] **S.5** Compare: error in strike points and in strike increments, per side, per delta level, per time of day. Compute the implied skew multiplier m = actual distance ÷ BS distance at each point — is it stable enough within the day to look calibratable? *(Done — m_put ≈ 1.30 @0.10Δ, m_call ≈ 1.05, both stable; vol level identified as the dominant error source.)*
- [x] **S.6** Write the spike memo: error tables, whether raw BS alone or BS+single-m gets the 0.10Δ strike within one 5-pt increment, and what the result implies about FR-4.3 feasibility. *(Done — `spike/memo.md`. Finding: feasible with the right vol level (92% within one strike at 0.10Δ); requires adding a time-of-day vol multiplier curve f(t) to the calibration framework.)*

**Gate S: ✅ PASSED 2026-06-11 (trader decision).** Outcome: **restructure to the price-barrier model** — labels are price facts (no-touch primary, settle-beyond secondary); no option pricing in the pipeline; derived-pricing/delta-breach machinery shelved as documented v2. Vol convention: VIX1D anchor for normalization/features/baseline (f(t) correction only if FR-5.1 baseline validation demands it). Recording plan: already running (`eleuthera/events/`). Requirements rewritten accordingly (Sections 1, 3, FR-3/4/5, 6.2–6.3, 8).

---

## Phase 0 — Data Foundation

**Goal:** All raw inputs acquired, cleaned, versioned, and queryable.

- [x] **0.1** Acquire SPX 1-min OHLC, ≥4 years, RTH only (SPX is a calculated index — no volume exists). *(Done 2026-06-11 — IB TWS API via `data/ib_download.py`, $0. 2004-03→present, 5,615 days, audited by `data/qa_ib.py` (zero dups/OHLC violations; documented holes in `data/ib/README.md`). Cross-checked vs the trader's 2026-06-01 recording: 390/390 bars aligned, 381 exact.)* Verify: 390 bars/day, correct holiday/half-day handling, no duplicate or missing bars; document any vendor quirks (index print vs. futures-derived). Note: 1-min index data has more vendor-specific gaps/anomalies than 5-min — budget extra QA time here.
- [x] **0.2** Acquire VIX1D history (daily at minimum; intraday if available). Document exact availability start date — this sets the training-universe start. *(Done 2026-06-11 — VIX1D 1-min from IB, 2023-04-26→present, 784 days, fully clean → training universe starts 2023-04-26. Also pulled: VIX 1-min 2005→present, SPX/VIX daily for regime-percentile features. Audited by `data/qa_ib.py`.)*
- [x] **0.3** Build the economic calendar table: FOMC, CPI, NFP, monthly/quarterly OPEX, half-days, for the full span. *(Done 2026-06-11 — `load_calendar()` in `data/loader.py`: half-days from bar-count signatures, OPEX computed, FOMC/CPI/NFP from `data/raw/econ-events.csv` (102 events compiled from federalreserve.gov + bls.gov, incl. 2025-shutdown reschedules). Scope: event flags cover the v1 universe (2023-04→) only — extend before any pre-2023 ablation. NFP releases on Good Fridays deliberately unflagged.)*
- [x] **0.4** Inventory the trader's existing 0DTE chain snapshots: list days, times, VIX1D level on each. Identify regime gaps (target ≥3 VIX1D regimes × ≥2 times of day per FR-5.1). *(Done 2026-06-11 — `data/raw/README.md`: one day (2026-06-01, full session, calm tercile, VIX1D ≈ 10). Coverage matrix: calm ✅ both times of day; mid and elevated regimes empty → feeds task 0.5 recording priorities.)*
- [x] **0.5** If gaps exist, set up an ongoing recording habit/process so the trader captures chain snapshots on days in the missing regime cells (no purchases — coverage grows only through recording). Document which cells remain open at Gate 0. *(Resolved 2026-06-11 — already running: the trader's system (`../eleuthera`, `writer` module) has been recording full chains continuously; `eleuthera/events/` holds 303 days (2024-12-17→2026-06-05, 223 GB) and grows daily. Remaining regime-coverage audit moves to Phase 1 calibration prep.)*
- [x] **0.6** Digitize the trade log: entry time, side, strike, stop/exit time and reason, credit, for every past 0DTE trade available. *(Done 2026-06-11 — source is better than expected: 301 days of rules-based backtest replay over real recorded chains (`eleuthera/analyzer/logs`, JSON-lines, machine-readable entry/exit/reason/fills/P&L; 294 spreads). Parsed by `spike/false_breakout_cost.py`; exit-rule spec extracted from strategy code. Timestamps US/Pacific. Note: replay of the live rules, not broker fills — fills use recorded quotes, deterministic rules; treat as the canonical rule-behavior record.)*
- [x] **0.7** Stand up versioned storage (even just parquet + a manifest with hashes) and a single load API all later code uses. No notebook reads raw vendor files directly. *(Done 2026-06-11 — `data/loader.py`: `build` produces `data/processed/*.parquet` + sha256 manifest from the raw IB CSVs (sort/dedupe, RTH filter, closed-day junk drop, half-day truncation at 12:59 ET); `load_bars()/trading_days()` is the API, with manifest-hash verification on every load. Tests: `tests/test_loader.py`, 11 passing. Outstanding: `VIX1D-1day` not yet downloaded from IB.)*

**Gate 0:** A script reproduces every dataset from raw → clean with one command; bar-count and calendar audits pass; chain coverage matrix documented (empty cells flagged as risks, with a recording plan — they do not block the gate, since coverage can only grow over calendar time).

---

## Phase 1 — Grid, Baseline & Validation Framework *(rewritten at Gate S)*

**Goal:** The analytic framework around the price labels: strike-grid placement, the p_mkt no-touch baseline with documented bias, distance buckets, and the exit-rule characterization that links model output to overlay value.

- [x] **1.1** Implement the normalized-distance machinery behind the load API: D(K, S, σ, T), grid placement at the FR-4 anchors with 5-pt snapping, and the σ convention (VIX1D daily anchor; calendar-time √T). Unit tests. *(Done 2026-06-11 — `quant/distance.py` + `quant/conventions.py` (SigmaAnchor: prior_close default, day_open variant for 1.7). Math-verifier VERIFIED (round-trip 3.6e-13); its late-day finding fixed: snapped strikes at/across spot are dropped, not emitted as ITM rows.)*
- [x] **1.2** Implement analytic p_mkt: no-touch probability from (D, side, T) via the reflection-principle barrier formula, same σ convention. Unit-test against Monte Carlo GBM paths. *(Done 2026-06-11 — `quant/pmkt.py`. Math-verifier VERIFIED: independent derivation agrees to 1.4e-14 rel; 300k-path MC within error after Broadie–Glasserman–Kou discrete-monitoring correction; monotonicity and put<call drift asymmetry confirmed. Tests: `tests/test_quant.py`, 11 tests incl. seeded MC.)*
- [x] **1.3** Document the distance↔delta mapping empirically: on recorded-chain days (`eleuthera/events/`), regress recorded deltas against D by side/regime/time-of-day. Fix the bucket boundaries (incl. band of record) in D units; record the delta-equivalent labels. *(Done 2026-06-11 — `analysis/delta_mapping.py`, 62 stratified days × 4 snaps × both sides = 2,932 points; results + decisions in `docs/calibration.md`. Frozen: per-side `GRID_ANCHORS` (FR-4.1 amended — original draft anchors missed the 0.05–0.10Δ put region) and `BUCKET_EDGES_D` in `quant/conventions.py`. Regime/time drift (~10–18%) documented as bucket noise. Math-verifier spot-check on 3 days: VERIFIED, max diff 0.0065%; frozen edges = exact medians.)*
- [x] **1.4** Baseline validation (FR-5.1): compare analytic p_mkt against market-implied touch probability from recorded chains across ≥3 VIX1D regimes × ≥2 times of day. Decide: raw VIX1D anchor vs f(t)-corrected. Freeze the choice; document bias by regime/slice. *(Done 2026-06-11 — `analysis/baseline_validation.py`, 2,451 grid-strike points. Raw anchor rejected: +0.21/+0.14 one-sided bias in band of record. **Decision: f(t)-corrected baseline** — per-side 4-knot correction frozen in `F_T_KNOTS`, callable `quant.pmkt.p_mkt()`; held-out bias −0.026/−0.006 (p/c), MAE 0.063/0.077 on a **chronological + embargo split** (regenerated at trader review — the original interleaved-split figure of −0.005 violated rule #2 and was mildly optimistic; decision unaffected), regime-stable. Scope: f(t) lives in p_mkt only (NFR-3.4 amended); D/grid/buckets stay raw-anchor. Record: `docs/calibration.md`. Math-verifier: VERIFIED — knots = recomputed medians, p_mkt identity bit-exact, interleaved-split figures independently reproduced pre-correction.)*
- [x] **1.5** Grid coverage acceptance (FR-4.3): grid spans the chains' actual 0.05–0.30Δ strikes ≥ ~95% of observations on recorded days. If failing: widen anchors, re-test. *(Done 2026-06-11 — `analysis/grid_coverage.py`. Median-only anchors failed as expected; final 7-anchor sets (interior medians + bracketing anchors) **PASS: puts 98.7%, calls 97.9% (in-sample — anchors widened on the same 62 days; re-checkable free on each newly recorded day)**. FR-4.1 updated. Math-verifier: VERIFIED, coverage reproduced to 0.04pp.)*
- [x] **1.6** Exit-rule characterization (FR-5.2): from the eleuthera logs, for each early-exit reason: exit→touch frequency, exit→settle outcomes, timing distributions, by regime. (Extends `spike/false_breakout_cost.py` to a versioned analysis.) *(Done 2026-06-11 — `analysis/exit_characterization.py`, all 294 trades with D/p_mkt at entry and exit. Headline: entries at median D 2.13 / p_mkt 0.81 (band of record confirmed); `risk_off_reversal` fires on vol state with price far away (median p_mkt at exit 0.80–0.86); $138k of the exit cost sits in the p_mkt 0.70–0.85 band where 96% settled safe — but the ≥0.85 band hides the catastrophic tail, proving conditional discrimination (not a p_mkt threshold) is required. Tables in `docs/calibration.md`.)*
- [x] **1.7** Sensitivity memo: how grid placement, buckets, and p_mkt shift under σ-anchor variants (prior-close vs open VIX1D; ±f(t)). Labels are σ-free by construction — confirm nothing else is fragile. *(Done 2026-06-11 — `analysis/sensitivity.py`; memo in `docs/calibration.md`. Anchor mode is the one consequential convention (29% D shift if changed) — frozen with warning; f(t) fit noise small (Δp_mkt ~0.024); band-of-record bucket 92% within-one-bucket vs recorded deltas.)*

**Gate 1: READY FOR TRADER REVIEW (2026-06-11).** Evidence package: `docs/calibration.md` (tasks 1.3–1.7 records), `quant/` (frozen conventions, all math-verifier VERIFIED), `analysis/` (reproducible scripts + CSVs), 26 tests green. Review items: (1) f(t)-corrected baseline, held-out bias −0.026/−0.006, MAE 0.063/0.077 (chronological split); (2) 7-anchor grid, coverage 98.7%/97.9% in-sample; (3) exit characterization — especially the discrimination table; (4) sensitivity memo. **Trader review round 1 (2026-06-11) addressed:** split-discipline violation in 1.4 evidence fixed (chronological + embargo), 1.3/1.5 figures labeled in-sample, dead expression in sensitivity.py removed, trade-log path env-overridable (`OSAKA_TRADE_LOGS`) + settlement-proxy caveat documented, conventions.py import side effect removed, calendar-aware `time_to_settle_cal()` added for the half-day foot-gun, composite p_mkt-through-f(t) monotonicity test added (28 tests green). **Gate 1: ✅ PASSED — trader sign-off 2026-06-11 (after review round 1).**

---

## Phase 2 — Label Generation

**Goal:** The full training table's target columns, validated.

- [x] **2.1** Implement the per-bar label engine (FR-3): for every day × sample-stride bar × side × grid distance anchor, place the strike (1.1), scan forward **at 1-min resolution** against bar highs/lows, emit: no-touch label, touch time (if any), closest approach, settle-beyond secondary label. Sample stride configurable (default 5 min); touch detection always 1-min. No option math anywhere in this engine. *(Done 2026-06-11 — `data/labels.py::day_labels`, suffix-extrema + first-touch index scan; σ-free by construction (closest approach emitted in RAW points; implied-move normalization deferred to Phase 3 table assembly — documented FR-3.3 deviation in the module docstring). Scan interval (t, settlement] per spec: entry bar excluded, flagged for 2.4. 7 synthetic-bar tests.)*
- [x] **2.1b** Stride sensitivity check: regenerate labels for a sample month at 1-min stride and confirm conclusions in 2.3 are stride-invariant before committing the default. *(Done — 2025-03 at 1-min vs 5-min: max bucket-survival diff 0.017; stride-invariant. In `analysis/label_stats.py`.)*
- [x] **2.2** Run over full history (VIX1D universe, 2023-04-26→); persist as the labels table keyed by (date, stride bar, side, grid_anchor). *(Done — `labels-5min.parquet`: 847,385 rows, 783 days (2023-04-27→2026-06-10; 2023-04-26 skipped, no prior VIX1D close), own provenance manifest (`labels-manifest.json` with source-parquet hashes), hash-verified `load_labels()`.)*
- [x] **2.3** Sanity statistics: base no-touch rates per distance bucket vs. the analytic baseline; touch-time distributions; put/call asymmetry; regime breakdowns. Cross-check against the trade-log base rates. *(Done — `analysis/label_stats.py`, **statistics on pre-TEST_START data only** (regenerated after leakage-redteam finding F1; an earlier full-span run had summarized the locked tail). Band-of-record failure 20% puts / 26% calls (plausible zone); zero monotonicity violations; survival near-flat across regimes (0.76–0.78); median entry→touch 41 min. **Notable: realized survival exceeds p_mkt by +5 to +10 pts on the put side (calls ≈0 to −2 pts) — the put-skew insurance premium; unconditional baseline bias the Phase 4 baselines must absorb before any conditional edge claim.**)*
- [x] **2.4** Spot-validation (FR-5.3): for all 294 logged trades, compare pipeline labels at the actual entry bar/strike against the logged outcome. *(Done — `analysis/spot_validation.py`. **A: 128/128 expiration trades** — eleuthera's recorded settlement economics vs our SPX-bars settle label, two independent data paths, perfect agreement. **B: 294/294** touch-scan consistency. Zero disagreements to explain.)*
- [x] **2.5** Visual audit: plot ~15 randomly sampled days (price path, strikes, touch markers) and have the trader eyeball them. *(Plots produced — `analysis/visual_audit.py` → `analysis/plots/audit-*.png`, 15 days stratified 5/tercile, entries at 10:00/12:00/14:00 ET; regenerated pre-TEST_START only after redteam F1 (original run had plotted two locked-period days; deleted). **Trader eyeball pending at Gate 2.**)*

**Leakage-redteam review (2026-06-11): initial verdict BLOCK — remediated same day.** F1 (BLOCKER): 2.3/2.5 had summarized/plotted the chronologically-last span that Phase 4 will lock. Remediation: **test boundary declared — `TEST_START = 2025-12-01`** (~130 days, ≈17% of universe, grows with new data; can never move later; **pending trader ratification at Gate 2**); date guard installed in `load_bars`/`load_labels`/`SigmaAnchor` (default-truncate; greppable `_unlocked_full_span` escape hatch for sanctioned builders/FR-5.3 scripts only); 2.3/2.5 regenerated pre-boundary; locked-period plots deleted; regime terciles refit pre-boundary (10.8/14.6, centralized in `quant.conventions.regime`). F2: `load_labels` now verifies VIX1D + calendar provenance hashes and an engine-version field. F3 (docstring drift) and F4 (gapless-session asserts) fixed. Engine itself passed all attacks (label purity, point-in-time placement, scan window, no feature contact). Math-verifier on the engine: VERIFIED, zero discrepancies on 8,174 real rows incl. the 2025-04-09 crash day, a half day, and 11 entry-bar boundary cases. **Carve-out memo items for Phase 4 (redteam F6): document that Phase 1 chain calibrations (62 days, 2024-12→2026-06) and the FR-5.3 trade-log analyses overlap the locked window, plus this pre-lock analysis contact.**

**Trader review round 2 (2026-06-12) addressed:** (1) Phase-1 calibration scripts (`delta_mapping.py`, `baseline_validation.py`) explicitly marked as frozen pre-lock reproductions — `_unlocked_full_span=True` with F6-decision headers, so re-runs reproduce the frozen config exactly instead of silently shrinking the sample; (2) chains-not-gated documented in both the lock docstring and `data/chains.py`; (3) label build now skip-and-logs session-integrity failures per day instead of aborting the whole build; (4) `label_stats.add_pmkt` uses canonical `time_to_settle` (no reinlined convention; output verified identical); (5) 2.3 realized-vs-p_mkt table labeled as a seeded 120k subsample; (6) Phase 3 watch-item recorded on task 3.5: normalize `closest_pts`, strip raw level columns before the feature table, truncation harness asserts absence.

**Gate 2: ✅ PASSED — trader sign-off 2026-06-12** (after review round 2). Visual audit accepted; **TEST_START = 2025-12-01 ratified**.

---

## Phase 3 — Feature Engineering

**Goal:** The 20–30 feature columns, point-in-time-safe, each specified.

- [ ] **3.1** Write the feature spec sheet first (name, formula, lookback, normalization, point-in-time rule) covering the FR-1 list: strike encoding, clock/calendar, vol state, today's tape, prior-day context. Trader reviews the spec before code. *(**SIGNED v1.1, trader 2026-06-12** — 28 features (trader added `vix_term_ratio`, `dist_pdh`, `dist_pdl`; declined impulse/velocity → Phase 4 candidate). Hard constraints recorded: no volume/VWAP ever, no overnight SPX. pmkt-as-input conditional: without-pmkt variant first-class through 4.8; edge judged in band + slices, never pooled.)*
- [ ] **3.2** Implement features against the load API. One function per feature, config-registered.
- [ ] **3.3** Build the lookahead test harness (NFR-1.1): recompute every feature at bar t using data truncated at t; assert exact equality with the full-data computation, across many random (day, bar) pairs. This runs in CI on every change.
- [ ] **3.4** Feature QA: distributions, NaN handling at day-open edges (several features are undefined in the first bars — define and document the policy), correlation matrix to spot redundancies.
- [ ] **3.5** Assemble the master training table: features ⋈ labels, one row per (date, stride bar, side, grid_anchor). Persist versioned. **Watch-item (trader, Gate 2 review): the labels parquet deliberately carries raw `spot`/`strike`/`closest_pts` columns (FR-3.3 deviation for label purity). At assembly, `closest_pts` must be normalized to implied-move units from the stored entry-bar values, and the raw level columns must NOT pass into the feature table (rule #4). The truncation harness should assert their absence.**
- [ ] **3.6** Attach p_mkt (the Phase 1 analytic no-touch baseline, frozen in 1.4) per row. Store as a column — it is both a candidate feature input and the evaluation benchmark.

**Gate 3:** Lookahead harness green; spec sheet matches code; master table builds reproducibly end-to-end from raw data.

---

## Phase 4 — Modeling

**Goal:** A trained, calibrated, monotone GBT that beats nothing yet — just trains correctly.

- [ ] **4.1** Implement the split scheme (NFR-1.2): chronological splits by week with an embargo gap; final test period (most recent ~15–20% of days) carved out and **locked** — no code path reads it except the Phase 5 final run.
- [ ] **4.2** Baselines first: (a) constant base-rate predictor, (b) p_mkt alone, (c) logistic regression on 5 features. These define the floor and the bar.
- [ ] **4.3** Train v1 GBT (LightGBM/XGBoost) with the monotone constraint on normalized distance (survival non-decreasing in distance); side as indicator (compare against two-head variant in 4.6).
- [ ] **4.4** Hyperparameter search via the walk-forward validation folds only — small grid, heavy regularization priors given effective N ≈ days, not rows.
- [ ] **4.5** Post-hoc calibration (isotonic or Platt) fit on a validation fold never used for model selection.
- [ ] **4.6** Ablations on validation folds: side-indicator vs. two heads **+ threat-frame sign convention (run together — same underlying question, per trader at spec sign-off)**; with/without prior-day block; with/without p_mkt as input feature **(without-pmkt is a FIRST-CLASS variant carried through 4.8 — trader condition)**; recent-impulse/velocity candidate feature targeted at the near-strike-stress slice; feature-importance review and pruning of dead weight back toward the 20–30 budget.
- [ ] **4.7** Monotonicity verification: strike sweeps at sampled (day, bar) states; zero violations required.
- [ ] **4.8** Residual analysis on validation: where does p_model − p_mkt concentrate (time of day, regime, day-type ingredients)? Does it look like signal or noise? Written up before touching the test set. **Includes the without-pmkt variant as a first-class model (trader condition at spec sign-off); all edge claims judged in the band of record + NFR-2.1b slices, never pooled — pmkt-as-input can collapse the model onto the baseline and pooled Brier won't catch it.**

**Gate 4:** Model beats baselines (b) and (c) on validation Brier in the 0.10–0.15Δ band; calibration curves acceptable per-bucket; monotonicity clean; residual write-up reviewed.

---

## Phase 5 — Final Evaluation (one shot)

**Goal:** The honest answer, against the pre-registered criteria.

- [ ] **5.1** Freeze: model artifact, calibrator, feature code, grid anchors, bucket boundaries, p_mkt parameters — all hashed and recorded.
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
