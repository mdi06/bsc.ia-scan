// Checks that dist/app.js (the minified build) keeps every global that the page and the tests need.
//
//   npm run build && npm run check:minified      (or: node scripts/check_minified.js)
//
// 1. Collects every function called from an inline event handler (onclick="...", oninput="...", ...) in
//    index.html and in the HTML strings inside app.js.
// 2. Collects every top-level function / const / let / var name declared in app.js.
// 3. Loads the original and the minified script as classic scripts in separate vm contexts (the way a
//    browser loads them) and checks that each name from 1 and 2 that exists in the original also exists,
//    with the same type, in the minified one.
// 4. Checks that the module.exports surface the Node tests use is identical.
//
// Exits non-zero on any mismatch. Read-only: it never writes to the source files.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');

const JS_KEYWORDS = new Set(['if', 'for', 'while', 'switch', 'return', 'function', 'catch', 'typeof', 'void', 'new', 'await', 'async', 'delete', 'else', 'do', 'try', 'var', 'let', 'const']);

// Handler bodies: find on<event>= then read up to the matching closing quote, skipping ${...} template parts.
function extractHandlers(src) {
  const out = [];
  const re = /\bon[a-z]+\s*=\s*(\\?)(["'])/g;
  let m;
  while ((m = re.exec(src))) {
    const quote = m[2];
    let i = re.lastIndex;
    let depth = 0;
    let body = '';
    for (; i < src.length; i++) {
      const ch = src[i];
      if (depth === 0 && ch === '$' && src[i + 1] === '{') { depth = 1; i++; continue; }
      if (depth > 0) {
        if (ch === '{') depth++;
        else if (ch === '}') depth--;
        continue;
      }
      if (ch === '\\' && src[i + 1] === quote) { i++; body += quote; continue; }
      if (ch === quote) break;
      if (ch === '\n' && quote === "'") break;
      body += ch;
    }
    out.push(body);
  }
  return out;
}

function calledNames(handlerBody) {
  const names = new Set();
  const re = /(^|[^.\w$])([A-Za-z_$][\w$]*)\s*\(/g;
  let m;
  while ((m = re.exec(handlerBody))) {
    if (!JS_KEYWORDS.has(m[2])) names.add(m[2]);
  }
  return names;
}

function topLevelNames(src) {
  const names = new Set();
  for (const m of src.matchAll(/^(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)/gm)) names.add(m[1]);
  for (const m of src.matchAll(/^(?:const|let|var)\s+([A-Za-z_$][\w$]*)/gm)) names.add(m[1]);
  return names;
}

// Load a script the way a browser does (classic script, no module wrapper). Returns a function that
// evaluates `typeof <name>` in that script's global scope, which sees function/var/let/const alike.
function load(file) {
  const sandbox = { console: { log() {}, warn() {}, error() {}, info() {}, debug() {} }, setTimeout, clearTimeout, setInterval() { return 0; }, clearInterval() {} };
  sandbox.module = { exports: {} };
  const ctx = vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(file, 'utf8'), ctx, { filename: file });
  return { typeOf: (name) => vm.runInContext(`typeof ${name}`, ctx), exports: sandbox.module.exports };
}

function main() {
  const srcFile = path.join(ROOT, 'app.js');
  const minFile = path.join(ROOT, 'dist', 'app.js');
  if (!fs.existsSync(minFile)) {
    console.error('dist/app.js not found. Run `npm run build` first.');
    process.exit(2);
  }

  const appSrc = read('app.js');
  const htmlSrc = read('index.html');

  const handlerNames = new Set();
  let handlerCount = 0;
  for (const src of [htmlSrc, appSrc]) {
    for (const body of extractHandlers(src)) {
      handlerCount++;
      for (const n of calledNames(body)) handlerNames.add(n);
    }
  }
  const declared = topLevelNames(appSrc);

  const orig = load(srcFile);
  const mini = load(minFile);

  const failures = [];
  if (process.argv.includes('--list')) console.log('handler functions: ' + [...handlerNames].sort().join(', '));
  const notAppGlobals = [];

  for (const name of handlerNames) {
    const o = orig.typeOf(name);
    const m = mini.typeOf(name);
    if (o === 'undefined' && m === 'undefined') { notAppGlobals.push(name); continue; }
    if (o !== m) failures.push(`handler function ${name}: original is ${o}, minified is ${m}`);
  }
  let declaredChecked = 0;
  for (const name of declared) {
    const o = orig.typeOf(name);
    const m = mini.typeOf(name);
    declaredChecked++;
    if (o !== m) failures.push(`top-level ${name}: original is ${o}, minified is ${m}`);
  }

  const oExports = Object.keys(orig.exports).sort();
  const mExports = Object.keys(mini.exports).sort();
  if (JSON.stringify(oExports) !== JSON.stringify(mExports)) {
    failures.push('module.exports keys differ between original and minified');
  }

  console.log(`inline handlers scanned: ${handlerCount}`);
  console.log(`distinct function names called from handlers: ${handlerNames.size}`);
  console.log(`  defined at top level in the minified file: ${handlerNames.size - notAppGlobals.length - failures.filter((f) => f.startsWith('handler')).length}`);
  console.log(`  undefined in BOTH original and minified (browser built-ins, or functions that were already missing before minifying): ${notAppGlobals.sort().join(', ') || 'none'}`);
  console.log(`top-level declarations in app.js checked against the minified file: ${declaredChecked}`);
  console.log(`module.exports keys (original / minified): ${oExports.length} / ${mExports.length}`);

  if (failures.length) {
    console.error('\nFAIL');
    for (const f of failures) console.error('  ' + f);
    process.exit(1);
  }
  console.log('\nOK: every handler function and top-level name survives minification.');
}

main();
