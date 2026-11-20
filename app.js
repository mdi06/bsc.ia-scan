// BNB AGENT RADAR - Client Web Application

const DONATION_WALLET = '0xa15BB4AEdC1b3426159C3cACf8548Cf09506A20a';
const PANCAKE_ROUTER_V2 = '0x10ED43C718714eb63d5aA57B78B54704E256024E';
const WBNB_ADDRESS = '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c';

let allDrops = [];
// Bumped wherever drop records change in place (merge, balances, market overlay, favourites). renderTable() uses it,
// together with the identity/size of the other inputs, to skip the filter counts and the 24h pulse card on renders
// that only change the filter, search, sort or page.
let dropsDataVersion = 0;
function markDropsDataChanged() { dropsDataVersion++; }
let activeTab = 'RADAR';
let activeFilter = 'ALL';
let sortMode = 'NEWEST';
let searchQuery = '';
// USD per BNB from /api/bnb-price. null until the first answer: no silent fallback number is ever shown.
let bnbPrice = null;
let bnbPriceLive = false;   // the server says its price is fresh (a recent successful on-chain lookup)
let bnbPriceAsOf = null;    // unix seconds of that lookup

function hasUsableBnbPrice() { return typeof bnbPrice === 'number' && isFinite(bnbPrice) && bnbPrice > 0; }

// BNB amount -> USD number, or null when there is no price to convert with
function bnbToUsd(bnb) {
  const n = Number(bnb);
  return hasUsableBnbPrice() && isFinite(n) ? n * bnbPrice : null;
}

// "$12.34" for a BNB amount; "—" without a price; "≈$12.34" (tooltip via bnbPriceNote) when the server's price is stale
function formatBnbAsUsd(bnb) {
  const usd = bnbToUsd(bnb);
  if (usd === null) return '—';
  return (bnbPriceLive ? '' : '≈') + formatCryptoPrice(usd);
}
function bnbPriceNote() {
  if (!hasUsableBnbPrice()) return 'BNB/USD price not available yet';
  return bnbPriceLive ? '' : 'BNB/USD price is stale (the server has no fresh on-chain lookup)';
}

// Web3 State & Live Portfolio Scan
let connectedWallet = null;
let walletProviderName = null;
let isScanningPortfolio = false;
let userNativeBnbBalance = 0.0;
let isAgenticWallet = false;
const AGENT_ID_REGISTRY = '0x60b9262456de011f7d15a54cdd8c48baaa8b022f';
let userOwnedContracts = new Map(); // contract.toLowerCase() -> BigInt(rawBalance)
let nativeBnbItem = null;
let cloudSyncStatus = 'offline';

// Favorites / Watchlist System (persisted in localStorage + auto-stars owned tokens)
const FAVORITES_STORAGE_KEY_GUEST = 'agent_id_favorites';
let favorites = new Set();

function getFavoritesStorageKey() {
  if (connectedWallet) {
    return `agent_id_favorites_${connectedWallet.toLowerCase()}`;
  }
  return FAVORITES_STORAGE_KEY_GUEST;
}

function loadFavoritesForActiveWallet() {
  const key = getFavoritesStorageKey();
  let localFavs = [];
  try {
    const raw = (typeof localStorage !== 'undefined') ? localStorage.getItem(key) : null;
    if (raw) {
      localFavs = JSON.parse(raw);
    } else if (connectedWallet) {
      // Migrate from guest favorites if wallet favorites don't exist yet
      const guestRaw = (typeof localStorage !== 'undefined') ? localStorage.getItem(FAVORITES_STORAGE_KEY_GUEST) : null;
      if (guestRaw) localFavs = JSON.parse(guestRaw);
    }
  } catch (e) {}

  favorites = new Set((Array.isArray(localFavs) ? localFavs : []).map(s => String(s).toLowerCase()));

  // Trigger cloud sync if connected to wallet
  if (connectedWallet) {
    syncFavoritesFromCloud(connectedWallet);
  }
  updateFavPillCount();
}

// Initial load
loadFavoritesForActiveWallet();

function saveFavorites() {
  try {
    if (typeof localStorage !== 'undefined') {
      const key = getFavoritesStorageKey();
      localStorage.setItem(key, JSON.stringify([...favorites]));
    }
  } catch (e) { }

  if (connectedWallet) {
    scheduleCloudFavoritesSync(connectedWallet);
  }
}

let syncTimeout = null;
function scheduleCloudFavoritesSync(wallet) {
  if (syncTimeout) clearTimeout(syncTimeout);
  syncTimeout = setTimeout(() => {
    pushFavoritesToCloud(wallet);
  }, 600);
}

// When running locally (http://localhost:8080), use local backend seamlessly with 0 cloud bandwidth
const BACKEND_URL = (typeof window !== 'undefined' && (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'))
  ? ''
  : 'https://bsc-ia-scan-onrender-com.onrender.com';

// Calls the backend (BACKEND_URL is '' on localhost, so that is the local API there). No same-origin retry: in
// production the page is served by Vercel, which has no /api, so a second attempt could only 404 and add console noise.
// Callers all handle a non-ok response and a thrown network error.
async function apiFetch(path, options) {
  return fetch(BACKEND_URL + path, options);
}

// Lazy features (see scripts/build.js): features/<name>.js modules loaded on first use. The build compiles
// __FEATURE_MANIFEST__ into this file as { name: '/assets/features/<name>.<hash>.js' }; without a build (local
// development) it is undefined and the module is imported straight from /features/<name>.js. Each feature is fetched
// once; a failed load (offline, a deploy replaced the file) is forgotten so the next call tries again.
const FEATURE_MANIFEST = typeof __FEATURE_MANIFEST__ !== 'undefined' ? __FEATURE_MANIFEST__ : null;
const featureLoads = {};
function loadFeature(name) {
  if (!featureLoads[name]) {
    const url = FEATURE_MANIFEST ? FEATURE_MANIFEST[name] : `/features/${name}.js`;
    if (!url) return Promise.reject(new Error(`Unknown feature: ${name}`));
    featureLoads[name] = import(url).catch((err) => {
      delete featureLoads[name];
      throw err;
    });
  }
  return featureLoads[name];
}

// Loads the token modal module after window load, in an idle moment (window.loadWhenIdle, index.html), so it is ready
// before the first click without competing with first paint.
function prefetchFeatures() {
  const whenIdle = typeof window !== 'undefined' && typeof window.loadWhenIdle === 'function'
    ? window.loadWhenIdle
    : (fn) => setTimeout(fn, 3000);
  whenIdle(() => {
    loadFeature('modal').then((m) => { modalFeature = modalFeature || m; }).catch(() => {});
  });
}

// Live BNB/USD from the backend. Keeps the previous value if the lookup fails, and redraws the table when the
// price moves by 1% or more or when it flips between live and stale. A price the server has never fetched
// (live=false and no as_of: its static fallback) is not used at all.
async function refreshBnbPrice() {
  try {
    const res = await apiFetch('/api/bnb-price');
    if (!res.ok) return;
    const data = await res.json();
    const px = Number(data && data.bnb_price_usd);
    if (!(data && px > 0)) return;
    const live = Boolean(data.live);
    if (!live && !data.as_of) {
      bnbPriceLive = false;
      return;
    }
    const prev = bnbPrice;
    const moved = !(prev > 0) || Math.abs(px - prev) / prev >= 0.01 || live !== bnbPriceLive;
    bnbPrice = px;
    bnbPriceLive = live;
    bnbPriceAsOf = data.as_of || null;
    if (moved && typeof renderTable === 'function') renderTable();
  } catch (e) {
    // keep the last known price
  }
}

async function syncFavoritesFromCloud(wallet) {
  if (!wallet) return;
  try {
    const res = await apiFetch(`/api/favorites?wallet=${wallet.toLowerCase()}&_t=${Date.now()}`);
    if (res && res.ok) {
      const data = await res.json();
      if (data && Array.isArray(data.favorites)) {
        let changed = false;
        data.favorites.forEach(c => {
          const cl = String(c).toLowerCase();
          if (!favorites.has(cl)) {
            favorites.add(cl);
            changed = true;
          }
        });
        if (changed) {
          try {
            if (typeof localStorage !== 'undefined') {
              localStorage.setItem(getFavoritesStorageKey(), JSON.stringify([...favorites]));
            }
          } catch (e) {}
          updateFavPillCount();
          renderTable();
        }
        cloudSyncStatus = 'synced';
      }
    }
  } catch (err) {
    // Graceful offline fallback
  }
}

async function pushFavoritesToCloud(wallet) {
  if (!wallet) return;
  try {
    const payload = {
      wallet: wallet.toLowerCase(),
      favorites: [...favorites],
      timestamp: Math.floor(Date.now() / 1000)
    };
    const res = await apiFetch('/api/favorites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (res.ok) cloudSyncStatus = 'synced';
  } catch (err) {
    // Graceful offline fallback
  }
}

function isFavorited(contract, item) {
  if (!contract) return false;
  const c = String(contract).toLowerCase();
  if (favorites.has(c)) return true;
  if (item && item.user_balance && Number(item.user_balance) > 0) return true;
  return false;
}

function toggleFavorite(contract, e) {
  if (e) {
    e.stopPropagation();
    e.preventDefault();
  }
  if (!contract) return;
  const c = String(contract).toLowerCase();
  if (favorites.has(c)) {
    favorites.delete(c);
  } else {
    favorites.add(c);
  }
  saveFavorites();
  markDropsDataChanged();
  updateFavPillCount();
  renderTable();
}
function toggleModalFav(contract) {
  if (!contract) return;
  const c = String(contract).toLowerCase();
  if (favorites.has(c)) {
    favorites.delete(c);
  } else {
    favorites.add(c);
  }
  saveFavorites();
  markDropsDataChanged();
  updateFavPillCount();
  renderTable();
  const btn = document.getElementById('modalFavBtn');
  if (btn) {
    const isFav = favorites.has(c);
    btn.className = isFav ? 'modal-fav-btn starred' : 'modal-fav-btn';
    btn.textContent = isFav ? '★ Favorited' : '☆ Add to Favorites';
  }
}
if (typeof window !== 'undefined') window.toggleModalFav = toggleModalFav;

function updateFavPillCount() {
  if (typeof document === 'undefined') return;
  const favEl = document.getElementById('favCount');
  if (!favEl) return;
  const count = allDrops.filter(d => isFavorited(d.contract, d)).length;
  favEl.textContent = count;
}

// Audio Alert, Discovery Tracking & Feed State
let audioCtx = null;
let soundEnabled = (typeof localStorage !== 'undefined') ? (localStorage.getItem('agent_sound_alerts') !== 'false') : false;
const knownContracts = new Set();
const knownSniperReady = new Set();
let isInitialLoad = true;
let latestKnownBlock = 0;
let globalStats = null;
let isLoadingMore = false;
let hasMoreDrops = true;

// Responsive client detection helper for mobile-first silence & optimization
function isMobileClient() {
  if (typeof window === 'undefined') return false;
  const isSmall = Boolean(window.innerWidth && window.innerWidth <= 768);
  const isMobileUa = typeof navigator !== 'undefined' && Boolean(navigator.userAgent) && /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
  return isSmall || isMobileUa;
}
if (typeof window !== 'undefined') window.isMobileClient = isMobileClient;

// Native Browser / Desktop Push Notification System
function requestNotificationPermission() {
  if (isMobileClient()) return; // Silent on mobile: no push notification prompts
  if (typeof window !== 'undefined' && 'Notification' in window) {
    if (Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {});
    }
  }
}
if (typeof window !== 'undefined') window.requestNotificationPermission = requestNotificationPermission;

function sendDesktopNotification(title, body) {
  if (isMobileClient()) return; // Silent on mobile: no system push notifications
  if (typeof window === 'undefined' || !('Notification' in window)) return;
  if (Notification.permission === 'granted') {
    try {
      new Notification(title, {
        body: body,
        icon: 'icon.svg',
        badge: 'icon.svg'
      });
    } catch (e) {
      // Safe fallback on strict mobile browsers
    }
  }
}
if (typeof window !== 'undefined') window.sendDesktopNotification = sendDesktopNotification;

// ---- Opt-in drop alerts (the "ALERTS" toggle next to SOUND) ----
// Browser only, no backend: it works while the page is open and polling. Permission is requested only from the
// toggle's own click, never on load. While on, each NEW drop that matches the active toolbar filter, or is Free Mint
// plus Snipe Ready, gets its own notification. Notifications go out at most once per DROP_ALERT_MIN_GAP_MS; drops that
// arrive inside the gap are sent as one batch when it ends. "Seen" is knownContracts: the snapshot and the first live
// load seed it, and new-drop detection only runs after that, so drops already on the page never alert.
const DROP_ALERT_STORAGE_KEY = 'agent_drop_alerts';
const DROP_ALERT_MIN_GAP_MS = 30000;
let dropAlertPref = readDropAlertPref();
let dropAlertQueue = [];
let dropAlertFlushTimer = null;
let dropAlertLastShownAt = 0;
let dropAlertConstructFailed = false; // `new Notification` threw (Android Chrome): treat the API as unsupported here

function readDropAlertPref() {
  try {
    return typeof localStorage !== 'undefined' && localStorage.getItem(DROP_ALERT_STORAGE_KEY) === 'on';
  } catch (e) {
    return false; // storage blocked (private mode, sandboxed frame): the toggle starts off
  }
}

function writeDropAlertPref(on) {
  dropAlertPref = Boolean(on);
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem(DROP_ALERT_STORAGE_KEY, dropAlertPref ? 'on' : 'off');
  } catch (e) {
    // Storage blocked: the setting then lasts for this page view only
  }
}

function getNotificationCtor() {
  try {
    if (typeof window !== 'undefined' && window.Notification) return window.Notification;
    if (typeof Notification !== 'undefined') return Notification;
  } catch (e) {
    // No Notification API in this context
  }
  return null;
}

// 'unsupported' (no API, e.g. iOS Safari tabs), 'granted', 'denied' or 'default' (never asked, or dismissed)
function getDropAlertPermission() {
  const N = getNotificationCtor();
  if (!N || dropAlertConstructFailed) return 'unsupported';
  return typeof N.permission === 'string' ? N.permission : 'default';
}

function isDropAlertActive() {
  return dropAlertPref && getDropAlertPermission() === 'granted';
}

// Called only from the toggle click. Resolves 'granted' | 'denied' | 'default'. Handles both the promise and the older
// callback form of requestPermission; resolves 'default' if the browser throws.
function askDropAlertPermission() {
  return new Promise(resolve => {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve(result || 'default');
    };
    try {
      const ret = getNotificationCtor().requestPermission(finish);
      if (ret && typeof ret.then === 'function') ret.then(finish, () => finish('default'));
    } catch (e) {
      finish('default');
    }
  });
}

async function onDropAlertToggleClick() {
  const before = getDropAlertPermission();
  if (before === 'unsupported') return;
  if (dropAlertPref && before === 'granted') {
    writeDropAlertPref(false);
    dropAlertQueue = [];
    if (dropAlertFlushTimer) { clearTimeout(dropAlertFlushTimer); dropAlertFlushTimer = null; }
    updateDropAlertToggle();
    return;
  }
  // Only a 'default' permission opens the browser prompt; 'granted' and 'denied' are answered without one.
  const after = before === 'default' ? await askDropAlertPermission() : before;
  const on = after === 'granted';
  writeDropAlertPref(on);
  trackAnalytics('alert_enabled', {
    result: on ? 'on' : (after === 'denied' ? 'blocked' : 'dismissed'),
    permission_before: before,
    permission: after,
    filter: activeFilter
  });
  if (on) {
    showToast('🔔 NOTIFICATIONS ON', 'One notification per new matching drop, at most every 30 seconds.', '🔔');
  } else if (after === 'denied') {
    showToast('🔕 NOTIFICATIONS BLOCKED', 'Allow notifications for this site in your browser settings, then click ALERTS again.', '🔕');
  }
  updateDropAlertToggle();
}

// Same rules as the toolbar filter block in renderTable (keep the two in step). 'ALL' and unknown keys match everything.
function dropMatchesToolbarFilter(d, key) {
  if (!d) return false;
  switch (key) {
    case 'LAUNCHPAD': return isLaunchpadDrop(d);
    case 'SNIPER_READY': return isSniperReady(d);
    case 'NFT_SURGE': return Boolean(d.is_nft_surge);
    case 'PROXY': return Boolean(d.is_proxy_clone);
    case 'FAVORITES': return isFavorited(d.contract, d);
    case 'FOLLOWED': {
      if (followedWallets.size === 0) return false;
      const sw = Array.isArray(d.smart_wallets) && d.smart_wallets.length > 0
        ? d.smart_wallets
        : (Array.isArray(d.smart_minters) ? d.smart_minters : []);
      return sw.some(w => followedWallets.has(String(w).toLowerCase().trim()));
    }
    case 'FREE': return isFreeMintDrop(d);
    case 'SMART': return Boolean((d.smart_wallets && d.smart_wallets.length > 0) || (d.smart_minters && d.smart_minters.length > 0));
    case 'LP': return Number(d.lp_usd || 0) > 0;
    case 'ACTIVE': {
      const ms = Number(d.max_supply || 0);
      const tm = Number(d.total_minted || 0);
      return ms === 0 || tm < ms;
    }
    default: return true;
  }
}

// Why a new drop qualifies: 'filter', 'free_snipe', 'filter_and_free_snipe', or null (no alert).
function alertMatchType(drop) {
  const byFilter = dropMatchesToolbarFilter(drop, activeFilter);
  const byFreeSnipe = isFreeMintDrop(drop) && isSniperReady(drop);
  if (byFilter && byFreeSnipe) return 'filter_and_free_snipe';
  if (byFilter) return 'filter';
  if (byFreeSnipe) return 'free_snipe';
  return null;
}

function dropAlertMatchLabel(match) {
  if (match === 'free_snipe') return 'Free Mint + Snipe Ready';
  if (match === 'filter_and_free_snipe') return 'Matches your filter · Free Mint + Snipe Ready';
  return 'Matches your filter';
}

// Called with the drops each feed poll found for the first time (after the first live load).
function queueDropAlerts(drops) {
  if (!dropAlertPref || !Array.isArray(drops) || drops.length === 0) return;
  if (!isDropAlertActive()) {
    updateDropAlertToggle(); // e.g. permission was revoked in the browser since the toggle was turned on
    return;
  }
  let added = false;
  drops.forEach(drop => {
    const contract = String((drop && drop.contract) || '').toLowerCase();
    if (!contract || dropAlertQueue.some(q => q.contract === contract)) return;
    const match = alertMatchType(drop);
    if (!match) return;
    dropAlertQueue.push({ contract, drop, match });
    added = true;
  });
  if (added) scheduleDropAlertFlush();
}

function scheduleDropAlertFlush() {
  if (dropAlertFlushTimer || dropAlertQueue.length === 0) return;
  const wait = Math.max(0, dropAlertLastShownAt + DROP_ALERT_MIN_GAP_MS - Date.now());
  if (wait === 0) {
    // The gap is open, so the first drop gets its own notification. Anything else queued waits for the gap to end.
    flushDropAlerts(1);
    scheduleDropAlertFlush();
    return;
  }
  dropAlertFlushTimer = setTimeout(() => {
    dropAlertFlushTimer = null;
    flushDropAlerts(0); // gap over: everything still queued goes out as one notification (a single drop stays a single)
  }, wait);
}

// Sends one notification for up to `limit` queued drops (0 = all of them).
function flushDropAlerts(limit) {
  if (dropAlertFlushTimer) { clearTimeout(dropAlertFlushTimer); dropAlertFlushTimer = null; }
  if (dropAlertQueue.length === 0) return;
  if (!isDropAlertActive()) { dropAlertQueue = []; return; } // turned off or blocked while queued
  const N = getNotificationCtor();
  if (!N) { dropAlertQueue = []; return; }
  const take = limit > 0 ? Math.min(limit, dropAlertQueue.length) : dropAlertQueue.length;
  const batch = dropAlertQueue.splice(0, take);
  const target = batch[batch.length - 1]; // a click opens the most recent drop in the batch
  const matchTypes = [...new Set(batch.map(q => q.match))];
  const matchType = matchTypes.length === 1 ? matchTypes[0] : 'mixed';
  const filterKey = activeFilter;
  let title;
  let body;
  if (batch.length === 1) {
    const d = target.drop;
    const blk = d.block ? `#${String(d.block).slice(-6)}` : 'LIVE';
    title = `🚨 New drop: ${d.name || 'New contract'}${d.symbol ? ` (${d.symbol})` : ''}`;
    body = `${dropAlertMatchLabel(target.match)} · ${d.mint_price_str || 'TBD'} · Block ${blk}`;
  } else {
    const names = batch.map(q => q.drop.name || 'New contract');
    title = `🚨 ${batch.length} new drops match your alerts`;
    body = names.length > 3 ? `${names.slice(0, 3).join(', ')} and ${names.length - 3} more` : names.join(', ');
  }

  const sentAt = Date.now();
  let note;
  try {
    note = new N(title, { body, tag: `radar-drop-${sentAt}`, icon: 'icon.svg', badge: 'icon.svg' });
  } catch (e) {
    // Some mobile browsers only allow notifications through a service worker: treat the API as unsupported here
    dropAlertQueue = [];
    dropAlertConstructFailed = true;
    updateDropAlertToggle();
    return;
  }
  dropAlertLastShownAt = sentAt;
  note.onclick = () => {
    try { if (typeof window !== 'undefined' && typeof window.focus === 'function') window.focus(); } catch (e) { /* ignore */ }
    try { if (typeof note.close === 'function') note.close(); } catch (e) { /* ignore */ }
    trackAnalytics('alert_clicked', { match_type: matchType, batch_size: batch.length, permission: 'granted', filter: filterKey });
    if (typeof openInspectModalByContract === 'function') openInspectModalByContract(target.contract, target.drop);
  };
  trackAnalytics('alert_shown', { match_type: matchType, batch_size: batch.length, permission: 'granted', filter: filterKey });
}

// Reflects state only. Called on load and after each toggle; it never requests permission.
function updateDropAlertToggle() {
  if (typeof document === 'undefined' || typeof document.getElementById !== 'function') return;
  const btn = document.getElementById('dropAlertToggle');
  if (!btn) return;
  // The visible label spells out the state (ON / OFF / BLOCKED) next to the icon and colour, plus aria-pressed and the
  // accessible name. "N/A" is shown only where the API is missing.
  const perm = getDropAlertPermission();
  let mode;
  let icon;
  let visible;
  let stateWord;
  let hint;
  if (perm === 'unsupported') {
    mode = 'unsupported'; icon = '🚫'; visible = 'ALERTS N/A'; stateWord = 'not supported here';
    hint = 'Drop alerts need browser notifications, which this browser does not offer. Nothing is requested.';
  } else if (perm === 'denied') {
    mode = 'blocked'; icon = '⛔'; visible = 'ALERTS BLOCKED'; stateWord = 'blocked';
    hint = 'Notifications are blocked for this site. Click for how to allow them.';
  } else if (isDropAlertActive()) {
    mode = 'on'; icon = '🔔'; visible = 'ALERTS ON'; stateWord = 'on';
    hint = 'Drop alerts are on: one notification per new drop that matches your filter or is Free Mint + Snipe Ready (at most every 30 seconds). Click to turn off.';
  } else {
    mode = 'off'; icon = '🔕'; visible = 'ALERTS OFF'; stateWord = 'off';
    hint = 'Get a browser notification when a new drop matches your filter or is Free Mint + Snipe Ready. Click to allow.';
  }
  // Polls call this while alerts are on but blocked; re-rendering an unchanged state would only churn the DOM.
  if (typeof btn.getAttribute === 'function' && btn.getAttribute('data-state') === mode) return;
  btn.className = mode === 'on' ? 'sound-toggle-btn alert-toggle-btn active'
    : `sound-toggle-btn alert-toggle-btn muted${mode === 'blocked' ? ' blocked' : ''}${mode === 'unsupported' ? ' unsupported' : ''}`;
  btn.innerHTML = `<span class="sound-icon">${icon}</span><span class="sound-label">${visible}</span>`;
  btn.disabled = mode === 'unsupported';
  if (typeof btn.setAttribute === 'function') {
    btn.setAttribute('aria-pressed', mode === 'on' ? 'true' : 'false');
    btn.setAttribute('aria-label', `Drop alerts, ${stateWord}`);
    btn.setAttribute('title', hint);
    btn.setAttribute('data-state', mode);
  }
}

function setupDropAlertToggle() {
  if (typeof document === 'undefined' || typeof document.getElementById !== 'function') return;
  const btn = document.getElementById('dropAlertToggle');
  if (btn && typeof btn.addEventListener === 'function') {
    btn.addEventListener('click', () => {
      onDropAlertToggleClick().catch(() => {});
    });
  }
  updateDropAlertToggle(); // state only: no permission request on load
}

// Safe HTML escaping helper
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Crisp, futuristic Cyberpunk Web Audio Chime (D5 -> A5 -> D6)
function playMarketAlertSound() {
  if (isMobileClient()) return; // Silent on mobile: updates table silently without audio disruption
  if (!soundEnabled) return;
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    if (!audioCtx) {
      audioCtx = new AudioCtx();
    }
    if (audioCtx.state === 'suspended') {
      audioCtx.resume();
    }

    const now = audioCtx.currentTime;

    // Harmonic 3-note ascending victory chime (587Hz -> 880Hz -> 1175Hz)
    const notes = [
      { freq: 587.33, start: 0.00, dur: 0.12, gain: 0.22 },
      { freq: 880.00, start: 0.09, dur: 0.16, gain: 0.28 },
      { freq: 1174.66, start: 0.20, dur: 0.35, gain: 0.25 }
    ];

    notes.forEach(n => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();

      osc.type = 'sine';
      osc.frequency.setValueAtTime(n.freq, now + n.start);

      gain.gain.setValueAtTime(0.0001, now + n.start);
      gain.gain.linearRampToValueAtTime(n.gain, now + n.start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + n.start + n.dur);

      osc.connect(gain);
      gain.connect(audioCtx.destination);

      osc.start(now + n.start);
      osc.stop(now + n.start + n.dur);
    });
  } catch (err) {
    console.warn('Audio alert error:', err);
  }
}

// Visual Toast notification on new drop
function showNewMarketToast(item) {
  if (isMobileClient()) return; // Silent on mobile: updates table silently without toast popups
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = 'market-toast drop-alert';
  const blkStr = item.block ? `#${String(item.block).slice(-6)}` : 'LIVE';
  toast.innerHTML = `
    <div class="toast-icon">🚨</div>
    <div class="toast-content">
      <div class="toast-title">NEW MARKET DISCOVERED</div>
      <div class="toast-desc">${escapeHtml(item.name || 'New Agent Contract')} (${escapeHtml(item.symbol || 'AGENT')})</div>
      <div class="toast-meta">Block ${blkStr} · ${escapeHtml(item.mint_price_str || 'TBD')} · ${escapeHtml(item.standard || 'ERC-721')}</div>
    </div>
  `;
  container.appendChild(toast);

  // With drop alerts on, queueDropAlerts() owns browser notifications (one per matching drop), so skip this generic one.
  if (!isDropAlertActive()) {
    sendDesktopNotification(
      '🚨 New .ai Agent ID Drop!',
      `${item.name || 'New Contract'} (${item.symbol || ''}) at Block ${blkStr}`
    );
  }

  // Auto-dismiss after 5s
  setTimeout(() => {
    if (toast.classList) toast.classList.add('fade-out');
    setTimeout(() => {
      if (toast.remove) toast.remove();
      else if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 400);
  }, 5000);
}

// Generic feedback toast
function showToast(title, desc, icon = '💎', meta = '') {
  // Silent market/drop alerts on mobile (allow explicit user actions like copy address)
  if (isMobileClient() && (String(title).includes('ALERT') || String(title).includes('DROP') || String(title).includes('MARKET'))) {
    return;
  }
  const container = document.getElementById('toastContainer');
  if (!container) return;

  // Clean duplicate leading emoji from title if icon is present
  const cleanTitle = (icon && title)
    ? title.replace(/^[\p{Extended_Pictographic}\uFE0F\s]+/u, '').trim()
    : title;

  const toast = document.createElement('div');
  toast.className = 'market-toast drop-alert';
  toast.innerHTML = `
    <div class="toast-icon">${icon}</div>
    <div class="toast-content">
      <div class="toast-title">${escapeHtml(cleanTitle)}</div>
      <div class="toast-desc">${escapeHtml(desc)}</div>
      ${meta ? `<div class="toast-meta">${escapeHtml(meta)}</div>` : ''}
    </div>
  `;
  container.appendChild(toast);

  setTimeout(() => {
    if (toast.classList) toast.classList.add('fade-out');
    setTimeout(() => {
      if (toast.remove) toast.remove();
      else if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 400);
  }, 4500);
}

// Copy Donation Wallet Address Helper
function copyDonationAddress(btn, location = 'header') {
  if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(DONATION_WALLET);
  }
  if (btn) {
    const orig = btn.innerHTML;
    btn.innerHTML = '✓ Copied!';
    btn.style.color = 'var(--color-green)';
    setTimeout(() => {
      btn.innerHTML = orig;
      btn.style.color = '';
    }, 2000);
  }
  const shortAddr = `${DONATION_WALLET.slice(0, 10)}...${DONATION_WALLET.slice(-8)}`;
  showToast(
    'DONATION WALLET COPIED',
    `${shortAddr} (BNB / EVM)`,
    '💎',
    'Address copied to clipboard · Thank you for supporting!'
  );
  if (typeof window !== 'undefined' && window.va) {
    window.va('event', { name: 'tip_dev_copy', data: { location } });
  }
}
if (typeof window !== 'undefined') window.copyDonationAddress = copyDonationAddress;

// Element Market URL helper (hyphenated slug)
function getElementUrl(name, contract) {
  if (name && name !== "Unknown") {
    const cleanName = name.replace(/\([^)]*\)/g, '').trim();
    const slug = cleanName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    if (slug) {
      return `https://element.market/collections/${slug}`;
    }
  }
  return `https://element.market/collections/${contract}`;
}

// Universal DexScreener BSC Pair / Contract URL Resolver
function getDexScreenerUrl(item) {
  if (!item) return '';
  if (typeof item === 'string') {
    const s = item.trim();
    if (s.startsWith('http')) return s;
    if (s.startsWith('0x')) return `https://dexscreener.com/bsc/${s.toLowerCase()}`;
    return '';
  }
  if (item.pair_url && item.pair_url.includes('dexscreener.com')) return item.pair_url;
  if (item.pair_address && item.pair_address.startsWith('0x')) {
    return `https://dexscreener.com/bsc/${item.pair_address.toLowerCase()}`;
  }
  const contract = item.contract || item.address || '';
  if (contract && contract.startsWith('0x') && contract.toLowerCase() !== '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee') {
    return `https://dexscreener.com/bsc/${contract.toLowerCase()}`;
  }
  return '';
}
if (typeof window !== 'undefined') {
  window.getDexScreenerUrl = getDexScreenerUrl;
}

// Format numbers nicely (e.g. 1.2K, 500). Unknown (null / undefined / NaN / -Infinity) is "—", never "0";
// an unlimited supply (+Infinity or >= 1e20) is "∞". Tiers K M B T Q; negatives are abbreviated too, and a value
// that rounds up to the next tier is shown in that tier (999950 -> "1.0M", not "1000.0K").
const COUNT_TIERS = [[1e15, 'Q'], [1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']];
function formatCount(val) {
  if (val === null || val === undefined || val === '') return '—';
  const n = Number(val);
  if (Number.isNaN(n) || n === -Infinity) return '—';
  if (n === Infinity || Math.abs(n) >= 1e20) return n < 0 ? '—' : '∞';
  const sign = n < 0 ? '-' : '';
  const abs = Math.abs(n);
  for (let i = 0; i < COUNT_TIERS.length; i++) {
    const [div, suffix] = COUNT_TIERS[i];
    if (abs >= div) {
      let text = (abs / div).toFixed(1);
      if (parseFloat(text) >= 1000 && i > 0) {  // 999.96K rounds to 1000.0K: that is 1.0M
        const [upDiv, upSuffix] = COUNT_TIERS[i - 1];
        text = (abs / upDiv).toFixed(1);
        return sign + text + upSuffix;
      }
      return sign + text + suffix;
    }
  }
  return n.toLocaleString();
}

// BNB amount without float noise or lost precision: 0.30000000000000004 -> "0.3", 0.00001 -> "0.00001"
function formatBnb(val) {
  if (val === null || val === undefined || val === '') return '—';
  const n = Number(val);
  if (!isFinite(n)) return '—';
  if (n === 0) return '0';
  const abs = Math.abs(n);
  const decimals = abs >= 1 ? 4 : Math.min(18, Math.ceil(-Math.log10(abs)) + 3);
  const text = n.toFixed(decimals);
  return text.indexOf('.') >= 0 ? text.replace(/0+$/, '').replace(/\.$/, '') : text;
}

// "MC" when the number is a market cap, "FDV" when only a fully diluted valuation is known
function mcapLabel(item) {
  if (!item) return 'MC';
  return (numOrZero(item.market_cap) > 0 || numOrZero(item.fdv) <= 0) ? 'MC' : 'FDV';
}

// Age of the record's market data, from market_updated_at (unix seconds)
function formatMarketAge(ts, nowMs = Date.now()) {
  const t = Number(ts);
  if (!isFinite(t) || t <= 0) return '';
  const sec = Math.max(0, Math.round(nowMs / 1000 - t));
  if (sec < 90) return 'just now';
  if (sec < 5400) return `${Math.round(sec / 60)}m ago`;
  if (sec < 172800) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

// Small badge for a record whose market data was cleared as stale, or an age hint when it is old.
function marketFreshnessHtml(item, nowMs = Date.now()) {
  if (!item) return '';
  if (item.market_stale) {
    return ` <span class="badge badge-yellow" style="font-size: 9px; padding: 1px 4px;" title="No fresh DexScreener / Pancake data for this token for hours: its market numbers were cleared">⚠ stale</span>`;
  }
  const t = Number(item.market_updated_at);
  if (isFinite(t) && t > 0 && nowMs / 1000 - t > 1800) {
    return ` <span class="dim" style="font-size: 9px;" title="Market data last confirmed ${formatMarketAge(t, nowMs)}">· ${formatMarketAge(t, nowMs)}</span>`;
  }
  return '';
}

function withThousands(fixedStr) {
  const dot = fixedStr.indexOf('.');
  const intPart = dot >= 0 ? fixedStr.slice(0, dot) : fixedStr;
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (dot >= 0 ? fixedStr.slice(dot) : '');
}

// Universal Crypto Price & Net Profit Formatter (Supports Sub-Cent & Micro-Cap Tokens).
// Unknown or non-finite values are "—"; a value that rounds up into the next band uses that band's format
// (0.99999 -> "$1.00", not "$1.0000"); large values get thousands separators.
function formatCryptoPrice(val, isNet = false) {
  if (val === null || val === undefined || val === '') return '—';
  const n = Number(val);
  if (!isFinite(n)) return '—';
  if (n === 0) return isNet ? '+$0.00' : '$0.00';
  const sign = n < 0 ? '-' : (isNet ? '+' : '');
  const absN = Math.abs(n);
  if (absN >= 1e21) return `${sign}$${absN.toExponential(2)}`;
  // [lower bound, decimals]; a result that rounds up to the band above is formatted with that band instead
  const bands = [[1.0, 2], [0.01, 4], [0.0001, 6], [1e-6, 8]];
  for (let i = 0; i < bands.length; i++) {
    const [lower, dec] = bands[i];
    if (absN >= lower) {
      let text = absN.toFixed(dec);
      if (i > 0 && Number(text) >= bands[i - 1][0]) text = absN.toFixed(bands[i - 1][1]);
      return `${sign}$${Number(text) >= 1 ? withThousands(text) : text}`;
    }
  }
  if (absN >= 1e-8) {
    const cleanStr = absN.toFixed(10).replace(/0+$/, '').replace(/\.$/, '');
    return `${sign}$${cleanStr || '0.00'}`;
  }
  if (absN < 1e-12) {
    return `${sign}<$0.000000000001`;
  }
  const fullStr = absN.toFixed(14).replace(/0+$/, '').replace(/\.$/, '');
  if (!fullStr || fullStr === '0') return `${sign}$0.00`;
  return `${sign}$${fullStr}`;
}

// Known major pegged / Layer-1 tokens and synthetic equities on BSC.
// These lists, isExcludedMajorAsset, getSanitizedMcap, isSecondaryMarketAsset and computeItemProfit mirror
// filters.py / monitor.py line by line; test_parity_python_js.py fails when they drift.
const EXCLUDED_PEGS_SYMBOLS = new Set([
  'BTCB', 'WBTC', 'BTC', 'ETH', 'WETH', 'WBNB', 'BNB', 'USDT', 'USDC', 'BUSD',
  'DAI', 'TUSD', 'FDUSD', 'CAKE', 'U', 'OSK', 'FIST',
  'SOL', 'XRP', 'ADA', 'DOGE', 'DOT', 'AVAX', 'MATIC', 'POL', 'LINK', 'LTC', 'SHIB', 'UNI',
  'ZEC', 'BCH', 'XLM', 'EOS', 'ATOM', 'TRX', 'FIL', 'NEAR', 'XMR', 'ETC', 'APT', 'SUI', 'PEPE', 'FLOKI', 'DOGEZILLA',
  // Other dollar stablecoins: not agent mints, and their multi-billion caps would top every valuation stat
  'USD1', 'USDE', 'USDD', 'USDS', 'USD0', 'FRAX', 'LUSD', 'PYUSD', 'USDP', 'GUSD'
]);

const EXCLUDED_PEGS_CONTRACTS = new Set([
  '0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c', // BTCB
  '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c', // WBNB
  '0x55d398326f99059ff775485246999027b3197955', // USDT
  '0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d', // USDC
  '0xe9e7cea3dedca5984780bafc599bd69add087d56', // BUSD
  '0x2170ed0880ac9a755fd29b2688956bd959f933f8', // ETH (Binance-Peg)
  '0x14016e85a25a55f2e084741d69d9d82361f38e2b', // DAI
  '0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82'  // CAKE
]);

// Contracts the API removes outright (filters.BOGUS_SCAM_CONTRACTS)
const BOGUS_SCAM_CONTRACTS = new Set([
  '0xd0523ce816e2b404db5633d181572cded33d7fa9', // PrinterAi
  '0xc5a5f6337bd9d1ee1a7367a804fce07094956922', // The Bank
  '0x2170ed0880ac9a755fd29b2688956bd959f933f8', // Binance-Peg ETH
  '0x7425889fe94f9d693e8daefe88bcced6acfef4c0', // METAB
  '0xbe9d156892e55e7154bcd3cb0fea677f9d3103e1', // SPCXB
  '0x80106cb3ead06659a5ad19df39d9b4733863b9b0', // MSFTB
  '0x5b1910eaad6450e50f816082aa078c41f10c292f', // TSLAB
  '0x431a6136ee1b2f76378c8a14b53ef11ba9099b7a', // AAPLB
  '0x360d0b00c3c3a9d70e6e7372cfbe85e68b95973c', // DogeZilla legacy clone
  '0xb9e1fd5a02d3a33b25a14d661414e6ed6954a721', // SOON Token
  '0x2f8a339b5889ffac4c5a956787cda593b3c36867'  // CMC20 (CoinMarketCap 20 Index DTF)
]);
const EXCLUDED_NATIVE_PLACEHOLDER = '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee';

const SYNTHETIC_EQUITY_SYMBOLS = new Set([
  'TSLA', 'TSLAB', 'AAPL', 'AAPLB', 'MSFT', 'MSFTB', 'META', 'METAB', 'SPCXB', 'NVDA', 'NVDAB', 
  'AMZN', 'AMZNB', 'GOOGL', 'GOOGLB', 'MRNA', 'MRNAB', 'SOXS', 'SOXSB', 'SOXL', 'SOXLB', 
  'CRCLB', 'QQQ', 'QQQB', 'TQQQ', 'TQQQB', 'MSTR', 'MSTRB', 'BABA', 'BABAB', 'BNCB', 'SKHYB', 
  'COIN', 'COINB', 'PLTR', 'PLTRB', 'AMD', 'AMDB', 'SPY', 'SPYB', 'TSMC', 'TSMB', 'PDD', 'PDDB', 
  'NFLX', 'NFLXB', 'INTC', 'INTCB', 'DIS', 'DISB', 'UBER', 'UBERB', 'BA', 'BAAB'
]);

// Matched as whole words / phrases, not substrings ("ETF" must not hit "Rocketfi")
const EXCLUDED_NAME_PHRASES = [
  'ETF', 'PROSHARES', 'DIREXION', 'BEAR 3X', 'BULL 3X', 'INVESQO', 'INVESCO',
  'BSTOCKS', 'TOKENIZED STOCK', 'S&P 500', 'NASDAQ', 'ISHARES', 'VANGUARD',
  'PANCAKESWAP', 'BINANCE-PEG', 'WRAPPED BNB', 'TETHER USD', 'USD COIN', 'WORLD LIBERTY', 'ZCASH', 'DOGEZILLA'
];
const EXCLUDED_NAME_RE = new RegExp(
  EXCLUDED_NAME_PHRASES.map(p => '(?<![A-Z0-9])' + p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?![A-Z0-9])').join('|')
);

// Finite number or 0 (the same coercion as filters._num)
function numOrZero(v) {
  const n = Number(v || 0);
  return Number.isFinite(n) ? n : 0;
}

// The server's answers for this record (api.with_server_metrics): { excluded, market_cap, secondary, profit? }.
// null when the record does not carry them (old cached responses, the static seeded_*.json / feed_snapshot.json
// files, anything the browser built or changed itself); the callers below then calculate by themselves, with the
// code that mirrors the server's.
function serverMetrics(d) {
  const sm = d && typeof d === 'object' ? d.server_metrics : null;
  return sm && typeof sm === 'object' ? sm : null;
}

// Determine if a token is a major pegged/infrastructure asset, stablecoin, synthetic stock, known scam or DEX platform token
function isExcludedMajorAsset(d) {
  if (!d || typeof d !== 'object' || Object.keys(d).length === 0) return true;
  const sm = serverMetrics(d);
  if (sm && typeof sm.excluded === 'boolean') return sm.excluded;
  const c = String(d.contract || d.address || '').toLowerCase().trim();
  if (EXCLUDED_PEGS_CONTRACTS.has(c) || BOGUS_SCAM_CONTRACTS.has(c) || c === EXCLUDED_NATIVE_PLACEHOLDER) return true;
  const sym = String(d.symbol || d.token_symbol || '').toUpperCase().trim();
  if (EXCLUDED_PEGS_SYMBOLS.has(sym) || SYNTHETIC_EQUITY_SYMBOLS.has(sym)) return true;
  return EXCLUDED_NAME_RE.test(String(d.name || '').toUpperCase().trim());
}

// Interest tier from the score: the worker's bands (filters.interest_tier)
function interestTier(score) {
  const s = numOrZero(score);
  if (s >= 80) return 'SURGING';
  if (s >= 50) return 'HIGH';
  if (s >= 25) return 'MODERATE';
  return 'EARLY';
}

// One badge for every table render path (renderTable and appendNextTableBatch): the real score, one threshold set
function interestBadgeHtml(score) {
  const sc = numOrZero(score);
  switch (interestTier(sc)) {
    case 'SURGING': return `<span style="color: var(--color-red); font-weight:700;">🔥 Surging (${sc})</span>`;
    case 'HIGH': return `<span style="color: var(--color-magenta); font-weight:700;">🚀 High (${sc})</span>`;
    case 'MODERATE': return `<span style="color: var(--color-yellow); font-weight:600;">⚡ Moderate (${sc})</span>`;
    default: return `<span style="color: var(--text-dim);">🌱 Early (${sc})</span>`;
  }
}

// Compute Individual Project Profit and Maintain ATH High-Water Mark.
// Line-by-line twin of monitor.update_post_mint_profit_metrics (rules are written out above it there):
//   cost known = paid mint price above zero, or a VERIFIED free mint (has_free_mint / "FREE"); "DEX Traded" has none.
//   profit / multiplier are null unless the cost is known; a loss stays negative; a free mint has no multiplier.
//   ATH price needs >= ATH_MIN_LP_USD of pool liquidity (floors excepted); ATH profit = max(previous, current).
// The client therefore recomputes exactly what the server would: it never overwrites the server's ATH with a
// different rule. Returns multiplier = ATH multiplier, currentMultiplier = the current one.
const ATH_MIN_LP_USD = 1000;
const ATH_RESET_FACTOR = 50;

function round2(x) { return Math.floor(x * 100 + 0.5) / 100; }
function sig10(x) { return Number(Number(x).toPrecision(10)); }
function noProfit() {
  return { secUsd: 0, mintUsd: null, profit: null, athPrice: null, athProfit: null, multiplier: null,
           currentMultiplier: null, isSold: false, isFree: false };
}

function computeItemProfit(item, bnbPriceUsd = bnbPrice) {
  // The server's figures (monitor.update_post_mint_profit_metrics on the same record), when it sent them
  const sm = serverMetrics(item);
  if (sm && sm.profit && typeof sm.profit === 'object') {
    const sp = sm.profit;
    const n = v => (v === null || v === undefined) ? null : v;
    return {
      secUsd: Number(sp.sec_usd || 0),
      mintUsd: n(sp.mint_usd),
      profit: n(sp.profit),
      athPrice: n(sp.ath_price_usd),
      athProfit: n(sp.ath_profit_usd),
      multiplier: n(sp.ath_multiplier),
      currentMultiplier: n(sp.current_multiplier),
      isSold: Boolean(sp.is_sold),
      isFree: Boolean(sp.is_free)
    };
  }
  if (isExcludedMajorAsset(item)) return noProfit();

  const flUsd = numOrZero(item.floor_price_usd);
  const flBnb = numOrZero(item.floor_price_bnb);
  const tkUsd = numOrZero(item.token_price_usd || item.market_price_usd);
  const priceFromPair = !(flUsd > 0 || flBnb > 0);
  const secUsd = flUsd > 0 ? flUsd : (flBnb > 0 ? flBnb * bnbPriceUsd : tkUsd);

  // Micro-pool / scam price distortion guard: reject astronomical prices (> $1000) or micro-wei supplies
  const tmVal = numOrZero(item.total_minted);
  const lpVal = numOrZero(item.lp_usd);
  if (secUsd > 1000.0 || (tmVal > 0 && tmVal < 1e-6) || (lpVal > 0 && lpVal < 5.0 && secUsd > 5.0)) {
    return noProfit();
  }

  const pStr = String(item.mint_price_str || '').toUpperCase();
  const vWei = Math.trunc(numOrZero(item.mint_value_wei));
  const verifiedFree = Boolean(item.has_free_mint) || pStr.includes('FREE');
  const paidUsd = vWei > 0 ? (vWei / 1e18) * bnbPriceUsd : numOrZero(item.mint_price_bnb) * bnbPriceUsd;
  const isFree = verifiedFree;
  const costKnown = !pStr.includes('DEX') && (verifiedFree || paidUsd > 0);
  const mintUsd = costKnown ? (isFree ? 0 : paidUsd) : null;

  let rawMs = item.max_supply;
  if (rawMs !== null && rawMs !== undefined && (Number(rawMs) >= 1e20 || !isFinite(Number(rawMs)))) {
    rawMs = null;
  }
  const ms = Number(rawMs || 0);
  const tm = Number(item.total_minted || 0);
  const isSold = Boolean(ms > 0 && tm && tm >= ms);

  let profit = null;
  let multiplier = null;
  if (costKnown && secUsd > 0) {
    profit = secUsd - mintUsd;
    multiplier = mintUsd > 0 ? round2(secUsd / mintUsd) : null;
  }

  // --- ATH (high-water mark) ---
  const lpOk = !priceFromPair || lpVal >= ATH_MIN_LP_USD;
  let prevAth = numOrZero(item.ath_price_usd);
  if (prevAth > 0 && secUsd > 0 && priceFromPair && lpVal < ATH_MIN_LP_USD && prevAth > ATH_RESET_FACTOR * secUsd) {
    // a poisoned ATH (thin pool spike, stale seed): drop it, it is re-learned from a trustworthy price
    item.ath_price_usd = null;
    item.ath_profit_usd = null;
    item.ath_multiplier = null;
    prevAth = 0;
  }
  if (secUsd > 0 && lpOk && secUsd > prevAth) {
    item.ath_price_usd = sig10(secUsd);
  }
  const athPrice = numOrZero(item.ath_price_usd);

  if (costKnown && profit !== null && lpOk) {
    const prevP = item.ath_profit_usd;
    item.ath_profit_usd = sig10((prevP === null || prevP === undefined) ? profit : Math.max(Number(prevP), profit));
  } else if (!costKnown) {
    item.ath_profit_usd = null;
  }
  if (costKnown && !isFree && mintUsd > 0 && athPrice > 0) {
    const m = round2(athPrice / mintUsd);
    const prevM = item.ath_multiplier;
    item.ath_multiplier = (prevM === null || prevM === undefined) ? m : Math.max(Number(prevM), m);
  } else {
    item.ath_multiplier = null;
  }

  item.current_profit_usd = profit !== null ? sig10(profit) : null;
  item.current_multiplier = multiplier;
  item.mint_cost_usd = mintUsd !== null ? Math.round(mintUsd * 10000) / 10000 : null;
  item.secondary_price_usd = secUsd > 0 ? sig10(secUsd) : null;

  return {
    secUsd,
    mintUsd,
    profit,
    athPrice: item.ath_price_usd === undefined ? null : item.ath_price_usd,
    athProfit: item.ath_profit_usd === undefined ? null : item.ath_profit_usd,
    multiplier: item.ath_multiplier === undefined ? null : item.ath_multiplier,
    currentMultiplier: multiplier,
    isSold,
    isFree
  };
}

// Persistent Memory for Top Post-Mint Gainers & Hall of Fame Markets
// Eliminates full 2k+ project scans; incrementally updates and saves top winners from incoming 150-drop batches.
const SAVED_TOP_MARKETS_KEY = 'agent_id_saved_top_markets_v3';  // v2 held USD values computed with a fallback BNB price
let savedTopGainers = [];

// There is no hard-coded list of "historical winners" any more: its profits, free-mint flags and holder
// counts were invented. Kept as a function returning [] so existing fallbacks degrade to an empty state.
function getBaselineTopGainers() {
  return [];
}

function isValidTopMarketItem(item) {
  if (!item) return false;
  if (isExcludedMajorAsset(item)) {
    return false;
  }
  const pStr = String(item.mint_price_str || (item.item && item.item.mint_price_str) || '').toUpperCase();
  if (pStr.includes('DEX TRADED')) return false;

  const hasFree = Boolean(item.has_free_mint || (item.item && item.item.has_free_mint));
  const hasCalldata = Boolean(item.mint_calldata || item.has_mint_calldata
    || (item.item && (item.item.mint_calldata || item.item.has_mint_calldata)));
  if (pStr.includes('TBD') && !hasFree && !hasCalldata) return false;

  const flUsd = Number(item.floor_price_usd || (item.item && item.item.floor_price_usd) || 0);
  const tkUsd = Number(item.token_price_usd || item.market_price_usd || item.secUsd || (item.item && item.item.token_price_usd) || 0);
  const secUsd = flUsd > 0 ? flUsd : tkUsd;

  if (secUsd > 1000.0) return false;

  const tm = Number(item.total_minted || (item.item && item.item.total_minted) || 0);
  if (tm > 0 && tm < 1e-6) return false;

  const lp = Number(item.lp_usd || (item.item && item.item.lp_usd) || 0);
  if (lp > 0 && lp < 5.0 && secUsd > 5.0) return false;

  return true;
}

function loadSavedTopGainers() {
  try {
    if (typeof localStorage !== 'undefined') {
      // Clean up legacy poisoned cache keys
      localStorage.removeItem('agent_id_saved_top_markets');
      localStorage.removeItem('agent_id_saved_top_markets_v2');
      localStorage.removeItem('saved_top_markets_v1');

      const stored = localStorage.getItem(SAVED_TOP_MARKETS_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed) && parsed.length > 0) {
          savedTopGainers = parsed.filter(g => isValidTopMarketItem(g.item || g));
          saveSavedTopGainers(); // Immediately persist sanitized list to purge corrupted entries
          if (savedTopGainers.length > 0) return;
        }
      }
    }
  } catch (e) {
    console.warn('Error reading saved top gainers from localStorage:', e);
  }

  // Nothing stored yet: start empty. The leaderboard fills from the API and from live data; there is no
  // hard-coded list of historical winners (its profits, free-mint flags and holder counts were invented).
  savedTopGainers = [];
}

function saveSavedTopGainers() {
  if (!bnbPriceLive) return;  // USD values computed with a stale / missing BNB price are not cached
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(SAVED_TOP_MARKETS_KEY, JSON.stringify(savedTopGainers, (k, v) => (k === 'server_metrics' ? undefined : v)));  // a saved record outlives the figures the server computed for it
    }
  } catch (e) {}
}

// Where the leaderboard data came from. A seed (a frozen file) is always labelled with its date.
let topGainersSource = { seeded: false, asOf: null };

// 24h Pulse aggregates computed by the server over the FULL dataset (/api/stats -> pulse_24h). null until loaded.
let pulse24hServer = null;

function formatAsOfDate(asOf) {
  if (asOf === null || asOf === undefined || asOf === '') return '';
  const ms = typeof asOf === 'number' ? (asOf < 1e12 ? asOf * 1000 : asOf) : Date.parse(asOf);
  if (!isFinite(ms)) return String(asOf);
  return new Date(ms).toISOString().slice(0, 10);
}

// "Seed data, as of 2026-11-08" when the shown list is a frozen seed file, '' for live data
function topGainersSeedLabel() {
  if (!topGainersSource.seeded) return '';
  const d = formatAsOfDate(topGainersSource.asOf);
  return d ? `Seed data, as of ${d}` : 'Seed data (date unknown)';
}

async function syncSeededTopGainers() {
  try {
    const resApi = await fetchWithTimeout(`${BACKEND_URL}/api/top-gainers`, { cache: 'default' }, 4000);
    if (resApi.ok) {
      const dataApi = await resApi.json();
      const list = dataApi && (Array.isArray(dataApi.gainers) ? dataApi.gainers : (Array.isArray(dataApi.items) ? dataApi.items : null));
      if (list && list.length > 0) {
        topGainersSource = { seeded: Boolean(dataApi.seeded), asOf: dataApi.as_of || null };
        mergeTopGainersList(list);
        return;
      }
    }
  } catch (e) {
    // fall through to the static seed file
  }
  try {
    const res = await fetchWithTimeout(`seeded_top_gainers.json`, { cache: 'default' }, 4000);
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data) && data.length > 0) {
        topGainersSource = { seeded: true, asOf: res.headers && res.headers.get ? res.headers.get('last-modified') : null };
        mergeTopGainersList(data);
      }
    }
  } catch (e2) {}
}

function mergeTopGainersList(externalList) {
  if (!Array.isArray(externalList) || externalList.length === 0) return;
  let updated = false;

  externalList.forEach(item => {
    if (!isValidTopMarketItem(item.item || item)) return;
    const c = String(item.contract || (item.item && item.item.contract) || '').toLowerCase();
    if (!c) return;

    const existingIdx = savedTopGainers.findIndex(g => String(g.contract || '').toLowerCase() === c);
    const peakAth = Math.max(Number(item.athPrice || 0), Number(item.secUsd || 0));
    const peakProfit = Math.max(Number(item.athProfit || 0), Number(item.profit || 0));

    if (existingIdx >= 0) {
      const existing = savedTopGainers[existingIdx];
      if (peakAth > Number(existing.athPrice || 0)) {
        existing.athPrice = peakAth;
        existing.athProfit = Math.max(Number(existing.athProfit || 0), peakProfit);
        updated = true;
      }
    } else {
      savedTopGainers.push({
        item: item.item || item,
        name: item.name || 'Unknown',
        symbol: item.symbol || '',
        contract: item.contract || c,
        standard: item.standard || '',
        secUsd: Number(item.secUsd || 0),
        mintUsd: (item.mintUsd === null || item.mintUsd === undefined) ? null : Number(item.mintUsd),
        profit: Number(item.profit || 0),
        athPrice: peakAth,
        athProfit: peakProfit,
        multiplier: Number(item.multiplier || item.ath_multiplier || (item.item && item.item.ath_multiplier) || 0),
        isSold: Boolean(item.isSold),
        isFree: Boolean(item.isFree),
        holders: Number(item.holders || 0)
      });
      updated = true;
    }
  });

  if (updated) {
    savedTopGainers.sort((a, b) => Math.max(b.athProfit, b.profit) - Math.max(a.athProfit, a.profit));
    if (savedTopGainers.length > 50) savedTopGainers = savedTopGainers.slice(0, 50);
    saveSavedTopGainers();
  }
}

// Incremental update: inspects incoming recent drops (e.g. 150 items) without scanning 2,000+ items
function updateSavedTopMarkets(incomingDrops) {
  if (!Array.isArray(incomingDrops) || incomingDrops.length === 0) return savedTopGainers;
  if (!hasUsableBnbPrice()) return savedTopGainers;  // profits need a BNB/USD price; none is invented
  markDropsDataChanged();

  let changed = false;

  incomingDrops.forEach(item => {
    if (!isValidTopMarketItem(item)) return;
    const c = String(item.contract || '').toLowerCase();
    if (!c) return;

    const pInfo = computeItemProfit(item, bnbPrice);
    const peakPrice = Math.max(Number(pInfo.athPrice || 0), Number(pInfo.secUsd || 0));
    const peakProfit = Math.max(Number(pInfo.athProfit || 0), Number(pInfo.profit || 0));

    if (peakPrice > 0 && peakProfit > 0) {
      const idx = savedTopGainers.findIndex(g => String(g.contract || '').toLowerCase() === c);
      if (idx >= 0) {
        const existing = savedTopGainers[idx];
        const oldAth = Math.max(existing.athProfit || 0, existing.profit || 0);
        if (peakProfit > oldAth || pInfo.secUsd !== existing.secUsd || pInfo.isSold !== existing.isSold) {
          existing.secUsd = pInfo.secUsd;
          existing.mintUsd = pInfo.mintUsd;
          existing.profit = pInfo.profit;
          existing.athPrice = Math.max(Number(existing.athPrice || 0), peakPrice);
          existing.athProfit = Math.max(Number(existing.athProfit || 0), peakProfit);
          existing.multiplier = pInfo.multiplier || existing.multiplier;
          existing.isSold = pInfo.isSold || existing.isSold;
          existing.isFree = pInfo.isFree || existing.isFree;
          existing.holders = Number(item.holders || existing.holders || 0);
          existing.item = { ...existing.item, ...item };
          if (!item.server_metrics) delete existing.item.server_metrics;   // never keep figures of an older copy
          changed = true;
        }
      } else {
        savedTopGainers.push({
          item,
          name: item.name || 'Unknown',
          symbol: item.symbol || '',
          contract: item.contract || c,
          standard: item.standard || '',
          secUsd: pInfo.secUsd,
          mintUsd: pInfo.mintUsd,
          profit: pInfo.profit,
          athPrice: peakPrice,
          athProfit: peakProfit,
          multiplier: pInfo.multiplier,
          isSold: pInfo.isSold,
          isFree: pInfo.isFree,
          holders: Number(item.holders || 0)
        });
        changed = true;
      }
    }
  });

  if (changed) {
    savedTopGainers.sort((a, b) => Math.max(b.athProfit, b.profit) - Math.max(a.athProfit, a.profit));
    if (savedTopGainers.length > 50) savedTopGainers = savedTopGainers.slice(0, 50);
    saveSavedTopGainers();
  }

  return savedTopGainers;
}

// =========================================================================
// Smart Money Intelligence & Win-Rate Tracking (Option 2)
// =========================================================================
const FOLLOWED_WALLETS_KEY = 'agent_id_followed_wallets';
let followedWallets = new Set();
let smartHuntersCache = [];
let currentSmartFilter = 'ALL';
let smartSearchKeyword = '';
let smartDisplayLimit = 100;

function updateFollowedFilterCount() {
  if (typeof document === 'undefined') return;
  const el = document.getElementById('followedFilterCount');
  if (el) el.textContent = followedWallets.size;
}

function loadFollowedWallets() {
  try {
    if (typeof localStorage !== 'undefined') {
      const stored = localStorage.getItem(FOLLOWED_WALLETS_KEY);
      if (stored) {
        const arr = JSON.parse(stored);
        if (Array.isArray(arr)) {
          followedWallets = new Set(arr.map(w => String(w).toLowerCase()));
        }
      }
    }
  } catch (e) {}
  updateFollowedFilterCount();
}

function saveFollowedWallets() {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(FOLLOWED_WALLETS_KEY, JSON.stringify([...followedWallets]));
    }
  } catch (e) {}
  updateFollowedFilterCount();
}

function toggleFollowWallet(walletName) {
  const clean = String(walletName || '').toLowerCase().trim();
  if (!clean) return false;
  const isNowFollowing = !followedWallets.has(clean);
  if (isNowFollowing) {
    followedWallets.add(clean);
    showToast('⭐ SMART WALLET FOLLOWED', `Tracking ${clean} · High-conviction alerts on new mints active`, '⭐');
  } else {
    followedWallets.delete(clean);
    showToast('UNFOLLOWED', `Removed ${clean} from your followed alpha hunters`, 'ℹ️');
  }
  saveFollowedWallets();
  renderTable();
  const smModal = document.getElementById('smartLeaderboardModal');
  if (smModal && (smModal.open || smModal.hasAttribute('open'))) {
    renderSmartLeaderboardRows();
  }
  return isNowFollowing;
}
if (typeof window !== 'undefined') window.toggleFollowWallet = toggleFollowWallet;

let hasLoadedSmartDrops = false;
let isLoadingSmartDrops = false;

// Appends drops that are not already in allDrops (matched by lowercase contract). Returns how many were added.
// Used by "Load Older Drops" so overlapping pages never produce duplicate rows.
function appendUniqueDrops(drops) {
  markDropsDataChanged();
  if (!Array.isArray(drops)) return 0;
  const present = new Set(allDrops.map(d => (d.contract || '').toLowerCase()));
  let added = 0;
  drops.forEach(p => {
    const c = (p.contract || '').toLowerCase();
    if (c) {
      knownContracts.add(c);
      if (isSniperReady(p)) knownSniperReady.add(c);
      if (present.has(c)) return;
      present.add(c);
    }
    allDrops.push(p);
    added++;
  });
  return added;
}

// Re-counts every filter pill / tab badge from allDrops (or the server's totals where it has them). These counts
// were only refreshed by renderTable(), so a merge of more drops (smart drops, the secondary list) left them at the
// count of whatever page was rendered last: FREE MINTS showed 109 instead of 1,290 depending on load timing.
function refreshFilterPillCounts() {
  updateAllFilterCounts();
}

// Every filter pill / tab count in one pass over allDrops (the individual update*Count functions each filter
// allDrops again). Counts the server already totals (sniper ready, NFT surge, proxy) are not recomputed locally.
function countDropsByFilter(drops = allDrops, stats = globalStats) {
  const needSniper = !(stats && stats.sniper_ready !== undefined);
  const needSurge = !(stats && stats.nft_surge !== undefined);
  const needProxy = !(stats && stats.proxy !== undefined);
  const counts = { favorites: 0, sniperReady: 0, freeMints: 0, nftSurge: 0, proxy: 0, launchpad: 0, secondary: 0 };
  for (const d of drops) {
    if (!d) continue;
    if (isFavorited(d.contract, d)) counts.favorites++;
    if (needSniper && isSniperReady(d)) counts.sniperReady++;
    if (isFreeMintableNow(d)) counts.freeMints++;
    if (needSurge && d.is_nft_surge) counts.nftSurge++;
    if (needProxy && d.is_proxy_clone) counts.proxy++;
    if (isLaunchpadDrop(d)) counts.launchpad++;
    if (isSecondaryMarketAsset(d)) counts.secondary++;
  }
  if (!needSniper) counts.sniperReady = stats.sniper_ready;
  if (!needSurge) counts.nftSurge = stats.nft_surge;
  if (!needProxy) counts.proxy = stats.proxy;
  // Secondary = pairs actually loaded (what the table can show), not the server's total: a 2,809 badge over an
  // empty table read as broken. The server total stays in globalStats.secondary for the overview stats.
  return counts;
}

function updateAllFilterCounts() {
  if (typeof document === 'undefined') return;
  const counts = countDropsByFilter();
  const setText = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  setText('favCount', counts.favorites);
  setText('sniperReadyCount', counts.sniperReady);
  setText('freeMintsCount', counts.freeMints);
  setText('nftSurgeCount', counts.nftSurge);
  setText('proxyCount', counts.proxy);
  setText('launchpadTabCount', counts.launchpad);
  setText('launchpadCount', counts.launchpad);
  setText('secondaryTabCount', counts.secondary);
  if (typeof syncMobileTabSelector === 'function') syncMobileTabSelector();
  updateFollowedFilterCount();
}

// renderTable() refreshes the counts and the 24h pulse card only when one of their inputs changed since the last
// time: in-place drop changes (dropsDataVersion), a reassigned or resized allDrops / top gainers list, new server
// stats, favourites, followed wallets, wallet balances, the latest block or the BNB price.
let lastDataDerivedInputs = null;
function dataDerivedInputsChanged() {
  const inputs = [
    dropsDataVersion, allDrops, allDrops.length, savedTopGainers, savedTopGainers.length, globalStats,
    favorites, favorites.size, followedWallets, followedWallets.size, userOwnedContracts, userOwnedContracts.size,
    connectedWallet, latestKnownBlock, pulse24hServer, bnbPrice, bnbPriceLive,
  ];
  const prev = lastDataDerivedInputs;
  lastDataDerivedInputs = inputs;
  return !prev || inputs.some((v, i) => v !== prev[i]);
}
let lastRenderedTab = null;

function mergeDropsIntoAllDrops(newDrops) {
  if (!Array.isArray(newDrops) || newDrops.length === 0) return 0;
  markDropsDataChanged();
  let added = 0;
  newDrops.forEach(drop => {
    const c = (drop.contract || '').toLowerCase();
    if (!c) return;
    if (!knownContracts.has(c)) {
      knownContracts.add(c);
      if (isSniperReady(drop)) knownSniperReady.add(c);
      allDrops.push(drop);
      added++;
    } else {
      const existing = allDrops.find(d => (d.contract || '').toLowerCase() === c);
      if (existing) {
        if ((!existing.smart_wallets || existing.smart_wallets.length === 0) && drop.smart_wallets && drop.smart_wallets.length > 0) {
          existing.smart_wallets = drop.smart_wallets;
        }
        if ((!existing.smart_minters || existing.smart_minters.length === 0) && drop.smart_minters && drop.smart_minters.length > 0) {
          existing.smart_minters = drop.smart_minters;
        }
      }
    }
  });

  if (added > 0) {
    allDrops.sort((a, b) => Number(b.block || 0) - Number(a.block || 0));
    const sEl = document.getElementById('smartCount');
    if (sEl) {
      const count = allDrops.filter(d => (d.smart_wallets && d.smart_wallets.length > 0) || (d.smart_minters && d.smart_minters.length > 0)).length;
      sEl.textContent = count > 0 ? count : (globalStats ? globalStats.smart : 0);
    }
    const rEl = document.getElementById('radarCount');
    if (rEl) {
      rEl.textContent = Math.max(allDrops.length, globalStats ? globalStats.total : 0);
    }
    refreshFilterPillCounts();
  }
  return added;
}

// The seeded files are static snapshots, so their prices and market caps go stale. Overlay the backend's
// latest market numbers on every drop that came from them (incl. tokens /api/feed never returns).
const MARKET_OVERLAY_FIELDS = ['token_price_usd', 'market_cap', 'fdv', 'lp_usd', 'volume_24h', 'price_change_24h', 'buys_24h', 'sells_24h', 'buys_1h'];

function applyMarketOverlay(drops, market) {
  markDropsDataChanged();
  let updated = 0;
  // /api/market-data lists the contracts whose live record was cleared as stale in `_stale` (a short array of addresses)
  // and, for the others, only the fields that have a value.
  const staleSet = new Set((Array.isArray(market._stale) ? market._stale : []).map(c => String(c).toLowerCase()));
  drops.forEach(d => {
    const c = (d.contract || '').toLowerCase();
    const m = market[c] || (staleSet.has(c) ? {} : null);
    if (!m) return;
    let changed = false;
    // A record the worker cleared as stale: its numbers are unknown now, so the seeded ones go too.
    const stale = Boolean(m.market_stale) || staleSet.has(c);
    MARKET_OVERLAY_FIELDS.forEach(k => {
      const v = m[k] === undefined ? null : m[k];
      if ((stale || v !== null) && d[k] !== v) {
        d[k] = v;
        changed = true;
      }
    });
    ['market_stale', 'market_updated_at', 'market_source'].forEach(k => {
      if (m[k] !== undefined && m[k] !== null && d[k] !== m[k]) {
        d[k] = m[k];
        changed = true;
      }
    });
    if (staleSet.has(c) && d.market_stale !== true) {   // the table shows "⚠ stale" and the age of old data
      d.market_stale = true;
      changed = true;
    }
    if (changed) {
      updated++;
      delete d.server_metrics;   // computed from the numbers just replaced; the browser calculates it from now on
    }
  });
  return updated;
}

async function refreshSeededMarketData(seededDrops) {
  try {
    const res = await apiFetch('/api/market-data');
    if (!res.ok) return 0;
    const market = await res.json();
    const targets = allDrops.filter(d => seededDrops.has(d));
    return applyMarketOverlay(targets, market);
  } catch (e) {
    return 0; // keep the seeded numbers
  }
}

async function loadSmartDrops() {
  if (hasLoadedSmartDrops) return;
  if (isLoadingSmartDrops) {
    while (isLoadingSmartDrops) {
      await new Promise(r => setTimeout(r, 40));
    }
    return;
  }
  isLoadingSmartDrops = true;
  try {
    let res;
    try {
      res = await fetchWithTimeout(`seeded_smart_drops.json`, { cache: 'default' }, 4000);
      if (!res.ok) throw new Error('Local seeded_smart_drops.json failed');
    } catch (e1) {
      try {
        res = await fetchWithTimeout(`${BACKEND_URL}/api/smart-drops`, { cache: 'default' }, 6000);
        if (!res.ok) throw new Error('Render smart-drops failed');
      } catch (e2) {
        return; // Guard against falling back to massive 12.4 MB history.json
      }
    }

    if (res && res.ok) {
      const body = await res.json();
      // the static seed file is a bare array; /api/smart-drops answers {seeded, as_of, items} (?shape=array is the old shape)
      const drops = Array.isArray(body) ? body : (body && Array.isArray(body.items) ? body.items : null);
      if (Array.isArray(drops) && drops.length > 0) {
        mergeDropsIntoAllDrops(drops);
        const refreshed = await refreshSeededMarketData(new Set(drops));
        if (refreshed > 0 && typeof updateDailyPulseUI === 'function') updateDailyPulseUI();
        hasLoadedSmartDrops = true;
      }
    }
  } catch (err) {
    console.warn('Smart drops load fallback:', err);
  } finally {
    isLoadingSmartDrops = false;
  }
}

async function viewWalletActivity(walletName) {
  const clean = String(walletName || '').trim();
  if (!clean) return;

  const smModal = document.getElementById('smartLeaderboardModal');
  if (smModal) {
    if (smModal.close) smModal.close();
    else smModal.removeAttribute('open');
  }

  const inspModal = document.getElementById('inspectModal');
  if (inspModal) {
    if (inspModal.close) inspModal.close();
    else inspModal.removeAttribute('open');
  }

  // Set search query and update input field immediately
  searchQuery = clean;
  const sInput = document.getElementById('searchInput');
  if (sInput) sInput.value = clean;
  const clearBtn = document.getElementById('searchClearBtn');
  if (clearBtn) clearBtn.style.display = clean ? 'inline-flex' : 'none';

  // Set tab to Live Radar and filter to ALL so drops are not hidden by other filters
  const tabRadar = document.getElementById('tabRadar');
  if (tabRadar && !tabRadar.classList.contains('active')) tabRadar.click();

  if (typeof document !== 'undefined' && document.querySelectorAll) {
    document.querySelectorAll('.pill').forEach(b => b.classList.remove('active'));
    const pillAll = document.querySelector('.pill[data-filter="ALL"]');
    if (pillAll) pillAll.classList.add('active');
  }
  activeFilter = 'ALL';

  // Ensure all historical smart drops are loaded
  if (!hasLoadedSmartDrops) {
    await loadSmartDrops();
  }

  renderTable();
  window.scrollTo({ top: 0, behavior: 'smooth' });
  const count = getFilteredDrops().length;
  showToast('⭐ FILTERED BY SMART HUNTER', `Showing ${count} drop(s) minted by ${clean}`, '🔍');
}
if (typeof window !== 'undefined') {
  window.loadSmartDrops = loadSmartDrops;
  window.viewWalletActivity = viewWalletActivity;
}

function onSmartSearchInput(val) {
  smartSearchKeyword = String(val || '').trim().toLowerCase();
  smartDisplayLimit = 100;
  renderSmartLeaderboardRows();
}
if (typeof window !== 'undefined') window.onSmartSearchInput = onSmartSearchInput;

function loadMoreHunters() {
  smartDisplayLimit += 100;
  renderSmartLeaderboardRows();
}
if (typeof window !== 'undefined') window.loadMoreHunters = loadMoreHunters;

function getBaselineSmartHunters() {
  return [
    { name: "adadcarry.ai", mints: 23, wins: 11, winRate: 47.8, topPicks: ["AGORA", "GBOT", "TSLAB"] },
    { name: "shdjjdjd.ai", mints: 12, wins: 9, winRate: 75.0, topPicks: ["BNBAGENT", "HASH", "PULL"] },
    { name: "qifeiba.ai", mints: 14, wins: 9, winRate: 64.3, topPicks: ["HASH", "MAO", "FIST"] },
    { name: "a9julebu.ai", mints: 14, wins: 8, winRate: 57.1, topPicks: ["RUNE", "WARS", "HASH"] },
    { name: "ysjsjjs.ai", mints: 13, wins: 7, winRate: 53.8, topPicks: ["SAFU", "BNBH", "PULL"] },
    { name: "602.ai", mints: 14, wins: 7, winRate: 50.0, topPicks: ["AGC", "YYDS", "MSN"] },
    { name: "zhou4399.ai", mints: 14, wins: 7, winRate: 50.0, topPicks: ["Burn", "AGC", "屎币Agent"] },
    { name: "lucky2027.ai", mints: 14, wins: 7, winRate: 50.0, topPicks: ["AGC", "GENESIS", "JAGENT"] },
    { name: "rajasawit.ai", mints: 7, wins: 7, winRate: 100.0, topPicks: ["SPCXB", "SOXSB", "MRNAB"] },
    { name: "pingz.ai", mints: 16, wins: 7, winRate: 43.8, topPicks: ["BSCJS", "AGC", "MSN"] }
  ];
}

async function syncSeededSmartHunters() {
  try {
    const res = await fetchWithTimeout(`seeded_smart_hunters.json`, { cache: 'default' }, 4000);
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data) && data.length > 0) {
        smartHuntersCache = data;
        return;
      }
    }
  } catch (e) {
    try {
      const resApi = await fetchWithTimeout(`${BACKEND_URL}/api/smart-hunters`, { cache: 'default' }, 4000);
      if (resApi.ok) {
        const dataApi = await resApi.json();
        const apiHunters = dataApi && (Array.isArray(dataApi.items) ? dataApi.items : dataApi.hunters);
        if (Array.isArray(apiHunters) && apiHunters.length > 0) {
          smartHuntersCache = apiHunters;
          return;
        }
      }
    } catch (e2) {}
  }
  if (!smartHuntersCache || smartHuntersCache.length === 0) {
    smartHuntersCache = getBaselineSmartHunters();
  }
}

function computeSmartWalletLeaderboard(drops = allDrops) {
  const map = new Map();

  // Populate from pre-calculated seed list (2,116 hunters across full history)
  const base = (smartHuntersCache && smartHuntersCache.length > 0) ? smartHuntersCache : getBaselineSmartHunters();
  base.forEach(h => {
    map.set(h.name.toLowerCase(), {
      name: h.name,
      mints: Number(h.mints || 0),
      wins: Number(h.wins || 0),
      winRate: Number(h.winRate || 0),
      topPicks: [...(h.topPicks || [])]
    });
  });

  // Incrementally register newly seen wallets from live drops if not already in seed
  drops.forEach(d => {
    const sw = Array.isArray(d.smart_wallets) && d.smart_wallets.length > 0
      ? d.smart_wallets
      : (Array.isArray(d.smart_minters) && d.smart_minters.length > 0 ? d.smart_minters : []);
    
    if (sw.length === 0) return;

    const pInfo = computeItemProfit(d, bnbPrice);
    const isWin = (pInfo.secUsd > 0 && pInfo.athProfit > 0) || Number(d.lp_usd || 0) > 0 || (d.sniper_badge && d.sniper_badge.includes('DEX'));
    const sym = d.symbol || d.name || 'TOKEN';

    sw.forEach(w => {
      const clean = String(w).toLowerCase().trim();
      if (!clean) return;
      if (!map.has(clean)) {
        map.set(clean, {
          name: w,
          mints: 1,
          wins: isWin ? 1 : 0,
          winRate: isWin ? 100 : 0,
          topPicks: isWin ? [sym] : []
        });
      }
    });
  });

  const list = [...map.values()];
  list.forEach(h => {
    h.isFollowed = followedWallets.has(h.name.toLowerCase());
  });

  // Sort by wins descending, then winRate descending, then mints descending
  list.sort((a, b) => (b.wins - a.wins) || (b.winRate - a.winRate) || (b.mints - a.mints));
  return list;
}

function renderSmartLeaderboardRows() {
  const tbody = document.getElementById('smartLeaderboardTbody');
  const summaryEl = document.getElementById('smartSummaryPill');
  if (!tbody) return;

  const allHunters = computeSmartWalletLeaderboard(allDrops);
  let list = allHunters;
  if (currentSmartFilter === 'HIGH_WIN') {
    list = allHunters.filter(h => h.winRate >= 50.0);
  } else if (currentSmartFilter === 'FOLLOWED') {
    list = allHunters.filter(h => h.isFollowed);
  }

  if (smartSearchKeyword) {
    list = list.filter(h => h.name.toLowerCase().includes(smartSearchKeyword));
  }

  const followedCount = allHunters.filter(h => h.isFollowed).length;
  if (summaryEl) {
    summaryEl.textContent = `🎯 Tracking ${allHunters.length.toLocaleString()} Smart Hunters │ ${followedCount} Followed ${smartSearchKeyword ? `│ ${list.length} matches` : ''}`;
  }

  if (list.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding: 28px; color: var(--text-dim); font-size: 13px;">${
      currentSmartFilter === 'FOLLOWED'
        ? 'No smart alpha hunters followed yet. Click "⭐ Follow" next to any wallet to track their moves here.'
        : (smartSearchKeyword ? `No smart hunters found matching "${escapeHtml(smartSearchKeyword)}"` : 'No smart hunters match current filter.')
    }</td></tr>`;
    return;
  }

  const slice = list.slice(0, smartDisplayLimit);
  let rowsHtml = '';

  slice.forEach((h, idx) => {
    const rankNum = idx + 1;
    let rankBadge = `<span class="hof-rank" style="color: var(--text-dim);">#${rankNum}</span>`;
    if (rankNum === 1 && !smartSearchKeyword && currentSmartFilter === 'ALL') rankBadge = `<span class="hof-rank hof-rank-1">🥇 #1</span>`;
    else if (rankNum === 2 && !smartSearchKeyword && currentSmartFilter === 'ALL') rankBadge = `<span class="hof-rank hof-rank-2">🥈 #2</span>`;
    else if (rankNum === 3 && !smartSearchKeyword && currentSmartFilter === 'ALL') rankBadge = `<span class="hof-rank hof-rank-3">🥉 #3</span>`;

    const isF = followedWallets.has(h.name.toLowerCase());
    const followBtnClass = isF ? 'btn-follow-wallet following' : 'btn-follow-wallet';
    const followBtnText = isF ? '★ Following' : '⭐ Follow';

    let winRateClass = 'badge-win-low';
    if (h.winRate >= 60) winRateClass = 'badge-win-high';
    else if (h.winRate >= 40) winRateClass = 'badge-win-med';

    const picksHtml = (h.topPicks || []).map(p => `<span class="pick-pill">${escapeHtml(p)}</span>`).join('') || '<span class="dim">—</span>';

    rowsHtml += `
      <tr>
        <td>${rankBadge}</td>
        <td>
          <div style="font-weight: 700; color: var(--color-cyan); display: flex; align-items: center; gap: 4px;">
            <a href="javascript:void(0)" onclick="viewWalletActivity('${escapeHtml(h.name)}')" style="color: inherit; text-decoration: none;" title="Click to view all drops minted by ${escapeHtml(h.name)}">
              ${isF ? '<span style="color: var(--color-yellow);">⭐</span> ' : ''}${escapeHtml(h.name)}
            </a>
          </div>
        </td>
        <td><strong>${h.mints}</strong> <span class="dim">mints</span></td>
        <td><strong style="color: var(--color-green);">${h.wins}</strong> <span class="dim">wins</span></td>
        <td>
          <span class="badge-win-rate ${winRateClass}">${h.winRate.toFixed(1)}%</span>
        </td>
        <td>${picksHtml}</td>
        <td>
          <div style="display: flex; gap: 6px; align-items: center;">
            <button class="${followBtnClass}" onclick="toggleFollowWallet('${escapeHtml(h.name)}')">${followBtnText}</button>
            <button class="btn-view-activity" onclick="viewWalletActivity('${escapeHtml(h.name)}')" title="View all drops minted by ${escapeHtml(h.name)}">👁️ Activity</button>
          </div>
        </td>
      </tr>
    `;
  });

  if (list.length > smartDisplayLimit) {
    rowsHtml += `
      <tr>
        <td colspan="7" style="text-align: center; padding: 14px; background: rgba(0, 240, 255, 0.02);">
          <button class="btn-load-more-hunters" onclick="loadMoreHunters()">
            ⬇️ Load More Hunters (Showing ${slice.length} of ${list.length.toLocaleString()})
          </button>
        </td>
      </tr>
    `;
  }

  tbody.innerHTML = rowsHtml;
}

function openSmartLeaderboardModal(filterMode = 'ALL') {
  currentSmartFilter = filterMode;
  smartDisplayLimit = 100;
  const modal = document.getElementById('smartLeaderboardModal');
  const content = document.getElementById('smartLeaderboardModalContent');
  if (!modal || !content) return;

  if (typeof window !== 'undefined' && window.va) {
    window.va('event', { name: 'view_smart_leaderboard', data: { filter: filterMode } });
  }

  const allHunters = computeSmartWalletLeaderboard(allDrops);
  const highWinCount = allHunters.filter(h => h.winRate >= 50).length;
  const followedCount = allHunters.filter(h => h.isFollowed).length;

  content.innerHTML = `
    <div class="smart-header">
      <div class="smart-title-group">
        <h2>🧠 SMART HUNTER LEADERBOARD · ALPHA WIN-RATES</h2>
        <p class="smart-subtitle">Historical track record of all ${allHunters.length.toLocaleString()}+ tracked .ai agent wallets across BSC Agent ID mints. Ranked by successful graduation to DEX, secondary volume, and peak multipliers.</p>
      </div>
      <button class="modal-close-btn" id="closeSmartLeaderboardBtn" onclick="document.getElementById('smartLeaderboardModal').close()" aria-label="Close modal">✕ CLOSE</button>
    </div>

    <div class="smart-controls">
      <div class="smart-filters">
        <button class="smart-filter-btn ${filterMode === 'ALL' ? 'active' : ''}" onclick="openSmartLeaderboardModal('ALL')">🌟 All Hunters (${allHunters.length.toLocaleString()})</button>
        <button class="smart-filter-btn ${filterMode === 'HIGH_WIN' ? 'active' : ''}" onclick="openSmartLeaderboardModal('HIGH_WIN')">🔥 High Win-Rate (≥50%) (${highWinCount})</button>
        <button class="smart-filter-btn ${filterMode === 'FOLLOWED' ? 'active' : ''}" onclick="openSmartLeaderboardModal('FOLLOWED')">⭐ Followed Only (${followedCount})</button>
      </div>
      <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
        <input type="text" id="smartLeaderboardSearch" class="smart-search-input" placeholder="🔍 Search 2,100+ hunters..." value="${escapeHtml(smartSearchKeyword)}" oninput="onSmartSearchInput(this.value)" />
        <div class="smart-summary-pill" id="smartSummaryPill">
          🎯 Tracking ${allHunters.length.toLocaleString()} Smart Hunters │ ${followedCount} Followed
        </div>
      </div>
    </div>

    <div class="smart-table-wrap">
      <table class="smart-table">
        <thead>
          <tr>
            <th style="width: 55px;">Rank</th>
            <th style="min-width: 140px;">Smart Wallet (.ai)</th>
            <th style="width: 95px;">Total Mints</th>
            <th style="width: 95px;">Winning Plays</th>
            <th style="width: 95px;">Win Rate %</th>
            <th>Top Winning Picks</th>
            <th style="width: 185px;">Actions / Activity</th>
          </tr>
        </thead>
        <tbody id="smartLeaderboardTbody">
        </tbody>
      </table>
    </div>
  `;

  renderSmartLeaderboardRows();

  if (typeof modal.showModal === 'function') modal.showModal();
  else modal.setAttribute('open', '');
}
if (typeof window !== 'undefined') window.openSmartLeaderboardModal = openSmartLeaderboardModal;

// =========================================================================
// Dedicated Wallet Leaderboard Tab (Feature 2)
// =========================================================================
let currentTabWalletFilter = 'ALL';
let tabWalletsSortMode = 'DEFAULT';
let tabWalletsSearchKeyword = '';
let tabWalletsDisplayLimit = 50;
let cachedHunters = null;
let cachedHuntersAllDropsLen = '';

function updateWalletsTabBadge() {
  if (typeof document === 'undefined') return;
  const countEl = document.getElementById('walletsTabCount');
  // Keyed on everything the leaderboard reads: in-place drop edits (dropsDataVersion), the drop count, and the hunter
  // seed list. Keying on allDrops.length alone left the badge on a stale count (2,149) while the table, which always
  // recomputes, showed the current one (2,655) once seeded_smart_hunters.json or merged smart wallets arrived.
  const huntersKey = `${dropsDataVersion}|${allDrops.length}|${smartHuntersCache ? smartHuntersCache.length : 0}`;
  if (!cachedHunters || cachedHuntersAllDropsLen !== huntersKey) {
    cachedHunters = computeSmartWalletLeaderboard(allDrops);
    cachedHuntersAllDropsLen = huntersKey;
  }
  const allHunters = cachedHunters;
  if (countEl) countEl.textContent = allHunters.length.toLocaleString();
  const summaryEl = document.getElementById('walletsTabSummaryPill');
  if (summaryEl) {
    const fCount = allHunters.filter(h => h.isFollowed).length;
    summaryEl.textContent = `🎯 Tracking ${allHunters.length.toLocaleString()} Hunters │ ⭐ ${fCount} Followed`;
  }
  const fTabCount = document.getElementById('walletsTabFollowedCount');
  if (fTabCount) {
    fTabCount.textContent = allHunters.filter(h => h.isFollowed).length;
  }
}

function updateWalletsTabHeaderUI() {
  if (typeof document === 'undefined') return;

  const sortBtnWin = document.getElementById('tabWalletSortWinRate');
  const sortBtnWins = document.getElementById('tabWalletSortWins');
  const sortBtnPicks = document.getElementById('tabWalletSortPicks');
  const filterBtnAll = document.getElementById('tabWalletFilterAll');
  const filterBtnHigh = document.getElementById('tabWalletFilterHighWin');
  const filterBtnFollowed = document.getElementById('tabWalletFilterFollowed');

  const indWin = document.getElementById('tabWalletSortIndicatorWinRate');
  const indWins = document.getElementById('tabWalletSortIndicatorWins');
  const indPicks = document.getElementById('tabWalletSortIndicatorPicks');

  if (typeof document.querySelectorAll === 'function') {
    const pills = document.querySelectorAll('.wallets-tab-filters .filter-pill');
    if (pills && pills.forEach) {
      pills.forEach(b => {
        if (b && b.classList) b.classList.remove('active');
      });
    }
  }

  if (indWin) indWin.textContent = '↕';
  if (indWins) indWins.textContent = '↕';
  if (indPicks) indPicks.textContent = '↕';

  if (tabWalletsSortMode === 'WIN_RATE_DESC' || tabWalletsSortMode === 'WIN_RATE_ASC') {
    if (sortBtnWin) sortBtnWin.classList.add('active');
    if (indWin) indWin.textContent = (tabWalletsSortMode === 'WIN_RATE_DESC') ? '▼' : '▲';
  } else if (tabWalletsSortMode === 'WINS_DESC' || tabWalletsSortMode === 'WINS_ASC') {
    if (sortBtnWins) sortBtnWins.classList.add('active');
    if (indWins) indWins.textContent = (tabWalletsSortMode === 'WINS_DESC') ? '▼' : '▲';
  } else if (tabWalletsSortMode === 'PICKS_DESC' || tabWalletsSortMode === 'PICKS_ASC') {
    if (sortBtnPicks) sortBtnPicks.classList.add('active');
    if (indPicks) indPicks.textContent = (tabWalletsSortMode === 'PICKS_DESC') ? '▼' : '▲';
  }

  if (currentTabWalletFilter === 'HIGH_WIN') {
    if (filterBtnHigh) filterBtnHigh.classList.add('active');
  } else if (currentTabWalletFilter === 'FOLLOWED') {
    if (filterBtnFollowed) filterBtnFollowed.classList.add('active');
  } else if (tabWalletsSortMode === 'DEFAULT') {
    if (filterBtnAll) filterBtnAll.classList.add('active');
  }

  // Update table headers
  const thRank = document.getElementById('thWalletRank');
  const thName = document.getElementById('thWalletName');
  const thWinRate = document.getElementById('thWalletWinRate');
  const thWins = document.getElementById('thWalletWinsMints');
  const thPicks = document.getElementById('thWalletTopPicks');

  const indThRank = document.getElementById('thSortWalletRank');
  const indThName = document.getElementById('thSortWalletName');
  const indThWin = document.getElementById('thSortWalletWinRate');
  const indThWins = document.getElementById('thSortWalletWinsMints');
  const indThPicks = document.getElementById('thSortWalletTopPicks');

  [thRank, thName, thWinRate, thWins, thPicks].forEach(th => {
    if (th && th.classList) th.classList.remove('active-sort');
  });

  if (indThRank) indThRank.textContent = (tabWalletsSortMode === 'DEFAULT') ? '▼' : '↕';
  if (indThName) indThName.textContent = '↕';
  if (indThWin) indThWin.textContent = '↕';
  if (indThWins) indThWins.textContent = '↕';
  if (indThPicks) indThPicks.textContent = '↕';

  if (tabWalletsSortMode === 'DEFAULT') {
    if (thRank) thRank.classList.add('active-sort');
  } else if (tabWalletsSortMode === 'WIN_RATE_DESC' || tabWalletsSortMode === 'WIN_RATE_ASC') {
    if (thWinRate) thWinRate.classList.add('active-sort');
    if (indThWin) indThWin.textContent = (tabWalletsSortMode === 'WIN_RATE_DESC') ? '▼' : '▲';
  } else if (tabWalletsSortMode === 'WINS_DESC' || tabWalletsSortMode === 'WINS_ASC') {
    if (thWins) thWins.classList.add('active-sort');
    if (indThWins) indThWins.textContent = (tabWalletsSortMode === 'WINS_DESC') ? '▼' : '▲';
  } else if (tabWalletsSortMode === 'PICKS_DESC' || tabWalletsSortMode === 'PICKS_ASC') {
    if (thPicks) thPicks.classList.add('active-sort');
    if (indThPicks) indThPicks.textContent = (tabWalletsSortMode === 'PICKS_DESC') ? '▼' : '▲';
  } else if (tabWalletsSortMode === 'NAME_ASC' || tabWalletsSortMode === 'NAME_DESC') {
    if (thName) thName.classList.add('active-sort');
    if (indThName) indThName.textContent = (tabWalletsSortMode === 'NAME_ASC') ? '▲' : '▼';
  }
}

function setWalletsTabSort(sortKey) {
  if (sortKey === 'WIN_RATE') {
    tabWalletsSortMode = (tabWalletsSortMode === 'WIN_RATE_DESC') ? 'WIN_RATE_ASC' : 'WIN_RATE_DESC';
  } else if (sortKey === 'WINS') {
    tabWalletsSortMode = (tabWalletsSortMode === 'WINS_DESC') ? 'WINS_ASC' : 'WINS_DESC';
  } else if (sortKey === 'PICKS') {
    tabWalletsSortMode = (tabWalletsSortMode === 'PICKS_DESC') ? 'PICKS_ASC' : 'PICKS_DESC';
  } else if (sortKey === 'NAME') {
    tabWalletsSortMode = (tabWalletsSortMode === 'NAME_ASC') ? 'NAME_DESC' : 'NAME_ASC';
  } else {
    tabWalletsSortMode = 'DEFAULT';
  }

  tabWalletsDisplayLimit = 50;
  updateWalletsTabHeaderUI();
  renderWalletsTabLeaderboard();
}
if (typeof window !== 'undefined') window.setWalletsTabSort = setWalletsTabSort;

function setWalletsTabFilter(filter) {
  currentTabWalletFilter = filter;
  tabWalletsDisplayLimit = 50;
  if (filter === 'ALL') {
    tabWalletsSortMode = 'DEFAULT';
  }
  updateWalletsTabHeaderUI();
  renderWalletsTabLeaderboard();
}
if (typeof window !== 'undefined') window.setWalletsTabFilter = setWalletsTabFilter;

function onWalletsTabSearch(val) {
  tabWalletsSearchKeyword = (val || '').trim().toLowerCase();
  tabWalletsDisplayLimit = 50;
  renderWalletsTabLeaderboard();
}
if (typeof window !== 'undefined') window.onWalletsTabSearch = onWalletsTabSearch;

function loadMoreTabWallets() {
  tabWalletsDisplayLimit += 50;
  renderWalletsTabLeaderboard();
}
if (typeof window !== 'undefined') window.loadMoreTabWallets = loadMoreTabWallets;

function renderWalletsTabLeaderboard() {
  if (typeof document === 'undefined') return;
  const tbody = document.getElementById('tabWalletsTbody');
  if (!tbody) return;

  const allHunters = computeSmartWalletLeaderboard(allDrops);
  let list = allHunters;

  if (currentTabWalletFilter === 'HIGH_WIN') {
    list = allHunters.filter(h => h.winRate >= 50.0);
  } else if (currentTabWalletFilter === 'FOLLOWED') {
    list = allHunters.filter(h => h.isFollowed);
  }

  if (tabWalletsSearchKeyword) {
    list = list.filter(h => h.name.toLowerCase().includes(tabWalletsSearchKeyword));
  }

  // Sort list according to tabWalletsSortMode
  list = [...list];
  if (tabWalletsSortMode === 'WIN_RATE_DESC') {
    list.sort((a, b) => (b.winRate - a.winRate) || (b.wins - a.wins) || (b.mints - a.mints));
  } else if (tabWalletsSortMode === 'WIN_RATE_ASC') {
    list.sort((a, b) => (a.winRate - b.winRate) || (a.wins - b.wins));
  } else if (tabWalletsSortMode === 'WINS_DESC') {
    list.sort((a, b) => (b.wins - a.wins) || (b.winRate - a.winRate) || (b.mints - a.mints));
  } else if (tabWalletsSortMode === 'WINS_ASC') {
    list.sort((a, b) => (a.wins - b.wins) || (a.winRate - b.winRate));
  } else if (tabWalletsSortMode === 'PICKS_DESC') {
    list.sort((a, b) => ((b.topPicks || []).length - (a.topPicks || []).length) || (b.wins - a.wins) || (b.winRate - a.winRate));
  } else if (tabWalletsSortMode === 'PICKS_ASC') {
    list.sort((a, b) => ((a.topPicks || []).length - (b.topPicks || []).length) || (a.wins - b.wins));
  } else if (tabWalletsSortMode === 'NAME_ASC') {
    list.sort((a, b) => a.name.localeCompare(b.name));
  } else if (tabWalletsSortMode === 'NAME_DESC') {
    list.sort((a, b) => b.name.localeCompare(a.name));
  } else {
    // DEFAULT: Top PnL rank
    list.sort((a, b) => (b.wins - a.wins) || (b.winRate - a.winRate) || (b.mints - a.mints));
  }

  const footerCountEl = document.getElementById('footerCount');
  if (footerCountEl) {
    footerCountEl.textContent = `Showing ${Math.min(list.length, tabWalletsDisplayLimit)} of ${allHunters.length} Smart Hunters`;
  }

  updateWalletsTabBadge();

  if (list.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align: center; padding: 36px; color: var(--text-dim); font-size: 13px;">
          ${currentTabWalletFilter === 'FOLLOWED'
            ? 'No smart alpha hunters followed yet. Click "⭐ Follow" next to any wallet to track them here!'
            : 'No matching Agent ID smart wallets found.'}
        </td>
      </tr>
    `;
    const lm = document.getElementById('tabWalletsLoadMoreWrap');
    if (lm) lm.style.display = 'none';
    return;
  }

  const visible = list.slice(0, tabWalletsDisplayLimit);
  let html = '';

  visible.forEach((h, idx) => {
    const isF = followedWallets.has(h.name.toLowerCase());
    const rank = idx + 1;
    let rankBadge = `<span style="color: var(--text-dim); font-weight:700;">#${rank}</span>`;
    if (rank === 1) rankBadge = '🥇';
    else if (rank === 2) rankBadge = '🥈';
    else if (rank === 3) rankBadge = '🥉';

    const winRateColor = h.winRate >= 50 ? 'var(--color-green)' : (h.winRate >= 30 ? 'var(--color-yellow)' : 'var(--text-dim)');
    const topPicksHtml = (h.topPicks || []).slice(0, 3).map(p => {
      const pName = typeof p === 'string' ? p : (p.name || 'Drop');
      return `<span class="badge badge-dim" style="font-size: 10px; margin-right: 4px; padding: 2px 6px;">${escapeHtml(pName)}</span>`;
    }).join('') || '<span class="dim">Early Track</span>';

    html += `
      <tr>
        <td style="text-align: center; font-size: 13px;">${rankBadge}</td>
        <td>
          <div style="display: flex; align-items: center; gap: 8px;">
            <strong style="color: ${isF ? 'var(--color-yellow)' : 'var(--color-cyan)'}; font-size: 13px;">
              ${isF ? '⭐ ' : ''}${escapeHtml(h.name)}
            </strong>
            ${isF ? '<span class="badge-followed-star">FOLLOWED</span>' : ''}
          </div>
        </td>
        <td style="text-align: center;">
          <span style="font-weight: 700; color: ${winRateColor}; font-size: 13px;">${h.winRate.toFixed(1)}%</span>
        </td>
        <td style="text-align: center;">
          <strong>${h.wins}</strong> <span class="dim">/ ${h.mints}</span>
        </td>
        <td>
          <div style="display: flex; flex-wrap: wrap; gap: 4px;">${topPicksHtml}</div>
        </td>
        <td style="text-align: center;">
          <div style="display: flex; gap: 6px; justify-content: center;">
            <button class="${isF ? 'btn-follow-wallet following' : 'btn-follow-wallet'}" onclick="toggleFollowWallet('${escapeHtml(h.name)}'); renderWalletsTabLeaderboard();">
              ${isF ? '★ Following' : '⭐ Follow'}
            </button>
            <button class="btn-view-activity" onclick="viewWalletActivity('${escapeHtml(h.name)}')">
              🔍 Mints
            </button>
          </div>
        </td>
      </tr>
    `;
  });

  tbody.innerHTML = html;

  const loadMoreEl = document.getElementById('tabWalletsLoadMoreWrap');
  if (loadMoreEl) {
    loadMoreEl.style.display = list.length > tabWalletsDisplayLimit ? 'block' : 'none';
  }
}
if (typeof window !== 'undefined') window.renderWalletsTabLeaderboard = renderWalletsTabLeaderboard;

function toggleDexChart(contract, customUrl) {
  const wrap = document.getElementById('dexChartWrap');
  const label = document.getElementById('chartToggleLabel');
  const icon = document.getElementById('chartToggleIcon');
  if (!wrap) return;

  if (wrap.style.display === 'none') {
    wrap.style.display = 'block';
    const chartSrc = (customUrl && customUrl.includes('dexscreener.com'))
      ? `${customUrl}?embed=1&theme=dark&trades=0&info=0`
      : `https://dexscreener.com/bsc/${contract}?embed=1&theme=dark&trades=0&info=0`;
    wrap.innerHTML = `
      <iframe src="${chartSrc}" width="100%" height="340" style="border:0;" loading="lazy"></iframe>
    `;
    if (label) label.textContent = 'Hide Live Chart';
    if (icon) icon.textContent = '✕';
  } else {
    wrap.style.display = 'none';
    wrap.innerHTML = '';
    if (label) label.textContent = 'Show Live DEX Chart';
    if (icon) icon.textContent = '📊';
  }
}
if (typeof window !== 'undefined') window.toggleDexChart = toggleDexChart;

// How many momentum tokens the banner names, and how long one name may be before it is cut with an ellipsis.
const MOMENTUM_NAMES_SHOWN = 3;
const MOMENTUM_NAME_MAX_CHARS = 14;

// Short, never-empty label for a token in the stats banner: name, else symbol, else the shortened contract.
// Cuts by code point (Array.from) so a CJK / emoji name is never split mid-character.
function bannerTokenName(item) {
  const raw = String((item && (item.name || item.symbol)) || '').trim();
  if (raw) {
    const chars = Array.from(raw);
    return chars.length > MOMENTUM_NAME_MAX_CHARS ? `${chars.slice(0, MOMENTUM_NAME_MAX_CHARS - 1).join('')}…` : raw;
  }
  const contract = String((item && item.contract) || '');
  return contract.length > 10 ? `${contract.slice(0, 6)}…${contract.slice(-4)}` : (contract || 'Unnamed');
}

// Compute Macro Stats
function computeStats(drops) {
  let cappedTotal = 0;
  let soldOutCount = 0;
  let nearCapCount = 0;
  let warmCapCount = 0;
  const warmCapItems = [];   // the tokens behind warmCapCount, so the banner can name them

  drops.forEach(item => {
    let rawMs = item.max_supply;
    if (rawMs !== null && rawMs !== undefined && (Number(rawMs) >= 1e20 || !isFinite(Number(rawMs)))) {
      rawMs = null;
    }
    const ms = Number(rawMs || 0);
    const tm = Number(item.total_minted || 0);

    if (ms > 0) {
      cappedTotal++;
      const pct = (tm / ms) * 100;
      if (pct >= 99.9 || tm >= ms) {
        soldOutCount++;
      } else if (pct >= 80) {
        nearCapCount++;
      } else if (pct >= 50) {
        warmCapCount++;
        // Rounded for display but kept inside this bucket: 79.6% must not read "80%" while it is not counted as near cap.
        warmCapItems.push({ name: bannerTokenName(item), pct: Math.min(79, Math.round(pct)), exactPct: pct });
      }
    }
  });
  warmCapItems.sort((a, b) => (b.exactPct - a.exactPct) || a.name.localeCompare(b.name));

  // Incrementally update our saved top markets memory from incoming recent drops
  updateSavedTopMarkets(drops);

  const fillRatePct = cappedTotal > 0 ? (soldOutCount / cappedTotal) * 100 : 0;
  const allGainers = [...savedTopGainers]
    .filter(g => isValidTopMarketItem(g.item || g))
    .sort((a, b) => Math.max(b.athProfit, b.profit) - Math.max(a.athProfit, a.profit));
  const soldGainers = allGainers.filter(g => g.isSold);
  const majorGainers = allGainers.filter(g => Math.max(g.athProfit, g.profit) >= 0.001);

  const topGainer = soldGainers[0] || allGainers[0] || null;
  let runnerUp = null;
  if (topGainer) {
    runnerUp = allGainers.find(g => (g.contract || '').toLowerCase() !== (topGainer.contract || '').toLowerCase()) || null;
  }

  return {
    cappedTotal,
    soldOutCount,
    fillRatePct,
    nearCapCount,
    warmCapCount,
    warmCapItems,
    topGainer,
    runnerUp,
    allGainers,
    soldGainers,
    majorGainers
  };
}

const AGENT_ID_REGISTRY_ADDR = '0x60B9262456dE011F7d15a54CdD8c48bAaa8B022F';
const ERC721_TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef';
const ZERO_ADDRESS_TOPIC = '0x0000000000000000000000000000000000000000000000000000000000000000';

let currentAgentWalletsCount = 178001;
let agentWalletsAnchorBlock = 132757997;
let isSyncingWalletsOnChain = false;
let lastWalletChainSyncAt = 0;

function updateDigitalAiCounter(count = currentAgentWalletsCount) {
  const prevCount = currentAgentWalletsCount;
  if (typeof count === 'number' && !isNaN(count) && count > 0) {
    currentAgentWalletsCount = count;
  } else {
    count = currentAgentWalletsCount;
  }
  const formatted = count.toLocaleString('en-US');
  const screen = document.getElementById('digitalLcdScreen');
  if (screen) {
    screen.innerHTML = '';
    for (const char of formatted) {
      const span = document.createElement('span');
      if (char === ',') {
        span.className = 'dcb-comma';
        span.textContent = ',';
      } else {
        span.className = 'dcb-digit';
        span.textContent = char;
      }
      screen.appendChild(span);
    }
  }
  const hdrCount = document.getElementById('headerAiWalletsCount');
  if (hdrCount) {
    hdrCount.textContent = formatted;
    if (count > prevCount && hdrCount.style) {
      hdrCount.style.color = '#00ff88';
      hdrCount.style.textShadow = '0 0 12px rgba(0, 255, 136, 0.85)';
      setTimeout(() => {
        hdrCount.style.color = '#ffd600';
        hdrCount.style.textShadow = '0 0 6px rgba(255, 214, 0, 0.5)';
      }, 900);
    }
  }
  const tickerEl = document.getElementById('tickerAiWallets');
  if (tickerEl) tickerEl.textContent = `🤖 ${formatted} .ai`;
}

function applyAgentWalletsState(count, blockNum = 0) {
  const n = Number(count || 0);
  const b = Number(blockNum || 0);
  if (!n || isNaN(n) || n <= 0) return;

  if (b > 0) {
    if (b >= agentWalletsAnchorBlock) {
      agentWalletsAnchorBlock = b;
      const nextCount = (n >= currentAgentWalletsCount) ? n : currentAgentWalletsCount;
      updateDigitalAiCounter(nextCount);
    }
  } else if (n > currentAgentWalletsCount) {
    updateDigitalAiCounter(n);
  }
}

async function syncLiveAgentWalletsOnChain(currBlk) {
  if (typeof fetch === 'undefined' || !currBlk || currBlk <= agentWalletsAnchorBlock) return;
  if (typeof document !== 'undefined' && document.hidden) return;
  if (isSyncingWalletsOnChain) return;

  const now = Date.now();
  if (now - lastWalletChainSyncAt < 14000) return;

  isSyncingWalletsOnChain = true;
  lastWalletChainSyncAt = now;

  try {
    const startBlk = Math.max(agentWalletsAnchorBlock + 1, currBlk - 200000);
    const chunkSize = 40000;
    let totalNew = 0;
    let fBlk = startBlk;
    let allOk = true;

    while (fBlk <= currBlk) {
      const tBlk = Math.min(fBlk + chunkSize - 1, currBlk);
      const chunkLogs = await fetchRegistryMintLogs(fBlk, tBlk);

      if (chunkLogs === null) {
        allOk = false;
        break;
      }
      totalNew += chunkLogs.length;
      agentWalletsAnchorBlock = tBlk;
      fBlk = tBlk + 1;
    }

    if (allOk || totalNew > 0) {
      if (totalNew > 0) {
        updateDigitalAiCounter(currentAgentWalletsCount + totalNew);
      }
    }
  } finally {
    isSyncingWalletsOnChain = false;
  }
}

// The banner never shows sample text: every span below is rewritten on each render, and a span with nothing to say
// is cleared ('' hides it through the :empty rule in style.css) or shows the dash.
function setBannerText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = String(text);
  return el;
}

// Update DOM Header with Macro Stats
function renderStats(drops) {
  const stats = computeStats(drops);

  updateDigitalAiCounter(currentAgentWalletsCount);

  // Fill rate is a ratio: with no capped collections it is undefined, not "0.0% (0 / 0 Sold Out)".
  const hasCapped = stats.cappedTotal > 0;
  setBannerText('fillRatePct', hasCapped ? `${stats.fillRatePct.toFixed(1)}%` : '—');
  setBannerText('fillRateRatio', hasCapped ? `(${stats.soldOutCount} / ${stats.cappedTotal} Sold Out)` : '');
  setBannerText('nearCapCount', stats.nearCapCount);
  setBannerText('momentumCount', `${stats.warmCapCount} ACTIVE`);

  // Momentum names come from the same list as the count above: the top few tokens at 50-79% of their cap.
  const momentum = (stats.warmCapItems || []).slice(0, MOMENTUM_NAMES_SHOWN);
  const elMomNames = setBannerText('momentumNames',
    momentum.length ? `[${momentum.map(t => `${t.name} ${t.pct}%`).join(', ')}]` : '—');
  if (elMomNames) {
    // The full list as a tooltip: the banner cell cuts long names with an ellipsis.
    const all = (stats.warmCapItems || []).map(t => `${t.name} ${t.pct}%`).join(', ');
    if (all && typeof elMomNames.setAttribute === 'function') elMomNames.setAttribute('title', all);
    else if (typeof elMomNames.removeAttribute === 'function') elMomNames.removeAttribute('title');
  }

  const tg = stats.topGainer;
  if (tg) {
    const tgMint = tg.isFree || tg.mintUsd === 0 ? 'FREE' : formatCryptoPrice(tg.mintUsd);
    const tgAth = Math.max(tg.athPrice, tg.secUsd);
    const tgNet = Math.max(tg.athProfit, tg.profit);
    setBannerText('topGainerName', `${tg.name} (${tg.symbol})`);
    setBannerText('topGainerDetails', `Mint: ${tgMint} ➔ ATH: ${formatCryptoPrice(tgAth)}`);
    setBannerText('topGainerNet', `Net: ${formatCryptoPrice(tgNet, true)} / token`);
  } else {
    setBannerText('topGainerName', '—');
    setBannerText('topGainerDetails', '');
    setBannerText('topGainerNet', '');
  }

  const ru = stats.runnerUp;
  if (ru) {
    const ruAth = Math.max(ru.athPrice, ru.secUsd);
    setBannerText('runnerUpDetails', `│ 🥈 Runner-Up: ${ru.name} (${ru.symbol}) [${formatCryptoPrice(ruAth)}]`);
  } else {
    setBannerText('runnerUpDetails', '');
  }

  renderSmartStreamTicker(drops);
}

// Dedicated Overview Statistics System: Cache-First multi-mirror resilience & offline fallbacks
const OVERVIEW_STATS_CACHE_KEY = 'agent_id_overview_stats';
let isFetchingOverviewStats = false;
let overviewStatsRetryCount = 0;
const MAX_STATS_RETRIES = 2;

function hydrateCachedOverviewStats() {
  if (typeof localStorage !== 'undefined') {
    try {
      const raw = localStorage.getItem(OVERVIEW_STATS_CACHE_KEY);
      if (raw) {
        const cached = JSON.parse(raw);
        if (cached && (cached.stats || cached.total !== undefined || cached.count !== undefined || cached.agent_wallets || cached.total_created_wallets)) {
          applyOverviewStats(cached, false);
          return true;
        }
      }
    } catch (_) {}
  }
  if (typeof applyOverviewStatsFallback === 'function') {
    applyOverviewStatsFallback();
  }
  return false;
}

function applyOverviewStats(data, persist = true) {
  if (!data) return;
  const s = data.stats || data;
  if (s && typeof s === 'object') {
    if (s.pulse_24h && typeof s.pulse_24h === 'object') pulse24hServer = s.pulse_24h;
    globalStats = {
      total: Number(s.total !== undefined ? s.total : (s.count !== undefined ? s.count : (allDrops ? allDrops.length : 0))),
      smart: Number(s.smart !== undefined ? s.smart : 0),
      sniper_ready: Number(s.sniper_ready !== undefined ? s.sniper_ready : 0),
      proxy: Number(s.proxy !== undefined ? s.proxy : 0),
      nft_surge: Number(s.nft_surge !== undefined ? s.nft_surge : 0),
      secondary: Number(s.secondary !== undefined ? s.secondary : 0),
      latest_block: s.latest_block || latestKnownBlock || 0,
      agent_wallets: Number(s.agent_wallets || s.total_created_wallets || currentAgentWalletsCount)
    };

    if (persist && typeof localStorage !== 'undefined') {
      try {
        localStorage.setItem(OVERVIEW_STATS_CACHE_KEY, JSON.stringify(data));
      } catch (_) {}
    }

    if (s.agent_wallets || s.total_created_wallets) {
      applyAgentWalletsState(
        Number(s.agent_wallets || s.total_created_wallets),
        Number(s.agent_wallets_block || s.last_block || data.agent_wallets_block || 0)
      );
      globalStats.agent_wallets = currentAgentWalletsCount;
    }

    if (typeof document !== 'undefined') {
      const rEl = document.getElementById('radarCount');
      if (rEl) rEl.textContent = globalStats.total;
      const sEl = document.getElementById('smartCount');
      if (sEl) sEl.textContent = globalStats.smart;

      updateSniperReadyCount();
      updateNftSurgeCount();
      updateProxyCount();
      updateLaunchpadCount();
      updateSecondaryTabCount();
      if (allDrops && allDrops.length > 0) {
        renderStats(allDrops);
      }
    }
  } else if (data && data.total_created_wallets) {
    updateDigitalAiCounter(Number(data.total_created_wallets));
  }
}

function applyOverviewStatsFallback() {
  if (!allDrops || allDrops.length === 0) return;
  const localSmart = allDrops.filter(d => (d.smart_wallets && d.smart_wallets.length > 0) || (d.smart_minters && d.smart_minters.length > 0)).length;
  const localSecondary = allDrops.filter(d => isSecondaryMarketAsset(d)).length;
  const localSniper = allDrops.filter(d => isSniperReady(d)).length;
  const localProxy = allDrops.filter(d => Boolean(d.is_proxy_clone)).length;
  const localNft = allDrops.filter(d => evaluateNftSurge(d).is_nft_surge).length;

  globalStats = {
    total: (globalStats && globalStats.total) ? globalStats.total : allDrops.length,
    smart: (globalStats && globalStats.smart) ? globalStats.smart : localSmart,
    sniper_ready: (globalStats && globalStats.sniper_ready) ? globalStats.sniper_ready : localSniper,
    proxy: (globalStats && globalStats.proxy) ? globalStats.proxy : localProxy,
    nft_surge: (globalStats && globalStats.nft_surge) ? globalStats.nft_surge : localNft,
    secondary: (globalStats && globalStats.secondary) ? globalStats.secondary : localSecondary,
    latest_block: latestKnownBlock || (allDrops[0] ? allDrops[0].block : 0),
    agent_wallets: currentAgentWalletsCount
  };

  if (typeof document !== 'undefined') {
    const rEl = document.getElementById('radarCount');
    if (rEl) rEl.textContent = globalStats.total;
    const sEl = document.getElementById('smartCount');
    if (sEl) sEl.textContent = globalStats.smart;

    updateSniperReadyCount();
    updateNftSurgeCount();
    updateProxyCount();
    updateLaunchpadCount();
    updateSecondaryTabCount();
    renderStats(allDrops);
  }
}

async function fetchOverviewStats(isRetry = false) {
  // Cache-First: immediately hydrate from cached local state before attempting background network fetch
  if (!isRetry) {
    hydrateCachedOverviewStats();
  }

  if (isFetchingOverviewStats && !isRetry) return false;
  isFetchingOverviewStats = true;

  // Backend first (BACKEND_URL is '' on localhost, i.e. the local API), then the bundled stats files. No same-origin
  // /api/* attempts: on Vercel those can only 404.
  const candidateEndpoints = [
    `${BACKEND_URL}/api/stats`,
    `${BACKEND_URL}/api/overview`,
    `agent_id_stats.json`,
    `data/agent_id_stats.json`
  ];
  const endpoints = Array.from(new Set(candidateEndpoints.filter(Boolean)));

  let loadedStats = null;
  let lastErr = null;

  for (const ep of endpoints) {
    try {
      const res = await fetchWithTimeout(ep, { cache: 'default' }, 3500);
      if (!res || !res.ok) {
        lastErr = new Error(`HTTP ${res ? res.status : 'ERR'} from ${ep}`);
        continue;
      }
      const data = await res.json();
      if (data && (data.stats || data.total_created_wallets || data.agent_wallets || data.total)) {
        loadedStats = data;
        break;
      }
    } catch (err) {
      lastErr = err;
    }
  }

  if (loadedStats) {
    overviewStatsRetryCount = 0;
    applyOverviewStats(loadedStats);
    isFetchingOverviewStats = false;
    return true;
  }

  // Graceful background retry without alarming warnings since local cache is already active
  if (overviewStatsRetryCount < MAX_STATS_RETRIES) {
    overviewStatsRetryCount++;
    setTimeout(() => {
      fetchOverviewStats(true).catch(() => {});
    }, overviewStatsRetryCount * 1200);
  } else {
    applyOverviewStatsFallback();
  }

  isFetchingOverviewStats = false;
  return false;
}

if (typeof window !== 'undefined') {
  window.fetchOverviewStats = fetchOverviewStats;
  window.applyOverviewStats = applyOverviewStats;
  window.applyOverviewStatsFallback = applyOverviewStatsFallback;
  window.hydrateCachedOverviewStats = hydrateCachedOverviewStats;
}

// Evaluate Assisted Sniper Conviction (60% Rule & Anti-Rug)
function getSniperEval(item) {
  const std = String(item.standard || '');
  const isNft = std.includes('NFT') || std.includes('721') || std.includes('1155') || (item.classification && item.classification.is_nft);
  const isToken = std.includes('BEP-20') || std.includes('Fair-Mint') || (item.classification && (item.classification.category === 'TOKEN' || item.classification.category === 'TOKEN_FAIR_MINT'));

  const ms = Number(item.max_supply || 0);
  const tm = Number(item.total_minted || 0);
  const lp = Number(item.lp_usd || 0);
  const hasRef = Boolean(item.has_refund || (item.security && item.security.has_refund));
  const rLvl = String(item.risk_level || 'SAFE').toUpperCase();
  const sec = item.security || {};
  const isSerial = Boolean(item.is_serial_dev || sec.is_serial_dev);
  const isDrained = Boolean(item.is_fund_drain || sec.is_fund_drain);
  const isFresh = Boolean(item.is_fresh_dev || sec.is_fresh_dev);
  const isPaused = Boolean(item.is_paused);

  const valWei = Number(item.mint_value_wei || 0);
  const priceBnb = Number(item.mint_price_bnb || 0);
  const isPaid = priceBnb > 0.001 || valWei > 0;

  let prog = 0;
  if (isToken && !isNft) {
    if (ms > 0 && tm > 0 && ms === tm && !item.sold_sel && !item.cap) {
      prog = 0;
    } else {
      prog = ms > 0 ? (tm / ms) : 0;
    }
  } else {
    prog = ms > 0 ? (tm / ms) : 0;
  }

  const agents = Number(item.holders || item.unique_minters || 0);
  const isSoldOut = (prog >= 0.999) || (isNft && ms > 0 && tm >= ms);
  const progPass = (prog >= 0.60 && prog < 0.999) || (prog === 0 && agents >= 100);

  const checklist = [];
  if (isSoldOut) {
    checklist.push('<span style="color:var(--color-red);">✖ Progress: 100% Sold Out / Completed</span>');
  } else if (prog >= 0.60) {
    checklist.push(`<span style="color:var(--color-green);">✔ Progress: ${(prog * 100).toFixed(1)}% (≥60% conviction threshold passed)</span>`);
  } else if (prog === 0 && agents >= 100) {
    checklist.push(`<span style="color:var(--color-green);">✔ Progress: Crowd Surge (${agents} active agent wallets)</span>`);
  } else {
    const pStr = prog > 0 ? `${(prog * 100).toFixed(1)}%` : '0%';
    checklist.push(`<span style="color:var(--color-yellow);">⏳ Progress: ${pStr} (&lt;60% threshold, tracking)</span>`);
  }

  if (isSerial) {
    const sCnt = sec.serial_count || item.serial_count || 3;
    checklist.push(`<span style="color:var(--color-red);">✖ Deployer: 🚨 Serial Dev (${sCnt} contracts deployed in 24h)</span>`);
  } else if (isFresh) {
    checklist.push('<span style="color:var(--color-yellow);">⚠️ Deployer: Fresh wallet (&lt;8h, low nonce)</span>');
  } else {
    checklist.push('<span style="color:var(--color-green);">✔ Deployer: Clean reputation (No spam deployment)</span>');
  }

  if (rLvl === 'RUG' || isDrained) {
    checklist.push('<span style="color:var(--color-red);">✖ Capital Drain: 🚨 Empty contract with no DEX LP (Funds pocketed)</span>');
  } else {
    checklist.push('<span style="color:var(--color-green);">✔ Capital Safety: No fund drain detected</span>');
  }

  if (isPaid) {
    if (hasRef) {
      const refM = (sec.refund_methods || item.refund_methods || ['refund()'])[0];
      checklist.push(`<span style="color:var(--color-green);">✔ Refund Guarantee: Bytecode refund verified (${refM})</span>`);
    } else {
      checklist.push('<span style="color:var(--color-red);">✖ Refund Guarantee: ⚠️ No refund method (Capital trapped if unfilled)</span>');
    }
  } else {
    checklist.push('<span class="dim">▫ Refund Guarantee: N/A (Free Mint / Gas Only)</span>');
  }

  if (isToken) {
    if (lp >= 500) {
      checklist.push(`<span style="color:var(--color-yellow);">✖ Secondary Market: DEX LP already active ($${lp.toLocaleString(undefined, { maximumFractionDigits: 0 })}) → Trade on DEX</span>`);
    } else {
      checklist.push('<span style="color:var(--color-green);">✔ Secondary Market: Primary bonding phase (No DEX LP yet)</span>');
    }
  }

  if (isPaused) {
    return { is_ready: false, status: 'PAUSED', badge: '⏸️ Paused', prog, checklist };
  }
  if (rLvl === 'RUG' || isDrained) {
    return { is_ready: false, status: 'RUG_RISK', badge: '🚨 Rug Risk', prog, checklist };
  }
  if (isSerial) {
    return { is_ready: false, status: 'SERIAL_DEV', badge: '🚨 Serial Dev', prog, checklist };
  }
  if (isToken && lp >= 500) {
    return { is_ready: false, status: 'GRADUATED_TO_DEX', badge: '📈 On DEX', prog, checklist };
  }
  if (isPaid && !hasRef) {
    return { is_ready: false, status: 'NO_REFUND', badge: '⚠️ No Refund', prog, checklist };
  }
  if (isSoldOut) {
    return { is_ready: false, status: 'SOLD_OUT', badge: '🛑 Sold Out', prog, checklist };
  }
  if (progPass) {
    return { is_ready: true, status: 'SNIPER_READY', badge: '🎯 Snipe Ready', prog, checklist };
  }

  return {
    is_ready: false,
    status: 'TRACKING',
    badge: prog > 0 ? `⏳ Track (${(prog * 100).toFixed(0)}%)` : '⏳ Tracking',
    prog,
    checklist
  };
}

function isSniperReady(item) {
  if (item.sniper_ready !== undefined) {
    return Boolean(item.sniper_ready);
  }
  return getSniperEval(item).is_ready;
}

function updateSniperReadyCount() {
  if (typeof document === 'undefined') return;
  const el = document.getElementById('sniperReadyCount');
  if (el) {
    const cnt = (globalStats && globalStats.sniper_ready !== undefined)
      ? globalStats.sniper_ready
      : allDrops.filter(d => isSniperReady(d)).length;
    el.textContent = cnt;
  }
}

function isFreeMintDrop(d) {
  if (!d) return false;
  const pStr = String(d.mint_price_str || '').toUpperCase();
  const vWei = Number(d.mint_value_wei || 0);
  return Boolean(d.has_free_mint || pStr.includes('FREE') || (vWei === 0 && Number(d.mint_price_bnb || 0) === 0 && !pStr.includes('PAID') && !pStr.includes('DEX') && !pStr.includes('TBD')));
}

// Mint terms read by the worker (record.mint_terms, see monitor.probe_mint_terms): per-wallet limit, start / end
// time, who may mint (gate) and whether the supply cap was actually read. Slots left come from the supply fields.
// Anything not read stays null and is shown as unknown, never guessed.
const MINT_GATE_LABELS = {
  open: { chip: '🟢 Open to all', title: 'A wallet with no Agent ID and no whitelist could mint in a simulation' },
  agent_id: { chip: '🪪 Agent ID needed', title: 'An Agent ID wallet could mint in a simulation; a wallet without one was refused' },
  whitelist: { chip: '📝 Whitelist', title: 'Only eligible wallets (allow list or claim allocation) could mint in a simulation' },
};

function getMintTerms(item, nowSec = Date.now() / 1000) {
  const t = (item && item.mint_terms && typeof item.mint_terms === 'object') ? item.mint_terms : {};
  const msRaw = Number(item && item.max_supply);
  const ms = (Number.isFinite(msRaw) && msRaw > 0 && msRaw < 1e20) ? msRaw : 0;
  const tm = Number((item && item.total_minted) || 0);
  const num = (v) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : null);
  const start = num(t.start);
  const end = num(t.end);
  return {
    slotsLeft: ms > 0 ? Math.max(0, Math.floor(ms - tm)) : null,
    perWallet: num(t.per_wallet),
    start,
    end,
    notStarted: start !== null && start > nowSec,
    ended: end !== null && end <= nowSec,
    gate: MINT_GATE_LABELS[t.gate] ? t.gate : null,
    cap: t.cap || (ms > 0 ? 'capped' : null),
    checked: Boolean(item && item.mint_terms),
    closed: t.open === false || t.state === 'not_open',   // mintOpen()-style switch off, or refused as "not open"
    stateEnded: t.state === 'ended',
    stateSoldOut: t.state === 'sold_out',
    invalidCall: t.mint_call === 'invalid',                // the stored call can't mint (e.g. deployment bytecode)
    paidUnknown: Boolean(t.paid),                          // refused for the price: paid, price not read
  };
}

// "3m", "5h", "2d" for a number of seconds.
function formatDurationShort(sec) {
  const s = Math.max(0, Math.round(sec));
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

// Timing chip text: "Starts in 2h" / "Ends in 5h" / "Ended", or '' if neither time is known.
function mintTimingLabel(terms, nowSec = Date.now() / 1000) {
  if (terms.stateEnded) return '⌛ Ended';
  if (terms.closed && !terms.notStarted) return '⏸ Mint closed';
  if (terms.notStarted) return `⏳ Starts in ${formatDurationShort(terms.start - nowSec)}`;
  if (terms.ended) return '⌛ Ended';
  if (terms.end) return `⌛ Ends in ${formatDurationShort(terms.end - nowSec)}`;
  return '';
}

// Who may mint, next to the drop's name on every row (desktop and cards), so it is seen before anything else.
// From record.mint_terms.gate (see monitor.probe_mint_terms); "open" is shown in the mint chips instead.
function gateBadgeHtml(item) {
  const gate = getMintTerms(item).gate;
  if (gate === 'agent_id') {
    const how = item.mint_terms && item.mint_terms.gate_from === 'bytecode'
      ? 'The contract code checks for an Agent ID'
      : 'A test mint from a wallet without an Agent ID was refused';
    return `<span class="gate-badge gate-badge-agent" title="Agent ID needed to mint. ${how}.">🪪 Agent ID</span>`;
  }
  if (gate === 'whitelist') {
    return `<span class="gate-badge gate-badge-wl" title="${MINT_GATE_LABELS.whitelist.title}">📝 Whitelist</span>`;
  }
  return '';
}

// Mobile card only (hidden on desktop, where the chips sit under the mint price): a full-width row with the mint
// terms chips and a one-tap Mint button that opens the token modal at its Mint section. No button for drops that
// cannot be minted (DEX-only, sold out, ended) or that are flagged unsafe (honeypot / rug / drain / high risk).
function canMintFromCard(item) {
  if (!item || String(item.mint_price_str || '').toUpperCase().includes('DEX')) return false;
  if (isDropSoldOut(item) || isDropUnsafeToMint(item) || item.is_paused) return false;
  const terms = getMintTerms(item);
  if (terms.ended || terms.stateEnded || terms.stateSoldOut || terms.slotsLeft === 0 || terms.invalidCall) return false;
  return Boolean(item.mint_calldata || item.has_mint_calldata || isFreeMintDrop(item));
}

function cardTermsCellHtml(item) {
  const chips = mintTermsChipsHtml(item);
  const terms = getMintTerms(item);
  let btn = '';
  if (canMintFromCard(item)) {
    const label = (terms.notStarted || terms.closed) ? '⏳ Mint soon' : (isFreeMintDrop(item) ? '⚡ Mint free' : '⚡ Mint');
    btn = `<button type="button" class="card-mint-btn" onclick="openMintFromCard('${item.contract}', event)" title="Open the mint panel for this drop">${label}</button>`;
  }
  return (chips || btn) ? `<td class="col-terms">${chips}${btn}</td>` : '<td class="col-terms"></td>';
}

// Card Mint button: open the token modal and jump straight to its Mint section.
function openMintFromCard(contract, ev) {
  if (ev) { ev.stopPropagation(); if (typeof ev.preventDefault === 'function') ev.preventDefault(); }
  const c = String(contract || '').toLowerCase();
  const item = allDrops.find(d => String((d && d.contract) || '').toLowerCase() === c);
  if (!item) return;
  trackAnalytics('card_mint_clicked', { token_symbol: item.symbol || '', is_free: isFreeMintDrop(item) });
  Promise.resolve(openModal(item, 'card_mint')).then(() => {
    setTimeout(() => jumpToModalSection('mint_action', item.symbol || '', isFreeMintDrop(item), false, isDropSoldOut(item)), 60);
  });
}
if (typeof window !== 'undefined') window.openMintFromCard = openMintFromCard;

// The compact terms line under a row's mint price: slots left · per wallet · timing · gate.
function mintTermsChipsHtml(item) {
  if (!item || String(item.mint_price_str || '').toUpperCase().includes('DEX')) return '';
  const terms = getMintTerms(item);
  const chips = [];
  if (terms.paidUnknown && String(item.mint_price_str || '').toUpperCase().includes('TBD')) {
    chips.push('<span class="mint-chip" title="The contract refused a free mint for its price; the price itself could not be read">💎 Paid</span>');
  }
  if (terms.stateSoldOut) chips.push('<span class="mint-chip mint-chip-bad">Sold out</span>');
  else if (terms.slotsLeft !== null) {
    chips.push(terms.slotsLeft > 0
      ? `<span class="mint-chip" title="Max supply minus minted">${formatCount(terms.slotsLeft)} left</span>`
      : '<span class="mint-chip mint-chip-bad">Sold out</span>');
  }
  if (terms.perWallet !== null) chips.push(`<span class="mint-chip" title="Per-wallet limit read from the contract">Max ${formatCount(terms.perWallet)}/wallet</span>`);
  const timing = mintTimingLabel(terms);
  if (timing) chips.push(`<span class="mint-chip${(terms.ended || terms.stateEnded || terms.closed) ? ' mint-chip-bad' : ''}" title="Mint window read from the contract">${timing}</span>`);
  if (terms.gate) chips.push(`<span class="mint-chip mint-chip-gate-${terms.gate}" title="${MINT_GATE_LABELS[terms.gate].title}">${MINT_GATE_LABELS[terms.gate].chip}</span>`);
  return chips.length ? `<div class="mint-chips">${chips.join('')}</div>` : '';
}

const UNCAPPED_MINT_TITLE = 'No max supply found: an open edition, or a cap the radar could not read';

// Cap filled. Mirrors computeSniperReady: an ERC-20 whose whole supply was pre-minted (minted === supply with no
// mint-cap markers) is NOT sold out — that is the token's supply, not a filled mint.
function isDropSoldOut(d) {
  if (!d) return false;
  const ms = Number(d.max_supply || 0);
  const tm = Number(d.total_minted || 0);
  if (!(ms > 0)) return false;
  const std = String(d.standard || '').toUpperCase();
  const isNft = /721|1155|NFT/.test(std);
  if (!isNft && tm === ms && !d.sold_sel && !d.cap) return false;
  return tm / ms >= 0.999;
}

// Contracts a free-mint hunter should never be sent to: rug / high risk, honeypot, fund drain.
function isDropUnsafeToMint(d) {
  if (!d) return false;
  const level = String(d.risk_level || '').toUpperCase();
  const badge = String(d.risk_badge || '').toUpperCase();
  return level === 'RUG' || level === 'HIGH'
    || badge.includes('HONEYPOT') || badge.includes('FUND DRAIN') || badge.includes('DRAIN')
    || Boolean(d.is_honeypot) || Boolean(d.security && d.security.is_fund_drain);
}

// The FREE pill: free, not sold out, not paused, not flagged unsafe, and inside its mint window if one is known.
function isFreeMintableNow(d) {
  if (!isFreeMintDrop(d) || isDropSoldOut(d) || isDropUnsafeToMint(d) || (d && d.is_paused)) return false;
  const terms = getMintTerms(d);
  // a known mint window that is closed or not open yet, a switched-off mint, or one the contract says is over / full
  return !terms.ended && !terms.notStarted && !terms.closed && !terms.stateEnded && !terms.stateSoldOut;
}

// Back to the ALL pill, More Filters cleared. Used when the user switches tabs.
function resetActiveFilterToAll() {
  activeFilter = 'ALL';
  if (typeof document === 'undefined' || !document.querySelectorAll) return;
  document.querySelectorAll('.pill').forEach(b => b.classList.remove('active'));
  const pillAll = document.querySelector('.pill[data-filter="ALL"]');
  if (pillAll) pillAll.classList.add('active');
  const mfTrigger = document.getElementById('moreFiltersTrigger');
  const mfLabel = document.getElementById('moreFiltersLabel');
  if (mfTrigger) mfTrigger.classList.remove('active');
  if (mfLabel) mfLabel.textContent = '⚙️ More Filters';
  document.querySelectorAll('#moreFiltersOptions .custom-option').forEach(o => o.classList.remove('selected'));
}

function updateFreeMintsCount() {
  if (typeof document === 'undefined') return;
  const el = document.getElementById('freeMintsCount');
  if (el) {
    const cnt = allDrops.filter(d => isFreeMintableNow(d)).length;
    el.textContent = cnt;
  }
}

// Server timestamps ("YYYY-MM-DD HH:MM:SS" or ISO without a zone) are UTC. Returns a Date, or null if unparseable.
function parseServerUtc(value) {
  if (!value) return null;
  let str = String(value).trim().replace(' ', 'T');
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(str)) return null;
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(str)) str += 'Z';
  const dt = new Date(str);
  return Number.isNaN(dt.getTime()) ? null : dt;
}

// Registry of all 24H Market Pulse & Hero Card items for instant modal inspection
const pulseItemsMap = new Map();

function registerPulseItem(contractAddress, item) {
  if (!contractAddress || !item) return;
  const c = String(contractAddress).trim().toLowerCase();
  if (c) {
    pulseItemsMap.set(c, item.item ? { ...item, ...item.item } : item);
  }
}

// Open inspect modal by contract address (interactive ticker & pulse cards click handler)
function openInspectModalByContract(contractAddress, fallbackData = null) {
  if (!contractAddress && !fallbackData) return;
  const cNorm = String(contractAddress || (fallbackData && fallbackData.contract) || '').trim().toLowerCase();

  let target = null;

  // 1. A full record this page already has (allDrops) wins over the slim row a Pulse card was built from
  {
    let list = [];
    if (typeof allDrops !== 'undefined' && Array.isArray(allDrops) && allDrops.length > 0) {
      list = allDrops;
    } else if (typeof window !== 'undefined' && Array.isArray(window.allDrops) && window.allDrops.length > 0) {
      list = window.allDrops;
    } else if (typeof global !== 'undefined' && Array.isArray(global.allDrops) && global.allDrops.length > 0) {
      list = global.allDrops;
    }
    const found = cNorm ? list.find(d => (d.contract || '').toLowerCase() === cNorm) : null;
    if (found) target = found;
  }

  // 2. Instant pulseItemsMap (every row of the 24H Pulse cards: Runners, Launches, Smart Mints, NFT Mints)
  if (!target && cNorm && pulseItemsMap.has(cNorm)) {
    target = pulseItemsMap.get(cNorm);
  }

  // 3. Check savedTopGainers (Hall of Fame & Post-Mint winners)
  if (!target && typeof savedTopGainers !== 'undefined' && Array.isArray(savedTopGainers)) {
    const foundGainer = savedTopGainers.find(g => {
      const gC = String(g.contract || (g.item && g.item.contract) || '').toLowerCase();
      return gC === cNorm;
    });
    if (foundGainer) {
      target = foundGainer.item || foundGainer;
    }
  }

  // 4. Check getBaselineTopGainers
  if (!target && typeof getBaselineTopGainers === 'function') {
    const baselines = getBaselineTopGainers();
    const foundBase = baselines.find(g => {
      const gC = String(g.contract || (g.item && g.item.contract) || '').toLowerCase();
      return gC === cNorm;
    });
    if (foundBase) {
      target = foundBase.item || foundBase;
    }
  }

  // 5. Check secondaryDropsCache or smartDropsCache
  if (!target && typeof secondaryDropsCache !== 'undefined' && Array.isArray(secondaryDropsCache)) {
    target = secondaryDropsCache.find(d => (d.contract || '').toLowerCase() === cNorm);
  }
  if (!target && typeof smartDropsCache !== 'undefined' && Array.isArray(smartDropsCache)) {
    target = smartDropsCache.find(d => (d.contract || '').toLowerCase() === cNorm);
  }

  // 6. Use fallbackData if provided
  if (!target && fallbackData) {
    const rawFb = fallbackData.item || fallbackData;
    target = {
      contract: contractAddress || rawFb.contract,
      ...rawFb
    };
  }

  // 7. Resilient fallback: synthesize a valid drop item so the inspection modal ALWAYS opens!
  if (!target && cNorm) {
    target = {
      contract: contractAddress,
      name: (fallbackData && fallbackData.name) || 'BNB Chain Asset',
      symbol: (fallbackData && fallbackData.symbol) || '',
      standard: (fallbackData && fallbackData.standard) || 'BEP-20',
      holders: 1,
      block: typeof latestKnownBlock !== 'undefined' ? latestKnownBlock : 0,
      token_price_usd: (fallbackData && fallbackData.price) || 0,
      risk_level: 'SAFE',
      risk_badge: '🟢 Clean'
    };
  }

  const modalFn = typeof openModal === 'function' ? openModal : (typeof window !== 'undefined' && typeof window.openModal === 'function' ? window.openModal : (typeof global !== 'undefined' && typeof global.openModal === 'function' ? global.openModal : null));
  if (target && modalFn) {
    const finalTarget = target.item ? { ...target, ...target.item } : target;
    modalFn(finalTarget);
  }
}

// Render Real-Time Smart Money Stream Ticker Tape
function renderSmartStreamTicker(drops) {
  if (typeof document === 'undefined') return;
  const trackEl = document.getElementById('tickerTrack');
  if (!trackEl || !Array.isArray(drops) || drops.length === 0) return;

  // detected_at is UTC ("YYYY-MM-DD HH:MM:SS", no zone). Show it in the viewer's own time with the zone named,
  // e.g. "14:11 CET": a bare "02:11:57" left people guessing whose clock it was.
  const formatTickerTime = (detected_at) => {
    const dt = parseServerUtc(detected_at);
    if (!dt) return 'LIVE';
    try {
      return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
    } catch (e) {
      return `${dt.toISOString().slice(11, 16)} UTC`;
    }
  };

  const events = [];
  const targetDrops = drops.length > 80 ? drops.slice(0, 80) : drops;

  targetDrops.forEach((d) => {
    const symbol = escapeHtml((d.symbol || d.name || 'UNKNOWN').toUpperCase());
    const contract = escapeHtml(d.contract || '');
    const time = formatTickerTime(d.detected_at);
    const smartWallets = Array.isArray(d.smart_wallets) && d.smart_wallets.length > 0
      ? d.smart_wallets
      : (Array.isArray(d.smart_minters) && d.smart_minters.length > 0 ? d.smart_minters : []);

    const ms = Number(d.max_supply || 0);
    const tm = Number(d.total_minted || 0);
    const prog = ms > 0 ? (tm / ms) : 0;
    const isNft = Boolean((d.standard && (d.standard.includes('NFT') || d.standard.includes('721') || d.standard.includes('1155'))) || (d.classification && d.classification.is_nft));
    const isSoldOut = (prog >= 0.999) || (isNft && ms > 0 && tm >= ms);
    const sniperReady = isSniperReady(d);
    const blockNum = Number(d.block || 0);

    // 1. Smart Money / Multi-wallet entry
    if (smartWallets.length > 1) {
      events.push({
        block: blockNum,
        time,
        tagClass: 'tag-smart',
        tagText: '🧠 SMART MONEY',
        text: `🔥 <span class="ticker-wallet">${smartWallets.length} Smart Wallets</span> entered <strong>$${symbol}</strong> simultaneously`,
        contract
      });
    } else if (smartWallets.length === 1) {
      const w = smartWallets[0];
      const isF = typeof followedWallets !== 'undefined' && followedWallets.has(String(w).toLowerCase());
      const wLabel = w.length > 18 ? `${w.slice(0, 6)}...${w.slice(-4)}` : w;
      const action = tm >= 1 ? `minted ${formatCount(tm)} ` : 'entered ';
      events.push({
        block: blockNum,
        time,
        tagClass: 'tag-smart',
        tagText: '🧠 SMART MONEY',
        text: `🧠 Smart Hunter <span class="ticker-wallet${isF ? ' followed' : ''}">${isF ? '⭐ ' : ''}${escapeHtml(wLabel)}</span> ${action}<strong>$${symbol}</strong>`,
        contract
      });
    }

    // 2. Sold Out or Sniper Ready Conviction
    if (isSoldOut && ms > 0) {
      events.push({
        block: blockNum,
        time,
        tagClass: 'tag-snipe',
        tagText: '🔒 SOLD OUT',
        text: `🔒 <strong>$${symbol}</strong> Sold Out (100% cap filled)`,
        contract
      });
    } else if (sniperReady && prog >= 0.6) {
      events.push({
        block: blockNum,
        time,
        tagClass: 'tag-snipe',
        tagText: '🎯 SNIPER READY',
        text: `🎯 <strong>$${symbol}</strong> reached ${(prog * 100).toFixed(1)}% (Sniper Ready)`,
        contract
      });
    }

    // 3. NFT Surge / Crowd Surge
    if (isNft && Number(d.holders || 0) >= 30) {
      events.push({
        block: blockNum,
        time,
        tagClass: 'tag-surge',
        tagText: '🦋 NFT SURGE',
        text: `🦋 <strong>$${symbol}</strong> surge: <strong>${d.holders} agent minters</strong>`,
        contract
      });
    }

    // 4. ATH Gainer
    const ath = Math.max(Number(d.athPrice || 0), Number(d.token_price_usd || 0), Number(d.secUsd || 0));
    const profit = Number(d.athProfit || d.profit || 0);
    if (profit >= 20 && ath > 0) {
      events.push({
        block: blockNum,
        time,
        tagClass: 'tag-profit',
        tagText: '🚀 TOP GAIN',
        text: `🚀 <strong>$${symbol}</strong> hit ATH <strong>${formatCryptoPrice(ath)}</strong> (+${profit.toFixed(0)}%)`,
        contract
      });
    }
  });

  // If fewer events, backfill with newest drops
  if (events.length < 15) {
    drops.slice(0, 5).forEach((d) => {
      const symbol = escapeHtml((d.symbol || d.name || 'UNKNOWN').toUpperCase());
      const contract = escapeHtml(d.contract || '');
      const time = formatTickerTime(d.detected_at);
      events.push({
        block: Number(d.block || 0),
        time,
        tagClass: 'tag-new',
        tagText: '✨ NEW DROP',
        text: `✨ New drop <strong>$${symbol}</strong> (${escapeHtml(d.name || '')}) detected`,
        contract
      });
    });
  }

  // Deduplicate and take top 20 chronologically (latest block first)
  events.sort((a, b) => b.block - a.block);
  const selectedEvents = events.slice(0, 20);
  if (selectedEvents.length === 0) return;

  const itemsHtml = selectedEvents.map((e) => `
    <div class="ticker-item" onclick="openInspectModalByContract('${e.contract}')" title="Click to inspect drop">
      <span class="ticker-time">[${e.time}]</span>
      <span class="ticker-tag ${e.tagClass}">${e.tagText}</span>
      <span class="ticker-body">${e.text}</span>
    </div>
  `).join('');

  // Duplicate items for a seamless, continuous infinite CSS marquee scroll
  trackEl.innerHTML = itemsHtml + itemsHtml;
}

if (typeof window !== 'undefined') {
  window.openInspectModalByContract = openInspectModalByContract;
  window.renderSmartStreamTicker = renderSmartStreamTicker;
  window.openModal = openModal;
  window.registerPulseItem = registerPulseItem;
  window.pulseItemsMap = pulseItemsMap;
}

// Option D: Evaluate NFT Volume Surge Conviction (adapted from watch.mjs nftVolWatch)
function evaluateNftSurge(item, fillPrices = [], secondaryMoves = 0, userHeld = 0, prevStats = null) {
  const std = String(item.standard || '');
  const isNft = std.includes('721') || std.includes('NFT') || std.includes('1155') || Boolean(item.is_nft);
  if (!isNft) {
    return {
      is_nft_surge: false,
      sales_count: 0,
      vol_bnb: 0,
      floor_price_bnb: 0,
      high_price_bnb: 0,
      secondary_transfers: 0,
      badge: ''
    };
  }

  const px = fillPrices.filter(p => typeof p === 'number' && p >= 0);
  const salesCnt = px.length || Number(item.secondary_sales_count || 0);
  const volBnb = px.length ? px.reduce((a, b) => a + b, 0) : Number(item.secondary_vol_bnb || 0);
  const floorBnb = px.length ? Math.min(...px) : Number(item.floor_price_bnb || 0);
  const highBnb = px.length ? Math.max(...px) : Number(item.high_price_bnb || 0);
  const moves = secondaryMoves || Number(item.secondary_transfers || 0);
  // Any sale in the 24h window marks an active collection (same rule as evaluate_nft_surge in monitor.py).
  const isHot = salesCnt >= 1;

  return {
    is_nft_surge: isHot,
    sales_count: salesCnt,
    vol_bnb: volBnb,
    floor_price_bnb: floorBnb,
    high_price_bnb: highBnb,
    secondary_transfers: moves,
    badge: isHot ? `🦋 SURGE (${salesCnt} sales)` : ''
  };
}

function updateNftSurgeCount() {
  if (typeof document === 'undefined') return;
  const el = document.getElementById('nftSurgeCount');
  if (el) {
    const cnt = (globalStats && globalStats.nft_surge !== undefined)
      ? globalStats.nft_surge
      : allDrops.filter(d => Boolean(d.is_nft_surge)).length;
    el.textContent = cnt;
  }
}

function updateProxyCount() {
  if (typeof document === 'undefined') return;
  const el = document.getElementById('proxyCount');
  if (el) {
    const cnt = (globalStats && globalStats.proxy !== undefined)
      ? globalStats.proxy
      : allDrops.filter(d => Boolean(d.is_proxy_clone)).length;
    el.textContent = cnt;
  }
}

function isLaunchpadDrop(d) {
  if (!d) return false;
  const std = String(d.standard || '').toLowerCase();
  const cat = String(d.category || (d.classification && d.classification.category) || '').toUpperCase();
  const pType = String(d.proxy_type || '').toLowerCase();
  const sigs = (d.signals || []).join(' ').toLowerCase();
  const dep = String(d.deployer || '').toLowerCase();
  const master = String(d.master_implementation || '').toLowerCase();
  const contract = String(d.contract || '').toLowerCase();

  const FOUR_MEME_FACTORY = '0x5c952063c7fc8610ffdb798152d69f0b9550762b';
  const FOUR_MEME_ROUTER = '0xf251f83e40a78868fcfa3fa4599dad6494e46034';
  const FLAP_FACTORY = '0x90497450f2a706f1951b5bdda52b4e5d16f34c06';

  if (dep === FOUR_MEME_FACTORY || dep === FOUR_MEME_ROUTER || dep === FLAP_FACTORY) return true;
  if (master === FOUR_MEME_FACTORY || master === FOUR_MEME_ROUTER || master === FLAP_FACTORY) return true;
  if (contract === FOUR_MEME_FACTORY || contract === FOUR_MEME_ROUTER || contract === FLAP_FACTORY) return true;

  if (Boolean(d.is_proxy_clone) || pType.includes('clone') || pType.includes('proxy')) return true;
  if (std.includes('fair-mint') || std.includes('launchpad') || cat === 'TOKEN_FAIR_MINT') return true;
  if (sigs.includes('four.meme') || sigs.includes('flap') || sigs.includes('launchpad') || sigs.includes('bonding')) return true;

  return false;
}

function updateLaunchpadCount() {
  if (typeof document === 'undefined') return;
  const cnt = allDrops.filter(d => isLaunchpadDrop(d)).length;
  const tabEl = document.getElementById('launchpadTabCount');
  const pillEl = document.getElementById('launchpadCount');
  if (tabEl) tabEl.textContent = cnt;
  if (pillEl) pillEl.textContent = cnt;
}

// Market cap to show, or null (unknown / excluded / implausible). Twin of filters.sanitized_mcap.
const MCAP_GUARD_ABOVE_USD = 50000000;
const MCAP_GUARD_LP_BELOW_USD = 250000;
function getSanitizedMcap(d) {
  const sm = serverMetrics(d);
  if (sm && Object.prototype.hasOwnProperty.call(sm, 'market_cap')) {
    const v = Number(sm.market_cap);   // the server's value: a positive number, or null (unknown / implausible)
    return sm.market_cap !== null && Number.isFinite(v) && v > 0 ? v : null;
  }
  if (!d || isExcludedMajorAsset(d)) return null;
  let mc = numOrZero(d.market_cap) || numOrZero(d.fdv);
  if (mc <= 0) {
    // supply x price only where the supply unit is certain (a BEP-20 token's whole-token supply); no magnitude guess
    const px = numOrZero(d.token_price_usd) || numOrZero(d.market_price_usd);
    const supply = numOrZero(d.total_minted) || numOrZero(d.max_supply);
    if (px > 0 && supply > 0 && String(d.standard || '').includes('BEP-20')) mc = supply * px;
  }
  if (mc <= 0) return null;
  // Sanity check: an implausible valuation without proportional pool liquidity is not shown
  if (mc > MCAP_GUARD_ABOVE_USD && numOrZero(d.lp_usd) < MCAP_GUARD_LP_BELOW_USD) return null;
  return mc;
}

// Secondary Market asset detector (twin of filters.is_secondary_market)
function isSecondaryMarketAsset(d) {
  const sm = serverMetrics(d);
  if (sm && typeof sm.secondary === 'boolean') return sm.secondary;
  if (!d || isExcludedMajorAsset(d)) return false;
  const lp = numOrZero(d.lp_usd);
  const mc = getSanitizedMcap(d) || 0;
  const px = numOrZero(d.token_price_usd);
  const vol = numOrZero(d.volume_24h);
  const secVol = numOrZero(d.secondary_vol_bnb);
  const hasPair = Boolean(d.pair_address || d.pair_url);
  const isGrad = Boolean(d.is_graduated);
  return lp > 50 || mc > 500 || (px > 0 && vol > 0) || secVol > 0 || hasPair || isGrad;
}

let hasLoadedSecondaryDrops = false;
let isLoadingSecondaryDrops = false;
// The Secondary list is the top SECONDARY_FIRST_PAGE + SECONDARY_REST_PAGE by market cap, in two requests. The second one
// starts SECONDARY_OVERLAP rows early because the ranking can shift between the two (a minute-old snapshot each); the
// merge ignores contracts it already has.
const SECONDARY_FIRST_PAGE = 150;
const SECONDARY_REST_PAGE = 150;
const SECONDARY_OVERLAP = 10;
let secondaryRestRequested = false;

function afterSecondaryMerge() {
  updateSecondaryTabCount();
  updateSecondaryBannerUI();
  updateDailyPulseUI();
  if (activeTab === 'SECONDARY') {
    renderTable();
  }
}

async function loadSecondaryRest() {
  if (secondaryRestRequested) return;
  secondaryRestRequested = true;
  try {
    const offset = SECONDARY_FIRST_PAGE - SECONDARY_OVERLAP;
    const res = await fetchWithTimeout(
      `${BACKEND_URL}/api/feed?filter=secondary&limit=${SECONDARY_REST_PAGE + SECONDARY_OVERLAP}&offset=${offset}&view=list`,
      { cache: 'default' }, 8000);
    if (!res || !res.ok) { secondaryRestRequested = false; return; }
    const payload = await res.json();
    const drops = Array.isArray(payload) ? payload : (payload.projects || []);
    if (drops.length > 0 && mergeDropsIntoAllDrops(drops) > 0) afterSecondaryMerge();
  } catch (err) {
    secondaryRestRequested = false;      // the first page is already on screen; a later load tries again
  }
}

async function loadSecondaryDrops() {
  if (hasLoadedSecondaryDrops) return;
  if (isLoadingSecondaryDrops) {
    while (isLoadingSecondaryDrops) {
      await new Promise(r => setTimeout(r, 40));
    }
    return;
  }
  isLoadingSecondaryDrops = true;
  try {
    // One attempt only: BACKEND_URL already is the Render host in production, so a second request to the same
    // host would just wait out another timeout on a sleeping server.
    // `view=list` asks for the slim record (no modal-only fields, see api.LIST_VIEW_DROP); an older backend ignores it
    // and answers with the full shape, which works the same. The first page is what the tab shows first (about 55 KB
    // gzipped instead of the 137 KB the 300 full records took); the rest follows in the background.
    let res;
    try {
      res = await fetchWithTimeout(`${BACKEND_URL}/api/feed?filter=secondary&limit=${SECONDARY_FIRST_PAGE}&view=list`, { cache: 'default' }, 8000);
    } catch (e1) {
      return;
    }

    if (res && res.ok) {
      const payload = await res.json();
      const drops = Array.isArray(payload) ? payload : (payload.projects || []);
      if (Array.isArray(drops) && drops.length > 0) {
        mergeDropsIntoAllDrops(drops);
        hasLoadedSecondaryDrops = true;
        afterSecondaryMerge();
        if (payload && payload.has_more) setTimeout(loadSecondaryRest, 0);
      }
    }
  } catch (err) {
    console.warn('Secondary drops load fallback:', err);
  } finally {
    isLoadingSecondaryDrops = false;
  }
}
if (typeof window !== 'undefined') {
  window.loadSecondaryDrops = loadSecondaryDrops;
}

function updateSecondaryTabCount() {
  if (typeof document === 'undefined') return;
  const count = allDrops.filter(d => isSecondaryMarketAsset(d)).length;
  const tabEl = document.getElementById('secondaryTabCount');
  if (tabEl) tabEl.textContent = count;
  if (typeof syncMobileTabSelector === 'function') syncMobileTabSelector();
}

function updateSecondaryBannerUI() {
  if (typeof document === 'undefined') return;
  const banner = document.getElementById('secondaryMarketBanner');
  if (!banner) return;

  const secDrops = allDrops.filter(d => isSecondaryMarketAsset(d));
  let totalMcap = 0;
  let totalVol = 0;
  let totalLp = 0;
  let topAsset = null;
  let topMcap = 0;

  secDrops.forEach(d => {
    const mc = getSanitizedMcap(d);
    const vol = Number(d.volume_24h || 0);
    const lp = Number(d.lp_usd || 0);

    totalMcap += mc;
    totalVol += vol;
    totalLp += lp;

    if (mc > topMcap) {
      topMcap = mc;
      topAsset = d;
    }
  });

  const secTotalMcapEl = document.getElementById('secTotalMcap');
  const secTotalVolEl = document.getElementById('secTotalVol');
  const secTotalLpEl = document.getElementById('secTotalLp');
  const secTopAssetEl = document.getElementById('secTopAsset');

  if (secTotalMcapEl) secTotalMcapEl.textContent = `$${formatCount(totalMcap)}`;
  if (secTotalVolEl) secTotalVolEl.textContent = `$${formatCount(totalVol)}`;
  if (secTotalLpEl) secTotalLpEl.textContent = `$${formatCount(totalLp)}`;
  if (secTopAssetEl) {
    if (topAsset) {
      secTopAssetEl.textContent = `${topAsset.name || topAsset.symbol} ($${formatCount(topMcap)})`;
      secTopAssetEl.onclick = () => openModal(topAsset);
    } else {
      secTopAssetEl.textContent = '—';
      secTopAssetEl.onclick = null;
    }
  }
}

// ==========================================================================
// ☀️ 24H Market Pulse & Alpha Briefing Computation & UI
// ==========================================================================

function renderPulseRunnerRow(r, idx) {
  if (!r) return '';
  const rankClass = idx === 0 ? 'rank-1' : (idx === 1 ? 'rank-2' : 'rank-3');
  const rankLabel = '#' + (idx + 1);
  const name = escapeHtml(r.name || 'Unknown');
  const sym = escapeHtml(r.symbol || '');
  const chg = Number(r.price_change_24h || 0);
  const isUp = chg >= 0;
  const chgBadge = isUp ? 'badge-green' : 'badge-red';
  const chgText = (isUp ? '+' : '') + chg.toFixed(1) + '%';
  const price = Number(r.token_price_usd || r.market_price_usd || 0);
  const priceStr = price > 0 ? formatCryptoPrice(price) : '$0.00';
  const mcap = getSanitizedMcap(r);
  const mcapStr = mcap > 0 ? mcapLabel(r) + ': $' + formatCount(mcap) : '';
  const dexUrl = getDexScreenerUrl(r) || ('https://pancakeswap.finance/swap?outputCurrency=' + r.contract);
  const cAddr = escapeHtml(r.contract || '');
  const idAttr = idx === 0 ? ' id="pulseRunnerName"' : '';

  if (r.contract) {
    registerPulseItem(r.contract, r);
  }

  return `<div class="pulse-list-row pulse-runner-row" onclick="openInspectModalByContract('${cAddr}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openInspectModalByContract('${cAddr}');}" role="button" tabindex="0" title="Click to inspect ${name}">
    <div class="pulse-row-left">
      <span class="pulse-rank-tag ${rankClass}">${rankLabel}</span>
      <div class="pulse-row-identity">
        <span class="pulse-row-name"${idAttr}>${name}</span>
        ${sym ? `<span class="pulse-row-sym">${sym}</span>` : ''}
      </div>
    </div>
    <div class="pulse-row-mid">
      <span class="pulse-row-price">${priceStr}</span>
      ${mcapStr ? `<span class="pulse-row-mcap">${mcapStr}</span>` : ''}
    </div>
    <div class="pulse-row-right">
      <span class="badge ${chgBadge} pulse-row-badge">${chgText}</span>
      <a href="${dexUrl}" target="_blank" rel="noopener" class="pulse-mini-dex-btn" title="DexScreener Live Chart" onclick="event.stopPropagation()">📈</a>
    </div>
  </div>`;
}

function renderPulseSmartMintRow(sm, idx) {
  if (!sm || !sm.drop) return '';
  const d = sm.drop;
  const count = sm.count;
  const rankClass = idx === 0 ? 'rank-1' : (idx === 1 ? 'rank-2' : 'rank-3');
  const rankLabel = '#' + (idx + 1);
  const name = escapeHtml(d.name || 'Unknown');
  const sym = escapeHtml(d.symbol || '');
  const cAddr = escapeHtml(d.contract || (d.item && d.item.contract) || '');
  const std = String(d.standard || '');
  const isNft = std.includes('NFT') || std.includes('721');
  const priceStr = d.mint_price_str && d.mint_price_str !== 'TBD' ? d.mint_price_str : (isNft ? 'ERC-721 NFT' : 'BEP-20');
  const subStr = escapeHtml(priceStr);

  if (cAddr) {
    registerPulseItem(cAddr, d);
  }

  return `<div class="pulse-list-row pulse-smart-row" onclick="openInspectModalByContract('${cAddr}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openInspectModalByContract('${cAddr}');}" role="button" tabindex="0" title="Click to inspect ${name}">
    <div class="pulse-row-left">
      <span class="pulse-rank-tag ${rankClass}">${rankLabel}</span>
      <div class="pulse-row-identity">
        <span class="pulse-row-name">${name}</span>
        ${sym ? `<span class="pulse-row-sym">${sym}</span>` : ''}
      </div>
    </div>
    <div class="pulse-row-mid">
      <span class="pulse-row-sub">${subStr}</span>
    </div>
    <div class="pulse-row-right">
      <span class="badge badge-yellow pulse-row-badge">${count} Wallets</span>
      <a href="javascript:void(0)" class="pulse-mini-dex-btn" title="Inspect Smart Minters" onclick="event.stopPropagation(); openInspectModalByContract('${cAddr}')">🧠</a>
    </div>
  </div>`;
}

// Pulse card 2: a NEW token (first seen in the 24h window) that already trades, ranked by 24h volume.
// Same rule as api.pulse_launch_row (the server list is preferred; this is the fallback before /api/stats loads).
const PULSE_LAUNCH_MIN_LP_USD = 1000;
const PULSE_LAUNCH_MIN_VOLUME_USD = 1000;
const PULSE_UNCAPPED_SUPPLY = 1e20;

function isPulseNftRecord(d) {
  const std = String((d && d.standard) || '');
  return /721|1155|NFT/.test(std) || String((d && d.market_url) || '').includes('element.market');
}

function isPulseFlaggedRisky(d) {
  const level = String((d && d.risk_level) || '').toUpperCase();
  const badge = String((d && d.risk_badge) || '').toUpperCase();
  return level === 'RUG' || badge.includes('HONEYPOT');
}

function pulseSmartCount(d) {
  const w = (d && (d.smart_wallets && d.smart_wallets.length ? d.smart_wallets : d.smart_minters)) || [];
  return Array.isArray(w) ? w.length : 0;
}

const PULSE_LAUNCH_MAX_PAIR_AGE_SEC = 3 * 86400;

function pulseLaunchRow(d) {
  if (!d || isPulseNftRecord(d) || d.market_stale || isPulseFlaggedRisky(d)) return null;
  // a DEX pair older than 3 days is not a new launch (detected_at is only when the worker first saw the token)
  const created = Number(d.pair_created_at || 0);
  if (created > 0 && Date.now() / 1000 - created > PULSE_LAUNCH_MAX_PAIR_AGE_SEC) return null;
  const lp = Number(d.lp_usd || 0);
  const vol = Number(d.volume_24h || 0);
  const px = Number(d.token_price_usd || 0);
  if (!(lp >= PULSE_LAUNCH_MIN_LP_USD) || !(vol >= PULSE_LAUNCH_MIN_VOLUME_USD) || !(px > 0)) return null;
  const chg = d.price_change_24h;
  return {
    contract: d.contract, name: d.name || 'Unknown', symbol: d.symbol || '', standard: d.standard || '',
    token_price_usd: px, volume_24h: vol, lp_usd: lp,
    price_change_24h: (chg === null || chg === undefined) ? null : Number(chg),
    market_cap: getSanitizedMcap(d), smart_count: pulseSmartCount(d),
    pair_url: d.pair_url || '', detected_at: d.detected_at
  };
}

// Pulse card 4: a NEW NFT collection, ranked by how many were minted. Same rule as api.pulse_nft_mint_row.
function pulseNftMintRow(d) {
  if (!d || !isPulseNftRecord(d) || isPulseFlaggedRisky(d)) return null;
  const minted = Number(d.total_minted || 0);
  if (!(minted >= 1)) return null;
  const capRaw = Number(d.max_supply || 0);
  const cap = (capRaw > 0 && capRaw < PULSE_UNCAPPED_SUPPLY) ? capRaw : null;
  return {
    contract: d.contract, name: d.name || 'NFT Collection', symbol: d.symbol || '', standard: d.standard || '',
    total_minted: Math.floor(minted), max_supply: cap ? Math.floor(cap) : null,
    fill_pct: cap ? Math.round(Math.min(100, minted / cap * 100) * 10) / 10 : null,
    mint_price_str: d.mint_price_str || '', smart_count: pulseSmartCount(d),
    market_url: d.market_url || '', detected_at: d.detected_at
  };
}

function pulseRankParts(idx) {
  return { rankClass: idx === 0 ? 'rank-1' : (idx === 1 ? 'rank-2' : 'rank-3'), rankLabel: '#' + (idx + 1) };
}

function renderPulseLaunchRow(r, idx) {
  if (!r) return '';
  const { rankClass, rankLabel } = pulseRankParts(idx);
  const name = escapeHtml(r.name || 'Unknown');
  const sym = escapeHtml(r.symbol || '');
  const cAddr = escapeHtml(r.contract || '');
  const priceStr = formatCryptoPrice(Number(r.token_price_usd || 0));
  const volStr = 'Vol: $' + formatCount(Number(r.volume_24h || 0));
  const chg = r.price_change_24h;
  const hasChg = chg !== null && chg !== undefined && isFinite(Number(chg));
  const chgText = hasChg ? ((Number(chg) >= 0 ? '+' : '') + Number(chg).toFixed(1) + '%') : ('LP $' + formatCount(Number(r.lp_usd || 0)));
  const chgBadge = hasChg ? (Number(chg) >= 0 ? 'badge-green' : 'badge-red') : 'badge-dim';
  const dexUrl = getDexScreenerUrl(r) || r.pair_url || ('https://pancakeswap.finance/swap?outputCurrency=' + (r.contract || ''));
  if (r.contract) registerPulseItem(r.contract, r);
  return `<div class="pulse-list-row pulse-ath-row" onclick="openInspectModalByContract('${cAddr}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openInspectModalByContract('${cAddr}');}" role="button" tabindex="0" title="Click to inspect ${name} (${volStr}, liquidity $${formatCount(Number(r.lp_usd || 0))})">
    <div class="pulse-row-left">
      <span class="pulse-rank-tag ${rankClass}">${rankLabel}</span>
      <div class="pulse-row-identity">
        <span class="pulse-row-name">${name}</span>
        ${sym ? `<span class="pulse-row-sym">${sym}</span>` : ''}
      </div>
    </div>
    <div class="pulse-row-mid">
      <span class="pulse-row-price">${priceStr}</span>
      <span class="pulse-row-mcap">${volStr}</span>
    </div>
    <div class="pulse-row-right">
      <span class="badge ${chgBadge} pulse-row-badge" title="24h price change">${chgText}</span>
      <a href="${dexUrl}" target="_blank" rel="noopener" class="pulse-mini-dex-btn" title="DexScreener Live Chart" onclick="event.stopPropagation()">📈</a>
    </div>
  </div>`;
}

function renderPulseNftMintRow(n, idx) {
  if (!n) return '';
  const { rankClass, rankLabel } = pulseRankParts(idx);
  const name = escapeHtml(n.name || 'NFT Collection');
  const sym = escapeHtml(n.symbol || '');
  const cAddr = escapeHtml(n.contract || '');
  const minted = Number(n.total_minted || 0);
  const mintedStr = n.max_supply ? `${formatCount(minted)} / ${formatCount(Number(n.max_supply))} minted` : `${formatCount(minted)} minted`;
  const priceLabel = escapeHtml(n.mint_price_str ? String(n.mint_price_str).replace(/^⭐\s*/, '') : 'Mint: TBD');
  const fill = n.fill_pct;
  const hasFill = fill !== null && fill !== undefined;
  const badgeText = hasFill ? (fill >= 100 ? 'Sold out' : `${Number(fill).toFixed(fill >= 10 ? 0 : 1)}% filled`) : `${formatCount(minted)} mints`;
  const badgeClass = hasFill && fill >= 100 ? 'badge-yellow' : 'badge-magenta';
  const smart = Number(n.smart_count || 0);
  const smartStr = smart > 0 ? ` · 🧠 ${smart}` : '';
  const elementUrl = n.market_url || ('https://element.market/collections/' + (n.contract || ''));
  if (n.contract) registerPulseItem(n.contract, n);
  return `<div class="pulse-list-row pulse-nft-row" onclick="openInspectModalByContract('${cAddr}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openInspectModalByContract('${cAddr}');}" role="button" tabindex="0" title="Click to inspect ${name} (${mintedStr})">
    <div class="pulse-row-left">
      <span class="pulse-rank-tag ${rankClass}">${rankLabel}</span>
      <div class="pulse-row-identity">
        <span class="pulse-row-name">${name}</span>
        ${sym ? `<span class="pulse-row-sym">${sym}</span>` : ''}
      </div>
    </div>
    <div class="pulse-row-mid">
      <span class="pulse-row-price" style="color: #f472b6;">${mintedStr}</span>
      <span class="pulse-row-mcap">${priceLabel}${smartStr}</span>
    </div>
    <div class="pulse-row-right">
      <span class="badge ${badgeClass} pulse-row-badge" title="Share of the supply minted">${badgeText}</span>
      <a href="${elementUrl}" target="_blank" rel="noopener" class="pulse-mini-dex-btn" title="View Collection on Element Market" onclick="event.stopPropagation()">🎨</a>
    </div>
  </div>`;
}

// `currentBlock` is kept for callers but no longer used: the 24h window is a timestamp (the server's
// pulse_24h.since, compared with each record's detected_at), not a block count (28,800 blocks is ~3.6 h at
// BSC's current block time). Aggregates (graduated, DEX volume, smart inflows, ...) come from the server
// (`serverPulse`); computed from a partial list they were wrong, so without them they are reported as local-only.
function compute24hPulseMetrics(drops = allDrops, topGainersList = savedTopGainers, currentBlock = latestKnownBlock, serverPulse = pulse24hServer) {
  const list = Array.isArray(drops) ? drops : [];
  const topList = Array.isArray(topGainersList) ? topGainersList : [];

  // Candidates for Top 24h Runners
  const pool = [...list, ...topList.map(g => (g && g.item) ? g.item : g)].filter(Boolean);
  const seenContracts = new Set();
  const uniquePool = [];
  pool.forEach(item => {
    if (isExcludedMajorAsset(item)) return;
    const c = String(item.contract || '').toLowerCase();
    if (c && !seenContracts.has(c)) {
      seenContracts.add(c);
      uniquePool.push(item);
    }
  });

  // Top 3 Runners: prioritize positive 24h momentum with volume, or leading secondary market assets
  const runnerCandidates = [];
  // Runners are LIVE 24h movers: a saved leaderboard / frozen seed entry carries no 24h data and must not stand in
  // for one (a seed's old price and market cap showed up as "#3 runner +0.0%")
  const liveContracts = new Set(list.map(d => String((d && d.contract) || '').toLowerCase()));
  const isLive = (d) => liveContracts.has(String((d && d.contract) || '').toLowerCase());
  uniquePool.forEach(d => {
    if (isExcludedMajorAsset(d) || !isLive(d)) return;
    const chg = Number(d.price_change_24h || 0);
    const mcap = getSanitizedMcap(d);
    const vol = Number(d.volume_24h || 0);
    const lp = Number(d.lp_usd || 0);
    const tkUsd = Number(d.token_price_usd || d.market_price_usd || 0);
    // Strict Runner Quality Guard: Must have genuine market cap, real non-zero price, and active liquidity
    if (mcap < 500 || tkUsd <= 1e-8 || lp < 500 || vol < 200) return;

    const rLvl = String(d.risk_level || '').toUpperCase();
    const rBdg = String(d.risk_badge || '').toUpperCase();
    if (rLvl === 'RUG' || rBdg.includes('HONEYPOT')) return;

    let score = 0;
    if (chg > 0) {
      score = 100000000 + (chg * 10000) + Math.min(vol, 500000);
    } else {
      score = mcap + (vol * 2);
    }

    runnerCandidates.push({ item: d, score });
  });

  runnerCandidates.sort((a, b) => b.score - a.score);
  // The server ranks every live token with the same rule (pulse_24h.top_runners), so the card no longer depends on
  // which part of the dataset this page happens to have loaded (it used to need the 120 KB secondary preload).
  const serverRunners = serverPulse && Array.isArray(serverPulse.top_runners) ? serverPulse.top_runners : null;
  let topRunners = serverRunners
    ? serverRunners.filter(r => r && !isExcludedMajorAsset(r)).slice(0, 3)
    : runnerCandidates.slice(0, 3).map(x => x.item);

  // If fewer than 3 candidates, fill with secondary market assets by market cap
  if (topRunners.length < 3) {
    const fallbackAssets = uniquePool.filter(d => !isExcludedMajorAsset(d) && isLive(d) && !topRunners.some(r => r.contract === d.contract));
    fallbackAssets.sort((a, b) => (getSanitizedMcap(b) || Number(b.lp_usd || 0)) - (getSanitizedMcap(a) || Number(a.lp_usd || 0)));
    for (const fb of fallbackAssets) {
      if (topRunners.length >= 3) break;
      topRunners.push(fb);
    }
  }

  const topRunner = topRunners[0] || null;

  // 24h window: records detected since the server's cutoff ("YYYY-MM-DD HH:MM:SS", same clock as detected_at)
  const since = serverPulse && serverPulse.since ? String(serverPulse.since) : null;
  const inWindow = (d) => !since || String((d && d.detected_at) || '') >= since;

  // Card 3: Top 3 Mints with the biggest amount of smart wallets involved (Past 24h Window)
  const smartMintCandidates = [];
  const seenSmartContracts = new Set();
  list.forEach(d => {
    if (isExcludedMajorAsset(d)) return;
    const c = String(d.contract || '').toLowerCase();
    if (c && !seenSmartContracts.has(c)) {
      if (!inWindow(d)) return;

      const smList = (d.smart_wallets && d.smart_wallets.length > 0)
        ? d.smart_wallets
        : (d.smart_minters && d.smart_minters.length > 0 ? d.smart_minters : []);
      const count = Number(d.smart_wallets_count || smList.length || 0);
      if (count > 0) {
        seenSmartContracts.add(c);
        smartMintCandidates.push({ item: d, count });
      }
    }
  });

  smartMintCandidates.sort((a, b) => b.count - a.count);
  let topSmartMints = smartMintCandidates.slice(0, 3).map(x => ({
    drop: x.item,
    count: x.count
  }));

  // Fallback if test fixture has no block properties or very few 24h items
  if (topSmartMints.length < 3) {
    list.forEach(d => {
      if (topSmartMints.length >= 3) return;
      if (isExcludedMajorAsset(d)) return;
      const c = String(d.contract || '').toLowerCase();
      if (!seenSmartContracts.has(c)) {
        const smList = (d.smart_wallets && d.smart_wallets.length > 0)
          ? d.smart_wallets
          : (d.smart_minters && d.smart_minters.length > 0 ? d.smart_minters : []);
        const count = Number(d.smart_wallets_count || smList.length || 0);
        if (count > 0) {
          seenSmartContracts.add(c);
          topSmartMints.push({ drop: d, count });
        }
      }
    });
  }

  // Card 2: Top 3 NEW tokens (first seen in the 24h window) that already trade, by 24h volume. The server list covers
  // the full dataset; before /api/stats loads, the loaded records are ranked with the same rule. A token card 1
  // already shows is skipped, so the two cards never repeat each other.
  const lowerC = (d) => String((d && d.contract) || '').toLowerCase();
  const runnerContracts = new Set(topRunners.map(lowerC));
  const serverLaunches = serverPulse && Array.isArray(serverPulse.top_launches) ? serverPulse.top_launches : null;
  const launchPool = serverLaunches || list.filter(inWindow).map(pulseLaunchRow).filter(Boolean)
    .sort((a, b) => b.volume_24h - a.volume_24h);
  const topLaunches = launchPool.filter(r => r && !runnerContracts.has(lowerC(r)) && !isExcludedMajorAsset(r)).slice(0, 3);

  // Card 4: Top 3 NEW NFT collections by mints (server list over the full dataset, same fallback rule).
  const serverNftMints = serverPulse && Array.isArray(serverPulse.top_nft_mints) ? serverPulse.top_nft_mints : null;
  const topNftMints = (serverNftMints || list.filter(inWindow).map(pulseNftMintRow).filter(Boolean)
    .sort((a, b) => (b.total_minted - a.total_minted) || (b.smart_count - a.smart_count)))
    .filter(n => n && !isExcludedMajorAsset(n)).slice(0, 3);

  // Graduations & Volume
  let gradCount = 0;
  let totalDexVol = 0;
  let launchpadCount = 0;

  // Smart Money
  let totalSmartInflows = 0;
  const hunterCounts = new Map();
  let clustersCount = 0;

  // Security
  let cleanMintsCount = 0;
  let refundsCount = 0;
  let threatsBlockedCount = 0;

  // Track the primary secondary market anchor asset (highest genuine mcap with liquidity)
  let anchorAsset = null;
  let maxAnchorMcap = 0;

  list.forEach(d => {
    if (isExcludedMajorAsset(d)) return;

    const isSec = isSecondaryMarketAsset(d);
    if (isSec || d.is_graduated) gradCount++;
    if (isLaunchpadDrop(d)) launchpadCount++;

    const vol = Number(d.volume_24h || 0);
    totalDexVol += vol;

    const smList = (d.smart_wallets && d.smart_wallets.length > 0)
      ? d.smart_wallets
      : (d.smart_minters && d.smart_minters.length > 0 ? d.smart_minters : []);
    if (smList.length > 0) {
      totalSmartInflows += smList.length;
      if (smList.length >= 3) clustersCount++;
      smList.forEach(w => {
        const key = String(w).toLowerCase();
        hunterCounts.set(key, (hunterCounts.get(key) || 0) + 1);
      });
    }

    const rLvl = String(d.risk_level || 'SAFE').toUpperCase();
    const rBdg = String(d.risk_badge || '').toUpperCase();
    if (rBdg.includes('HONEYPOT') || rBdg.includes('SERIAL') || rBdg.includes('DRAIN') || rLvl === 'RUG' || rLvl === 'HIGH') {
      threatsBlockedCount++;
    } else {
      cleanMintsCount++;
    }

    if (d.security && d.security.has_refund) {
      refundsCount++;
    }

    const sMcap = getSanitizedMcap(d);
    if (sMcap > maxAnchorMcap && Number(d.lp_usd || 0) >= 50000) {
      maxAnchorMcap = sMcap;
      anchorAsset = d;
    }
  });

  // Top Smart Hunter
  let topHunter = 'Alpha Hunter';
  let topHunterCount = 0;
  hunterCounts.forEach((cnt, w) => {
    if (cnt > topHunterCount) {
      topHunterCount = cnt;
      topHunter = w;
    }
  });

  // Dynamic 1-sentence macro synthesis connecting all 4 cards
  let macroHeadline = 'Analyzing 24h rolling activity across fair-mints, secondary trading pairs, and smart hunter capital...';
  const headlineParts = [];
  if (topRunner) {
    const rName = topRunner.name || topRunner.symbol || 'Leading asset';
    const rChg = Number(topRunner.price_change_24h || 0);
    if (rChg > 0) {
      headlineParts.push(`${rName} leads 24h momentum (+${rChg.toFixed(1)}%)`);
    } else {
      const rMcap = getSanitizedMcap(topRunner);
      headlineParts.push(`${rName} anchors secondary valuation at $${formatCount(rMcap)}`);
    }
  }

  if (topLaunches.length > 0) {
    headlineParts.push(`${topLaunches[0].name} leads new launches ($${formatCount(topLaunches[0].volume_24h)} 24h volume)`);
  }

  if (topSmartMints && topSmartMints.length > 0) {
    headlineParts.push(`${topSmartMints[0].drop.name || topSmartMints[0].drop.symbol} leads smart alpha (${topSmartMints[0].count} wallets)`);
  }

  if (topNftMints.length > 0) {
    headlineParts.push(`${topNftMints[0].name} leads NFT mints (${formatCount(topNftMints[0].total_minted)} minted)`);
  }

  if (headlineParts.length > 0) {
    macroHeadline = headlineParts.join(' • ') + '.';
  }

  const fromServer = Boolean(serverPulse && typeof serverPulse === 'object');
  const agg = fromServer ? {
    gradCount: Number(serverPulse.graduated || 0),
    totalDexVol: Number(serverPulse.dex_volume_usd || 0),
    totalSmartInflows: Number(serverPulse.smart_inflows || 0),
    topHunter: serverPulse.top_hunter || 'Alpha Hunter',
    topHunterCount: Number(serverPulse.top_hunter_count || 0),
    clustersCount: Number(serverPulse.smart_clusters || 0),
    cleanMintsCount: Number(serverPulse.clean_mints || 0),
    refundsCount: Number(serverPulse.refunds || 0),
    threatsBlockedCount: Number(serverPulse.threats_blocked || 0)
  } : { gradCount, totalDexVol, totalSmartInflows, topHunter, topHunterCount, clustersCount, cleanMintsCount, refundsCount, threatsBlockedCount };

  return {
    topRunner,
    topRunners,
    topLaunches,
    topSmartMints,
    topNftMints,
    anchorAsset,
    launchpadCount,
    ...agg,
    aggregatesSource: fromServer ? 'server' : 'local',   // 'local' = from the loaded page only: not a 24h number
    macroHeadline
  };
}

function updateDailyPulseUI() {
  if (typeof document === 'undefined') return;
  const hero = document.getElementById('dailyPulseHero');
  if (!hero) return;

  const metrics = compute24hPulseMetrics();

  // 1. Top 24h Runners Card (Top 3)
  const runnersListEl = document.getElementById('pulseRunnersList');
  if (runnersListEl) {
    if (metrics.topRunners && metrics.topRunners.length > 0) {
      runnersListEl.innerHTML = metrics.topRunners.map((r, i) => renderPulseRunnerRow(r, i)).join('');
    } else {
      runnersListEl.innerHTML = '<div class="pulse-empty-note">No active runners in 24h window</div>';
    }
  }

  // Backward compatibility for existing single runner references / unit test mocks
  const runner = metrics.topRunner;
  const rNameEl = document.getElementById('pulseRunnerName');
  const rSymEl = document.getElementById('pulseRunnerSym');
  const rChgEl = document.getElementById('pulseRunnerChg');
  const rPriceEl = document.getElementById('pulseRunnerPrice');
  const rMcapEl = document.getElementById('pulseRunnerMcap');
  const rDexLink = document.getElementById('pulseRunnerDexLink');

  if (runner) {
    if (rNameEl) {
      rNameEl.textContent = runner.name || 'Unknown';
      rNameEl.onclick = () => openModal(runner);
    }
    if (rSymEl) rSymEl.textContent = runner.symbol ? `(${runner.symbol})` : '';

    const chg = Number(runner.price_change_24h || 0);
    if (rChgEl) {
      const isUp = chg >= 0;
      rChgEl.className = `badge ${isUp ? 'badge-green' : 'badge-red'} pulse-card-badge`;
      rChgEl.textContent = `${isUp ? '+' : ''}${chg.toFixed(1)}%`;
    }

    const tkPrice = Number(runner.token_price_usd || runner.market_price_usd || 0);
    if (rPriceEl) {
      rPriceEl.textContent = tkPrice > 0 ? formatCryptoPrice(tkPrice) : '—';
    }

    const mcap = getSanitizedMcap(runner);
    if (rMcapEl) {
      rMcapEl.textContent = mcap > 0 ? `$${formatCount(mcap)}` : '—';
    }

    const dexUrl = getDexScreenerUrl(runner);
    if (rDexLink) {
      if (dexUrl) {
        rDexLink.href = dexUrl;
        rDexLink.style.display = 'inline-flex';
      } else {
        rDexLink.href = `https://pancakeswap.finance/swap?outputCurrency=${runner.contract}`;
      }
    }
  }

  // 2. Top 24h New Launches (Top 3)
  const athListEl = document.getElementById('pulseAthList');
  if (athListEl) {
    if (metrics.topLaunches && metrics.topLaunches.length > 0) {
      athListEl.innerHTML = metrics.topLaunches.map((r, i) => renderPulseLaunchRow(r, i)).join('');
    } else {
      athListEl.innerHTML = '<div class="pulse-empty-note">No new token is trading yet in the past 24h</div>';
    }
  }

  // Backward compatibility for existing grad/volume element references
  const gradCountEl = document.getElementById('pulseGradCount');
  const volEl = document.getElementById('pulse24hVol');
  const launchEl = document.getElementById('pulseLaunchpadCount');
  const serverAgg = metrics.aggregatesSource === 'server';
  const aggText = (v) => (serverAgg ? v : '—');
  if (gradCountEl) gradCountEl.textContent = aggText(metrics.gradCount);
  if (volEl) volEl.textContent = serverAgg ? `$${formatCount(metrics.totalDexVol)}` : '—';
  if (launchEl) launchEl.textContent = metrics.launchpadCount;

  // 3. Top Smart Wallet Mints (Top 3)
  const smartListEl = document.getElementById('pulseSmartMintsList');
  if (smartListEl) {
    if (metrics.topSmartMints && metrics.topSmartMints.length > 0) {
      smartListEl.innerHTML = metrics.topSmartMints.map((sm, i) => renderPulseSmartMintRow(sm, i)).join('');
    } else {
      smartListEl.innerHTML = '<div class="pulse-empty-note">No smart wallet mint activity detected</div>';
    }
  }

  const smartInflowsEl = document.getElementById('pulseSmartInflows');
  const topHunterEl = document.getElementById('pulseTopHunter');
  const clustersEl = document.getElementById('pulseClustersCount');
  if (smartInflowsEl) smartInflowsEl.textContent = aggText(metrics.totalSmartInflows);
  if (topHunterEl) {
    const hunterDisplay = metrics.topHunter.length > 14 ? `${metrics.topHunter.slice(0, 6)}...${metrics.topHunter.slice(-4)}` : metrics.topHunter;
    topHunterEl.textContent = hunterDisplay;
    topHunterEl.title = metrics.topHunter;
  }
  if (clustersEl) clustersEl.textContent = aggText(metrics.clustersCount);

  // 4. Top 24h NFT Mints (Top 3)
  const nftListEl = document.getElementById('pulseNftSurgesList');
  if (nftListEl) {
    if (metrics.topNftMints && metrics.topNftMints.length > 0) {
      nftListEl.innerHTML = metrics.topNftMints.map((n, i) => renderPulseNftMintRow(n, i)).join('');
    } else {
      nftListEl.innerHTML = '<div class="pulse-empty-note">No NFT collection minted in the past 24h</div>';
    }
  }

  // Backward compatibility for security element references
  const cleanMintsEl = document.getElementById('pulseCleanMints');
  const refundEl = document.getElementById('pulseRefundCount');
  const threatsEl = document.getElementById('pulseThreatsBlocked');
  if (cleanMintsEl) cleanMintsEl.textContent = aggText(metrics.cleanMintsCount);
  if (refundEl) refundEl.textContent = aggText(metrics.refundsCount);
  if (threatsEl) threatsEl.textContent = aggText(metrics.threatsBlockedCount);

  // 5. Headline Synthesis
  const headlineEl = document.getElementById('pulseMacroHeadline');
  if (headlineEl) headlineEl.textContent = metrics.macroHeadline;

  // 6. Collapsed Ticker Bar
  const tickerRunner = document.getElementById('tickerTopRunner');
  const tickerVolume = document.getElementById('tickerVolume');
  const tickerGrad = document.getElementById('tickerGraduated');
  const tickerInflow = document.getElementById('tickerSmartInflow');

  if (tickerRunner && metrics.topRunners && metrics.topRunners.length > 0) {
    const r1 = metrics.topRunners[0];
    const chg = Number(r1.price_change_24h || 0);
    tickerRunner.textContent = `🏆 Top: ${r1.name || r1.symbol} (${chg >= 0 ? '+' : ''}${chg.toFixed(1)}%)`;
  }
  if (tickerVolume) tickerVolume.textContent = `🌊 DEX Vol: ${serverAgg ? '$' + formatCount(metrics.totalDexVol) : '—'}`;
  if (tickerGrad) tickerGrad.textContent = `🚀 ${serverAgg ? metrics.gradCount : '—'} Graduated`;
  if (tickerInflow) {
    if (metrics.topSmartMints && metrics.topSmartMints.length > 0) {
      const sm1 = metrics.topSmartMints[0];
      tickerInflow.textContent = `🧠 Top Mint: ${sm1.drop.name || sm1.drop.symbol} (${sm1.count} Wallets)`;
    } else {
      tickerInflow.textContent = `🧠 ${aggText(metrics.totalSmartInflows)} Smart Inflows`;
    }
  }
}

function toggleDailyPulseHero() {
  if (typeof document === 'undefined') return;
  const hero = document.getElementById('dailyPulseHero');
  const icon = document.getElementById('dailyPulseToggleIcon');
  const text = document.getElementById('dailyPulseToggleText');
  const btn = document.getElementById('dailyPulseToggleBtn');
  if (!hero) return;

  const isCollapsed = hero.classList && (hero.classList.contains ? hero.classList.contains('collapsed') : (hero.classList.has ? hero.classList.has('collapsed') : false));
  if (isCollapsed) {
    if (hero.classList.remove) hero.classList.remove('collapsed');
    else if (hero.classList.delete) hero.classList.delete('collapsed');
    if (icon) icon.textContent = '▾';
    if (text) text.textContent = 'Collapse';
    if (btn) btn.setAttribute('aria-expanded', 'true');
  } else {
    hero.classList.add('collapsed');
    if (icon) icon.textContent = '▸';
    if (text) text.textContent = 'Expand';
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }

  try {
    localStorage.setItem('agent_id_pulse_collapsed', !isCollapsed ? '1' : '0');
  } catch (e) {}
}

function initDailyPulseHero() {
  if (typeof document === 'undefined') return;
  const hero = document.getElementById('dailyPulseHero');
  const icon = document.getElementById('dailyPulseToggleIcon');
  const text = document.getElementById('dailyPulseToggleText');
  const btn = document.getElementById('dailyPulseToggleBtn');
  if (!hero) return;

  let savedState = null;
  try {
    savedState = localStorage.getItem('agent_id_pulse_collapsed');
  } catch (e) {}

  if (savedState === '1') {
    hero.classList.add('collapsed');
    if (icon) icon.textContent = '▸';
    if (text) text.textContent = 'Expand';
    if (btn) btn.setAttribute('aria-expanded', 'false');
  } else {
    if (hero.classList.remove) hero.classList.remove('collapsed');
    else if (hero.classList.delete) hero.classList.delete('collapsed');
    if (icon) icon.textContent = '▾';
    if (text) text.textContent = 'Collapse';
    if (btn) btn.setAttribute('aria-expanded', 'true');
  }

  updateDailyPulseUI();
  setupPulseCarousel();
}

function updateActivePulseDot(activeIdx) {
  if (typeof document === 'undefined') return;
  const dots = document.querySelectorAll('#pulseCarouselDots .pulse-carousel-pill, #pulseCarouselDots .pulse-dot');
  if (!dots || !dots.length) return;
  dots.forEach((dot, idx) => {
    if (idx === activeIdx) {
      dot.classList.add('active');
      dot.setAttribute('aria-current', 'true');
    } else {
      dot.classList.remove('active');
      dot.removeAttribute('aria-current');
    }
  });
}

function scrollPulseCarousel(dir) {
  if (typeof document === 'undefined') return;
  const grid = document.getElementById('dailyPulseGrid') || document.querySelector('.daily-pulse-grid');
  if (!grid) return;
  const cards = grid.querySelectorAll('.pulse-card');
  if (!cards || !cards.length) return;

  const scrollLeft = grid.scrollLeft;
  const cardWidth = (cards[0].offsetWidth || 300) + 12;
  const currentIdx = Math.round(scrollLeft / cardWidth);
  const targetIdx = Math.max(0, Math.min(cards.length - 1, currentIdx + dir));

  if (cards[targetIdx] && typeof cards[targetIdx].scrollIntoView === 'function') {
    cards[targetIdx].scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
  } else if (typeof grid.scrollTo === 'function') {
    grid.scrollTo({ left: targetIdx * cardWidth, behavior: 'smooth' });
  }
  updateActivePulseDot(targetIdx);
}

function setupPulseCarousel() {
  if (typeof document === 'undefined') return;
  const grid = document.getElementById('dailyPulseGrid') || document.querySelector('.daily-pulse-grid');
  const dots = document.querySelectorAll('#pulseCarouselDots .pulse-carousel-pill, #pulseCarouselDots .pulse-dot');
  if (!grid) return;

  // Dot click navigation
  if (dots && dots.length) {
    dots.forEach((dot) => {
      if (dot._hasCarouselListener) return;
      dot._hasCarouselListener = true;
      dot.addEventListener('click', () => {
        const idx = parseInt(dot.getAttribute('data-index'), 10);
        if (isNaN(idx)) return;
        const cards = grid.querySelectorAll('.pulse-card');
        if (cards[idx]) {
          if (typeof cards[idx].scrollIntoView === 'function') {
            cards[idx].scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
          } else if (typeof grid.scrollTo === 'function') {
            const cardWidth = (cards[0].offsetWidth || 300) + 12;
            grid.scrollTo({ left: idx * cardWidth, behavior: 'smooth' });
          }
          updateActivePulseDot(idx);
        }
      });
    });
  }

  // Real-time scroll indicator update with requestAnimationFrame
  if (!grid._hasScrollSnapListener) {
    grid._hasScrollSnapListener = true;
    let isTicking = false;
    grid.addEventListener('scroll', () => {
      if (!isTicking) {
        if (typeof window !== 'undefined' && window.requestAnimationFrame) {
          window.requestAnimationFrame(() => {
            const cards = grid.querySelectorAll('.pulse-card');
            if (cards && cards.length) {
              const cardWidth = (cards[0].offsetWidth || 300) + 12;
              const activeIdx = Math.max(0, Math.min(cards.length - 1, Math.round(grid.scrollLeft / cardWidth)));
              updateActivePulseDot(activeIdx);
            }
            isTicking = false;
          });
        } else {
          const cards = grid.querySelectorAll('.pulse-card');
          if (cards && cards.length) {
            const cardWidth = (cards[0].offsetWidth || 300) + 12;
            const activeIdx = Math.max(0, Math.min(cards.length - 1, Math.round(grid.scrollLeft / cardWidth)));
            updateActivePulseDot(activeIdx);
          }
          isTicking = false;
        }
        isTicking = true;
      }
    }, { passive: true });

    // Modern scrollsnapchange support
    if ('onscrollsnapchange' in grid) {
      grid.addEventListener('scrollsnapchange', () => {
        const cards = grid.querySelectorAll('.pulse-card');
        if (cards && cards.length) {
          const cardWidth = (cards[0].offsetWidth || 300) + 12;
          const activeIdx = Math.max(0, Math.min(cards.length - 1, Math.round(grid.scrollLeft / cardWidth)));
          updateActivePulseDot(activeIdx);
        }
      });
    }
  }
}

if (typeof window !== 'undefined') {
  window.compute24hPulseMetrics = compute24hPulseMetrics;
  window.updateDailyPulseUI = updateDailyPulseUI;
  window.toggleDailyPulseHero = toggleDailyPulseHero;
  window.initDailyPulseHero = initDailyPulseHero;
  window.isExcludedMajorAsset = isExcludedMajorAsset;
  window.getSanitizedMcap = getSanitizedMcap;
  window.renderPulseRunnerRow = renderPulseRunnerRow;
  window.renderPulseSmartMintRow = renderPulseSmartMintRow;
  window.renderPulseLaunchRow = renderPulseLaunchRow;
  window.renderPulseNftMintRow = renderPulseNftMintRow;
  window.setupPulseCarousel = setupPulseCarousel;
  window.scrollPulseCarousel = scrollPulseCarousel;
  window.updateActivePulseDot = updateActivePulseDot;
}

// ABI pad32 helper for PancakeSwap V2 Router Calldata
function pad32(val) {
  if (typeof val === 'number' || typeof val === 'bigint') {
    return BigInt(val).toString(16).padStart(64, '0');
  }
  if (typeof val === 'string') {
    const v = val.startsWith('0x') ? val.slice(2) : val;
    return v.toLowerCase().padStart(64, '0');
  }
  return '0'.repeat(64);
}

// 1-Click PancakeSwap V2 Sell Calldata Generator (Selector: 0x791ac947)
function buildPancakeSwapSellCalldata(tokenAddr, amountWei, recipientWallet, minOutWei = 0, deadline = 0xffffffff) {
  const selector = '791ac947';
  const p0 = pad32(amountWei);
  const p1 = pad32(minOutWei);
  const p2 = pad32(160); // 0xa0 offset to path array
  const p3 = pad32(recipientWallet);
  const p4 = pad32(deadline);
  const p5 = pad32(2);   // path length: [token, WBNB]
  const p6 = pad32(tokenAddr);
  const p7 = pad32(WBNB_ADDRESS);
  return `0x${selector}${p0}${p1}${p2}${p3}${p4}${p5}${p6}${p7}`;
}

// Canonical Multicall3 Contract Address on BSC (same address across all EVM chains)
const MULTICALL3_ADDRESS = '0xcA11bde05977b3631167028862bE2a173976CA11';

// ABI Encoder for Multicall3.aggregate3(Call3[] calls) (Selector: 0x82ad56cb)
function encodeMulticall3Aggregate3(calls) {
  const selector = '82ad56cb';
  const offsetCalls = pad32(32);
  const countCalls = pad32(calls.length);
  const structOffsets = [];
  const structBodies = [];
  let currentOffset = calls.length * 32;

  for (let i = 0; i < calls.length; i++) {
    const { target, allowFailure, callData } = calls[i];
    structOffsets.push(pad32(currentOffset));
    const cBytes = (callData.startsWith('0x') ? callData.slice(2) : callData);
    const cLen = Math.floor(cBytes.length / 2);
    const padLen = (cLen % 32 !== 0) ? (32 - (cLen % 32)) : 0;
    const paddedCBytes = cBytes + '00'.repeat(padLen);

    const body = pad32(target) + pad32(allowFailure ? 1 : 0) + pad32(96) + pad32(cLen) + paddedCBytes;
    structBodies.push(body);
    currentOffset += Math.floor(body.length / 2);
  }

  return `0x${selector}${offsetCalls}${countCalls}${structOffsets.join('')}${structBodies.join('')}`;
}

// ABI Decoder for Multicall3.aggregate3 returns (Result[] returnData)
function decodeMulticall3Aggregate3(hexStr) {
  if (!hexStr || hexStr === '0x') return [];
  const clean = hexStr.startsWith('0x') ? hexStr.slice(2) : hexStr;
  if (clean.length < 128) return [];

  const count = parseInt(clean.slice(64, 128), 16);
  if (isNaN(count) || count <= 0) return [];

  const results = [];
  for (let i = 0; i < count; i++) {
    const offsetHex = clean.slice(128 + i * 64, 128 + (i + 1) * 64);
    const relOffset = parseInt(offsetHex, 16);
    const itemCharIndex = 128 + relOffset * 2;

    const successHex = clean.slice(itemCharIndex, itemCharIndex + 64);
    const success = (parseInt(successHex, 16) === 1);

    const dataOffsetHex = clean.slice(itemCharIndex + 64, itemCharIndex + 128);
    const dataOffset = parseInt(dataOffsetHex, 16);

    const dataLenCharIndex = itemCharIndex + dataOffset * 2;
    const dataLenHex = clean.slice(dataLenCharIndex, dataLenCharIndex + 64);
    const dataLen = parseInt(dataLenHex, 16);

    const returnDataHex = clean.slice(dataLenCharIndex + 64, dataLenCharIndex + 64 + dataLen * 2);
    results.push({
      success,
      returnData: returnDataHex ? `0x${returnDataHex}` : '0x'
    });
  }
  return results;
}

// Evaluate Take-Profit / Stop-Loss Exit Conviction for Portfolio Holdings
function evaluatePortfolioExit(item, bnbPriceUsd = bnbPrice) {
  const contract = (item.contract || '').toLowerCase();
  const std = String(item.standard || '');
  const isNft = std.includes('721') || std.includes('NFT') || Boolean(item.is_nft);

  if (contract === '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee' || item.symbol === 'BNB') {
    return {
      signal: '⛽ GAS RESERVE',
      badge: '⛽ GAS RESERVE',
      badge_class: 'badge-cyan',
      action: 'KEEP',
      action_label: 'Gas Reserve',
      multiple: 1.0,
      pnl_usd: 0.0,
      pnl_pct: 0.0,
      recommendation: 'Binance Agentic Wallet operational gas reserve. Keep funded for transaction fees.',
      can_swap: false
    };
  }

  if (isNft) {
    const isSurge = Boolean(item.is_nft_surge);
    const salesCnt = Number(item.secondary_sales_count || 0);
    const flr = Number(item.floor_price_bnb || 0);

    if (isSurge) {
      return {
        signal: '🦋 ELEMENT SURGE',
        badge: `🦋 SURGE (${salesCnt} sales)`,
        badge_class: 'badge-magenta',
        action: 'LIST_ELEMENT',
        action_label: 'Element Surge',
        multiple: 1.0,
        pnl_usd: 0.0,
        pnl_pct: 0.0,
        recommendation: `Element volume spike! ${salesCnt} secondary sales (Floor: ${formatBnb(flr)} BNB). List or sell manually on Element.`,
        can_swap: false
      };
    }

    return {
      signal: '🎨 LIST ON ELEMENT',
      badge: '🎨 LIST ON ELEMENT',
      badge_class: 'badge-magenta',
      action: 'LIST_ELEMENT',
      action_label: 'List on Element',
      multiple: 1.0,
      pnl_usd: 0.0,
      pnl_pct: 0.0,
      recommendation: 'NFTs trade on Element Market. AMMs like PancakeSwap do not support ERC-721.',
      can_swap: false
    };
  }

  const bal = Number(item.balance || item.user_balance || 1);
  let curPrice = Number(item.price_usd || item.token_price_usd || item.market_price_usd || 0.0);
  let curVal = Number(item.value_usd || 0.0);
  if (curVal <= 0 && curPrice > 0) curVal = bal * curPrice;

  const mintBnb = Number(item.mint_price_bnb || 0.0);
  if (mintBnb > 0 && !(Number(bnbPriceUsd) > 0)) {
    // the cost basis is in BNB and there is no BNB/USD price: a made-up one would fake the profit signal
    return {
      signal: '—',
      badge: '—',
      badge_class: 'badge-dim',
      action: 'HOLD',
      action_label: 'Holding',
      multiple: 1.0,
      pnl_usd: 0.0,
      pnl_pct: 0.0,
      recommendation: 'BNB/USD price unavailable: profit and exit signals are not computed.',
      can_swap: true
    };
  }
  const costUsd = mintBnb * bnbPriceUsd;

  let peakPrice = Number(item.peak_price_usd || 0.0);
  if (curPrice > peakPrice) {
    peakPrice = curPrice;
    item.peak_price_usd = peakPrice;
  }

  let peakVal = Number(item.peak_val_usd || 0.0);
  if (curVal > peakVal) {
    peakVal = curVal;
    item.peak_val_usd = peakVal;
  }

  // Trailing Stop Check (-40% crash)
  let isCrash = false;
  let drawdownPct = 0.0;
  if (peakVal > 0 && curVal > 0 && curVal < peakVal) {
    drawdownPct = (peakVal - curVal) / peakVal;
    if (drawdownPct >= 0.40) isCrash = true;
  } else if (peakPrice > 0 && curPrice > 0 && curPrice < peakPrice) {
    drawdownPct = (peakPrice - curPrice) / peakPrice;
    if (drawdownPct >= 0.40) isCrash = true;
  }

  if (isCrash) {
    return {
      signal: '⚠️ -40% CRASH',
      badge: '⚠️ -40% CRASH',
      badge_class: 'badge-red',
      action: 'SELL_PANCAKE',
      action_label: 'Exit on PancakeSwap',
      multiple: costUsd > 0 ? (curVal / costUsd) : 1.0,
      pnl_usd: curVal - costUsd,
      pnl_pct: -drawdownPct * 100,
      recommendation: `Trailing Crash Stop: Dropped ${(drawdownPct * 100).toFixed(1)}% from peak (${formatCryptoPrice(peakVal)}). Exit to preserve capital!`,
      can_swap: true
    };
  }

  let mult = 1.0;
  let pnlUsd = curVal;
  let pnlPct = 0.0;
  if (costUsd > 0) {
    mult = curVal / costUsd;
    pnlUsd = curVal - costUsd;
    pnlPct = ((curVal - costUsd) / costUsd) * 100;
  }

  if (mult >= 10.0 || (costUsd === 0 && curVal >= 20.0)) {
    return {
      signal: '🚀 10x MOON',
      badge: '🚀 10x MOON',
      badge_class: 'badge-green',
      action: 'SELL_PANCAKE',
      action_label: 'Lock 80% Profit',
      multiple: mult,
      pnl_usd: pnlUsd,
      pnl_pct: pnlPct,
      recommendation: `Moonshot Target Reached (${mult.toFixed(1)}x)! Lock in 80% profit on PancakeSwap V2.`,
      can_swap: true
    };
  } else if (mult >= 5.0 || (costUsd === 0 && curVal >= 10.0)) {
    return {
      signal: '🚀 5x MOON',
      badge: '🚀 5x MOON',
      badge_class: 'badge-green',
      action: 'SELL_PANCAKE',
      action_label: 'Scale Out 50%',
      multiple: mult,
      pnl_usd: pnlUsd,
      pnl_pct: pnlPct,
      recommendation: `Major Profit Target Reached (${mult.toFixed(1)}x)! Scale out 50–70% into BNB.`,
      can_swap: true
    };
  } else if (mult >= 2.0 || (costUsd === 0 && curVal >= 2.0)) {
    return {
      signal: '🟢 2x RECOUP',
      badge: '🟢 2x RECOUP',
      badge_class: 'badge-green',
      action: 'SELL_PANCAKE',
      action_label: 'Recoup Principal',
      multiple: mult,
      pnl_usd: pnlUsd,
      pnl_pct: pnlPct,
      recommendation: `Recoup initial principal (${mult.toFixed(1)}x return)! Sell 50% to play risk-free with house money.`,
      can_swap: true
    };
  } else if (costUsd > 0 && pnlPct <= -30.0) {
    return {
      signal: '🔻 STOP LOSS',
      badge: '🔻 STOP LOSS',
      badge_class: 'badge-yellow',
      action: 'SELL_PANCAKE',
      action_label: 'Cut Loss',
      multiple: mult,
      pnl_usd: pnlUsd,
      pnl_pct: pnlPct,
      recommendation: `Position down ${Math.abs(pnlPct).toFixed(1)}% from entry cost. Consider stop loss on PancakeSwap.`,
      can_swap: true
    };
  } else {
    return {
      signal: '⏳ HODL',
      badge: '⏳ HODL',
      badge_class: 'badge-dim',
      action: 'HOLD',
      action_label: 'Holding',
      multiple: mult,
      pnl_usd: pnlUsd,
      pnl_pct: pnlPct,
      recommendation: 'Valuation within normal volatility threshold. Continue monitoring market progress.',
      can_swap: true
    };
  }
}

// One rule for "does this drop match the search text" (name, symbol, contract, deployer, smart wallets). `q` must already
// be trimmed and lower-cased. Used by the table filter and by the "matches hidden by the current view" hint.
function dropMatchesQuery(d, q) {
  const sw = Array.isArray(d.smart_wallets) && d.smart_wallets.length > 0
    ? d.smart_wallets
    : (Array.isArray(d.smart_minters) ? d.smart_minters : []);
  const matchSw = sw.some(w => String(w).toLowerCase().includes(q));
  return Boolean((d.name && d.name.toLowerCase().includes(q)) ||
    (d.symbol && d.symbol.toLowerCase().includes(q)) ||
    (d.contract && d.contract.toLowerCase().includes(q)) ||
    (d.deployer && d.deployer.toLowerCase().includes(q)) ||
    matchSw);
}

// --- Filter pill selection: one place that sets the UI and `activeFilter`, plus remembering the user's last choice ---
// Replay analysis: at least 8 of 56 scanned sessions re-clicked "FREE MINTS" in every new tab / visit. The last
// explicit choice is remembered (localStorage) and `?filter=free` links work. Only filters that need no async
// data are restored; "FOLLOWED" / "SMART" / "FAVORITES" always start from ALL.
const FILTER_STORAGE_KEY = 'agentRadar.filter';
const RESTORABLE_FILTERS = ['ALL', 'LAUNCHPAD', 'SNIPER_READY', 'FREE', 'ACTIVE', 'NFT_SURGE', 'PROXY', 'LP'];
const FILTER_URL_ALIASES = {
  all: 'ALL', free: 'FREE', freemints: 'FREE', launchpad: 'LAUNCHPAD', snipe: 'SNIPER_READY',
  sniper: 'SNIPER_READY', sniperready: 'SNIPER_READY', active: 'ACTIVE', nft: 'NFT_SURGE', proxy: 'PROXY', lp: 'LP'
};

function applyFilterSelection(key) {
  if (typeof document === 'undefined' || typeof document.querySelectorAll !== 'function' || typeof document.querySelector !== 'function') return false;
  const pill = document.querySelector(`.pill[data-filter="${key}"]`);
  const opt = document.querySelector(`#moreFiltersOptions .custom-option[data-filter="${key}"]`);
  if (!pill && !opt) return false;
  document.querySelectorAll('.pill').forEach(b => b.classList.toggle('active', b === pill));
  document.querySelectorAll('#moreFiltersOptions .custom-option').forEach(o => o.classList.toggle('selected', o === opt));
  const mfTrigger = document.getElementById('moreFiltersTrigger');
  const mfLabel = document.getElementById('moreFiltersLabel');
  if (mfTrigger) mfTrigger.classList.toggle('active', Boolean(opt));
  if (mfLabel) {
    const optText = opt ? ((opt.querySelector('span') || {}).textContent || key).trim() : '';
    mfLabel.textContent = opt ? `⚙️ ${optText}` : '⚙️ More Filters';
  }
  activeFilter = key;
  return true;
}

function saveFilterChoice(key) {
  try {
    if (typeof localStorage === 'undefined') return;
    if (RESTORABLE_FILTERS.includes(key)) localStorage.setItem(FILTER_STORAGE_KEY, key);
    else localStorage.removeItem(FILTER_STORAGE_KEY); // a non-restorable pick (e.g. FOLLOWED) must not leave a stale choice
  } catch (_) { /* storage blocked: the filter simply is not remembered */ }
}

function restoreSavedFilter() {
  let key = null;
  let source = null;
  try {
    const param = new URLSearchParams(window.location.search).get('filter');
    const fromUrl = param && FILTER_URL_ALIASES[String(param).toLowerCase().replace(/[^a-z]/g, '')];
    if (fromUrl) { key = fromUrl; source = 'url'; }
  } catch (_) { /* no URL API */ }
  if (!key) {
    try {
      const saved = typeof localStorage !== 'undefined' ? localStorage.getItem(FILTER_STORAGE_KEY) : null;
      if (saved && RESTORABLE_FILTERS.includes(saved)) { key = saved; source = 'saved'; }
    } catch (_) { /* storage blocked */ }
  }
  if (!key || key === 'ALL') return false;
  if (!applyFilterSelection(key)) return false;
  trackAnalytics('filter_restored', { filter: key, source });
  return true;
}

// Back to the unfiltered Live Radar list, without touching the remembered choice (the view is temporary). Callers
// re-render themselves.
function resetViewForSearch() {
  const tabRadar = document.getElementById('tabRadar');
  if (activeTab !== 'RADAR' && tabRadar) tabRadar.click();
  applyFilterSelection('ALL');
}

const FILTER_LABELS = {
  FREE: 'Free Mints', LAUNCHPAD: 'Launchpad', SNIPER_READY: 'Snipe Ready', ACTIVE: 'Active Mints', FAVORITES: 'Favorites',
  NFT_SURGE: 'NFT Surge', PROXY: 'Proxy', LP: 'LP', SMART: 'Smart Money', FOLLOWED: 'Followed'
};
const TAB_LABELS = { SECONDARY: 'Secondary Market', LAUNCHPAD: 'Launchpad', SMART: 'Smart Money', PORTFOLIO: 'My Portfolio' };

// Empty table + a search: if drops DO match the text but the active tab / filter pill hides them (replay analysis: a
// token searched on the main list was found on the Secondary Market tab, and recent chips emptied the table), say so and
// offer the one click that shows them. Returns '' when nothing matches anywhere, so a genuine "no such token" stays plain.
function hiddenMatchesHintHtml() {
  const q = String(searchQuery || '').trim().toLowerCase();
  if (!q || !Array.isArray(allDrops)) return '';
  const hidden = allDrops.filter(d => !isExcludedMajorAsset(d) && dropMatchesQuery(d, q)).length;
  if (hidden === 0) return '';
  const parts = [];
  if (activeFilter !== 'ALL') parts.push(`the “${escapeHtml(FILTER_LABELS[activeFilter] || activeFilter)}” filter`);
  if (activeTab !== 'RADAR') parts.push(`the ${escapeHtml(TAB_LABELS[activeTab] || activeTab)} tab`);
  if (parts.length === 0) return '';
  return `
    <div class="search-hidden-hint" role="status">
      <span><strong>${hidden}</strong> match${hidden === 1 ? '' : 'es'} for “<strong>${escapeHtml(String(searchQuery).trim())}</strong>” ${hidden === 1 ? 'is' : 'are'} hidden by ${parts.join(' and ')}.</span>
      <button type="button" class="search-hidden-btn" onclick="showAllForSearch()">Show ${hidden === 1 ? 'it' : 'all'}</button>
    </div>`;
}

// "Show them" action of the hidden-matches hint in the empty table state.
function showAllForSearch() {
  trackAnalytics('search_hidden_matches_shown', { query_len: String(searchQuery || '').trim().length, from_filter: activeFilter, from_tab: activeTab });
  resetViewForSearch();
  renderTable();
}

// Filter and Sort Drops
function getFilteredDrops() {
  let list = [...allDrops];

  // If searching, also include any known Hall of Fame / Top Gainers not yet in the active feed chunk
  if (searchQuery.trim() && Array.isArray(savedTopGainers) && savedTopGainers.length > 0) {
    const existingContracts = new Set(list.map(d => String(d.contract || '').toLowerCase()));
    for (const g of savedTopGainers) {
      const item = g.item || g;
      const c = String(item.contract || '').toLowerCase();
      if (c && !existingContracts.has(c) && !isExcludedMajorAsset(item)) {
        list.push(item);
        existingContracts.add(c);
      }
    }
  }

  // Tab filtering
  if (activeTab === 'SECONDARY') {
    list = list.filter(d => isSecondaryMarketAsset(d));
  } else if (activeTab === 'LAUNCHPAD') {
    list = list.filter(d => isLaunchpadDrop(d));
  } else if (activeTab === 'SMART') {
    list = list.filter(d => (d.smart_wallets && d.smart_wallets.length > 0) || (d.smart_minters && d.smart_minters.length > 0));
  } else if (activeTab === 'PORTFOLIO') {
    if (connectedWallet) {
      list = list.filter(d => Number(d.user_balance) > 0);
      if (nativeBnbItem && userNativeBnbBalance > 0) {
        list.unshift(nativeBnbItem);
      }
    } else {
      list = [];
    }
  }

  // Toolbar filter pills
  if (activeFilter === 'LAUNCHPAD') {
    list = list.filter(d => isLaunchpadDrop(d));
  } else if (activeFilter === 'SNIPER_READY') {
    list = list.filter(d => isSniperReady(d));
  } else if (activeFilter === 'NFT_SURGE') {
    list = list.filter(d => Boolean(d.is_nft_surge));
  } else if (activeFilter === 'PROXY') {
    list = list.filter(d => Boolean(d.is_proxy_clone));
  } else if (activeFilter === 'FAVORITES') {
    list = list.filter(d => isFavorited(d.contract, d));
  } else if (activeFilter === 'FOLLOWED') {
    if (followedWallets.size === 0) {
      list = [];
    } else {
      list = list.filter(d => {
        const sw = Array.isArray(d.smart_wallets) && d.smart_wallets.length > 0
          ? d.smart_wallets
          : (Array.isArray(d.smart_minters) ? d.smart_minters : []);
        return sw.some(w => followedWallets.has(String(w).toLowerCase().trim()));
      });
    }
  } else if (activeFilter === 'FREE') {
    list = list.filter(d => isFreeMintableNow(d));
  } else if (activeFilter === 'SMART') {
    list = list.filter(d => (d.smart_wallets && d.smart_wallets.length > 0) || (d.smart_minters && d.smart_minters.length > 0));
  } else if (activeFilter === 'LP') {
    list = list.filter(d => Number(d.lp_usd || 0) > 0);
  } else if (activeFilter === 'ACTIVE') {
    list = list.filter(d => {
      const ms = Number(d.max_supply || 0);
      const tm = Number(d.total_minted || 0);
      return ms === 0 || tm < ms;
    });
  }

  // Search (matches name, symbol, contract, deployer, and smart wallets)
  if (searchQuery.trim()) {
    const q = searchQuery.toLowerCase().trim();
    list = list.filter(d => dropMatchesQuery(d, q));
  }

  // Sort
  if (sortMode === 'MCAP' || sortMode === 'MCAP_DESC') {
    list.sort((a, b) => getSanitizedMcap(b) - getSanitizedMcap(a));
  } else if (sortMode === 'MCAP_ASC') {
    list.sort((a, b) => {
      const ma = getSanitizedMcap(a);
      const mb = getSanitizedMcap(b);
      if (!ma && mb > 0) return 1;   // getSanitizedMcap is null (unknown) rather than 0
      if (!mb && ma > 0) return -1;
      return ma - mb;
    });
  } else if (sortMode === 'VOLUME' || sortMode === 'VOLUME_DESC') {
    list.sort((a, b) => Number(b.volume_24h || 0) - Number(a.volume_24h || 0));
  } else if (sortMode === 'VOLUME_ASC') {
    list.sort((a, b) => Number(a.volume_24h || 0) - Number(b.volume_24h || 0));
  } else if (sortMode === 'PROFIT' || sortMode === 'PROFIT_DESC') {
    list.sort((a, b) => {
      const profitA = Math.max(Number(a.ath_profit_usd || 0), Number(a.current_profit_usd || 0));
      const profitB = Math.max(Number(b.ath_profit_usd || 0), Number(b.current_profit_usd || 0));
      return profitB - profitA;
    });
  } else if (sortMode === 'PROFIT_ASC') {
    list.sort((a, b) => {
      const profitA = Math.max(Number(a.ath_profit_usd || 0), Number(a.current_profit_usd || 0));
      const profitB = Math.max(Number(b.ath_profit_usd || 0), Number(b.current_profit_usd || 0));
      return profitA - profitB;
    });
  } else if (sortMode === 'INTEREST' || sortMode === 'INTEREST_DESC') {
    list.sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
  } else if (sortMode === 'INTEREST_ASC') {
    list.sort((a, b) => Number(a.score || 0) - Number(b.score || 0));
  } else if (sortMode === 'HOLDERS' || sortMode === 'HOLDERS_DESC') {
    list.sort((a, b) => Number(b.holders || 0) - Number(a.holders || 0));
  } else if (sortMode === 'HOLDERS_ASC') {
    list.sort((a, b) => Number(a.holders || 0) - Number(b.holders || 0));
  } else if (sortMode === 'PROGRESS' || sortMode === 'PROGRESS_DESC') {
    list.sort((a, b) => {
      const pctA = a.max_supply ? (Number(a.total_minted || 0) / Number(a.max_supply)) : 0;
      const pctB = b.max_supply ? (Number(b.total_minted || 0) / Number(b.max_supply)) : 0;
      return pctB - pctA;
    });
  } else if (sortMode === 'PROGRESS_ASC') {
    list.sort((a, b) => {
      const pctA = a.max_supply ? (Number(a.total_minted || 0) / Number(a.max_supply)) : 0;
      const pctB = b.max_supply ? (Number(b.total_minted || 0) / Number(b.max_supply)) : 0;
      return pctA - pctB;
    });
  } else if (sortMode === 'LP' || sortMode === 'LP_DESC') {
    list.sort((a, b) => Number(b.lp_usd || 0) - Number(a.lp_usd || 0));
  } else if (sortMode === 'LP_ASC') {
    list.sort((a, b) => Number(a.lp_usd || 0) - Number(b.lp_usd || 0));
  } else if (sortMode === 'PRICE' || sortMode === 'PRICE_DESC') {
    list.sort((a, b) => Number(b.token_price_usd || b.market_price_usd || 0) - Number(a.token_price_usd || a.market_price_usd || 0));
  } else if (sortMode === 'PRICE_ASC') {
    list.sort((a, b) => Number(a.token_price_usd || a.market_price_usd || 0) - Number(b.token_price_usd || b.market_price_usd || 0));
  } else if (sortMode === 'MINT_PRICE_DESC') {
    list.sort((a, b) => Number(b.mint_price_bnb || 0) - Number(a.mint_price_bnb || 0));
  } else if (sortMode === 'MINT_PRICE' || sortMode === 'MINT_PRICE_ASC') {
    list.sort((a, b) => Number(a.mint_price_bnb || 0) - Number(b.mint_price_bnb || 0));
  } else if (sortMode === 'NAME_ASC') {
    list.sort((a, b) => String(a.name || a.symbol || '').localeCompare(String(b.name || b.symbol || '')));
  } else if (sortMode === 'NAME_DESC') {
    list.sort((a, b) => String(b.name || b.symbol || '').localeCompare(String(a.name || a.symbol || '')));
  } else if (sortMode === 'BLOCK_ASC') {
    list.sort((a, b) => Number(a.block || 0) - Number(b.block || 0));
  } else if (sortMode === 'SMART_DESC' || sortMode === 'SMART_ASC') {
    // Smart-wallet mints (the 🧠 / 🔥N column). Ties: newest first. Ascending keeps drops with none at the bottom.
    const dir = sortMode === 'SMART_DESC' ? -1 : 1;
    list.sort((a, b) => {
      const sa = smartWalletCount(a);
      const sb = smartWalletCount(b);
      if (sa !== sb) {
        if (!sa) return 1;
        if (!sb) return -1;
        return dir * (sa - sb);
      }
      return Number(b.block || 0) - Number(a.block || 0);
    });
  } else {
    // NEWEST / Default
    if (activeTab === 'SECONDARY' && sortMode === 'NEWEST') {
      list.sort((a, b) => (getSanitizedMcap(b) - getSanitizedMcap(a)) || (Number(b.block || 0) - Number(a.block || 0)));
    } else {
      list.sort((a, b) => Number(b.block || 0) - Number(a.block || 0));
    }
  }

  return list;
}

// ==========================================================================
// 🔍 Sticky Search Bar & Recent Search Chips System
// ==========================================================================

const RECENT_INSPECTED_STORAGE_KEY = 'agent_id_recent_inspected';
const MAX_RECENT_CHIPS = 8;
let recentInspectedTokens = [];

function loadRecentInspected() {
  if (typeof localStorage === 'undefined') return [];
  try {
    const saved = localStorage.getItem(RECENT_INSPECTED_STORAGE_KEY);
    if (saved !== null) {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed)) return parsed;
    }
  } catch (_) {}
  // Default starter chips from popular alpha tokens if no prior inspections
  return [
    { symbol: 'Ptah', name: 'Ptah Protocol', contract: '0x360d0b00c3c3a9d70e6e7372cfbe85e68b95973c' },
    { symbol: 'TIMIS', name: 'Timis Alpha', contract: '0x17bfa63510ec1469e7be9401824cae3cb5871216' },
    { symbol: 'Chuffa', name: 'Chuffa Agent', contract: '0x999338b58a1f81cf0ec700e475877f10b77b7522' }
  ];
}

function saveRecentInspected(list) {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(RECENT_INSPECTED_STORAGE_KEY, JSON.stringify(list));
  } catch (_) {}
}

function addRecentInspected(token) {
  if (!token) return;
  const sym = token.symbol || token.name;
  if (!sym) return;
  const cleanSym = String(sym).trim();
  if (!cleanSym) return;

  const cAddr = String(token.contract || '').toLowerCase().trim();

  // Deduplicate: filter out if already exists by contract or symbol
  recentInspectedTokens = recentInspectedTokens.filter(item => {
    if (cAddr && item.contract && item.contract.toLowerCase() === cAddr) return false;
    if (item.symbol && item.symbol.toLowerCase() === cleanSym.toLowerCase()) return false;
    return true;
  });

  // Prepend to front
  recentInspectedTokens.unshift({
    symbol: cleanSym,
    name: token.name || cleanSym,
    contract: token.contract || '',
    timestamp: Date.now()
  });

  if (recentInspectedTokens.length > MAX_RECENT_CHIPS) {
    recentInspectedTokens = recentInspectedTokens.slice(0, MAX_RECENT_CHIPS);
  }

  saveRecentInspected(recentInspectedTokens);
  renderRecentChips();
}

function clearRecentChips() {
  recentInspectedTokens = [];
  saveRecentInspected([]);
  renderRecentChips();
}

// Search input renders (table + dropdown) are debounced so a burst of keystrokes renders once.
const SEARCH_RENDER_DEBOUNCE_MS = 120;
let searchRenderTimer = null;
function cancelPendingSearchRender() {
  if (searchRenderTimer) {
    clearTimeout(searchRenderTimer);
    searchRenderTimer = null;
  }
}
function flushSearchRender() {
  cancelPendingSearchRender();
  renderTable();
  renderSearchDropdown(searchQuery);
}
function isSearchDropdownOpen() {
  if (typeof document === 'undefined') return false;
  const dropdown = document.getElementById('searchResultsDropdown');
  return Boolean(dropdown && dropdown.style.display && dropdown.style.display !== 'none');
}

function setSearchQuery(q, andOpenModal = false) {
  cancelPendingSearchRender();
  searchQuery = q || '';
  const sInput = document.getElementById('searchInput');
  const sClear = document.getElementById('searchClearBtn');
  if (sInput) {
    sInput.value = searchQuery;
  }
  if (sClear) {
    sClear.style.display = searchQuery ? 'inline-flex' : 'none';
  }
  renderTable();

  if (andOpenModal && searchQuery) {
    const target = (typeof findTokenByQuery === 'function') ? findTokenByQuery(searchQuery) : null;
    if (target) {
      openModal(target, 'recent_chip');
    } else if (typeof resolveTokenAndInspect === 'function') {
      resolveTokenAndInspect(searchQuery);
    }
  }
}

function handleRecentChipClick(symbol, contract, event) {
  if (event) {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
  }
  const cleanSym = String(symbol || '').trim();
  // A recent chip is a token the user already looked at: search it across everything instead of inside whichever tab or
  // filter happens to be active (that produced an empty table and a manual reset in replay analysis).
  resetViewForSearch();
  setSearchQuery(cleanSym, true);
}

function renderRecentChips() {
  if (typeof document === 'undefined') return;
  const wrap = document.getElementById('recentSearchChipsWrap');
  const listEl = document.getElementById('recentChipsList');
  if (!wrap || !listEl) return;

  if (!recentInspectedTokens || recentInspectedTokens.length === 0) {
    wrap.style.display = 'none';
    listEl.innerHTML = '';
    return;
  }

  wrap.style.display = 'flex';
  listEl.innerHTML = recentInspectedTokens.map(item => {
    const sym = escapeHtml(item.symbol || item.name || 'Token');
    const c = escapeHtml(item.contract || '');
    return `
      <button type="button" class="recent-chip" onclick="handleRecentChipClick('${sym.replace(/'/g, "\\'")}', '${c}', event)" title="Jump back to ${sym}">
        <span class="recent-chip-icon">⚡</span>
        <span class="recent-chip-text">${sym}</span>
      </button>
    `;
  }).join('');
}

function setupStickyHeaderScroll() {
  if (typeof window === 'undefined') return;
  const header = document.querySelector('.suite-header');
  if (!header) return;
  window.addEventListener('scroll', () => {
    if (window.scrollY > 15) {
      header.classList.add('scrolled');
    } else {
      header.classList.remove('scrolled');
    }
  }, { passive: true });
}

// Mobile: only the search bar stays pinned to the top; the rest of the header scrolls away with the page.
// `position: sticky` cannot do this (a sticky child never leaves its parent's box, and the search bar lives inside the
// header), so the bar switches to `position: fixed` once its natural position reaches the top of the viewport and a
// same-height spacer holds its place in the layout. Positions are re-read every frame, so late layout changes
// (recent chips appearing, fonts loading, rotation) need no special handling.
function setupMobileStickySearch() {
  if (typeof window === 'undefined' || typeof document === 'undefined' || typeof window.matchMedia !== 'function') return;
  const wrap = document.querySelector('.suite-search-wrap');
  if (!wrap || !wrap.parentNode || wrap.dataset.mobileStickyReady) return;
  wrap.dataset.mobileStickyReady = '1';

  const mq = window.matchMedia('(max-width: 768px)');
  const spacer = document.createElement('div');
  spacer.className = 'search-stick-spacer';
  spacer.setAttribute('aria-hidden', 'true');
  spacer.style.display = 'none';
  wrap.parentNode.insertBefore(spacer, wrap);

  let stuck = false;
  let ticking = false;
  let marginTop = 0; // the bar's own top margin, which the spacer reproduces as part of its height

  function setStuck(next) {
    if (next === stuck) return;
    if (next) {
      // Measure before leaving the flow so the spacer matches the bar's natural footprint (height + margins).
      const rect = wrap.getBoundingClientRect();
      const cs = window.getComputedStyle(wrap);
      marginTop = parseFloat(cs.marginTop) || 0;
      spacer.style.height = Math.round(rect.height + marginTop + (parseFloat(cs.marginBottom) || 0)) + 'px';
      spacer.style.display = 'block';
      wrap.classList.add('is-stuck');
    } else {
      wrap.classList.remove('is-stuck');
      spacer.style.display = 'none';
    }
    stuck = next;
  }

  function update() {
    ticking = false;
    // Desktop widths, or the very top of the page: the bar is always in its normal place in the header.
    // (scrollY <= 0 also covers iOS rubber-banding, where it goes negative.)
    if (!mq.matches || (window.pageYOffset || 0) <= 0) { setStuck(false); return; }
    // Natural position: the bar itself while in flow, the spacer once the bar is fixed.
    const rect = (stuck ? spacer : wrap).getBoundingClientRect();
    // A zero-size rect means the header is not laid out (hidden / not rendered yet): never treat that as "at the top".
    if (!stuck && rect.height === 0) return;
    setStuck(rect.top + (stuck ? marginTop : 0) <= 0);
  }

  function schedule() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(update);
  }

  window.addEventListener('scroll', schedule, { passive: true });
  window.addEventListener('resize', schedule, { passive: true });
  window.addEventListener('orientationchange', schedule, { passive: true });
  schedule();
}

if (typeof window !== 'undefined') {
  window.setupMobileStickySearch = setupMobileStickySearch;
  window.addRecentInspected = addRecentInspected;
  window.clearRecentChips = clearRecentChips;
  window.handleRecentChipClick = handleRecentChipClick;
  window.renderRecentChips = renderRecentChips;
  window.setSearchQuery = setSearchQuery;
  window.setupStickyHeaderScroll = setupStickyHeaderScroll;
}

// Quick Search Dropdown
let searchDropdownResults = [];
let searchDropdownSelectedIndex = -1;

// Server-side fallback for the dropdown. The in-memory feed is only a subset of all drops, and replay analysis showed
// visitors pasting contract addresses / tickers that were not loaded and hitting a dead-end "No agentic projects" state.
// One lookup per distinct query (cached), debounced, and ignored if the user has typed on since.
const remoteSearchCache = new Map(); // lowercased query -> array of projects ([] = looked up successfully, nothing found)
// A FAILED lookup (backend down / slow / non-2xx) is not "nothing found": it is remembered separately and only briefly,
// so the dropdown can say "search is unavailable" instead of the misleading "No agentic projects matching", and a retry
// is possible. (Before this, an outage was cached as an empty result and reported as a genuine miss.)
const REMOTE_SEARCH_ERROR_TTL_MS = 15000;
const remoteSearchErrorUntil = new Map(); // lowercased query -> epoch ms until which the failure is shown
let remoteSearchTimer = null;
let remoteSearchInFlight = null;

function remoteSearchFailed(q) {
  const key = String(q || '').trim().toLowerCase();
  const until = remoteSearchErrorUntil.get(key);
  if (!until) return false;
  if (Date.now() < until) return true;
  remoteSearchErrorUntil.delete(key); // expired: a new keystroke / focus may try again
  return false;
}

// A lookup is worth making (and the dropdown shows a "searching" state) for queries of 4+ characters that have not
// been looked up yet (and have not just failed), in a browser that can fetch.
function isRemoteSearchPending(q) {
  const key = String(q || '').trim().toLowerCase();
  return typeof fetch !== 'undefined' && key.length >= 4 && !remoteSearchCache.has(key) && !remoteSearchFailed(key);
}

function scheduleRemoteSearch(q) {
  const key = String(q || '').trim().toLowerCase();
  clearTimeout(remoteSearchTimer);
  if (!isRemoteSearchPending(key) || remoteSearchInFlight === key) return;
  remoteSearchTimer = setTimeout(async () => {
    remoteSearchInFlight = key;
    let ok = false;
    let projects = [];
    try {
      const url = (typeof BACKEND_URL !== 'undefined' ? BACKEND_URL : '') + `/api/projects?limit=7&search=${encodeURIComponent(key)}`;
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        if (data && Array.isArray(data.projects)) {
          projects = data.projects.slice(0, 7);
          ok = true;
        }
      }
    } catch (_) {
      ok = false;
    } finally {
      remoteSearchInFlight = null;
    }
    if (ok) {
      remoteSearchCache.set(key, projects);
      remoteSearchErrorUntil.delete(key);
    } else {
      remoteSearchErrorUntil.set(key, Date.now() + REMOTE_SEARCH_ERROR_TTL_MS);
    }
    // Measures how often the fallback rescues a search the loaded feed could not answer, and how often it could not
    // answer at all (status 'error' is an outage, not a miss).
    trackAnalytics('token_search_remote', {
      query: key,
      status: ok ? 'ok' : 'error',
      result_count: ok ? projects.length : null,
      is_address: /^0x[0-9a-f]{4,}$/i.test(key)
    });
    // Re-render for the query that is still on screen: results, the empty state, or the unavailable state (replacing
    // the "searching" state in every case).
    const input = document.getElementById('searchInput');
    const current = input ? String(input.value || '').trim().toLowerCase() : '';
    if (current === key && isSearchDropdownOpen()) renderSearchDropdown(input.value);
  }, 400);
}

// "Try again" on the unavailable state.
function retryRemoteSearch() {
  const input = document.getElementById('searchInput');
  if (!input) return;
  remoteSearchErrorUntil.delete(String(input.value || '').trim().toLowerCase());
  renderSearchDropdown(input.value);
}

function highlightSearchMatch(text, query) {
  if (!text) return '';
  const escapedText = escapeHtml(String(text));
  if (!query || !query.trim()) return escapedText;
  const q = escapeHtml(query.trim());
  const regex = new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
  return escapedText.replace(regex, '<mark class="search-hl">$1</mark>');
}

function getMatchingDrops(query, maxLimit = 7) {
  if (!query || !query.trim()) return [];
  const q = query.toLowerCase().trim();

  const candidateMap = new Map();

  // Active radar feed
  const dropsList = (Array.isArray(allDrops) && allDrops.length > 0)
    ? allDrops
    : ((typeof global !== 'undefined' && Array.isArray(global.allDrops)) ? global.allDrops : (allDrops || []));

  if (Array.isArray(dropsList)) {
    for (const d of dropsList) {
      if (!d || isExcludedMajorAsset(d)) continue;
      const c = String(d.contract || d.id || '').toLowerCase();
      if (c && !candidateMap.has(c)) {
        candidateMap.set(c, d);
      }
    }
  }

  // Top gainers / Hall of fame
  if (Array.isArray(savedTopGainers)) {
    for (const g of savedTopGainers) {
      const item = g.item || g;
      if (!item || isExcludedMajorAsset(item)) continue;
      const c = String(item.contract || item.id || '').toLowerCase();
      if (c && !candidateMap.has(c)) {
        candidateMap.set(c, item);
      }
    }
  }

  const matches = [];
  for (const item of candidateMap.values()) {
    const sym = String(item.symbol || '').toLowerCase();
    const name = String(item.name || '').toLowerCase();
    const contract = String(item.contract || '').toLowerCase();
    const deployer = String(item.deployer || '').toLowerCase();

    const sw = Array.isArray(item.smart_wallets) && item.smart_wallets.length > 0
      ? item.smart_wallets
      : (Array.isArray(item.smart_minters) ? item.smart_minters : []);
    const matchSw = sw.some(w => String(w).toLowerCase().includes(q));

    let score = 0;
    if (sym === q) score = 100;
    else if (sym.startsWith(q)) score = 80;
    else if (name.startsWith(q)) score = 65;
    else if (sym.includes(q)) score = 50;
    else if (name.includes(q)) score = 40;
    else if (contract === q) score = 95;
    else if (contract.startsWith(q)) score = 35;
    else if (contract.includes(q)) score = 30;
    else if (deployer.includes(q)) score = 20;
    else if (matchSw) score = 15;

    if (score > 0) {
      matches.push({ item, score });
    }
  }

  // Sort by score descending, then block / id descending
  matches.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    const bBlock = Number(b.item.block_number || b.item.id || 0);
    const aBlock = Number(a.item.block_number || a.item.id || 0);
    return bBlock - aBlock;
  });

  return matches.slice(0, maxLimit).map(m => m.item);
}

function renderSearchDropdown(query) {
  const dropdown = document.getElementById('searchResultsDropdown');
  if (!dropdown) return;

  const q = String(query || '').trim();
  if (!q) {
    hideSearchDropdown();
    return;
  }

  // (The active row is only reset when the list is actually rebuilt, further down: resetting it on every render would
  // pull the highlight off the row the user is on whenever an identical re-render happens.)
  searchDropdownResults = getMatchingDrops(q, 7);

  if (searchDropdownResults.length === 0) {
    const remote = remoteSearchCache.get(q.toLowerCase());
    if (remote && remote.length > 0) {
      searchDropdownResults = remote;
    } else {
      scheduleRemoteSearch(q);
    }
  }

  // Nothing loaded matches and the backend lookup is still to come: say so instead of showing a dead-end.
  if (searchDropdownResults.length === 0 && isRemoteSearchPending(q)) {
    delete dropdown.dataset.renderedHtml; // this state replaces the list: the "skip identical render" cache is stale
    searchDropdownSelectedIndex = -1;
    dropdown.innerHTML = `
      <div class="search-dropdown-empty search-dropdown-searching" role="status" aria-live="polite">
        <div class="search-empty-icon">⏳</div>
        <div class="search-empty-title">Searching all drops for "<strong>${escapeHtml(q)}</strong>"…</div>
        <div class="search-empty-desc">Not in the loaded feed yet. Checking the full database.</div>
      </div>
    `;
    dropdown.style.display = 'block';
    return;
  }

  // The lookup itself failed: do not claim the token does not exist.
  if (searchDropdownResults.length === 0 && remoteSearchFailed(q)) {
    delete dropdown.dataset.renderedHtml;
    searchDropdownSelectedIndex = -1;
    dropdown.innerHTML = `
      <div class="search-dropdown-empty search-dropdown-error" role="alert">
        <div class="search-empty-icon">⚠️</div>
        <div class="search-empty-title">Couldn't search all drops for "<strong>${escapeHtml(q)}</strong>"</div>
        <div class="search-empty-desc">Only the drops already loaded were checked. The full database is unreachable right now.</div>
        <div class="search-empty-actions">
          <button type="button" class="search-empty-btn" onclick="retryRemoteSearch()">Try again</button>
        </div>
      </div>
    `;
    dropdown.style.display = 'block';
    return;
  }

  if (searchDropdownResults.length === 0) {
    delete dropdown.dataset.renderedHtml;
    searchDropdownSelectedIndex = -1;
    dropdown.innerHTML = `
      <div class="search-dropdown-empty">
        <div class="search-empty-icon">🔍</div>
        <div class="search-empty-title">No agentic projects matching "<strong>${escapeHtml(q)}</strong>"</div>
        <div class="search-empty-desc">Check the contract or token symbol, or trace directly on BscScan</div>
        <div class="search-empty-actions">
          <a href="https://bscscan.com/search?q=${encodeURIComponent(q)}" target="_blank" rel="noopener" class="search-empty-btn">
            Search on BscScan ↗
          </a>
        </div>
      </div>
    `;
    dropdown.style.display = 'block';
    return;
  }

  const itemsHtml = searchDropdownResults.map((item, idx) => {
    const sym = item.symbol || 'TOKEN';
    const name = item.name || sym;
    const cAddr = String(item.contract || '');
    const shortAddr = cAddr.length > 12 ? `${cAddr.slice(0, 6)}...${cAddr.slice(-4)}` : cAddr;
    const initial = (sym[0] || name[0] || '?').toUpperCase();

    // Badges: a row never wraps, so at most 3, most important first (a honeypot warning must never be the one dropped).
    const badges = [];
    const pStr = String(item.mint_price_str || '').toUpperCase();
    const isFree = item.has_free_mint || pStr.includes('FREE');
    const sw = Array.isArray(item.smart_wallets) && item.smart_wallets.length > 0 ? item.smart_wallets : (item.smart_minters || []);
    if (item.safety_score === 'HONEYPOT' || item.is_honeypot) badges.push('<span class="search-drop-badge badge-honeypot">Honeypot</span>');
    if (isFree) badges.push('<span class="search-drop-badge badge-free">Free</span>');
    if (typeof isSniperReady === 'function' && isSniperReady(item)) badges.push('<span class="search-drop-badge badge-sniper" title="Snipe Ready">Snipe</span>');
    if (sw.length > 0) badges.push(`<span class="search-drop-badge badge-smart">🧠 ${sw.length}</span>`);
    badges.length = Math.min(badges.length, 3);

    // Price / gain
    const price = item.token_price_usd || item.market_price_usd || item.current_price;
    const priceDisplay = price > 0 ? formatCryptoPrice(price) : (isFree ? 'FREE' : (item.mint_price_bnb ? `${item.mint_price_bnb} BNB` : '—'));

    let gainDisplay = '';
    let gainClass = 'gain-neutral';
    const gainPct = Number(item.gain_pct || item.price_change_24h || 0);
    const athMult = Number(item.ath_multiplier || item.gain_multiple || 0);

    if (gainPct !== 0) {
      const isPos = gainPct > 0;
      gainDisplay = `${isPos ? '+' : ''}${gainPct.toFixed(1)}%`;
      gainClass = isPos ? 'gain-pos' : 'gain-neg';
    } else if (athMult > 1) {
      gainDisplay = `${athMult.toFixed(1)}x ATH`;
      gainClass = 'gain-pos';
    } else if (getSanitizedMcap(item) > 0) {
      gainDisplay = `${mcapLabel(item)}: $${formatCount(getSanitizedMcap(item))}`;
      gainClass = 'gain-neutral';
    }

    return `
      <div class="search-dropdown-item" role="option" id="searchOpt-${idx}" aria-selected="false" data-idx="${idx}" data-contract="${escapeHtml(cAddr)}" tabindex="-1">
        <div class="search-drop-avatar">${initial}</div>
        <div class="search-drop-main">
          <div class="search-drop-title-row">
            <span class="search-drop-name" title="${escapeHtml(name)}">${highlightSearchMatch(name, q)}</span>
            <span class="search-drop-symbol">$${highlightSearchMatch(sym, q)}</span>
            <span class="search-drop-badges">${badges.join('')}</span>
          </div>
          <div class="search-drop-sub">
            <span class="search-drop-addr">${shortAddr}</span>
            ${item.standard ? `<span class="search-drop-type" title="${escapeHtml(item.standard)}">${escapeHtml(shortStandardLabel(item.standard))}</span>` : ''}
          </div>
        </div>
        <div class="search-drop-meta">
          <div class="search-drop-price">${priceDisplay}</div>
          ${gainDisplay ? `<div class="search-drop-gain ${gainClass}">${gainDisplay}</div>` : ''}
        </div>
        <div class="search-drop-action" aria-hidden="true">↗</div>
      </div>
    `;
  }).join('');

  const dropdownHtml = `
    <div class="search-dropdown-inner">
      <div class="search-dropdown-header">
        <span class="search-dropdown-title">Matching Drops <span class="search-dropdown-count">(${searchDropdownResults.length})</span></span>
        <span class="search-dropdown-hint desktop-only">↵ to inspect</span>
      </div>
      <div class="search-dropdown-list" role="listbox" id="searchDropdownList">
        ${itemsHtml}
      </div>
      <div class="search-dropdown-footer">
        <button type="button" class="search-dropdown-view-table" id="searchDropdownViewTable">
          <span>View in table ↓</span>
        </button>
        <div class="search-dropdown-shortcuts desktop-only">
          <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
          <span><kbd>↵</kbd> inspect</span>
          <span><kbd>esc</kbd> close</span>
        </div>
      </div>
    </div>
  `;

  // Rebuild only when the content really changed. Re-rendering identical markup (the debounced table render, a late
  // backend answer, the smart-drops load) recreates every row under a stationary mouse pointer, which restarts the
  // hover state and reads as flicker/shaking; it also throws away the list's scroll position.
  if (dropdown.dataset.renderedHtml !== dropdownHtml) {
    dropdown.innerHTML = dropdownHtml;
    dropdown.dataset.renderedHtml = dropdownHtml;
    searchDropdownSelectedIndex = -1;
    updateSearchDropdownSelection(defaultSearchDropdownSelection(), { scroll: false });
  }
  bindSearchDropdownEvents(dropdown);

  dropdown.style.display = 'block';
}

// "Matching drops" glyph for the second line: the long standard strings ("ERC-721 (NFT Collection)",
// "BEP-20 Token (18 decimals)") wrapped onto extra lines and made rows uneven. The full text stays in the tooltip.
function shortStandardLabel(std) {
  const s = String(std || '');
  if (/1155/.test(s)) return 'ERC-1155';
  if (/721|NFT/i.test(s)) return 'NFT';
  if (/BEP-?20|ERC-?20|token/i.test(s)) return 'BEP-20';
  return s.split('(')[0].trim() || s;
}

// With a mouse or trackpad the first row is the "active" one, so Enter visibly opens what is highlighted. On touch
// screens nothing is pre-highlighted (a lit-up first row there just looks like a stuck tap state).
function defaultSearchDropdownSelection() {
  try {
    if (typeof window.matchMedia === 'function' && window.matchMedia('(hover: hover) and (pointer: fine)').matches) return 0;
  } catch (_) { /* no matchMedia */ }
  return -1;
}

// One delegated set of handlers on the dropdown element, bound once (the rows themselves are rebuilt freely).
function bindSearchDropdownEvents(dropdown) {
  if (!dropdown || dropdown.dataset.eventsBound === '1') return;
  dropdown.dataset.eventsBound = '1';

  let lastX = null;
  let lastY = null;

  dropdown.addEventListener('click', (e) => {
    const target = e.target;
    if (!target || typeof target.closest !== 'function') return;
    if (target.closest('#searchDropdownViewTable')) {
      e.preventDefault();
      e.stopPropagation();
      hideSearchDropdown();
      const tableSec = document.getElementById('tableSection') || document.querySelector('.table-wrapper') || document.getElementById('tableBody');
      if (tableSec) tableSec.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    const row = target.closest('.search-dropdown-item');
    if (!row) return;
    e.preventDefault();
    e.stopPropagation();
    const item = searchDropdownResults[Number(row.getAttribute('data-idx'))];
    if (item) selectSearchDropdownItem(item);
  });

  // The hover highlight is driven by REAL pointer movement only. Plain :hover / mouseenter also fire when the list
  // scrolls under a stationary pointer (arrow-key navigation, wheel), which made the highlight fight the keyboard
  // selection and the rows appear to shake. Browsers also fire synthetic mousemoves after a scroll: those carry the
  // same coordinates, so they are ignored.
  dropdown.addEventListener('mousemove', (e) => {
    if (e.clientX === lastX && e.clientY === lastY) return;
    lastX = e.clientX;
    lastY = e.clientY;
    const target = e.target;
    const row = target && typeof target.closest === 'function' ? target.closest('.search-dropdown-item') : null;
    if (!row) return;
    const idx = Number(row.getAttribute('data-idx'));
    if (idx !== searchDropdownSelectedIndex) updateSearchDropdownSelection(idx, { scroll: false });
  });
}

function hideSearchDropdown() {
  const dropdown = document.getElementById('searchResultsDropdown');
  if (dropdown) {
    dropdown.style.display = 'none';
    delete dropdown.dataset.renderedHtml; // reopening rebuilds the list, so it never comes back with a stale highlight
  }
  searchDropdownSelectedIndex = -1;
  const input = document.getElementById('searchInput');
  if (input && typeof input.removeAttribute === 'function') input.removeAttribute('aria-activedescendant');
}

function selectSearchDropdownItem(item) {
  if (!item) return;
  hideSearchDropdown();
  addRecentInspected(item);
  openModal(item, 'search_dropdown');
}

// The single place that decides which row is active (keyboard, mouse and default all go through here).
// `opts.scroll === false` (mouse / initial render) never moves the list; keyboard navigation scrolls only the LIST by
// hand instead of scrollIntoView(), which also scrolls the page and any other scrollable ancestor.
function updateSearchDropdownSelection(newIndex, opts) {
  const items = document.querySelectorAll('#searchResultsDropdown .search-dropdown-item');
  if (!items || items.length === 0) return;

  items.forEach(el => {
    el.classList.remove('is-selected');
    el.setAttribute('aria-selected', 'false');
  });
  const input = document.getElementById('searchInput');

  if (newIndex >= 0 && newIndex < items.length) {
    searchDropdownSelectedIndex = newIndex;
    const target = items[newIndex];
    target.classList.add('is-selected');
    target.setAttribute('aria-selected', 'true');
    if (input && target.id) input.setAttribute('aria-activedescendant', target.id);

    const scrollIt = !opts || opts.scroll !== false;
    const list = document.getElementById('searchDropdownList');
    if (scrollIt && list && typeof target.offsetTop === 'number') {
      const top = target.offsetTop;
      const bottom = top + target.offsetHeight;
      if (top < list.scrollTop) list.scrollTop = top;
      else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
    }
  } else {
    searchDropdownSelectedIndex = -1;
    if (input && typeof input.removeAttribute === 'function') input.removeAttribute('aria-activedescendant');
  }
}

if (typeof window !== 'undefined') {
  window.renderSearchDropdown = renderSearchDropdown;
  window.hideSearchDropdown = hideSearchDropdown;
  window.getMatchingDrops = getMatchingDrops;
  window.selectSearchDropdownItem = selectSearchDropdownItem;
}

const TABLE_RENDER_CHUNK = 60;
let tableRenderLimit = 60;

// How many smart wallets minted a drop: the count the 🧠 / 🔥N badge shows (smart_wallets, else smart_minters).
function smartWalletCount(d) {
  if (!d) return 0;
  if (Array.isArray(d.smart_wallets) && d.smart_wallets.length > 0) return d.smart_wallets.length;
  return Array.isArray(d.smart_minters) ? d.smart_minters.length : 0;
}

// Universal Table Header Sorter with Bi-Directional Indicator
function makeHeaderSortable(el, baseLabel, descMode, ascMode, customToggle) {
  if (!el) return;
  const isDesc = (sortMode === descMode) || 
    (descMode === 'NEWEST' && (sortMode === 'NEWEST' || !sortMode)) ||
    (descMode === 'MCAP_DESC' && sortMode === 'MCAP') ||
    (descMode === 'PROGRESS_DESC' && sortMode === 'PROGRESS') ||
    (descMode === 'HOLDERS_DESC' && sortMode === 'HOLDERS') ||
    (descMode === 'LP_DESC' && sortMode === 'LP') ||
    (descMode === 'VOLUME_DESC' && sortMode === 'VOLUME') ||
    (descMode === 'PROFIT_DESC' && sortMode === 'PROFIT') ||
    (descMode === 'INTEREST_DESC' && sortMode === 'INTEREST');

  const isAsc = (sortMode === ascMode);
  const isDefaultBlock = (descMode === 'NEWEST');
  const isActive = isDefaultBlock ? (sortMode === 'NEWEST' || sortMode === 'BLOCK_ASC') : (isDesc || isAsc);

  let indicator = '<span class="sort-indicator dim-hint">↕</span>';
  let title = `Click to sort by ${baseLabel.replace(/^[^\w\s]+/, '').trim()}`;

  if (isDesc && (descMode !== 'NEWEST' || sortMode === 'NEWEST')) {
    indicator = '<span class="sort-indicator desc">▼</span>';
    title = `Sorted: High ➔ Low (Click to sort Low ➔ High)`;
  } else if (isAsc) {
    indicator = '<span class="sort-indicator asc">▲</span>';
    title = `Sorted: Low ➔ High (Click to reset)`;
  }

  // Write only what changed: this runs on every table render, and rewriting an identical header restyles it.
  const headHtml = `${baseLabel} ${indicator}`;
  if (el.innerHTML !== headHtml) el.innerHTML = headHtml;
  const baseClass = (el.className || '').replace(/\s*(sortable-th|active-sort)\b/g, '').trim();
  const nextClass = `${baseClass} sortable-th${(isActive && (!isDefaultBlock || sortMode === 'BLOCK_ASC')) ? ' active-sort' : ''}`.trim();
  if (el.className !== nextClass) el.className = nextClass;
  if (el.style.cursor !== 'pointer') el.style.cursor = 'pointer';
  if (el.title !== title) el.title = title;

  el.onclick = () => {
    if (typeof customToggle === 'function') {
      const next = customToggle(sortMode);
      setSortMode(next);
      return;
    }
    let next;
    if (descMode === 'NEWEST') {
      next = (sortMode === 'BLOCK_ASC') ? 'NEWEST' : 'BLOCK_ASC';
    } else {
      if (isDesc) next = ascMode;
      else if (isAsc) next = 'NEWEST';
      else next = descMode;
    }
    setSortMode(next);
  };
}

// Puts the rows into tbody in the given order, touching the DOM only where it differs: rows that left are removed,
// and a row is moved or inserted only where it is out of place. An unchanged list makes no DOM changes.
function patchRowNodes(tbody, nodes) {
  if (!('firstElementChild' in tbody) || typeof tbody.insertBefore !== 'function' || typeof tbody.removeChild !== 'function') {
    const fragment = document.createDocumentFragment();
    nodes.forEach(node => fragment.appendChild(node));
    if (typeof tbody.replaceChildren === 'function') {
      tbody.replaceChildren(fragment);
    } else {
      tbody.innerHTML = '';
      tbody.appendChild(fragment);
    }
    return;
  }
  const keep = new Set(nodes);
  Array.from(tbody.children).forEach(child => {
    if (!keep.has(child)) tbody.removeChild(child);
  });
  let ref = tbody.firstElementChild;
  nodes.forEach(node => {
    if (node === ref) {
      ref = ref.nextElementSibling;
    } else {
      tbody.insertBefore(node, ref);
    }
  });
}

// Filter and tab taps: the new active state is painted first, and the table rebuild runs in the task after that paint,
// so the tap's next paint is not held up by the row work. Taps within one frame render once, with the latest state.
let tableRenderQueued = false;
function scheduleTableRender() {
  if (tableRenderQueued) return;
  tableRenderQueued = true;
  const run = () => {
    tableRenderQueued = false;
    renderTable();
  };
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(() => setTimeout(run, 0));
  } else {
    setTimeout(run, 0);
  }
}

// Rows built by recent renderTable calls, by drop object (see the row loop in renderTable).
const TABLE_ROW_CACHE_MAX = 240;
let tableRowCache = new Map();

// Render Table Rows
function renderTable(keepLimit = false) {
  if (typeof document === 'undefined') return;
  const tbody = document.getElementById('tableBody');
  if (!tbody) return;
  if (!keepLimit) {
    tableRenderLimit = TABLE_RENDER_CHUNK;
  }
  const filtered = getFilteredDrops();

  // The table's reserved height (style.css .table-reserve) only covers the wait for the first feed render; after that,
  // or on tabs that do not show the table, the table sizes to its content again.
  if (allDrops.length > 0 || activeTab === 'WALLETS' || activeTab === 'PORTFOLIO') {
    const wrapEl = (typeof document.querySelector === 'function') ? document.querySelector('.table-wrapper') : null;
    if (wrapEl && wrapEl.classList && typeof wrapEl.classList.remove === 'function') wrapEl.classList.remove('table-reserve');
  }

  // Counts, the secondary banner and the pulse card depend on the data, not on the filter / search / sort / page.
  const dataChanged = dataDerivedInputsChanged();
  if (dataChanged) {
    updateAllFilterCounts();
    updateDailyPulseUI();
  }
  if (activeTab === 'SECONDARY' && (dataChanged || lastRenderedTab !== 'SECONDARY')) {
    updateSecondaryBannerUI();
  }
  lastRenderedTab = activeTab;
  const totalDisplay = globalStats ? globalStats.total : allDrops.length;
  const footerCountEl = document.getElementById('footerCount');
  if (footerCountEl) {
    footerCountEl.textContent = `Showing ${Math.min(filtered.length, tableRenderLimit)} of ${totalDisplay} collections`;
  }

  // Dynamic table headers based on active tab
  const thColBlock = document.getElementById('thColBlock');
  const thColProject = document.getElementById('thColProject');
  const thColInterest = document.getElementById('thColInterest');
  const thCol9 = document.getElementById('thCol9');
  const thCol10 = document.getElementById('thCol10') || document.getElementById('thProgress');
  const thCol11 = document.getElementById('thCol11') || document.getElementById('thPrice');
  const thCol12 = document.getElementById('thCol12') || document.getElementById('thLp');
  const thCol13 = document.getElementById('thCol13') || document.getElementById('thFloor');
  const thAction = document.getElementById('thAction');

  if (thColBlock) makeHeaderSortable(thColBlock, 'BLOCK', 'NEWEST', 'BLOCK_ASC');
  if (thColProject) makeHeaderSortable(thColProject, 'PROJECT', 'NAME_ASC', 'NAME_DESC');
  if (thColInterest) makeHeaderSortable(thColInterest, 'INTEREST', 'INTEREST_DESC', 'INTEREST_ASC');
  const thColSm = document.getElementById('thColSm');
  if (thColSm) makeHeaderSortable(thColSm, '🧠', 'SMART_DESC', 'SMART_ASC');

  const dropsTable = document.getElementById('dropsTable');
  if (dropsTable && dropsTable.classList) {
    if (typeof dropsTable.classList.toggle === 'function') {
      dropsTable.classList.toggle('secondary-mode', activeTab === 'SECONDARY');
    } else {
      if (activeTab === 'SECONDARY') dropsTable.classList.add('secondary-mode');
      else dropsTable.classList.remove('secondary-mode');
    }
  }

  if (activeTab === 'SECONDARY') {
    if (thCol9) {
      thCol9.className = 'col-floor';
      makeHeaderSortable(thCol9, '📈 DEX PRICE', 'PRICE_DESC', 'PRICE_ASC');
    }
    if (thCol10) {
      thCol10.className = 'col-progress';
      makeHeaderSortable(thCol10, '💎 MARKET CAP', 'MCAP_DESC', 'MCAP_ASC');
    }
    if (thCol11) {
      thCol11.className = 'col-price';
      makeHeaderSortable(thCol11, '🌊 24H VOL', 'VOLUME_DESC', 'VOLUME_ASC');
    }
    if (thCol12) {
      thCol12.className = 'col-lp';
      makeHeaderSortable(thCol12, '🥞 DEX LP', 'LP_DESC', 'LP_ASC');
    }
    if (thCol13) {
      thCol13.className = 'col-holders';
      makeHeaderSortable(thCol13, '👥 HOLDERS', 'HOLDERS_DESC', 'HOLDERS_ASC');
    }
    if (thAction) {
      thAction.textContent = '📈 DEXSCREENER';
    }
  } else {
    if (thCol9) {
      thCol9.className = 'col-holders';
      makeHeaderSortable(thCol9, 'HOLDERS / BUYS', 'HOLDERS_DESC', 'HOLDERS_ASC');
    }
    if (thCol10) {
      thCol10.className = 'col-lp';
      makeHeaderSortable(thCol10, 'LP / VOL', 'LP_DESC', 'LP_ASC', (curr) => {
        if (curr === 'LP' || curr === 'LP_DESC') return 'VOLUME_DESC';
        if (curr === 'VOLUME' || curr === 'VOLUME_DESC') return 'LP_ASC';
        if (curr === 'LP_ASC') return 'NEWEST';
        return 'LP_DESC';
      });
    }
    if (thCol11) {
      thCol11.className = 'col-progress';
      makeHeaderSortable(thCol11, 'MINT PROGRESS', 'PROGRESS_DESC', 'PROGRESS_ASC');
    }
    if (thCol12) {
      thCol12.className = 'col-price';
      makeHeaderSortable(thCol12, 'MINT PRICE', 'MINT_PRICE_ASC', 'MINT_PRICE_DESC');
    }
    if (thCol13) {
      thCol13.className = 'col-floor';
      makeHeaderSortable(thCol13, 'FLOOR / TRADE', 'PROFIT_DESC', 'PROFIT_ASC');
    }
    if (thAction) {
      thAction.textContent = 'INSPECT';
    }
  }

  // Feature 2: Dedicated Wallet Leaderboard Tab Visibility Toggle
  const walletsContainer = document.getElementById('walletsLeaderboardContainer');
  const loadMoreWrap = document.getElementById('loadMoreWrap');

  if (activeTab === 'WALLETS') {
    if (dropsTable) dropsTable.style.display = 'none';
    if (loadMoreWrap) loadMoreWrap.style.display = 'none';
    if (walletsContainer) {
      walletsContainer.style.display = 'flex';
      renderWalletsTabLeaderboard();
    }
    updateWalletsTabBadge();
    return;
  } else {
    if (dropsTable) dropsTable.style.display = '';
    if (walletsContainer) walletsContainer.style.display = 'none';
    updateWalletsTabBadge();
  }

  if (filtered.length === 0) {
    const tr = document.createElement('tr');
    if (activeTab === 'PORTFOLIO') {
      if (!connectedWallet) {
        tr.innerHTML = `
          <td colspan="14" style="padding: 0;">
            <div class="portfolio-onboarding">
              <div class="portfolio-onboarding-icon">👛</div>
              <div class="portfolio-onboarding-title">Connect Web3 Wallet for Live BSC Portfolio Sync</div>
              <div class="portfolio-onboarding-desc">
                Connect MetaMask, Binance Web3 Wallet, or Rabby to dynamically scan all 348+ .ai agent tokens and NFTs in your wallet, monitor real-time USD balances, and receive automated 1-Click Take-Profit recommendations.
              </div>
              <div class="portfolio-onboarding-actions">
                <button class="onboarding-connect-btn" onclick="openWalletModal()">
                  <span>👛 Connect Web3 Wallet</span>
                </button>
                <button class="onboarding-demo-btn" onclick="setWatchOnlyWallet('0x6827d403df6b07ba405a6cfe0305dbd0fc727f55')">
                  <span>👁️ Demo with Smart Wallet (0x6827...7f55)</span>
                </button>
              </div>
              <div style="margin-top: 16px; font-size: 11px; color: var(--text-dim);">
                Or enter any public BSC address in the <a href="javascript:void(0)" onclick="openWalletModal()" style="color: var(--color-cyan); text-decoration: underline;">Watch-Only Auditor</a>
              </div>
            </div>
          </td>
        `;
      } else {
        tr.innerHTML = `
          <td colspan="14" style="text-align: center; padding: 40px; color: var(--text-dim);">
            🎒 No .ai tokens or NFTs detected in connected wallet <code>${connectedWallet.slice(0, 8)}...${connectedWallet.slice(-6)}</code>.<br/><br/>
            <button class="port-action-btn" onclick="scanUserPortfolio('${connectedWallet}', true)" style="font-size: 11px; padding: 6px 14px; margin-right: 8px;">🔄 Rescan On-Chain</button>
            or discover and snipe new drops in <strong style="color: var(--color-cyan); cursor: pointer;" onclick="document.getElementById('tabRadar').click()">1: 📡 Live Radar</strong>!
          </td>
        `;
      }
    } else if (activeFilter === 'FOLLOWED') {
      const fList = [...followedWallets];
      tr.innerHTML = `
        <td colspan="14" style="text-align: center; padding: 40px 20px; color: var(--text-dim);">
          <div style="font-size: 28px; margin-bottom: 8px;">⭐</div>
          <div style="font-weight: 700; color: var(--color-yellow); font-size: 14px; margin-bottom: 6px;">
            ${fList.length === 0 ? 'No Smart Alpha Hunters Followed Yet' : `No Drops Found for Followed Hunters (${fList.join(', ')})`}
          </div>
          <div style="font-size: 12px; max-width: 500px; margin: 0 auto 14px; line-height: 1.5;">
            ${fList.length === 0
              ? 'Click "⭐ Follow" next to top-performing .ai wallets in the Smart Leaderboard or Drop Inspect modal to stream their drops here.'
              : `Tracking ${fList.length} followed hunter(s). As soon as they enter a mint, it will automatically appear here with high-conviction star tags.`}
          </div>
          <button class="pill-action pill-smart-leaderboard" onclick="openSmartLeaderboardModal('ALL')" style="font-size: 12px; padding: 6px 14px;">
            🏆 Open Smart Hunter Leaderboard (2,100+ Wallets)
          </button>
        </td>
      `;
    } else if (activeTab === 'SECONDARY') {
      // Until the secondary-market list has actually arrived, "no pairs" would be a false statement (replay analysis saw
      // "No Secondary Market Pairs Found" while the data request was still in flight).
      const secondaryStillLoading = typeof hasLoadedSecondaryDrops !== 'undefined' && !hasLoadedSecondaryDrops;
      tr.innerHTML = secondaryStillLoading ? `
        <td colspan="14" style="text-align: center; padding: 40px 20px; color: var(--text-dim);">
          <div style="font-size: 28px; margin-bottom: 8px;">⏳</div>
          <div style="font-weight: 700; color: #ffd600; font-size: 14px; margin-bottom: 6px;" role="status" aria-live="polite">
            Loading secondary market pairs…
          </div>
          ${hiddenMatchesHintHtml()}
        </td>
      ` : `
        <td colspan="14" style="text-align: center; padding: 40px 20px; color: var(--text-dim);">
          <div style="font-size: 28px; margin-bottom: 8px;">📈</div>
          <div style="font-weight: 700; color: #ffd600; font-size: 14px; margin-bottom: 6px;">
            No Secondary Market Pairs Found
          </div>
          <div style="font-size: 12px; max-width: 500px; margin: 0 auto 14px; line-height: 1.5;">
            Tokens automatically graduate here once they inject PancakeSwap LP, achieve &gt;$500 market cap, or begin active secondary trading.
          </div>
          ${hiddenMatchesHintHtml()}
        </td>
      `;
    } else {
      tr.innerHTML = `<td colspan="14" style="text-align: center; padding: 30px; color: var(--text-dim);">No agentic projects match current filter [${activeFilter}] or search.${hiddenMatchesHintHtml()}</td>`;
    }
    if (typeof tbody.replaceChildren === 'function') {
      tbody.replaceChildren(tr);
    } else {
      tbody.innerHTML = '';
      tbody.appendChild(tr);
    }
    return;
  }

  const rowNodes = [];
  const visible = filtered.slice(0, tableRenderLimit);
  // Rows are reused across renders when their markup is unchanged (see rowSig below).
  const previousRows = tableRowCache;
  const nextRows = new Map();
  visible.forEach((item, index) => {
    const tr = document.createElement('tr');
    if (item._isNew) tr.classList.add('row-new');
    tr.setAttribute('title', `Click row to inspect ${escapeHtml(item.name || item.symbol || 'token')}`);
    tr.addEventListener('click', (e) => {
      if (e && e.target && (e.target.closest('a') || e.target.closest('button'))) return;
      openModal(item);
    });

    const blk = item.block ? `#${String(item.block).slice(-6)}` : '-';
    const smWalletsList = (item.smart_wallets && item.smart_wallets.length > 0)
      ? item.smart_wallets
      : (item.smart_minters && item.smart_minters.length > 0 ? item.smart_minters : []);
    const isSm = smWalletsList.length > 0;
    const hasFollowed = smWalletsList.some(w => followedWallets.has(String(w).toLowerCase()));
    const isCluster = smWalletsList.length >= 3;
    const isReady = isSniperReady(item);

    let smBadge = '';
    if (hasFollowed) {
      const followedNames = smWalletsList.filter(w => followedWallets.has(String(w).toLowerCase()));
      smBadge = `<span class="badge-cluster" style="background: rgba(240, 185, 11, 0.2); border-color: rgba(240, 185, 11, 0.7); color: #ffd000;" title="⭐ Followed Alpha Hunter: ${escapeHtml(followedNames.join(', '))}">⭐</span>`;
    } else if (isCluster && isReady) {
      smBadge = `<span class="badge-cluster" title="🔥 ${smWalletsList.length} Smart Money Wallets Cluster & Sniper Ready">🔥${smWalletsList.length}🎯</span>`;
    } else if (isCluster) {
      smBadge = `<span class="badge-cluster" title="🔥 ${smWalletsList.length} Smart Money Wallets Cluster Inflow">🔥${smWalletsList.length}</span>`;
    } else if (isSm && isReady) {
      smBadge = `<span title="Smart Alpha & Sniper Ready: ${escapeHtml(smWalletsList.join(', '))}">🧠🎯</span>`;
    } else if (isReady) {
      smBadge = `<span title="Sniper Ready">🎯</span>`;
    } else if (isSm) {
      smBadge = `<span title="${escapeHtml(smWalletsList.join(', '))}">🧠</span>`;
    }

    // Standard badge
    const std = String(item.standard || 'ERC-721');
    let stdBadge = `<span style="color: var(--color-magenta); font-weight:600;">ERC-721</span>`;
    if (item.is_nft_surge) {
      stdBadge = `<span class="badge badge-magenta" style="box-shadow: 0 0 8px rgba(217, 70, 239, 0.4); font-weight:700;">🦋 SURGE (${item.secondary_sales_count || 0})</span>`;
    } else if (item.is_proxy_clone) {
      stdBadge = `<span class="badge badge-cyan" style="color: #00f0ff !important; background: rgba(0, 240, 255, 0.18) !important; border: 1px solid rgba(0, 240, 255, 0.5) !important; box-shadow: 0 0 10px rgba(0, 240, 255, 0.3); font-weight:800;" title="${escapeHtml(item.proxy_type || 'Proxy')} Master: ${escapeHtml(item.master_implementation || '')}">🧩 PROXY</span>`;
    } else if (std.includes('BEP-20')) {
      stdBadge = `<span style="color: var(--color-cyan); font-weight:600;">BEP-20</span>`;
    } else if (std.includes('Fair-Mint')) {
      stdBadge = `<span style="color: var(--color-yellow); font-weight:600;">Fair-Mint</span>`;
    }

    // Safety badge
    const rLvl = String(item.risk_level || 'SAFE').toUpperCase();
    const rBdg = String(item.risk_badge || '').toUpperCase();
    let safeBadge = `<span style="color: var(--color-green); font-weight:600;">🟢 Clean</span>`;
    if (rBdg.includes('HONEYPOT') || rLvl === 'RUG') {
      safeBadge = `<span class="badge badge-red">🚨 Honeypot</span>`;
    } else if (rBdg.includes('SERIAL') || (item.security && item.security.is_serial_dev)) {
      safeBadge = `<span class="badge badge-red">🚨 SerialDev</span>`;
    } else if (rBdg.includes('DRAIN') || (item.security && item.security.is_fund_drain)) {
      safeBadge = `<span class="badge badge-red">🚨 Drained</span>`;
    } else if (rBdg.includes('HIGH') || rLvl === 'HIGH') {
      safeBadge = `<span class="badge badge-yellow">🔴 HighRisk</span>`;
    } else if (rBdg.includes('MED') || rLvl === 'MED') {
      safeBadge = `<span style="color: var(--color-yellow); font-weight:600;">🟡 Med Risk</span>`;
    } else if (isReady) {
      safeBadge = `<span class="badge badge-green" style="box-shadow: 0 0 8px rgba(74, 222, 128, 0.4); font-weight: 700;">🎯 READY</span>`;
    }

    // Interest badge
    const intBadge = interestBadgeHtml(item.score);

    // Holders / Buys
    // Unknown counts are "—" and real zeros are "0"; the total supply is never shown as a holder / buy count
    const hbStr = `<span class="hld">${formatCount(item.holders)}</span> <span class="dim">/</span> <span class="buy">${formatCount(item.buys_24h)}</span>`;

    // LP / Vol & Market Cap
    const lpUsd = Number(item.lp_usd || 0);
    const mcapUsd = getSanitizedMcap(item) || 0;
    const volBnb = item.volume_bnb || item.nft_volume_bnb;
    const secVol = Number(item.secondary_vol_bnb || 0);
    let lpStr = `<span class="dim">—</span>`;
    if (lpUsd > 0) {
      const dexScreenerUrl = getDexScreenerUrl(item);
      lpStr = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight:600; text-decoration: none;" title="View Liquidity on DexScreener">$${lpUsd.toLocaleString(undefined, { maximumFractionDigits: 0 })} ↗</a>`;
      if (mcapUsd > 0) {
        lpStr += `<div class="sub-mcap" style="font-size: 10px; color: var(--color-cyan); font-weight:600;" title="Market Cap / FDV: $${formatCount(mcapUsd)}"><a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-cyan); text-decoration: none;">${mcapLabel(item)}: $${formatCount(mcapUsd)}</a></div>`;
      }
    } else if (item.is_nft_surge && secVol > 0) {
      lpStr = `<span style="color: var(--color-magenta); font-weight:700;" title="${item.secondary_sales_count || 0} Element sales">${formatBnb(secVol)} BNB 🦋</span>`;
    } else if (secVol > 0) {
      lpStr = `<span style="color: var(--color-cyan); font-weight:600;" title="${item.secondary_sales_count || 0} Element sales">${formatBnb(secVol)} BNB</span>`;
    } else if (volBnb && Number(volBnb) > 0) {
      lpStr = `<span style="color: var(--color-cyan); font-weight:600;">${formatBnb(volBnb)} BNB</span>`;
    } else if (mcapUsd > 0) {
      lpStr = `<span style="color: var(--color-cyan); font-weight:600;" title="Market Cap / FDV: $${formatCount(mcapUsd)}">${mcapLabel(item)}: $${formatCount(mcapUsd)}</span>`;
    }

    // Progress Bar
    let rawMs = item.max_supply;
    if (rawMs !== null && rawMs !== undefined) {
      const numMs = Number(rawMs);
      if (!isFinite(numMs) || numMs >= 1e20 || (typeof rawMs === 'string' && rawMs.length > 20)) {
        rawMs = null;
      }
    }
    const ms = Number(rawMs || 0);
    const tm = Number(item.total_minted || 0);

    let progBar = '';
    if (ms > 0) {
      let pct = (tm / ms) * 100;
      pct = Math.min(100, Math.max(0, pct));
      let progTag = 'MINT';
      let progColor = 'var(--color-cyan)';
      if (tm >= ms) {
        progTag = 'SOLD';
        progColor = 'var(--color-magenta)';
      } else if (pct >= 80) {
        progTag = 'HIGH';
        progColor = 'var(--color-red)';
      } else if (pct >= 50) {
        progTag = 'WARM';
        progColor = 'var(--color-yellow)';
      } else if (std.includes('BEP-20')) {
        progTag = 'TOKEN';
        progColor = 'var(--color-green)';
      }
      progBar = `
        <div class="progress-cell">
          <span class="prog-tag" style="color: ${progColor}">${progTag} ${pct.toFixed(0)}%</span>
          <div class="prog-bar-container">
            <div class="prog-bar-fill" style="width: ${pct}%; background-color: ${progColor};"></div>
          </div>
          <span class="prog-ratio">${formatCount(tm)}/${formatCount(ms)}</span>
        </div>
      `;
    } else {
      // No max supply found: an open edition, or a cap the worker could not read. Drawn as a striped bar, never a
      // full one: a solid 100% bar next to "MINT 1%" rows read as sold out, the opposite of what it means.
      const mintsLabel = `${formatCount(tm)} minted`;
      const isDexOnly = String(item.mint_price_str || '').toUpperCase().includes('DEX');
      const capTitle = (item.mint_terms && item.mint_terms.cap === 'unlimited')
        ? 'The contract reports an unlimited supply (open edition)' : UNCAPPED_MINT_TITLE;
      if (isDexOnly) {
        // A token with no mint function: its "minted" is just the total supply, not progress of anything.
        progBar = `<div class="progress-cell" title="No mint function: this token trades on DEX"><span class="prog-tag dim">ON DEX</span><span class="prog-ratio dim">${formatCount(tm)} supply</span></div>`;
      } else progBar = `
        <div class="progress-cell" title="${capTitle}">
          <span class="prog-tag" style="color: var(--color-cyan); font-weight:700;">NO CAP</span>
          <div class="prog-bar-container">
            <div class="prog-bar-fill prog-bar-uncapped" style="width: 100%;"></div>
          </div>
          <span class="prog-ratio" style="color: var(--text-white); font-weight: 600;">${mintsLabel}</span>
        </div>
      `;
    }

    // Mint Price
    const pStr = String(item.mint_price_str || '').toUpperCase();
    const vWei = Number(item.mint_value_wei || 0);
    const isFree = isFreeMintDrop(item);
    let priceStr = `<span class="dim">TBD</span>`;
    if (pStr.includes('DEX')) {
      priceStr = `<span style="color: var(--color-cyan);">DEX Traded</span>`;
    } else if (isFree) {
      priceStr = `<span style="color: var(--color-green); font-weight:700;">⭐ FREE</span>`;
    } else if (vWei > 0) {
      priceStr = `<span style="color: var(--color-yellow); font-weight:600;">💎 ${formatBnb(vWei / 1e18)} BNB</span>`;
    }

    // Floor / Trade
    const flUsd = Number(item.floor_price_usd || 0);
    const flBnb = Number(item.floor_price_bnb || 0);
    const tkUsd = Number(item.token_price_usd || item.market_price_usd || 0);
    let floorStr = '';
    if (flUsd > 0) {
      floorStr = `<span style="color: var(--color-cyan); font-weight:600;">${formatCryptoPrice(flUsd)}</span>`;
    } else if (flBnb > 0) {
      if (item.is_nft_surge) {
        floorStr = `<span style="color: var(--color-magenta); font-weight:700;">${formatBnb(flBnb)} BNB</span>`;
      } else {
        floorStr = `<span style="color: var(--color-cyan); font-weight:600;">${flBnb} BNB</span>`;
      }
    } else if (tkUsd > 0) {
      const dexScreenerUrl = getDexScreenerUrl(item);
      floorStr = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight:600; text-decoration: none;" title="View Live Chart on DexScreener">${formatCryptoPrice(tkUsd)} ↗</a>`;
      const chg24 = Number(item.price_change_24h || 0);
      if (chg24 !== 0) {
        const isUp = chg24 > 0;
        floorStr += ` <span class="badge ${isUp ? 'badge-green' : 'badge-red'}" style="font-size: 9px; padding: 1px 3px;" title="24h Price Change: ${chg24.toFixed(1)}%">${isUp ? '+' : ''}${chg24.toFixed(1)}%</span>`;
      }
    } else {
      if (ms > 0 && tm >= ms) {
        floorStr = `<span style="color: var(--color-yellow); font-weight:600;">100% Sold</span>`;
      } else if (item.market_stale || String(item.mint_price_str || '').toUpperCase().includes('DEX')) {
        // a token that already trades (or whose market data was cleared as stale) is not in its fair-mint phase
        floorStr = `<span class="dim">—</span>`;
      } else {
        floorStr = `<span style="color: var(--color-cyan); font-weight:600;">Fair Mint</span>`;
      }
    }
    floorStr += marketFreshnessHtml(item);   // "⚠ stale", or the age of data that has not been confirmed for a while

    const athProf = Number(item.ath_profit_usd || 0);
    const curProf = Number(item.current_profit_usd || 0);
    const maxProf = Math.max(athProf, curProf);
    if (maxProf > 0) {
      const pStrVal = formatCryptoPrice(maxProf, true);
      const multVal = item.ath_multiplier ? ` [${item.ath_multiplier}x]` : '';
      floorStr += ` <span class="badge badge-green" style="font-size: 9px; padding: 1px 4px; margin-left: 2px;" title="Peak ATH Post-Mint Net: ${pStrVal} / token${multVal}">🏆 ${pStrVal}</span>`;
    }

    const exitSignal = evaluatePortfolioExit(item, bnbPrice);
    if (exitSignal.can_swap && (exitSignal.signal.includes('MOON') || exitSignal.signal.includes('RECOUP') || exitSignal.signal.includes('CRASH'))) {
      const shortSig = exitSignal.signal.includes('10x') ? '🚀 10x' : (exitSignal.signal.includes('5x') ? '🚀 5x' : (exitSignal.signal.includes('2x') ? '🟢 2x' : '⚠️ -40%'));
      floorStr += ` <span class="badge ${exitSignal.badge_class}" style="font-size: 9px; padding: 1px 4px; margin-left: 2px;" title="${escapeHtml(exitSignal.recommendation)}">${shortSig}</span>`;
    }

    const isFav = isFavorited(item.contract, item);
    const starClass = isFav ? 'star-btn starred' : 'star-btn';
    const starIcon = isFav ? '★' : '☆';

    const isSec = (activeTab === 'SECONDARY');
    const isPort = (activeTab === 'PORTFOLIO');
    const isNft = std.includes('721') || std.includes('NFT');
    let cellHolders = hbStr;
    let cellLp = lpStr;
    let cellProg = progBar;
    let cellPrice = priceStr + ((!isSec && !isPort) ? mintTermsChipsHtml(item) : '');
    let cellFloor = floorStr;

    const tokenSym = item.symbol || item.name || item.contract || '';
    const tokenHash = '#token=' + encodeURIComponent(tokenSym);
    const tokenLink = `<a href="${tokenHash}" class="token-permalink" onclick="handleTokenClick(event, '${item.contract}')" title="Inspect ${escapeHtml(item.name || tokenSym)} (Ctrl+Click to open in new tab)"><strong>${item.name || 'Unknown'}</strong><span class="sym">(${item.symbol || ''})</span></a>`;
    let actBtn = `<a href="${tokenHash}" class="inspect-btn" onclick="handleTokenClick(event, '${item.contract}')" title="Inspect & Snipe CLI (Ctrl+Click to open in new tab)"><span class="desktop-only">Inspect ➔</span><span class="mobile-only">🔍 Inspect ➔</span></a>`;

    if (isSec) {
      const dexScreenerUrl = getDexScreenerUrl(item);
      if (lpUsd > 0) {
        cellLp = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight:700; text-decoration: none;" title="View Liquidity Pool on DexScreener">$${formatCount(lpUsd)} ↗</a>`;
      } else if (secVol > 0) {
        cellLp = `<span style="color: var(--color-magenta); font-weight:600;">${formatBnb(secVol)} BNB</span>`;
      } else {
        cellLp = `<span class="dim">—</span>`;
      }

      const mcapVal = getSanitizedMcap(item);
      if (mcapVal > 0) {
        const hasFdvDiff = item.fdv && item.market_cap && Math.abs(Number(item.fdv) - Number(item.market_cap)) > 100;
        cellProg = `
          <div style="display: flex; flex-direction: column; gap: 2px;">
            <a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight: 700; font-size: 13px; letter-spacing: 0.3px; text-decoration: none;" title="View Market Cap on DexScreener">$${formatCount(mcapVal)} ↗</a>
            ${hasFdvDiff ? `<span class="dim" style="font-size: 9px;">FDV: $${formatCount(item.fdv)}</span>` : ''}
          </div>
        `;
      } else {
        cellProg = `<span class="dim">—</span>`;
      }

      const volVal = Number(item.volume_24h || 0);
      if (volVal > 0) {
        cellPrice = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-cyan); font-weight: 700; text-decoration: none;" title="View 24h Volume on DexScreener">$${formatCount(volVal)} ↗</a>`;
      } else {
        cellPrice = `<span class="dim">—</span>`;
      }

      const tkUsd = Number(item.token_price_usd || item.market_price_usd || 0);
      if (tkUsd > 0) {
        const chg24 = Number(item.price_change_24h || 0);
        const isUp = chg24 >= 0;
        const chgBadge = chg24 !== 0
          ? `<span class="badge ${isUp ? 'badge-green' : 'badge-red'}" style="font-size: 9px; padding: 1px 4px; margin-left: 4px;">${isUp ? '+' : ''}${chg24.toFixed(1)}%</span>`
          : '';
        cellFloor = `
          <div style="display: flex; align-items: center; gap: 2px;">
            <a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight: 700; text-decoration: none;" title="Open live chart on DexScreener">${formatCryptoPrice(tkUsd)} ↗</a>
            ${chgBadge}
          </div>
        `;
      } else {
        cellFloor = `<span class="dim">—</span>`;
      }

      const dexUrl = dexScreenerUrl || `https://pancakeswap.finance/swap?outputCurrency=${item.contract}`;
      actBtn = `<button class="inspect-btn trade-dex-btn dex-screener-btn" onclick="event.stopPropagation(); window.open('${dexUrl}', '_blank')" style="border-color: var(--color-cyan); color: var(--color-cyan); font-weight: 700; background: rgba(0, 240, 255, 0.08);" title="View Live Chart & Order Book on DexScreener"><span class="desktop-only">📈 DexScreener ↗</span><span class="mobile-only">📈 Chart ↗</span></button>`;
    } else if (isPort) {
      if (item.contract === '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee') {
        cellHolders = `<span style="color: var(--color-green); font-weight:700;">Gas: ${Number(item.user_balance || 0).toFixed(4)} BNB</span>`;
        cellFloor = `<span class="badge badge-cyan" style="font-size: 11px;">⛽ GAS RESERVE</span>`;
        actBtn = `<a href="${tokenHash}" class="inspect-btn" onclick="handleTokenClick(event, '${item.contract}')" style="border-color: var(--color-cyan); color: var(--color-cyan); font-weight:700;"><span class="desktop-only">⛽ Gas Reserve</span><span class="mobile-only">⛽ Gas ➔</span></a>`;
      } else if (isNft) {
        cellHolders = `<span style="color: var(--color-green); font-weight:700;">Holdings: ${formatCount(item.user_balance || 1)} NFT</span>`;
        cellFloor = `<span class="badge ${exitSignal.badge_class}" style="font-size: 11px;">${exitSignal.badge}</span>`;
        actBtn = `<a href="${tokenHash}" class="inspect-btn" onclick="handleTokenClick(event, '${item.contract}')" style="border-color: var(--color-magenta); color: var(--color-magenta); font-weight:700;"><span class="desktop-only">🎨 Element List</span><span class="mobile-only">🎨 Element ➔</span></a>`;
      } else {
        const balNum = Number(item.user_balance || 0);
        const balStr = balNum >= 1000 ? formatCount(balNum) : (balNum < 0.01 ? balNum.toFixed(4) : balNum.toFixed(2));
        cellHolders = `<span style="color: var(--color-green); font-weight:700;">Holdings: ${balStr}</span>`;
        cellFloor = `<span class="badge ${exitSignal.badge_class}" style="font-size: 11px;">${exitSignal.badge}</span>`;
        actBtn = `<a href="${tokenHash}" class="inspect-btn" onclick="handleTokenClick(event, '${item.contract}')" style="border-color: #f0b90b; color: #f0b90b; font-weight:700;"><span class="desktop-only">🥞 1-Click Exit</span><span class="mobile-only">🥞 Exit CLI ➔</span></a>`;
      }
    }

    let rowHtml;
    if (isSec) {
      rowHtml = `
        <td class="col-fav"><button class="${starClass}" onclick="toggleFavorite('${item.contract}', event)" title="${isFav ? 'Remove from favorites' : 'Add to favorites'}">${starIcon}</button></td>
        <td class="col-num">${index + 1}</td>
        <td class="col-block"><code>${blk}</code></td>
        <td class="col-sm">${smBadge}</td>
        <td class="col-project">${tokenLink}${gateBadgeHtml(item)}</td>
        <td class="col-type">${stdBadge}</td>
        <td class="col-safety">${safeBadge}</td>
        <td class="col-interest">${intBadge}</td>
        <td class="col-floor" data-label="DEX PRICE"><span class="cell-val">${cellFloor}</span></td>
        <td class="col-progress" data-label="MARKET CAP">${cellProg}</td>
        <td class="col-price" data-label="24H VOLUME"><span class="cell-val">${cellPrice}</span></td>
        <td class="col-lp" data-label="PANCAKESWAP LP"><span class="cell-val">${cellLp}</span></td>
        <td class="col-holders" data-label="HOLDERS / BUYS"><span class="cell-val">${cellHolders}</span></td>
        <td class="col-action">${actBtn}</td>
      `;
    } else {
      rowHtml = `
        <td class="col-fav"><button class="${starClass}" onclick="toggleFavorite('${item.contract}', event)" title="${isFav ? 'Remove from favorites' : 'Add to favorites'}">${starIcon}</button></td>
        <td class="col-num">${index + 1}</td>
        <td class="col-block"><code>${blk}</code></td>
        <td class="col-sm">${smBadge}</td>
        <td class="col-project">${tokenLink}${gateBadgeHtml(item)}</td>
        <td class="col-type">${stdBadge}</td>
        <td class="col-safety">${safeBadge}</td>
        <td class="col-interest">${intBadge}</td>
        <td class="col-holders" data-label="${isPort ? 'HOLDINGS' : 'HOLDERS / BUYS'}"><span class="cell-val">${cellHolders}</span></td>
        <td class="col-lp" data-label="LIQUIDITY"><span class="cell-val">${cellLp}</span></td>
        <td class="col-progress" data-label="PROGRESS">${cellProg}</td>
        <td class="col-price" data-label="MINT PRICE"><span class="cell-val">${cellPrice}</span></td>
        <td class="col-floor" data-label="${isSec ? 'DEX PRICE' : (isPort ? 'RECOMMEND' : 'STATUS')}"><span class="cell-val">${cellFloor}</span></td>
        <td class="col-action">${actBtn}</td>
        ${(!isSec && !isPort) ? cardTermsCellHtml(item) : ''}
      `;
    }
    // Unchanged row: keep the <tr> already in the DOM (its click handler and cell listeners stay attached).
    const rowTitle = (typeof tr.getAttribute === 'function') ? tr.getAttribute('title') : '';
    const rowSig = `${tr.className || ''}\u0001${rowTitle}\u0001${rowHtml}`;
    const cachedRow = previousRows.get(item);
    if (cachedRow && cachedRow.sig === rowSig && !nextRows.has(item)) {
      nextRows.set(item, cachedRow);
      rowNodes.push(cachedRow.tr);
      return;
    }
    tr.innerHTML = rowHtml;
    nextRows.set(item, { sig: rowSig, tr });
    rowNodes.push(tr);
  });
  // Keep the rows just rendered plus recent older ones (up to TABLE_ROW_CACHE_MAX), so switching between two
  // filters or tabs reuses the rows of both. The oldest leftovers are dropped first.
  const merged = new Map();
  previousRows.forEach((cached, key) => { if (!nextRows.has(key)) merged.set(key, cached); });
  nextRows.forEach((cached, key) => merged.set(key, cached));
  const cacheLimit = Math.max(TABLE_ROW_CACHE_MAX, nextRows.size);
  while (merged.size > cacheLimit) merged.delete(merged.keys().next().value);
  tableRowCache = merged;
  patchRowNodes(tbody, rowNodes);

  // Update pagination button visibility and progress
  if (loadMoreWrap) {
    if (tableRenderLimit < filtered.length || (activeTab === 'RADAR' && activeFilter === 'ALL' && !searchQuery && hasMoreDrops)) {
      loadMoreWrap.style.display = 'block';
      const loadedCountEl = document.getElementById('loadedCount');
      const totalAvailEl = document.getElementById('totalAvailableCount');
      if (loadedCountEl) loadedCountEl.textContent = Math.min(filtered.length, tableRenderLimit);
      if (totalAvailEl) totalAvailEl.textContent = (activeTab === 'RADAR' && activeFilter === 'ALL' && !searchQuery && globalStats) ? globalStats.total : filtered.length;
    } else {
      loadMoreWrap.style.display = 'none';
    }
  }
}

// Progressive Batch Appender for smooth 60 FPS scrolling
let isBatchAppending = false;
function appendNextTableBatch() {
  if (isBatchAppending) return;
  isBatchAppending = true;
  try {
    const filtered = getFilteredDrops();
    if (tableRenderLimit >= filtered.length) return;
    const nextBatch = filtered.slice(tableRenderLimit, tableRenderLimit + TABLE_RENDER_CHUNK);
    if (nextBatch.length === 0) return;

  const tbody = document.getElementById('tableBody');
  if (!tbody) return;
  const fragment = document.createDocumentFragment();

  nextBatch.forEach((item, idx) => {
    const index = tableRenderLimit + idx;
    const tr = document.createElement('tr');
    if (item._isNew) tr.classList.add('row-new');
    tr.setAttribute('title', `Click row to inspect ${escapeHtml(item.name || item.symbol || 'token')}`);
    tr.addEventListener('click', (e) => {
      if (e && e.target && (e.target.closest('a') || e.target.closest('button'))) return;
      openModal(item);
    });

    const blk = item.block ? `#${String(item.block).slice(-6)}` : '-';
    const smWalletsList = (item.smart_wallets && item.smart_wallets.length > 0)
      ? item.smart_wallets
      : (item.smart_minters && item.smart_minters.length > 0 ? item.smart_minters : []);
    const isSm = smWalletsList.length > 0;
    const hasFollowed = smWalletsList.some(w => followedWallets.has(String(w).toLowerCase()));
    const isCluster = smWalletsList.length >= 3;
    const isReady = isSniperReady(item);

    let smBadge = '';
    if (hasFollowed) {
      const followedNames = smWalletsList.filter(w => followedWallets.has(String(w).toLowerCase()));
      smBadge = `<span class="badge-cluster" style="background: rgba(240, 185, 11, 0.2); border-color: rgba(240, 185, 11, 0.7); color: #ffd000;" title="⭐ Followed Alpha Hunter: ${escapeHtml(followedNames.join(', '))}">⭐</span>`;
    } else if (isCluster && isReady) {
      smBadge = `<span class="badge-cluster" title="🔥 ${smWalletsList.length} Smart Money Wallets Cluster & Sniper Ready">🔥${smWalletsList.length}🎯</span>`;
    } else if (isCluster) {
      smBadge = `<span class="badge-cluster" title="🔥 ${smWalletsList.length} Smart Money Wallets Cluster Inflow">🔥${smWalletsList.length}</span>`;
    } else if (isSm && isReady) {
      smBadge = `<span title="Smart Alpha & Sniper Ready: ${escapeHtml(smWalletsList.join(', '))}">🧠🎯</span>`;
    } else if (isReady) {
      smBadge = `<span title="Sniper Ready">🎯</span>`;
    } else if (isSm) {
      smBadge = `<span title="${escapeHtml(smWalletsList.join(', '))}">🧠</span>`;
    }

    // Standard badge
    const std = String(item.standard || 'ERC-721');
    let stdBadge = `<span style="color: var(--color-magenta); font-weight:600;">ERC-721</span>`;
    if (item.is_nft_surge) {
      stdBadge = `<span class="badge badge-magenta" style="box-shadow: 0 0 8px rgba(217, 70, 239, 0.4); font-weight:700;">🦋 SURGE (${item.secondary_sales_count || 0})</span>`;
    } else if (item.is_proxy_clone) {
      stdBadge = `<span class="badge badge-cyan" style="color: #00f0ff !important; background: rgba(0, 240, 255, 0.18) !important; border: 1px solid rgba(0, 240, 255, 0.5) !important; box-shadow: 0 0 10px rgba(0, 240, 255, 0.3); font-weight:800;" title="${escapeHtml(item.proxy_type || 'Proxy')} Master: ${escapeHtml(item.master_implementation || '')}">🧩 PROXY</span>`;
    } else if (std.includes('BEP-20')) {
      stdBadge = `<span style="color: var(--color-cyan); font-weight:600;">BEP-20</span>`;
    } else if (std.includes('Fair-Mint')) {
      stdBadge = `<span style="color: var(--color-yellow); font-weight:600;">Fair-Mint</span>`;
    }

    // Safety badge
    const rLvl = String(item.risk_level || 'SAFE').toUpperCase();
    const rBdg = String(item.risk_badge || '').toUpperCase();
    let safeBadge = `<span style="color: var(--color-green); font-weight:600;">🟢 Clean</span>`;
    if (rBdg.includes('HONEYPOT') || rLvl === 'RUG') {
      safeBadge = `<span class="badge badge-red">🚨 Honeypot</span>`;
    } else if (rBdg.includes('SERIAL') || (item.security && item.security.is_serial_dev)) {
      safeBadge = `<span class="badge badge-red">🚨 SerialDev</span>`;
    } else if (rBdg.includes('DRAIN') || (item.security && item.security.is_fund_drain)) {
      safeBadge = `<span class="badge badge-red">🚨 Drained</span>`;
    } else if (rBdg.includes('HIGH') || rLvl === 'HIGH') {
      safeBadge = `<span class="badge badge-yellow">🔴 HighRisk</span>`;
    } else if (rBdg.includes('MED') || rLvl === 'MED') {
      safeBadge = `<span style="color: var(--color-yellow); font-weight:600;">🟡 Med Risk</span>`;
    } else if (isReady) {
      safeBadge = `<span class="badge badge-green" style="box-shadow: 0 0 8px rgba(74, 222, 128, 0.4); font-weight: 700;">🎯 READY</span>`;
    }

    // Interest Badge
    const intBadge = interestBadgeHtml(item.score);

    // Progress Bar
    const isNft = std.includes('721') || std.includes('NFT');
    const msRaw = Number(item.max_supply || 0);
    const ms = (isFinite(msRaw) && msRaw < 1e20) ? msRaw : 0;   // 1e20+ is the "uncapped" sentinel, not a real cap
    const tm = Number(item.total_minted || 0);
    let progPct = 0;
    let progLabel = 'TOKEN 0%';
    if (isNft && ms > 0) {
      progPct = Math.min(100, Math.max(0, (tm / ms) * 100));
      progLabel = `${progPct.toFixed(0)}%`;
    } else if (ms > 0) {
      progPct = Math.min(100, Math.max(0, (tm / ms) * 100));
      progLabel = `TOKEN ${progPct.toFixed(0)}%`;
    } else if (String(item.mint_price_str || '').toUpperCase().includes('DEX')) {
      progLabel = `ON DEX · ${formatCount(tm)} supply`;   // no mint function: no progress to show
      progPct = 0;
    } else {
      progLabel = `NO CAP · ${formatCount(tm)} minted`;   // striped bar below, not a full "100%" one
      progPct = 100;
    }
    const uncapped = !(ms > 0) && !String(item.mint_price_str || '').toUpperCase().includes('DEX');
    if (ms > 0 && tm >= ms) {
      progLabel = 'SOLD 100%';
      progPct = 100;
    }
    const progColor = uncapped ? 'var(--color-cyan)' : progPct >= 99 ? 'var(--color-magenta)' : (progPct >= 50 ? 'var(--color-green)' : 'var(--color-cyan)');
    const progBar = `
      <div style="font-size: 10px; color: ${progColor}; font-weight: 700; margin-bottom: 2px;">${progLabel}</div>
      <div class="progress-bar-wrap"${uncapped ? ` title="${UNCAPPED_MINT_TITLE}"` : ''}>
        <div class="progress-bar-fill${uncapped ? ' prog-bar-uncapped' : ''}" style="width: ${progPct}%;${uncapped ? '' : ` background: ${progColor};`}"></div>
      </div>
    `;

    // Mint Price
    const pStr = String(item.mint_price_str || '').toUpperCase();
    const vWei = Number(item.mint_value_wei || 0);
    const isFree = isFreeMintDrop(item);
    let priceStr = `<span class="dim">TBD</span>`;
    if (pStr.includes('DEX')) {
      priceStr = `<span style="color: var(--color-cyan);">DEX Traded</span>`;
    } else if (isFree) {
      priceStr = `<span style="color: var(--color-green); font-weight:700;">⭐ FREE</span>`;
    } else if (vWei > 0) {
      priceStr = `<span style="color: var(--color-yellow); font-weight:600;">💎 ${formatBnb(vWei / 1e18)} BNB</span>`;
    }

    // Floor / Trade
    const flUsd = Number(item.floor_price_usd || 0);
    const flBnb = Number(item.floor_price_bnb || 0);
    const tkUsd = Number(item.token_price_usd || item.market_price_usd || 0);
    let floorStr = '';
    if (flUsd > 0) {
      floorStr = `<span style="color: var(--color-cyan); font-weight:600;">${formatCryptoPrice(flUsd)}</span>`;
    } else if (flBnb > 0) {
      if (item.is_nft_surge) {
        floorStr = `<span style="color: var(--color-magenta); font-weight:700;">${formatBnb(flBnb)} BNB</span>`;
      } else {
        floorStr = `<span style="color: var(--color-cyan); font-weight:600;">${flBnb} BNB</span>`;
      }
    } else if (tkUsd > 0) {
      const dexScreenerUrl = getDexScreenerUrl(item);
      floorStr = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight:600; text-decoration: none;" title="View Live Chart on DexScreener">${formatCryptoPrice(tkUsd)} ↗</a>`;
      const chg24 = Number(item.price_change_24h || 0);
      if (chg24 !== 0) {
        const isUp = chg24 > 0;
        floorStr += ` <span class="badge ${isUp ? 'badge-green' : 'badge-red'}" style="font-size: 9px; padding: 1px 3px;" title="24h Price Change: ${chg24.toFixed(1)}%">${isUp ? '+' : ''}${chg24.toFixed(1)}%</span>`;
      }
    } else {
      if (ms > 0 && tm >= ms) {
        floorStr = `<span style="color: var(--color-yellow); font-weight:600;">100% Sold</span>`;
      } else if (item.market_stale || String(item.mint_price_str || '').toUpperCase().includes('DEX')) {
        // a token that already trades (or whose market data was cleared as stale) is not in its fair-mint phase
        floorStr = `<span class="dim">—</span>`;
      } else {
        floorStr = `<span style="color: var(--color-cyan); font-weight:600;">Fair Mint</span>`;
      }
    }
    floorStr += marketFreshnessHtml(item);   // "⚠ stale", or the age of data that has not been confirmed for a while

    const athProf = Number(item.ath_profit_usd || 0);
    const curProf = Number(item.current_profit_usd || 0);
    const maxProf = Math.max(athProf, curProf);
    if (maxProf > 0) {
      const pStrVal = formatCryptoPrice(maxProf, true);
      const multVal = item.ath_multiplier ? ` [${item.ath_multiplier}x]` : '';
      floorStr += ` <span class="badge badge-green" style="font-size: 9px; padding: 1px 4px; margin-left: 2px;" title="Peak ATH Post-Mint Net: ${pStrVal} / token${multVal}">🏆 ${pStrVal}</span>`;
    }

    const exitSignal = evaluatePortfolioExit(item, bnbPrice);
    const isPort = (activeTab === 'PORTFOLIO');
    let cellHolders = `${formatCount(item.holders)} / ${formatCount(item.buys_24h)}`;
    if (isPort && item.user_balance > 0) {
      const balVal = isNft ? `${item.user_balance} NFT` : formatCount(item.user_balance);
      const usdVal = exitSignal.holding_usd > 0 ? ` (~${formatCryptoPrice(exitSignal.holding_usd)})` : '';
      cellHolders = `<strong style="color: var(--color-green);">${balVal}</strong>${usdVal}`;
    }

    let cellFloor = floorStr;
    if (isPort && item.user_balance > 0) {
      cellFloor = `
        <div style="display: flex; flex-direction: column; gap: 2px;">
          <span>${floorStr}</span>
          <span class="badge ${exitSignal.badge_class || 'badge-cyan'}" style="font-size: 9px; padding: 1px 4px;" title="${escapeHtml(exitSignal.recommendation)}">
            ${exitSignal.badge}
          </span>
        </div>
      `;
    }

    const isFav = isFavorited(item.contract, item);
    const starClass = isFav ? 'btn-star starred' : 'btn-star';
    const starIcon = isFav ? '★' : '☆';

    const mcapUsdBatch = getSanitizedMcap(item) || 0;
    let lpStr = '—';
    if (Number(item.lp_usd || 0) > 0) {
      const dexScreenerUrl = getDexScreenerUrl(item);
      lpStr = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight:600; text-decoration: none;" title="View Liquidity on DexScreener">$${formatCount(item.lp_usd)} ↗</a>`;
      if (mcapUsdBatch > 0) {
        lpStr += `<div class="sub-mcap" style="font-size: 10px; color: var(--color-cyan); font-weight:600;" title="Market Cap / FDV: $${formatCount(mcapUsdBatch)}"><a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-cyan); text-decoration: none;">${mcapLabel(item)}: $${formatCount(mcapUsdBatch)}</a></div>`;
      }
    } else if (item.is_nft_surge && Number(item.secondary_vol_bnb || 0) > 0) {
      lpStr = `<span style="color: var(--color-magenta); font-weight:600;">${formatBnb(item.secondary_vol_bnb)} BNB</span>`;
    } else if (mcapUsdBatch > 0) {
      lpStr = `<span style="color: var(--color-cyan); font-weight:600;" title="Market Cap / FDV: $${formatCount(mcapUsdBatch)}">${mcapLabel(item)}: $${formatCount(mcapUsdBatch)}</span>`;
    }

    const isSec = (activeTab === 'SECONDARY');
    let cellLp = lpStr;
    let cellProg = progBar;
    let cellPrice = priceStr + ((!isSec && activeTab !== 'PORTFOLIO') ? mintTermsChipsHtml(item) : '');

    const tokenSym = item.symbol || item.name || item.contract || '';
    const tokenHash = '#token=' + encodeURIComponent(tokenSym);
    const tokenLink = `<a href="${tokenHash}" class="token-permalink" onclick="handleTokenClick(event, '${item.contract}')" title="Inspect ${escapeHtml(item.name || tokenSym)} (Ctrl+Click to open in new tab)"><strong>${item.name || 'Unknown'}</strong><span class="sym">(${item.symbol || ''})</span></a>`;
    let actBtn = `<a href="${tokenHash}" class="inspect-btn" onclick="handleTokenClick(event, '${item.contract}')" title="Inspect & Snipe CLI (Ctrl+Click to open in new tab)"><span class="desktop-only">Inspect ➔</span><span class="mobile-only">🔍 Inspect ➔</span></a>`;

    if (isSec) {
      const dexScreenerUrl = getDexScreenerUrl(item);
      if (Number(item.lp_usd || 0) > 0) {
        cellLp = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight:700; text-decoration: none;" title="View Liquidity Pool on DexScreener">$${formatCount(item.lp_usd)} ↗</a>`;
      } else if (Number(item.secondary_vol_bnb || 0) > 0) {
        cellLp = `<span style="color: var(--color-magenta); font-weight:600;">${formatBnb(item.secondary_vol_bnb)} BNB</span>`;
      } else {
        cellLp = `<span class="dim">—</span>`;
      }

      const mcapVal = getSanitizedMcap(item);
      if (mcapVal > 0) {
        const hasFdvDiff = item.fdv && item.market_cap && Math.abs(Number(item.fdv) - Number(item.market_cap)) > 100;
        cellProg = `
          <div style="display: flex; flex-direction: column; gap: 2px;">
            <a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight: 700; font-size: 13px; letter-spacing: 0.3px; text-decoration: none;" title="View Market Cap on DexScreener">$${formatCount(mcapVal)} ↗</a>
            ${hasFdvDiff ? `<span class="dim" style="font-size: 9px;">FDV: $${formatCount(item.fdv)}</span>` : ''}
          </div>
        `;
      } else {
        cellProg = `<span class="dim">—</span>`;
      }

      const volVal = Number(item.volume_24h || 0);
      if (volVal > 0) {
        cellPrice = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-cyan); font-weight: 700; text-decoration: none;" title="View 24h Volume on DexScreener">$${formatCount(volVal)} ↗</a>`;
      } else {
        cellPrice = `<span class="dim">—</span>`;
      }

      const tkUsd = Number(item.token_price_usd || item.market_price_usd || 0);
      if (tkUsd > 0) {
        const chg24 = Number(item.price_change_24h || 0);
        const isUp = chg24 >= 0;
        const chgBadge = chg24 !== 0
          ? `<span class="badge ${isUp ? 'badge-green' : 'badge-red'}" style="font-size: 9px; padding: 1px 4px; margin-left: 4px;">${isUp ? '+' : ''}${chg24.toFixed(1)}%</span>`
          : '';
        cellFloor = `
          <div style="display: flex; align-items: center; gap: 2px;">
            <a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight: 700; text-decoration: none;" title="Open live chart on DexScreener">${formatCryptoPrice(tkUsd)} ↗</a>
            ${chgBadge}
          </div>
        `;
      } else {
        cellFloor = `<span class="dim">—</span>`;
      }

      const dexUrl = dexScreenerUrl || `https://pancakeswap.finance/swap?outputCurrency=${item.contract}`;
      actBtn = `<button class="inspect-btn trade-dex-btn dex-screener-btn" onclick="event.stopPropagation(); window.open('${dexUrl}', '_blank')" style="border-color: var(--color-cyan); color: var(--color-cyan); font-weight: 700; background: rgba(0, 240, 255, 0.08);" title="View Live Chart & Order Book on DexScreener"><span class="desktop-only">📈 DexScreener ↗</span><span class="mobile-only">📈 Chart ↗</span></button>`;
    } else if (isPort && item.user_balance > 0) {
      if (item.is_native_gas) {
        cellHolders = `<span style="color: var(--color-green); font-weight:700;">Gas: ${Number(item.user_balance || 0).toFixed(4)} BNB</span>`;
        cellFloor = `<span class="badge badge-cyan" style="font-size: 11px;">⛽ GAS RESERVE</span>`;
        actBtn = `<a href="${tokenHash}" class="inspect-btn" onclick="handleTokenClick(event, '${item.contract}')" style="border-color: var(--color-cyan); color: var(--color-cyan); font-weight:700;"><span class="desktop-only">⛽ Gas Reserve</span><span class="mobile-only">⛽ Gas ➔</span></a>`;
      } else if (isNft) {
        cellHolders = `<span style="color: var(--color-green); font-weight:700;">Holdings: ${formatCount(item.user_balance || 1)} NFT</span>`;
        cellFloor = `<span class="badge ${exitSignal.badge_class}" style="font-size: 11px;">${exitSignal.badge}</span>`;
        actBtn = `<a href="${tokenHash}" class="inspect-btn" onclick="handleTokenClick(event, '${item.contract}')" style="border-color: var(--color-magenta); color: var(--color-magenta); font-weight:700;"><span class="desktop-only">🎨 Element List</span><span class="mobile-only">🎨 Element ➔</span></a>`;
      } else {
        const balNum = Number(item.user_balance || 0);
        const balStr = balNum >= 1000 ? formatCount(balNum) : (balNum < 0.01 ? balNum.toFixed(4) : balNum.toFixed(2));
        cellHolders = `<span style="color: var(--color-green); font-weight:700;">Holdings: ${balStr}</span>`;
        cellFloor = `<span class="badge ${exitSignal.badge_class}" style="font-size: 11px;">${exitSignal.badge}</span>`;
        actBtn = `<a href="${tokenHash}" class="inspect-btn" onclick="handleTokenClick(event, '${item.contract}')" style="border-color: #f0b90b; color: #f0b90b; font-weight:700;"><span class="desktop-only">🥞 1-Click Exit</span><span class="mobile-only">🥞 Exit CLI ➔</span></a>`;
      }
    }

    if (isSec) {
      tr.innerHTML = `
        <td class="col-fav"><button class="${starClass}" onclick="toggleFavorite('${item.contract}', event)" title="${isFav ? 'Remove from favorites' : 'Add to favorites'}">${starIcon}</button></td>
        <td class="col-num">${index + 1}</td>
        <td class="col-block"><code>${blk}</code></td>
        <td class="col-sm">${smBadge}</td>
        <td class="col-project">${tokenLink}${gateBadgeHtml(item)}</td>
        <td class="col-type">${stdBadge}</td>
        <td class="col-safety">${safeBadge}</td>
        <td class="col-interest">${intBadge}</td>
        <td class="col-floor" data-label="DEX PRICE"><span class="cell-val">${cellFloor}</span></td>
        <td class="col-progress" data-label="MARKET CAP">${cellProg}</td>
        <td class="col-price" data-label="24H VOLUME"><span class="cell-val">${cellPrice}</span></td>
        <td class="col-lp" data-label="PANCAKESWAP LP"><span class="cell-val">${cellLp}</span></td>
        <td class="col-holders" data-label="HOLDERS / BUYS"><span class="cell-val">${cellHolders}</span></td>
        <td class="col-action">${actBtn}</td>
      `;
    } else {
      tr.innerHTML = `
        <td class="col-fav"><button class="${starClass}" onclick="toggleFavorite('${item.contract}', event)" title="${isFav ? 'Remove from favorites' : 'Add to favorites'}">${starIcon}</button></td>
        <td class="col-num">${index + 1}</td>
        <td class="col-block"><code>${blk}</code></td>
        <td class="col-sm">${smBadge}</td>
        <td class="col-project">${tokenLink}${gateBadgeHtml(item)}</td>
        <td class="col-type">${stdBadge}</td>
        <td class="col-safety">${safeBadge}</td>
        <td class="col-interest">${intBadge}</td>
        <td class="col-holders" data-label="${isPort ? 'HOLDINGS' : 'HOLDERS / BUYS'}"><span class="cell-val">${cellHolders}</span></td>
        <td class="col-lp" data-label="LIQUIDITY"><span class="cell-val">${cellLp}</span></td>
        <td class="col-progress" data-label="PROGRESS">${cellProg}</td>
        <td class="col-price" data-label="MINT PRICE"><span class="cell-val">${cellPrice}</span></td>
        <td class="col-floor" data-label="${isSec ? 'DEX PRICE' : (isPort ? 'RECOMMEND' : 'STATUS')}"><span class="cell-val">${cellFloor}</span></td>
        <td class="col-action">${actBtn}</td>
        ${(!isSec && !isPort) ? cardTermsCellHtml(item) : ''}
      `;
    }
    fragment.appendChild(tr);
  });

  tbody.appendChild(fragment);
  tableRenderLimit += nextBatch.length;

  const footerCountEl = document.getElementById('footerCount');
  const totalDisplay = globalStats ? globalStats.total : allDrops.length;
  if (footerCountEl) {
    footerCountEl.textContent = `Showing ${Math.min(filtered.length, tableRenderLimit)} of ${totalDisplay} collections`;
  }

  const loadMoreWrap = document.getElementById('loadMoreWrap');
  if (loadMoreWrap) {
    const loadedCountEl = document.getElementById('loadedCount');
    if (loadedCountEl) loadedCountEl.textContent = Math.min(filtered.length, tableRenderLimit);
    if (tableRenderLimit >= filtered.length && !hasMoreDrops) {
      loadMoreWrap.style.display = 'none';
    }
  }
  } finally {
    isBatchAppending = false;
  }
}
if (typeof window !== 'undefined') window.appendNextTableBatch = appendNextTableBatch;

function handleLoadMoreClick() {
  const filtered = getFilteredDrops();
  if (tableRenderLimit < filtered.length) {
    appendNextTableBatch();
  } else if (hasMoreDrops && !isLoadingMore) {
    loadMoreDrops();
  }
}
if (typeof window !== 'undefined') {
  window.handleLoadMoreClick = handleLoadMoreClick;
}

// Dynamic Holder Distribution Intelligence
// Analyzes on-chain fair-mint state, smart money concentration, sniper allocation, and creator share
function computeHolderDistribution(item) {
  if (!item) return { dev: 0, insider: 0, smart: 0, sniper: 0, fresh: 0, public: 100, isUnminted: false };
  if (item._cachedHolderDist) return item._cachedHolderDist;
  
  // If explicitly provided with realistic distribution, preserve it
  if (item.hold && typeof item.hold === 'object' && Object.keys(item.hold).length > 0 &&
      (item.hold.dev > 0 || item.hold.insider > 0 || item.hold.sniper > 0 || item.hold.smart > 0 || item.hold.fresh > 0)) {
    const res = { ...item.hold, isUnminted: false };
    item._cachedHolderDist = res;
    return res;
  }
  if (item.holder_distribution && typeof item.holder_distribution === 'object' && Object.keys(item.holder_distribution).length > 0 &&
      (item.holder_distribution.dev > 0 || item.holder_distribution.insider > 0 || item.holder_distribution.sniper > 0 || item.holder_distribution.smart > 0 || item.holder_distribution.fresh > 0)) {
    const res = { ...item.holder_distribution, isUnminted: false };
    item._cachedHolderDist = res;
    return res;
  }

  const tm = Number(item.total_minted || 0);
  const holders = Number(item.holders || 1);
  const isUnminted = (tm === 0 && holders <= 1);

  if (isUnminted) {
    const res = {
      isUnminted: true,
      dev: 0.0,
      insider: 0.0,
      smart: 0.0,
      sniper: 0.0,
      fresh: 0.0,
      public: 100.0
    };
    item._cachedHolderDist = res;
    return res;
  }

  // Dynamic calculation for minted tokens
  // 1. Dev Pre-Mine (creator holding from security audit or market data)
  let devHold = 0.0;
  if (item.security && item.security.creator_percent) {
    devHold = Math.min(100, Math.max(0, Number(item.security.creator_percent) * 100));
  } else if (item.dev_holding_pct) {
    devHold = Math.min(100, Math.max(0, Number(item.dev_holding_pct)));
  }

  // 2. Smart Money Share (tracked .ai hunters)
  const smartCount = Array.isArray(item.smart_wallets) ? item.smart_wallets.length : (item.smart_wallets ? 1 : 0);
  let smartHold = 0.0;
  if (smartCount > 0 && holders > 0) {
    smartHold = Math.min(30.0, Math.max(1.0, (smartCount / Math.max(holders, 1)) * 100));
  }

  // 3. Insider / Dev Syndicate Cluster
  const clusterDevs = Number(item.cluster_devs || (item.security && item.security.cluster_devs) || 1);
  const clusterN = Number(item.cluster_n || (item.security && item.security.cluster_n) || 1);
  let insiderHold = 0.0;
  if (clusterDevs >= 2 || clusterN >= 3) {
    insiderHold = Math.min(30.0, 5.0 + (clusterDevs * 3.0) + (clusterN * 1.5));
  }

  // 4. Snipers / Block-0 velocity
  const buys1h = Number(item.buys_1h || 0);
  const pctClaimed = Number(item.percent_claimed || (item.max_supply ? (tm / item.max_supply * 100) : 0));
  let sniperHold = 0.0;
  if (pctClaimed >= 50 || buys1h >= 20) {
    sniperHold = Math.min(25.0, Math.max(2.0, (buys1h > 30 ? 14.0 : 8.0)));
  }

  // 5. Fresh low-nonce minters
  let freshHold = (holders > 5) ? Math.min(8.0, 4.0) : 0.0;

  // Remainder is organic public community minters
  const nonPublic = devHold + insiderHold + sniperHold + smartHold + freshHold;
  const publicHold = Math.max(0.0, 100.0 - nonPublic);

  const result = {
    isUnminted: false,
    dev: Number(devHold.toFixed(1)),
    insider: Number(insiderHold.toFixed(1)),
    smart: Number(smartHold.toFixed(1)),
    sniper: Number(sniperHold.toFixed(1)),
    fresh: Number(freshHold.toFixed(1)),
    public: Number(publicHold.toFixed(1))
  };
  item._cachedHolderDist = result;
  return result;
}

// In-Modal Token Navigation System (Prev / Next & Arrow Keys)
let currentInspectItem = null;

function getInspectNavContext(item = currentInspectItem) {
  if (!item) return { list: [], index: -1, total: 0, hasPrev: false, hasNext: false, prevItem: null, nextItem: null };
  const cAddr = String(item.contract || '').toLowerCase().trim();

  // Try current active filtered table list first
  let list = [];
  try {
    list = (typeof getFilteredDrops === 'function') ? getFilteredDrops() : [];
  } catch (e) {
    list = [];
  }

  let idx = list.findIndex(d => String(d.contract || '').toLowerCase().trim() === cAddr);

  // If not found in current filtered list, fallback to allDrops (local or global/window)
  if (idx === -1) {
    if (typeof allDrops !== 'undefined' && Array.isArray(allDrops) && allDrops.length > 0) {
      list = allDrops;
      idx = list.findIndex(d => String(d.contract || '').toLowerCase().trim() === cAddr);
    }
    if (idx === -1 && typeof global !== 'undefined' && Array.isArray(global.allDrops) && global.allDrops.length > 0) {
      list = global.allDrops;
      idx = list.findIndex(d => String(d.contract || '').toLowerCase().trim() === cAddr);
    }
    if (idx === -1 && typeof window !== 'undefined' && Array.isArray(window.allDrops) && window.allDrops.length > 0) {
      list = window.allDrops;
      idx = list.findIndex(d => String(d.contract || '').toLowerCase().trim() === cAddr);
    }
  }

  const hasPrev = idx > 0;
  const hasNext = idx !== -1 && idx < list.length - 1;
  const prevItem = hasPrev ? list[idx - 1] : null;
  const nextItem = hasNext ? list[idx + 1] : null;

  return {
    list,
    index: idx,
    total: list.length,
    hasPrev,
    hasNext,
    prevItem,
    nextItem
  };
}

// ==========================================================================
// 📊 Telemetry & Granular Interaction Analytics (PostHog & Vercel)
// ==========================================================================

function trackAnalytics(eventName, eventData = {}) {
  try {
    if (typeof window !== 'undefined') {
      if (window.isDevOptedOut) return;
      if (window.posthog && typeof window.posthog.capture === 'function') {
        window.posthog.capture(eventName, eventData);
      } else if (typeof window.va === 'function') {
        window.va('event', { name: eventName, data: eventData });
      }
    }
  } catch (err) {
    // Non-blocking telemetry guard
  }
}

let _modalOpenTimestamp = null;
let _modalObservedSections = new Set();
let _modalSectionObserver = null;
let _activeInspectItem = null;

function setupModalTelemetry(item, source = 'direct') {
  _activeInspectItem = item;
  _modalOpenTimestamp = Date.now();
  _modalObservedSections = new Set();

  const isFree = Boolean(item.is_free || item.mint_price_bnb === '0' || Number(item.mint_price_usd) === 0);
  const mcapNum = Number(item.market_cap_usd || item.fdv_usd || item.mcap || 0);
  const bondingPct = Number(item.bonding_progress || item.mint_progress || 0);

  trackAnalytics('token_inspected', {
    name: item.name || '',
    symbol: item.symbol || '',
    contract: item.contract || '',
    is_free: isFree,
    mint_progress: bondingPct,
    risk_level: item.risk_badge || item.risk_level || 'Clean',
    mcap_usd: mcapNum,
    has_lp: Boolean(Number(item.lp_usd || 0) > 0),
    nav_source: source
  });

  if (typeof window === 'undefined' || typeof IntersectionObserver === 'undefined') return;

  if (_modalSectionObserver) {
    _modalSectionObserver.disconnect();
  }

  const modalContent = document.getElementById('modalContent');
  if (!modalContent) return;

  _modalSectionObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const sectionName = entry.target.getAttribute('data-modal-section');
        if (sectionName && !_modalObservedSections.has(sectionName)) {
          _modalObservedSections.add(sectionName);
          trackAnalytics('modal_section_viewed', {
            section: sectionName,
            token_symbol: item.symbol || '',
            token_contract: item.contract || '',
            is_free: isFree,
            total_sections_viewed: _modalObservedSections.size
          });
        }
      }
    });
  }, {
    root: modalContent,
    threshold: 0.3
  });

  setTimeout(() => {
    const freshContent = document.getElementById('modalContent');
    if (freshContent && _modalSectionObserver) {
      freshContent.querySelectorAll('[data-modal-section]').forEach(el => {
        _modalSectionObserver.observe(el);
      });
    }
  }, 120);
}

function finishModalTelemetry(exitMethod = 'closed') {
  if (!_modalOpenTimestamp || !_activeInspectItem) return;

  const dwellSeconds = Math.max(1, Math.round((Date.now() - _modalOpenTimestamp) / 1000));
  const viewedArr = Array.from(_modalObservedSections);

  trackAnalytics('modal_dwell_time', {
    token_symbol: _activeInspectItem.symbol || '',
    token_contract: _activeInspectItem.contract || '',
    dwell_seconds: dwellSeconds,
    sections_count: viewedArr.length,
    sections_viewed: viewedArr,
    exit_method: exitMethod
  });

  if (_modalSectionObserver) {
    _modalSectionObserver.disconnect();
    _modalSectionObserver = null;
  }
  _modalOpenTimestamp = null;
  _activeInspectItem = null;
  _modalObservedSections.clear();
}

// `source` = where the copy was tapped (mint_section, shortcut_bar, sniper_section, mint_gate, pancakeswap_exit).
// `copied` = whether the text reached the clipboard; callers that cannot tell leave it out.
function trackCliCopy(type, symbol, contract, source = 'unknown', copied) {
  const props = {
    type: type,
    token_symbol: symbol || '',
    token_contract: contract || '',
    source: source
  };
  if (typeof copied === 'boolean') props.copied = copied;
  trackAnalytics('cli_copied', props);
}

// Copies text for a one-tap button. Resolves true when the text reached the clipboard. The async Clipboard API is
// called synchronously (Safari only allows that inside the tap); a hidden textarea is the fallback where it is missing
// or refuses (older mobile browsers, non-secure origins).
function copyTextToClipboard(text) {
  try {
    const clipboard = (typeof navigator !== 'undefined') ? navigator.clipboard : null;
    if (clipboard && typeof clipboard.writeText === 'function') {
      return Promise.resolve(clipboard.writeText(text)).then(() => true, () => legacyCopyText(text));
    }
  } catch (err) {
    // no async clipboard here: the legacy path below
  }
  return Promise.resolve(legacyCopyText(text));
}

function legacyCopyText(text) {
  try {
    if (typeof document === 'undefined' || typeof document.createElement !== 'function' || typeof document.execCommand !== 'function' ||
        !document.body || typeof document.body.appendChild !== 'function' || typeof document.body.removeChild !== 'function') {
      return false;
    }
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    try {
      if (typeof area.select === 'function') area.select();
      if (typeof area.setSelectionRange === 'function') area.setSelectionRange(0, text.length);
      return Boolean(document.execCommand('copy'));
    } finally {
      document.body.removeChild(area);
    }
  } catch (err) {
    return false;
  }
}

// The Sniper CLI copy buttons of the token modal (pinned bar, Mint section, Sniper section, Mint gate). The command
// is the one openModal built for the open token; every tap is tracked with its source.
let currentSniperCliSnippet = '';   // set by openModal for the token modal that is open

function copyModalSniperCli(btn, source) {
  const text = currentSniperCliSnippet;
  if (!text) return;
  const item = currentInspectItem || {};
  const symbol = item.symbol || '';
  const contract = item.contract || '';
  copyTextToClipboard(text).then(copied => {
    trackCliCopy('sniper_cli', symbol, contract, source, copied);
    flashCopyLabel(btn, copied ? 'Copied!' : 'Copy failed');
  });
}

// Shows a short label on a copy button, then puts its own label back.
function flashCopyLabel(btn, label) {
  if (!btn) return;
  if (btn._copyLabel === undefined) btn._copyLabel = btn.textContent;
  btn.textContent = label;
  clearTimeout(btn._copyTimer);
  btn._copyTimer = setTimeout(() => { btn.textContent = btn._copyLabel; }, 1500);
}

function trackExternalClick(target, symbol, contract) {
  trackAnalytics('external_link_clicked', {
    target: target,
    token_symbol: symbol || '',
    token_contract: contract || ''
  });
}

function navigateInspectModal(direction, via = 'arrow') {
  const modal = (typeof document !== 'undefined') ? document.getElementById('inspectModal') : null;
  if (!modal) return;
  const isOpen = Boolean(modal.open || modal.hasAttribute('open'));
  if (!isOpen && typeof modal.open !== 'undefined') return;
  if (!currentInspectItem) return;

  const { list, index } = getInspectNavContext(currentInspectItem);
  if (!list || list.length === 0 || index === -1) return;

  const targetIndex = index + direction;
  const swiped = via === 'swipe';
  if (targetIndex >= 0 && targetIndex < list.length) {
    const nextTarget = list[targetIndex];
    if (nextTarget) {
      const reason = swiped
        ? (direction > 0 ? 'next_swipe' : 'prev_swipe')
        : (direction > 0 ? 'next_arrow' : 'prev_arrow');
      finishModalTelemetry(reason);
      openModal(nextTarget, reason);
      const content = document.getElementById('modalContent');
      if (content) content.scrollTop = 0;
      if (swiped) playModalSwipeFeedback(content, direction, 'enter');
    }
  } else if (swiped) {
    trackAnalytics('modal_swipe_edge', { direction: direction > 0 ? 'next' : 'prev' });
    playModalSwipeFeedback(document.getElementById('modalContent'), direction, 'edge');
  }

  // Pre-load older drops if approaching the end of loaded batch in radar feed
  if (direction > 0 && targetIndex >= list.length - 2 && hasMoreDrops && !isLoadingMore && activeTab === 'RADAR' && activeFilter === 'ALL' && !searchQuery) {
    if (typeof loadMoreDrops === 'function') {
      loadMoreDrops().catch(() => {});
    }
  }
}

// ==========================================================================
// 👆 Swipe between tokens in the inspect modal
// Swipe left = next token, swipe right = previous token, the same as the ‹ › buttons. A gesture counts only when it is
// one quick, mostly horizontal drag of at least MODAL_SWIPE_MIN_PX. Vertical scrolls, text selection, the CLI box and
// anything inside a horizontal scroller (wide tables, long code lines) keep their normal behaviour.
// ==========================================================================
const MODAL_SWIPE_MIN_PX = 60;
const MODAL_SWIPE_MAX_MS = 900;
const MODAL_SWIPE_RATIO = 1.5; // horizontal travel must beat vertical travel by this factor
const MODAL_SWIPE_IGNORE_SELECTOR = '.cli-box, pre, code, button, a, input, textarea, select, label, [contenteditable], [data-no-swipe]';

function prefersReducedMotion() {
  try {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      && Boolean(window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  } catch (_) {
    return false;
  }
}

function hasTextSelection() {
  try {
    const sel = (typeof window !== 'undefined' && typeof window.getSelection === 'function') ? window.getSelection() : null;
    return Boolean(sel) && String(sel).length > 0;
  } catch (_) {
    return false;
  }
}

// True when the touch started inside a box that scrolls sideways on its own (a wide table, a long code line).
function isInHorizontalScroller(node, stopAt) {
  if (typeof getComputedStyle !== 'function') return false;
  for (let n = node; n && n !== stopAt; n = n.parentElement) {
    if (n.scrollWidth > n.clientWidth + 1 && /(auto|scroll)/.test(getComputedStyle(n).overflowX || '')) return true;
  }
  return false;
}

// Slide the modal toward the new token (kind 'enter') or nudge it when there is no token that way (kind 'edge').
// Both are skipped for reduced-motion users; the navigation itself still happens.
function playModalSwipeFeedback(content, direction, kind) {
  if (!content || !content.classList || typeof content.classList.add !== 'function') return;
  if (prefersReducedMotion()) return;
  const side = direction > 0 ? 'next' : 'prev';
  const cls = kind === 'edge' ? `is-swipe-edge-${side}` : `is-swipe-enter-${side}`;
  content.classList.remove('is-swipe-enter-next', 'is-swipe-enter-prev', 'is-swipe-edge-next', 'is-swipe-edge-prev');
  if (typeof content.offsetWidth === 'number') void content.offsetWidth; // restart the CSS animation
  content.classList.add(cls);
  setTimeout(() => {
    if (content.classList && typeof content.classList.remove === 'function') content.classList.remove(cls);
  }, 260);
}

function setupModalSwipeNav() {
  const content = (typeof document !== 'undefined' && typeof document.getElementById === 'function')
    ? document.getElementById('modalContent') : null;
  if (!content || typeof content.addEventListener !== 'function') return;
  // #modalContent is static markup and survives every re-render, so bind once.
  if (content.dataset && content.dataset.swipeNav === '1') return;
  if (content.dataset) content.dataset.swipeNav = '1';

  let start = null;
  content.addEventListener('touchstart', (e) => {
    start = null;
    if (!e.touches || e.touches.length !== 1) return; // a second finger means pinch or zoom, never a swipe
    const target = e.target;
    if (target && typeof target.closest === 'function' && target.closest(MODAL_SWIPE_IGNORE_SELECTOR)) return;
    if (isInHorizontalScroller(target, content)) return;
    const t = e.touches[0];
    start = { x: t.clientX, y: t.clientY, at: Date.now() };
  }, { passive: true });

  content.addEventListener('touchcancel', () => { start = null; }, { passive: true });

  content.addEventListener('touchend', (e) => {
    const s = start;
    start = null;
    const t = e.changedTouches && e.changedTouches[0];
    if (!s || !t) return;
    if (Date.now() - s.at > MODAL_SWIPE_MAX_MS) return;
    const dx = t.clientX - s.x;
    const dy = t.clientY - s.y;
    if (Math.abs(dx) < MODAL_SWIPE_MIN_PX) return;
    if (Math.abs(dx) < Math.abs(dy) * MODAL_SWIPE_RATIO) return; // a vertical scroll
    if (hasTextSelection()) return; // a long-press selection is not a swipe
    navigateInspectModal(dx < 0 ? 1 : -1, 'swipe');
  }, { passive: true });
}

if (typeof window !== 'undefined') {
  window.trackAnalytics = trackAnalytics;
  window.trackCliCopy = trackCliCopy;
  window.trackExternalClick = trackExternalClick;
  window.setupModalTelemetry = setupModalTelemetry;
  window.finishModalTelemetry = finishModalTelemetry;
  window.navigateInspectModal = navigateInspectModal;
  window.getInspectNavContext = getInspectNavContext;
}

// ==========================================================================
// 🔗 URL Hash Permalinks for Modals (e.g. /#token=PTAH or /#inspect=0x...)
// ==========================================================================

function parseTokenFromHash(hashStr) {
  if (!hashStr || typeof hashStr !== 'string') return '';
  const clean = hashStr.replace(/^#/, '').trim();
  if (!clean) return '';
  if (clean.includes('=')) {
    try {
      const params = new URLSearchParams(clean);
      const val = params.get('token') || params.get('inspect') || '';
      if (val) return val.trim();
    } catch (_) {}
  }
  const match = clean.match(/^(?:token|inspect)=([^&]+)/i);
  if (match && match[1]) {
    try {
      return decodeURIComponent(match[1]).trim();
    } catch (_) {
      return match[1].trim();
    }
  }
  return '';
}

function isSameToken(item, query) {
  if (!item || !query) return false;
  const q = String(query).trim().toLowerCase();
  const sym = String(item.symbol || '').trim().toLowerCase();
  const name = String(item.name || '').trim().toLowerCase();
  const addr = String(item.contract || '').trim().toLowerCase();
  return (sym && sym === q) || (name && name === q) || (addr && addr === q);
}

function findTokenByQuery(query) {
  if (!query) return null;
  const q = String(query).trim().toLowerCase();
  if (!q) return null;

  // Search in memory allDrops (or global.allDrops in unit tests)
  const dropsList = (typeof allDrops !== 'undefined' && Array.isArray(allDrops))
    ? allDrops
    : ((typeof global !== 'undefined' && Array.isArray(global.allDrops)) ? global.allDrops : []);

  if (dropsList.length > 0) {
    // 1. Exact symbol match has highest priority
    let found = dropsList.find(d => d && d.symbol && d.symbol.toLowerCase() === q);
    if (found) return found;

    // 2. Contract address match
    found = dropsList.find(d => d && d.contract && d.contract.toLowerCase() === q);
    if (found) return found;

    // 3. Project name match
    found = dropsList.find(d => d && d.name && d.name.toLowerCase() === q);
    if (found) return found;
  }

  // Search in pulseItemsMap if available
  const pMap = (typeof pulseItemsMap !== 'undefined') ? pulseItemsMap : ((typeof global !== 'undefined') ? global.pulseItemsMap : null);
  if (pMap && typeof pMap.values === 'function') {
    for (const item of pMap.values()) {
      if (!item) continue;
      if (item.symbol && item.symbol.toLowerCase() === q) return item;
      if (item.contract && item.contract.toLowerCase() === q) return item;
      if (item.name && item.name.toLowerCase() === q) return item;
    }
  }

  return null;
}

function setModalHash(item) {
  if (typeof window === 'undefined' || !window.location || !item) return;
  const sym = item.symbol || item.name || item.contract;
  if (!sym) return;
  const targetHash = '#token=' + encodeURIComponent(sym);
  if (window.location.hash !== targetHash) {
    try {
      window.location.hash = targetHash;
    } catch (e) {
      if (window.location) window.location.hash = targetHash;
    }
  }
}

function clearModalHash() {
  if (typeof window === 'undefined' || !window.location) return;
  const hash = window.location.hash || '';
  if (hash.startsWith('#token=') || hash.startsWith('#inspect=')) {
    if (window.history && typeof window.history.replaceState === 'function' && window.location.pathname) {
      try {
        const cleanUrl = window.location.pathname + (window.location.search || '');
        window.history.replaceState(null, '', cleanUrl);
      } catch (e) {
        window.location.hash = '';
      }
    } else {
      window.location.hash = '';
    }
    if (window.location.hash && (window.location.hash.startsWith('#token=') || window.location.hash.startsWith('#inspect='))) {
      try { window.location.hash = ''; } catch (_) {}
    }
  }
}

// Share link for a token: the modal's own #token= deep link, plus UTM tags so visits that come from a shared link
// can be measured in PostHog. The origin and path come from the current page; nothing else in the query string is
// carried over. The inline script in index.html strips utm_* from the address bar after the visit is recorded and
// keeps the hash, so the modal still opens from the link.
const TOKEN_SHARE_LABEL = '🔗 Copy share link';

function buildTokenShareUrl(symbol) {
  const sym = String(symbol || '').trim();
  if (!sym || typeof window === 'undefined' || !window.location) return '';
  const loc = window.location;
  const origin = loc.origin && loc.origin !== 'null' ? loc.origin : '';
  const path = loc.pathname || '/';
  const enc = encodeURIComponent(sym);
  return origin + path + '?utm_source=share&utm_medium=modal&utm_campaign=' + enc + '#token=' + enc;
}

function copyTokenShareLink(btn) {
  const sym = btn && btn.dataset ? btn.dataset.shareSymbol : '';
  const url = buildTokenShareUrl(sym);
  if (!url) return;

  const flash = (text) => {
    if (!btn) return;
    btn.textContent = text;
    setTimeout(() => { btn.textContent = TOKEN_SHARE_LABEL; }, 1500);
  };
  const onCopied = () => {
    trackAnalytics('token_share_link_copied', { token_symbol: sym });
    flash('✓ Link copied');
  };
  const onFailed = () => {
    // Last resort: show the link so it can still be copied by hand.
    if (typeof window !== 'undefined' && typeof window.prompt === 'function') {
      window.prompt('Copy this link:', url);
    } else {
      flash('Copy failed');
    }
  };
  const execCopy = () => {
    let ok = false;
    try {
      if (typeof document !== 'undefined' && typeof document.createElement === 'function' && typeof document.execCommand === 'function' && document.body && typeof document.body.appendChild === 'function') {
        const ta = document.createElement('textarea');
        ta.value = url;
        ta.setAttribute('readonly', '');
        if (ta.style) { ta.style.position = 'fixed'; ta.style.opacity = '0'; }
        document.body.appendChild(ta);
        if (typeof ta.select === 'function') ta.select();
        ok = Boolean(document.execCommand('copy'));
        if (typeof document.body.removeChild === 'function') document.body.removeChild(ta);
      }
    } catch (_) {
      ok = false;
    }
    if (ok) onCopied(); else onFailed();
  };

  const clip = (typeof navigator !== 'undefined' && navigator) ? navigator.clipboard : null;
  if (clip && typeof clip.writeText === 'function') {
    let pending = null;
    try { pending = clip.writeText(url); } catch (_) { pending = null; }
    if (pending && typeof pending.then === 'function') {
      pending.then(onCopied, execCopy);
      return;
    }
  }
  execCopy();
}

if (typeof window !== 'undefined') {
  window.copyTokenShareLink = copyTokenShareLink;
}

async function resolveTokenAndInspect(tokenQuery, attempts = 0) {
  if (!tokenQuery) return;
  const modal = (typeof document !== 'undefined') ? document.getElementById('inspectModal') : null;
  const isAlreadyOpen = Boolean(modal && (modal.open || (typeof modal.hasAttribute === 'function' && modal.hasAttribute('open'))));

  if (isAlreadyOpen && currentInspectItem && isSameToken(currentInspectItem, tokenQuery)) {
    return;
  }

  let target = findTokenByQuery(tokenQuery);

  if (!target && attempts >= 2 && typeof fetch !== 'undefined') {
    try {
      const apiUrl = (typeof BACKEND_URL !== 'undefined' ? BACKEND_URL : '') + `/api/projects?search=${encodeURIComponent(tokenQuery)}`;
      const res = await fetch(apiUrl);
      if (res.ok) {
        const data = await res.json();
        if (data && data.projects && data.projects.length > 0) {
          target = data.projects[0];
        }
      }
    } catch (e) {
      // API fallback error
    }
  }

  if (target) {
    openModal(target, 'url_hash');
  } else if (attempts < 25) {
    setTimeout(() => {
      if (typeof window !== 'undefined' && window.location) {
        const currentHashToken = parseTokenFromHash(window.location.hash);
        if (currentHashToken && currentHashToken.toLowerCase() !== tokenQuery.toLowerCase()) {
          return;
        }
      }
      resolveTokenAndInspect(tokenQuery, attempts + 1);
    }, 150);
  }
}

function checkUrlHash() {
  if (typeof window === 'undefined' || !window.location) return;
  const tokenQuery = parseTokenFromHash(window.location.hash);
  if (!tokenQuery) return;
  resolveTokenAndInspect(tokenQuery);
}

function handleHashChange() {
  if (typeof window === 'undefined' || !window.location) return;
  const tokenQuery = parseTokenFromHash(window.location.hash);
  const modal = (typeof document !== 'undefined') ? document.getElementById('inspectModal') : null;
  const isAlreadyOpen = Boolean(modal && (modal.open || (typeof modal.hasAttribute === 'function' && modal.hasAttribute('open'))));

  if (tokenQuery) {
    resolveTokenAndInspect(tokenQuery);
  } else if (isAlreadyOpen) {
    if (typeof modal.close === 'function') {
      modal.close();
    } else {
      modal.removeAttribute('open');
    }
  }
}

function handleTokenClick(event, contract) {
  if (!event) return;
  // If user clicked with Ctrl, Cmd (metaKey), Shift, Alt, or middle mouse button (button === 1),
  // let browser handle default link navigation (opening in new tab or window).
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button === 1) {
    return;
  }
  // Standard left click: prevent default full page jump/reload and open modal in-page
  if (typeof event.preventDefault === 'function') event.preventDefault();
  if (typeof event.stopPropagation === 'function') event.stopPropagation();

  const target = findTokenByQuery(contract);
  if (target) {
    openModal(target, 'table_click');
  }
}

if (typeof window !== 'undefined') {
  window.setModalHash = setModalHash;
  window.clearModalHash = clearModalHash;
  window.checkUrlHash = checkUrlHash;
  window.handleHashChange = handleHashChange;
  window.handleTokenClick = handleTokenClick;
  window.parseTokenFromHash = parseTokenFromHash;
  window.findTokenByQuery = findTokenByQuery;
  window.resolveTokenAndInspect = resolveTokenAndInspect;
}

// Modal Inspection Dialog
// List responses leave out oversized mint calldata (has_mint_calldata=true marks it). Loads the full record's
// calldata into `item`; resolves true if it got one.
// The complete record of a contract from the API, or null.
async function fetchFullProject(contract) {
  try {
    const c = String(contract || '').toLowerCase();
    if (!c) return null;
    const res = await apiFetch(`/api/projects?search=${encodeURIComponent(c)}&limit=5`);
    if (!res || !res.ok) return null;
    const data = await res.json();
    return (data.projects || []).find(p => String(p.contract || '').toLowerCase() === c) || null;
  } catch (e) {
    return null;
  }
}

async function loadFullMintCalldata(item) {
  const full = await fetchFullProject(item.contract);
  if (!full || !full.mint_calldata) return false;
  item.mint_calldata = full.mint_calldata;
  return true;
}

// Some lists carry slim records (`_slim`): the Pulse cards (runners, launches, NFT mints: only what a card shows) and the
// Secondary list (`view=list`: no modal-only fields). A slim record opens the modal at once, then is replaced by the
// complete record when the API answers: one request, only on a click. The full fields are copied into every object the
// page holds for that contract (the list row, the Pulse row, the one the modal shows), so later opens are complete.
const slimPulseRequested = new Set();
function upgradeSlimItem(item) {
  const c = String((item && item.contract) || '').toLowerCase();
  if (!c || slimPulseRequested.has(c)) return;
  slimPulseRequested.add(c);
  fetchFullProject(c).then(full => {
    if (!full) { slimPulseRequested.delete(c); return; }   // try again on the next click
    const apply = (obj) => {
      if (obj && obj._slim) { Object.assign(obj, full); delete obj._slim; }
    };
    allDrops.forEach(d => { if (String((d && d.contract) || '').toLowerCase() === c) apply(d); });
    apply(item);
    pulseItemsMap.set(c, full);
    if (currentInspectItem && String(currentInspectItem.contract || '').toLowerCase() === c) {
      openModal(currentInspectItem._slim ? full : currentInspectItem, 'redraw');
    }
  });
}

// Pinned "Mint / Snipe" shortcut at the bottom of the token modal. Replay analysis: only ~17% of sessions that opened a
// token reached the Mint action / Sniper CLI sections far down the modal. The bar jumps straight there (and is tracked),
// then hides itself while either target section is on screen.
let _modalShortcutObserver = null;

function jumpToModalSection(name, sym, isFree, isDexTraded, soldOut) {
  endModalOpenGuard(); // a deliberate jump must not be undone by the open-at-top guard
  trackAnalytics('modal_shortcut_clicked', {
    target: name,
    token_symbol: sym,
    is_free: Boolean(isFree),
    is_dex_traded: Boolean(isDexTraded),
    sold_out: Boolean(soldOut)
  });
  const content = document.getElementById('modalContent');
  const el = content && typeof content.querySelector === 'function' && content.querySelector(`[data-modal-section="${name}"]`);
  if (!el || typeof el.scrollIntoView !== 'function') return;
  const reduceMotion = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
}

function setupModalShortcut() {
  if (_modalShortcutObserver) {
    _modalShortcutObserver.disconnect();
    _modalShortcutObserver = null;
  }
  const content = document.getElementById('modalContent');
  if (!content || typeof content.querySelector !== 'function' || typeof IntersectionObserver === 'undefined') return;
  const bar = content.querySelector('#modalShortcutBar');
  if (!bar) return;
  const targets = content.querySelectorAll('[data-modal-section="mint_action"], [data-modal-section="sniper_cli"]');
  const onScreen = new Set();
  _modalShortcutObserver = new IntersectionObserver(entries => {
    entries.forEach(e => { if (e.isIntersecting) onScreen.add(e.target); else onScreen.delete(e.target); });
    bar.classList.toggle('is-hidden', onScreen.size > 0);
  }, { root: content, threshold: 0.15 });
  targets.forEach(t => _modalShortcutObserver.observe(t));
}

// A token opens at the TOP of the modal. For a short window after opening, any scroll the user did not cause (wheel,
// touch, key, click) is undone, so nothing (focus handling, late layout, a stray programmatic scroll) can leave the modal
// opening halfway down at the Mint / Sniper / PancakeSwap sections. A deliberate scroll or the shortcut button ends it.
let _modalOpenGuard = null;

function endModalOpenGuard() {
  if (_modalOpenGuard) {
    _modalOpenGuard();
    _modalOpenGuard = null;
  }
}

function holdModalAtTop(content) {
  endModalOpenGuard();
  if (!content || typeof content.addEventListener !== 'function' || typeof content.removeEventListener !== 'function') return;
  // `?scroll=down` is the screenshot helper that deliberately scrolls the modal; leave it alone.
  try { if (new URLSearchParams(window.location.search).get('scroll') === 'down') return; } catch (_) { /* no URL API */ }
  let userMoved = false;
  const mark = () => { userMoved = true; };
  const onScroll = () => { if (!userMoved && content.scrollTop > 0) content.scrollTop = 0; };
  const userEvents = ['wheel', 'touchstart', 'pointerdown', 'keydown'];
  userEvents.forEach(ev => content.addEventListener(ev, mark, { passive: true, capture: true }));
  content.addEventListener('scroll', onScroll, { passive: true });
  const timer = setTimeout(endModalOpenGuard, 1500);
  _modalOpenGuard = () => {
    clearTimeout(timer);
    userEvents.forEach(ev => content.removeEventListener(ev, mark, { capture: true }));
    content.removeEventListener('scroll', onScroll);
  };
}

// The token modal itself lives in features/modal.js and loads on first use (prefetched when the page goes idle, see
// prefetchFeatures), so its ~44 KB is not parsed at startup. Once loaded it draws synchronously, as it always did; only
// an open before the module has arrived waits for it. Returns a promise that settles once the modal is drawn.
let modalFeature = null;
function openModal(item, source = 'direct') {
  if (modalFeature) {
    modalFeature.openModal(item, source);
    return Promise.resolve();
  }
  return loadFeature('modal')
    .then((m) => {
      modalFeature = m;
      m.openModal(item, source);
    })
    .catch((err) => console.error('Token modal failed to load:', err));
}

let currentHofFilter = 'ALL';

// Open All-Time Post-Mint Profit Hall of Fame Leaderboard Modal
function openHallOfFameModal(filterMode = 'ALL') {
  currentHofFilter = filterMode;
  const modal = document.getElementById('hallOfFameModal');
  const content = document.getElementById('hofModalContent');
  if (!modal || !content) return;

  if (typeof window !== 'undefined' && window.va) {
    window.va('event', { name: 'view_hall_of_fame', data: { filter: filterMode } });
  }

  const stats = computeStats(allDrops);
  let list = stats.allGainers;
  if (filterMode === 'SOLD') {
    list = stats.soldGainers;
  } else if (filterMode === 'MAJOR') {
    list = stats.majorGainers;
  }

  // Strict non-zero guard & validity check: completely filter out zero-price/profit entries and non-legit drops
  list = list.filter(g => isValidTopMarketItem(g.item || g) && g.athPrice > 0 && (g.athProfit > 0 || g.profit > 0));

  let rowsHtml = '';
  if (!list || list.length === 0) {
    rowsHtml = `<tr><td colspan="7" style="text-align:center; padding: 24px; color: var(--text-dim);">No post-mint gains recorded for current filter. As secondary trades settle on Element and PancakeSwap, top performers will appear here.</td></tr>`;
  } else {
    list.forEach((g, idx) => {
      const rankNum = idx + 1;
      let rankBadge = `<span class="hof-rank">${rankNum}</span>`;
      if (rankNum === 1) rankBadge = `<span class="hof-rank hof-rank-1">🥇 #1</span>`;
      else if (rankNum === 2) rankBadge = `<span class="hof-rank hof-rank-2">🥈 #2</span>`;
      else if (rankNum === 3) rankBadge = `<span class="hof-rank hof-rank-3">🥉 #3</span>`;
      else rankBadge = `<span class="hof-rank" style="color: var(--text-dim);">#${rankNum}</span>`;

      const mintCostStr = g.isFree
        ? '<span style="color: var(--color-green); font-weight:700;">FREE</span>'
        : (g.mintUsd > 0 ? formatCryptoPrice(g.mintUsd) : '<span class="dim" title="Mint cost not known">TBD</span>');
      const athPriceStr = formatCryptoPrice(g.athPrice);
      const athProfitStr = formatCryptoPrice(g.athProfit, true);
      const athMultiplier = g.multiplier || (g.item && g.item.ath_multiplier) || null;   // the API sends ath_multiplier, not "multiplier"
      const multStr = athMultiplier ? `<span class="badge badge-yellow" style="font-size: 10px;">${athMultiplier}x</span>` : (g.isFree ? `<span class="badge badge-green" style="font-size: 10px;">FREE ALPHA</span>` : '');

      // Profit % needs a KNOWN mint cost. A free or unknown mint has no base to divide by: no % is shown
      // (the net profit and multiplier next to it are real); there is no assumed base price.
      const mintCost = (g.mintUsd === null || g.mintUsd === undefined) ? null : Number(g.mintUsd);
      const athPrice = Math.max(Number(g.athPrice || 0), Number(g.secUsd || 0));
      let gainPct = null;
      if (mintCost > 0 && athPrice > 0) {
        gainPct = ((athPrice - mintCost) / mintCost) * 100;
      }
      const isUp = gainPct === null || gainPct >= 0;
      const gainPctStr = gainPct === null ? '' : (isUp ? '+' : '') + (gainPct >= 1000 ? Math.round(gainPct).toLocaleString('en-US') : gainPct.toFixed(1)) + '%';
      const profitPctBadge = gainPct !== null && athPrice > 0 ? `<span class="badge ${isUp ? 'badge-green' : 'badge-red'}" style="font-size: 10px; font-weight: 800; padding: 2px 5px; letter-spacing: 0.2px;">${gainPctStr}</span>` : '';

      const curPriceStr = g.secUsd > 0 ? formatCryptoPrice(g.secUsd) : '<span class="dim">—</span>';
      const curNetStr = g.profit > 0 ? `<span style="color: var(--color-green); font-weight:600;">${formatCryptoPrice(g.profit, true)}</span>` : '<span class="dim">—</span>';

      const statusBadge = g.isSold
        ? `<span class="badge badge-magenta" style="font-size: 10px;">Sold Out</span>`
        : `<span class="badge badge-cyan" style="font-size: 10px;">Active</span>`;

      // Marketplace / DEX links
      const std = String((g.item && g.item.standard) || g.standard || '');
      const isNft = std.includes('721') || std.includes('NFT');
      const cleanName = (g.name || '').replace(/\([^)]*\)/g, '').trim();
      const slug = cleanName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
      const elementUrl = (g.item && g.item.market_url) || (slug ? `https://element.market/collections/${slug}` : `https://element.market/collections/${g.contract}`);
      const dexScreenerUrl = (g.item && g.item.pair_url && g.item.pair_url.includes('dexscreener.com'))
        ? g.item.pair_url
        : `https://dexscreener.com/bsc/${g.contract}`;
      const pancakeUrl = `https://pancakeswap.finance/swap?outputCurrency=${g.contract}`;
      const bscUrl = `https://bscscan.com/address/${g.contract}`;

      let actionLinks = '';
      if (isNft) {
        actionLinks += `<a href="${elementUrl}" target="_blank" rel="noopener" class="hof-btn hof-btn-mkt" title="Open on Element NFT Market">🦋 Element</a>`;
        actionLinks += `<a href="${bscUrl}" target="_blank" rel="noopener" class="hof-btn hof-btn-scan" title="View BscScan">🔍 BscScan</a>`;
      } else {
        actionLinks += `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" class="hof-btn hof-btn-chart" title="View Live Chart & Trades on DexScreener">📈 DexScreener</a>`;
        actionLinks += `<a href="${pancakeUrl}" target="_blank" rel="noopener" class="hof-btn hof-btn-dex" title="Trade on PancakeSwap">🥞 Trade</a>`;
      }

      rowsHtml += `
        <tr>
          <td class="hof-col-rank">${rankBadge}</td>
          <td class="hof-col-project">
            <div style="font-weight: 700; color: var(--text-white);">${escapeHtml(g.name)}</div>
            <div style="font-size: 10.5px; color: var(--color-cyan);">${escapeHtml(g.symbol)} <span style="color: var(--text-dim); font-size: 9.5px;">(${g.contract.slice(0, 6)}...${g.contract.slice(-4)})</span></div>
          </td>
          <td class="hof-col-mint">${mintCostStr}</td>
          <td class="hof-col-ath">
            <div style="display: flex; align-items: center; gap: 6px;">
              <span style="font-weight: 700; color: var(--color-yellow);">${athPriceStr}</span>
              ${profitPctBadge}
            </div>
            <div style="font-size: 10.5px; color: var(--color-green); font-weight: 700; margin-top: 2px;">Net: ${athProfitStr} ${multStr}</div>
          </td>
          <td class="hof-col-current">
            <div>${curPriceStr}</div>
            <div style="font-size: 10.5px;">${curNetStr}</div>
          </td>
          <td class="hof-col-status">
            <div>${statusBadge}</div>
            <div style="font-size: 10px; color: var(--text-dim); margin-top: 2px;">${formatCount(g.holders)} holders</div>
          </td>
          <td class="hof-col-action">
            <div class="hof-action-links">${actionLinks}</div>
          </td>
        </tr>
      `;
    });
  }

  const topPeak = list[0] ? Math.max(list[0].athProfit, list[0].profit) : 0;
  const topPeakStr = formatCryptoPrice(topPeak, true);

  content.innerHTML = `
    <div class="hof-header">
      <div class="hof-title-group">
        <h2>🏆 ALL-TIME POST-MINT PROFIT LEADERBOARD</h2>
        <p class="hof-subtitle">Persistent Hall of Fame tracking peak All-Time High (ATH) net gains, secondary floor/DEX prices, and multipliers across all indexed Agent ID drops.${topGainersSeedLabel() ? ` <span class="badge badge-yellow" style="font-size: 10px;" title="Part of this list comes from a frozen seed file">${topGainersSeedLabel()}</span>` : ''}</p>
      </div>
      <button class="modal-close-btn" id="closeHofBtn" aria-label="Close modal">✕ CLOSE</button>
    </div>

    <div class="hof-controls">
      <div class="hof-filters">
        <button class="hof-filter-btn ${filterMode === 'ALL' ? 'active' : ''}" id="hofFilterAll">🌟 All Gainers (${stats.allGainers.length})</button>
        <button class="hof-filter-btn ${filterMode === 'MAJOR' ? 'active' : ''}" id="hofFilterMajor">💎 Major Gainers (≥$0.001) (${stats.majorGainers.length})</button>
        <button class="hof-filter-btn ${filterMode === 'SOLD' ? 'active' : ''}" id="hofFilterSold">🔒 Sold Out Only (${stats.soldGainers.length})</button>
      </div>
      <div class="hof-summary-pill">
        📈 Tracking ${list.length} Profitable Drops │ Top Gain: ${topPeakStr} / token
      </div>
    </div>

    <div class="hof-table-wrap">
      <table class="hof-table">
        <thead>
          <tr>
            <th style="width: 60px;">Rank</th>
            <th>Collection / Token</th>
            <th style="width: 100px;">Mint Price</th>
            <th style="width: 165px;">Peak ATH (% Gain)</th>
            <th style="width: 130px;">Current Price</th>
            <th style="width: 110px;">Status</th>
            <th style="width: 185px;">Market Links</th>
          </tr>
        </thead>
        <tbody>
          ${rowsHtml}
        </tbody>
      </table>
    </div>
  `;

  if (typeof modal.showModal === 'function') {
    modal.showModal();
  } else {
    modal.setAttribute('open', '');
  }

  const closeBtn = document.getElementById('closeHofBtn');
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      modal.close ? modal.close() : modal.removeAttribute('open');
    });
  }

  const btnAll = document.getElementById('hofFilterAll');
  if (btnAll) {
    btnAll.addEventListener('click', () => openHallOfFameModal('ALL'));
  }

  const btnMajor = document.getElementById('hofFilterMajor');
  if (btnMajor) {
    btnMajor.addEventListener('click', () => openHallOfFameModal('MAJOR'));
  }

  const btnSold = document.getElementById('hofFilterSold');
  if (btnSold) {
    btnSold.addEventListener('click', () => openHallOfFameModal('SOLD'));
  }
}

// ---------------------------------------------------------------------------------------------------
// Browser-side BSC reads. Every public-node read goes through rpcReadBody(): per-node backoff with
// jitter, rotation to the next node, a short cache of identical reads and de-duplication of identical
// in-flight reads. Only the methods in RPC_READ_METHODS get this. Transactions and wallet requests
// (eth_sendTransaction, eth_sendRawTransaction, wallet_*, and the injected provider's request()) never
// reach this layer, so they are never retried, cached or de-duplicated here.
// ---------------------------------------------------------------------------------------------------

// THE ONE PLACE TO CONFIGURE BSC RPC ENDPOINTS.
//   keyedUrl          KEYED PROVIDER SLOT, not configured yet. Paste the full https:// URL, key included, to enable
//                     it. While it is '' (or not an https URL) it is skipped. When set, it is tried first for every
//                     read and log query, and the public nodes below are the fallback. Everything in this file
//                     ships to the browser, so the key is visible to every visitor: use a key restricted to this site.
//   reads             Public nodes for eth_call, eth_getBalance, eth_blockNumber and batched eth_call. Each call
//                     starts at the next node in turn, so no single node sees all of a visitor's traffic.
//   logs              Public nodes for eth_getLogs (the agent-ID registry scan). Kept separate, as before.
//   requestTimeoutMs  Abort for one read request (eth_getLogs uses its own 4.5 s, see fetchRegistryMintLogs).
//   backoffBaseMs     A node that answers 429, 401, 403, 408 or 5xx, times out or returns bad JSON is skipped for
//   backoffMaxMs      backoffBaseMs, doubling per consecutive failure up to backoffMaxMs. Each cooldown is jittered
//                     (between half and all of its length). A Retry-After header raises it, up to the cap.
//   cacheTtlMs        Identical reads within this window reuse the last successful answer.
//   cacheMaxEntries   Upper bound on cached answers.
const BSC_RPC_CONFIG = {
  keyedUrl: '', // KEYED PROVIDER: paste the full https:// URL here (see above). Empty = skipped.
  reads: [
    'https://bsc-dataseed.bnbchain.org',
    'https://bsc-dataseed1.defibit.io',
    'https://bsc-dataseed1.ninicoin.io',
    'https://1rpc.io/bnb'
  ],
  logs: [
    'https://bsc-rpc.publicnode.com',
    'https://bsc.publicnode.com'
  ],
  requestTimeoutMs: 8000,
  backoffBaseMs: 2000,
  backoffMaxMs: 60000,
  cacheTtlMs: 4000,
  cacheMaxEntries: 200
};

const RPC_READ_METHODS = new Set(['eth_call', 'eth_getLogs', 'eth_blockNumber', 'eth_getBalance']);
const rpcEndpointHealth = new Map(); // node url -> { failures, cooldownUntil }
const rpcInFlight = new Map();       // request key -> promise of response text (null when no node answered)
const rpcCache = new Map();          // request key -> { text, expiresAt }
let rpcRoundRobin = 0;
const rpcClock = { now: () => Date.now(), random: () => Math.random() };

function rpcUrlOf(value) {
  const url = typeof value === 'string' ? value.trim() : '';
  return /^https:\/\/\S+$/.test(url) ? url : '';
}

// Nodes for one read, in the order to try them: the keyed node first (when configured and not cooling
// down), then the public nodes rotated so the starting node changes from call to call.
function rpcCandidateUrls(kind, now) {
  const keyed = rpcUrlOf(BSC_RPC_CONFIG.keyedUrl);
  const ready = (url) => {
    const health = rpcEndpointHealth.get(url);
    return !health || health.cooldownUntil <= now;
  };
  const publicUrls = (kind === 'logs' ? BSC_RPC_CONFIG.logs : BSC_RPC_CONFIG.reads)
    .map(rpcUrlOf)
    .filter((url, i, all) => url && url !== keyed && all.indexOf(url) === i && ready(url));
  const order = keyed && ready(keyed) ? [keyed] : [];
  if (publicUrls.length) {
    const start = rpcRoundRobin++ % publicUrls.length;
    order.push(...publicUrls.slice(start), ...publicUrls.slice(0, start));
  }
  return order;
}

// 429 is the rate limit. 401/403 (key or origin refused), 408, and 5xx mean this node cannot serve us now.
function rpcStatusIsNodeFault(status) {
  return status === 401 || status === 403 || status === 408 || status === 429 || status >= 500;
}

function rpcIsRateLimitError(error) {
  return !!error && /\brate.?limit|too many requests|\b429\b/i.test(String(error.message || ''));
}

function rpcRetryAfterMs(res) {
  try {
    const raw = res && res.headers && typeof res.headers.get === 'function' ? res.headers.get('Retry-After') : null;
    if (!raw) return 0;
    const seconds = Number(raw);
    if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
    const at = Date.parse(raw);
    return Number.isFinite(at) ? Math.max(0, at - rpcClock.now()) : 0;
  } catch (_) {
    return 0;
  }
}

// One POST to one node. Resolves to { ok: true, data } when the node answered this request, or to
// { ok: false, nodeFault, rateLimited, retryAfterMs }. A nodeFault is charged to that node's backoff.
// Anything else (for example a JSON-RPC error about this one call, such as a block range the node
// refuses) only moves on to the next node.
async function rpcPostOnce(url, payload, timeoutMs, accept) {
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller ? controller.signal : undefined
    });
    if (!res || !res.ok) {
      const status = res ? res.status : 0;
      return { ok: false, nodeFault: !res || rpcStatusIsNodeFault(status), rateLimited: status === 429, retryAfterMs: rpcRetryAfterMs(res) };
    }
    const data = await res.json();
    const items = Array.isArray(data) ? data : [data];
    if (items.some((item) => item && rpcIsRateLimitError(item.error))) {
      return { ok: false, nodeFault: true, rateLimited: true, retryAfterMs: rpcRetryAfterMs(res) };
    }
    return accept(data) ? { ok: true, data } : { ok: false, nodeFault: false };
  } catch (_) {
    return { ok: false, nodeFault: true, rateLimited: false, retryAfterMs: 0 };
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// Starts or extends a node's cooldown after a fault. Exponential in the consecutive failure count, with jitter.
function rpcChargeNode(url, { retryAfterMs = 0, rateLimited = false } = {}) {
  const now = rpcClock.now();
  let health = rpcEndpointHealth.get(url);
  if (!health) {
    health = { failures: 0, cooldownUntil: 0 };
    rpcEndpointHealth.set(url, health);
  }
  // Several reads can hit the same failing node at once. Count that outage once, not once per read.
  if (health.cooldownUntil > now) return;
  health.failures += 1;
  const cap = BSC_RPC_CONFIG.backoffMaxMs;
  const exp = Math.min(cap, BSC_RPC_CONFIG.backoffBaseMs * Math.pow(2, health.failures - 1));
  const jittered = exp / 2 + rpcClock.random() * (exp / 2);
  const delay = Math.min(cap, Math.max(jittered, retryAfterMs));
  health.cooldownUntil = now + delay;
  trackAnalytics('rpc_endpoint_backoff', {
    endpoint: url === rpcUrlOf(BSC_RPC_CONFIG.keyedUrl) ? 'keyed' : 'public',
    rate_limited: !!rateLimited,
    failures: health.failures,
    delay_ms: Math.round(delay)
  });
}

// Asks nodes in order until one answers. The first good answer is cached and returned as JSON text, so
// every caller parses its own copy. Resolves to null when no node answered.
async function rpcFirstAnswer(key, payload, { kind, timeoutMs, accept }) {
  for (const url of rpcCandidateUrls(kind, rpcClock.now())) {
    const out = await rpcPostOnce(url, payload, timeoutMs, accept);
    if (out.ok) {
      const health = rpcEndpointHealth.get(url);
      if (health) {
        health.failures = 0;
        health.cooldownUntil = 0;
      }
      const text = JSON.stringify(out.data);
      rpcCache.set(key, { text, expiresAt: rpcClock.now() + BSC_RPC_CONFIG.cacheTtlMs });
      rpcTrimCache();
      return text;
    }
    if (out.nodeFault) rpcChargeNode(url, out);
  }
  return null;
}

function rpcTrimCache() {
  const max = BSC_RPC_CONFIG.cacheMaxEntries;
  if (rpcCache.size <= max) return;
  const now = rpcClock.now();
  for (const [key, entry] of rpcCache) {
    if (entry.expiresAt <= now) rpcCache.delete(key);
  }
  while (rpcCache.size > max) rpcCache.delete(rpcCache.keys().next().value);
}

// Runs one read (a JSON-RPC object, or a batch array) through the cache, the in-flight table and the
// node list. Resolves to the parsed response, or null when the request is not a read or no node answered.
async function rpcReadBody(payload, { logs = false, timeoutMs = BSC_RPC_CONFIG.requestTimeoutMs, accept } = {}) {
  const methods = Array.isArray(payload) ? payload.map((p) => p && p.method) : [payload && payload.method];
  if (!methods.length || !methods.every((method) => RPC_READ_METHODS.has(method))) return null;
  const check = accept || (Array.isArray(payload) ? Array.isArray : (d) => !!d && d.result !== undefined);
  const kind = logs ? 'logs' : 'reads';
  const key = kind + '|' + JSON.stringify(payload);

  const hit = rpcCache.get(key);
  if (hit && hit.expiresAt > rpcClock.now()) return JSON.parse(hit.text);
  if (hit) rpcCache.delete(key);

  if (!rpcInFlight.has(key)) {
    const pending = rpcFirstAnswer(key, payload, { kind, timeoutMs, accept: check })
      .finally(() => rpcInFlight.delete(key));
    rpcInFlight.set(key, pending);
  }
  const text = await rpcInFlight.get(key);
  return text === null ? null : JSON.parse(text);
}

// Robust JSON-RPC single read with per-node backoff and fallback. Resolves to the `result`, or null.
async function sendRpcCall(method, params) {
  const data = await rpcReadBody({ jsonrpc: '2.0', id: 1, method, params });
  return data && data.result !== undefined ? data.result : null;
}

// JSON-RPC batch read (eth_call items). Resolves to the response items sorted by id, or [] when no node answered.
async function sendRpcBatch(batch) {
  if (!Array.isArray(batch) || batch.length === 0) return [];
  const data = await rpcReadBody(batch);
  if (!Array.isArray(data)) return [];
  data.sort((a, b) => Number((a && a.id) || 0) - Number((b && b.id) || 0));
  return data;
}

// eth_getLogs for agent-ID registry mints between two blocks (inclusive). Resolves to the log array,
// or null when no logs node answered.
async function fetchRegistryMintLogs(fromBlk, toBlk) {
  const data = await rpcReadBody({
    jsonrpc: '2.0',
    id: 1,
    method: 'eth_getLogs',
    params: [{
      fromBlock: '0x' + fromBlk.toString(16),
      toBlock: '0x' + toBlk.toString(16),
      address: AGENT_ID_REGISTRY_ADDR,
      topics: [ERC721_TRANSFER_TOPIC, ZERO_ADDRESS_TOPIC]
    }]
  }, { logs: true, timeoutMs: 4500, accept: (d) => !!d && Array.isArray(d.result) });
  return data ? data.result : null;
}

// Test seams for test_rpc_read_resilience.js: a fake clock and random source, and a reset of all state.
const rpcReadTestHooks = {
  setClock(next) {
    Object.assign(rpcClock, next);
  },
  setRoundRobin(n) {
    rpcRoundRobin = n;
  },
  reset() {
    rpcEndpointHealth.clear();
    rpcInFlight.clear();
    rpcCache.clear();
    rpcRoundRobin = 0;
    rpcClock.now = () => Date.now();
    rpcClock.random = () => Math.random();
  },
  endpointHealth(url) {
    const health = rpcEndpointHealth.get(url);
    return health ? { ...health } : null;
  },
  cacheSize() {
    return rpcCache.size;
  }
};

// Injected Web3 Provider Resolver (MetaMask, Binance Web3 Wallet, Rabby, EIP-1193)
function getInjectedProvider(preferred = 'auto') {
  if (typeof window === 'undefined') return null;

  // 1. Explicit Binance Web3 Wallet Request
  if (preferred === 'binance') {
    if (window.binance || window.binancew3w) {
      return window.binance || window.binancew3w;
    }
    if (window.ethereum) {
      if (Array.isArray(window.ethereum.providers) && window.ethereum.providers.length > 0) {
        const p = window.ethereum.providers.find(x => x.isBinance);
        if (p) return p;
      } else if (window.ethereum.isBinance) {
        return window.ethereum;
      }
    }
    // Do NOT silently fall back to Rabby or MetaMask when Binance is specifically requested
    return null;
  }

  // 2. Explicit Rabby Wallet Request
  if (preferred === 'rabby') {
    if (window.ethereum) {
      if (Array.isArray(window.ethereum.providers) && window.ethereum.providers.length > 0) {
        const p = window.ethereum.providers.find(x => x.isRabby);
        if (p) return p;
      } else if (window.ethereum.isRabby) {
        return window.ethereum;
      }
    }
    if (window.rabby) return window.rabby;
    return null;
  }

  // 3. Explicit MetaMask Request
  if (preferred === 'metamask') {
    if (window.ethereum) {
      if (Array.isArray(window.ethereum.providers) && window.ethereum.providers.length > 0) {
        const p = window.ethereum.providers.find(x => x.isMetaMask && !x.isRabby && !x.isBinance);
        if (p) return p;
      } else if (window.ethereum.isMetaMask && !window.ethereum.isRabby && !window.ethereum.isBinance) {
        return window.ethereum;
      }
    }
    return null;
  }

  // 4. Auto-Detect Injected Provider
  if (window.binance || window.binancew3w) {
    return window.binance || window.binancew3w;
  }
  if (window.ethereum) {
    if (Array.isArray(window.ethereum.providers) && window.ethereum.providers.length > 0) {
      return window.ethereum.providers[0];
    }
    return window.ethereum;
  }
  return null;
}

// Binance Web3 Wallet Click Handler (direct connection if injected, otherwise native in-modal QR connect)
function handleBinanceWalletClick() {
  const provider = getInjectedProvider('binance');
  if (provider) {
    connectWeb3('binance');
  } else {
    openBinanceQrModal();
  }
}

// Web3 Wallet Connect Method
async function connectWeb3(providerType = 'auto') {
  const provider = getInjectedProvider(providerType);
  if (!provider) {
    if (providerType === 'binance') {
      openBinanceQrModal();
      return false;
    } else if (providerType === 'metamask') {
      alert("🦊 MetaMask was not detected (or another extension is overriding it). Check your browser extensions or use Binance Web3 / Watch-Only.");
    } else if (providerType === 'rabby') {
      alert("🐰 Rabby Wallet extension was not detected in this browser!");
    } else {
      openBinanceQrModal();
    }
    return false;
  }

  const btn = document.getElementById('connectWalletBtn');
  if (btn) btn.classList.add('connecting');
  const label = document.getElementById('walletBtnLabel');
  if (label) label.textContent = 'Connecting...';

  try {
    const accounts = await provider.request({ method: 'eth_requestAccounts' });
    if (!accounts || accounts.length === 0) {
      throw new Error('No accounts selected');
    }
    const account = accounts[0].toLowerCase();
    connectedWallet = account;

    let provName = 'Web3 Wallet';
    if (provider.isRabby) {
      provName = 'Rabby';
    } else if (provider.isBinance || provider === window.binance || provider === window.binancew3w) {
      provName = 'Binance Web3';
    } else if (provider.isMetaMask) {
      provName = 'MetaMask';
    }
    walletProviderName = provName;
    trackAnalytics('wallet_connected', {
      wallet_provider: provName,
      is_mobile: isMobileClient(),
      source: _walletSession ? _walletSession.source : 'direct'
    });

    if (typeof window !== 'undefined' && window.posthog && typeof window.posthog.identify === 'function') {
      window.posthog.identify(account, { wallet_provider: provName });
    }

    // Check & switch to BSC Mainnet (56 / 0x38)
    try {
      const chainId = await provider.request({ method: 'eth_chainId' });
      if (chainId !== '0x38' && parseInt(chainId, 16) !== 56) {
        try {
          await provider.request({
            method: 'wallet_switchEthereumChain',
            params: [{ chainId: '0x38' }]
          });
        } catch (switchErr) {
          if (switchErr.code === 4902 || switchErr.code === -32603) {
            await provider.request({
              method: 'wallet_addEthereumChain',
              params: [{
                chainId: '0x38',
                chainName: 'BNB Smart Chain',
                nativeCurrency: { name: 'BNB', symbol: 'BNB', decimals: 18 },
                rpcUrls: ['https://bsc-dataseed.bnbchain.org'],
                blockExplorerUrls: ['https://bscscan.com']
              }]
            });
          }
        }
      }
    } catch (netErr) {
      console.warn('Network check error:', netErr);
    }

    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('agent_id_connected_wallet', connectedWallet);
      localStorage.setItem('agent_id_wallet_provider', walletProviderName);
    }

    updateWalletButtonUI();
    loadFavoritesForActiveWallet();
    await scanUserPortfolio(connectedWallet);

    // Event listeners
    if (provider.on) {
      provider.on('accountsChanged', (newAccounts) => {
        if (!newAccounts || newAccounts.length === 0) {
          disconnectWallet();
        } else {
          connectedWallet = newAccounts[0].toLowerCase();
          localStorage.setItem('agent_id_connected_wallet', connectedWallet);
          updateWalletButtonUI();
          loadFavoritesForActiveWallet();
          scanUserPortfolio(connectedWallet);
        }
      });
      provider.on('chainChanged', () => {
        window.location.reload();
      });
    }

    const modal = document.getElementById('walletModal');
    if (modal && (modal.open || modal.hasAttribute('open'))) {
      modal.close ? modal.close() : modal.removeAttribute('open');
    }

    return true;
  } catch (err) {
    console.error('Wallet connection error:', err);
    trackAnalytics('wallet_connect_failed', {
      wallet_provider: providerType,
      rejected: Boolean(err && (err.code === 4001 || String(err.message || '').toLowerCase().includes('user rejected'))),
      is_mobile: isMobileClient(),
      source: _walletSession ? _walletSession.source : 'direct'
    });
    updateWalletButtonUI();
    return false;
  }
}

// Build EVM transaction parameters for 1-Click Web3 Direct Minting
function buildMintTxParams(contractAddress, rawCalldata, valueWei, fromWallet) {
  if (!contractAddress || typeof contractAddress !== 'string' || !contractAddress.startsWith('0x') || contractAddress.length !== 42) {
    throw new Error('Invalid contract address');
  }

  const isRecv = !rawCalldata || rawCalldata === '0x' || rawCalldata === 'recv';
  let calldata = isRecv ? '0x' : rawCalldata;

  // Dynamic recipient injection if calldata supports it (e.g., 0x836a1040)
  if (!isRecv && calldata.startsWith('0x836a1040') && calldata.length >= 138 && fromWallet) {
    const cleanW = fromWallet.replace('0x', '').padStart(40, '0').toLowerCase();
    calldata = calldata.slice(0, 74) + '000000000000000000000000' + cleanW + calldata.slice(138);
  }

  // Convert valueWei to 0x-hex string
  let valHex = '0x0';
  if (valueWei && valueWei !== '0' && valueWei !== 0) {
    try {
      const bn = BigInt(valueWei);
      valHex = '0x' + bn.toString(16);
    } catch (e) {
      valHex = '0x0';
    }
  }

  const tx = {
    to: contractAddress.toLowerCase(),
    data: calldata,
    value: valHex
  };

  if (fromWallet) {
    tx.from = fromWallet.toLowerCase();
  }

  return tx;
}

// 1-Click Web3 Mint Execution (MetaMask, Binance Web3 Wallet, Rabby, EIP-1193)
async function executeWeb3Mint(contractAddress, rawCalldata, valueWei, name = 'Drop') {
  const btn = document.getElementById('modalMintBtn');
  const statusBox = document.getElementById('modalMintStatus');

  const updateStatus = (html, display = 'block') => {
    if (statusBox) {
      statusBox.innerHTML = html;
      statusBox.style.display = display;
    }
  };

  try {
    // 1. Injected Provider Check. Nothing in this browser can sign: explain the next step (wallet gate) instead of a dead end.
    let provider = getInjectedProvider();
    if (!provider) {
      trackAnalytics('mint_wallet_gate_shown', {
        token_symbol: (currentInspectItem && currentInspectItem.symbol) || '',
        is_mobile: isMobileClient(),
        watch_only: Boolean(connectedWallet) && walletProviderName === 'Watch-Only'
      });
      openMintWalletGate();
      return;
    }

    // 2. Ensure Wallet Connected
    if (!connectedWallet) {
      if (btn) btn.innerHTML = '<span class="spinner"></span> <span>Connecting Wallet...</span>';
      updateStatus('<div class="mint-status-info">👛 Please connect your Web3 wallet to proceed...</div>');
      const ok = await connectWeb3();
      if (!ok || !connectedWallet) {
        if (btn) btn.innerHTML = '<span>⚡ 1-Click Mint</span>';
        updateStatus('<div class="mint-status-warning">⚠️ Wallet connection required to mint.</div>');
        return;
      }
      provider = getInjectedProvider();
    }

    // 3. Switch network to BSC Mainnet (56 / 0x38)
    try {
      const chainId = await provider.request({ method: 'eth_chainId' });
      if (chainId !== '0x38' && parseInt(chainId, 16) !== 56) {
        updateStatus('<div class="mint-status-info">🌐 Switching wallet to BSC Mainnet (Chain 56)...</div>');
        await provider.request({
          method: 'wallet_switchEthereumChain',
          params: [{ chainId: '0x38' }]
        });
      }
    } catch (switchErr) {
      console.warn('Network switch warning:', switchErr);
    }

    // 4. Build standard EVM tx parameters
    const txParams = buildMintTxParams(contractAddress, rawCalldata, valueWei, connectedWallet);

    // 5. Pre-flight simulation to catch reverts (e.g. NoAgentId, insufficient balance, cap reached) BEFORE broadcasting
    try {
      await provider.request({
        method: 'eth_call',
        params: [txParams, 'latest']
      });
    } catch (simErr) {
      console.warn('Pre-flight simulation reverted:', simErr);
      const errStr = String(simErr?.message || simErr?.data || JSON.stringify(simErr) || '');
      if (errStr.includes('0xf947691f') || errStr.toLowerCase().includes('noagentid')) {
        updateStatus(`
          <div class="mint-status-error" style="line-height: 1.5; padding: 10px 12px; background: rgba(239, 68, 68, 0.1); border: 1px solid var(--color-red); border-radius: 6px;">
            <strong>🚨 On-Chain Gate: NoAgentId (0xf947691f)</strong><br/>
            This project is an <em>Agent ID Exclusive launch</em> and strictly requires an active <code>.ai</code> Agent ID to mint during the bonding curve phase.<br/>
            <span style="font-size: 11px; color: var(--color-yellow);">Your wallet does not hold a .ai Agent ID. We stopped the transaction before broadcast to save you from losing gas fees!</span>
          </div>
        `);
        if (btn) {
          btn.disabled = false;
          btn.classList.remove('confirming');
          btn.classList.add('error');
          btn.innerHTML = '<span>⚠️ Requires .ai Agent ID</span>';
        }
        return;
      }
      if (errStr.includes('insufficient funds') || errStr.includes('exceeds balance')) {
        updateStatus('<div class="mint-status-error">❌ Insufficient BNB balance for mint value + gas.</div>');
        if (btn) {
          btn.disabled = false;
          btn.classList.remove('confirming');
          btn.innerHTML = '<span>❌ Insufficient BNB</span>';
        }
        return;
      }
    }

    // 6. Update UI to awaiting user signature state
    if (btn) {
      btn.disabled = true;
      btn.classList.add('confirming');
      btn.innerHTML = `<span class="spinner"></span> <span>Awaiting Wallet Signature...</span>`;
    }
    updateStatus(`<div class="mint-status-pending">⏳ Confirm transaction in <strong>${walletProviderName || 'your wallet'}</strong>...</div>`);

    // 6. Dispatch transaction via standard EIP-1193 eth_sendTransaction
    const txHash = await provider.request({
      method: 'eth_sendTransaction',
      params: [txParams]
    });

    if (!txHash) throw new Error('No transaction hash returned from wallet');

    // 7. Success State
    playMarketAlertSound();
    console.log(`%c[🎉 WEB3 MINT SUCCESS]`, 'color: #00ff88; font-weight: bold;', { name, contract: contractAddress, txHash });

    if (btn) {
      btn.classList.remove('confirming');
      btn.classList.add('success');
      btn.innerHTML = `<span>✔ Mint Submitted!</span>`;
    }

    updateStatus(`
      <div class="mint-status-success">
        <span class="status-icon" style="font-size: 18px;">🚀</span>
        <div>
          <strong style="color: var(--color-green);">Transaction Broadcasted to BSC!</strong><br/>
          <a href="https://bscscan.com/tx/${txHash}" target="_blank" rel="noopener" style="color: var(--color-cyan); text-decoration: underline; font-family: monospace; font-size: 11px;">
            ${txHash.slice(0, 10)}...${txHash.slice(-8)} ↗ (View on BscScan)
          </a>
        </div>
      </div>
    `);

    showToast('🚀 MINT BROADCASTED', `Mint transaction submitted for ${name}!`, '⚡');

    // Auto-favorite the newly minted contract so it appears in Favorites tab
    toggleFavorite(contractAddress, null, true);

  } catch (err) {
    console.error('Web3 Mint failed:', err);
    const isUserRejection = (err && (err.code === 4001 || err.code === 'ACTION_REJECTED' || (err.message && err.message.toLowerCase().includes('user rejected'))));

    if (btn) {
      btn.disabled = false;
      btn.classList.remove('confirming');
      btn.classList.add('error');
      btn.innerHTML = isUserRejection ? '<span>⚠️ Signature Cancelled</span>' : '<span>❌ Mint Failed</span>';
    }

    if (isUserRejection) {
      updateStatus('<div class="mint-status-warning">⚠️ Transaction was rejected in your wallet.</div>');
    } else {
      const errMsg = (err && err.message) ? err.message.split('\n')[0].slice(0, 100) : 'Transaction failed';
      updateStatus(`<div class="mint-status-error">❌ ${escapeHtml(errMsg)}</div>`);
    }

    // Reset button state after 3 seconds
    setTimeout(() => {
      if (btn) {
        btn.classList.remove('error');
        btn.disabled = false;
        btn.innerHTML = `<span>⚡ 1-Click Mint</span>`;
      }
    }, 3000);
  }
}
if (typeof window !== 'undefined') window.executeWeb3Mint = executeWeb3Mint;

// ==========================================================================
// 👛 Mint with no signing wallet in this browser (mobile browsers, desktops without an extension)
// ==========================================================================
// Before this, Mint with no injected provider opened the generic extension list (MetaMask / Rabby cards) with no
// explanation. On Android Chrome the wallet dialog was opened and left without a provider chosen. Now the click says
// what is missing and opens the wallet dialog with the next steps that work on this device: the Binance app (Discover
// link / QR) or the Sniper CLI. With an injected provider Mint goes straight to executeWeb3Mint, unchanged.
//
// Funnel (PostHog): mint_button_clicked -> mint_wallet_gate_shown -> mint_gate_action | cli_copied (source mint_gate)
// | wallet_modal_abandoned -> wallet_connected | wallet_connect_failed. A wallet_modal_opened starts a session; the
// dialog closing with no signing wallet ends it as wallet_modal_abandoned.
let _walletSession = null;   // { source, view, token_symbol, openedAt } while the wallet dialog is open

function walletDialogIsOpen() {
  const modal = (typeof document !== 'undefined') ? document.getElementById('walletModal') : null;
  if (!modal) return false;
  return Boolean(modal.open || (typeof modal.hasAttribute === 'function' && modal.hasAttribute('open')));
}

// Starts a wallet-dialog session (one wallet_modal_opened) unless the dialog is already open; then only the view changes.
// `view` is what the user sees: connect_list, account, mint_gate or binance_qr.
function beginWalletSession(source, view, extra = {}) {
  if (_walletSession && walletDialogIsOpen()) {
    _walletSession.view = view || _walletSession.view;
    return _walletSession;
  }
  _walletSession = {
    source: source,
    view: view || source,
    token_symbol: extra.token_symbol || '',
    openedAt: Date.now()
  };
  trackAnalytics('wallet_modal_opened', {
    source: source,
    view: _walletSession.view,
    token_symbol: _walletSession.token_symbol,
    is_mobile: isMobileClient(),
    has_injected_provider: Boolean(getInjectedProvider()),
    wallet_connected: Boolean(connectedWallet)
  });
  return _walletSession;
}

// The wallet dialog's close event (ESC and the backdrop included). A session that ends with no signing wallet connected
// is an abandon. The account drawer is not part of the connect funnel, so it is never counted.
function onWalletModalClosed() {
  const session = _walletSession;
  _walletSession = null;
  if (!session || session.source === 'account_drawer') return;
  const signing = Boolean(connectedWallet) && walletProviderName !== 'Watch-Only';
  if (signing) return;
  trackAnalytics('wallet_modal_abandoned', {
    source: session.source,
    view: session.view,
    token_symbol: session.token_symbol,
    seconds_open: Math.max(0, Math.round((Date.now() - session.openedAt) / 1000)),
    is_mobile: isMobileClient(),
    has_injected_provider: Boolean(getInjectedProvider()),
    watch_only_connected: Boolean(connectedWallet)
  });
}

// Properties of the mint_button_clicked event (the token modal's Mint button): what the user had when they tapped.
function mintClickProps(symbol, isFree) {
  return {
    token_symbol: symbol || '',
    is_free: Boolean(isFree),
    wallet_connected: Boolean(connectedWallet),
    watch_only: Boolean(connectedWallet) && walletProviderName === 'Watch-Only',
    has_injected_provider: Boolean(getInjectedProvider()),
    is_mobile: isMobileClient(),
    source: 'modal_mint_button'
  };
}

function trackMintGateAction(action) {
  trackAnalytics('mint_gate_action', {
    action: action,
    token_symbol: (_walletSession && _walletSession.token_symbol) || '',
    is_mobile: isMobileClient()
  });
}

// Mint without a signing wallet, as a step: why nothing can sign here and what to do next on this device. Nothing on
// this dialog signs. Its options open the Binance guide, copy the Sniper CLI or link to a wallet extension.
function openMintWalletGate(sym) {
  const modal = (typeof document !== 'undefined') ? document.getElementById('walletModal') : null;
  const content = (typeof document !== 'undefined') ? document.getElementById('walletModalContent') : null;
  if (!modal || !content) return;
  const symbol = sym || (currentInspectItem && currentInspectItem.symbol) || '';
  beginWalletSession('mint_gate', 'mint_gate', { token_symbol: symbol });

  const isMobile = isMobileClient();
  const watchOnly = Boolean(connectedWallet) && walletProviderName === 'Watch-Only';
  const shortAddr = connectedWallet ? `${connectedWallet.slice(0, 6)}...${connectedWallet.slice(-4)}` : '';
  const lead = watchOnly
    ? `You are in watch-only mode (<code>${shortAddr}</code>). It can read a wallet, but it cannot sign a mint.`
    : (isMobile
      ? 'Mint sends a transaction from your wallet. Browser-extension wallets (MetaMask, Rabby) do not run in mobile browsers, so nothing on this page can sign yet.'
      : 'Mint sends a transaction from your wallet. No browser wallet extension was found in this browser, so nothing on this page can sign yet.');

  const binanceOption = `
      <button type="button" class="mint-gate-option mint-gate-primary" onclick="trackMintGateAction('binance_app'); openBinanceQrModal()">
        <span class="mint-gate-icon" aria-hidden="true">📱</span>
        <span class="mint-gate-text"><strong>${isMobile ? 'Open in Binance App' : 'Mint from the Binance app'}</strong><small>${isMobile ? 'Binance Web3 Discover: your Binance wallet signs inside the app.' : 'Scan a QR code with the Binance app, then mint from your phone.'}</small></span>
      </button>`;
  const cliOption = `
      <button type="button" class="mint-gate-option" onclick="copyModalSniperCli(this, 'mint_gate')">
        <span class="mint-gate-icon" aria-hidden="true">⌨</span>
        <span class="mint-gate-text"><strong>Copy Sniper CLI</strong><small>Mint from a terminal with the Binance Agentic Wallet CLI.</small></span>
      </button>`;
  const extensionOptions = `
      <a class="mint-gate-option" href="https://metamask.io/download/" target="_blank" rel="noopener" onclick="trackMintGateAction('install_metamask')">
        <span class="mint-gate-icon" aria-hidden="true">🦊</span>
        <span class="mint-gate-text"><strong>Install MetaMask</strong><small>Browser extension for desktop Chrome, Edge or Brave.</small></span>
      </a>
      <a class="mint-gate-option" href="https://rabby.io/" target="_blank" rel="noopener" onclick="trackMintGateAction('install_rabby')">
        <span class="mint-gate-icon" aria-hidden="true">🐰</span>
        <span class="mint-gate-text"><strong>Install Rabby</strong><small>DeFi wallet extension for desktop.</small></span>
      </a>`;
  const options = isMobile ? binanceOption + cliOption : extensionOptions + binanceOption + cliOption;
  const note = isMobile
    ? 'In the Binance app, open this page in Discover and tap Mint there.'
    : 'After installing a wallet, reload this page and click Mint again.';

  content.innerHTML = `
    <div class="wallet-card-container">
      <div class="modal-header wallet-card-header">
        <div class="modal-title wallet-card-title">
          <span class="wallet-title-icon">👛</span>
          <span>Wallet needed to mint${symbol ? ` ${escapeHtml(symbol)}` : ''}</span>
        </div>
        <button class="modal-close-btn" onclick="closeWalletDialog()">✕ [ESC]</button>
      </div>

      <div class="wallet-connect-body">
        <p class="wallet-connect-desc">${lead} Pick the next step:</p>
        <div class="mint-gate-options">${options}</div>
        <p class="mint-gate-note">${note}</p>
        <div style="text-align: center; margin-top: 8px;">
          <button type="button" class="modal-close-btn" onclick="closeWalletDialog()">Not now</button>
        </div>
      </div>
    </div>
  `;

  // The token modal keeps the reason too, so the Mint section still explains itself after this dialog is closed.
  const statusBox = document.getElementById('modalMintStatus');
  if (statusBox) {
    statusBox.innerHTML = `<div class="mint-status-info">👛 <strong>Minting needs a wallet that can sign in this browser.</strong> <button type="button" class="mint-status-link" onclick="openMintWalletGate()">See your options ›</button></div>`;
    statusBox.style.display = 'block';
  }

  if (typeof modal.showModal === 'function') modal.showModal();
  else modal.setAttribute('open', '');
}

// "← Back" on the Binance guide: to the Mint gate when the dialog was opened from Mint, else to the connect list.
function walletModalBack() {
  if (_walletSession && _walletSession.source === 'mint_gate') openMintWalletGate(_walletSession.token_symbol);
  else openWalletModal();
}

function closeWalletDialog() {
  const modal = (typeof document !== 'undefined') ? document.getElementById('walletModal') : null;
  if (!modal) return;
  if (typeof modal.close === 'function') modal.close();
  else modal.removeAttribute('open');
}

// Watch-Only Mode (Inspect Any BSC Address Without Private Key)
function setWatchOnlyWallet(addr) {
  if (!addr || !addr.startsWith('0x') || addr.length !== 42) {
    alert('Please enter a valid 42-character 0x... BSC address.');
    return false;
  }
  connectedWallet = addr.toLowerCase();
  walletProviderName = 'Watch-Only';
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem('agent_id_connected_wallet', connectedWallet);
    localStorage.setItem('agent_id_wallet_provider', 'Watch-Only');
  }
  updateWalletButtonUI();
  loadFavoritesForActiveWallet();
  scanUserPortfolio(connectedWallet);

  const modal = (typeof document !== 'undefined') ? document.getElementById('walletModal') : null;
  if (modal && (modal.open || modal.hasAttribute('open'))) {
    modal.close ? modal.close() : modal.removeAttribute('open');
  }
  return true;
}

// Disconnect Wallet
function disconnectWallet() {
  connectedWallet = null;
  walletProviderName = null;
  userNativeBnbBalance = 0.0;
  isAgenticWallet = false;
  userOwnedContracts.clear();
  nativeBnbItem = null;

  if (typeof localStorage !== 'undefined') {
    localStorage.removeItem('agent_id_connected_wallet');
    localStorage.removeItem('agent_id_wallet_provider');
  }

  if (typeof window !== 'undefined' && window.posthog && typeof window.posthog.reset === 'function') {
    window.posthog.reset();
  }

  // Clear balances on allDrops
  allDrops.forEach(d => {
    d.user_balance = 0;
    d.user_balance_wei = 0n;
  });

  updateWalletButtonUI();
  loadFavoritesForActiveWallet();
  markDropsDataChanged();
  updatePortfolioBannerUI();
  renderTable();

  const discModal = (typeof document !== 'undefined') ? document.getElementById('walletModal') : null;
  if (discModal && (discModal.open || discModal.hasAttribute('open'))) {
    discModal.close ? discModal.close() : discModal.removeAttribute('open');
  }
}

// Update Header Connect Button State
function updateWalletButtonUI() {
  if (typeof document === 'undefined') return;
  const btn = document.getElementById('connectWalletBtn');
  if (!btn) return;

  btn.classList.remove('connecting');
  if (connectedWallet) {
    btn.classList.add('connected');
    const shortAddr = `${connectedWallet.slice(0, 6)}...${connectedWallet.slice(-4)}`;
    const agentBadge = isAgenticWallet ? `<span class="badge badge-cyan" style="font-size:9px; padding: 2px 5px; font-weight:800; box-shadow: 0 0 8px rgba(0,240,255,0.4);" title="Verified .ai Agent ID Owner">🤖 AGENTIC</span>` : '';
    btn.innerHTML = `
      <span class="wallet-dot ${isAgenticWallet ? 'agentic' : ''}"></span>
      <span class="wallet-label">${shortAddr}</span>
      ${agentBadge}
      <span class="wallet-badge-bnb desktop-only">${userNativeBnbBalance.toFixed(3)} BNB</span>
    `;
    btn.title = `Connected (${walletProviderName}): ${connectedWallet} ${isAgenticWallet ? '[Verified Agentic .ai Wallet]' : '[Standard Wallet]'} - Click for account details`;
  } else {
    btn.classList.remove('connected');
    btn.innerHTML = `
      <span class="wallet-icon">👛</span>
      <span class="wallet-label"><span class="desktop-only">Connect Wallet</span><span class="mobile-only">Connect</span></span>
    `;
    btn.title = 'Connect MetaMask, Binance Web3 Wallet, or Rabby for live portfolio sync';
  }
}

// 100% Dynamic On-Chain Portfolio Scanner (Native BNB + 348+ drops via Multicall3)
// Decimals of a token: the stored token_decimals, else the "(N decimals)" in its standard string, else 18.
// (The portfolio scan used to special-case only "8 decimals": a 9-decimal token was off by 1e9, a 0-decimal one by 1e18.)
function tokenDecimalsOf(drop) {
  const stored = drop && drop.token_decimals;
  if (typeof stored === 'number' && Number.isInteger(stored) && stored >= 0 && stored <= 36) return stored;
  const m = /\((\d+) decimals\)/.exec(String((drop && drop.standard) || ''));
  if (m) {
    const d = Number(m[1]);
    if (d >= 0 && d <= 36) return d;
  }
  return 18;
}

// Whole-token balance from a raw on-chain balance (BigInt or number)
function balanceFromRaw(drop, raw) {
  return Number(raw) / (10 ** tokenDecimalsOf(drop));
}

async function scanUserPortfolio(walletAddr, force = false) {
  if (!walletAddr || isScanningPortfolio) return;
  isScanningPortfolio = true;

  const hasDom = (typeof document !== 'undefined');
  const btn = hasDom ? document.getElementById('connectWalletBtn') : null;
  if (btn) btn.classList.add('connecting');
  const rescanBtn = hasDom ? document.getElementById('portRescanBtn') : null;
  if (rescanBtn) rescanBtn.classList.add('connecting');

  const tsEl = hasDom ? document.getElementById('portScanTimestamp') : null;
  if (tsEl) tsEl.textContent = 'Scanning on-chain BSC (Multicall3)...';

  try {
    const contracts = [];
    const seen = new Set();
    allDrops.forEach(d => {
      const c = (d.contract || '').toLowerCase();
      if (c && c.startsWith('0x') && c.length === 42 && !seen.has(c)) {
        seen.add(c);
        contracts.push(c);
      }
    });

    userOwnedContracts.clear();

    const cleanWallet = walletAddr.replace('0x', '').toLowerCase().padStart(64, '0');
    const balanceCalldata = '0x70a08231' + cleanWallet;
    const ethBalanceCalldata = '0x4d2301cc' + cleanWallet;

    // Multicall3: 1 Single Network Roundtrip for Native BNB + all 348+ token balances
    let multicallSucceeded = false;
    try {
      const calls = [
        { target: MULTICALL3_ADDRESS, allowFailure: true, callData: ethBalanceCalldata },
        { target: AGENT_ID_REGISTRY, allowFailure: true, callData: balanceCalldata }
      ];
      for (let i = 0; i < contracts.length; i++) {
        calls.push({
          target: contracts[i],
          allowFailure: true,
          callData: balanceCalldata
        });
      }

      const encoded = encodeMulticall3Aggregate3(calls);
      const rawResult = await sendRpcCall('eth_call', [{ to: MULTICALL3_ADDRESS, data: encoded }, 'latest']);

      if (rawResult && rawResult !== '0x') {
        const decoded = decodeMulticall3Aggregate3(rawResult);
        if (decoded && decoded.length === calls.length) {
          multicallSucceeded = true;

          // 1. Native BNB balance from Call 0
          const bnbRes = decoded[0];
          if (bnbRes && bnbRes.success && bnbRes.returnData && bnbRes.returnData !== '0x') {
            try {
              const bnbWei = BigInt(bnbRes.returnData);
              userNativeBnbBalance = Number(bnbWei) / 1e18;
            } catch (e) {
              userNativeBnbBalance = 0.0;
            }
          } else {
            userNativeBnbBalance = 0.0;
          }

          // 2. Agent ID Registry ownership from Call 1
          const agentRes = decoded[1];
          if (agentRes && agentRes.success && agentRes.returnData && agentRes.returnData !== '0x' && !/^0x0+$/.test(agentRes.returnData)) {
            try {
              isAgenticWallet = (BigInt(agentRes.returnData) > 0n);
            } catch (e) {
              isAgenticWallet = false;
            }
          } else {
            isAgenticWallet = false;
          }

          // 3. Token balances from Calls 2..N
          for (let i = 0; i < contracts.length; i++) {
            const tokenRes = decoded[i + 2];
            const c = contracts[i];
            if (tokenRes && tokenRes.success && tokenRes.returnData && tokenRes.returnData !== '0x' && !/^0x0+$/.test(tokenRes.returnData)) {
              try {
                const raw = BigInt(tokenRes.returnData);
                if (raw > 0n) {
                  userOwnedContracts.set(c, raw);
                }
              } catch (e) {}
            }
          }
        }
      }
    } catch (mcErr) {
      console.warn('Multicall3 query error, triggering fallback:', mcErr);
    }

    // Graceful fallback to legacy JSON-RPC batches if Multicall3 fails
    if (!multicallSucceeded) {
      try {
        const bnbHex = await sendRpcCall('eth_getBalance', [walletAddr, 'latest']);
        if (bnbHex && bnbHex !== '0x') {
          userNativeBnbBalance = Number(BigInt(bnbHex)) / 1e18;
        } else {
          userNativeBnbBalance = 0.0;
        }
      } catch (e) {
        userNativeBnbBalance = 0.0;
      }

      const chunks = [];
      for (let i = 0; i < contracts.length; i += 60) {
        chunks.push(contracts.slice(i, i + 60));
      }

      await Promise.all(chunks.map(async (chunk) => {
        const batch = chunk.map((c, idx) => ({
          jsonrpc: '2.0',
          id: idx + 1,
          method: 'eth_call',
          params: [{ to: c, data: balanceCalldata }, 'latest']
        }));
        const results = await sendRpcBatch(batch);
        if (Array.isArray(results)) {
          results.forEach((res, idx) => {
            const c = chunk[idx];
            if (res && res.result && res.result !== '0x' && !/^0x0+$/.test(res.result)) {
              try {
                const raw = BigInt(res.result);
                if (raw > 0n) {
                  userOwnedContracts.set(c, raw);
                }
              } catch (e) {}
            }
          });
        }
      }));
    }

    nativeBnbItem = {
      contract: '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
      name: 'Binance Coin (Gas Reserve)',
      symbol: 'BNB',
      standard: 'Native Gas',
      user_balance: userNativeBnbBalance,
      user_balance_wei: BigInt(Math.floor(userNativeBnbBalance * 1e18)),
      token_price_usd: bnbPrice,
      token_symbol: 'BNB',
      mint_price_bnb: 0,
      score: 100,
      holders: 1500000,
      is_nft: false
    };

    // Re-hydrate allDrops balances
    allDrops.forEach(drop => {
      const c = (drop.contract || '').toLowerCase();
      const raw = userOwnedContracts.get(c);
      if (raw && raw > 0n) {
        const std = String(drop.standard || '');
        const isNft = std.includes('721') || std.includes('NFT') || Boolean(drop.is_nft);
        drop.user_balance = isNft ? Number(raw) : balanceFromRaw(drop, raw);
        drop.user_balance_wei = raw;
      } else {
        drop.user_balance = 0;
        drop.user_balance_wei = 0n;
      }
    });

  } catch (err) {
    console.error('Portfolio scan failed:', err);
  } finally {
    isScanningPortfolio = false;
    if (btn) btn.classList.remove('connecting');
    if (rescanBtn) rescanBtn.classList.remove('connecting');
    updateWalletButtonUI();
    markDropsDataChanged();
    updatePortfolioBannerUI();
    renderTable();
  }
}

// Update Dynamic Tab 3 Portfolio Banner
function updatePortfolioBannerUI() {
  if (typeof document === 'undefined') return;
  const banner = document.getElementById('portfolioBanner');
  if (!banner) return;

  const bnbPx = hasUsableBnbPrice() ? bnbPrice : 0;
  let totalUsd = userNativeBnbBalance * bnbPx;
  let ownedTokens = 0;
  let ownedNfts = 0;
  let profitSignals = 0;

  allDrops.forEach(drop => {
    if (drop.user_balance && Number(drop.user_balance) > 0) {
      const std = String(drop.standard || '');
      const isNft = std.includes('721') || std.includes('NFT') || Boolean(drop.is_nft);
      if (isNft) {
        ownedNfts++;
        const fl = Number(drop.floor_price_bnb || 0);
        totalUsd += drop.user_balance * (fl * bnbPx);
      } else {
        ownedTokens++;
        const p = Number(drop.token_price_usd || drop.market_price_usd || 0);
        totalUsd += drop.user_balance * p;
      }
      const exit = evaluatePortfolioExit(drop, bnbPrice);
      if (exit.can_swap && (exit.signal.includes('MOON') || exit.signal.includes('RECOUP') || exit.signal.includes('CRASH') || exit.signal.includes('SURGE'))) {
        profitSignals++;
      }
    }
  });

  const totalAssets = ownedTokens + ownedNfts + (userNativeBnbBalance > 0 ? 1 : 0);

  const valEl = document.getElementById('portTotalValue');
  if (valEl) valEl.textContent = hasUsableBnbPrice() ? `${bnbPriceLive ? '' : '≈'}$${totalUsd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';

  const gasEl = document.getElementById('portGasReserve');
  if (gasEl) gasEl.textContent = `${userNativeBnbBalance.toFixed(4)} BNB (${formatBnbAsUsd(userNativeBnbBalance)})`;

  const assetsEl = document.getElementById('portAssetsCount');
  if (assetsEl) assetsEl.textContent = `${totalAssets} Asset${totalAssets === 1 ? '' : 's'}`;

  const tnEl = document.getElementById('portTokensNftsCount');
  if (tnEl) tnEl.textContent = `(${ownedTokens} Tokens / ${ownedNfts} NFTs)`;

  const sigEl = document.getElementById('portProfitSignalsCount');
  if (sigEl) {
    sigEl.textContent = `${profitSignals} Target${profitSignals === 1 ? '' : 's'} Ready`;
    sigEl.className = profitSignals > 0 ? 'badge badge-green' : 'badge badge-dim';
  }

  const tsEl = document.getElementById('portScanTimestamp');
  if (tsEl) tsEl.textContent = `Scanned ${new Date().toLocaleTimeString()}`;

  const portCountEl = document.getElementById('portfolioCount');
  if (portCountEl) portCountEl.textContent = totalAssets;
}

// Open Connect / Account Drawer Modal
function openWalletModal() {
  const modal = document.getElementById('walletModal');
  const content = document.getElementById('walletModalContent');
  if (!modal || !content) return;
  beginWalletSession(connectedWallet ? 'account_drawer' : 'connect_list', connectedWallet ? 'account' : 'connect_list');

  if (!connectedWallet) {
    content.innerHTML = `
      <div class="wallet-card-container">
        <div class="modal-header wallet-card-header">
          <div class="modal-title wallet-card-title">
            <span class="wallet-title-icon">👛</span>
            <span>Connect Web3 Wallet</span>
          </div>
          <button class="modal-close-btn" onclick="document.getElementById('walletModal').close ? document.getElementById('walletModal').close() : document.getElementById('walletModal').removeAttribute('open')">✕ [ESC]</button>
        </div>

        <div class="wallet-connect-body">
          <p class="wallet-connect-desc">
            Select your injected browser wallet to dynamically scan your on-chain .ai holdings and receive automated profit alerts on BNB Chain.
          </p>

          <div class="provider-grid">
            <div class="provider-card" onclick="connectWeb3('metamask')">
              <span class="provider-icon">🦊</span>
              <span class="provider-name">MetaMask</span>
              <span class="provider-badge">Injected</span>
            </div>
            <div class="provider-card" onclick="handleBinanceWalletClick()">
              <span class="provider-icon">🟡</span>
              <span class="provider-name">Binance Web3</span>
              <span class="provider-badge">QR / Injected</span>
            </div>
            <div class="provider-card" onclick="connectWeb3('rabby')">
              <span class="provider-icon">🐰</span>
              <span class="provider-name">Rabby</span>
              <span class="provider-badge">DeFi Native</span>
            </div>
            <div class="provider-card" onclick="connectWeb3('auto')">
              <span class="provider-icon">⚡</span>
              <span class="provider-name">Auto Detect</span>
              <span class="provider-badge">EIP-1193</span>
            </div>
          </div>

          <div style="margin-top: 14px; text-align: center;">
            <button class="wallet-action-btn" onclick="openBinanceQrModal()" style="width: 100%; border: 1px dashed rgba(240, 185, 11, 0.45); background: rgba(240, 185, 11, 0.08); color: #F0B90B; padding: 10px 14px; font-weight: 700; cursor: pointer; border-radius: 6px; display: flex; align-items: center; justify-content: center; gap: 8px; transition: all 0.2s ease;">
              <span>📱</span>
              <span>Scan QR with Binance Mobile Web3 App</span>
            </button>
          </div>

          <div class="wallet-switch-card">
            <div class="wallet-switch-header">
              <span class="switch-title">👁️ Watch-Only Address (Auditor Mode)</span>
              <span class="dim">No extension required</span>
            </div>
            <div class="wallet-switch-bar">
              <span class="switch-icon">🔍</span>
              <input type="text" id="watchAddressInput" placeholder="0x... BSC address or whale wallet" />
              <button class="wallet-switch-submit" onclick="const addr = document.getElementById('watchAddressInput').value.trim(); if(addr) setWatchOnlyWallet(addr);">Audit</button>
            </div>
            <div class="wallet-demo-row">
              <span class="dim">Try Demo Smart Wallet:</span>
              <button class="pill demo-pill" onclick="setWatchOnlyWallet('0x6827d403df6b07ba405a6cfe0305dbd0fc727f55')">0x6827...7f55 (0724.ai)</button>
            </div>
          </div>
        </div>
      </div>
    `;
  } else {
    const bscUrl = `https://bscscan.com/address/${connectedWallet}`;
    const shortAddr = `${connectedWallet.slice(0, 8)}...${connectedWallet.slice(-6)}`;
    const isWatchOnly = walletProviderName === 'Watch-Only';

    content.innerHTML = `
      <div class="wallet-card-container">
        <!-- Header -->
        <div class="modal-header wallet-card-header">
          <div class="modal-title wallet-card-title">
            <span class="wallet-title-icon">👛</span>
            <span>Connected Account</span>
            <span class="badge ${isWatchOnly ? 'badge-yellow' : 'badge-green'} wallet-status-badge">
              ${isWatchOnly ? '👁️ Watch-Only Auditor' : '🟢 Live Web3'}
            </span>
          </div>
          <button class="modal-close-btn" onclick="document.getElementById('walletModal').close ? document.getElementById('walletModal').close() : document.getElementById('walletModal').removeAttribute('open')">✕ [ESC]</button>
        </div>

        <!-- Hero Identity Banner -->
        <div class="wallet-hero-card">
          <div class="wallet-hero-avatar">
            <span class="avatar-emoji">${isWatchOnly ? '👁️' : '👛'}</span>
          </div>
          <div class="wallet-hero-content">
            <div class="wallet-hero-meta">
              <span class="wallet-hero-tag provider-tag">${walletProviderName}</span>
              <span class="wallet-hero-tag network-tag">🟢 BSC Chain ID: 56</span>
              <span class="wallet-hero-tag ${isAgenticWallet ? 'badge-green' : 'badge-yellow'}" style="font-weight: 700;">
                ${isAgenticWallet ? '🤖 Agentic (.ai Owner)' : '👤 Non-Agentic (No .ai NFT)'}
              </span>
            </div>
            <div class="wallet-addr-action-row">
              <span class="wallet-addr-display" title="${connectedWallet}">${shortAddr}</span>
              <button class="wallet-inline-btn" id="walletCopyBtn" onclick="navigator.clipboard.writeText('${connectedWallet}'); this.innerHTML='<span style=\\'color:var(--color-green)\\'>✓ Copied</span>'; setTimeout(()=>this.innerHTML='📋 Copy', 1500);" title="Copy Address">
                📋 Copy
              </button>
              <a href="${bscUrl}" target="_blank" rel="noopener" class="wallet-inline-btn" title="View on BscScan">
                ↗ BscScan
              </a>
            </div>
            <div class="wallet-addr-full">
              <code>${connectedWallet}</code>
            </div>
            ${!isAgenticWallet ? `
              <div style="margin-top: 12px; font-size: 11px; line-height: 1.5; color: var(--color-yellow); background: rgba(234, 179, 8, 0.08); border: 1px solid rgba(234, 179, 8, 0.25); border-radius: 6px; padding: 8px 12px; text-align: left;">
                ℹ️ <strong>Standard Wallet:</strong> This address does not hold a Space ID <code>.ai</code> Agent ID NFT. You can track portfolios and trade on DEXs, but <em>Agent ID Exclusive</em> bonding curve drops require an <code>.ai</code> domain.
              </div>
            ` : `
              <div style="margin-top: 12px; font-size: 11px; line-height: 1.5; color: var(--color-green); background: rgba(0, 255, 136, 0.08); border: 1px solid rgba(0, 255, 136, 0.25); border-radius: 6px; padding: 8px 12px; text-align: left;">
                🤖 <strong>Agent ID Verified:</strong> On-chain verification confirms this wallet owns an active <code>.ai</code> Agent ID NFT. Authorized for 1-Click Exclusive Bonding Curve Mints!
              </div>
            `}
          </div>
        </div>

        <!-- 4 Clean Metric Tiles (2x2 Grid) -->
        <div class="wallet-stats-grid">
          <div class="wallet-stat-tile">
            <div class="tile-header">
              <span class="tile-icon">⛽</span>
              <span class="tile-label">GAS RESERVE</span>
            </div>
            <div class="tile-value gold">${userNativeBnbBalance.toFixed(6)} BNB</div>
            <div class="tile-sub">≈ ${formatBnbAsUsd(userNativeBnbBalance).replace(/^≈/, '')} USD</div>
          </div>

          <div class="wallet-stat-tile">
            <div class="tile-header">
              <span class="tile-icon">🎒</span>
              <span class="tile-label">HOLDINGS TRACKED</span>
            </div>
            <div class="tile-value cyan">${userOwnedContracts.size} .ai</div>
            <div class="tile-sub">Active ecosystem positions</div>
          </div>

          <div class="wallet-stat-tile">
            <div class="tile-header">
              <span class="tile-icon">🌐</span>
              <span class="tile-label">NETWORK</span>
            </div>
            <div class="tile-value green">BNB Smart Chain</div>
            <div class="tile-sub">Chain ID: 56 Mainnet</div>
          </div>

          <div class="wallet-stat-tile">
            <div class="tile-header">
              <span class="tile-icon">☁️</span>
              <span class="tile-label">CLOUD WATCHLIST</span>
            </div>
            <div class="tile-value green">
              <span class="pulse-dot" style="margin-right: 4px;"></span>Active 24/7
            </div>
            <div class="tile-sub">Cloud Sync Engine</div>
          </div>
        </div>

        <!-- Actions Grid -->
        <div class="wallet-actions-section">
          <div class="wallet-section-title">ACCOUNT ACTIONS</div>
          <div class="wallet-actions-grid">
            <button class="wallet-action-btn btn-rescan" onclick="scanUserPortfolio(connectedWallet, true)">
              <span class="btn-icon">🔄</span>
              <span class="btn-text">Rescan Portfolio</span>
            </button>
            <button class="wallet-action-btn btn-sync" onclick="openSyncModal()">
              <span class="btn-icon">📱</span>
              <span class="btn-text">Sync to Mobile</span>
            </button>
            <a href="${bscUrl}" target="_blank" rel="noopener" class="wallet-action-btn btn-explorer">
              <span class="btn-icon">🔍</span>
              <span class="btn-text">View on BscScan</span>
            </a>
            <button class="wallet-action-btn btn-disconnect" onclick="disconnectWallet()">
              <span class="btn-icon">🔌</span>
              <span class="btn-text">Disconnect Wallet</span>
            </button>
          </div>
        </div>

        <!-- Switch Address Section -->
        <div class="wallet-switch-card">
          <div class="wallet-switch-header">
            <span class="switch-title">Switch Address (Watch-Only Auditor):</span>
          </div>
          <div class="wallet-switch-bar">
            <span class="switch-icon">👁️</span>
            <input type="text" id="switchAddressInput" placeholder="0x... paste new BSC address" />
            <button class="wallet-switch-submit" onclick="const addr = document.getElementById('switchAddressInput').value.trim(); if(addr) setWatchOnlyWallet(addr);">
              Switch
            </button>
          </div>
        </div>
      </div>
    `;
  }

  if (typeof modal.showModal === 'function') modal.showModal();
  else modal.setAttribute('open', '');
}

// Binance Web3 Mobile App & Connection Guide Modal
function openBinanceQrModal() {
  const modal = document.getElementById('walletModal');
  const content = document.getElementById('walletModalContent');
  if (!modal || !content) return;
  beginWalletSession('qr_guide', 'binance_qr');

  const currentOrigin = (typeof window !== 'undefined' && window.location.origin) ? window.location.origin : 'https://agent-id-monitor.vercel.app';
  const qrApiUrl = `https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=${encodeURIComponent(currentOrigin)}&bgcolor=ffffff&color=03080c&margin=6`;

  content.innerHTML = `
    <div class="wallet-card-container">
      <div class="modal-header wallet-card-header">
        <div class="modal-title wallet-card-title">
          <button class="wallet-back-btn" onclick="walletModalBack()">← Back</button>
          <span class="wallet-title-icon" style="margin-left: 8px;">🟡</span>
          <span>Connect Binance Agentic Wallet</span>
        </div>
        <button class="modal-close-btn" onclick="document.getElementById('walletModal').close ? document.getElementById('walletModal').close() : document.getElementById('walletModal').removeAttribute('open')">✕ [ESC]</button>
      </div>

      <div class="wallet-connect-body" style="text-align: center; padding: 16px 14px;">
        <!-- Method 1: Binance Web3 Discover (Native Mobile DApp Browser) -->
        <div style="background: rgba(240, 185, 11, 0.05); border: 1px solid rgba(240, 185, 11, 0.3); border-radius: 8px; padding: 14px; margin-bottom: 14px; text-align: left;">
          <div style="font-weight: 700; color: #F0B90B; font-size: 13px; margin-bottom: 8px;">
            📱 Method 1: Open in Binance Web3 Discover (Recommended)
          </div>
          <div style="font-size: 12px; color: var(--text-dim); line-height: 1.6; margin-bottom: 10px;">
            1. Open <strong>Binance App</strong> ➔ tap <strong>Web3</strong> tab at the top.<br/>
            2. Tap <strong>Discover</strong> (compass icon 🧭 at bottom).<br/>
            3. Paste this link in the top search bar & press enter:
          </div>
          <div style="display: flex; gap: 8px;">
            <input type="text" id="binanceDappUrlInput" readonly value="${currentOrigin}" style="flex: 1; background: #03080c; border: 1px solid var(--border-color); color: var(--color-cyan); padding: 8px 10px; border-radius: 4px; font-family: monospace; font-size: 12px;" />
            <button class="wallet-switch-submit" onclick="navigator.clipboard.writeText('${currentOrigin}'); this.textContent='✓ Copied!'; setTimeout(()=>this.textContent='Copy', 1500);" style="padding: 0 16px;">Copy</button>
          </div>
          <div style="font-size: 11px; color: var(--color-green); margin-top: 6px;">
            🟢 Inside Discover, your .ai Agentic Wallet is automatically injected with 1-Click Mint authorization!
          </div>
        </div>

        <!-- Method 2: Camera QR Scan (Standard Safari / Chrome) -->
        <div style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; padding: 12px; margin-bottom: 14px;">
          <div style="font-size: 12px; color: var(--text-dim); margin-bottom: 8px;">
            Or scan with your phone's <strong>Standard Camera</strong> (Safari / Chrome):
          </div>
          <div style="background: #ffffff; padding: 8px; border-radius: 6px; display: inline-block; margin-bottom: 6px;">
            <img src="${qrApiUrl}" alt="Standard Camera QR" style="width: 140px; height: 140px; display: block;" />
          </div>
        </div>

        <!-- Method 3: Desktop Import via Rabby -->
        <div style="background: rgba(0, 240, 255, 0.05); border: 1px solid rgba(0, 240, 255, 0.2); border-radius: 8px; padding: 12px 14px; margin-bottom: 14px; text-align: left;">
          <div style="font-weight: 700; color: var(--color-cyan); font-size: 12px; margin-bottom: 6px;">
            💻 Prefer Desktop? Use in Rabby directly:
          </div>
          <div style="font-size: 11px; color: var(--text-dim); line-height: 1.5;">
            In Binance App: <strong>Web3 ➔ Settings ➔ Wallet Management ➔ Export Key</strong>.<br/>
            In Rabby Desktop: <strong>Add Account ➔ Import Private Key</strong>.<br/>
            You can then 1-click mint directly from desktop using your official Agentic address!
          </div>
        </div>

        <!-- Method 4: Watch-Only Auditor Mode -->
        <div class="wallet-switch-card" style="margin-top: 6px; text-align: left;">
          <div class="wallet-switch-header">
            <span class="switch-title">👁️ Watch-Only Auditor (Audit Holdings):</span>
          </div>
          <div class="wallet-switch-bar">
            <span class="switch-icon">🤖</span>
            <input type="text" id="binanceSyncInput" placeholder="Paste BSC wallet or e.g. huys.ai" />
            <button class="wallet-switch-submit" onclick="const a = document.getElementById('binanceSyncInput').value.trim(); if(a) setWatchOnlyWallet(a);">Sync</button>
          </div>
        </div>
      </div>
    </div>
  `;

  if (typeof modal.showModal === 'function') modal.showModal();
  else modal.setAttribute('open', '');
}

// Cross-Device Sync Modal (QR Code & Watchlist Link)
function openSyncModal() {
  const modal = document.getElementById('syncModal');
  const content = document.getElementById('syncModalContent');
  if (!modal || !content) return;

  const activeWallet = connectedWallet || 'guest';
  const syncUrl = `${(typeof window !== 'undefined' ? window.location.origin : '')}/?wallet=${activeWallet}`;
  const qrApiUrl = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(syncUrl)}&bgcolor=ffffff&color=03080c&margin=8`;

  content.innerHTML = `
    <div class="modal-header">
      <div class="modal-title">📱 Cross-Device Watchlist & Portfolio Sync</div>
      <button class="modal-close-btn" onclick="document.getElementById('syncModal').close ? document.getElementById('syncModal').close() : document.getElementById('syncModal').removeAttribute('open')">✕ [ESC]</button>
    </div>
    <div class="modal-section">
      <div style="font-size: 12px; color: var(--text-dim); line-height: 1.6;">
        Your starred favorites and monitored tokens are bound to your wallet address:
        <code style="color: var(--color-cyan);">${activeWallet}</code>.
        <br/>Watchlists automatically synchronize between desktop and mobile browsers via cloud sync engine.
      </div>

      <div class="qr-container">
        <img src="${qrApiUrl}" alt="Scan QR Code to open on Mobile" style="width: 100%; height: 100%; object-fit: contain; border-radius: 4px;" onerror="this.parentElement.innerHTML='<div style=\\'color:#333; font-size:11px; text-align:center; padding:20px;\\'>Scan Link Below</div>'" />
      </div>
      <div style="text-align: center; font-size: 11px; color: var(--color-cyan); margin-top: 4px;">
        Scan with Phone Camera or MetaMask / Binance Web3 App Browser
      </div>

      <div class="sync-url-box">
        <input type="text" id="syncShareInput" readonly value="${syncUrl}" />
        <button class="copy-btn" onclick="navigator.clipboard.writeText('${syncUrl}'); this.textContent='Copied!'; setTimeout(()=>this.textContent='Copy', 1500)">Copy</button>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 14px; font-size: 11px; padding: 8px 12px; background: rgba(0, 255, 136, 0.06); border: 1px solid rgba(0, 255, 136, 0.2); border-radius: 4px;">
        <span>Cloud Sync Status:</span>
        <strong style="color: var(--color-green);">🟢 Synced (Cloud Engine)</strong>
      </div>
    </div>
  `;

  if (typeof modal.showModal === 'function') modal.showModal();
  else modal.setAttribute('open', '');
}

// Check URL search params for deep linked wallet (e.g. ?wallet=0x...) or modal (e.g. ?modal=hof)
function checkUrlParams() {
  if (typeof window === 'undefined') return;
  const urlParams = new URLSearchParams(window.location.search);
  const qWallet = urlParams.get('wallet');
  if (qWallet && qWallet.startsWith('0x') && qWallet.length === 42) {
    setWatchOnlyWallet(qWallet);
  }
  const qModal = urlParams.get('modal');
  if (qModal === 'hof' || qModal === 'hall-of-fame') {
    setTimeout(() => openHallOfFameModal('ALL'), 300);
  } else if (qModal === 'smart' || qModal === 'smart-hunters') {
    setTimeout(() => openSmartLeaderboardModal('ALL'), 300);
  }
  const qFollow = urlParams.get('follow');
  if (qFollow) {
    toggleFollowWallet(qFollow);
  }
  const qHunter = urlParams.get('hunter') || urlParams.get('activity');
  if (qHunter) {
    setTimeout(() => viewWalletActivity(qHunter), 300);
  }
  const qFilter = urlParams.get('filter');
  if (qFilter === 'FOLLOWED' || qFilter === 'followed') {
    setTimeout(async () => {
      const p = document.getElementById('pillFollowed');
      if (p) p.click();
      else {
        activeFilter = 'FOLLOWED';
        await loadSmartDrops();
        renderTable();
      }
    }, 200);
  }
  const qInspect = urlParams.get('inspect') || urlParams.get('token') || urlParams.get('symbol');
  if (qInspect) {
    const tryInspect = async (attempts = 0) => {
      let target = findTokenByQuery(qInspect);
      if (!target && attempts >= 2) {
        try {
          const res = await fetch(`${BACKEND_URL}/api/projects?search=${encodeURIComponent(qInspect)}`);
          if (res.ok) {
            const data = await res.json();
            if (data && data.projects && data.projects.length > 0) {
              target = data.projects[0];
            }
          }
        } catch (e) {}
      }
      if (target) {
        // The modal module may still be loading: wait for the modal before opening its chart or scrolling it.
        openModal(target).then(() => {
          if (urlParams.get('chart') === '1') {
            setTimeout(() => toggleDexChart(target.contract), 200);
          }
          if (urlParams.get('scroll') === 'down') {
            setTimeout(() => {
              const m = document.getElementById('modalContent') || document.querySelector('#inspectModal .modal-content');
              if (m) m.scrollTop = m.scrollHeight;
            }, 350);
          }
        });
      } else if (attempts < 25) {
        setTimeout(() => tryInspect(attempts + 1), 200);
      }
    };
    tryInspect();
  }
}

// Auto-reconnect previously authorized wallet
async function tryAutoReconnect() {
  if (typeof localStorage === 'undefined') return;
  const saved = localStorage.getItem('agent_id_connected_wallet');
  const providerType = localStorage.getItem('agent_id_wallet_provider');
  if (!saved || !saved.startsWith('0x')) return;

  if (providerType === 'Watch-Only') {
    connectedWallet = saved.toLowerCase();
    walletProviderName = 'Watch-Only';
    updateWalletButtonUI();
    loadFavoritesForActiveWallet();
    scanUserPortfolio(connectedWallet);
    return;
  }

  const provider = getInjectedProvider(providerType);
  if (provider && provider.request) {
    try {
      const accounts = await provider.request({ method: 'eth_accounts' });
      if (accounts && accounts.length > 0 && accounts[0].toLowerCase() === saved.toLowerCase()) {
        connectedWallet = accounts[0].toLowerCase();
        walletProviderName = providerType || 'Web3 Wallet';
        updateWalletButtonUI();
        loadFavoritesForActiveWallet();
        scanUserPortfolio(connectedWallet);

        if (provider.on) {
          provider.on('accountsChanged', (newAccounts) => {
            if (!newAccounts || newAccounts.length === 0) disconnectWallet();
            else {
              connectedWallet = newAccounts[0].toLowerCase();
              localStorage.setItem('agent_id_connected_wallet', connectedWallet);
              updateWalletButtonUI();
              loadFavoritesForActiveWallet();
              scanUserPortfolio(connectedWallet);
            }
          });
        }
      }
    } catch (e) {
      console.warn('Auto-reconnect silent probe failed:', e);
    }
  }
}

// Expose Web3 functions on window for inline handlers
if (typeof window !== 'undefined') {
  window.openWalletModal = openWalletModal;
  window.openBinanceQrModal = openBinanceQrModal;
  window.handleBinanceWalletClick = handleBinanceWalletClick;
  window.openSyncModal = openSyncModal;
  window.connectWeb3 = connectWeb3;
  window.disconnectWallet = disconnectWallet;
  window.setWatchOnlyWallet = setWatchOnlyWallet;
  window.scanUserPortfolio = scanUserPortfolio;
}

// Fetch Latest BSC Block via Public RPC (with fallback)
// Latest-block poll (public BSC RPCs + syncLiveAgentWalletsOnChain). Like the delta feed poll it skips hidden
// tabs: the interval is stopped on visibilitychange -> hidden and restarted (after one immediate poll) on return.
const BLOCK_POLL_INTERVAL_MS = 10000;
let blockPollTimer = null;
function startBlockPolling() {
  if (blockPollTimer) clearInterval(blockPollTimer);
  blockPollTimer = null;
  if (typeof document !== 'undefined' && document.hidden) return;
  blockPollTimer = setInterval(pollLatestBlock, BLOCK_POLL_INTERVAL_MS);
}
function stopBlockPolling() {
  if (blockPollTimer) {
    clearInterval(blockPollTimer);
    blockPollTimer = null;
  }
}

async function pollLatestBlock() {
  if (typeof document !== 'undefined' && document.hidden) return;
  const hex = await sendRpcCall('eth_blockNumber', []);
  if (hex) {
    const blkNum = parseInt(hex, 16);
    document.getElementById('latestBlock').textContent = `${blkNum.toLocaleString()}`;
    syncLiveAgentWalletsOnChain(blkNum).catch(() => {});
    return;
  }
  document.getElementById('latestBlock').textContent = 'Live (BSC 56)';
}

// Strict timeout fetch helper to guarantee cold-boot backends never freeze initial UI
async function fetchWithTimeout(url, options = {}, timeoutMs = 12000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...options, signal: controller.signal });
    return res;
  } finally {
    clearTimeout(timer);
  }
}

// High-speed real-time delta polling state
let isPollingDelta = false;
let deltaPollTimer = null;
let lastDeltaEtag = '';
let lastFeedEtag = '';
// Full refresh of the first feed page (44 KB compressed) of a tab that stays open and visible. The server only picks up
// new market numbers about every 10 minutes (the worker batches its Supabase writes, DB_SUPABASE_FLUSH_INTERVAL_SEC),
// so asking more often downloads the same data again. New drops arrive through the 25 s delta poll below.
const FEED_REFRESH_INTERVAL_MS = 10 * 60 * 1000;
const DELTA_POLL_INTERVAL_MS = 25000; // 25s interval protects hosting bandwidth while keeping market data near real-time

// Real-Time Delta Polling (auto-pauses when tab is hidden or backgrounded, unless drop alerts are on)
async function pollDeltaFeed() {
  // A hidden tab polls only while drop alerts are on (see startDeltaPolling); otherwise it stays paused as before.
  if (isPollingDelta || !latestKnownBlock || (typeof document !== 'undefined' && document.hidden && !isDropAlertActive())) return;
  isPollingDelta = true;

  try {
    const fetchOptions = {
      headers: lastDeltaEtag ? { 'If-None-Match': lastDeltaEtag } : {}
    };
    const res = await fetchWithTimeout(
      `${BACKEND_URL}/api/feed?since_block=${latestKnownBlock}`,
      fetchOptions,
      4000
    );
    if (res.status === 304) {
      // Bandwidth Guard: Zero bytes downloaded! State is unchanged.
      return;
    }
    if (!res.ok) return;

    const newEtag = res.headers.get('ETag');
    if (newEtag) lastDeltaEtag = newEtag;

    const data = await res.json();

    // Hidden tab: block polling is paused, so move since_block on with the feed's own head. Otherwise it goes stale and
    // every poll re-downloads drops the page already has.
    if (data && data.latest_block && typeof document !== 'undefined' && document.hidden) latestKnownBlock = data.latest_block;

    if (data && data.stats) {
      applyOverviewStats(data.stats);
    }
    if (data && data.agent_wallets) {
      applyAgentWalletsState(
        Number(data.agent_wallets),
        Number(data.agent_wallets_block || (data.stats && data.stats.agent_wallets_block) || 0)
      );
    }

    if (data && data.projects && data.projects.length > 0) {
      const newlyFound = [];
      data.projects.forEach(drop => {
        const c = (drop.contract || '').toLowerCase();
        if (c && !knownContracts.has(c)) {
          knownContracts.add(c);
          newlyFound.push(drop);
          drop._isNew = true;
        }
      });

      if (newlyFound.length > 0) {
        console.log(`%c[🚨 REAL-TIME DELTA DETECTED]`, 'color: #00ff88; font-weight: bold;', newlyFound);
        // Prepend new drops to the front of allDrops
        allDrops.unshift(...newlyFound);

        // Advance latest known block
        if (data.latest_block) {
          latestKnownBlock = data.latest_block;
        } else if (newlyFound[0].block) {
          latestKnownBlock = newlyFound[0].block;
        }

        // A hidden tab stays silent, as it always has. Its drop alerts are the OS notifications.
        if (!(typeof document !== 'undefined' && document.hidden)) playMarketAlertSound();
        newlyFound.slice(0, 3).forEach(drop => showNewMarketToast(drop));
        queueDropAlerts(newlyFound);

        // Check for sniper ready in new drops
        newlyFound.forEach(drop => {
          const c = (drop.contract || '').toLowerCase();
          const ready = isSniperReady(drop);
          if (ready && c && !knownSniperReady.has(c)) {
            knownSniperReady.add(c);
            const ms = Number(drop.max_supply || 0);
            const tm = Number(drop.total_minted || 0);
            const pct = ms > 0 ? (tm / ms * 100).toFixed(0) : '60+';
            showToast('🎯 SNIPER READY ALERT', `${drop.name || 'Drop'} reached ${pct}% progress (Passed 60% rule)`, '🎯');
          }
        });

        // Re-hydrate user balances if wallet connected
        if (connectedWallet && userOwnedContracts.size > 0) {
          allDrops.forEach(drop => {
            const c = (drop.contract || '').toLowerCase();
            const raw = userOwnedContracts.get(c);
            if (raw && raw > 0n) {
              const std = String(drop.standard || '');
              const isNft = std.includes('721') || std.includes('NFT') || Boolean(drop.is_nft);
              drop.user_balance = isNft ? Number(raw) : balanceFromRaw(drop, raw);
              drop.user_balance_wei = raw;
            }
          });
          markDropsDataChanged();
          updatePortfolioBannerUI();
        }

        renderStats(allDrops);
        renderTable();
        checkUrlParams();
        checkUrlHash();

        const timeStr = new Date().toLocaleTimeString();
        const syncEl = document.getElementById('syncTime');
        if (syncEl) syncEl.textContent = `Synced ${timeStr}`;
      }
    }
  } catch (err) {
    // Delta poll is resilient; silent fallback on minor network hitch
  } finally {
    isPollingDelta = false;
  }
}

// A hidden tab keeps the delta poll only while drop alerts are on, and then at this slower pace (one request a minute,
// block polling stays paused). Otherwise the poll stops, as it always has.
const DROP_ALERT_BACKGROUND_POLL_MS = 60 * 1000;
function startDeltaPolling() {
  if (deltaPollTimer) clearInterval(deltaPollTimer);
  deltaPollTimer = null;
  if (typeof document !== 'undefined' && document.hidden) {
    if (isDropAlertActive()) deltaPollTimer = setInterval(pollDeltaFeed, DROP_ALERT_BACKGROUND_POLL_MS);
    return;
  }
  deltaPollTimer = setInterval(pollDeltaFeed, DELTA_POLL_INTERVAL_MS);
}

// Bandwidth Guard: Auto-pause polling when tab is backgrounded, resume when active
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    const ticker = document.getElementById('tickerTrack');
    if (document.hidden) {
      startDeltaPolling(); // hidden: drops the fast poll, keeps the slow one only while drop alerts are on
      stopBlockPolling();
      if (ticker) ticker.style.animationPlayState = 'paused';
    } else {
      if (ticker) ticker.style.animationPlayState = 'running';
      pollDeltaFeed();
      startDeltaPolling();
      pollLatestBlock();
      startBlockPolling();
    }
  });
}

// Load older drops when user scrolls or clicks Load More button
let loadMoreOffset = null; // rows of the server feed already requested by loadMoreDrops
async function loadMoreDrops() {
  if (isLoadingMore || !hasMoreDrops) return;
  isLoadingMore = true;
  const btn = document.getElementById('loadMoreBtn');
  if (btn) btn.innerHTML = '<span>⏳ Loading Older Drops...</span>';

  try {
    // Page by how many rows we already requested from the server, not allDrops.length:
    // allDrops also holds secondary/smart items, so its length drifts from the server's feed order.
    const offset = loadMoreOffset !== null ? loadMoreOffset : allDrops.length;
    const res = await fetchWithTimeout(`${BACKEND_URL}/api/feed?limit=150&offset=${offset}`, { cache: 'default' }, 8000);
    if (!res.ok) throw new Error('Failed to load more');
    const data = await res.json();
    if (data && data.projects && data.projects.length > 0) {
      appendUniqueDrops(data.projects);
      loadMoreOffset = offset + data.projects.length;
      hasMoreDrops = Boolean(data.has_more);
      if (data.stats) globalStats = data.stats;
      renderTable();
    } else {
      hasMoreDrops = false;
      const wrap = document.getElementById('loadMoreWrap');
      if (wrap) wrap.style.display = 'none';
    }
  } catch (err) {
    console.error('Failed to load more drops:', err);
  } finally {
    isLoadingMore = false;
    if (btn) {
      const tot = globalStats ? globalStats.total : allDrops.length;
      btn.innerHTML = `<span>⬇️ Load Older Drops (<span id="loadedCount">${allDrops.length}</span> of <span id="totalAvailableCount">${tot}</span>)</span>`;
    }
  }
}
if (typeof window !== 'undefined') window.loadMoreDrops = loadMoreDrops;

// While the page shows the bundled snapshot (backend asleep / cold-starting), retry the live feed on a short
// back-off instead of waiting for the 5-minute refresh, so the page goes live as soon as Render wakes up.
const LIVE_FEED_RETRY_DELAYS_MS = [5000, 15000, 30000];
let hasLoadedLiveFeed = false;
let liveFeedRetryIndex = 0;
let liveFeedRetryTimer = null;
function scheduleLiveFeedRetry() {
  if (hasLoadedLiveFeed) return;
  watchForBackendWake();
  if (liveFeedRetryTimer || liveFeedRetryIndex >= LIVE_FEED_RETRY_DELAYS_MS.length) return;
  const delay = LIVE_FEED_RETRY_DELAYS_MS[liveFeedRetryIndex++];
  liveFeedRetryTimer = setTimeout(() => {
    liveFeedRetryTimer = null;
    fetchDrops().catch(() => {});
  }, delay);
}

// Render's free instance sleeps after 15 minutes without traffic and takes ~35 s to answer again; the failed live-feed
// request has already woken it. Rather than guess when it is up (the back-off above can land just before it is, then
// wait 15-30 s more), hold one long /healthz request open and reload the live data the moment it answers. Once per
// page: if the feed still fails after that, the back-off carries on as before.
const BACKEND_WAKE_TIMEOUT_MS = 90000;
let backendWakeWatchStarted = false;
function watchForBackendWake() {
  if (backendWakeWatchStarted || hasLoadedLiveFeed) return;
  backendWakeWatchStarted = true;
  fetchWithTimeout(`${BACKEND_URL}/healthz`, { cache: 'no-store' }, BACKEND_WAKE_TIMEOUT_MS)
    .then((res) => {
      if (!res || !res.ok || hasLoadedLiveFeed) return;
      if (liveFeedRetryTimer) {
        clearTimeout(liveFeedRetryTimer);
        liveFeedRetryTimer = null;
      }
      fetchDrops().catch(() => {});
      fetchOverviewStats().catch(() => {});
    })
    .catch(() => {});
}

// Fetch drops from 24/7 Render cloud scanner using lightweight initial feed (/api/feed?limit=150)
async function fetchDrops() {
  if (typeof document !== 'undefined' && document.hidden && !isInitialLoad) return;
  try {
    // Fast path: On initial load, immediately populate from local feed_snapshot.json (<20ms)
    // so mobile/desktop users see the complete market table instantly with zero white/blank space.
    if (isInitialLoad && (!allDrops || allDrops.length === 0)) {
      try {
        const localRes = await fetchWithTimeout(`feed_snapshot.json`, { cache: 'default' }, 2000);
        if (localRes.ok) {
          const localDrops = await localRes.json();
          if (Array.isArray(localDrops) && localDrops.length > 0) {
            allDrops = localDrops;
            allDrops.forEach(drop => {
              const c = (drop.contract || '').toLowerCase();
              if (c) knownContracts.add(c);
              if (isSniperReady(drop) && c) knownSniperReady.add(c);
            });
            renderStats(allDrops);
            renderTable();
            const timeStr = new Date().toLocaleTimeString();
            const syncEl = document.getElementById('syncTime');
            if (syncEl) syncEl.textContent = `Synced ${timeStr}`;
          }
        }
      } catch (err) {
        console.warn('Initial fast snapshot bypassed:', err);
      }
    }

    let res;
    let source = 'Render Feed';
    const feedHeaders = lastFeedEtag ? { 'If-None-Match': lastFeedEtag } : {};
    // One attempt at the backend, then straight to the bundled snapshot. BACKEND_URL already is the Render host in
    // production, so retrying the same host only waits out a second timeout while it cold-starts (~35 s).
    try {
      res = await fetchWithTimeout(`${BACKEND_URL}/api/feed?limit=150`, { headers: feedHeaders }, 8000);
      if (res.status === 304) {
        // Feed unchanged, zero bytes transferred
        return;
      }
      if (!res.ok) throw new Error('Render /api/feed failed');
    } catch (e) {
      source = 'Local Snapshot';
      res = await fetchWithTimeout(`feed_snapshot.json`, { cache: 'default' }, 4000);
      // Resilient stats hydration with multi-mirror fallback and automatic retry
      fetchOverviewStats().catch(() => {});
    }
    const isLiveSource = source !== 'Local Snapshot';
    // New-market / sniper alerts compare against what is already on screen. Until the live feed has loaded once,
    // that is the (possibly days-old) snapshot, so the first live load must not toast every drop since then.
    const alertsEnabled = !isInitialLoad && hasLoadedLiveFeed;
    if (isLiveSource) {
      hasLoadedLiveFeed = true;
      liveFeedRetryIndex = LIVE_FEED_RETRY_DELAYS_MS.length; // live: no more fast retries needed
    }

    if (res && res.headers) {
      const feedEtag = res.headers.get('ETag');
      if (feedEtag) lastFeedEtag = feedEtag;
    }

    const payload = await res.json();
    let freshDrops = [];
    if (Array.isArray(payload)) {
      freshDrops = payload;
    } else if (payload && Array.isArray(payload.projects)) {
      freshDrops = payload.projects;
      if (payload.stats) {
        applyOverviewStats(payload.stats);
      }
      if (payload.agent_wallets) {
        applyAgentWalletsState(
          Number(payload.agent_wallets),
          Number(payload.agent_wallets_block || (payload.stats && payload.stats.agent_wallets_block) || 0)
        );
      }
      hasMoreDrops = Boolean(payload.has_more);
      if (payload.latest_block) latestKnownBlock = payload.latest_block;
    }

    if (freshDrops.length > 0) {
      // Merge fresh drops while strictly preserving all loaded secondary and smart market runners
      const mergedMap = new Map();
      freshDrops.forEach(d => {
        const c = (d.contract || '').toLowerCase();
        if (c) mergedMap.set(c, d);
      });
      allDrops.forEach(d => {
        const c = (d.contract || '').toLowerCase();
        if (c && !mergedMap.has(c)) {
          mergedMap.set(c, d);
        }
      });
      allDrops = Array.from(mergedMap.values());
      if (activeTab === 'SECONDARY') {
        allDrops.sort((a, b) => getSanitizedMcap(b) - getSanitizedMcap(a));
      } else {
        allDrops.sort((a, b) => Number(b.block || 0) - Number(a.block || 0));
      }
      if (!latestKnownBlock && allDrops[0] && allDrops[0].block) {
        latestKnownBlock = allDrops[0].block;
      }
    }

    // Check for newly discovered markets
    const newlyFound = [];
    allDrops.forEach(drop => {
      const c = (drop.contract || '').toLowerCase();
      if (c && !knownContracts.has(c)) {
        knownContracts.add(c);
        if (alertsEnabled) {
          newlyFound.push(drop);
          drop._isNew = true;
        }
      }
    });

    if (alertsEnabled && newlyFound.length > 0) {
      console.log(`%c[🚨 NEW MARKET DETECTED]`, 'color: #00ff88; font-weight: bold;', newlyFound);
      playMarketAlertSound();
      newlyFound.slice(0, 3).forEach(drop => showNewMarketToast(drop));
      queueDropAlerts(newlyFound);
    }

    // Check for newly qualified Sniper Ready projects (Option B fast-track trigger)
    const newlySniperReady = [];
    allDrops.forEach(drop => {
      const c = (drop.contract || '').toLowerCase();
      const ready = isSniperReady(drop);
      if (ready && c && !knownSniperReady.has(c)) {
        knownSniperReady.add(c);
        if (alertsEnabled) {
          newlySniperReady.push(drop);
        }
      } else if (!ready && c && knownSniperReady.has(c)) {
        knownSniperReady.delete(c);
      }
    });

    if (alertsEnabled && newlySniperReady.length > 0) {
      console.log(`%c[🎯 NEW SNIPER READY DETECTED]`, 'color: #00ff88; font-weight: bold;', newlySniperReady);
      playMarketAlertSound();
      newlySniperReady.slice(0, 2).forEach(drop => {
        const ms = Number(drop.max_supply || 0);
        const tm = Number(drop.total_minted || 0);
        const pct = ms > 0 ? (tm / ms * 100).toFixed(0) : '60+';
        showToast('🎯 SNIPER READY ALERT', `${drop.name || 'Drop'} reached ${pct}% progress (Passed 60% rule)`, '🎯');
      });
    }
    isInitialLoad = false;
    if (!isLiveSource) scheduleLiveFeedRetry();

    // Header Counters with Global Pre-Calculated Stats
    const rEl = document.getElementById('radarCount');
    if (rEl) rEl.textContent = globalStats ? globalStats.total : allDrops.length;
    const sEl = document.getElementById('smartCount');
    if (sEl) {
      sEl.textContent = globalStats ? globalStats.smart : allDrops.filter(d => (d.smart_wallets && d.smart_wallets.length > 0) || (d.smart_minters && d.smart_minters.length > 0)).length;
    }
    markDropsDataChanged();
    updateAllFilterCounts();

    // Re-hydrate user balances if wallet is connected
    if (connectedWallet && userOwnedContracts.size > 0) {
      allDrops.forEach(drop => {
        const c = (drop.contract || '').toLowerCase();
        const raw = userOwnedContracts.get(c);
        if (raw && raw > 0n) {
          const std = String(drop.standard || '');
          const isNft = std.includes('721') || std.includes('NFT') || Boolean(drop.is_nft);
          drop.user_balance = isNft ? Number(raw) : balanceFromRaw(drop, raw);
          drop.user_balance_wei = raw;
        } else {
          drop.user_balance = 0;
          drop.user_balance_wei = 0n;
        }
      });
      markDropsDataChanged();
      updatePortfolioBannerUI();
    } else {
      const pCountEl = document.getElementById('portfolioCount');
      if (pCountEl) pCountEl.textContent = '0';
    }

    renderStats(allDrops);
    renderTable();
    checkUrlHash();

    const timeStr = new Date().toLocaleTimeString();
    const syncEl = document.getElementById('syncTime');
    if (syncEl) syncEl.textContent = `Synced ${timeStr}`;

    console.log(
      `%c[☁️ Cloud Sync: ${source}]`,
      'color: #00ff88; font-weight: bold;',
      `Loaded ${allDrops.length} drops from ${source} at ${timeStr}`
    );

    // Initialize 4-second real-time delta polling for instant updates
    startDeltaPolling();

    // Historical smart drops (seeded_smart_drops.json, ~220 KB compressed) are NOT preloaded: every entry point that
    // needs them (Smart tab, Followed / More Filters, search, wallet activity, ?filter=followed) awaits
    // loadSmartDrops() itself. Only visitors who open those views pay for the download, unless the URL deep-links
    // straight into one of them.
    if (activeFilter === 'FOLLOWED' || activeFilter === 'SMART' || activeTab === 'SMART' || searchQuery.trim()) {
      loadSmartDrops().then(() => renderTable());
    }

    // The secondary-market list (/api/feed?filter=secondary&limit=300, ~120 KB compressed: more than the first page of
    // the feed itself) is NOT preloaded. It was ~70% of what one visit downloaded from Render, and Render's free tier
    // only has 5 GB of bandwidth a month. What needed it now comes from elsewhere: the Secondary tab count from
    // /api/stats, the Pulse cards from pulse_24h (top_runners / top_launches / top_nft_mints). It loads when the
    // Secondary tab opens, or when someone starts a search (so established tokens stay searchable).

  } catch (err) {
    console.error('Failed to load drops:', err);
    scheduleLiveFeedRetry();
  }
}

// ========================================================
// Manual Tab Sequence Customization & Drag-and-Drop
// ========================================================
const DEFAULT_PILLS_SEQUENCE = [
  'ALL',
  'LAUNCHPAD',
  'SNIPER_READY',
  'FREE',
  'ACTIVE',
  'FAVORITES'
];

const PILL_SEQUENCE_STORAGE_KEY = 'agent_id_pills_sequence';

let isDraggingPill = false;
let justFinishedDrag = false;
let draggedPill = null;

function getPillKey(el) {
  if (!el) return '';
  return el.getAttribute('data-filter') || el.id || '';
}

function getPillElementByKey(key) {
  if (!key) return null;
  const container = document.getElementById('filterPillsContainer');
  if (!container) return null;
  return container.querySelector(`[data-filter="${key}"]`) || document.getElementById(key);
}

function getCurrentPillSequence() {
  const container = document.getElementById('filterPillsContainer');
  if (!container || !container.children) return [...DEFAULT_PILLS_SEQUENCE];
  const seq = [];
  Array.from(container.children).forEach(child => {
    const key = getPillKey(child);
    if (key) seq.push(key);
  });
  return seq.length > 0 ? seq : [...DEFAULT_PILLS_SEQUENCE];
}

function updateResetPillBtnVisibility(seq) {
  const resetBtn = document.getElementById('resetPillOrderBtn');
  if (!resetBtn) return;
  const isDefault = JSON.stringify(seq || getCurrentPillSequence()) === JSON.stringify(DEFAULT_PILLS_SEQUENCE);
  resetBtn.style.display = isDefault ? 'none' : 'inline-flex';
}

function savePillSequence(seq) {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(PILL_SEQUENCE_STORAGE_KEY, JSON.stringify(seq));
    }
  } catch (e) {
    console.warn('Failed to save pill sequence to localStorage:', e);
  }
  updateResetPillBtnVisibility(seq);
}

function loadSavedPillSequence() {
  try {
    if (typeof localStorage !== 'undefined') {
      const saved = localStorage.getItem(PILL_SEQUENCE_STORAGE_KEY);
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) {
          const validSet = new Set(parsed);
          const complete = [...parsed];
          DEFAULT_PILLS_SEQUENCE.forEach(k => {
            if (!validSet.has(k)) complete.push(k);
          });
          return complete;
        }
      }
    }
  } catch (e) {
    console.warn('Failed to parse saved pill sequence:', e);
  }
  return [...DEFAULT_PILLS_SEQUENCE];
}

function applyPillSequence(seq) {
  const container = document.getElementById('filterPillsContainer');
  if (!container) return;
  const targetSeq = Array.isArray(seq) && seq.length > 0 ? seq : DEFAULT_PILLS_SEQUENCE;
  
  targetSeq.forEach(key => {
    const el = getPillElementByKey(key);
    if (el && el.parentNode === container) {
      container.appendChild(el);
    }
  });

  // Always keep moreFiltersWrapper pinned at the end of the quick preset pills
  const moreFilters = document.getElementById('moreFiltersWrapper');
  if (moreFilters && moreFilters.parentNode === container) {
    container.appendChild(moreFilters);
  }

  updateResetPillBtnVisibility(getCurrentPillSequence());
}

function resetPillSequence() {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.removeItem(PILL_SEQUENCE_STORAGE_KEY);
    }
  } catch (e) {
    console.warn('Failed to remove pill sequence from localStorage:', e);
  }
  applyPillSequence(DEFAULT_PILLS_SEQUENCE);
  const modal = document.getElementById('reorderPillsModal');
  if (modal && (modal.open || modal.hasAttribute('open'))) {
    renderReorderModalList();
  }
}

function openReorderModal() {
  const modal = document.getElementById('reorderPillsModal');
  if (!modal) return;
  renderReorderModalList();
  if (modal.showModal) {
    modal.showModal();
  } else {
    modal.setAttribute('open', '');
  }
}

function getPillDisplayLabel(el, key) {
  if (!el) {
    return key;
  }
  const clone = el.cloneNode(true);
  const spans = clone.querySelectorAll('span');
  spans.forEach(s => s.remove());
  let txt = clone.textContent.replace(/\(\s*\)/g, '').trim();
  if (!txt) txt = el.textContent.trim();
  return txt;
}

function renderReorderModalList() {
  const listEl = document.getElementById('reorderPillsList');
  if (!listEl) return;
  const currentSeq = getCurrentPillSequence();
  listEl.innerHTML = '';
  if (listEl.replaceChildren) {
    listEl.replaceChildren();
  }

  currentSeq.forEach((key, index) => {
    const el = getPillElementByKey(key);
    const label = getPillDisplayLabel(el, key);
    const isFirst = (index === 0);
    const isLast = (index === currentSeq.length - 1);

    const item = document.createElement('div');
    item.className = 'reorder-item';
    // Edge buttons use aria-disabled, not the disabled attribute: a truly disabled button swallows the tap, so a repeated
    // tap at the top or bottom looked like a dead button (replay: ~10 taps on ▲ Up in 5 s). They stay tappable and
    // movePill / movePillToTop say why nothing moved.
    item.innerHTML = `
      <div class="reorder-item-left">
        <span class="reorder-item-idx">#${index + 1}</span>
        <span class="reorder-item-name">${label}</span>
      </div>
      <div class="reorder-item-actions">
        <button class="reorder-btn" title="${isFirst ? 'Already at the top' : 'Move to Top'}" aria-disabled="${isFirst}" onclick="movePillToTop('${key}')">⬆ Top</button>
        <button class="reorder-btn" title="${isFirst ? 'Already at the top' : 'Move Up'}" aria-disabled="${isFirst}" onclick="movePill('${key}', -1)">▲ Up</button>
        <button class="reorder-btn" title="${isLast ? 'Already at the bottom' : 'Move Down'}" aria-disabled="${isLast}" onclick="movePill('${key}', 1)">▼ Down</button>
      </div>
    `;
    listEl.appendChild(item);
  });
}

// Reorder sheet feedback. A move highlights the row that moved and says where it went; a tap that cannot move the row
// (already first or last) shakes that row and says so, instead of doing nothing.
let _reorderStatusTimer = null;

function getReorderEl(id) {
  return (typeof document !== 'undefined' && typeof document.getElementById === 'function') ? document.getElementById(id) : null;
}

function showReorderStatus(text, tone = 'ok') {
  const el = getReorderEl('reorderPillsStatus');
  if (!el) return;
  el.textContent = text;
  if (el.dataset) el.dataset.tone = tone;
  clearTimeout(_reorderStatusTimer);
  _reorderStatusTimer = setTimeout(() => { el.textContent = ''; }, 1800);
}

function pulseReorderRow(index, className) {
  const list = getReorderEl('reorderPillsList');
  const row = list && list.children ? list.children[index] : null;
  if (!row || !row.classList || typeof row.classList.add !== 'function') return;
  row.classList.remove('reorder-item--moved', 'reorder-item--edge');
  if (typeof row.offsetWidth === 'number') void row.offsetWidth; // restart the CSS animation on repeated taps
  row.classList.add(className);
  setTimeout(() => {
    if (row.classList && typeof row.classList.remove === 'function') row.classList.remove(className);
  }, 900);
}

function reportReorderMove(newIndex) {
  pulseReorderRow(newIndex, 'reorder-item--moved');
  showReorderStatus(`Moved to #${newIndex + 1}`, 'ok');
}

function reportReorderEdge(index, edge) {
  trackAnalytics('reorder_edge_tapped', { edge: edge });
  pulseReorderRow(index, 'reorder-item--edge');
  showReorderStatus(edge === 'top' ? 'Already at the top' : 'Already at the bottom', 'edge');
}

function movePill(key, delta) {
  const seq = getCurrentPillSequence();
  const idx = seq.indexOf(key);
  if (idx === -1) return;
  const targetIdx = idx + delta;
  if (targetIdx < 0 || targetIdx >= seq.length) {
    reportReorderEdge(idx, targetIdx < 0 ? 'top' : 'bottom');
    return;
  }

  const temp = seq[idx];
  seq[idx] = seq[targetIdx];
  seq[targetIdx] = temp;

  applyPillSequence(seq);
  savePillSequence(seq);
  renderReorderModalList();
  reportReorderMove(targetIdx);
}

function movePillToTop(key) {
  const seq = getCurrentPillSequence();
  const idx = seq.indexOf(key);
  if (idx === -1) return;
  if (idx === 0) {
    reportReorderEdge(0, 'top');
    return;
  }
  seq.splice(idx, 1);
  seq.unshift(key);

  applyPillSequence(seq);
  savePillSequence(seq);
  renderReorderModalList();
  reportReorderMove(0);
}

function setupFilterPillsDragAndDrop() {
  const container = document.getElementById('filterPillsContainer');
  if (!container) return;

  const items = container.querySelectorAll('.pill, .pill-action');
  items.forEach(item => {
    item.setAttribute('draggable', 'true');

    item.addEventListener('dragstart', (e) => {
      isDraggingPill = true;
      draggedPill = item;
      item.classList.add('dragging');
      if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', getPillKey(item));
      }
    });

    item.addEventListener('dragend', () => {
      isDraggingPill = false;
      draggedPill = null;
      item.classList.remove('dragging');
      container.querySelectorAll('.pill, .pill-action').forEach(p => {
        p.classList.remove('drag-over-left', 'drag-over-right');
      });
      justFinishedDrag = true;
      setTimeout(() => {
        justFinishedDrag = false;
      }, 120);
    });

    item.addEventListener('dragover', (e) => {
      e.preventDefault();
      if (!draggedPill || draggedPill === item) return;
      if (e.dataTransfer) {
        e.dataTransfer.dropEffect = 'move';
      }

      const rect = item.getBoundingClientRect();
      const midX = rect.left + rect.width / 2;
      if (e.clientX < midX) {
        item.classList.add('drag-over-left');
        item.classList.remove('drag-over-right');
      } else {
        item.classList.add('drag-over-right');
        item.classList.remove('drag-over-left');
      }
    });

    item.addEventListener('dragleave', () => {
      item.classList.remove('drag-over-left', 'drag-over-right');
    });

    item.addEventListener('drop', (e) => {
      e.preventDefault();
      item.classList.remove('drag-over-left', 'drag-over-right');
      if (!draggedPill || draggedPill === item) return;

      const rect = item.getBoundingClientRect();
      const midX = rect.left + rect.width / 2;
      const insertBefore = (e.clientX < midX);

      if (insertBefore) {
        container.insertBefore(draggedPill, item);
      } else {
        container.insertBefore(draggedPill, item.nextSibling);
      }

      const newSeq = getCurrentPillSequence();
      savePillSequence(newSeq);
    });
  });
}

// Window globals for inline onclicks in HTML
if (typeof window !== 'undefined') {
  window.resetPillSequence = resetPillSequence;
  window.openReorderModal = openReorderModal;
  window.movePill = movePill;
  window.movePillToTop = movePillToTop;
  window.setSortMode = setSortMode;
}

function setSortMode(val) {
  sortMode = val;
  if (typeof document === 'undefined') return;
  const nativeSelect = document.getElementById('sortSelect');
  if (nativeSelect) nativeSelect.value = val;

  const label = document.getElementById('customSortLabel');
  const options = document.querySelectorAll('#customSortOptions .custom-option');

  options.forEach(o => {
    const oVal = o.getAttribute('data-value');
    if (oVal === val || (val === 'MCAP_DESC' && oVal === 'MCAP')) {
      o.classList.add('selected');
      o.setAttribute('aria-selected', 'true');
      const textNode = o.querySelector('span');
      if (label && textNode) label.textContent = textNode.textContent;
    } else {
      o.classList.remove('selected');
      o.setAttribute('aria-selected', 'false');
    }
  });

  if (typeof window !== 'undefined' && window.va) {
    window.va('event', { name: 'sort_changed', data: { sort: val } });
  }

  renderTable();
}

// Custom Cyberpunk Dropdown Handler
function setupCustomDropdown() {
  const wrapper = document.getElementById('customSortWrapper');
  const trigger = document.getElementById('customSortTrigger');
  const label = document.getElementById('customSortLabel');
  const options = wrapper ? wrapper.querySelectorAll('.custom-option') : [];
  const nativeSelect = document.getElementById('sortSelect');

  if (!wrapper || !trigger) return;

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    const isOpen = wrapper.classList.toggle('open');
    trigger.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
  });

  options.forEach(opt => {
    opt.addEventListener('click', (e) => {
      e.stopPropagation();
      const val = opt.getAttribute('data-value');
      setSortMode(val);

      wrapper.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');
    });
  });

  // Light dismiss: Close on click outside
  document.addEventListener('click', (e) => {
    if (!wrapper.contains(e.target)) {
      wrapper.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');
    }
  });

  // Close on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && wrapper.classList.contains('open')) {
      wrapper.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');
      trigger.focus();
    }
  });
}

// Setup More Filters Dropdown (DexScreener / GMGN Model)


// Synchronize Mobile Tab Selector UI with active tab and live badge counts
function syncMobileTabSelector() {
  const trigger = document.getElementById('customMobileTabTrigger');
  const activeLabel = document.getElementById('mobileTabActiveLabel');
  if (!trigger || !activeLabel) return;

  const tabDefs = {
    RADAR: { label: '1: 📡 Live Radar', countId: 'radarCount', badgeClass: 'badge-yellow' },
    SECONDARY: { label: '2: 📈 Secondary Market', countId: 'secondaryTabCount', badgeClass: 'badge-yellow' },
    WALLETS: { label: '3: 🏆 Wallet Leaderboard', countId: 'walletsTabCount', badgeClass: 'badge-yellow' },
    PORTFOLIO: { label: '4: 🎒 My Portfolio', countId: 'portfolioCount', badgeClass: 'badge-cyan' }
  };

  const curr = tabDefs[activeTab] || tabDefs.RADAR;
  const countEl = document.getElementById(curr.countId);
  const countVal = countEl ? countEl.textContent : '0';

  activeLabel.innerHTML = `
    <span class="mobile-tab-curr-name">${curr.label}</span>
    <span class="badge ${curr.badgeClass}">${countVal}</span>
  `;

  // Update selected class and counts in mobile dropdown options
  document.querySelectorAll('.mobile-tab-option').forEach(opt => {
    const t = opt.getAttribute('data-tab');
    const isSel = (t === activeTab);
    opt.classList.toggle('selected', isSel);
    opt.setAttribute('aria-selected', isSel ? 'true' : 'false');

    const optDef = tabDefs[t];
    if (optDef) {
      const optCountEl = document.getElementById(optDef.countId);
      const optBadge = opt.querySelector('.mobile-opt-badge');
      if (optBadge && optCountEl) {
        optBadge.textContent = optCountEl.textContent;
      }
    }
  });
}

// Setup Mobile Tab Dropdown Handler
function setupMobileTabDropdown() {
  const wrapper = document.getElementById('customMobileTabWrapper');
  const trigger = document.getElementById('customMobileTabTrigger');
  if (!wrapper || !trigger) return;

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    const isOpen = wrapper.classList.toggle('open');
    trigger.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    if (isOpen) {
      syncMobileTabSelector();
    }
  });

  document.querySelectorAll('.mobile-tab-option').forEach(opt => {
    opt.addEventListener('click', (e) => {
      e.stopPropagation();
      const tabKey = opt.getAttribute('data-tab');
      const targetBtn = document.querySelector(`.tab-btn[data-tab="${tabKey}"]`);
      if (targetBtn) {
        targetBtn.click();
      }
      wrapper.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');
      syncMobileTabSelector();
    });
  });

  // Light dismiss: Close on click outside
  document.addEventListener('click', (e) => {
    if (!wrapper.contains(e.target)) {
      wrapper.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');
    }
  });

  // Close on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && wrapper.classList.contains('open')) {
      wrapper.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');
      trigger.focus();
    }
  });
}

// Setup More Filters Dropdown (DexScreener / GMGN Model)
function setupMoreFiltersDropdown() {
  const wrapper = document.getElementById('moreFiltersWrapper');
  const trigger = document.getElementById('moreFiltersTrigger');
  const labelEl = document.getElementById('moreFiltersLabel');
  if (!wrapper || !trigger) return;

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    const isOpen = wrapper.classList.toggle('open');
    trigger.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    if (isOpen) {
      const options = document.getElementById('moreFiltersOptions');
      if (options) {
        options.style.left = '0';
        options.style.right = 'auto';
        const rect = options.getBoundingClientRect();
        if (rect.right > window.innerWidth - 10) {
          options.style.left = 'auto';
          options.style.right = '0';
        }
      }
    }
  });

  document.querySelectorAll('#moreFiltersOptions .custom-option').forEach(opt => {
    opt.addEventListener('click', async (e) => {
      e.stopPropagation();
      const filterKey = opt.getAttribute('data-filter');
      if (!filterKey) return;

      // Deselect standard pills
      document.querySelectorAll('.pill').forEach(b => b.classList.remove('active'));

      // Highlight selected option in dropdown
      document.querySelectorAll('#moreFiltersOptions .custom-option').forEach(o => o.classList.remove('selected'));
      opt.classList.add('selected');

      // Update trigger styling and label
      trigger.classList.add('active');
      const optText = opt.querySelector('span')?.textContent?.trim() || filterKey;
      if (labelEl) labelEl.textContent = `⚙️ ${optText}`;

      activeFilter = filterKey;
      saveFilterChoice(activeFilter);
      if ((activeFilter === 'FOLLOWED' || activeFilter === 'SMART') && !hasLoadedSmartDrops) {
        await loadSmartDrops();
      }

      wrapper.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');

      if (typeof window !== 'undefined' && window.va) {
        window.va('event', { name: 'filter_changed', data: { filter: activeFilter, source: 'more_dropdown' } });
      }
      renderTable();
    });
  });

  // Light dismiss: Close on click outside
  document.addEventListener('click', (e) => {
    if (!wrapper.contains(e.target)) {
      wrapper.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');
    }
  });

  // Close on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && wrapper.classList.contains('open')) {
      wrapper.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');
      trigger.focus();
    }
  });
}


// Initialize Application
async function init() {
  try {
    // Initialize persistent top gainers memory and sync historical seed
    loadSavedTopGainers();
    syncSeededTopGainers();

    // Initialize smart hunters & followed wallets
    loadFollowedWallets();
    syncSeededSmartHunters().then(() => { if (typeof updateWalletsTabBadge === 'function') updateWalletsTabBadge(); });

    // Initialize customized tab/pill sequence & drag-and-drop
    const savedPillsSeq = loadSavedPillSequence();
    applyPillSequence(savedPillsSeq);
    setupFilterPillsDragAndDrop();
    setupModalSwipeNav();

    // Sound alert toggle button setup
    const soundBtn = document.getElementById('soundToggle');
    const updateSoundBtn = () => {
      if (!soundBtn) return;
      if (soundEnabled) {
        soundBtn.className = 'sound-toggle-btn active';
        soundBtn.innerHTML = '<span class="sound-icon">🔊</span><span class="sound-label">SOUND ON</span>';
        soundBtn.title = 'Sound alerts enabled. Click to mute or test audio chime.';
      } else {
        soundBtn.className = 'sound-toggle-btn muted';
        soundBtn.innerHTML = '<span class="sound-icon">🔇</span><span class="sound-label">SOUND OFF</span>';
        soundBtn.title = 'Sound alerts muted. Click to enable.';
      }
    };
    updateSoundBtn();

    if (soundBtn) {
      soundBtn.addEventListener('click', () => {
        soundEnabled = !soundEnabled;
        localStorage.setItem('agent_sound_alerts', soundEnabled ? 'true' : 'false');
        updateSoundBtn();
        requestNotificationPermission();
        if (soundEnabled) {
          playMarketAlertSound(); // play quick preview chime
        }
      });
    }

    // Drop alerts toggle: state only on load; the permission prompt comes from its own click
    setupDropAlertToggle();

    // Donation buttons
    const donateHeader = document.getElementById('donateHeaderBtn');
    if (donateHeader) {
      donateHeader.addEventListener('click', () => copyDonationAddress(donateHeader, 'header'));
    }
    const footerDonate = document.getElementById('footerDonate');
    if (footerDonate) {
      footerDonate.addEventListener('click', () => copyDonationAddress(footerDonate, 'footer'));
    }

    // Unlock the Web Audio context on the user's first click or keystroke anywhere. This does not request notification
    // permission: that is asked only by the SOUND and ALERTS toggles, so no browser prompt appears before the user chooses.
    const unlockAudio = () => {
      if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume();
      }
    };
    document.addEventListener('click', unlockAudio, { once: true });
    document.addEventListener('keydown', unlockAudio, { once: true });

    // Infinite scroll: smoothly append batches and load older drops with RAF throttle
    let isScrollTicking = false;
    const handleInfiniteScroll = (target) => {
      if (isScrollTicking) return;
      isScrollTicking = true;
      requestAnimationFrame(() => {
        try {
          const isNearBottom = target === window
            ? (window.innerHeight + window.scrollY >= document.body.offsetHeight - 600)
            : (target.scrollTop + target.clientHeight >= target.scrollHeight - 300);

          if (!isNearBottom) return;

          const filtered = getFilteredDrops();
          if (tableRenderLimit < filtered.length) {
            appendNextTableBatch();
          } else if (activeTab === 'RADAR' && activeFilter === 'ALL' && !searchQuery && hasMoreDrops && !isLoadingMore) {
            loadMoreDrops();
          }
        } finally {
          isScrollTicking = false;
        }
      });
    };

    window.addEventListener('scroll', () => handleInfiniteScroll(window), { passive: true });
    const tableWrap = document.querySelector('.table-wrapper');
    if (tableWrap) {
      tableWrap.addEventListener('scroll', () => handleInfiniteScroll(tableWrap), { passive: true });
      tableWrap.addEventListener('wheel', (e) => {
        if (!e.deltaY) return;
        const isAtTop = tableWrap.scrollTop <= 0;
        const isAtBottom = Math.ceil(tableWrap.scrollTop + tableWrap.clientHeight) >= tableWrap.scrollHeight - 2;

        if ((isAtTop && e.deltaY < 0) || (isAtBottom && e.deltaY > 0)) {
          window.scrollBy({ top: e.deltaY, behavior: 'auto' });
        }
      }, { passive: true });
    }

    // Tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => {
          b.classList.remove('active');
          b.setAttribute('aria-selected', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');
        const prevTab = activeTab;
        activeTab = btn.getAttribute('data-tab');
        // A filter picked on one tab must not carry over: FREE left on while opening Secondary emptied its table.
        if (prevTab !== activeTab) resetActiveFilterToAll();

        if (activeTab === 'SMART' && !hasLoadedSmartDrops) {
          loadSmartDrops().then(() => renderTable());
        }
        if (activeTab === 'SECONDARY' && !hasLoadedSecondaryDrops) {
          loadSecondaryDrops().then(() => {
            updateSecondaryTabCount();
            updateSecondaryBannerUI();
            renderTable();
          });
        }

        const isPort = (activeTab === 'PORTFOLIO');
        const isSec = (activeTab === 'SECONDARY');
        const statsBanner = document.getElementById('statsBanner');
        const portBanner = document.getElementById('portfolioBanner');
        const secBanner = document.getElementById('secondaryMarketBanner');
        if (statsBanner) statsBanner.style.display = (isPort || isSec) ? 'none' : 'flex';
        if (portBanner) portBanner.style.display = isPort ? 'flex' : 'none';
        if (secBanner) secBanner.style.display = isSec ? 'flex' : 'none';

        if (isPort) {
          markDropsDataChanged();
          updatePortfolioBannerUI();
          if (connectedWallet && userOwnedContracts.size === 0 && !isScanningPortfolio) {
            scanUserPortfolio(connectedWallet);
          }
        } else if (isSec) {
          updateSecondaryBannerUI();
        }

        if (typeof window !== 'undefined' && window.va) {
          window.va('event', { name: 'tab_switch', data: { tab: activeTab } });
        }
        scheduleTableRender();
        syncMobileTabSelector();
      });
    });

    // In-modal Arrow Key Navigation (← Prev / Next →) & Global Tab Shortcuts
    document.addEventListener('keydown', (e) => {
      if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;

      const inspectModal = document.getElementById('inspectModal');
      const isInspectOpen = Boolean(inspectModal && (inspectModal.open || inspectModal.hasAttribute('open')));

      if (isInspectOpen) {
        if (e.key === 'ArrowLeft') {
          e.preventDefault();
          navigateInspectModal(-1);
          return;
        }
        if (e.key === 'ArrowRight') {
          e.preventDefault();
          navigateInspectModal(1);
          return;
        }
        // When inspection modal is open, suppress background tab switching
        return;
      }

      if (e.key === '1') {
        const b = document.getElementById('tabRadar');
        if (b) b.click();
      } else if (e.key === '2') {
        const b = document.getElementById('tabSecondary');
        if (b) b.click();
      } else if (e.key === '3') {
        const b = document.getElementById('tabWallets');
        if (b) b.click();
      } else if (e.key === '4') {
        const b = document.getElementById('tabPortfolio');
        if (b) b.click();
      }
    });

    // Filter pills
    document.querySelectorAll('.pill').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (isDraggingPill || justFinishedDrag) return;
        document.querySelectorAll('.pill').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        // Reset More Filters dropdown active state
        const mfTrigger = document.getElementById('moreFiltersTrigger');
        const mfLabel = document.getElementById('moreFiltersLabel');
        if (mfTrigger) mfTrigger.classList.remove('active');
        if (mfLabel) mfLabel.textContent = '⚙️ More Filters';
        document.querySelectorAll('#moreFiltersOptions .custom-option').forEach(o => o.classList.remove('selected'));

        activeFilter = btn.getAttribute('data-filter');
        saveFilterChoice(activeFilter);
        if (activeFilter === 'FOLLOWED' && !hasLoadedSmartDrops) {
          await loadSmartDrops();
        }
        if (typeof window !== 'undefined' && window.va) {
          window.va('event', { name: 'filter_changed', data: { filter: activeFilter } });
        }
        scheduleTableRender();
      });
    });

    // Search input & controls
    const searchInput = document.getElementById('searchInput');
    const searchClearBtn = document.getElementById('searchClearBtn');

    let searchDebounceTimer = null;
    if (searchInput) {
      // Auto-select text on click if field already contains search query for frictionless replacement
      searchInput.addEventListener('click', () => {
        if (searchInput.value && searchInput.selectionStart === searchInput.selectionEnd) {
          searchInput.select();
        }
      });

      searchInput.addEventListener('focus', () => {
        if (searchInput.value && searchInput.value.trim()) {
          renderSearchDropdown(searchInput.value);
        }
      });

      searchInput.addEventListener('input', e => {
        searchQuery = e.target.value;
        if (searchClearBtn) {
          searchClearBtn.style.display = searchQuery ? 'inline-flex' : 'none';
        }
        // The historical smart drops (a large JSON) are loaded in the background: render with what is loaded now,
        // then once more when they arrive, if a search is still active.
        const needsHistory = searchQuery.includes('.ai') || searchQuery.startsWith('0x') || searchQuery.length >= 3;
        const rerenderAfterLoad = (loaded) => {
          if (!loaded() || !searchQuery.trim() || searchRenderTimer) return; // a pending render will include them
          renderTable();
          if (isSearchDropdownOpen()) renderSearchDropdown(searchQuery);
        };
        if (!hasLoadedSmartDrops && needsHistory) {
          loadSmartDrops().then(() => rerenderAfterLoad(() => hasLoadedSmartDrops)).catch(() => {});
        }
        // established (secondary-market) tokens are searchable too: they are not part of the first feed page
        if (!hasLoadedSecondaryDrops && needsHistory) {
          loadSecondaryDrops().then(() => rerenderAfterLoad(() => hasLoadedSecondaryDrops)).catch(() => {});
        }
        cancelPendingSearchRender();
        searchRenderTimer = setTimeout(flushSearchRender, SEARCH_RENDER_DEBOUNCE_MS);

        // Track debounced search queries so we know what tokens users are looking for
        clearTimeout(searchDebounceTimer);
        const qClean = String(searchQuery || '').trim();
        if (qClean.length >= 2) {
          searchDebounceTimer = setTimeout(() => {
            // result_count / is_address make failed searches (esp. pasted contract addresses) measurable.
            let resultCount = null;
            try { resultCount = getFilteredDrops().length; } catch (_) { /* analytics must never throw */ }
            trackAnalytics('token_searched', {
              query: qClean,
              active_filter: activeFilter,
              active_tab: activeTab,
              result_count: resultCount,
              is_address: /^0x[0-9a-f]{4,}$/i.test(qClean)
            });
          }, 650);
        }
      });

      searchInput.addEventListener('keydown', e => {
        const dropdown = document.getElementById('searchResultsDropdown');
        let isDropdownOpen = dropdown && dropdown.style.display !== 'none';

        if (e.key === 'ArrowDown') {
          if (!isDropdownOpen && searchInput.value.trim()) {
            e.preventDefault();
            renderSearchDropdown(searchInput.value);
            return;
          }
          if (isDropdownOpen && searchDropdownResults && searchDropdownResults.length > 0) {
            e.preventDefault();
            const nextIdx = (searchDropdownSelectedIndex + 1) % searchDropdownResults.length;
            updateSearchDropdownSelection(nextIdx);
            return;
          }
        }

        if (e.key === 'ArrowUp') {
          if (isDropdownOpen && searchDropdownResults && searchDropdownResults.length > 0) {
            e.preventDefault();
            const prevIdx = searchDropdownSelectedIndex <= 0 ? searchDropdownResults.length - 1 : searchDropdownSelectedIndex - 1;
            updateSearchDropdownSelection(prevIdx);
            return;
          }
        }

        if (e.key === 'Enter') {
          if (searchRenderTimer) {
            // Keystrokes still waiting on the debounce: render now so Enter acts on the current query.
            flushSearchRender();
            isDropdownOpen = isSearchDropdownOpen();
          }
          if (isDropdownOpen && searchDropdownResults && searchDropdownResults.length > 0) {
            e.preventDefault();
            const chosen = searchDropdownSelectedIndex >= 0
              ? searchDropdownResults[searchDropdownSelectedIndex]
              : searchDropdownResults[0];
            selectSearchDropdownItem(chosen);
            return;
          }
        }

        if (e.key === 'Escape') {
          hideSearchDropdown();
          const hadPendingRender = Boolean(searchRenderTimer);
          cancelPendingSearchRender();
          if (searchInput.value) {
            clearTimeout(searchDebounceTimer);
            searchInput.value = '';
            searchQuery = '';
            if (searchClearBtn) searchClearBtn.style.display = 'none';
            renderTable();
          } else if (hadPendingRender) {
            renderTable(); // the field was emptied by typing and that render had not run yet
          }
          searchInput.blur();
        }
      });
    }

    if (searchClearBtn) {
      searchClearBtn.addEventListener('click', (e) => {
        if (e) {
          e.preventDefault();
          e.stopPropagation();
        }
        clearTimeout(searchDebounceTimer);
        cancelPendingSearchRender();
        hideSearchDropdown();
        if (searchInput) {
          searchInput.value = '';
          searchQuery = '';
          searchClearBtn.style.display = 'none';
          renderTable();
          searchInput.focus();
        }
      });
    }

    // Dismiss search dropdown on click outside
    document.addEventListener('click', e => {
      const searchWrap = document.querySelector('.suite-search-wrap');
      if (searchWrap && !searchWrap.contains(e.target)) {
        hideSearchDropdown();
      }
    });

    // Global '/' keyboard shortcut to focus prominent search bar
    window.addEventListener('keydown', e => {
      if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
        if (searchInput) {
          e.preventDefault();
          searchInput.focus();
          searchInput.select();
        }
      }
    });

    // Custom Cyberpunk Sort Dropdown
    setupCustomDropdown();
    setupMobileTabDropdown();
    setupMoreFiltersDropdown();
    syncMobileTabSelector();

    // Start from the user's last filter (or ?filter=free) instead of making them re-click it in every tab
    restoreSavedFilter();

    // Sort select fallback
    const sortSel = document.getElementById('sortSelect');
    if (sortSel) {
      sortSel.addEventListener('change', e => {
        setSortMode(e.target.value);
      });
    }

    // Close modal on backdrop click and sync URL hash
    const modal = document.getElementById('inspectModal');
    if (modal) {
      modal.addEventListener('click', e => {
        if (e.target === modal) modal.close();
      });
      modal.addEventListener('close', () => {
        finishModalTelemetry('modal_closed');
        clearModalHash();
        currentInspectItem = null;
      });
    }

    // Top Gainer Banner Click -> Hall of Fame Modal
    const gainerBanner = document.getElementById('topGainerBanner');
    if (gainerBanner) {
      gainerBanner.addEventListener('click', () => {
        openHallOfFameModal('ALL');
      });
      gainerBanner.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          openHallOfFameModal('ALL');
        }
      });
    }

    // Close Hall of Fame modal on backdrop click
    const hofModal = document.getElementById('hallOfFameModal');
    if (hofModal) {
      hofModal.addEventListener('click', e => {
        if (e.target === hofModal) hofModal.close ? hofModal.close() : hofModal.removeAttribute('open');
      });
    }

    // Smart Hunters Leaderboard Pill Click
    const smartPill = document.getElementById('pillSmartLeaderboard');
    if (smartPill) {
      smartPill.addEventListener('click', (e) => {
        if (isDraggingPill || justFinishedDrag) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }
        openSmartLeaderboardModal('ALL');
      });
    }

    // Close Smart Leaderboard modal on backdrop click
    const smModal = document.getElementById('smartLeaderboardModal');
    if (smModal) {
      smModal.addEventListener('click', e => {
        if (e.target === smModal) smModal.close ? smModal.close() : smModal.removeAttribute('open');
      });
    }

    // Close Reorder Pills modal on backdrop click
    const reorderModal = document.getElementById('reorderPillsModal');
    if (reorderModal) {
      reorderModal.addEventListener('click', e => {
        if (e.target === reorderModal) reorderModal.close ? reorderModal.close() : reorderModal.removeAttribute('open');
      });
    }

    // Connect Wallet Button
    const connectBtn = document.getElementById('connectWalletBtn');
    if (connectBtn) {
      connectBtn.addEventListener('click', openWalletModal);
    }

    // Portfolio Action Buttons
    const rescanBtn = document.getElementById('portRescanBtn');
    if (rescanBtn) {
      rescanBtn.addEventListener('click', () => {
        if (connectedWallet) scanUserPortfolio(connectedWallet, true);
        else openWalletModal();
      });
    }

    const syncMobileBtn = document.getElementById('portSyncMobileBtn');
    if (syncMobileBtn) {
      syncMobileBtn.addEventListener('click', openSyncModal);
    }

    // Close Wallet & Sync modals on backdrop click
    const walletModal = document.getElementById('walletModal');
    if (walletModal) {
      walletModal.addEventListener('click', e => {
        if (e.target === walletModal) walletModal.close ? walletModal.close() : walletModal.removeAttribute('open');
      });
      // Ends a wallet-dialog session; with no signing wallet connected it is counted as an abandon (wallet_modal_abandoned).
      if (typeof walletModal.addEventListener === 'function') walletModal.addEventListener('close', onWalletModalClosed);
    }

    const syncModal = document.getElementById('syncModal');
    if (syncModal) {
      syncModal.addEventListener('click', e => {
        if (e.target === syncModal) syncModal.close ? syncModal.close() : syncModal.removeAttribute('open');
      });
    }

    // Check URL parameters and hash permalinks (e.g. ?wallet=0x... or #token=PTAH)
    checkUrlParams();
    checkUrlHash();

    // Listen to URL hash changes for permalinks & browser back/forward navigation
    window.addEventListener('hashchange', handleHashChange);

    // Initialize 24h Daily Pulse Hero Card
    initDailyPulseHero();

    // Auto-reconnect previously authorized wallet
    tryAutoReconnect();

    // Initialize Recent Search & Inspected Chips and Sticky Header Scroll
    recentInspectedTokens = loadRecentInspected();
    renderRecentChips();
    setupStickyHeaderScroll();
    setupMobileStickySearch();

    // Cache-First: Eagerly hydrate overview statistics from local cache immediately, then sync fresh in background
    hydrateCachedOverviewStats();
    fetchOverviewStats().catch(() => {});

    // Start cloud feed fetch & periodic background sync (non-blocking so UI is instant)
    fetchDrops().catch(err => console.warn('Initial fetchDrops hitch:', err));
    setInterval(fetchDrops, FEED_REFRESH_INTERVAL_MS);

    // Poll latest block
    pollLatestBlock();
    startBlockPolling();

    refreshBnbPrice();
    setInterval(refreshBnbPrice, 60000);

    // Fetch the lazy features once the page is idle, so the first click on a row rarely waits for the modal module.
    prefetchFeatures();

  } catch (err) {
    console.error('Failed to load history.json:', err);
  }
}

if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', init);
}

if (typeof module !== 'undefined' && module.exports) {
  // Node tests require() this file, so its top-level names live in this module's scope, not in a shared global scope
  // as in the browser. Load the lazy features into this scope instead (bundled into one script by esbuild, then run by
  // a direct eval here, in strict mode as in the browser), so openModal draws synchronously, as the tests expect.
  // Skipped where there is no require (scripts/check_minified.js loads this file in a bare sandbox).
  if (typeof require === 'function' && typeof __dirname === 'string') {
    const loadFeatureIntoAppScope = (name) => {
      const built = require('esbuild').buildSync({
        entryPoints: [require('path').join(__dirname, 'features', name + '.js')],
        bundle: true,
        format: 'iife',
        globalName: '__feature',
        write: false,
        logLevel: 'silent',
      });
      return eval('"use strict";\n' + built.outputFiles[0].text + '\n;__feature'); // eslint-disable-line no-eval
    };
    modalFeature = loadFeatureIntoAppScope('modal');
  }
  module.exports = {
    readDropAlertPref,
    writeDropAlertPref,
    getDropAlertPermission,
    isDropAlertActive,
    onDropAlertToggleClick,
    showNewMarketToast,
    pollDeltaFeed,
    startDeltaPolling,
    getLatestKnownBlock: () => latestKnownBlock,
    setLatestKnownBlock: (b) => { latestKnownBlock = b; },
    dropMatchesToolbarFilter,
    alertMatchType,
    queueDropAlerts,
    flushDropAlerts,
    updateDropAlertToggle,
    setupDropAlertToggle,
    sendRpcCall,
    sendRpcBatch,
    fetchRegistryMintLogs,
    BSC_RPC_CONFIG,
    rpcReadTestHooks,
    buildTokenShareUrl,
    copyTokenShareLink,
    mergeTopGainersList,
    syncSeededTopGainers,
    topGainersSeedLabel,
    getTopGainersSource: () => topGainersSource,
    getPulse24hServer: () => pulse24hServer,
    setPulse24hServer: (p) => { pulse24hServer = p; },
    mcapLabel,
    marketFreshnessHtml,
    formatMarketAge,
    saveSavedTopGainers,
    tokenDecimalsOf,
    balanceFromRaw,
    formatCount,
    formatBnb,
    formatBnbAsUsd,
    bnbToUsd,
    hasUsableBnbPrice,
    bnbPriceNote,
    setBnbPriceState: (price, live, asOf = null) => { bnbPrice = price; bnbPriceLive = live; bnbPriceAsOf = asOf; },
    getBnbPriceState: () => ({ price: bnbPrice, live: bnbPriceLive, asOf: bnbPriceAsOf }),
    refreshBnbPrice,
    EXCLUDED_PEGS_SYMBOLS,
    EXCLUDED_PEGS_CONTRACTS,
    BOGUS_SCAM_CONTRACTS,
    SYNTHETIC_EQUITY_SYMBOLS,
    EXCLUDED_NAME_PHRASES,
    isExcludedMajorAsset,
    interestTier,
    interestBadgeHtml,
    pad32,
    buildPancakeSwapSellCalldata,
    evaluatePortfolioExit,
    getSniperEval,
    isSniperReady,
    evaluateNftSurge,
    updateProxyCount,
    updateLaunchpadCount,
    isLaunchpadDrop,
    formatCryptoPrice,
    computeItemProfit,
    applyMarketOverlay,
    computeStats,
    renderStats,
    openHallOfFameModal,
    openWalletModal,
    openBinanceQrModal,
    handleBinanceWalletClick,
    openSyncModal,
    connectWeb3,
    disconnectWallet,
    setWatchOnlyWallet,
    scanUserPortfolio,
    updateWalletButtonUI,
    updatePortfolioBannerUI,
    isSecondaryMarketAsset,
    getSanitizedMcap,
    updateSecondaryTabCount,
    updateSecondaryBannerUI,
    getFavoritesStorageKey,
    loadFavoritesForActiveWallet,
    saveFavorites,
    PANCAKE_ROUTER_V2,
    WBNB_ADDRESS,
    MULTICALL3_ADDRESS,
    encodeMulticall3Aggregate3,
    decodeMulticall3Aggregate3,
    loadMoreDrops,
    requestNotificationPermission,
    sendDesktopNotification,
    buildMintTxParams,
    executeWeb3Mint,
    mintClickProps,
    copyModalSniperCli,
    copyTextToClipboard,
    trackCliCopy,
    openMintWalletGate,
    onWalletModalClosed,
    walletModalBack,
    closeWalletDialog,
    beginWalletSession,
    getWalletSession: () => _walletSession,
    setConnectedWalletForTest: (addr, provider) => { connectedWallet = addr; walletProviderName = provider; },
    getInjectedProvider,
    openInspectModalByContract,
    renderSmartStreamTicker,
    isValidTopMarketItem,
    updateSavedTopMarkets,
    loadSavedTopGainers,
    getBaselineTopGainers,
    getSavedTopGainers: () => savedTopGainers,
    setSavedTopGainers: (arr) => { savedTopGainers = arr; },
    toggleFollowWallet,
    getBaselineSmartHunters,
    syncSeededSmartHunters,
    computeSmartWalletLeaderboard,
    openSmartLeaderboardModal,
    toggleDexChart,
    viewWalletActivity,
    loadSmartDrops,
    loadSecondaryDrops,
    mergeDropsIntoAllDrops,
    refreshFilterPillCounts,
    countDropsByFilter,
    updateAllFilterCounts,
    markDropsDataChanged,
    getDropsDataVersion: () => dropsDataVersion,
    flushSearchRender,
    cancelPendingSearchRender,
    updateFreeMintsCount,
    appendUniqueDrops,
    getFilteredDrops,
    renderSmartLeaderboardRows,
    onSmartSearchInput,
    loadMoreHunters,
    updateFollowedFilterCount,
    getFollowedWallets: () => followedWallets,
    setFollowedWallets: (s) => { followedWallets = s; },
    getSmartHuntersCache: () => smartHuntersCache,
    setSmartHuntersCache: (arr) => { smartHuntersCache = arr; },
    getAllDrops: () => allDrops,
    setAllDrops: (arr) => { allDrops = arr; },
    computeHolderDistribution,
    openModal,
    setActiveTab: (t) => { activeTab = t; },
    getActiveTab: () => activeTab,
    setActiveFilter: (f) => { activeFilter = f; },
    getActiveFilter: () => activeFilter,
    renderWalletsTabLeaderboard,
    setWalletsTabFilter,
    setWalletsTabSort,
    getWalletsTabSortMode: () => tabWalletsSortMode,
    onWalletsTabSearch,
    loadMoreTabWallets,
    updateWalletsTabBadge,
    DEFAULT_PILLS_SEQUENCE,
    PILL_SEQUENCE_STORAGE_KEY,
    getPillKey,
    getPillElementByKey,
    getCurrentPillSequence,
    savePillSequence,
    loadSavedPillSequence,
    applyPillSequence,
    resetPillSequence,
    openReorderModal,
    renderReorderModalList,
    movePill,
    movePillToTop,
    setupModalSwipeNav,
    setupFilterPillsDragAndDrop,
    showToast,
    copyDonationAddress,
    DONATION_WALLET,
    setSortMode,
    getSortMode: () => sortMode,
    renderTable,
    getDexScreenerUrl,
    compute24hPulseMetrics,
    updateDailyPulseUI,
    toggleDailyPulseHero,
    initDailyPulseHero,
    renderPulseRunnerRow,
    renderPulseSmartMintRow,
    renderPulseLaunchRow,
    renderPulseNftMintRow,
    pulseLaunchRow,
    pulseNftMintRow,
    setupPulseCarousel,
    scrollPulseCarousel,
    updateActivePulseDot,
    registerPulseItem,
    pulseItemsMap,
    updateDigitalAiCounter,
    applyAgentWalletsState,
    syncLiveAgentWalletsOnChain,
    getAgentWalletsAnchorBlock: () => agentWalletsAnchorBlock,
    setAgentWalletsAnchorBlock: (b) => { agentWalletsAnchorBlock = b; },
    getCurrentAgentWalletsCount: () => currentAgentWalletsCount,
    fetchOverviewStats,
    applyOverviewStats,
    applyOverviewStatsFallback,
    getGlobalStats: () => globalStats,
    setGlobalStats: (s) => { globalStats = s; },
    navigateInspectModal,
    getInspectNavContext,
    getCurrentInspectItem: () => currentInspectItem,
    setCurrentInspectItem: (item) => { currentInspectItem = item; },
    isMobileClient,
    parseTokenFromHash,
    findTokenByQuery,
    setModalHash,
    clearModalHash,
    checkUrlHash,
    handleHashChange,
    handleTokenClick,
    resolveTokenAndInspect,
    hydrateCachedOverviewStats,
    OVERVIEW_STATS_CACHE_KEY,
    RECENT_INSPECTED_STORAGE_KEY,
    loadRecentInspected,
    saveRecentInspected,
    addRecentInspected,
    clearRecentChips,
    renderRecentChips,
    handleRecentChipClick,
    setSearchQuery,
    getRecentInspectedTokens: () => recentInspectedTokens,
    setRecentInspectedTokens: (t) => { recentInspectedTokens = t; },
    setupStickyHeaderScroll,
    getMatchingDrops,
    renderSearchDropdown,
    hideSearchDropdown,
    selectSearchDropdownItem,
    highlightSearchMatch,
    isFreeMintDrop,
    isDropSoldOut,
    isDropUnsafeToMint,
    isFreeMintableNow,
    parseServerUtc,
    getMintTerms,
    mintTermsChipsHtml,
    mintTimingLabel,
    smartWalletCount,
    canMintFromCard,
    cardTermsCellHtml,
    gateBadgeHtml
  };
}
