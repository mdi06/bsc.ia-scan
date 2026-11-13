// Task 5 step one: the four metric helpers in app.js return the server's answer (record.server_metrics, added by
// api.with_server_metrics) when the record carries it, and calculate by themselves when it does not.
const assert = require('assert');
const app = require('./app.js');

const BNB = 705;
const A = '0x' + 'a'.repeat(40);
const clone = o => JSON.parse(JSON.stringify(o));

// ---------------------------------------------------------------------------------------------- fallback
// No server_metrics (old cached responses, seeded_*.json, feed_snapshot.json): the existing calculation runs.
const paid = { contract: A, name: 'Paid', symbol: 'PAID', standard: 'BEP-20 Token (18 decimals)',
  mint_price_str: '0.0100 BNB', mint_value_wei: '10000000000000000', mint_price_bnb: 0.01,
  token_price_usd: 14.1, lp_usd: 50000, market_cap: 1.5e6 };
{
  const r = app.computeItemProfit(clone(paid), BNB);
  assert.strictEqual(r.mintUsd, 7.05);
  assert.strictEqual(r.secUsd, 14.1);
  assert.ok(Math.abs(r.profit - 7.05) < 1e-9);
  assert.strictEqual(r.currentMultiplier, 2);
  assert.strictEqual(app.getSanitizedMcap(clone(paid)), 1.5e6);
  assert.strictEqual(app.isSecondaryMarketAsset(clone(paid)), true);
  assert.strictEqual(app.isExcludedMajorAsset(clone(paid)), false);
  // the pre-existing exclusion rule still applies
  assert.strictEqual(app.isExcludedMajorAsset({ contract: A, symbol: 'SOL' }), true);
  // and a bare empty object / null still behave as before
  assert.strictEqual(app.isExcludedMajorAsset({}), true);
  assert.strictEqual(app.getSanitizedMcap(null), null);
  assert.strictEqual(app.isSecondaryMarketAsset(null), false);
}

// An unrelated or malformed server_metrics value does not break the fallback
for (const junk of [null, 'x', 5, [], {}]) {
  const d = Object.assign(clone(paid), { server_metrics: junk });
  assert.strictEqual(app.getSanitizedMcap(d), 1.5e6, 'junk ' + JSON.stringify(junk));
  assert.strictEqual(app.isSecondaryMarketAsset(d), true);
  assert.strictEqual(app.isExcludedMajorAsset(d), false);
  assert.strictEqual(app.computeItemProfit(clone(Object.assign({}, d)), BNB).mintUsd, 7.05);
}

// ---------------------------------------------------------------------------------------- server preferred
// The server's answer wins even where it differs from what the browser would calculate (it is the truth).
{
  const d = Object.assign(clone(paid), { server_metrics: {
    excluded: false, market_cap: 123456.5, secondary: false,
    profit: { sec_usd: 99, mint_usd: 3, profit: 96, ath_price_usd: 120, ath_profit_usd: 117, ath_multiplier: 40,
              current_multiplier: 33, is_sold: true, is_free: false },
  } });
  assert.strictEqual(app.getSanitizedMcap(d), 123456.5);
  assert.strictEqual(app.isSecondaryMarketAsset(d), false);   // the client rule (lp > 50) would say true
  assert.strictEqual(app.isExcludedMajorAsset(d), false);
  const r = app.computeItemProfit(d, BNB);
  assert.deepStrictEqual(r, { secUsd: 99, mintUsd: 3, profit: 96, athPrice: 120, athProfit: 117, multiplier: 40,
                              currentMultiplier: 33, isSold: true, isFree: false });
  assert.strictEqual(d.current_profit_usd, undefined, 'the server path does not write into the record');
}

// null from the server is an answer too: unknown market cap stays unknown, unknown cost stays unknown
{
  const d = Object.assign(clone(paid), { market_cap: 1.9e9, lp_usd: 57526.62, server_metrics: {
    excluded: false, market_cap: null, secondary: true,
    profit: { sec_usd: 0, mint_usd: null, profit: null, ath_price_usd: null, ath_profit_usd: null, ath_multiplier: null,
              current_multiplier: null, is_sold: false, is_free: false },
  } });
  assert.strictEqual(app.getSanitizedMcap(d), null);          // not the raw 1.9e9
  const r = app.computeItemProfit(d, BNB);
  assert.deepStrictEqual(r, { secUsd: 0, mintUsd: null, profit: null, athPrice: null, athProfit: null,
                              multiplier: null, currentMultiplier: null, isSold: false, isFree: false });
}

