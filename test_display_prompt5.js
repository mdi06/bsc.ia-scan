// Frontend display fixes (metrics audit, Prompt 5): formatters, BNB price handling, modal mint rules, table prices,
// market cap labels, stale markers, holders / buys, hard-coded cards. Run with: node test_display_prompt5.js
const assert = require('assert');

// ---- a minimal DOM: only what openModal / renderTable touch
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
  appendChild(k) { this.innerHTML += k instanceof Frag ? k.kids.map(c => c.innerHTML).join('') : (k && k.innerHTML) || ''; }
  replaceChildren(f) { this.innerHTML = f instanceof Frag ? f.kids.map(c => c.innerHTML).join('') : ''; }
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
  createElement: () => new El('x'),
  createDocumentFragment: () => new Frag(),
  addEventListener() {},
  body: new El('body'),
  documentElement: new El('html'),
};

const app = require('./app.js');
const {
  formatCount, formatCryptoPrice, formatBnb, openModal, renderTable, setAllDrops, setActiveTab,
  setBnbPriceState, getBnbPriceState, bnbToUsd, formatBnbAsUsd, mcapLabel, marketFreshnessHtml, formatMarketAge,
} = app;

function modalHtml(item) {
  const content = getEl('modalContent');
  content.innerHTML = '';
  openModal(item);
  return content.innerHTML;
}
function tableHtml(items, tab = 'RADAR') {
  const body = getEl('tableBody');
  body.innerHTML = '';
  setAllDrops(items);
  setActiveTab(tab);
  renderTable();
  return body.innerHTML;
}

// ------------------------------------------------------------------------------------------------ L1-L3: formatCount
assert.strictEqual(formatCount(999950), '1.0M', 'rounding across tiers: 999950 is 1.0M, not 1000.0K');
assert.strictEqual(formatCount(999949), '999.9K');
assert.strictEqual(formatCount(1e12), '1.0T');
assert.strictEqual(formatCount(2.5e15), '2.5Q');
assert.strictEqual(formatCount(-1500), '-1.5K', 'negatives are abbreviated');
assert.strictEqual(formatCount(-999950), '-1.0M');
assert.strictEqual(formatCount(0), '0');
assert.strictEqual(formatCount(5), '5');
['x', null, undefined, NaN, -Infinity].forEach(v => assert.strictEqual(formatCount(v === 'x' ? 'abc' : v), '—', `formatCount(${v}) is "—"`));
assert.strictEqual(formatCount(Infinity), '∞');
assert.strictEqual(formatCount(1e20), '∞', 'the uncapped supply sentinel');
console.log('✔ formatCount: tiers, no cross-tier rounding, negatives, unknown is "—", -Infinity is not "∞": PASS');

// ------------------------------------------------------------------------------------------------ formatCryptoPrice ladder
const ladder = [1e-14, 1e-13, 1e-12, 5e-12, 1e-10, 1.555e-10, 9e-9, 1e-8, 5e-7, 1e-6, 5e-5, 1e-4, 0.0099999999, 0.01, 0.5,
  0.99999, 1, 12.345, 999.999, 1234567.891, 1e9, 1e12, 1e15];
let prev = null;
ladder.forEach((v) => {
  const out = formatCryptoPrice(v);
  assert(/^<?\$[0-9,]+(\.[0-9]+)?$/.test(out) || out.startsWith('<$'), `ladder ${v} -> ${out} is a well-formed price`);
  assert(!/^\$0\.0+$/.test(out) || v === 0, `ladder ${v} never renders as zero (${out})`);
  assert(!out.includes('e'), `no exponent notation (${out})`);
});
assert.strictEqual(formatCryptoPrice(1.555e-10), '$0.0000000001555', 'XGirl-size price keeps its digits');
assert.strictEqual(formatCryptoPrice(0.99999), '$1.00', '0.99999 is not "$1.0000"');
assert.strictEqual(formatCryptoPrice(1234567.891), '$1,234,567.89', 'thousands separators');
assert.strictEqual(formatCryptoPrice(1e15), '$1,000,000,000,000,000.00');
assert.strictEqual(formatCryptoPrice(-1234.5), '-$1,234.50');
assert.strictEqual(formatCryptoPrice(0), '$0.00');
assert.strictEqual(formatCryptoPrice(0, true), '+$0.00');
[NaN, Infinity, -Infinity, null, undefined].forEach(v => assert.strictEqual(formatCryptoPrice(v), '—', `formatCryptoPrice(${v})`));
assert.strictEqual(formatCryptoPrice(1e-14), '<$0.000000000001');
console.log('✔ formatCryptoPrice ladder 1e-14..1e15, edge values, no "$0.000000", NaN/Infinity are "—": PASS');

