// Opt-in drop alerts (the ALERTS toggle): permission paths, filter matching, one notification per drop, the 30 s
// rate limit with batching, click-through, analytics, and the existing per-drop popup being replaced while alerts are on.
// Runs against stand-in browser objects and fake timers. Run with: node test_drop_alerts.js
const assert = require('assert');

// ---- stand-in DOM: only what the toggle, the toast and the drop code touch
class El {
  constructor(id) {
    this.id = id;
    this.innerHTML = '';
    this.textContent = '';
    this.className = '';
    this.disabled = false;
    this.attrs = {};
    this.style = {};
    this.children = [];
    this.listeners = {};
    this.classList = { add() {}, remove() {}, contains: () => false, toggle() {} };
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  click() { (this.listeners.click || []).forEach(fn => fn({ target: this })); }
  appendChild(c) { this.children.push(c); return c; }
  removeChild() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  showModal() {}
  close() {}
  remove() {}
}
const els = new Map();
const getEl = (id) => { if (!els.has(id)) els.set(id, new El(id)); return els.get(id); };
// Document-level listeners, so a test can fire the page's own click and keydown handlers
const docListeners = {};
global.document = {
  getElementById: getEl,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => new El('created'),
  addEventListener(type, fn, opts) { (docListeners[type] = docListeners[type] || []).push({ fn, opts }); },
  removeEventListener() {},
  hidden: false,
  body: new El('body'),
  documentElement: new El('html'),
};
global.window = {
  isDevOptedOut: false,
  location: { hostname: 'radar.example', protocol: 'https:', search: '', hash: '', href: 'https://radar.example/' },
  addEventListener() {},
  removeEventListener() {},
  focus() {},
  scrollTo() {},
  posthog: { capture: (name, props) => events.push({ name, props: JSON.parse(JSON.stringify(props || {})) }) },
};
const events = [];

// localStorage stub that can be made to throw
const store = new Map();
const storage = {
  throwOnGet: false,
  throwOnSet: false,
  getItem: (k) => { if (storage.throwOnGet) throw new Error('blocked'); return store.has(k) ? store.get(k) : null; },
  setItem: (k, v) => { if (storage.throwOnSet) throw new Error('blocked'); store.set(k, String(v)); },
  removeItem: (k) => store.delete(k),
};
global.localStorage = storage;

// Fake clock and timers, so the 30 second gap can be advanced without waiting
let fakeNow = 1000000;
let timers = [];
global.setTimeout = (fn, ms) => {
  const t = { id: timers.length + 1, at: fakeNow + Number(ms || 0), fn, done: false };
  timers.push(t);
  return t.id;
};
global.clearTimeout = (id) => {
  const t = timers.find(x => x.id === id);
  if (t) t.done = true;
};
// Recorded intervals, so the poll cadence can be checked
const intervals = [];
global.setInterval = (fn, ms) => {
  const iv = { id: intervals.length + 1, ms, fn, cleared: false };
  intervals.push(iv);
  return iv.id;
};
global.clearInterval = (id) => {
  const iv = intervals.find(x => x.id === id);
  if (iv) iv.cleared = true;
};
const liveIntervals = () => intervals.filter(x => !x.cleared);
Date.now = () => fakeNow;
function advance(ms) {
  const end = fakeNow + ms;
  for (;;) {
    const due = timers.filter(t => !t.done && t.at <= end).sort((a, b) => (a.at - b.at) || (a.id - b.id))[0];
    if (!due) break;
    fakeNow = Math.max(fakeNow, due.at);
    due.done = true;
    due.fn();
  }
  fakeNow = end;
}

// Notification stand-in. `requestResult` is what requestPermission grants or refuses; `callbackStyle` uses the old form.
function makeNotification({ permission = 'granted', requestResult = 'granted', callbackStyle = false, throwOnConstruct = false } = {}) {
  const log = { created: [], requests: 0, permission };
  class FakeNotification {
    constructor(title, opts) {
      if (throwOnConstruct) throw new TypeError("Failed to construct 'Notification': Illegal constructor.");
      this.title = title;
      this.body = (opts && opts.body) || '';
      this.closed = false;
      this.onclick = null;
      log.created.push(this);
    }
    close() { this.closed = true; }
  }
  Object.defineProperty(FakeNotification, 'permission', { get: () => log.permission, configurable: true });
  FakeNotification.requestPermission = (cb) => {
    log.requests++;
    log.permission = requestResult;
    if (callbackStyle) {
      if (cb) cb(requestResult);
      return undefined;
    }
    if (cb) cb(requestResult);
    return Promise.resolve(requestResult);
  };
  log.FakeNotification = FakeNotification;
  return log;
}
function installNotification(log) {
  global.window.Notification = log.FakeNotification;
  global.Notification = log.FakeNotification;
}
function uninstallNotification() {
  delete global.window.Notification;
  delete global.Notification;
}

const app = require('./app.js');
const {
  readDropAlertPref, writeDropAlertPref, getDropAlertPermission, isDropAlertActive,
  onDropAlertToggleClick, showNewMarketToast, dropMatchesToolbarFilter, alertMatchType,
  queueDropAlerts, updateDropAlertToggle, setupDropAlertToggle, setActiveFilter,
  pollDeltaFeed, startDeltaPolling, getLatestKnownBlock, setLatestKnownBlock,
} = app;

let checks = 0;
function check(name, fn) {
  try {
    fn();
    checks++;
  } catch (err) {
    console.error(`FAIL: ${name}`);
    throw err;
  }
}
function drop(name, extra = {}) {
  const n = String(name).toLowerCase().replace(/[^a-z0-9]/g, '');
  return Object.assign({
    contract: `0x${n.padEnd(40, '0')}`,
    name,
    symbol: name.slice(0, 3).toUpperCase(),
    block: 133526300,
    mint_price_str: 'FREE',
    has_free_mint: true,
    sniper_ready: false,
    total_minted: 0,
    max_supply: 1000,
    lp_usd: 0,
  }, extra);
}
function toggleBtn() { return getEl('dropAlertToggle'); }
function resetState() {
  // Run out any flush still pending from the previous section, then start from a closed 30 s gap.
  advance(120000);
  timers = [];
  intervals.length = 0;
  global.document.hidden = false;
  store.clear();
  events.length = 0;
  storage.throwOnGet = false;
  storage.throwOnSet = false;
  uninstallNotification();
  setActiveFilter('ALL');
  writeDropAlertPref(false);
  updateDropAlertToggle();
}
// Let pending promise callbacks (permission prompt results) run.
const flush = () => new Promise(r => setImmediate(r));

(async () => {
  // ---- first click or keydown: the page unlocks audio and asks for no notification permission. Runs the real init()
  // (the DOMContentLoaded listener) so the page's own document listeners are the ones exercised.
  resetState();
  els.clear(); // fresh stand-in controls, so init's listeners are the only ones on each one
  global.fetch = async () => { throw new Error('no network in tests'); };
  const firstLog = makeNotification({ permission: 'default', requestResult: 'granted' });
  installNotification(firstLog);
  await docListeners.DOMContentLoaded[0].fn();
  const once = (type) => (docListeners[type] || []).filter(l => l.opts && l.opts.once);
  check('first click/keydown: unlock listeners are registered', () => {
    assert.strictEqual(once('click').length >= 1, true);
    assert.strictEqual(once('keydown').length >= 1, true);
  });
  // Fire the page's document listeners the way a first click and a first key press would.
  const fire = (type, evt) => {
    for (const l of (docListeners[type] || []).slice()) {
      if (l.opts && l.opts.once) docListeners[type] = docListeners[type].filter(x => x !== l);
      try { l.fn(evt); } catch (err) { /* other page handlers may touch the stand-in DOM; only the permission call matters here */ }
    }
  };
  fire('click', { type: 'click', target: getEl('body') });
  fire('keydown', { type: 'keydown', key: 'a', target: getEl('body') });
  await flush();
  check('first click or keydown: no notification permission request', () => {
    assert.strictEqual(firstLog.requests, 0);
    assert.strictEqual(firstLog.permission, 'default');
  });
  // ALERTS is the one control that asks, and it asks exactly once
  getEl('dropAlertToggle').click();
  await flush();
  check('ALERTS click: asks for permission exactly once and turns alerts on', () => {
    assert.strictEqual(firstLog.requests, 1);
    assert.strictEqual(readDropAlertPref(), true);
    assert.strictEqual(getEl('dropAlertToggle').getAttribute('data-state'), 'on');
  });
  // The SOUND toggle's existing request is unchanged
  firstLog.permission = 'default';
  getEl('soundToggle').click();
  await flush();
  check('SOUND click: still asks for permission, as before', () => assert.strictEqual(firstLog.requests, 2));
  uninstallNotification();

  // ---- storage: persisted with try/catch
  resetState();
  check('storage: default off', () => assert.strictEqual(readDropAlertPref(), false));
  writeDropAlertPref(true);
  check('storage: on persists', () => assert.strictEqual(store.get('agent_drop_alerts'), 'on'));
  check('storage: read back on', () => assert.strictEqual(readDropAlertPref(), true));
  storage.throwOnGet = true;
  check('storage: read blocked falls back to off', () => assert.strictEqual(readDropAlertPref(), false));
  storage.throwOnGet = false;
  storage.throwOnSet = true;
  check('storage: write blocked does not throw', () => { writeDropAlertPref(false); });
  storage.throwOnSet = false;

  // ---- unsupported: no Notification API (iOS Safari tab), nothing requested, toggle says so
  resetState();
  check('unsupported: permission reported as unsupported', () => assert.strictEqual(getDropAlertPermission(), 'unsupported'));
  setupDropAlertToggle();
  check('unsupported: toggle disabled and labelled N/A', () => {
    assert.strictEqual(toggleBtn().disabled, true);
    assert.strictEqual(toggleBtn().getAttribute('data-state'), 'unsupported');
    assert.ok(/ALERTS N\/A/.test(toggleBtn().innerHTML));
    assert.strictEqual(toggleBtn().getAttribute('aria-label'), 'Drop alerts, not supported here');
  });
  await onDropAlertToggleClick();
  check('unsupported: clicking does nothing and stores nothing', () => {
    assert.strictEqual(readDropAlertPref(), false);
    assert.notStrictEqual(store.get('agent_drop_alerts'), 'on');
    assert.strictEqual(events.length, 0);
  });

  // ---- permission already granted: turning on needs no prompt, and loading the page never prompts
  resetState();
  let log = makeNotification({ permission: 'granted' });
  installNotification(log);
  setupDropAlertToggle();
  check('granted: load shows OFF and does not prompt', () => {
    assert.strictEqual(toggleBtn().getAttribute('data-state'), 'off');
    assert.strictEqual(log.requests, 0);
  });
  await onDropAlertToggleClick();
  check('granted: click turns on without prompting', () => {
    assert.strictEqual(log.requests, 0);
    assert.strictEqual(readDropAlertPref(), true);
    assert.strictEqual(toggleBtn().getAttribute('data-state'), 'on');
    assert.strictEqual(toggleBtn().getAttribute('aria-pressed'), 'true');
  });
  check('granted: alert_enabled tracked with permission state', () => {
    const e = events.find(x => x.name === 'alert_enabled');
    assert.ok(e, 'alert_enabled missing');
    assert.deepStrictEqual(e.props, { result: 'on', permission_before: 'granted', permission: 'granted', filter: 'ALL' });
  });

  // ---- permission never asked: the prompt is raised by the click, and the answer is honoured
  resetState();
  log = makeNotification({ permission: 'default', requestResult: 'granted' });
  installNotification(log);
  setupDropAlertToggle();
  check('default: load does not prompt', () => assert.strictEqual(log.requests, 0));
  await onDropAlertToggleClick();
  await flush();
  check('default: click prompts once and turning on follows "granted"', () => {
    assert.strictEqual(log.requests, 1);
    assert.strictEqual(readDropAlertPref(), true);
    assert.strictEqual(toggleBtn().getAttribute('data-state'), 'on');
  });

  // callback-only requestPermission (old Safari form)
  resetState();
  log = makeNotification({ permission: 'default', requestResult: 'granted', callbackStyle: true });
  installNotification(log);
  await onDropAlertToggleClick();
  check('default, callback form: granted is honoured', () => {
    assert.strictEqual(log.requests, 1);
    assert.strictEqual(readDropAlertPref(), true);
  });

  // prompt dismissed: stays off, no error
  resetState();
  log = makeNotification({ permission: 'default', requestResult: 'default' });
  installNotification(log);
  await onDropAlertToggleClick();
  await flush();
  check('dismissed: stays off and is tracked as dismissed', () => {
    assert.strictEqual(log.requests, 1);
    assert.strictEqual(readDropAlertPref(), false);
    assert.strictEqual(toggleBtn().getAttribute('data-state'), 'off');
    assert.strictEqual(events.find(x => x.name === 'alert_enabled').props.result, 'dismissed');
  });

  // permission denied: no prompt is possible, the toggle says blocked and explains
  resetState();
  log = makeNotification({ permission: 'denied' });
  installNotification(log);
  setupDropAlertToggle();
  check('denied: load shows blocked', () => assert.strictEqual(toggleBtn().getAttribute('data-state'), 'blocked'));
  await onDropAlertToggleClick();
  check('denied: click does not prompt, stays off, shows the blocked explanation', () => {
    assert.strictEqual(log.requests, 0);
    assert.strictEqual(readDropAlertPref(), false);
    assert.strictEqual(toggleBtn().getAttribute('data-state'), 'blocked');
    assert.ok(events.some(x => x.name === 'alert_enabled' && x.props.result === 'blocked'));
  });

  // preference on, but the browser later blocks notifications: no alerts, the toggle shows blocked
  resetState();
  log = makeNotification({ permission: 'granted' });
  installNotification(log);
  await onDropAlertToggleClick();
  log.permission = 'denied';
  updateDropAlertToggle();
  check('revoked: state follows the browser', () => {
    assert.strictEqual(readDropAlertPref(), true);
    assert.strictEqual(isDropAlertActive(), false);
    assert.strictEqual(toggleBtn().getAttribute('data-state'), 'blocked');
  });

  // ---- filter matching: the active toolbar filter, or Free Mint + Snipe Ready
  resetState();
  setActiveFilter('ALL');
  check('match: ALL matches every drop', () => assert.strictEqual(alertMatchType(drop('A')), 'filter'));
  setActiveFilter('LP');
  check('match: LP by liquidity', () => assert.strictEqual(alertMatchType(drop('B', { lp_usd: 5, has_free_mint: false, mint_price_str: 'PAID 1 BNB' })), 'filter'));
  check('match: Free Mint + Snipe Ready outside the filter', () => assert.strictEqual(alertMatchType(drop('C', { has_free_mint: true, sniper_ready: true })), 'free_snipe'));
  check('match: both', () => assert.strictEqual(alertMatchType(drop('D', { lp_usd: 9, has_free_mint: true, sniper_ready: true })), 'filter_and_free_snipe'));
  check('match: neither', () => assert.strictEqual(alertMatchType(drop('E', { has_free_mint: false, mint_price_str: 'PAID 1 BNB' })), null));
  check('match: Snipe Ready alone is not a free snipe', () => assert.strictEqual(alertMatchType(drop('F', { has_free_mint: false, mint_price_str: 'PAID 1 BNB', sniper_ready: true })), null));
  setActiveFilter('SNIPER_READY');
  check('match: SNIPER_READY filter', () => assert.strictEqual(alertMatchType(drop('G', { sniper_ready: true, has_free_mint: false, mint_price_str: 'PAID 1 BNB' })), 'filter'));
  check('match: FOLLOWED with nobody followed matches nothing', () => assert.strictEqual(dropMatchesToolbarFilter(drop('H'), 'FOLLOWED'), false));
  check('match: FREE uses the free-mint rule', () => {
    assert.strictEqual(dropMatchesToolbarFilter(drop('I', { has_free_mint: false, mint_price_str: 'PAID 1 BNB' }), 'FREE'), false);
    assert.strictEqual(dropMatchesToolbarFilter(drop('J', { has_free_mint: true }), 'FREE'), true);
  });
  check('match: ACTIVE excludes sold-out drops', () => {
    assert.strictEqual(dropMatchesToolbarFilter(drop('K', { max_supply: 10, total_minted: 10 }), 'ACTIVE'), false);
    assert.strictEqual(dropMatchesToolbarFilter(drop('L', { max_supply: 10, total_minted: 3 }), 'ACTIVE'), true);
  });
  check('match: unknown key matches everything (same as the table)', () => assert.strictEqual(dropMatchesToolbarFilter(drop('M'), 'SOMETHING_ELSE'), true));

  // ---- queue: one notification per drop, at most one per 30 s, the rest batched
  resetState();
  log = makeNotification({ permission: 'granted' });
  installNotification(log);
  await onDropAlertToggleClick();
  events.length = 0;
  setActiveFilter('ALL');
  queueDropAlerts([drop('Alpha')]);
  check('queue: first drop gets its own notification at once', () => {
    assert.strictEqual(log.created.length, 1);
    assert.strictEqual(log.created[0].title, '🚨 New drop: Alpha (ALP)');
    assert.strictEqual(events.filter(x => x.name === 'alert_shown').length, 1);
  });
  queueDropAlerts([drop('Beta'), drop('Gamma'), drop('Beta')]);
  check('queue: drops inside the 30 s gap are held, duplicates ignored', () => assert.strictEqual(log.created.length, 1));
  advance(29999);
  check('queue: still held at 29.999 s', () => assert.strictEqual(log.created.length, 1));
  advance(1);
  check('queue: gap over, the held drops go out as one batch', () => {
    assert.strictEqual(log.created.length, 2);
    assert.strictEqual(log.created[1].title, '🚨 2 new drops match your alerts');
    assert.strictEqual(log.created[1].body, 'Beta, Gamma');
    const shown = events.filter(x => x.name === 'alert_shown').map(x => x.props);
    assert.deepStrictEqual(shown[shown.length - 1], { match_type: 'filter', batch_size: 2, permission: 'granted', filter: 'ALL' });
  });
  advance(30000);
  queueDropAlerts([drop('Delta')]);
  check('queue: gap open again, a lone drop is a single notification', () => {
    assert.strictEqual(log.created.length, 3);
    assert.strictEqual(log.created[2].title, '🚨 New drop: Delta (DEL)');
  });
  queueDropAlerts([drop('Epsilon')]);
  queueDropAlerts([drop('Zeta')]);
  advance(30000);
  check('queue: two drops held inside the gap go out as one batch when it ends', () => {
    assert.strictEqual(log.created.length, 4);
    assert.strictEqual(log.created[3].title, '🚨 2 new drops match your alerts');
    assert.strictEqual(log.created[3].body, 'Epsilon, Zeta');
  });
  advance(60000);
  check('queue: nothing is sent when the gap is quiet', () => assert.strictEqual(log.created.length, 4));

  // a drop that matches nothing is ignored; Free Mint + Snipe Ready matches a filter it is not in
  setActiveFilter('SNIPER_READY');
  queueDropAlerts([drop('Eta', { has_free_mint: false, mint_price_str: 'PAID 1 BNB' })]);
  check('queue: non-matching drop sends nothing', () => assert.strictEqual(log.created.length, 4));
  setActiveFilter('LP');
  queueDropAlerts([drop('Theta', { has_free_mint: true, sniper_ready: true })]);
  check('queue: Free Mint + Snipe Ready body text', () => {
    assert.strictEqual(log.created.length, 5);
    assert.ok(/Free Mint \+ Snipe Ready/.test(log.created[4].body), log.created[4].body);
  });

  // click-through: opens the token (most recent in a batch) and is tracked
  events.length = 0;
  queueDropAlerts([drop('Iota', { lp_usd: 1 })]);
  advance(30000);
  queueDropAlerts([drop('Kappa', { lp_usd: 1 }), drop('Lambda', { lp_usd: 1 })]);
  advance(30000);
  const batchNote = log.created[log.created.length - 1];
  check('click: batch notification has an onclick', () => assert.strictEqual(typeof batchNote.onclick, 'function'));
  let clickThrew = null;
  try { batchNote.onclick(); } catch (err) { clickThrew = err; }
  check('click: tracked as alert_clicked with batch size', () => {
    const e = events.find(x => x.name === 'alert_clicked');
    assert.ok(e, 'alert_clicked missing');
    assert.deepStrictEqual(e.props, { match_type: 'filter', batch_size: 2, permission: 'granted', filter: 'LP' });
  });
  check('click: notification closed', () => assert.strictEqual(batchNote.closed, true));
  if (clickThrew) console.warn('note: modal open threw in the stand-in DOM (not a feature failure):', clickThrew.message);

  // turning off clears what is queued and stops further alerts
  resetState();
  log = makeNotification({ permission: 'granted' });
  installNotification(log);
  await onDropAlertToggleClick();
  setActiveFilter('ALL');
  queueDropAlerts([drop('Mu')]);
  queueDropAlerts([drop('Nu')]);
  await onDropAlertToggleClick();
  advance(60000);
  check('off: queued drops are dropped, nothing more is sent', () => {
    assert.strictEqual(readDropAlertPref(), false);
    assert.strictEqual(log.created.length, 1);
  });
  queueDropAlerts([drop('Xi')]);
  check('off: new drops are not queued', () => assert.strictEqual(log.created.length, 1));

  // ---- while alerts are on, the existing per-drop popup must not add a second notification
  resetState();
  log = makeNotification({ permission: 'granted' });
  installNotification(log);
  await onDropAlertToggleClick();
  setActiveFilter('ALL');
  showNewMarketToast(drop('Pi'));
  check('one notification per drop: the legacy desktop popup is replaced while alerts are on', () => {
    assert.strictEqual(log.created.length, 0);
  });
  // with alerts off but permission granted (SOUND toggle path), the legacy popup is unchanged
  resetState();
  log = makeNotification({ permission: 'granted' });
  installNotification(log);
  showNewMarketToast(drop('Rho'));
  check('alerts off: legacy per-drop popup still fires as before', () => {
    assert.strictEqual(log.created.length, 1);
    assert.strictEqual(log.created[0].title, '🚨 New .ai Agent ID Drop!');
  });

  // ---- background polling: a hidden tab keeps the delta poll only while alerts are on, at a slower pace
  resetState();
  global.document.hidden = true;
  startDeltaPolling();
  check('hidden, alerts off: no delta poll', () => assert.strictEqual(liveIntervals().length, 0));

  log = makeNotification({ permission: 'granted' });
  installNotification(log);
  await onDropAlertToggleClick();
  startDeltaPolling();
  check('hidden, alerts on: one delta poll a minute', () => {
    const live = liveIntervals();
    assert.strictEqual(live.length, 1);
    assert.strictEqual(live[0].ms, 60000);
  });
  global.document.hidden = false;
  startDeltaPolling();
  check('visible again: the fast poll replaces the slow one', () => {
    const live = liveIntervals();
    assert.strictEqual(live.length, 1);
    assert.strictEqual(live[0].ms, 25000);
  });

  // a hidden poll moves the block on and alerts for a new drop, and stays silent
  global.document.hidden = true;
  const requests = [];
  const respond = (projects, latestBlock) => async (url) => {
    requests.push(String(url));
    return {
      status: 200,
      ok: true,
      headers: { get: () => null },
      json: async () => ({ success: true, mode: 'delta', projects, latest_block: latestBlock }),
    };
  };
  global.fetch = respond([drop('Bg Drop', { block: 1005 })], 1005);
  let audioContexts = 0;
  global.window.AudioContext = class {
    constructor() { audioContexts++; this.currentTime = 0; this.state = 'running'; this.destination = {}; }
    resume() {}
    createOscillator() { return { type: '', frequency: { setValueAtTime() {} }, connect() {}, start() {}, stop() {} }; }
    createGain() { return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }; }
  };
  setLatestKnownBlock(1000);
  await pollDeltaFeed();
  check('hidden + alerts on: polls since the last block', () => {
    assert.strictEqual(requests.length, 1);
    assert.ok(requests[0].includes('since_block=1000'), requests[0]);
  });
  check('hidden: the block moves on to the response head', () => assert.strictEqual(getLatestKnownBlock(), 1005));
  check('hidden: the new drop alerts once', () => assert.strictEqual(log.created.length, 1));
  check('hidden: no chime', () => assert.strictEqual(audioContexts, 0));

  requests.length = 0;
  global.fetch = respond([], 1005);
  await pollDeltaFeed();
  check('hidden: the next poll asks from the new head', () => {
    assert.strictEqual(requests.length, 1);
    assert.ok(requests[0].includes('since_block=1005'), requests[0]);
  });
  delete global.window.AudioContext;

  // hidden with alerts off: no request at all
  resetState();
  global.document.hidden = true;
  log = makeNotification({ permission: 'granted' });
  installNotification(log);
  requests.length = 0;
  global.fetch = respond([], 1005);
  setLatestKnownBlock(1000);
  await pollDeltaFeed();
  check('hidden, alerts off: no delta request', () => assert.strictEqual(requests.length, 0));

  // visible: a poll that finds nothing new leaves the block alone (block polling owns it there)
  resetState();
  global.document.hidden = false;
  requests.length = 0;
  global.fetch = respond([], 1005);
  setLatestKnownBlock(1000);
  await pollDeltaFeed();
  check('visible: foreground poll does not move the block', () => assert.strictEqual(getLatestKnownBlock(), 1000));

  // notification constructor throws (Android Chrome: only service-worker notifications): reported as unsupported.
  // Last on purpose: this sets a flag that stays set for the life of the module.
  resetState();
  log = makeNotification({ permission: 'granted', throwOnConstruct: true });
  installNotification(log);
  await onDropAlertToggleClick();
  let threw = null;
  try { queueDropAlerts([drop('Omicron')]); } catch (err) { threw = err; }
  check('ctor-throws: no exception escapes', () => assert.strictEqual(threw, null));
  check('ctor-throws: state becomes unsupported', () => {
    assert.strictEqual(getDropAlertPermission(), 'unsupported');
    assert.strictEqual(toggleBtn().getAttribute('data-state'), 'unsupported');
    assert.strictEqual(toggleBtn().disabled, true);
  });

  uninstallNotification();
  console.log(`test_drop_alerts.js: ${checks} checks passed`);
})().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
