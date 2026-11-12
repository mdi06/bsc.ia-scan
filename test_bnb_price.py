import pytest
from fastapi.testclient import TestClient

import api
import monitor


@pytest.fixture(autouse=True)
def reset_price_state():
    saved = dict(monitor._bnb_price_state)
    monitor._bnb_price_state.update(usd=monitor.BNB_PRICE_FALLBACK_USD, fetched_at=0.0, live=False, ok_at=0.0)
    yield
    monitor._bnb_price_state.update(saved)


def test_uses_live_price_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: calls.append(1) or 612.5)
    assert monitor.refresh_bnb_price(object()) == 612.5
    assert monitor.refresh_bnb_price(object()) == 612.5
    assert len(calls) == 1  # second call served from cache
    assert monitor.cached_bnb_price() == 612.5


def test_failure_keeps_last_good_price(monkeypatch):
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: 640.0)
    assert monitor.refresh_bnb_price(object()) == 640.0
    monitor._bnb_price_state["fetched_at"] = 0.0  # force a refresh
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: None)
    assert monitor.refresh_bnb_price(object()) == 640.0


def test_falls_back_when_never_fetched(monkeypatch):
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: 0.0)
    assert monitor.refresh_bnb_price(object()) == monitor.BNB_PRICE_FALLBACK_USD
    assert monitor._bnb_price_state["live"] is False


def test_display_helpers_use_cached_price_when_not_given(monkeypatch):
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: 500.0)
    monitor.refresh_bnb_price(object())
    item = {"floor_price_bnb": 0.1, "standard": "ERC-721 (NFT Collection)"}
    assert "$50.00" in monitor.format_floor_trade_price(item)
    assert "$70.50" in monitor.format_floor_trade_price(item, bnb_price=705.0)


def test_bnb_price_endpoint(monkeypatch):
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: 688.0)
    res = TestClient(api.app).get("/api/bnb-price")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True and body["bnb_price_usd"] == 688.0 and body["live"] is True
    assert body["as_of"] == monitor.bnb_price_as_of() and body["as_of"] > 0
