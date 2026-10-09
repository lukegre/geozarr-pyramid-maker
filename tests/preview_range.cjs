// Execute the viewer's real Auto-range helpers (D-48) against fake tile caches.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const start = html.indexOf('const AUTO_MAX');
const end = html.indexOf('\nfunction buildStyleControls(', start);
assert.ok(start >= 0 && end > start, 'auto range helpers');
const state = {vlayers: new Map()};
const cmapGroups = [
  {label: 'Sequential', source: 'core', names: ['viridis', 'RdBu_r']},
  {label: 'Diverging', source: 'core', names: ['RdBu', 'coolwarm']},
  {label: 'cmocean: Sequential', source: 'cmocean', names: ['cmo.thermal']},
  {label: 'cmocean: Diverging', source: 'cmocean', names: ['cmo.balance']},
  {label: 'cmasher: Diverging', source: 'cmasher', names: ['cmr.iceburn']},
];
const context = vm.createContext({state, cmapGroups});
vm.runInContext(html.slice(start, end), context);
const autoRange = (v) => vm.runInContext('autoRange', context)(v);
const frameValues = (n) => Array.from(vm.runInContext('frameValues', context)(n));
const symmetricCmap = (c, r) => vm.runInContext('symmetricCmap', context)(c, r);
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

// autoRange: 2-98 % percentiles with linear interpolation.
const ramp = Array.from({length: 101}, (_, i) => i);  // 0..100
let r = autoRange(ramp);
near(r.vmin, 2); near(r.vmax, 98);
// skewed positive data (chlorophyll-like): no negative limit, no symmetry
const skew = Array.from({length: 1000}, (_, i) => Math.exp(i / 200));
r = autoRange(skew);
assert.ok(r.vmin > 0 && r.vmax > r.vmin);
assert.ok(r.vmax < Math.max(...skew));  // the extreme tail is clipped
// balanced signs -> symmetric +-5
r = autoRange(Array.from({length: 101}, (_, i) => -3 + (8 * i) / 100));  // [-3, 5]
near(r.vmax, 5 - 0.02 * 8); near(r.vmin, -r.vmax);
// unbalanced signs ([-1, 10]) stay as they are
r = autoRange(Array.from({length: 101}, (_, i) => -1 + (11 * i) / 100));
near(r.vmin, -1 + 0.22); near(r.vmax, 10 - 0.22);
// close all-positive values are not symmetric (needs lo < 0 < hi)
r = autoRange([10, 12]);
assert.ok(r.vmin > 0 && r.vmin < r.vmax);
// exactly half balanced counts as symmetric
r = autoRange([-5, -5, 10, 10]);
assert.deepEqual([r.vmin, r.vmax], [-10, 10]);
// the symmetric flag tells the Auto handler when the symmetric rule fired
assert.equal(autoRange(ramp).symmetric, false);
assert.equal(autoRange(skew).symmetric, false);
assert.equal(autoRange([10, 12]).symmetric, false);
assert.equal(autoRange([-5, -5, 10, 10]).symmetric, true);
assert.equal(autoRange(Array.from({length: 101}, (_, i) => -3 + (8 * i) / 100)).symmetric, true);
assert.equal(autoRange(Array.from({length: 101}, (_, i) => -1 + (11 * i) / 100)).symmetric, false);
assert.equal(autoRange([4, 4, 4]).symmetric, false);
// symmetric range: a non-diverging colourmap becomes RdBu reversed; a diverging one is kept with its Reversed state
assert.deepEqual({...symmetricCmap('viridis', false)}, {cmap: 'RdBu', reversed: true});
assert.deepEqual({...symmetricCmap('RdBu_r', true)}, {cmap: 'RdBu', reversed: true});  // legacy RdBu_r is in Sequential here
assert.deepEqual({...symmetricCmap('cmo.thermal', true)}, {cmap: 'RdBu', reversed: true});
assert.deepEqual({...symmetricCmap('RdBu', false)}, {cmap: 'RdBu', reversed: false});
assert.deepEqual({...symmetricCmap('coolwarm', true)}, {cmap: 'coolwarm', reversed: true});
assert.deepEqual({...symmetricCmap('cmo.balance', false)}, {cmap: 'cmo.balance', reversed: false});
assert.deepEqual({...symmetricCmap('cmr.iceburn', true)}, {cmap: 'cmr.iceburn', reversed: true});
// constant -> vmax = vmin + 1; empty / no finite values -> null
assert.deepEqual({...autoRange([4, 4, 4])}, {vmin: 4, vmax: 5, symmetric: false});
assert.equal(autoRange([]), null);
assert.equal(autoRange([NaN, Infinity]), null);
r = autoRange([NaN, 1, 3]);  // non-finite values are skipped
near(r.vmin, 1.04); near(r.vmax, 2.96);

