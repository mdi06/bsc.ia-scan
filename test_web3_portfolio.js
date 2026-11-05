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

console.log('🎉 All Web3 & Multicall3 tests verified successfully!');
