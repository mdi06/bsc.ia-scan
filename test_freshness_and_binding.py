"""Regression tests for the metrics audit, Prompt 1: market-data freshness (H1), pair binding (H2),
Pancake liquidity (M2) and BNB price liveness (M8)."""
import time

import pytest
from fastapi.testclient import TestClient

import api
import monitor
import worker

WBNB = monitor.WBNB_ADDRESS.lower()
HOUR = 3600.0

AVESAKE = "0x105d904d64520d9bcefb66af936c2cce59037777"
MARSID = "0xcbac4fac33afb361e836e6a0120f5a5aac9122b3"
MARS_TOKEN = "0xfe189e97" + "0" * 28 + "7777"   # base token of the MarsCoin/SPCXB pair (shape only)
MARS_PAIR = "0x94f3ed36706c746ad59fadcaf271b7431ab1d8f1"


def _stale_avesake(age_h):
    return {
        "contract": AVESAKE, "standard": "BEP-20 Token (18 decimals)", "name": "AVESAKE", "symbol": "AVESAKE",
        "pair_url": "https://dexscreener.com/bsc/0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "lp_usd": 8776.51, "volume_24h": 1200.0, "token_price_usd": 1.2e-6, "market_cap": 52000.0, "fdv": 52000.0,
        "price_change_24h": 12.5, "buys_24h": 40, "sells_24h": 12, "buys_1h": 3, "lp_wbnb": 12.0,
        "total_minted": 1_000_000, "score": 60,
        "market_updated_at": time.time() - age_h * HOUR,
    }


# ----------------------------------------------------------------------------------------------- H1

def test_expire_clears_fields_to_none_not_zero():
    item = _stale_avesake(age_h=7)
    assert worker._expire_stale_market(item, time.time(), max_age=6 * HOUR) is True
    assert item["lp_usd"] is None
    assert item["market_stale"] is True
    for f in worker.STALE_CLEARED_FIELDS:
        assert item[f] is None, f


def test_fresh_record_is_left_alone():
    item = _stale_avesake(age_h=1)
    assert worker._expire_stale_market(item, time.time(), max_age=6 * HOUR) is False
    assert item["lp_usd"] == 8776.51
    assert not item.get("market_stale")


def test_legacy_record_without_timestamp_counts_as_stale():
    item = _stale_avesake(age_h=0)
    del item["market_updated_at"]
    assert worker._expire_stale_market(item, time.time()) is True
    assert item["lp_usd"] is None


def test_record_without_market_data_is_not_flagged():
    item = {"contract": "0x" + "1" * 40, "lp_usd": 0, "token_price_usd": None}
    assert worker._expire_stale_market(item, time.time()) is False
    assert "market_stale" not in item


def test_sweep_with_empty_dexscreener_answer_ends_with_lp_none(monkeypatch):
    """AVESAKE: DexScreener answers {"pairs": null}; after the age limit the record is cleared, rescored, saved."""
    item = _stale_avesake(age_h=7)
    history = [item]
    saved = []
    monkeypatch.setattr(worker, "_fetch_dexscreener_pairs",
                        lambda url: (lambda d: d.get("pairs") or [])({"pairs": None}))
    monkeypatch.setattr(worker.monitor, "save_history", lambda h, delta=None: saved.append(list(delta)))

    dex_updates = {}
    worker._collect_dex_pairs(worker._fetch_dexscreener_pairs("x"), {AVESAKE}, dex_updates)
    changed, pancake, no_pair, n = worker._apply_market_sweep(history, dex_updates, time.time())
    assert n == 0 and no_pair == [item]
    assert worker._expire_stale_market(item, time.time(), max_age=6 * HOUR)
    worker._recompute_interest(item)

    assert item["lp_usd"] is None
    assert item["market_stale"] is True
    assert item["score"] == 0  # rescored from the cleared fields


def test_returning_record_is_marked_fresh_again():
    item = _stale_avesake(age_h=7)
    worker._expire_stale_market(item, time.time(), max_age=6 * HOUR)
    pair = {"baseToken": {"address": AVESAKE, "symbol": "AVESAKE"}, "liquidity": {"usd": 5000},
            "priceUsd": "0.000002", "volume": {"h24": 10}, "marketCap": 9000, "fdv": 9000,
            "txns": {"h24": {"buys": 4, "sells": 1}, "h1": {"buys": 1}}, "url": "https://dexscreener.com/bsc/0xbb"}
    before = time.time()
    worker._apply_dex_pair(item, pair)
    assert item["lp_usd"] == 5000.0
    assert item["market_stale"] is False
    assert item["market_updated_at"] >= before
    assert item["market_source"] == "dexscreener"


