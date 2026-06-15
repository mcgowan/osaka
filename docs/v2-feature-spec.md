# v2 Feature Spec — OR-Breakout Conviction Model

**Spec version 2.0.** This sheet is the contract: `data/breakout_features.FEATURES`
must match it exactly (drift is a bug — `build()` asserts the column set, and the
lookahead harness in `tests/test_breakout_features.py` pins point-in-time).

## Conventions

- **Unit of analysis:** one row per OR-breakout *event* (`data/breakouts.py`),
  scored at its **decision bar** = the close of the breakout bar (first 1-min
  close outside the OR). Information set: completed bars `0..start_idx` of today
  (incl. the breakout bar) + completed prior days + day-constant context.
- **Normalization:** price distances by **ATR** (prior-completed 14-day, a price
  fact available for all SPY history from 2008) or by **OR width** / ratios.
  *Not* the VIX1D implied move — VIX1D only exists 2023-04-26+, and normalizing
  by it would discard the deep history that motivated SPY. VIX1D/VIX regime
  features are recent-era extras (NaN before ~2023), never the core signal.
- **Targets** (not features): `reversed` (max_adverse_orw ≥ 1.0, the label),
  `held_to_eod` (secondary), `max_adverse_orw` (continuous).
- **Hard exclusions:** no eleuthera gate outputs (pressure/RSI/risk/5-bar count),
  no option pricing/greeks, no raw price levels, no lookahead.

## Block 1 — breakout state

| feature | formula | normalization | PIT |
|---|---|---|---|
| `ext_atr` | (close − OR boundary), signed per side | ÷ ATR | breakout bar |
| `ext_orw` | same extension | ÷ OR width | breakout bar |
| `or_width_atr` | OR high − OR low | ÷ ATR | OR (first 30 min) |
| `attempt_n` | event attempt #, capped at 4 | — | event |
| `minutes_since_open` | breakout start, minutes from 09:30 | — | breakout bar |
| `side_down` | 1 if down-breakout else 0 | — | event |

## Block 2 — volume / participation (SPY)

| feature | formula | normalization | PIT / lookback |
|---|---|---|---|
| `rel_vol_tod` | breakout-bar volume ÷ trailing same-minute median | ratio | 20 prior days |
| `vol_surge` | breakout-bar volume ÷ today's mean bar volume so far | ratio | bars [:si+1] |
| `vwap_dist_atr` | (close − session VWAP, typical-price) | ÷ ATR | bars [:si+1] |
| `vol_trend` | last-5-bar mean volume ÷ prior-session mean | ratio | bars [:si+1] |

## Block 3 — today's tape (ATR-normalized)

| feature | formula | normalization | PIT / lookback |
|---|---|---|---|
| `range_atr` | session high−low so far | ÷ ATR | bars [:si+1] |
| `range_pos` | (close − low) ÷ (high − low) so far | ratio | bars [:si+1] |
| `open_drive` | close − day open | ÷ ATR | bars [:si+1] |
| `efficiency_ratio` | net move ÷ summed |Δclose| | ratio | bars [:si+1] |
| `persist_count` | signed run length of last same-sign Δclose | — | bars [:si+1] |
| `rv_ratio` | today's realized vol so far ÷ trailing RV baseline | ratio | 20 prior days |
| `gap_filled` | 1 if path retraced to yesterday's close | — | bars [:si+1] |

## Block 4 — multi-day / regime / key levels

| feature | formula | normalization | PIT / lookback |
|---|---|---|---|
| `gap_atr` | day open − prior close | ÷ ATR | prior day |
| `dist_pdh` | prior-day high − breakout close | ÷ ATR | prior day |
| `dist_pdl` | breakout close − prior-day low | ÷ ATR | prior day |
| `dist_hi20` | prior 20-day high − breakout close | ÷ ATR | 20 prior days |
| `dist_lo20` | breakout close − prior 20-day low | ÷ ATR | 20 prior days |
| `mom3d_atr` | prior close − close 3 sessions back | ÷ ATR | 4 prior days |
| `yest_close_pos` | (prior close − prior low) ÷ prior range | ratio | prior day |
| `vix1d_anchor` | prior VIX1D close | — (level) | prior day; NaN < 2023 |
| `vix1d_chg` | log(VIX1D_{t-1} / VIX1D_{t-2}) | — | 2 prior days; NaN < 2023 |

## Lookahead coverage

- Prefix-only (intraday): `volume_vwap`, `tape_features` — recomputed on bar
  arrays truncated at the decision bar, asserted bit-equal.
- Day-level (rolling/pivot): `_atr14_by_day`, `_tod_volume_baseline`,
  `_multiday_context` (N-day levels), `_realized_vol_baseline` — recomputed on
  day-truncated history, asserted bit-equal (the `shift(1)` off-by-one / pivot
  re-median failure modes).

## Open / gate items

- Signed sign-off by the trader (rule #6) and an independent leakage-redteam pass
  (full feature set) before Phase-3 modeling.
- Per-bar monitoring decision surface (scoring along an event's life) is a
  Phase-3 extension; v2.0 scores once, at the breakout bar.
