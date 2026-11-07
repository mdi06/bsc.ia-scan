// BNB Chain .ai Agent ID Suite - Client Web Application

const DONATION_WALLET = '0xa15BB4AEdC1b3426159C3cACf8548Cf09506A20a';
const PANCAKE_ROUTER_V2 = '0x10ED43C718714eb63d5aA57B78B54704E256024E';
const WBNB_ADDRESS = '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c';

let allDrops = [];
let activeTab = 'RADAR';
let activeFilter = 'ALL';
let sortMode = 'NEWEST';
let searchQuery = '';
let bnbPrice = 705.0;

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
  : 'https://bsc-ia-scan.onrender.com';

async function syncFavoritesFromCloud(wallet) {
  if (!wallet) return;
  try {
    let res = null;
    try {
      res = await fetch(`${BACKEND_URL}/api/favorites?wallet=${wallet.toLowerCase()}&_t=${Date.now()}`);
      if (!res.ok) throw new Error('Render fetch failed');
    } catch (e) {
      try {
        res = await fetch(`https://agent-id-monitor.fly.dev/api/favorites?wallet=${wallet.toLowerCase()}&_t=${Date.now()}`);
        if (!res.ok) throw new Error('Fly fetch failed');
      } catch (e2) {
        res = await fetch(`/api/favorites?wallet=${wallet.toLowerCase()}&_t=${Date.now()}`);
      }
    }
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
    let ok = false;
    try {
      const res = await fetch(`${BACKEND_URL}/api/favorites`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (res.ok) ok = true;
    } catch (e) {}

    if (!ok) {
      try {
        const resFly = await fetch('https://agent-id-monitor.fly.dev/api/favorites', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (resFly.ok) ok = true;
      } catch (e) {}
    }

    if (!ok) {
      const resLocal = await fetch('/api/favorites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (resLocal.ok) ok = true;
    }
    if (ok) cloudSyncStatus = 'synced';
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

// Native Browser / Desktop Push Notification System
function requestNotificationPermission() {
  if (typeof window !== 'undefined' && 'Notification' in window) {
    if (Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {});
    }
  }
}
if (typeof window !== 'undefined') window.requestNotificationPermission = requestNotificationPermission;

function sendDesktopNotification(title, body) {
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
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = 'market-toast';
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

  sendDesktopNotification(
    '🚨 New .ai Agent ID Drop!',
    `${item.name || 'New Contract'} (${item.symbol || ''}) at Block ${blkStr}`
  );

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
  const container = document.getElementById('toastContainer');
  if (!container) return;

  // Clean duplicate leading emoji from title if icon is present
  const cleanTitle = (icon && title)
    ? title.replace(/^[\p{Extended_Pictographic}\uFE0F\s]+/u, '').trim()
    : title;

  const toast = document.createElement('div');
  toast.className = 'market-toast';
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

// Format numbers nicely (e.g. 1.2K, 500)
function formatCount(val) {
  const n = Number(val || 0);
  if (!isFinite(n) || n >= 1e20) return '∞';
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return n.toLocaleString();
}

// Universal Crypto Price & Net Profit Formatter (Supports Sub-Cent & Micro-Cap Tokens)
function formatCryptoPrice(val, isNet = false) {
  const n = Number(val || 0);
  if (!isFinite(n) || n === 0) return isNet ? '+$0.00' : '$0.00';
  const sign = n < 0 ? '-' : (isNet ? '+' : '');
  const absN = Math.abs(n);
  if (absN >= 1.0) return `${sign}$${absN.toFixed(2)}`;
  if (absN >= 0.01) return `${sign}$${absN.toFixed(4)}`;
  if (absN >= 0.0001) return `${sign}$${absN.toFixed(6)}`;
  if (absN >= 1e-6) return `${sign}$${absN.toFixed(8)}`;
  // Trim redundant trailing zeros for clean readability
  const rawStr = absN.toFixed(10);
  const cleanStr = rawStr.replace(/0+$/, '');
  return `${sign}$${cleanStr}`;
}

// Known major pegged / Layer-1 tokens and synthetic equities on BSC
const EXCLUDED_PEGS_SYMBOLS = new Set([
  'BTCB', 'WBTC', 'BTC', 'ETH', 'WETH', 'WBNB', 'BNB', 'USDT', 'USDC', 'BUSD', 
  'DAI', 'TUSD', 'FDUSD', 'CAKE', 'U', 'OSK', 'FIST'
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

const SYNTHETIC_EQUITY_SYMBOLS = new Set([
  'TSLAB', 'AAPLB', 'MSFTB', 'METAB', 'SPCXB', 'NVDA', 'NVDAB', 'AMZNB', 'GOOGLB', 'MRNAB', 'SOXSB', 'CRCLB', 'QQQB', 'MSTRB'
]);

// Compute Individual Project Profit and Maintain ATH High-Water Mark
function computeItemProfit(item, bnbPriceUsd = bnbPrice) {
  const c = String(item.contract || '').toLowerCase();
  const sym = String(item.symbol || '').toUpperCase();
  const isPegged = EXCLUDED_PEGS_CONTRACTS.has(c) || EXCLUDED_PEGS_SYMBOLS.has(sym) || SYNTHETIC_EQUITY_SYMBOLS.has(sym);
  if (isPegged) {
    return { secUsd: 0, mintUsd: 0, profit: 0, athProfit: 0, athPrice: 0, multiplier: 0, isSold: false, isFree: false };
  }

  const flUsd = Number(item.floor_price_usd || 0);
  const flBnb = Number(item.floor_price_bnb || 0);
  const tkUsd = Number(item.token_price_usd || item.market_price_usd || 0);
  const secUsd = flUsd > 0 ? flUsd : (flBnb > 0 ? flBnb * bnbPriceUsd : tkUsd);

  // Micro-pool / scam price distortion guard: reject astronomical prices (> $1000) or micro-wei supplies
  const tmVal = Number(item.total_minted || 0);
  const lpVal = Number(item.lp_usd || 0);
  if (secUsd > 1000.0 || (tmVal > 0 && tmVal < 1e-6) || (lpVal > 0 && lpVal < 5.0 && secUsd > 5.0)) {
    return { secUsd: 0, mintUsd: 0, profit: 0, athProfit: 0, athPrice: 0, multiplier: 0, isSold: false, isFree: false };
  }

  const pStr = String(item.mint_price_str || '').toUpperCase();
  const vWei = Number(item.mint_value_wei || 0);
  const hasFreeFlag = Boolean(item.has_free_mint);
  const isFree = Boolean(hasFreeFlag || pStr.includes('FREE') || (vWei === 0 && Number(item.mint_price_bnb || 0) === 0 && !pStr.includes('PAID') && !pStr.includes('DEX') && !pStr.includes('TBD')));
  
  // If mint price is unknown (TBD) and not verified free or with calldata, cannot claim 0 mint cost
  if (pStr.includes('TBD') && !hasFreeFlag && !item.mint_calldata) {
    return { secUsd: 0, mintUsd: 0, profit: 0, athProfit: 0, athPrice: 0, multiplier: 0, isSold: false, isFree: false };
  }

  const mintUsd = isFree ? 0 : (vWei > 0 ? (vWei / 1e18) * bnbPriceUsd : Number(item.mint_price_bnb || 0) * bnbPriceUsd);
  const profit = secUsd > 0 ? (secUsd - mintUsd) : 0;

  let rawMs = item.max_supply;
  if (rawMs !== null && rawMs !== undefined && (Number(rawMs) >= 1e20 || !isFinite(Number(rawMs)))) {
    rawMs = null;
  }
  const ms = Number(rawMs || 0);
  const tm = Number(item.total_minted || 0);
  const isSold = Boolean(ms > 0 && tm >= ms);

  const athPrice = Math.max(Number(item.ath_price_usd || 0), secUsd);
  const athProfit = Math.max(Number(item.ath_profit_usd || 0), profit);
  let multiplier = Number(item.ath_multiplier || 0);
  if (!multiplier && mintUsd > 0 && athPrice > 0) {
    multiplier = Number((athPrice / mintUsd).toFixed(2));
  }

  // Update item in place
  if (secUsd > Number(item.ath_price_usd || 0) && secUsd > 0) {
    item.ath_price_usd = athPrice;
    item.ath_profit_usd = athProfit;
    item.ath_multiplier = multiplier || null;
  } else if (!item.ath_price_usd && secUsd > 0) {
    item.ath_price_usd = athPrice;
    item.ath_profit_usd = athProfit;
    item.ath_multiplier = multiplier || null;
  }
  item.current_profit_usd = profit;
  item.mint_cost_usd = mintUsd;
  item.secondary_price_usd = secUsd;

  return {
    secUsd,
    mintUsd,
    profit,
    athPrice,
    athProfit,
    multiplier,
    isSold,
    isFree
  };
}

// Persistent Memory for Top Post-Mint Gainers & Hall of Fame Markets
// Eliminates full 2k+ project scans; incrementally updates and saves top winners from incoming 150-drop batches.
const SAVED_TOP_MARKETS_KEY = 'agent_id_saved_top_markets_v2';
let savedTopGainers = [];

function getBaselineTopGainers() {
  return [
    {
      name: "FishingAi Rod",
      symbol: "ROD",
      contract: "0xbdb206b823e2782dd75fff0ef89795146c29ed76",
      standard: "ERC-721 (NFT Collection)",
      secUsd: 6.75,
      mintUsd: 0.0,
      profit: 6.75,
      athPrice: 6.75,
      athProfit: 6.75,
      multiplier: 0,
      isSold: false,
      isFree: true,
      holders: 1524,
      item: {
        name: "FishingAi Rod",
        symbol: "ROD",
        contract: "0xbdb206b823e2782dd75fff0ef89795146c29ed76",
        standard: "ERC-721 (NFT Collection)",
        market_url: "https://element.market/collections/fishingai-rod",
        token_price_usd: 6.75,
        ath_price_usd: 6.75,
        holders: 1524
      }
    },
    {
      name: "MLEE DAO",
      symbol: "MDAO",
      contract: "0xc4bf2518aa953304170753388f83277eb9588a7a",
      standard: "BEP-20 Token (18 decimals)",
      secUsd: 0.1070,
      mintUsd: 0.0,
      profit: 0.1070,
      athPrice: 0.1070,
      athProfit: 0.1070,
      multiplier: 0,
      isSold: false,
      isFree: true,
      holders: 8400,
      item: {
        name: "MLEE DAO",
        symbol: "MDAO",
        contract: "0xc4bf2518aa953304170753388f83277eb9588a7a",
        standard: "BEP-20 Token (18 decimals)",
        pair_url: "https://pancakeswap.finance/swap?outputCurrency=0xc4bf2518aa953304170753388f83277eb9588a7a",
        token_price_usd: 0.1070,
        ath_price_usd: 0.1070,
        holders: 8400
      }
    },
    {
      name: "AGENT人生8",
      symbol: "AGENT人生8",
      contract: "0xca45f13730154fbbf2d93acf169de83f52864f80",
      standard: "BEP-20 Token (18 decimals)",
      secUsd: 0.04794,
      mintUsd: 0.0,
      profit: 0.04794,
      athPrice: 0.04794,
      athProfit: 0.04794,
      multiplier: 0,
      isSold: true,
      isFree: true,
      holders: 888,
      item: {
        name: "AGENT人生8",
        symbol: "AGENT人生8",
        contract: "0xca45f13730154fbbf2d93acf169de83f52864f80",
        standard: "BEP-20 Token (18 decimals)",
        pair_url: "https://pancakeswap.finance/swap?outputCurrency=0xca45f13730154fbbf2d93acf169de83f52864f80",
        token_price_usd: 0.04794,
        ath_price_usd: 0.04794,
        max_supply: 88888888,
        total_minted: 88888888,
        holders: 888,
        is_sold: true
      }
    },
    {
      name: "The Donkey",
      symbol: "驴来",
      contract: "0xa856c666595f556f647f94d741aae389baf97777",
      standard: "BEP-20 Token (18 decimals)",
      secUsd: 0.00004160,
      mintUsd: 0.0,
      profit: 0.00004160,
      athPrice: 0.00004160,
      athProfit: 0.00004160,
      multiplier: 0,
      isSold: true,
      isFree: true,
      holders: 550,
      item: {
        name: "The Donkey",
        symbol: "驴来",
        contract: "0xa856c666595f556f647f94d741aae389baf97777",
        standard: "BEP-20 Token (18 decimals)",
        pair_url: "https://pancakeswap.finance/swap?outputCurrency=0xa856c666595f556f647f94d741aae389baf97777",
        token_price_usd: 0.00004160,
        ath_price_usd: 0.00004160,
        max_supply: 210000000000,
        total_minted: 210000000000,
        holders: 550,
        is_sold: true
      }
    },
    {
      name: "4Broker Bibi",
      symbol: "BIBI",
      contract: "0xd6e8fd077adbd6d7a7cc24bf981c5a969bbd124e",
      standard: "BEP-20 Token (18 decimals)",
      secUsd: 0.00000379,
      mintUsd: 0.0,
      profit: 0.00000379,
      athPrice: 0.00000379,
      athProfit: 0.00000379,
      multiplier: 0,
      isSold: false,
      isFree: true,
      holders: 1100,
      item: {
        name: "4Broker Bibi",
        symbol: "BIBI",
        contract: "0xd6e8fd077adbd6d7a7cc24bf981c5a969bbd124e",
        standard: "BEP-20 Token (18 decimals)",
        pair_url: "https://pancakeswap.finance/swap?outputCurrency=0xd6e8fd077adbd6d7a7cc24bf981c5a969bbd124e",
        token_price_usd: 0.00000379,
        ath_price_usd: 0.00000379,
        holders: 1100
      }
    },
    {
      name: "币安ID",
      symbol: "币安ID",
      contract: "0x90f6b49ebdaee09b67ffc71e2c9efca1b2be4822",
      standard: "BEP-20 Token (18 decimals)",
      secUsd: 0.00000353,
      mintUsd: 0.0,
      profit: 0.00000353,
      athPrice: 0.000005042,
      athProfit: 0.000005042,
      multiplier: 0,
      isSold: true,
      isFree: true,
      holders: 4820,
      item: {
        name: "币安ID",
        symbol: "币安ID",
        contract: "0x90f6b49ebdaee09b67ffc71e2c9efca1b2be4822",
        standard: "BEP-20 Token (18 decimals)",
        pair_url: "https://pancakeswap.finance/swap?outputCurrency=0x90f6b49ebdaee09b67ffc71e2c9efca1b2be4822",
        token_price_usd: 0.00000353,
        ath_price_usd: 0.000005042,
        max_supply: 10000000000,
        total_minted: 10000000000,
        holders: 4820,
        is_sold: true
      }
    }
  ];
}

function isValidTopMarketItem(item) {
  if (!item) return false;
  const c = String(item.contract || '').toLowerCase();
  const sym = String(item.symbol || '').toUpperCase();
  if (EXCLUDED_PEGS_CONTRACTS.has(c) || EXCLUDED_PEGS_SYMBOLS.has(sym) || SYNTHETIC_EQUITY_SYMBOLS.has(sym)) {
    return false;
  }
  const pStr = String(item.mint_price_str || (item.item && item.item.mint_price_str) || '').toUpperCase();
  if (pStr.includes('DEX TRADED')) return false;

  const hasFree = Boolean(item.has_free_mint || (item.item && item.item.has_free_mint));
  const hasCalldata = Boolean(item.mint_calldata || (item.item && item.item.mint_calldata));
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

  // Fallback to baseline historical winners if storage is fresh/empty
  savedTopGainers = getBaselineTopGainers().filter(g => isValidTopMarketItem(g.item || g));
  saveSavedTopGainers();
}

function saveSavedTopGainers() {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(SAVED_TOP_MARKETS_KEY, JSON.stringify(savedTopGainers));
    }
  } catch (e) {}
}

async function syncSeededTopGainers() {
  try {
    const res = await fetchWithTimeout(`seeded_top_gainers.json`, { cache: 'default' }, 4000);
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data) && data.length > 0) {
        mergeTopGainersList(data);
      }
    }
  } catch (e) {
    try {
      const resApi = await fetchWithTimeout(`${BACKEND_URL}/api/top-gainers`, { cache: 'default' }, 4000);
      if (resApi.ok) {
        const dataApi = await resApi.json();
        if (dataApi && Array.isArray(dataApi.gainers) && dataApi.gainers.length > 0) {
          mergeTopGainersList(dataApi.gainers);
        }
      }
    } catch (e2) {}
  }
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
        mintUsd: Number(item.mintUsd || 0),
        profit: Number(item.profit || 0),
        athPrice: peakAth,
        athProfit: peakProfit,
        multiplier: Number(item.multiplier || 0),
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
          existing.holders = Number(item.holders || item.total_minted || existing.holders || 0);
          existing.item = { ...existing.item, ...item };
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
          holders: Number(item.holders || item.total_minted || 0)
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

function mergeDropsIntoAllDrops(newDrops) {
  if (!Array.isArray(newDrops) || newDrops.length === 0) return 0;
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
  }
  return added;
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
      const drops = await res.json();
      if (Array.isArray(drops) && drops.length > 0) {
        mergeDropsIntoAllDrops(drops);
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
        if (dataApi && Array.isArray(dataApi.hunters) && dataApi.hunters.length > 0) {
          smartHuntersCache = dataApi.hunters;
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
let tabWalletsSearchKeyword = '';
let tabWalletsDisplayLimit = 50;
let cachedHunters = null;
let cachedHuntersAllDropsLen = 0;

function updateWalletsTabBadge() {
  if (typeof document === 'undefined') return;
  const countEl = document.getElementById('walletsTabCount');
  if (!cachedHunters || cachedHuntersAllDropsLen !== allDrops.length) {
    cachedHunters = computeSmartWalletLeaderboard(allDrops);
    cachedHuntersAllDropsLen = allDrops.length;
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

function setWalletsTabFilter(filter) {
  currentTabWalletFilter = filter;
  tabWalletsDisplayLimit = 50;
  if (typeof document !== 'undefined') {
    document.querySelectorAll('.wallets-tab-filters .filter-pill').forEach(btn => {
      btn.classList.remove('active');
    });
    const idMap = {
      'ALL': 'tabWalletFilterAll',
      'HIGH_WIN': 'tabWalletFilterHighWin',
      'FOLLOWED': 'tabWalletFilterFollowed'
    };
    const activeBtn = document.getElementById(idMap[filter]);
    if (activeBtn) activeBtn.classList.add('active');
  }
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

// Compute Macro Stats
function computeStats(drops) {
  let cappedTotal = 0;
  let soldOutCount = 0;
  let nearCapCount = 0;
  let warmCapCount = 0;

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
      }
    }
  });

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
    topGainer,
    runnerUp,
    allGainers,
    soldGainers,
    majorGainers
  };
}

// Update DOM Header with Macro Stats
function renderStats(drops) {
  const stats = computeStats(drops);

  document.getElementById('fillRatePct').textContent = `${stats.fillRatePct.toFixed(1)}%`;
  document.getElementById('fillRateRatio').textContent = `(${stats.soldOutCount} / ${stats.cappedTotal} Sold Out)`;
  document.getElementById('nearCapCount').textContent = stats.nearCapCount;
  document.getElementById('momentumCount').textContent = `${stats.warmCapCount} ACTIVE`;

  if (stats.topGainer) {
    const tg = stats.topGainer;
    const tgMint = tg.isFree || tg.mintUsd === 0 ? 'FREE' : formatCryptoPrice(tg.mintUsd);
    const tgAth = Math.max(tg.athPrice, tg.secUsd);
    const tgNet = Math.max(tg.athProfit, tg.profit);
    document.getElementById('topGainerName').textContent = `${tg.name} (${tg.symbol})`;
    document.getElementById('topGainerDetails').textContent = `Mint: ${tgMint} ➔ ATH: ${formatCryptoPrice(tgAth)}`;
    document.getElementById('topGainerNet').textContent = `Net: ${formatCryptoPrice(tgNet, true)} / token`;
  }

  if (stats.runnerUp) {
    const ru = stats.runnerUp;
    const ruAth = Math.max(ru.athPrice, ru.secUsd);
    document.getElementById('runnerUpDetails').textContent = `│ 🥈 Runner-Up: ${ru.name} (${ru.symbol}) [${formatCryptoPrice(ruAth)}]`;
  }

  renderSmartStreamTicker(drops);
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

// Open inspect modal by contract address (interactive ticker click handler)
function openInspectModalByContract(contractAddress) {
  if (!contractAddress) return;
  let list = [];
  if (typeof allDrops !== 'undefined' && Array.isArray(allDrops) && allDrops.length > 0) {
    list = allDrops;
  } else if (typeof window !== 'undefined' && Array.isArray(window.allDrops) && window.allDrops.length > 0) {
    list = window.allDrops;
  } else if (typeof global !== 'undefined' && Array.isArray(global.allDrops) && global.allDrops.length > 0) {
    list = global.allDrops;
  }
  const target = list.find(d => (d.contract || '').toLowerCase() === String(contractAddress).toLowerCase());
  const modalFn = typeof openModal === 'function' ? openModal : (typeof global !== 'undefined' && typeof global.openModal === 'function' ? global.openModal : null);
  if (target && modalFn) {
    modalFn(target);
  }
}

// Render Real-Time Smart Money Stream Ticker Tape
function renderSmartStreamTicker(drops) {
  if (typeof document === 'undefined') return;
  const trackEl = document.getElementById('tickerTrack');
  if (!trackEl || !Array.isArray(drops) || drops.length === 0) return;

  const formatTickerTime = (detected_at) => {
    if (!detected_at) return 'LIVE';
    const str = String(detected_at).trim();
    if (str.includes(' ')) {
      const parts = str.split(' ');
      if (parts[1]) return parts[1].slice(0, 8);
    }
    if (str.includes('T')) {
      const parts = str.split('T');
      if (parts[1]) return parts[1].slice(0, 8);
    }
    return str.slice(-8);
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
        text: `🔥 <strong>${smartWallets.length} Smart Wallets</strong> entered <strong>$${symbol}</strong> simultaneously`,
        contract
      });
    } else if (smartWallets.length === 1) {
      const w = smartWallets[0];
      const wLabel = w.length > 18 ? `${w.slice(0, 6)}...${w.slice(-4)}` : w;
      const action = tm >= 1 ? `minted ${formatCount(tm)} ` : 'entered ';
      events.push({
        block: blockNum,
        time,
        tagClass: 'tag-smart',
        tagText: '🧠 SMART MONEY',
        text: `🧠 Smart Wallet <strong>${escapeHtml(wLabel)}</strong> ${action}<strong>$${symbol}</strong>`,
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
  const held = userHeld || Number(item.user_balance || 0);

  const prevF = prevStats ? Number(prevStats.sales_count || 0) : Number(item.prev_sales_count || 0);

  let isHot = false;
  if (held > 0) {
    isHot = (salesCnt >= 3 && volBnb > 0) || (prevF > 0 && salesCnt >= 2 * prevF);
  } else {
    isHot = (salesCnt >= 5) || (volBnb >= 0.5) || (prevF > 0 && salesCnt >= 5 && salesCnt >= 2 * prevF);
  }

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

function getSanitizedMcap(d) {
  if (!d) return 0;
  let mc = Number(d.market_cap || d.fdv || 0);
  if (mc === 0) {
    const px = Number(d.token_price_usd || d.market_price_usd || 0);
    const tm = Number(d.total_minted || 0);
    const ms = Number(d.max_supply || 0);
    if (px > 0) {
      if (tm > 0 && tm < 1e14) mc = tm * px;
      else if (ms > 0 && ms < 1e14) mc = ms * px;
      else if (tm >= 1e14) mc = (tm / 1e18) * px;
      else if (ms >= 1e14) mc = (ms / 1e18) * px;
    }
  }
  if (mc > 100000000 && Number(d.lp_usd || 0) < 50000) return 0;
  return mc;
}

// Secondary Market asset detector
function isSecondaryMarketAsset(d) {
  if (!d) return false;
  const c = String(d.contract || '').toLowerCase();
  const sym = String(d.symbol || '').toUpperCase();
  if (EXCLUDED_PEGS_CONTRACTS.has(c) || EXCLUDED_PEGS_SYMBOLS.has(sym) || SYNTHETIC_EQUITY_SYMBOLS.has(sym)) {
    return false;
  }
  const lp = Number(d.lp_usd || 0);
  const mc = getSanitizedMcap(d);
  const px = Number(d.token_price_usd || 0);
  const vol = Number(d.volume_24h || 0);
  const secVol = Number(d.secondary_vol_bnb || 0);
  const hasPair = Boolean(d.pair_address);
  const isGrad = Boolean(d.is_graduated);
  return lp > 50 || mc > 500 || (px > 0 && vol > 0) || secVol > 0 || hasPair || isGrad;
}

let hasLoadedSecondaryDrops = false;
let isLoadingSecondaryDrops = false;

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
    let res;
    try {
      res = await fetchWithTimeout(`${BACKEND_URL}/api/feed?filter=secondary&limit=300`, { cache: 'default' }, 8000);
      if (!res.ok) throw new Error('Backend filter=secondary failed');
    } catch (e1) {
      try {
        res = await fetchWithTimeout(`https://bsc-ia-scan.onrender.com/api/feed?filter=secondary&limit=300`, { cache: 'default' }, 8000);
      } catch (e2) {
        return;
      }
    }

    if (res && res.ok) {
      const payload = await res.json();
      const drops = Array.isArray(payload) ? payload : (payload.projects || []);
      if (Array.isArray(drops) && drops.length > 0) {
        mergeDropsIntoAllDrops(drops);
        hasLoadedSecondaryDrops = true;
        updateSecondaryTabCount();
        updateSecondaryBannerUI();
        updateDailyPulseUI();
        if (activeTab === 'SECONDARY') {
          renderTable();
        }
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
  const localCnt = allDrops.filter(d => isSecondaryMarketAsset(d)).length;
  const count = (globalStats && globalStats.secondary) ? Math.max(localCnt, globalStats.secondary) : localCnt;
  const tabEl = document.getElementById('secondaryTabCount');
  if (tabEl) tabEl.textContent = count;
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
function compute24hPulseMetrics(drops = allDrops, topGainersList = savedTopGainers, currentBlock = latestKnownBlock) {
  const list = Array.isArray(drops) ? drops : [];
  const topList = Array.isArray(topGainersList) ? topGainersList : [];

  // Candidates for Top 24h Runner
  const pool = [...list, ...topList.map(g => (g && g.item) ? g.item : g)].filter(Boolean);
  const seenContracts = new Set();
  const uniquePool = [];
  pool.forEach(item => {
    const c = String(item.contract || '').toLowerCase();
    if (c && !seenContracts.has(c)) {
      seenContracts.add(c);
      uniquePool.push(item);
    }
  });

  // Top 24h Runner: prioritize highest positive price change with active volume, or highest market cap
  let topRunner = null;
  let topRunnerScore = -Infinity;

  uniquePool.forEach(d => {
    if (d.contract === '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee') return;
    const chg = Number(d.price_change_24h || 0);
    const mcap = getSanitizedMcap(d);
    const vol = Number(d.volume_24h || 0);
    const tkUsd = Number(d.token_price_usd || d.market_price_usd || 0);
    if (tkUsd <= 0 && mcap <= 0) return;

    let score = mcap;
    if (chg > 0) score += chg * 50000;
    if (vol > 0) score += vol * 2;

    if (score > topRunnerScore) {
      topRunnerScore = score;
      topRunner = d;
    }
  });

  if (!topRunner && uniquePool.length > 0) {
    topRunner = uniquePool.find(d => isSecondaryMarketAsset(d)) || uniquePool[0];
  }

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

  list.forEach(d => {
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

  // Dynamic 1-sentence macro synthesis
  let macroHeadline = 'Analyzing 24h rolling activity across fair-mints, secondary trading pairs, and smart hunter capital...';
  if (topRunner) {
    const rName = topRunner.name || topRunner.symbol || 'Leading asset';
    const rMcap = getSanitizedMcap(topRunner);
    const rChg = Number(topRunner.price_change_24h || 0);
    const chgStr = rChg !== 0 ? ` (${rChg >= 0 ? '+' : ''}${rChg.toFixed(1)}%)` : '';
    const volStr = totalDexVol > 0 ? `$${formatCount(totalDexVol)}` : '$0';
    macroHeadline = `${rName} anchors 24h market valuation at $${formatCount(rMcap)}${chgStr} • ${gradCount} tokens trading on PancakeSwap DEX (${volStr} 24h vol) • ${totalSmartInflows} smart hunter inflows active.`;
  }

  return {
    topRunner,
    gradCount,
    totalDexVol,
    launchpadCount,
    totalSmartInflows,
    topHunter,
    topHunterCount,
    clustersCount,
    cleanMintsCount,
    refundsCount,
    threatsBlockedCount,
    macroHeadline
  };
}

function updateDailyPulseUI() {
  if (typeof document === 'undefined') return;
  const hero = document.getElementById('dailyPulseHero');
  if (!hero) return;

  const metrics = compute24hPulseMetrics();

  // 1. Top 24h Runner Card
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
      rPriceEl.textContent = tkPrice > 0 ? formatCryptoPrice(tkPrice) : '$0.00';
    }

    const mcap = getSanitizedMcap(runner);
    if (rMcapEl) {
      rMcapEl.textContent = mcap > 0 ? `$${formatCount(mcap)}` : '$0';
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

  // 2. Graduations & Volume
  const gradCountEl = document.getElementById('pulseGradCount');
  const volEl = document.getElementById('pulse24hVol');
  const launchEl = document.getElementById('pulseLaunchpadCount');
  if (gradCountEl) gradCountEl.textContent = metrics.gradCount;
  if (volEl) volEl.textContent = `$${formatCount(metrics.totalDexVol)}`;
  if (launchEl) launchEl.textContent = metrics.launchpadCount;

  // 3. Smart Money Alpha Inflow
  const smartInflowsEl = document.getElementById('pulseSmartInflows');
  const topHunterEl = document.getElementById('pulseTopHunter');
  const clustersEl = document.getElementById('pulseClustersCount');
  if (smartInflowsEl) smartInflowsEl.textContent = metrics.totalSmartInflows;
  if (topHunterEl) {
    const hunterDisplay = metrics.topHunter.length > 14 ? `${metrics.topHunter.slice(0, 6)}...${metrics.topHunter.slice(-4)}` : metrics.topHunter;
    topHunterEl.textContent = hunterDisplay;
    topHunterEl.title = metrics.topHunter;
  }
  if (clustersEl) clustersEl.textContent = metrics.clustersCount;

  // 4. Security & Anti-Rug
  const cleanMintsEl = document.getElementById('pulseCleanMints');
  const refundEl = document.getElementById('pulseRefundCount');
  const threatsEl = document.getElementById('pulseThreatsBlocked');
  if (cleanMintsEl) cleanMintsEl.textContent = metrics.cleanMintsCount;
  if (refundEl) refundEl.textContent = metrics.refundsCount;
  if (threatsEl) threatsEl.textContent = metrics.threatsBlockedCount;

  // 5. Headline Synthesis
  const headlineEl = document.getElementById('pulseMacroHeadline');
  if (headlineEl) headlineEl.textContent = metrics.macroHeadline;

  // 6. Collapsed Ticker Bar
  const tickerRunner = document.getElementById('tickerTopRunner');
  const tickerVol = document.getElementById('tickerVolume');
  const tickerGrad = document.getElementById('tickerGraduated');
  const tickerInflow = document.getElementById('tickerSmartInflow');

  if (tickerRunner && runner) {
    const chg = Number(runner.price_change_24h || 0);
    const mcap = getSanitizedMcap(runner);
    tickerRunner.textContent = `🏆 Top: ${runner.name || runner.symbol} $${formatCount(mcap)} (${chg >= 0 ? '+' : ''}${chg.toFixed(1)}%)`;
  }
  if (tickerVol) tickerVol.textContent = `🌊 DEX Vol: $${formatCount(metrics.totalDexVol)}`;
  if (tickerGrad) tickerGrad.textContent = `🚀 ${metrics.gradCount} Graduated`;
  if (tickerInflow) tickerInflow.textContent = `🧠 ${metrics.totalSmartInflows} Smart Inflows`;
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
}

if (typeof window !== 'undefined') {
  window.compute24hPulseMetrics = compute24hPulseMetrics;
  window.updateDailyPulseUI = updateDailyPulseUI;
  window.toggleDailyPulseHero = toggleDailyPulseHero;
  window.initDailyPulseHero = initDailyPulseHero;
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
function evaluatePortfolioExit(item, bnbPriceUsd = 705.0) {
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
        recommendation: `Element volume spike! ${salesCnt} secondary sales (Floor: ${flr.toFixed(4)} BNB). List or sell manually on Element.`,
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
      recommendation: `Trailing Crash Stop: Dropped ${(drawdownPct * 100).toFixed(1)}% from peak ($${peakVal.toFixed(2)}). Exit to preserve capital!`,
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

// Filter and Sort Drops
function getFilteredDrops() {
  let list = [...allDrops];

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
    list = list.filter(d => {
      const pStr = String(d.mint_price_str || '').toUpperCase();
      const vWei = Number(d.mint_value_wei || 0);
      return d.has_free_mint || pStr.includes('FREE') || (vWei === 0 && Number(d.mint_price_bnb || 0) === 0 && !pStr.includes('PAID') && !pStr.includes('DEX') && !pStr.includes('TBD'));
    });
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
    list = list.filter(d => {
      const sw = Array.isArray(d.smart_wallets) && d.smart_wallets.length > 0
        ? d.smart_wallets
        : (Array.isArray(d.smart_minters) ? d.smart_minters : []);
      const matchSw = sw.some(w => String(w).toLowerCase().includes(q));
      return (d.name && d.name.toLowerCase().includes(q)) ||
        (d.symbol && d.symbol.toLowerCase().includes(q)) ||
        (d.contract && d.contract.toLowerCase().includes(q)) ||
        (d.deployer && d.deployer.toLowerCase().includes(q)) ||
        matchSw;
    });
  }

  // Sort
  if (sortMode === 'MCAP' || sortMode === 'MCAP_DESC') {
    list.sort((a, b) => getSanitizedMcap(b) - getSanitizedMcap(a));
  } else if (sortMode === 'MCAP_ASC') {
    list.sort((a, b) => {
      const ma = getSanitizedMcap(a);
      const mb = getSanitizedMcap(b);
      if (ma === 0 && mb > 0) return 1;
      if (mb === 0 && ma > 0) return -1;
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

const TABLE_RENDER_CHUNK = 60;
let tableRenderLimit = 60;

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

  el.innerHTML = `${baseLabel} ${indicator}`;
  const baseClass = (el.className || '').replace(/\s*(sortable-th|active-sort)\b/g, '').trim();
  el.className = `${baseClass} sortable-th${(isActive && (!isDefaultBlock || sortMode === 'BLOCK_ASC')) ? ' active-sort' : ''}`.trim();
  el.style.cursor = 'pointer';
  el.title = title;

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

// Render Table Rows
function renderTable(keepLimit = false) {
  if (typeof document === 'undefined') return;
  const tbody = document.getElementById('tableBody');
  if (!tbody) return;
  if (!keepLimit) {
    tableRenderLimit = TABLE_RENDER_CHUNK;
  }
  tbody.innerHTML = '';
  const filtered = getFilteredDrops();

  updateFavPillCount();
  updateSniperReadyCount();
  updateNftSurgeCount();
  updateProxyCount();
  updateLaunchpadCount();
  updateSecondaryTabCount();
  updateFollowedFilterCount();
  if (activeTab === 'SECONDARY') {
    updateSecondaryBannerUI();
  }
  updateDailyPulseUI();
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
      makeHeaderSortable(thCol9, '📈 DEX PRICE / 24H', 'PRICE_DESC', 'PRICE_ASC');
    }
    if (thCol10) {
      thCol10.className = 'col-progress';
      makeHeaderSortable(thCol10, '💎 MARKET CAP / FDV', 'MCAP_DESC', 'MCAP_ASC');
    }
    if (thCol11) {
      thCol11.className = 'col-price';
      makeHeaderSortable(thCol11, '🌊 24H VOLUME', 'VOLUME_DESC', 'VOLUME_ASC');
    }
    if (thCol12) {
      thCol12.className = 'col-lp';
      makeHeaderSortable(thCol12, '🥞 PANCAKESWAP LP', 'LP_DESC', 'LP_ASC');
    }
    if (thCol13) {
      thCol13.className = 'col-holders';
      makeHeaderSortable(thCol13, '👥 HOLDERS / BUYS', 'HOLDERS_DESC', 'HOLDERS_ASC');
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
      tr.innerHTML = `
        <td colspan="14" style="text-align: center; padding: 40px 20px; color: var(--text-dim);">
          <div style="font-size: 28px; margin-bottom: 8px;">📈</div>
          <div style="font-weight: 700; color: #ffd600; font-size: 14px; margin-bottom: 6px;">
            No Secondary Market Pairs Found
          </div>
          <div style="font-size: 12px; max-width: 500px; margin: 0 auto 14px; line-height: 1.5;">
            Tokens automatically graduate here once they inject PancakeSwap LP, achieve &gt;$500 market cap, or begin active secondary trading.
          </div>
        </td>
      `;
    } else {
      tr.innerHTML = `<td colspan="14" style="text-align: center; padding: 30px; color: var(--text-dim);">No agentic projects match current filter [${activeFilter}] or search.</td>`;
    }
    tbody.appendChild(tr);
    return;
  }

  const fragment = document.createDocumentFragment();
  const visible = filtered.slice(0, tableRenderLimit);
  visible.forEach((item, index) => {
    const tr = document.createElement('tr');
    if (item._isNew) tr.classList.add('row-new');
    tr.addEventListener('click', () => openModal(item));

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
      const firstF = followedNames[0] || 'Alpha';
      smBadge = `<span class="badge-followed-star" title="⭐ Followed Alpha Hunter: ${escapeHtml(followedNames.join(', '))}">⭐ ${escapeHtml(firstF)}</span>`;
    } else if (isCluster && isReady) {
      smBadge = `<span class="badge-cluster" title="🔥 ${smWalletsList.length} Smart Wallets Cluster & Sniper Ready">🔥🎯 CLUSTER (${smWalletsList.length})</span>`;
    } else if (isCluster) {
      smBadge = `<span class="badge-cluster" title="🔥 ${smWalletsList.length} Smart Wallets Cluster Inflow">🔥 CLUSTER (${smWalletsList.length})</span>`;
    } else if (isSm && isReady) {
      smBadge = `<span title="Smart Alpha & Sniper Ready">🧠🎯</span>`;
    } else if (isReady) {
      smBadge = `<span title="Sniper Ready">🎯</span>`;
    } else if (isSm) {
      smBadge = `<span title="${smWalletsList.join(', ')}">🧠</span>`;
    }

    // Standard badge
    const std = String(item.standard || 'ERC-721');
    let stdBadge = `<span style="color: var(--color-magenta); font-weight:600;">ERC-721</span>`;
    if (item.is_nft_surge) {
      stdBadge = `<span class="badge badge-magenta" style="box-shadow: 0 0 8px rgba(217, 70, 239, 0.4); font-weight:700;">🦋 SURGE (${item.secondary_sales_count || 0})</span>`;
    } else if (item.is_proxy_clone) {
      stdBadge = `<span class="badge badge-cyan" style="box-shadow: 0 0 8px rgba(56, 189, 248, 0.4); font-weight:700;" title="${escapeHtml(item.proxy_type || 'Proxy')} Master: ${escapeHtml(item.master_implementation || '')}">🧩 PROXY</span>`;
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
    const score = Number(item.score || 0);
    let intBadge = `<span style="color: var(--text-dim);">🧊 Cold (${score})</span>`;
    if (score >= 70) intBadge = `<span style="color: var(--color-magenta); font-weight:700;">🚀 Viral (${score})</span>`;
    else if (score >= 50) intBadge = `<span style="color: var(--color-red); font-weight:700;">🔥 High (${score})</span>`;
    else if (score >= 25) intBadge = `<span style="color: var(--color-yellow); font-weight:700;">🌱 Warm (${score})</span>`;

    // Holders / Buys
    const hld = Number(item.holders || item.total_minted || 0);
    const buys = Number(item.buys_24h || item.total_minted || 0);
    const hbStr = (hld > 0 || buys > 0)
      ? `<span class="hld">${formatCount(hld)}</span> <span class="dim">/</span> <span class="buy">${formatCount(buys)}</span>`
      : `<span class="dim">0 / 0</span>`;

    // LP / Vol & Market Cap
    const lpUsd = Number(item.lp_usd || 0);
    const mcapUsd = Number(item.market_cap || item.fdv || 0);
    const volBnb = item.volume_bnb || item.nft_volume_bnb;
    const secVol = Number(item.secondary_vol_bnb || 0);
    let lpStr = `<span class="dim">—</span>`;
    if (lpUsd > 0) {
      const dexScreenerUrl = getDexScreenerUrl(item);
      lpStr = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight:600; text-decoration: none;" title="View Liquidity on DexScreener">$${lpUsd.toLocaleString(undefined, { maximumFractionDigits: 0 })} ↗</a>`;
      if (mcapUsd > 0) {
        lpStr += `<div class="sub-mcap" style="font-size: 10px; color: var(--color-cyan); font-weight:600;" title="Market Cap / FDV: $${formatCount(mcapUsd)}"><a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-cyan); text-decoration: none;">MC: $${formatCount(mcapUsd)}</a></div>`;
      }
    } else if (item.is_nft_surge && secVol > 0) {
      lpStr = `<span style="color: var(--color-magenta); font-weight:700;" title="${item.secondary_sales_count || 0} Element sales">${secVol.toFixed(3)} BNB 🦋</span>`;
    } else if (secVol > 0) {
      lpStr = `<span style="color: var(--color-cyan); font-weight:600;" title="${item.secondary_sales_count || 0} Element sales">${secVol.toFixed(3)} BNB</span>`;
    } else if (volBnb && Number(volBnb) > 0) {
      lpStr = `<span style="color: var(--color-cyan); font-weight:600;">${Number(volBnb).toFixed(2)} BNB</span>`;
    } else if (mcapUsd > 0) {
      lpStr = `<span style="color: var(--color-cyan); font-weight:600;" title="Market Cap / FDV: $${formatCount(mcapUsd)}">MC: $${formatCount(mcapUsd)}</span>`;
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
      // Uncapped Open Edition / Fair Mint (No fixed supply cap)
      let tag = std.includes('BEP-20') ? 'TOKEN' : 'OPEN';
      let color = std.includes('BEP-20') ? 'var(--color-green)' : 'var(--color-cyan)';
      let fillWidth = tm > 0 ? '100%' : '0%';
      let mintsLabel = tm > 0 ? `${formatCount(tm)} (Open)` : '0 (Open)';
      progBar = `
        <div class="progress-cell">
          <span class="prog-tag" style="color: ${color}; font-weight:700;">${tag} FAIR</span>
          <div class="prog-bar-container">
            <div class="prog-bar-fill" style="width: ${fillWidth}; background: linear-gradient(90deg, ${color}, rgba(56, 189, 248, 0.4));"></div>
          </div>
          <span class="prog-ratio" style="color: var(--text-white); font-weight: 600;">${mintsLabel}</span>
        </div>
      `;
    }

    // Mint Price
    const pStr = String(item.mint_price_str || '').toUpperCase();
    const vWei = Number(item.mint_value_wei || 0);
    const isFree = Boolean(item.has_free_mint || pStr.includes('FREE') || (vWei === 0 && Number(item.mint_price_bnb || 0) === 0 && !pStr.includes('PAID') && !pStr.includes('DEX') && !pStr.includes('TBD')));
    let priceStr = `<span class="dim">TBD</span>`;
    if (pStr.includes('DEX')) {
      priceStr = `<span style="color: var(--color-cyan);">DEX Traded</span>`;
    } else if (isFree) {
      priceStr = `<span style="color: var(--color-green); font-weight:700;">⭐ FREE</span>`;
    } else if (vWei > 0) {
      priceStr = `<span style="color: var(--color-yellow); font-weight:600;">💎 ${(vWei / 1e18).toFixed(4)} BNB</span>`;
    }

    // Floor / Trade
    const flUsd = Number(item.floor_price_usd || 0);
    const flBnb = Number(item.floor_price_bnb || 0);
    const tkUsd = Number(item.token_price_usd || item.market_price_usd || 0);
    let floorStr = `<span class="dim">—</span>`;
    if (flUsd > 0) {
      floorStr = `<span style="color: var(--color-cyan); font-weight:600;">$${flUsd.toFixed(2)}</span>`;
    } else if (flBnb > 0) {
      if (item.is_nft_surge) {
        floorStr = `<span style="color: var(--color-magenta); font-weight:700;">${flBnb.toFixed(4)} BNB</span>`;
      } else {
        floorStr = `<span style="color: var(--color-cyan); font-weight:600;">${flBnb} BNB</span>`;
      }
    } else if (tkUsd > 0) {
      const dexScreenerUrl = getDexScreenerUrl(item);
      floorStr = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight:600; text-decoration: none;" title="View Live Chart on DexScreener">$${tkUsd < 0.001 ? tkUsd.toFixed(6) : tkUsd.toFixed(4)} ↗</a>`;
      const chg24 = Number(item.price_change_24h || 0);
      if (chg24 !== 0) {
        const isUp = chg24 > 0;
        floorStr += ` <span class="badge ${isUp ? 'badge-green' : 'badge-red'}" style="font-size: 9px; padding: 1px 3px;" title="24h Price Change: ${chg24.toFixed(1)}%">${isUp ? '+' : ''}${chg24.toFixed(1)}%</span>`;
      }
    }

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
    let cellPrice = priceStr;
    let cellFloor = floorStr;
    let actBtn = `<button class="inspect-btn"><span class="desktop-only">Inspect</span><span class="mobile-only">🔍 Deep Dive & Snipe CLI ➔</span></button>`;

    if (isSec) {
      const dexScreenerUrl = getDexScreenerUrl(item);
      if (lpUsd > 0) {
        cellLp = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight:700; text-decoration: none;" title="View Liquidity Pool on DexScreener">$${formatCount(lpUsd)} ↗</a>`;
      } else if (secVol > 0) {
        cellLp = `<span style="color: var(--color-magenta); font-weight:600;">${secVol.toFixed(2)} BNB</span>`;
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
            <a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight: 700; text-decoration: none;" title="Open live chart on DexScreener">$${tkUsd < 0.001 ? tkUsd.toFixed(6) : tkUsd.toFixed(4)} ↗</a>
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
        actBtn = `<button class="inspect-btn" style="border-color: var(--color-cyan); color: var(--color-cyan); font-weight:700;"><span class="desktop-only">⛽ Gas Reserve</span><span class="mobile-only">⛽ Gas ➔</span></button>`;
      } else if (isNft) {
        cellHolders = `<span style="color: var(--color-green); font-weight:700;">Holdings: ${formatCount(item.user_balance || 1)} NFT</span>`;
        cellFloor = `<span class="badge ${exitSignal.badge_class}" style="font-size: 11px;">${exitSignal.badge}</span>`;
        actBtn = `<button class="inspect-btn" style="border-color: var(--color-magenta); color: var(--color-magenta); font-weight:700;"><span class="desktop-only">🎨 Element List</span><span class="mobile-only">🎨 Element ➔</span></button>`;
      } else {
        const balNum = Number(item.user_balance || 0);
        const balStr = balNum >= 1000 ? formatCount(balNum) : (balNum < 0.01 ? balNum.toFixed(4) : balNum.toFixed(2));
        cellHolders = `<span style="color: var(--color-green); font-weight:700;">Holdings: ${balStr}</span>`;
        cellFloor = `<span class="badge ${exitSignal.badge_class}" style="font-size: 11px;">${exitSignal.badge}</span>`;
        actBtn = `<button class="inspect-btn" style="border-color: #f0b90b; color: #f0b90b; font-weight:700;"><span class="desktop-only">🥞 1-Click Exit</span><span class="mobile-only">🥞 Exit CLI ➔</span></button>`;
      }
    }

    if (isSec) {
      tr.innerHTML = `
        <td class="col-fav"><button class="${starClass}" onclick="toggleFavorite('${item.contract}', event)" title="${isFav ? 'Remove from favorites' : 'Add to favorites'}">${starIcon}</button></td>
        <td class="col-num">${index + 1}</td>
        <td class="col-block"><code>${blk}</code></td>
        <td class="col-sm">${smBadge}</td>
        <td class="col-project"><strong>${item.name || 'Unknown'}</strong><span class="sym">(${item.symbol || ''})</span></td>
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
        <td class="col-project"><strong>${item.name || 'Unknown'}</strong><span class="sym">(${item.symbol || ''})</span></td>
        <td class="col-type">${stdBadge}</td>
        <td class="col-safety">${safeBadge}</td>
        <td class="col-interest">${intBadge}</td>
        <td class="col-holders" data-label="${isPort ? 'HOLDINGS' : 'HOLDERS'}"><span class="cell-val">${cellHolders}</span></td>
        <td class="col-lp" data-label="LIQUIDITY"><span class="cell-val">${cellLp}</span></td>
        <td class="col-progress" data-label="PROGRESS">${cellProg}</td>
        <td class="col-price" data-label="MINT"><span class="cell-val">${cellPrice}</span></td>
        <td class="col-floor" data-label="FLOOR"><span class="cell-val">${cellFloor}</span></td>
        <td class="col-action">${actBtn}</td>
      `;
    }
    fragment.appendChild(tr);
  });
  tbody.appendChild(fragment);

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
function appendNextTableBatch() {
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
    tr.addEventListener('click', () => openModal(item));

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
      const firstF = followedNames[0] || 'Alpha';
      smBadge = `<span class="badge-followed-star" title="⭐ Followed Alpha Hunter: ${escapeHtml(followedNames.join(', '))}">⭐ ${escapeHtml(firstF)}</span>`;
    } else if (isCluster && isReady) {
      smBadge = `<span class="badge-cluster" title="🔥 ${smWalletsList.length} Smart Wallets Cluster & Sniper Ready">🔥🎯 CLUSTER (${smWalletsList.length})</span>`;
    } else if (isCluster) {
      smBadge = `<span class="badge-cluster" title="🔥 ${smWalletsList.length} Smart Wallets Cluster Inflow">🔥 CLUSTER (${smWalletsList.length})</span>`;
    } else if (isSm && isReady) {
      smBadge = `<span title="Smart Alpha & Sniper Ready">🧠🎯</span>`;
    } else if (isReady) {
      smBadge = `<span title="Sniper Ready">🎯</span>`;
    } else if (isSm) {
      smBadge = `<span title="${smWalletsList.join(', ')}">🧠</span>`;
    }

    // Standard badge
    const std = String(item.standard || 'ERC-721');
    let stdBadge = `<span style="color: var(--color-magenta); font-weight:600;">ERC-721</span>`;
    if (item.is_nft_surge) {
      stdBadge = `<span class="badge badge-magenta" style="box-shadow: 0 0 8px rgba(217, 70, 239, 0.4); font-weight:700;">🦋 SURGE (${item.secondary_sales_count || 0})</span>`;
    } else if (item.is_proxy_clone) {
      stdBadge = `<span class="badge badge-cyan" style="box-shadow: 0 0 8px rgba(56, 189, 248, 0.4); font-weight:700;" title="${escapeHtml(item.proxy_type || 'Proxy')} Master: ${escapeHtml(item.master_implementation || '')}">🧩 PROXY</span>`;
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
    const sc = Number(item.score || 0);
    let intBadge = `<span style="color: var(--text-dim);">Cold (0)</span>`;
    if (sc >= 80) {
      intBadge = `<span style="color: var(--color-red); font-weight:700;">🔥 Hot (${sc})</span>`;
    } else if (sc >= 50) {
      intBadge = `<span style="color: var(--color-yellow); font-weight:600;">⚡ Med (${sc})</span>`;
    } else if (sc >= 20) {
      intBadge = `<span style="color: var(--color-cyan);">🌱 Warm (${sc})</span>`;
    }

    // Progress Bar
    const isNft = std.includes('721') || std.includes('NFT');
    const ms = Number(item.max_supply || 0);
    const tm = Number(item.total_minted || 0);
    let progPct = 0;
    let progLabel = 'TOKEN 0%';
    if (isNft && ms > 0) {
      progPct = Math.min(100, Math.max(0, (tm / ms) * 100));
      progLabel = `${progPct.toFixed(0)}%`;
    } else if (ms > 0) {
      progPct = Math.min(100, Math.max(0, (tm / ms) * 100));
      progLabel = `TOKEN ${progPct.toFixed(0)}%`;
    } else if (tm > 0) {
      progLabel = 'OPEN FAIR';
      progPct = 100;
    }
    if (ms > 0 && tm >= ms) {
      progLabel = 'SOLD 100%';
      progPct = 100;
    }
    const progColor = progPct >= 99 ? 'var(--color-magenta)' : (progPct >= 50 ? 'var(--color-green)' : 'var(--color-cyan)');
    const progBar = `
      <div style="font-size: 10px; color: ${progColor}; font-weight: 700; margin-bottom: 2px;">${progLabel}</div>
      <div class="progress-bar-wrap">
        <div class="progress-bar-fill" style="width: ${progPct}%; background: ${progColor};"></div>
      </div>
    `;

    // Mint Price
    const pStr = String(item.mint_price_str || '').toUpperCase();
    const vWei = Number(item.mint_value_wei || 0);
    const isFree = item.has_free_mint || pStr.includes('FREE') || (vWei === 0 && Number(item.mint_price_bnb || 0) === 0 && !pStr.includes('PAID') && !pStr.includes('DEX') && !pStr.includes('TBD'));
    let priceStr = `<span class="dim">TBD</span>`;
    if (pStr.includes('DEX')) {
      priceStr = `<span style="color: var(--color-cyan);">DEX Traded</span>`;
    } else if (isFree) {
      priceStr = `<span style="color: var(--color-green); font-weight:700;">⭐ FREE</span>`;
    } else if (vWei > 0) {
      priceStr = `<span style="color: var(--color-yellow); font-weight:600;">💎 ${(vWei / 1e18).toFixed(4)} BNB</span>`;
    }

    // Floor / Trade
    const flUsd = Number(item.floor_price_usd || 0);
    const flBnb = Number(item.floor_price_bnb || 0);
    const tkUsd = Number(item.token_price_usd || item.market_price_usd || 0);
    let floorStr = `<span class="dim">—</span>`;
    if (flUsd > 0) {
      floorStr = `<span style="color: var(--color-cyan); font-weight:600;">$${flUsd.toFixed(2)}</span>`;
    } else if (flBnb > 0) {
      if (item.is_nft_surge) {
        floorStr = `<span style="color: var(--color-magenta); font-weight:700;">${flBnb.toFixed(4)} BNB</span>`;
      } else {
        floorStr = `<span style="color: var(--color-cyan); font-weight:600;">${flBnb} BNB</span>`;
      }
    } else if (tkUsd > 0) {
      const dexScreenerUrl = getDexScreenerUrl(item);
      floorStr = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight:600; text-decoration: none;" title="View Live Chart on DexScreener">$${tkUsd < 0.001 ? tkUsd.toFixed(6) : tkUsd.toFixed(4)} ↗</a>`;
      const chg24 = Number(item.price_change_24h || 0);
      if (chg24 !== 0) {
        const isUp = chg24 > 0;
        floorStr += ` <span class="badge ${isUp ? 'badge-green' : 'badge-red'}" style="font-size: 9px; padding: 1px 3px;" title="24h Price Change: ${chg24.toFixed(1)}%">${isUp ? '+' : ''}${chg24.toFixed(1)}%</span>`;
      }
    }

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
    let cellHolders = `${formatCount(item.holders || 1)} / ${formatCount(item.buys_24h || 1)}`;
    if (isPort && item.user_balance > 0) {
      const balVal = isNft ? `${item.user_balance} NFT` : formatCount(item.user_balance);
      const usdVal = exitSignal.holding_usd > 0 ? ` (~$${exitSignal.holding_usd.toFixed(2)})` : '';
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

    const mcapUsdBatch = Number(item.market_cap || item.fdv || 0);
    let lpStr = '—';
    if (Number(item.lp_usd || 0) > 0) {
      const dexScreenerUrl = getDexScreenerUrl(item);
      lpStr = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight:600; text-decoration: none;" title="View Liquidity on DexScreener">$${formatCount(item.lp_usd)} ↗</a>`;
      if (mcapUsdBatch > 0) {
        lpStr += `<div class="sub-mcap" style="font-size: 10px; color: var(--color-cyan); font-weight:600;" title="Market Cap / FDV: $${formatCount(mcapUsdBatch)}"><a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-cyan); text-decoration: none;">MC: $${formatCount(mcapUsdBatch)}</a></div>`;
      }
    } else if (item.is_nft_surge && Number(item.secondary_vol_bnb || 0) > 0) {
      lpStr = `<span style="color: var(--color-magenta); font-weight:600;">${Number(item.secondary_vol_bnb).toFixed(2)} BNB</span>`;
    } else if (mcapUsdBatch > 0) {
      lpStr = `<span style="color: var(--color-cyan); font-weight:600;" title="Market Cap / FDV: $${formatCount(mcapUsdBatch)}">MC: $${formatCount(mcapUsdBatch)}</span>`;
    }

    const isSec = (activeTab === 'SECONDARY');
    let cellLp = lpStr;
    let cellProg = progBar;
    let cellPrice = priceStr;
    let actBtn = `<button class="inspect-btn" onclick="openModal(allDrops[${index}] || allDrops.find(x => x.contract === '${item.contract}'))"><span class="desktop-only">Inspect</span><span class="mobile-only">➔</span></button>`;

    if (isSec) {
      const dexScreenerUrl = getDexScreenerUrl(item);
      if (Number(item.lp_usd || 0) > 0) {
        cellLp = `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight:700; text-decoration: none;" title="View Liquidity Pool on DexScreener">$${formatCount(item.lp_usd)} ↗</a>`;
      } else if (Number(item.secondary_vol_bnb || 0) > 0) {
        cellLp = `<span style="color: var(--color-magenta); font-weight:600;">${Number(item.secondary_vol_bnb).toFixed(2)} BNB</span>`;
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
            <a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation();" style="color: var(--color-green); font-weight: 700; text-decoration: none;" title="Open live chart on DexScreener">$${tkUsd < 0.001 ? tkUsd.toFixed(6) : tkUsd.toFixed(4)} ↗</a>
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
        actBtn = `<button class="inspect-btn" style="border-color: var(--color-cyan); color: var(--color-cyan); font-weight:700;"><span class="desktop-only">⛽ Gas Reserve</span><span class="mobile-only">⛽ Gas ➔</span></button>`;
      } else if (isNft) {
        cellHolders = `<span style="color: var(--color-green); font-weight:700;">Holdings: ${formatCount(item.user_balance || 1)} NFT</span>`;
        cellFloor = `<span class="badge ${exitSignal.badge_class}" style="font-size: 11px;">${exitSignal.badge}</span>`;
        actBtn = `<button class="inspect-btn" style="border-color: var(--color-magenta); color: var(--color-magenta); font-weight:700;"><span class="desktop-only">🎨 Element List</span><span class="mobile-only">🎨 Element ➔</span></button>`;
      } else {
        const balNum = Number(item.user_balance || 0);
        const balStr = balNum >= 1000 ? formatCount(balNum) : (balNum < 0.01 ? balNum.toFixed(4) : balNum.toFixed(2));
        cellHolders = `<span style="color: var(--color-green); font-weight:700;">Holdings: ${balStr}</span>`;
        cellFloor = `<span class="badge ${exitSignal.badge_class}" style="font-size: 11px;">${exitSignal.badge}</span>`;
        actBtn = `<button class="inspect-btn" style="border-color: #f0b90b; color: #f0b90b; font-weight:700;"><span class="desktop-only">🥞 1-Click Exit</span><span class="mobile-only">🥞 Exit CLI ➔</span></button>`;
      }
    }

    if (isSec) {
      tr.innerHTML = `
        <td class="col-fav"><button class="${starClass}" onclick="toggleFavorite('${item.contract}', event)" title="${isFav ? 'Remove from favorites' : 'Add to favorites'}">${starIcon}</button></td>
        <td class="col-num">${index + 1}</td>
        <td class="col-block"><code>${blk}</code></td>
        <td class="col-sm">${smBadge}</td>
        <td class="col-project"><strong>${item.name || 'Unknown'}</strong><span class="sym">(${item.symbol || ''})</span></td>
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
        <td class="col-project"><strong>${item.name || 'Unknown'}</strong><span class="sym">(${item.symbol || ''})</span></td>
        <td class="col-type">${stdBadge}</td>
        <td class="col-safety">${safeBadge}</td>
        <td class="col-interest">${intBadge}</td>
        <td class="col-holders" data-label="${isPort ? 'HOLDINGS' : 'HOLDERS'}"><span class="cell-val">${cellHolders}</span></td>
        <td class="col-lp" data-label="LIQUIDITY"><span class="cell-val">${cellLp}</span></td>
        <td class="col-progress" data-label="PROGRESS">${cellProg}</td>
        <td class="col-price" data-label="MINT"><span class="cell-val">${cellPrice}</span></td>
        <td class="col-floor" data-label="FLOOR"><span class="cell-val">${cellFloor}</span></td>
        <td class="col-action">${actBtn}</td>
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

// Modal Inspection Dialog
function openModal(item) {
  const modal = document.getElementById('inspectModal');
  const content = document.getElementById('modalContent');
  if (!modal || !content) return;

  if (typeof window !== 'undefined' && window.va) {
    window.va('event', { name: 'inspect_drop', data: { name: item.name || '', symbol: item.symbol || '' } });
  }

  const std = String(item.standard || 'ERC-721');
  const isNft = std.includes('721') || std.includes('NFT');
  const cAddr = item.contract || '';
  const name = item.name || 'Unknown';
  const sym = item.symbol || '';

  const dexScreenerUrl = getDexScreenerUrl(item);
  const mktUrl = isNft
    ? getElementUrl(name, cAddr)
    : (dexScreenerUrl || item.market_url || `https://pancakeswap.finance/swap?outputCurrency=${cAddr}`);

  const smList = item.smart_wallets || item.smart_minters || [];
  const sEval = getSniperEval(item);
  const isReady = sEval.is_ready;
  const rawCd = item.mint_calldata;
  const isRecv = !rawCd || rawCd === '0x' || rawCd === 'recv';
  const userWallet = connectedWallet || '<YOUR_WALLET>';
  let calldata = isRecv ? '0x' : rawCd;
  if (!isRecv && calldata.startsWith('0x836a1040') && calldata.length >= 138) {
    const cleanW = userWallet.replace('0x', '').padStart(40, '0');
    calldata = calldata.slice(0, 74) + '000000000000000000000000' + cleanW + calldata.slice(138);
  }
  const valWei = item.mint_value_wei || '0';
  const cliSnippet = `baw contract-call preview --binanceChainId 56 --from ${userWallet} --to ${cAddr} --value ${valWei} --inputData ${calldata} --json`;

  const isFav = isFavorited(cAddr, item);
  const favBtnClass = isFav ? 'modal-fav-btn starred' : 'modal-fav-btn';
  const favBtnText = isFav ? '★ Favorited' : '☆ Add to Favorites';

  const exitEval = evaluatePortfolioExit(item, bnbPrice);
  const rawHeldWei = item.user_balance_wei || 1000000000000000000n;
  const sellCalldata = !isNft ? buildPancakeSwapSellCalldata(cAddr, rawHeldWei, userWallet) : '';
  const sellCliSnippet = !isNft ? `baw contract-call preview --binanceChainId 56 --from ${userWallet} --to ${PANCAKE_ROUTER_V2} --value 0 --inputData ${sellCalldata} --json` : '';

  const mintBnbNum = Number(item.mint_price_bnb || 0);
  const isFree = item.has_free_mint || mintBnbNum === 0 || valWei === '0';
  const mintCostLabel = isFree ? 'FREE MINT' : `${mintBnbNum.toFixed(4)} BNB (~$${(mintBnbNum * bnbPrice).toFixed(2)})`;
  const isSoldOut = Boolean(item.max_supply && item.total_minted && Number(item.total_minted) >= Number(item.max_supply));
  const isRiskContract = Boolean((item.risk_badge && (item.risk_badge.includes('🚨') || item.risk_badge.includes('Fund Drain') || item.risk_badge.includes('Honeypot'))) || item.risk_level === 'RUG' || (item.security && item.security.is_fund_drain));

  const ms = Number(item.max_supply || 0);
  const tm = Number(item.total_minted || 0);
  const lp = Number(item.lp_usd || 0);
  const bondingGraduated = (lp >= 500) || Boolean(item.sniper_badge && item.sniper_badge.includes('DEX'));
  let bondingPct = 0;
  if (bondingGraduated) {
    bondingPct = 100;
  } else if (ms > 0) {
    bondingPct = Math.min(100, Math.max(0, (tm / ms) * 100));
  } else if (tm > 0) {
    bondingPct = Math.min(99, Math.max(10, Math.log10(tm + 1) * 20));
  }

  const holdersCount = Number(item.holders || 0);
  let whaleConcentrationBadge = '<span class="whale-alert-pill" style="background: rgba(74, 222, 128, 0.15); border: 1px solid var(--color-green); color: var(--color-green);">🟢 Distributed</span>';
  let whaleConcentrationDesc = `Clean holder distribution across ${formatCount(holdersCount)} agent wallets. Low single-holder dump risk.`;
  if (holdersCount > 0 && holdersCount < 30) {
    whaleConcentrationBadge = '<span class="whale-alert-pill" style="background: rgba(239, 68, 68, 0.15); border: 1px solid var(--color-red); color: var(--color-red);">🚨 Extreme Whale Concentration (>50% Top 10)</span>';
    whaleConcentrationDesc = `High dump risk: Supply is concentrated in only ${holdersCount} wallets. Early snipers hold outsized control.`;
  } else if (holdersCount >= 30 && holdersCount < 100) {
    whaleConcentrationBadge = '<span class="whale-alert-pill" style="background: rgba(250, 204, 21, 0.15); border: 1px solid var(--color-yellow); color: var(--color-yellow);">🟡 Moderate Distribution (25%-49% Top 10)</span>';
    whaleConcentrationDesc = `Medium wallet clustering across ${holdersCount} wallets. Monitor large seller outflows.`;
  }

  // Feature 1: Dev Funding Root & Cluster Metrics
  const devRootAddr = (item.dev_root || (item.security && item.security.dev_root) || '').trim();
  const clusterDevs = Number(item.cluster_devs || (item.security && item.security.cluster_devs) || 1);
  const clusterN = Number(item.cluster_n || (item.security && item.security.cluster_n) || 1);
  const isCluster = (clusterDevs >= 2 || clusterN >= 3);

  // Feature 3: Dynamic Holder Distribution Breakdown
  const dist = computeHolderDistribution(item);
  const devHold = Number(dist.dev || 0);
  const insiderHold = Number(dist.insider || 0);
  const sniperHold = Number(dist.sniper || 0);
  const smartHold = Number(dist.smart || 0);
  const freshHold = Number(dist.fresh || 0);
  const publicHold = Number(dist.public || 0);

  const mcapNum = getSanitizedMcap(item);
  const vol24Num = Number(item.volume_24h || 0);
  const chg24Num = Number(item.price_change_24h || 0);
  const tkPriceNum = Number(item.token_price_usd || item.market_price_usd || 0);

  let mcapFormatted = mcapNum > 0 ? `$${formatCount(mcapNum)}` : '<span class="dim">Fair Mint Phase</span>';
  let vol24Formatted = vol24Num > 0 ? `$${formatCount(vol24Num)}` : '<span class="dim">—</span>';
  let chg24Badge = '';
  if (chg24Num !== 0) {
    const isUp = chg24Num > 0;
    chg24Badge = `<span class="badge ${isUp ? 'badge-green' : 'badge-red'}" style="font-size: 11px; font-weight: 700;">${isUp ? '▲ +' : '▼ '}${chg24Num.toFixed(1)}% (24h)</span>`;
  }
  const isUnminted = Boolean(dist.isUnminted);

  const isDevHeavy = devHold > 10.0;
  const isInsiderHeavy = insiderHold > 15.0;
  const isSniperHeavy = sniperHold > 15.0;

  const deployerAddr = (item.deployer || '').trim();
  const hasDeployer = Boolean(deployerAddr && deployerAddr !== 'N/A' && deployerAddr !== '0x0000000000000000000000000000000000000000');

  const cAddrFormatted = `<a href="https://bscscan.com/address/${cAddr}" target="_blank" rel="noopener" style="color: var(--color-cyan); text-decoration: underline;" title="${cAddr}"><code>${cAddr.slice(0, 8)}...${cAddr.slice(-6)}</code> ↗</a>`;
  
  const deployerFormatted = hasDeployer
    ? `<a href="https://bscscan.com/address/${deployerAddr}" target="_blank" rel="noopener" style="color: var(--color-cyan); text-decoration: underline;" title="${deployerAddr}"><code>${deployerAddr.slice(0, 8)}...${deployerAddr.slice(-6)}</code> ↗</a>`
    : `<span class="dim">N/A (Contract Direct)</span>`;

  let devRootFormatted = `<span class="dim">Unresolved</span>`;
  if (hasDeployer) {
    if (devRootAddr && devRootAddr.toLowerCase() !== deployerAddr.toLowerCase()) {
      devRootFormatted = `<a href="https://bscscan.com/address/${devRootAddr}" target="_blank" rel="noopener" style="color: var(--color-cyan); text-decoration: underline;" title="${devRootAddr}"><code>${devRootAddr.slice(0, 8)}...${devRootAddr.slice(-6)}</code> ↗</a> <span class="badge badge-red" style="font-size: 9px;">🚨 Funder</span>`;
    } else {
      devRootFormatted = `<code>${deployerAddr.slice(0, 6)}...${deployerAddr.slice(-4)}</code> <a href="https://bscscan.com/address/${deployerAddr}#internaltx" target="_blank" rel="noopener" style="color: var(--color-cyan); text-decoration: underline; font-size: 10.5px; margin-left: 4px;" title="Inspect incoming gas funding transactions on BscScan">🔍 Trace ↗</a>`;
    }
  } else if (devRootAddr) {
    devRootFormatted = `<a href="https://bscscan.com/address/${devRootAddr}" target="_blank" rel="noopener" style="color: var(--color-cyan); text-decoration: underline;" title="${devRootAddr}"><code>${devRootAddr.slice(0, 8)}...${devRootAddr.slice(-6)}</code> ↗</a>`;
  }

  let devSyndicateFormatted = `<span class="badge badge-dim" style="font-size: 10px;">Unindexed</span>`;
  if (hasDeployer || devRootAddr) {
    if (isCluster) {
      devSyndicateFormatted = `<span class="badge badge-red" style="font-weight: 700; font-size: 10px;">🚨 Dev Cluster (${clusterDevs} devs / ${clusterN} drops)</span>`;
    } else {
      devSyndicateFormatted = `<span class="badge badge-dim" style="font-size: 10px;">🟢 Standalone (1 drop)</span>`;
    }
  }

  content.innerHTML = `
    <div class="modal-header">
      <div class="modal-title">🔍 ${name} (${sym}) - Deep Dive Inspection</div>
      <div style="display: flex; gap: 8px; align-items: center;">
        <button class="${favBtnClass}" id="modalFavBtn" onclick="toggleModalFav('${cAddr}')">${favBtnText}</button>
        <button class="modal-close-btn" onclick="document.getElementById('inspectModal').close()">✕ Close [ESC]</button>
      </div>
    </div>

    <div class="modal-section">
      <div class="modal-section-title">Contract & On-Chain Architecture</div>
      <div class="modal-grid">
        <div><span class="dim">Contract:</span> ${cAddrFormatted}</div>
        <div><span class="dim">Deployer:</span> ${deployerFormatted}</div>
        <div><span class="dim">Funding Root:</span> ${devRootFormatted}</div>
        <div><span class="dim">Syndicate:</span> ${devSyndicateFormatted}</div>
        <div><span class="dim">Standard:</span> <strong>${std}</strong></div>
        <div><span class="dim">Deployment Block:</span> <code>#${item.block || 'N/A'}</code></div>
        <div><span class="dim">Total Minted:</span> <strong>${formatCount(item.total_minted)}</strong> / ${formatCount(item.max_supply) || '∞'}</div>
        <div><span class="dim">Holders:</span> <strong>${formatCount(item.holders)} unique wallets</strong></div>
      </div>
    </div>

    <div class="modal-section market-dynamics-card">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span style="color: var(--color-cyan); font-weight: 700; display: inline-flex; align-items: center; gap: 6px;">
          💎 Market Valuation & Trading Dynamics
        </span>
        <div style="display: flex; gap: 6px; align-items: center;">
          ${dexScreenerUrl ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" class="badge badge-cyan" style="text-decoration: none; font-weight: 700; padding: 3px 8px; font-size: 11px;" title="Open live chart and trades on DexScreener">📈 DexScreener ↗</a>` : ''}
          ${chg24Badge}
        </div>
      </div>
      <div class="modal-grid" style="grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 6px;">
        <div class="market-metric-box">
          <span class="dim">Market Cap / FDV</span>
          <strong style="color: ${mcapNum > 0 ? 'var(--color-green)' : 'var(--text-primary)'}; font-size: 15px; display: block; margin-top: 2px;">
            ${dexScreenerUrl && mcapNum > 0 ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" style="color: var(--color-green); text-decoration: none;" title="View Valuation on DexScreener">${mcapFormatted} ↗</a>` : mcapFormatted}
          </strong>
        </div>
        <div class="market-metric-box">
          <span class="dim">24h Trading Vol</span>
          <strong style="color: ${vol24Num > 0 ? 'var(--color-cyan)' : 'var(--text-dim)'}; font-size: 15px; display: block; margin-top: 2px;">
            ${dexScreenerUrl && vol24Num > 0 ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" style="color: var(--color-cyan); text-decoration: none;" title="View Volume on DexScreener">${vol24Formatted} ↗</a>` : vol24Formatted}
          </strong>
        </div>
        <div class="market-metric-box">
          <span class="dim">PancakeSwap LP</span>
          <strong style="color: ${lp > 0 ? 'var(--color-yellow)' : 'var(--text-dim)'}; font-size: 15px; display: block; margin-top: 2px;">
            ${dexScreenerUrl && lp > 0 ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" style="color: var(--color-yellow); text-decoration: none;" title="View Liquidity Pool on DexScreener">$${formatCount(lp)} ↗</a>` : (lp > 0 ? `$${formatCount(lp)}` : '$0')}
          </strong>
        </div>
        <div class="market-metric-box">
          <span class="dim">Current Price</span>
          <strong style="font-size: 15px; display: block; margin-top: 2px;">
            ${dexScreenerUrl && tkPriceNum > 0 ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" style="color: var(--color-green); text-decoration: none;" title="Open live chart on DexScreener">${formatCryptoPrice(tkPriceNum)} ↗</a>` : (tkPriceNum > 0 ? formatCryptoPrice(tkPriceNum) : (Number(item.floor_price_usd || 0) > 0 ? `$${Number(item.floor_price_usd).toFixed(2)}` : '<span class="dim">—</span>'))}
          </strong>
        </div>
      </div>
    </div>

    ${!isNft ? `
    <div class="modal-section bonding-gauge-card">
      <div class="bonding-gauge-header">
        <span class="bonding-gauge-title">${bondingGraduated ? '🚀 GRADUATED TO PANCAKESWAP DEX' : '📈 Secondary Market Bonding Curve & DEX Graduation Gauge'}</span>
        <span class="badge ${bondingGraduated ? 'badge-green' : (bondingPct >= 80 ? 'badge-red' : (bondingPct >= 50 ? 'badge-yellow' : 'badge-cyan'))}" style="font-weight: 700;">
          ${bondingGraduated ? '100% DEX LIQUIDITY' : `${bondingPct.toFixed(1)}% GRADUATED`}
        </span>
      </div>
      <div class="bonding-gauge-bar">
        <div class="bonding-gauge-fill" style="width: ${bondingGraduated ? 100 : bondingPct}%;"></div>
      </div>
      <div class="bonding-gauge-stats">
        <span>${bondingGraduated ? `Secondary Liquidity Pool: $${formatCount(lp)}` : `Primary Minting: ${formatCount(tm)} / ${formatCount(ms) || 'Target Cap'}`}</span>
        <span>${bondingGraduated ? (dexScreenerUrl ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" style="color: var(--color-green); font-weight: 600; text-decoration: none;" title="View pair on DexScreener">🟢 Active Trading on PancakeSwap V2 ➔ View on DexScreener ↗</a>` : '🟢 Active Trading on PancakeSwap V2') : (bondingPct >= 80 ? '🔥 Near DEX Pool Injection (≥80%)' : (bondingPct >= 50 ? '⚡ Strong Momentum (≥50%)' : '🌱 Early Fair-Mint Phase'))}</span>
      </div>
    </div>
    ` : ''}

    <div class="modal-section">
      <div class="modal-section-title">🛡️ Security & Anti-Rug Audit</div>
      <div class="modal-grid">
        <div><span class="dim">Risk Rating:</span> <strong>${item.risk_badge || item.risk_level || '🟢 Clean (SAFE)'}</strong></div>
        <div><span class="dim">Bytecode Refund:</span> ${item.security && item.security.has_refund ? '<span style="color: var(--color-green); font-weight:600;">🛡️ Verified (' + (item.security.refund_methods || []).slice(0, 1).join('') + ')</span>' : (Number(item.mint_price_bnb || 0) > 0 ? '<span style="color: var(--color-yellow); font-weight:600;">⚠️ No Refund Function</span>' : '<span class="dim">N/A (Free Mint)</span>')}</div>
        <div><span class="dim">Deployer Profile:</span> ${item.security && item.security.is_serial_dev ? '<span style="color: var(--color-red); font-weight:700;">🚨 Serial Dev (' + item.security.serial_count + ' drops)</span>' : (item.security && item.security.is_fresh_dev ? '<span style="color: var(--color-yellow);">🟡 Fresh Wallet</span>' : '<span style="color: var(--color-green);">🟢 Established Nonce</span>')}</div>
        <div><span class="dim">PancakeSwap LP:</span> ${item.lp_usd ? '<span style="color: var(--color-green); font-weight:700;">$' + Number(item.lp_usd).toLocaleString(undefined, { maximumFractionDigits: 0 }) + '</span>' : '<span class="dim">No LP Pool detected</span>'}</div>
        <div><span class="dim">Whale Concentration:</span> ${whaleConcentrationBadge}</div>
        <div><span class="dim">Dev Cluster Audit:</span> ${isCluster ? `<span class="badge badge-red" style="font-weight:700;">🚨 Dev Cluster: ${clusterDevs} linked wallets / ${clusterN} drops</span>` : '<span style="color: var(--color-green);">🟢 Independent Deployer</span>'}</div>
      </div>
      <div style="margin-top: 8px; font-size: 11px; color: var(--text-dim); display: flex; align-items: center; gap: 6px;">
        <span class="dim">Distribution Guard:</span> <span>${whaleConcentrationDesc}</span>
      </div>
      ${item.risk_flags && item.risk_flags.length > 0 ? `
        <div style="margin-top: 8px; font-size: 11px;">
          <span class="dim">Audit Flags:</span>
          ${item.risk_flags.map(f => `<span class="badge ${f.includes('🚨') || f.includes('Honeypot') || f.includes('Serial') || f.includes('Drain') || f.includes('Cluster') ? 'badge-red' : 'badge-yellow'}" style="margin: 2px;">${f}</span>`).join(' ')}
        </div>
      ` : ''}
    </div>

    <div class="modal-section holder-dist-card">
      <div class="holder-dist-header">
        <span class="holder-dist-title">📊 Holder Distribution Breakdown</span>
        <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
          ${isUnminted ? `
            <span class="badge badge-cyan" style="font-size: 10px;">🌱 Primary Fair-Mint Phase</span>
            <span class="badge badge-green" style="font-size: 10px;">🟢 Zero Dev Pre-Mine (100% Public Cap)</span>
          ` : `
            ${isDevHeavy ? `<span class="badge badge-red" style="font-size: 10px;">🚨 High Dev Holding (${devHold.toFixed(1)}% > 10%)</span>` : ''}
            ${isInsiderHeavy ? `<span class="badge badge-yellow" style="font-size: 10px;">⚠️ Insider Concentration (${insiderHold.toFixed(1)}%)</span>` : ''}
            ${isSniperHeavy ? `<span class="badge badge-red" style="font-size: 10px;">⚠️ Heavy Sniper Allocation (${sniperHold.toFixed(1)}%)</span>` : ''}
            ${!isDevHeavy && !isInsiderHeavy && !isSniperHeavy ? `<span class="badge badge-green" style="font-size: 10px;">🟢 Healthy Public Distribution</span>` : ''}
          `}
        </div>
      </div>
      ${isUnminted ? `
        <div class="holder-dist-bar unminted-bar" title="Unminted: 0% circulating supply. 100% of supply is available for public minting directly from smart contract.">
          <div class="holder-dist-seg-unminted" style="width: 100%;"></div>
        </div>
        <div class="holder-dist-legend" style="justify-content: space-between;">
          <span class="holder-dist-chip"><span class="holder-dist-dot dev"></span>Dev Pre-Mine: <strong style="color: #a855f7;">0.0% (Zero Dev Pre-Mine)</strong></span>
          <span class="holder-dist-chip"><span class="holder-dist-dot public"></span>Public Allocation: <strong style="color: #22c55e;">100.0% Available</strong></span>
          <span class="holder-dist-chip"><span class="holder-dist-dot smart"></span>Circulating: <strong style="color: var(--color-cyan);">0% Minted</strong></span>
        </div>
        <div class="holder-dist-explainer">
          ℹ️ <strong>Fair-Mint Protocol:</strong> Zero pre-minted developer tokens. 100% of the total supply is sitting directly in the verified contract, waiting to be claimed on-chain by public minters.
        </div>
      ` : `
        <div class="holder-dist-bar" title="Dev: ${devHold.toFixed(1)}% | Insiders: ${insiderHold.toFixed(1)}% | Snipers: ${sniperHold.toFixed(1)}% | Smart Agents: ${smartHold.toFixed(1)}% | Fresh: ${freshHold.toFixed(1)}% | Public: ${publicHold.toFixed(1)}%">
          ${devHold > 0 ? `<div class="holder-dist-seg dev" style="width: ${devHold}%;"></div>` : ''}
          ${insiderHold > 0 ? `<div class="holder-dist-seg insider" style="width: ${insiderHold}%;"></div>` : ''}
          ${sniperHold > 0 ? `<div class="holder-dist-seg sniper" style="width: ${sniperHold}%;"></div>` : ''}
          ${smartHold > 0 ? `<div class="holder-dist-seg smart" style="width: ${smartHold}%;"></div>` : ''}
          ${freshHold > 0 ? `<div class="holder-dist-seg fresh" style="width: ${freshHold}%;"></div>` : ''}
          <div class="holder-dist-seg public" style="width: ${publicHold}%;"></div>
        </div>
        <div class="holder-dist-legend">
          <span class="holder-dist-chip"><span class="holder-dist-dot dev"></span>Dev: <strong style="color: #a855f7;">${devHold.toFixed(1)}%</strong></span>
          <span class="holder-dist-chip"><span class="holder-dist-dot insider"></span>Insiders: <strong style="color: #f97316;">${insiderHold.toFixed(1)}%</strong></span>
          <span class="holder-dist-chip"><span class="holder-dist-dot sniper"></span>Snipers: <strong style="color: #ef4444;">${sniperHold.toFixed(1)}%</strong></span>
          <span class="holder-dist-chip"><span class="holder-dist-dot smart"></span>Smart Agents: <strong style="color: #00f0ff;">${smartHold.toFixed(1)}%</strong></span>
          <span class="holder-dist-chip"><span class="holder-dist-dot fresh"></span>Fresh: <strong style="color: #eab308;">${freshHold.toFixed(1)}%</strong></span>
          <span class="holder-dist-chip"><span class="holder-dist-dot public"></span>Public: <strong style="color: #22c55e;">${publicHold.toFixed(1)}%</strong></span>
        </div>
        <div class="holder-dist-explainer">
          ℹ️ <strong>Distribution Breakdown:</strong> Real-time estimation based on tracked .ai hunters (${dist.smart}%), block-0 mint velocity, developer cluster links, and on-chain holder balances.
        </div>
      `}
    </div>

    ${(item.is_proxy_clone || item.is_factory) ? `
    <div class="modal-section" style="border-color: rgba(56, 189, 248, 0.4); background: rgba(56, 189, 248, 0.03);">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span style="color: var(--color-cyan);">🧩 Proxy & Clone Architecture (Option E)</span>
        <span class="badge badge-cyan" style="font-size: 11px;">${escapeHtml(item.proxy_type || 'Clone Contract')}</span>
      </div>
      <div class="modal-grid" style="margin-top: 6px;">
        <div><span class="dim">Proxy Pattern:</span> <strong>${escapeHtml(item.proxy_type || 'EIP-1167 / EIP-1967')}</strong></div>
        <div><span class="dim">Implementation:</span> <strong>${item.master_implementation ? `<a href="https://bscscan.com/address/${item.master_implementation}" target="_blank" rel="noopener" style="color: var(--color-cyan); text-decoration: underline;">${item.master_implementation.slice(0, 10)}...${item.master_implementation.slice(-6)}</a>` : 'Direct Contract'}</strong></div>
        ${item.is_factory ? `<div><span class="dim">Factory:</span> <strong style="color: var(--color-yellow);">${escapeHtml(item.factory_badge || 'Launchpad Factory')}</strong></div>` : ''}
      </div>
    </div>
    ` : ''}

    <div class="modal-section" style="${isReady ? 'border-color: rgba(74, 222, 128, 0.4); background: rgba(74, 222, 128, 0.03);' : ''}">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span>🎯 Assisted Conviction Audit (60% Rule & Anti-Rug)</span>
        <span class="${isReady ? 'badge badge-green' : 'badge badge-yellow'}" style="font-size: 11px;">${sEval.badge}</span>
      </div>
      <div style="font-size: 12px; margin-top: 6px; line-height: 1.6;">
        ${sEval.checklist.map(line => `<div style="padding: 2px 0;">${line}</div>`).join('')}
      </div>
    </div>

    ${smList.length > 0 ? `
    <div class="modal-section">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span>🧠 Smart Money Activity (${smList.length} Tracked Wallets)</span>
        <button class="btn-sm-view-leaderboard" onclick="document.getElementById('inspectModal').close(); openSmartLeaderboardModal('ALL')">🏆 View All Smart Hunters (Win Rates) ↗</button>
      </div>
      <div style="color: var(--color-cyan); font-size: 12px; margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px;">
        ${smList.slice(0, 12).map(w => {
          const isF = followedWallets.has(String(w).toLowerCase());
          return `
            <div style="display: inline-flex; align-items: center; gap: 4px; background: rgba(0, 240, 255, 0.05); border: 1px solid rgba(0, 240, 255, 0.2); border-radius: 4px; padding: 2px 6px; margin: 2px;">
              <span style="font-weight: 700; color: ${isF ? 'var(--color-yellow)' : 'var(--color-cyan)'}; font-size: 11px;">${isF ? '⭐ ' : ''}${escapeHtml(w)}</span>
              <button class="${isF ? 'btn-follow-wallet following' : 'btn-follow-wallet'}" style="padding: 2px 6px; font-size: 9.5px;" onclick="toggleFollowWallet('${escapeHtml(w)}'); this.classList.toggle('following'); this.textContent = this.classList.contains('following') ? '★ Following' : '⭐ Follow';">
                ${isF ? '★ Following' : '⭐ Follow'}
              </button>
              <button class="btn-view-activity" style="padding: 2px 6px; font-size: 9.5px;" onclick="viewWalletActivity('${escapeHtml(w)}')" title="View all drops minted by ${escapeHtml(w)}">
                👁️ Activity
              </button>
            </div>
          `;
        }).join('')}
        ${smList.length > 12 ? `<span class="dim" style="align-self: center; font-size: 11px;">(+${smList.length - 12} more)</span>` : ''}
      </div>
    </div>
    ` : ''}

    ${isNft || item.secondary_sales_count ? `
    <div class="modal-section" style="${item.is_nft_surge ? 'border-color: rgba(217, 70, 239, 0.5); background: rgba(217, 70, 239, 0.04);' : ''}">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span style="color: var(--color-magenta);">🦋 Element Market Secondary Trading Activity</span>
        ${item.is_nft_surge ? `<span class="badge badge-magenta" style="font-size: 11px; font-weight:700;">🦋 SURGE (${item.secondary_sales_count} sales)</span>` : ''}
      </div>
      <div class="modal-grid" style="margin-top: 6px;">
        <div><span class="dim">Secondary Sales:</span> <strong style="color: ${item.secondary_sales_count >= 3 ? 'var(--color-magenta)' : 'inherit'};">${item.secondary_sales_count || 0} sales</strong></div>
        <div><span class="dim">Element Volume:</span> <strong>${item.secondary_vol_bnb ? Number(item.secondary_vol_bnb).toFixed(4) + ' BNB' : '0.0000 BNB'}</strong></div>
        <div><span class="dim">Floor Price:</span> <strong>${item.floor_price_bnb ? Number(item.floor_price_bnb).toFixed(4) + ' BNB' : '—'}</strong></div>
        <div><span class="dim">Secondary Transfers:</span> <strong>${item.secondary_transfers || 0} transfers</strong></div>
      </div>
      <div style="margin-top: 10px; font-size: 11px; color: var(--text-dim);">
        🔗 Marketplace: <a href="${mktUrl}" target="_blank" rel="noopener" style="color: var(--color-cyan); text-decoration: underline;">${mktUrl}</a> (Manual confirmation only — no auto-trade)
      </div>
    </div>
    ` : ''}

    ${isNft && (item.user_balance > 0 || activeTab === 'PORTFOLIO') ? `
    <div class="modal-section" style="border-color: rgba(217, 70, 239, 0.4); background: rgba(217, 70, 239, 0.03);">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span style="color: var(--color-magenta);">🎨 Element NFT Portfolio Exit Action</span>
        <span class="badge ${exitEval.badge_class || 'badge-magenta'}" style="font-size: 11px;">${exitEval.badge}</span>
      </div>
      <div style="font-size: 12px; margin-top: 4px; margin-bottom: 8px; color: var(--text-dim);">
        <strong style="color: var(--text-white);">Exit Strategy:</strong> ${exitEval.recommendation}
      </div>
      <div class="modal-links">
        <a href="${mktUrl}" target="_blank" rel="noopener" class="modal-btn" style="background: rgba(217, 70, 239, 0.2); border: 1px solid var(--color-magenta); color: #ffffff; text-decoration: none;">🛒 Open Element Market to List/Sell NFT</a>
      </div>
    </div>
    ` : ''}

    <div class="modal-section">
      <div class="modal-section-title">🔗 Official Secondary Links</div>
      <div class="modal-links">
        ${!isNft && dexScreenerUrl ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" class="modal-btn" style="background: rgba(0, 240, 255, 0.15); border: 1px solid var(--color-cyan); color: var(--color-cyan); font-weight: 700;">📈 View on DexScreener ↗</a>` : ''}
        <a href="${isNft ? mktUrl : `https://pancakeswap.finance/swap?outputCurrency=${cAddr}`}" target="_blank" rel="noopener" class="modal-btn btn-element">🛒 ${isNft ? 'Element NFT Collection' : 'Trade on PancakeSwap DEX'}</a>
        ${!isNft ? `<a href="https://pancakeswap.finance/swap?inputCurrency=${cAddr}&outputCurrency=BNB" target="_blank" rel="noopener" class="modal-btn" style="background: rgba(240, 185, 11, 0.15); border: 1px solid #f0b90b; color: #f0b90b;">🥞 Swap to BNB (PancakeSwap)</a>` : ''}
        <a href="https://bscscan.com/address/${cAddr}" target="_blank" rel="noopener" class="modal-btn btn-bscscan">🔍 View on BscScan</a>
        ${item.website ? `<a href="${item.website}" target="_blank" rel="noopener" class="modal-btn btn-bscscan">🌐 Website</a>` : ''}
        ${item.twitter ? `<a href="${item.twitter}" target="_blank" rel="noopener" class="modal-btn btn-bscscan">🐦 Twitter / X</a>` : ''}
      </div>
    </div>

    ${!isNft ? `
    <div class="modal-section dex-chart-section">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <span style="font-size: 12px; font-weight: 700; color: var(--color-cyan);">📈 Live DEX Candlestick Price Action Chart</span>
        <div style="display: flex; gap: 8px; align-items: center;">
          ${dexScreenerUrl ? `
            <a href="${dexScreenerUrl}" target="_blank" rel="noopener" class="dex-chart-toggle-btn" style="color: var(--color-cyan); text-decoration: none; padding: 4px 8px; border: 1px solid rgba(0, 240, 255, 0.3); border-radius: 4px; font-size: 11px; display: inline-flex; align-items: center; gap: 4px;" title="Open live pair on DexScreener in new window">
              <span>↗</span> <span>Open DexScreener</span>
            </a>
          ` : ''}
          <button class="dex-chart-toggle-btn" id="chartToggleBtn" onclick="toggleDexChart('${cAddr}', '${dexScreenerUrl}')">
            <span id="chartToggleIcon">📊</span> <span id="chartToggleLabel">Show Live DEX Chart</span>
          </button>
        </div>
      </div>
      <div id="dexChartWrap" class="dex-chart-frame-wrap" style="display: none;"></div>
    </div>
    ` : ''}

    <div class="modal-section mint-action-section" style="${isSoldOut ? 'opacity: 0.85;' : (isReady ? 'border-color: rgba(74, 222, 128, 0.5); box-shadow: 0 0 15px rgba(74, 222, 128, 0.1);' : 'border-color: rgba(0, 240, 255, 0.4);')}">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span style="color: var(--color-cyan); font-weight: 700; display: inline-flex; align-items: center; gap: 6px;">
          ⚡ 1-Click Web3 Direct Mint
        </span>
        <span class="badge ${isSoldOut ? 'badge-dim' : (isFree ? 'badge-green' : 'badge-yellow')}" style="font-size: 11px; font-weight: 700;">
          ${isSoldOut ? '🔒 SOLD OUT (100%)' : mintCostLabel}
        </span>
      </div>

      <div class="mint-summary-row" style="display: flex; justify-content: space-between; align-items: center; margin: 8px 0; font-size: 12px; flex-wrap: wrap; gap: 8px;">
        <div><span class="dim">Mint Cost:</span> <strong style="color: ${isFree ? 'var(--color-green)' : 'var(--color-yellow)'};">${mintCostLabel}</strong></div>
        <div><span class="dim">Progress:</span> <strong>${formatCount(item.total_minted)}</strong> / ${formatCount(item.max_supply) || '∞'}</div>
        <div><span class="dim">Calldata:</span> <code>${calldata.slice(0, 10)}${calldata.length > 10 ? '...' : ''}</code></div>
      </div>

      ${isRiskContract ? `
        <div class="mint-risk-warning" style="background: rgba(239, 68, 68, 0.12); border: 1px solid var(--color-red); border-radius: 6px; padding: 8px 12px; margin: 8px 0; font-size: 11px; color: #ff8888; display: flex; align-items: center; gap: 8px;">
          <span>🚨</span>
          <span><strong>Risk Warning:</strong> Contract is flagged for elevated risk (${escapeHtml(item.risk_badge || item.risk_level)}). Verify bytecode before signing.</span>
        </div>
      ` : ''}

      ${connectedWallet && !isAgenticWallet ? `
        <div class="agentic-status-warning" style="background: rgba(240, 185, 11, 0.08); border: 1px dashed rgba(240, 185, 11, 0.4); border-radius: 6px; padding: 8px 12px; margin: 8px 0; font-size: 11px; color: var(--color-yellow); display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap;">
          <span>ℹ️ <strong>Non-Agentic Wallet:</strong> If this drop is an Agent ID exclusive launch, it requires an active <code>.ai</code> Agent ID to mint.</span>
          <a href="https://web3.binance.com/en/dev-docs/products/agentic-wallet/ai-pro#came-from-the-agent-id-campaign" target="_blank" rel="noopener" style="color: var(--color-cyan); text-decoration: underline;">Claim .ai in Binance App ↗</a>
        </div>
      ` : (connectedWallet && isAgenticWallet ? `
        <div class="agentic-status-success" style="background: rgba(0, 240, 255, 0.08); border: 1px solid rgba(0, 240, 255, 0.3); border-radius: 6px; padding: 6px 12px; margin: 8px 0; font-size: 11px; color: var(--color-cyan); display: flex; align-items: center; gap: 8px;">
          <span>🤖</span>
          <span><strong>Agentic Wallet Active:</strong> Your wallet holds a verified <code>.ai</code> Agent ID on BSC. Eligible for exclusive inner-market minting.</span>
        </div>
      ` : '')}

      ${isSoldOut ? `
        <div style="display: flex; gap: 10px; margin-top: 8px;">
          <a href="${!isNft ? (dexScreenerUrl || mktUrl) : mktUrl}" target="_blank" rel="noopener" class="web3-mint-btn sold-out" style="text-align: center; text-decoration: none; display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%;">
            <span>🔒 Sold Out (100% Capped) ➔ ${!isNft ? 'View Chart & Trade on DexScreener ↗' : 'Trade on Secondary Market'}</span>
          </a>
        </div>
      ` : `
        <div class="mint-btn-container" style="margin-top: 8px;">
          <button id="modalMintBtn" class="web3-mint-btn" onclick="executeWeb3Mint('${cAddr}', '${calldata}', '${valWei}', '${escapeHtml(name)}')">
            <span class="mint-icon">⚡</span>
            <span id="modalMintBtnText">${connectedWallet ? `1-Click Mint (${mintCostLabel})` : `👛 Connect Wallet & Mint (${mintCostLabel})`}</span>
          </button>
        </div>
        <div id="modalMintStatus" class="mint-status-box" style="display: none; margin-top: 10px;"></div>
      `}
    </div>

    <div class="modal-section">
      <div class="modal-section-title">⚡ 1-Click Sniper CLI Command (Binance Agentic Wallet)</div>
      <div class="cli-box">
        <code>${escapeHtml(cliSnippet)}</code>
        <button class="copy-btn" onclick="navigator.clipboard.writeText('${cliSnippet.replace(/'/g, "\\'")}'); this.textContent='Copied!'; setTimeout(()=>this.textContent='Copy', 1500)">Copy</button>
      </div>
    </div>

    ${!isNft ? `
    <div class="modal-section" style="border-color: rgba(240, 185, 11, 0.4); background: rgba(240, 185, 11, 0.03);">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span style="color: var(--color-yellow);">🥞 1-Click PancakeSwap V2 Take-Profit / Exit Command</span>
        <span class="badge ${exitEval.badge_class || 'badge-green'}" style="font-size: 11px;">${exitEval.badge}</span>
      </div>
      <div style="font-size: 12px; margin-top: 4px; margin-bottom: 8px; color: var(--text-dim);">
        <strong style="color: var(--text-white);">Exit Strategy:</strong> ${exitEval.recommendation}
      </div>
      <div class="modal-links" style="margin-bottom: 10px;">
        ${dexScreenerUrl ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" class="modal-btn" style="background: rgba(0, 240, 255, 0.15); border: 1px solid var(--color-cyan); color: var(--color-cyan); font-weight: 700; text-decoration: none;">📈 DexScreener Live Chart ↗</a>` : ''}
        <a href="https://pancakeswap.finance/swap?inputCurrency=${cAddr}&outputCurrency=BNB" target="_blank" rel="noopener" class="modal-btn" style="background: rgba(240, 185, 11, 0.2); border: 1px solid #f0b90b; color: #f0b90b; text-decoration: none;">🥞 1-Click Swap on PancakeSwap ↗</a>
      </div>
      <div class="cli-box">
        <code>${escapeHtml(sellCliSnippet)}</code>
        <button class="copy-btn" onclick="navigator.clipboard.writeText('${sellCliSnippet.replace(/'/g, "\\'")}'); this.textContent='Copied!'; setTimeout(()=>this.textContent='Copy', 1500)">Copy Exit CLI</button>
      </div>
    </div>
    ` : ''}

    <div class="modal-section" style="border-top: 1px dashed var(--border-dim); margin-top: 14px; padding-top: 14px;">
      <div class="modal-section-title" style="color: var(--color-yellow);">💎 Support & Donate (BNB / BEP-20 / EVM)</div>
      <div style="font-size: 11px; color: var(--text-dim); margin-bottom: 8px;">
        Help keep the 24/7 autonomous cloud discovery engine running:
      </div>
      <div class="cli-box">
        <code style="color: var(--color-yellow);">${DONATION_WALLET}</code>
        <button class="copy-btn" onclick="copyDonationAddress(this, 'inspect_modal')">Copy Address</button>
      </div>
    </div>
  `;

  modal.showModal();
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

      const mintCostStr = g.isFree || g.mintUsd === 0 ? '<span style="color: var(--color-green); font-weight:700;">FREE</span>' : formatCryptoPrice(g.mintUsd);
      const athPriceStr = formatCryptoPrice(g.athPrice);
      const athProfitStr = formatCryptoPrice(g.athProfit, true);
      const multStr = g.multiplier ? `<span class="badge badge-yellow" style="font-size: 10px;">${g.multiplier}x</span>` : (g.isFree ? `<span class="badge badge-green" style="font-size: 10px;">FREE ALPHA</span>` : '');

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
          <td>${rankBadge}</td>
          <td>
            <div style="font-weight: 700; color: var(--text-white);">${escapeHtml(g.name)}</div>
            <div style="font-size: 10.5px; color: var(--color-cyan);">${escapeHtml(g.symbol)} <span style="color: var(--text-dim); font-size: 9.5px;">(${g.contract.slice(0, 6)}...${g.contract.slice(-4)})</span></div>
          </td>
          <td>${mintCostStr}</td>
          <td>
            <div style="font-weight:700; color: var(--color-yellow);">${athPriceStr}</div>
            <div style="font-size: 10.5px; color: var(--color-green); font-weight: 700;">${athProfitStr} ${multStr}</div>
          </td>
          <td>
            <div>${curPriceStr}</div>
            <div style="font-size: 10.5px;">${curNetStr}</div>
          </td>
          <td>
            <div>${statusBadge}</div>
            <div style="font-size: 10px; color: var(--text-dim); margin-top: 2px;">${formatCount(g.holders)} holders</div>
          </td>
          <td>
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
        <p class="hof-subtitle">Persistent Hall of Fame tracking peak All-Time High (ATH) net gains, secondary floor/DEX prices, and multipliers across all indexed Agent ID drops.</p>
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
            <th style="width: 150px;">Peak ATH / Net</th>
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

// Official Reliable BSC RPC Endpoints with automatic fallback
const BSC_PUBLIC_RPCS = [
  'https://bsc-dataseed.bnbchain.org',
  'https://bsc-dataseed1.defibit.io',
  'https://bsc-dataseed1.ninicoin.io',
  'https://1rpc.io/bnb'
];

// Robust JSON-RPC Single Call with failover
async function sendRpcCall(method, params) {
  for (const endpoint of BSC_PUBLIC_RPCS) {
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params })
      });
      if (!res.ok) continue;
      const data = await res.json();
      if (data && data.result !== undefined) {
        return data.result;
      }
    } catch (err) {
      // try next RPC
    }
  }
  return null;
}

// High-Throughput JSON-RPC Batch Call with failover
async function sendRpcBatch(batch) {
  if (!Array.isArray(batch) || batch.length === 0) return [];
  for (const endpoint of BSC_PUBLIC_RPCS) {
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(batch)
      });
      if (!res.ok) continue;
      const data = await res.json();
      if (Array.isArray(data)) {
        data.sort((a, b) => Number(a.id || 0) - Number(b.id || 0));
        return data;
      }
    } catch (err) {
      // try next RPC
    }
  }
  return [];
}

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
    // 1. Injected Provider Check
    let provider = getInjectedProvider();
    if (!provider) {
      openWalletModal();
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

  // Clear balances on allDrops
  allDrops.forEach(d => {
    d.user_balance = 0;
    d.user_balance_wei = 0n;
  });

  updateWalletButtonUI();
  loadFavoritesForActiveWallet();
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
        let dec = 18;
        if (std.includes('8 decimals')) dec = 8;
        drop.user_balance = isNft ? Number(raw) : (Number(raw) / (10 ** dec));
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
    updatePortfolioBannerUI();
    renderTable();
  }
}

// Update Dynamic Tab 3 Portfolio Banner
function updatePortfolioBannerUI() {
  if (typeof document === 'undefined') return;
  const banner = document.getElementById('portfolioBanner');
  if (!banner) return;

  let totalUsd = userNativeBnbBalance * bnbPrice;
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
        totalUsd += drop.user_balance * (fl * bnbPrice);
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
  if (valEl) valEl.textContent = `$${totalUsd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  const gasEl = document.getElementById('portGasReserve');
  if (gasEl) gasEl.textContent = `${userNativeBnbBalance.toFixed(4)} BNB ($${(userNativeBnbBalance * bnbPrice).toFixed(2)})`;

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
            <div class="tile-sub">≈ $${(userNativeBnbBalance * bnbPrice).toFixed(2)} USD</div>
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

  const currentOrigin = (typeof window !== 'undefined' && window.location.origin) ? window.location.origin : 'https://agent-id-monitor.vercel.app';
  const qrApiUrl = `https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=${encodeURIComponent(currentOrigin)}&bgcolor=ffffff&color=03080c&margin=6`;

  content.innerHTML = `
    <div class="wallet-card-container">
      <div class="modal-header wallet-card-header">
        <div class="modal-title wallet-card-title">
          <button class="wallet-back-btn" onclick="openWalletModal()">← Back</button>
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
  const qInspect = urlParams.get('inspect');
  if (qInspect) {
    const tryInspect = async (attempts = 0) => {
      let target = (allDrops || []).find(d => (d.contract || '').toLowerCase() === qInspect.toLowerCase());
      if (!target && attempts >= 3) {
        try {
          const res = await fetch(`${BACKEND_URL}/api/projects?search=${qInspect}`);
          if (res.ok) {
            const data = await res.json();
            if (data && data.projects && data.projects.length > 0) {
              target = data.projects[0];
            }
          }
        } catch (e) {}
      }
      if (target) {
        openModal(target);
        if (urlParams.get('chart') === '1') {
          setTimeout(() => toggleDexChart(target.contract), 200);
        }
        if (urlParams.get('scroll') === 'down') {
          setTimeout(() => {
            const m = document.getElementById('modalContent') || document.querySelector('#inspectModal .modal-content');
            if (m) m.scrollTop = m.scrollHeight;
          }, 350);
        }
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
async function pollLatestBlock() {
  for (const endpoint of BSC_PUBLIC_RPCS) {
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'eth_blockNumber', params: [] })
      });
      if (!res.ok) continue;
      const data = await res.json();
      if (data && data.result) {
        const blkNum = parseInt(data.result, 16);
        document.getElementById('latestBlock').textContent = `#${blkNum.toLocaleString()}`;
        return;
      }
    } catch (err) {
      // try next RPC endpoint
    }
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
const DELTA_POLL_INTERVAL_MS = 15000; // 15s interval protects hosting bandwidth while keeping market data near real-time

// Real-Time Delta Polling (auto-pauses when tab is hidden or backgrounded)
async function pollDeltaFeed() {
  if (isPollingDelta || !latestKnownBlock || (typeof document !== 'undefined' && document.hidden)) return;
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

    if (data && data.stats) {
      globalStats = data.stats;
      const rEl = document.getElementById('radarCount');
      if (rEl) rEl.textContent = globalStats.total;
      const sEl = document.getElementById('smartCount');
      if (sEl) sEl.textContent = globalStats.smart;
      updateSniperReadyCount();
      updateNftSurgeCount();
      updateProxyCount();
      updateLaunchpadCount();
      updateSecondaryTabCount();
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

        playMarketAlertSound();
        newlyFound.slice(0, 3).forEach(drop => showNewMarketToast(drop));

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
              let dec = 18;
              if (std.includes('8 decimals')) dec = 8;
              drop.user_balance = isNft ? Number(raw) : (Number(raw) / (10 ** dec));
              drop.user_balance_wei = raw;
            }
          });
          updatePortfolioBannerUI();
        }

        renderStats(allDrops);
        renderTable();
        checkUrlParams();

        const timeStr = new Date().toLocaleTimeString();
        const syncEl = document.getElementById('syncTime');
        if (syncEl) syncEl.textContent = `Synced ${timeStr}`;
      } else if (data.latest_block && data.latest_block > latestKnownBlock) {
        latestKnownBlock = data.latest_block;
      }
    } else if (data && data.latest_block && data.latest_block > latestKnownBlock) {
      latestKnownBlock = data.latest_block;
    }
  } catch (err) {
    // Delta poll is resilient; silent fallback on minor network hitch
  } finally {
    isPollingDelta = false;
  }
}

function startDeltaPolling() {
  if (deltaPollTimer) clearInterval(deltaPollTimer);
  if (typeof document !== 'undefined' && document.hidden) return;
  deltaPollTimer = setInterval(pollDeltaFeed, DELTA_POLL_INTERVAL_MS);
}

// Bandwidth Guard: Auto-pause polling when tab is backgrounded, resume when active
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (deltaPollTimer) {
        clearInterval(deltaPollTimer);
        deltaPollTimer = null;
      }
    } else {
      pollDeltaFeed();
      startDeltaPolling();
    }
  });
}

