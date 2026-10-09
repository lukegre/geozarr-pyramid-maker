// Execute the viewer's real "Auto range on add" code (D-53): the trigger, the card's Auto/apply, the explicit-range tracking.
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

// ---- stand-ins
const kv = new Map();
const store = {get: (k) => kv.has(k) ? kv.get(k) : null, set: (k, v) => kv.set(k, v), remove: (k) => kv.delete(k)};
const fake = (tag, cls, text) => {
  const el = {tag, text, children: [], handlers: {}, attrs: {}, style: {}, value: '', checked: false,
    classList: {add() {}, remove() {}},
    append(...n) { this.children.push(...n); },
    addEventListener(type, fn) { this.handlers[type] = fn; },
    setAttribute(k, v) { this.attrs[k] = v; },
    replaceWith(n) { n.replaced = true; }};
  all.push(el);
  return el;
};
const all = [];
let urlSyncs = 0;
const styled = [];
const pending = [];  // once('rendercomplete') listeners
const map = {once: (type, fn) => { assert.equal(type, 'rendercomplete'); pending.push(fn); }};
const state = {vlayers: new Map(), selected: [], hidden: new Set(), map};
const variables = [
  {name: 'a', dims: [], categorical: false, vmin: 0, vmax: 1},
  {name: 'b', dims: [], categorical: false, vmin: 0, vmax: 1},
  {name: 'attr', dims: [], categorical: false, vmin: 0, vmax: 1, limits_from_attrs: true},
  {name: 'cat', dims: [], categorical: true, vmin: 0, vmax: 3},
];
const params = new URLSearchParams();
const context = vm.createContext({
  cfg: {variables, title: 't', colormaps: data.colormaps, ramp: data.colormaps.viridis, dims: {}},
  store, state, console: {warn() {}}, setTimeout, params, blank: false, ROLES: [], dimFromParam: () => null,
  cmapGroups: [{label: 'Sequential', names: ['viridis']}, {label: 'Diverging', names: ['RdBu', 'coolwarm']}],
  cmapFills: new Set(), fillCmapSelect() {}, stepSelect() {}, reverseKey() {}, stopsOf: () => [[0, 0, 0], [1, 1, 1]],
  gradientCss: () => 'grad', buildLegend: () => fake('legend'), colorExpr: (v) => `expr:${v.name}`,
  syncUrl: () => { urlSyncs++; }, h: fake, varOf: (n) => variables.find((v) => v.name === n),
  cards: new Map(), autoOnAddHook: null,
});
const run = (code) => vm.runInContext(code, context);
run('const cmaps = cfg.colormaps;');
run(between('const styleKey', 'const stopsOf'));  // styleKey, resolveCmap, defaultStyle, explicit, styles, loadStyle, styleOf
run(between('const AUTO_TITLE', '\nfunction refreshCards'));  // Auto helpers, buildStyleControls
run(between('// Auto range on add (D-53)', "// Add or remove one variable's layer group"));  // autoOnAdd
const explicit = run('explicit'), styleOf = run('styleOf');
const key = (n) => `gpm-preview:style:t:${n}`;

// Fake loaded tiles for frameValues: variable `name` shows `values` (one tile, key K).
const K = 'k';
const show = (name, values) => {
  const tile = {key: K, tileCoord: [0, 0, 0], getState: () => 2, getSize: () => [values.length, 1],
    getData: () => Float32Array.from(values)};
  const layer = {getRenderer: () => ({tileRepresentationCache: new Map([['x', {loaded: true, gutter: 0, tile}]])}),
    setStyle: (s) => styled.push([name, s.color])};
  state.vlayers.set(name, {layers: [layer], sources: [{getKey: () => K}]});
};
const ramp = Array.from({length: 101}, (_, i) => i);          // 0..100: 2-98 %
const signed = Array.from({length: 101}, (_, i) => i - 50);   // balanced signs: symmetric
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

