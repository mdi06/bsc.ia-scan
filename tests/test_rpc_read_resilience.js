// Browser-side BSC read resilience: per-node backoff with jitter, node rotation, a short read cache,
// de-duplication of identical in-flight reads, and the transaction-path guard.
// Uses a mock fetch and a fake clock, so there is no network and no real waiting.
const assert = require('assert');
const {
  sendRpcCall,
  sendRpcBatch,
  fetchRegistryMintLogs,
  BSC_RPC_CONFIG,
  rpcReadTestHooks: hooks
} = require('./app.js');

const BNB = 'https://bsc-dataseed.bnbchain.org';
const DEFIBIT = 'https://bsc-dataseed1.defibit.io';
const NINICOIN = 'https://bsc-dataseed1.ninicoin.io';
const ONE_RPC = 'https://1rpc.io/bnb';
const PUBLICNODE = 'https://bsc-rpc.publicnode.com';
const PUBLICNODE_2 = 'https://bsc.publicnode.com';
// A placeholder for the keyed slot. It is not a real provider and carries no real key.
const KEYED = 'https://keyed-provider.test/rpc/PLACEHOLDER';

const realFetch = globalThis.fetch;
let clockMs = 1_000_000;
let rnd = 0.5;

// Clears all read state, installs a fake clock, and resets the config fields the tests change.
function fresh(random = 0.5) {
  hooks.reset();
  clockMs = 1_000_000;
  rnd = random;
  hooks.setClock({ now: () => clockMs, random: () => rnd });
  BSC_RPC_CONFIG.keyedUrl = '';
  BSC_RPC_CONFIG.requestTimeoutMs = 8000;
}

function response({ status = 200, headers = {}, json }) {
  const lower = {};
  for (const [k, v] of Object.entries(headers)) lower[k.toLowerCase()] = v;
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => (name.toLowerCase() in lower ? lower[name.toLowerCase()] : null) },
    json: async () => json
  };
}

// Installs a mock fetch. handler(url, body, signal) returns { status, headers, json } (or a promise of it).
// Returns the array the calls are recorded in.
function mockNodes(handler) {
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    const body = JSON.parse(opts.body);
    calls.push({ url, body });
    return response(await handler(url, body, opts.signal));
  };
  return calls;
}

const rateLimited = (headers = {}) => ({ status: 429, headers, json: { error: 'rate limited' } });
const result = (value) => ({ status: 200, json: { jsonrpc: '2.0', id: 1, result: value } });

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

test('429 on the first node: backoff, then fallback to the next node', async () => {
  fresh();
  const calls = mockNodes((url) => (url === BNB ? rateLimited() : result('0x2a')));

  assert.strictEqual(await sendRpcCall('eth_blockNumber', []), '0x2a');
  assert.deepStrictEqual(calls.map((c) => c.url), [BNB, DEFIBIT], 'the 429 node is followed by the next node');

  const health = hooks.endpointHealth(BNB);
  assert.strictEqual(health.failures, 1);
  // First cooldown: base 2000 ms, equal jitter at random 0.5 -> 1500 ms.
  assert.strictEqual(health.cooldownUntil - clockMs, 1500);
});

test('a node in cooldown is skipped, then probed again with a doubled cooldown', async () => {
  fresh();
  let bnbHits = 0;
  const calls = mockNodes((url) => {
    if (url === BNB) {
      bnbHits += 1;
      return rateLimited();
    }
    return result('0x1');
  });

  await sendRpcCall('eth_blockNumber', []);
  assert.strictEqual(bnbHits, 1);

  calls.length = 0;
  await sendRpcCall('eth_getBalance', ['0xabc', 'latest']);
  assert.ok(calls.length > 0, 'the other read still goes out');
  assert.ok(calls.every((c) => c.url !== BNB), 'no request reaches the node in cooldown');

  clockMs += 1500; // the first cooldown is over
  hooks.setRoundRobin(0);
  calls.length = 0;
  await sendRpcCall('eth_getBalance', ['0xdef', 'latest']);
  assert.strictEqual(calls[0].url, BNB, 'the node is probed again first');
  assert.strictEqual(bnbHits, 2);

  const health = hooks.endpointHealth(BNB);
  assert.strictEqual(health.failures, 2);
  // Second cooldown: base 2000 doubled to 4000, equal jitter at random 0.5 -> 3000 ms.
  assert.strictEqual(health.cooldownUntil - clockMs, 3000);
});