// Load older drops when user scrolls or clicks Load More button
async function loadMoreDrops() {
  if (isLoadingMore || !hasMoreDrops) return;
  isLoadingMore = true;
  const btn = document.getElementById('loadMoreBtn');
  if (btn) btn.innerHTML = '<span>⏳ Loading Older Drops...</span>';

  try {
    const offset = allDrops.length;
    let res;
    try {
      res = await fetchWithTimeout(`${BACKEND_URL}/api/feed?limit=150&offset=${offset}`, { cache: 'default' }, 8000);
      if (!res.ok) throw new Error('Render load more failed');
    } catch (e) {
      res = await fetchWithTimeout(`https://bsc-ia-scan.onrender.com/api/feed?limit=150&offset=${offset}`, { cache: 'default' }, 8000);
    }

    if (!res.ok) throw new Error('Failed to load more');
    const data = await res.json();
    if (data && data.projects && data.projects.length > 0) {
      data.projects.forEach(p => {
        const c = (p.contract || '').toLowerCase();
        if (c) knownContracts.add(c);
        if (isSniperReady(p) && c) knownSniperReady.add(c);
        allDrops.push(p);
      });
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
    try {
      res = await fetchWithTimeout(`${BACKEND_URL}/api/feed?limit=150`, { headers: feedHeaders }, 8000);
      if (res.status === 304) {
        // Feed unchanged, zero bytes transferred
        return;
      }
      if (!res.ok) throw new Error('Render /api/feed failed');
    } catch (e) {
      try {
        res = await fetchWithTimeout(`https://bsc-ia-scan.onrender.com/api/feed?limit=150`, { headers: feedHeaders }, 8000);
        if (res.status === 304) return;
        if (!res.ok) throw new Error('Render direct /api/feed failed');
      } catch (e2) {
        source = 'Local Snapshot';
        res = await fetchWithTimeout(`feed_snapshot.json`, { cache: 'default' }, 4000);
      }
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
      if (payload.stats) globalStats = payload.stats;
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
        if (!isInitialLoad) {
          newlyFound.push(drop);
          drop._isNew = true;
        }
      }
    });

    if (!isInitialLoad && newlyFound.length > 0) {
      console.log(`%c[🚨 NEW MARKET DETECTED]`, 'color: #00ff88; font-weight: bold;', newlyFound);
      playMarketAlertSound();
      newlyFound.slice(0, 3).forEach(drop => showNewMarketToast(drop));
    }

    // Check for newly qualified Sniper Ready projects (Option B fast-track trigger)
    const newlySniperReady = [];
    allDrops.forEach(drop => {
      const c = (drop.contract || '').toLowerCase();
      const ready = isSniperReady(drop);
      if (ready && c && !knownSniperReady.has(c)) {
        knownSniperReady.add(c);
        if (!isInitialLoad) {
          newlySniperReady.push(drop);
        }
      } else if (!ready && c && knownSniperReady.has(c)) {
        knownSniperReady.delete(c);
      }
    });

    if (!isInitialLoad && newlySniperReady.length > 0) {
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

    // Header Counters with Global Pre-Calculated Stats
    const rEl = document.getElementById('radarCount');
    if (rEl) rEl.textContent = globalStats ? globalStats.total : allDrops.length;
    const sEl = document.getElementById('smartCount');
    if (sEl) {
      sEl.textContent = globalStats ? globalStats.smart : allDrops.filter(d => (d.smart_wallets && d.smart_wallets.length > 0) || (d.smart_minters && d.smart_minters.length > 0)).length;
    }
    updateSniperReadyCount();
    updateNftSurgeCount();
    updateProxyCount();
    updateLaunchpadCount();
    updateSecondaryTabCount();

    // Re-hydrate user balances if wallet is connected
    if (connectedWallet && userOwnedContracts.size > 0) {
      allDrops.forEach(drop => {
        const c = (drop.contract || '').toLowerCase();
        const raw = userOwnedContracts.get(c);
        if (raw && raw > 0n) {
          const std = String(drop.standard || '');
          const isNft = std.includes('721') || std.includes('NFT') || Boolean(drop.is_nft);
          let dec = 18;
          if (std.includes('8 decimals')) dec = 8;
          drop.user_balance = isNft ? Number(raw) : (Number(raw) / (10 ** dec));
          drop.user_balance_wei = raw;
        } else {
          drop.user_balance = 0;
          drop.user_balance_wei = 0n;
        }
      });
      updatePortfolioBannerUI();
    } else {
      const pCountEl = document.getElementById('portfolioCount');
      if (pCountEl) pCountEl.textContent = '0';
    }

    renderStats(allDrops);
    renderTable();

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

    // Preload all historical smart drops in the background so followed & activity views are instant
    setTimeout(() => {
      loadSmartDrops().then(() => {
        if (activeFilter === 'FOLLOWED' || searchQuery.trim() || activeTab === 'SMART') {
          renderTable();
        }
      });
    }, 100);

    // Preload all historical secondary market runners across entire database
    setTimeout(() => {
      loadSecondaryDrops().then(() => {
        updateSecondaryTabCount();
        updateSecondaryBannerUI();
        updateDailyPulseUI();
        if (activeTab === 'SECONDARY') {
          renderTable();
        }
      });
    }, 200);

  } catch (err) {
    console.error('Failed to load drops:', err);
  }
}

