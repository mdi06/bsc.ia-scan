"""'Crowd surge' (snipe ready with no cap) used to be `holders >= 100`. For a token that is every wallet holding it, so DEX-traded
1B-supply tokens with no mint at all passed (539 of 683 such records, 2026-11-24); unique_minters was never stored."""
import time

import filters
import monitor
import worker

NOW = time.time()
TOKEN = "BEP-20 Token (18 decimals)"
NFT = "ERC-721 (NFT Collection)"


def drop(**kw):
    d = {"contract": "0x" + "a" * 40, "standard": TOKEN, "max_supply": None, "total_minted": 5_000_000.0, "tokens_per_mint": 10_000.0,
         "mint_price_str": "⭐ FREE", "last_mint_at": NOW - 600, "holders": 5000, "lp_usd": 0.0, "risk_level": "SAFE"}
    d.update(kw)
    return d


def test_a_live_uncapped_mint_with_a_crowd_is_a_surge():
    assert filters.crowd_surge_mints(drop(), NOW) == 500                       # 5M tokens / 10k per mint
    assert filters.crowd_surge_mints(drop(last_mint_at=0, mint_terms={"open": True}), NOW) == 500


def test_holders_alone_never_make_a_surge():
    assert filters.crowd_surge_mints(drop(total_minted=0, holders=9999), NOW) == 0
    assert filters.crowd_surge_mints(drop(tokens_per_mint=None), NOW) == 0     # 1B supply is not 1B mints


def test_dex_traded_capped_or_stale_drops_are_not_surges():
    assert filters.crowd_surge_mints(drop(mint_price_str="DEX Trading"), NOW) == 0
    assert filters.crowd_surge_mints(drop(max_supply=1e9), NOW) == 0
    assert filters.crowd_surge_mints(drop(last_mint_at=NOW - 3 * 86400), NOW) == 0
    assert filters.crowd_surge_mints(drop(total_minted=500_000.0), NOW) == 0   # 50 mints: too few


def test_nft_counts_items_as_mints():
    assert filters.crowd_surge_mints(drop(standard=NFT, total_minted=250.0, tokens_per_mint=None), NOW) == 250


def test_sniper_rule_uses_the_surge():
    assert monitor.evaluate_sniper_ready(drop())["sniper_ready"] is True
    assert monitor.evaluate_sniper_ready(drop(last_mint_at=NOW - 3 * 86400))["sniper_ready"] is False
    assert monitor.evaluate_sniper_ready(drop(tokens_per_mint=None))["sniper_ready"] is False


def test_stored_flags_that_fail_the_new_rule_are_cleared_and_others_kept():
    stale = drop(contract="0x" + "1" * 40, sniper_ready=True, mint_price_str="DEX Trading")
    good = drop(contract="0x" + "2" * 40, sniper_ready=True)
    never = drop(contract="0x" + "3" * 40, sniper_ready=False, mint_price_str="DEX Trading")
    changed = worker.reevaluate_ready_flags([stale, good, never])
    assert changed == [stale] and stale["sniper_ready"] is False and stale["sniper_status"] != "SNIPER_READY"
    assert good["sniper_ready"] is True and never["sniper_ready"] is False
    assert worker.reevaluate_ready_flags([stale, good, never]) == []         # nothing more to do
