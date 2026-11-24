// Pulse: the page-side runner ranking matches the server's tiers, and the renamed server fields are read under their new
// names with the old ones as a fallback. Run with: node test_pulse_fields.js
const assert = require('assert');

class El {
  constructor(id) {
    this.id = id;
    this.innerHTML = '';
    this.style = {};
    this.children = [];
    this.classList = { add() {}, remove() {}, contains: () => false, toggle() {} };
  }
  showModal() {}
  close() {}
  setAttribute() {}
  removeAttribute() {}
  addEventListener() {}
  appendChild(k) { this.innerHTML += (k && k.innerHTML) || ''; }
  replaceChildren() { this.innerHTML = ''; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  set textContent(v) { this.innerHTML = String(v); }
  get textContent() { return this.innerHTML; }
}
class Frag { constructor() { this.kids = []; } appendChild(k) { this.kids.push(k); } }
const els = new Map();
const getEl = (id) => { if (!els.has(id)) els.set(id, new El(id)); return els.get(id); };
global.document = {
  getElementById: getEl,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: (tag) => new El(tag),
  createDocumentFragment: () => new Frag(),
  addEventListener() {},
  body: new El('body'),
  documentElement: new El('html'),
};
global.window = {
  location: { origin: 'https://agent-id-monitor.vercel.app', pathname: '/', search: '', hash: '' },
  addEventListener() {},
  removeEventListener() {},
};
Object.defineProperty(global, 'navigator', { configurable: true, writable: true, value: { clipboard: { writeText: () => Promise.resolve() } } });

const app = require('./app.js');
const { compute24hPulseMetrics } = app;

const live = (n, over) => ({
  contract: '0x' + String(n).repeat(40), name: 'Tok' + n, symbol: 'T' + n, standard: 'BEP-20 Token (18 decimals)',
  token_price_usd: 0.01, market_cap: 80000, lp_usd: 20000, volume_24h: 5000, price_change_24h: 0,
  detected_at: '2026-11-24 10:00:00', block: 1000 + n, ...over,
});

// 1. A huge token that is DOWN never outranks a gainer (the server rule, mirrored for the page-side fallback)
const drops = [
  live(1, { name: 'BigLoser', price_change_24h: -0.96, market_cap: 126000000, lp_usd: 5000000, volume_24h: 2000000 }),
  live(2, { name: 'SmallGainer', price_change_24h: 270, market_cap: 53000, volume_24h: 9000 }),
  live(3, { name: 'MidGainer', price_change_24h: 153, market_cap: 11000000, lp_usd: 900000, volume_24h: 40000 }),
  live(4, { name: 'HugeFlat', price_change_24h: 0, market_cap: 900000000, lp_usd: 9000000, volume_24h: 1000000 }),
];
const ranked = compute24hPulseMetrics(drops, [], 0, null).topRunners.map((r) => r.name);
assert.deepStrictEqual(ranked, ['SmallGainer', 'MidGainer', 'HugeFlat'],
  `gainers first, then the rest by market cap (got ${ranked.join(', ')})`);
console.log('✔ Page-side runner fallback ranks gainers before a bigger loser: PASS');

// 2. The renamed fields: new name wins, old name is the fallback, neither gives 0
const both = compute24hPulseMetrics([], [], 0, {
  since: null, trading_on_dex: 7, graduated: 99, smart_wallet_entries: 5, smart_inflows: 99, flagged_risky: 2, threats_blocked: 99,
  dex_volume_usd: 1234,
});
assert.strictEqual(both.gradCount, 7);
assert.strictEqual(both.totalSmartInflows, 5);
assert.strictEqual(both.threatsBlockedCount, 2);
assert.strictEqual(both.totalDexVol, 1234);
const oldOnly = compute24hPulseMetrics([], [], 0, { since: null, graduated: 4, smart_inflows: 9, threats_blocked: 1 });
assert.deepStrictEqual([oldOnly.gradCount, oldOnly.totalSmartInflows, oldOnly.threatsBlockedCount], [4, 9, 1]);
const zero = compute24hPulseMetrics([], [], 0, { since: null, trading_on_dex: 0, smart_wallet_entries: 0 });
assert.deepStrictEqual([zero.gradCount, zero.totalSmartInflows], [0, 0], 'a real 0 is kept, not replaced by the old name');
const neither = compute24hPulseMetrics([], [], 0, { since: null });
assert.deepStrictEqual([neither.gradCount, neither.totalSmartInflows, neither.threatsBlockedCount], [0, 0, 0]);
console.log('✔ Renamed Pulse fields are read by new name, old name as fallback: PASS');

console.log('test_pulse_fields.js: all assertions passed');
