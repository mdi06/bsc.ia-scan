// Shared by api/card.js (the preview image) and api/share.js (the page crawlers read). Files under api/ whose name
// starts with "_" are not routes, so this folder is plain library code.
//
// Both endpoints answer one question: "what does this token look like right now?". They get it from the backend's
// /api/projects?search= (served from its in-memory cache) and reduce it to a few display values.

const SITE = 'https://agent-id-monitor.vercel.app';
const BACKEND = (process.env.RADAR_API_BASE || 'https://agent-id-backend-production.up.railway.app').replace(/\/+$/, '');

const CONTRACT_RE = /^0x[0-9a-fA-F]{40}$/;
// A symbol / name used as the id (old #token=SYMBOL links): short, no control characters, no markup characters.
const SYMBOL_RE = /^[^\u0000-\u001f<>"'`\\]{1,40}$/u;
const PCT_SANITY_MAX = 10000; // a 24h move beyond this is almost always a near-zero base price: shown as n/a

function isContract(id) { return CONTRACT_RE.test(String(id || '')); }
function isValidId(id) { const s = String(id || '').trim(); return isContract(s) || SYMBOL_RE.test(s); }

function num(v) {
  if (typeof v === 'boolean' || v === null || v === undefined || v === '') return 0;
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function fmtInt(v) { return Math.round(num(v)).toLocaleString('en-US'); }

// 9,999 stays as is; 10,000 -> 10K, 1,000,000,000 -> 1B (keeps long supplies on one line of a tile)
function fmtCompact(v) {
  const x = num(v);
  const trim = (n) => n.toFixed(1).replace(/\.0$/, '');
  if (x >= 1e9) return `${trim(x / 1e9)}B`;
  if (x >= 1e6) return `${trim(x / 1e6)}M`;
  if (x >= 1e4) return `${trim(x / 1e3)}K`;
  return fmtInt(x);
}

function fmtMoney(v) {
  const x = num(v);
  if (x >= 1e9) return `$${(x / 1e9).toFixed(1)}B`;
  if (x >= 1e6) return `$${(x / 1e6).toFixed(1)}M`;
  if (x >= 1e3) return `$${(x / 1e3).toFixed(1)}K`;
  return `$${x.toFixed(0)}`;
}

// { text: '+12.3%' | 'n/a', tone: 'up' | 'down' | 'flat' }
function fmtPct(v) {
  if (v === null || v === undefined || v === '' || typeof v === 'boolean') return { text: 'n/a', tone: 'flat' };
  const x = Number(v);
  if (!Number.isFinite(x) || Math.abs(x) > PCT_SANITY_MAX) return { text: 'n/a', tone: 'flat' };
  const text = Math.abs(x) >= 100 ? `${x > 0 ? '+' : ''}${Math.round(x).toLocaleString('en-US')}%` : `${x > 0 ? '+' : ''}${x.toFixed(1)}%`;
  return { text, tone: x > 0 ? 'up' : x < 0 ? 'down' : 'flat' };
}

function shortAddr(a) {
  const s = String(a || '');
  return s.length > 14 ? `${s.slice(0, 6)}…${s.slice(-4)}` : s;
}

function clip(s, n) {
  const t = String(s || '').trim();
  return t.length > n ? t.slice(0, n - 1) + '…' : t;
}

function isNft(rec) { return /NFT|ERC-?721|ERC-?1155/i.test(String((rec && rec.standard) || '')); }

const RISK = {
  SAFE: { label: 'CLEAN', tone: 'good' },
  LOW: { label: 'LOW RISK', tone: 'good' },
  MED: { label: 'MEDIUM RISK', tone: 'warn' },
  HIGH: { label: 'HIGH RISK', tone: 'bad' },
  RUG: { label: 'RUG RISK', tone: 'bad' },
};

// What the card and the share text show for one backend record. Pure: no I/O.
function describeToken(rec) {
  const lp = num(rec.lp_usd), vol = num(rec.volume_24h);
  const traded = lp > 0 || vol > 0;
  const smart = Math.max(
    Array.isArray(rec.smart_wallets) ? rec.smart_wallets.length : 0,
    Array.isArray(rec.smart_minters) ? rec.smart_minters.length : 0,
  );
  const risk = RISK[String(rec.risk_level || '').toUpperCase()] || null;
  const name = clip(rec.name || rec.symbol || 'Unknown', 48);
  const symbol = clip(rec.symbol || '', 14);
  const kind = isNft(rec) ? 'NFT COLLECTION' : 'TOKEN';
  const change = fmtPct(rec.price_change_24h);

  let tiles;
  if (traded) {
    tiles = [
      { label: '24H VOLUME', value: fmtMoney(vol), tone: 'cyan' },
      { label: 'LIQUIDITY', value: fmtMoney(lp), tone: 'gold' },
      { label: '24H CHANGE', value: change.text, tone: change.tone === 'up' ? 'green' : change.tone === 'down' ? 'red' : 'gray' },
      { label: 'SMART WALLETS', value: fmtInt(smart), tone: 'green' },
    ];
  } else {
    const cap = num(rec.max_supply);
    const minted = num(rec.total_minted);
    const capped = cap > 0 && cap < 1e20;
    tiles = [
      { label: 'MINTED', value: capped ? `${fmtCompact(minted)} / ${fmtCompact(cap)}` : fmtCompact(minted), tone: 'gold' },
      // "DEX Traded" is the backend's placeholder for tokens that were never fair-minted: not a price
      /dex/i.test(String(rec.mint_price_str || ''))
        ? { label: 'STATUS', value: 'On DEX', tone: 'cyan' }
        : { label: 'MINT PRICE', value: clip(rec.mint_price_str || 'n/a', 14), tone: 'cyan' },
      { label: 'HOLDERS', value: fmtCompact(rec.holders), tone: 'gray' },
      { label: 'SMART WALLETS', value: fmtInt(smart), tone: 'green' },
    ];
  }

  const bits = [];
  if (traded) bits.push(`24h vol ${fmtMoney(vol)}`, `liquidity ${fmtMoney(lp)}`);
  else bits.push(tiles[0].value + ' minted');
  bits.push(`${fmtInt(smart)} smart wallet${smart === 1 ? '' : 's'}`);
  if (risk) bits.push(`risk: ${risk.label.toLowerCase()}`);

  return {
    name, symbol, kind, risk, tiles, traded, smart,
    contract: String(rec.contract || ''),
    title: `${name}${symbol && symbol !== name ? ` (${symbol})` : ''} on BNB Agent Radar`,
    description: `${bits.join(' · ')}. Live on BNB Chain. On-chain data, not financial advice.`,
  };
}

function pickMatch(list, id) {
  const q = String(id).trim().toLowerCase();
  const rows = Array.isArray(list) ? list : [];
  return rows.find((p) => String(p.contract || '').toLowerCase() === q)
    || rows.find((p) => String(p.symbol || '').toLowerCase() === q)
    || rows.find((p) => String(p.name || '').toLowerCase() === q)
    || null;
}

// The record for `id` (a contract address, or a symbol / name from an old #token= link), or null if the backend has
// no such token. Throws if the backend cannot be reached, so callers can tell "unknown token" from "backend down".
async function fetchToken(id, { fetchImpl = fetch, timeoutMs = 8000 } = {}) {
  const url = `${BACKEND}/api/projects?search=${encodeURIComponent(String(id).trim())}&limit=5`;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    let res;
    // The first requests on a fresh connection to the backend's edge are sometimes reset (ECONNRESET, seen twice in a
    // row from a cold process): retry a few times. An HTTP error status is not retried; that is the backend answering.
    for (let attempt = 0; ; attempt++) {
      try {
        res = await fetchImpl(url, { headers: { Accept: 'application/json', 'User-Agent': 'radar-share-card/1.0' }, signal: ctl.signal });
        break;
      } catch (e) {
        if (attempt >= 4 || ctl.signal.aborted) throw e;
        await new Promise((r) => setTimeout(r, 200 * (attempt + 1)));
      }
    }
    if (!res.ok) throw new Error(`backend HTTP ${res.status}`);
    const body = await res.json();
    return pickMatch(body && body.projects, id);
  } finally {
    clearTimeout(timer);
  }
}

module.exports = { SITE, BACKEND, isContract, isValidId, num, fmtInt, fmtCompact, fmtMoney, fmtPct, shortAddr, clip, isNft, describeToken, pickMatch, fetchToken };
