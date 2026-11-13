import worker


def test_scan_range_no_gap_when_slightly_behind():
    # 8 blocks behind: every block is scanned and last_block ends at the head
    assert worker._next_scan_range(100, 108) == (101, 108)


def test_scan_range_catches_up_in_steps_without_skipping():
    assert worker._next_scan_range(100, 125) == (101, 110)   # 25 behind: first 10, nothing skipped
    assert worker._next_scan_range(110, 125) == (111, 120)
    assert worker._next_scan_range(120, 125) == (121, 125)


def test_scan_range_jumps_when_far_behind():
    assert worker._next_scan_range(100, 200) == (191, 200)


def test_scan_range_single_block():
    assert worker._next_scan_range(100, 101) == (101, 101)


def test_known_infra_set_is_unchanged():
    import monitor
    assert worker.monitor.known_infra_addresses() == {
        "0xca143ce32fe78f1f7019d7d551a6402fc5350c73",  # Pancake factory
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
        "0x55d398326f99059ff775485246999027b3197955",  # USDT
        "0x60b9262456de011f7d15a54cdd8c48baaa8b022f",  # Agent ID registry
        "0x08ced32a7f3eec915ba84415e9c07a7286977956",  # Space ID master registry
        "0x5c952063c7fc8610ffdb798152d69f0b9550762b",  # four.meme factory
        "0xf251f83e40a78868fcfa3fa4599dad6494e46034",  # four.meme router
        "0x10ed43c718714eb63d5aa57b78b54704e256024e",  # Pancake router
        "0x13f4ea83d0bd40e75c8222255bc855a974568dd4",
    }


def _nft(c, **kw):
    d = {"contract": c, "standard": "ERC-721 (NFT Collection)", "name": "N", "symbol": "N"}
    d.update(kw)
    return d


def test_element_sweeper_applies_scan_results_and_reports_changes(monkeypatch):
    import monitor
    a, b = "0x" + "a" * 40, "0x" + "b" * 40
    token = {"contract": "0x" + "c" * 40, "standard": "BEP-20 Token", "name": "T", "symbol": "T"}
    history = [_nft(a), _nft(b), token]

    def fake_scan(rpc, items, **kw):
        for it in items:
            if it["contract"] == a:
                it.update(secondary_sales_count=6, secondary_vol_bnb=1.5, floor_price_bnb=0.2,
                          high_price_bnb=0.4, secondary_transfers=3, is_nft_surge=True,
                          nft_surge_badge="[bold]x[/bold]")
        return {a: {"badge_plain": "🦋 SURGE (6 sales)"}}

    monkeypatch.setattr(monitor, "scan_element_nft_volume", fake_scan)
    changed = worker._sweep_element_nft_once(None, history)
    assert changed == [history[0]]
    assert history[0]["floor_price_bnb"] == 0.2
    assert history[0]["floor_price_str"] == "0.200 BNB"
    assert history[0]["signals"] == ["🦋 SURGE (6 sales)"]
    assert "secondary_sales_count" not in history[1] and "floor_price_bnb" not in token

    # a second identical sweep changes nothing, and a new sales count replaces the old surge signal
    assert worker._sweep_element_nft_once(None, history) == []
    monkeypatch.setattr(monitor, "scan_element_nft_volume",
                        lambda rpc, items, **kw: (items[0].update(secondary_sales_count=7, secondary_vol_bnb=1.6,
                                                                  floor_price_bnb=0.2, high_price_bnb=0.4,
                                                                  secondary_transfers=3, is_nft_surge=True,
                                                                  nft_surge_badge="x"), {a: {"badge_plain": "🦋 SURGE (7 sales)"}})[1])
    assert worker._sweep_element_nft_once(None, history) == [history[0]]
    assert history[0]["signals"] == ["🦋 SURGE (7 sales)"]


def test_sqlite_schema_is_created_on_demand_in_supabase_mode(tmp_path, monkeypatch):
    import db as db_module
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "k")
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "none.json"))
    monkeypatch.setattr(db_module, "FAVORITES_FILE", str(tmp_path / "none2.json"))
    mgr = db_module.DatabaseManager(db_path=str(tmp_path / "s.db"))
    assert mgr.mode == "supabase"
    mgr.save_agent_id_stats({"total_created_wallets": 5, "last_block": 1}, force_cloud=False)  # used to raise "no such table"
    conn = mgr._get_sqlite_connection()
    try:
        rows = conn.execute("SELECT wallet FROM user_favorites").fetchall()
    finally:
        conn.close()
    assert [r["wallet"] for r in rows] == ["__system_agent_id_stats__"]


