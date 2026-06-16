#!/usr/bin/env python3
"""v2 — host the (Python) breakout model as a local HTTP service for eleuthera.

All model logic stays in tested Python; eleuthera calls over HTTP and reads a
probability. Two endpoints:

  POST /score_bars  (LIVE-FAITHFUL — recommended)
      You pass TODAY's SPY 1-min bars (open -> the breakout bar, OHLCV). The
      service computes the 26 features from ONLY those bars + PRIOR-day history
      it holds (ATR, 20-day levels, trailing volume/RV baselines — all from days
      before `day`, never the current day), then scores with the walk-forward
      version trained before `day`. Nothing about the current day reaches the
      model except the bars you pass. Works for backtest AND live.
      Body: {"day":"YYYY-MM-DD","side":"up|down",
             "bars":[[mod,open,high,low,close,volume], ...]}   # mod = min since 09:30
      -> {"p_success":..., "p_reversed":..., "model_version":...}

  GET  /score?day=&side=&start_et=   (data-resident convenience, backtest only)
      The service pulls the historical SPY bars itself. Same point-in-time
      result; handy when you don't want to ship bars.

VOLUME: SPX (index) has none, so /score_bars needs SPY OHLCV bars (with volume).

Run:  .venv/bin/python serve_model.py [--port 8771]
"""

import argparse
import bisect
import json
import math
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from data.breakout_features import (  # noqa: E402
    FEATURES, breakout_state, tape_features, volume_vwap, multiday_features,
    _multiday_context, _realized_vol_baseline, _tod_volume_baseline, build)
from data.breakouts import _mod, OR_BARS, MIN_OR_BARS  # noqa: E402
from data.loader import OUT_DIR, load_bars  # noqa: E402

MODELS = os.path.join(OUT_DIR, "breakout-models-v2")
DEPLOY = [("2024-10-01", "sep-2024"), ("2025-01-01", "dec-2024"),
          ("2025-04-01", "mar-2025"), ("2025-07-01", "jun-2025"),
          ("2025-10-01", "sep-2025"), ("2026-01-01", "dec-2025"),
          ("2026-04-01", "mar-2026")]
MATCH_TOL = 3


def version_for(day):
    v = None
    for start, label in DEPLOY:
        if day >= start:
            v = label
    return v


def p_reversed(art, feats):
    x = [feats.get(f) for f in art["features"]]

    def leaf(n):
        while "leaf_value" not in n:
            val = x[n["split_feature"]]
            go = n["default_left"] if (val is None or val != val) else (val <= n["threshold"])
            n = n["left_child"] if go else n["right_child"]
        return n["leaf_value"]

    margin = sum(leaf(t) for t in art["trees"]) + art["init_offset"]
    raw = 1 / (1 + math.exp(-margin))
    xs, ys = art["isotonic"]["x"], art["isotonic"]["y"]
    if raw <= xs[0]:
        return ys[0]
    if raw >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_left(xs, raw)
    return ys[i - 1] + (ys[i] - ys[i - 1]) * (raw - xs[i - 1]) / (xs[i] - xs[i - 1])


class S:
    arts = {}
    mctx = {}              # day -> prior-day daily context (ATR, levels, ...)
    rv_base = {}           # day -> trailing realized-vol baseline
    tod_base = None        # DataFrame [day x minute] trailing volume baseline
    by_key = {}            # (day, side) -> sorted [(start_mod, feat_row)] (for /score)


def load():
    S.arts = {label: json.load(open(os.path.join(MODELS, f"{label}.json")))
              for _, label in DEPLOY}
    spy = load_bars("SPY", start="2024-06-01", _unlocked_full_span=True)   # warmup for baselines
    spy["mod"] = _mod(spy["ts"]); spy["day"] = spy["ts"].dt.strftime("%Y-%m-%d")
    spy_daily = load_bars("SPY", freq="1day", _unlocked_full_span=True)
    v1d = load_bars("VIX1D", freq="1day", _unlocked_full_span=True)
    vix = load_bars("VIX", freq="1day", _unlocked_full_span=True)
    S.mctx = _multiday_context(spy_daily, v1d, vix)
    S.rv_base = _realized_vol_baseline(spy)
    S.tod_base = _tod_volume_baseline(
        spy.pivot_table(index="day", columns="mod", values="volume", aggfunc="first"))
    # /score convenience table (data-resident)
    df = build(start="2024-09-01", symbol="SPY", _unlocked_full_span=True)
    for r in df.to_dict("records"):
        S.by_key.setdefault((r["day"], r["side"]), []).append((int(r["start_mod"]), r))
    for k in S.by_key:
        S.by_key[k].sort()
    print(f"[serve_model] ready: {len(S.arts)} versions, "
          f"{sum(len(v) for v in S.by_key.values())} breakouts "
          f"({df['day'].min()}..{df['day'].max()})", flush=True)


