// The page twin of filters.crowd_surge_mints. Run with: node test_crowd_surge.js
const assert = require('assert');
class El { constructor(id){this.id=id;this.innerHTML='';this.style={};this.classList={add(){},remove(){},contains:()=>false,toggle(){}};}
  showModal(){} close(){} setAttribute(){} removeAttribute(){} addEventListener(){} appendChild(){} replaceChildren(){}
  querySelector(){return null;} querySelectorAll(){return [];} set textContent(v){this.innerHTML=String(v);} get textContent(){return this.innerHTML;} }
global.document = { getElementById: (id) => new El(id), querySelector: () => null, querySelectorAll: () => [], createElement: (t) => new El(t),
  createDocumentFragment: () => ({ appendChild() {} }), addEventListener() {}, body: new El('body'), documentElement: new El('html') };
global.window = { location: { origin: 'https://x', pathname: '/', search: '', hash: '' }, addEventListener() {}, removeEventListener() {} };
Object.defineProperty(global, 'navigator', { configurable: true, writable: true, value: { clipboard: { writeText: () => Promise.resolve() } } });
const { crowdSurgeMints } = require('./app.js');

const NOW = 1_800_000_000;
const d = (o) => ({ standard: 'BEP-20 Token (18 decimals)', max_supply: null, total_minted: 5e6, tokens_per_mint: 1e4,
  mint_price_str: '⭐ FREE', last_mint_at: NOW - 600, holders: 5000, ...o });

assert.strictEqual(crowdSurgeMints(d({}), NOW), 500);
assert.strictEqual(crowdSurgeMints(d({ last_mint_at: 0, mint_terms: { open: true } }), NOW), 500);
assert.strictEqual(crowdSurgeMints(d({ total_minted: 0, holders: 9999 }), NOW), 0);
assert.strictEqual(crowdSurgeMints(d({ tokens_per_mint: null }), NOW), 0);
assert.strictEqual(crowdSurgeMints(d({ mint_price_str: 'DEX Trading' }), NOW), 0);
assert.strictEqual(crowdSurgeMints(d({ max_supply: 1e9 }), NOW), 0);
assert.strictEqual(crowdSurgeMints(d({ last_mint_at: NOW - 3 * 86400 }), NOW), 0);
assert.strictEqual(crowdSurgeMints(d({ total_minted: 5e5 }), NOW), 0);
assert.strictEqual(crowdSurgeMints(d({ standard: 'ERC-721 (NFT Collection)', total_minted: 250, tokens_per_mint: null }), NOW), 250);
console.log('crowd surge: all checks passed');
