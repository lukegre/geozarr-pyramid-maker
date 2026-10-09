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
const elements = {cmapmore: element('div'), 'cmap-matplotlib': element('input'), 'cmap-cmocean': element('input'),
  'cmap-cmasher': element('input')};
elements.cmapmore.hidden = true;
const storage = new Map();
const failing = {get: () => { throw new Error('denied'); }, set: () => { throw new Error('denied'); }};
function makeContext(extra = {}, kv = storage, blank = false) {
  const store = {get: (k) => kv.has(k) ? kv.get(k) : null, set: (k, v) => kv.set(k, v), remove: (k) => kv.delete(k)};
  return vm.createContext({
    cfg: {colormaps: data.colormaps, colormap_groups: data.groups, ramp: data.colormaps.viridis, title: 't'},
    blank, store, $: (id) => elements[id],
    Event: class { constructor(type, init = {}) { this.type = type; this.bubbles = !!init.bubbles; } },
    document: {createElement: (tag) => element(tag)},
    Option: function (text, value) { return {tag: 'option', textContent: text, value}; },
    ...extra,
  });
}
function load(context) {
  vm.runInContext('const cmaps = cfg.colormaps;', context);
  vm.runInContext(between('const CMAP_SHOW_KEYS', '\ninitCmapToggle();') + '\ninitCmapToggle();', context);
  return (code) => vm.runInContext(code, context);
}
const optionsOf = (sel) => sel.children.flatMap((og) => og.children.map((o) => [og.label, o.value, o.textContent]));
const labelsOf = (sel) => sel.children.map((og) => og.label);
const values = (sel) => optionsOf(sel).map((o) => o[1]);

// Default: matplotlib on, cmocean and cmasher off, only the matplotlib groups are listed, control visible with a store.
let run = load(makeContext());
assert.equal(elements.cmapmore.hidden, false);
assert.equal(elements['cmap-matplotlib'].checked, true);
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

// The last remaining source cannot be unchecked (D-47): the box is disabled and a programmatic change is refused.
const box = (n) => elements['cmap-' + n];
const change = (n, on) => { box(n).checked = on; box(n).handlers.change(); };
assert.equal(box('matplotlib').disabled, true);
assert.match(box('matplotlib').title, /At least one colourmap source/);
assert.equal(box('cmocean').disabled, false);
assert.equal(box('cmocean').title, '');
const refused = element('select');
run('cmapFills').add(() => run('fillCmapSelect')(refused, 'viridis'));
run('cmapFills').forEach((f) => f());
change('matplotlib', false);
assert.equal(box('matplotlib').checked, true);
assert.equal(run('cmapShow').core, true);
assert.equal(storage.has('gpm-preview:cmaps-matplotlib'), false);
run('setCmapShow')('core', false);
assert.equal(run('cmapShow').core, true);
assert.equal(storage.has('gpm-preview:cmaps-matplotlib'), false);
assert.equal(labelsOf(refused).length, 6);
// Checking a second source re-enables the first.
change('cmasher', true);
assert.equal(box('matplotlib').disabled, false);
assert.equal(box('cmasher').disabled, false);
assert.equal(box('cmocean').disabled, false);
assert.equal(box('matplotlib').title, '');
run('cmapFills').clear();

// Unchecking matplotlib (cmasher is on) hides every core group but the card's current map; the lookup is unaffected.
const mpl = element('select');
run('cmapFills').add(() => run('fillCmapSelect')(mpl, 'viridis'));
run('cmapFills').forEach((f) => f());
elements['cmap-matplotlib'].checked = false;
elements['cmap-matplotlib'].handlers.change();
assert.equal(storage.get('gpm-preview:cmaps-matplotlib'), '0');
assert.deepEqual(optionsOf(mpl).filter((o) => !o[0].startsWith('cmasher')), [['Perceptually uniform sequential', 'viridis', 'viridis']]);
assert.equal(mpl.value, 'viridis');
assert.equal(box('cmasher').disabled, true);
assert.ok(run('cmaps')['plasma'] && run('cmaps')['cmo.thermal']);
run('fillCmapSelect')(sel, 'cmo.thermal');
assert.deepEqual(optionsOf(sel).filter((o) => o[0].startsWith('cmocean')), [['cmocean: Sequential', 'cmo.thermal', 'thermal']]);
elements['cmap-matplotlib'].checked = true;
elements['cmap-matplotlib'].handlers.change();
assert.equal(storage.get('gpm-preview:cmaps-matplotlib'), '1');
assert.equal(labelsOf(mpl).length, 9);
assert.equal(box('cmasher').disabled, false);
change('cmasher', false);
assert.equal(box('matplotlib').disabled, true);
assert.equal(labelsOf(mpl).length, 6);
run('cmapFills').clear();