test('the cooldown is capped at backoffMaxMs and Retry-After raises it', async () => {
  fresh();
  mockNodes((url) => (url === BNB ? rateLimited({ 'Retry-After': '30' }) : result('0x1')));
  await sendRpcCall('eth_blockNumber', []);
  assert.strictEqual(hooks.endpointHealth(BNB).cooldownUntil - clockMs, 30000, 'Retry-After: 30 is honoured');

  fresh();
  mockNodes((url) => (url === BNB ? rateLimited({ 'Retry-After': '3600' }) : result('0x1')));
  await sendRpcCall('eth_blockNumber', []);
  assert.strictEqual(hooks.endpointHealth(BNB).cooldownUntil - clockMs, BSC_RPC_CONFIG.backoffMaxMs, 'capped at the maximum');
});

test('when every node is rate limited the read resolves to null and stops sending', async () => {
  fresh();
  const calls = mockNodes(() => rateLimited());
  assert.strictEqual(await sendRpcCall('eth_blockNumber', []), null);
  assert.deepStrictEqual(calls.map((c) => c.url).sort(), [BNB, DEFIBIT, NINICOIN, ONE_RPC].sort(), 'each public node tried once');

  calls.length = 0;
  assert.strictEqual(await sendRpcCall('eth_getBalance', ['0x1', 'latest']), null);
  assert.strictEqual(calls.length, 0, 'every node is cooling down, so nothing is sent');
});

test('a successful answer clears the failure count of the node that gave it', async () => {
  fresh();
  let failing = true;
  mockNodes((url) => (url === BNB && failing ? rateLimited() : result('0x1')));
  await sendRpcCall('eth_blockNumber', []);
  assert.strictEqual(hooks.endpointHealth(BNB).failures, 1);

  failing = false;
  clockMs += 1500;
  hooks.setRoundRobin(0);
  assert.strictEqual(await sendRpcCall('eth_getBalance', ['0x2', 'latest']), '0x1');
  assert.deepStrictEqual(hooks.endpointHealth(BNB), { failures: 0, cooldownUntil: 0 });
});

test('a JSON-RPC error about one call moves on without a cooldown for the node', async () => {
  fresh();
  const calls = mockNodes((url) => (url === BNB
    ? { status: 200, json: { jsonrpc: '2.0', id: 1, error: { code: -32000, message: 'execution reverted' } } }
    : result('0x9')));
  assert.strictEqual(await sendRpcCall('eth_call', [{ to: '0x1', data: '0x' }, 'latest']), '0x9');
  assert.strictEqual(calls[0].url, BNB);
  assert.strictEqual(hooks.endpointHealth(BNB), null, 'no backoff for an error about one call');
});

test('a rate-limit error inside a 200 response counts as a 429', async () => {
  fresh();
  mockNodes((url) => (url === BNB
    ? { status: 200, json: { jsonrpc: '2.0', id: 1, error: { code: -32005, message: 'rate limit exceeded' } } }
    : result('0x4')));
  assert.strictEqual(await sendRpcCall('eth_blockNumber', []), '0x4');
  assert.strictEqual(hooks.endpointHealth(BNB).failures, 1);
});

test('network errors, timeouts and bad JSON count as node faults', async () => {
  fresh();
  BSC_RPC_CONFIG.requestTimeoutMs = 20;
  mockNodes((url, body, signal) => {
    if (url === BNB) {
      // Never answers: the request timeout must abort it.
      return new Promise((_, reject) => signal.addEventListener('abort', () => reject(new Error('aborted'))));
    }
    if (url === DEFIBIT) throw new TypeError('Failed to fetch');
    return result('0x3');
  });
  assert.strictEqual(await sendRpcCall('eth_blockNumber', []), '0x3');
  assert.strictEqual(hooks.endpointHealth(BNB).failures, 1, 'timeout charged to the stalled node');
  assert.strictEqual(hooks.endpointHealth(DEFIBIT).failures, 1, 'network error charged to its node');
});

