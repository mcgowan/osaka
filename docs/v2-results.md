# v2 Results & Status — OR-Breakout Conviction Model

**As of 2026-06-15.** Companion to `docs/v2-plan.md` (the plan), `docs/v2-feature-spec.md`
(the feature contract), and `docs/v2-model-serving.md` (the eleuthera/JS integration).

## TL;DR

The model predicts, per OR breakout, the probability it **reverses** (gives back
≥1 OR-width before EOD). The signal is **real** — it beats the 5-bar rule at
flagging reversals, and on the cleaner SPX index it's materially better than on
SPY. But against the trader's actual economics it splits sharply:

- **As an ENTRY gate: documented NO-GO** (one-shot economic gate, SPY model).
  The 10-delta short strike sits ~2 OR-widths beyond entry, so ~88% of breakouts
  win regardless — most "reversals" are shakeouts the far strike absorbs. Using
  the model to gate entries **destroys P&L** (it skips winners). The reversal
  signal is economically inert at entry.
- **As an EXIT override: promising** (exploratory, second look). The trader's
  premature `risk_off_reversal` stops fire on OR-retreats during VIX-risk spikes,
  booking losses on shakeouts that would have settled fine. The model's entry
  conviction **separates shakeouts from genuine reversals**, recovering ~$96k of
  the over-exit drag in the chain era.
- **SPX > SPY:** training on the SPX index (not the SPY ETF) is a genuine
  improvement, not a cosmetic swap — see §4. It reopens the entry question, but
  only a **forward** test can settle it (the chain era is already examined).

## 1. The model

