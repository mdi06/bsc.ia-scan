// Stats banner: no sample data. The page used to ship placeholder tokens in index.html (Hallowed, SOLVED, Hash21,
// Ptah) that renderStats() never overwrote when there was no data (and #momentumNames was never written at all).
// Checks: the HTML ships neutral placeholders, renderStats clears stale text when data is empty, and the momentum
// names come from the same list as the count. Run with: node test_stats_banner.js
const assert = require('assert');
const fs = require('fs');

// ---- a minimal DOM: only the banner spans (plus whatever the stand-in objects below add); every other id is null
class El {
  constructor(id, text = '') {
    this.id = id;
    this.textContent = text;
    this.attrs = {};
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
}
const BANNER_IDS = ['fillRatePct', 'fillRateRatio', 'nearCapCount', 'momentumCount', 'momentumNames',
  'topGainerName', 'topGainerDetails', 'topGainerNet', 'runnerUpDetails'];
let els = new Map();
// What index.html shipped before this fix: stale sample text that must never survive a render.
const STALE = {
  fillRatePct: '15.3%', fillRateRatio: '(11 / 72 Sold Out)', nearCapCount: '0', momentumCount: '2 ACTIVE',
  momentumNames: '[Hallowed 60%, SOLVED 60%]', topGainerName: 'Hash21 (HASH)',
  topGainerDetails: 'Mint: FREE ➔ Secondary: $0.0148', topGainerNet: 'Net: +$0.0148 / token',
  runnerUpDetails: '│ 🥈 Runner-Up: Ptah (PTAH) [$0.0017]',
};
function resetDom() {
  els = new Map(BANNER_IDS.map(id => [id, new El(id, STALE[id])]));
}
global.document = {
  getElementById: (id) => els.get(id) || null,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
};

const app = require('./app.js');
const txt = (id) => els.get(id).textContent;
const FAKE = /Hallowed|SOLVED|Hash21|Ptah|PTAH|\$0\.0148|\$0\.0017/;
const allBannerText = () => BANNER_IDS.map(txt).join(' | ');

const mk = (n, extra) => ({
  contract: '0x' + n.toString(16).padStart(40, '0'), name: 'Token ' + n, symbol: 'T' + n,
  standard: 'ERC-721 (NFT Collection)', max_supply: 100, total_minted: 10, ...extra,
});

// ------------------------------------------------------------------------------------------ 1. the HTML placeholders
{
  const html = fs.readFileSync(require('path').join(__dirname, 'index.html'), 'utf8');
  const inner = (id) => {
    const m = html.match(new RegExp(`id="${id}"[^>]*>([^<]*)<`));
    assert(m, `index.html still has #${id}`);
    return m[1].trim();
  };
  const NEUTRAL = new Set(['', '—']);
  BANNER_IDS.forEach(id => assert(NEUTRAL.has(inner(id)), `#${id} ships neutral text, not "${inner(id)}"`));
  assert(NEUTRAL.has(inner('footerCount')), '#footerCount ships neutral text, not a made-up "Showing 75 of 75"');
  // Rendered markup only: an HTML comment is never shown (one documents the recent-search chips with example names).
  assert(!FAKE.test(html.replace(/<!--[\s\S]*?-->/g, '')), 'index.html renders none of the old sample tokens');
  console.log('✔ index.html: every banner placeholder is "—" or empty, no sample tokens: PASS');
}

// ------------------------------------------------------------------------ 2. empty data clears stale text (cold load)
app.setSavedTopGainers([]);
app.setBnbPriceState(705.0, true);
resetDom();
app.renderStats([]);
assert.strictEqual(txt('momentumNames'), '—', 'no momentum tokens: dash, not the old "[Hallowed 60%, SOLVED 60%]"');
assert.strictEqual(txt('momentumCount'), '0 ACTIVE');
assert.strictEqual(txt('fillRatePct'), '—', 'no capped collections: the ratio is undefined, not "0.0%"');
assert.strictEqual(txt('fillRateRatio'), '', 'no capped collections: no "(0 / 0 Sold Out)"');
assert.strictEqual(txt('nearCapCount'), '0');
assert.strictEqual(txt('topGainerName'), '—', 'no top gainer: dash, not "Hash21 (HASH)"');
assert.strictEqual(txt('topGainerDetails'), '');
assert.strictEqual(txt('topGainerNet'), '');
assert.strictEqual(txt('runnerUpDetails'), '', 'no runner-up: cleared, not "Ptah (PTAH)"');
assert(!FAKE.test(allBannerText()), 'no sample token survives an empty render: ' + allBannerText());
assert.strictEqual(els.get('momentumNames').attrs.title, undefined, 'no tooltip when there is nothing to list');
console.log('✔ renderStats([]) clears every stale banner span: PASS');

// Capped data but nobody in 50-79%: the names say so, and it matches the count.
resetDom();
app.renderStats([mk(1, { total_minted: 10 }), mk(2, { total_minted: 100 })]);   // 10% and sold out
assert.strictEqual(txt('momentumCount'), '0 ACTIVE');
assert.strictEqual(txt('momentumNames'), '—');
assert.strictEqual(txt('fillRatePct'), '50.0%');
assert.strictEqual(txt('fillRateRatio'), '(1 / 2 Sold Out)');
console.log('✔ renderStats: capped drops but none warm -> "0 ACTIVE" and "—": PASS');

// ---------------------------------------------------------------------------------- 3. momentum names = the real data
const warm = [
  mk(10, { name: 'Alpha', total_minted: 78 }),
  mk(11, { name: 'Bravo', total_minted: 65 }),
  mk(12, { name: 'Charlie', total_minted: 60 }),
  mk(13, { name: 'Delta', total_minted: 55 }),
  mk(14, { name: 'Echo', total_minted: 50 }),
];
const notWarm = [
  mk(20, { name: 'NearCap', total_minted: 85 }),                 // >= 80%: counted as near cap
  mk(21, { name: 'SoldOut', total_minted: 100 }),
  mk(22, { name: 'Cold', total_minted: 49 }),                    // just under 50%
  mk(23, { name: 'Uncapped', max_supply: 0, total_minted: 90 }),  // no cap: no progress
  mk(24, { name: 'Absurd', max_supply: 1e25, total_minted: 9e24 }), // sentinel cap is ignored by computeStats
];
resetDom();
const stats = app.computeStats([...notWarm, ...warm]);
assert.strictEqual(stats.warmCapCount, 5);
assert.strictEqual(stats.warmCapItems.length, stats.warmCapCount, 'the named list is exactly the list behind the count');
assert.deepStrictEqual(stats.warmCapItems.map(t => t.name), ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo'], 'highest progress first');
app.renderStats([...notWarm, ...warm]);
assert.strictEqual(txt('momentumCount'), '5 ACTIVE');
assert.strictEqual(txt('momentumNames'), '[Alpha 78%, Bravo 65%, Charlie 60%]', 'top 3 by progress, never the old sample');
assert.strictEqual(txt('nearCapCount'), '1');
assert.strictEqual(els.get('momentumNames').attrs.title, 'Alpha 78%, Bravo 65%, Charlie 60%, Delta 55%, Echo 50%',
  'the tooltip lists every momentum token');
assert(!FAKE.test(allBannerText()), 'real data replaced every sample string');
console.log('✔ renderStats names the top 3 warm tokens, matching the count and the data: PASS');

// Fewer than three: only those; names use name, else symbol, else the shortened contract; long names are cut.
resetDom();
app.renderStats([
  mk(30, { name: '', symbol: 'SYMB', total_minted: 70 }),
  mk(31, { name: '', symbol: '', total_minted: 60 }),
]);
assert.strictEqual(txt('momentumCount'), '2 ACTIVE');
assert.strictEqual(txt('momentumNames'), '[SYMB 70%, 0x0000…001f 60%]', 'symbol, then shortened contract, as the label');
resetDom();
app.renderStats([mk(32, { name: 'A very long collection name', total_minted: 60 })]);
assert.strictEqual(txt('momentumNames'), '[A very long c… 60%]', 'long names are cut to 14 characters (13 + ellipsis)');
resetDom();
app.renderStats([mk(33, { name: '龍龍龍龍龍龍龍龍龍龍龍龍龍龍龍龍', total_minted: 60 })]);
assert.strictEqual(txt('momentumNames'), '[' + '龍'.repeat(13) + '… 60%]', 'CJK names are cut on whole characters');
console.log('✔ renderStats: label fallbacks and truncation: PASS');

// 79.6% is shown as 79%: it is not "80%" while it is counted as 50-79%, not near cap.
resetDom();
app.renderStats([mk(34, { name: 'Edge', max_supply: 1000, total_minted: 796 }), mk(35, { name: 'Floor', max_supply: 1000, total_minted: 500 })]);
assert.strictEqual(txt('momentumNames'), '[Edge 79%, Floor 50%]');
assert.strictEqual(txt('nearCapCount'), '0');
console.log('✔ renderStats: percentages stay inside the 50-79% bucket: PASS');

// ---------------------------------------------------------------- 4. top gainer / runner-up fill, then clear again
const freeItem = {
  contract: '0x1111111111111111111111111111111111111111', name: 'AlphaBot Genesis', symbol: 'ABOT',
  standard: 'ERC-721 (NFT Collection)', mint_price_str: 'FREE', max_supply: 1000, total_minted: 1000,
  floor_price_usd: 0.05, holders: 850,
};
const paidItem = {
  contract: '0x2222222222222222222222222222222222222222', name: 'CyberAgents VIP', symbol: 'CVIP',
  standard: 'ERC-721 (NFT Collection)', mint_price_bnb: 0.01, mint_value_wei: (0.01 * 1e18).toString(),
  max_supply: 500, total_minted: 500, floor_price_usd: 35.25, holders: 420,
};
app.setSavedTopGainers([]);
resetDom();
app.renderStats([freeItem, paidItem]);
assert.strictEqual(txt('topGainerName'), 'CyberAgents VIP (CVIP)');
assert(txt('topGainerDetails').startsWith('Mint: '), txt('topGainerDetails'));
assert(txt('topGainerNet').startsWith('Net: +$'), txt('topGainerNet'));
assert(txt('runnerUpDetails').includes('AlphaBot Genesis (ABOT)'), txt('runnerUpDetails'));
assert(!FAKE.test(allBannerText()));
console.log('✔ renderStats fills top gainer and runner-up from the data: PASS');

// The data goes away (saved markets reset, no drops): the real names must not linger either.
app.setSavedTopGainers([]);
app.renderStats([]);
assert.strictEqual(txt('topGainerName'), '—');
['topGainerDetails', 'topGainerNet', 'runnerUpDetails'].forEach(id => assert.strictEqual(txt(id), '', `#${id} cleared`));
assert.strictEqual(txt('momentumNames'), '—');
assert(!/CyberAgents|AlphaBot|CVIP|ABOT/.test(allBannerText()), 'previous real text is cleared as well: ' + allBannerText());
console.log('✔ renderStats([]) after real data clears top gainer, runner-up and momentum: PASS');

// A top gainer without a runner-up clears the runner-up span (it used to be left untouched).
app.setSavedTopGainers([]);
resetDom();
app.renderStats([paidItem]);
assert.strictEqual(txt('topGainerName'), 'CyberAgents VIP (CVIP)');
assert.strictEqual(txt('runnerUpDetails'), '', 'single gainer: no runner-up, and the stale one is gone');
console.log('✔ renderStats: missing runner-up is cleared, not left stale: PASS');

// ---------------------------------------------------------- 5. stand-in DOM objects without setAttribute / removeAttribute
// (the stand-ins in the other tests are plain { textContent }): the tooltip is optional, never a crash.
els = new Map(BANNER_IDS.map(id => [id, { textContent: STALE[id] }]));
assert.doesNotThrow(() => app.renderStats([...warm]));
assert.strictEqual(els.get('momentumNames').textContent, '[Alpha 78%, Bravo 65%, Charlie 60%]');
assert.doesNotThrow(() => app.renderStats([]));
assert.strictEqual(els.get('momentumNames').textContent, '—');
els = new Map();   // none of the banner elements exist (another page / tab)
assert.doesNotThrow(() => app.renderStats([...warm]));
console.log('✔ renderStats tolerates stand-in DOM objects and missing elements: PASS');

console.log('🎉 Stats banner tests passed.');
