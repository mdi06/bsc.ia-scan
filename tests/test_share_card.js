// Share cards: api/_lib/token.js (lookup + display values), api/share.js (the page crawlers read) and api/card.js
// (the PNG). No network: global fetch is replaced, so the font download fails on purpose and the Latin fallback
// font is exercised. Run with: node tests/test_share_card.js
const assert = require('assert');
const path = require('path');

const api = (f) => require(path.join(__dirname, '..', 'api', f));
const token = api('_lib/token');
const share = api('share');
const card = api('card');

const CONTRACT = '0x26040E441A7369CCC7BD8BFE96F596DF4DB87777';
const rec = (over) => Object.assign({
  name: 'Stand With Jensen', symbol: 'SWJ', contract: CONTRACT.toLowerCase(), standard: 'BEP-20 Token (18 decimals)',
  volume_24h: 3481.46, lp_usd: 11085.84, price_change_24h: -33.3, holders: 263, total_minted: 1e9, max_supply: 1e9,
  mint_price_str: 'DEX Traded', risk_level: 'SAFE', smart_wallets: ['a', 'b', 'c'],
}, over || {});

// ---------------------------------------------------------------- ids
assert.ok(token.isValidId(CONTRACT));
assert.ok(token.isValidId('PTAH'));
assert.ok(token.isValidId('坐以待币'));
for (const bad of ['', '   ', '<script>', 'a"b', "a'b", 'a\nb', 'x'.repeat(41), undefined, null]) {
  assert.ok(!token.isValidId(bad), `rejected: ${JSON.stringify(bad)}`);
}

// ---------------------------------------------------------------- formatting
assert.strictEqual(token.fmtMoney(3481.46), '$3.5K');
assert.strictEqual(token.fmtMoney(12160440), '$12.2M');
assert.strictEqual(token.fmtMoney(0), '$0');
assert.strictEqual(token.fmtCompact(9999), '9,999');
assert.strictEqual(token.fmtCompact(10000), '10K');
assert.strictEqual(token.fmtCompact(1e9), '1B');
assert.strictEqual(token.fmtCompact(1.5e6), '1.5M');
assert.deepStrictEqual(token.fmtPct(-33.3), { text: '-33.3%', tone: 'down' });
assert.deepStrictEqual(token.fmtPct(897), { text: '+897%', tone: 'up' });
assert.deepStrictEqual(token.fmtPct(158051), { text: 'n/a', tone: 'flat' }, 'moves beyond 10,000% are not printed');
assert.deepStrictEqual(token.fmtPct(null), { text: 'n/a', tone: 'flat' });
assert.deepStrictEqual(token.fmtPct(true), { text: 'n/a', tone: 'flat' });
assert.strictEqual(token.shortAddr(CONTRACT), '0x2604…7777');

// ---------------------------------------------------------------- describeToken
let d = token.describeToken(rec());
assert.strictEqual(d.kind, 'TOKEN');
assert.deepStrictEqual(d.tiles.map((t) => t.label), ['24H VOLUME', 'LIQUIDITY', '24H CHANGE', 'SMART WALLETS']);
assert.strictEqual(d.tiles[2].tone, 'red');
assert.strictEqual(d.risk.label, 'CLEAN');
assert.ok(d.title.includes('Stand With Jensen (SWJ)'));
assert.ok(d.description.includes('3 smart wallets') && d.description.includes('not financial advice'));

d = token.describeToken(rec({ standard: 'ERC-721 (NFT Collection)', volume_24h: 0, lp_usd: 0, total_minted: 10000, max_supply: 10000, mint_price_str: '0.0005 BNB', risk_level: null }));
assert.strictEqual(d.kind, 'NFT COLLECTION');
assert.strictEqual(d.risk, null);
assert.strictEqual(d.tiles[0].value, '10K / 10K');
assert.strictEqual(d.tiles[1].label, 'MINT PRICE');

