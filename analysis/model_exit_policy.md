# Model-driven exit policy — memo (real-chain valuation)

**Question.** Replace the trader's exit rules with a model-driven policy: query
the frozen v1 model every stride bar after entry; exit at the first bar the
model sees more danger than the market implies (`p_model < p_mkt`), else hold to
settlement. Does it beat the actual rules? Reproducible:
`analysis/model_exit_policy.py`. Pre-TEST_START only (194 spreads, ~10.7k
stride-bar queries); frozen v1 model read once; deterministic.

**Valuation.** Partial-credit closes are marked at the trader's **recorded
chain mids** (`eleuthera/events/`, via `data/chains.contract_quote_asof`) at the
policy's chosen exit minute — the same recorded-quote basis on which the logged
P&L was struck, so all three strategies are apples-to-apples. Always-hold is
exact at settle (intrinsic). A BS-on-VIX1D pass was run first and is reported
only as a cautionary contrast (it proved unusable — see below).

## Results — HELD-OUT era (honest), real recorded-chain marks

Coverage: 161/174 held-out trades markable (93%).

| strategy | P&L | vs always-hold |
|---|---|---|
| **always-hold to settlement** | **+$116,528** | — |
| actual exits (your rules, logged) | +$82,950 | −$33,578 |
| **model policy (`p_model<p_mkt`)** | **+$31,763** | **−$84,765** |

The model policy is **dead last.** It also loses to your own rules by −$51,187.

## No threshold rescues it (real-chain sweep, HELD-OUT)

| exit rule | policy P&L | vs always-hold | # early exits |
|---|---|---|---|
| headline `p<p_mkt` | +$31,763 | −$84,765 | 171 |
| `p<p_mkt−0.05` | +$104,985 | −$17,505 | 63 |
| `p<p_mkt−0.10` | +$129,615 | **+$525** | 19 |
| `p<0.60` | +$75,337 | −$53,115 | 44 |
| `p<0.70` | +$74,100 | −$54,390 | 57 |
| `p<0.80` | +$72,450 | −$54,465 | 93 |

The pattern is the whole story: **the more the policy acts on the model, the
more it loses.** The only rule that doesn't lose (`p<p_mkt−0.10`, +$525 ≈ noise
on $117k) gets there by exiting just 19/174 trades — i.e. by doing almost
nothing and converging to "hold." There is no setting where the model's exit
signal adds dollars over holding to settlement.

## Isolated near-strike test (the decisive one)

The headline policy is dominated by far-OTM exits (D>0.5, no edge), so it never
isolates the model's *validated* near-strike edge. So we ran exactly that: hold
every trade until price enters the D≤0.5 zone, then let the model cut (exit iff
D≤0.5 and p_model<p_mkt), on the 29 held-out trades that reach the zone, marked
on real chains.

| on the 29 near-strike trades | P&L |
|---|---|
| trader's actual rules | **−$38,025** |
| always-hold | −$123,397 |
| near-strike model policy | **−$134,048** |

The near-strike policy **loses to holding** (−$10,650) and is far worse than the
trader's own rules. Its cuts are mostly wrong: **of 7 cuts, 5 cut false
breakouts (winners), only 2 caught real breakouts.** A better Brier score near
the strike is *not* the ability to discriminate which trades to cut: the edge is
a hold-bias that holding already captures, and the trader's rules manage the
genuine danger far better than the model.

## The BS cautionary note
- **BS-on-VIX1D was unusable:** on the same trades it marked the policy at
  +$229,100 vs the real-chain +$31,763 — a **+$197,338 overstatement**. A flat
  VIX1D vol (no skew, wrong level near the money) makes early closes look nearly
  free; on real quotes they are expensive. Any BS-marked figure here is fiction;
  the real-chain numbers above are the trustworthy ones.

## Verdict

**KILL — documented negative; no viable model-driven exit policy.** On real
recorded quotes the policy is the worst of the three strategies (+$32k vs
always-hold +$117k vs actual +$83k); no threshold beats simply holding to
settlement. And when the validated near-strike edge is given its best shot —
isolated to the D≤0.5 zone — it still loses to holding and is far worse than the
trader's own rules, cutting winners 5 times out of 7. A better-calibrated
survival probability near the strike does not translate into profitable exit
discrimination; it is a hold-bias that holding already captures. Third
independent test (Gate-4 band, veto overlay, exit policy) to converge on the
same answer.