- **Target:** `reversed` = breakout's max adverse give-back ≥ 1.0 OR-width before
  EOD (K=1.0, calibrated to the trader's stop economics; `docs/v2-plan.md` §4).
- **Instrument: SPX** (the traded instrument; osaka's SPX intraday → 2004,
  deeper than SPY). Originally built on SPY for volume + history, but volume
  proved useless and SPX is the cleaner signal (§4).
- **Features:** 20 SPX-price features (breakout-state, tape, multi-day levels) —
  no volume, no VIX1D (both dropped at ~0 cost; `docs/v2-feature-spec.md`).
- **Model:** LightGBM, heavily regularized (effective N = trading days):
  num_leaves 7, min_data_in_leaf 1600, frozen by walk-forward OOF logloss search.
  Isotonic-calibrated (near-perfect reliability on held-out CALIB).
- **Production:** 7 expanding-window quarterly versions (sep-2024 … mar-2026),
  each a self-contained JSON for pure-JS serving (`models/walk_forward_train.py`,
  `docs/v2-model-serving.md`).

## 2. Statistical evaluation (development; TRAIN walk-forward OOF)

The model beats the 5-bar rule at flagging reversals — but the edge is
session-dependent, and the confound guard (must beat the rule *within* start-hour
strata, not by skimming the time-of-day base rate) is the real test.

| model | AUC | pooled ratio* | morning (10-11) | afternoon (12-14) |
|---|---|---|---|---|
| SPY, 20-feat | 0.663 | 0.77 | 0.96 (≈ 5-bar) | 0.73 |
| **SPX, 20-feat** | **0.708** | **0.66** | **0.79** | **0.64** |

\* ratio = model greenlit-reversal rate ÷ 5-bar greenlit-reversal rate at matched
selectivity; lower is better; the §7.1 PASS bar is ≤ 0.83.

The SPY model **failed** the within-stratum confound guard in the morning (0.96).
The SPX model **passes both sessions** (0.79 / 0.64). Both are dev (TRAIN-OOF),
no held-out spent — see §4 for why SPX is better and §5 for what it does/doesn't
license.

## 3. Economic evaluation (chain era, real logged P&L)

### Entry gate — ONE-SHOT, pre-registered, FAIL (SPY model)

Model as an extra gate on the 294 actual chain-era trades (skip if P(reversed) >
0.372):

| | n | total P&L | risk_off |
|---|---|---|---|
| ACTUAL (all) | 294 | +$136,725 | 130 / −$50,850 |
| model KEEP | 100 | +$50,812 | 42 / −$16,875 |
| model SKIP | 194 | **+$85,912** | 88 / −$33,975 |

The skipped trades were **net-positive** — the model skips winners. P&L is
monotone in trades kept; every operating point loses vs keep-all. **Why:** at the
10-delta/40 strike ~88% of breakouts win held-to-EOD (the far strike absorbs the
shakeouts), so reversal-propensity ≠ loss-propensity. The −$50,850 of
`risk_off_reversal` losses are triggered by **VIX-RSI risk spikes** (the trader's
risk gate), not by entry-time reversal-proneness the model can see. Documented
NO-GO for the entry use.

### Exit override — EXPLORATORY (second look), promising

The premature stops (`risk_off_reversal` fires on an OR-retreat *while VIX risk is
armed* — no strike-proximity test; the spread is barely underwater). Policy: when
a stop would fire, hold to expiry if entry conviction was high (P ≤ 0.372), else
take the stop. Over the 166 chain-era stopped trades:

| policy | total P&L |
|---|---|
| actual (stop every time) | −$79,800 |
| naive hold-all | −$2,175 |
| **model (selective hold)** | **+$16,448** |

Discriminator: trades the model says HOLD recovered **+$96,248** (held − actual);
trades it says STOP would have lost **another −$18,622** if held. The conviction
genuinely separates shakeouts (ride) from genuine reversals (keep stopping), and
beats naive hold-all by *being selective*. **Caveat:** this is a **second look**
at the chain era (the entry gate examined it first) → exploratory, not a clean
gate. Clean confirmation needs fresh forward trades.

The exit engine was reproduced and validated to do this: `analysis/exit_replica.py`
reproduces the VIX-RSI risk-heat model (armed on 83% of actual `risk_off_reversal`
exits, off on `sr_inner_breach`/winners).

## 4. SPX (index) > SPY (ETF) — a real finding

Rebuilding on SPX (for the no-transfer-gap + traded-instrument reasons) turned out
to be a genuine signal improvement, not cosmetic. Evidence it's real, not leakage:

- **Lower base rate:** SPX tradeable reversal 35.9% vs SPY 40.5%. The SPY *ETF*
  carries 1-min microstructure noise (bid-ask bounce) the SPX *index* (a smooth
  500-stock average) doesn't — ~4.5pp of **spurious, unpredictable** OR-reversals
  that dirty the label and drag AUC.
- **Broad, realistic lift:** SPX OOF AUC runs 0.59→0.76 year-by-year with normal
  variation — not the uniform 0.9+ of leakage.
- **No suspicious feature; coherent mechanism.** The morning (noisiest session →
  most ETF noise) improves most (0.96 → 0.79), exactly as the story predicts.

Lesson for v3+: model the **index**, not the ETF, for index-tracking signals.

## 5. Honest status & the reopened entry question

- **Entry use:** the documented one-shot NO-GO was for the **SPY** model and
  stands. The **SPX** model is materially stronger (it passes the dev confound
  guard the SPY model failed), which *reopens* the entry question — but only in
  **development**. We will **not** re-run the entry gate on the already-examined
  chain era and declare a pass; that's the iterate-until-it-works pattern the
  one-shot discipline forbids. The SPX model has earned a clean **forward** shot.
- **Exit override:** promising but exploratory (second look). Needs forward
  confirmation too.
- **Rolling-window versions** (regime-matched, vs the current expanding window)
  are deferred to v3 — a separate experiment, not a v2 retune.

## 6. What's next

The only clean adjudication for both the (reopened) entry use and the exit
override is **forward**: deploy the 7 SPX versions in eleuthera, run the
exploratory backtest (Oct-2024 → Jun-2026, each quarter scored by a version that
never saw it), and then run genuinely forward on new trades past today with a
**fresh pre-registered criterion**. The walk-forward infra + JS serving contract
exist for exactly this (`docs/v2-model-serving.md`).

## 7. Key lessons

1. **Reversal-rate ≠ loss-rate.** A far-OTM credit spread wins ~88% regardless of
   OR-reversals; the entry signal is economically inert because the strike
   absorbs what it predicts.
2. **The value is management, not selection.** The same signal that's useless at
   entry is promising at the *exit* — deciding which premature stops are shakeouts.
3. **Index > ETF** for this signal (microstructure noise manufactures spurious
   reversals).
4. **The discipline worked.** The one-shot entry gate prevented a false-positive
   ship; the validation test caught that SPX wasn't a cosmetic swap; and the
   second-look/forward distinction is keeping the reopened entry question honest.
