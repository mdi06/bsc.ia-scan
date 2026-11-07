const assert = require('assert');
const {
  pad32,
  buildPancakeSwapSellCalldata,
  evaluatePortfolioExit,
  buildMintTxParams,
  getInjectedProvider,
  openBinanceQrModal,
  handleBinanceWalletClick,
  PANCAKE_ROUTER_V2,
  WBNB_ADDRESS
} = require('./app.js');

console.log('Testing Web3 Portfolio Calldata & Take-Profit Recommendations...');

// 1. Test buildPancakeSwapSellCalldata with real wallet address
const tokenAddr = '0x105d904d64520d9bcefb66af936c2cce59037777';
const userWallet = '0xa15BB4AEdC1b3426159C3cACf8548Cf09506A20a';
const amountWei = 1000000000000000000n; // 1 token

const calldata = buildPancakeSwapSellCalldata(tokenAddr, amountWei, userWallet);
assert(calldata.startsWith('0x791ac947'), 'Must start with swapExactTokensForETHSupportingFeeOnTransferTokens selector');
assert(calldata.toLowerCase().includes(userWallet.slice(2).toLowerCase()), 'Must contain user wallet address');
assert(calldata.toLowerCase().includes(tokenAddr.slice(2).toLowerCase()), 'Must contain token address');
assert(calldata.toLowerCase().includes(WBNB_ADDRESS.slice(2).toLowerCase()), 'Must contain WBNB address in path');
console.log('✔ buildPancakeSwapSellCalldata with connected wallet: PASS');

// 2. Test evaluatePortfolioExit with 10x Moonshot
const item10x = {
  contract: tokenAddr,
  standard: 'BEP-20 Token (18 decimals)',
  mint_price_bnb: 0.001, // ~$0.705 at $705 BNB
  user_balance: 100,
  token_price_usd: 0.08, // ~$8.00 total (11.3x gain)
};
const exit10x = evaluatePortfolioExit(item10x, 705.0);
assert(exit10x.signal.includes('10x MOON'), 'Should trigger 10x MOON signal');
assert(exit10x.can_swap === true, 'Should allow swap');
console.log('✔ evaluatePortfolioExit 10x MOON: PASS');

// 3. Test evaluatePortfolioExit with -40% Trailing Crash Stop
const itemCrash = {
  contract: tokenAddr,
  standard: 'BEP-20 Token (18 decimals)',
  mint_price_bnb: 0.001,
  user_balance: 100,
  peak_price_usd: 0.10, // peaked at $0.10
  token_price_usd: 0.055, // dropped to $0.055 (-45% drawdown)
};
const exitCrash = evaluatePortfolioExit(itemCrash, 705.0);
assert(exitCrash.signal.includes('-40% CRASH'), 'Should trigger -40% CRASH trailing stop');
assert(exitCrash.action_label.includes('Exit on PancakeSwap'), 'Should recommend exit');
console.log('✔ evaluatePortfolioExit -40% CRASH trailing stop: PASS');

// 4. Test evaluatePortfolioExit with Native BNB Gas Reserve
const gasItem = {
  contract: '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
  symbol: 'BNB',
  standard: 'Native Gas',
  user_balance: 0.5,
  token_price_usd: 705.0
};
const exitGas = evaluatePortfolioExit(gasItem, 705.0);
assert(exitGas.signal.includes('GAS RESERVE'), 'Should identify Gas Reserve');
assert(exitGas.action === 'KEEP', 'Should recommend KEEP');
console.log('✔ evaluatePortfolioExit Native Gas: PASS');

// 5. Test evaluatePortfolioExit with NFT Surge
const nftItem = {
  contract: '0x2222222222222222222222222222222222222222',
  name: 'Agent NFT',
  standard: 'ERC-721 (NFT Collection)',
  user_balance: 2,
  is_nft: true,
  is_nft_surge: true,
  secondary_sales_count: 5,
  floor_price_bnb: 0.05
};
const exitNft = evaluatePortfolioExit(nftItem, 705.0);
assert(exitNft.signal.includes('ELEMENT SURGE'), 'Should identify Element NFT Surge');
assert(exitNft.action === 'LIST_ELEMENT', 'Should recommend listing on Element');
console.log('✔ evaluatePortfolioExit Element NFT Surge: PASS');

console.log('All Web3 Portfolio unit tests passed successfully!');

// 6. Test Wallet-Bound Storage Scoping
const {
  getFavoritesStorageKey,
  setWatchOnlyWallet,
  disconnectWallet,
  MULTICALL3_ADDRESS,
  encodeMulticall3Aggregate3,
  decodeMulticall3Aggregate3
} = require('./app.js');

assert.strictEqual(getFavoritesStorageKey(), 'agent_id_favorites', 'Default key should be guest favorites');
setWatchOnlyWallet('0xa15bb4aedc1b3426159c3cacf8548cf09506a20a');
assert.strictEqual(getFavoritesStorageKey(), 'agent_id_favorites_0xa15bb4aedc1b3426159c3cacf8548cf09506a20a', 'Key should be scoped to connected wallet');
disconnectWallet();
assert.strictEqual(getFavoritesStorageKey(), 'agent_id_favorites', 'Key should revert to guest on disconnect');
console.log('✔ Wallet-Bound Storage Scoping & Reset: PASS');

// 7. Test Multicall3 ABI Encoding & Decoding (Phase 2 Optimization)
assert.strictEqual(MULTICALL3_ADDRESS.toLowerCase(), '0xca11bde05977b3631167028862be2a173976ca11', 'Must match canonical Multicall3 contract');

const testCalls = [
  { target: MULTICALL3_ADDRESS, allowFailure: true, callData: '0x4d2301cc' + pad32('0xa15BB4AEdC1b3426159C3cACf8548Cf09506A20a') },
  { target: tokenAddr, allowFailure: true, callData: '0x70a08231' + pad32('0xa15BB4AEdC1b3426159C3cACf8548Cf09506A20a') }
];

const encoded = encodeMulticall3Aggregate3(testCalls);
assert(encoded.startsWith('0x82ad56cb'), 'Must start with aggregate3 selector 0x82ad56cb');
assert(encoded.length > 200, 'Must contain packed Call3 structs');
console.log('✔ encodeMulticall3Aggregate3: PASS');

// Test decoding valid response with 1 success and 1 simulated revert failure
const mockHex = '0x' +
  pad32(32) + // offset to Result[]
  pad32(2) +  // count = 2
  pad32(64) + // offset to item 0 (2 words = 64 bytes)
  pad32(192) + // offset to item 1 (64 + 128 = 192 bytes)
  // Item 0: success=true, balance=1000000000000000000 (1 ether)
  pad32(1) +  // success: true
  pad32(64) + // offset to returnData
  pad32(32) + // length: 32 bytes
  pad32(1000000000000000000n) +
  // Item 1: success=false (revert isolation)
  pad32(0) +  // success: false
  pad32(64) + // offset to returnData
  pad32(0) +  // length: 0 bytes
  '';

const decoded = decodeMulticall3Aggregate3(mockHex);
assert.strictEqual(decoded.length, 2, 'Should decode exactly 2 results');
assert.strictEqual(decoded[0].success, true, 'Item 0 must be success');
assert.strictEqual(BigInt(decoded[0].returnData), 1000000000000000000n, 'Item 0 returnData must match 1 ETH');
assert.strictEqual(decoded[1].success, false, 'Item 1 must isolate revert (success: false)');
console.log('✔ decodeMulticall3Aggregate3 with revert isolation: PASS');

// 8. Test 1-Click Web3 Direct Mint Tx Builder
const testTarget = '0x8b710e45d38fd55e2d198d2295c0a0d24ebfd64c';
const freeTx = buildMintTxParams(testTarget, '0x1249c58b', '0', userWallet);
assert.strictEqual(freeTx.to, testTarget.toLowerCase(), 'Must point to target contract');
assert.strictEqual(freeTx.data, '0x1249c58b', 'Must retain calldata');
assert.strictEqual(freeTx.value, '0x0', 'Free mint value must be 0x0');
assert.strictEqual(freeTx.from, userWallet.toLowerCase(), 'Must specify from user wallet');
console.log('✔ buildMintTxParams (Free Mint): PASS');

const pricedTx = buildMintTxParams(testTarget, '0xa0712d680000000000000000000000000000000000000000000000000000000000000001', '2400000000000000', userWallet);
assert.strictEqual(pricedTx.value, '0x886c98b760000', 'Value must correctly encode 2400000000000000 wei in hex');
assert(pricedTx.data.startsWith('0xa0712d68'), 'Must retain mint(1) selector');
console.log('✔ buildMintTxParams (Priced 0.0024 BNB Mint): PASS');

// Test recipient address injection in 0x836a1040
const raw836 = '0x836a1040000000000000000000000000000000000000000000000000000000000000000100000000000000000000000011111111111111111111111111111111111111110000000000000000000000000000000000000000000000000000000000000002';
const injTx = buildMintTxParams(testTarget, raw836, '0', userWallet);
assert(injTx.data.toLowerCase().includes(userWallet.slice(2).toLowerCase()), 'Must inject connected wallet into recipient slot');
console.log('✔ buildMintTxParams (Recipient Dynamic Injection): PASS');

