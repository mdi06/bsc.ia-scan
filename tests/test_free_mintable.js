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
assert.doesNotMatch(chips, /Open to all/, 'open gate is the name badge, not a chip');
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

// Mobile card: terms row + one-tap Mint button
const mintable = free({ contract: '0xm1', mint_calldata: '0x1249c58b', max_supply: 100, total_minted: 10, mint_terms: { gate: 'open', per_wallet: 2 } });
assert.strictEqual(app.canMintFromCard(mintable), true);
assert.strictEqual(app.canMintFromCard(free({ mint_calldata: '0x1249c58b', risk_level: 'RUG' })), false, 'no button on a rug');
assert.strictEqual(app.canMintFromCard(free({ mint_calldata: '0x1249c58b', standard: 'ERC721', max_supply: 5, total_minted: 5 })), false, 'no button when sold out');
assert.strictEqual(app.canMintFromCard(free({ mint_calldata: '0x1249c58b', mint_terms: { end: now - 5 } })), false, 'no button after the window');
assert.strictEqual(app.canMintFromCard({ mint_price_str: 'DEX Traded', mint_calldata: '0x' }), false, 'no button on a DEX-only token');
const cell = app.cardTermsCellHtml(mintable);
assert.match(cell, /class="col-terms"/);
assert.match(cell, /90 left/);
assert.match(cell, /openMintFromCard\('0xm1', event\)/);
assert.match(cell, /Mint free/);
assert.match(app.cardTermsCellHtml(free({ mint_calldata: '0x1249c58b', mint_terms: { start: now + 3600 } })), /Mint soon/);
assert.strictEqual(app.cardTermsCellHtml({ mint_price_str: 'DEX Traded' }), '<td class="col-terms"></td>', 'empty cell (hidden by :empty)');
console.log('✔ Mobile card terms row and Mint button: PASS');

// Probe v2 states: switched-off mint, refused as ended / sold out, unusable mint call, paid with unread price
const closedDrop = free({ mint_calldata: '0x1249c58b', mint_terms: { gate: 'open', open: false } });
assert.strictEqual(app.isFreeMintableNow(closedDrop), false, 'switched-off mint is not mintable now');
assert.match(app.mintTermsChipsHtml(closedDrop), /Mint closed/);
assert.match(app.cardTermsCellHtml(closedDrop), /Mint soon/);
const endedDrop = free({ mint_calldata: '0x1249c58b', mint_terms: { gate: 'unknown', state: 'ended' } });
assert.strictEqual(app.isFreeMintableNow(endedDrop), false);
assert.strictEqual(app.canMintFromCard(endedDrop), false);
assert.match(app.mintTermsChipsHtml(endedDrop), /Ended/);
const soldDrop = free({ mint_calldata: '0x1249c58b', mint_terms: { gate: 'unknown', state: 'sold_out' } });
assert.strictEqual(app.isFreeMintableNow(soldDrop), false);
assert.match(app.mintTermsChipsHtml(soldDrop), /Sold out/);
const badCall = free({ mint_calldata: '0x6080', mint_terms: { gate: 'unknown', mint_call: 'invalid' } });
assert.strictEqual(app.canMintFromCard(badCall), false, 'no Mint button for an unusable mint call');
const paidTbd = { mint_price_str: 'TBD', mint_calldata: '0x1249c58b', mint_terms: { gate: 'agent_id', paid: true } };
assert.match(app.mintTermsChipsHtml(paidTbd), /💎 Paid/);
assert.ok(!/Agent ID needed/.test(app.mintTermsChipsHtml(paidTbd)), 'Agent ID is the badge next to the name, not a second chip');
assert.match(app.gateBadgeHtml(paidTbd), /Agent ID/);
console.log('✔ Probe v2 states (closed, ended, sold out, unusable call, paid): PASS');