d = token.describeToken(rec({ volume_24h: 0, lp_usd: 0, mint_price_str: 'DEX Traded', max_supply: 1e30 }));
assert.deepStrictEqual(d.tiles[1], { label: 'STATUS', value: 'On DEX', tone: 'cyan' }, '"DEX Traded" is not a mint price');
assert.strictEqual(d.tiles[0].value, '1B', 'an uncapped supply shows only what was minted');

assert.strictEqual(token.describeToken(rec({ price_change_24h: 158051 })).tiles[2].value, 'n/a');
assert.strictEqual(token.describeToken(rec({ smart_wallets: [], smart_minters: ['x', 'y'] })).smart, 2);
assert.strictEqual(token.describeToken(rec({ risk_level: 'RUG' })).risk.label, 'RUG RISK');

// ---------------------------------------------------------------- lookup
const rows = [{ contract: '0xaa', symbol: 'ABC', name: 'Other' }, { contract: CONTRACT.toLowerCase(), symbol: 'SWJ', name: 'Jensen' }];
assert.strictEqual(token.pickMatch(rows, CONTRACT).symbol, 'SWJ', 'a contract matches case-insensitively');
assert.strictEqual(token.pickMatch(rows, 'abc').contract, '0xaa', 'then by symbol');
assert.strictEqual(token.pickMatch(rows, 'jensen').symbol, 'SWJ', 'then by name');
assert.strictEqual(token.pickMatch(rows, 'zzz'), null, 'a loose substring hit is not a match');
assert.strictEqual(token.pickMatch(undefined, 'x'), null);

