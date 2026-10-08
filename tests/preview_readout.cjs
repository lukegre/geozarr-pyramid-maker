// Execute the viewer's real pointer-readout sampler (D-39, D-41) against a fake renderer tile cache.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const start = html.indexOf('function sampleLayer(');
assert.ok(start >= 0, 'sampleLayer');
const end = html.indexOf('\nfunction ', start + 1);
const context = vm.createContext({});
vm.runInContext(html.slice(start, end), context);
const sampleLayer = (layer, source, coord) => vm.runInContext('sampleLayer', context)(layer, source, coord);

// One 2x2-pixel tile per level over [0, 4] x [0, 4]; level z has resolution 2 / (z + 1).
const grid = {
  getTileCoordExtent: () => [0, 0, 4, 4],
  getResolution: (z) => 2 / (z + 1),
};
const tile = (z, key, value, state = 2) => ({
  key, tileCoord: [z, 0, 0], getState: () => state, getSize: () => [2 * (z + 1), 2 * (z + 1)],
  getData: () => new Float32Array(4 * (z + 1) ** 2).fill(value),
});
const layerWith = (reps) => {
  const cache = new Map(reps.map((rep, i) => [`k${i}`, {loaded: true, gutter: 0, ...rep}]));
  return {getRenderer: () => ({tileRepresentationCache: cache})};
};
const sourceAt = (key) => ({getKey: () => key, getTileGrid: () => grid});
const at = [1, 1];

// Current slice only.
assert.equal(sampleLayer(layerWith([{tile: tile(0, 'a:{"month":9}', 9)}]), sourceAt('a:{"month":9}'), at), 9);

// The renderer keeps tiles of earlier slices (higher z than the new one): they must be ignored.
const reps = [
  {tile: tile(2, 'a:{"month":3}', 3)},  // stale, finer
  {tile: tile(0, 'a:{"month":9}', 9)},  // current, coarser
  {tile: tile(1, 'a:{"month":4}', 4)},  // stale
];
assert.equal(sampleLayer(layerWith(reps), sourceAt('a:{"month":9}'), at), 9);
// After the dimension changes again, the previously stale tile is the current one.
assert.equal(sampleLayer(layerWith(reps), sourceAt('a:{"month":3}'), at), 3);

// Current slice not loaded yet: no value rather than a stale one.
assert.equal(sampleLayer(layerWith(reps.slice(0, 1).concat(reps.slice(2))), sourceAt('a:{"month":9}'), at), null);
assert.equal(sampleLayer(layerWith([{tile: tile(0, 'a:{"month":9}', 9, 1)}]), sourceAt('a:{"month":9}'), at), null);  // loading
assert.equal(sampleLayer(layerWith([{tile: tile(0, 'a:{"month":9}', 9), loaded: false}]), sourceAt('a:{"month":9}'), at), null);

// Several dims: a tile for another index of any dim is stale.
const two = [{tile: tile(1, 'a:{"month":9,"depth":0}', 1)}, {tile: tile(0, 'a:{"month":9,"depth":5}', 5)}];
assert.equal(sampleLayer(layerWith(two), sourceAt('a:{"month":9,"depth":5}'), at), 5);