// Test safety error on invalid address
assert.throws(() => {
  buildMintTxParams('0xinvalid', '0x', '0', userWallet);
}, /Invalid contract address/);
console.log('✔ buildMintTxParams (Address Validation Guard): PASS');

// 9. Test Injected Provider Isolation (Prevent Rabby hijacking Binance requests)
global.window = {
  ethereum: { isRabby: true }
};
assert.strictEqual(getInjectedProvider('binance'), null, 'Must NOT fall back to Rabby when Binance is explicitly requested');
assert.strictEqual(getInjectedProvider('rabby'), global.window.ethereum, 'Must return Rabby provider');

// With Binance extension present
global.window = {
  binance: { isBinance: true },
  ethereum: { isRabby: true }
};
assert.strictEqual(getInjectedProvider('binance'), global.window.binance, 'Must return Binance provider when present');
assert.strictEqual(getInjectedProvider('rabby'), global.window.ethereum, 'Must return Rabby provider when requested');
console.log('✔ getInjectedProvider Isolation & Anti-Hijack Guard: PASS');

// 10. Test Binance Web3 QR Modal & Anti-Alert Click Handler
const mockContent = { innerHTML: '' };
const mockModal = {
  showModal: () => {},
  setAttribute: () => {}
};
global.document = {
  getElementById: (id) => {
    if (id === 'walletModal') return mockModal;
    if (id === 'walletModalContent') return mockContent;
    return null;
  }
};
global.window = {
  location: { origin: 'https://agent-id-monitor.vercel.app' },
  ethereum: { isRabby: true } // Rabby is active, no Binance desktop extension
};

// Clicking Binance Web3 should NOT pop an alert; it must render the QR code & Discover guide modal
handleBinanceWalletClick();
assert(mockContent.innerHTML.includes('Connect Binance Agentic Wallet'), 'Modal title must be Connect Binance Agentic Wallet');
assert(mockContent.innerHTML.includes('Binance Web3 Discover'), 'Must explain Discover flow');
assert(mockContent.innerHTML.includes('wallet-back-btn'), 'Must have back button');
console.log('✔ handleBinanceWalletClick Native In-Modal QR Fallback: PASS');

// Test Live Smart Stream Ticker Engine
const { renderSmartStreamTicker, openInspectModalByContract } = require('./app');

const mockTickerTrack = { innerHTML: '' };
let inspectedContract = null;
global.openModal = (target) => {
  inspectedContract = target.contract;
};

const mockInspectModal = { showModal: () => {}, close: () => {} };
const mockInspectContent = { innerHTML: '' };

global.document.getElementById = (id) => {
  if (id === 'walletModal') return mockModal;
  if (id === 'walletModalContent') return mockContent;
  if (id === 'tickerTrack') return mockTickerTrack;
  if (id === 'inspectModal') return mockInspectModal;
  if (id === 'modalContent') return mockInspectContent;
  return null;
};

const sampleDrops = [
  {
    name: 'Lazabit',
    symbol: 'LAZABIT',
    contract: '0x1111111111111111111111111111111111111111',
    smart_wallets: ['0x68271234567890abcdef1234567890abcdef1234'],
    total_minted: 500,
    max_supply: 1000,
    detected_at: '2026-11-04 16:42:01',
    block: 100
  },
  {
    name: 'MSN Core',
    symbol: 'MSN',
    contract: '0x2222222222222222222222222222222222222222',
    smart_wallets: ['wallet1.ai', 'wallet2.ai', 'wallet3.ai'],
    total_minted: 200,
    max_supply: 1000,
    detected_at: '2026-11-04 16:41:15',
    block: 99
  },
  {
    name: 'Raisin',
    symbol: 'RAISIN',
    contract: '0x3333333333333333333333333333333333333333',
    smart_wallets: [],
    total_minted: 1000,
    max_supply: 1000,
    detected_at: '2026-11-04 16:39:50',
    block: 98
  }
];

renderSmartStreamTicker(sampleDrops);
assert(mockTickerTrack.innerHTML.length > 0, 'Ticker track must be populated');
assert(mockTickerTrack.innerHTML.includes('0x6827...1234'), 'Must format long smart wallet address');
assert(mockTickerTrack.innerHTML.includes('3 Smart Wallets'), 'Must format multi-wallet entry');
assert(mockTickerTrack.innerHTML.includes('Sold Out (100% cap filled)'), 'Must flag sold out token');
assert(mockTickerTrack.innerHTML.includes('tag-smart'), 'Must have tag-smart badge');
assert(mockTickerTrack.innerHTML.includes('tag-snipe'), 'Must have tag-snipe badge');
console.log('✔ renderSmartStreamTicker Populates Infinite Ticker Tape: PASS');

// Test openInspectModalByContract
global.allDrops = sampleDrops;
openInspectModalByContract('0x2222222222222222222222222222222222222222');
assert(mockInspectContent.innerHTML.includes('0x2222222222222222222222222222222222222222'), 'Must populate modal content for matching contract');
console.log('✔ openInspectModalByContract Target Resolution: PASS');

// ==========================================
// Test Persistent Top Gainers Separate Memory
// ==========================================
const app = require('./app.js');
const { updateSavedTopMarkets, getSavedTopGainers, setSavedTopGainers, computeStats, loadSavedTopGainers } = app;

// 1. Initialize persistent memory with baseline legends
loadSavedTopGainers();
const initialMemory = getSavedTopGainers();
assert(initialMemory.length > 0, 'Saved top gainers must contain baseline legends');
const hasAgent8 = initialMemory.some(g => (g.name || '').includes('AGENT人生8') || (g.contract || '').toLowerCase() === '0xca45f13730154fbbf2d93acf169de83f52864f80');
assert(hasAgent8, 'Saved top gainers must contain AGENT人生8');

// 2. Simulate 150 incoming new drops that DO NOT contain AGENT人生8
const newBatch150 = [];
for (let i = 0; i < 150; i++) {
  newBatch150.push({
    contract: `0xaaaa${i.toString(16).padStart(36, '0')}`,
    name: `NewDrop #${i}`,
    symbol: `ND${i}`,
    standard: 'BEP-20 Token (18 decimals)',
    mint_price_str: 'FREE',
    mint_price_bnb: 0.0,
    mint_value_wei: '0',
    max_supply: 1000000,
    total_minted: 500,
    token_price_usd: i === 42 ? 0.00025 : 0.0, // only drop #42 has secondary trades
    holders: 100
  });
}

// Compute stats on the new 150-drop batch
const statsNew = computeStats(newBatch150);

// Verify that AGENT人生8 was NOT lost!
const updatedMemory = getSavedTopGainers();
const stillHasAgent8 = updatedMemory.some(g => (g.name || '').includes('AGENT人生8') || (g.contract || '').toLowerCase() === '0xca45f13730154fbbf2d93acf169de83f52864f80');
assert(stillHasAgent8, 'Historical winner AGENT人生8 must remain in separate saved memory after 150-drop batch roll-off!');

// Verify the new gainer from the 150 batch (NewDrop #42) was incrementally added to saved memory
const hasNewDrop42 = updatedMemory.some(g => g.name === 'NewDrop #42');
assert(hasNewDrop42, 'New gainer from incoming 150-drop batch must be incrementally added to saved top markets');

// Verify that statsNew.allGainers includes both historical legends and new gainer
assert(statsNew.allGainers.some(g => (g.name || '').includes('AGENT人生8')), 'stats.allGainers must include AGENT人生8');
assert(statsNew.allGainers.some(g => g.name === 'NewDrop #42'), 'stats.allGainers must include NewDrop #42');
assert(statsNew.topGainer !== null, 'stats.topGainer must not be null');

console.log(`✔ Persistent Top Gainers Separate Memory (Zero 2k+ Scans & Incremental 150 Merge): PASS [Tracking ${updatedMemory.length} winners, AGENT人生8 preserved]`);

// =========================================================================
// Test Option 2: Smart Money Intelligence & Win-Rate Tracking
// =========================================================================
const {
  toggleFollowWallet,
  getFollowedWallets,
  setFollowedWallets,
  computeSmartWalletLeaderboard,
  toggleDexChart
} = app;

console.log('Testing Option 2: Smart Money Intelligence & Win-Rate Tracking...');

// Test 1: Following & Unfollowing Smart Wallets
setFollowedWallets(new Set());
assert.strictEqual(getFollowedWallets().has('18881.ai'), false, 'Should not follow 18881.ai initially');

const followed = toggleFollowWallet('18881.ai');
assert.strictEqual(followed, true, 'toggleFollowWallet should return true when followed');
assert.strictEqual(getFollowedWallets().has('18881.ai'), true, 'followedWallets must contain 18881.ai');

const unfollowed = toggleFollowWallet('18881.ai');
assert.strictEqual(unfollowed, false, 'toggleFollowWallet should return false when unfollowed');
assert.strictEqual(getFollowedWallets().has('18881.ai'), false, 'followedWallets must not contain 18881.ai after unfollow');
console.log('✔ toggleFollowWallet (Follow & Unfollow State): PASS');

