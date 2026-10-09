// Execute the viewer's real log-scale code (D-54): colour stops, effectiveLog, log ticks and legend, persistence, URL.
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
const kv = new Map();
const store = {get: (k) => kv.has(k) ? kv.get(k) : null, set: (k, v) => kv.set(k, v), remove: (k) => kv.delete(k)};
const params = new URLSearchParams();
const state = {selected: ['a'], dims: {}, roles: {}, opacity: {}, hidden: new Set()};
const variables = [{name: 'a', vmin: 1, vmax: 1000}, {name: 'b', vmin: 2, vmax: 9}, {name: 'c', categorical: true}];
const varOf = (name) => variables.find((x) => x.name === name);
const el = () => ({style: {}, children: [], append(...n) { this.children.push(...n); }});
const context = vm.createContext({
  cfg: {colormaps: data.colormaps, ramp: data.colormaps.viridis, title: 't', dims: {}}, store, params, state,
  blank: false, ROLES: [], window: {}, console, varOf,
  h: (tag, cls, text) => { const e = el(); e.cls = cls; e.text = text; return e; },
  clamp: (x, lo, hi) => Math.min(hi, Math.max(lo, x)), fmt: (x) => String(+Number(x).toPrecision(6)),
  opacityOf: () => 1, rgba: (c) => [c[0], c[1], c[2], 1],
});
const run = (code) => vm.runInContext(code, context);
run('const cmaps = cfg.colormaps; const styles = {};');
run(between('const styleKey', 'const styles = {};'));  // styleKey, resolveCmap, defaultStyle
run(between('function loadStyle', 'const stopsOf'));  // loadStyle, styleOf
run(between('const stopsOf', '// selected: ordered variable names'));  // stopsOf, gradientCss, effectiveLog, niceTicks, niceLogTicks, colorExpr
run(between('function styleParams(', '\n(function applyUrlState'));
run(between('function buildLegend(', '\nconst cards = new window.Map'));
const near = (a, b, tol = 1e-9) => assert.ok(Math.abs(a - b) <= tol * Math.max(1, Math.abs(b)), `${a} != ${b}`);
const setStyle = (name, st) => { run('styles')[name] = {cmap: 'viridis', reversed: false, ...st}; };
const stopValues = (expr) => expr.slice(3).filter((_, i) => i % 2 === 0);
const stopColors = (expr) => expr.slice(3).filter((_, i) => i % 2 === 1);

// effectiveLog: the flag only counts with a positive Min; the stored flag itself is never changed.
const eff = (st) => run('effectiveLog')(st);
assert.equal(eff({log: true, vmin: 1, vmax: 10}), true);
assert.equal(eff({log: true, vmin: 0, vmax: 10}), false);
assert.equal(eff({log: true, vmin: -2, vmax: 10}), false);
assert.equal(eff({log: false, vmin: 1, vmax: 10}), false);
assert.equal(eff({vmin: 1, vmax: 10}), false);  // absent flag = off

// colorExpr: linear is unchanged; log places the same colours at geometric stop values.
const v = variables[0];
setStyle('a', {vmin: 1, vmax: 1000});
const lin = run('colorExpr')(v);
assert.deepEqual([lin[0], lin[1][0], [...lin[2]]], ['interpolate', 'linear', ['band', 1]]);
near(stopValues(lin)[0], 1); near(stopValues(lin)[10], 1000); near(stopValues(lin)[5], 500.5);
setStyle('a', {vmin: 1, vmax: 1000, log: true});
const lg = run('colorExpr')(v);
assert.equal(lg[0], 'interpolate');
assert.equal(lg[1][0], 'linear');
assert.deepEqual([...lg[2]], ['band', 1]);
const vals = stopValues(lg);
assert.equal(vals.length, 11);
near(vals[0], 1); near(vals[10], 1000);
for (let i = 1; i < vals.length; i++) near(vals[i] / vals[i - 1], 10 ** 0.3, 1e-9);  // constant ratio
for (let i = 1; i < vals.length; i++) assert.ok(vals[i] > vals[i - 1]);
assert.deepEqual(JSON.stringify(stopColors(lg)), JSON.stringify(stopColors(lin)));  // same colours
// reversed colours follow the flag as before
setStyle('a', {vmin: 1, vmax: 1000, log: true, reversed: true});
assert.deepEqual(JSON.stringify(stopColors(run('colorExpr')(v))), JSON.stringify([...stopColors(lin)].reverse()));
// vmin <= 0: the log flag is kept but the expression stays linear
setStyle('a', {vmin: 0, vmax: 10, log: true});
const flat = run('colorExpr')(v);
near(stopValues(flat)[5], 5); near(stopValues(flat)[1], 1);
assert.equal(run('styles').a.log, true);