class _FakeRpc:
    def __init__(self, replies):
        self.replies = replies

    def call_contract(self, to, data):
        return self.replies.get((to, data[:10]))


def test_get_pair_address_and_reserves():
    import monitor
    pair = "0x" + "ab" * 20
    rpc = _FakeRpc({
        (monitor.PANCAKE_FACTORY_V2, "0xe6a43905"): "0x" + "0" * 24 + "ab" * 20,
        (pair, "0x0902f1ac"): "0x" + hex(5 * 10**18)[2:].zfill(64) + hex(7 * 10**18)[2:].zfill(64) + "0" * 64,
    })
    assert monitor.get_pair_address(rpc, monitor.PANCAKE_FACTORY_V2, "0x" + "1" * 40, monitor.WBNB_ADDRESS) == pair
    assert monitor.get_reserves(rpc, pair) == (5 * 10**18, 7 * 10**18)


def test_pair_helpers_handle_missing_pair_and_failures():
    import monitor
    zero_pair = _FakeRpc({(monitor.PANCAKE_FACTORY_V2, "0xe6a43905"): "0x" + "0" * 64})
    assert monitor.get_pair_address(zero_pair, monitor.PANCAKE_FACTORY_V2, "0x" + "1" * 40, monitor.WBNB_ADDRESS) is None
    assert monitor.get_pair_address(_FakeRpc({}), monitor.PANCAKE_FACTORY_V2, "0x" + "1" * 40, monitor.WBNB_ADDRESS) is None
    assert monitor.get_reserves(_FakeRpc({}), "0x" + "ab" * 20) is None


def _uint(n):
    return "0x" + hex(n)[2:].zfill(64)


def _rpc_for(contract, **getters):
    import monitor
    return _FakeRpc({(contract, monitor.keccak_selector(sig)[:10]): _uint(v) for sig, v in getters.items()})


def test_read_supply_raw_and_scaled():
    import monitor
    c = "0x" + "1" * 40
    # raw (NFT / fair-mint): first answering getter wins, cap comes from the cap getters
    assert monitor.read_supply(_rpc_for(c, **{"totalMinted()": 42, "MAX_SUPPLY()": 1000}), [c]) == (1000, 42)
    assert monitor.read_supply(_rpc_for(c, **{"nextTokenId()": 43}), [c]) == (None, 42)  # ids start at 1
    assert monitor.read_supply(_rpc_for(c), [c]) == (None, None)
    # "unlimited" caps are ignored
    assert monitor.read_supply(_rpc_for(c, **{"maxSupply()": 2**256 - 1, "totalSupply()": 7}), [c]) == (None, 7)
    # BEP-20: scaled by decimals
    cap, minted = monitor.read_supply(_rpc_for(c, **{"cap()": 1000 * 10**18, "totalSupply()": 250 * 10**18}), [c], 18)
    assert (cap, minted) == (1000.0, 250.0)
    cap, minted = monitor.read_supply(_rpc_for(c, **{"cap()": 5000, "totalSupply()": 1250}), [c], 6)
    assert (cap, minted) == (0.005, 0.00125)
    # a 0-decimal token's raw supply is already whole tokens (H5: it used to be divided by 1e18)
    assert monitor.read_supply(_rpc_for(c, **{"totalSupply()": 10**18}), [c], 0) == (None, 1e18)
    assert monitor.read_supply(_rpc_for(c, **{"totalSupply()": 1_000_000}), [c], 0) == (None, 1_000_000.0)


def test_read_supply_need_max_false_skips_cap_probes_and_uses_core():
    import monitor
    c, core = "0x" + "1" * 40, "0x" + "9" * 40
    calls = []

    class Counting(_FakeRpc):
        def call_contract(self, to, data):
            calls.append((to, data[:10]))
            return super().call_contract(to, data)

    rpc = Counting(_rpc_for(core, **{"totalMinted()": 11, "maxSupply()": 99}).replies)
    assert monitor.read_supply(rpc, [c, core], need_max=False) == (None, 11)  # minted found on the companion
    cap_selectors = {monitor.keccak_selector(s)[:10] for s in monitor.MAX_SUPPLY_GETTERS}
    assert not any(sel in cap_selectors for _, sel in calls)


