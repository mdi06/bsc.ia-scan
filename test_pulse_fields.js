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
const { compute24hPulseMetrics, comparePulseLaunches, pulseLaunchRow, isBackedVolume } = app;

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

// 2. The renamed fields are read by their new names only; the old names (graduated, smart_inflows, threats_blocked) are no longer sent or read
const both = compute24hPulseMetrics([], [], 0, {
  since: null, trading_on_dex: 7, graduated: 99, smart_wallet_entries: 5, smart_inflows: 99, flagged_risky: 2, threats_blocked: 99,
  dex_volume_usd: 1234,
});
assert.strictEqual(both.gradCount, 7);
assert.strictEqual(both.totalSmartInflows, 5);
assert.strictEqual(both.threatsBlockedCount, 2);
assert.strictEqual(both.totalDexVol, 1234);
const oldOnly = compute24hPulseMetrics([], [], 0, { since: null, graduated: 4, smart_inflows: 9, threats_blocked: 1 });
assert.deepStrictEqual([oldOnly.gradCount, oldOnly.totalSmartInflows, oldOnly.threatsBlockedCount], [0, 0, 0], 'old names are ignored');
const zero = compute24hPulseMetrics([], [], 0, { since: null, trading_on_dex: 0, smart_wallet_entries: 0 });
assert.deepStrictEqual([zero.gradCount, zero.totalSmartInflows], [0, 0], 'a real 0 is kept, not replaced by the old name');
const neither = compute24hPulseMetrics([], [], 0, { since: null });
assert.deepStrictEqual([neither.gradCount, neither.totalSmartInflows, neither.threatsBlockedCount], [0, 0, 0]);
console.log('✔ Renamed Pulse fields are read by new name, old name as fallback: PASS');

console.log('test_pulse_fields.js: all assertions passed');

// 4. Card 2 (new launches): gainers first, wash-traded pools left out (twin of api.pulse_launch_rank / pulse_launch_row)
{
  const row = (n, chg, vol, lp) => ({ name: 'L' + n, price_change_24h: chg, volume_24h: vol, lp_usd: lp });
  const sorted = [row(1, -52.9, 616165, 11033), row(3, 194, 202617, 14401), row(4, -74.2, 246600, 27714), row(5, 12, 60000, 50000)]
    .sort(comparePulseLaunches).map((r) => r.name);
  assert.deepStrictEqual(sorted, ['L3', 'L5', 'L1', 'L4']);
  const base = { contract: '0x' + '7'.repeat(40), standard: 'BEP-20 Token (18 decimals)', token_price_usd: 0.01, detected_at: '2026-11-24 10:00:00' };
  assert.strictEqual(pulseLaunchRow({ ...base, lp_usd: 4254, volume_24h: 352802 }), null, 'a pool turning over 83x is wash trading');
  assert.ok(pulseLaunchRow({ ...base, lp_usd: 14401, volume_24h: 202617 }), 'a pool turning over 14x is kept');
  console.log('✔ New launches: gainers first, wash-traded pools left out: PASS');
}

// 5. The Secondary banner's volume uses the Pulse's rule: flagged tokens, unbacked pools and wash trading are not volume
{
  const base = { contract: '0x' + '8'.repeat(40), standard: 'BEP-20 Token (18 decimals)' };
  assert.strictEqual(isBackedVolume({ ...base, lp_usd: 20000, volume_24h: 50000 }), true);
  assert.strictEqual(isBackedVolume({ ...base, lp_usd: 20000, volume_24h: 50000, risk_level: 'RUG' }), false, 'a flagged token');
  assert.strictEqual(isBackedVolume({ ...base, lp_usd: 20000, volume_24h: 50000, risk_badge: '🍯 Honeypot' }), false, 'a honeypot');
  assert.strictEqual(isBackedVolume({ ...base, lp_usd: 300, volume_24h: 900 }), false, 'a pool under $500');
  assert.strictEqual(isBackedVolume({ ...base, lp_usd: 4254, volume_24h: 352802 }), false, 'a pool turned over 83x');
  assert.strictEqual(isBackedVolume({ ...base, lp_usd: 1000, volume_24h: 50000 }), true, 'exactly 50x is still kept');
  console.log('✔ Secondary volume counts only backed volume: PASS');
}