// ========================================================
// Manual Tab Sequence Customization & Drag-and-Drop
// ========================================================
const DEFAULT_PILLS_SEQUENCE = [
  'ALL',
  'LAUNCHPAD',
  'SNIPER_READY',
  'NFT_SURGE',
  'PROXY',
  'FAVORITES',
  'FOLLOWED',
  'FREE',
  'SMART',
  'pillSmartLeaderboard',
  'LP',
  'ACTIVE'
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
    item.innerHTML = `
      <div class="reorder-item-left">
        <span class="reorder-item-idx">#${index + 1}</span>
        <span class="reorder-item-name">${label}</span>
      </div>
      <div class="reorder-item-actions">
        <button class="reorder-btn" title="Move to Top" ${isFirst ? 'disabled' : ''} onclick="movePillToTop('${key}')">⬆ Top</button>
        <button class="reorder-btn" title="Move Up" ${isFirst ? 'disabled' : ''} onclick="movePill('${key}', -1)">▲ Up</button>
        <button class="reorder-btn" title="Move Down" ${isLast ? 'disabled' : ''} onclick="movePill('${key}', 1)">▼ Down</button>
      </div>
    `;
    listEl.appendChild(item);
  });
}

function movePill(key, delta) {
  const seq = getCurrentPillSequence();
  const idx = seq.indexOf(key);
  if (idx === -1) return;
  const targetIdx = idx + delta;
  if (targetIdx < 0 || targetIdx >= seq.length) return;

  const temp = seq[idx];
  seq[idx] = seq[targetIdx];
  seq[targetIdx] = temp;

  applyPillSequence(seq);
  savePillSequence(seq);
  renderReorderModalList();
}

