"""Fewer RPC requests from the fast sweeper: batched eth_call (BscRpcClient.call_batch), a per-contract memory of which
supply getter answers (monitor._GETTER_MEMO), and worker.fast_sweep_batch. Answers must match the one-by-one path;
only the number of requests may differ."""
import monitor
import worker


def _uint(n):
    return "0x" + hex(n)[2:].zfill(64)


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


class _FakeHttp:
    """Answers JSON-RPC batches from `handler(payload) -> response data`; records every post."""
    def __init__(self, handler):
        self.handler, self.posts = handler, []

    def post(self, url, json=None, headers=None):
        self.posts.append((url, json))
        out = self.handler(json)
        return out if isinstance(out, _Resp) else _Resp(out)


def _client(http):
    c = monitor.BscRpcClient(["https://a.example", "https://b.example"])
    c._http_client = http
    return c


def _echo_batch(payload):
    return [{"jsonrpc": "2.0", "id": p["id"], "result": _uint(p["id"] + 1)} for p in payload]


def test_call_batch_maps_results_by_id_even_out_of_order_and_item_errors_are_none():
    def handler(payload):
        rows = [{"jsonrpc": "2.0", "id": p["id"], "result": _uint(p["id"] + 10)} for p in payload]
        rows[1] = {"jsonrpc": "2.0", "id": rows[1]["id"], "error": {"message": "execution reverted"}}
        return list(reversed(rows))

    http = _FakeHttp(handler)
    res = _client(http).call_batch([("eth_call", [{"to": "0x1"}, "latest"]) for _ in range(3)])
    assert res == [_uint(10), None, _uint(12)]
    assert len(http.posts) == 1                      # three calls, one request


def test_call_batch_splits_into_chunks():
    http = _FakeHttp(_echo_batch)
    res = _client(http).call_batch([("eth_blockNumber", []) for _ in range(120)], chunk=50)
    assert len(res) == 120 and len(http.posts) == 3
    assert [len(p[1]) for p in http.posts] == [50, 50, 20]


def test_call_batch_falls_back_to_single_calls_when_batches_are_refused():
    http = _FakeHttp(lambda payload: {"jsonrpc": "2.0", "id": 1, "error": {"message": "batch not supported"}})
    c = _client(http)
    singles = []
    c.call = lambda m, p: singles.append((m, p)) or "0xok"
    res = c.call_batch([("eth_call", [1]), ("eth_call", [2])])
    assert res == ["0xok", "0xok"] and len(singles) == 2
    posts_after_first = len(http.posts)
    c.call_batch([("eth_call", [3])])                # batches are not retried straight away
    assert len(http.posts) == posts_after_first


def test_call_batch_treats_an_all_empty_batch_as_a_refusal():
    http = _FakeHttp(lambda payload: [{"jsonrpc": "2.0", "id": p["id"], "error": {"message": "limit"}} for p in payload])
    c = _client(http)
    c.call = lambda m, p: "0xone"
    assert c.call_batch([("eth_call", [1]), ("eth_call", [2])]) == ["0xone", "0xone"]


def test_call_batch_without_http_client_uses_single_calls():
    c = monitor.BscRpcClient(["https://a.example"])
    c._http_client = None
    c.call = lambda m, p: "0x1"
    assert c.call_batch([("eth_call", [1])]) == ["0x1"]


class _CountingRpc:
    """call_contract + call_batch over one reply table; counts both."""
    def __init__(self, replies):
        self.replies, self.single, self.batched, self.batches = replies, 0, 0, 0

    def _answer(self, to, data):
        return self.replies.get((to, data[:10]))

    def call_contract(self, to, data):
        self.single += 1
        return self._answer(to, data)

    def call_batch(self, calls):
        self.batches += 1
        self.batched += len(calls)
        return [self._answer(p[0]["to"], p[0]["data"]) for _m, p in calls]


def _replies(contract, **getters):
    return {(contract, monitor.keccak_selector(sig)[:10]): _uint(v) for sig, v in getters.items()}


def test_read_supply_remembers_the_getter_that_answered():
    c = "0x" + "1" * 40
    rpc = _CountingRpc(_replies(c, **{"nextTokenId()": 43, "maxSupply()": 100}))
    assert monitor.read_supply(rpc, [c]) == (100, 42)
    first = rpc.single
    assert monitor.read_supply(rpc, [c]) == (100, 42)
    assert rpc.single - first == 2                   # one cap getter + one minted getter, not the whole list
    assert first > 2


def test_read_supply_forgets_a_getter_that_stops_answering_and_relearns():
    c = "0x" + "1" * 40
    rpc = _CountingRpc(_replies(c, **{"totalSupply()": 5}))
    assert monitor.read_supply(rpc, [c], need_max=False) == (None, 5)
    assert monitor._memo_get(c, "minted") == "totalSupply()"
    rpc.replies = _replies(c, **{"nextTokenId()": 9})
    assert monitor.read_supply(rpc, [c], need_max=False) == (None, 8)
    assert monitor._memo_get(c, "minted") == "nextTokenId()"
    rpc.replies = {}
    assert monitor.read_supply(rpc, [c], need_max=False) == (None, None)
    assert monitor._memo_get(c, "minted") is None


def test_read_supply_picks_up_an_earlier_getter_after_the_memo_expires(monkeypatch):
    c = "0x" + "1" * 40
    rpc = _CountingRpc(_replies(c, **{"totalSupply()": 5}))
    monitor.read_supply(rpc, [c], need_max=False)
    rpc.replies = _replies(c, **{"totalMinted()": 7, "totalSupply()": 5})
    assert monitor.read_supply(rpc, [c], need_max=False) == (None, 5)        # memo still valid: the remembered one wins
    now = __import__("time").time()
    monkeypatch.setattr(monitor.time, "time", lambda: now + 3 * monitor.GETTER_MEMO_TTL_SEC)
    assert monitor.read_supply(rpc, [c], need_max=False) == (None, 7)        # expired: list order again


