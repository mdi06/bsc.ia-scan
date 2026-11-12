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


def test_supply_helpers_read_first_available_getter():
    import monitor
    c = "0x" + "1" * 40
    assert monitor.get_token_count(_rpc_for(c, **{"totalMinted()": 42}), c) == 42
    assert monitor.get_token_count(_rpc_for(c, **{"nextTokenId()": 43}), c) == 42  # ids start at 1
    assert monitor.get_token_count(_rpc_for(c), c) is None
    assert monitor.get_total_supply(_rpc_for(c, **{"totalSupply()": 7}), c) == 7
    assert monitor.get_max_supply(_rpc_for(c, **{"MAX_SUPPLY()": 1000}), c) == 1000
    assert monitor.get_max_supply(_rpc_for(c, **{"maxSupply()": 2**256 - 1}), c) is None  # "unlimited"
    assert monitor.get_max_supply(_rpc_for(c), c) is None


def test_fast_sweep_reads_erc721_and_flags_change():
    c = "0x" + "2" * 40
    rpc = _rpc_for(c, **{"totalMinted()": 500, "maxSupply()": 1000})
    item = {"contract": c, "standard": "ERC-721 (NFT Collection)", "total_minted": 100, "max_supply": None, "holders": 3}
    worker._supply_probe_at.clear()
    addr, minted, supply, holders, changed = worker.fast_sweep_contract(rpc, item)
    assert (addr, minted, supply, changed) == (c, 500, 1000, True)
    assert holders == 426  # min(500, int(500 * 0.85) + 1)

    # unchanged on-chain state -> not flagged
    item.update(total_minted=500, max_supply=1000)
    assert worker.fast_sweep_contract(rpc, item)[4] is False


def test_fast_sweep_ignores_tokens_and_1155():
    c = "0x" + "3" * 40
    rpc = _rpc_for(c, **{"totalSupply()": 10**27})
    for std in ("BEP-20 Token (18 decimals)", "Fair-Mint Contract (Dispenses token)", "ERC-1155 (Multi-Token NFT)", "Custom Smart Contract"):
        item = {"contract": c, "standard": std, "total_minted": 0}
        assert worker.fast_sweep_contract(rpc, item) == (c, None, None, None, False)


def test_fast_sweep_does_not_reprobe_missing_supply_cap_every_sweep():
    c = "0x" + "4" * 40
    calls = []

    class Counting(_FakeRpc):
        def call_contract(self, to, data):
            calls.append(data[:10])
            return super().call_contract(to, data)

    rpc = Counting(_rpc_for(c, **{"totalMinted()": 5}).replies)
    item = {"contract": c, "standard": "ERC-721 (NFT Collection)", "total_minted": 5, "max_supply": None}
    worker._supply_probe_at.clear()
    worker.fast_sweep_contract(rpc, item)
    first = len(calls)
    worker.fast_sweep_contract(rpc, item)
    assert len(calls) - first < first  # second sweep skipped the 8 cap probes
