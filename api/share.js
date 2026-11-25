// GET /t/<contract or symbol>  (rewritten to /api/share?c=<id> in vercel.json)
//
// The page a shared link points at. Crawlers (X, Telegram, Discord, Slack...) read the Open Graph tags and show the
// token's card; people are sent on to the app with the token modal open. The app's own #token= deep link cannot do
// this job: a URL fragment is never sent to a server, so every crawler saw the same generic preview.

const { SITE, isContract, isValidId, describeToken, fetchToken } = require('./_lib/token');

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const UTM_KEYS = ['utm_source', 'utm_medium', 'utm_campaign'];

// Where a person lands: the app, with the token modal open (#token= resolves a contract), keeping any utm_* tags of
// the shared link so PostHog can attribute the visit. The defaults mark it as coming from a share card.
function destination(query, token) {
  const q = new URLSearchParams();
  for (const k of UTM_KEYS) {
    const v = query && typeof query[k] === 'string' ? query[k].slice(0, 100) : '';
    if (v) q.set(k, v);
  }
  if (!q.has('utm_source')) q.set('utm_source', 'share');
  if (!q.has('utm_medium')) q.set('utm_medium', 'card');
  if (!q.has('utm_campaign')) q.set('utm_campaign', token || 'token');
  return `/?${q.toString()}#token=${encodeURIComponent(token || '')}`;
}

function page({ title, description, image, url, dest }) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>${esc(title)}</title>
<meta name="description" content="${esc(description)}">
<link rel="canonical" href="${esc(url)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="BNB Agent Radar">
<meta property="og:title" content="${esc(title)}">
<meta property="og:description" content="${esc(description)}">
<meta property="og:url" content="${esc(url)}">
<meta property="og:image" content="${esc(image)}">
<meta property="og:image:secure_url" content="${esc(image)}">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${esc(title)}">
<meta name="twitter:description" content="${esc(description)}">
<meta name="twitter:image" content="${esc(image)}">
<meta name="robots" content="noindex">
<style>body{margin:0;background:#020408;color:#e2e8f0;font:16px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center}a{color:#f0b90b}</style>
</head>
<body>
<p>Opening <a href="${esc(dest)}">BNB Agent Radar</a>…</p>
<script>location.replace(${JSON.stringify(dest).replace(/</g, '\\u003c')});</script>
</body>
</html>`;
}

module.exports = async function handler(req, res) {
  const query = req.query || {};
  const id = String(query.c || '').trim();
  res.setHeader('Content-Type', 'text/html; charset=utf-8');

  const generic = (cache) => {
    res.statusCode = 200;
    res.setHeader('Cache-Control', cache);
    res.end(page({
      title: 'BNB AGENT RADAR | Live BNB Chain Agent ID monitor',
      description: 'Autonomous on-chain radar for Binance agentic wallets, stealth Block 0 fair-mints and smart-wallet inflows.',
      image: `${SITE}/og-preview.png?v=4`,
      url: SITE,
      dest: destination(query, ''),
    }));
  };

  if (!isValidId(id)) return generic('public, s-maxage=3600');
  let rec;
  try {
    rec = await fetchToken(id);
  } catch (e) {
    console.error('share lookup failed', id, e && e.message);
    return generic('public, s-maxage=30'); // backend down: do not let a crawler pin the generic card for long
  }
  if (!rec) return generic('public, s-maxage=300');

  const d = describeToken(rec);
  const token = isContract(d.contract) ? d.contract.toLowerCase() : id;
  const hourBucket = Math.floor(Date.now() / 3600000); // a new image URL each hour, so crawlers refresh the numbers
  res.statusCode = 200;
  res.setHeader('Cache-Control', 'public, max-age=60, s-maxage=600, stale-while-revalidate=3600');
  res.end(page({
    title: d.title,
    description: d.description,
    image: `${SITE}/api/card?c=${encodeURIComponent(token)}&v=${hourBucket}`,
    url: `${SITE}/t/${encodeURIComponent(token)}`,
    dest: destination(query, token),
  }));
};

module.exports.page = page;
module.exports.destination = destination;
