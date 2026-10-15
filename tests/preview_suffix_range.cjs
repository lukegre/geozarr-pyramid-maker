// Execute the viewer's real noHeadSuffixReads (D-56): suffix reads become one GET with Range: bytes=-N, never a HEAD.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const start = html.indexOf('function noHeadSuffixReads');
assert.ok(start >= 0, 'noHeadSuffixReads');
const end = html.indexOf('\n}\n', start);
assert.ok(end > start, 'end of noHeadSuffixReads');
const code = html.slice(start, end + 2);
const context = vm.createContext({});
const patch = vm.runInContext(`${code}; noHeadSuffixReads`, context);

// Mimics zarrita's FetchStore: suffix reads send a HEAD first unless useSuffixRequest is set.
function makeStore(log) {
  return class FetchStore {
    constructor(url, opts = {}) { this.url = url; this.fetch = opts.fetch; }
    async get(key, opts = {}) {
      log.push({method: 'GET', key, headers: {...opts.headers}});
      return new Uint8Array([1]);
    }
    async getRange(key, range, opts = {}) {
      if ('suffixLength' in range) {
        log.push({method: 'HEAD', key});
        return new Uint8Array([9]);
      }
      log.push({method: 'GET', key, headers: {Range: `bytes=${range.offset}-${range.offset + range.length - 1}`}});
      return new Uint8Array([2]);
    }
  };
}

(async () => {
  const log = [];
  const FetchStore = makeStore(log);
  patch(FetchStore);
  const store = new FetchStore('https://x/y.zarr');
  await store.getRange('/a/c/0', {suffixLength: 16420}, {headers: {'X-Test': '1'}});
  assert.deepEqual(log, [{method: 'GET', key: '/a/c/0', headers: {'X-Test': '1', Range: 'bytes=-16420'}}]);
  log.length = 0;
  await store.getRange('/a/c/0', {offset: 5, length: 10});
  assert.deepEqual(log, [{method: 'GET', key: '/a/c/0', headers: {Range: 'bytes=5-14'}}]);
  log.length = 0;
  patch(FetchStore);  // idempotent: must not wrap twice
  await store.getRange('/a/c/0', {suffixLength: 7});
  assert.deepEqual(log, [{method: 'GET', key: '/a/c/0', headers: {Range: 'bytes=-7'}}]);
})().catch((e) => { console.error(e); process.exit(1); });
