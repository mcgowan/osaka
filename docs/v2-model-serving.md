# v2 Model Serving — eleuthera (JS) integration

7 expanding-window quarterly versions, built by `models/walk_forward_train.py`,
in `data/processed/breakout-models-v2/` (derived/gitignored — regenerate or copy
to eleuthera). Each `<label>.json` is self-contained; a pure-JS evaluator runs it
with no Python at trade time.

## Deploy schedule (no-lookahead)

Each version trains on 2008 → end of its month and scores **only the following
quarter** (it never sees a bar it scores):

| version | trained through | scores backtest quarter |
|---|---|---|
| sep-2024 | 2024-09-30 | Oct–Dec 2024 |
| dec-2024 | 2024-12-31 | Jan–Mar 2025 |
| mar-2025 | 2025-03-31 | Apr–Jun 2025 |
| jun-2025 | 2025-06-30 | Jul–Sep 2025 |
| sep-2025 | 2025-09-30 | Oct–Dec 2025 |
| dec-2025 | 2025-12-31 | Jan–Mar 2026 |
| mar-2026 | 2026-03-31 | Apr–Jun 2026 |

## Hosting one model at a time

You never load more than **one** model. Each `<label>.json` is fully
self-contained (~1 MB, ~400 trees), so the pattern is: load the version whose
deploy window contains the current backtest date, keep using it, and **swap to
the next file only when the date crosses a quarter boundary.** The 7 files map to
windows exactly as in the table above:

```
sep-2024.json → 2024-10-01 .. 2024-12-31      sep-2025.json → 2025-10-01 .. 2025-12-31
dec-2024.json → 2025-01-01 .. 2025-03-31      dec-2025.json → 2026-01-01 .. 2026-03-31
mar-2025.json → 2025-04-01 .. 2025-06-30      mar-2026.json → 2026-04-01 .. 2026-06-30 (last)
jun-2025.json → 2025-07-01 .. 2025-09-30
```

**Selection rule (in code):** for a bar dated `day`, use the version whose deploy
date is the latest one `<= day`. `js/breakout_model.js` implements this:

```js
const { versionForDate, modelPathFor, makeHost } = require('./breakout_model');

versionForDate('2025-02-14');   // 'dec-2024'
versionForDate('2024-09-15');   // null  — no model live before 2024-10-01
```

**Recommended: the stateful host** — keeps exactly one model in memory and swaps
it automatically at quarter boundaries, so you don't re-read the 1 MB file every
bar:

```js
const { makeHost } = require('./breakout_model');
const host = makeHost('data/processed/breakout-models-v2');

for (const breakout of breakouts) {          // your backtest stream, in date order
  const features = computeFeatures(breakout);          // the 26 SPY features (your port)
  const out = host.scoreAt(breakout.day, features);    // loads/swaps as needed
  if (out === null) continue;                          // before 2024-10-01: no model, skip
  // out = { raw, prob, threshold, flagged }
  // entry-gate use:  enter only if !out.flagged  (prob <= threshold)
  // exit-override:   on a stop, hold if the entry-bar prob was <= threshold
}
```

**Hosting a single fixed version** (e.g. to score one quarter, or to inspect):

```js
const { loadModel, predict } = require('./breakout_model');
const model = loadModel('data/processed/breakout-models-v2/dec-2024.json');
const { prob, flagged } = predict(model, features);
```

**Edge cases:**
- **Before 2024-10-01:** `versionForDate` returns `null` — no version is live yet
  (the first model trained through Sep-2024). Don't score; fall back to your
  existing rule.
- **After 2026-06-30:** `mar-2026` is the last version. For live trading past its
  window, **retrain** (re-run `models/walk_forward_train.py` with new cutoffs) —
  do not keep scoring 2026-Q3+ bars with a model trained through Mar-2026.

## Artifact schema (`<label>.json`)

- `features` — the 26 input names, **in order** (the tree `split_feature` is an
  index into this list). Full set incl. volume/VWAP + VIX1D.
- `trees` — array of LightGBM tree nodes (`split_feature`, `threshold`,
  `default_left`, `left_child`/`right_child`, or terminal `leaf_value`).
- `init_offset` — float added to the summed leaf margin.
- `isotonic` — `{x, y}` monotone calibration knots.
- `operating_threshold`, `calib_greenlight_fraction` — the operating point
  (matched to the 5-bar greenlight rate on the version's calib tail).
- `golden` — 5 `{x, raw, prob}` acceptance vectors.

## JS evaluation (reproduce exactly — golden vectors are the test)

1. Compute the 26 `features` from the **SPY** bars (+ VIX1D) at the breakout
   decision bar (close of the first 1-min close outside the OR). Spec:
   `docs/v2-feature-spec.md`, the full 26-feature set (incl. volume/VWAP). Match
   by timestamp to the SPX trade. Missing → `null`.
2. `x = features.map(f => featureValues[f] ?? null)`.
3. `margin = init_offset + Σ_trees leaf(tree)`, where `leaf` walks from the root:
   at each node `v = x[split_feature]`; go **left** if `v == null ? default_left
   : v <= threshold`; stop at `leaf_value`.
4. `raw = 1 / (1 + exp(-margin))`.
5. `P = ` clipped linear interpolation of `raw` through `isotonic.{x,y}`.
6. **Decision:** higher `P` = more reversal-prone. Entry-gate use: skip if
   `P > operating_threshold`. Exit-override use: on a stop, hold if the breakout's
   entry `P <= operating_threshold` (high conviction), else take the stop. (Or use
   `P` directly with your own cutoff.)
7. **Acceptance:** for each `golden` vector, feeding `x` must reproduce `raw`
   (±1e-9) and `prob`. (The Python build self-checks this; mirror it in JS.)

## Reference implementation (in-repo, proven)

The model evaluator is implemented and tested in JS in this repo:
- **`js/breakout_model.js`** — `loadModel(path)` + `predict(model, features)`
  implementing the algorithm above exactly. Drop it into eleuthera (or use as the
  reference for your own).
- **`js/test_breakout_model.js`** — loads all 7 artifacts and re-scores their
  golden vectors; **passes 35/35 to 1e-9** (`node js/test_breakout_model.js`).
  So the model-eval half is settled; only the feature port remains.

## Integration risk (do this before trusting it)

Trained on **SPY with volume** (the config that performed best for the exit
use). eleuthera needs a **SPY 1-min feed (with volume) + VIX1D**, computes the 26
features, and matches by timestamp to its SPX trades (SPY↔SPX ~0.999). Two risks:
(1) the **JS feature port** — the 26-feature computation must match the
harness-tested Python *exactly* (port with its own truncation/golden tests); and
(2) the **SPY→SPX timestamp match** for trade alignment.

## Honest status

These are the **expanding** (2008→cutoff) family — the same family whose 2008–2020
member **failed** the one-shot entry gate. The chain-era backtest of these is
**exploratory**, not a verdict; the clean confirmation is **forward** (genuinely
new trades after today). The promising result is the **exit-override** use; the
entry use is a documented NO-GO pending fresh-data evidence. Rolling-window
versions (regime-matched) are deferred to v3.
