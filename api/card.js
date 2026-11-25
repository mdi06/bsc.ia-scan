// GET /api/card?c=<contract or symbol>  ->  a 1200x630 PNG preview of one token (the image behind a shared link).
//
// The page at /t/<contract> (api/share.js) points its og:image / twitter:image here. Crawlers fetch this once per
// URL; the CDN then serves it for an hour, so the backend is hit a handful of times per token, not per view.

const fs = require('fs');
const path = require('path');
const { SITE, isValidId, describeToken, fetchToken, shortAddr, clip } = require('./_lib/token');

const FALLBACK_FONT = fs.readFileSync(path.join(__dirname, '_lib', 'fonts', 'Geist-Regular.ttf')); // SIL OFL, from Vercel's Geist

const W = 1200;
const H = 630;
const COLORS = { gold: '#F0B90B', cyan: '#38BDF8', green: '#00FF9D', red: '#F87171', gray: '#94A3B8', white: '#FFFFFF' };
const RISK_COLORS = { good: '#00FF9D', warn: '#FBBF24', bad: '#F87171' };

// satori only does flexbox, and every element with more than one child must be display:flex
const h = (type, style, children) => ({ type, props: { style: { display: 'flex', ...style }, children } });

// Tokens are often named in Chinese / Japanese, which the bundled Latin font cannot draw. Google Fonts serves a tiny
// subset of Noto Sans SC containing only the glyphs we ask for (the same approach Vercel documents for OG images).
const fontCache = new Map();
const OLD_UA = 'Mozilla/5.0 (Macintosh; U; Intel Mac OS X 10_6_8; de-at) AppleWebKit/533.21.1 (KHTML, like Gecko) Version/5.0.5 Safari/533.21.1'; // makes Google answer with TrueType, not woff2

async function loadFont(text) {
  const chars = Array.from(new Set(Array.from(text + '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz$%.,+-/:·…—')))
    .join('').slice(0, 400);
  if (fontCache.has(chars)) return fontCache.get(chars);
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 4000);
    try {
      const css = await (await fetch(
        `https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@700&text=${encodeURIComponent(chars)}`,
        { headers: { 'User-Agent': OLD_UA }, signal: ctl.signal },
      )).text();
      const m = css.match(/src: url\((.+?)\) format\('(?:opentype|truetype)'\)/);
      if (!m) return null;
      const data = await (await fetch(m[1], { signal: ctl.signal })).arrayBuffer();
      if (fontCache.size > 50) fontCache.clear();
      fontCache.set(chars, data);
      return data;
    } finally {
      clearTimeout(timer);
    }
  } catch (_) {
    return null;
  }
}

function nameSize(name) {
  const n = Array.from(name).length;
  return n <= 14 ? 84 : n <= 22 ? 68 : n <= 32 ? 54 : 44;
}

