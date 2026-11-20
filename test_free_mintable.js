const assert = require('assert');
const app = require('./app.js');

const free = (extra = {}) => ({ contract: '0x' + Math.random().toString(16).slice(2), has_free_mint: true, mint_price_str: '⭐ FREE', risk_level: 'SAFE', ...extra });

// FREE pill = free AND mintable now
assert.strictEqual(app.isFreeMintableNow(free()), true, 'clean free mint is mintable');
assert.strictEqual(app.isFreeMintableNow(free({ risk_level: 'RUG' })), false, 'rug excluded');
assert.strictEqual(app.isFreeMintableNow(free({ risk_level: 'HIGH' })), false, 'high risk excluded');
assert.strictEqual(app.isFreeMintableNow(free({ risk_badge: '🚨 Honeypot' })), false, 'honeypot badge excluded');
assert.strictEqual(app.isFreeMintableNow(free({ security: { is_fund_drain: true } })), false, 'fund drain excluded');
assert.strictEqual(app.isFreeMintableNow(free({ is_paused: true })), false, 'paused excluded');
assert.strictEqual(app.isFreeMintableNow(free({ standard: 'ERC721', max_supply: 100, total_minted: 100 })), false, 'sold-out NFT excluded');
assert.strictEqual(app.isFreeMintableNow(free({ standard: 'ERC721', max_supply: 100, total_minted: 40 })), true, 'NFT with slots left kept');
assert.strictEqual(app.isFreeMintableNow({ mint_price_str: '0.01 BNB', mint_value_wei: '10000000000000000', mint_price_bnb: 0.01 }), false, 'paid mint excluded');
console.log('✔ FREE pill keeps only free, open, safe drops: PASS');

// An ERC-20 whose supply was fully pre-minted is not "sold out" (same rule as computeSniperReady)
assert.strictEqual(app.isDropSoldOut({ standard: 'ERC20', max_supply: 1000, total_minted: 1000 }), false);
assert.strictEqual(app.isDropSoldOut({ standard: 'ERC20', max_supply: 1000, total_minted: 1000, cap: true }), true);
console.log('✔ Pre-minted token supply is not treated as sold out: PASS');

// Filter count matches the filter
app.setAllDrops([free(), free({ risk_level: 'RUG' }), free({ standard: 'ERC721', max_supply: 5, total_minted: 5 })]);
assert.strictEqual(app.countDropsByFilter(app.getAllDrops(), null).freeMints, 1);
console.log('✔ FREE count only counts mintable drops: PASS');

// Secondary badge = loaded rows, not the server total
assert.strictEqual(app.countDropsByFilter([], { secondary: 2809 }).secondary, 0);
console.log('✔ Secondary count is what the table can show: PASS');

// detected_at is UTC
assert.strictEqual(app.parseServerUtc('2026-11-14 02:11:57').toISOString(), '2026-11-14T02:11:57.000Z');
assert.strictEqual(app.parseServerUtc('2026-11-14T02:11:57+01:00').toISOString(), '2026-11-14T01:11:57.000Z');
assert.strictEqual(app.parseServerUtc('LIVE'), null);
console.log('✔ Server timestamps parse as UTC: PASS');

// Mint terms (record.mint_terms from the worker)
const now = Date.now() / 1000;
const t = app.getMintTerms({ max_supply: 1000, total_minted: 11, mint_terms: { per_wallet: 10, gate: 'agent_id', end: now + 7200 } });
assert.strictEqual(t.slotsLeft, 989);
assert.strictEqual(t.perWallet, 10);
assert.strictEqual(t.gate, 'agent_id');
assert.strictEqual(t.ended, false);
assert.strictEqual(app.getMintTerms({ max_supply: 0, total_minted: 5 }).slotsLeft, null, 'no cap: slots unknown, not 0');
assert.strictEqual(app.getMintTerms({ mint_terms: { gate: 'bogus' } }).gate, null, 'unknown gate values are dropped');
assert.match(app.mintTimingLabel(app.getMintTerms({ mint_terms: { start: now + 7200 } })), /Starts in 2h/);
assert.strictEqual(app.mintTimingLabel(app.getMintTerms({ mint_terms: { end: now - 10 } })), '⌛ Ended');
console.log('✔ Mint terms: slots left, limit, gate, timing: PASS');

const chips = app.mintTermsChipsHtml({ mint_price_str: '⭐ FREE', max_supply: 1000, total_minted: 11, mint_terms: { per_wallet: 10, gate: 'open' } });
assert.match(chips, /989 left/);
assert.match(chips, /Max 10\/wallet/);
assert.match(chips, /Open to all/);
assert.strictEqual(app.mintTermsChipsHtml({ mint_price_str: 'DEX Traded', max_supply: 1000, total_minted: 1 }), '', 'no chips for DEX tokens');
assert.strictEqual(app.mintTermsChipsHtml({ mint_price_str: '⭐ FREE' }), '', 'nothing known: no chips');
console.log('✔ Mint terms chips: PASS');

// FREE & MINTABLE NOW honours a known mint window
assert.strictEqual(app.isFreeMintableNow(free({ mint_terms: { end: now - 60 } })), false, 'ended mint excluded');
assert.strictEqual(app.isFreeMintableNow(free({ mint_terms: { start: now + 3600 } })), false, 'not started yet excluded');
assert.strictEqual(app.isFreeMintableNow(free({ mint_terms: { start: now - 60, end: now + 3600 } })), true, 'inside window kept');
console.log('✔ FREE pill respects the mint window: PASS');

// Sort by smart-wallet mints (🧠 column), inside the FREE filter
assert.strictEqual(app.smartWalletCount({ smart_wallets: ['a', 'b'], smart_minters: ['x'] }), 2);
assert.strictEqual(app.smartWalletCount({ smart_minters: ['x', 'y', 'z'] }), 3);
assert.strictEqual(app.smartWalletCount({}), 0);
app.setAllDrops([
  free({ contract: '0xa', block: 3, smart_wallets: ['w1'] }),
  free({ contract: '0xb', block: 2, smart_wallets: ['w1', 'w2', 'w3', 'w4', 'w5'] }),
  free({ contract: '0xc', block: 5 }),
  free({ contract: '0xd', block: 1, smart_minters: ['w1', 'w2', 'w3'] }),
  free({ contract: '0xe', block: 4, smart_wallets: ['w1'] }),
]);
app.setActiveFilter('FREE');
app.setSortMode('SMART_DESC');
assert.deepStrictEqual(app.getFilteredDrops().map(d => d.contract), ['0xb', '0xd', '0xe', '0xa', '0xc'], 'most smart wallets first, ties newest first, none last');
app.setSortMode('SMART_ASC');
assert.deepStrictEqual(app.getFilteredDrops().map(d => d.contract), ['0xe', '0xa', '0xd', '0xb', '0xc'], 'fewest first, none still last');
app.setSortMode('NEWEST');
app.setActiveFilter('ALL');
console.log('✔ Sort by smart-wallet mints: PASS');