test('identical reads inside the cache window are answered from memory', async () => {
  fresh();
  let served = 0;
  mockNodes(() => {
    served += 1;
    return result('0x' + served.toString(16));
  });
  const first = await sendRpcCall('eth_getBalance', ['0xabc', 'latest']);
  clockMs += 1000;
  const second = await sendRpcCall('eth_getBalance', ['0xabc', 'latest']);
  assert.strictEqual(served, 1, 'the second read did not reach a node');
  assert.strictEqual(second, first);

  clockMs += BSC_RPC_CONFIG.cacheTtlMs; // past the window
  await sendRpcCall('eth_getBalance', ['0xabc', 'latest']);
  assert.strictEqual(served, 2, 'after the window the read goes out again');
});

test('identical reads in flight share one request', async () => {
  fresh();
  const calls = mockNodes(async () => {
    await new Promise((resolve) => setImmediate(resolve));
    return result('0x7');
  });
  const args = [{ to: '0x1', data: '0x' }, 'latest'];
  const [a, b] = await Promise.all([sendRpcCall('eth_call', args), sendRpcCall('eth_call', args)]);
  assert.strictEqual(a, '0x7');
  assert.strictEqual(b, '0x7');
  assert.strictEqual(calls.length, 1, 'one request for two identical in-flight reads');
});

test('transaction and wallet methods are refused without any request', async () => {
  fresh();
  const calls = mockNodes(() => result('0xabc'));
  assert.strictEqual(await sendRpcCall('eth_sendRawTransaction', ['0x00']), null);
  assert.strictEqual(await sendRpcCall('eth_sendTransaction', [{ from: '0x1' }]), null);
  assert.strictEqual(await sendRpcCall('wallet_switchEthereumChain', [{ chainId: '0x38' }]), null);
  const mixed = [
    { jsonrpc: '2.0', id: 1, method: 'eth_call', params: [{ to: '0x1', data: '0x' }, 'latest'] },
    { jsonrpc: '2.0', id: 2, method: 'eth_sendRawTransaction', params: ['0x00'] }
  ];
  assert.deepStrictEqual(await sendRpcBatch(mixed), [], 'a batch with one write is refused as a whole');
  assert.strictEqual(calls.length, 0);
});

test('a batch of eth_call reads falls back on a 429 and comes back sorted by id', async () => {
  fresh();
  const batch = [
    { jsonrpc: '2.0', id: 2, method: 'eth_call', params: [{ to: '0xaa', data: '0x70a08231' }, 'latest'] },
    { jsonrpc: '2.0', id: 1, method: 'eth_call', params: [{ to: '0xbb', data: '0x70a08231' }, 'latest'] }
  ];
  const calls = mockNodes((url) => (url === BNB
    ? rateLimited()
    : { status: 200, json: [{ jsonrpc: '2.0', id: 2, result: '0x2' }, { jsonrpc: '2.0', id: 1, result: '0x1' }] }));
  const items = await sendRpcBatch(batch);
  assert.deepStrictEqual(calls.map((c) => c.url), [BNB, DEFIBIT]);
  assert.deepStrictEqual(items.map((i) => i.id), [1, 2]);
  assert.deepStrictEqual(items.map((i) => i.result), ['0x1', '0x2']);
});

test('a batch with a rate-limit item inside a 200 response is treated as a 429', async () => {
  fresh();
  const batch = [{ jsonrpc: '2.0', id: 1, method: 'eth_call', params: [{ to: '0xaa', data: '0x' }, 'latest'] }];
  const calls = mockNodes((url) => (url === BNB
    ? { status: 200, json: [{ jsonrpc: '2.0', id: 1, error: { code: -32005, message: 'Too Many Requests' } }] }
    : { status: 200, json: [{ jsonrpc: '2.0', id: 1, result: '0x5' }] }));
  const items = await sendRpcBatch(batch);
  assert.strictEqual(calls[1].url, DEFIBIT);
  assert.strictEqual(items[0].result, '0x5');
  assert.strictEqual(hooks.endpointHealth(BNB).failures, 1);
});

