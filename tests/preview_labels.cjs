// Execute the viewer's real label-mode code (D-44) with a small DOM stand-in: labelOf, persistence, in-place relabelling
// of cards, the spine, the info panel and the pointer readout.
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
function between(from, to) {
  const start = html.indexOf(from);
  assert.ok(start >= 0, from);
  const end = html.indexOf(to, start + 1);
  assert.ok(end > start, to);
  return html.slice(start, end);
}
function element() {
  const classes = new Set();
  return {
    handlers: {}, children: [], hidden: false, attributes: {}, value: '', textContent: '', title: '',
    classList: {
      add: (v) => classes.add(v), remove: (v) => classes.delete(v),
      toggle: (v, on) => on ? classes.add(v) : classes.delete(v), contains: (v) => classes.has(v),
    },
    addEventListener(type, cb) { this.handlers[type] = cb; },
    setAttribute(name, value) { this.attributes[name] = value; }, contains: () => false,
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; },
    getBoundingClientRect: () => ({top: 0, height: 100}),
  };
}
const variables = [
  {name: 'a', long_name: '', display_name: 'a', dims: [], units: '', categorical: true},
  {name: 'b', long_name: 'Long $B$', display_name: 'Long B', dims: [], units: '', categorical: true},
  {name: 'c', long_name: '   ', display_name: '   ', dims: [], units: '', categorical: true},
];
const [A, B, C] = variables;

function makeContext(kv = new Map(), extra = {}) {
  const elements = Object.fromEntries(['sec-selected', 'sec-other', 'sel-cards', 'other-cards', 'sel-head',
    'other-label', 'other-toggle', 'readout', 'selhint', 'labelrow', 'labelmode', 'sbspine', 'info-vars'].map((n) => [n, element()]));
  elements.sbspine.firstElementChild = element();
  const store = {get: (k) => kv.has(k) ? kv.get(k) : null, set: (k, v) => kv.set(k, v), remove: (k) => kv.delete(k)};
  const cards = new Map();
  const ctx = vm.createContext({
    store, cards, elements, blank: false,
    state: {selected: [], vlayers: new Map(), open: new Set(), hidden: new Set()},
    cfg: {variables, title: 't', source: 'src'}, ICON: {eye: 'eye', eyeOff: 'eyeOff', gear: 'gear'}, otherCollapsed: false,
    draggedVariable: null, $: (id) => elements[id], varOf: (name) => variables.find((v) => v.name === name),
    selVars: () => ctx.state.selected.map((n) => variables.find((v) => v.name === n)),
    h(tag, cls, text) { const el = element(); el.tag = tag; el.className = cls; if (text !== undefined) el.textContent = text; return el; },
    document: {createTextNode: (text) => ({text, textContent: text})},
    unitsOf: () => '', opacityOf: () => 1, buildLegend: () => element(), buildStyleControls: () => null,
    toggleVar() {}, toggleVisibility() {}, moveVariable() {}, syncUrl() {}, clearDragFeedback() {}, closeVarInfo() {},
    ...extra,
  });
  vm.runInContext(between('const LABEL_KEY', '\nfunction labelOf'), ctx);
  for (const name of ['labelOf', 'setLabelMode', 'applyLabels', 'initLabelMode', 'updateSpine', 'refreshCards', 'buildCards',
    'initReadout']) {
    vm.runInContext(source(name), ctx);
  }
  ctx.run = (code) => vm.runInContext(code, ctx);
  ctx.run('buildCards()');
  return ctx;
}

// labelOf: array name by default; long name (LaTeX-cleaned display_name) only in long mode and only when non-empty.
let ctx = makeContext();
assert.equal(ctx.run('labelMode'), 'name');
for (const v of variables) assert.equal(ctx.run('labelOf')(v), v.name);
ctx.run("labelMode = 'long'");
assert.equal(ctx.run('labelOf')(A), 'a');            // empty long_name: array name
assert.equal(ctx.run('labelOf')(B), 'Long B');       // display_name, not the raw LaTeX long_name
assert.equal(ctx.run('labelOf')(C), 'c');            // whitespace-only long_name: array name
assert.equal(ctx.run('labelOf')({name: 'd'}), 'd');  // no long_name key at all
assert.equal(ctx.run('labelOf')({name: 'e', long_name: 'Raw E'}), 'Raw E');  // no display_name: raw long_name