assert.strictEqual(formatBnb(0.30000000000000004), '0.3', 'floor in BNB without float noise');
assert.strictEqual(formatBnb(0.00001), '0.00001', '0.00001 BNB is not "0.0000"');
assert.strictEqual(formatBnb(1.23456), '1.2346');
assert.strictEqual(formatBnb(0), '0');
assert.strictEqual(formatBnb(null), '—');
assert.strictEqual(formatCryptoPrice(0.004), '$0.004000', 'a $0.004 floor is not "$0.00"');
console.log('✔ formatBnb / small floors: PASS');

// ------------------------------------------------------------------------------------------------ M8: no made-up BNB price
setBnbPriceState(null, false);
assert.strictEqual(bnbToUsd(2), null, 'no price: no USD value');
assert.strictEqual(formatBnbAsUsd(2), '—', 'no BNB price renders "—", not a 705-based number');
setBnbPriceState(600, false);
assert.strictEqual(formatBnbAsUsd(2), '≈$1,200.00', 'a stale price is marked with ≈');
setBnbPriceState(600, true);
assert.strictEqual(formatBnbAsUsd(2), '$1,200.00');
console.log('✔ BNB price: null until known, "—" without it, "≈" when stale: PASS');

(async () => {
  // refreshBnbPrice: never adopts the server's static fallback (live=false and no as_of)
  setBnbPriceState(null, false);
  global.fetch = async () => ({ ok: true, json: async () => ({ success: true, bnb_price_usd: 705.0, live: false, as_of: null }) });
  await app.refreshBnbPrice();
  assert.strictEqual(getBnbPriceState().price, null, 'the 705 fallback is never used');
  global.fetch = async () => ({ ok: true, json: async () => ({ success: true, bnb_price_usd: 660.0, live: true, as_of: 1790000000 }) });
  await app.refreshBnbPrice();
  assert.deepStrictEqual(getBnbPriceState(), { price: 660.0, live: true, asOf: 1790000000 });
  global.fetch = async () => ({ ok: true, json: async () => ({ success: true, bnb_price_usd: 660.0, live: false, as_of: 1790000000 }) });
  await app.refreshBnbPrice();
  assert.strictEqual(getBnbPriceState().live, false, 'a stale server price flips the client to not-live (shown with ≈)');
  console.log('✔ refreshBnbPrice: fallback ignored, live / stale tracked: PASS');

  setBnbPriceState(660.0, true);

  // ---------------------------------------------------------------------------------------- M7: modal mint rules
  const base = { standard: 'ERC-721 (NFT Collection)', mint_calldata: '0x1249c58b', mint_value_wei: '0' };
  const marsid = { ...base, contract: '0xcbac4fac33afb361e836e6a0120f5a5aac9122b3', name: 'Mars Nexus Identity', symbol: 'MARSID',
    mint_price_str: 'TBD', mint_price_bnb: 0.000478250988556096, total_minted: 9903, max_supply: 12000 };
  const marsidHtml = modalHtml(marsid);
  assert(!marsidHtml.includes('FREE MINT'), 'MARSID (TBD) must not get a FREE MINT button');
  assert(marsidHtml.includes('0.0004783 BNB'), 'its known BNB mint price is shown (the same rule the profit code uses)');

  const tbd = { ...base, contract: '0x' + '7'.repeat(40), name: 'Unknown Cost', symbol: 'UNK', mint_price_str: 'TBD', mint_price_bnb: 0 };
  const tbdHtml = modalHtml(tbd);
  assert(!tbdHtml.includes('FREE MINT'), 'a TBD record is not "FREE MINT"');
  assert(tbdHtml.includes('TBD'), 'a TBD record shows TBD');

  const dex = { standard: 'BEP-20 Token (18 decimals)', contract: '0x39b58c3930d4352b2e4d8697ea6188c864455555', name: '大狗币', symbol: 'DAGOU',
    mint_price_str: 'DEX Traded', mint_value_wei: '0', mint_calldata: '0x1249c58b', token_price_usd: 69.65, lp_usd: 13620.69 };
  const dexHtml = modalHtml(dex);
  assert(!dexHtml.includes('FREE MINT'), 'a DEX-traded token is not "FREE MINT"');
  assert(!dexHtml.includes('modalMintBtn'), 'a DEX-traded token has no mint button');
  assert(dexHtml.includes('DEX TRADED'), 'it says DEX TRADED instead of a mint cost');

  const free = { standard: 'BEP-20 Token (18 decimals)', contract: '0x' + '8'.repeat(40), name: 'Free Token', symbol: 'FRE',
    mint_price_str: '⭐ FREE', has_free_mint: true, mint_value_wei: '0', mint_calldata: '0x1249c58b' };
  const freeHtml = modalHtml(free);
  assert(freeHtml.includes('FREE MINT') && freeHtml.includes('modalMintBtn'), 'a verified free mint keeps its FREE MINT button');
  const freeStr = { ...free, contract: '0x' + '9'.repeat(40), has_free_mint: false, mint_price_str: 'FREE' };
  assert(modalHtml(freeStr).includes('FREE MINT'), 'a "FREE" price string is a free mint too (same rule as the table)');

  const paid = { standard: 'BEP-20 Token (18 decimals)', contract: '0x' + 'a'.repeat(40), name: 'Paid', symbol: 'PAID',
    mint_price_str: '0.0100 BNB', mint_price_bnb: 0.01, mint_value_wei: '10000000000000000', mint_calldata: '0x1249c58b' };
  const paidHtml = modalHtml(paid);
  assert(paidHtml.includes('0.01 BNB (~$6.60)'), 'paid mint: BNB amount and a USD value from the live price');
  setBnbPriceState(null, false);
  assert(modalHtml(paid).includes('0.01 BNB (~—)'), 'paid mint without a BNB price: "—", not a made-up USD number');
  setBnbPriceState(660.0, true);
  console.log('✔ Modal mint rules (M7): TBD / DEX Traded are never "FREE MINT", MARSID has no free button: PASS');

  // ---------------------------------------------------------------------------------------- L2: supply label
  const uncapped = { ...free, contract: '0x' + 'b'.repeat(40), name: 'Uncapped', total_minted: 18e9, max_supply: null };
  assert(modalHtml(uncapped).includes('18.0B</strong> / ∞'), 'no cap: "18.0B / ∞", not "18.0B / 0"');
  const sentinel = { ...free, contract: '0x' + 'c'.repeat(40), name: 'Sentinel', total_minted: 18e9, max_supply: 1.15e77 };
  assert(modalHtml(sentinel).includes('18.0B</strong> / ∞'), 'a 1e77 cap sentinel is "∞"');
  console.log('✔ Modal progress label: "18.0B / ∞": PASS');

  // ---------------------------------------------------------------------------------------- M6: table price == modal price
  const xgirl = { standard: 'BEP-20 Token (18 decimals)', contract: '0x733ec70cf26c6b46dbd0df3acffe7c95f45bc2ab', name: 'XGirl', symbol: 'XGIRL',
    mint_price_str: 'DEX Traded', mint_value_wei: '0', token_price_usd: 1.555e-10, lp_usd: 4200, volume_24h: 300, market_cap: 1555,
    holders: 12, buys_24h: 0, total_minted: 1e10, pair_url: 'https://dexscreener.com/bsc/0xabc' };
  const tHtml = tableHtml([xgirl]);
  assert(tHtml.includes('$0.0000000001555'), 'the table shows XGirl\'s real price');
  assert(!/\$0\.000000(?![0-9])/.test(tHtml), 'the table never shows a price of exactly $0.000000');
  assert(modalHtml(xgirl).includes('$0.0000000001555'), 'the modal shows the same price');
  console.log('✔ XGirl price renders identically in table and modal (M6): PASS');

  // ---------------------------------------------------------------------------------------- L1: holders / buys
  const rowHtml = tableHtml([{ ...xgirl, holders: 0, buys_24h: 0 }]);
  assert(/class="hld">0<\/span>[\s\S]*class="buy">0<\/span>/.test(rowHtml) || rowHtml.includes('0 / 0'), 'a real zero renders 0, not 1');
  const unknownHtml = tableHtml([{ ...xgirl, contract: '0x' + 'd'.repeat(40), holders: null, buys_24h: undefined, total_minted: 1.04e25 }]);
  assert(!unknownHtml.includes('10.4'), 'the total supply is never shown as a buy / holder count');
  assert(unknownHtml.includes('—'), 'unknown counts render "—"');
  console.log('✔ Holders / buys: unknown is "—", zero is 0, never total_minted or a made-up 1: PASS');

  // ---------------------------------------------------------------------------------------- H9 display: one market cap
  const mlee = { standard: 'BEP-20 Token (18 decimals)', contract: '0xc4bf2518aa953304170753388f83277eb9588a7a', name: 'MLEE DAO', symbol: 'MDAO',
    mint_price_str: 'DEX Traded', token_price_usd: 0.1008, lp_usd: 57526.62, market_cap: 1892334511, fdv: 1892334511, total_minted: 18e9,
    pair_url: 'https://dexscreener.com/bsc/0xff3f', volume_24h: 0.8 };
  const mleeTable = tableHtml([mlee]);
  assert(!mleeTable.includes('$1.9B'), 'the RADAR column does not show the unsanitized $1.9B market cap');
  const mleeModal = modalHtml(mlee);
  assert(!mleeModal.includes('Fair Mint Phase'), 'a token that already trades never shows "Fair Mint Phase"');
  assert(!mleeModal.includes('$1.9B'), 'the modal does not show the raw $1.9B either');
  const fdvOnly = { ...mlee, contract: '0x' + '1'.repeat(40), name: 'FdvOnly', market_cap: 0, fdv: 250000, lp_usd: 300000 };
  assert.strictEqual(mcapLabel(fdvOnly), 'FDV', 'an FDV is labelled FDV');
  assert.strictEqual(mcapLabel({ ...fdvOnly, market_cap: 5000 }), 'MC');
  assert(tableHtml([fdvOnly]).includes('FDV: $250.0K'), 'the RADAR column labels the number FDV');
  console.log('✔ Market cap: sanitized everywhere, FDV labelled, no "Fair Mint Phase" for trading tokens: PASS');

  // ---------------------------------------------------------------------------------------- stale marker
  const nowMs = Date.UTC(2026, 10, 12, 12, 0, 0);
  const stale = { ...xgirl, market_stale: true, lp_usd: null, token_price_usd: null, market_cap: null };
  assert(marketFreshnessHtml(stale, nowMs).includes('stale'), 'a stale record carries a stale marker');
  assert.strictEqual(marketFreshnessHtml({ ...xgirl, market_updated_at: nowMs / 1000 - 60 }, nowMs), '', 'fresh data: no marker');
  assert(marketFreshnessHtml({ ...xgirl, market_updated_at: nowMs / 1000 - 7200 }, nowMs).includes('2h ago'), 'old data shows its age');
  assert.strictEqual(formatMarketAge(nowMs / 1000 - 30, nowMs), 'just now');
  assert(modalHtml(stale).includes('stale'), 'the modal shows the stale marker next to price and LP');
  console.log('✔ Stale marker and age from market_updated_at: PASS');

  // ---------------------------------------------------------------------------------------- portfolio decimals (suspected item)
  const { balanceFromRaw, tokenDecimalsOf } = app;
  const dogshit = { standard: 'BEP-20 Token (9 decimals)', contract: '0x389cb330' + '0'.repeat(32) };
  const agentDog = { standard: 'BEP-20 Token (0 decimals)', contract: '0x8e1155d3790b916b0a884be77a138a41f7777777' };
  assert.strictEqual(balanceFromRaw(dogshit, 5000000000n), 5, 'a 9-decimal token: 5e9 raw is 5 tokens (was 5e-9)');
  assert.strictEqual(balanceFromRaw(agentDog, 123456n), 123456, 'a 0-decimal token: raw is whole tokens (was 1.2e-13)');
  assert.strictEqual(balanceFromRaw({ standard: 'BEP-20 Token (18 decimals)' }, 2500000000000000000n), 2.5);
  assert.strictEqual(balanceFromRaw({ standard: 'BEP-20 Token (8 decimals)' }, 150000000n), 1.5);
  assert.strictEqual(tokenDecimalsOf({ standard: 'Fair-Mint Contract (Dispenses $X Token)', token_decimals: 6 }), 6, 'stored token_decimals wins');
  assert.strictEqual(tokenDecimalsOf({ standard: 'Custom Smart Contract' }), 18, 'unknown decimals default to 18');
  console.log('✔ Portfolio balances use the token\'s real decimals (9 and 0 decimal tokens): PASS');

  // ---------------------------------------------------------------------------------------- M9: seeds are labelled, API first
  global.fetch = async (url) => {
    const u = String(url);
    if (u.includes('/api/top-gainers')) {
      return { ok: true, json: async () => ({ gainers: [{ name: 'ApiOne', symbol: 'API1', contract: '0x' + '1'.repeat(40), standard: 'BEP-20 Token (18 decimals)',
        secUsd: 1, mintUsd: 0.5, profit: 0.5, athPrice: 1, athProfit: 0.5, multiplier: 2, isSold: false, isFree: false, holders: 5,
        item: { name: 'ApiOne', contract: '0x' + '1'.repeat(40), mint_price_str: '0.0007 BNB', token_price_usd: 1, lp_usd: 9000 } }],
        seeded: true, as_of: '2026-11-08T10:00:00Z' }) };
    }
    throw new Error('static seed file must not be requested when the API answered: ' + u);
  };
  app.setSavedTopGainers([]);
  await app.syncSeededTopGainers();
  assert.deepStrictEqual(app.getTopGainersSource(), { seeded: true, asOf: '2026-11-08T10:00:00Z' }, 'the API response says it is a seed, with its date');
  assert.strictEqual(app.topGainersSeedLabel(), 'Seed data, as of 2026-11-08', 'a seeded list is labelled with its date');
  assert(app.getSavedTopGainers().some(g => g.name === 'ApiOne'), 'the API list is merged');
  global.fetch = async (url) => {
    if (String(url).includes('/api/top-gainers')) return { ok: true, json: async () => ({ gainers: [{ name: 'LiveOne', contract: '0x' + '2'.repeat(40), secUsd: 2, mintUsd: 1, profit: 1, athPrice: 2, athProfit: 1,
      item: { name: 'LiveOne', contract: '0x' + '2'.repeat(40), mint_price_str: '0.0015 BNB', token_price_usd: 2, lp_usd: 9000 } }] }) };
    throw new Error('unexpected ' + url);
  };
  await app.syncSeededTopGainers();
  assert.strictEqual(app.topGainersSeedLabel(), '', 'live API data carries no seed label');
  // API down -> the static seed file, labelled with the file's date
  global.fetch = async (url) => {
    if (String(url).includes('/api/top-gainers')) throw new Error('offline');
    return { ok: true, headers: { get: () => 'Sat, 08 Nov 2025 10:00:00 GMT' }, json: async () => [] };
  };
  await app.syncSeededTopGainers();
  console.log('✔ Top gainers: API first, seeds labelled "as of <date>", live data unlabelled: PASS');

  // ---------------------------------------------------------------------------------------- M8: no USD cache with a stale price
  const stored = new Map();
  global.localStorage = { getItem: (k) => (stored.has(k) ? stored.get(k) : null), setItem: (k, v) => stored.set(k, v), removeItem: (k) => stored.delete(k) };
  app.setSavedTopGainers([{ name: 'X', contract: '0x' + '3'.repeat(40) }]);
  setBnbPriceState(660.0, false);
  app.saveSavedTopGainers();
  assert.strictEqual(stored.size, 0, 'nothing is cached while the BNB price is stale');
  setBnbPriceState(660.0, true);
  app.saveSavedTopGainers();
  assert([...stored.keys()].some(k => k.includes('saved_top_markets_v3')), 'cached (under the v3 key) once the price is live');
  setBnbPriceState(null, false);
  assert.strictEqual(app.updateSavedTopMarkets([{ contract: '0x' + '4'.repeat(40), name: 'N', mint_price_str: 'FREE', has_free_mint: true,
    token_price_usd: 5, lp_usd: 9000, standard: 'BEP-20 Token (18 decimals)' }]).length, 1, 'without a BNB price nothing new is computed or added');
  setBnbPriceState(660.0, true);
  console.log('✔ Saved gainers: not cached with a stale BNB price, nothing computed without one: PASS');

  // ---------------------------------------------------------------------------------------- Pulse: seeds are not "24h" winners
  const seedEntry = { name: 'Old Winner', symbol: 'OLD', contract: '0x' + '5'.repeat(40), athPrice: 6.75, athProfit: 6.75, profit: 6.75, secUsd: 6.75, isFree: true,
    item: { name: 'Old Winner', contract: '0x' + '5'.repeat(40), standard: 'BEP-20 Token (18 decimals)', mint_price_str: 'FREE', has_free_mint: true,
            token_price_usd: 6.75, lp_usd: 20000, detected_at: '2026-11-01 09:00:00' } };
  assert.strictEqual(app.compute24hPulseMetrics([], [seedEntry], 0, null).topAthMints.length, 0, 'no known 24h window: a saved/seed winner is not a 24h mint');
  const win = { since: '2026-11-11 12:00:00', graduated: 0, dex_volume_usd: 0 };
  assert.strictEqual(app.compute24hPulseMetrics([], [seedEntry], 0, win).topAthMints.length, 0, 'older than the window: not a 24h mint');
  seedEntry.item.detected_at = '2026-11-12 09:00:00';
  assert.deepStrictEqual(app.compute24hPulseMetrics([], [seedEntry], 0, win).topAthMints.map(a => a.name), ['Old Winner'], 'inside the window: counted');
  console.log('✔ Pulse ATH card: historical winners only count inside the server window: PASS');

  // ---------------------------------------------------------------------------------------- table: stale / DEX-traded record (preview run)
  // SPACEDOG after the market data was cleared as stale showed "Fair Mint" in the price column and no stale marker
  const staleDex = { standard: 'BEP-20 Token (18 decimals)', contract: '0x99487232829ac745b5be20c1d215f255335812ef', name: 'Space Dog', symbol: 'SPACEDOG',
    mint_price_str: 'DEX Traded', total_minted: 2.4e9, max_supply: null, holders: 235, buys_24h: null, market_stale: true,
    token_price_usd: null, lp_usd: null, market_cap: null };
  const staleRow = tableHtml([staleDex]);
  assert(!staleRow.includes('Fair Mint'), 'a cleared DEX-traded token is not in its "Fair Mint" phase');
  assert(staleRow.includes('stale'), 'the table row carries the stale marker');
  const dexOnly = tableHtml([{ ...staleDex, contract: '0x' + '6'.repeat(40), market_stale: false }]);
  assert(!dexOnly.includes('Fair Mint'), 'a DEX-traded token with no price is "—", not "Fair Mint"');
  const preMarket = tableHtml([{ standard: 'ERC-721 (NFT Collection)', contract: '0x' + '4'.repeat(40), name: 'Fresh Drop', symbol: 'FD',
    mint_price_str: '0.0100 BNB', mint_price_bnb: 0.01, total_minted: 5, max_supply: 1000 }]);
  assert(preMarket.includes('Fair Mint'), 'a genuine pre-market drop keeps its "Fair Mint" label');
  const oldData = tableHtml([{ ...xgirl, contract: '0x' + '3'.repeat(40), market_updated_at: Date.now() / 1000 - 3 * 3600 }]);
  assert(oldData.includes('3h ago'), 'market data that has not been confirmed for hours shows its age next to the price');
  console.log('✔ Table: stale marker, no "Fair Mint" for a cleared / DEX-traded token, age of old data: PASS');

  // ---------------------------------------------------------------------------------------- Pulse runners are live movers only
  const seedMars = { name: 'Mars Nexus Identity', symbol: 'MARSID', contract: '0xcbac4fac33afb361e836e6a0120f5a5aac9122b3', standard: 'ERC-721 (NFT Collection)',
    secUsd: 0.0486, athPrice: 0.0486, athProfit: 0.05, profit: 0.05,
    item: { name: 'Mars Nexus Identity', symbol: 'MARSID', contract: '0xcbac4fac33afb361e836e6a0120f5a5aac9122b3', standard: 'ERC-721 (NFT Collection)',
            mint_price_str: 'FREE', has_free_mint: true, token_price_usd: 0.0486, market_cap: 4300000, lp_usd: 900000, volume_24h: 5000 } };
  const liveRunner = { name: 'Live Mover', symbol: 'LIVE', contract: '0x' + '2'.repeat(40), standard: 'BEP-20 Token (18 decimals)', token_price_usd: 0.002,
    market_cap: 90000, lp_usd: 9000, volume_24h: 4000, price_change_24h: 12.0 };
  const runners = app.compute24hPulseMetrics([liveRunner], [seedMars], 0, null).topRunners.map(r => r.name);
  assert.deepStrictEqual(runners, ['Live Mover'], 'a saved / seed entry never stands in for a live 24h runner');
  assert.strictEqual(app.compute24hPulseMetrics([], [seedMars], 0, null).topRunners.length, 0, 'no live drops: no runners (not seed entries)');
  console.log('✔ Pulse runners: live drops only, seeds excluded: PASS');

  // ---------------------------------------------------------------------------------------- filter pills after a merge
  // The first page is rendered (pills count it), then the seeded smart drops / secondary list are merged in. The pills used
  // to keep the first page's count (FREE MINTS 109 instead of 1,290) unless something happened to call renderTable().
  const mk = (i, extra) => ({ contract: '0x' + i.toString(16).padStart(40, '0'), name: 'Pill ' + i, symbol: 'P' + i,
    standard: 'BEP-20 Token (18 decimals)', block: 1000 + i, ...extra });
  const firstPage = [mk(1, { has_free_mint: true, mint_price_str: 'FREE' }), mk(2, { mint_price_str: 'DEX Traded' })];
  app.setGlobalStats(null);
  tableHtml(firstPage);                                                    // renderTable() counts the pills
  assert.strictEqual(getEl('freeMintsCount').innerHTML, '1', 'first page: one free mint');
  const rest = [];
  for (let i = 10; i < 40; i++) rest.push(mk(i, { has_free_mint: true, mint_price_str: 'FREE' }));    // 30 more free mints
  for (let i = 40; i < 45; i++) rest.push(mk(i, { token_price_usd: 0.1, lp_usd: 9000, volume_24h: 500, mint_price_str: 'DEX Traded' }));
  app.mergeDropsIntoAllDrops(rest);                                        // what loadSmartDrops / loadSecondaryDrops do
  assert.strictEqual(getEl('freeMintsCount').innerHTML, '31', 'FREE MINTS follows the merged set (1 + 30), not the first page');
  assert.strictEqual(String(getEl('secondaryTabCount').innerHTML), '5', 'the SECONDARY badge follows the merged set too');
  app.mergeDropsIntoAllDrops([mk(10, {})]);                                // nothing new: counts unchanged
  assert.strictEqual(getEl('freeMintsCount').innerHTML, '31');
  console.log('✔ Filter pills are re-counted after a merge (no more stale FREE MINTS / LAUNCHPAD counts): PASS');

  console.log('🎉 Prompt 5 display tests passed.');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
