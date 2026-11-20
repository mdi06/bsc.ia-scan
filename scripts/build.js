// Builds the deployable static site into dist/.
//
//   npm run build
//
// app.js and style.css are minified with esbuild. This is NOT a bundle: app.js stays one plain
// script, and esbuild keeps every top-level function/variable name, which index.html and the HTML
// strings built inside app.js rely on (inline onclick="..." handlers). Every other file the site
// serves is copied unchanged. The source files are only ever read, never written.
//
// Cache busting: the minified app.js and style.css are also written as assets/app.<hash>.js and
// assets/style.<hash>.css (hash = first 10 hex chars of the SHA-256 of the minified content), and
// dist/index.html is rewritten to load those. vercel.json gives /assets/* a one-year immutable cache,
// while index.html keeps revalidating, so a deploy is picked up on the next page load and unchanged
// assets are never downloaded again. dist/app.js and dist/style.css stay as unhashed copies (for
// scripts/check_minified.js and for any index.html cached before a deploy). The repository's own
// index.html keeps loading the plain app.js / style.css, so local development needs no build.
//
// Lazy features: each features/<name>.js is an ES module that app.js loads on first use with loadFeature('<name>')
// (a dynamic import(), which works from app.js's classic script). They are bundled with code splitting into
// assets/features/<name>.<hash>.js. Only files directly in features/ are entries; code they share lives in
// features/lib/ and is emitted once as chunk.<hash>.js. The name -> URL
// map is compiled into app.js as __FEATURE_MANIFEST__. app.js itself stays a classic, unbundled script, so its
// top-level names stay global: a feature module reads and assigns them (allDrops, escapeHtml, ...) by plain name, and
// sets window.<fn> for any function that inline onclick="..." handlers call. Locally, without a build, app.js finds
// no manifest and imports /features/<name>.js straight from the repository (api.py serves that folder).
//
// The copy list is derived from what is served today: .vercelignore removes the Python/backend files,
// and of what is left, index.html, app.js and style.css reference only the files listed below
// (index.html: style.css, app.js, icon.svg, /thumbnail-square.png, og-preview.png via the og:image tags;
// app.js: icon.svg, seeded_*.json, feed_snapshot.json, agent_id_stats.json, data/agent_id_stats.json),
// plus the template-*.html pages and the Google verification file.

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const esbuild = require('esbuild');

const ROOT = path.resolve(__dirname, '..');
const DIST = path.join(ROOT, 'dist');

const MINIFY = ['app.js', 'style.css'];

const COPY = [
  'index.html',
  'icon.svg',
  'og-preview.png',
  'thumbnail-square.png',
  'seeded_smart_drops.json',
  'seeded_smart_hunters.json',
  'seeded_top_gainers.json',
  'agent_id_stats.json',
  'template-og.html',
  'template-square.html',
  'google4b1d4cb8f54015fa.html',
];

// feed_snapshot.json is written slimmed rather than copied: compact JSON (the source is pretty-printed) and without
// the fields below, which app.js never reads (it builds its own checklist from the other fields). The browser parses
// it on every first paint, so this cuts that work by about a third. The source file is left as it is.
const SNAPSHOT = 'feed_snapshot.json';
const SNAPSHOT_DROP_FIELDS = ['sniper_checklist'];

function writeSlimSnapshot() {
  const records = JSON.parse(fs.readFileSync(path.join(ROOT, SNAPSHOT), 'utf8'));
  if (!Array.isArray(records)) throw new Error(`${SNAPSHOT}: expected a JSON array`);
  for (const rec of records) {
    if (rec && typeof rec === 'object') for (const key of SNAPSHOT_DROP_FIELDS) delete rec[key];
  }
  const out = JSON.stringify(records);
  fs.writeFileSync(path.join(DIST, SNAPSHOT), out);
  const before = fs.statSync(path.join(ROOT, SNAPSHOT)).size;
  console.log(`${SNAPSHOT}: ${before} -> ${out.length} bytes`);
}

// app.js falls back to data/agent_id_stats.json as its last candidate. data/ is gitignored, so
// copy it only when it exists locally.
const COPY_OPTIONAL = ['data/agent_id_stats.json'];

const ASSETS_DIR = 'assets';
const FEATURES_DIR = 'features';

