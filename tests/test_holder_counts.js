// Real NFT holder counts: what the table shows before / after a count, and the card's request (/api/holders).
const assert = require('assert');
const app = require('./app.js');

const nft = (extra = {}) => ({ contract: '0x' + 'a1'.repeat(20), standard: 'ERC-721 (NFT Collection)', holders: 1, total_minted: 1551, ...extra });

// placeholder holders on a countable NFT: "Tap to see"
assert.deepStrictEqual(app.effectiveHolders(nft()), { count: null, source: 'tap' });
assert.strictEqual(app.holdersBuysDisplay(nft()).holders, 'Tap to see');
// too big to count, ERC-1155, or already found uncountable: "—"
assert.strictEqual(app.holdersBuysDisplay(nft({ total_minted: 1589678 })).holders, '—');
assert.strictEqual(app.holdersBuysDisplay(nft({ standard: 'ERC-1155' })).holders, '—');
assert.strictEqual(app.holdersBuysDisplay(nft({ holders_uncountable: true })).holders, '—');
// a counted value wins, and is not capped by the minted count rule
assert.strictEqual(app.holdersBuysDisplay(nft({ holders_onchain: 949 })).holders, '949');
assert.deepStrictEqual(app.effectiveHolders(nft({ holders_onchain: 949 })), { count: 949, source: 'onchain' });
// a real stored count, a token, a 1-item NFT: unchanged
assert.deepStrictEqual(app.effectiveHolders(nft({ holders: 81, total_minted: 154 })), { count: 81, source: 'stored' });
// a token's 1 is the discovery seed, not a count: unknown ("—"); a real count stays
assert.deepStrictEqual(app.effectiveHolders({ standard: 'BEP-20 Token (18 decimals)', holders: 1, total_minted: 1e9 }), { count: null, source: 'unknown' });
assert.strictEqual(app.holdersBuysDisplay({ standard: 'BEP-20 Token (18 decimals)', holders: 1, total_minted: 1e9 }).holders, '—');
assert.deepStrictEqual(app.effectiveHolders({ standard: 'BEP-20 Token (18 decimals)', holders: 37, total_minted: 1e9 }), { count: 37, source: 'stored' });
assert.deepStrictEqual(app.effectiveHolders(nft({ total_minted: 1 })), { count: 1, source: 'stored' });
console.log('✔ Holders cell: Tap to see / — / the on-chain count: PASS');

(async () => {
  const urls = [];
  let answer = { success: true, status: 'ok', holders: 949, counted_at: Date.now() / 1000 };
  global.fetch = async (url) => { urls.push(url); return { ok: true, status: 200, json: async () => answer }; };
  const row = nft();
  app.setAllDrops([row]);
  const item = { ...row };                         // the card's own object for the same contract
  assert.strictEqual(app.loadHolderCount(item), true);
  assert.strictEqual(item._holdersCounting, true);
  assert.strictEqual(app.loadHolderCount(item), true);   // same card again: no second request
  await new Promise(r => setTimeout(r, 20));
  assert.strictEqual(urls.length, 1);
  assert.ok(urls[0].endsWith('/api/holders?contract=' + row.contract), urls[0]);
  assert.strictEqual(row.holders_onchain, 949, 'the table row gets the count too');
  assert.strictEqual(item.holders_onchain, 949);
  assert.strictEqual(item._holdersCounting, undefined);
  assert.strictEqual(app.loadHolderCount(item), false, 'a fresh count is not asked again');
  // not countable: never asked
  assert.strictEqual(app.loadHolderCount(nft({ contract: '0x' + 'b2'.repeat(20), total_minted: 2e6 })), false);
  // unsupported answer is remembered on the record
  answer = { success: true, status: 'unsupported' };
  const odd = nft({ contract: '0x' + 'c3'.repeat(20) });
  app.setAllDrops([odd]);
  app.loadHolderCount(odd);
  await new Promise(r => setTimeout(r, 20));
  assert.strictEqual(odd.holders_uncountable, true);
  assert.strictEqual(app.holdersBuysDisplay(odd).holders, '—');
  console.log('✔ The card counts holders once and shares the result: PASS');
})().catch(e => { console.error(e); process.exit(1); });
