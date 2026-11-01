// BNB Chain .ai Agent ID Suite - Client Web Application

const DONATION_WALLET = '0xa15BB4AEdC1b3426159C3cACf8548Cf09506A20a';

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
  const stored = JSON.parse(localStorage.getItem(FAVORITES_STORAGE_KEY) || '[]');
  if (Array.isArray(stored)) {
    favorites = new Set(stored.map(s => String(s).toLowerCase()));
  }
} catch (e) {
  favorites = new Set();
}

function saveFavorites() {
  try {
    localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify([...favorites]));
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
window.toggleModalFav = toggleModalFav;

function updateFavPillCount() {
  const favEl = document.getElementById('favCount');
  if (!favEl) return;
  const count = allDrops.filter(d => isFavorited(d.contract, d)).length;
  favEl.textContent = count;
}

// Audio Alert & Discovery Tracking
let audioCtx = null;
let soundEnabled = localStorage.getItem('agent_sound_alerts') !== 'false'; // default ON
const knownContracts = new Set();
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
window.copyDonationAddress = copyDonationAddress;

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
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return n.toLocaleString();
}

// Compute Macro Stats
function computeStats(drops) {
  let cappedTotal = 0;
  let soldOutCount = 0;
  let nearCapCount = 0;
  let warmCapCount = 0;
  const gainers = [];

  drops.forEach(item => {
    const ms = Number(item.max_supply || 0);
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

    const flUsd = Number(item.floor_price_usd || 0);
    const flBnb = Number(item.floor_price_bnb || 0);
    const tkUsd = Number(item.token_price_usd || item.market_price_usd || 0);
    const secUsd = flUsd > 0 ? flUsd : (flBnb > 0 ? flBnb * bnbPrice : tkUsd);

    if (secUsd > 0) {
      const pStr = String(item.mint_price_str || '').toUpperCase();
      const vWei = Number(item.mint_value_wei || 0);
      const isFree = Boolean(item.has_free_mint || pStr.includes('FREE') || (vWei === 0 && Number(item.mint_price_bnb || 0) === 0 && !pStr.includes('PAID') && !pStr.includes('DEX')));
      const mintUsd = isFree ? 0 : (vWei > 0 ? (vWei / 1e18) * bnbPrice : Number(item.mint_price_bnb || 0) * bnbPrice);
      const profit = secUsd - mintUsd;
      const isSold = Boolean(ms > 0 && tm >= ms);

      gainers.push({
        item,
        name: item.name || 'Unknown',
        symbol: item.symbol || '',
        secUsd,
        mintUsd,
        profit,
        isSold,
        isFree
      });
    }
  });

  const fillRatePct = cappedTotal > 0 ? (soldOutCount / cappedTotal) * 100 : 0;
  const soldGainers = gainers.filter(g => g.isSold).sort((a, b) => b.profit - a.profit);
  const allGainers = gainers.sort((a, b) => b.profit - a.profit);

  const topGainer = soldGainers[0] || allGainers[0] || null;
  const runnerUp = soldGainers[1] || allGainers[1] || null;

  return {
    cappedTotal,
    soldOutCount,
    fillRatePct,
    nearCapCount,
    warmCapCount,
    topGainer,
    runnerUp
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
    document.getElementById('topGainerName').textContent = `${tg.name} (${tg.symbol})`;
    document.getElementById('topGainerDetails').textContent = `Mint: ${tgMint} ➔ Secondary: $${tg.secUsd.toFixed(4)}`;
    document.getElementById('topGainerNet').textContent = `Net: +$${tg.profit.toFixed(4)} / token`;
  }

  if (stats.runnerUp) {
    const ru = stats.runnerUp;
    document.getElementById('runnerUpDetails').textContent = `│ 🥈 Runner-Up: ${ru.name} (${ru.symbol}) [$${ru.secUsd.toFixed(4)}]`;
  }
}

