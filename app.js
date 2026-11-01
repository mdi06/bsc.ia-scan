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

// Favorites / Watchlist System (persisted in localStorage + auto-stars owned tokens)
const FAVORITES_STORAGE_KEY = 'agent_id_favorites';
let favorites = new Set();
try {
  const stored = (typeof localStorage !== 'undefined') ? JSON.parse(localStorage.getItem(FAVORITES_STORAGE_KEY) || '[]') : [];
  if (Array.isArray(stored)) {
    favorites = new Set(stored.map(s => String(s).toLowerCase()));
  }
} catch (e) {
  favorites = new Set();
}

function saveFavorites() {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify([...favorites]));
    }
  } catch (e) {}
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
  const favEl = document.getElementById('favCount');
  if (!favEl) return;
  const count = allDrops.filter(d => isFavorited(d.contract, d)).length;
  favEl.textContent = count;
}

// Audio Alert & Discovery Tracking
let audioCtx = null;
let soundEnabled = (typeof localStorage !== 'undefined') ? (localStorage.getItem('agent_sound_alerts') !== 'false') : false;
const knownContracts = new Set();
const knownSniperReady = new Set();
let isInitialLoad = true;

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

  // Auto-dismiss after 5s
  setTimeout(() => {
    toast.classList.add('fade-out');
    setTimeout(() => toast.remove(), 400);
  }, 5000);
}

// Generic feedback toast
function showToast(title, desc, icon = '💎') {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = 'market-toast';
  toast.innerHTML = `
    <div class="toast-icon">${icon}</div>
    <div class="toast-content">
      <div class="toast-title">${escapeHtml(title)}</div>
      <div class="toast-desc">${escapeHtml(desc)}</div>
    </div>
  `;
  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('fade-out');
    setTimeout(() => toast.remove(), 400);
  }, 4500);
}

