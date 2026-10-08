// Execute the viewer's real slider-assignment code (D-40) with a small DOM stand-in.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
function between(from, to) {
  const start = html.indexOf(from);
  assert.ok(start >= 0, from);
  const end = html.indexOf(to, start + 1);
  assert.ok(end > start, to);
  return html.slice(start, end);
}
const source = (name) => between(`function ${name}(`, '\nfunction ');
function element(tag) {
  const classes = new Set();
  const el = {
    tag, handlers: {}, children: [], hidden: false, style: {}, attributes: {}, value: '',
    classList: {
      add: (v) => classes.add(v), remove: (v) => classes.delete(v),
      toggle: (v, on) => on ? classes.add(v) : classes.delete(v), contains: (v) => classes.has(v),
    },
    addEventListener(type, cb) { this.handlers[type] = cb; },
    setAttribute(name, value) { this.attributes[name] = value; },
    append(...nodes) { this.children.push(...nodes); },
    getBoundingClientRect: () => ({left: 0, right: 0, top: 0, width: 100, height: 20}),
    setPointerCapture() {}, focus() { focused = this; },
    set innerHTML(v) { this._html = v; if (!v) this.children = []; },
    get innerHTML() { return this._html || ''; },
  };
  return el;
}
let focused = null;
const elements = Object.fromEntries(['scrubber', 'dimstack', 'dimroles', 'dimrows'].map((n) => [n, element('div')]));
elements.dimroles.hidden = true;
const body = element('body');
const walk = (el) => [el, ...el.children.flatMap(walk)];
const find = (el, pred) => walk(el).filter(pred);

const dates = (n) => Array.from({length: n}, (_, i) => `2020-01-0${i + 1}T00:00:00Z`);
const cfg = {
  source: 's', global: false,
  dims: {time: {size: 5, iso: dates(5)}, depth: {size: 4}, band: {size: 3}},
  variables: [
    {name: 'v1', dims: ['time', 'depth'], categorical: true},
    {name: 'v2', dims: ['band'], categorical: true},
  ],
};
const updates = [];
const context = vm.createContext({
  cfg, blank: false, params: new URLSearchParams(''), URLSearchParams,
  state: {selected: ['v1'], dims: {}, roles: {}, opacity: {}, vlayers: new Map(), map: null, scrub: null,
    scrubKey: null, timer: null, open: new Set(), hidden: new Set(), centerLon: 0},
  $: (id) => elements[id],
  document: {createElement: (tag) => element(tag), body, addEventListener() {}},
  window: {addEventListener() {}},
  setTimeout: (fn) => fn(), clearTimeout() {},
  setInterval: () => 1, clearInterval() {},
  history: {replaceState(_, __, url) { context.lastUrl = url; }}, location: {pathname: '/'},
  lastUrl: '',
});
context.state.vlayers.set('v1', {sources: [{updateDimensions: (d) => updates.push(d)}]});
const run = (code) => vm.runInContext(code, context);
run(between('const varOf', 'function dimFromParam('));  // varOf, unionDims, ROLES, defaultRoles, dimToParam
run(source('dimFromParam'));
run(between('const MONTHS', '\n\n'));
for (const name of ['styleParams', 'syncUrl', 'h', 'stepPositions', 'dateTicks', 'indexTicks', 'stopPlay', 'setDim',
  'syncRoles', 'setRole', 'buildDimRoles', 'buildScrubber', 'buildTimeline', 'buildDimSlider']) run(source(name));
const loader = between('(function applyUrlState() {', '})();\n') + '})();';
function load(query, selected = ['v1']) {
  context.state.selected = selected;
  context.state.roles = {};
  context.state.dims = {};
  context.params = new URLSearchParams(query);
  run(loader);
  context.buildScrubber();
}
const roles = () => run('state.roles');
const rowNames = () => elements.dimrows.children.map((row) => row.children[0].textContent);
const rowSelects = () => elements.dimrows.children.map((row) => row.children[1]);
const rowValues = () => rowSelects().map((s) => s.value);
const stackLabels = () => elements.dimstack.children.map((item) => item.children[0].textContent);
const handleLabel = () => find(elements.scrubber, (e) => e.attributes.role === 'slider')[0]?.attributes['aria-label'];
function choose(dim, role) {
  const sel = rowSelects()[rowNames().indexOf(dim)];
  sel.value = role;
  sel.handlers.change();
}
const urlParams = () => { context.syncUrl(); return new URLSearchParams(context.lastUrl.split('?')[1]); };
const sliderParams = () => Object.fromEntries([...urlParams()].filter(([k]) => k.startsWith('slider.')));

// Markup of the Dimensions block in the sidebar footer.
assert.ok(html.indexOf('<select id="basemap">') < html.indexOf('id="dimroles"'));
assert.ok(html.indexOf('id="dimroles"') < html.indexOf('id="lonrow"'));
assert.ok(/id="dimroles" hidden/.test(html));

