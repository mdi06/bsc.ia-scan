"""Shared test setup.

BNB/USD: the profit maths and the worker's USD conversions only use a LIVE price (monitor.live_bnb_price), never the
built-in placeholder. Tests start with a live price equal to that placeholder, so they exercise the maths with the same
numbers as before; a test about an outage sets the state itself (see test_bnb_price.py, test_server_metrics.py).
"""
import pytest

import monitor

FAR_FUTURE = 4102444800.0      # year 2100: the price always counts as recently fetched, so nothing goes to the network


@pytest.fixture(autouse=True)
def live_bnb_price_by_default():
    saved = dict(monitor._bnb_price_state)
    monitor._bnb_price_state.update(usd=monitor.BNB_PRICE_FALLBACK_USD, live=True, ok_at=FAR_FUTURE, fetched_at=FAR_FUTURE)
    yield
    monitor._bnb_price_state.clear()
    monitor._bnb_price_state.update(saved)
