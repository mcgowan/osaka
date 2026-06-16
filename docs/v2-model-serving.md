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

## Artifact schema (`<label>.json`)

- `features` — the 20 input names, **in order** (the tree `split_feature` is an
  index into this list).
- `trees` — array of LightGBM tree nodes (`split_feature`, `threshold`,
  `default_left`, `left_child`/`right_child`, or terminal `leaf_value`).
- `init_offset` — float added to the summed leaf margin.
- `isotonic` — `{x, y}` monotone calibration knots.
- `operating_threshold`, `calib_greenlight_fraction` — the operating point
  (matched to the 5-bar greenlight rate on the version's calib tail).
- `golden` — 5 `{x, raw, prob}` acceptance vectors.

## JS evaluation (reproduce exactly — golden vectors are the test)

1. Compute the 20 `features` from the **SPX** bars at the breakout decision bar
   (close of the first 1-min close outside the OR). Spec: `docs/v2-feature-spec.md`,
   **excluding** the 4 volume + 2 vix1d features. Missing → `null`.
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

The model is now trained **on SPX itself** (osaka's SPX intraday reaches back to
2004), so there is **no SPY→SPX transfer gap** — train and infer are the same
instrument. The one remaining risk is the **JS feature port**: the 20-feature
computation in JS must match the harness-tested Python *exactly* — port it with
its own truncation/golden tests (the model eval above is the easy part; the
feature math is where bugs hide).

## Honest status

These are the **expanding** (2008→cutoff) family — the same family whose 2008–2020
member **failed** the one-shot entry gate. The chain-era backtest of these is
**exploratory**, not a verdict; the clean confirmation is **forward** (genuinely
new trades after today). The promising result is the **exit-override** use; the
entry use is a documented NO-GO pending fresh-data evidence. Rolling-window
versions (regime-matched) are deferred to v3.
