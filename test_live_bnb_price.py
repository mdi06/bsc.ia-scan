"""USD figures that get stored or served are never built on the placeholder BNB/USD price.

monitor.BNB_PRICE_FALLBACK_USD was $705 when BNB was $553: a profit / ATH computed with it is 27% off and the worker keeps
the ATH as a running maximum. Without a live price those figures are left out and filled in on a later sweep.
"""
import pytest

import monitor
import worker


def set_price(live: bool, usd: float = 553.0):
    monitor._bnb_price_state.update(usd=usd, live=live, ok_at=4102444800.0 if live else 0.0, fetched_at=4102444800.0)


def paid_token(**kw):
    d = {"contract": "0x" + "9" * 40, "name": "T", "symbol": "TST", "standard": "BEP-20 Token (18 decimals)",
         "mint_price_bnb": 0.01, "mint_value_wei": "10000000000000000", "tokens_per_mint": 1,
         "token_price_usd": 20.0, "lp_usd": 50000.0}
    d.update(kw)
    return d


def test_live_bnb_price_is_none_without_a_live_lookup():
    set_price(live=False, usd=monitor.BNB_PRICE_FALLBACK_USD)
    assert monitor.live_bnb_price() is None
    assert monitor.cached_bnb_price() == monitor.BNB_PRICE_FALLBACK_USD      # the placeholder is still there for display paths
    set_price(live=True, usd=553.0)
    assert monitor.live_bnb_price() == 553.0


def test_no_profit_and_no_ath_are_stored_without_a_live_price():
    set_price(live=False, usd=monitor.BNB_PRICE_FALLBACK_USD)
    item = paid_token()
    before = dict(item)
    info = monitor.update_post_mint_profit_metrics(item)
    assert info["mint_usd"] is None and info["profit"] is None
    assert item == before, "nothing is written to the record: no ATH built on the placeholder"


def test_profit_uses_the_live_price_and_an_explicit_price_still_works():
    set_price(live=True, usd=553.0)
    item = paid_token()
    info = monitor.update_post_mint_profit_metrics(item)
    assert info["mint_usd"] == pytest.approx(0.01 * 553.0)
    set_price(live=False, usd=monitor.BNB_PRICE_FALLBACK_USD)
    explicit = monitor.update_post_mint_profit_metrics(paid_token(), 600.0)      # the caller vouches for the price
    assert explicit["mint_usd"] == pytest.approx(0.01 * 600.0)


def test_the_served_copy_of_a_record_has_no_profit_without_a_live_price():
    import api
    set_price(live=False, usd=monitor.BNB_PRICE_FALLBACK_USD)
    served = api.with_server_metrics(paid_token())
    assert "profit" not in served["server_metrics"]


def test_pancake_estimate_stores_no_usd_numbers_without_a_live_price(monkeypatch):
    token = "0x" + "1" * 40
    monkeypatch.setattr(monitor, "get_pair_address", lambda rpc, f, a, b: "0x" + "ab" * 20)
    monkeypatch.setattr(monitor, "get_reserves", lambda rpc, p: (9 * 10 ** 18, 4 * 10 ** 18))
    monkeypatch.setattr(monitor, "refresh_bnb_price", lambda rpc=None, max_age=60.0: monitor.cached_bnb_price())
    set_price(live=False, usd=monitor.BNB_PRICE_FALLBACK_USD)
    item = {"contract": token}
    assert worker._apply_pancake_estimate(None, item) is True, "the pair link is new information"
    assert item.get("pair_url", "").endswith("ab" * 20)
    assert not any(k in item for k in ("lp_usd", "lp_wbnb", "token_price_usd", "token_price_bnb")), "no USD numbers from the placeholder"
    assert worker._apply_pancake_estimate(None, item) is False, "nothing new on the second try"
    assert worker._needs_pancake_fallback({**item, "standard": "BEP-20 Token (18 decimals)", "total_minted": 5}), \
        "so the next sweep tries again"
    set_price(live=True, usd=553.0)
    assert worker._apply_pancake_estimate(None, item) is True
    assert item["lp_usd"] == pytest.approx(4 * 553.0 * 2)