// Filter and Sort Drops
function getFilteredDrops() {
  let list = [...allDrops];

  // Tab filtering
  if (activeTab === 'SMART') {
    list = list.filter(d => (d.smart_wallets && d.smart_wallets.length > 0) || (d.smart_minters && d.smart_minters.length > 0));
  }

  // Toolbar filter pills
  if (activeFilter === 'FAVORITES') {
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
  if (sortMode === 'INTEREST') {
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
  document.getElementById('footerCount').textContent = `Showing ${filtered.length} of ${allDrops.length} collections`;

  if (filtered.length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="14" style="text-align: center; padding: 30px; color: var(--text-dim);">No agentic projects match current filter [${activeFilter}] or search.</td>`;
    tbody.appendChild(tr);
    return;
  }

  filtered.forEach((item, index) => {
    const tr = document.createElement('tr');
    if (item._isNew) tr.classList.add('row-new');
    tr.addEventListener('click', () => openModal(item));

    const blk = item.block ? `#${String(item.block).slice(-6)}` : '-';
    const isSm = (item.smart_wallets && item.smart_wallets.length > 0) || (item.smart_minters && item.smart_minters.length > 0);
    const smBadge = isSm ? `<span title="${(item.smart_wallets || []).join(', ')}">🧠</span>` : '';

    // Standard badge
    const std = String(item.standard || 'ERC-721');
    let stdBadge = `<span style="color: var(--color-magenta); font-weight:600;">ERC-721</span>`;
    if (std.includes('BEP-20')) stdBadge = `<span style="color: var(--color-cyan); font-weight:600;">BEP-20</span>`;
    else if (std.includes('Fair-Mint')) stdBadge = `<span style="color: var(--color-yellow); font-weight:600;">Fair-Mint</span>`;

    // Safety badge
    const rLvl = String(item.risk_level || 'SAFE').toUpperCase();
    const rBdg = String(item.risk_badge || '').toUpperCase();
    let safeBadge = `<span style="color: var(--color-green); font-weight:600;">🟢 Clean</span>`;
    if (rBdg.includes('HONEYPOT') || rLvl === 'RUG') {
      safeBadge = `<span class="badge badge-red">🚨 Honeypot</span>`;
    } else if (rBdg.includes('HIGH') || rLvl === 'HIGH') {
      safeBadge = `<span class="badge badge-yellow">🔴 HighRisk</span>`;
    } else if (rBdg.includes('MED') || rLvl === 'MED') {
      safeBadge = `<span style="color: var(--color-yellow); font-weight:600;">🟡 Med Risk</span>`;
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
    let lpStr = `<span class="dim">—</span>`;
    if (lpUsd > 0) {
      lpStr = `<span style="color: var(--color-green); font-weight:600;">$${lpUsd.toLocaleString(undefined, {maximumFractionDigits: 0})}</span>`;
    } else if (volBnb && Number(volBnb) > 0) {
      lpStr = `<span style="color: var(--color-cyan); font-weight:600;">${Number(volBnb).toFixed(2)} BNB</span>`;
    }

    // Progress Bar
    const ms = Number(item.max_supply || 0);
    const tm = Number(item.total_minted || 0);
    let pct = ms > 0 ? (tm / ms) * 100 : 0;
    pct = Math.min(100, Math.max(0, pct));

    let progTag = 'MINT';
    let progColor = 'var(--color-cyan)';
    if (ms > 0 && tm >= ms) {
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

    const progBar = `
      <div class="progress-cell">
        <span class="prog-tag" style="color: ${progColor}">${progTag} ${pct.toFixed(0)}%</span>
        <div class="prog-bar-container">
          <div class="prog-bar-fill" style="width: ${pct}%; background-color: ${progColor};"></div>
        </div>
        <span class="prog-ratio">${formatCount(tm)}/${ms > 0 ? formatCount(ms) : '∞'}</span>
      </div>
    `;

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
      floorStr = `<span style="color: var(--color-cyan); font-weight:600;">${flBnb} BNB</span>`;
    } else if (tkUsd > 0) {
      floorStr = `<span style="color: var(--color-green); font-weight:600;">$${tkUsd < 0.001 ? tkUsd.toFixed(6) : tkUsd.toFixed(4)}</span>`;
    }

    const isFav = isFavorited(item.contract, item);
    const starClass = isFav ? 'star-btn starred' : 'star-btn';
    const starIcon = isFav ? '★' : '☆';

    tr.innerHTML = `
      <td class="col-fav"><button class="${starClass}" onclick="toggleFavorite('${item.contract}', event)" title="${isFav ? 'Remove from favorites' : 'Add to favorites'}">${starIcon}</button></td>
      <td class="col-num">${index + 1}</td>
      <td class="col-block"><code>${blk}</code></td>
      <td class="col-sm">${smBadge}</td>
      <td class="col-project"><strong>${item.name || 'Unknown'}</strong><span class="sym">(${item.symbol || ''})</span></td>
      <td class="col-type">${stdBadge}</td>
      <td class="col-safety">${safeBadge}</td>
      <td class="col-interest">${intBadge}</td>
      <td class="col-holders">${hbStr}</td>
      <td class="col-lp">${lpStr}</td>
      <td class="col-progress">${progBar}</td>
      <td class="col-price">${priceStr}</td>
      <td class="col-floor">${floorStr}</td>
      <td class="col-action"><button class="inspect-btn"><span class="desktop-only">Inspect</span><span class="mobile-only">🔍 Deep Dive & Snipe CLI ➔</span></button></td>
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

    ${smList.length > 0 ? `
    <div class="modal-section">
      <div class="modal-section-title">🧠 Smart Money Activity (${smList.length} Tracked Wallets)</div>
      <div style="color: var(--color-cyan); font-size: 12px; margin-top: 4px;">
        ${smList.slice(0, 10).map(w => `<span class="badge badge-dim" style="margin: 2px;">${w}</span>`).join(' ')}
        ${smList.length > 10 ? `<span class="dim">(+${smList.length - 10} more)</span>` : ''}
      </div>
    </div>
    ` : ''}

    <div class="modal-section">
      <div class="modal-section-title">🔗 Official Secondary Links</div>
      <div class="modal-links">
        <a href="${mktUrl}" target="_blank" rel="noopener" class="modal-btn btn-element">🛒 ${isNft ? 'Element NFT Collection' : 'PancakeSwap / DEX'}</a>
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
    isInitialLoad = false;

    const smartDrops = allDrops.filter(d => (d.smart_wallets && d.smart_wallets.length > 0) || (d.smart_minters && d.smart_minters.length > 0));
    document.getElementById('radarCount').textContent = allDrops.length;
    document.getElementById('smartCount').textContent = smartDrops.length;

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

    // Poll latest block
    pollLatestBlock();
    setInterval(pollLatestBlock, 10000);

  } catch (err) {
    console.error('Failed to load history.json:', err);
  }
}

document.addEventListener('DOMContentLoaded', init);

