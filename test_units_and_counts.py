"""Regression tests for the metrics audit, Prompt 2: unknown counts (H3), supply units and market cap (H4, H5),
and token_price_bnb (M1)."""
import io
import json
import time

import pytest

import monitor
import worker

WBNB = monitor.WBNB_ADDRESS.lower()
USDT = monitor.USDT_ADDRESS.lower()


class FakeRpc:
    """call_contract answers from {(address, signature): int}; anything else reverts."""
    def __init__(self, answers):
        self.answers = {(a.lower(), monitor.keccak_selector(sig)): v for (a, sig), v in answers.items()}

    def call_contract(self, addr, data):
        v = self.answers.get((addr.lower(), data))
        return None if v is None else "0x" + hex(v)[2:].zfill(64)


# ---------------------------------------------------------------------------------------------- H3

def _token_details(minted, standard="BEP-20 Token (18 decimals)", category="TOKEN"):
    return {"classification": {"category": category, "standard": standard, "decimals": 18},
            "name": "T", "symbol": "T", "total_minted": minted, "max_supply": None,
            "mint_price_bnb": 0.0, "mint_value_wei": "0"}


class SilentRpc:
    def call_contract(self, addr, data):
        return None


def test_unknown_holders_and_buys_stay_none_not_total_supply(monkeypatch):
    """YAN: buys_24h = 1.04e25 and HIVE: holders = 74574586.24 came from total_minted."""
    monkeypatch.setattr(monitor, "fetch_token_market_data",
                        lambda t: {"holders": None, "buys_24h": None, "buys_1h": None, "sells_24h": None, "lp_usd": 0.0})
    monkeypatch.setattr(monitor, "get_bnb_price_usd", lambda rpc: 700.0)
    yan = "0xe5a672a3b36c01dcd6c0c63e6dd4953fc90c5065"
    stats = monitor.analyze_market_interest(SilentRpc(), yan, None, _token_details(1.04e25))
    assert stats["holders"] is None and stats["buys_24h"] is None
    assert stats["buys_1h"] is None and stats["sells_24h"] is None


def test_known_counts_are_integers(monkeypatch):
    monkeypatch.setattr(monitor, "fetch_token_market_data",
                        lambda t: {"holders": 3, "buys_24h": 7.0, "buys_1h": 0, "sells_24h": 2, "lp_usd": 0.0})
    monkeypatch.setattr(monitor, "get_bnb_price_usd", lambda rpc: 700.0)
    stats = monitor.analyze_market_interest(SilentRpc(), "0x" + "5" * 40, None, _token_details(74574586.24))
    assert (stats["holders"], stats["buys_24h"], stats["buys_1h"], stats["sells_24h"]) == (3, 7, 0, 2)
    assert all(isinstance(stats[k], int) for k in ("holders", "buys_24h"))


