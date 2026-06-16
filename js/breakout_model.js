// v2 OR-breakout reversal model — pure-JS evaluator (the eleuthera runtime).
//
// Loads a `<label>.json` artifact (built by models/walk_forward_train.py) and
// scores a feature vector with NO Python at trade time. Reproduces the validated
// Python reference bit-exactly; see docs/v2-model-serving.md for the contract and
// js/test_breakout_model.js for the golden-vector acceptance test.
//
// Usage:
//   const { loadModel, predict } = require('./breakout_model');
//   const model = loadModel('data/processed/breakout-models-v2/sep-2024.json');
//   const { prob, flagged } = predict(model, { ext_atr: 0.3, range_atr: 0.5, ... });
//
// `features` is an object keyed by the 20 feature names (model.features). A
// missing or NaN value is treated as null -> the tree's default_left branch
// (matching LightGBM). Compute the features from SPX bars per the feature spec
// (docs/v2-feature-spec.md, production 20) — that port is the integrator's job.

'use strict';
const fs = require('fs');
const path = require('path');

function loadModel(jsonPath) {
  return JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
}

// ── Version selection (walk-forward deploy schedule) ──────────────────────────
// Each version scores from its deploy date until the next version takes over, so
// a model never scores a bar it could have trained on. You only ever need ONE
// model loaded at a time — the one for the current backtest date.
const DEPLOY = [
  ['2024-10-01', 'sep-2024'], ['2025-01-01', 'dec-2024'],
  ['2025-04-01', 'mar-2025'], ['2025-07-01', 'jun-2025'],
  ['2025-10-01', 'sep-2025'], ['2026-01-01', 'dec-2025'],
  ['2026-04-01', 'mar-2026'],
];

// versionForDate('2025-02-14') -> 'dec-2024'.  Returns null before the first
// deploy date (2024-10-01) — no model is live yet, so don't score.
function versionForDate(day) {
  let label = null;
  for (const [from, v] of DEPLOY) if (day >= from) label = v;
  return label;
}

// Full path to the artifact that should score `day`, or null if none is live.
function modelPathFor(modelsDir, day) {
  const v = versionForDate(day);
  return v === null ? null : path.join(modelsDir, `${v}.json`);
}

// Stateful host that keeps exactly ONE model in memory and swaps it only when
// the backtest crosses into a new deploy quarter. Use this in a streaming loop:
//   const host = makeHost('data/processed/breakout-models-v2');
//   const out = host.scoreAt(bar.day, features);   // null before 2024-10-01
function makeHost(modelsDir) {
  let current = null, model = null;
  return {
    scoreAt(day, features) {
      const v = versionForDate(day);
      if (v === null) return null;
      if (v !== current) { model = loadModel(path.join(modelsDir, `${v}.json`)); current = v; }
      return predict(model, features);
    },
    get version() { return current; },
  };
}

// Walk one tree to its leaf. `x` is the ordered feature-value array.
function leafValue(node, x) {
  while (node.leaf_value === undefined) {
    const v = x[node.split_feature];
    const goLeft = (v === null || v === undefined || Number.isNaN(v))
      ? node.default_left
      : (v <= node.threshold);
    node = goLeft ? node.left_child : node.right_child;
  }
  return node.leaf_value;
}

// Clipped linear interpolation of `raw` through the isotonic calibration knots.
function isotonic(model, raw) {
  const xs = model.isotonic.x, ys = model.isotonic.y;
  const n = xs.length;
  if (raw <= xs[0]) return ys[0];
  if (raw >= xs[n - 1]) return ys[n - 1];
  let lo = 0, hi = n - 1;                 // first index i with xs[i] >= raw
  while (lo < hi) { const mid = (lo + hi) >> 1; if (xs[mid] < raw) lo = mid + 1; else hi = mid; }
  const i = lo;
  return ys[i - 1] + (ys[i] - ys[i - 1]) * (raw - xs[i - 1]) / (xs[i] - xs[i - 1]);
}

// Score a feature object. Returns { raw, prob, threshold, flagged }:
//   raw      = uncalibrated sigmoid(sum(leaf) + init_offset)
//   prob     = isotonic-calibrated P(reversed)
//   flagged  = prob > operating_threshold (higher prob = more reversal-prone)
function predict(model, features) {
  const x = model.features.map((f) => {
    const v = features[f];
    return v === undefined ? null : v;
  });
  let margin = model.init_offset;
  for (const tree of model.trees) margin += leafValue(tree, x);
  const raw = 1 / (1 + Math.exp(-margin));
  const prob = isotonic(model, raw);
  return {
    raw,
    prob,
    threshold: model.operating_threshold,
    flagged: prob > model.operating_threshold,
  };
}

module.exports = {
  loadModel, predict, leafValue, isotonic,
  versionForDate, modelPathFor, makeHost, DEPLOY,
};