// frameValues: tiles of the current key and the highest loaded z only, nodata skipped.
const tile = (z, key, data, size = [2, 2], state = 2) => ({
  key, tileCoord: [z, 0, 0], getState: () => state, getSize: () => size, getData: () => data,
});
const layerWith = (reps) => ({
  getRenderer: () => ({tileRepresentationCache: new Map(reps.map((rep, i) => [`k${i}`, {loaded: true, gutter: 0, ...rep}]))}),
});
const set = (name, layers, keys) => {
  state.vlayers.set(name, {layers, sources: keys.map((k) => ({getKey: () => k}))});
};
const K = 'a:{"t":1}';
set('v', [layerWith([
  {tile: tile(2, 'a:{"t":0}', new Float32Array([100, 100, 100, 100]))},  // stale slice, finer
  {tile: tile(0, K, new Float32Array([7, 7, 7, 7]))},                   // current, coarser
  {tile: tile(1, K, new Float32Array([1, 2, NaN, 4]))},                 // current, highest z; NaN skipped
  {tile: tile(2, K, new Float32Array([9, 9, 9, 9]), [2, 2], 1)},        // still loading
  {tile: tile(2, K, new Float32Array([9, 9, 9, 9])), loaded: false},    // not loaded
])], [K]);
assert.deepEqual(frameValues('v'), [1, 2, 4]);
// every source copy contributes (global world copies)
set('w', [layerWith([{tile: tile(0, K, new Float32Array([1, 2, 3, 4]))}]), layerWith([{tile: tile(0, K, new Float32Array([5, 6, 7, 8]))}])], [K, K]);
assert.deepEqual(frameValues('w'), [1, 2, 3, 4, 5, 6, 7, 8]);
// each copy is checked against its own key
set('x', [layerWith([{tile: tile(0, K, new Float32Array([1, 2, 3, 4]))}]), layerWith([{tile: tile(0, 'other', new Float32Array([5, 6, 7, 8]))}])], [K, K]);
assert.deepEqual(frameValues('x'), [1, 2, 3, 4]);
// nothing of the current slice loaded, no renderer yet, unknown variable
set('y', [layerWith([{tile: tile(0, 'a:{"t":0}', new Float32Array(4))}])], [K]);
assert.deepEqual(frameValues('y'), []);
set('z', [{}], [K]);
assert.deepEqual(frameValues('z'), []);
assert.deepEqual(frameValues('missing'), []);
// alpha = last band when there are several, 0 = nodata
set('a', [layerWith([{tile: tile(0, K, new Float32Array([1, 1, 2, 0, 3, 1, 4, 1]))}])], [K]);  // 2x2, 2 bands
assert.deepEqual(frameValues('a'), [1, 3, 4]);
// gutter of 1 around a 1x1 tile (3x3 pixels): only the centre pixel counts
set('g', [layerWith([{gutter: 1, tile: tile(0, K, new Float32Array([0, 0, 0, 0, 5, 0, 0, 0, 0]), [1, 1])}])], [K]);
assert.deepEqual(frameValues('g'), [5]);
// subsampling: a 1000x1000 tile yields at most ~200k values
const big = new Float32Array(1000 * 1000).map((_, i) => i);
set('b', [layerWith([{tile: tile(0, K, big, [1000, 1000])}])], [K]);
const n = frameValues('b').length;
assert.ok(n > 0 && n <= 200000, `n = ${n}`);
