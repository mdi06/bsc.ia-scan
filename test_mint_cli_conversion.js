// Mint -> Sniper CLI conversion in the token modal: the one-tap "Copy CLI" buttons (pinned bar, Mint section, Sniper
// section), the wallet gate when nothing in the browser can sign, and the wallet dialog open / abandon / connect events
// that measure the funnel. Run with: node test_mint_cli_conversion.js
const assert = require('assert');

// ---- a minimal DOM: what openModal, the wallet dialog and the copy buttons touch
class El {
  constructor(id) {
    this.id = id;
    this.innerHTML = '';
    this.style = {};
    this.children = [];
    this.open = false;
    this.opens = 0;
    this.classList = { add() {}, remove() {}, contains: () => false, toggle() {} };
  }
  showModal() { this.open = true; this.opens += 1; }
  close() { this.open = false; }
  setAttribute() {}
  removeAttribute() {}
  addEventListener() {}
  appendChild() {}
  replaceChildren() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  set textContent(v) { this.innerHTML = String(v); }
  get textContent() { return this.innerHTML; }
}
const els = new Map();
const getEl = (id) => { if (!els.has(id)) els.set(id, new El(id)); return els.get(id); };
global.document = {
  getElementById: getEl,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => new El('x'),
  createDocumentFragment: () => ({ appendChild() {} }),
  addEventListener() {},
  body: new El('body'),
  documentElement: new El('html'),
};

