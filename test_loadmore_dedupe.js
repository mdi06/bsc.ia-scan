const assert = require('assert');
const app = require('./app.js');

const mk = (c, block = 1) => ({ contract: c, block });

// Existing rows (mixed case) + a page that overlaps them
app.setAllDrops([mk('0xAAA'), mk('0xbbb')]);
const added = app.appendUniqueDrops([mk('0xaaa'), mk('0xBBB'), mk('0xccc'), mk('0xCCC')]);
assert.strictEqual(added, 1, 'only the genuinely new contract is added');
const contracts = app.getAllDrops().map(d => d.contract.toLowerCase());
assert.deepStrictEqual(contracts.sort(), ['0xaaa', '0xbbb', '0xccc']);
console.log('✔ appendUniqueDrops skips overlapping and repeated contracts: PASS');

// Calling it again with the same page must be a no-op
assert.strictEqual(app.appendUniqueDrops([mk('0xaaa'), mk('0xccc')]), 0);
assert.strictEqual(app.getAllDrops().length, 3);
console.log('✔ appendUniqueDrops is idempotent: PASS');

// Bad input is ignored
assert.strictEqual(app.appendUniqueDrops(null), 0);
console.log('✔ appendUniqueDrops ignores non-arrays: PASS');
