#!/usr/bin/env python3
"""v2 — host the (Python) breakout model as a local HTTP service for eleuthera.

Everything stays in tested Python: this process loads the 7 walk-forward model
versions AND computes the features, then answers scoring requests over HTTP. Your
JS engine just calls it and reads a number — no JS model, no feature port, no CSV.

On startup it builds the feature table for the deploy window (Oct-2024 -> latest)
once, so each /score request is an in-memory lookup + tree eval (fast). The
correct point-in-time walk-forward version is used per date (no lookahead).

Run:    .venv/bin/python serve_model.py [--port 8771]
Query:  GET /score?day=2025-02-14&side=up&start_mod=37
        -> {"p_success":0.63,"p_reversed":0.37,"model_version":"dec-2024",...}
        (side = up|down; start_mod = minutes since 09:30, OR start_et=HH:MM)
        /health -> {"ok":true,"breakouts":2991}
"""

import argparse
import bisect
import json
import math
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))
from data.breakout_features import build  # noqa: E402
from data.loader import OUT_DIR  # noqa: E402

MODELS = os.path.join(OUT_DIR, "breakout-models-v2")
DEPLOY = [("2024-10-01", "sep-2024"), ("2025-01-01", "dec-2024"),
          ("2025-04-01", "mar-2025"), ("2025-07-01", "jun-2025"),
          ("2025-10-01", "sep-2025"), ("2026-01-01", "dec-2025"),
          ("2026-04-01", "mar-2026")]
MATCH_TOL = 3        # match a query start_mod to the nearest breakout within N min


def version_for(day):
    v = None
    for start, label in DEPLOY:
        if day >= start:
            v = label
    return v


def p_reversed(art, row):
    x = [row.get(f) for f in art["features"]]

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


class State:
    arts = {}
    by_key = {}          # (day, side) -> sorted list of (start_mod, row)


def load():
    State.arts = {label: json.load(open(os.path.join(MODELS, f"{label}.json")))
                  for _, label in DEPLOY}
    df = build(start="2024-09-01", symbol="SPY", _unlocked_full_span=True)
    for r in df.to_dict("records"):
        State.by_key.setdefault((r["day"], r["side"]), []).append((int(r["start_mod"]), r))
    for k in State.by_key:
        State.by_key[k].sort()
    n = sum(len(v) for v in State.by_key.values())
    print(f"[serve_model] ready: {len(State.arts)} versions, {n} breakouts "
          f"({df['day'].min()}..{df['day'].max()})", flush=True)


def score(day, side, start_mod):
    v = version_for(day)
    if v is None:
        return {"error": f"no model deployed for {day} (first is 2024-10-01)"}
    cands = State.by_key.get((day, side))
    if not cands:
        return {"error": f"no {side} breakout on {day}"}
    sm, row = min(cands, key=lambda x: abs(x[0] - start_mod))
    if abs(sm - start_mod) > MATCH_TOL:
        return {"error": f"no {side} breakout within {MATCH_TOL}min of {start_mod} on {day}"}
    pr = p_reversed(State.arts[v], row)
    return {"p_success": round(1 - pr, 4), "p_reversed": round(pr, 4),
            "model_version": v, "matched_start_mod": sm,
            "reversal_threshold": round(State.arts[v]["operating_threshold"], 4)}


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

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self._send({"ok": True, "breakouts": sum(len(v) for v in State.by_key.values())})
        if u.path != "/score":
            return self._send({"error": "GET /score?day=&side=&start_mod= or /health"}, 404)
        q = parse_qs(u.query)
        try:
            day = q["day"][0]; side = q["side"][0]
            start_mod = (int(q["start_mod"][0]) if "start_mod" in q
                         else _emod(q["start_et"][0]))
        except (KeyError, ValueError):
            return self._send({"error": "need day=YYYY-MM-DD, side=up|down, start_mod=N (or start_et=HH:MM)"}, 400)
        if side not in ("up", "down"):
            return self._send({"error": "side must be up or down"}, 400)
        res = score(day, side, start_mod)
        self._send(res, 200 if "error" not in res else 404)


def _emod(s):
    h, m = s.split(":"); return int(h) * 60 + int(m) - 570


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8771)
    a = ap.parse_args()
    load()
    print(f"[serve_model] listening on http://127.0.0.1:{a.port}", flush=True)
    HTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
