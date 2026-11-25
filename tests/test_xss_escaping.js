// Token names, symbols and links come from whoever deployed the contract (name(), symbol(), DexScreener's token
// info), so they must never reach the page as markup or code. Run with: node test_xss_escaping.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');

// ---- the same minimal DOM test_token_share_link.js uses: only what openModal touches
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
  appendChild(k) { this.innerHTML += (k && k.innerHTML) || ''; }
  replaceChildren() { this.innerHTML = ''; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  select() {}
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
  createElement: (tag) => new El(tag),
  createDocumentFragment: () => new Frag(),
  addEventListener() {},
  body: new El('body'),
  documentElement: new El('html'),
};
global.window = {
  location: { origin: 'https://agent-id-monitor.vercel.app', pathname: '/', search: '', hash: '' },
  addEventListener() {},
  removeEventListener() {},
};
Object.defineProperty(global, 'navigator', { configurable: true, writable: true, value: { clipboard: { writeText: () => Promise.resolve() } } });

const app = require('../app.js');
const { escapeHtml, jsAttr, safeUrl, isDexScreenerUrl, getDexScreenerUrl, openModal } = app;

// The browser decodes HTML entities in an attribute before it runs an inline handler. Do the same, then check the
// handler code still holds the hostile text as ONE string literal.
const decodeAttr = (s) => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');

// 1. jsAttr: a ' or " in a symbol cannot end the JS string
for (const hostile of ["');alert(1);//", '");alert(1);//', "Don't copy", '</script><img src=x>', '\\\');alert(1)//']) {
  const attr = jsAttr(hostile);
  assert.ok(!/["'<>]/.test(attr), `attribute value has no raw quote or bracket: ${attr}`);
  assert.strictEqual(JSON.parse(decodeAttr(attr)), hostile, 'decodes back to exactly the original string');
}
assert.strictEqual(jsAttr(null), escapeHtml('""'));
console.log('✔ jsAttr keeps hostile symbols inside one JS string: PASS');

// 2. safeUrl: only absolute http(s) links survive
assert.strictEqual(safeUrl('https://x.com/token'), 'https://x.com/token');
assert.strictEqual(safeUrl('http://example.org/a?b=1'), 'http://example.org/a?b=1');
for (const bad of ['javascript:alert(1)', ' JavaScript:alert(1)', 'data:text/html,<script>', 'vbscript:x', '//evil.com',
                   'not a url', '', null, undefined, 42]) {
  assert.strictEqual(safeUrl(bad), '', `rejected: ${String(bad)}`);
}
console.log('✔ safeUrl drops javascript:, data: and relative links: PASS');

// 3. DexScreener links: the host must really be dexscreener.com (a substring check let javascript:...dexscreener.com through)
assert.ok(isDexScreenerUrl('https://dexscreener.com/bsc/0xabc'));
assert.ok(!isDexScreenerUrl('javascript:alert(1)//dexscreener.com'));
assert.ok(!isDexScreenerUrl('https://dexscreener.com.evil.io/bsc/0xabc'));
assert.strictEqual(getDexScreenerUrl({ pair_url: 'javascript:alert(1)//dexscreener.com', contract: '0x' + 'a'.repeat(40) }),
  'https://dexscreener.com/bsc/0x' + 'a'.repeat(40), 'a hostile pair_url falls back to the contract link');
assert.strictEqual(getDexScreenerUrl('javascript:alert(1)'), '');
assert.strictEqual(getDexScreenerUrl('0x<img src=x>'), '');
console.log('✔ DexScreener links are checked by host: PASS');

// 4. The token modal: hostile name, symbol, website and twitter render inert
const evil = '<img src=x onerror=alert(1)>';
getEl('modalContent').innerHTML = '';
openModal({
  name: evil, symbol: "X');alert(2);//", contract: '0x' + '2'.repeat(40), standard: 'BEP-20 Token (18 decimals)',
  website: 'javascript:alert(3)', twitter: 'https://x.com/real', market_url: 'javascript:alert(4)',
  pair_url: 'javascript:alert(5)//dexscreener.com', token_price_usd: 0.01, lp_usd: 5000, market_cap: 10000, volume_24h: 1000,
});
const html = getEl('modalContent').innerHTML;
assert.ok(html.length > 1000, 'the modal rendered');
assert.ok(!html.includes('<img src=x'), 'the name is escaped, not markup');
assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'), 'the name is shown as text');
assert.ok(!html.includes('javascript:'), 'no javascript: link survives (website, market_url, pair_url)');
assert.ok(html.includes('href="https://x.com/real"'), 'a real twitter link is kept');
// every inline handler that mentions the symbol must hold it as one complete JS string
const handlers = [...html.matchAll(/on(?:click|keydown)="([^"]*)"/g)].map((m) => decodeAttr(m[1]));
const withSym = handlers.filter((h) => h.includes('alert(2)'));
assert.ok(withSym.length > 5, 'the symbol is passed to several handlers');
for (const h of withSym) {
  assert.ok(h.includes(JSON.stringify("X');alert(2);//")), `symbol stays one string literal in: ${h.slice(0, 120)}`);
}
console.log('✔ token modal renders a hostile token inert: PASS');

// 5. Source guards for the table rows (renderTable needs a full DOM): name / symbol are escaped where they are rendered
const src = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
assert.ok(!src.includes("<strong>${item.name || 'Unknown'}</strong>"), 'table row name is escaped');
assert.ok(!src.includes("(${item.symbol || ''})</span>"), 'table row symbol is escaped');
for (const file of ['app.js', 'features/modal.js']) {
  const code = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  assert.ok(!/on[a-z]+="[^"]*'\$\{escapeHtml\(/.test(code), `${file}: no '\${escapeHtml(...)}' inside an inline handler (use jsAttr)`);
}
console.log('✔ table rows and inline handlers use the safe helpers: PASS');

console.log('test_xss_escaping.js: all assertions passed');