// A card whose current map is in a hidden group keeps exactly that option, with the prefix stripped from the text.
run('fillCmapSelect')(sel, 'cmo.thermal');
assert.equal(sel.value, 'cmo.thermal');
assert.deepEqual(optionsOf(sel).filter((o) => o[0].startsWith('cmocean')), [['cmocean: Sequential', 'cmo.thermal', 'thermal']]);
assert.equal(labelsOf(sel).some((l) => l.startsWith('cmasher')), false);
// Another subgroup of the same hidden source keeps only its own current map under its own label.
run('fillCmapSelect')(sel, 'cmo.balance');
assert.deepEqual(optionsOf(sel).filter((o) => /^cm[or]\./.test(o[1])), [['cmocean: Diverging', 'cmo.balance', 'balance']]);
assert.equal(sel.value, 'cmo.balance');
run('fillCmapSelect')(sel, 'cmr.seasons_s');
assert.deepEqual(optionsOf(sel).filter((o) => /^cm[or]\./.test(o[1])), [['cmasher: Cyclic', 'cmr.seasons_s', 'seasons_s']]);

// Toggling rebuilds every registered select and keeps each current value.
const a = element('select'), b = element('select');
const cur = {a: 'cmr.ember', b: 'viridis'};
run('cmapFills').add(() => run('fillCmapSelect')(a, cur.a));
run('cmapFills').add(() => run('fillCmapSelect')(b, cur.b));
run('cmapFills').forEach((f) => f());
const isGroupOf = (source) => (o) => o[0].startsWith(source + ':');
assert.deepEqual(optionsOf(a).filter(isGroupOf('cmasher')), [['cmasher: Sequential', 'cmr.ember', 'ember']]);
elements['cmap-cmasher'].checked = true;
elements['cmap-cmasher'].handlers.change();
assert.equal(storage.get('gpm-preview:cmaps-cmasher'), '1');
assert.equal(storage.has('gpm-preview:cmaps-cmocean'), false);
const cmasherGroups = data.groups.filter((g) => g.source === 'cmasher');
const cmasher = cmasherGroups.flatMap((g) => g.names);
assert.deepEqual(cmasherGroups.map((g) => g.label), ['cmasher: Sequential', 'cmasher: Diverging', 'cmasher: Cyclic']);
assert.equal(cmasher.length, 57);
assert.equal(data.groups.filter((g) => g.source === 'cmocean').flatMap((g) => g.names).length, 22);
assert.deepEqual(labelsOf(a).filter((l) => l.startsWith('cmasher')), cmasherGroups.map((g) => g.label));
assert.deepEqual(values(a).filter((v) => v.startsWith('cmr.')), cmasher);
assert.equal(a.value, 'cmr.ember');
assert.ok(labelsOf(b).some((l) => l.startsWith('cmasher')) && !labelsOf(b).some((l) => l.startsWith('cmocean')));
assert.equal(b.children.find((og) => og.label === 'cmasher: Sequential').children[0].textContent, cmasher[0].slice(4));
assert.equal(b.children.find((og) => og.label === 'cmasher: Cyclic').children.at(-1).textContent, 'seasons_s');
elements['cmap-cmocean'].checked = true;
elements['cmap-cmocean'].handlers.change();
assert.equal(storage.get('gpm-preview:cmaps-cmocean'), '1');
assert.deepEqual(labelsOf(b).slice(-6), ['cmocean: Sequential', 'cmocean: Diverging', 'cmocean: Cyclic',
  'cmasher: Sequential', 'cmasher: Diverging', 'cmasher: Cyclic']);
assert.equal(b.value, 'viridis');

// Switching off again: the card keeps its current map, the others disappear.
elements['cmap-cmasher'].checked = false;
elements['cmap-cmasher'].handlers.change();
assert.equal(storage.get('gpm-preview:cmaps-cmasher'), '0');
assert.deepEqual(optionsOf(a).filter(isGroupOf('cmasher')).map((o) => o[1]), ['cmr.ember']);
assert.equal(labelsOf(b).some((l) => l.startsWith('cmasher')), false);

