"""Gaps found while testing the six fix branches against a copy of production (preview run):
the Pancake deferral had no bound, and the junk-count clean-up skipped DEX-listed tokens."""
import time

import pytest

import monitor
import worker


def _legacy(i):
    """A BEP-20 record from before market_updated_at existed that still shows a price: no liquidity and no DexScreener
    pair any more, so it queues for a Pancake lookup (AVESAKE in the preview run)."""
    return {"contract": f"0x{i:040x}", "standard": "BEP-20 Token (18 decimals)", "name": f"L{i}", "symbol": f"L{i}",
            "total_minted": 1000.0, "token_price_usd": 3.9e-06, "lp_usd": 0.0, "volume_24h": 10.0, "pair_url": ""}


class Stop(Exception):
    pass


def _one_sweep(monkeypatch, history):
    monkeypatch.setattr(worker, "get_shared_history", lambda force_refresh=False: history)
    monkeypatch.setattr(worker, "_fetch_dexscreener_pairs", lambda url: [])          # DexScreener lists none of them
    monkeypatch.setattr(monitor, "save_history", lambda h, delta=None: None)
    monkeypatch.setattr(monitor, "BscRpcClient", lambda eps: object())
    monkeypatch.setattr(monitor, "get_pair_address", lambda *a: None)               # and neither does Pancake
    monkeypatch.setattr(monitor, "refresh_bnb_price", lambda *a, **k: 650.0)
    monkeypatch.setattr(monitor, "evaluate_sniper_readiness", lambda it: {})

    def fake_sleep(s):
        if s >= 100:
            raise Stop()
    monkeypatch.setattr(worker.time, "sleep", fake_sleep)
    try:
        worker.run_market_dynamics_updater_loop()
    except Stop:
        pass


def _reset():
    worker._pancake_checked_at.clear()
    worker._pancake_defer_since.clear()


def test_records_queued_for_pancake_are_not_expired_inside_their_window(monkeypatch):
    _reset()
    history = [_legacy(i) for i in range(1, 61)]                                    # 60 candidates, 25 lookups per sweep
    _one_sweep(monkeypatch, history)
    expired = [x for x in history if x.get("market_stale")]
    waiting = [x for x in history if not x.get("market_stale")]
    assert len(expired) == worker.PANCAKE_FALLBACK_PER_SWEEP                        # looked up, nothing there: cleared
    assert len(waiting) == 35 and all(x["token_price_usd"] == 3.9e-06 for x in waiting)   # their chance is still coming


def test_records_waiting_too_long_are_expired_anyway(monkeypatch):
    """Legacy records queued behind thousands of others kept their old liquidity for hours (AVESAKE in the preview)."""
    _reset()
    history = [_legacy(i) for i in range(1, 61)]
    long_ago = time.time() - worker.PANCAKE_DEFER_MAX_SEC - 1
    for x in history:
        worker._pancake_defer_since[x["contract"]] = long_ago
    _one_sweep(monkeypatch, history)
    assert all(x.get("market_stale") for x in history)
    assert all(x["token_price_usd"] is None for x in history)


def test_a_record_that_gets_its_lookup_leaves_the_waiting_list(monkeypatch):
    _reset()
    history = [_legacy(i) for i in range(1, 31)]
    _one_sweep(monkeypatch, history)
    looked_up = {c for c in worker._pancake_checked_at}
    assert looked_up.isdisjoint(worker._pancake_defer_since)
    assert len(worker._pancake_defer_since) == 5                                    # only the 5 that are still waiting


# ------------------------------------------------------------------------------------------------ clean-up (H3 gap)
def test_dex_listed_token_with_holders_equal_to_its_supply_is_cleaned():
    listed = {"standard": "BEP-20 Token (18 decimals)", "total_minted": 1e9, "holders": 1e9, "buys_24h": 40,
              "pair_url": "https://dexscreener.com/bsc/0xpair", "market_source": "dexscreener"}
    assert worker.clean_count_junk(listed) == ["holders"]
    assert listed["holders"] is None and listed["buys_24h"] == 40                   # the real buy count stays


def test_buys_that_drifted_from_the_supply_are_cleaned():
    """珍珠币: buys 107,782,200 against total_minted 108,146,000 (the supply grew after the fallback copied it)."""
    item = {"total_minted": 108146000.0, "buys_24h": 107782200.0, "holders": 12, "pair_url": ""}
    assert worker.clean_count_junk(item) == ["buys_24h"] and item["holders"] == 12


def test_small_real_counts_on_listed_tokens_are_never_touched():
    item = {"total_minted": 50000.0, "holders": 50000, "buys_24h": 48000, "pair_url": "https://dexscreener.com/bsc/0xp"}
    assert worker.clean_count_junk(item) == []
    item = {"total_minted": 5e8, "holders": 3200, "buys_24h": 900, "pair_url": "https://dexscreener.com/bsc/0xp"}
    assert worker.clean_count_junk(item) == []