function movePillToTop(key) {
  const seq = getCurrentPillSequence();
  const idx = seq.indexOf(key);
  if (idx <= 0) return;
  seq.splice(idx, 1);
  seq.unshift(key);

  applyPillSequence(seq);
  savePillSequence(seq);
  renderReorderModalList();
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
  const nativeSelect = document.getElementById('sortSelect');
  if (nativeSelect) nativeSelect.value = val;

  const label = document.getElementById('customSortLabel');
  const options = document.querySelectorAll('.custom-option');

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
  const options = document.querySelectorAll('.custom-option');
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

// Initialize Application
async function init() {
  try {
    // Initialize persistent top gainers memory and sync historical seed
    loadSavedTopGainers();
    syncSeededTopGainers();

    // Initialize smart hunters & followed wallets
    loadFollowedWallets();
    syncSeededSmartHunters();

    // Initialize customized tab/pill sequence & drag-and-drop
    const savedPillsSeq = loadSavedPillSequence();
    applyPillSequence(savedPillsSeq);
    setupFilterPillsDragAndDrop();

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

    // Donation buttons
    const donateHeader = document.getElementById('donateHeaderBtn');
    if (donateHeader) {
      donateHeader.addEventListener('click', () => copyDonationAddress(donateHeader, 'header'));
    }
    const footerDonate = document.getElementById('footerDonate');
    if (footerDonate) {
      footerDonate.addEventListener('click', () => copyDonationAddress(footerDonate, 'footer'));
    }

    // Unlock Web Audio context & request push notifications on user's first click or keystroke anywhere
    const unlockAudio = () => {
      if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume();
      }
      requestNotificationPermission();
    };
    document.addEventListener('click', unlockAudio, { once: true });
    document.addEventListener('keydown', unlockAudio, { once: true });

    // Infinite scroll: smoothly append batches and load older drops as user scrolls down
    const handleInfiniteScroll = (target) => {
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
    };

    window.addEventListener('scroll', () => handleInfiniteScroll(window), { passive: true });
    const tableWrap = document.querySelector('.table-wrapper');
    if (tableWrap) {
      tableWrap.addEventListener('scroll', () => handleInfiniteScroll(tableWrap), { passive: true });
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
        activeTab = btn.getAttribute('data-tab');

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
        renderTable();
      });
    });

    // Global keyboard shortcuts (1: Radar, 2: Secondary Market, 3: Launchpads, 4: Smart Alpha, 5: Wallets, 6: Portfolio)
    document.addEventListener('keydown', (e) => {
      if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
      if (e.key === '1') {
        const b = document.getElementById('tabRadar');
        if (b) b.click();
      } else if (e.key === '2') {
        const b = document.getElementById('tabSecondary');
        if (b) b.click();
      } else if (e.key === '3') {
        const b = document.getElementById('tabLaunchpad');
        if (b) b.click();
      } else if (e.key === '4') {
        const b = document.getElementById('tabSmart');
        if (b) b.click();
      } else if (e.key === '5') {
        const b = document.getElementById('tabWallets');
        if (b) b.click();
      } else if (e.key === '6') {
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
        activeFilter = btn.getAttribute('data-filter');
        if (activeFilter === 'FOLLOWED' && !hasLoadedSmartDrops) {
          await loadSmartDrops();
        }
        if (typeof window !== 'undefined' && window.va) {
          window.va('event', { name: 'filter_changed', data: { filter: activeFilter } });
        }
        renderTable();
      });
    });

    // Search input & controls
    const searchInput = document.getElementById('searchInput');
    const searchClearBtn = document.getElementById('searchClearBtn');

    if (searchInput) {
      searchInput.addEventListener('input', async e => {
        searchQuery = e.target.value;
        if (searchClearBtn) {
          searchClearBtn.style.display = searchQuery ? 'inline-flex' : 'none';
        }
        if (!hasLoadedSmartDrops && (searchQuery.includes('.ai') || searchQuery.startsWith('0x') || searchQuery.length >= 3)) {
          await loadSmartDrops();
        }
        renderTable();
      });

      searchInput.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
          if (searchInput.value) {
            searchInput.value = '';
            searchQuery = '';
            if (searchClearBtn) searchClearBtn.style.display = 'none';
            renderTable();
          }
          searchInput.blur();
        }
      });
    }

    if (searchClearBtn) {
      searchClearBtn.addEventListener('click', () => {
        if (searchInput) {
          searchInput.value = '';
          searchQuery = '';
          searchClearBtn.style.display = 'none';
          renderTable();
          searchInput.focus();
        }
      });
    }

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

    // Sort select fallback
    const sortSel = document.getElementById('sortSelect');
    if (sortSel) {
      sortSel.addEventListener('change', e => {
        setSortMode(e.target.value);
      });
    }

    // Close modal on backdrop click
    const modal = document.getElementById('inspectModal');
    modal.addEventListener('click', e => {
      if (e.target === modal) modal.close();
    });

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
    }

    const syncModal = document.getElementById('syncModal');
    if (syncModal) {
      syncModal.addEventListener('click', e => {
        if (e.target === syncModal) syncModal.close ? syncModal.close() : syncModal.removeAttribute('open');
      });
    }

    // Check URL parameters (e.g. ?wallet=0x...)
    checkUrlParams();

    // Initialize 24h Daily Pulse Hero Card
    initDailyPulseHero();

    // Auto-reconnect previously authorized wallet
    tryAutoReconnect();

    // Start cloud feed fetch & periodic background sync (non-blocking so UI is instant)
    fetchDrops().catch(err => console.warn('Initial fetchDrops hitch:', err));
    setInterval(fetchDrops, 300000);

    // Poll latest block
    pollLatestBlock();
    setInterval(pollLatestBlock, 10000);

  } catch (err) {
    console.error('Failed to load history.json:', err);
  }
}

if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', init);
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
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
    computeStats,
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
    pollDeltaFeed,
    requestNotificationPermission,
    sendDesktopNotification,
    buildMintTxParams,
    executeWeb3Mint,
    getInjectedProvider,
    openInspectModalByContract,
    renderSmartStreamTicker,
    isValidTopMarketItem,
    updateSavedTopMarkets,
    loadSavedTopGainers,
    saveSavedTopGainers,
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
    setSearchQuery: (q) => { searchQuery = q; },
    renderWalletsTabLeaderboard,
    setWalletsTabFilter,
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
    setupFilterPillsDragAndDrop,
    showToast,
    copyDonationAddress,
    DONATION_WALLET,
    setSortMode,
    getSortMode: () => sortMode,
    renderTable,
    getDexScreenerUrl,
    isSecondaryMarketAsset,
    compute24hPulseMetrics,
    updateDailyPulseUI,
    toggleDailyPulseHero,
    initDailyPulseHero
  };
}

