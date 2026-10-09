// Execute the viewer's real per-variable attributes popover (D-54) with a small DOM stand-in: the info button on
// every card, the table content, toggle/move/close behaviour and the browser-side attr formatting.
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
function element(tag = 'div') {
  return {
    tag, handlers: {}, children: [], hidden: false, attributes: {}, style: {}, className: '', textContent: '',
    offsetWidth: 300, offsetHeight: 200,
    classList: {add() {}, remove() {}, toggle() {}, contains: () => false},
    addEventListener(type, cb) { this.handlers[type] = cb; },
    setAttribute(name, value) { this.attributes[name] = value; },
    contains(node) { return node === this || this.children.some((c) => c === node || c.contains?.(node)); },
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; },
    getBoundingClientRect() { return this.rect || {top: 0, right: 0, height: 100}; },
  };
}
const variables = [
  {name: 'a', long_name: '', display_name: 'a', dims: [], units: '', categorical: true,
    attrs: {units: 'K', vmin: 1.5, flags: '1, 2'}},
  {name: 'b', long_name: 'Long B', display_name: 'Long B', dims: [], units: '', categorical: true, attrs: {}},
];
const elements = Object.fromEntries(['sel-cards', 'selhint', 'varinfo', 'sidebar'].map((n) => [n, element()]));
elements.sidebar.rect = {top: 0, right: 340, height: 800};
const docHandlers = {};
const cards = new Map();
const ctx = vm.createContext({
  cards, cfg: {variables}, labelMode: 'name', ICON: {info: 'i'}, innerWidth: 1000, innerHeight: 600,
  $: (id) => elements[id], varOf: (n) => variables.find((v) => v.name === n),
  h(tag, cls, text) { const el = element(tag); el.className = cls || ''; if (text !== undefined) el.textContent = text; return el; },
  document: {addEventListener(type, cb) { docHandlers[type] = cb; }},
  unitsOf: () => '', opacityOf: () => 1, buildLegend: () => element(), buildStyleControls: () => null,
  toggleVar() {}, toggleVisibility() {}, moveVariable() {}, syncUrl() {}, clearDragFeedback() {},
  state: {open: new Set(), hidden: new Set()}, refreshCards() {},
});
vm.runInContext(source('labelOf'), ctx);
vm.runInContext(source('buildCards'), ctx);
vm.runInContext(between('// D-54: attribute display values', '\nfunction initSidebar'), ctx);
ctx.buildCards();
ctx.initVarInfo();

const panel = elements.varinfo;
const info = (name) => cards.get(name).info;
const click = {stopPropagation() { this.stopped = true; }};
const rowsOf = () => panel.children[1].children[0].children.map((tr) => [tr.children[0].textContent, tr.children[1].textContent]);

// every card has a keyboard-reachable button with an aria-label, closed at first
assert.equal(info('a').attributes['aria-label'], 'Attributes of a');
assert.equal(info('b').attributes['aria-label'], 'Attributes of b');
assert.equal(info('a').tag, 'button');
assert.equal(info('a').attributes['aria-expanded'], 'false');
assert.equal(panel.hidden, false); // the stand-in starts visible; real markup has `hidden`, closing sets it

// open: title and the attrs table, the click does not reach the card
info('a').rect = {top: 120, right: 330};
info('a').handlers.click(click);
assert.equal(click.stopped, true);
assert.equal(panel.hidden, false);
assert.equal(info('a').attributes['aria-expanded'], 'true');
assert.equal(panel.children[0].textContent, 'a');
assert.deepEqual(rowsOf(), [['units', 'K'], ['vmin', '1.5'], ['flags', '1, 2']]);
assert.equal(panel.style.left, '348px'); // right of the sidebar edge (340 + 8)
assert.equal(panel.style.top, '120px');

// the same icon again closes; reopening works
info('a').handlers.click(click);
assert.equal(panel.hidden, true);
assert.equal(info('a').attributes['aria-expanded'], 'false');
info('a').handlers.click(click);
assert.equal(panel.hidden, false);

// another card's icon moves it, with its own content (no attrs -> one muted line), clamped inside the viewport
info('b').rect = {top: 590, right: 330};
info('b').handlers.click(click);
assert.equal(info('a').attributes['aria-expanded'], 'false');
assert.equal(info('b').attributes['aria-expanded'], 'true');
assert.equal(panel.children[0].textContent, 'b');
assert.equal(panel.children[1].className, 'none');
assert.equal(panel.children[1].textContent, 'No attributes');
assert.equal(panel.style.top, '392px'); // 600 - 200 - 8

// Escape and an outside click close it; a click inside does not
docHandlers.keydown({key: 'a'});
assert.equal(panel.hidden, false);
docHandlers.keydown({key: 'Escape'});
assert.equal(panel.hidden, true);
info('a').handlers.click(click);
docHandlers.click({target: panel.children[0]});
assert.equal(panel.hidden, false);
docHandlers.click({target: info('a')}); // own button: its handler toggles, the outside rule ignores it
assert.equal(panel.hidden, false);
docHandlers.click({target: elements['sel-cards']});
assert.equal(panel.hidden, true);
docHandlers.click({target: elements['sel-cards']}); // closed: nothing to do
assert.equal(panel.hidden, true);

// the Long name label changes the title and aria-label source
ctx.labelMode = 'long';
info('b').handlers.click(click);
assert.equal(panel.children[0].textContent, 'Long B');

// attrValue mirrors preview.py `_attr_value`
const f = ctx.attrValue;
assert.equal(f('x'), 'x');
assert.equal(f(3), 3);
assert.equal(f(1.5), 1.5);
assert.equal(f(true), 'True');
assert.equal(f(false), 'False');
assert.equal(f(NaN), 'NaN');
assert.equal(f([1, 2, 'a']), '1, 2, a');
assert.equal(f({a: 1}), '{"a":1}');
assert.equal(f([{a: 1}, 2]), '{"a":1}, 2');
assert.equal(f(null), 'None');
assert.equal(f('y'.repeat(2500)), 'y'.repeat(2000) + '…');