// ---- a card per variable, built with the real buildStyleControls
const card = (name) => {
  const entry = {legend: {replaceWith() {}}, sync: null};
  const before = all.length;
  const opts = fake('opts');
  entry.sync = run('buildStyleControls')(variables.find((v) => v.name === name), opts, entry);
  const mine = all.slice(before);
  const labelled = (tag, label) => mine.find((e) => e.tag === tag && e.attrs['aria-label'] === label);
  entry.btn = (label) => mine.find((e) => e.tag === 'button' && e.text === label);
  entry.min = labelled('input', 'Min'); entry.max = labelled('input', 'Max');
  entry.sel = labelled('select', 'Colourmap');
  entry.rev = mine.find((e) => e.tag === 'input' && e.type === 'checkbox');
  context.cards.set(name, entry);
  return entry;
};
const [a, b] = ['a', 'b'].map(card);

// ---- explicit tracking
assert.equal(explicit.has('a'), false);  // default style (loaded when the card was built): not explicit
kv.set(key('b'), JSON.stringify({cmap: 'viridis', reversed: false, vmin: 3, vmax: 4}));
run('delete styles.b');
assert.equal(styleOf(variables[1]).vmin, 3);
assert.equal(explicit.has('b'), true);   // loaded from localStorage
kv.set(key('b'), '{broken');
run('delete styles.b'); explicit.delete('b');
styleOf(variables[1]);
assert.equal(explicit.has('b'), false);  // corrupt entry: defaults
kv.delete(key('b'));

// ---- card.auto: the automatic Auto (no persistence, not explicit), through entry.auto()
assert.equal(typeof a.auto, 'function');
state.vlayers.clear();
assert.equal(a.auto(), false);  // nothing loaded: style kept
assert.deepEqual({...styleOf(variables[0])}, {cmap: 'viridis', reversed: false, vmin: 0, vmax: 1});
show('a', ramp);
const syncs = urlSyncs;
assert.equal(a.auto(), true);
near(styleOf(variables[0]).vmin, 2); near(styleOf(variables[0]).vmax, 98);
assert.equal(styleOf(variables[0]).cmap, 'viridis');
assert.equal(kv.has(key('a')), false, 'automatic Auto must not be stored');
assert.equal(explicit.has('a'), false, 'automatic Auto must not mark explicit');
assert.equal(urlSyncs, syncs + 1);                              // shows up in the URL as before
assert.deepEqual(styled.at(-1), ['a', 'expr:a']);               // layer restyled
assert.equal(a.min.value, '2');                                 // card inputs follow
show('a', signed);  // balanced signs: same symmetric / diverging switch as the button
assert.equal(a.auto(), true);
assert.deepEqual({...styleOf(variables[0])}, {cmap: 'RdBu', reversed: true, vmin: -48, vmax: 48});
assert.equal(kv.has(key('a')), false);
assert.equal(explicit.has('a'), false);

// ---- the Auto button: identical result, but a user edit (stored, explicit)
run('delete styles.a');
show('a', ramp);
styleOf(variables[0]);
a.btn('Auto').handlers.click();
near(styleOf(variables[0]).vmin, 2);
assert.equal(kv.has(key('a')), true);
assert.equal(explicit.has('a'), true);
// other edits: range inputs, colourmap, Reversed, Min/Max, Symmetric
for (const [n, edit] of [
  ['range', (c) => { c.min.value = '1'; c.max.value = '9'; c.min.handlers.change(); }],
  ['cmap', (c) => { c.sel.value = 'RdBu'; c.sel.handlers.change(); }],
  ['rev', (c) => { c.rev.checked = true; c.rev.handlers.change(); }],
  ['minmax', (c) => c.btn('Min/Max').handlers.click()],
  ['sym', (c) => c.btn('Symmetric').handlers.click()],
]) {
  run('delete styles.b'); explicit.delete('b'); kv.delete(key('b'));
  show('b', ramp);
  styleOf(variables[1]);
  edit(b);
  assert.equal(explicit.has('b'), true, n);
  assert.equal(kv.has(key('b')), true, n);
}
// an invalid range edit changes nothing
run('delete styles.b'); explicit.delete('b'); kv.delete(key('b'));
b.min.value = '5'; b.max.value = '1'; b.min.handlers.change();
assert.equal(explicit.has('b'), false);
assert.equal(kv.has(key('b')), false);
// Auto with no data: nothing explicit
state.vlayers.clear();
b.btn('Auto').handlers.click();
assert.equal(explicit.has('b'), false);

