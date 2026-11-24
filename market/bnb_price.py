"""Fetching the BNB/USD price from the PancakeSwap V2 WBNB/USDT pool and caching it for a minute (the cache state lives here).
The small accessors that say whether the price is live (cached_bnb_price, bnb_price_is_live, live_bnb_price, ...) stay in
monitor.py, which the rest of the code and the tests patch them on. Moved out of monitor.py unchanged; monitor.py re-exports
these names and the shared `_bnb_price_state` dict."""

import logging
import threading
import time
from typing import Optional

from chain.addresses import PANCAKE_FACTORY_V2, USDT_ADDRESS, WBNB_ADDRESS
from chain.rpc import RPC_ENDPOINTS, BscRpcClient

_log = logging.getLogger(__name__)



def fetch_bnb_price_usd(rpc: BscRpcClient) -> Optional[float]:
    """BNB/USD from the PancakeSwap V2 WBNB/USDT pool reserves, or None if the lookup fails."""
    try:
        call_data = "0xe6a43905" + WBNB_ADDRESS[2:].lower().zfill(64) + USDT_ADDRESS[2:].lower().zfill(64)
        pair_res = rpc.call_contract(PANCAKE_FACTORY_V2, call_data)
        if not pair_res or len(pair_res) < 66:
            return None
        pair_addr = "0x" + pair_res[-40:]
        res = rpc.call_contract(pair_addr, "0x0902f1ac")
        if not res or len(res) < 130:
            return None
        r0 = int(res[2:66], 16)
        r1 = int(res[66:130], 16)
        t0 = rpc.call_contract(pair_addr, "0x0dfe1681")
        if USDT_ADDRESS[2:].lower() in (t0 or "").lower():
            return (r0 / 1e18) / (r1 / 1e18)
        else:
            return (r1 / 1e18) / (r0 / 1e18)
    except Exception as exc:
        _log.debug("BNB price lookup failed: %s", exc)
        return None


BNB_PRICE_FALLBACK_USD = 705.0  # used until the first successful lookup


BNB_PRICE_TTL_SEC = 60.0


BNB_PRICE_STALE_AFTER_TTLS = 3  # the price stops counting as live once its last success is older than 3 TTLs


# fetched_at = last *attempt* (throttles retries); ok_at = last *successful* lookup (0.0 = never).
_bnb_price_state = {"usd": BNB_PRICE_FALLBACK_USD, "fetched_at": 0.0, "live": False, "ok_at": 0.0}


_bnb_price_lock = threading.Lock()


_bnb_price_rpc: Optional[BscRpcClient] = None


def refresh_bnb_price(rpc: Optional[BscRpcClient] = None, max_age: float = BNB_PRICE_TTL_SEC) -> float:
    """Returns BNB/USD, re-reading the Pancake WBNB/USDT pool when the cached value is older than `max_age`.
    A failed lookup keeps the last good price (or BNB_PRICE_FALLBACK_USD if there never was one)."""
    global _bnb_price_rpc
    with _bnb_price_lock:
        now = time.time()
        if now - _bnb_price_state["fetched_at"] >= max_age:
            if rpc is None:
                if _bnb_price_rpc is None:
                    _bnb_price_rpc = BscRpcClient(RPC_ENDPOINTS)
                rpc = _bnb_price_rpc
            price = fetch_bnb_price_usd(rpc)
            if price and price > 0:
                _bnb_price_state.update(usd=price, live=True, ok_at=now)
            else:
                _log.debug("BNB price lookup failed; keeping $%.2f", _bnb_price_state["usd"])
            _bnb_price_state["fetched_at"] = now  # also after a failure, so a down RPC isn't retried per call
        return _bnb_price_state["usd"]
