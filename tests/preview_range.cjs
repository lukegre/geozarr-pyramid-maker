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
const autoRange = (v, positive) => vm.runInContext('autoRange', context)(v, positive);
const frameExtent = (n, positive) => vm.runInContext('frameExtent', context)(n, positive);
const minMaxRange = (a, b) => vm.runInContext('minMaxRange', context)(a, b);
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

// frameExtent / minMaxRange (D-51): true extremes of the current frame, every pixel scanned (no stride).
// 1500x1500 = 2.25M values > AUTO_MAX, so the stride of frameValues skips pixels; the extremes sit off-grid.
const huge = new Float32Array(1500 * 1500).fill(5);
huge[1 * 1500 + 1] = -42;   // row 1, col 1: not on any stride >= 2 grid
huge[1499 * 1500 + 1499] = 99;  // last pixel: not on the stride grid either
set('h', [layerWith([{tile: tile(0, K, huge, [1500, 1500])}])], [K]);
const sampled = frameValues('h');
assert.ok(sampled.length <= 200000 && sampled.every((x) => x === 5), 'stride misses the extremes');
assert.deepEqual({...frameExtent('h')}, {min: -42, max: 99});
// stale keys ignored, only the highest z used, across world copies
set('e', [layerWith([
  {tile: tile(2, 'a:{"t":0}', new Float32Array([-1000, 1000, 0, 0]))},  // stale slice
  {tile: tile(0, K, new Float32Array([-500, 500, 0, 0]))},              // coarser than z = 1
  {tile: tile(1, K, new Float32Array([3, 8, NaN, -2]))},
]), layerWith([{tile: tile(1, K, new Float32Array([20, 1, 1, 1]))}])], [K, K]);
assert.deepEqual({...frameExtent('e')}, {min: -2, max: 20});
// nodata (alpha 0) and non-finite values are skipped
set('n', [layerWith([{tile: tile(0, K, new Float32Array([-9, 0, 4, 1, Infinity, 1, 6, 1]))}])], [K]);  // 2 bands
assert.deepEqual({...frameExtent('n')}, {min: 4, max: 6});
// gutter handled as in frameValues
set('gx', [layerWith([{gutter: 1, tile: tile(0, K, new Float32Array([-7, 0, 0, 0, 5, 0, 0, 0, 77]), [1, 1])}])], [K]);
assert.deepEqual({...frameExtent('gx')}, {min: 5, max: 5});
// nothing loaded, no renderer, unknown variable, all nodata -> null
assert.equal(frameExtent('y'), null);
assert.equal(frameExtent('z'), null);
assert.equal(frameExtent('missing'), null);
set('nan', [layerWith([{tile: tile(0, K, new Float32Array([NaN, NaN, NaN, NaN]))}])], [K]);
assert.equal(frameExtent('nan'), null);
// minMaxRange: exact limits, max = min + 1 for constant data, null passes through
assert.deepEqual({...minMaxRange(-3, 8)}, {vmin: -3, vmax: 8});
assert.deepEqual({...minMaxRange(4, 4)}, {vmin: 4, vmax: 5});
assert.deepEqual({...minMaxRange(-2, 20)}, {vmin: -2, vmax: 20});
assert.equal(minMaxRange(null, null), null);
assert.equal(minMaxRange(NaN, 1), null);
set('c', [layerWith([{tile: tile(0, K, new Float32Array([4, 4, 4, 4]))}])], [K]);
const ce = frameExtent('c');
assert.deepEqual({...minMaxRange(ce.min, ce.max)}, {vmin: 4, vmax: 5});

// Log scale (D-54): with `positive` only values > 0 count, so the range stays positive; none left -> null.
const mixed = [...Array(20).fill(-5), 0, ...Array.from({length: 100}, (_, i) => 1 + i)];  // 1..100 plus non-positive values
r = autoRange(mixed, true);
assert.ok(r.vmin > 0 && r.symmetric === false);
near(r.vmin, 1 + 0.02 * 99); near(r.vmax, 100 - 0.02 * 99);
assert.ok(autoRange(mixed).vmin < 0);  // without the flag nothing changes
assert.equal(autoRange([-3, 0, -1], true), null);
set('p', [layerWith([{tile: tile(0, K, new Float32Array([-4, 0, 3, 8]))}])], [K]);
assert.deepEqual({...frameExtent('p', true)}, {min: 3, max: 8});
assert.deepEqual({...frameExtent('p')}, {min: -4, max: 8});
set('q', [layerWith([{tile: tile(0, K, new Float32Array([-4, 0, -1, 0]))}])], [K]);
assert.equal(frameExtent('q', true), null);
