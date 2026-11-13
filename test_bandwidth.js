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
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