def test_rescored_record_without_pair_is_saved():
    """_recompute_interest changes were not saved for records without a DEX pair (not in changed_items)."""
    item = {"contract": "0x" + "2" * 40, "standard": "ERC-721 (NFT Collection)", "score": 90, "rating": "🔥 SURGING",
            "lp_usd": 0, "volume_24h": 0, "buys_24h": 0}
    changed, _, no_pair, _ = worker._apply_market_sweep([item], {}, time.time())
    assert item["score"] < 90
    assert changed == [item] and no_pair == [item]


def test_unchanged_record_without_pair_is_not_resaved():
    item = {"contract": "0x" + "3" * 40, "standard": "ERC-721 (NFT Collection)", "lp_usd": 0}
    worker._recompute_interest(item)
    changed, _, _, _ = worker._apply_market_sweep([item], {}, time.time())
    assert changed == []


def test_cleared_record_is_a_valid_input_everywhere():
    """None market fields must not crash the scoring / sniper / stats code."""
    item = _stale_avesake(age_h=7)
    item.update(detected_at="2026-11-12 10:00:00", mint_price_str="FREE", has_free_mint=True)
    worker._expire_stale_market(item, time.time(), max_age=6 * HOUR)
    worker._recompute_interest(item)
    monitor.compute_market_cap_stats([item], 700.0)
    monitor.update_post_mint_profit_metrics(dict(item), 700.0)
    monitor.format_lp_or_volume(item, 700.0)
    monitor.format_floor_trade_price(item, 700.0)


# ----------------------------------------------------------------------------------------------- M2

class FakeRpc:
    def call_contract(self, addr, data):
        return None


def _pancake_item(contract):
    return {"contract": contract, "standard": "BEP-20 Token (18 decimals)", "total_minted": 1, "lp_usd": 0}


@pytest.mark.parametrize("token_below_wbnb", [True, False])
def test_pancake_estimate_is_two_sided_and_prices_the_token(monkeypatch, token_below_wbnb):
    token = "0x" + ("11" if token_below_wbnb else "ff") * 20
    assert (token < WBNB) == token_below_wbnb
    tok_raw, wbnb_raw = 1_000_000 * 10**18, 50 * 10**18
    reserves = (tok_raw, wbnb_raw) if token_below_wbnb else (wbnb_raw, tok_raw)
    monkeypatch.setattr(worker.monitor, "get_pair_address", lambda rpc, f, a, b: "0x" + "9" * 40)
    monkeypatch.setattr(worker.monitor, "get_reserves", lambda rpc, pair: reserves)
    monkeypatch.setattr(worker.monitor, "refresh_bnb_price", lambda rpc=None, **k: 700.0)

    item = _pancake_item(token)
    before = time.time()
    assert worker._apply_pancake_estimate(FakeRpc(), item) is True
    assert item["lp_usd"] == pytest.approx(70000.0)
    assert item["lp_wbnb"] == pytest.approx(100.0)
    assert item["token_price_usd"] == pytest.approx(0.035)
    assert item["token_price_bnb"] == pytest.approx(50 / 1_000_000)
    assert item["market_updated_at"] >= before and item["market_stale"] is False
    assert item["market_source"] == "pancake"


def test_pancake_estimate_uses_token_decimals(monkeypatch):
    token = "0x" + "11" * 20
    monkeypatch.setattr(worker.monitor, "get_pair_address", lambda *a: "0x" + "9" * 40)
    monkeypatch.setattr(worker.monitor, "get_reserves", lambda rpc, pair: (1_000_000 * 10**9, 50 * 10**18))
    monkeypatch.setattr(worker.monitor, "refresh_bnb_price", lambda rpc=None, **k: 700.0)
    item = _pancake_item(token)
    item["standard"] = "BEP-20 Token (9 decimals)"
    worker._apply_pancake_estimate(FakeRpc(), item)
    assert item["token_price_usd"] == pytest.approx(0.035)


def test_pancake_sourced_records_are_rechecked_even_with_liquidity():
    item = _pancake_item("0x" + "11" * 20)
    item.update(lp_usd=35000.0, pair_url="https://pancakeswap.finance/info/v2/pairs/0x" + "9" * 40)
    assert worker._needs_pancake_fallback(item) is True          # legacy record, recognised by its URL
    item["pair_url"] = "https://dexscreener.com/bsc/0x" + "9" * 40
    assert worker._needs_pancake_fallback(item) is False         # DexScreener now knows it
    item["market_source"] = "pancake"
    assert worker._needs_pancake_fallback(item) is True


def test_dexscreener_listed_record_does_not_need_pancake():
    item = _pancake_item("0x" + "11" * 20)
    item.update(lp_usd=35000.0, pair_url="https://dexscreener.com/bsc/0x" + "9" * 40, market_source="dexscreener")
    assert worker._needs_pancake_fallback(item) is False


