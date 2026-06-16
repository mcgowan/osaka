// Acceptance test for the JS evaluator (js/breakout_model.js).
//
// Loads all 7 production artifacts and re-scores each version's golden vectors in
// JS, asserting the (raw, prob) outputs match the Python-computed values to 1e-9.
// This is the proof that the pure-JS model eval is correct. The FEATURE port
// (SPX bars -> the 20 feature values) is validated separately by the integrator;
// here we prove the model-eval half exactly.
//
// Run:  node js/test_breakout_model.js   (exit 0 = pass, 1 = fail)

'use strict';
const path = require('path');
const { loadModel, predict } = require('./breakout_model');

const MODELS = path.join(__dirname, '..', 'data', 'processed', 'breakout-models-v2');
const VERSIONS = ['sep-2024', 'dec-2024', 'mar-2025', 'jun-2025',
                  'sep-2025', 'dec-2025', 'mar-2026'];
const TOL = 1e-9;

let total = 0, failures = 0;
for (const v of VERSIONS) {
  const model = loadModel(path.join(MODELS, `${v}.json`));
  let vFail = 0;
  for (const g of model.golden) {
    total++;
    const { raw, prob } = predict(model, g.x);
    if (Math.abs(raw - g.raw) > TOL || Math.abs(prob - g.prob) > TOL) {
      vFail++; failures++;
      console.error(`  FAIL ${v}: raw ${raw} vs ${g.raw} | prob ${prob} vs ${g.prob}`);
    }
  }
  console.log(`${v}: ${model.trees.length} trees, ${model.features.length} feats, ` +
              `thr=${model.operating_threshold.toFixed(3)}, ` +
              `${model.golden.length} golden -> ${vFail === 0 ? 'OK' : vFail + ' FAIL'}`);
}

console.log(`\n${total - failures}/${total} golden vectors reproduced within ${TOL}`);
if (failures) { console.error('FAILED — JS evaluator does not match Python'); process.exit(1); }
console.log('PASS — JS evaluator matches Python to 1e-9');