// Test 2: computeSmartWalletLeaderboard Aggregation & Ranking
toggleFollowWallet('shdjjdjd.ai'); // Follow a high win rate hunter
const sampleHunterDrops = [
  {
    name: 'MoonDrop',
    symbol: 'MOON',
    contract: '0x9999999999999999999999999999999999999999',
    standard: 'BEP-20 Token (18 decimals)',
    smart_wallets: ['shdjjdjd.ai', 'newhunter.ai'],
    lp_usd: 12000,
    token_price_usd: 0.05
  }
];

const leaderboard = computeSmartWalletLeaderboard(sampleHunterDrops);
assert(leaderboard.length >= 10, 'Leaderboard must contain baseline hunters');
const shdEntry = leaderboard.find(h => h.name.toLowerCase() === 'shdjjdjd.ai');
assert(shdEntry !== undefined, 'shdjjdjd.ai must be in leaderboard');
assert.strictEqual(shdEntry.isFollowed, true, 'shdjjdjd.ai must be marked as isFollowed=true');
assert(shdEntry.wins >= 9, 'shdjjdjd.ai must have at least 9 wins');
assert(shdEntry.winRate >= 70, 'shdjjdjd.ai must have winRate >= 70%');

const newEntry = leaderboard.find(h => h.name.toLowerCase() === 'newhunter.ai');
assert(newEntry !== undefined, 'newhunter.ai must be incrementally aggregated from new drop');
assert.strictEqual(newEntry.wins, 1, 'newhunter.ai must have 1 win from MoonDrop');
assert.strictEqual(newEntry.winRate, 100.0, 'newhunter.ai winRate must be 100%');
console.log('✔ computeSmartWalletLeaderboard (Aggregation & Follow State): PASS');

// =========================================================================
// Test Option 3: Secondary Market Bonding Curve & DEX Live Chart Toggle
// =========================================================================
console.log('Testing Option 3: Secondary Market Bonding Curve & Live Chart...');

// Test 3: DexScreener Embed Toggle
const mockDexChartWrap = { style: { display: 'none' }, innerHTML: '' };
const mockChartLabel = { textContent: '' };
const mockChartIcon = { textContent: '' };

const origGetById = global.document.getElementById;
global.document.getElementById = (id) => {
  if (id === 'dexChartWrap') return mockDexChartWrap;
  if (id === 'chartToggleLabel') return mockChartLabel;
  if (id === 'chartToggleIcon') return mockChartIcon;
  return origGetById ? origGetById(id) : null;
};

// Toggle Open
toggleDexChart('0xc4bf2518aa953304170753388f83277eb9588a7a');
assert.strictEqual(mockDexChartWrap.style.display, 'block', 'Chart container must be displayed');
assert(mockDexChartWrap.innerHTML.includes('dexscreener.com/bsc/0xc4bf2518aa953304170753388f83277eb9588a7a'), 'Chart iframe must target contract address');
assert(mockDexChartWrap.innerHTML.includes('theme=dark'), 'Chart iframe must use dark theme');
assert.strictEqual(mockChartLabel.textContent, 'Hide Live Chart', 'Label must update to Hide Live Chart');

// Toggle Close
toggleDexChart('0xc4bf2518aa953304170753388f83277eb9588a7a');
assert.strictEqual(mockDexChartWrap.style.display, 'none', 'Chart container must be hidden');
assert.strictEqual(mockDexChartWrap.innerHTML, '', 'Chart iframe must be unloaded on close');
assert.strictEqual(mockChartLabel.textContent, 'Show Live DEX Chart', 'Label must update to Show Live DEX Chart');
console.log('✔ toggleDexChart (Lazy Embed Loading & Cleanup): PASS');

// Test 4: 2,116 Smart Hunters Scaling & Activity Navigation
const fs = require('fs');
const seedData = JSON.parse(fs.readFileSync('seeded_smart_hunters.json', 'utf8'));
assert.strictEqual(seedData.length, 2116, 'seeded_smart_hunters.json must contain all 2,116 hunters');

const { setSmartHuntersCache, viewWalletActivity, mergeDropsIntoAllDrops, getFilteredDrops } = app;
setSmartHuntersCache(seedData);

const fullLeaderboard = computeSmartWalletLeaderboard([]);
assert.strictEqual(fullLeaderboard.length, 2116, 'Leaderboard must scale to all 2,116 tracked hunters');
const top1 = fullLeaderboard[0];
assert.strictEqual(top1.name, 'adadcarry.ai', 'Top hunter must be adadcarry.ai');
assert.strictEqual(top1.wins, 11, 'adadcarry.ai must have 11 confirmed wins');
console.log('✔ 2,116 Smart Hunters Full Scale Leaderboard: PASS');

// Test 5: viewWalletActivity
const mockSearchInput = { value: '' };
global.document.getElementById = (id) => {
  if (id === 'searchInput') return mockSearchInput;
  if (id === 'followedFilterCount') return { textContent: '' };
  return null;
};
global.window.scrollTo = () => {};

viewWalletActivity('adadcarry.ai');
assert.strictEqual(mockSearchInput.value, 'adadcarry.ai', 'Search input must be set to wallet name');
console.log('✔ viewWalletActivity (Search and Filter Redirection): PASS');

// Test 6: Smart Drops Merge & Followed / Search Activity Resolution
const smartDropsRaw = JSON.parse(fs.readFileSync('seeded_smart_drops.json', 'utf8'));
assert(smartDropsRaw.length >= 1900, 'seeded_smart_drops.json must contain ~1,900 smart drops');
const addedDrops = mergeDropsIntoAllDrops(smartDropsRaw);
assert(addedDrops > 1800, 'Must merge new smart drops into allDrops');

// Verify adadcarry.ai activity when searching
searchQuery = 'adadcarry.ai';
activeFilter = 'ALL';
const adadSearchResults = getFilteredDrops();
assert.strictEqual(adadSearchResults.length, 23, 'Must find exactly 23 historical drops for adadcarry.ai');
console.log(`✔ Search by Smart Hunter (.ai): PASS [Found ${adadSearchResults.length} drops for adadcarry.ai]`);

// Verify adadcarry.ai activity under FOLLOWED filter
toggleFollowWallet('adadcarry.ai');
activeFilter = 'FOLLOWED';
searchQuery = '';
const followedResults = getFilteredDrops();
assert(followedResults.length >= 23, 'Followed filter must return all drops of followed hunter');
console.log(`✔ Followed Filter Activity Resolution: PASS [Found ${followedResults.length} drops for followed hunters]`);

// Restore getElementById
global.document.getElementById = origGetById;

// ========================================================
// Test Competitor Features: Dev Root, Wallet Tab, Holder Dist
// ========================================================
console.log('Testing Competitor Features (Dev Root, Wallet Tab, Holder Dist)...');

const {
  openModal,
  computeHolderDistribution,
  renderWalletsTabLeaderboard,
  setWalletsTabFilter,
  setWalletsTabSort,
  getWalletsTabSortMode,
  onWalletsTabSearch,
  loadMoreTabWallets
} = app;

// Test 7: Dev Root Tracing & Dev Cluster Badge in Modal
mockInspectContent.innerHTML = '';
global.document.getElementById = (id) => {
  if (id === 'inspectModal') return mockInspectModal;
  if (id === 'modalContent') return mockInspectContent;
  if (id === 'walletsLeaderboardContainer') return { style: { display: 'none' } };
  if (id === 'dropsTable') return { style: { display: '' } };
  if (id === 'loadMoreWrap') return { style: { display: 'none' } };
  if (id === 'tabWalletsTbody') return { innerHTML: '' };
  if (id === 'walletsTabCount') return { textContent: '' };
  if (id === 'walletsTabSummaryPill') return { textContent: '' };
  if (id === 'walletsTabFollowedCount') return { textContent: '' };
  if (id === 'footerCount') return { textContent: '' };
  return origGetById ? origGetById(id) : null;
};

const clusterDrop = {
  contract: '0x1111111111111111111111111111111111111111',
  deployer: '0x2222222222222222222222222222222222222222',
  dev_root: '0x9999999999999999999999999999999999999999',
  cluster_devs: 4,
  cluster_n: 12,
  name: 'Cluster Token',
  symbol: 'CLUST',
  standard: 'BEP-20',
  hold: {
    dev: 15.5,
    insider: 18.0,
    sniper: 6.0,
    smart: 10.0,
    fresh: 3.5,
    public: 47.0
  }
};

openModal(clusterDrop);
assert(mockInspectContent.innerHTML.includes('0x9999999999'), 'Modal must link Dev Funding Root address');
assert(mockInspectContent.innerHTML.includes('Dev Cluster: 4 linked wallets / 12 drops') && mockInspectContent.innerHTML.includes('4 Linked / 12 Drops'), 'Modal must display Dev Cluster badge and tooltip');
assert(mockInspectContent.innerHTML.includes('Holder Distribution Breakdown'), 'Modal must display Holder Distribution section');
assert(mockInspectContent.innerHTML.includes('High Dev Holding (15.5% > 10%)'), 'Modal must trigger High Dev Holding alert');
assert(mockInspectContent.innerHTML.includes('Insider Concentration (18.0%)'), 'Modal must trigger Insider Concentration alert');
assert(mockInspectContent.innerHTML.includes('holder-dist-seg dev'), 'Must contain dev segment');
assert(mockInspectContent.innerHTML.includes('holder-dist-seg public'), 'Must contain public segment');
console.log('✔ Feature 1 & 3 (Dev Root Tracing, Dev Cluster Badge & Holder Distribution): PASS');