// ---- URL parameters make a range explicit
const applyUrl = (query) => {
  for (const k of [...params.keys()]) params.delete(k);
  for (const [k, v] of new URLSearchParams(query)) params.append(k, v);
  run('delete styles.a; delete styles.b'); explicit.clear(); kv.clear();
  state.selected = []; state.hidden = new Set(); state.opacity = {}; state.roles = {}; state.dims = {};
  run(between('(function applyUrlState()', '\nlet urlTimer'));
};
applyUrl('var=a,b&vmin.a=1&vmax.a=5');
assert.deepEqual([...explicit], ['a']);
applyUrl('var=a,b&cmap.b=coolwarm');
assert.deepEqual([...explicit], ['b']);
applyUrl('var=a&rev=1');
assert.deepEqual([...explicit], ['a']);
applyUrl('var=a&vmin=0.5');
assert.deepEqual([...explicit], ['a']);
applyUrl('var=a,b&opacity.a=0.5');  // no style parameter: not explicit
assert.equal(explicit.size, 0);
applyUrl('var=a&vmin=5&vmax=1');    // invalid range is ignored
assert.equal(explicit.size, 0);
applyUrl('var=a');
assert.equal(explicit.size, 0);

// ---- autoOnAdd: waits for rendercomplete, then runs the card's Auto
const calls = [];
const stub = (name, results) => {  // card whose auto() returns the queued results
  context.cards.set(name, {auto: () => { calls.push(name); return results.length ? results.shift() : false; }});
};
const fire = () => pending.splice(0).forEach((fn) => fn());
const reset = () => {
  calls.length = 0; pending.length = 0; explicit.clear(); run('autoPending.clear()');
  state.selected = ['a', 'b', 'attr', 'cat']; state.hidden = new Set();
};
const addVar = (n) => run(`autoOnAdd(${JSON.stringify(n)})`);

reset(); stub('a', [true]);
addVar('a');
assert.equal(pending.length, 1);
assert.deepEqual(calls, []);  // nothing before the map has rendered
fire();
assert.deepEqual(calls, ['a']);
assert.equal(pending.length, 0);  // applied: done
stub('a', [true]); addVar('a'); fire();  // adding again runs it again
assert.deepEqual(calls, ['a', 'a']);

// not for explicit ranges: stored / URL / user edit (explicit), attrs limits, categorical
reset(); stub('a', [true]); explicit.add('a');
addVar('a'); assert.equal(pending.length, 0);
stub('attr', [true]); addVar('attr'); assert.equal(pending.length, 0);
context.cards.set('cat', {}); addVar('cat'); assert.equal(pending.length, 0);
assert.deepEqual(calls, []);

// retries: three empty renders, then silence
reset(); stub('a', []);
addVar('a');
fire(); fire(); fire();
assert.deepEqual(calls, ['a', 'a', 'a']);
assert.equal(pending.length, 0);
fire(); assert.equal(calls.length, 3);
addVar('a'); assert.equal(pending.length, 1);  // the pending flag was released
// an empty render followed by data
reset(); stub('a', [false, true]);
addVar('a'); fire(); assert.equal(pending.length, 1); fire();
assert.deepEqual(calls, ['a', 'a']);
assert.equal(pending.length, 0);

// one trigger per variable, variables independent
reset(); stub('a', [true]); stub('b', [true]);
addVar('a'); addVar('a'); addVar('b');
assert.equal(pending.length, 2);
fire();
assert.deepEqual(calls.sort(), ['a', 'b']);

// skipped when removed, hidden or edited in the meantime
for (const [n, change] of [
  ['removed', () => { state.selected = state.selected.filter((x) => x !== 'a'); }],
  ['hidden', () => state.hidden.add('a')],
  ['edited', () => explicit.add('a')],
]) {
  reset(); stub('a', [true]);
  addVar('a');
  change();
  fire();
  assert.deepEqual(calls, [], n);
  assert.equal(pending.length, 0, n);
}
// a retry is also skipped when the variable changes between renders
reset(); stub('a', []);
addVar('a'); fire(); explicit.add('a'); fire();
assert.deepEqual(calls, ['a']);
assert.equal(pending.length, 0);

console.log('ok');
