# Phase S Memo — Derived-Pricing Proof of Concept

**Date:** 2026-06-09
**Inputs:** one trading day (2026-06-01) of SPX 1-min bars, VIX 1-min bars, and a full
recording of every 0DTE chain update (502 contract files, strikes 3000–9800).
**Code:** `spike/derive_pricing.py` (stdlib-only). **Raw output:** `spike/results.csv`.

## Question

Can we derive 0DTE option pricing — specifically, which strike corresponds to a given
delta — from SPX 1-min bars plus a vol input, without purchased options data? (FR-5.0;
feasibility precursor to FR-4.3.)

## Data findings

- **Chain format:** `ts, bid, ask, last, delta, gamma, vega, theta, IV, mark, ?` per row,
  one row per quote change. `1.7976931348623157e+308` (DBL_MAX) = missing; bid/last `-1` =
  no quote. Column identification verified by independently recomputing vega (7600c @
  10:00 PT: computed 0.56 vs recorded 0.555) and by the put-skew shape of the IV column.
- **All timestamps are US/Pacific.** RTH = 06:30–13:00 PT. The platform's greeks use a
  calendar-time T convention (T = seconds-to-settlement / year-seconds) — ours matches.
- **The vol file is VIX, not VIX1D.** This matters; see findings.
- Near-money strikes are 5-pt spaced; chain quality near the money is excellent (deltas
  monotone across strikes, sub-second updates). Deep-OTM quotes are sparse/stale, as expected.
- SPX chart includes 3 prior days (May 27–29) — useful later for prior-day features.

## Method

At 13 snapshot times (06:35, then every 30 min to 12:30 PT), per side, per target delta
{0.05, 0.10, 0.15, 0.20, 0.30}:

- **Actual strike** = interpolated from the chain's recorded per-strike deltas (the
  platform's delta is the operational truth — it is what the trader's stops key off).
- **Derived strike** = closed-form BS inversion K(Δ, S, σ, T), S = bar open at t, three σ
  variants: raw `VIX`; `vixadj` = VIX × single day-level constant (median ATM-IV/VIX =
  0.783); `atmiv` = chain ATM IV (oracle — isolates skew error from vol-level error).
- **Implied skew multiplier** m = actual OTM distance ÷ derived OTM distance per FR-4.2,
  then residual error re-checked after applying one median m per (side, delta) —
  in-sample on this day, so an upper bound on achievable quality, not an FR-4.3 pass.

## Results

Within-one-strike (5 pts) rates at the 0.10Δ level, after single-m correction:

| σ variant | puts 0.10Δ | calls 0.10Δ | mean abs err (puts) |
|---|---|---|---|
| VIX raw | 62% | 85% | 6.3 pts |
| VIX × day constant | 62% | 85% | 6.3 pts |
| ATM IV (oracle) | **92%** | 77%* | 1.9 pts |

\* dragged by one noisy snapshot (10:30, ATM-IV estimate jumped 0.111→0.121); a smoothed
ATM-vol fit (median across several near strikes) would likely fix this — the other call
deltas hit 85–100%.

Skew multipliers are well-behaved and economically sensible: m_put ≈ 1.30 at 0.10Δ
(put strikes sit ~30% farther out than flat-vol BS implies), declining smoothly to ~1.08
at 0.30Δ; m_call ≈ 1.05 and nearly flat. Intraday stability is decent (puts 0.10Δ:
1.17–1.36 across snapshots, one 0.78 outlier at the noisy 10:30 snapshot).

## The key finding: skew is easy, vol level is the problem

With the right vol *level*, skew-adjusted BS clears the FR-4.3 bar on this day (92%/85%+
within one strike at the band of record). But the available historical vol input — VIX —
does not track 0DTE vol:

- 0DTE ATM IV decayed **0.193 → 0.104** through the session while VIX moved 16.2 → 15.9.
- The ATM-IV/VIX ratio followed a classic intraday seasonality curve: ~1.19 at the open,
  trough ~0.68 midday, ~0.81 into the close. A single day-level scalar cannot capture
  this, which is why `vixadj` is no better than raw VIX at the put wing.

**Implication for the framework:** the calibration target from recorded chains is not just
per-side skew multipliers (m_put, m_call) but also a **time-of-day vol multiplier curve**
f(t) such that σ(t) ≈ f(t) · VIX(t). On this day f(t) is smooth and would have closed most
of the gap between the `vix` and `atmiv` rows above. Whether f(t) is stable across days and
regimes is exactly what additional recorded days will tell us — it becomes part of the
Phase 1 m-stability analysis (task 1.3).

## Recommendations (Gate S decision items)

1. **Viability: yes, with a framework amendment.** Derived pricing reaches FR-4.3-quality
   accuracy when the vol level is right. Amend FR-4/FR-5: calibrate a time-of-day vol
   curve f(t) alongside m_put/m_call from recorded chains.