// ---- window / navigator: what trackAnalytics, isMobileClient and the wallet dialog read
const events = [];
const tracked = (name) => events.filter(e => e.name === name).map(e => e.props);
global.window = {
  innerWidth: 1280,
  location: { search: '', origin: 'https://radar.test', hash: '' },
  posthog: { capture: (name, props) => { events.push({ name, props }); } },
  matchMedia: () => ({ matches: false }),
  addEventListener() {},
};
function setNavigator({ userAgent, clipboard }) {
  Object.defineProperty(globalThis, 'navigator', {
    value: { userAgent: userAgent || 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 Chrome/126.0 Safari/537.36', clipboard },
    configurable: true,
    writable: true,
  });
}
const MOBILE_UA = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36';
const DESKTOP_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 Chrome/126.0 Safari/537.36';
setNavigator({ userAgent: DESKTOP_UA });

const app = require('./app.js');
const { openModal, copyModalSniperCli, trackCliCopy, executeWeb3Mint, openMintWalletGate, onWalletModalClosed,
  openBinanceQrModal, walletModalBack, openWalletModal, closeWalletDialog, getWalletSession, setConnectedWalletForTest,
  mintClickProps } = app;

const tick = () => new Promise(r => setImmediate(r));
const C = '0x' + '1'.repeat(40);
const CALLDATA = '0x1249c58b';
const VALUE_WEI = '10000000000000000';
// a name with an apostrophe: it used to break the Mint button's inline onclick (a dead button)
const item = {
  contract: C, name: "Agent's Drop", symbol: 'AGT', standard: 'BEP-20 Token (18 decimals)',
  mint_calldata: CALLDATA, mint_price_bnb: 0.01, mint_price_str: '0.01 BNB', mint_value_wei: VALUE_WEI, block: 7,
};
const EXPECTED_CLI = `baw contract-call preview --binanceChainId 56 --from <YOUR_WALLET> --to ${C} --value ${VALUE_WEI} --inputData ${CALLDATA} --json`;

function reset() {
  onWalletModalClosed();          // ends a session left open by the previous step (no event if a signer is connected)
  events.length = 0;
  setConnectedWalletForTest(null, null);
  delete global.window.ethereum;
  for (const id of ['walletModal', 'walletModalContent', 'modalMintStatus']) {
    const el = getEl(id);
    el.open = false;
    el.innerHTML = '';
    el.opens = 0;
  }
  global.window.innerWidth = 1280;
  setNavigator({ userAgent: DESKTOP_UA });
}

// The JS of an inline onclick handler in rendered markup, entity-decoded (what the browser runs).
function onclickJs(html, marker) {
  const i = html.indexOf(marker);
  assert.ok(i >= 0, `markup has ${marker}`);
  const start = html.indexOf('onclick="', i) + 'onclick="'.length;
  const end = html.indexOf('"', start);
  return html.slice(start, end)
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
}

(async () => {
  // ---- 1. The markup: a copy button in the pinned bar, next to Mint, and in the Sniper section; all parse
  reset();
  openModal(item);
  const html = getEl('modalContent').innerHTML;
  assert.ok(html.includes("copyModalSniperCli(this, 'shortcut_bar')"), 'the pinned bar has a one-tap Copy CLI');
  assert.ok(html.includes("copyModalSniperCli(this, 'mint_section')"), 'the Mint section has a Copy Sniper CLI');
  assert.ok(html.includes("copyModalSniperCli(this, 'sniper_section')"), 'the Sniper section copy button is the same helper');
  assert.ok(html.indexOf('mint-cli-row') < html.indexOf('data-modal-section="sniper_cli"'),
    'the Mint-section copy sits above the Sniper section (no scrolling to it)');
  assert.ok(html.includes("trackCliCopy('exit_cli', 'AGT', '" + C + "', 'pancakeswap_exit')"), 'the exit copy tracks its source');
  const mintJs = onclickJs(html, 'id="modalMintBtn"');
  assert.ok(mintJs.startsWith("trackAnalytics('mint_button_clicked', mintClickProps(\"AGT\", false)); executeWeb3Mint('" + C + "', '" + CALLDATA + "', '" + VALUE_WEI + "', \"Agent's Drop\")"),
    'the Mint button keeps its call and tracks through mintClickProps');
  for (const [marker, label] of [['id="modalMintBtn"', 'mint'], ['modal-shortcut-btn-secondary', 'pinned copy'], ['mint-cli-btn', 'mint-section copy'], ['data-modal-section="sniper_cli"', 'sniper copy']]) {
    assert.doesNotThrow(() => new Function(onclickJs(html, marker)), `the ${label} onclick is valid JavaScript`);
  }
  console.log('✔ Pinned bar, Mint section and Sniper section each carry a one-tap Copy CLI; onclick markup parses: PASS');

  // ---- 2. One tap copies the exact command and tracks cli_copied with its source
  reset();
  const copied = [];
  setNavigator({ clipboard: { writeText: async (text) => { copied.push(text); } } });
  openModal(item);
  const pinnedBtn = { textContent: '⌨ Copy CLI' };
  copyModalSniperCli(pinnedBtn, 'shortcut_bar');
  await tick();
  assert.deepStrictEqual(copied, [EXPECTED_CLI], 'the clipboard gets the Sniper CLI command of the open token');
  assert.deepStrictEqual(tracked('cli_copied'), [{
    type: 'sniper_cli', token_symbol: 'AGT', token_contract: C, source: 'shortcut_bar', copied: true,
  }], 'cli_copied carries the source and whether it copied');
  assert.strictEqual(pinnedBtn.textContent, 'Copied!', 'the tapped button confirms the copy');
  console.log('✔ One tap copies the Sniper CLI and tracks cli_copied with source: PASS');

  // ---- 3. A refused clipboard is reported as a failed copy (no false "Copied!")
  reset();
  setNavigator({ clipboard: { writeText: async () => { throw new Error('clipboard denied'); } } });
  openModal(item);
  const failBtn = { textContent: '⌨ Copy Sniper CLI' };
  copyModalSniperCli(failBtn, 'mint_section');
  await tick();
  assert.deepStrictEqual(tracked('cli_copied').map(p => [p.source, p.copied]), [['mint_section', false]]);
  assert.strictEqual(failBtn.textContent, 'Copy failed', 'a refused copy says so');
  console.log('✔ A refused clipboard is tracked as copied:false and says so: PASS');

  // ---- 4. Mint on a phone with no wallet: the gate explains and offers the Binance app or the CLI
  reset();
  setNavigator({ userAgent: MOBILE_UA });
  global.window.innerWidth = 375;
  openModal(item);
  getEl('walletModal').opens = 0;
  assert.deepStrictEqual(app.mintClickProps('AGT', false), {
    token_symbol: 'AGT', is_free: false, wallet_connected: false, watch_only: false,
    has_injected_provider: false, is_mobile: true, source: 'modal_mint_button',
  }, 'the mint click carries what the user had');
  await executeWeb3Mint(C, CALLDATA, VALUE_WEI, "Agent's Drop");
  const dialog = getEl('walletModal');
  const gate = getEl('walletModalContent').innerHTML;
  assert.strictEqual(dialog.open, true, 'the wallet dialog opens');
  assert.ok(gate.includes('Wallet needed to mint AGT'), 'the dialog says what it is for');
  assert.ok(gate.includes('Open in Binance App') && gate.includes('Copy Sniper CLI'), 'mobile offers the Binance app and the CLI');
  assert.ok(!gate.includes('provider-card'), 'the browser-extension list (which cannot work on this phone) is not shown');
  assert.ok(!gate.includes('Install MetaMask'), 'no desktop extension install on a phone');
  assert.ok(gate.includes("copyModalSniperCli(this, 'mint_gate')"), 'the gate copy is tracked with its source');
  assert.ok(getEl('modalMintStatus').innerHTML.includes('openMintWalletGate()'), 'the Mint section keeps the reason after the dialog closes');
  assert.deepStrictEqual(tracked('mint_wallet_gate_shown'), [{ token_symbol: 'AGT', is_mobile: true, watch_only: false }]);
  assert.deepStrictEqual(tracked('wallet_modal_opened'), [{
    source: 'mint_gate', view: 'mint_gate', token_symbol: 'AGT', is_mobile: true, has_injected_provider: false, wallet_connected: false,
  }]);
  console.log('✔ Mint on a phone with no wallet opens the gate with the Binance app and CLI options: PASS');

  // ---- 5. The Binance guide is reached from the gate and Back returns to the gate (one session, one open event)
  openBinanceQrModal();
  assert.strictEqual(getWalletSession().view, 'binance_qr', 'the guide is the current view');
  assert.ok(getEl('walletModalContent').innerHTML.includes('Method 1'), 'the Binance guide is rendered');
  walletModalBack();
  assert.ok(getEl('walletModalContent').innerHTML.includes('Pick the next step'), 'Back returns to the Mint gate');
  assert.strictEqual(getWalletSession().source, 'mint_gate', 'the session stays the Mint gate');
  assert.strictEqual(tracked('wallet_modal_opened').length, 1, 'going back and forth does not open a new session');
  console.log('✔ Binance guide and Back stay inside the Mint gate session: PASS');

  // ---- 6. Closing the gate without a wallet is an abandon, counted once, with where and how long
  closeWalletDialog();
  onWalletModalClosed();
  onWalletModalClosed();
  assert.deepStrictEqual(tracked('wallet_modal_abandoned'), [{
    source: 'mint_gate', view: 'mint_gate', token_symbol: 'AGT', seconds_open: 0, is_mobile: true,
    has_injected_provider: false, watch_only_connected: false,
  }], 'the abandon is recorded once with its source and view');
  console.log('✔ Closing the gate with no wallet is tracked once as wallet_modal_abandoned: PASS');

  // ---- 7. Desktop with no extension: install links, the Binance app and the CLI; the dialog reload hint
  reset();
  openModal(item);
  await executeWeb3Mint(C, CALLDATA, VALUE_WEI, "Agent's Drop");
  const desktopGate = getEl('walletModalContent').innerHTML;
  assert.ok(desktopGate.includes('https://metamask.io/download/') && desktopGate.includes('https://rabby.io/'), 'desktop links to the extensions');
  assert.ok(desktopGate.includes('Mint from the Binance app') && desktopGate.includes('Copy Sniper CLI'), 'desktop also offers the Binance app and the CLI');
  assert.ok(desktopGate.includes('reload this page and click Mint again'), 'desktop says what to do after installing');
  assert.strictEqual(tracked('mint_wallet_gate_shown')[0].is_mobile, false);
  console.log('✔ Desktop without an extension gets install links, the Binance app and the CLI: PASS');

  // ---- 8. Watch-only is not a signing wallet: the gate says so and an abandon records it
  reset();
  setConnectedWalletForTest('0x' + '2'.repeat(40), 'Watch-Only');
  openModal(item);
  await executeWeb3Mint(C, CALLDATA, VALUE_WEI, "Agent's Drop");
  assert.ok(getEl('walletModalContent').innerHTML.includes('watch-only mode'), 'watch-only is named in the gate');
  assert.strictEqual(tracked('mint_wallet_gate_shown')[0].watch_only, true);
  onWalletModalClosed();
  assert.strictEqual(tracked('wallet_modal_abandoned')[0].watch_only_connected, true, 'a watch-only session still counts as an abandon');
  setConnectedWalletForTest(null, null);
  console.log('✔ Watch-only mint shows the watch-only gate and abandons count as such: PASS');

  // ---- 9. The account drawer is not part of the connect funnel: closing it is not an abandon
  reset();
  setConnectedWalletForTest('0x' + '3'.repeat(40), 'MetaMask');
  openWalletModal();
  onWalletModalClosed();
  assert.deepStrictEqual(tracked('wallet_modal_abandoned'), [], 'closing the account drawer is not an abandon');
  setConnectedWalletForTest(null, null);
  console.log('✔ Account drawer closes without an abandon event: PASS');

  // ---- 10. With an injected provider Mint goes to the unchanged mint flow (eth_requestAccounts first, no gate)
  reset();
  const rpc = [];
  global.window.ethereum = {
    request: async ({ method }) => {
      rpc.push(method);
      if (method === 'eth_requestAccounts') {
        const e = new Error('User rejected the request.');
        e.code = 4001;
        throw e;
      }
      return null;
    },
  };
  const origError = console.error;
  console.error = () => {};
  openModal(item);
  await executeWeb3Mint(C, CALLDATA, VALUE_WEI, "Agent's Drop");
  console.error = origError;
  assert.deepStrictEqual(rpc, ['eth_requestAccounts'], 'the wallet is asked to connect, and nothing is sent');
  assert.deepStrictEqual(tracked('mint_wallet_gate_shown'), [], 'no gate when a provider exists');
  assert.deepStrictEqual(tracked('wallet_connect_failed'), [{
    wallet_provider: 'auto', rejected: true, is_mobile: false, source: 'direct',
  }], 'a rejected connection is tracked');
  delete global.window.ethereum;
  console.log('✔ With an injected provider Mint runs the unchanged flow; a rejected connect is tracked: PASS');

  // ---- 11. trackCliCopy keeps its old call shape and takes a source
  reset();
  trackCliCopy('exit_cli', 'X', '0xabc', 'pancakeswap_exit');
  trackCliCopy('sniper_cli', 'Y', '0xdef');
  assert.deepStrictEqual(tracked('cli_copied'), [
    { type: 'exit_cli', token_symbol: 'X', token_contract: '0xabc', source: 'pancakeswap_exit' },
    { type: 'sniper_cli', token_symbol: 'Y', token_contract: '0xdef', source: 'unknown' },
  ]);
  console.log('✔ trackCliCopy accepts source and stays compatible with the old call: PASS');

  console.log('\n🎉 Mint -> Sniper CLI conversion tests passed.');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