def test_sweep_kind_and_decimals():
    assert worker._sweep_kind({"standard": "ERC-721 (NFT Collection)"}) == "nft"
    assert worker._sweep_kind({"standard": "BEP-20 Token (18 decimals)"}) == "token"
    assert worker._sweep_kind({"standard": "Fair-Mint Contract (Dispenses $AGIO Token)"}) == "fair"
    assert worker._sweep_kind({"standard": "ERC-1155 (Multi-Token NFT)"}) is None
    assert worker._sweep_kind({"standard": "Custom Smart Contract"}) is None
    assert worker._token_decimals({"standard": "BEP-20 Token (6 decimals)"}) == 6
    assert worker._token_decimals({"standard": "BEP-20 Token"}) is None


def _sweep(rpc, item):
    worker._supply_probe_at.clear()
    worker._core_addr_cache.clear()
    return worker.fast_sweep_contract(rpc, item)


def test_fast_sweep_reads_erc721_and_flags_change():
    c = "0x" + "2" * 40
    rpc = _rpc_for(c, **{"totalMinted()": 500, "maxSupply()": 1000})
    item = {"contract": c, "standard": "ERC-721 (NFT Collection)", "total_minted": 100, "max_supply": None, "holders": 3}
    addr, minted, supply, holders, changed = _sweep(rpc, item)
    assert (addr, minted, supply, changed) == (c, 500, 1000, True)
    assert holders == 426  # min(500, int(500 * 0.85) + 1)

    item.update(total_minted=500, max_supply=1000)  # unchanged on-chain state -> not flagged
    assert worker.fast_sweep_contract(rpc, item)[4] is False


def test_fast_sweep_scales_capped_bep20_and_keeps_holders():
    c = "0x" + "5" * 40
    rpc = _rpc_for(c, **{"totalSupply()": 400 * 10**18})
    item = {"contract": c, "standard": "BEP-20 Token (18 decimals)", "total_minted": 100.0,
            "max_supply": 1000.0, "holders": 77}
    addr, minted, supply, holders, changed = _sweep(rpc, item)
    assert (minted, supply, holders, changed) == (400.0, 1000.0, None, True)  # no holder estimate for tokens

    item["total_minted"] = 400.0
    assert worker.fast_sweep_contract(rpc, item)[4] is False


def test_fast_sweep_fair_mint_is_raw_and_needs_a_cap():
    c = "0x" + "6" * 40
    rpc = _rpc_for(c, **{"totalMinted()": 1500})
    item = {"contract": c, "standard": "Fair-Mint Contract (Dispenses $WAT Token)", "total_minted": 4, "max_supply": 8000}
    assert _sweep(rpc, item)[1:] == (1500, 8000, None, True)
    item["max_supply"] = None  # no cap -> progress means nothing, so it is left alone
    assert _sweep(rpc, item) == (c, None, None, None, False)


def test_fast_sweep_skips_uncapped_tokens_undecodable_decimals_and_1155():
    c = "0x" + "3" * 40
    rpc = _rpc_for(c, **{"totalSupply()": 10**27})
    for item in (
        {"contract": c, "standard": "BEP-20 Token (18 decimals)", "total_minted": 0},                      # no cap
        {"contract": c, "standard": "BEP-20 Token", "total_minted": 0, "max_supply": 10},                  # no decimals
        {"contract": c, "standard": "ERC-1155 (Multi-Token NFT)", "total_minted": 0, "max_supply": 10},
        {"contract": c, "standard": "Custom Smart Contract", "total_minted": 0, "max_supply": 10},
    ):
        assert _sweep(rpc, item) == (c, None, None, None, False)


def test_fast_sweep_does_not_reprobe_missing_nft_cap_every_sweep():
    c = "0x" + "4" * 40
    calls = []

    class Counting(_FakeRpc):
        def call_contract(self, to, data):
            calls.append(data[:10])
            return super().call_contract(to, data)

    rpc = Counting(_rpc_for(c, **{"totalMinted()": 5}).replies)
    item = {"contract": c, "standard": "ERC-721 (NFT Collection)", "total_minted": 5, "max_supply": None}
    _sweep(rpc, item)
    first = len(calls)
    worker.fast_sweep_contract(rpc, item)
    assert len(calls) - first < first  # second sweep skipped the cap probes


