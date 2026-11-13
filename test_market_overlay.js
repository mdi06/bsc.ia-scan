const assert = require('assert');
const app = require('./app.js');

const FIST = '0xC9882DEF23BC42D53895B8361D0B1EDC7570BC6A';
const seeded = [
  { contract: FIST, name: 'FistToken', token_price_usd: 0.3218, lp_usd: 7841429.48, volume_24h: 1602125.07, total_minted: 200000000 },
  { contract: '0x' + 'b'.repeat(40), name: 'Untracked', token_price_usd: 1 },
];
const market = {
  [FIST.toLowerCase()]: { token_price_usd: 0.2636, market_cap: 48450648, fdv: 48450648, lp_usd: 7114700.09, volume_24h: 597717.62, price_change_24h: null, buys_24h: 652, sells_24h: 1009, buys_1h: null },
};

const updated = app.applyMarketOverlay(seeded, market);
assert.strictEqual(updated, 1, 'only the drop the backend knows is updated');
assert.strictEqual(seeded[0].token_price_usd, 0.2636);
assert.strictEqual(seeded[0].market_cap, 48450648, 'market cap now comes from the backend, not price x supply');
assert.strictEqual(seeded[0].lp_usd, 7114700.09);
assert.strictEqual(seeded[0].total_minted, 200000000, 'fields outside the market set are left alone');
assert.ok(!('price_change_24h' in seeded[0]), 'null values do not overwrite or create fields');
assert.strictEqual(seeded[1].token_price_usd, 1, 'unknown contracts are untouched');
assert.strictEqual(app.applyMarketOverlay(seeded, market), 0, 'idempotent');
console.log('✔ applyMarketOverlay refreshes seeded drops from live market data: PASS');

// A seeded drop whose live record the worker cleared as stale loses its frozen numbers too.
const STALE = '0x' + 'c'.repeat(40);
const seededStale = [{ contract: STALE, name: 'Gone', token_price_usd: 0.002061, lp_usd: 9280.36, market_cap: 247000 }];
const staleMarket = { [STALE]: { token_price_usd: null, market_cap: null, fdv: null, lp_usd: null, volume_24h: null,
  price_change_24h: null, buys_24h: null, sells_24h: null, buys_1h: null, market_stale: true, market_updated_at: null } };
assert.strictEqual(app.applyMarketOverlay(seededStale, staleMarket), 1);
assert.strictEqual(seededStale[0].token_price_usd, null, 'stale: the frozen seed price is cleared');
assert.strictEqual(seededStale[0].lp_usd, null);
assert.strictEqual(seededStale[0].market_stale, true, 'and the stale flag is carried for the badge');
assert.strictEqual(app.applyMarketOverlay(seededStale, staleMarket), 0, 'idempotent');
console.log('✔ applyMarketOverlay clears seeded numbers of a record that went stale: PASS');
