// Execute the viewer's real selection and drag handlers with a small DOM/map stand-in.
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
function element() {
  const classes = new Set();
  return {
    handlers: {}, children: [], hidden: false,
    classList: {
      add: (v) => classes.add(v), remove: (v) => classes.delete(v),
      toggle: (v, on) => on ? classes.add(v) : classes.delete(v),
      contains: (v) => classes.has(v),
    },
    addEventListener(type, cb) { this.handlers[type] = cb; },
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; }, contains: () => false,
    append(...nodes) {
      for (const node of nodes) {
        if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1);
        node.parent = this;
        this.children.push(node);
      }
    },
    getBoundingClientRect: () => ({top: 0, height: 100}),
  };
}
const elements = Object.fromEntries([
  'sec-selected', 'sec-other', 'sel-cards', 'other-cards', 'sel-head',
  'other-label', 'other-toggle', 'readout', 'selhint',
].map((name) => [name, element()]));
const cards = new Map(['a', 'b', 'c'].map((name) => [name, {
  el: element(), gear: element(), visibility: element(),
}]));
const base = {name: 'basemap'};
const layers = [base];
const collection = {
  remove(layer) { const i = layers.indexOf(layer); if (i >= 0) layers.splice(i, 1); },
  push(layer) { layers.push(layer); },
};
const variables = ['a', 'b', 'c'].map((name) => ({name, dims: [], categorical: true}));
variables[0].long_name = '';
variables[1].long_name = 'Long B';
variables[1].display_name = 'Readable B';
let created = 0;
const context = vm.createContext({
  state: {selected: [], vlayers: new Map(), open: new Set(), hidden: new Set(), scrubKey: ''},
  cfg: {variables}, cards, ICON: {eye: 'eye', eyeOff: 'eyeOff', gear: 'gear'}, otherCollapsed: true, draggedVariable: null,
  $: (id) => elements[id], varOf: (name) => variables.find((v) => v.name === name),
  makeLayers: (v) => {
    created++;
    return {layers: [0, 1, 2].map((part) => ({name: v.name, part,
      visible: true, setVisible(value) { this.visible = value; },
      dispose() { this.disposed = true; },
    }))};
  },
  h(tag, cls, text) { const el = element(); el.tag = tag; el.className = cls; el.textContent = text; return el; },
  unitsOf: () => '', opacityOf: () => 1, buildLegend: () => element(),
  stopPlay() {}, syncUrl() {}, updateSpine() {}, buildScrubber() {}, unionDims: () => [],
});
context.state.map = {getLayers: () => collection};
for (const name of ['toggleVar', 'moveVariable', 'addVariable', 'removeVariable',
  'clearDragFeedback', 'selectedDropBefore', 'initCardDragging', 'refreshCards', 'toggleVisibility', 'buildCards']) {
  vm.runInContext(source(name), context);
}
function order(expected) {
  assert.deepEqual(Array.from(context.state.selected), expected);
  assert.deepEqual(layers.map((l) => l.name), ['basemap', ...expected.flatMap((n) => [n, n, n])]);
  assert.deepEqual(elements['sel-cards'].children, expected.toReversed().map((n) => cards.get(n).el));
}
cards.clear();
context.buildCards();
assert.equal(cards.get('b').el.children.at(-1).children[0].textContent, 'Readable B');
assert.notEqual(cards.get('a').el.children.at(-1).children[0].className, 'longname');
assert.notEqual(cards.get('c').el.children.at(-1).children[0].className, 'longname');
context.toggleVar('a');
context.toggleVar('b');
context.toggleVar('c');
order(['a', 'b', 'c']);
const original = layers.slice();
context.toggleVar('b'); // A selected card stays selected and does not move.
order(['a', 'b', 'c']);
assert.deepEqual(layers, original);
const click = {stopPropagation() {}};
cards.get('b').visibility.handlers.click(click);
assert.equal(context.state.hidden.has('b'), true);
assert.ok(layers.filter((l) => l.name === 'b').every((l) => !l.visible));
assert.equal(cards.get('b').visibility.attributes['aria-label'], 'Show b');
order(['a', 'b', 'c']);
cards.get('b').visibility.handlers.click(click);
assert.equal(context.state.hidden.has('b'), false);
assert.ok(layers.filter((l) => l.name === 'b').every((l) => l.visible));
assert.equal(cards.get('b').visibility.attributes['aria-label'], 'Hide b');
assert.equal(elements['sec-other'].hidden, false); // Empty Other remains a drop target.
context.moveVariable('a', true, 'c'); // Move bottom card to top.
order(['b', 'c', 'a']);
assert.equal(created, 3);
assert.ok(layers.every((l) => original.includes(l))); // Reuse all layer/source groups.
context.moveVariable('a', true); // Drop below all selected cards.
order(['a', 'b', 'c']);
context.toggleVisibility('b');
const removeB = cards.get('b').el.children[0].children.at(-1);
assert.equal(removeB.attributes['aria-label'], 'Remove b from Selected');
removeB.handlers.click(click);
assert.equal(context.state.hidden.has('b'), false);
order(['a', 'c']);
assert.ok(original.filter((l) => l.name === 'b').every((l) => l.disposed));
context.initCardDragging();
context.draggedVariable = 'c';
let prevented = 0;
const event = {clientY: 0, preventDefault() { prevented++; }, dataTransfer: {}};
elements['sec-other'].handlers.dragover(event);
assert.equal(event.dataTransfer.dropEffect, 'move');
assert.equal(elements['sec-other'].classList.contains('drop-target'), true);
elements['sec-other'].handlers.drop(event); // Collapsed Other accepts removals.
order(['a']);
assert.equal(prevented, 2);
context.draggedVariable = 'a';
elements['sec-other'].handlers.drop(event); // The final selected variable can also be removed.
order([]);
assert.equal(elements['sec-selected'].hidden, false); // Keep a target for adding variables.
context.toggleVar('a');
context.toggleVisibility('a'); // Hidden cards keep their order when dragged.
context.draggedVariable = 'b';
elements['sec-selected'].handlers.drop(event); // Add Other above the first card.
order(['a', 'b']);
context.draggedVariable = 'a';
elements['sec-selected'].handlers.drop(event); // Reorder via the actual drop handler.
order(['b', 'a']);
assert.ok(layers.filter((l) => l.name === 'a').every((l) => !l.visible));
elements['sec-selected'].handlers.dragover(event);
elements['sec-selected'].handlers.dragleave({relatedTarget: null});
assert.equal(elements['sec-selected'].classList.contains('drop-target'), false);
context.draggedVariable = null;
elements['sec-other'].handlers.drop(event); // Ignore unrelated external drops.
order(['b', 'a']);