test('eth_getLogs uses only the logs nodes and falls back between them', async () => {
  fresh();
  const calls = mockNodes((url) => (url === PUBLICNODE
    ? rateLimited()
    : { status: 200, json: { jsonrpc: '2.0', id: 1, result: [{ blockNumber: '0x10' }] } }));
  const logs = await fetchRegistryMintLogs(100, 200);
  assert.deepStrictEqual(logs, [{ blockNumber: '0x10' }]);
  assert.deepStrictEqual(calls.map((c) => c.url), [PUBLICNODE, PUBLICNODE_2]);
  assert.ok(calls.every((c) => c.body.method === 'eth_getLogs'));
  assert.strictEqual(calls[0].body.params[0].fromBlock, '0x64');
  assert.strictEqual(calls[0].body.params[0].toBlock, '0xc8');
});

test('eth_getLogs gives up with null when no logs node returns a result array', async () => {
  fresh();
  const calls = mockNodes(() => ({ status: 200, json: { jsonrpc: '2.0', id: 1, result: null } }));
  assert.strictEqual(await fetchRegistryMintLogs(1, 2), null);
  assert.deepStrictEqual(calls.map((c) => c.url), [PUBLICNODE, PUBLICNODE_2]);
});

test('the keyed slot is skipped while empty or not https, and tried first when set', async () => {
  fresh();
  let calls = mockNodes(() => result('0x1'));
  await sendRpcCall('eth_blockNumber', []);
  assert.strictEqual(calls[0].url, BNB, 'an empty keyed slot adds no request');

  BSC_RPC_CONFIG.keyedUrl = 'http://plain-http.test/rpc';
  hooks.reset();
  hooks.setClock({ now: () => clockMs, random: () => rnd });
  calls = mockNodes(() => result('0x1'));
  await sendRpcCall('eth_blockNumber', []);
  assert.ok(calls.every((c) => !c.url.startsWith('http://')), 'a non-https keyed URL is never used');

  BSC_RPC_CONFIG.keyedUrl = KEYED;
  hooks.reset();
  hooks.setClock({ now: () => clockMs, random: () => rnd });
  calls = mockNodes((url) => (url === KEYED ? rateLimited() : result('0x2')));
  assert.strictEqual(await sendRpcCall('eth_getBalance', ['0x9', 'latest']), '0x2');
  assert.deepStrictEqual(calls.map((c) => c.url), [KEYED, BNB], 'keyed first, then the public node');
  assert.strictEqual(hooks.endpointHealth(KEYED).failures, 1);

  calls = mockNodes(() => result('0x3'));
  await sendRpcCall('eth_getBalance', ['0x8', 'latest']);
  assert.ok(calls.every((c) => c.url !== KEYED), 'while the keyed node cools down, public nodes answer');
});

test('the read cache stays within its entry limit', async () => {
  fresh();
  mockNodes(() => result('0x1'));
  const limit = BSC_RPC_CONFIG.cacheMaxEntries;
  for (let i = 0; i < limit + 25; i += 1) {
    await sendRpcCall('eth_getBalance', ['0x' + i.toString(16), 'latest']);
  }
  assert.ok(hooks.cacheSize() <= limit, `cache holds ${hooks.cacheSize()} entries, limit ${limit}`);
});

(async () => {
  let failed = 0;
  for (const t of tests) {
    try {
      await t.fn();
      console.log(`✔ ${t.name}: PASS`);
    } catch (err) {
      failed += 1;
      console.error(`✘ ${t.name}: FAIL\n${err.stack}`);
    }
  }
  globalThis.fetch = realFetch;
  if (failed) {
    console.error(`${failed} of ${tests.length} RPC read resilience checks failed`);
    process.exit(1);
  }
  console.log(`All ${tests.length} RPC read resilience checks passed`);
})();
