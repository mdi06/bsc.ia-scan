// Hall of Fame: ranked by peak profit per mint; drops whose mint cost is not known are left out; the filter buttons,
// the banner and the table count the same list. Run with: node test_hall_of_fame.js
const assert = require('assert');

const modalContent = { innerHTML: '' };
const modal = { showModal() {}, close() {}, setAttribute() {}, removeAttribute() {} };
global.document = {
  getElementById: (id) => (id === 'hofModalContent' ? modalContent : id === 'hallOfFameModal' ? modal : null),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
};

const app = require('../app.js');

let n = 0;
function nft(name, { mintUsd, athPrice, isFree = false, isSold = false, priceStr = '0.001 BNB' }) {
  n += 1;
  const contract = '0x' + String(n).padStart(40, '0');
  const standard = 'ERC-721 (NFT Collection)';
  return {
    name, symbol: name.slice(0, 4).toUpperCase(), contract, standard,
    secUsd: athPrice / 2, mintUsd, profit: 0, athPrice, athProfit: 0, multiplier: mintUsd > 0 ? athPrice / mintUsd : 0,
    isSold, isFree, holders: 10,
    item: { name, contract, standard, mint_price_str: isFree ? 'FREE' : priceStr, has_free_mint: isFree, mint_calldata: '0x1' },
  };
}

// ---- mint cost: free is 0; $0 that is not a verified free mint, or no price at all, is unknown
assert.strictEqual(app.gainerMintCost({ isFree: true, mintUsd: 0 }), 0);
assert.strictEqual(app.gainerMintCost({ isFree: false, mintUsd: 0 }), null, '$0 without the free flag is not known');
assert.strictEqual(app.gainerMintCost({ isFree: false, mintUsd: null }), null);
assert.strictEqual(app.gainerMintCost({ isFree: false, mintUsd: 8.57 }), 8.57);
console.log('✔ Mint cost: free = 0, $0 / TBD without the free flag = unknown: PASS');

// ---- a row with an unknown mint cost is not ranked (the "Mars Nexus Identity, TBD" row)
const tbd = nft('MarsNexus', { mintUsd: 0, athPrice: 0.0508, priceStr: 'TBD' });
assert.strictEqual(app.gainerPeakProfit(tbd), null, 'no profit without a mint cost');
assert.strictEqual(app.isRankableGainer(tbd), false, 'a TBD row is not listed');
console.log('✔ Unknown mint cost: no profit, not listed: PASS');

// ---- order: peak profit per mint, not the multiplier
const highMultSmallProfit = nft('HighMult', { mintUsd: 0.35, athPrice: 1.21 });    // 3.4x, ~$0.86
const lowMultBigProfit = nft('BigProfit', { mintUsd: 40, athPrice: 90 });          // 2.25x, ~$50
const freeMid = nft('FreeMid', { mintUsd: 0, athPrice: 6.75, isFree: true });       // no multiplier, ~$6.75
const loser = nft('Loser', { mintUsd: 5, athPrice: 2 });                           // peak below the mint: not listed
app.setSavedTopGainers([highMultSmallProfit, tbd, freeMid, loser, lowMultBigProfit]);
const stats = app.computeStats([]);
assert.deepStrictEqual(stats.allGainers.map(g => g.name), ['BigProfit', 'FreeMid', 'HighMult'],
  'ranked by peak profit per mint; TBD and losing rows left out');
for (let i = 1; i < stats.allGainers.length; i++) {
  assert(app.gainerPeakProfit(stats.allGainers[i - 1]) >= app.gainerPeakProfit(stats.allGainers[i]), 'profit never rises down the list');
}
assert.deepStrictEqual(stats.majorGainers.map(g => g.name), ['BigProfit', 'FreeMid'], 'major = at least $1 per mint');
console.log('✔ Ranking: by peak profit per mint, free mints included, multiplier only breaks ties: PASS');

// ---- the modal: the button count, the banner count and the table rows are the same number
app.openHallOfFameModal('ALL');
const html = modalContent.innerHTML;
const rows = (html.match(/<td class="hof-col-rank">/g) || []).length;
const btn = Number((html.match(/All Gainers \((\d+)\)/) || [])[1]);
const banner = Number((html.match(/(\d+) profitable drops/) || [])[1]);
assert.strictEqual(rows, 3);
assert.strictEqual(btn, rows, 'the All Gainers button counts the rows the table shows');
assert.strictEqual(banner, rows, 'the banner counts the rows the table shows');
assert(!html.includes('MarsNexus'), 'the TBD row is not in the table');
assert(html.indexOf('BigProfit') < html.indexOf('FreeMid') && html.indexOf('FreeMid') < html.indexOf('HighMult'), 'table order = profit order');
app.openHallOfFameModal('MAJOR');
const majorRows = (modalContent.innerHTML.match(/<td class="hof-col-rank">/g) || []).length;
assert.strictEqual(majorRows, Number((modalContent.innerHTML.match(/Major Gainers \(≥\$1 per mint\) \((\d+)\)/) || [])[1]));
console.log('✔ Modal: button count = banner count = table rows: PASS');

console.log('🎉 Hall of Fame tests passed.');