// Test 7b: Unminted Primary Fair-Mint Phase & Standalone Deployer Inflow Trace
const unmintedDrop = {
  contract: '0x6d91b203e663ef5dfd8e168811cad81a87984b29',
  deployer: '0xcc7fade382e870072495a1a8bd90e12ff78ba1dd',
  total_minted: 0,
  max_supply: 1000,
  holders: 1,
  name: 'Signal Ray',
  symbol: 'SGRA',
  standard: 'Custom Smart Contract'
};

const unmintedDist = computeHolderDistribution(unmintedDrop);
assert.strictEqual(unmintedDist.isUnminted, true, 'Drop with 0 minted must be identified as isUnminted');
assert.strictEqual(unmintedDist.dev, 0, 'Dev pre-mine must be 0 for fair mint');
assert.strictEqual(unmintedDist.public, 100, 'Public allocation must be 100% available');

openModal(unmintedDrop);
assert(mockInspectContent.innerHTML.includes('Primary Fair-Mint Phase'), 'Must display Primary Fair-Mint Phase badge');
assert(mockInspectContent.innerHTML.includes('Zero Dev Pre-Mine'), 'Must clarify zero dev pre-mine');
assert(mockInspectContent.innerHTML.includes('holder-dist-seg-unminted'), 'Must render unminted phase progress bar');
assert(mockInspectContent.innerHTML.includes('Trace ↗'), 'Must link to BscScan inflow trace');
assert(mockInspectContent.innerHTML.includes('Standalone (1 drop)'), 'Must mark unclustered deployer as standalone');
console.log('✔ Feature 1 & 3b (Unminted Fair-Mint State & BscScan Inflow Tracing): PASS');

// Test 7c: Dynamic Minted Distribution with Smart Money & Snipers
const mintedDrop = {
  contract: '0xb736d06c52fff74cac4619200c0f3be24c777591',
  deployer: '0x185992f983307cd50d4d668e4d0fa42e47d08fe8',
  total_minted: 50000,
  max_supply: 100000,
  holders: 35,
  smart_wallets: ['adadcarry.ai', 'qifeiba.ai'],
  buys_1h: 25,
  percent_claimed: 50.0,
  name: 'Cipher Alpha',
  symbol: 'CIPA',
  standard: 'BEP-20'
};

const mintedDist = computeHolderDistribution(mintedDrop);
assert.strictEqual(mintedDist.isUnminted, false, 'Circulating drop must not be unminted');
assert(mintedDist.smart > 0, 'Smart money share must be computed from smart hunters');
assert(mintedDist.sniper > 0, 'Sniper share must be computed from rapid buys');
assert(mintedDist.public > 0, 'Public community share must be computed');
console.log('✔ Feature 1 & 3c (Dynamic Minted Distribution with Smart Money & Snipers): PASS');

// Test 8: Dedicated Wallet Leaderboard Tab Rendering & Filters
const mockWalletsTbody = { innerHTML: '' };
const mockSummaryPill = { textContent: '' };
const mockFollowedCount = { textContent: '' };
const mockWalletsCount = { textContent: '' };
const mockLoadMoreWrap = { style: { display: 'none' } };

global.document.getElementById = (id) => {
  if (id === 'tabWalletsTbody') return mockWalletsTbody;
  if (id === 'walletsTabSummaryPill') return mockSummaryPill;
  if (id === 'walletsTabFollowedCount') return mockFollowedCount;
  if (id === 'walletsTabCount') return mockWalletsCount;
  if (id === 'tabWalletsLoadMoreWrap') return mockLoadMoreWrap;
  if (id === 'footerCount') return { textContent: '' };
  return null;
};
global.document.querySelectorAll = () => [];

renderWalletsTabLeaderboard();
assert(mockWalletsTbody.innerHTML.includes('adadcarry.ai'), 'Wallet leaderboard must render top hunters including adadcarry.ai');
assert(mockWalletsTbody.innerHTML.includes('🥇'), 'Top hunter must display gold medal badge');
assert(!mockWalletsTbody.innerHTML.includes('WIN RATE'), 'Headers should not be in tbody');

// Test Search in Wallet Leaderboard
onWalletsTabSearch('adadcarry');
assert(mockWalletsTbody.innerHTML.includes('adadcarry.ai'), 'Searching adadcarry must return matching row');

onWalletsTabSearch('nonexistentwalletxyz');
assert(mockWalletsTbody.innerHTML.includes('No matching Agent ID smart wallets found'), 'Empty state must show when no match');

// Reset Search and Test Filter High Win
onWalletsTabSearch('');
setWalletsTabFilter('HIGH_WIN');
assert(mockWalletsTbody.innerHTML.includes('shdjjdjd.ai'), 'High win filter must include shdjjdjd.ai (75% win rate)');

// Test Filter Followed
setWalletsTabFilter('FOLLOWED');
assert(mockWalletsTbody.innerHTML.includes('adadcarry.ai'), 'Followed filter must include followed hunter');

setWalletsTabFilter('ALL');
console.log('✔ Feature 2 (Dedicated Wallet Leaderboard Tab, Filtering & Search): PASS');

// Test Sorting by Win Rate, Wins, Picks, and Reset
setWalletsTabSort('WIN_RATE');
assert.strictEqual(getWalletsTabSortMode(), 'WIN_RATE_DESC', 'Sorting mode must toggle to WIN_RATE_DESC');
assert(mockWalletsTbody.innerHTML.includes('100.0%') || mockWalletsTbody.innerHTML.includes('75.0%'), 'Win Rate sort must show top win rates');

setWalletsTabSort('WIN_RATE');
assert.strictEqual(getWalletsTabSortMode(), 'WIN_RATE_ASC', 'Second click toggles to WIN_RATE_ASC');

setWalletsTabSort('WINS');
assert.strictEqual(getWalletsTabSortMode(), 'WINS_DESC', 'Sorting mode must toggle to WINS_DESC');
assert(mockWalletsTbody.innerHTML.includes('adadcarry.ai'), 'Wins sort should have top winner adadcarry.ai');

setWalletsTabSort('PICKS');
assert.strictEqual(getWalletsTabSortMode(), 'PICKS_DESC', 'Sorting mode must toggle to PICKS_DESC');

setWalletsTabSort('DEFAULT');
assert.strictEqual(getWalletsTabSortMode(), 'DEFAULT', 'Sorting mode resets to DEFAULT');
console.log('✔ Feature 2b (Dedicated Wallet Leaderboard Sorting by Win Rate, Wins, Picks): PASS');

// ========================================================
// Test: Manual Tab Sequence Customization & Drag-and-Drop
// ========================================================
console.log('Testing Manual Tab Sequence Customization & Drag-and-Drop...');

const {
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
  setupFilterPillsDragAndDrop
} = app;

// Ensure localStorage mock is available
if (!global.localStorage) {
  global.localStorage = {
    store: {},
    getItem(k) { return this.store[k] !== undefined ? this.store[k] : null; },
    setItem(k, v) { this.store[k] = String(v); },
    removeItem(k) { delete this.store[k]; },
    clear() { this.store = {}; }
  };
} else {
  global.localStorage.clear();
}

// 1. Verify DEFAULT_PILLS_SEQUENCE contents
assert.strictEqual(DEFAULT_PILLS_SEQUENCE.length, 12, 'Default pills sequence must have 12 items');
assert.strictEqual(DEFAULT_PILLS_SEQUENCE[0], 'ALL', 'First item must be ALL');
assert.strictEqual(DEFAULT_PILLS_SEQUENCE[1], 'LAUNCHPAD', 'Second item must be LAUNCHPAD');
assert(DEFAULT_PILLS_SEQUENCE.includes('SNIPER_READY'), 'Must include SNIPER_READY');
assert(DEFAULT_PILLS_SEQUENCE.includes('NFT_SURGE'), 'Must include NFT_SURGE');
assert(DEFAULT_PILLS_SEQUENCE.includes('pillSmartLeaderboard'), 'Must include pillSmartLeaderboard');
console.log('✔ DEFAULT_PILLS_SEQUENCE integrity: PASS');

