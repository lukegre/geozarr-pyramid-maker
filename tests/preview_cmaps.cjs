// Execute the viewer's real colourmap option code (D-42) with a small DOM stand-in.
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
function element(tag) {
  const handlers = {};
  return {
    tag, handlers, children: [], hidden: false, checked: false, value: '', label: '',
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; },
    addEventListener(type, cb) { handlers[type] = cb; },
  };
}
const elements = {cmapmore: element('div'), 'cmap-cmocean': element('input'), 'cmap-cmasher': element('input')};
elements.cmapmore.hidden = true;
const storage = new Map();
const failing = {get: () => { throw new Error('denied'); }, set: () => { throw new Error('denied'); }};
function makeContext(extra = {}, kv = storage, blank = false) {
  const store = {get: (k) => kv.has(k) ? kv.get(k) : null, set: (k, v) => kv.set(k, v), remove: (k) => kv.delete(k)};
  return vm.createContext({
    cfg: {colormaps: data.colormaps, colormap_groups: data.groups, ramp: data.colormaps.viridis, title: 't'},
    blank, store, $: (id) => elements[id],
    document: {createElement: (tag) => element(tag)},
    Option: function (text, value) { return {tag: 'option', textContent: text, value}; },
    ...extra,
  });
}
function load(context) {
  vm.runInContext('const cmaps = cfg.colormaps;', context);
  vm.runInContext(between('const CMAP_EXTRA_KEYS', '\ninitCmapToggle();') + '\ninitCmapToggle();', context);
  return (code) => vm.runInContext(code, context);
}
const optionsOf = (sel) => sel.children.flatMap((og) => og.children.map((o) => [og.label, o.value, o.textContent]));
const labelsOf = (sel) => sel.children.map((og) => og.label);
const values = (sel) => optionsOf(sel).map((o) => o[1]);

// Default: both checkboxes off, only the matplotlib groups are listed, control visible with a store.
let run = load(makeContext());
assert.equal(elements.cmapmore.hidden, false);
assert.equal(elements['cmap-cmocean'].checked, false);
assert.equal(elements['cmap-cmasher'].checked, false);
const sel = element('select');
run('cmapFills.clear()');
const fill = () => run('fillCmapSelect')(sel, 'viridis');
fill();
assert.deepEqual(labelsOf(sel), ['Perceptually uniform sequential', 'Sequential', 'Sequential (2)', 'Diverging',
  'Cyclic', 'Miscellaneous']);
assert.ok(!values(sel).some((v) => /^cm[or]\./.test(v)));
assert.equal(sel.value, 'viridis');
for (const name of ['bone', 'Spectral', 'berlin', 'managua', 'RdYlBu', 'twilight', 'twilight_shifted', 'gray', 'terrain']) {
  assert.ok(values(sel).includes(name), name);
}

// A card whose current map is in a hidden group keeps exactly that option, with the prefix stripped from the text.
run('fillCmapSelect')(sel, 'cmo.thermal');
assert.equal(sel.value, 'cmo.thermal');
assert.deepEqual(optionsOf(sel).filter((o) => o[0] === 'cmocean'), [['cmocean', 'cmo.thermal', 'thermal']]);
assert.equal(labelsOf(sel).includes('cmasher'), false);

// Toggling rebuilds every registered select and keeps each current value.
const a = element('select'), b = element('select');
const cur = {a: 'cmr.ember', b: 'viridis'};
run('cmapFills').add(() => run('fillCmapSelect')(a, cur.a));
run('cmapFills').add(() => run('fillCmapSelect')(b, cur.b));
run('cmapFills').forEach((f) => f());
assert.deepEqual(optionsOf(a).filter((o) => o[0] === 'cmasher'), [['cmasher', 'cmr.ember', 'ember']]);
elements['cmap-cmasher'].checked = true;
elements['cmap-cmasher'].handlers.change();
assert.equal(storage.get('gpm-preview:cmaps-cmasher'), '1');
assert.equal(storage.has('gpm-preview:cmaps-cmocean'), false);
const cmasher = data.groups.find((g) => g.source === 'cmasher').names;
assert.deepEqual(a.children.find((og) => og.label === 'cmasher').children.map((o) => o.value), cmasher);
assert.equal(a.value, 'cmr.ember');
assert.ok(b.children.some((og) => og.label === 'cmasher') && !b.children.some((og) => og.label === 'cmocean'));
assert.equal(b.children.find((og) => og.label === 'cmasher').children[0].textContent, cmasher[0].slice(4));
elements['cmap-cmocean'].checked = true;
elements['cmap-cmocean'].handlers.change();
assert.equal(storage.get('gpm-preview:cmaps-cmocean'), '1');
assert.deepEqual(labelsOf(b).slice(-2), ['cmocean', 'cmasher']);
assert.equal(b.value, 'viridis');

// Switching off again: the card keeps its current map, the others disappear.
elements['cmap-cmasher'].checked = false;
elements['cmap-cmasher'].handlers.change();
assert.equal(storage.get('gpm-preview:cmaps-cmasher'), '0');
assert.deepEqual(optionsOf(a).filter((o) => o[0] === 'cmasher').map((o) => o[1]), ['cmr.ember']);
assert.equal(b.children.some((og) => og.label === 'cmasher'), false);

// Persisted state is restored on load; storage failures fall back to memory.
run = load(makeContext({}, new Map([['gpm-preview:cmaps-cmocean', '1']])));
assert.equal(elements['cmap-cmocean'].checked, true);
assert.equal(elements['cmap-cmasher'].checked, false);
run = load(makeContext({store: {get: () => null, set() { throw new Error('denied'); }}}));
assert.equal(elements['cmap-cmocean'].checked, false);

// Blank viewer: no store, no control.
load(makeContext({}, new Map(), true));
assert.equal(elements.cmapmore.hidden, true);

// A stored style with a prefixed map resolves although its group is hidden.
const ctx = makeContext({}, new Map([['gpm-preview:style:t:v',
  JSON.stringify({cmap: 'cmo.thermal', reversed: true, vmin: 0, vmax: 1})]]));
run = load(ctx);
vm.runInContext(between('const styleKey', '\nconst styleOf') + '\nconst styleOf = (v) => loadStyle(v);', ctx);
vm.runInContext('const stopsOf = (st) => { const s = cmaps[st.cmap] || cfg.ramp; return st.reversed ? [...s].reverse() : s; };', ctx);
const st = run('styleOf({name: "v", vmin: 0, vmax: 1})');
assert.equal(st.cmap, 'cmo.thermal');
assert.equal(JSON.stringify(run('stopsOf')(st)), JSON.stringify([...data.colormaps['cmo.thermal']].reverse()));

// Fallback without colormap_groups: the old Sequential / Diverging split.
const old = makeContext();
old.cfg = {colormaps: {viridis: [[0, 0, 0]], RdBu_r: [[1, 1, 1]], BrBG: [[2, 2, 2]]}, title: 't'};
run = load(old);
const fb = element('select');
run('fillCmapSelect')(fb, 'viridis');
assert.deepEqual(optionsOf(fb).map((o) => o.join(':')), ['Sequential:viridis:viridis', 'Diverging:RdBu_r:RdBu_r', 'Diverging:BrBG:BrBG']);
console.log('ok');