function tree(d, ascii) {
  // without the Noto font the bundled one only has Latin glyphs: keep what it can draw, never print boxes
  const safe = (s, fallback) => {
    if (!ascii) return s;
    const t = String(s).replace(/[^\x20-\x7E]/g, '').trim();
    return t || fallback;
  };
  const name = safe(d.name, shortAddr(d.contract));
  const symbol = safe(d.symbol, '');

  const pill = (text, color) => h('div', {
    padding: '6px 16px', borderRadius: 999, fontSize: 20, fontWeight: 700, letterSpacing: 1.5, color,
    border: `1.5px solid ${color}`, backgroundColor: 'rgba(255,255,255,0.04)',
  }, text);

  // one value size for the whole row, set by its longest value, so the four numbers sit on the same baseline
  const longest = Math.max(...d.tiles.map((t) => t.value.length));
  const valueSize = longest <= 8 ? 46 : longest <= 11 ? 38 : 30;

  const tiles = d.tiles.map((t) => h('div', {
    flex: 1, flexDirection: 'column', padding: '20px 24px', borderRadius: 20, boxSizing: 'border-box', minWidth: 0,
    backgroundImage: 'linear-gradient(180deg, rgba(255,255,255,0.07), rgba(12,18,30,0.95))',
    border: '1.5px solid rgba(148,163,184,0.28)',
  }, [
    h('div', { fontSize: 17, fontWeight: 700, letterSpacing: 1.6, color: COLORS.gray }, t.label),
    h('div', { fontSize: valueSize, fontWeight: 700, color: COLORS[t.tone] || COLORS.white, marginTop: 6, whiteSpace: 'nowrap' }, t.value),
  ]));

  return h('div', {
    boxSizing: 'border-box', width: W, height: H, flexDirection: 'column', justifyContent: 'space-between', padding: '44px 56px',
    backgroundColor: '#020408', backgroundImage: 'radial-gradient(circle at 50% -10%, rgba(240,185,11,0.20), rgba(2,4,8,0) 55%)',
    color: '#E2E8F0', fontFamily: 'Noto',
  }, [
    h('div', { justifyContent: 'space-between', alignItems: 'center' }, [
      h('div', { fontSize: 24, fontWeight: 700, letterSpacing: 5, color: COLORS.gold }, 'BNB AGENT RADAR'),
      h('div', { alignItems: 'center', padding: '8px 18px', borderRadius: 999, fontSize: 18, fontWeight: 700, letterSpacing: 1.5, color: COLORS.green, border: '1.5px solid rgba(0,255,157,0.45)' }, [
        h('div', { width: 10, height: 10, borderRadius: 999, backgroundColor: COLORS.green, marginRight: 10 }, ''),
        h('div', {}, 'LIVE RADAR'),
      ]),
    ]),
    h('div', { flexDirection: 'column' }, [
      h('div', { fontSize: nameSize(name), fontWeight: 700, color: COLORS.white, lineHeight: 1.05 }, clip(name, 48)),
      h('div', { alignItems: 'center', marginTop: 18 }, [
        symbol ? h('div', { fontSize: 32, fontWeight: 700, color: COLORS.gold, marginRight: 18 }, symbol) : null,
        h('div', { marginRight: 12 }, pill(d.kind, COLORS.gray)),
        d.risk ? h('div', { marginRight: 12 }, pill(d.risk.label, RISK_COLORS[d.risk.tone])) : null,
        h('div', { fontSize: 20, color: '#64748B' }, shortAddr(d.contract)),
      ].filter(Boolean)),
    ]),
    h('div', { gap: 16 }, tiles),
    h('div', { justifyContent: 'space-between', alignItems: 'center', fontSize: 20 }, [
      h('div', { fontWeight: 700, color: COLORS.gold }, 'agent-id-monitor.vercel.app'),
      h('div', { color: '#64748B' }, 'On-chain data · not financial advice'),
    ]),
  ]);
}

async function renderPng(d) {
  const satori = (await import('satori')).default; // satori is an ES module; the handler is CommonJS
  const { Resvg } = require('@resvg/resvg-js');
  const drawn = [d.name, d.symbol, d.kind, d.risk ? d.risk.label : '', ...d.tiles.map((t) => t.label + t.value)].join('');
  const font = await loadFont(drawn);
  // No Noto (offline, or Google unreachable): Latin-only Geist, which cannot draw CJK, so the tree drops those glyphs
  const fonts = font
    ? [{ name: 'Noto', data: font, weight: 700, style: 'normal' }]
    : [{ name: 'Noto', data: FALLBACK_FONT, weight: 400, style: 'normal' }];
  const svg = await satori(tree(d, !font), { width: W, height: H, fonts });
  return new Resvg(svg, { fitTo: { mode: 'width', value: W } }).render().asPng();
}

module.exports = async function handler(req, res) {
  const id = String((req.query && req.query.c) || '').trim();
  const fallback = () => { res.statusCode = 302; res.setHeader('Location', `${SITE}/og-preview.png?v=4`); res.setHeader('Cache-Control', 'public, s-maxage=60'); res.end(); };
  if (!isValidId(id)) return fallback();
  try {
    const rec = await fetchToken(id);
    if (!rec) return fallback();
    const png = await renderPng(describeToken(rec));
    res.statusCode = 200;
    res.setHeader('Content-Type', 'image/png');
    res.setHeader('Cache-Control', 'public, max-age=300, s-maxage=3600, stale-while-revalidate=86400');
    res.end(png);
  } catch (e) {
    console.error('card failed', id, e && e.message);
    fallback();
  }
};

module.exports.tree = tree;
module.exports.renderPng = renderPng;