// 2. Mock DOM Elements for Pills and Container
class MockElement {
  constructor(tag, attrs = {}) {
    this.tagName = tag.toUpperCase();
    this.attributes = { ...attrs };
    this.classList = new Set();
    this.classList.contains = (cls) => this.classList.has(cls);
    this.classList.remove = (...cls) => cls.forEach(c => this.classList.delete(c));
    this.classList.toggle = (cls, force) => {
      const has = this.classList.has(cls);
      const shouldHave = force !== undefined ? force : !has;
      if (shouldHave) this.classList.add(cls);
      else this.classList.delete(cls);
      return shouldHave;
    };
    this.children = [];
    this.parentNode = null;
    this._textContent = attrs.textContent || '';
    this.style = {};
    this.eventListeners = {};
  }
  get textContent() {
    if (this._textContent) return this._textContent;
    if (this._innerHTML) return this._innerHTML;
    return this.children.map(c => c.textContent).join(' ');
  }
  set textContent(val) {
    this._textContent = String(val);
  }
  get innerHTML() {
    if (this.children.length > 0) {
      return this.children.map(c => c.innerHTML || c.textContent || '').join('');
    }
    return this._innerHTML !== undefined ? this._innerHTML : '';
  }
  set innerHTML(val) {
    this._innerHTML = val;
    this._textContent = val;
    if (val === '') {
      this.children = [];
    }
  }
  getAttribute(name) {
    return this.attributes[name] !== undefined ? this.attributes[name] : null;
  }
  setAttribute(name, val) {
    this.attributes[name] = String(val);
  }
  removeAttribute(name) {
    delete this.attributes[name];
  }
  get id() {
    return this.attributes.id || '';
  }
  set id(val) {
    this.attributes.id = val;
  }
  addEventListener(event, fn) {
    if (!this.eventListeners[event]) this.eventListeners[event] = [];
    this.eventListeners[event].push(fn);
  }
  appendChild(child) {
    if (child.tagName === 'FRAGMENT') {
      while (child.children.length > 0) {
        const c = child.children.shift();
        this.appendChild(c);
      }
      return;
    }
    if (child.parentNode) {
      child.parentNode.removeChild(child);
    }
    child.parentNode = this;
    this.children.push(child);
  }
  insertBefore(newChild, refChild) {
    if (newChild.parentNode) {
      newChild.parentNode.removeChild(newChild);
    }
    newChild.parentNode = this;
    if (!refChild) {
      this.children.push(newChild);
      return;
    }
    const idx = this.children.indexOf(refChild);
    if (idx === -1) {
      this.children.push(newChild);
    } else {
      this.children.splice(idx, 0, newChild);
    }
  }
  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx !== -1) {
      this.children.splice(idx, 1);
      child.parentNode = null;
    }
  }
  remove() {
    if (this.parentNode) {
      this.parentNode.removeChild(this);
    }
  }
  querySelector(sel) {
    for (const child of this.children) {
      if (sel.startsWith('[data-filter="') && sel.endsWith('"]')) {
        const val = sel.slice(14, -2);
        if (child.getAttribute('data-filter') === val) return child;
      }
      if (sel.startsWith('#') && child.id === sel.slice(1)) return child;
    }
    return null;
  }
  querySelectorAll(sel) {
    return this.children.filter(c => {
      if (sel.includes('.pill') && c.classList.has('pill')) return true;
      if (sel.includes('.pill-action') && c.classList.has('pill-action')) return true;
      return false;
    });
  }
  cloneNode(deep) {
    const clone = new MockElement(this.tagName, { ...this.attributes, textContent: this.textContent });
    return clone;
  }
}

// Build mock pills
const mockPillElements = {};
const mockContainer = new MockElement('div', { id: 'filterPillsContainer' });
DEFAULT_PILLS_SEQUENCE.forEach(key => {
  const isAction = key === 'pillSmartLeaderboard';
  const pill = new MockElement('button', {
    id: isAction ? key : `pill_${key}`,
    textContent: key
  });
  if (!isAction) pill.setAttribute('data-filter', key);
  pill.classList.add(isAction ? 'pill-action' : 'pill');
  mockContainer.appendChild(pill);
  mockPillElements[key] = pill;
});

const mockResetBtn = new MockElement('button', { id: 'resetPillOrderBtn' });
mockResetBtn.style.display = 'none';

const mockReorderList = new MockElement('div', { id: 'reorderPillsList' });
const mockReorderModal = new MockElement('dialog', { id: 'reorderPillsModal' });
mockReorderModal.showModal = () => { mockReorderModal.open = true; };
mockReorderModal.close = () => { mockReorderModal.open = false; };

global.document.getElementById = (id) => {
  if (id === 'filterPillsContainer') return mockContainer;
  if (id === 'resetPillOrderBtn') return mockResetBtn;
  if (id === 'reorderPillsList') return mockReorderList;
  if (id === 'reorderPillsModal') return mockReorderModal;
  if (mockPillElements[id]) return mockPillElements[id];
  return null;
};
global.document.createElement = (tag) => new MockElement(tag);
global.document.createDocumentFragment = () => new MockElement('fragment');

// 3. Test getPillKey
assert.strictEqual(getPillKey(mockPillElements['ALL']), 'ALL', 'getPillKey should return data-filter value');
assert.strictEqual(getPillKey(mockPillElements['pillSmartLeaderboard']), 'pillSmartLeaderboard', 'getPillKey should return ID when data-filter absent');
console.log('✔ getPillKey: PASS');

// 4. Test getCurrentPillSequence
const seqInitial = getCurrentPillSequence();
assert.deepStrictEqual(seqInitial, DEFAULT_PILLS_SEQUENCE, 'Current sequence should match default sequence initially');
console.log('✔ getCurrentPillSequence: PASS');

// 5. Test movePill (move down and move up)
movePill('ALL', 1); // Swap ALL and LAUNCHPAD -> [LAUNCHPAD, ALL, ...]
const seqAfterSwap = getCurrentPillSequence();
assert.strictEqual(seqAfterSwap[0], 'LAUNCHPAD', 'LAUNCHPAD should now be first');
assert.strictEqual(seqAfterSwap[1], 'ALL', 'ALL should now be second');
assert.strictEqual(mockResetBtn.style.display, 'inline-flex', 'Reset button should appear when sequence is customized');

const savedInStorage = JSON.parse(global.localStorage.getItem(PILL_SEQUENCE_STORAGE_KEY));
assert.deepStrictEqual(savedInStorage, seqAfterSwap, 'Storage must match new sequence');
console.log('✔ movePill & Persistence: PASS');

// 6. Test movePillToTop
movePillToTop('pillSmartLeaderboard'); // Move leaderboard to top -> [pillSmartLeaderboard, LAUNCHPAD, ALL, ...]
const seqAfterTop = getCurrentPillSequence();
assert.strictEqual(seqAfterTop[0], 'pillSmartLeaderboard', 'pillSmartLeaderboard should be at top index 0');
console.log('✔ movePillToTop: PASS');

// 7. Test renderReorderModalList
openReorderModal();
assert(mockReorderModal.open, 'Reorder modal should be opened');
assert.strictEqual(mockReorderList.children.length, 12, 'Reorder list must render 12 items');
assert(mockReorderList.children[0].textContent.includes('#1'), 'First item must show #1');
console.log('✔ openReorderModal & renderReorderModalList: PASS');

// 8. Test resetPillSequence
resetPillSequence();
const seqAfterReset = getCurrentPillSequence();
assert.deepStrictEqual(seqAfterReset, DEFAULT_PILLS_SEQUENCE, 'Reset sequence must restore DEFAULT_PILLS_SEQUENCE');
assert.strictEqual(global.localStorage.getItem(PILL_SEQUENCE_STORAGE_KEY), null, 'Storage key must be cleared on reset');
assert.strictEqual(mockResetBtn.style.display, 'none', 'Reset button should be hidden on default sequence');
console.log('✔ resetPillSequence: PASS');

// 9. Test setupFilterPillsDragAndDrop
setupFilterPillsDragAndDrop();
const firstPill = mockContainer.children[0];
assert(firstPill.eventListeners['dragstart'] !== undefined, 'Pill should have dragstart listener');
assert(firstPill.eventListeners['dragover'] !== undefined, 'Pill should have dragover listener');
assert(firstPill.eventListeners['drop'] !== undefined, 'Pill should have drop listener');
console.log('✔ setupFilterPillsDragAndDrop: PASS');

// ========================================================
// Test: Toast Notifications & Clean Feedback Styling
// ========================================================
console.log('Testing Toast Notifications & Clean Feedback Styling...');

const {
  showToast,
  copyDonationAddress,
  DONATION_WALLET
} = app;

const mockToastContainer = new MockElement('div', { id: 'toastContainer' });
const origGetElem = global.document.getElementById;
global.document.getElementById = (id) => {
  if (id === 'toastContainer') return mockToastContainer;
  return origGetElem ? origGetElem(id) : null;
};

// Clipboard Mock
let clipboardContent = '';
if (typeof navigator !== 'undefined') {
  Object.defineProperty(navigator, 'clipboard', {
    value: {
      writeText: async (txt) => { clipboardContent = txt; }
    },
    configurable: true
  });
} else {
  global.navigator = {
    clipboard: {
      writeText: async (txt) => { clipboardContent = txt; }
    }
  };
}

const mockTipBtn = new MockElement('button', { textContent: '💎 Tip Dev' });
copyDonationAddress(mockTipBtn, 'header');

assert.strictEqual(clipboardContent, DONATION_WALLET, 'Clipboard must receive full 42-character donation address');
assert.strictEqual(mockTipBtn.textContent, '✓ Copied!', 'Button text must briefly update to ✓ Copied!');

assert.strictEqual(mockToastContainer.children.length, 1, 'Toast container must receive 1 toast');
const toastEl = mockToastContainer.children[0];
const toastHtml = toastEl.innerHTML;

// 1. Verify duplicate emoji was removed from title
assert(toastHtml.includes('toast-icon">💎<'), 'Toast must have diamond icon');
assert(toastHtml.includes('toast-title">DONATION WALLET COPIED<'), 'Toast title must NOT duplicate diamond icon');
assert(!toastHtml.includes('toast-title">💎'), 'Toast title must be clean text without duplicate emoji');