def test_read_minted_batch_matches_read_supply_and_leaves_unknowns_to_the_caller():
    a, b = "0x" + "a" * 40, "0x" + "b" * 40
    rpc = _CountingRpc({**_replies(a, **{"totalMinted()": 11}), **_replies(b, **{"nextTokenId()": 21})})
    assert monitor.read_minted_batch(rpc, [([a], None), ([b], None)]) == [None, None]   # nothing remembered yet
    assert rpc.batches == 0
    monitor.read_supply(rpc, [a], need_max=False)
    monitor.read_supply(rpc, [b], need_max=False)
    assert monitor.read_minted_batch(rpc, [([a], None), ([b], None)]) == [11, 20]
    assert rpc.batches == 1 and rpc.batched == 2


def test_read_minted_batch_scales_tokens_and_defers_when_a_companion_is_needed():
    t, core = "0x" + "c" * 40, "0x" + "d" * 40
    rpc = _CountingRpc({**_replies(t, **{"totalSupply()": 250 * 10**18}), **_replies(core, **{"totalMinted()": 3})})
    monitor.read_supply(rpc, [t], 18, need_max=False)
    assert monitor.read_minted_batch(rpc, [([t], 18)]) == [250.0]
    rpc.replies = {}                                  # the remembered getter stops answering
    assert monitor.read_minted_batch(rpc, [([t], 18)]) == [None]
    assert monitor._memo_get(t, "minted_tok") is None     # forgotten, so read_supply relearns it


def _items():
    nft = "0x" + "2" * 40
    tok = "0x" + "5" * 40
    nocap = "0x" + "4" * 40
    erc1155 = "0x" + "3" * 40
    replies = {
        **_replies(nft, **{"totalMinted()": 500}),
        **_replies(tok, **{"totalSupply()": 400 * 10**18}),
        **_replies(nocap, **{"totalMinted()": 5, "maxSupply()": 50}),
        **_replies(erc1155, **{"totalSupply()": 9}),
    }
    items = [
        {"contract": nft, "standard": "ERC-721 (NFT Collection)", "total_minted": 100, "max_supply": 1000, "holders": 3},
        {"contract": tok, "standard": "BEP-20 Token (18 decimals)", "total_minted": 100.0, "max_supply": 1000.0},
        {"contract": nocap, "standard": "ERC-721 (NFT Collection)", "total_minted": 1, "max_supply": None},
        {"contract": erc1155, "standard": "ERC-1155 (Multi-Token NFT)", "total_minted": 0, "max_supply": 10},
        {"contract": "not-an-address", "standard": "ERC-721 (NFT Collection)", "max_supply": 10},
    ]
    return items, replies


def _reset_worker_caches():
    worker._supply_probe_at.clear()
    worker._core_addr_cache.clear()
    monitor._GETTER_MEMO.clear()


def test_fast_sweep_batch_gives_the_same_results_as_one_by_one_and_uses_fewer_requests():
    items, replies = _items()
    key = lambda r: str(r[0])
    _reset_worker_caches()
    one_rpc = _CountingRpc(replies)
    # the one-by-one path, swept twice (a missing cap is only probed once an hour, so the sweeps differ)
    expected1 = sorted((worker.fast_sweep_contract(one_rpc, it) for it in items), key=key)
    expected2 = sorted((worker.fast_sweep_contract(one_rpc, it) for it in items), key=key)

    _reset_worker_caches()
    rpc = _CountingRpc(replies)
    first = sorted(worker.fast_sweep_batch(rpc, items), key=key)
    assert first == expected1                         # first sweep: nothing remembered, so all one by one
    assert rpc.batches == 0

    singles_before = rpc.single
    second = sorted(worker.fast_sweep_batch(rpc, items), key=key)
    assert second == expected2
    assert rpc.batches >= 1 and rpc.batched >= 2      # the capped nft and token went out in a batch
    assert rpc.single - singles_before < singles_before


def test_fast_sweep_batch_falls_back_when_a_remembered_getter_stops_answering():
    items, replies = _items()
    _reset_worker_caches()
    rpc = _CountingRpc(replies)
    worker.fast_sweep_batch(rpc, items)
    rpc.replies = {**replies, **_replies(items[0]["contract"], **{"nextTokenId()": 601})}
    rpc.replies.pop((items[0]["contract"], monitor.keccak_selector("totalMinted()")[:10]))
    out = {r[0]: r for r in worker.fast_sweep_batch(rpc, items)}
    assert out[items[0]["contract"]][1] == 600        # relearned through the one-by-one path in the same sweep


def test_call_batch_moves_on_to_an_endpoint_that_accepts_the_batch_size():
    """The bnbchain dataseeds answer a large batch with an error per item; publicnode answers it."""
    def handler(payload):
        url = http.posts[-1][0]
        if url == "https://a.example":
            return [{"jsonrpc": "2.0", "id": p["id"], "error": {"message": "batch limit exceeded"}} for p in payload]
        return _echo_batch(payload)

    http = _FakeHttp(handler)
    c = _client(http)
    res = c.call_batch([("eth_call", [i]) for i in range(30)])
    assert res == [_uint(i + 1) for i in range(30)]
    assert [p[0] for p in http.posts] == ["https://a.example", "https://b.example"]
    assert c.endpoints[c.current_idx] == "https://b.example"       # and it stays on the one that works
