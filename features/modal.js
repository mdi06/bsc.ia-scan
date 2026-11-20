// Token modal (the inspect dialog a table row, search result, Pulse card or #token= link opens). Loaded on first use
// by the openModal stub in app.js (see loadFeature there), and prefetched when the page is idle, so its ~44 KB of
// HTML building is not parsed at startup.
//
// This is an ES module, but it shares app.js's global scope: every app.js top-level name (allDrops, escapeHtml,
// currentInspectItem, ...) is used and assigned here by plain name, exactly as when this code lived in app.js. Inline
// onclick="..." handlers in the HTML built here may only call global functions (defined in app.js, or set on window by
// a feature); scripts/check_minified.js checks that.

export function openModal(item, source = 'direct') {
  const modal = document.getElementById('inspectModal');
  const content = document.getElementById('modalContent');
  if (!modal || !content) return;
  // The mint-calldata redraw (source 'calldata') re-renders an already open modal about a second after it opened:
  // keep wherever the user has scrolled to instead of snapping back to the top.
  const keepScrollTop = source === 'calldata' ? (content.scrollTop || 0) : 0;

  currentInspectItem = item;
  setModalHash(item);
  if (source !== 'redraw') {   // a redraw after the calldata arrived is not a new inspection
    addRecentInspected(item);
    setupModalTelemetry(item, source);
  }
  if (item._slim) upgradeSlimItem(item);
  // (a slim record's upgrade above fetches the whole record, calldata included: no second request for it)
  if (item.has_mint_calldata && !item.mint_calldata && !item._calldataRequested && !item._slim) {
    item._calldataRequested = true;
    loadFullMintCalldata(item).then(ok => {
      if (ok && currentInspectItem === item) openModal(item, 'redraw');
    });
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
  // the feed left out this record's (large) calldata and it is still loading: never mint with an empty one
  const calldataPending = Boolean(item.has_mint_calldata && !rawCd);
  const userWallet = connectedWallet || '<YOUR_WALLET>';
  let calldata = isRecv ? '0x' : rawCd;
  if (!isRecv && calldata.startsWith('0x836a1040') && calldata.length >= 138) {
    const cleanW = userWallet.replace('0x', '').padStart(40, '0');
    calldata = calldata.slice(0, 74) + '000000000000000000000000' + cleanW + calldata.slice(138);
  }
  const valWei = item.mint_value_wei || '0';
  const cliSnippet = `baw contract-call preview --binanceChainId 56 --from ${userWallet} --to ${cAddr} --value ${valWei} --inputData ${calldata} --json`;
  currentSniperCliSnippet = cliSnippet;

  const isFav = isFavorited(cAddr, item);
  const favBtnClass = isFav ? 'modal-fav-btn starred' : 'modal-fav-btn';
  const favBtnText = isFav ? '★ Favorited' : '☆ Add to Favorites';

  const exitEval = evaluatePortfolioExit(item, bnbPrice);
  const rawHeldWei = item.user_balance_wei || 1000000000000000000n;
  const sellCalldata = !isNft ? buildPancakeSwapSellCalldata(cAddr, rawHeldWei, userWallet) : '';
  const sellCliSnippet = !isNft ? `baw contract-call preview --binanceChainId 56 --from ${userWallet} --to ${PANCAKE_ROUTER_V2} --value 0 --inputData ${sellCalldata} --json` : '';

  // Same rule as the table and the FREE pill (isFreeMintDrop). DEX-traded records have no mint at all.
  const mintBnbNum = Number(item.mint_price_bnb || 0);
  const mintPriceUpper = String(item.mint_price_str || '').toUpperCase();
  const isDexTraded = mintPriceUpper.includes('DEX');
  const isFree = !isDexTraded && isFreeMintDrop(item);
  const hasPaidMint = !isDexTraded && !isFree && (mintBnbNum > 0 || Number(valWei) > 0);
  const paidBnb = mintBnbNum > 0 ? mintBnbNum : Number(valWei) / 1e18;
  const mintCostLabel = isDexTraded ? 'DEX TRADED'
    : (isFree ? 'FREE MINT'
      : (hasPaidMint ? `${formatBnb(paidBnb)} BNB (~${formatBnbAsUsd(paidBnb)})` : 'TBD'));
  const isSoldOut = Boolean(item.max_supply && item.total_minted && Number(item.total_minted) >= Number(item.max_supply));
  const isRiskContract = Boolean((item.risk_badge && (item.risk_badge.includes('🚨') || item.risk_badge.includes('Fund Drain') || item.risk_badge.includes('Honeypot'))) || item.risk_level === 'RUG' || (item.security && item.security.is_fund_drain));

  const msRaw = Number(item.max_supply || 0);
  const ms = (isFinite(msRaw) && msRaw < 1e20) ? msRaw : 0;   // 1e20+ is the "uncapped" sentinel
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
  // Real top-10 share from the security scan (security.top10_pct). Holder count alone says nothing about
  // concentration: a fresh drop with 1 holder at 0 minted used to read "Extreme (>50%)", a false alarm.
  const top10Raw = item.security ? Number(item.security.top10_pct) : NaN;
  const hasTop10 = Number.isFinite(top10Raw) && top10Raw > 0;
  const tooEarly = tm === 0 || holdersCount < 10;
  const neutralPill = (label, title) => `<span class="whale-alert-pill" style="background: rgba(148, 163, 184, 0.12); border: 1px solid rgba(148, 163, 184, 0.5); color: var(--text-secondary, #94a3b8);" title="${title}">${label}</span>`;
  let whaleConcentrationBadge;
  let whaleConcentrationDesc;
  if (tooEarly) {
    whaleConcentrationBadge = neutralPill('⏳ Too early', 'Not enough mints or holders yet to judge concentration');
    whaleConcentrationDesc = `Only ${formatCount(holdersCount)} holder${holdersCount === 1 ? '' : 's'} so far${tm === 0 ? ' and nothing minted yet' : ''} — normal for a fresh drop, not a whale signal.`;
  } else if (!hasTop10) {
    whaleConcentrationBadge = neutralPill('— Not measured', 'No top-holder scan for this contract yet');
    whaleConcentrationDesc = `Spread across ${formatCount(holdersCount)} wallets; top-10 holder share not scanned yet.`;
  } else {
    const pct = Math.min(100, top10Raw);
    const pctLabel = `${pct.toFixed(pct >= 10 ? 0 : 1)}%`;
    if (pct > 50) {
      whaleConcentrationBadge = `<span class="whale-alert-pill" style="background: rgba(239, 68, 68, 0.15); border: 1px solid var(--color-red); color: var(--color-red);" title="Top 10 wallets hold ${pctLabel} of supply">🚨 Extreme (top 10: ${pctLabel})</span>`;
      whaleConcentrationDesc = `High dump risk: the top 10 wallets hold ${pctLabel} of supply across ${formatCount(holdersCount)} holders.`;
    } else if (pct >= 25) {
      whaleConcentrationBadge = `<span class="whale-alert-pill" style="background: rgba(250, 204, 21, 0.15); border: 1px solid var(--color-yellow); color: var(--color-yellow);" title="Top 10 wallets hold ${pctLabel} of supply">🟡 Moderate (top 10: ${pctLabel})</span>`;
      whaleConcentrationDesc = `Medium clustering: the top 10 wallets hold ${pctLabel} across ${formatCount(holdersCount)} holders. Watch large sellers.`;
    } else {
      whaleConcentrationBadge = `<span class="whale-alert-pill" style="background: rgba(74, 222, 128, 0.15); border: 1px solid var(--color-green); color: var(--color-green);" title="Top 10 wallets hold ${pctLabel} of supply">🟢 Distributed (top 10: ${pctLabel})</span>`;
      whaleConcentrationDesc = `Clean distribution: the top 10 wallets hold ${pctLabel} across ${formatCount(holdersCount)} holders.`;
    }
  }

  // Feature 1: Dev Funding Root & Cluster Metrics (Exclude zero / burn address from ever being a dev root)
  const rawDevRoot = (item.dev_root || (item.security && item.security.dev_root) || '').trim();
  const isZeroOrBurn = (addr) => !addr || addr.toLowerCase() === '0x0000000000000000000000000000000000000000' || addr.toLowerCase() === '0x0' || addr.toLowerCase() === 'n/a';
  const devRootAddr = isZeroOrBurn(rawDevRoot) ? '' : rawDevRoot;

  const rawClusterDevs = Number(item.cluster_devs || (item.security && item.security.cluster_devs) || 1);
  const rawClusterN = Number(item.cluster_n || (item.security && item.security.cluster_n) || 1);
  const isCluster = Boolean(devRootAddr && (rawClusterDevs >= 2 || rawClusterN >= 3));
  const clusterDevs = isCluster ? rawClusterDevs : 1;
  const clusterN = isCluster ? rawClusterN : 1;

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

  // A token that already trades but has no trustworthy market cap shows "—"; only a pre-market drop is in its fair-mint phase
  const isTradingNow = isSecondaryMarketAsset(item);
  let mcapFormatted = mcapNum > 0 ? `$${formatCount(mcapNum)}` : (isTradingNow ? '<span class="dim">—</span>' : '<span class="dim">Fair Mint Phase</span>');
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

  let devSyndicateFormatted = `<span class="badge badge-dim" style="font-size: 10px;">🟢 Independent Contract</span>`;
  if (hasDeployer || devRootAddr) {
    if (isCluster) {
      devSyndicateFormatted = `<span class="badge badge-red" style="font-weight: 700; font-size: 10px;">🚨 Dev Cluster (${clusterDevs} devs / ${clusterN} drops)</span>`;
    } else {
      devSyndicateFormatted = `<span class="badge badge-dim" style="font-size: 10px;">🟢 Standalone (1 drop)</span>`;
    }
  }

  const mintedStr = formatCount(item.total_minted);
  const supplyStr = (item.max_supply && Number(item.max_supply) > 0) ? `/ ${formatCount(item.max_supply)}` : '<span class="dim">(Uncapped)</span>';

  // Navigation context for in-modal flipping (Prev / Next & Arrow Keys)
  currentInspectItem = item;
  const navCtx = getInspectNavContext(item);
  const navIndex = navCtx.index;
  const navTotal = navCtx.total;
  const hasPrev = navCtx.hasPrev;
  const hasNext = navCtx.hasNext;
  const prevItem = navCtx.prevItem;
  const nextItem = navCtx.nextItem;
  const prevTitle = hasPrev && prevItem ? `Previous: ${escapeHtml(prevItem.name)} (${escapeHtml(prevItem.symbol || '')}) [← Left Arrow]` : 'First token in list';
  const nextTitle = hasNext && nextItem ? `Next: ${escapeHtml(nextItem.name)} (${escapeHtml(nextItem.symbol || '')}) [→ Right Arrow]` : 'Last token in list';

  content.innerHTML = `
    <div class="modal-header">
      <div class="modal-title-group">
        <div class="modal-title">🔍 ${name} (${sym}) - Deep Dive Inspection</div>
        ${navTotal > 1 && navIndex !== -1 ? `
          <div class="modal-nav-compact" role="group" aria-label="In-Modal Token Navigation">
            <button class="modal-nav-btn prev-btn" id="modalPrevBtn" onclick="navigateInspectModal(-1)" ${hasPrev ? '' : 'disabled'} title="${prevTitle}" aria-label="Previous Token">
              <span class="nav-arrow">←</span> <span class="nav-label">Prev</span> <span class="nav-key">[←]</span>
            </button>
            <span class="modal-nav-counter" id="modalNavCounter" title="Token ${navIndex + 1} of ${navTotal} in active list">
              ${navIndex + 1} / ${navTotal}
            </span>
            <button class="modal-nav-btn next-btn" id="modalNextBtn" onclick="navigateInspectModal(1)" ${hasNext ? '' : 'disabled'} title="${nextTitle}" aria-label="Next Token">
              <span class="nav-label">Next</span> <span class="nav-arrow">→</span> <span class="nav-key">[→]</span>
            </button>
          </div>
        ` : ''}
      </div>
      <div class="modal-header-actions" style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
        <button class="${favBtnClass}" id="modalFavBtn" onclick="toggleModalFav('${cAddr}')">${favBtnText}</button>
        <button type="button" class="modal-share-btn" id="modalShareBtn" data-share-symbol="${escapeHtml(item.symbol || item.name || item.contract || '')}" onclick="copyTokenShareLink(this)" title="Copy a link to this token; visits from it are tagged utm_source=share, utm_medium=modal">${TOKEN_SHARE_LABEL}</button>
        <button class="modal-close-btn" onclick="document.getElementById('inspectModal').close()" title="Close dialog [ESC]">✕ Close [ESC]</button>
      </div>
    </div>

    <div class="modal-section" data-modal-section="architecture">
      <div class="modal-section-title">Contract & On-Chain Architecture</div>
      <div class="modal-grid">
        <div><span class="dim">Contract:</span> ${cAddrFormatted}</div>
        <div><span class="dim">Deployer:</span> ${deployerFormatted}</div>
        <div><span class="dim">Funding Root:</span> ${devRootFormatted}</div>
        <div><span class="dim">Syndicate:</span> ${devSyndicateFormatted}</div>
        <div><span class="dim">Standard:</span> <strong>${std}</strong></div>
        <div><span class="dim">Deployment Block:</span> <code>#${item.block || 'N/A'}</code></div>
        <div><span class="dim">Total Minted:</span> <strong>${mintedStr}</strong> ${supplyStr}</div>
        <div><span class="dim">Holders:</span> <strong>${formatCount(item.holders)} unique wallets</strong></div>
      </div>
    </div>

    <div class="modal-section market-dynamics-card" data-modal-section="market_valuation">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span style="color: var(--color-cyan); font-weight: 700; display: inline-flex; align-items: center; gap: 6px;">
          💎 Market Valuation & Trading Dynamics
        </span>
        <div style="display: flex; gap: 6px; align-items: center;">
          ${dexScreenerUrl ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" class="badge badge-cyan" onclick="trackExternalClick('dexscreener_badge', '${escapeHtml(sym)}', '${cAddr}')" style="text-decoration: none; font-weight: 700; padding: 3px 8px; font-size: 11px;" title="Open live chart and trades on DexScreener">📈 DexScreener ↗</a>` : ''}
          ${chg24Badge}
        </div>
      </div>
      <div class="modal-grid" style="grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 6px;">
        <div class="market-metric-box">
          <span class="dim">${mcapLabel(item) === 'FDV' ? 'FDV' : 'Market Cap'}</span>
          <strong style="color: ${mcapNum > 0 ? 'var(--color-green)' : 'var(--text-primary)'}; font-size: 15px; display: block; margin-top: 2px;">
            ${dexScreenerUrl && mcapNum > 0 ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="trackExternalClick('dexscreener_mcap', '${escapeHtml(sym)}', '${cAddr}')" style="color: var(--color-green); text-decoration: none;" title="View Valuation on DexScreener">${mcapFormatted} ↗</a>` : mcapFormatted}
          </strong>
        </div>
        <div class="market-metric-box">
          <span class="dim">24h Trading Vol</span>
          <strong style="color: ${vol24Num > 0 ? 'var(--color-cyan)' : 'var(--text-dim)'}; font-size: 15px; display: block; margin-top: 2px;">
            ${dexScreenerUrl && vol24Num > 0 ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="trackExternalClick('dexscreener_vol', '${escapeHtml(sym)}', '${cAddr}')" style="color: var(--color-cyan); text-decoration: none;" title="View Volume on DexScreener">${vol24Formatted} ↗</a>` : vol24Formatted}
          </strong>
        </div>
        <div class="market-metric-box">
          <span class="dim">PancakeSwap LP${marketFreshnessHtml(item)}</span>
          <strong style="color: ${lp > 0 ? 'var(--color-yellow)' : 'var(--text-dim)'}; font-size: 15px; display: block; margin-top: 2px;">
            ${dexScreenerUrl && lp > 0 ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="trackExternalClick('dexscreener_lp', '${escapeHtml(sym)}', '${cAddr}')" style="color: var(--color-yellow); text-decoration: none;" title="View Liquidity Pool on DexScreener">$${formatCount(lp)} ↗</a>` : (lp > 0 ? `$${formatCount(lp)}` : '$0')}
          </strong>
        </div>
        <div class="market-metric-box">
          <span class="dim">Current Price${marketFreshnessHtml(item)}</span>
          <strong style="font-size: 15px; display: block; margin-top: 2px;">
            ${dexScreenerUrl && tkPriceNum > 0 ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="trackExternalClick('dexscreener_price', '${escapeHtml(sym)}', '${cAddr}')" style="color: var(--color-green); text-decoration: none;" title="Open live chart on DexScreener">${formatCryptoPrice(tkPriceNum)} ↗</a>` : (tkPriceNum > 0 ? formatCryptoPrice(tkPriceNum) : (Number(item.floor_price_usd || 0) > 0 ? formatCryptoPrice(item.floor_price_usd) : '<span class="dim">—</span>'))}
          </strong>
        </div>
      </div>
    </div>

    ${!isNft ? `
    <div class="modal-section bonding-gauge-card" data-modal-section="bonding_gauge">
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
        <span>${bondingGraduated ? `Secondary Liquidity Pool: $${formatCount(lp)}` : `Primary Minting: ${formatCount(tm)} / ${ms > 0 ? formatCount(ms) : 'Target Cap'}`}</span>
        <span>${bondingGraduated ? (dexScreenerUrl ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="trackExternalClick('dexscreener_graduated', '${escapeHtml(sym)}', '${cAddr}')" style="color: var(--color-green); font-weight: 600; text-decoration: none;" title="View pair on DexScreener">🟢 Active Trading on PancakeSwap V2 ➔ View on DexScreener ↗</a>` : '🟢 Active Trading on PancakeSwap V2') : (bondingPct >= 80 ? '🔥 Near DEX Pool Injection (≥80%)' : (bondingPct >= 50 ? '⚡ Strong Momentum (≥50%)' : '🌱 Early Fair-Mint Phase'))}</span>
      </div>
    </div>
    ` : ''}

    <div class="modal-section" data-modal-section="security_audit">
      <div class="modal-section-title">🛡️ Security & Anti-Rug Audit</div>
      <div class="modal-grid">
        <div><span class="dim">Risk Rating:</span> <strong>${item.risk_badge || item.risk_level || '🟢 Clean (SAFE)'}</strong></div>
        <div><span class="dim">Bytecode Refund:</span> ${item.security && item.security.has_refund ? '<span style="color: var(--color-green); font-weight:600;">🛡️ Verified (' + (item.security.refund_methods || []).slice(0, 1).join('') + ')</span>' : (Number(item.mint_price_bnb || 0) > 0 ? '<span style="color: var(--color-yellow); font-weight:600;">⚠️ No Refund Function</span>' : '<span class="dim">N/A (Free Mint)</span>')}</div>
        <div><span class="dim">Deployer Profile:</span> ${item.security && item.security.is_serial_dev ? '<span style="color: var(--color-red); font-weight:700;">🚨 Serial Dev (' + item.security.serial_count + ' drops)</span>' : (item.security && item.security.is_fresh_dev ? '<span style="color: var(--color-yellow);">🟡 Fresh Wallet</span>' : '<span style="color: var(--color-green);">🟢 Established Nonce</span>')}</div>
        <div><span class="dim">PancakeSwap LP:</span> ${item.lp_usd ? '<span style="color: var(--color-green); font-weight:700;">$' + Number(item.lp_usd).toLocaleString(undefined, { maximumFractionDigits: 0 }) + '</span>' : '<span class="dim">No LP Pool detected</span>'}</div>
        <div><span class="dim">Whale Concentration:</span> ${whaleConcentrationBadge}</div>
        <div><span class="dim">Dev Cluster Audit:</span> ${isCluster ? `<span class="badge badge-red" style="font-weight:700;" title="Dev Cluster: ${clusterDevs} linked ${clusterDevs === 1 ? 'wallet' : 'wallets'} / ${clusterN} drops">🚨 ${clusterDevs} Linked / ${clusterN} Drops</span>` : '<span style="color: var(--color-green);" title="Independent Deployer (No linked developer cluster)">🟢 Independent</span>'}</div>
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

    <div class="modal-section holder-dist-card" data-modal-section="holder_distribution">
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
    <div class="modal-section" data-modal-section="proxy_architecture" style="border-color: rgba(56, 189, 248, 0.4); background: rgba(56, 189, 248, 0.03);">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span style="color: var(--color-cyan);">🧩 Proxy & Clone Architecture (Option E)</span>
        <span class="badge badge-cyan" style="font-size: 11px;">${escapeHtml(item.proxy_type || 'Clone Contract')}</span>
      </div>
      <div class="modal-grid" style="margin-top: 6px;">
        <div><span class="dim">Proxy Pattern:</span> <strong>${escapeHtml(item.proxy_type || 'EIP-1167 / EIP-1967')}</strong></div>
        <div><span class="dim">Implementation:</span> <strong>${item.master_implementation ? `<a href="https://bscscan.com/address/${item.master_implementation}" target="_blank" rel="noopener" onclick="trackExternalClick('bscscan_proxy_impl', '${escapeHtml(sym)}', '${cAddr}')" style="color: var(--color-cyan); text-decoration: underline;">${item.master_implementation.slice(0, 10)}...${item.master_implementation.slice(-6)}</a>` : 'Direct Contract'}</strong></div>
        ${item.is_factory ? `<div><span class="dim">Factory:</span> <strong style="color: var(--color-yellow);">${escapeHtml(item.factory_badge || 'Launchpad Factory')}</strong></div>` : ''}
      </div>
    </div>
    ` : ''}

    <div class="modal-section" data-modal-section="assisted_conviction" style="${isReady ? 'border-color: rgba(74, 222, 128, 0.4); background: rgba(74, 222, 128, 0.03);' : ''}">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span>🎯 Assisted Conviction Audit (60% Rule & Anti-Rug)</span>
        <span class="${isReady ? 'badge badge-green' : 'badge badge-yellow'}" style="font-size: 11px;">${sEval.badge}</span>
      </div>
      <div style="font-size: 12px; margin-top: 6px; line-height: 1.6;">
        ${sEval.checklist.map(line => `<div style="padding: 2px 0;">${line}</div>`).join('')}
      </div>
    </div>

    ${smList.length > 0 ? `
    <div class="modal-section" data-modal-section="smart_money">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span style="display: inline-flex; align-items: center; gap: 8px;">
          🧠 Smart Money Activity (${smList.length} Tracked Wallets)
          ${smList.length >= 3 ? `<span class="badge-cluster" style="font-size: 10px;" title="3+ Smart Money Alpha Wallets Inflow Detected">🔥 SM Cluster (${smList.length})</span>` : ''}
        </span>
        <button class="btn-sm-view-leaderboard" onclick="trackAnalytics('leaderboard_opened_from_modal', { token_symbol: '${escapeHtml(sym)}' }); document.getElementById('inspectModal').close(); openSmartLeaderboardModal('ALL')">🏆 View All Smart Hunters (Win Rates) ↗</button>
      </div>
      <div style="color: var(--color-cyan); font-size: 12px; margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px;">
        ${smList.slice(0, 12).map(w => {
          const isF = followedWallets.has(String(w).toLowerCase());
          return `
            <div style="display: inline-flex; align-items: center; gap: 4px; background: rgba(0, 240, 255, 0.05); border: 1px solid rgba(0, 240, 255, 0.2); border-radius: 4px; padding: 2px 6px; margin: 2px;">
              <span style="font-weight: 700; color: ${isF ? 'var(--color-yellow)' : 'var(--color-cyan)'}; font-size: 11px;">${isF ? '⭐ ' : ''}${escapeHtml(w)}</span>
              <button class="${isF ? 'btn-follow-wallet following' : 'btn-follow-wallet'}" style="padding: 2px 6px; font-size: 9.5px;" onclick="trackAnalytics('wallet_follow_toggled', { wallet: '${escapeHtml(w)}', will_follow: !followedWallets.has('${escapeHtml(w)}'.toLowerCase()) }); toggleFollowWallet('${escapeHtml(w)}'); this.classList.toggle('following'); this.textContent = this.classList.contains('following') ? '★ Following' : '⭐ Follow';">
                ${isF ? '★ Following' : '⭐ Follow'}
              </button>
              <button class="btn-view-activity" style="padding: 2px 6px; font-size: 9.5px;" onclick="trackAnalytics('wallet_activity_viewed', { wallet: '${escapeHtml(w)}' }); viewWalletActivity('${escapeHtml(w)}')" title="View all drops minted by ${escapeHtml(w)}">
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
    <div class="modal-section" data-modal-section="secondary_trading" style="${item.is_nft_surge ? 'border-color: rgba(217, 70, 239, 0.5); background: rgba(217, 70, 239, 0.04);' : ''}">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span style="color: var(--color-magenta);">🦋 Element Market Secondary Trading Activity</span>
        ${item.is_nft_surge ? `<span class="badge badge-magenta" style="font-size: 11px; font-weight:700;">🦋 SURGE (${item.secondary_sales_count} sales)</span>` : ''}
      </div>
      <div class="modal-grid" style="margin-top: 6px;">
        <div><span class="dim">Secondary Sales:</span> <strong style="color: ${item.secondary_sales_count >= 3 ? 'var(--color-magenta)' : 'inherit'};">${item.secondary_sales_count || 0} sales</strong></div>
        <div><span class="dim">Element Volume:</span> <strong>${item.secondary_vol_bnb ? formatBnb(item.secondary_vol_bnb) + ' BNB' : '0 BNB'}</strong></div>
        <div><span class="dim">Floor Price:</span> <strong>${item.floor_price_bnb ? formatBnb(item.floor_price_bnb) + ' BNB' : '—'}</strong></div>
        <div><span class="dim">Secondary Transfers:</span> <strong>${item.secondary_transfers || 0} transfers</strong></div>
      </div>
      <div style="margin-top: 10px; font-size: 11px; color: var(--text-dim);">
        🔗 Marketplace: <a href="${mktUrl}" target="_blank" rel="noopener" onclick="trackExternalClick('element_market', '${escapeHtml(sym)}', '${cAddr}')" style="color: var(--color-cyan); text-decoration: underline;">${mktUrl}</a> (Manual confirmation only — no auto-trade)
      </div>
    </div>
    ` : ''}

    ${isNft && (item.user_balance > 0 || activeTab === 'PORTFOLIO') ? `
    <div class="modal-section" data-modal-section="nft_portfolio_exit" style="border-color: rgba(217, 70, 239, 0.4); background: rgba(217, 70, 239, 0.03);">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span style="color: var(--color-magenta);">🎨 Element NFT Portfolio Exit Action</span>
        <span class="badge ${exitEval.badge_class || 'badge-magenta'}" style="font-size: 11px;">${exitEval.badge}</span>
      </div>
      <div style="font-size: 12px; margin-top: 4px; margin-bottom: 8px; color: var(--text-dim);">
        <strong style="color: var(--text-white);">Exit Strategy:</strong> ${exitEval.recommendation}
      </div>
      <div class="modal-links">
        <a href="${mktUrl}" target="_blank" rel="noopener" onclick="trackExternalClick('element_sell_nft', '${escapeHtml(sym)}', '${cAddr}')" class="modal-btn" style="background: rgba(217, 70, 239, 0.2); border: 1px solid var(--color-magenta); color: #ffffff; text-decoration: none;">🛒 Open Element Market to List/Sell NFT</a>
      </div>
    </div>
    ` : ''}

    <div class="modal-section" data-modal-section="secondary_links">
      <div class="modal-section-title">🔗 Official Secondary Links</div>
      <div class="modal-links">
        ${!isNft && dexScreenerUrl ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="trackExternalClick('dexscreener_links', '${escapeHtml(sym)}', '${cAddr}')" class="modal-btn" style="background: rgba(0, 240, 255, 0.15); border: 1px solid var(--color-cyan); color: var(--color-cyan); font-weight: 700;">📈 View on DexScreener ↗</a>` : ''}
        <a href="${isNft ? mktUrl : `https://pancakeswap.finance/swap?outputCurrency=${cAddr}`}" target="_blank" rel="noopener" onclick="trackExternalClick('pancakeswap_links', '${escapeHtml(sym)}', '${cAddr}')" class="modal-btn btn-element">🛒 ${isNft ? 'Element NFT Collection' : 'Trade on PancakeSwap DEX'}</a>
        ${!isNft ? `<a href="https://pancakeswap.finance/swap?inputCurrency=${cAddr}&outputCurrency=BNB" target="_blank" rel="noopener" onclick="trackExternalClick('pancakeswap_swap_bnb', '${escapeHtml(sym)}', '${cAddr}')" class="modal-btn" style="background: rgba(240, 185, 11, 0.15); border: 1px solid #f0b90b; color: #f0b90b;">🥞 Swap to BNB (PancakeSwap)</a>` : ''}
        <a href="https://bscscan.com/address/${cAddr}" target="_blank" rel="noopener" onclick="trackExternalClick('bscscan_address', '${escapeHtml(sym)}', '${cAddr}')" class="modal-btn btn-bscscan">🔍 View on BscScan</a>
        ${item.website ? `<a href="${item.website}" target="_blank" rel="noopener" onclick="trackExternalClick('website', '${escapeHtml(sym)}', '${cAddr}')" class="modal-btn btn-bscscan">🌐 Website</a>` : ''}
        ${item.twitter ? `<a href="${item.twitter}" target="_blank" rel="noopener" onclick="trackExternalClick('twitter', '${escapeHtml(sym)}', '${cAddr}')" class="modal-btn btn-bscscan">🐦 Twitter / X</a>` : ''}
      </div>
    </div>

    ${!isNft ? `
    <div class="modal-section dex-chart-section" data-modal-section="dex_chart">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <span style="font-size: 12px; font-weight: 700; color: var(--color-cyan);">📈 Live DEX Candlestick Price Action Chart</span>
        <div style="display: flex; gap: 8px; align-items: center;">
          ${dexScreenerUrl ? `
            <a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="trackExternalClick('dexscreener_chart_open', '${escapeHtml(sym)}', '${cAddr}')" class="dex-chart-toggle-btn" style="color: var(--color-cyan); text-decoration: none; padding: 4px 8px; border: 1px solid rgba(0, 240, 255, 0.3); border-radius: 4px; font-size: 11px; display: inline-flex; align-items: center; gap: 4px;" title="Open live pair on DexScreener in new window">
              <span>↗</span> <span>Open DexScreener</span>
            </a>
          ` : ''}
          <button class="dex-chart-toggle-btn" id="chartToggleBtn" onclick="trackAnalytics('dex_chart_toggled', { token_symbol: '${escapeHtml(sym)}' }); toggleDexChart('${cAddr}', '${dexScreenerUrl}')">
            <span id="chartToggleIcon">📊</span> <span id="chartToggleLabel">Show Live DEX Chart</span>
          </button>
        </div>
      </div>
      <div id="dexChartWrap" class="dex-chart-frame-wrap" style="display: none;"></div>
    </div>
    ` : ''}

    <div class="modal-section mint-action-section" data-modal-section="mint_action" style="${isSoldOut ? 'opacity: 0.85;' : (isReady ? 'border-color: rgba(74, 222, 128, 0.5); box-shadow: 0 0 15px rgba(74, 222, 128, 0.1);' : 'border-color: rgba(0, 240, 255, 0.4);')}">
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
        <div><span class="dim">Progress:</span> <strong>${formatCount(item.total_minted)}</strong> / ${ms > 0 ? formatCount(ms) : '∞'}</div>
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
          <a href="https://web3.binance.com/en/dev-docs/products/agentic-wallet/ai-pro#came-from-the-agent-id-campaign" target="_blank" rel="noopener" onclick="trackExternalClick('binance_claim_ai', '${escapeHtml(sym)}', '${cAddr}')" style="color: var(--color-cyan); text-decoration: underline;">Claim .ai in Binance App ↗</a>
        </div>
      ` : (connectedWallet && isAgenticWallet ? `
        <div class="agentic-status-success" style="background: rgba(0, 240, 255, 0.08); border: 1px solid rgba(0, 240, 255, 0.3); border-radius: 6px; padding: 6px 12px; margin: 8px 0; font-size: 11px; color: var(--color-cyan); display: flex; align-items: center; gap: 8px;">
          <span>🤖</span>
          <span><strong>Agentic Wallet Active:</strong> Your wallet holds a verified <code>.ai</code> Agent ID on BSC. Eligible for exclusive inner-market minting.</span>
        </div>
      ` : '')}

      ${isSoldOut ? `
        <div style="display: flex; gap: 10px; margin-top: 8px;">
          <a href="${!isNft ? (dexScreenerUrl || mktUrl) : mktUrl}" target="_blank" rel="noopener" onclick="trackExternalClick('sold_out_trade', '${escapeHtml(sym)}', '${cAddr}')" class="web3-mint-btn sold-out" style="text-align: center; text-decoration: none; display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%;">
            <span>🔒 Sold Out (100% Capped) ➔ ${!isNft ? 'View Chart & Trade on DexScreener ↗' : 'Trade on Secondary Market'}</span>
          </a>
        </div>
      ` : `
        ${isDexTraded ? `
        <div class="mint-btn-container" style="margin-top: 8px;">
          <a href="${dexScreenerUrl || mktUrl}" target="_blank" rel="noopener" class="web3-mint-btn sold-out" style="text-align: center; text-decoration: none; display: flex; align-items: center; justify-content: center; gap: 8px;" onclick="trackExternalClick('dex_traded_trade', '${escapeHtml(sym)}', '${cAddr}')">
            <span>📈 Already trading on a DEX: no mint (open the live chart ↗)</span>
          </a>
        </div>
        ` : `<div class="mint-btn-container" style="margin-top: 8px;">
          ${calldataPending ? `<button id="modalMintBtn" class="web3-mint-btn sold-out" disabled>
            <span class="mint-icon">⏳</span>
            <span id="modalMintBtnText">Loading mint data…</span>
          </button>` : `<button id="modalMintBtn" class="web3-mint-btn" onclick="trackAnalytics('mint_button_clicked', mintClickProps(${escapeHtml(JSON.stringify(sym))}, ${isFree})); executeWeb3Mint('${cAddr}', '${calldata}', '${valWei}', ${escapeHtml(JSON.stringify(name))})">
            <span class="mint-icon">⚡</span>
            <span id="modalMintBtnText">${connectedWallet ? `1-Click Mint (${mintCostLabel})` : `👛 Connect Wallet & Mint (${mintCostLabel})`}</span>
          </button>`}
        </div>
        <div id="modalMintStatus" class="mint-status-box" style="display: none; margin-top: 10px;"></div>
        <div class="mint-cli-row">
          <span class="mint-cli-hint">Or mint from a terminal with the Binance Agentic Wallet CLI:</span>
          <button type="button" class="mint-cli-btn" onclick="copyModalSniperCli(this, 'mint_section')">⌨ Copy Sniper CLI</button>
        </div>`}
      `}
    </div>

    <div class="modal-section" data-modal-section="sniper_cli">
      <div class="modal-section-title">⚡ 1-Click Sniper CLI Command (Binance Agentic Wallet)</div>
      <div class="cli-box">
        <code>${escapeHtml(cliSnippet)}</code>
        <button class="copy-btn" onclick="copyModalSniperCli(this, 'sniper_section')">Copy</button>
      </div>
    </div>

    ${!isNft ? `
    <div class="modal-section" data-modal-section="pancakeswap_exit" style="border-color: rgba(240, 185, 11, 0.4); background: rgba(240, 185, 11, 0.03);">
      <div class="modal-section-title" style="display: flex; justify-content: space-between; align-items: center;">
        <span style="color: var(--color-yellow);">🥞 1-Click PancakeSwap V2 Take-Profit / Exit Command</span>
        <span class="badge ${exitEval.badge_class || 'badge-green'}" style="font-size: 11px;">${exitEval.badge}</span>
      </div>
      <div style="font-size: 12px; margin-top: 4px; margin-bottom: 8px; color: var(--text-dim);">
        <strong style="color: var(--text-white);">Exit Strategy:</strong> ${exitEval.recommendation}
      </div>
      <div class="modal-links" style="margin-bottom: 10px;">
        ${dexScreenerUrl ? `<a href="${dexScreenerUrl}" target="_blank" rel="noopener" onclick="trackExternalClick('exit_dexscreener_chart', '${escapeHtml(sym)}', '${cAddr}')" class="modal-btn" style="background: rgba(0, 240, 255, 0.15); border: 1px solid var(--color-cyan); color: var(--color-cyan); font-weight: 700; text-decoration: none;">📈 DexScreener Live Chart ↗</a>` : ''}
        <a href="https://pancakeswap.finance/swap?inputCurrency=${cAddr}&outputCurrency=BNB" target="_blank" rel="noopener" onclick="trackExternalClick('exit_pancakeswap_swap', '${escapeHtml(sym)}', '${cAddr}')" class="modal-btn" style="background: rgba(240, 185, 11, 0.2); border: 1px solid #f0b90b; color: #f0b90b; text-decoration: none;">🥞 1-Click Swap on PancakeSwap ↗</a>
      </div>
      <div class="cli-box">
        <code>${escapeHtml(sellCliSnippet)}</code>
        <button class="copy-btn" onclick="trackCliCopy('exit_cli', '${escapeHtml(sym)}', '${cAddr}', 'pancakeswap_exit'); navigator.clipboard.writeText('${sellCliSnippet.replace(/'/g, "\\'")}'); this.textContent='Copied!'; setTimeout(()=>this.textContent='Copy', 1500)">Copy Exit CLI</button>
      </div>
    </div>
    ` : ''}

    ${navTotal > 1 && navIndex !== -1 ? `
    <div class="modal-section modal-footer-nav-card" style="border-color: rgba(0, 240, 255, 0.25); background: rgba(0, 240, 255, 0.03);">
      <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span style="font-size: 13px; font-weight: 700; color: var(--color-cyan);">⚡ In-Modal Quick Flip</span>
          <span style="font-size: 11px; color: var(--text-dim);">Use <kbd class="nav-kbd">←</kbd> / <kbd class="nav-kbd">→</kbd> arrow keys to browse</span>
        </div>
        <div class="modal-footer-nav-buttons" style="display: flex; align-items: center; gap: 8px;">
          <button class="modal-nav-btn footer-nav-btn prev-btn" onclick="navigateInspectModal(-1)" ${hasPrev ? '' : 'disabled'} title="${prevTitle}">
            ← Prev ${prevItem ? `(${escapeHtml(prevItem.symbol || prevItem.name)})` : ''}
          </button>
          <span class="modal-nav-counter" style="font-weight: 700; color: var(--color-yellow); font-size: 12px; padding: 4px 10px; background: rgba(0,0,0,0.4); border-radius: 4px; border: 1px solid var(--border-dim);">
            ${navIndex + 1} of ${navTotal}
          </span>
          <button class="modal-nav-btn footer-nav-btn next-btn" onclick="navigateInspectModal(1)" ${hasNext ? '' : 'disabled'} title="${nextTitle}">
            Next ${nextItem ? `(${escapeHtml(nextItem.symbol || nextItem.name)})` : ''} →
          </button>
        </div>
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

    <div class="modal-shortcut-bar" id="modalShortcutBar">
      <button type="button" class="modal-shortcut-btn"
        onclick="jumpToModalSection('mint_action', '${escapeHtml(sym)}', ${isFree}, ${isDexTraded}, ${isSoldOut})">
        ${(!isSoldOut && !isDexTraded && isFree) ? '⚡ Free Mint / Snipe' : '⚡ Mint / Snipe'} <span aria-hidden="true">↓</span>
      </button>
      <button type="button" class="modal-shortcut-btn modal-shortcut-btn-secondary" title="Copy the Sniper CLI command"
        onclick="copyModalSniperCli(this, 'shortcut_bar')">⌨ Copy CLI</button>
    </div>
  `;

  content.scrollTop = keepScrollTop;
  setupModalShortcut();
  if (typeof modal.showModal === 'function') modal.showModal();
  else modal.setAttribute('open', '');
  if (source === 'calldata') return;
  // Set again once the dialog is actually open and laid out (the first assignment can be ignored while it is closed),
  // then keep it there against any scroll the user did not ask for.
  content.scrollTop = 0;
  holdModalAtTop(content);
}
