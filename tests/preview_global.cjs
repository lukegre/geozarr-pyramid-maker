// Execute the viewer's real Global settings collapse code (D-46) with a small DOM stand-in.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
function line(prefix) {
  const start = html.indexOf(prefix);
  assert.ok(start >= 0, prefix);
  return html.slice(start, html.indexOf('\n', start));
}
function source(name) {
  const start = html.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  return html.slice(start, html.indexOf('\n}\n', start) + 2);
}
function element() {
  const classes = new Set();
  return {
    handlers: {}, hidden: false, attributes: {}, innerHTML: '',
    classList: {toggle: (v, on) => on ? classes.add(v) : classes.delete(v), contains: (v) => classes.has(v)},
    addEventListener(type, cb) { this.handlers[type] = cb; },
    setAttribute(name, value) { this.attributes[name] = value; },
  };
}
function makeContext(kv = new Map(), failing = false) {
  const elements = Object.fromEntries(['gs-title', 'gs-icon', 'gs-body', 'sbfoot', 'error', 'note'].map((n) => [n, element()]));
  // The real `store` helper over a localStorage stand-in that throws when storage is unavailable.
  const localStorage = failing
    ? {getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }}
    : {getItem: (k) => kv.has(k) ? kv.get(k) : null, setItem: (k, v) => kv.set(k, v)};
  const ctx = vm.createContext({localStorage, elements, $: (id) => elements[id], ICON: {gear: 'GEAR'}});
  const start = html.indexOf('const store = {');
  vm.runInContext(html.slice(start, html.indexOf('\n};', start) + 3), ctx);
  vm.runInContext(line('const GLOBAL_KEY'), ctx);
  vm.runInContext(line('let globalCollapsed'), ctx);
  vm.runInContext(source('initGlobalCollapse'), ctx);
  ctx.run = (code) => vm.runInContext(code, ctx);
  ctx.run('initGlobalCollapse()');
  return ctx;
}

// Default: expanded, gear icon filled from ICON.gear, nothing stored yet.
let kv = new Map();
let ctx = makeContext(kv);
let {elements: el} = ctx;
assert.equal(el['gs-icon'].innerHTML, 'GEAR');
assert.equal(el['gs-body'].hidden, false);
assert.equal(el['gs-title'].attributes['aria-expanded'], 'true');
assert.equal(kv.has('gpm-preview:global-collapsed'), false);

// Toggle collapses, stores '1', aria-expanded follows; toggling again expands and stores '0'.
el['gs-title'].handlers.click();
assert.equal(el['gs-body'].hidden, true);
assert.equal(el['gs-title'].attributes['aria-expanded'], 'false');
assert.equal(el.sbfoot.classList.contains('gs-collapsed'), true);
assert.equal(kv.get('gpm-preview:global-collapsed'), '1');
// #error and #note sit outside the wrapper, so they are never touched.
assert.equal(el.error.hidden, false);
assert.equal(el.note.hidden, false);
el['gs-title'].handlers.click();
assert.equal(el['gs-body'].hidden, false);
assert.equal(el['gs-title'].attributes['aria-expanded'], 'true');
assert.equal(el.sbfoot.classList.contains('gs-collapsed'), false);
assert.equal(kv.get('gpm-preview:global-collapsed'), '0');

// Stored '1' restores the collapsed state; '0' and junk stay expanded.
ctx = makeContext(new Map([['gpm-preview:global-collapsed', '1']]));
assert.equal(ctx.elements['gs-body'].hidden, true);
assert.equal(ctx.elements['gs-title'].attributes['aria-expanded'], 'false');
for (const v of ['0', 'bogus']) {
  assert.equal(makeContext(new Map([['gpm-preview:global-collapsed', v]])).elements['gs-body'].hidden, false);
}

// Storage failure: the choice still works in memory.
ctx = makeContext(new Map(), true);
assert.equal(ctx.elements['gs-body'].hidden, false);
ctx.elements['gs-title'].handlers.click();
assert.equal(ctx.elements['gs-body'].hidden, true);
assert.equal(ctx.run('globalCollapsed'), true);
ctx.elements['gs-title'].handlers.click();
assert.equal(ctx.elements['gs-body'].hidden, false);