// excluded from the server wins, in both directions
assert.strictEqual(app.isExcludedMajorAsset({ contract: A, symbol: 'SOL', server_metrics: { excluded: false } }), false);
assert.strictEqual(app.isExcludedMajorAsset({ contract: A, symbol: 'OK', server_metrics: { excluded: true } }), true);
assert.strictEqual(app.getSanitizedMcap({ contract: A, symbol: 'SOL', market_cap: 5e6, server_metrics: { excluded: true, market_cap: null } }), null);

// a free mint: multiplier null, isFree true
{
  const d = { contract: A, symbol: 'FREE', server_metrics: { excluded: false, market_cap: null, secondary: true,
    profit: { sec_usd: 0.5, mint_usd: 0, profit: 0.5, ath_price_usd: 0.5, ath_profit_usd: 0.5, ath_multiplier: null,
              current_multiplier: null, is_sold: false, is_free: true } } };
  const r = app.computeItemProfit(d, BNB);
  assert.strictEqual(r.isFree, true);
  assert.strictEqual(r.mintUsd, 0);
  assert.strictEqual(r.multiplier, null);
}

// server_metrics without a profit block (the server's BNB price was not live): the browser calculates the profit,
// but still takes the other three answers from the server
{
  const d = Object.assign(clone(paid), { server_metrics: { excluded: false, market_cap: 777, secondary: true } });
  assert.strictEqual(app.computeItemProfit(d, BNB).mintUsd, 7.05);
  assert.strictEqual(app.getSanitizedMcap(d), 777);
}

// ----------------------------------------------------------------------------------- the market overlay
// Prices replaced by /api/market-data make the server's figures stale: they are dropped and the browser calculates.
{
  const d = Object.assign(clone(paid), { server_metrics: { excluded: false, market_cap: 1, secondary: false } });
  const untouched = Object.assign(clone(paid), { contract: '0x' + 'b'.repeat(40),
    server_metrics: { excluded: false, market_cap: 1, secondary: false } });
  const n = app.applyMarketOverlay([d, untouched], { [A]: { token_price_usd: 20, market_cap: 2.5e6, lp_usd: 60000 } });
  assert.strictEqual(n, 1);
  assert.strictEqual(d.server_metrics, undefined);
  assert.strictEqual(app.getSanitizedMcap(d), 2.5e6);
  assert.ok(untouched.server_metrics, 'a record the overlay did not change keeps the server figures');
}
// an overlay that changes nothing keeps them
{
  const d = Object.assign(clone(paid), { server_metrics: { excluded: false, market_cap: 1, secondary: false } });
  assert.strictEqual(app.applyMarketOverlay([d], { [A]: { token_price_usd: 14.1, market_cap: 1.5e6, lp_usd: 50000 } }), 0);
  assert.ok(d.server_metrics);
}

// ------------------------------------------------------------------------------------ the saved leaderboard
// What is written to localStorage never carries the server's figures (they are stale the next day).
{
  const store = {};
  global.localStorage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; } };
  app.setBnbPriceState(BNB, true);
  const item = Object.assign(clone(paid), { server_metrics: { excluded: false, market_cap: 1, secondary: true } });
  app.setSavedTopGainers([{ contract: A, item }]);
  app.saveSavedTopGainers();
  const written = Object.values(store).join('');
  assert.ok(written.includes(A), 'the saved list was written');
  assert.ok(!written.includes('server_metrics'), 'server_metrics must not be persisted');
  assert.ok(app.getSavedTopGainers()[0].item.server_metrics, 'the in-memory record keeps them');
  delete global.localStorage;
}

// A saved entry is merged with a newer copy that has no server figures: the older copy's figures are not kept
{
  app.setBnbPriceState(BNB, true);
  const stale = Object.assign(clone(paid), { server_metrics: { excluded: false, market_cap: 1, secondary: false } });
  app.setSavedTopGainers([{ contract: A, name: 'Paid', athProfit: 0, profit: 0, secUsd: 0, item: stale }]);
  const fresh = clone(paid);   // no server_metrics
  app.updateSavedTopMarkets([fresh]);
  const saved = app.getSavedTopGainers()[0];
  assert.ok(saved.profit > 0, 'the entry was updated');
  assert.strictEqual(saved.item.server_metrics, undefined);
}

console.log('server metrics tests passed');