2. **Vol input:** the docs mandate VIX1D and the recording is VIX. If the platform can
   record VIX1D 1-min, start now — VIX1D embeds much of the 1-day term structure and
   should shrink f(t)'s job. Either way f(t) calibration is needed; the "never VIX"
   convention should be amended to "VIX (or VIX1D when available) × calibrated
   time-of-day curve." Trader to decide.
3. **Keep recording full chain days.** One day calibrates nothing out-of-sample. Every
   recorded day (especially high-VIX days) extends the f(t)/m stability analysis. Also
   record the same day's VIX1D if possible.
4. **Method improvements for Phase 1** (known, minor): smoothed ATM-vol estimate instead
   of nearest-strike average; sub-minute spot (bar open was used); investigate the 06:35
   open snapshot separately (auction noise).

## Addendum (2026-06-11): delta-breach vs touch vs settlement, measured

Gate S decision input for the proposed price-barrier restructure
(`spike/stop_vs_touch.py`, per-entry detail in `spike/stop_vs_touch.csv`).
For 72 hypothetical entries on the recorded day (every 30 min × both sides ×
{0.10, 0.15, 0.20}Δ chain strikes), using the chain's own recorded deltas:

| Failure definition | Fired |
|---|---|
| A. delta reached 0.30 (δ\*-style stop) | 38/72 (53%) |
| B. price touched the strike | 29/72 (40%) |
| C. settled through the strike | 11/72 (15%) |

- **9 of the 38 delta-breaches (24%) never touched the strike.** Under
  price-barrier labels those are survivals; under a delta/credit stop they
  are realized losses.
- Where both fired, the delta stop preceded touch by ~30–120 minutes.
- Context: a steady trend-up day (calls crushed, puts safe until a late
  fade), VIX1D ≈ 10 (15th percentile — calm tercile). Entries share the same
  underlying path, so effective N ≈ 1 day; treat magnitudes as illustrative,
  the *structure* as confirmed.

**Implication:** the A↔B gap is material, not theoretical. The decisive
question for Gate S is where the trader's real exits sit between A and B —
which is exactly what the trade-log digitization (task 0.6) will measure.
If real exits cluster near touch, the price-barrier label is the trade's
truth; if near the delta stop, the price-barrier model over-promises
survival on trend days specifically.

## Addendum 2 (2026-06-11): the trade log changes everything

Two discoveries from the trader's system repo (`../eleuthera`):

**1. A 303-day full-chain archive exists.** `eleuthera/events/` holds the
trader's own recordings — every chain update (quotes + IB greeks), per
strike, 2024-12-17 → 2026-06-05, 223 GB, same format as the single day in
`data/raw/`, produced continuously by the system's `writer` module. FR-5's
"thin chain coverage" risk is largely void: skew/vol-curve calibration has
hundreds of days with true held-out testing, and ~60% of the VIX1D-era
training universe has *recorded* deltas — for those days, delta-breach
labels need no BS reconstruction at all. Coverage keeps growing daily.

**2. The false-breakout cost is measured, and it is the project's economic
case** (`spike/false_breakout_cost.py`, 294 backtested trades, 301 days,
real recorded fills): the system's two early-exit rules
(`risk_off_reversal`, `sr_inner_breach`) cost **$77,625** vs holding to
settlement — against total strategy P&L of $136,725. 153/166 early exits
would have been better held (the whipsaw tax: only 25–31% ever touched the
short strike after exit); the other 13 were true breaks, including
single-trade catastrophes of −$57.6k and −$31.1k that the exits correctly
insured against. The model's job, stated precisely: at the moment an exit
rule fires, discriminate the 153 from the 13 better than the market does.
A perfect discriminator is worth ≈ +57% of strategy P&L; the kill criterion
asks whether any of that is capturable beyond what delta already knows.

Trade base rates for label sanity-checks (task 2.3): 294 spreads, 63% win,
sides 167 bull / 127 bear; exits: 130 risk_off_reversal (41% win),
128 expiration (100% win), 36 sr_inner_breach (14% win).

## Gate S decision (2026-06-11)

**Passed — trader approved the price-barrier restructure.** Labels are price
facts (no-touch primary, settle-beyond secondary); option pricing removed
from the v1 pipeline; the delta-breach/δ\* design shelved as a documented v2
option. Requirements, plan, CLAUDE.md, and README rewritten accordingly.

## Caveats

- Single day, calm regime (VIX ~16), m fit in-sample. This is a feasibility result, not
  an acceptance test — FR-4.3 still requires held-out chain days (task 1.5).
- The platform's greeks (not exchange-disseminated values) define "actual." This is
  deliberate: the trader's stop behavior keys off these numbers.
- The 0.05Δ wing is materially harder everywhere (sparse quotes, steep skew); if the
  trading range stays 0.10–0.30Δ this does not block anything.
