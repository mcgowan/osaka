# False-breakout overlay memo — does the near-strike edge save money at real exits?

**Question.** Replace each logged early exit with a model-gated decision —
when the exit rule fires, consult the frozen v1 model and hold to settlement
instead of exiting iff `p_model > p_mkt` — and measure net P&L vs what the exit
rule actually did. Reproducible: `analysis/model_exit_overlay.py` (reuses
`exit_characterization.py`). Pre-TEST_START only; frozen v1 29-feature model
read once for decision support; deterministic.

**Universe.** 107 early exits (exit rule fired, not expiration), pre-TEST_START.
11 are TRAIN-era (in-sample for the model), 96 are HELD-OUT (CALIB+VALID+embargo).
Lean on the held-out figure.

## Headline (parameter-free rule: hold iff p_model > p_mkt)

| era | #exits | #override | net Δ $ | $/override |
|---|---|---|---|---|
| TRAIN (in-sample) | 11 | 4 | **−$2,535** | −$634 |
| **HELD-OUT (honest)** | 96 | 55 | **+$28,072** | +$510 |

Raw held-out delta is positive (+$28k). But three independent checks show it is
**not the validated edge** and **not a product**.

## Why +$28k is not a product

**1. Attribution — the model LOSES to naively holding everything (HELD-OUT):**

| strategy | net Δ $ |
|---|---|
| always-hold all 96 early exits | **+$41,940** |
| model-gated (hold the 55 it likes) | +$28,072 |
| **model's selectivity vs always-hold** | **−$13,868** |

The trader's early exits are mostly unnecessary (task 1.6: ~$138k of exit cost
sat where 96% settled safe), so *any* hold-biased signal books money. The model
books **less** than ignoring it and holding everything. Of the 41 exits the
model endorsed (`p_model ≤ p_mkt` → "exit"), **93% would have settled safe** —
its exit-calls are almost all wrong, leaving +$13,868 on the table.

**2. Regime mismatch — the validated edge is never exercised (pre-registered
kill trigger):**

| D at exit | #exits | #override | net Δ |
|---|---|---|---|
| D ≤ 0.5 (near-strike, the validated regime) | **0** | 0 | $0 |
| D > 0.5 (outside the validated edge) | 107 | 59 | +$25,538 |

**Zero** early exits fire in the model's only validated regime. The
risk-off/SR-breach rules trigger while the strike is still far OTM (median exit
D ~2); by construction the model's near-strike edge cannot be consulted at the
moments exits actually happen. The +$25.5k is generated entirely at D > 0.5,
where 4.6/4.8 found **no robust edge** (band negative in calm). This is the
dollar-form of the "too late / wrong regime" intuition.

**3. Fat backfire tail.** Headline rule (held-out): 48 saved (+$114k) vs **7
backfired (−$86k)**; backfired$ / saved$ = **0.75**; worst single backfire
**−$22,942**. Seven bad holds eat three-quarters of the savings — one regime
shift from erasing the rest.

(Sensitivity sweep at fixed `p_model ≥ {0.7,0.8,0.9}` shows larger positive
deltas, but those rules hold ~90% of exits = hold-bias, are confounded with
distance, and are explicitly **not** the verdict lens. The parameter-free rule
is.)

## Verdict

**KILL — documented negative; no overlay product, no one-shot run.** On the
honest held-out era the parameter-free model-gated rule (a) underperforms the
trivial always-hold baseline (+$28k vs +$42k; selectivity −$13.9k), (b) fires
**zero** overrides in the validated near-strike regime (the pre-registered kill
trigger), and (c) mis-endorses exits that were 93% safe. The near-strike edge is
real but lands where the trader's exits never reach; where exits do fire the
model has no validated edge and is worse than holding everything. The
false-breakout *overlay* is not viable. (This is independent of the Gate-4
band-of-record finding and points the same way.)