// Persisted state is restored on load; storage failures fall back to memory.
run = load(makeContext({}, new Map([['gpm-preview:cmaps-cmocean', '1']])));
assert.equal(elements['cmap-cmocean'].checked, true);
assert.equal(elements['cmap-cmasher'].checked, false);
assert.equal(elements['cmap-matplotlib'].checked, true);  // nothing stored: matplotlib on
run = load(makeContext({}, new Map([['gpm-preview:cmaps-matplotlib', '1']])));
assert.equal(elements['cmap-matplotlib'].checked, true);
// No source on in storage (matplotlib '0', or everything off): matplotlib on at load, nothing written back.
for (const kv of [new Map([['gpm-preview:cmaps-matplotlib', '0']]),
  new Map([['gpm-preview:cmaps-matplotlib', '0'], ['gpm-preview:cmaps-cmocean', '0'], ['gpm-preview:cmaps-cmasher', '0']])]) {
  const before = new Map(kv);
  run = load(makeContext({}, kv));
  assert.equal(box('matplotlib').checked, true);
  assert.equal(box('cmocean').checked, false);
  assert.equal(box('cmasher').checked, false);
  assert.equal(box('matplotlib').disabled, true);
  assert.deepEqual([...kv], [...before]);
  const fb2 = element('select');
  run('fillCmapSelect')(fb2, 'viridis');
  assert.equal(labelsOf(fb2).length, 6);
}
// Normal stored states are respected: only cmocean on leaves cmocean checked and disabled.
run = load(makeContext({}, new Map([['gpm-preview:cmaps-matplotlib', '0'], ['gpm-preview:cmaps-cmocean', '1']])));
assert.equal(box('matplotlib').checked, false);
assert.equal(box('cmocean').checked, true);
assert.equal(box('cmocean').disabled, true);
assert.equal(box('matplotlib').disabled, false);
assert.equal(box('cmasher').disabled, false);
// Storage failure (the real `store` swallows errors and returns null): defaults hold, state stays in memory.
run = load(makeContext({store: {get: () => null, set() { /* swallowed like the real store */ }}}));
assert.equal(elements['cmap-cmocean'].checked, false);
assert.equal(elements['cmap-matplotlib'].checked, true);
const mem = element('select');
run('cmapFills').add(() => run('fillCmapSelect')(mem, 'viridis'));
change('cmocean', true);
change('matplotlib', false);
assert.equal(optionsOf(mem).filter((o) => o[0].startsWith('Perceptually')).length, 1);
assert.equal(optionsOf(mem).filter((o) => o[0].startsWith('cmocean')).length, 22);

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
// The fallback groups are core too: they follow the matplotlib checkbox and keep the current map.
change('cmasher', true);
change('matplotlib', false);
run('fillCmapSelect')(fb, 'BrBG');
assert.deepEqual(optionsOf(fb).map((o) => o.join(':')), ['Diverging:BrBG:BrBG']);  // no cmasher maps in this config

// Arrow-key stepping (stepSelect): previous/next listed option across optgroups, clamped, modifiers ignored.
run = load(makeContext({}, new Map()));  // fresh defaults: matplotlib groups listed
const keySel = element('select');
Object.defineProperty(keySel, 'options', {get: () => keySel.children.flatMap((og) => og.children)});
const fired = [];
keySel.dispatchEvent = (ev) => { fired.push(ev); };
run('fillCmapSelect')(keySel, 'viridis');
const keyOpts = values(keySel);
const press = (key, mods = {}) => {
  const ev = {key, ...mods, prevented: 0, stopped: 0, preventDefault() { this.prevented++; }, stopPropagation() { this.stopped++; }};
  run('stepSelect')(keySel, ev);
  return ev;
};
let ev = press('ArrowRight');
assert.equal(keySel.value, keyOpts[1]);
assert.equal(fired.length, 1);
assert.equal(fired[0].type, 'change');
assert.equal(fired[0].bubbles, true);
assert.equal(ev.prevented, 1);
assert.equal(ev.stopped, 1);
press('ArrowLeft');
assert.equal(keySel.value, keyOpts[0]);
// clamp at the start: nothing changes and no event, but the key is still swallowed
ev = press('ArrowLeft');
assert.equal(keySel.value, keyOpts[0]);
assert.equal(fired.length, 2);
assert.equal(ev.prevented, 1);
assert.equal(ev.stopped, 1);
// across an optgroup boundary
const firstGroup = keySel.children[0].children.length;
keySel.value = keyOpts[firstGroup - 1];
press('ArrowRight');
assert.equal(keySel.value, keyOpts[firstGroup]);
assert.notEqual(optionsOf(keySel)[firstGroup - 1][0], optionsOf(keySel)[firstGroup][0]);
press('ArrowLeft');
assert.equal(keySel.value, keyOpts[firstGroup - 1]);
// clamp at the end
keySel.value = keyOpts.at(-1);
const before = fired.length;
press('ArrowRight');
assert.equal(keySel.value, keyOpts.at(-1));
assert.equal(fired.length, before);
// modifiers and other keys are ignored entirely
keySel.value = keyOpts[3];
for (const mods of [{ctrlKey: true}, {metaKey: true}, {altKey: true}, {shiftKey: true}]) {
  ev = press('ArrowRight', mods);
  assert.equal(keySel.value, keyOpts[3]);
  assert.equal(ev.prevented + ev.stopped, 0);
}
for (const key of ['ArrowDown', 'ArrowUp', 'a']) {
  ev = press(key);
  assert.equal(keySel.value, keyOpts[3]);
  assert.equal(ev.prevented + ev.stopped, 0);
}
assert.equal(fired.length, before);
// Repeated presses keep stepping when `change` refills the select (apply -> sync -> fillCmapSelect rebuilds the options).
let current = 'viridis';
keySel.dispatchEvent = () => { current = keySel.value; run('fillCmapSelect')(keySel, current); };
run('fillCmapSelect')(keySel, current);
const start = values(keySel).indexOf('viridis');
press('ArrowRight'); press('ArrowRight'); press('ArrowRight');
assert.equal(keySel.value, values(keySel)[start + 3]);
assert.equal(current, values(keySel)[start + 3]);
press('ArrowLeft');
assert.equal(keySel.value, values(keySel)[start + 2]);
// A select listing only the current map (hidden groups) has nowhere to step.
run = load(makeContext({cfg: {colormaps: {viridis: [[0, 0, 0]]}, colormap_groups: [{label: 'One', source: 'core', names: ['viridis']}],
  ramp: [[0, 0, 0]], title: 't'}}, new Map()));
