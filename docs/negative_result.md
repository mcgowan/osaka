# v1 Negative-Result Memo — SPX 0DTE Strike Survival Model

**Decision: NO-GO for v1 (trader, 2026-06-13).** The pre-registered kill
criterion is met: the edge residual `p_model − p_mkt` is not a tradeable signal
where the strategy actually operates. A documented negative result is an
explicit, acceptable project outcome (requirements §8; plan Gate 5). The locked
test set is **deliberately left unspent** (see "On the locked test").

This memo records what was tested, the evidence, why delta (p_mkt) proved
sufficient, and what would change the answer. No pre-registered goalposts were
moved.

## What was tested

The project's product is the **edge residual** p_model − p_mkt: a calibrated,
monotone GBT (29 features, VIX1D-based normalized distance, no option pricing in
the pipeline) that should beat the analytic market-implied no-touch baseline in
the band of record (≈0.10–0.15Δ), persistently and where the trader acts. Three
independent evaluations, all leakage-controlled (week splits + embargo; VALID
one-shot; TRAIN walk-forward OOF for development):

## The evidence (three tests, one answer)

**1. Gate-4 validation — band of record (`docs/modeling.md`).** No robust edge
at the entry band. OOF band-of-record Brier skill vs p_mkt is **−0.0074**
(P(skill>0)=0.014 — worse). Decomposed, the edge is regime-conditional: positive
only in elevated vol (+0.009, P=0.98) and the near-strike slice, **negative in
calm and at the session open** (the pre-OR entry use case — its weakest spot).
The headline criterion (stable band edge across sub-periods) is **not met**.
Calibration also fails the ±3pt bar (raw ≈7pt; isotonic drifts CALIB→VALID and
makes it worse). Monotonicity: clean (0 violations).

**2. False-breakout veto overlay (`analysis/model_exit_overlay.md`).** Gating
the trader's actual early exits on the model (hold iff p_model>p_mkt) **loses to
naively holding everything** (held-out +$28k vs always-hold +$42k; selectivity
−$13.9k); of the exits it endorsed, 93% would have settled safe; and **0/107
exits fall in the validated near-strike regime** (D≤0.5) — the exits fire far
from the strike (median D~2), where the model has no edge.

**3. Model-driven exit policy, real-chain marked (`analysis/model_exit_policy.md`).**
Replacing the exit rules with a model-driven policy, valued on the trader's
recorded chain mids: the policy is **dead last** — +$32k vs always-hold +$117k
vs the actual rules +$83k (held-out). **No threshold beats holding to
settlement.** (BS-on-VIX1D had overstated it by +$197k — a pure pricing
artifact; the real-chain re-mark, demanded before any write-off, removed the
illusion.)

**3b. Isolated near-strike test (the decisive one).** Because (1)–(3) might
"never give the edge its shot," we ran the edge exactly where it is validated:
hold every trade until price enters the D≤0.5 zone, then let the model cut
(exit iff D≤0.5 and p_model<p_mkt), on the 29 held-out trades that reach the
zone, marked on real chains. The near-strike policy **loses to holding**
(−$134,048 vs −$123,397; −$10,650) and is **far worse than the trader's own
rules** (−$38,025). Its cuts are mostly wrong: **of 7 cuts, 5 cut false
breakouts (winners), only 2 caught real breakouts.**

## Why delta (p_mkt) proved sufficient — and the near-strike edge doesn't convert

The model's one **real, validated edge is near-strike** (D≤0.5: positive Brier
skill vs p_mkt across OOF/CALIB/VALID; mean residual +2.7pts correcting the
put-skew premium). The decisive finding (test 3b) is that **a better-calibrated
survival probability is not the ability to make profitable exit decisions.**
The near-strike edge is a *hold-bias* — "survival is higher than the market
implies," i.e. false breakouts recover — and **holding to settlement already
captures that fully** (it rides out every recovery). To beat holding, a policy
must *discriminate which* near-strike trades to **cut** (the real breakouts
headed to max loss); the model cannot — cutting in the validated zone is wrong
5 of 7 times and loses to holding. So the edge is real but **redundant with
holding** and useless for the cut decision.

Meanwhile, at the points the strategy actually operates — selling ~0.10Δ spreads
and managing them while the short strike is still 1–2+ implied-moves away
(D>0.5) — the analytic VIX1D baseline is as good as or better than the model
(negative skill in calm and at the open). And on the genuinely dangerous trades
that do reach the strike, **the trader's own exit rules manage them far better
than the model** (−$38k vs −$134k model vs −$123k holding on the 29 near-strike
trades). Delta was sufficient where the model has no edge; where the model has a
(probability) edge, it doesn't translate into dollars, and the trader's existing
rules already dominate. Hence: no-go.

## On the locked test (deliberately unspent)

The pre-registered kill criterion is judged "after honest evaluation." Honest
validation (test 1) plus two real-economics checks (tests 2–3) already establish
no tradeable edge. The locked test (≥2025-12-01) is a one-shot confirmation
instrument for a *promising* result; spending it to confirm a negative the
validation already shows would waste it. It is left **untouched and available**
for a genuinely new (v2) thesis. This is a discipline choice, not an evasion —
the no-go stands on the validation + economic evidence.

## What would change the answer (v2 candidates)

The near-strike edge is real; it just doesn't fit the v1 product. Directions
that would put the edge where it lives — each requiring fresh pre-registration
and its own held-out budget:
- A **near-strike / closer-to-money product** (or gamma/management overlay)
  where D≤0.5 states are the norm, not a rare tail.
- **Recorded-delta features** (chains, 2024-12→) and the shelved **delta-breach
  (δ\*) label** — both deferred at Gate S as v2.
- **Regime-conditional or deployment-adjacent calibration** (the isotonic
  CALIB→VALID drift showed the v1 calibration approach is wrong for this data).
- The **v2 feature prune** (`gbt.PRUNE_V2_CANDIDATE`) and any recent-impulse
  feature, re-validated properly.

## Status of artifacts

Frozen v1 model (29 features, `models/gbt_params.json`), eval harness, splits,
and the three analyses are committed and reproducible. 71 tests green. The
locked test remains unread outside this memo's scope.
