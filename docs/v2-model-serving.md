# v2 Model Serving — eleuthera integration

The breakout model runs as a **local Python service**. eleuthera passes the
current day's bars and gets back a probability. All model logic — feature
computation and the trees — stays in Python.

**The model knows nothing about the current day except the bars you pass.** Each
of the 7 versions is trained only on data *before* its quarter, and the service
auto-picks the right one by date — so you never score a day with a model that saw
it. The service holds only **prior-day** history (yesterday's ATR, 20-day levels,
trailing baselines); the current day comes entirely from your bars. No lookahead.

## 1. Start the service

```
cd /Users/michael/GitHub/osaka
.venv/bin/python serve_model.py        # default port 8771
```
Loads the 7 versions + prior-day context (~a few seconds), then stays up:
```
[serve_model] ready: 7 versions, 3186 breakouts (2024-09-03..2026-06-12)
[serve_model] listening on http://127.0.0.1:8771
```

## 2. Score a breakout — `POST /score_bars` (pass today's bars)

When your detector fires a breakout, send today's SPY 1-min bars from the open up
to and including the breakout bar:

```js
const body = {
  day,            // 'YYYY-MM-DD'
  side,           // 'up' | 'down'  (breakout direction)
  bars,           // [[mod, open, high, low, close, volume], ...]
};                //   mod = minutes since 09:30 (09:31->1, 10:07->37); last row = breakout bar
const r = await fetch('http://127.0.0.1:8771/score_bars', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});
const { p_success } = await r.json();   // probability the breakout holds to EOD
```

The service builds all 26 features from **only the bars you pass** (today) plus
the prior-day history it holds, scores with the version trained before `day`, and
returns the probability — exactly what a live model would have produced at that
bar.

### Volume — important

The model uses volume, and **volume only exists on SPY** (the SPX index has none).
So the `bars` you pass must be **SPY OHLC*V*** — i.e. eleuthera needs a **SPY
1-min feed (with volume)**, in addition to whatever it uses to trade SPX. Each
bar is `[mod, open, high, low, close, volume]`; the service reads volume off them
to compute the 4 volume features. (No SPY feed ⇒ you'd have to fall back to the
volume-free model, which tested worse for the exit use.)

## 3. Response

```json
{ "p_success": 0.6316, "p_reversed": 0.3684,
  "model_version": "sep-2024", "reversal_threshold": 0.4746 }
```
| field | meaning |
|---|---|
| `p_success` | probability the breakout holds (= 1 − `p_reversed`) — **use this** |
| `p_reversed` | model's raw output: chance it reverses/fails |
| `model_version` | which walk-forward version scored it (auto-picked by date) |
| `reversal_threshold` | the version's operating point on `p_reversed` |

Errors return HTTP 4xx with `{ "error": "..." }` (e.g. no model for the date, or
fewer than 25 opening-range bars passed).

## 4. Convenience for a backtest — `GET /score`

If you'd rather not ship bars during a *historical* backtest, the service can pull
the SPY bars itself (it has the history). Same point-in-time result:
```
GET /score?day=2025-02-14&side=up&start_et=10:07     # or start_mod=37
```
Use `/score_bars` for anything you'd run live; `/score` is a backtest shortcut.

## 5. What the service handles for you

- **Version by date** — sep-2024 scores Oct–Dec 2024, dec-2024 scores Jan–Mar
  2025, … (no lookahead).
- **Point-in-time** — features use only the bars up to the breakout bar (today)
  + history strictly before `day`.
- `GET /health` → `{ "ok": true, ... }`.

## 6. Coverage / retraining

Covers **Oct-2024 → Jun-2026** (7 versions). For later dates, re-run
`models/walk_forward_train.py` with new cutoffs and restart the service. For
*live* use, the service's prior-day history must be kept current (feed osaka the
recent daily/intraday SPY data).
