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
const { openModal } = app;

const C = '0x' + 'a'.repeat(40);
const BIG = '0x' + 'ab'.repeat(2000);

(async () => {
  let fetched = [];
  global.fetch = async (url) => {
    fetched.push(String(url));
    return { ok: true, status: 200, json: async () => ({ projects: [{ contract: C, mint_calldata: BIG }] }) };
  };

  // the feed left this record's calldata out (has_mint_calldata): the modal must not offer a mint with "0x"
  const item = { contract: C, name: 'Big', symbol: 'BIG', standard: 'ERC-721 NFT', mint_price_str: '0.01 BNB',
    mint_price_bnb: 0.01, mint_value_wei: '10000000000000000', has_mint_calldata: true, block: 1 };
  const content = getEl('modalContent');
  openModal(item);
  const first = content.innerHTML;
  assert.ok(first.includes('Loading mint data'), 'mint button shows that the calldata is loading');
  assert.ok(!first.includes("executeWeb3Mint('" + C + "', '0x'"), 'no mint with empty calldata is offered');

  await new Promise(r => setTimeout(r, 20));
  assert.ok(fetched.some(u => u.includes('/api/projects?search=' + C)), 'the full record is looked up');
  assert.strictEqual(item.mint_calldata, BIG);
  const second = content.innerHTML;
  assert.ok(second.includes("executeWeb3Mint('" + C + "', '" + BIG + "'"), 'after loading, the mint uses the full calldata');
  assert.ok(!second.includes('Loading mint data'));

  // a record that came with its calldata needs no lookup
  fetched = [];
  openModal({ contract: '0x' + 'b'.repeat(40), name: 'Small', standard: 'ERC-721 NFT', mint_calldata: '0x1249c58b', block: 2 });
  await new Promise(r => setTimeout(r, 20));
  assert.ok(!fetched.some(u => u.includes('/api/projects')), 'no lookup when the calldata is already there');

  console.log('✔ Oversized calldata is loaded on demand and never minted empty: PASS');

  // ---- Pulse cards list slim server rows: a full record this page has wins, an unknown one is upgraded on click
  const { openInspectModalByContract, registerPulseItem, setAllDrops, pulseItemsMap, compute24hPulseMetrics,
    applyMarketOverlay } = app;
  const FULL = '0x' + 'c'.repeat(40);
  const SLIM_ONLY = '0x' + 'd'.repeat(40);
  const fullRecord = { contract: FULL, name: 'Full Record', symbol: 'FULL', standard: 'BEP-20 Token (18 decimals)',
    token_price_usd: 0.5, lp_usd: 9000, block: 5, security: { is_honeypot: '0' }, risk_level: 'SAFE' };
  setAllDrops([fullRecord]);
  registerPulseItem(FULL, { contract: FULL, name: 'Slim Row', symbol: 'SLIM', token_price_usd: 0.5, _slim: true });
  registerPulseItem(SLIM_ONLY, { contract: SLIM_ONLY, name: 'Only Slim', symbol: 'ONLY', standard: 'BEP-20 Token (18 decimals)',
    token_price_usd: 0.25, lp_usd: 4000, block: 6, _slim: true });
  fetched = [];
  global.fetch = async (url) => {
    fetched.push(String(url));
    return { ok: true, status: 200, json: async () => ({ projects: [
      { contract: SLIM_ONLY, name: 'Only Slim FULL RECORD', symbol: 'ONLY', standard: 'BEP-20 Token (18 decimals)',
        token_price_usd: 0.25, lp_usd: 4000, block: 6, security: { is_honeypot: '0' } }] }) };
  };
  openInspectModalByContract(FULL);
  assert.ok(content.innerHTML.includes('Full Record') && !content.innerHTML.includes('Slim Row'),
    'a full record in allDrops is shown instead of the slim Pulse row');
  await new Promise(r => setTimeout(r, 20));
  assert.ok(!fetched.some(u => u.includes('/api/projects')), 'no lookup when the page already has the full record');

  openInspectModalByContract(SLIM_ONLY);
  assert.ok(content.innerHTML.includes('Only Slim') && !content.innerHTML.includes('FULL RECORD'), 'the slim row opens the modal at once');
  await new Promise(r => setTimeout(r, 20));
  assert.ok(fetched.some(u => u.includes('/api/projects?search=' + SLIM_ONLY)), 'the full record is requested once');
  assert.ok(content.innerHTML.includes('Only Slim FULL RECORD'), 'and the modal is redrawn with it');
  assert.strictEqual(pulseItemsMap.get(SLIM_ONLY).security.is_honeypot, '0', 'the full record replaces the slim row');
  fetched = [];
  openInspectModalByContract(SLIM_ONLY);
  await new Promise(r => setTimeout(r, 20));
  assert.ok(!fetched.some(u => u.includes('/api/projects')), 'a second click does not ask again');
  console.log('✔ Slim Pulse rows: full records win, unknown ones are upgraded on click: PASS');

  // ---- card 1 comes from the server list; excluded majors are still dropped
  const runnerRow = (c, name, chg) => ({ contract: c, name, symbol: name.slice(0, 3).toUpperCase(), token_price_usd: 0.01, market_cap: 50000,
    lp_usd: 5000, volume_24h: 5000, price_change_24h: chg, _slim: true });
  const withRunners = compute24hPulseMetrics([], [], 0, { since: null, top_runners: [
    runnerRow('0x' + '1'.repeat(40), 'Alpha', 90), runnerRow('0x' + '2'.repeat(40), 'Beta', 40),
    { ...runnerRow('0x' + '0e09fabb73bd3ade0a17ecc321fd13a19e81ce82'.slice(2), 'PancakeSwap Token', 5), symbol: 'Cake' },
    runnerRow('0x' + '3'.repeat(40), 'Gamma', 10), runnerRow('0x' + '4'.repeat(40), 'Delta', 8)] });
  assert.deepStrictEqual(withRunners.topRunners.map(r => r.name), ['Alpha', 'Beta', 'Gamma'], 'server runners, majors dropped, three at most');
  assert.strictEqual(withRunners.topRunner.name, 'Alpha');
  console.log('✔ Pulse runners come from the server list: PASS');

  // ---- /api/market-data: stale contracts are a plain address list
  const SEEDED_STALE = '0x' + 'e'.repeat(40);
  const SEEDED_LIVE = '0x' + 'f'.repeat(40);
  const seededDrops = [{ contract: SEEDED_STALE, token_price_usd: 0.5, lp_usd: 9000, market_cap: 247000 },
    { contract: SEEDED_LIVE, token_price_usd: 1, lp_usd: 500 }, { contract: '0x' + '9'.repeat(40), token_price_usd: 7 }];
  const changed = applyMarketOverlay(seededDrops, { _stale: [SEEDED_STALE.toUpperCase()], [SEEDED_LIVE]: { token_price_usd: 2, market_updated_at: 1794000000 } });
  assert.strictEqual(changed, 2, 'the stale and the live contract change; the one the response does not mention is untouched');
  assert.strictEqual(seededDrops[0].token_price_usd, null);
  assert.strictEqual(seededDrops[0].lp_usd, null);
  assert.strictEqual(seededDrops[0].market_stale, true, 'and it is flagged stale for the table badge');
  assert.strictEqual(seededDrops[1].token_price_usd, 2);
  assert.strictEqual(seededDrops[1].lp_usd, 500, 'a field the response leaves out is not cleared');
  assert.strictEqual(seededDrops[2].token_price_usd, 7);
  console.log('✔ Market overlay: _stale clears seeded numbers, absent fields are left alone: PASS');
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
