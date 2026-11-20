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
