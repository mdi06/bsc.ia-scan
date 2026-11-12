import monitor
import worker


def _reset():
    worker._bnb_price.update(usd=worker.BNB_PRICE_FALLBACK_USD, fetched_at=0.0)


def test_uses_live_price_and_caches(monkeypatch):
    _reset()
    calls = []
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: calls.append(1) or 612.5)
    assert worker.get_bnb_price(None) == 612.5
    assert worker.get_bnb_price(None) == 612.5
    assert len(calls) == 1  # second call served from cache


def test_failure_keeps_last_good_price(monkeypatch):
    _reset()
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: 640.0)
    assert worker.get_bnb_price(None) == 640.0
    worker._bnb_price["fetched_at"] = 0.0  # force a refresh
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: None)
    assert worker.get_bnb_price(None) == 640.0


def test_falls_back_when_never_fetched(monkeypatch):
    _reset()
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: 0.0)
    assert worker.get_bnb_price(None) == worker.BNB_PRICE_FALLBACK_USD


def test_get_bnb_price_usd_keeps_750_fallback(monkeypatch):
    monkeypatch.setattr(monitor, "fetch_bnb_price_usd", lambda rpc: None)
    assert monitor.get_bnb_price_usd(None) == 750.0
