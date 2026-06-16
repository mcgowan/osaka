# v2 Results & Status — OR-Breakout Conviction Model

**As of 2026-06-15.** Companion to `docs/v2-plan.md` (the plan), `docs/v2-feature-spec.md`
(the feature contract), and `docs/v2-model-serving.md` (the eleuthera/JS integration).

## TL;DR

The model predicts, per OR breakout, the probability it **reverses** (gives back
≥1 OR-width before EOD). The signal is **real** as a *statistical* object — it
beats the 5-bar rule at flagging reversals, more so on the cleaner SPX index. But
against the trader's actual economics it mostly does not pay:

- **As an ENTRY gate: documented NO-GO** (one-shot economic gate). The 10-delta
  short strike sits ~2 OR-widths beyond entry, so ~88% of breakouts win regardless
  — most "reversals" are shakeouts the far strike absorbs. Gating entries
  **destroys P&L** (it skips winners), on **both** SPY and SPX. The reversal
  signal is economically inert at entry.
- **As an EXIT override: narrowly promising, unconfirmed.** The trader's premature
  `risk_off_reversal` stops fire on OR-retreats during VIX spikes, booking losses
  on shakeouts that would have settled fine. The model's conviction separates
  shakeouts from genuine reversals — but this is **SPY-specific** (+$16k 26-feat /
  +$3k 20-feat; SPX *fails* to reproduce it), exploratory (second look), and
  volume-sensitive. The one promising use is real but narrow and unsettled.
- **SPX is a real *dev* improvement but a *dollar* mirage:** SPX beats SPY on dev
  AUC (cleaner index vs noisy ETF, §4), which briefly reopened the entry question
  — but the held-out log validation shows the dev edge **does not translate** to
  P&L (entry still fails; exit is worse on SPX). SPX stays the right instrument
  for other reasons, just not for log economics. Forward is the only clean test.

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

## 4. SPX (index) vs SPY (ETF) — better in dev, but it did NOT translate to the log

Rebuilding on SPX (for the no-transfer-gap + traded-instrument reasons) is a
genuine **development** signal improvement, not cosmetic — and it's not leakage:

- **Lower base rate:** SPX tradeable reversal 35.9% vs SPY 40.5%. The SPY *ETF*
  carries 1-min microstructure noise (bid-ask bounce) the SPX *index* (a smooth
  500-stock average) doesn't — ~4.5pp of **spurious, unpredictable** OR-reversals
  that dirty the label and drag AUC (dev OOF AUC 0.708 vs 0.663).
- **Broad, realistic lift:** SPX OOF AUC runs 0.59→0.76 year-by-year — not the
  uniform 0.9+ of leakage. The no-future-leakage harness passes on the SPX path.
- **Coherent mechanism:** the morning (noisiest → most ETF noise) improves most
  (dev within-stratum ratio 0.96 → 0.79).

**BUT — the dev advantage did NOT carry to the held-out trading log** (clean
instrument test, `analysis/spx_frozen_compare.py`; both single dev-trained
20-feature models, differing only in instrument):

| against the log | SPY | SPX |
|---|---|---|
| entry: kept ≥ actual? | FAIL | FAIL (keeps more, still fails) |
| exit-override (selective) | +$3,038 | **−$13,065** |
| exit STOP-set recovery (want ≤0) | −$5,212 ✓ | +$10,890 ✗ |

The SPX walk-forward versions agree (`analysis/spx_log_validation.py`: exit
−$20,625). So SPX's higher dev AUC is a **dev mirage** by the economic measure —
it does not produce superior dollars on the actual trades, for entry *or* exit.
The SPX rebuild stays justified on its other merits (no transfer gap, the traded
instrument, deeper 2004+ history), **not** on log economics. Lesson: dev AUC ≠
held-out P&L — the discipline of not trusting the dev win was right.

*(Aside, noisy: dropping volume/VIX1D weakened the SPY exit-override too — 26-feat
+$16,448 vs 20-feat +$3,038 — hinting volume may carry some exit-timing signal
despite useless AUC. Small sample; revisit only if the exit use is pursued.)*

Lesson for v3+: model the **index**, not the ETF, for index-tracking signals.

## 5. Honest status & the reopened entry question

- **Entry use: NO-GO, now on stronger ground.** The one-shot gate killed it for
  SPY. SPX passes the *dev* confound guard (which briefly reopened the question),
  but §4's log validation shows that dev edge **does not translate** — SPX still
  fails the entry gate on real dollars (skips winners). So the entry NO-GO holds
  across both instruments; the SPX "reopening" was a dev mirage. A forward test
  could still surprise, but the held-out evidence is now negative for SPX too.
- **Exit override:** the promising result is **SPY-specific** and exploratory.
  SPX does *not* reproduce it (exit −$13k vs SPY +$3k/+$16k); and the SPY exit
  edge weakened when volume was dropped (§4 aside). So the one genuinely
  promising use is narrow and unconfirmed — it needs a forward test, and the
  feature set / instrument for it is *not* settled (SPY-with-volume may matter).
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
