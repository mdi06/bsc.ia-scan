// Token share link: buildTokenShareUrl, the modal's "Copy share link" button and its copy fallback.
// Run with: node test_token_share_link.js
const assert = require('assert');

const SITE = 'https://agent-id-monitor.vercel.app';

// ---- a minimal DOM and browser: only what openModal and the copy helper touch
class El {
  constructor(id) {
    this.id = id;
    this.innerHTML = '';
    this.style = {};
    this.children = [];
    this.classList = { add() {}, remove() {}, contains: () => false, toggle() {} };
  }
  showModal() {}
  close() {}
  setAttribute() {}
  removeAttribute() {}
  addEventListener() {}
  appendChild(k) { this.innerHTML += (k && k.innerHTML) || ''; }
  replaceChildren() { this.innerHTML = ''; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  select() {}
  set textContent(v) { this.innerHTML = String(v); }
  get textContent() { return this.innerHTML; }
}
class Frag { constructor() { this.kids = []; } appendChild(k) { this.kids.push(k); } }
const els = new Map();
const getEl = (id) => { if (!els.has(id)) els.set(id, new El(id)); return els.get(id); };

let lastTextarea = null;
const execCalls = [];
global.document = {
  getElementById: getEl,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: (tag) => { const el = new El(tag); lastTextarea = el; return el; },
  createDocumentFragment: () => new Frag(),
  execCommand: (cmd) => { execCalls.push({ cmd, value: lastTextarea && lastTextarea.value }); return true; },
  addEventListener() {},
  body: new El('body'),
  documentElement: new El('html'),
};
global.window = {
  location: { origin: SITE, pathname: '/', search: '?utm_source=x&foo=1', hash: '' },
  addEventListener() {},
  removeEventListener() {},
};

let clipboardMode = 'ok';
const copied = [];
// Node 21+ defines a read-only global navigator without a clipboard, so replace it outright.
Object.defineProperty(global, 'navigator', {
  configurable: true,
  writable: true,
  value: {
    clipboard: {
      writeText(text) {
        copied.push(text);
        return clipboardMode === 'ok' ? Promise.resolve() : Promise.reject(new Error('denied'));
      },
    },
  },
});

const app = require('./app.js');
const { buildTokenShareUrl, copyTokenShareLink, openModal } = app;

// 1. A plain symbol: the site's origin and path, the UTM tags, then the modal's own #token= hash
assert.strictEqual(
  buildTokenShareUrl('ALPHA'),
  SITE + '/?utm_source=share&utm_medium=modal&utm_campaign=ALPHA#token=ALPHA'
);

// 2. Reserved and unusual ASCII characters are percent-encoded in both the query and the hash
assert.strictEqual(
  buildTokenShareUrl('A&B #1 +x?'),
  SITE + '/?utm_source=share&utm_medium=modal&utm_campaign=A%26B%20%231%20%2Bx%3F#token=A%26B%20%231%20%2Bx%3F'
);

// 3. Non-ASCII symbols survive a round trip through URLSearchParams and the hash
const unicodeUrl = new URL(buildTokenShareUrl('🚀 DOGE/ö'));
assert.strictEqual(unicodeUrl.searchParams.get('utm_campaign'), '🚀 DOGE/ö');
assert.strictEqual(decodeURIComponent(unicodeUrl.hash.replace('#token=', '')), '🚀 DOGE/ö');

// 4. Other query parameters of the current page are not carried over; an empty symbol gives no link
assert.ok(!buildTokenShareUrl('ALPHA').includes('foo=1'));
assert.ok(!buildTokenShareUrl('ALPHA').includes('utm_source=x'));
assert.strictEqual(buildTokenShareUrl(''), '');
assert.strictEqual(buildTokenShareUrl('   '), '');
assert.strictEqual(buildTokenShareUrl(undefined), '');

// 5. The modal renders the button with the symbol in a data attribute (escaped, so quotes cannot break out)
const item = {
  name: 'Alpha "Coin"', symbol: 'AL"PHA', contract: '0x' + '1'.repeat(40), standard: 'ERC-20',
};
getEl('modalContent').innerHTML = '';
openModal(item);
const html = getEl('modalContent').innerHTML;
assert.ok(html.includes('id="modalShareBtn"'), 'share button is rendered in the modal header');
assert.ok(html.includes('data-share-symbol="AL&quot;PHA"'), 'symbol is attribute-escaped');
assert.ok(html.includes('onclick="copyTokenShareLink(this)"'), 'button calls the copy helper');

// 6. Clipboard API success: the share URL is copied and the label confirms it
(async () => {
  const tick = () => new Promise((r) => setImmediate(r));

  const btn = { dataset: { shareSymbol: 'ALPHA' }, textContent: 'label' };
  copyTokenShareLink(btn);
  await tick();
  assert.deepStrictEqual(copied, [buildTokenShareUrl('ALPHA')]);
  assert.strictEqual(btn.textContent, '✓ Link copied');
  assert.strictEqual(execCalls.length, 0, 'no fallback when the clipboard API works');

  // 7. Clipboard API rejects: falls back to a hidden textarea and execCommand('copy')
  clipboardMode = 'deny';
  const btn2 = { dataset: { shareSymbol: 'A&B #1 +x?' }, textContent: 'label' };
  copyTokenShareLink(btn2);
  await tick();
  await tick();
  assert.strictEqual(execCalls.length, 1);
  assert.strictEqual(execCalls[0].cmd, 'copy');
  assert.strictEqual(execCalls[0].value, buildTokenShareUrl('A&B #1 +x?'));
  assert.strictEqual(btn2.textContent, '✓ Link copied');

  // 8. A button without a symbol does nothing
  copied.length = 0;
  copyTokenShareLink({ dataset: { shareSymbol: '' }, textContent: 'label' });
  await tick();
  assert.deepStrictEqual(copied, []);

  console.log('test_token_share_link.js: all assertions passed');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
