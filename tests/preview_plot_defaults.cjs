// Execute the viewer's real plot-default code (D-43): attr limits, cmap resolution, defaultStyle and its precedence.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const data = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
function between(from, to) {
  const start = html.indexOf(from);
  assert.ok(start >= 0, from);
  const end = html.indexOf(to, start + 1);
  assert.ok(end > start, to);
  return html.slice(start, end);
}
const warnings = [];
const kv = new Map();
const store = {get: (k) => kv.has(k) ? kv.get(k) : null, set: (k, v) => kv.set(k, v), remove: (k) => kv.delete(k)};
const context = vm.createContext({
  cfg: {colormaps: data.colormaps, ramp: data.colormaps.viridis, title: 't'}, store,
  console: {warn: (m) => warnings.push(m)},
});
const run = (code) => vm.runInContext(code, context);
run('const cmaps = cfg.colormaps;');
run(between('// D-43 (mirrors preview._attr_limits)', 'async function browserConfig'));  // attrNum, attrLimits
run(between('const styleKey', 'const styles = {};') + 'const styles = {};');  // styleKey, resolveCmap, defaultStyle
run(between('function loadStyle', 'const styleOf'));
const style = (v) => ({...run('defaultStyle')(v)});
const limits = (a, lo = 0, hi = 10) => [...run('attrLimits')(a, lo, hi, 'v')];

// attrLimits: each attr overrides independently; invalid values are ignored.
assert.deepEqual(limits({}), [0, 10]);
assert.deepEqual(limits({vmin: 2}), [2, 10]);
assert.deepEqual(limits({vmax: 5}), [0, 5]);
assert.deepEqual(limits({vmin: 270, vmax: 305}), [270, 305]);
assert.deepEqual(limits({vmin: true, vmax: '5'}), [0, 10]);
assert.deepEqual(limits({vmin: NaN, vmax: null}), [0, 10]);
assert.equal(warnings.length, 0);
// One-sided inversion: vmax = vmin + 1, no warning; both inverted: warn and ignore both.
assert.deepEqual(limits({vmin: 20}), [20, 21]);
assert.deepEqual(limits({vmax: -4}), [0, 1]);
assert.equal(warnings.length, 0);
assert.deepEqual(limits({vmin: 8, vmax: 3}), [0, 10]);
assert.deepEqual(limits({vmin: 3, vmax: 3}), [0, 10]);
assert.equal(warnings.length, 2);
warnings.length = 0;

// defaultStyle: without the new keys it is exactly the old default.
assert.deepEqual(style({name: 'a', vmin: 1, vmax: 2}), {cmap: 'viridis', reversed: false, vmin: 1, vmax: 2});
// Resolved config from the server.
assert.deepEqual(style({name: 'a', vmin: 1, vmax: 2, cmap: 'cmo.thermal', reversed: true}),
  {cmap: 'cmo.thermal', reversed: true, vmin: 1, vmax: 2});
assert.deepEqual(style({name: 'a', vmin: 1, vmax: 2, reversed: true}), {cmap: 'viridis', reversed: true, vmin: 1, vmax: 2});
// Raw attr from the in-browser loader: exact key, "_r" suffix, legacy RdBu_r key, unknown.
assert.deepEqual(style({name: 'a', vmin: 0, vmax: 1, cmap: 'cmr.ember'}), {cmap: 'cmr.ember', reversed: false, vmin: 0, vmax: 1});
assert.deepEqual(style({name: 'a', vmin: 0, vmax: 1, cmap: 'viridis_r'}), {cmap: 'viridis', reversed: true, vmin: 0, vmax: 1});
assert.deepEqual(style({name: 'a', vmin: 0, vmax: 1, cmap: 'RdBu_r'}), {cmap: 'RdBu_r', reversed: false, vmin: 0, vmax: 1});
assert.equal(warnings.length, 0);
assert.deepEqual(style({name: 'a', vmin: 0, vmax: 1, cmap: 'nonsense_r'}), {cmap: 'viridis', reversed: false, vmin: 0, vmax: 1});
assert.equal(warnings.length, 1);
assert.ok(warnings[0].includes('nonsense_r'));

// Precedence: a valid stored style beats the attrs; a corrupt one falls back to them.
const v = {name: 'a', vmin: 270, vmax: 305, cmap: 'cmo.thermal', reversed: false};
context.v = v;
assert.deepEqual({...run('loadStyle(v)')}, {cmap: 'cmo.thermal', reversed: false, vmin: 270, vmax: 305});
kv.set('gpm-preview:style:t:a', JSON.stringify({cmap: 'magma', reversed: true, vmin: 1, vmax: 2}));
assert.deepEqual({...run('loadStyle(v)')}, {cmap: 'magma', reversed: true, vmin: 1, vmax: 2});
kv.set('gpm-preview:style:t:a', '{broken');
assert.deepEqual({...run('loadStyle(v)')}, {cmap: 'cmo.thermal', reversed: false, vmin: 270, vmax: 305});