def test_dex_pair_replaces_pancake_estimate_when_listed():
    c = "0x" + "11" * 20
    item = _pancake_item(c)
    item.update(lp_usd=35000.0, market_source="pancake",
                pair_url="https://pancakeswap.finance/info/v2/pairs/0x" + "9" * 40)
    pair = {"baseToken": {"address": c, "symbol": "T"}, "liquidity": {"usd": 70000}, "priceUsd": "0.035",
            "url": "https://dexscreener.com/bsc/0x" + "8" * 40, "txns": {}}
    changed, pancake, _, n = worker._apply_market_sweep([item], {c: pair}, time.time())
    assert n == 1 and item["market_source"] == "dexscreener" and item["lp_usd"] == 70000.0
    assert pancake == []


# ----------------------------------------------------------------------------------------------- H2

class LinkedRpc:
    """An NFT whose token() getter points at another contract; every other call reverts."""
    def call_contract(self, addr, data):
        if addr.lower() == MARSID and data == "0xfc0c546a":
            return "0x" + MARS_TOKEN[2:].zfill(64)
        return None


def _mars_market(token_addr):
    return {
        "holders": 5000, "buys_24h": 800, "buys_1h": 30, "sells_24h": 700, "volume_24h": 90000.0,
        "market_cap": 45_200_000.0, "fdv": 45_200_000.0, "price_change_24h": 3.0, "pair_url":
        f"https://dexscreener.com/bsc/{MARS_PAIR}", "market_price_usd": 0.045, "market_price_bnb": 0.00006,
        "lp_usd": 1_020_000.0, "pair_address": MARS_PAIR, "token_symbol": "MARS",
        "security": {}, "risk_level": "SAFE", "risk_badge": "🟢 Clean", "risk_flags": [],
    }


def _nft_details():
    return {"classification": {"category": "NFT", "is_nft": True, "standard": "ERC-721 (NFT Collection)"},
            "name": "MARSID", "symbol": "MARSID", "total_minted": 120, "max_supply": 1000,
            "mint_price_bnb": 0.0, "mint_value_wei": "0", "decimals": 0}


def test_nft_does_not_inherit_linked_tokens_pair(monkeypatch):
    monkeypatch.setattr(monitor, "fetch_token_market_data",
                        lambda t: _mars_market(t) if t.lower() == MARS_TOKEN else {})
    monkeypatch.setattr(monitor, "get_bnb_price_usd", lambda rpc: 700.0)
    stats = monitor.analyze_market_interest(LinkedRpc(), MARSID, None, _nft_details())

    assert stats["linked_token"] == MARS_TOKEN
    assert not stats["lp_usd"] and not stats["market_cap"] and not stats["token_price_usd"]
    assert not stats["pair_url"] and not stats["fdv"] and not stats["volume_24h"]
    # the linked token's numbers are kept, clearly separated
    assert stats["linked_lp_usd"] == 1_020_000.0
    assert stats["linked_market_cap"] == 45_200_000.0
    assert stats["linked_pair_url"].endswith(MARS_PAIR)
    assert monitor.linked_market_fields(stats)["linked_token"] == MARS_TOKEN


def test_pancake_pool_of_linked_token_stays_off_the_record(monkeypatch):
    """The multi-quote Pancake block must not write lp_usd / pair_url / lp_pool of the linked token either."""
    pool = "0x" + "7" * 40
    class PoolRpc(LinkedRpc):
        def call_contract(self, addr, data):
            if addr.lower() == monitor.PANCAKE_FACTORY_V2.lower() and data.startswith("0xe6a43905"):
                return "0x" + pool[2:].zfill(64)
            if addr.lower() == pool and data == "0x0902f1ac":
                return "0x" + hex(10**24)[2:].zfill(64) + hex(50 * 10**18)[2:].zfill(64)
            if addr.lower() == pool and data == "0x0dfe1681":
                return "0x" + MARS_TOKEN[2:].zfill(64)
            return super().call_contract(addr, data)
    monkeypatch.setattr(monitor, "fetch_token_market_data", lambda t: {})
    monkeypatch.setattr(monitor, "get_bnb_price_usd", lambda rpc: 700.0)
    stats = monitor.analyze_market_interest(PoolRpc(), MARSID, None, _nft_details())
    assert stats["linked_token"] == MARS_TOKEN
    assert not stats["lp_usd"] and not stats["lp_pool"] and not stats["pair_url"]
    assert stats["linked_lp_usd"] > 0 and stats["linked_lp_pool"] == pool