// 2. Verify shortened address and meta line
const expectedShortAddr = `${DONATION_WALLET.slice(0, 10)}...${DONATION_WALLET.slice(-8)}`;
assert(toastHtml.includes(expectedShortAddr), 'Toast must contain shortened address preview');
assert(toastHtml.includes('Address copied to clipboard · Thank you for supporting!'), 'Toast meta must confirm clipboard copy');

// 3. Test generic showToast emoji cleaning on other alert types
mockToastContainer.children = [];
showToast('⭐ SMART WALLET FOLLOWED', 'Tracking 18881.ai', '⭐');
assert(mockToastContainer.children[0].innerHTML.includes('toast-title">SMART WALLET FOLLOWED<'), 'Star emoji should be cleaned from title');

showToast('🎯 SNIPER READY ALERT', 'Project is 60% minted', '🎯');
assert(mockToastContainer.children[1].innerHTML.includes('toast-title">SNIPER READY ALERT<'), 'Target emoji should be cleaned from title');

console.log('✔ Toast Notifications & Clean Feedback: PASS');

console.log('Testing Dedicated Secondary Market Tab & Metrics...');
const {
  isSecondaryMarketAsset,
  getSanitizedMcap,
  updateSecondaryTabCount,
  updateSecondaryBannerUI,
  setActiveTab,
  setActiveFilter,
  setSearchQuery,
  setAllDrops,
  setSortMode,
  getSortMode,
  renderTable
} = app;

// Test 1: Secondary Market Asset Detection
const testGang = {
  name: "Gang",
  symbol: "GANG",
  contract: "0x0c6302401aa5325c2e36f170f52c00b1d220b405",
  market_cap: 2426607,
  lp_usd: 214316,
  volume_24h: 680690,
  token_price_usd: 0.002451
};

const testPtah = {
  name: "Ptah",
  symbol: "PTAH",
  contract: "0x999980fb8dff1799c7e9d7f71bca899e43d39999",
  market_cap: 320058,
  lp_usd: 424,
  volume_24h: 219,
  token_price_usd: 0.000320
};

const testRegularMint = {
  name: "Unpaired Mint",
  symbol: "MINT",
  contract: "0x123",
  lp_usd: 0,
  market_cap: 0,
  volume_24h: 0,
  token_price_usd: 0
};

const testPegged = {
  name: "Binance-Peg USD",
  symbol: "USDT",
  contract: "0x55d398326f99059ff775485246999027b3197955",
  market_cap: 5000000000,
  lp_usd: 10000000,
  volume_24h: 50000000,
  token_price_usd: 1.0
};

assert.strictEqual(isSecondaryMarketAsset(testGang), true, 'GANG must be identified as secondary market asset');
assert.strictEqual(isSecondaryMarketAsset(testPtah), true, 'PTAH must be identified as secondary market asset');
assert.strictEqual(isSecondaryMarketAsset(testRegularMint), false, 'Unpaired mint must NOT be secondary market asset');
assert.strictEqual(isSecondaryMarketAsset(testPegged), false, 'Pegged USDT must be excluded from secondary market tab');
console.log('✔ isSecondaryMarketAsset detection & pegged exclusion: PASS');

// Test 2: Filtering & Default Sorting by Valuation Descending
setActiveFilter('ALL');
setSearchQuery('');
setAllDrops([testRegularMint, testPtah, testPegged, testGang]);
setActiveTab('SECONDARY');
const secFiltered = getFilteredDrops();
assert.strictEqual(secFiltered.length, 2, 'Should filter strictly to the 2 secondary market tokens');
assert.strictEqual(secFiltered[0].symbol, 'GANG', 'Top valuation token (GANG $2.43M) must sort first');
assert.strictEqual(secFiltered[1].symbol, 'PTAH', 'PTAH ($320K) must sort second');
console.log('✔ Secondary Market Tab filtering & default valuation sort: PASS');

// Test 2b: Market Cap Ascending vs Descending Sort
setSortMode('MCAP_ASC');
const secAsc = getFilteredDrops();
assert.strictEqual(secAsc[0].symbol, 'PTAH', 'Ascending sort: PTAH ($320K) must come before GANG ($2.43M)');
assert.strictEqual(secAsc[1].symbol, 'GANG', 'Ascending sort: GANG ($2.43M) must come second');

setSortMode('MCAP_DESC');
const secDesc = getFilteredDrops();
assert.strictEqual(secDesc[0].symbol, 'GANG', 'Descending sort: GANG ($2.43M) must come first');
assert.strictEqual(secDesc[1].symbol, 'PTAH', 'Descending sort: PTAH ($320K) must come second');
console.log('✔ Secondary Market Market Cap High/Low and Low/High Two-Way Sort: PASS');

// Test 3: Tab Count and Overview Banner UI
const mockTabBadge = new MockElement('span');
const mockTotalMcap = new MockElement('span');
const mockTotalVol = new MockElement('span');
const mockTotalLp = new MockElement('span');
const mockTopAsset = new MockElement('span');
const mockSecBanner = new MockElement('div');

const prevGetElem = global.document.getElementById;
global.document.getElementById = (id) => {
  if (id === 'secondaryTabCount') return mockTabBadge;
  if (id === 'secondaryMarketBanner') return mockSecBanner;
  if (id === 'secTotalMcap') return mockTotalMcap;
  if (id === 'secTotalVol') return mockTotalVol;
  if (id === 'secTotalLp') return mockTotalLp;
  if (id === 'secTopAsset') return mockTopAsset;
  return prevGetElem ? prevGetElem(id) : null;
};

updateSecondaryTabCount();
assert.strictEqual(String(mockTabBadge.textContent), '2', 'Secondary Tab badge count must display 2');

updateSecondaryBannerUI();
assert(mockTotalMcap.textContent.includes('2.7M') || mockTotalMcap.textContent.includes('2.75M'), 'Overview banner valuation must sum tracked assets');
assert(mockTopAsset.textContent.includes('Gang'), 'Top valuation asset must be Gang');
console.log('✔ Secondary Tab Badge & Overview Banner UI: PASS');

// Reset tab back to RADAR
setActiveTab('RADAR');

// Test 4: Interactive Table Column Header Sorting
console.log('Testing Interactive Table Column Header Sorting...');
const mockThBlock = new MockElement('th', { id: 'thColBlock' });
const mockThProgress = new MockElement('th', { id: 'thCol11' });
const mockThHolders = new MockElement('th', { id: 'thCol9' });
const mockTbody = new MockElement('tbody', { id: 'tableBody' });

const savedGetElem = global.document.getElementById;
global.document.getElementById = (id) => {
  if (id === 'thColBlock') return mockThBlock;
  if (id === 'thCol11') return mockThProgress;
  if (id === 'thCol9') return mockThHolders;
  if (id === 'tableBody') return mockTbody;
  return savedGetElem ? savedGetElem(id) : null;
};

// Render table in RADAR mode
setActiveTab('RADAR');
setSortMode('NEWEST');
renderTable();

// Verify MINT PROGRESS header is configured and clickable
assert(mockThProgress.innerHTML.includes('MINT PROGRESS'), 'Header must contain MINT PROGRESS');
assert(typeof mockThProgress.onclick === 'function', 'MINT PROGRESS header must have onclick handler');

// First click: Sorts Progress Descending (100% -> 0%)
mockThProgress.onclick();
assert.strictEqual(getSortMode(), 'PROGRESS_DESC', '1st click on MINT PROGRESS must set PROGRESS_DESC');

// Second click: Sorts Progress Ascending (0% -> 100%)
mockThProgress.onclick();
assert.strictEqual(getSortMode(), 'PROGRESS_ASC', '2nd click on MINT PROGRESS must set PROGRESS_ASC');

// Third click: Resets to NEWEST
mockThProgress.onclick();
assert.strictEqual(getSortMode(), 'NEWEST', '3rd click on MINT PROGRESS must reset to NEWEST');

// Test BLOCK column toggling
assert(typeof mockThBlock.onclick === 'function', 'BLOCK header must have onclick handler');
mockThBlock.onclick();
assert.strictEqual(getSortMode(), 'BLOCK_ASC', 'Clicking BLOCK when in NEWEST must toggle to BLOCK_ASC');
mockThBlock.onclick();
assert.strictEqual(getSortMode(), 'NEWEST', 'Clicking BLOCK when in BLOCK_ASC must toggle to NEWEST');

// Test HOLDERS column toggling
assert(typeof mockThHolders.onclick === 'function', 'HOLDERS header must have onclick handler');
mockThHolders.onclick();
assert.strictEqual(getSortMode(), 'HOLDERS_DESC', 'Clicking HOLDERS must set HOLDERS_DESC');
mockThHolders.onclick();
assert.strictEqual(getSortMode(), 'HOLDERS_ASC', '2nd click on HOLDERS must set HOLDERS_ASC');
mockThHolders.onclick();
assert.strictEqual(getSortMode(), 'NEWEST', '3rd click on HOLDERS must reset to NEWEST');

global.document.getElementById = savedGetElem;
console.log('✔ Interactive Table Column Header Sorting: PASS');

// ==========================================
// Test Universal DexScreener Integration for Trading Assets
// ==========================================
console.log('Testing Universal DexScreener Integration for Trading Assets...');
const { getDexScreenerUrl } = app;

