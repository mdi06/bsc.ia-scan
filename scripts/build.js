// Builds the deployable static site into dist/.
//
//   npm run build
//
// app.js and style.css are minified with esbuild. This is NOT a bundle: app.js stays one plain
// script, and esbuild keeps every top-level function/variable name, which index.html and the HTML
// strings built inside app.js rely on (inline onclick="..." handlers). Every other file the site
// serves is copied unchanged. The source files are only ever read, never written.
//
// The copy list is derived from what is served today: .vercelignore removes the Python/backend files,
// and of what is left, index.html, app.js and style.css reference only the files listed below
// (index.html: style.css, app.js, icon.svg, /thumbnail-square.png, og-preview.png via the og:image tags;
// app.js: icon.svg, seeded_*.json, feed_snapshot.json, agent_id_stats.json, data/agent_id_stats.json),
// plus the template-*.html pages and the Google verification file.

const fs = require('fs');
const path = require('path');
const esbuild = require('esbuild');

const ROOT = path.resolve(__dirname, '..');
const DIST = path.join(ROOT, 'dist');

const MINIFY = ['app.js', 'style.css'];

const COPY = [
  'index.html',
  'icon.svg',
  'og-preview.png',
  'thumbnail-square.png',
  'feed_snapshot.json',
  'seeded_smart_drops.json',
  'seeded_smart_hunters.json',
  'seeded_top_gainers.json',
  'agent_id_stats.json',
  'template-og.html',
  'template-square.html',
  'google4b1d4cb8f54015fa.html',
];

// app.js falls back to data/agent_id_stats.json as its last candidate. data/ is gitignored, so
// copy it only when it exists locally.
const COPY_OPTIONAL = ['data/agent_id_stats.json'];

function main() {
  fs.rmSync(DIST, { recursive: true, force: true });
  fs.mkdirSync(DIST, { recursive: true });

  for (const name of MINIFY) {
    esbuild.buildSync({
      entryPoints: [path.join(ROOT, name)],
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