// Copy Donation Wallet Address Helper
function copyDonationAddress(btn) {
  navigator.clipboard.writeText(DONATION_WALLET);
  if (btn) {
    const orig = btn.innerHTML;
    btn.innerHTML = '✓ Copied!';
    btn.style.color = 'var(--color-green)';
    setTimeout(() => {
      btn.innerHTML = orig;
      btn.style.color = '';
    }, 2000);
  }
  showToast('💎 DONATION WALLET COPIED', `${DONATION_WALLET} (BNB / EVM) · Thank you for supporting!`, '💎');
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

// Format numbers nicely (e.g. 1.2K, 500)
function formatCount(val) {
  const n = Number(val || 0);
  if (!isFinite(n) || n >= 1e20) return '∞';
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return n.toLocaleString();
}

// Compute Individual Project Profit and Maintain ATH High-Water Mark
function computeItemProfit(item, bnbPriceUsd = bnbPrice) {
  const c = String(item.contract || '').toLowerCase();
  const sym = String(item.symbol || '').toUpperCase();
  const isPegged = c === '0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c' || c === '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c' || c === '0x55d398326f99059ff775485246999027b3197955' || ['BTCB', 'WBNB', 'USDT', 'USDC', 'BUSD'].includes(sym);
  if (isPegged) {
    return { secUsd: 0, mintUsd: 0, profit: 0, athProfit: 0, athPrice: 0, multiplier: 0, isSold: false, isFree: false };
  }

  const flUsd = Number(item.floor_price_usd || 0);
  const flBnb = Number(item.floor_price_bnb || 0);
  const tkUsd = Number(item.token_price_usd || item.market_price_usd || 0);
  const secUsd = flUsd > 0 ? flUsd : (flBnb > 0 ? flBnb * bnbPriceUsd : tkUsd);

  const pStr = String(item.mint_price_str || '').toUpperCase();
  const vWei = Number(item.mint_value_wei || 0);
  const isFree = Boolean(item.has_free_mint || pStr.includes('FREE') || (vWei === 0 && Number(item.mint_price_bnb || 0) === 0 && !pStr.includes('PAID') && !pStr.includes('DEX') && !pStr.includes('TBD')));
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

// Compute Macro Stats
function computeStats(drops) {
  let cappedTotal = 0;
  let soldOutCount = 0;
  let nearCapCount = 0;
  let warmCapCount = 0;
  const gainers = [];

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

    const pInfo = computeItemProfit(item, bnbPrice);
    if (pInfo.secUsd > 0 || pInfo.athProfit > 0) {
      gainers.push({
        item,
        name: item.name || 'Unknown',
        symbol: item.symbol || '',
        contract: item.contract || '',
        secUsd: pInfo.secUsd,
        mintUsd: pInfo.mintUsd,
        profit: pInfo.profit,
        athPrice: pInfo.athPrice,
        athProfit: pInfo.athProfit,
        multiplier: pInfo.multiplier,
        isSold: pInfo.isSold,
        isFree: pInfo.isFree,
        holders: Number(item.holders || item.total_minted || 0)
      });
    }
  });

  const fillRatePct = cappedTotal > 0 ? (soldOutCount / cappedTotal) * 100 : 0;
  const soldGainers = gainers.filter(g => g.isSold).sort((a, b) => Math.max(b.athProfit, b.profit) - Math.max(a.athProfit, a.profit));
  const allGainers = gainers.sort((a, b) => Math.max(b.athProfit, b.profit) - Math.max(a.athProfit, a.profit));

  const topGainer = soldGainers[0] || allGainers[0] || null;
  const runnerUp = soldGainers[1] || allGainers[1] || null;

  return {
    cappedTotal,
    soldOutCount,
    fillRatePct,
    nearCapCount,
    warmCapCount,
    topGainer,
    runnerUp,
    allGainers,
    soldGainers
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
    const tgMint = tg.isFree || tg.mintUsd === 0 ? 'FREE' : `$${tg.mintUsd.toFixed(4)}`;
    const tgAth = Math.max(tg.athPrice, tg.secUsd);
    const tgNet = Math.max(tg.athProfit, tg.profit);
    document.getElementById('topGainerName').textContent = `${tg.name} (${tg.symbol})`;
    document.getElementById('topGainerDetails').textContent = `Mint: ${tgMint} ➔ ATH: $${tgAth.toFixed(4)}`;
    document.getElementById('topGainerNet').textContent = `Net: +$${tgNet.toFixed(4)} / token`;
  }

  if (stats.runnerUp) {
    const ru = stats.runnerUp;
    const ruAth = Math.max(ru.athPrice, ru.secUsd);
    document.getElementById('runnerUpDetails').textContent = `│ 🥈 Runner-Up: ${ru.name} (${ru.symbol}) [$${ruAth.toFixed(4)}]`;
  }
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
      checklist.push(`<span style="color:var(--color-yellow);">✖ Secondary Market: DEX LP already active ($${lp.toLocaleString(undefined, {maximumFractionDigits: 0})}) → Trade on DEX</span>`);
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
    const cnt = allDrops.filter(d => isSniperReady(d)).length;
    el.textContent = cnt;
  }
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
    const cnt = allDrops.filter(d => Boolean(d.is_nft_surge)).length;
    el.textContent = cnt;
  }
}