// Agent ID badge next to the name, on every row
assert.match(app.gateBadgeHtml({ mint_terms: { gate: 'agent_id' } }), /🪪 Agent ID/);
assert.match(app.gateBadgeHtml({ mint_terms: { gate: 'agent_id', gate_from: 'bytecode' } }), /contract code checks/);
assert.match(app.gateBadgeHtml({ mint_terms: { gate: 'whitelist' } }), /Whitelist/);
assert.match(app.gateBadgeHtml({ mint_terms: { gate: 'open' } }), /gate-badge-open[^>]*>🟢 Open to all/);
assert.match(app.gateBadgeHtml({ mint_terms: { gate: 'whitelist', gate_from: 'holders' } }), /Only holders of the project/);
assert.strictEqual(app.getMintTerms({ mint_terms: { mint_call: 'none' } }).noMintFunction, true);
assert.strictEqual(app.getMintTerms({ mint_terms: { mint_call: 'none' } }).invalidCall, true, 'no mint function: no Mint button');
assert.strictEqual(app.gateBadgeHtml({}), '');
console.log('✔ Agent ID badge next to the drop name: PASS');

// Holders / buys: no placeholder "1 / 1"
let hbv = app.holdersBuysDisplay({ standard: 'ERC-721 (NFT Collection)', holders: 1, buys_24h: 1, total_minted: 0 });
assert.strictEqual(hbv.holders, '0', 'an NFT with 0 minted has 0 holders');
assert.strictEqual(hbv.buys, '—', 'no market data: no placeholder buy count');
hbv = app.holdersBuysDisplay({ standard: 'BEP-20 Token', holders: 76, buys_24h: 12, pair_url: 'https://dexscreener.com/x', total_minted: 1e10 });
assert.strictEqual(hbv.holders, '76');
assert.strictEqual(hbv.buys, '12', 'market data: real buy count');
hbv = app.holdersBuysDisplay({ standard: 'ERC-721', holders: 40, buys_24h: 1, total_minted: 61 });
assert.strictEqual(hbv.holders, '40', 'holders below minted stay as they are');
console.log('✔ Holders / buys without placeholders: PASS');

// Slots left: a fully pre-minted token is not "sold out"
assert.strictEqual(app.getMintTerms({ standard: 'BEP-20 Token (18 decimals)', max_supply: 1e10, total_minted: 1e10 }).slotsLeft, null);
assert.ok(!/Sold out/.test(app.mintTermsChipsHtml({ mint_price_str: '⭐ FREE', standard: 'BEP-20 Token', max_supply: 1e10, total_minted: 1e10, mint_terms: { gate: 'agent_id' } })));
assert.strictEqual(app.getMintTerms({ standard: 'ERC-721', max_supply: 100, total_minted: 100 }).slotsLeft, 0, 'an NFT at its cap is sold out');
assert.strictEqual(app.getMintTerms({ standard: 'BEP-20 Token', max_supply: 1e10, total_minted: 1e10, cap: true }).slotsLeft, 0, 'a token with a mint cap at its cap is sold out');
console.log('✔ Slots left respects pre-minted tokens: PASS');
assert.strictEqual(app.isPreMintedToken({ standard: 'BEP-20 Token', max_supply: 1e10, total_minted: 1e10 }), true);
assert.strictEqual(app.isPreMintedToken({ standard: 'ERC-721', max_supply: 100, total_minted: 100 }), false);
assert.strictEqual(app.isPreMintedToken({ standard: 'BEP-20 Token', max_supply: 1e10, total_minted: 5e9 }), false);
console.log('✔ One pre-minted rule for chips, filter and progress: PASS');
assert.match(app.gateBadgeHtml({ mint_terms: { gate: 'agent_id', gate_from: 'earlier_check' } }), /earlier check/);
console.log('✔ Badge explains a gate kept from an earlier check: PASS');

