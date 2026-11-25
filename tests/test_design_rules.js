// Heat Terminal colour rules: red is risk's colour and nothing else's; traction escalates ice -> charge -> gold; the safety chip is a
// coloured dot and a word, filled only for a verdict. Run with: node test_design_rules.js
const assert = require('assert');

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

const app = require('./app.js');
const { interestBadgeHtml, rowHeatClass, safetyChipHtml } = app;

// 1. Traction never uses red (red is risk's colour); it escalates ice -> charge -> gold
const tiers = { 90: 'var(--heat-hot)', 60: 'var(--heat-warm)', 30: 'var(--heat-cool)', 5: 'var(--text-dim)' };
for (const [score, colour] of Object.entries(tiers)) {
  const html = interestBadgeHtml(Number(score));
  assert.ok(html.includes(colour), `score ${score} uses ${colour}: ${html}`);
  assert.ok(!/color-red|risk-danger|#ff/i.test(html), `score ${score} does not use a risk colour`);
}
assert.deepStrictEqual([90, 60, 30, 5].map((s) => rowHeatClass({ score: s })), ['heat-surging', 'heat-high', 'heat-moderate', '']);
assert.strictEqual(rowHeatClass(null), '');
assert.strictEqual(rowHeatClass({}), '');
console.log('✔ Traction colours and the row heat class: PASS');

// 2. The safety chip: a dot and a word; filled only for a verdict
const kind = (item, ready) => (safetyChipHtml(item, ready).match(/risk-(safe|caution|high|verdict|ready)/) || [])[1];
const word = (item, ready) => safetyChipHtml(item, ready).replace(/<[^>]+>/g, '');
assert.strictEqual(kind({}), 'safe');
assert.strictEqual(word({}), 'Clean');
assert.strictEqual(kind({ risk_level: 'MED' }), 'caution');
assert.strictEqual(kind({ risk_level: 'HIGH' }), 'high');
assert.strictEqual(kind({ risk_badge: '🚨 Honeypot' }), 'verdict');
assert.strictEqual(kind({ risk_level: 'RUG' }), 'verdict');
assert.strictEqual(word({ risk_level: 'RUG' }), 'Rug risk', 'a RUG with no named finding is not called a honeypot');
assert.strictEqual(word({ risk_level: 'RUG', risk_badge: '🚨 Serial Dev' }), 'Serial dev', 'a serial deployer is not a honeypot');
assert.strictEqual(word({ risk_level: 'RUG', security: { is_honeypot: true } }), 'Honeypot');
assert.strictEqual(word({ risk_level: 'RUG', security: { cannot_sell_all: true } }), 'Honeypot');
assert.strictEqual(kind({ security: { is_serial_dev: true } }), 'verdict');
assert.strictEqual(word({ security: { is_serial_dev: true } }), 'Serial dev');
assert.strictEqual(kind({ security: { is_fund_drain: true } }), 'verdict');
assert.strictEqual(word({ security: { is_fund_drain: true } }), 'Drained');
assert.strictEqual(kind({}, true), 'ready');
assert.strictEqual(kind({ risk_level: 'MED' }, true), 'caution', 'a risk grade wins over "ready"');
assert.strictEqual(kind({ risk_badge: '🚨 Honeypot' }, true), 'verdict');
assert.ok(safetyChipHtml({}).includes('<i class="risk-dot"></i>'), 'every chip carries the dot');
assert.ok(!/[🟢🟡🔴🚨🎯]/u.test(safetyChipHtml({ risk_level: 'RUG' })), 'no emoji: the dot is the glyph');
console.log('✔ Safety chip: a dot and a word, filled only for a verdict: PASS');

console.log('test_design_rules.js: all assertions passed');