// 1. Test getDexScreenerUrl Resolver
assert.strictEqual(
  getDexScreenerUrl('0x6ced5c6d3f913b48d59fa07abbfe9060c409087c'),
  'https://dexscreener.com/bsc/0x6ced5c6d3f913b48d59fa07abbfe9060c409087c',
  'String address must resolve to BSC DexScreener URL'
);

const dropCustomPair = {
  contract: '0x1111111111111111111111111111111111111111',
  pair_url: 'https://dexscreener.com/bsc/0xpair123456789'
};
assert.strictEqual(
  getDexScreenerUrl(dropCustomPair),
  'https://dexscreener.com/bsc/0xpair123456789',
  'Item with pair_url must use exact pair_url'
);

const dropPairAddr = {
  contract: '0x2222222222222222222222222222222222222222',
  pair_address: '0x9876543210987654321098765432109876543210'
};
assert.strictEqual(
  getDexScreenerUrl(dropPairAddr),
  'https://dexscreener.com/bsc/0x9876543210987654321098765432109876543210',
  'Item with pair_address must resolve to BSC pair URL'
);

const dropNative = {
  contract: '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
};
assert.strictEqual(
  getDexScreenerUrl(dropNative),
  '',
  'Native BNB placeholder must not resolve to token DexScreener'
);
console.log('✔ getDexScreenerUrl URL resolution: PASS');

// 2. Test Secondary Market Table Rendering
const tradingDrop = {
  contract: '0x6ced5c6d3f913b48d59fa07abbfe9060c409087c',
  name: 'AntFun',
  symbol: 'ANTFUN',
  standard: 'BEP-20 Token (18 decimals)',
  token_price_usd: 0.01709,
  market_cap: 32200000,
  fdv: 21200000,
  volume_24h: 551000,
  lp_usd: 2000000,
  holders: 268200,
  total_minted: 1200000000,
  max_supply: 1200000000,
  is_graduated: true
};

assert(isSecondaryMarketAsset(tradingDrop), 'AntFun must be recognized as secondary market asset');

const savedGetElem2 = global.document.getElementById;
const mockTbodyDex = new MockElement('tbody');
const mockThActionDex = new MockElement('th');
mockThActionDex.textContent = 'ACTION';

global.document.getElementById = (id) => {
  if (id === 'tableBody') return mockTbodyDex;
  if (id === 'thAction') return mockThActionDex;
  if (id === 'thColBlock') return new MockElement('th');
  if (id === 'thColProject') return new MockElement('th');
  if (id === 'thColInterest') return new MockElement('th');
  if (id === 'thCol9' || id === 'thHolders') return new MockElement('th');
  if (id === 'thCol10' || id === 'thProg') return new MockElement('th');
  if (id === 'thCol11' || id === 'thPrice') return new MockElement('th');
  if (id === 'thCol12' || id === 'thLp') return new MockElement('th');
  if (id === 'thCol13' || id === 'thFloor') return new MockElement('th');
  if (id === 'footerCount') return new MockElement('div');
  if (id === 'dropsTable') return new MockElement('table');
  if (id === 'secondaryMarketBanner') return new MockElement('div');
  return savedGetElem2 ? savedGetElem2(id) : null;
};

// Render in SECONDARY mode
setActiveTab('SECONDARY');
setAllDrops([tradingDrop]);
renderTable();

// Verify Secondary Table Header has DEXSCREENER
assert.strictEqual(mockThActionDex.textContent, '📈 DEXSCREENER', 'Secondary tab action header must be 📈 DEXSCREENER');

// Verify table row contains DexScreener links
const renderedHtml = mockTbodyDex.innerHTML;
assert(renderedHtml.includes('https://dexscreener.com/bsc/0x6ced5c6d3f913b48d59fa07abbfe9060c409087c'), 'Rendered table must include DexScreener URL for AntFun');
assert(renderedHtml.includes('📈 DexScreener ↗'), 'Action button must render 📈 DexScreener ↗');
assert(renderedHtml.includes('col-floor'), 'Must include floor price column');
assert(renderedHtml.includes('title="Open live chart on DexScreener"'), 'DEX price must be wrapped with DexScreener link');
console.log('✔ Secondary Market Table DexScreener rendering: PASS');

// 3. Test Deep Dive Inspection Modal Links
const mockInspectModalDex = new MockElement('dialog');
mockInspectModalDex.showModal = () => {};
mockInspectModalDex.close = () => {};
const mockInspectModalContentDex = new MockElement('div');

global.document.getElementById = (id) => {
  if (id === 'inspectModal') return mockInspectModalDex;
  if (id === 'modalContent') return mockInspectModalContentDex;
  return savedGetElem2 ? savedGetElem2(id) : null;
};

openModal(tradingDrop);
const modalHtml = mockInspectModalContentDex.innerHTML;

assert(modalHtml.includes('📈 View on DexScreener ↗'), 'Inspection modal must contain 📈 View on DexScreener ↗ link');
assert(modalHtml.includes('Open DexScreener'), 'Chart section must contain Open DexScreener link');
assert(modalHtml.includes('📈 DexScreener Live Chart ↗'), 'Exit command section must contain DexScreener live chart link');
assert(modalHtml.includes('View Chart & Trade on DexScreener ↗'), 'Sold out banner must link to DexScreener for BEP-20');
console.log('✔ Inspection Modal DexScreener links: PASS');

global.document.getElementById = savedGetElem2;

// ==========================================
// Test 24H Market Pulse & Alpha Briefing Card (Option A)
// ==========================================
console.log('Testing 24H Market Pulse & Alpha Briefing Card (Option A)...');
const { compute24hPulseMetrics, updateDailyPulseUI, toggleDailyPulseHero, isExcludedMajorAsset, renderPulseNftRow, formatCryptoPrice } = app;

// Test Major Pegs & Platform Asset Exclusions
const cakeDrop = {
  contract: '0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82',
  name: 'PancakeSwap Token',
  symbol: 'Cake',
  standard: 'BEP-20 Token (18 decimals)',
  market_cap: 10700000000,
  fdv: 10700000000,
  volume_24h: 51100000,
  token_price_usd: 1.86,
  price_change_24h: 0.0
};
const btcbDrop = {
  contract: '0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c',
  name: 'BTCB Token',
  symbol: 'BTCB',
  market_cap: 4900000000,
  volume_24h: 360000
};
const snowballDrop = {
  contract: '0x79b5d86089a29a65fac3278572f00c8fadc361fb',
  name: 'SnowBall Agent',
  symbol: 'SNOWBALL',
  standard: 'BEP-20 Token (18 decimals)',
  market_cap: 53132,
  lp_usd: 27071.35,
  volume_24h: 52343.73,
  token_price_usd: 8.962e-8,
  price_change_24h: 110.0
};

assert.strictEqual(isExcludedMajorAsset(cakeDrop), true, 'Cake must be excluded from 24h pulse metrics');
assert.strictEqual(isExcludedMajorAsset(btcbDrop), true, 'BTCB must be excluded from 24h pulse metrics');
assert.strictEqual(isExcludedMajorAsset(tradingDrop), false, 'AntFun must NOT be excluded');
assert.strictEqual(isExcludedMajorAsset(snowballDrop), false, 'SnowBall Agent must NOT be excluded');

const samplePulseDrops = [
  tradingDrop,
  cakeDrop, // Injected to verify exclusion
  btcbDrop, // Injected to verify exclusion
  {
    contract: '0x3333333333333333333333333333333333333333',
    name: 'SafeDrop',
    symbol: 'SAFE',
    standard: 'BEP-20 Token (18 decimals)',
    smart_wallets: ['adadcarry.ai', '0x1111111111111111111111111111111111111111', '0x2222222222222222222222222222222222222222'],
    risk_level: 'SAFE',
    security: { has_refund: true }
  },
  {
    contract: '0x4444444444444444444444444444444444444444',
    name: 'RugDrop',
    symbol: 'RUG',
    standard: 'BEP-20 Token (18 decimals)',
    risk_level: 'RUG',
    risk_badge: 'HONEYPOT'
  }
];

const pulseMetrics = compute24hPulseMetrics(samplePulseDrops, [tradingDrop, cakeDrop], 132123566);
assert(pulseMetrics.topRunner, 'Must compute a top 24h runner');
assert.notStrictEqual(pulseMetrics.topRunner.symbol.toUpperCase(), 'CAKE', 'Top runner must NEVER be CAKE');
assert.notStrictEqual(pulseMetrics.topRunner.symbol.toUpperCase(), 'BTCB', 'Top runner must NEVER be BTCB');
assert.strictEqual(pulseMetrics.topRunner.contract, tradingDrop.contract, 'Top runner should be AntFun when no positive gainers');
assert.strictEqual(pulseMetrics.totalDexVol < 50000000, true, 'Cake volume must not inflate totalDexVol');
assert.strictEqual(pulseMetrics.gradCount >= 1, true, 'Graduated count should be tracked for legitimate drops');
assert.strictEqual(pulseMetrics.totalSmartInflows, 3, 'Smart inflows should sum to 3');
assert.strictEqual(pulseMetrics.clustersCount, 1, 'Should detect 1 cluster with >=3 smart wallets');
assert.strictEqual(pulseMetrics.cleanMintsCount >= 1, true, 'Clean mints should be tracked');
assert.strictEqual(pulseMetrics.threatsBlockedCount >= 1, true, 'Threats blocked should be tracked');
assert(pulseMetrics.macroHeadline.includes('AntFun') || pulseMetrics.macroHeadline.includes('ANTFUN'), 'Macro headline should highlight the market leader');