// Bundle features/*.js and return { name: '/assets/features/<name>.<hash>.js' }. Empty when there are none yet.
function buildFeatures() {
  const srcDir = path.join(ROOT, FEATURES_DIR);
  const entries = fs.existsSync(srcDir)
    ? fs.readdirSync(srcDir).filter((f) => f.endsWith('.js')).sort().map((f) => path.join(srcDir, f))
    : [];
  if (!entries.length) return {};
  const outdir = path.join(DIST, ASSETS_DIR, FEATURES_DIR);
  const result = esbuild.buildSync({
    entryPoints: entries,
    outdir,
    bundle: true,
    splitting: true,
    format: 'esm',
    entryNames: '[name].[hash]',
    chunkNames: 'chunk.[hash]',
    minify: true,
    target: 'es2020',
    legalComments: 'none',
    logLevel: 'warning',
    metafile: true,
  });
  const manifest = {};
  for (const [outPath, info] of Object.entries(result.metafile.outputs)) {
    if (!info.entryPoint) continue;
    const name = path.basename(info.entryPoint, '.js');
    manifest[name] = '/' + path.relative(DIST, path.resolve(ROOT, outPath)).split(path.sep).join('/');
  }
  console.log(`features: ${Object.keys(manifest).length} (${Object.keys(manifest).join(', ')})`);
  return manifest;
}

function contentHash(buf) {
  return crypto.createHash('sha256').update(buf).digest('hex').slice(0, 10);
}

// Replace exactly one occurrence of `from` in `html`; fail the build if it is missing or ambiguous so
// a changed index.html can never silently ship pointing at the unversioned files.
function replaceOnce(html, from, to) {
  const n = html.split(from).length - 1;
  if (n !== 1) throw new Error(`index.html: expected exactly one ${from}, found ${n}`);
  return html.replace(from, to);
}

function main() {
  fs.rmSync(DIST, { recursive: true, force: true });
  fs.mkdirSync(DIST, { recursive: true });

  // Features first: their hashed URLs are compiled into app.js, so a changed feature also changes app.js's hash.
  const featureManifest = buildFeatures();

  for (const name of MINIFY) {
    esbuild.buildSync({
      entryPoints: [path.join(ROOT, name)],
      define: name.endsWith('.js') ? { __FEATURE_MANIFEST__: JSON.stringify(featureManifest) } : undefined,
      outfile: path.join(DIST, name),
      minify: true,
      bundle: false,
      // A conservative target so minification never introduces syntax an older mobile browser lacks.
      target: 'es2020',
      legalComments: 'none',
      logLevel: 'warning',
    });
  }

  for (const name of COPY) {
    fs.copyFileSync(path.join(ROOT, name), path.join(DIST, name));
  }
  writeSlimSnapshot();

  // Content-hashed copies of the minified files, referenced from dist/index.html.
  fs.mkdirSync(path.join(DIST, ASSETS_DIR), { recursive: true });
  const hashed = {};
  for (const name of MINIFY) {
    const buf = fs.readFileSync(path.join(DIST, name));
    const ext = path.extname(name);
    const out = `${ASSETS_DIR}/${path.basename(name, ext)}.${contentHash(buf)}${ext}`;
    fs.writeFileSync(path.join(DIST, out), buf);
    hashed[name] = out;
  }
  let html = fs.readFileSync(path.join(DIST, 'index.html'), 'utf8');
  html = replaceOnce(html, '<link rel="stylesheet" href="style.css">', `<link rel="stylesheet" href="/${hashed['style.css']}">`);
  html = replaceOnce(html, '<script src="app.js"></script>', `<script src="/${hashed['app.js']}"></script>`);
  fs.writeFileSync(path.join(DIST, 'index.html'), html);
  console.log(`index.html -> /${hashed['style.css']}, /${hashed['app.js']}`);
  for (const name of COPY_OPTIONAL) {
    const src = path.join(ROOT, name);
    if (fs.existsSync(src)) {
      fs.mkdirSync(path.dirname(path.join(DIST, name)), { recursive: true });
      fs.copyFileSync(src, path.join(DIST, name));
    }
  }

  console.log('dist/ written: ' + fs.readdirSync(DIST).sort().join(', '));
}

main();