const ok = (body) => async () => ({ ok: true, status: 200, json: async () => body });
(async () => {
  assert.strictEqual((await token.fetchToken(CONTRACT, { fetchImpl: ok({ projects: [rec()] }) })).symbol, 'SWJ');
  assert.strictEqual(await token.fetchToken('nope', { fetchImpl: ok({ projects: [] }) }), null);
  await assert.rejects(token.fetchToken('x', { fetchImpl: async () => ({ ok: false, status: 503 }) }), /HTTP 503/, 'an HTTP error is not retried');
  let calls = 0;
  const flaky = async () => { calls++; if (calls < 3) throw new Error('ECONNRESET'); return { ok: true, status: 200, json: async () => ({ projects: [rec()] }) }; };
  assert.strictEqual((await token.fetchToken(CONTRACT, { fetchImpl: flaky })).symbol, 'SWJ');
  assert.strictEqual(calls, 3, 'network resets are retried');

  // ------------------------------------------------------------ share page
  const origErr = console.error; console.error = () => {}; // the backend-down case logs on purpose
  const run = async (query, backend) => {
    global.fetch = backend;
    const res = { headers: {}, statusCode: 0, body: '', setHeader(k, v) { this.headers[k] = v; }, end(b) { this.body = b || ''; } };
    await share({ query }, res);
    return res;
  };

  let res = await run({ c: CONTRACT }, ok({ projects: [rec()] }));
  assert.strictEqual(res.statusCode, 200);
  assert.ok(res.body.includes('<meta property="og:title" content="Stand With Jensen (SWJ) on BNB Agent Radar">'));
  assert.ok(res.body.includes('content="summary_large_image"'));
  assert.ok(res.body.includes(`https://agent-id-monitor.vercel.app/api/card?c=${CONTRACT.toLowerCase()}&amp;v=`), 'image URL carries the lower-cased contract and an hourly version');
  assert.ok(res.body.includes(`/t/${CONTRACT.toLowerCase()}`), 'canonical share URL');
  assert.ok(res.body.includes(`#token=${CONTRACT.toLowerCase()}`), 'people are sent to the app with the modal open');
  assert.ok(res.body.includes('utm_source=share') && res.body.includes('utm_medium=card'), 'default utm tags');
  assert.ok(/s-maxage=600/.test(res.headers['Cache-Control']));
  assert.ok(!/<meta[^>]*http-equiv="refresh"/i.test(res.body), 'no meta refresh: a crawler would follow it and scrape the app instead of this card');

  // utm_* of the shared link are kept, anything else is dropped
  res = await run({ c: CONTRACT, utm_source: 'x', utm_medium: 'tweet', utm_campaign: 'daily', foo: 'bar' }, ok({ projects: [rec()] }));
  assert.ok(res.body.includes('utm_source=x') && res.body.includes('utm_medium=tweet') && res.body.includes('utm_campaign=daily'));
  assert.ok(!res.body.includes('foo=bar'));

  // a hostile token name cannot break out of the HTML or the script
  res = await run({ c: CONTRACT }, ok({ projects: [rec({ name: '<script>alert(1)</script>"x', symbol: "A'B" })] }));
  assert.ok(!res.body.includes('<script>alert(1)'), 'name is escaped');
  assert.ok(res.body.includes('&lt;script&gt;alert(1)&lt;/script&gt;&quot;x'));
  assert.strictEqual((res.body.match(/<script>/g) || []).length, 1, 'only the redirect script exists');

  // unknown token / bad id / backend down: a generic page that still redirects, cached for different lengths
  res = await run({ c: 'nothing' }, ok({ projects: [] }));
  assert.ok(res.body.includes('og-preview.png') && /s-maxage=300/.test(res.headers['Cache-Control']));
  res = await run({ c: '<script>' }, async () => { throw new Error('must not be called'); });
  assert.ok(res.body.includes('og-preview.png') && /s-maxage=3600/.test(res.headers['Cache-Control']));
  res = await run({ c: CONTRACT }, async () => { throw new Error('down'); });
  assert.strictEqual(res.statusCode, 200);
  assert.ok(res.body.includes('og-preview.png') && /s-maxage=30\b/.test(res.headers['Cache-Control']), 'a backend outage is cached briefly');

  // ------------------------------------------------------------ card image
  global.fetch = async () => { throw new Error('offline'); }; // the Noto font download fails: Latin fallback font
  const png = await card.renderPng(token.describeToken(rec()));
  assert.strictEqual(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a', 'a PNG');
  assert.strictEqual(png.readUInt32BE(16), 1200, 'width');
  assert.strictEqual(png.readUInt32BE(20), 630, 'height');
  assert.ok(png.length < 600000, 'well under X\'s 5 MB image limit');

  // a CJK-only name with no font falls back to the contract instead of printing boxes
  const cjk = await card.renderPng(token.describeToken(rec({ name: '坐以待币', symbol: '坐以待币' })));
  assert.strictEqual(cjk.subarray(0, 4).toString('hex'), '89504e47');
  const tree = card.tree(token.describeToken(rec({ name: '坐以待币', symbol: '坐以待币' })), true);
  assert.ok(JSON.stringify(tree).includes('0x2604…7777'), 'ascii fallback shows the short contract in place of an undrawable name');

  // the handler: bad id and unknown token redirect to the generic preview, a real token returns image/png
  const handle = async (query, backend) => {
    global.fetch = backend;
    const r = { headers: {}, statusCode: 0, body: null, setHeader(k, v) { this.headers[k] = v; }, end(b) { this.body = b; } };
    await card({ query }, r);
    return r;
  };
  let r = await handle({ c: '<x>' }, async () => { throw new Error('must not be called'); });
  assert.strictEqual(r.statusCode, 302);
  assert.ok(r.headers.Location.endsWith('/og-preview.png?v=6'));
  r = await handle({ c: 'nothing' }, ok({ projects: [] }));
  assert.strictEqual(r.statusCode, 302);
  global.fetch = async (url) => (String(url).includes('railway') ? { ok: true, status: 200, json: async () => ({ projects: [rec()] }) } : (() => { throw new Error('offline'); })());
  const real = { headers: {}, statusCode: 0, body: null, setHeader(k, v) { this.headers[k] = v; }, end(b) { this.body = b; } };
  await card({ query: { c: CONTRACT } }, real);
  assert.strictEqual(real.statusCode, 200);
  assert.strictEqual(real.headers['Content-Type'], 'image/png');
  assert.ok(/s-maxage=3600/.test(real.headers['Cache-Control']));
  assert.ok(Buffer.isBuffer(real.body) && real.body.length > 5000);
  console.error = origErr;

  console.log('test_share_card.js: all assertions passed');
})().catch((e) => { console.error(e); process.exit(1); });