def test_token_with_own_pair_still_gets_its_market_data(monkeypatch):
    class TokenRpc:
        def call_contract(self, addr, data):
            return None
    monkeypatch.setattr(monitor, "fetch_token_market_data", lambda t: _mars_market(t))
    monkeypatch.setattr(monitor, "get_bnb_price_usd", lambda rpc: 700.0)
    det = {"classification": {"category": "TOKEN", "standard": "BEP-20 Token (18 decimals)"},
           "name": "T", "symbol": "T", "total_minted": 1e9, "max_supply": 1e9, "decimals": 18}
    stats = monitor.analyze_market_interest(TokenRpc(), MARS_TOKEN, None, det)
    assert "linked_token" not in stats
    assert stats["lp_usd"] == 1_020_000.0 and stats["market_cap"] == 45_200_000.0


def _pair(addr, base):
    return {"pairAddress": addr, "baseToken": {"address": base}}


def test_cleanup_finds_only_records_bound_to_a_foreign_pair():
    nft = {"contract": MARSID, "pair_url": f"https://dexscreener.com/bsc/{MARS_PAIR}",
           "lp_usd": 1_020_000.0, "market_cap": 45_200_000.0, "token_price_usd": 0.04}
    own_pair = "0x" + "5" * 40
    good = {"contract": AVESAKE, "pair_url": f"https://dexscreener.com/bsc/{own_pair}", "lp_usd": 100.0}
    unknown = {"contract": "0x" + "6" * 40, "pair_url": "https://dexscreener.com/bsc/0x" + "4" * 40, "lp_usd": 5.0}
    pancake = {"contract": "0x" + "3" * 40, "pair_url": "https://pancakeswap.finance/info/v2/pairs/0x" + "2" * 40}
    returned = [_pair(MARS_PAIR, MARS_TOKEN), _pair(own_pair, AVESAKE)]
    bad = worker.find_foreign_pair_records([nft, good, unknown, pancake], fetch_pairs=lambda url: returned)
    assert bad == [nft]

    worker.clear_foreign_pair_market(nft)
    assert nft["lp_usd"] is None and nft["market_cap"] is None and nft["token_price_usd"] is None
    assert nft["pair_url"] == "" and nft["market_stale"] is True


# ----------------------------------------------------------------------------------------------- M8

@pytest.fixture
def price_state():
    saved = dict(monitor._bnb_price_state)
    monitor._bnb_price_state.update(usd=monitor.BNB_PRICE_FALLBACK_USD, fetched_at=0.0, live=False, ok_at=0.0)
    yield monitor._bnb_price_state
    monitor._bnb_price_state.clear()
    monitor._bnb_price_state.update(saved)


def test_price_is_live_only_while_last_success_is_recent(monkeypatch, price_state):
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: 650.0)
    monitor.refresh_bnb_price(object())
    assert monitor.bnb_price_is_live() is True
    assert monitor.bnb_price_as_of() == price_state["ok_at"]

    # RPC outage: every later lookup fails, the old price is kept but ages out of "live"
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: None)
    ttl = monitor.BNB_PRICE_TTL_SEC
    price_state.update(ok_at=time.time() - 2 * ttl, fetched_at=0.0)
    assert monitor.refresh_bnb_price(object()) == 650.0
    assert monitor.bnb_price_is_live() is True                    # 2 TTLs old: still within 3
    price_state.update(ok_at=time.time() - 4 * ttl, fetched_at=0.0)
    assert monitor.refresh_bnb_price(object()) == 650.0
    assert monitor.bnb_price_is_live() is False                   # a failed refresh does not renew it
    assert price_state["ok_at"] < time.time() - 3 * ttl


def test_failed_fetch_never_renews_ok_at(monkeypatch, price_state):
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: None)
    monitor.refresh_bnb_price(object())
    assert monitor.bnb_price_is_live() is False and monitor.bnb_price_as_of() is None


def test_bnb_price_endpoint_reports_live_and_as_of(monkeypatch, price_state):
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: 688.0)
    body = TestClient(api.app).get("/api/bnb-price").json()
    assert body["live"] is True and body["bnb_price_usd"] == 688.0 and body["as_of"] == price_state["ok_at"]

    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: None)
    price_state.update(ok_at=time.time() - 10 * monitor.BNB_PRICE_TTL_SEC, fetched_at=0.0)
    body = TestClient(api.app).get("/api/bnb-price").json()
    assert body["live"] is False and body["bnb_price_usd"] == 688.0


def test_stale_recheck_slice_rotates_and_is_bounded():
    items = [{"contract": f"0x{i:040x}"} for i in range(150)]
    worker._stale_recheck_offset = 0
    seen = []
    for _ in range(3):
        chunk = worker._next_stale_slice(items)
        assert len(chunk) == worker.STALE_RECHECK_PER_SWEEP
        seen += [c["contract"] for c in chunk]
    assert len(set(seen)) == 150          # 3 sweeps of 60 wrap around and cover every record
    assert worker._next_stale_slice([]) == []
    assert len(worker._next_stale_slice(items[:5])) == 5
