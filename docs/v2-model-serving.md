# v2 Model Serving — eleuthera integration

The breakout model runs as a **local Python service**. eleuthera calls it over
HTTP and reads back a probability. All model logic — feature computation and the
trees — stays in Python; eleuthera never computes features or runs model code.

## 1. Start the service

```
cd /Users/michael/GitHub/osaka
.venv/bin/python serve_model.py        # default port 8771
```

It loads the 7 walk-forward model versions and builds the features (~a few
seconds), then stays up:

```
[serve_model] ready: 7 versions, 3186 breakouts (2024-09-03..2026-06-12)
[serve_model] listening on http://127.0.0.1:8771
```

## 2. Call it from eleuthera (Node)

When your backtest detects a breakout, ask the service for its probability:

```js
const q = new URLSearchParams({ day, side, start_et });    // side: 'up' | 'down'
const r = await fetch(`http://127.0.0.1:8771/score?${q}`); // day:  'YYYY-MM-DD'
const { p_success } = await r.json();                       // start_et: 'HH:MM' (ET)
```

`p_success` = the probability the breakout holds to EOD. That's the whole
integration — one HTTP call, one number.

## 3. Response

```json
{ "p_success": 0.6316, "p_reversed": 0.3684, "model_version": "sep-2024",
  "matched_start_mod": 37, "reversal_threshold": 0.4746 }
```

or `{ "error": "..." }` with HTTP 404 if no breakout matches the query.

| field | meaning |
|---|---|
| `p_success` | probability the breakout holds (= 1 − `p_reversed`) — **use this** |
| `p_reversed` | the model's raw output: chance it reverses/fails |
| `model_version` | which walk-forward version scored it (auto-selected by date) |
| `matched_start_mod` | the breakout bar it matched (minutes since 09:30) |
| `reversal_threshold` | the version's operating point on `p_reversed` |

## 4. Query parameters

- `day` — `YYYY-MM-DD`.
- `side` — `up` or `down` (the breakout direction).
- `start_et` — breakout bar clock time `HH:MM` (ET). **Or** `start_mod=N`
  (minutes since 09:30) if that's easier for your engine.
- `GET /health` → `{ "ok": true, "breakouts": 3186 }`.

## 5. What the service handles for you

- **Picks the right model version by date** — sep-2024 scores Oct–Dec 2024,
  dec-2024 scores Jan–Mar 2025, … — each version only scores dates after its
  training cutoff (no lookahead).
- **Matches your breakout to the nearest bar within 3 minutes** (absorbs any
  SPX-vs-SPY timing drift).
- **Point-in-time:** every score uses only data up to that breakout's bar.

## 6. Coverage / retraining

Covers **Oct-2024 → Jun-2026** (the 7 versions). For dates past the last version,
re-run `models/walk_forward_train.py` with new cutoffs and restart the service.