// Fresher copies of a drop refresh its mint terms (snapshot rows used to keep none)
const snapRow = { contract: '0xs1', mint_calldata: '0x6080' };
assert.strictEqual(app.refreshDropTerms(snapRow, { contract: '0xs1', mint_terms: { gate: 'agent_id', checked_at: 200, mint_call: 'repaired' }, mint_calldata: '0x1249c58b' }), true);
assert.strictEqual(snapRow.mint_terms.gate, 'agent_id');
assert.strictEqual(snapRow.mint_calldata, '0x1249c58b', 'the repaired call comes along');
assert.strictEqual(app.refreshDropTerms(snapRow, { contract: '0xs1', mint_terms: { gate: 'unknown', checked_at: 100 } }), false, 'an older check never replaces a newer one');
assert.strictEqual(snapRow.mint_terms.gate, 'agent_id');
app.setAllDrops([{ contract: '0xS2', block: 1 }]);
app.appendUniqueDrops([{ contract: '0xs2', block: 1, mint_terms: { gate: 'open', checked_at: 5 } }]);
assert.strictEqual(app.getAllDrops().length, 1, 'still no duplicate row');
assert.strictEqual(app.getAllDrops()[0].mint_terms.gate, 'open', 'Load Older Drops refreshes terms of rows already shown');
console.log('✔ Mint terms refresh from fresher copies; no duplicate Agent ID chip: PASS');

// "Showing X of Y" above the table: Y is the view's server total when it pages from the server, else the filtered rows
{
  const rows = n => Array.from({ length: n }, () => free());
  app.setActiveTab('RADAR');
  app.setActiveFilter('ALL');
  app.setGlobalStats({ total: 12647 });
  assert.deepStrictEqual(app.tableShowingCounts(rows(150)), { shown: 60, total: 12647, more: true }, 'whole feed: server total');
  app.setActiveFilter('SNIPER');
  assert.deepStrictEqual(app.tableShowingCounts(rows(10)), { shown: 10, total: 10, more: false }, 'local filter: filtered rows');
  assert.deepStrictEqual(app.tableShowingCounts(rows(150)), { shown: 60, total: 150, more: true }, 'local filter, more rows than one batch');
  app.setActiveFilter('ALL');
  app.setGlobalStats(null);
  console.log('✔ Table "Showing X of Y" uses the current view\'s total: PASS');
}

// FREE tab loads its own list from the server (/api/feed?filter=free_mintable) and pages through it
(async () => {
  const pages = {
    0: { projects: [free({ block: 50 }), free({ block: 40 })], has_more: true, filter_total: 3 },
    2: { projects: [free({ block: 30 })], has_more: false, filter_total: 3 },
  };
  const urls = [];
  global.fetch = async (url) => {
    urls.push(url);
    const off = Number(new URL(url).searchParams.get('offset'));
    return { ok: true, status: 200, json: async () => pages[off] };
  };
  app.setAllDrops([]);
  app.setActiveTab('RADAR');
  app.setActiveFilter('FREE');
  await app.loadFreeMintsPage();
  assert.ok(urls[0].includes('filter=free_mintable') && urls[0].includes('offset=0') && urls[0].includes('view=list'), urls[0]);
  assert.strictEqual(app.getAllDrops().length, 2);
  let paged = app.serverPagedList();
  assert.strictEqual(paged.hasMore, true);
  assert.strictEqual(paged.total, 3);
  await paged.load();
  assert.ok(urls[1].includes('offset=2'), urls[1]);
  assert.strictEqual(app.getAllDrops().length, 3);
  assert.strictEqual(app.serverPagedList().hasMore, false);
  await app.loadFreeMintsPage();                       // nothing left: no request
  assert.strictEqual(urls.length, 2);
  app.setActiveFilter('LP');
  assert.strictEqual(app.serverPagedList(), null, 'other filters only page what is loaded');
  app.setActiveFilter('ALL');
  console.log('✔ FREE tab pages the server free-mint list: PASS');

  // badge: the server total, never below what is loaded
  assert.strictEqual(app.countDropsByFilter([free()], { free_mintable: 1217 }).freeMints, 1217);
  assert.strictEqual(app.countDropsByFilter([free(), free()], { free_mintable: 0 }).freeMints, 2);
  console.log('✔ FREE badge uses the server count: PASS');
})().catch(e => { console.error(e); process.exit(1); });