const lone = element('select');
Object.defineProperty(lone, 'options', {get: () => lone.children.flatMap((og) => og.children)});
lone.dispatchEvent = () => { throw new Error('unexpected change'); };
run('fillCmapSelect')(lone, 'viridis');
run('stepSelect')(lone, {key: 'ArrowRight', preventDefault() {}, stopPropagation() {}});
assert.equal(lone.value, 'viridis');
// Space on the focused colourmap menu (reverseKey, D-45): clicks the card's Reversed checkbox so the map, legend, URL and
// storage follow its own change handler; the key is swallowed so the dropdown does not open and no shortcut fires.
const makeRev = () => {
  const rev = {checked: false, clicks: 0, changes: 0, focus() { throw new Error('focus must stay on the select'); }};
  rev.click = () => { rev.clicks++; rev.checked = !rev.checked; rev.changes++; };
  return rev;
};
const spaceEv = (extra = {}) => ({type: 'keydown', key: ' ', code: 'Space', prevented: 0, stopped: 0,
  preventDefault() { this.prevented++; }, stopPropagation() { this.stopped++; }, ...extra});
const rev = makeRev();
let sev = spaceEv();
run('reverseKey')(rev, sev);
assert.equal(rev.checked, true);
assert.equal(rev.changes, 1);
assert.equal(sev.prevented, 1);
assert.equal(sev.stopped, 1);
run('reverseKey')(rev, spaceEv());
assert.equal(rev.checked, false);  // toggles back
assert.equal(rev.changes, 2);
// key ' ' or code 'Space' alone is enough
run('reverseKey')(rev, spaceEv({key: 'Spacebar'}));
run('reverseKey')(rev, spaceEv({code: ''}));
assert.equal(rev.changes, 4);
// auto-repeat and keyup are swallowed without toggling again (keyup would otherwise open the list in some browsers)
sev = spaceEv({repeat: true});
run('reverseKey')(rev, sev);
assert.equal(rev.changes, 4);
assert.equal(sev.prevented, 1);
sev = spaceEv({type: 'keyup'});
run('reverseKey')(rev, sev);
assert.equal(rev.changes, 4);
assert.equal(sev.prevented, 1);
assert.equal(sev.stopped, 1);
// modifiers and other keys are untouched
for (const mods of [{ctrlKey: true}, {metaKey: true}, {altKey: true}, {shiftKey: true}]) {
  sev = spaceEv(mods);
  run('reverseKey')(rev, sev);
  assert.equal(sev.prevented + sev.stopped, 0);
}
for (const extra of [{key: 'Enter', code: 'Enter'}, {key: 'a', code: 'KeyA'}, {key: 'ArrowRight', code: 'ArrowRight'}]) {
  sev = spaceEv(extra);
  run('reverseKey')(rev, sev);
  assert.equal(sev.prevented + sev.stopped, 0);
}
assert.equal(rev.changes, 4);
console.log('ok');