def test_fetch_token_market_data_reports_unknown_counts_as_none(monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(monitor.urllib.request, "urlopen", boom)
    res = monitor.fetch_token_market_data("0x" + "4" * 40)
    assert res["holders"] is None and res["buys_24h"] is None and res["buys_1h"] is None and res["sells_24h"] is None


def test_unknown_count_does_not_overwrite_a_known_one_in_the_worker_enrichment(monkeypatch):
    c = "0x" + "7" * 40
    item = {"contract": c, "holders": 12, "buys_24h": 30}
    monkeypatch.setattr(worker, "get_shared_history", lambda force_refresh=False: [item])
    monkeypatch.setattr(worker.monitor, "analyze_market_interest",
                        lambda *a, **k: {"holders": None, "buys_24h": None, "buys_1h": None, "sells_24h": None})
    monkeypatch.setattr(worker.monitor, "save_history", lambda *a, **k: None)
    worker._enrich_contract_background(None, c, None, {})
    assert item["holders"] == 12 and item["buys_24h"] == 30


def test_nft_holder_estimate_is_not_stored_as_holders():
    c = "0x" + "8" * 40
    rpc = FakeRpc({(c, "totalMinted()"): 500})
    item = {"contract": c, "standard": "ERC-721 (NFT Collection)", "total_minted": 100, "holders": None}
    _, minted, _, estimate, changed = worker.fast_sweep_contract(rpc, item)
    assert minted == 500 and estimate == 426 and changed
    assert item["holders"] is None                      # untouched; the sweeper writes holders_estimate only


def test_cleanup_buys_equal_to_total_minted():
    tm = 1.04e25
    yan = {"contract": "0xe5", "total_minted": tm, "buys_24h": tm, "holders": 3,
           "pair_url": "https://dexscreener.com/bsc/0x" + "1" * 40}
    assert worker.clean_count_junk(yan) == ["buys_24h"] and yan["buys_24h"] is None and yan["holders"] == 3

    ptah = {"contract": "0x99", "total_minted": 1e9, "buys_24h": 1e9, "holders": 5}
    worker.clean_count_junk(ptah)
    assert ptah["buys_24h"] is None

    a404 = {"contract": "0xa4", "standard": "ERC-721 (NFT Collection)", "total_minted": 404404, "buys_24h": 404404,
            "holders": 404404, "pair_url": ""}
    assert set(worker.clean_count_junk(a404)) == {"buys_24h", "holders"}
    assert a404["buys_24h"] is None and a404["holders"] is None


def test_cleanup_keeps_real_counts():
    # a real buy count on a DexScreener-listed token that happens to be small and equal to minted stays
    listed = {"total_minted": 652, "buys_24h": 652, "holders": 10, "pair_url": "https://dexscreener.com/bsc/0xp"}
    assert worker.clean_count_junk(listed) == []
    plain = {"total_minted": 500, "buys_24h": 40, "holders": 17, "pair_url": ""}
    assert worker.clean_count_junk(plain) == []
    one = {"total_minted": 1, "buys_24h": 0, "holders": 1, "pair_url": ""}
    assert worker.clean_count_junk(one) == []


def test_cleanup_fractional_holders():
    hive = {"total_minted": 5e9, "buys_24h": 12, "holders": 74574586.24, "pair_url": "https://dexscreener.com/bsc/0xp"}
    assert worker.clean_count_junk(hive) == ["holders"] and hive["holders"] is None


# ---------------------------------------------------------------------------------------------- H4

TOKEN_STD = "BEP-20 Token (18 decimals)"


def test_dont_market_cap_from_supply():
    # DONT 0x29924cec…7777: supply 4.44e14 whole tokens at 2.713e-8 -> about $12.05M (was 1.2e-11)
    item = {"standard": TOKEN_STD, "total_minted": 4.44e14}
    worker._apply_dex_pair(item, {"liquidity": {"usd": 5}, "priceUsd": "2.713e-08"})
    assert item["market_cap"] == pytest.approx(1.2046e7, rel=1e-3)


def test_genesis_market_cap_from_supply():
    item = {"standard": TOKEN_STD, "total_minted": 1e15}
    worker._apply_dex_pair(item, {"liquidity": {"usd": 5}, "priceUsd": "5.045e-12"})
    assert item["market_cap"] == pytest.approx(5045.0, rel=1e-6)


def test_analyze_market_cap_fallback_uses_the_same_rule(monkeypatch):
    monkeypatch.setattr(monitor, "fetch_token_market_data", lambda t: {
        "holders": 5, "buys_24h": 1, "market_price_usd": 2.713e-08, "lp_usd": 100.0, "market_cap": 0.0})
    monkeypatch.setattr(monitor, "get_bnb_price_usd", lambda rpc: 700.0)
    stats = monitor.analyze_market_interest(SilentRpc(), "0x" + "6" * 40, None, _token_details(4.44e14))
    assert stats["market_cap"] == pytest.approx(1.2046e7, rel=1e-3)


def test_raw_18_decimal_supply_scaled_by_read_supply_gives_the_right_answer():
    c = "0x" + "1" * 40
    rpc = FakeRpc({(c, "totalSupply()"): 10**24})
    _, minted = monitor.read_supply(rpc, [c], 18)
    assert minted == 1e6
    assert monitor.market_cap_from_supply(TOKEN_STD, minted, None, 0.5) == pytest.approx(5e5)


def test_agentdog_zero_decimal_market_cap():
    # AgentDog is a 0-decimal token: raw supply = whole tokens
    c = "0x8e1155d3790b916b0a884be77a138a41f7777777"
    rpc = FakeRpc({(c, "totalSupply()"): 1_000_000_000})
    _, minted = monitor.read_supply(rpc, [c], 0)
    assert minted == 1e9
    item = {"standard": "BEP-20 Token (0 decimals)", "total_minted": minted}
    worker._apply_dex_pair(item, {"liquidity": {"usd": 5}, "priceUsd": "0.00002"})
    assert item["market_cap"] == pytest.approx(20000.0)


@pytest.mark.parametrize("standard", ["ERC-721 (NFT Collection)", "Fair-Mint Contract (Dispenses $HASH Token)",
                                      "Custom Smart Contract", None])
def test_no_market_cap_guess_where_the_supply_unit_is_unknown(standard):
    item = {"standard": standard, "total_minted": 334157746779034348324247, "max_supply": 21_000_000}
    worker._apply_dex_pair(item, {"liquidity": {"usd": 5}, "priceUsd": "0.03166"})
    assert item["market_cap"] is None
    assert monitor.supply_for_market_cap(standard, item["total_minted"], 5) is None


def test_dexscreener_market_cap_is_still_preferred():
    item = {"standard": TOKEN_STD, "total_minted": 1000.0}
    worker._apply_dex_pair(item, {"liquidity": {"usd": 5}, "priceUsd": "2", "marketCap": 1900, "fdv": 2100})
    assert item["market_cap"] == 1900 and item["fdv"] == 2100


# ---------------------------------------------------------------------------------------------- H5

def test_zero_decimal_supply_is_not_divided_by_1e18():
    c = "0x" + "2" * 40
    rpc = FakeRpc({(c, "totalSupply()"): 1_000_000, (c, "cap()"): 21_000_000})
    cap, minted = monitor.read_supply(rpc, [c], 0)
    assert (cap, minted) == (21_000_000.0, 1_000_000.0)         # was stored as 1e-12
    cap, minted = monitor.read_supply(rpc, [c], None)           # NFT / dispenser: raw
    assert (cap, minted) == (21_000_000, 1_000_000)


def test_total_supply_is_read_first_for_bep20():
    """SHIB 0x78573ab4…: a counter getter answered with 1 before totalSupply (5.8e11 tokens)."""
    shib = "0x78573ab46e5436e126d3ea1fdef8766cce4efd2b"
    rpc = FakeRpc({(shib, "totalMinted()"): 1, (shib, "totalSupply()"): 580_000_000_000 * 10**18})
    _, minted = monitor.read_supply(rpc, [shib], 18)
    assert minted == pytest.approx(5.8e11)
    # NFTs / dispensers keep the old order: their counter is the one that matters
    _, minted = monitor.read_supply(rpc, [shib], None)
    assert minted == 1


def test_read_supply_falls_back_to_other_getters_for_bep20():
    c = "0x" + "3" * 40
    _, minted = monitor.read_supply(FakeRpc({(c, "totalMinted()"): 5 * 10**18}), [c], 18)
    assert minted == 5.0


def test_supply_disagreeing_with_market_by_over_100x_is_corrected():
    item = {"contract": "0x78573ab46e5436e126d3ea1fdef8766cce4efd2b", "standard": TOKEN_STD,
            "total_minted": 1.185e-15, "max_supply": 2.37e-15}
    worker._apply_dex_pair(item, {"liquidity": {"usd": 9e5}, "priceUsd": "0.00002", "marketCap": 11_600_000})
    assert item["total_minted"] == pytest.approx(5.8e11)
    assert item["max_supply"] == pytest.approx(5.8e11 * 2)           # cap rescaled by the same factor
    assert item["supply_corrected_from"] == 1.185e-15
    assert item["market_cap"] == 11_600_000


def test_supply_within_100x_of_the_market_is_left_alone():
    # MEET48-style: minted 4.8e9, market-implied 2.19e9
    item = {"contract": "0x1", "standard": TOKEN_STD, "total_minted": 4.8e9, "max_supply": 4.8e9}
    worker._apply_dex_pair(item, {"liquidity": {"usd": 5}, "priceUsd": "0.03361", "marketCap": 73_708_952})
    assert item["total_minted"] == 4.8e9 and "supply_corrected_from" not in item


def test_supply_check_never_touches_nfts():
    item = {"contract": "0x1", "standard": "ERC-721 (NFT Collection)", "total_minted": 120}
    worker._apply_dex_pair(item, {"liquidity": {"usd": 5}, "priceUsd": "0.5", "marketCap": 9_000_000})
    assert item["total_minted"] == 120


def test_migrate_zero_decimal_supply():
    stored = {"standard": "BEP-20 Token (0 decimals)", "total_minted": 1e-12, "max_supply": 2.1e-11}
    assert worker.migrate_zero_decimal_supply(stored) is True
    assert stored["total_minted"] == 1_000_000 and stored["max_supply"] == 21_000_000 and stored["token_decimals"] == 0
    assert worker.migrate_zero_decimal_supply(stored) is False                       # idempotent
    assert worker.migrate_zero_decimal_supply({"standard": TOKEN_STD, "total_minted": 1e-12}) is False
    assert worker.migrate_zero_decimal_supply({"standard": "BEP-20 Token (0 decimals)", "total_minted": 5e6}) is False


def test_stored_token_decimals_win_over_the_standard_string():
    assert worker._token_decimals({"standard": "Fair-Mint Contract (Dispenses $X Token)", "token_decimals": 9}) == 9
    assert worker._token_decimals({"standard": "BEP-20 Token (6 decimals)"}) == 6
    assert worker._token_decimals({"standard": "BEP-20 Token (6 decimals)", "token_decimals": 0}) == 0
    assert worker._token_decimals({"standard": "Fair-Mint Contract (Dispenses $X Token)"}) is None


def test_analyze_contract_records_the_dispensed_tokens_decimals():
    class R(SilentRpc):
        pass
    c = "0x" + "9" * 40
    under = "0x" + "a" * 40
    answers = {(c, "token()"): int(under, 16), (under, "decimals()"): 9}
    class Rpc(FakeRpc):
        def get_code(self, a):
            return "0x"
    det = monitor.analyze_contract(Rpc(answers), c, "0x" + "1" * 40)
    assert det["classification"]["category"] == "TOKEN_FAIR_MINT"
    assert det["token_decimals"] == 9


# ---------------------------------------------------------------------------------------------- M1

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _dex_response(quote_addr, price_native, price_usd):
    tok = "0x" + "4" * 40
    return {"pairs": [{"baseToken": {"address": tok, "symbol": "T"}, "quoteToken": {"address": quote_addr, "symbol": "Q"},
                       "priceNative": price_native, "priceUsd": price_usd, "liquidity": {"usd": 1000},
                       "volume": {"h24": 1}, "txns": {"h24": {"buys": 1, "sells": 0}, "h1": {"buys": 0}},
                       "url": "https://dexscreener.com/bsc/0xpair", "pairAddress": "0xpair"}]}


@pytest.fixture
def live_bnb_600():
    saved = dict(monitor._bnb_price_state)
    monitor._bnb_price_state.update(usd=600.0, live=True, ok_at=time.time(), fetched_at=time.time())
    yield
    monitor._bnb_price_state.clear()
    monitor._bnb_price_state.update(saved)


def _fetch_with(monkeypatch, payload):
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "dexscreener" in url:
            return _Resp(json.dumps(payload).encode())
        raise OSError("offline")
    monkeypatch.setattr(monitor.urllib.request, "urlopen", fake_urlopen)
    return monitor.fetch_token_market_data("0x" + "4" * 40)


def test_usdt_quoted_pair_does_not_store_a_usd_price_as_bnb(live_bnb_600, monkeypatch):
    """JAGENT-style: priceNative on a USDT pair is the USD price."""
    res = _fetch_with(monkeypatch, _dex_response(USDT, "0.06", "0.06"))
    assert res["market_price_bnb"] == pytest.approx(0.06 / 600.0)      # not 0.06


def test_wbnb_quoted_pair_uses_price_native(live_bnb_600, monkeypatch):
    res = _fetch_with(monkeypatch, _dex_response(WBNB, "0.0001", "0.06"))
    assert res["market_price_bnb"] == 0.0001


def test_non_wbnb_pair_without_live_bnb_price_leaves_bnb_price_unknown(monkeypatch):
    saved = dict(monitor._bnb_price_state)
    monitor._bnb_price_state.update(live=False, ok_at=0.0)
    try:
        res = _fetch_with(monkeypatch, _dex_response(USDT, "0.06", "0.06"))
    finally:
        monitor._bnb_price_state.clear()
        monitor._bnb_price_state.update(saved)
    assert res["market_price_bnb"] == 0.0


def test_worker_price_bnb_follows_the_same_rule(live_bnb_600):
    usdt_pair = {"liquidity": {"usd": 5}, "priceUsd": "0.06", "priceNative": "0.06",
                 "quoteToken": {"address": USDT}}
    item = {}
    worker._apply_dex_pair(item, usdt_pair)
    assert item["token_price_bnb"] == pytest.approx(0.06 / 600.0)
    wbnb_pair = {"liquidity": {"usd": 5}, "priceUsd": "0.06", "priceNative": "0.0001", "quoteToken": {"address": WBNB}}
    worker._apply_dex_pair(item, wbnb_pair)
    assert item["token_price_bnb"] == 0.0001
    # the ratio the audit measured (token_price_bnb x BNB / token_price_usd) is 1 for every quote token
    assert item["token_price_bnb"] * 600.0 / item["token_price_usd"] == pytest.approx(1.0, rel=0.02)


# ---------------------------------------------------------------------------------------- Element NFT data

# A real ERC721SellOrderFilled log from BSC (block 133161775): 14 data words, `fees` is a dynamic array.
REAL_ELEMENT_LOG = {
    "address": monitor.ELEMENT_EX, "topics": [monitor.T_721_FILLED], "blockNumber": hex(133161775),
    "transactionHash": "0xfdd777916856425d095af17297f941add97bd7b3e5cc6aeab2b5019cebaf9454",
    "data": "0x" + "".join([
        "9f6bdf2c83fd7cf9bf9bf0b2a8d046df7fbfa26d4c5d7f5fb9add3e771513084",
        "0000000000000000000000008389a3e684d532a3b0b4cdbb72c9c7797e6fa4f7",
        "00000000000000000000000078c1aa2b540c6f08cbd349519ebc8eb36a30b2c0",
        "0000000000000000000000000000000000000000000000000000000000004ad6",
        "000000000000000000000000eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "00000000000000000000000000000000000000000000000000237dda214e6000",
        "0000000000000000000000000000000000000000000000000000000000000120",
        "00000000000000000000000043bde1047a25274c377800da13eaaa506f29618b",
        "00000000000000000000000000000000000000000000000000000000000000bc",
        "0000000000000000000000000000000000000000000000000000000000000002",
        "00000000000000000000000067c0a1fc3ec90aaa393adaea9cbe7230df3ec7ad",
        "0000000000000000000000000000000000000000000000000000b5b79006b000",
        "000000000000000000000000c15ced14eb0c17229d3e277d098a182360a46ec9",
        "0000000000000000000000000000000000000000000000000001c64ae810b800",
    ]),
}


def test_element_token_id_is_word_8_not_the_fees_offset():
    parsed = monitor.parse_element_trade_log(REAL_ELEMENT_LOG)
    assert parsed["token_id"] == 188                      # was 288, the offset of the fees array
    assert parsed["collection"] == "0x43bde1047a25274c377800da13eaaa506f29618b"
    assert parsed["price_bnb"] == pytest.approx(0.00999) and parsed["is_bnb"] is True


@pytest.fixture(autouse=True)
def _no_element_retry_wait(monkeypatch):
    monkeypatch.setattr(monitor, "ELEMENT_LOG_RETRY_SEC", 0.0)


class ElementRpc:
    def __init__(self, logs_by_chunk, fail_chunk=None):
        self.logs_by_chunk, self.fail_chunk = logs_by_chunk, fail_chunk
        self.calls = 0

    def get_block_number(self):
        return 20000

    def get_logs(self, params):
        if params["address"] == monitor.ELEMENT_EX:
            i = self.calls
            self.calls += 1
            if self.fail_chunk is not None and i >= self.fail_chunk:     # stays down through the retries
                raise OSError("rpc down")
            return self.logs_by_chunk[i] if i < len(self.logs_by_chunk) else []
        return []

    def call_contract(self, addr, data):
        return None


def _fill(collection, price_wei, pay="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"):
    log = dict(REAL_ELEMENT_LOG)
    words = [REAL_ELEMENT_LOG["data"][2 + 64 * i: 2 + 64 * (i + 1)] for i in range(14)]
    words[4] = pay.rjust(64, "0")
    words[5] = f"{price_wei:064x}"
    words[7] = collection[2:].rjust(64, "0")
    log["data"] = "0x" + "".join(words)
    return log


COL = "0x43bde1047a25274c377800da13eaaa506f29618b"


def _nft(**kw):
    d = {"contract": COL, "standard": "ERC-721 (NFT Collection)", "name": "N"}
    d.update(kw)
    return d


def test_element_skips_fills_paid_in_other_tokens():
    usdt_fill = _fill(COL, 5 * 10**18, pay=USDT[2:])
    bnb_fill = _fill(COL, 2 * 10**16)
    item = _nft()
    monitor.scan_element_nft_volume(ElementRpc([[usdt_fill, bnb_fill]]), [item], lookback_blocks=5000)
    assert item["secondary_sales_count"] == 1
    assert item["floor_price_bnb"] == pytest.approx(0.02)       # not dragged to 0 by the USDT fill


def test_element_window_without_fills_keeps_the_previous_floor():
    item = _nft(floor_price_bnb=0.3, high_price_bnb=0.5, floor_price_str="0.300 BNB", secondary_sales_count=4,
                secondary_vol_bnb=1.2)
    monitor.scan_element_nft_volume(ElementRpc([[]]), [item], lookback_blocks=5000)
    assert item["floor_price_bnb"] == 0.3 and item["high_price_bnb"] == 0.5
    assert item["floor_price_str"] == "0.300 BNB"
    assert item["secondary_sales_count"] == 0 and item["secondary_vol_bnb"] == 0.0   # this window really had none


def test_element_failed_log_chunk_writes_no_partial_totals():
    item = _nft(secondary_sales_count=4, secondary_vol_bnb=1.2, floor_price_bnb=0.3)
    ok_chunk = [_fill(COL, 2 * 10**16)]
    # lookback 15000 from head 20000 -> two 10k chunks; the second one fails
    res = monitor.scan_element_nft_volume(ElementRpc([ok_chunk], fail_chunk=1), [item], lookback_blocks=15000)
    assert res == {}
    assert item["secondary_sales_count"] == 4 and item["secondary_vol_bnb"] == 1.2 and item["floor_price_bnb"] == 0.3


def test_element_counts_accepted_offers_and_swaps_maker_and_taker():
    sell = _fill(COL, 2 * 10**16)
    buy = dict(_fill(COL, 3 * 10**16, pay=monitor.WBNB_ADDRESS[2:].lower()), topics=[monitor.T_721_BUY_FILLED])
    item = _nft()
    monitor.scan_element_nft_volume(ElementRpc([[sell, buy]]), [item], lookback_blocks=5000)
    assert item["secondary_sales_count"] == 2
    assert item["secondary_vol_bnb"] == pytest.approx(0.05)
    s, b = monitor.parse_element_trade_log(sell), monitor.parse_element_trade_log(buy)
    assert (b["seller"], b["buyer"]) == (s["buyer"], s["seller"])     # the maker of a buy order is the buyer


def test_get_logs_strict_raises_when_no_endpoint_answers(monkeypatch):
    rpc = monitor.BscRpcClient(["http://unused.invalid"])
    monkeypatch.setattr(rpc, "call", lambda method, params: None)
    assert rpc.get_logs({}) == []                                     # indistinguishable from "no logs"
    with pytest.raises(RuntimeError):
        rpc.get_logs_strict({})
    monkeypatch.setattr(rpc, "call", lambda method, params: [])
    assert rpc.get_logs_strict({}) == []


def test_element_scan_treats_an_unanswered_log_query_as_a_failure_not_as_no_sales():
    class DownRpc(ElementRpc):
        def get_logs_strict(self, params):
            raise RuntimeError("eth_getLogs failed on every RPC endpoint")
    item = _nft(secondary_sales_count=4, secondary_vol_bnb=1.2, floor_price_bnb=0.3, is_nft_surge=True)
    assert monitor.scan_element_nft_volume(DownRpc([[]]), [item], lookback_blocks=5000) == {}
    assert item["secondary_sales_count"] == 4 and item["secondary_vol_bnb"] == 1.2 and item["is_nft_surge"] is True


class ClockRpc(ElementRpc):
    """Blocks `spb` seconds apart; records every Element log query."""
    def __init__(self, head, spb, logs=()):
        super().__init__([])
        self.head, self.spb, self.logs, self.queries = head, spb, list(logs), []

    def get_block_number(self):
        return self.head

    def get_block(self, n, full_tx=True):
        return {"timestamp": hex(int(1_700_000_000 + n * self.spb))}

    def get_logs(self, params):
        if params["address"] != monitor.ELEMENT_EX:
            return []
        lo, hi = int(params["fromBlock"], 16), int(params["toBlock"], 16)
        self.queries.append((lo, hi))
        return [l for l in self.logs if lo <= int(l["blockNumber"], 16) <= hi]


def _fill_at(block, price_wei):
    return dict(_fill(COL, price_wei), blockNumber=hex(block))


def test_element_window_is_24_hours_whatever_the_block_time():
    assert monitor.element_lookback_blocks(ClockRpc(1_000_000, 0.45), 1_000_000) == 192_000
    assert monitor.element_lookback_blocks(ClockRpc(1_000_000, 3.0), 1_000_000) == 28_800
    # block time unavailable (ElementRpc has no get_block): the fallback pace, not a crash
    assert monitor.element_lookback_blocks(ElementRpc([]), 1_000_000) == int(24 * 3600 / monitor.BSC_BLOCK_TIME_FALLBACK_SEC)

    head = 1_000_000
    rpc = ClockRpc(head, 0.45, [_fill_at(head - 100_000, 10**16), _fill_at(head - 250_000, 10**16)])
    item = _nft()
    monitor.scan_element_nft_volume(rpc, [item])
    assert item["secondary_sales_count"] == 1                          # ~12.5h old: inside; ~31h old: outside
    assert rpc.queries[0][0] == head - 192_000 and rpc.queries[-1][1] == head


def test_element_fill_cache_reads_only_new_blocks_and_ages_fills_out():
    head = 1_000_000
    rpc = ClockRpc(head, 0.45, [_fill_at(head - 150_000, 10**16), _fill_at(head - 10, 2 * 10**16)])
    cache, item = {}, _nft()
    monitor.scan_element_nft_volume(rpc, [item], fill_cache=cache)
    assert item["secondary_sales_count"] == 2 and len(rpc.queries) == 20

    rpc.queries.clear()
    rpc.head = head + 130                                              # about a minute later
    rpc.logs.append(_fill_at(head + 100, 3 * 10**16))
    monitor.scan_element_nft_volume(rpc, [item], fill_cache=cache)
    assert rpc.queries == [(head - monitor.ELEMENT_REORG_BLOCKS + 1, head + 130)]   # only the unsettled tail + new blocks
    assert item["secondary_sales_count"] == 3                          # nothing counted twice
    assert item["secondary_vol_bnb"] == pytest.approx(0.06)

    rpc.head = head + 50_000                                           # the oldest fill is now over 24h old
    monitor.scan_element_nft_volume(rpc, [item], fill_cache=cache)
    assert item["secondary_sales_count"] == 2 and item["floor_price_bnb"] == pytest.approx(0.02)

    # a failed read leaves the item alone and the cache no further along than it was
    read_to, cached = cache["to_block"], list(cache["fills"])
    rpc.head += 500
    rpc.get_logs = lambda params: (_ for _ in ()).throw(OSError("rpc down"))
    assert monitor.scan_element_nft_volume(rpc, [item], fill_cache=cache) == {}
    assert item["secondary_sales_count"] == 2
    assert cache["to_block"] == read_to and cache["fills"] == cached


def test_element_log_chunk_is_retried_and_progress_survives_a_failed_scan():
    head = 1_000_000

    class FlakyRpc(ClockRpc):
        fail_from = None            # (lo block, how many more times that chunk fails)

        def get_logs(self, params):
            if params["address"] == monitor.ELEMENT_EX and self.fail_from:
                lo, left = self.fail_from
                if int(params["fromBlock"], 16) == lo and left > 0:
                    self.fail_from = (lo, left - 1)
                    self.queries.append("fail")
                    raise OSError("timeout")
            return super().get_logs(params)

    first_chunk = head - 192_000
    rpc = FlakyRpc(head, 0.45, [_fill_at(head - 190_000, 10**16), _fill_at(head - 50, 2 * 10**16)])
    item = _nft()

    # one timeout on a chunk: retried, the scan succeeds
    rpc.fail_from = (first_chunk + 10_000, 1)
    monitor.scan_element_nft_volume(rpc, [item], fill_cache={})
    assert item["secondary_sales_count"] == 2 and rpc.queries.count("fail") == 1

    # the chunk keeps failing: nothing written, but the chunk before it is remembered
    cache, item = {}, _nft(secondary_sales_count=7)
    rpc.queries.clear()
    rpc.fail_from = (first_chunk + 10_000, monitor.ELEMENT_LOG_ATTEMPTS)
    assert monitor.scan_element_nft_volume(rpc, [item], fill_cache=cache) == {}
    assert item["secondary_sales_count"] == 7
    assert cache["to_block"] == first_chunk + 9_999 and len(cache["fills"]) == 1

    # next sweep resumes after the remembered chunk and completes
    rpc.queries.clear()
    monitor.scan_element_nft_volume(rpc, [item], fill_cache=cache)
    assert rpc.queries[0][0] == first_chunk + 10_000
    assert item["secondary_sales_count"] == 2
