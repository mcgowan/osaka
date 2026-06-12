# Feature Spec Sheet (task 3.1)

**Status: SIGNED v1.1 — trader sign-off 2026-06-12** (v1 draft + trader
additions #26–28 and the resolutions recorded at the bottom).
This sheet is the contract: the spec doubles as the pipeline requirement
(NFR-3.2), the code must match it exactly, and drift is a bug. **28
features**, within the 20–30 budget (FR-1).

**Hard constraints on the record (trader, 2026-06-12):** no volume in any
form exists or may be approximated (no VWAP, no volume profile — SPX is a
calculated index); no overnight SPX (no true overnight high/low — RTH bars
only).

## Global conventions (apply to every feature)

- **Query time t** = a 1-min bar; the entry decision happens at the OPEN of
  bar t. **Information set: completed bars 0..t−1 of today, plus open(t),
  plus completed prior days, plus the day-constant anchor values.** The
  truncation harness (task 3.3) enforces exactly this.
- **σ_a** = prior-close VIX1D anchor (decimal, `SigmaAnchor`); **implied
  session move** M₀ = σ_a·√T₀·S_open where T₀ = full-session year fraction
  (390 or 210 min); **implied remaining move** M(t) = σ_a·√T(t)·S(t).
  Same convention as the grid/p_mkt (rule #5).
- **ATR₁₄** = 14-day simple average of daily true range, completed days
  only (as of yesterday's close).
- **Realized vol** RV(a→b) = stdev of 1-min log close returns over
  completed bars, annualized with the calendar-time convention
  (×√(YEAR_SECONDS/60s)).
- **No raw levels** (rule #4): every price-derived feature is a ratio,
  position, or normalized distance. The labels parquet's raw
  `spot`/`strike`/`closest_pts` columns never enter the feature table;
  `closest_pts` is normalized at assembly (task 3.5 watch-item; the
  auxiliary target, not a feature).
- **NaN policy** (day-open edges): features undefined early in the session
  are left as NaN — LightGBM handles missing natively; no zero-fills, no
  forward-fills across the undefined region. Per-feature minimums below.
- **Directional sign convention (RESOLVED):** market frame (up = positive)
  everywhere; side is a first-class split feature; the monotone constraint
  applies to D only. The Phase 1–2 asymmetries (side-specific anchors,
  f(t), bucket edges, the +5–10 pt put-side premium) are real and the
  model sees them rather than assuming them away. Threat-frame is a
  Phase 4 ablation, to be run TOGETHER with the two-head variant (4.6) —
  both test whether the single model learns the side interaction.

## Block 1 — Strike encoding (3)

| # | name | formula | lookback | normalization | PIT rule |
|---|---|---|---|---|---|
| 1 | `D` | (S−K)/(σ_a·√T·S), sign OTM-positive (puts), mirrored (calls) | none | implied-move units | S = open(t); σ_a day-constant (prior close); monotone-constrained input |
| 2 | `side` | put=0 / call=1 indicator | none | categorical | static per row |
| 3 | `minutes_to_settle` | session minutes remaining at t | none | minutes (390-scale) | from t + half-day calendar |

## Block 2 — Clock & calendar (6)

| # | name | formula | lookback | normalization | PIT rule |
|---|---|---|---|---|---|
| 4 | `minutes_since_open` | t − 09:30 in minutes | none | minutes | exact |
| 5 | `day_of_week` | 0–4 | none | categorical | static |
| 6 | `is_fomc` | calendar flag (decision day) | none | binary | known in advance |
| 7 | `is_cpi_nfp` | calendar flag (CPI OR NFP release day) | none | binary | known in advance; combined to save budget — both are 08:30 ET pre-open prints |
| 8 | `is_opex` | monthly OPEX flag (quarterly implied by month) | none | binary | known in advance |
| 9 | `is_half_day` | calendar flag | none | binary | known in advance |

## Block 3 — Volatility state (4)

| # | name | formula | lookback | normalization | PIT rule | NaN |
|---|---|---|---|---|---|---|
| 10 | `vix1d_anchor` | prior-close VIX1D (vol points) | 1 day | already a vol quote | completed day | never |
| 11 | `vix1d_chg` | ln(anchor_today / anchor_yesterday) | 2 days | log ratio | completed days | never |
| 12 | `rv_ratio_today` | RV(open→t) ÷ σ_a | today, ≥15 completed bars | ratio | bars 0..t−1 only | NaN before minute 15 |
| 13 | `rv5d_ratio` | RV over prior 5 completed sessions ÷ σ_a | 5 days | ratio | completed days | never |

| 26 | `vix_term_ratio` | prior-close VIX1D ÷ prior-close VIX | 1 day | ratio (≈1; >1 = near-term inversion) | both completed prior day | never (VIX present from 2005) |

*(#26 added at trader review: gives Block 3 the term-structure slope it was
missing — otherwise all level-and-realized — at near-zero cost.)*

*(FR-1.3's "subsampled RV variant" decision deferred to feature QA, task
3.4, as the requirement allows.)*

## Block 4 — Today's tape (7)

| # | name | formula | lookback | normalization | PIT rule | NaN |
|---|---|---|---|---|---|---|
| 14 | `range_ratio` | (high₀..ₜ − low₀..ₜ) ÷ M₀ | today | implied-move ratio | extrema over bars 0..t−1 + open(t) | NaN at minute 0 |
| 15 | `range_pos` | (open(t) − low) ÷ (high − low) | today | [0,1] | same | NaN until range ≥ 0.05·M₀ |
| 16 | `gap_atr` | (open₀ − close_yesterday) ÷ ATR₁₄ | 1 day | ATR units | both completed/known at open | never |
| 17 | `gap_filled` | 1 if path 0..t has touched yesterday's close | today | binary | bars 0..t−1 + open(t) | never |
| 18 | `persist_count` | signed count of consecutive same-direction closes on the 5-min rollup, ending at the last completed 5-min bar | today | count (±) | completed 5-min bars only | NaN before minute 10 |
| 19 | `efficiency_ratio` | (close_last − open₀) ÷ Σ|5-min moves| on the rollup | today | [−1,1] | completed 5-min bars | NaN before minute 15 |
| 20 | `open_drive` | (open(t) − open₀) ÷ M₀ | today | implied-move units | open(t) vs session open | never |

## Block 5 — Prior-day context (5)

| # | name | formula | lookback | normalization | PIT rule | NaN |
|---|---|---|---|---|---|---|
| 21 | `yest_close_pos` | (close_y − low_y) ÷ (high_y − low_y) | 1 day | [0,1] | completed day | never |
| 22 | `open_vs_yest_range` | (open₀ − mid_y) ÷ ATR₁₄, where mid_y = (high_y+low_y)/2; encodes inside/above/below continuously | 1 day | ATR units | completed day + today's open | never |
| 23 | `mom3d_atr` | (close_y − close_{y−3}) ÷ ATR₁₄ | 4 days | ATR units | completed days | never |
| 24 | `yest_range_vs_implied` | (high_y − low_y) ÷ M₀(yesterday) | 1 day | ratio | completed day | never |
| 25 | `pmkt` | the frozen baseline `quant.pmkt.p_mkt(...)` at (K, S, σ_a, T, t) | none | probability | same inputs as #1/#3 | never |

| 27 | `dist_pdh` | (high_y − S(t)) ÷ M(t) | 1 day | implied-move units, market-framed (+ = level above spot) | high_y completed day; S = open(t); M(t) = σ_a·√T(t)·S(t), same denominator as D (rule #5) | never |
| 28 | `dist_pdl` | (low_y − S(t)) ÷ M(t) | 1 day | implied-move units, market-framed (− = level below spot) | low_y completed day; S = open(t); M(t) as above | never |

*(#27/#28 added at trader review: prior-day high/low proximity in the same
implied-move coordinate as D, so the model can see whether a prior-day
reference sits between spot and the queried strike — the magnet/barrier
effect that drives intraday touches and that pooled distance cannot
express. Market-framed so the model combines them with side and D.)*

*(#25 per task 3.6: p_mkt stored as a column — candidate feature AND the
evaluation benchmark. CONDITIONAL sign-off, trader 2026-06-12: the
without-pmkt model is a FIRST-CLASS variant carried through residual
analysis (4.8), not a one-line ablation; the edge claim is judged in the
band of record + NFR-2.1b slices, never pooled — with pmkt as input the
model can collapse toward the baseline, trivially clear "beat baseline
(b)", and still carry zero independent edge. The job is to correct the
measured put-side bias and prove the correction is real where it matters.)*

## Explicitly excluded from v1 (for the record)

- Opening-range features (decision #4 + independence: the model must remain
  an uncorrelated second opinion to the trader's OR-breakout system).
- Eleuthera signals (RSI heat/pressure) — Phase 4 ablation candidates only.
- Recorded chain deltas — ablation candidate only (missing for ~40% of the
  universe; independence).
- Volume in any form (calculated index; no volume exists).
- Any full-history percentile rank (lookahead; trailing windows only — none
  needed in v1).

## Resolutions (trader sign-off, 2026-06-12)

1. **Sign convention:** market frame for v1; threat-frame deferred to
   Phase 4, run together with the two-head variant (same question). Side
   stays a first-class split feature; monotone constraint on D only.
2. **`is_cpi_nfp`:** combined (≈30 occurrences each pre-boundary is too
   thin to learn distinct signatures; both are 08:30 ET pre-open prints
   whose effect already lives in the gap/realized-vol features; reversible
   as an ablation). FOMC stays separate (14:00 ET, inside the operating
   window).
3. **Additions:** #26 `vix_term_ratio`, #27 `dist_pdh`, #28 `dist_pdl`
   (rows above). **Declined for budget:** a recent-impulse/velocity
   feature — logged as a Phase 4 candidate specifically for the
   near-strike-stress (use-case-b) discrimination question.
4. **`pmkt` as input:** accepted with the condition recorded at #25.
