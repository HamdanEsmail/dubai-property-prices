// Evaluates the exported LightGBM trees (web/data/model.json) in the browser.
// Mirrors LightGBM's decision rules, including how missing values are routed.
// Predictions are on the log scale; predictPpsqm() returns AED per square metre.

export function encodeRow(model, row) {
  const x = [];
  for (const name of model.features) {
    if (model.categorical.includes(name)) {
      const levels = model.categories[name];
      const value = row[name];
      const label = value === null || value === undefined || value === "" ? model.missing : String(value);
      let code = levels.indexOf(label);
      if (code < 0) code = levels.indexOf(model.unknown);
      x.push(code);
    } else if (name === "log_area") {
      x.push(Math.log(Number(row.actual_area_sqm)));
    } else {
      const value = row[name];
      x.push(value === null || value === undefined ? NaN : Number(value));
    }
  }
  return x;
}

function goLeft(node, value, categorical) {
  const threshold = node[1];
  const defaultLeft = node[4] & 1;
  const missingType = node[4] >> 1;
  if (categorical) {
    if (Number.isNaN(value)) {
      if (missingType === 2) return false;
      value = 0;
    }
    const code = Math.trunc(value);
    if (code < 0) return false;
    return threshold.includes(code);
  }
  if (Number.isNaN(value) && missingType !== 2) value = 0;
  if ((missingType === 1 && Math.abs(value) <= 1e-35) || (missingType === 2 && Number.isNaN(value))) {
    return defaultLeft === 1;
  }
  return value <= threshold;
}

export function predictLog(model, name, x) {
  const categoricalIdx = new Set(model.categorical.map((c) => model.features.indexOf(c)));
  let total = 0;
  for (const nodes of model.models[name]) {
    let node = nodes[0];
    while (Array.isArray(node)) {
      const left = goLeft(node, x[node[0]], categoricalIdx.has(node[0]));
      node = nodes[left ? node[2] : node[3]];
    }
    total += node;
  }
  return total;
}

export function predictPpsqm(model, row) {
  const x = encodeRow(model, row);
  const adjust = model.interval_adjust || 0; // conformal widening, log units
  const point = Math.exp(predictLog(model, "point", x));
  const q10 = Math.exp(predictLog(model, "q10", x) - adjust);
  const q90 = Math.exp(predictLog(model, "q90", x) + adjust);
  // The band always contains the point estimate, as in model.py.
  return { point, lo: Math.min(q10, q90, point), hi: Math.max(q10, q90, point) };
}