// The eye can hide the final visible layer without changing membership.
context.moveVariable('b', false);
context.toggleVisibility('a');
context.toggleVisibility('a');
order(['a']);
assert.ok(layers.filter((l) => l.name === 'a').every((l) => !l.visible));
cards.get('a').el.children[0].children.at(-1).handlers.click(click);
order([]);
assert.equal(context.state.hidden.size, 0);

// Restore explicit empty membership and visibility from the real URL loader.
context.blank = false;
context.cfg.dims = {};
context.URLSearchParams = URLSearchParams;
const loader = html.slice(html.indexOf('(function applyUrlState() {'),
  html.indexOf('})();', html.indexOf('(function applyUrlState() {')) + 5);
context.state.selected = ['a'];
context.params = new URLSearchParams('var=&hidden=a');
vm.runInContext(loader, context);
assert.deepEqual(Array.from(context.state.selected), []);
assert.equal(context.state.hidden.size, 0);
context.params = new URLSearchParams('var=a,b&hidden=b,unknown');
vm.runInContext(loader, context);
assert.deepEqual(Array.from(context.state.selected), ['a', 'b']);
assert.deepEqual(Array.from(context.state.hidden), ['b']);
context.state.selected = ['a'];
context.state.hidden.clear();
context.params = new URLSearchParams('var=unknown');
vm.runInContext(loader, context);
assert.deepEqual(Array.from(context.state.selected), ['a']);
