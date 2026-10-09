// Execute the viewer's real arrow-key code across the scrubbers (D-50) with a small DOM stand-in.
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
const fnSource = (name) => between(`function ${name}(`, '\n}\n') + '\n}';
function element(tag) {
  const classes = new Set();
  return {
    tag, handlers: {}, children: [], hidden: false, style: {}, attributes: {}, value: '',
    classList: {
      add: (v) => classes.add(v), remove: (v) => classes.delete(v),
      toggle: (v, on) => on ? classes.add(v) : classes.delete(v), contains: (v) => classes.has(v),
    },
    addEventListener(type, cb) { this.handlers[type] = cb; },
    setAttribute(name, value) { this.attributes[name] = value; },
    append(...nodes) { this.children.push(...nodes); },
    getBoundingClientRect: () => ({left: 0, right: 0, top: 0, width: 100, height: 20}),
    setPointerCapture() {}, focus() {},
    set innerHTML(v) { this._html = v; if (!v) this.children = []; },
    get innerHTML() { return this._html || ''; },
    set textContent(v) { this._text = String(v); },
    get textContent() { return this._text || ''; },
  };
}
const elements = Object.fromEntries(['scrubber', 'dimstack', 'dimroles', 'dimrows'].map((n) => [n, element('div')]));
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
context.state.vlayers.set('v1', {sources: [{updateDimensions: (d) => updates.push({...d})}]});
context.state.vlayers.set('v2', {sources: [{updateDimensions: (d) => updates.push({...d})}]});
const run = (code) => vm.runInContext(code, context);
run(between('const varOf', 'function dimFromParam('));
run(source('dimFromParam'));
run(between('const MONTHS', '\n\n'));
for (const name of ['styleParams', 'syncUrl', 'h', 'stepPositions', 'dateTicks', 'indexTicks', 'stopPlay', 'setDim',
  'syncRoles', 'setRole', 'buildDimRoles', 'buildScrubber', 'buildTimeline', 'buildDimSlider']) run(source(name));
for (const name of ['scrubKeyDown', 'dimKeyDown']) run(fnSource(name));
// the real document listener must call scrubKeyDown
assert.ok(/document\.addEventListener\('keydown', scrubKeyDown\)/.test(html));

function load(selected, roles) {
  context.state.selected = selected;
  context.state.roles = roles || {};
  context.state.dims = {};
  context.buildScrubber();
  updates.length = 0;
}
const inputs = () => find(elements.dimstack, (e) => e.tag === 'input');
const labels = () => elements.dimstack.children.map((item) => item.children[2].textContent);
const dims = () => ({...context.state.dims});
function key(k, opts = {}) {
  const e = {
    key: k, altKey: false, ctrlKey: false, metaKey: false, shiftKey: false,
    target: {closest: () => null}, prevented: false, stopped: false,
    preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; },
    ...opts,
  };
  return e;
}
const globalKey = (k, opts) => { const e = key(k, opts); context.scrubKeyDown(e); return e; };
// a keydown on vertical slider `i`: its own listener runs; the event only reaches the global handler if not stopped
function sliderKey(i, k, opts) {
  const inp = inputs()[i];
  const e = key(k, {target: {closest: () => inp}, ...opts});
  assert.equal(typeof inp.handlers.keydown, 'function');
  inp.handlers.keydown(e);
  if (!e.stopped) context.scrubKeyDown(e);
  return e;
}

// time horizontal, depth vertical
load(['v1']);
assert.equal(inputs().length, 1);

// Global: Left/Right step the horizontal dim as before and stop the play timer.
context.state.dims.time = 2;
const play = find(elements.scrubber, (e) => e.tag === 'button' && e.attributes['aria-label'] === 'Play')[0];
play.handlers.click();
assert.equal(context.state.timer, 1);
let e = globalKey('ArrowRight');
assert.equal(e.prevented, true);
assert.equal(dims().time, 3);
assert.equal(context.state.timer, null);
e = globalKey('ArrowLeft');
assert.equal(dims().time, 2);
globalKey('ArrowLeft'); globalKey('ArrowLeft'); globalKey('ArrowLeft');
assert.equal(dims().time, 0);  // clamped
globalKey('ArrowRight'); globalKey('ArrowRight'); globalKey('ArrowRight'); globalKey('ArrowRight'); globalKey('ArrowRight');
assert.equal(dims().time, 4);  // clamped
assert.equal(dims().depth, 0);

// Global: Down = index + 1, Up = index - 1, clamped; slider value and label follow; layers and URL updated.
updates.length = 0;
e = globalKey('ArrowUp');
assert.equal(dims().depth, 0);  // clamped at the top
e = globalKey('ArrowDown');
assert.equal(e.prevented, true);
assert.equal(dims().depth, 1);
assert.equal(inputs()[0].value, 1);
assert.equal(labels()[0], '1');
assert.deepEqual(updates.at(-1), {depth: 1});
assert.equal(new URLSearchParams(context.lastUrl.split('?')[1]).get('depth'), '1');
globalKey('ArrowDown'); globalKey('ArrowDown'); globalKey('ArrowDown');
assert.equal(dims().depth, 3);  // clamped at the bottom
globalKey('ArrowUp');
assert.equal(dims().depth, 2);
assert.equal(inputs()[0].value, 2);
assert.equal(dims().time, 4);  // horizontal untouched