function updateProxyCount() {
  if (typeof document === 'undefined') return;
  const el = document.getElementById('proxyCount');
  if (el) {
    const cnt = allDrops.filter(d => Boolean(d.is_proxy_clone)).length;
    el.textContent = cnt;
  }
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
  if (activeTab === 'SMART') {
    list = list.filter(d => (d.smart_wallets && d.smart_wallets.length > 0) || (d.smart_minters && d.smart_minters.length > 0));
  } else if (activeTab === 'PORTFOLIO') {
    list = list.filter(d => (Number(d.user_balance) > 0) || (d.name && d.name.toLowerCase().includes('butterfli')));
  }

  // Toolbar filter pills
  if (activeFilter === 'SNIPER_READY') {
    list = list.filter(d => isSniperReady(d));
  } else if (activeFilter === 'NFT_SURGE') {
    list = list.filter(d => Boolean(d.is_nft_surge));
  } else if (activeFilter === 'PROXY') {
    list = list.filter(d => Boolean(d.is_proxy_clone));
  } else if (activeFilter === 'FAVORITES') {
    list = list.filter(d => isFavorited(d.contract, d));
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

  // Search
  if (searchQuery.trim()) {
    const q = searchQuery.toLowerCase().trim();
    list = list.filter(d => 
      (d.name && d.name.toLowerCase().includes(q)) ||
      (d.symbol && d.symbol.toLowerCase().includes(q)) ||
      (d.contract && d.contract.toLowerCase().includes(q))
    );
  }

  // Sort
  if (sortMode === 'PROFIT') {
    list.sort((a, b) => {
      const profitA = Math.max(Number(a.ath_profit_usd || 0), Number(a.current_profit_usd || 0));
      const profitB = Math.max(Number(b.ath_profit_usd || 0), Number(b.current_profit_usd || 0));
      return profitB - profitA;
    });
  } else if (sortMode === 'INTEREST') {
    list.sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
  } else if (sortMode === 'HOLDERS') {
    list.sort((a, b) => Number(b.holders || 0) - Number(a.holders || 0));
  } else if (sortMode === 'PROGRESS') {
    list.sort((a, b) => {
      const pctA = a.max_supply ? (Number(a.total_minted || 0) / Number(a.max_supply)) : 0;
      const pctB = b.max_supply ? (Number(b.total_minted || 0) / Number(b.max_supply)) : 0;
      return pctB - pctA;
    });
  } else if (sortMode === 'LP') {
    list.sort((a, b) => Number(b.lp_usd || 0) - Number(a.lp_usd || 0));
  } else {
    // NEWEST
    list.sort((a, b) => Number(b.block || 0) - Number(a.block || 0));
  }

  return list;
}

// Render Table Rows
function renderTable() {
  const tbody = document.getElementById('tableBody');
  tbody.innerHTML = '';
  const filtered = getFilteredDrops();

  updateFavPillCount();
  updateSniperReadyCount();
  updateNftSurgeCount();
  updateProxyCount();
  document.getElementById('footerCount').textContent = `Showing ${filtered.length} of ${allDrops.length} collections`;

  if (filtered.length === 0) {
    const tr = document.createElement('tr');
    if (activeTab === 'PORTFOLIO') {
      tr.innerHTML = `<td colspan="14" style="text-align: center; padding: 40px; color: var(--text-dim);">🎒 Your on-chain portfolio is empty. Click <strong style="color: var(--color-cyan); cursor: pointer;" onclick="document.getElementById('tabRadar').click()">1: 📡 Live Radar</strong> to discover and snipe new drops!</td>`;
    } else {
      tr.innerHTML = `<td colspan="14" style="text-align: center; padding: 30px; color: var(--text-dim);">No agentic projects match current filter [${activeFilter}] or search.</td>`;
    }
    tbody.appendChild(tr);
    return;
  }

  filtered.forEach((item, index) => {
    const tr = document.createElement('tr');
    if (item._isNew) tr.classList.add('row-new');
    tr.addEventListener('click', () => openModal(item));

    const blk = item.block ? `#${String(item.block).slice(-6)}` : '-';
    const isSm = (item.smart_wallets && item.smart_wallets.length > 0) || (item.smart_minters && item.smart_minters.length > 0);
    const isReady = isSniperReady(item);
    const smBadge = (isSm && isReady) ? `<span title="Smart Alpha & Sniper Ready">🧠🎯</span>` : (isReady ? `<span title="Sniper Ready">🎯</span>` : (isSm ? `<span title="${(item.smart_wallets || []).join(', ')}">🧠</span>` : ''));

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

    // LP / Vol
    const lpUsd = Number(item.lp_usd || 0);
    const volBnb = item.volume_bnb || item.nft_volume_bnb;
    const secVol = Number(item.secondary_vol_bnb || 0);
    let lpStr = `<span class="dim">—</span>`;
    if (lpUsd > 0) {
      lpStr = `<span style="color: var(--color-green); font-weight:600;">$${lpUsd.toLocaleString(undefined, {maximumFractionDigits: 0})}</span>`;
    } else if (item.is_nft_surge && secVol > 0) {
      lpStr = `<span style="color: var(--color-magenta); font-weight:700;" title="${item.secondary_sales_count || 0} Element sales">${secVol.toFixed(3)} BNB 🦋</span>`;
    } else if (secVol > 0) {
      lpStr = `<span style="color: var(--color-cyan); font-weight:600;" title="${item.secondary_sales_count || 0} Element sales">${secVol.toFixed(3)} BNB</span>`;
    } else if (volBnb && Number(volBnb) > 0) {
      lpStr = `<span style="color: var(--color-cyan); font-weight:600;">${Number(volBnb).toFixed(2)} BNB</span>`;
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
      floorStr = `<span style="color: var(--color-green); font-weight:600;">$${tkUsd < 0.001 ? tkUsd.toFixed(6) : tkUsd.toFixed(4)}</span>`;
    }

    const athProf = Number(item.ath_profit_usd || 0);
    const curProf = Number(item.current_profit_usd || 0);
    const maxProf = Math.max(athProf, curProf);
    if (maxProf > 0) {
      const pStrVal = maxProf >= 1 ? `+$${maxProf.toFixed(2)}` : `+$${maxProf.toFixed(4)}`;
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

    const isPort = (activeTab === 'PORTFOLIO');
    const isNft = std.includes('721') || std.includes('NFT');
    const cellHolders = isPort 
      ? `<span style="color: var(--color-green); font-weight:700;">Holdings: ${formatCount(item.user_balance || 1)}</span>`
      : hbStr;
    const cellFloor = isPort 
      ? `<span class="badge ${exitSignal.badge_class}" style="font-size: 11px;">${exitSignal.badge}</span>` 
      : floorStr;
    let actBtn = `<button class="inspect-btn"><span class="desktop-only">Inspect</span><span class="mobile-only">🔍 Deep Dive & Snipe CLI ➔</span></button>`;
    if (isPort) {
      if (!isNft) {
        actBtn = `<button class="inspect-btn" style="border-color: #f0b90b; color: #f0b90b; font-weight:700;"><span class="desktop-only">🥞 1-Click Exit</span><span class="mobile-only">🥞 Exit CLI ➔</span></button>`;
      } else {
        actBtn = `<button class="inspect-btn" style="border-color: var(--color-magenta); color: var(--color-magenta); font-weight:700;"><span class="desktop-only">🎨 Element List</span><span class="mobile-only">🎨 Element ➔</span></button>`;
      }
    }

    tr.innerHTML = `
      <td class="col-fav"><button class="${starClass}" onclick="toggleFavorite('${item.contract}', event)" title="${isFav ? 'Remove from favorites' : 'Add to favorites'}">${starIcon}</button></td>
      <td class="col-num">${index + 1}</td>
      <td class="col-block"><code>${blk}</code></td>
      <td class="col-sm">${smBadge}</td>
      <td class="col-project"><strong>${item.name || 'Unknown'}</strong><span class="sym">(${item.symbol || ''})</span></td>
      <td class="col-type">${stdBadge}</td>
      <td class="col-safety">${safeBadge}</td>
      <td class="col-interest">${intBadge}</td>
      <td class="col-holders">${cellHolders}</td>
      <td class="col-lp">${lpStr}</td>
      <td class="col-progress">${progBar}</td>
      <td class="col-price">${priceStr}</td>
      <td class="col-floor">${cellFloor}</td>
      <td class="col-action">${actBtn}</td>
    `;
    tbody.appendChild(tr);
  });
}

// Modal Inspection Dialog
function openModal(item) {
  const modal = document.getElementById('inspectModal');
  const content = document.getElementById('modalContent');

  const std = String(item.standard || 'ERC-721');
  const isNft = std.includes('721') || std.includes('NFT');
  const cAddr = item.contract || '';
  const name = item.name || 'Unknown';
  const sym = item.symbol || '';

  const mktUrl = isNft 
    ? getElementUrl(name, cAddr) 
    : (item.market_url || `https://pancakeswap.finance/swap?outputCurrency=${cAddr}`);

  const smList = item.smart_wallets || item.smart_minters || [];
  const sEval = getSniperEval(item);
  const isReady = sEval.is_ready;
  const rawCd = item.mint_calldata;
  const isRecv = !rawCd || rawCd === '0x' || rawCd === 'recv';
  let calldata = isRecv ? '0x' : rawCd;
  if (!isRecv && calldata.startsWith('0x836a1040') && calldata.length >= 138) {
    calldata = calldata.slice(0, 74) + '000000000000000000000000<YOUR_WALLET>' + calldata.slice(138);
  }
  const valWei = item.mint_value_wei || '0';
  const cliSnippet = `baw contract-call preview --binanceChainId 56 --from <YOUR_WALLET> --to ${cAddr} --value ${valWei} --inputData ${calldata} --json`;

  const isFav = isFavorited(cAddr, item);
  const favBtnClass = isFav ? 'modal-fav-btn starred' : 'modal-fav-btn';
  const favBtnText = isFav ? '★ Favorited' : '☆ Add to Favorites';

  const exitEval = evaluatePortfolioExit(item, bnbPrice);
  const sellCalldata = !isNft ? buildPancakeSwapSellCalldata(cAddr, 1000000000000000000n, '<YOUR_WALLET>') : '';
  const sellCliSnippet = !isNft ? `baw contract-call preview --binanceChainId 56 --from <YOUR_WALLET> --to ${PANCAKE_ROUTER_V2} --value 0 --inputData ${sellCalldata} --json` : '';

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
        <div><span class="dim">Contract:</span> <code>${cAddr}</code></div>
        <div><span class="dim">Deployer:</span> <code>${item.deployer || 'N/A'}</code></div>
        <div><span class="dim">Standard:</span> <strong>${std}</strong></div>
        <div><span class="dim">Deployment Block:</span> <code>#${item.block || 'N/A'}</code></div>
        <div><span class="dim">Total Minted:</span> <strong>${formatCount(item.total_minted)}</strong> / ${formatCount(item.max_supply) || '∞'}</div>
        <div><span class="dim">Holders:</span> <strong>${formatCount(item.holders)} unique wallets</strong></div>
      </div>
    </div>

    <div class="modal-section">
      <div class="modal-section-title">🛡️ Security & Anti-Rug Audit</div>
      <div class="modal-grid">
        <div><span class="dim">Risk Rating:</span> <strong>${item.risk_badge || item.risk_level || '🟢 Clean (SAFE)'}</strong></div>
        <div><span class="dim">Bytecode Refund:</span> ${item.security && item.security.has_refund ? '<span style="color: var(--color-green); font-weight:600;">🛡️ Verified (' + (item.security.refund_methods || []).slice(0,1).join('') + ')</span>' : (Number(item.mint_price_bnb || 0) > 0 ? '<span style="color: var(--color-yellow); font-weight:600;">⚠️ No Refund Function</span>' : '<span class="dim">N/A (Free Mint)</span>')}</div>
        <div><span class="dim">Deployer Profile:</span> ${item.security && item.security.is_serial_dev ? '<span style="color: var(--color-red); font-weight:700;">🚨 Serial Dev (' + item.security.serial_count + ' drops)</span>' : (item.security && item.security.is_fresh_dev ? '<span style="color: var(--color-yellow);">🟡 Fresh Wallet</span>' : '<span style="color: var(--color-green);">🟢 Established Nonce</span>')}</div>
        <div><span class="dim">PancakeSwap LP:</span> ${item.lp_usd ? '<span style="color: var(--color-green); font-weight:700;">$' + Number(item.lp_usd).toLocaleString(undefined, {maximumFractionDigits: 0}) + '</span>' : '<span class="dim">No LP Pool detected</span>'}</div>
      </div>
      ${item.risk_flags && item.risk_flags.length > 0 ? `
        <div style="margin-top: 8px; font-size: 11px;">
          <span class="dim">Audit Flags:</span>
          ${item.risk_flags.map(f => `<span class="badge ${f.includes('🚨') || f.includes('Honeypot') || f.includes('Serial') || f.includes('Drain') ? 'badge-red' : 'badge-yellow'}" style="margin: 2px;">${f}</span>`).join(' ')}
        </div>
      ` : ''}
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
      <div class="modal-section-title">🧠 Smart Money Activity (${smList.length} Tracked Wallets)</div>
      <div style="color: var(--color-cyan); font-size: 12px; margin-top: 4px;">
        ${smList.slice(0, 10).map(w => `<span class="badge badge-dim" style="margin: 2px;">${w}</span>`).join(' ')}
        ${smList.length > 10 ? `<span class="dim">(+${smList.length - 10} more)</span>` : ''}
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
        <a href="${mktUrl}" target="_blank" rel="noopener" class="modal-btn btn-element">🛒 ${isNft ? 'Element NFT Collection' : 'PancakeSwap / DEX'}</a>
        ${!isNft ? `<a href="https://pancakeswap.finance/swap?inputCurrency=${cAddr}&outputCurrency=BNB" target="_blank" rel="noopener" class="modal-btn" style="background: rgba(240, 185, 11, 0.15); border: 1px solid #f0b90b; color: #f0b90b;">🥞 Swap to BNB (PancakeSwap)</a>` : ''}
        <a href="https://bscscan.com/address/${cAddr}" target="_blank" rel="noopener" class="modal-btn btn-bscscan">🔍 View on BscScan</a>
        ${item.website ? `<a href="${item.website}" target="_blank" rel="noopener" class="modal-btn btn-bscscan">🌐 Website</a>` : ''}
        ${item.twitter ? `<a href="${item.twitter}" target="_blank" rel="noopener" class="modal-btn btn-bscscan">🐦 Twitter / X</a>` : ''}
      </div>
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
        <button class="copy-btn" onclick="copyDonationAddress(this)">Copy Address</button>
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

  const stats = computeStats(allDrops);
  const list = filterMode === 'SOLD' ? stats.soldGainers : stats.allGainers;

  let rowsHtml = '';
  if (!list || list.length === 0) {
    rowsHtml = `<tr><td colspan="7" style="text-align:center; padding: 24px; color: var(--text-dim);">No post-mint gains recorded yet. As secondary trades settle on Element and PancakeSwap, top performers will appear here.</td></tr>`;
  } else {
    list.forEach((g, idx) => {
      const rankNum = idx + 1;
      let rankBadge = `<span class="hof-rank">${rankNum}</span>`;
      if (rankNum === 1) rankBadge = `<span class="hof-rank hof-rank-1">🥇 #1</span>`;
      else if (rankNum === 2) rankBadge = `<span class="hof-rank hof-rank-2">🥈 #2</span>`;
      else if (rankNum === 3) rankBadge = `<span class="hof-rank hof-rank-3">🥉 #3</span>`;
      else rankBadge = `<span class="hof-rank" style="color: var(--text-dim);">#${rankNum}</span>`;

      const mintCostStr = g.isFree || g.mintUsd === 0 ? '<span style="color: var(--color-green); font-weight:700;">FREE</span>' : `$${g.mintUsd.toFixed(4)}`;
      const athPriceStr = `$${g.athPrice.toFixed(4)}`;
      const athProfitStr = `+$${g.athProfit.toFixed(4)}`;
      const multStr = g.multiplier ? `<span class="badge badge-yellow" style="font-size: 10px;">${g.multiplier}x</span>` : (g.isFree ? `<span class="badge badge-green" style="font-size: 10px;">FREE ALPHA</span>` : '');

      const curPriceStr = g.secUsd > 0 ? `$${g.secUsd.toFixed(4)}` : '<span class="dim">—</span>';
      const curNetStr = g.profit > 0 ? `<span style="color: var(--color-green); font-weight:600;">+$${g.profit.toFixed(4)}</span>` : '<span class="dim">—</span>';

      const statusBadge = g.isSold 
        ? `<span class="badge badge-magenta" style="font-size: 10px;">Sold Out</span>` 
        : `<span class="badge badge-cyan" style="font-size: 10px;">Active</span>`;

      // Marketplace / DEX links
      const std = String(g.item.standard || '');
      const isNft = std.includes('721') || std.includes('NFT');
      const cleanName = (g.name || '').replace(/\([^)]*\)/g, '').trim();
      const slug = cleanName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
      const elementUrl = g.item.market_url || (slug ? `https://element.market/collections/${slug}` : `https://element.market/collections/${g.contract}`);
      const dexUrl = g.item.pair_url || `https://pancakeswap.finance/swap?outputCurrency=${g.contract}`;
      const bscUrl = `https://bscscan.com/address/${g.contract}`;

      let actionLinks = '';
      if (isNft) {
        actionLinks += `<a href="${elementUrl}" target="_blank" rel="noopener" class="hof-btn hof-btn-mkt" title="Open on Element NFT Market">🦋 Element</a>`;
      } else {
        actionLinks += `<a href="${dexUrl}" target="_blank" rel="noopener" class="hof-btn hof-btn-dex" title="Trade on PancakeSwap / DexScreener">🥞 DEX</a>`;
      }
      actionLinks += `<a href="${bscUrl}" target="_blank" rel="noopener" class="hof-btn hof-btn-scan" title="View BSCScan">🔍 BscScan</a>`;

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
        <button class="hof-filter-btn ${filterMode === 'SOLD' ? 'active' : ''}" id="hofFilterSold">🔒 Sold Out Only (${stats.soldGainers.length})</button>
      </div>
      <div class="hof-summary-pill">
        📈 Tracking ${list.length} Profitable Drops │ Top Gain: +$${(list[0] ? Math.max(list[0].athProfit, list[0].profit).toFixed(4) : '0.0000')} / token
      </div>
    </div>

    <div class="hof-table-wrap">
      <table class="hof-table">
        <thead>
          <tr>
            <th style="width: 60px;">Rank</th>
            <th>Collection / Token</th>
            <th style="width: 100px;">Mint Price</th>
            <th style="width: 140px;">Peak ATH / Net</th>
            <th style="width: 120px;">Current Price</th>
            <th style="width: 110px;">Status</th>
            <th style="width: 150px;">Market Links</th>
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

// Fetch drops from 24/7 Fly.io cloud scanner with local fallback
async function fetchDrops() {
  try {
    let res;
    let source = 'Fly.io';
    try {
      res = await fetch(`https://agent-id-monitor.fly.dev/history.json?_t=${Date.now()}`, { cache: 'no-store' });
      if (!res.ok) throw new Error('Fly fetch failed');
    } catch (e) {
      source = 'Local Cache';
      res = await fetch(`history.json?_t=${Date.now()}`, { cache: 'no-store' });
    }
    allDrops = await res.json();

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

    const smartDrops = allDrops.filter(d => (d.smart_wallets && d.smart_wallets.length > 0) || (d.smart_minters && d.smart_minters.length > 0));
    const portDrops = allDrops.filter(d => (Number(d.user_balance) > 0) || (d.name && d.name.toLowerCase().includes('butterfli')));
    document.getElementById('radarCount').textContent = allDrops.length;
    document.getElementById('smartCount').textContent = smartDrops.length;
    const pCountEl = document.getElementById('portfolioCount');
    if (pCountEl) pCountEl.textContent = portDrops.length;

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
  } catch (err) {
    console.error('Failed to load drops:', err);
  }
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
      const textNode = opt.querySelector('span');
      const text = textNode ? textNode.textContent : val;

      options.forEach(o => {
        o.classList.remove('selected');
        o.setAttribute('aria-selected', 'false');
      });
      opt.classList.add('selected');
      opt.setAttribute('aria-selected', 'true');

      if (label) label.textContent = text;
      sortMode = val;
      if (nativeSelect) nativeSelect.value = val;

      wrapper.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');

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
        if (soundEnabled) {
          playMarketAlertSound(); // play quick preview chime
        }
      });
    }

    // Donation buttons
    const donateHeader = document.getElementById('donateHeaderBtn');
    if (donateHeader) {
      donateHeader.addEventListener('click', () => copyDonationAddress(donateHeader));
    }
    const footerDonate = document.getElementById('footerDonate');
    if (footerDonate) {
      footerDonate.addEventListener('click', () => copyDonationAddress(footerDonate));
    }

    // Unlock Web Audio context on user's first click or keystroke anywhere
    const unlockAudio = () => {
      if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume();
      }
    };
    document.addEventListener('click', unlockAudio, { once: true });
    document.addEventListener('keydown', unlockAudio, { once: true });

    await fetchDrops();

    // Auto-refresh from cloud scanner every 15 seconds
    setInterval(fetchDrops, 15000);

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
        renderTable();
      });
    });

    // Global keyboard shortcuts (1: Radar, 2: Smart Alpha, 3: Portfolio)
    document.addEventListener('keydown', (e) => {
      if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
      if (e.key === '1') {
        const b = document.getElementById('tabRadar');
        if (b) b.click();
      } else if (e.key === '2') {
        const b = document.getElementById('tabSmart');
        if (b) b.click();
      } else if (e.key === '3') {
        const b = document.getElementById('tabPortfolio');
        if (b) b.click();
      }
    });

    // Filter pills
    document.querySelectorAll('.pill').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.pill').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activeFilter = btn.getAttribute('data-filter');
        renderTable();
      });
    });

    // Search input
    document.getElementById('searchInput').addEventListener('input', e => {
      searchQuery = e.target.value;
      renderTable();
    });

    // Custom Cyberpunk Sort Dropdown
    setupCustomDropdown();

    // Sort select fallback
    const sortSel = document.getElementById('sortSelect');
    if (sortSel) {
      sortSel.addEventListener('change', e => {
        sortMode = e.target.value;
        renderTable();
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
    computeItemProfit,
    computeStats,
    openHallOfFameModal,
    PANCAKE_ROUTER_V2,
    WBNB_ADDRESS
  };
}