// Test positive gainer runner selection
const pulseWithRunner = compute24hPulseMetrics([...samplePulseDrops, snowballDrop], [tradingDrop], 132123566);
assert.strictEqual(pulseWithRunner.topRunner.symbol, 'SNOWBALL', 'Top runner should be the surging token SnowBall (+110%)');
assert.strictEqual(Array.isArray(pulseWithRunner.topRunners), true, 'topRunners must be an array');
assert(pulseWithRunner.topRunners.length >= 1, 'topRunners array must not be empty');
assert.strictEqual(Array.isArray(pulseWithRunner.topSmartMints), true, 'topSmartMints must be an array');
assert.strictEqual(Array.isArray(pulseWithRunner.topAthMints), true, 'topAthMints must be an array');
assert(pulseWithRunner.topAthMints.length >= 1, 'topAthMints array must not be empty');
assert.strictEqual(Array.isArray(pulseWithRunner.topNftSurges), true, 'topNftSurges must be an array');
assert(pulseWithRunner.topNftSurges.length >= 1, 'topNftSurges array must not be empty');
pulseWithRunner.topNftSurges.forEach((nft, idx) => {
  assert(nft.gainPct > 0, `NFT surge at index ${idx} must have positive post-mint gain (got ${nft.gainPct})`);
  const rowHtml = renderPulseNftRow(nft, idx);
  assert(!rowHtml.includes('badge-red'), `NFT surge row for ${nft.name} must not have badge-red`);
});
assert(pulseWithRunner.macroHeadline.includes('SnowBall'), 'Macro headline should highlight positive runner');
console.log('✔ compute24hPulseMetrics calculation & macro synthesis: PASS');

// Test DOM population and Collapsible Toggle
const mockElementsMap = new Map();
function getMock(id) {
  if (!mockElementsMap.has(id)) {
    mockElementsMap.set(id, new MockElement('div', { id }));
  }
  return mockElementsMap.get(id);
}

const savedGetElemPulse = global.document.getElementById;
global.document.getElementById = (id) => getMock(id);

setAllDrops(samplePulseDrops);
updateDailyPulseUI();

const runnerNameEl = getMock('pulseRunnerName');
assert.strictEqual(runnerNameEl.textContent, 'AntFun', 'pulseRunnerName must be populated with AntFun');
const runnersListEl = getMock('pulseRunnersList');
assert(runnersListEl.innerHTML.includes('#1'), 'pulseRunnersList must render #1 runner row');
assert(runnersListEl.innerHTML.includes('AntFun'), 'pulseRunnersList must render AntFun');

const athListEl = getMock('pulseAthList');
assert(athListEl.innerHTML.includes('#1'), 'pulseAthList must render #1 ATH row');
assert(athListEl.innerHTML.includes('Peak') || athListEl.innerHTML.includes('ATH'), 'pulseAthList must display ATH/Peak badge');

const smartMintsListEl = getMock('pulseSmartMintsList');
assert(smartMintsListEl.innerHTML.includes('#1'), 'pulseSmartMintsList must render #1 smart mint row');
assert(smartMintsListEl.innerHTML.includes('SafeDrop'), 'pulseSmartMintsList must render SafeDrop with smart wallets');
assert(smartMintsListEl.innerHTML.includes('3 Wallets'), 'pulseSmartMintsList must display smart wallet count');

const nftSurgesListEl = getMock('pulseNftSurgesList');
assert(nftSurgesListEl.innerHTML.includes('#1'), 'pulseNftSurgesList must render #1 NFT surge row');
assert(nftSurgesListEl.innerHTML.includes('Element'), 'pulseNftSurgesList must render Element Market link');
assert(nftSurgesListEl.innerHTML.includes('%') || nftSurgesListEl.innerHTML.includes('x'), 'pulseNftSurgesList must render % gain or multiplier from mint');

const gradCountEl = getMock('pulseGradCount');
assert(Number(gradCountEl.textContent) >= 1, 'pulseGradCount must be populated');
const headlineEl = getMock('pulseMacroHeadline');
assert(headlineEl.textContent.length > 20, 'pulseMacroHeadline must be populated');

// Test toggle collapse / expand
const heroEl = getMock('dailyPulseHero');
const toggleIcon = getMock('dailyPulseToggleIcon');
const toggleText = getMock('dailyPulseToggleText');

assert.strictEqual(heroEl.classList.has('collapsed'), false, 'Hero should be expanded initially');
toggleDailyPulseHero();
assert.strictEqual(heroEl.classList.has('collapsed'), true, 'Hero should be collapsed after 1st toggle');
assert.strictEqual(toggleText.textContent, 'Expand', 'Toggle text should be Expand');
assert.strictEqual(toggleIcon.textContent, '▸', 'Toggle icon should be ▸');

toggleDailyPulseHero();
assert.strictEqual(heroEl.classList.has('collapsed'), false, 'Hero should be expanded after 2nd toggle');
assert.strictEqual(toggleText.textContent, 'Collapse', 'Toggle text should be Collapse');
assert.strictEqual(toggleIcon.textContent, '▾', 'Toggle icon should be ▾');

global.document.getElementById = savedGetElemPulse;
console.log('✔ 24H Daily Pulse DOM population & interactive toggle: PASS');

// Test Micro-Price Formatting & Outlier Protection
console.log('Testing Micro-Price Formatting & Outlier Protection...');
assert.strictEqual(formatCryptoPrice(0), '$0.00', 'Zero price should format as $0.00');
assert.strictEqual(formatCryptoPrice(0.0001495), '$0.000150', 'Sub-cent price should format cleanly');
assert(!formatCryptoPrice(1e-12).endsWith('.'), 'Micro price must NEVER format with trailing dot ($0.)');
assert(!formatCryptoPrice(1e-15).endsWith('.'), 'Extremely small sub-satoshi price must never format with trailing dot');
assert(formatCryptoPrice(1e-15).includes('<$0.000000000001'), 'Sub-satoshi price formats with upper bound indicator');

// Test Exclusion of Major Assets & Scam Clones (ZEC, DogeZilla)
assert.strictEqual(isExcludedMajorAsset({ symbol: 'ZEC', name: 'Zcash Token', market_cap: 290500000 }), true, 'Zcash (ZEC) must be excluded as major asset');
assert.strictEqual(isExcludedMajorAsset({ symbol: 'DogeZil', name: 'DogeZilla' }), true, 'DogeZilla must be excluded');

// Test Quality Guard on Top 24H Runners (Reject $0 Market Cap outliers)
const zeroMcapDrop = {
  contract: '0x360d0b00c3c3a9d70e6e7372cfbe85e68b95973c',
  name: 'DogeZilla',
  symbol: 'DogeZil',
  standard: 'BEP-20 Token (1 decimals)',
  market_cap: 0,
  fdv: 0,
  token_price_usd: 0.000000000000000001,
  volume_24h: 47300,
  lp_usd: 8300,
  price_change_24h: 2828.0
};
const pulseOutlierCheck = compute24hPulseMetrics([zeroMcapDrop, tradingDrop], [], 132123566);
assert.notStrictEqual(pulseOutlierCheck.topRunner.symbol, 'DogeZil', 'Top runner must NEVER be a $0 market cap glitch like DogeZilla');
assert.strictEqual(pulseOutlierCheck.topRunners.some(r => r.symbol === 'DogeZil'), false, 'DogeZil must not be in topRunners');

// Test Zero Address Dev Root & False Cluster Prevention
const zeroAddrDrop = {
  contract: '0x360d0b00c3c3a9d70e6e7372cfbe85e68b95973c',
  name: 'Zero Root Test',
  symbol: 'ZROOT',
  deployer: '0x0000000000000000000000000000000000000000',
  dev_root: '0x0000000000000000000000000000000000000000',
  cluster_devs: 1,
  cluster_n: 7
};
const mockModalContentZero = { innerHTML: '' };
const prevGetElemZero = global.document.getElementById;
global.document.getElementById = (id) => {
  if (id === 'inspectModal') return { showModal: () => {}, close: () => {} };
  if (id === 'modalContent') return mockModalContentZero;
  return null;
};
openModal(zeroAddrDrop);
assert(!mockModalContentZero.innerHTML.includes('0x000000...000000'), 'Modal must NEVER display 0x000000...000000 as funding root');
assert(!mockModalContentZero.innerHTML.includes('Dev Cluster (1 devs / 7 drops)'), 'Zero address must NEVER trigger a 7-drop Dev Cluster');
assert(mockModalContentZero.innerHTML.includes('Unresolved') || mockModalContentZero.innerHTML.includes('Independent'), 'Zero address must be marked as Unresolved or Independent');
global.document.getElementById = prevGetElemZero;
console.log('✔ Micro-Price Formatting, Outlier Guard & Zero-Address Dev Root: PASS');

console.log('🎉 All Web3, Multicall3, Smart Hunters, and Competitor Features verified successfully!');
process.exit(0);