// Persistence: default 'name', stored 'long' restored, junk ignored; storage failure keeps the choice in memory.
assert.equal(makeContext(new Map([['gpm-preview:label-mode', 'long']])).run('labelMode'), 'long');
assert.equal(makeContext(new Map([['gpm-preview:label-mode', 'name']])).run('labelMode'), 'name');
assert.equal(makeContext(new Map([['gpm-preview:label-mode', 'bogus']])).run('labelMode'), 'name');
const kv = new Map();
ctx = makeContext(kv);
ctx.run("setLabelMode('long')");
assert.equal(kv.get('gpm-preview:label-mode'), 'long');
ctx.run("setLabelMode('name')");
assert.equal(kv.get('gpm-preview:label-mode'), 'name');
ctx = makeContext(new Map(), {store: {get: () => null, set() { /* swallowed like the real store */ }, remove() {}}});
assert.equal(ctx.run('labelMode'), 'name');
ctx.run("setLabelMode('long')");
assert.equal(ctx.run('labelMode'), 'long');
assert.equal(ctx.run('labelOf')(B), 'Long B');

// The select reflects the mode, drives it, and is hidden in the blank viewer.
const kv2 = new Map([['gpm-preview:label-mode', 'long']]);
ctx = makeContext(kv2);
ctx.run('initLabelMode()');
assert.equal(ctx.elements.labelmode.value, 'long');
assert.equal(ctx.elements.labelrow.hidden, false);
ctx.elements.labelmode.value = 'name';
ctx.elements.labelmode.handlers.change();
assert.equal(ctx.run('labelMode'), 'name');
assert.equal(kv2.get('gpm-preview:label-mode'), 'name');
ctx.blank = true;
ctx.run('initLabelMode()');
assert.equal(ctx.elements.labelrow.hidden, true);

// Cards, spine, info panel and readout relabel in place when the mode flips (no rebuild: same nodes).
ctx = makeContext();
ctx.state.selected = ['a', 'b'];
ctx.run('refreshCards()');
ctx.run('updateSpine()');
const card = (n) => ctx.cards.get(n);
const nodes = ['a', 'b', 'c'].map((n) => card(n).el);
const longLine = (n) => card(n).longname;
assert.equal(card('b').title.textContent, 'b');
assert.equal(card('b').handle.attributes['aria-label'], 'Drag b');
assert.equal(card('b').remove.attributes['aria-label'], 'Remove b from Selected');
assert.equal(card('b').visibility.attributes['aria-label'], 'Hide b');
assert.equal(longLine('b').textContent, 'Long B');  // Array name mode: the settings show the long name
assert.equal(longLine('a'), null);                  // no long_name: no line
assert.equal(longLine('c'), null);
assert.equal(ctx.elements.sbspine.firstElementChild.children.at(-1).textContent, 'a, b');
ctx.run("setLabelMode('long')");
assert.equal(card('b').title.textContent, 'Long B');
assert.equal(card('a').title.textContent, 'a');
assert.equal(card('c').title.textContent, 'c');
assert.equal(card('b').handle.attributes['aria-label'], 'Drag Long B');
assert.equal(card('b').remove.attributes['aria-label'], 'Remove Long B from Selected');
assert.equal(card('b').visibility.attributes['aria-label'], 'Hide Long B');
assert.equal(card('b').visibility.title, 'Hide Long B');
assert.equal(longLine('b').textContent, 'b');  // Long name mode: the settings line shows the array name instead
assert.equal(ctx.elements.sbspine.firstElementChild.children.at(-1).textContent, 'a, Long B');
assert.ok(ctx.elements.sbspine.title.endsWith('a, Long B'));
assert.equal(ctx.elements['info-vars'].textContent, 'a, Long B, c');
assert.deepEqual(['a', 'b', 'c'].map((n) => card(n).el), nodes);
ctx.run("setLabelMode('name')");
assert.equal(card('b').title.textContent, 'b');
assert.equal(longLine('b').textContent, 'Long B');
assert.equal(ctx.elements.sbspine.firstElementChild.children.at(-1).textContent, 'a, b');
assert.equal(ctx.elements['info-vars'].textContent, 'a, b, c');

// Pointer readout lines follow the mode.
let moved = null;
ctx.sampleLayer = () => 7;
ctx.coordLine = () => '';
ctx.state.vlayers.set('b', {layers: [{}], sources: [{}]});
ctx.state.vlayers.set('a', {layers: [{}], sources: [{}]});
ctx.run('initReadout')({on: (type, cb) => { if (type === 'pointermove') moved = cb; }, getViewport: () => ({addEventListener() {}})});
Object.assign(ctx.elements.readout, {offsetWidth: 10, offsetHeight: 10, style: {}});
ctx.innerWidth = 1000; ctx.innerHeight = 1000;
const move = () => moved({dragging: false, coordinate: [0, 0], originalEvent: {clientX: 0, clientY: 0}});
const lines = () => ctx.elements.readout.children.map((e) => e.textContent);
move();
assert.deepEqual(lines(), ['b: 7', 'a: 7']);
ctx.run("setLabelMode('long')");
move();
assert.deepEqual(lines(), ['Long B: 7', 'a: 7']);
console.log('ok');