def test_mint_progress_is_part_of_the_db_fingerprint():
    from db import DatabaseManager
    base = {"contract": "0x" + "1" * 40, "total_minted": 100, "max_supply": 1000}
    assert DatabaseManager._compute_fingerprint(base) != DatabaseManager._compute_fingerprint({**base, "total_minted": 400})
    assert DatabaseManager._compute_fingerprint(base) == DatabaseManager._compute_fingerprint({**base, "total_minted": 104})


def test_cold_batch_rotates_through_every_item():
    items = list(range(120))
    seen, offset = [], 0
    for _ in range(3):
        batch, offset = worker._next_cold_batch(items, offset)
        assert len(batch) == 50
        seen += batch
    assert set(seen) == set(items[:120]) and len(seen) == 150  # 120 covered, wrapped 30 again
    assert worker._next_cold_batch([1, 2, 3], 7) == ([1, 2, 3], 0)


def _pair(base, pair, liq, px="1", quote="0x" + "f" * 40):
    return {"baseToken": {"address": base}, "quoteToken": {"address": quote}, "pairAddress": pair,
            "liquidity": {"usd": liq}, "priceUsd": px}


def test_collect_dex_pairs_picks_most_liquid_base_pair_and_ignores_quote_side():
    fist, other = "0x" + "a" * 40, "0x" + "b" * 40
    updates = {}
    worker._collect_dex_pairs([
        _pair(fist, "0xp1", 500, "0.2634"),
        _pair(fist, "0xp2", 7_000_000, "0.2636"),
        _pair(other, "0xp3", 10, "5"),
        _pair("0x" + "c" * 40, "0xp4", 99, "9", quote=fist),   # FIST is only the QUOTE here: not recorded for FIST
    ], {fist, other}, updates)
    assert updates[fist]["pairAddress"] == "0xp2"
    assert updates[other]["pairAddress"] == "0xp3"
    assert "0xp4" in updates and "0x" + "c" * 40 not in updates  # known by pair address only


