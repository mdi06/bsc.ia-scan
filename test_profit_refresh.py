"""H8: the served profit / ATH fields are refreshed by the worker whenever market data changes."""
import time

import pytest

import monitor
import worker

BNB = 705.0


@pytest.fixture(autouse=True)
def fixed_bnb(monkeypatch):
    monkeypatch.setattr(monitor, "cached_bnb_price", lambda: BNB)


def pair(price, lp, base="0x" + "9" * 40):
    return {"baseToken": {"address": base, "symbol": "T"}, "liquidity": {"usd": lp}, "priceUsd": str(price),
            "url": "https://dexscreener.com/bsc/0xpair", "txns": {}}


def paid_token(**kw):
    d = {"contract": "0x" + "9" * 40, "name": "T", "symbol": "TST", "standard": "BEP-20 Token (18 decimals)",
         "mint_price_bnb": 0.01, "mint_value_wei": "10000000000000000"}
    d.update(kw)
    return d


def test_dex_update_refreshes_profit_and_ath_and_ath_only_goes_up():
    item = paid_token()
    worker._apply_dex_pair(item, pair(20.0, 50000))
    assert item["current_profit_usd"] == pytest.approx(20.0 - 7.05)
    first_ath = item["ath_profit_usd"]
    worker._apply_dex_pair(item, pair(40.0, 50000))
    assert item["ath_profit_usd"] == pytest.approx(40.0 - 7.05) and item["ath_price_usd"] == 40.0
    worker._apply_dex_pair(item, pair(9.0, 50000))
    assert item["ath_profit_usd"] == pytest.approx(40.0 - 7.05)          # unchanged by the drop
    assert item["current_profit_usd"] == pytest.approx(9.0 - 7.05)
    assert item["ath_profit_usd"] > first_ath


def test_stale_poisoned_ath_from_a_thin_pool_is_dropped_on_the_next_update():
    """珍珠币: served 0.002061 which is 347x its live price, on a $1 pool."""
    item = {"contract": "0x7e2da878217eb995f9e5d36ed32c9cea52442681", "name": "珍珠币", "symbol": "珍珠币",
            "standard": "BEP-20 Token (8 decimals)", "mint_price_str": "DEX Traded", "token_price_usd": 0.002061,
            "ath_price_usd": 0.002061, "ath_profit_usd": 0.002061, "current_profit_usd": 0.002061,
            "secondary_price_usd": 0.002061}
    worker._apply_dex_pair(item, pair(1.153e-05, 1.26, base=item["contract"]))
    assert item["ath_price_usd"] is None and item["ath_profit_usd"] is None
    assert item["current_profit_usd"] is None                           # DEX Traded: no mint position
    assert item["secondary_price_usd"] == 1.153e-05


def test_cleared_stale_market_leaves_no_served_profit():
    item = paid_token(token_price_usd=20.0, lp_usd=50000.0, market_updated_at=time.time() - 8 * 3600)
    worker._refresh_profit_fields(item)
    assert item["current_profit_usd"] is not None
    assert worker._expire_stale_market(item, time.time())
    worker._refresh_profit_fields(item)
    assert item["current_profit_usd"] is None and item["secondary_price_usd"] is None
    assert item["ath_price_usd"] == 20.0                                # a price fact already learned is kept


def test_pancake_estimate_refreshes_profit(monkeypatch):
    token = "0x" + "1" * 40
    monkeypatch.setattr(worker.monitor, "get_pair_address", lambda *a: "0x" + "9" * 40)
    monkeypatch.setattr(worker.monitor, "get_reserves", lambda rpc, p: (1_000_000 * 10**18, 50 * 10**18))
    monkeypatch.setattr(worker.monitor, "refresh_bnb_price", lambda rpc=None, **k: BNB)
    item = {"contract": token, "standard": "BEP-20 Token (18 decimals)", "has_free_mint": True}
    worker._apply_pancake_estimate(object(), item)
    assert item["current_profit_usd"] == pytest.approx(0.035 * (BNB / 700.0))   # free mint: profit = price


def test_element_floor_change_refreshes_nft_profit(monkeypatch):
    a = "0x" + "a" * 40
    nft = {"contract": a, "standard": "ERC-721 (NFT Collection)", "name": "N", "symbol": "N", "has_free_mint": True}

    def fake_scan(rpc, items, **kw):
        items[0].update(secondary_sales_count=2, secondary_vol_bnb=0.2, floor_price_bnb=0.1, high_price_bnb=0.1)
        return {}
    monkeypatch.setattr(monitor, "scan_element_nft_volume", fake_scan)
    worker._sweep_element_nft_once(None, [nft])
    assert nft["current_profit_usd"] == pytest.approx(0.1 * BNB)
    assert nft["ath_price_usd"] == pytest.approx(0.1 * BNB)