// Modifiers are ignored.
for (const mod of ['altKey', 'ctrlKey', 'metaKey', 'shiftKey']) {
  for (const k of ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight']) {
    e = globalKey(k, {[mod]: true});
    assert.equal(e.prevented, false, `${mod} ${k}`);
  }
}
assert.deepEqual(dims(), {time: 4, depth: 2});

// A target inside input/select/textarea/contenteditable is ignored by the global handler.
for (const k of ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight']) {
  e = globalKey(k, {target: {closest: () => ({})}});
  assert.equal(e.prevented, false, k);
}
assert.deepEqual(dims(), {time: 4, depth: 2});
e = globalKey('a');
assert.equal(e.prevented, false);
e = globalKey('PageUp');
assert.equal(e.prevented, false);

// The vertical slider: Up/Down step itself, swallow the key (no native step, no second global step).
e = sliderKey(0, 'ArrowDown');
assert.equal(e.prevented, true);
assert.equal(e.stopped, true);
assert.equal(dims().depth, 3);
assert.equal(inputs()[0].value, 3);
assert.equal(labels()[0], '3');
e = sliderKey(0, 'ArrowDown');  // clamped, still swallowed
assert.equal(e.prevented, true);
assert.equal(dims().depth, 3);
e = sliderKey(0, 'ArrowUp');
assert.equal(dims().depth, 2);
assert.deepEqual(updates.at(-1), {depth: 2});
const before = updates.length;
sliderKey(0, 'ArrowUp');
assert.equal(updates.length, before + 1);  // exactly one step per key press
assert.equal(dims().depth, 1);

// Left/Right on the vertical slider step the horizontal scrubber and stop playing.
play.handlers.click();
assert.equal(context.state.timer, 1);
e = sliderKey(0, 'ArrowLeft');
assert.equal(e.prevented, true);
assert.equal(e.stopped, true);
assert.equal(dims().time, 3);
assert.equal(dims().depth, 1);
assert.equal(context.state.timer, null);
sliderKey(0, 'ArrowRight');
assert.equal(dims().time, 4);

// Home/End/PageUp/PageDown and modified arrows stay native on the slider.
for (const k of ['Home', 'End', 'PageUp', 'PageDown']) {
  e = sliderKey(0, k);
  assert.equal(e.prevented, false, k);
  assert.equal(e.stopped, false, k);
}
for (const mod of ['altKey', 'ctrlKey', 'metaKey', 'shiftKey']) {
  e = sliderKey(0, 'ArrowUp', {[mod]: true});
  assert.equal(e.prevented, false, mod);
  assert.equal(e.stopped, false, mod);
}
assert.deepEqual(dims(), {time: 4, depth: 1});

// No vertical dim: Up/Down are not swallowed and nothing changes; Left/Right still work.
load(['v1'], {time: 'h', depth: 'off'});
assert.equal(inputs().length, 0);
assert.deepEqual([...context.state.vsteps], []);
e = globalKey('ArrowUp');
assert.equal(e.prevented, false);
e = globalKey('ArrowDown');
assert.equal(e.prevented, false);
assert.equal(dims().depth, 0);
e = globalKey('ArrowRight');
assert.equal(e.prevented, true);
assert.equal(dims().time, 1);

// No horizontal dim: global Left/Right not swallowed, Up/Down still step the vertical one;
// on the slider Left/Right are not swallowed either (nothing to step) and the native step is left alone.
load(['v1'], {time: 'v', depth: 'v'});
assert.equal(context.state.scrub, null);
assert.equal(inputs().length, 2);
e = globalKey('ArrowLeft');
assert.equal(e.prevented, false);
e = globalKey('ArrowRight');
assert.equal(e.prevented, false);
e = sliderKey(0, 'ArrowLeft');
assert.equal(e.prevented, false);
assert.equal(e.stopped, false);
e = sliderKey(1, 'ArrowRight');
assert.equal(e.prevented, false);
assert.equal(e.stopped, false);
assert.deepEqual(dims(), {time: 0, depth: 0});

// Several vertical dims: the global handler uses the first (union order), a focused slider its own.
load(['v1', 'v2'], {time: 'off', depth: 'v', band: 'v'});
assert.deepEqual([...context.state.vsteps.map((v) => v.name)], ['depth', 'band']);
assert.equal(context.state.scrub, null);
globalKey('ArrowDown');
assert.deepEqual(dims(), {time: 0, depth: 1, band: 0});
globalKey('ArrowDown');
assert.deepEqual(dims(), {time: 0, depth: 2, band: 0});
e = sliderKey(1, 'ArrowDown');  // band slider, its own dim only
assert.equal(e.stopped, true);
assert.deepEqual(dims(), {time: 0, depth: 2, band: 1});
assert.equal(labels()[1], '1');
assert.equal(labels()[0], '2');
sliderKey(1, 'ArrowDown'); sliderKey(1, 'ArrowDown');
assert.equal(dims().band, 2);  // clamped at size 3
sliderKey(1, 'ArrowUp');
assert.equal(dims().band, 1);
sliderKey(0, 'ArrowUp');
assert.deepEqual(dims(), {time: 0, depth: 1, band: 1});

// Rebuilds leave no stale entries: roles change, selection change, empty selection.
context.state.roles.depth = 'off';
context.buildScrubber();
assert.deepEqual([...context.state.vsteps.map((v) => v.name)], ['band']);
globalKey('ArrowDown');  // first vertical is now band
assert.equal(dims().band, 2);
assert.equal(dims().depth, 1);
context.state.selected = [];
context.buildScrubber();
assert.deepEqual([...context.state.vsteps], []);
e = globalKey('ArrowDown');
assert.equal(e.prevented, false);
