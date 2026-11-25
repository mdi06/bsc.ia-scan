// The agent-wallet counter shows "—" until a real count has arrived (178,001 is only the baseline counting starts from),
// and nothing counts forward from that baseline. Run with: node test_agent_wallets_display.js
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

let fetchCalls = 0;
global.fetch = () => { fetchCalls += 1; return Promise.resolve({ ok: false, json: () => Promise.resolve({}) }); };

const app = require('../app.js');
const { updateDigitalAiCounter, applyAgentWalletsState, syncLiveAgentWalletsOnChain, isAgentWalletsKnown,
  getCurrentAgentWalletsCount, isStaleAgentStats, fetchOverviewStats, hydrateCachedOverviewStats } = app;

const hdr = { textContent: '', style: {} };
const ticker = { textContent: '' };
const screen = { innerHTML: '', appendChild(k) { this.innerHTML += k.textContent; } };
global.document.getElementById = (id) => (id === 'headerAiWalletsCount' ? hdr : id === 'tickerAiWallets' ? ticker : id === 'digitalLcdScreen' ? screen : null);
global.document.createElement = () => ({ className: '', textContent: '' });

(async () => {
  // 1. Before any real count: a dash, whatever the page redraws (renderStats calls this on every render)
  assert.strictEqual(isAgentWalletsKnown(), false);
  updateDigitalAiCounter();
  assert.strictEqual(hdr.textContent, '—', 'header shows a dash, not the 178,001 baseline');
  assert.strictEqual(ticker.textContent, '🤖 — .ai');
  assert.ok(!screen.innerHTML.includes('178'), 'the LCD does not show the baseline either');
  assert.strictEqual(getCurrentAgentWalletsCount(), 178001, 'the baseline is still the starting point for counting');

  // 2. Nothing counts forward from the baseline before a real count is known
  document.hidden = false;
  await syncLiveAgentWalletsOnChain(135000000);
  assert.strictEqual(fetchCalls, 0, 'no on-chain scan from the baseline');
  assert.strictEqual(hdr.textContent, '—');

  // 2b. A bundled stats file from days ago is not a count (it said 182,449 when the real number was ~264,000)
  const nowSec = Date.now() / 1000;
  assert.strictEqual(isStaleAgentStats({ total_created_wallets: 182449, last_updated: nowSec - 11 * 86400 }), true);
  assert.strictEqual(isStaleAgentStats({ total_created_wallets: 182449, last_updated: nowSec - 6 * 86400 }), false);
  assert.strictEqual(isStaleAgentStats({ total_created_wallets: 182449 }), false, 'no timestamp: given the benefit of the doubt');
  assert.strictEqual(isStaleAgentStats(null), false);
  let bundled = { total_created_wallets: 182449, last_updated: nowSec - 11 * 86400 };
  global.fetch = async (url) => {
    fetchCalls += 1;
    if (url.includes('agent_id_stats.json')) return { ok: true, status: 200, json: async () => bundled };
    return { ok: false, status: 503, json: async () => ({}) };           // the API is cold: it did not answer in time
  };
  assert.strictEqual(await fetchOverviewStats(true), false, 'a stale bundled file is skipped');
  assert.strictEqual(isAgentWalletsKnown(), false);
  assert.strictEqual(hdr.textContent, '—', 'the page keeps the dash instead of showing 182,449');

  // 2c. A stale bundled file that an older page version saved in localStorage is dropped at startup, not re-applied
  const store = new Map([['agent_id_overview_stats', JSON.stringify({ total_created_wallets: 182449, last_updated: nowSec - 11 * 86400 })]]);
  global.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
  hydrateCachedOverviewStats();
  assert.strictEqual(store.has('agent_id_overview_stats'), false, 'the stale cached entry is removed');
  assert.strictEqual(isAgentWalletsKnown(), false);
  assert.strictEqual(hdr.textContent, '—');

  // 3. A recent bundled file is still a fine fallback while the API is cold
  bundled = { total_created_wallets: 263700, last_updated: nowSec - 600 };
  assert.strictEqual(await fetchOverviewStats(true), true);
  assert.strictEqual(isAgentWalletsKnown(), true);
  assert.strictEqual(hdr.textContent, '263,700');

  // 3b. A real count from the server replaces it
  applyAgentWalletsState(263727, 135000000);
  assert.strictEqual(isAgentWalletsKnown(), true);
  assert.strictEqual(hdr.textContent, '263,727');
  assert.strictEqual(ticker.textContent, '🤖 263,727 .ai');

  // 4. Redraws keep the real number; an unknown (null) server count does not bring the dash back
  updateDigitalAiCounter();
  assert.strictEqual(hdr.textContent, '263,727');
  applyAgentWalletsState(null, 0);
  assert.strictEqual(hdr.textContent, '263,727');
  console.log('✔ Agent-wallet counter: a dash until a real count arrives, no counting from the baseline: PASS');
  console.log('test_agent_wallets_display.js: all assertions passed');
  process.exit(0);                      // fetchOverviewStats schedules retries after a failed round: do not wait for them
})().catch((err) => { console.error(err); process.exit(1); });
