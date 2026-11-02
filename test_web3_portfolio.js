const assert = require('assert');
const {
  pad32,
  buildPancakeSwapSellCalldata,
  evaluatePortfolioExit,
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
const { getFavoritesStorageKey, setWatchOnlyWallet, disconnectWallet } = require('./app.js');

assert.strictEqual(getFavoritesStorageKey(), 'agent_id_favorites', 'Default key should be guest favorites');
setWatchOnlyWallet('0xa15bb4aedc1b3426159c3cacf8548cf09506a20a');
assert.strictEqual(getFavoritesStorageKey(), 'agent_id_favorites_0xa15bb4aedc1b3426159c3cacf8548cf09506a20a', 'Key should be scoped to connected wallet');
disconnectWallet();
assert.strictEqual(getFavoritesStorageKey(), 'agent_id_favorites', 'Key should revert to guest on disconnect');
console.log('✔ Wallet-Bound Storage Scoping & Reset: PASS');