// Default: first dim horizontal, the rest vertical; no slider.* params.
load('');
assert.equal(elements.dimroles.hidden, false);
assert.deepEqual(rowNames(), ['time', 'depth']);
assert.deepEqual(rowValues(), ['h', 'v']);
assert.deepEqual(Array.from(rowSelects()[0].children.map((o) => [o.value, o.textContent])).map((p) => p.join(':')),
  ['h:Horizontal', 'v:Vertical', 'off:Off']);
assert.equal(elements.scrubber.hidden, false);
assert.equal(handleLabel(), 'time');
assert.equal(elements.dimstack.hidden, false);
assert.deepEqual(stackLabels(), ['depth']);
assert.equal(body.classList.contains('has-scrub'), true);
assert.deepEqual(sliderParams(), {});

// Horizontal moves the previous horizontal dim to vertical; the play timer stops.
const play = find(elements.scrubber, (e) => e.tag === 'button' && e.attributes['aria-label'] === 'Play')[0];
play.handlers.click();
assert.equal(context.state.timer, 1);
choose('depth', 'h');
assert.equal(context.state.timer, null);
assert.deepEqual(rowValues(), ['v', 'h']);
assert.equal(handleLabel(), 'depth');
assert.deepEqual(stackLabels(), ['time']);
assert.deepEqual(sliderParams(), {'slider.time': 'v', 'slider.depth': 'h'});
assert.equal(focused, rowSelects()[1]);  // the rebuilt select keeps keyboard focus

// Off hides that slider but keeps its index applied.
find(elements.dimstack, (e) => e.tag === 'input')[0].value = 3;
find(elements.dimstack, (e) => e.tag === 'input')[0].handlers.input();
assert.deepEqual({...updates.at(-1)}, {time: 3});
choose('time', 'off');
assert.deepEqual(rowValues(), ['off', 'h']);
assert.equal(elements.dimstack.hidden, true);
assert.equal(handleLabel(), 'depth');
assert.equal(context.state.dims.time, 3);
assert.deepEqual(sliderParams(), {'slider.time': 'off', 'slider.depth': 'h'});
assert.equal(urlParams().get('time'), '2020-01-04');

// No horizontal dim: the scrubber and its reserved space go away; the dimension block stays.
choose('depth', 'off');
assert.equal(elements.scrubber.hidden, true);
assert.equal(elements.dimstack.hidden, true);
assert.equal(body.classList.contains('has-scrub'), false);
assert.equal(elements.dimroles.hidden, false);
assert.equal(handleLabel(), undefined);
choose('depth', 'v');
assert.equal(elements.scrubber.hidden, true);
assert.deepEqual(stackLabels(), ['depth']);
assert.equal(elements.dimstack.hidden, false);

// Changing the selection keeps assignments; a new dim is horizontal only when nobody holds it.
context.state.selected = ['v1', 'v2'];
context.buildScrubber();
assert.deepEqual(rowNames(), ['time', 'depth', 'band']);
assert.deepEqual(rowValues(), ['off', 'v', 'h']);
assert.equal(handleLabel(), 'band');
choose('time', 'h');
assert.deepEqual(rowValues(), ['h', 'v', 'v']);
context.state.selected = ['v1'];
context.buildScrubber();
context.state.selected = ['v1', 'v2'];
context.buildScrubber();
assert.deepEqual(rowValues(), ['h', 'v', 'v']);  // band re-enters while time holds horizontal
context.state.selected = [];
context.buildScrubber();
assert.equal(elements.dimroles.hidden, true);
assert.equal(elements.scrubber.hidden, true);
assert.equal(elements.dimstack.hidden, true);

// URL: roles override the defaults; unknown values/dims are ignored; plain dim params still work.
load('slider.depth=h&slider.time=off&slider.band=bogus&slider.nope=h&time=2020-01-03');
assert.deepEqual(rowValues(), ['off', 'h']);
assert.equal(handleLabel(), 'depth');
assert.equal(elements.dimstack.hidden, true);
assert.equal(context.state.dims.time, 2);
assert.deepEqual(sliderParams(), {'slider.time': 'off', 'slider.depth': 'h'});
load('slider.depth=v&slider.time=v');
assert.deepEqual(rowValues(), ['v', 'v']);
assert.equal(elements.scrubber.hidden, true);
load('slider.time=h&slider.depth=h');  // two horizontals: the first dim keeps it
assert.deepEqual(rowValues(), ['h', 'v']);
assert.deepEqual(sliderParams(), {});
load('slider.time=h&slider.depth=v', ['v1', 'v2']);  // explicit default roles are not written back
assert.deepEqual(rowValues(), ['h', 'v', 'v']);
assert.deepEqual(sliderParams(), {});