// niceLogTicks: 1-2-5 per decade up to 6 ticks, else 1 per decade; vmin and vmax when fewer than 2 ticks fit.
const nlt = (a, b) => [...run('niceLogTicks')(a, b)];
assert.deepEqual(nlt(1, 10), [1, 2, 5, 10]);
assert.deepEqual(nlt(3, 80), [5, 10, 20, 50]);
assert.deepEqual(nlt(1, 1000), [1, 10, 100, 1000]);  // 1-2-5 would give 10 ticks
assert.deepEqual(nlt(0.001, 1), [0.001, 0.01, 0.1, 1]);
assert.deepEqual(nlt(0.0015, 0.9), [0.01, 0.1]);  // 1-2-5 would give 8 ticks, 1 per decade gives 2
assert.deepEqual(nlt(1, 100), [1, 10, 100]);
assert.deepEqual(nlt(1, 3), [1, 2]);  // nothing outside the range (5 is dropped)
assert.deepEqual(nlt(3, 4), [3, 4]);  // fewer than 2 ticks: vmin and vmax
assert.deepEqual(nlt(1.2, 1.8), [1.2, 1.8]);

// buildLegend: tick positions are log-fractions under an effective log scale, linear fractions otherwise.
const ticksOf = (name) => {
  const ticks = run('buildLegend')(varOf(name)).children[1].children;
  return {pos: ticks.filter((_, i) => i % 2 === 0).map((m) => parseFloat(m.style.left)),
    text: ticks.filter((_, i) => i % 2 === 1).map((l) => l.text)};
};
setStyle('a', {vmin: 1, vmax: 1000, log: true});
let lt = ticksOf('a');
assert.deepEqual(lt.text, ['1', '10', '100', '1000']);
[0, 100 / 3, 200 / 3, 100].forEach((p, i) => near(lt.pos[i], p));
setStyle('a', {vmin: 3, vmax: 80, log: true});
lt = ticksOf('a');
assert.deepEqual(lt.text, ['5', '10', '20', '50']);
lt.pos.forEach((p, i) => near(p, 100 * Math.log([5, 10, 20, 50][i] / 3) / Math.log(80 / 3)));
setStyle('a', {vmin: 0, vmax: 10, log: true});  // not effective: linear ticks
lt = ticksOf('a');
assert.deepEqual(lt.text, ['0', '2', '4', '6', '8', '10']);
[0, 20, 40, 60, 80, 100].forEach((p, i) => near(lt.pos[i], p));
setStyle('a', {vmin: 1, vmax: 1000});  // log off: linear ticks as before
assert.deepEqual(ticksOf('a').text, ['200', '400', '600', '800', '1000']);

// Persistence: loadStyle keeps `log` (false when absent); the style is stored as JSON.
const lv = {name: 'a', vmin: 1, vmax: 2};
context.lv = lv;
assert.equal(run('loadStyle(lv)').log, undefined);  // the default style has no flag
kv.set('gpm-preview:style:t:a', JSON.stringify({cmap: 'magma', reversed: false, vmin: 1, vmax: 2, log: true}));
assert.deepEqual({...run('loadStyle(lv)')}, {cmap: 'magma', reversed: false, vmin: 1, vmax: 2, log: true});
kv.set('gpm-preview:style:t:a', JSON.stringify({cmap: 'magma', reversed: false, vmin: 1, vmax: 2}));
assert.equal(run('loadStyle(lv)').log, false);

// URL: `log=1` is written only when set (suffixed per variable with several), read back; absent = off.
const asObj = (vars) => Object.fromEntries(run('styleParams')(vars));
setStyle('a', {vmin: 1, vmax: 1000, log: true});
setStyle('b', {vmin: 2, vmax: 9});
assert.equal(asObj([varOf('a')]).log, '1');
assert.equal('log' in asObj([varOf('b')]), false);
const two = asObj([varOf('a'), varOf('b')]);
assert.equal(two['log.a'], '1');
assert.equal('log.b' in two, false);
setStyle('a', {vmin: 1, vmax: 1000, log: false});
assert.equal('log' in asObj([varOf('a')]), false);
const apply = (query, sel = ['a']) => {
  for (const k of [...params.keys()]) params.delete(k);
  for (const [k, val] of new URLSearchParams(query)) params.append(k, val);
  context.state.selected = sel;
  delete run('styles').a; delete run('styles').b;
  kv.clear();
  run(between('(function applyUrlState', '\nlet urlTimer'));
  return (n) => run('styleOf')(varOf(n));
};
let sty = apply('var=a&log=1&vmin=1&vmax=1000');
assert.equal(sty('a').log, true);
near(sty('a').vmin, 1);
sty = apply('var=a&vmin=1&vmax=1000');  // absent = off
assert.equal(!!sty('a').log, false);
sty = apply('var=a,b&log.b=1&vmin.b=2&vmax.b=9', ['a', 'b']);
assert.equal(!!sty('a').log, false);
assert.equal(sty('b').log, true);
sty = apply('var=a&log=0');
assert.equal(!!sty('a').log, false);
// a colourmap in the URL keeps the log flag (unlike `rev`, which resets)
sty = apply('var=a&cmap=magma&log=1');
assert.equal(sty('a').cmap, 'magma');
assert.equal(sty('a').log, true);
