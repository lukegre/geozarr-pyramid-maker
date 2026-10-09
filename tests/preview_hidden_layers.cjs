// Hidden (eye off) variables must not hold layers or sources; only selected AND visible ones load data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
function source(name) {
  const start = html.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  const end = html.indexOf('\nfunction ', start + 1);
  return html.slice(start, end);
}
const base = {name: 'basemap'};
const layers = [base];
const collection = {
  remove(layer) { const i = layers.indexOf(layer); if (i >= 0) layers.splice(i, 1); },
  push(layer) { layers.push(layer); },
};
const variables = ['a', 'b', 'c'].map((name) => ({name, dims: name === 'c' ? [] : ['time']}));
const cards = new Map(variables.map((v) => [v.name, {}]));
const made = [];
const context = vm.createContext({
  state: {selected: [], vlayers: new Map(), open: new Set(), hidden: new Set(), scrubKey: '', dims: {}},
  cfg: {variables}, cards, $: () => ({}), varOf: (name) => variables.find((v) => v.name === name),
  makeLayers: (v) => {
    const vl = {
      layers: [0, 1].map((part) => ({name: v.name, part, dispose() { this.disposed = true; }})),
      sources: [0, 1].map(() => ({updates: [], updateDimensions(d) { this.updates.push(d); }})),
    };
    made.push(v.name);
    return vl;
  },
  stopPlay() {}, syncUrl() {}, updateSpine() {}, buildScrubber() {}, refreshCards() {}, unionDims: () => [],
});
context.state.map = {getLayers: () => collection};
for (const name of ['addVariable', 'removeVariable', 'restackLayers', 'toggleVisibility', 'moveVariable', 'setDim']) {
  vm.runInContext(source(name), context);
}
const {state} = context;
const onMap = () => layers.filter((l) => l !== base).map((l) => l.name);
const owners = () => Array.from(state.vlayers.keys());

for (const n of ['a', 'b', 'c']) context.moveVariable(n, true, state.selected.at(-1) || null);
assert.deepEqual(Array.from(state.selected), ['a', 'b', 'c']);
assert.deepEqual(onMap(), ['a', 'a', 'b', 'b', 'c', 'c']);
assert.equal(made.length, 3);

// Hiding disposes and removes the layers and creates nothing.
const bLayers = state.vlayers.get('b').layers.slice();
context.toggleVisibility('b');
assert.equal(state.hidden.has('b'), true);
assert.equal(made.length, 3);
assert.ok(bLayers.every((l) => l.disposed));
assert.equal(state.vlayers.has('b'), false);
assert.deepEqual(onMap(), ['a', 'a', 'c', 'c']);
assert.deepEqual(Array.from(state.selected), ['a', 'b', 'c']); // membership unchanged

// setDim only reaches the sources of visible variables.
state.dims.time = 0;
const aSources = state.vlayers.get('a').sources;
context.setDim('time', 4);
assert.ok(aSources.every((s) => s.updates.length === 1 && s.updates[0].time === 4));
assert.equal(state.vlayers.get('c').sources.every((s) => s.updates.length === 0), true); // no time dim

// Showing recreates the layers at the right stacking position.
context.toggleVisibility('b');
assert.equal(state.hidden.has('b'), false);
assert.equal(made.length, 4);
assert.deepEqual(onMap(), ['a', 'a', 'b', 'b', 'c', 'c']);
assert.ok(state.vlayers.get('b').layers.every((l) => !l.disposed));

// Hiding the bottom and top of the stack and showing in a different order keeps the stacking.
context.toggleVisibility('a');
context.toggleVisibility('c');
assert.deepEqual(onMap(), ['b', 'b']);
context.toggleVisibility('c');
context.toggleVisibility('a');
assert.deepEqual(onMap(), ['a', 'a', 'b', 'b', 'c', 'c']);

// Moving a hidden variable does not create layers.
context.toggleVisibility('b');
const before = made.length;
context.moveVariable('b', true, null); // drop below all
assert.deepEqual(Array.from(state.selected), ['b', 'a', 'c']);
assert.equal(made.length, before);
assert.equal(state.vlayers.has('b'), false);
assert.deepEqual(onMap(), ['a', 'a', 'c', 'c']);
context.toggleVisibility('b');
assert.deepEqual(onMap(), ['b', 'b', 'a', 'a', 'c', 'c']);

// A variable selected while hidden never gets layers; removing a hidden variable is fine.
context.toggleVisibility('a');
context.moveVariable('a', false);
assert.equal(state.hidden.has('a'), false);
assert.deepEqual(onMap(), ['b', 'b', 'c', 'c']);
state.hidden.add('a');
const count = made.length;
context.addVariable('a');
assert.equal(made.length, count);
assert.deepEqual(owners().sort(), ['b', 'c']);
