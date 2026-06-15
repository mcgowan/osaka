# v2 Plan — Opening-Range Breakout Conviction Model

**Status:** scoping complete (2026-06-14); data acquisition in progress.
Companion pivot from v1 (the SPX 0DTE strike-survival model, closed NO-GO —
see `docs/negative_result.md`). v2 reuses v1's infrastructure and discipline;
the *question* is new.

## 1. What this is

A model that scores the **conviction of an opening-range (OR) breakout
EVENT**: given a breakout in progress, the probability it **reverses** (gives
back >= 1 opening-range width against you before EOD — a weak/violent-reversal
breakout you'd be stopped on; see §4). It scores **every breakout
event** (per side, potentially several a day) **independently of whether the
trader would enter** — entry needs the other eleuthera gates, which is the
trader's job downstream. The trader composes the conviction score into his own
entry, sizing, and management decisions. The product is a clean, single-purpose
**"is this break real or a dud" signal**.

The benchmark is the crude "5 closes outside the OR" rule — **a better
confirmation than the 5-bar rule, not a market forecast.**

**Value evidence (trade-log assessment, `analysis/exit_characterization.csv`,
294 trades / 262 days, net +$136.7k):** the clean, measured prize is
**false-start discrimination** — 18 days where a dud breakout was stopped for a
loss (−$28.8k "tuition") and a *later* breakout that day succeeded (10/18 ended
green anyway). A model that skips the dud and keeps the later winner recovers
~$28.8k over 18 months. Plus ~$19k of genuine-reversal tail-insurance.
This counts only *traded* breakouts (multi-trade days are 12% of the log
because the gates rarely allow two); the **full event space — including
breakouts never traded — is dollar-quantifiable from the recorded chains**
(`eleuthera/events/`, Dec 2024→) via real spread marks, which is the Phase-4
economic judge (§7/§8). Separately, the single biggest raw inefficiency
(over-exiting good breakouts, a ~$100k+ swing) is a *management* question the
trader owns, which the same conviction signal can feed but which v2 does not
itself solve.

## 2. Why v2 — the one lesson from v1 that matters

v1 failed because its benchmark was **the market's own estimate** (the option
price), which is efficient: you can't beat the implied no-touch probability
from price/vol data. v2's benchmark is **the trader's own crude 5-bar rule** —
a deliberately rough tool, not the market. **Beating a crude in-house rule is
achievable even in a perfectly efficient market**, because you're competing
with "count 5 bars," not with every fund on earth. The escape is the
benchmark, not a cleverer horizon or instrument. (Corollary: any framing that
quietly reduces to "beat the option-implied probability that price stays above
level L to EOD" is v1 again and is out of scope.)

## 3. Inviolable rules

Carried from v1 (unchanged): point-in-time discipline (every feature at bar t
uses only data ≤ t; daily features use completed prior days only); split
discipline (chronological, week/day-grained, embargoed; **effective N = trading
days**); deterministic, config-driven, hash-versioned data; human gates (trader
signs off each phase). New for v2:

- **Independence from the existing gates.** The model is trained on the **full
  population of OR breakouts** from raw price+volume — never filtered to taken
  trades, and never fed any eleuthera gate output (pressure/SPX-RSI, risk/VIX-
  RSI, the 5-bar count) as a feature. v2 explicitly **excludes the pressure and
  risk gates** (it aims to replace them).
- **Beat the in-house rule, not the market.** The 5-bar rule is the bar. Report
  vs the option-implied probability only as an honesty check (did we build a
  smarter filter, or re-derive the chain?).

## 4. Core definitions (frozen)

- **Instrument: SPY** for price, OR levels, volume, and VWAP (IB history to
  1993, deeper than SPX, and it has volume — which SPX lacks; volume is the
  whole point). VIX1D + VIX for the vol-regime features. The trade is SPX, but
  the model uses *relative* features (ATR/range/implied-move units), so SPY↔SPX
  transfers. ES futures = a later upgrade (overnight + true volume, at the cost
  of roll engineering); not v2.
- **Opening range:** high/low of the first 30 min, fixed thereafter.
- **Breakout event (per side):** starts at the first 1-min **close** outside the
  OR (after OR-finalized); stays alive while no bar closes back inside;
  terminates and **re-arms** on a close back inside. Multiple events per side
  per day allowed, with an **attempt-number** feature (a re-break after a failed
  one behaves differently).
- **Incumbent (the bar to beat):** 5 consecutive 1-min closes fully outside the
  OR, no buffer, close-based reset (aligned to the event definition; a minor
  simplification of the live intrabar reset).
- **Label (failure) — FROZEN: adverse-reversal, K=1.0** (trader-ratified
  2026-06-14, superseding the original no-reentry label). A breakout `reversed`
  iff its **max adverse give-back from the breakout close reaches >= 1.0 OR-widths
  before EOD** (up: lowest low; down: highest high) — a pure **price fact**, no
  sigma/greeks (v1 labels-are-price-facts discipline). This is the **target the
  model predicts**: weak / violently-reversing breakouts to *avoid entering*.
  - *Why this, not no-reentry:* the 63%-win-rate reconciliation showed no-reentry
    (hold flawlessly to EOD) is disconnected from the trader's P&L — most winning
    spreads re-enter the OR yet stay beyond the far short strike. The trader's
    real target is **reducing stop-losses from weak breakouts / violent
    reversals**.
  - *Calibration (`analysis/breakout_label_calib.py`):* against the 103 pre-locked
    logged trades, max_adverse_orw separates real winners (med ~0.30) from losers
    (~1.67); K=1.0 flags **66% of real losers vs 16% of winners**.
  - *Scope:* the OR-relative label maps to the `risk_off_reversal` exit (price
    punctures the OR — the −$96k pool, a breakout-conviction failure). The
    `sr_inner_breach` exit (near-strike S/R) is **strike-relative**, depends on
    strike selection, and is downstream — out of scope for the conviction model.
  - `held_to_eod` (old no-reentry) is retained as a secondary diagnostic/feature.
- **Decision surface:** the model emits a per-bar probability over a breakout
  event's life. Train on **all-day** events; **evaluate** on the tradeable
  window (OR-finalized → 15:00 ET cutoff = the trader's 12:00 PST cutoff).
  Entry/sizing/management is the trader's downstream composition, not the model's.

## 5. Features

Reuse v1's point-in-time, harness-tested blocks; add the volume and multi-day
pieces:
- **Breakout state:** extension beyond OR (in ATR/range units), bars since
  break, attempt number, OR width vs ATR, time of day.
- **Volume / participation (new, SPY):** relative volume vs same-time-of-day
  baseline, breakout-bar volume, volume trend, VWAP relationship. **(Phase-0 QA:
  SPY per-minute volume has a strong secular trend — 2008 median ≫ 2024 — so
  ALL volume features MUST use a trailing-relative baseline, never absolute or
  full-history-normalized; investigate whether the trend is smooth vs a vendor
  units step before Phase 2.)**
- **Today's tape (v1 carryover):** range-so-far, efficiency ratio, persistence,
  open-drive, gap (`gap_atr`) — the "gapped up BIG → toppy" signal.
- **Multi-day context (the trader's key requirement; mostly v1 carryover +
  new):** vol regime (VIX1D level/change/term), 3-day momentum, prior-day
  high/low proximity (`dist_pdh/pdl`), open-vs-yesterday's-range, **plus new**
  prior-week / N-day key levels.
- Hard exclusions: no eleuthera gate outputs; no option pricing; no lookahead
  (daily features as of yesterday's close, gap as open-vs-prior-close).

## 6. Discipline that makes the richer data honest

- **Day-clustered splits + stats, but genuine within-day signal.** Each
  training example is an (event, bar) pair; all bars of one event share its
  label, and same-day events share the regime — so splits go by day/week with
  embargo and all significance is day-clustered. BUT unlike v1 (where every
  sample on a day shared one forward path), same-day breakout events here have
  **different outcomes** (one push gets rejected, a later one holds), so events
  carry real information beyond the day count. Effective N is **more than days,
  less than event-rows** — richer than v1, still day-correlated.
- **Lookahead harness** on every feature (recompute on truncated data, assert
  equality) — carried from v1.

## 7. Success criteria / pre-registered kill criterion

Two bars, both required:
1. **Statistical — FROZEN, pre-registered 2026-06-14 (trader-ratified). No
   moving any value below after modeling starts.**
   - *Evaluation set:* the held-out **VALID** region of the day-clustered dev
     split (`breakout_features.split()`), tradeable window [10:00, 15:00) ET.
     The locked test (>= V2_TEST_START) is NOT touched here — it is the §7.2
     economic judge's, run once.
   - *Benchmark, measured on the SAME set:* the 5-bar rule's greenlit
     (confirmed) reversal rate **on VALID** — 39.5% as of 2026-06-14 (NOT the
     pooled 35.7%; VALID is a higher-reversal regime). The benchmark is always
     recomputed on the evaluation set, never assumed.
   - *Operating point:* threshold the model's reversal probability — chosen on
     **CALIB, never on VALID** — so it greenlights the same *fraction* of VALID
     breakouts as the 5-bar rule (its realized selectivity, ~46.7% on VALID).
   - *Margin (PASS):* model greenlit reversal rate **<= 0.83 x the 5-bar rule's
     greenlit rate on the same set** (>= 17% relative reduction; ~33% absolute
     at the current 39.5% bar) AND the **paired day-clustered (resample-days)
     95% bootstrap CI of the difference excludes 0** (the ~3.6pp day-clustered
     noise floor makes smaller margins vacuous).
   - *Stability:* the improvement holds (same sign, does not collapse) in **each
     calendar-year sub-period** of VALID and in the NFR-2.1b slices (early
     session, near-strike stress, post-breakout retest).
   - *Confound guard (HARD requirement, not report-only):* the improvement must
     **survive within start-hour strata** — value must come from **correct
     within-context disagreements** with the 5-bar rule, NOT from skimming the
     known base-rate covariates (start hour 10:00 reverses ~50% vs 14:00 ~24%;
     1st attempt ~47% vs 2nd+ ~39%). A pooled win fully explained by a shift in
     the start-hour/attempt mix is a FAIL.
   - *Secondary (reported, non-gating):* at the 5-bar rule's reversal rate the
     model greenlights *more* (higher recall) — confirms the whole
     selectivity/reversal trade-off curve dominates, not one cherry-picked point.
2. **Economic (chain era, Dec 2024→):** the model's conviction calls must **pay
   in real recorded-chain spread P&L** — high-conviction breakouts net positive,
   skip-the-dud-take-the-later-one beats the naive rule — on *every* breakout
   (incl. untraded), priced from `eleuthera/events/`. The behavioral signal
   (label) must translate to dollars (judge); if it can't, that's a kill even if
   bar 1 passes.

**Kill** (documented negative, stop) if it can't clear the §7.1 margin with
day-clustered significance, the edge is one-sub-period-only or vanishes within
start-hour strata, "ML alone" needs the gates it was meant to replace, or the
conviction signal doesn't pay in chain dollars (§7.2). Both bars frozen above;
no moving them after modeling starts.

## 8. Phases (gated; trader signs off each)

- **0 — Data:** SPY 1-min (from 2008, resumable) + daily (done, 1996→) from IB;
  loader extension for SPY+volume; QA (bar counts, volume sanity, half-days).
- **1 — Event + label + benchmark:** OR-breakout event-builder; success labels;
  day-clustered split; characterize the 5-bar rule (base rates, hit rate, by
  regime/time) — the bar to beat.
- **2 — Features:** the blocks in §5 + lookahead harness; feature QA.
- **3 — Model:** LightGBM, heavy regularization (effective N = days),
  walk-forward tuning within TRAIN; per-bar probability; post-hoc calibration on
  a held-out fold.
- **4 — Evaluation (two parts):**
  - *Statistical:* vs 5-bar rule (and trust-everything) on held-out, in the
    tradeable window; ablations (full-stack replacement; ML-alone vs ML+gates
    overlap; volume value; multi-day value; feature pruning); honesty check vs
    option-implied.
    - *Eval-build notes (from the §7.1 freeze — honor at implementation):*
      (a) the 5-bar benchmark is **recomputed on whatever rows are being scored**
      and the PASS bar is `0.83 ×` that live rate — never hard-code 0.395/0.33,
      so a VALID regime shift can't move the goalposts. (b) The confound guard
      needs **start-hour strata wired into the slice machinery** (extend the v1
      NFR-2.1b slices with start-hour; the per-bucket within-strata test is
      gating, not report-only) — without the strata the guard is just prose.
  - *Economic (recorded-chain backtest, Dec 2024→):* price the spread P&L of the
    model's conviction calls on **every breakout including untraded** from
    `eleuthera/events/` real marks (reuse the v1 chain-marking machinery,
    `data/chains.contract_quote_asof`); report high- vs low-conviction P&L, the
    skip-dud-take-later economics, vs the 5-bar rule and trust-everything. This
    is the dollar judge and the does-the-behavioral-signal-pay honesty check.
- **5 — Go/no-go** against §7 (both bars). Documented either way.

## 9. What carries over from v1 (the real asset)

The expensive, painful infrastructure and the discipline: the versioned load
API, week-grained embargoed splits, the eval harness (day-clustered bootstrap,
per-slice metrics), the lookahead harness, the calendar, VIX1D/VIX data, the
prior-day/regime/gap feature implementations, and — most of all — the
no-tuning, pre-register-the-kill-criterion culture that kept v1 honest. v2 is a
new question pointed at a beatable bar, on top of a proven, honest pipeline.

## 10. Open items to confirm before modeling

1. ~~Label: no-reentry vs settle-beyond~~ → ~~no-reentry~~ — **RESOLVED:
   adverse-reversal, K=1.0** (§4; no-reentry shown disconnected from P&L and
   replaced 2026-06-14).
2. ~~v2 locked-test boundary~~ — **RESOLVED: V2_TEST_START = 2025-07-01**
   (ratified 2026-06-14).
3. ~~The exact "beats the 5-bar rule" metric + margin for the kill criterion~~
   — **RESOLVED: pre-registered 2026-06-14 (trader-ratified), frozen in §7.1.**
   Matched-selectivity greenlit-reversal-rate vs the 5-bar rule on VALID, margin
   >= 17% relative (~33% abs at the 39.5% bar) with day-clustered significance, a
   HARD within-start-hour confound guard, and per-year stability. The 5-bar rule
   is a **weak reversal filter** (greenlit breakouts still reverse 39.5% on VALID
   vs ~44% unconfirmed) — that gap is the room to beat.
4. 1-min history depth (2008 default; extend toward 1993 only if warranted —
   regime drift caveat).
5. Whether to also validate against the full live stack later (needs the
   eleuthera trade log + reproducing pressure/risk as *approximate benchmarks*,
   not labels; not required for the core build).