def _ctx_for(day, start_mod):
    ctx = S.mctx.get(day)
    if ctx is None:
        return None, None, None
    rv = S.rv_base.get(day, float("nan"))
    tb = (S.tod_base.at[day, start_mod]
          if (S.tod_base is not None and day in S.tod_base.index
              and start_mod in S.tod_base.columns) else float("nan"))
    return ctx, rv, tb


def features_from_bars(day, side, bars):
    """Compute the 26 features from PASSED today-bars + held prior-day context.
    bars: list of [mod, open, high, low, close, volume], open -> breakout bar."""
    mods = np.array([b[0] for b in bars])
    o = np.array([float(b[1]) for b in bars]); h = np.array([float(b[2]) for b in bars])
    l = np.array([float(b[3]) for b in bars]); c = np.array([float(b[4]) for b in bars])
    v = np.array([float(b[5]) for b in bars])
    orb = mods < OR_BARS
    if orb.sum() < MIN_OR_BARS:
        return None
    or_high = float(h[orb].max()); or_low = float(l[orb].min())
    if or_high <= or_low:
        return None
    up = side == "up"
    # attempt = number of side-episodes that have started up to (incl.) the last bar
    attempt = 0; in_ep = False
    for cl in c:
        outside = (cl > or_high) if up else (cl < or_low)
        if outside and not in_ep:
            attempt += 1; in_ep = True
        elif not outside:
            in_ep = False
    si = len(bars) - 1
    start_mod = int(mods[si])
    ctx, rv, tb = _ctx_for(day, start_mod)
    if ctx is None:
        return None
    atr = ctx["atr14"]
    ev = {"side": side, "or_high": or_high, "or_low": or_low,
          "or_width": or_high - or_low, "start_close": float(c[si]),
          "attempt": attempt, "start_mod": start_mod}
    feats = {}
    feats.update(breakout_state(ev, atr))
    feats.update(volume_vwap(o, h, l, c, v, si, atr, tb))
    feats.update(tape_features(o, h, l, c, si, atr, ctx["close_y"], rv))
    feats.update(multiday_features(float(c[si]), float(o[0]), ctx))
    return feats


def result(day, feats):
    v = version_for(day)
    pr = p_reversed(S.arts[v], feats)
    return {"p_success": round(1 - pr, 4), "p_reversed": round(pr, 4),
            "model_version": v,
            "reversal_threshold": round(S.arts[v]["operating_threshold"], 4)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/score_bars":
            return self._send({"error": "POST /score_bars"}, 404)
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
            day, side, bars = req["day"], req["side"], req["bars"]
        except (ValueError, KeyError):
            return self._send({"error": "body: {day, side, bars:[[mod,o,h,l,c,v],...]}"}, 400)
        if side not in ("up", "down"):
            return self._send({"error": "side must be up or down"}, 400)
        if version_for(day) is None:
            return self._send({"error": f"no model for {day} (first 2024-10-01)"}, 404)
        feats = features_from_bars(day, side, bars)
        if feats is None:
            return self._send({"error": "could not form features (need >=25 OR bars + prior-day context)"}, 422)
        self._send(result(day, feats))

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self._send({"ok": True, "breakouts": sum(len(v) for v in S.by_key.values())})
        if u.path != "/score":
            return self._send({"error": "POST /score_bars  or  GET /score?day=&side=&start_et="}, 404)
        q = parse_qs(u.query)
        try:
            day, side = q["day"][0], q["side"][0]
            sm = int(q["start_mod"][0]) if "start_mod" in q else _emod(q["start_et"][0])
        except (KeyError, ValueError):
            return self._send({"error": "need day, side, start_mod (or start_et=HH:MM)"}, 400)
        if version_for(day) is None:
            return self._send({"error": f"no model for {day}"}, 404)
        cands = S.by_key.get((day, side))
        if not cands:
            return self._send({"error": f"no {side} breakout on {day}"}, 404)
        msm, row = min(cands, key=lambda x: abs(x[0] - sm))
        if abs(msm - sm) > MATCH_TOL:
            return self._send({"error": f"no {side} breakout within {MATCH_TOL}min of {sm}"}, 404)
        out = result(day, row); out["matched_start_mod"] = msm
        self._send(out)


def _emod(s):
    h, m = s.split(":"); return int(h) * 60 + int(m) - 570


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8771)
    a = ap.parse_args()
    load()
    print(f"[serve_model] listening on http://127.0.0.1:{a.port}", flush=True)
    HTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