def test_fetch_dexscreener_pairs_accepts_list_and_dict_bodies(monkeypatch):
    import io, json, urllib.request

    seen = []

    def fake_urlopen(req, timeout=0):
        seen.append(req.full_url)
        return io.BytesIO(json.dumps(body["v"]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    body = {"v": [{"pairAddress": "0x1"}]}
    assert worker._fetch_dexscreener_pairs("https://x/a") == [{"pairAddress": "0x1"}]
    body["v"] = {"pairs": [{"pairAddress": "0x2"}]}
    assert worker._fetch_dexscreener_pairs("https://x/b") == [{"pairAddress": "0x2"}]
    body["v"] = {"pairs": None}
    assert worker._fetch_dexscreener_pairs("https://x/c") == []
    assert worker.DEXSCREENER_TOKENS_URL.endswith("/tokens/v1/bsc/")  # not the 30-pairs-total endpoint


def test_pancake_fallback_never_overwrites_known_liquidity_or_dexscreener_pair():
    base = {"total_minted": 5, "standard": "BEP-20 Token (18 decimals)"}
    assert worker._needs_pancake_fallback(base) is True
    assert worker._needs_pancake_fallback({**base, "lp_usd": 7_000_000}) is False          # FIST case
    assert worker._needs_pancake_fallback({**base, "pair_url": "https://dexscreener.com/bsc/0xabc"}) is False
    assert worker._needs_pancake_fallback({**base, "pair_url": "https://pancakeswap.finance/info/v2/pairs/0x1"}) is True
    assert worker._needs_pancake_fallback({**base, "total_minted": 0, "score": 0}) is False  # nothing worth estimating
    # NFTs and custom contracts have no WBNB pool, so they are never looked up
    assert worker._needs_pancake_fallback({"total_minted": 5, "standard": "ERC-721 (NFT Collection)"}) is False
    assert worker._needs_pancake_fallback({"total_minted": 5, "standard": "Custom Smart Contract"}) is False

def test_apply_dex_pair_copies_metrics_and_computes_missing_market_cap():
    item = {"total_minted": 200_000_000.0}
    worker._apply_dex_pair(item, {
        "liquidity": {"usd": 7_114_700.0}, "priceUsd": "0.2636", "baseToken": {"symbol": "FIST"},
        "url": "https://dexscreener.com/bsc/0xpair", "volume": {"h24": 608_291.0}, "marketCap": 48_450_648,
        "fdv": 48_450_648, "priceChange": {"h24": 7.58}, "txns": {"h24": {"buys": 652, "sells": 1009}, "h1": {"buys": 40}},
    })
    assert (item["token_price_usd"], item["lp_usd"], item["market_cap"], item["buys_24h"], item["buys_1h"]) == (0.2636, 7_114_700.0, 48_450_648, 652, 40)
    assert item["pair_url"] == "https://dexscreener.com/bsc/0xpair"
    # DexScreener gave no market cap: fall back to price x supply, like before
    item2 = {"total_minted": 1000.0, "standard": "BEP-20 Token (18 decimals)"}
    worker._apply_dex_pair(item2, {"liquidity": {"usd": 5}, "priceUsd": "2"})
    assert item2["market_cap"] == 2000.0
    # ... but only where the supply's unit is certain (H4): an NFT / dispenser counter is not a token supply
    item3 = {"total_minted": 1000.0, "standard": "ERC-721 (NFT Collection)"}
    worker._apply_dex_pair(item3, {"liquidity": {"usd": 5}, "priceUsd": "2"})
    assert item3["market_cap"] is None


def test_apply_pancake_estimate(monkeypatch):
    import monitor
    token = "0x" + "1" * 40  # sorts below WBNB, so WBNB is token1 and its reserve is r1
    pair = "0x" + "ab" * 20
    monkeypatch.setattr(monitor, "get_pair_address", lambda rpc, f, a, b: pair)
    monkeypatch.setattr(monitor, "get_reserves", lambda rpc, p: (9 * 10**18, 4 * 10**18))
    monkeypatch.setattr(monitor, "refresh_bnb_price", lambda rpc=None, max_age=60.0: 600.0)
    item = {"contract": token}
    assert worker._apply_pancake_estimate(None, item) is True
    # liquidity is two-sided: 2 x the WBNB reserve (4 WBNB at $600 -> $4800), like DexScreener's figure
    assert item["lp_wbnb"] == 8.0 and item["lp_usd"] == 4800.0 and item["pair_url"].endswith(pair)
    monkeypatch.setattr(monitor, "get_pair_address", lambda rpc, f, a, b: None)
    assert worker._apply_pancake_estimate(None, {"contract": token}) is False


def test_market_updater_saves_dexscreener_results_before_the_slow_pancake_pass(monkeypatch):
    """Regression: the sweep used to do one RPC call per unlisted drop (minutes) before saving anything."""
    import monitor
    listed = {"contract": "0x" + "a" * 40, "standard": "BEP-20 Token (18 decimals)", "token_price_usd": 1.0, "lp_usd": 10.0}
    unlisted = [{"contract": f"0x{i:040x}", "standard": "BEP-20 Token (18 decimals)", "total_minted": 5.0} for i in range(1, 61)]
    nft = {"contract": "0x" + "e" * 40, "standard": "ERC-721 (NFT Collection)", "total_minted": 5}
    history = [listed, nft] + unlisted

    events = []
    monkeypatch.setattr(worker, "get_shared_history", lambda force_refresh=False: history)
    monkeypatch.setattr(worker, "_fetch_dexscreener_pairs", lambda url: [
        {"baseToken": {"address": listed["contract"]}, "pairAddress": "0xp", "priceUsd": "2.5", "liquidity": {"usd": 99999},
         "volume": {"h24": 1}, "url": "https://dexscreener.com/bsc/0xp"}] if "tokens/v1" in url else [])
    monkeypatch.setattr(monitor, "save_history", lambda h, delta=None: events.append(("save", len(delta or []))))
    monkeypatch.setattr(monitor, "BscRpcClient", lambda eps: object())
    monkeypatch.setattr(monitor, "get_pair_address", lambda rpc, f, a, b: events.append(("rpc", a)) or None)
    monkeypatch.setattr(monitor, "evaluate_sniper_readiness", lambda it: {})
    worker._pancake_checked_at.clear()

    class Stop(Exception):
        pass

    def fake_sleep(s):
        if s >= 100:
            raise Stop()

    monkeypatch.setattr(worker.time, "sleep", fake_sleep)
    try:
        worker.run_market_dynamics_updater_loop()
    except Stop:
        pass

    assert listed["token_price_usd"] == 2.5 and listed["lp_usd"] == 99999.0
    assert events[0] == ("save", 1)                                   # saved first, before any RPC
    rpc_calls = [e for e in events if e[0] == "rpc"]
    assert len(rpc_calls) == worker.PANCAKE_FALLBACK_PER_SWEEP        # capped, not all 60
    assert nft["contract"] not in [c for _, c in rpc_calls]           # NFTs are never looked up
    assert len(worker._pancake_checked_at) == worker.PANCAKE_FALLBACK_PER_SWEEP  # cooldown recorded


def _http_429(retry_after=None):
    import urllib.error
    from email.message import Message
    hdrs = Message()
    if retry_after is not None:
        hdrs["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://x", 429, "Too Many Requests", hdrs, None)


def test_dexscreener_fetch_retries_429_then_succeeds(monkeypatch):
    import io, json, urllib.request
    waits, calls = [], []

    def fake_urlopen(req, timeout=0):
        calls.append(1)
        if len(calls) < 3:
            raise _http_429(retry_after=2 if len(calls) == 1 else None)
        return io.BytesIO(json.dumps([{"pairAddress": "0x1"}]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(worker.time, "sleep", lambda s: waits.append(s))
    assert worker._fetch_dexscreener_pairs("https://x/a") == [{"pairAddress": "0x1"}]
    assert len(calls) == 3 and waits == [2.0, 5.0 * 2]  # Retry-After honoured, else 5s * attempt


def test_dexscreener_fetch_gives_up_after_repeated_429_and_does_not_retry_other_errors(monkeypatch):
    import urllib.error, urllib.request
    calls = []
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)

    def always_429(req, timeout=0):
        calls.append(1)
        raise _http_429()

    monkeypatch.setattr(urllib.request, "urlopen", always_429)
    try:
        worker._fetch_dexscreener_pairs("https://x/a")
        assert False, "should raise"
    except urllib.error.HTTPError as e:
        assert e.code == 429
    assert len(calls) == 3

    calls.clear()

    def server_error(req, timeout=0):
        calls.append(1)
        raise urllib.error.HTTPError("https://x", 500, "boom", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", server_error)
    try:
        worker._fetch_dexscreener_pairs("https://x/a")
    except urllib.error.HTTPError:
        pass
    assert len(calls) == 1


def test_rate_limited_sweep_does_not_expire_the_batches_it_never_requested(monkeypatch):
    """Merge of the 429 back-off (stop requesting) with stale-market expiry: records in the batches a sweep never
    requested are unknown, not "no pair", so they must keep their market data. Only the batch that answered
    (with no pair) may expire."""
    import time as _t
    import monitor
    old = _t.time() - 7 * 3600
    history = [{"contract": f"0x{i:040x}", "standard": "ERC-721 (NFT Collection)", "token_price_usd": 0.5,
                "lp_usd": 2000.0, "market_source": "dexscreener", "market_updated_at": old} for i in range(1, 91)]
    calls = []

    def fetch(url):
        calls.append(url)
        if len(calls) >= 2:
            raise _http_429()
        return []  # first batch answers: DexScreener has no pair for these

    monkeypatch.setattr(worker, "get_shared_history", lambda force_refresh=False: history)
    monkeypatch.setattr(worker, "_fetch_dexscreener_pairs", fetch)
    monkeypatch.setattr(monitor, "save_history", lambda h, delta=None: None)
    monkeypatch.setattr(monitor, "BscRpcClient", lambda eps: object())
    monkeypatch.setattr(monitor, "refresh_bnb_price", lambda *a, **k: 650.0)
    monkeypatch.setattr(monitor, "evaluate_sniper_readiness", lambda it: {})

    class Stop(Exception):
        pass

    def fake_sleep(s):
        if s >= 100:
            raise Stop()

    monkeypatch.setattr(worker.time, "sleep", fake_sleep)
    try:
        worker.run_market_dynamics_updater_loop()
    except Stop:
        pass

    assert len(calls) == 2                                            # stopped after the 429, as before
    assert all(x["lp_usd"] is None and x["market_stale"] for x in history[:30])
    assert all(x["lp_usd"] == 2000.0 and not x.get("market_stale") for x in history[30:])


def test_unrequested_contracts_lists_the_tail_lowercased():
    recs = [{"contract": "0xAA"}, {"contract": "0xBb"}, {"contract": None}, {"contract": "0xCC"}]
    assert worker._unrequested_contracts(recs, 1) == {"0xbb", "0xcc"}
    assert worker._unrequested_contracts(recs, 9) == set()
