"""Stats bar mint numbers over every record (api.compute_mint_progress_stats / filters.mint_progress_bucket)."""
import time

import api
import filters

NOW = 1_800_000_000


def nft(n, minted, cap=100, **extra):
    d = {"contract": "0x%040x" % n, "name": f"N{n}", "standard": "ERC-721 (NFT Collection)",
         "max_supply": cap, "total_minted": minted}
    d.update(extra)
    return d


def test_buckets_and_pre_minted_tokens():
    assert filters.mint_progress_bucket(nft(1, 100)) == "sold_out"
    assert filters.mint_progress_bucket(nft(2, 85)) == "near_cap"
    assert filters.mint_progress_bucket(nft(3, 50)) == "momentum"
    assert filters.mint_progress_bucket(nft(4, 49)) == "open"
    assert filters.mint_progress_bucket(nft(5, 10, cap=0)) is None
    assert filters.mint_progress_bucket(nft(6, 9e24, cap=1e25)) is None          # "no cap" sentinel
    pre = {"standard": "BEP-20 Token (18 decimals)", "max_supply": 1e9, "total_minted": 1e9}
    assert filters.mint_progress_bucket(pre) is None                              # minted up front: not a mint


def test_active_means_minted_in_the_window():
    recent = NOW - 600
    old = NOW - 3 * 86400
    rows = [
        nft(1, 100), nft(2, 100), nft(3, 10),
        nft(4, 85, last_mint_at=recent), nft(5, 90, last_mint_at=old), nft(6, 95),
        nft(7, 70, name="Hot", last_mint_at=recent), nft(8, 60, name="Warm", last_mint_at=recent),
        nft(9, 55, last_mint_at=old),
        {"standard": "BEP-20 Token (18 decimals)", "max_supply": 1e9, "total_minted": 1e9},
    ]
    s = api.compute_mint_progress_stats(rows, now=NOW)
    assert s["capped"] == 9
    assert s["sold_out"] == 2
    assert s["fill_pct"] == round(2 / 9 * 100, 1)
    assert (s["near_cap"], s["near_cap_active"]) == (3, 1)
    assert (s["momentum"], s["momentum_active"]) == (3, 2)
    assert [r["name"] for r in s["active_top"]] == ["Hot", "Warm"]
    assert s["active_window_hours"] == 6


def test_no_capped_records():
    s = api.compute_mint_progress_stats([], now=NOW)
    assert s["capped"] == 0 and s["fill_pct"] is None and s["active_top"] == []


def test_overview_stats_carry_mint_progress():
    rows = [nft(1, 100), nft(2, 60, last_mint_at=time.time())]
    stats = api.compute_global_overview_stats(rows)
    assert stats["mint_progress"]["capped"] == 2
    assert stats["mint_progress"]["momentum_active"] == 1
