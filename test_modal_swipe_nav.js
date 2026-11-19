// Swipe navigation in the token modal and feedback in the tab-order sheet.
// The browser behaviour (thresholds, vertical scroll, CLI box, reduced motion) is checked in the preview; this file
// pins the parts that must hold on the stand-in DOM objects the other node tests use.
const assert = require('assert');
const app = require('./app.js');

// 1. Stand-in DOM with no addEventListener / dataset / getElementById must not throw.
global.document = { getElementById: () => null };
assert.doesNotThrow(() => app.setupModalSwipeNav());
console.log('✔ setupModalSwipeNav tolerates a document with no #modalContent: PASS');

// 2. Binds once, even when called again, and only the touch listeners it needs.
const listeners = {};
const content = {
  dataset: {},
  addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
};
global.document = { getElementById: (id) => (id === 'modalContent' ? content : null) };
app.setupModalSwipeNav();
app.setupModalSwipeNav();
assert.strictEqual(content.dataset.swipeNav, '1');
assert.strictEqual(listeners.touchstart.length, 1, 'touchstart bound once');
assert.strictEqual(listeners.touchend.length, 1, 'touchend bound once');
assert.strictEqual(listeners.touchcancel.length, 1, 'touchcancel bound once');
console.log('✔ setupModalSwipeNav binds once on #modalContent: PASS');

// 3. A swipe with no open modal (no inspect item) must be a silent no-op.
global.document = { getElementById: () => null };
assert.doesNotThrow(() => app.navigateInspectModal(1, 'swipe'));
console.log('✔ navigateInspectModal(…, "swipe") is a no-op without an open modal: PASS');

// 4. Edge taps on the tab-order sheet explain themselves instead of throwing or silently doing nothing.
global.document = { getElementById: () => null };
assert.doesNotThrow(() => app.movePill(app.DEFAULT_PILLS_SEQUENCE[0], -1));
assert.doesNotThrow(() => app.movePill(app.DEFAULT_PILLS_SEQUENCE[app.DEFAULT_PILLS_SEQUENCE.length - 1], 1));
assert.doesNotThrow(() => app.movePillToTop(app.DEFAULT_PILLS_SEQUENCE[0]));
assert.doesNotThrow(() => app.movePill('NOT_A_TAB', -1));
console.log('✔ movePill / movePillToTop at the edges and on unknown keys do not throw: PASS');

// 5. Restore the global so later tests in the same process see no stand-in document.
delete global.document;
