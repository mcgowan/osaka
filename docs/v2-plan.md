# v2 Plan — Opening-Range Breakout Conviction Model

**Status:** scoping complete (2026-06-14); data acquisition in progress.
Companion pivot from v1 (the SPX 0DTE strike-survival model, closed NO-GO —
see `docs/negative_result.md`). v2 reuses v1's infrastructure and discipline;
the *question* is new.

## 1. What this is

A model that scores the **conviction of an opening-range (OR) breakout
EVENT**: given a breakout in progress, the probability it **holds** (never
closes back inside the OR) through to settlement. It scores **every breakout
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
- **Label (success) — FROZEN: no-reentry.** A breakout event succeeds iff it
  **never closes back inside the OR before EOD**; the first close back inside =
  failure, period. This is self-consistent with the event/re-arm structure (the
  episode *is* the breakout run) and matches eleuthera's own gate logic. It is
  deliberately **not** the trader's P&L (a break that recedes, recovers, and
  settles beyond the short strike is a "failure" here but a winning trade) —
  the trader accepts this and handles entry/management separately; the model is
  one clean input, not a win-probability. (`settle-beyond` was considered and
  rejected: it doesn't compose with re-arm, and it collapses to a daily
  directional call near v1's efficient-pricing wall.) Expect a **low base
  rate** — holding flawlessly to EOD with zero re-entry is stringent.
- **Decision surface:** the model emits a per-bar probability over a breakout
  event's life. Train on **all-day** events; **evaluate** on the tradeable
  window (OR-finalized → 12:00 cutoff). Entry/sizing/management is the trader's
  downstream composition, not the model's.

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
1. **Statistical (full history):** on held-out (day-clustered) data in the
   tradeable window, beat the 5-bar rule by a pre-specified margin at separating
   hold-to-EOD breakouts, stable across sub-periods. Value must come from
   **correct disagreements** with the 5-bar rule (confirming good breakouts it
   misses; declining ones that survive 5 bars then fail).
2. **Economic (chain era, Dec 2024→):** the model's conviction calls must **pay
   in real recorded-chain spread P&L** — high-conviction breakouts net positive,
   skip-the-dud-take-the-later-one beats the naive rule — on *every* breakout
   (incl. untraded), priced from `eleuthera/events/`. The behavioral signal
   (label) must translate to dollars (judge); if it can't, that's a kill even if
   bar 1 passes.

**Kill** (documented negative, stop) if it can't clear the 5-bar rule by the
margin, the edge is one-sub-period-only, "ML alone" needs the gates it was meant
to replace, or the conviction signal doesn't pay in chain dollars. *Open item:*
fix the exact metric + margin with the trader (depends on his economics) before
modeling — pre-registered, no moving it after.

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

1. ~~Label: no-reentry vs settle-beyond~~ — **RESOLVED: no-reentry** (§4).
2. The exact "beats the 5-bar rule" metric + margin for the kill criterion
   (depends on the trader's economics; pre-register before modeling).
3. 1-min history depth (2008 default; extend toward 1993 only if warranted —
   regime drift caveat).
4. Whether to also validate against the full live stack later (needs the
   eleuthera trade log; not required for the core build).
