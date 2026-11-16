"""H10: the 24h Pulse aggregates are computed by the server over the full dataset, by timestamp."""
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

import api

NOW = 1_800_000_000.0


def stamp(seconds_ago):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - seconds_ago))


def drop(i, ago, **kw):
    d = {"contract": f"0x{i:040x}", "name": f"D{i}", "symbol": f"S{i}", "standard": "BEP-20 Token (18 decimals)",
         "detected_at": stamp(ago), "volume_24h": 100.0, "lp_usd": 0, "risk_level": "SAFE"}
    d.update(kw)
    return d


def test_window_is_a_timestamp_not_a_block_count():
    inside = [drop(i, 3600 * (i % 20)) for i in range(1, 41)]          # 40 drops, all within the last 20 h
    outside = [drop(100 + i, 86400 + 3600 * (i + 1)) for i in range(30)]   # older than 24 h
    pulse = api.compute_pulse_24h(inside + outside, now=NOW)
    assert pulse["new_drops"] == 40
    assert pulse["dex_volume_usd"] == pytest.approx(4000.0)
    assert pulse["since"] == stamp(86400) and pulse["window_hours"] == 24
    assert pulse["as_of"] == int(NOW)


def test_aggregates_cover_the_whole_dataset_whatever_page_the_browser_loaded():
    """The old client summed the loaded page only: $740.8K became $31.9M after opening another tab."""
    records = [drop(i, 600, volume_24h=1000.0) for i in range(1, 3001)]
    pulse = api.compute_pulse_24h(records, now=NOW)
    assert pulse["new_drops"] == 3000 and pulse["dex_volume_usd"] == pytest.approx(3_000_000.0)
    assert api.compute_pulse_24h(records[:150], now=NOW)["dex_volume_usd"] == pytest.approx(150_000.0)  # a page is a page


def test_security_smart_money_and_graduation_counts():
    records = [
        drop(1, 100, smart_wallets=["a.ai", "b.ai", "c.ai"], security={"has_refund": True}, lp_usd=60000),
        drop(2, 100, smart_wallets=["a.ai"], risk_level="RUG", risk_badge="HONEYPOT"),
        drop(3, 100, risk_level="HIGH"),
        drop(4, 100, risk_badge="Serial Dev"),
        drop(5, 100, is_graduated=True),
    ]
    p = api.compute_pulse_24h(records, now=NOW)
    assert (p["smart_inflows"], p["smart_clusters"]) == (4, 1)
    assert (p["top_hunter"], p["top_hunter_count"]) == ("a.ai", 2)
    assert (p["clean_mints"], p["threats_blocked"], p["refunds"]) == (2, 3, 1)
    assert p["graduated"] == 2                                          # the pooled drop and the graduated one


def test_excluded_assets_and_missing_timestamps_do_not_count():
    records = [drop(1, 100), drop(2, 100, symbol="SOL"), drop(3, 100, contract="0xd0523ce816e2b404db5633d181572cded33d7fa9"),
               {"contract": "0x" + "9" * 40, "name": "NoDate", "symbol": "ND"}]
    p = api.compute_pulse_24h(records, now=NOW)
    assert p["new_drops"] == 1 and p["top_hunter"] is None


def test_stats_endpoint_serves_pulse_and_its_etag_follows_it(monkeypatch):
    recs = [drop(1, 100, volume_24h=500.0)]
    monkeypatch.setattr(api.db, "get_all_projects", lambda *a, **k: [dict(r) for r in recs])
    monkeypatch.setattr(api.db, "get_agent_id_stats", lambda *a, **k: {})
    client = TestClient(api.app)
    r1 = client.get("/api/stats")
    assert r1.status_code == 200
    pulse = r1.json()["stats"]["pulse_24h"]
    assert pulse["new_drops"] == 1 and pulse["dex_volume_usd"] == pytest.approx(500.0)
    etag1 = r1.headers["etag"]
    recs.append(drop(2, 100, volume_24h=900.0))
    r2 = client.get("/api/stats", headers={"If-None-Match": etag1})
    assert r2.status_code == 200 and r2.headers["etag"] != etag1         # changed pulse: never a stale 304
    assert r2.json()["stats"]["pulse_24h"]["dex_volume_usd"] == pytest.approx(1400.0)


def test_display_tests_in_node():
    """test_display_prompt5.js: formatters, BNB price, modal mint rules, table prices, stale markers, seeds."""
    res = subprocess.run(["node", "test_display_prompt5.js"], capture_output=True, text=True)
    assert res.returncode == 0, res.stdout[-1500:] + res.stderr[-1500:]
    assert "Prompt 5 display tests passed" in res.stdout


# ------------------------------------------------------------------ cards 2 and 4: new launches, NFT mints

def nft(i, ago, minted, cap=None, **kw):
    return drop(i, ago, standard="ERC-721 (NFT Collection)", total_minted=minted, max_supply=cap,
                volume_24h=0, **kw)


def test_new_launches_are_new_trading_tokens_ranked_by_volume():
    rows = [
        drop(1, 600, token_price_usd=0.01, lp_usd=20000, volume_24h=50000, price_change_24h=12.5),
        drop(2, 600, token_price_usd=0.02, lp_usd=9000, volume_24h=900000),
        drop(3, 600, token_price_usd=0.03, lp_usd=500, volume_24h=10**6),          # too little liquidity
        drop(4, 600, token_price_usd=0.04, lp_usd=50000, volume_24h=500),           # too little volume
        drop(5, 600, token_price_usd=0, lp_usd=50000, volume_24h=10**6),            # no price
        drop(6, 600, token_price_usd=0.06, lp_usd=50000, volume_24h=10**6, market_stale=True),
        drop(7, 600, token_price_usd=0.07, lp_usd=50000, volume_24h=10**6, risk_level="RUG"),
        drop(8, 90000, token_price_usd=0.08, lp_usd=50000, volume_24h=10**7),        # older than 24 h: not new
        nft(9, 600, 100),                                                             # an NFT is not a launch
    ]
    launches = api.compute_pulse_24h(rows, now=NOW)["top_launches"]
    assert [r["name"] for r in launches] == ["D2", "D1"]
    assert launches[1]["price_change_24h"] == 12.5 and launches[1]["lp_usd"] == 20000


def test_nft_mints_rank_new_collections_by_mints_with_fill_share():
    rows = [
        nft(1, 600, 2405, 10000, smart_wallets=["a"] * 104),
        nft(2, 600, 10000, 10000),
        nft(3, 600, 1045, 2222),
        nft(4, 600, 50, None),                      # uncapped: no fill share
        nft(5, 600, 9000, 1e77),                    # "uncapped" sentinel: no fill share
        nft(6, 600, 0, 100),                        # nothing minted yet
        nft(7, 90000, 99999, 100000),               # older than 24 h
        nft(8, 600, 5000, 5000, risk_badge="🚨 HONEYPOT"),
    ]
    mints = api.compute_pulse_24h(rows, now=NOW)["top_nft_mints"]
    assert [m["name"] for m in mints] == ["D2", "D5", "D1"]
    assert mints[0]["fill_pct"] == 100.0 and mints[1]["fill_pct"] is None and mints[1]["max_supply"] is None
    assert mints[2]["fill_pct"] == 24.1 and mints[2]["smart_count"] == 104
    assert api.compute_pulse_24h([nft(4, 600, 50)], now=NOW)["top_nft_mints"][0]["total_minted"] == 50


# ------------------------------------------------------------------ card 1: runners over the whole dataset

def live(i, ago, **kw):
    d = drop(i, ago, token_price_usd=0.01, market_cap=50_000.0, lp_usd=5000.0, volume_24h=5000.0, price_change_24h=0.0)
    d.update(kw)
    return d


def test_runners_are_ranked_over_every_live_token_whatever_its_age():
    rows = [
        live(1, 10 * 86400, price_change_24h=40.0),                       # an old token that is moving today
        live(2, 600, price_change_24h=150.0, volume_24h=8000.0),
        live(3, 600, price_change_24h=-20.0, market_cap=900_000.0),       # losing, but the biggest
        live(4, 600, price_change_24h=5.0),
        live(5, 600, price_change_24h=500.0, lp_usd=100.0),               # too little liquidity: out
        live(6, 600, price_change_24h=500.0, volume_24h=50.0),            # too little volume: out
        live(7, 600, price_change_24h=500.0, market_cap=100.0),           # too small: out
        live(8, 600, price_change_24h=500.0, token_price_usd=1e-9),       # dust price: out
        live(9, 600, price_change_24h=500.0, market_stale=True),          # cleared as stale: out
        live(10, 600, price_change_24h=500.0, risk_level="RUG"),          # rug: out
        live(11, 600, price_change_24h=500.0, risk_badge="🚨 HONEYPOT"),  # honeypot: out
    ]
    runners = api.compute_pulse_24h(rows, now=NOW)["top_runners"]
    # gainers first, biggest gain first; any age counts (D1 is 10 days old)
    assert [r["name"] for r in runners] == ["D2", "D1", "D4"]
    assert runners[0]["price_change_24h"] == 150.0 and runners[0]["_slim"] is True


def test_runners_fall_back_to_market_cap_and_volume_when_nothing_is_gaining():
    rows = [live(1, 600, price_change_24h=-5.0, market_cap=10_000.0, volume_24h=500.0),
            live(2, 600, price_change_24h=-1.0, market_cap=80_000.0, volume_24h=500.0),
            live(3, 600, price_change_24h=0.0, market_cap=40_000.0, volume_24h=60_000.0)]
    assert [r["name"] for r in api.compute_pulse_24h(rows, now=NOW)["top_runners"]] == ["D3", "D2", "D1"]


def test_runner_rows_carry_what_the_card_and_modal_need():
    row = api.compute_pulse_24h([live(1, 600, price_change_24h=12.0, pair_url="https://dexscreener.com/bsc/0xabc",
                                      fdv=60_000.0)], now=NOW)["top_runners"][0]
    for key in ("contract", "name", "symbol", "token_price_usd", "price_change_24h", "market_cap", "fdv", "lp_usd",
                "volume_24h", "pair_url", "market_url", "risk_level", "risk_badge"):
        assert key in row, key
    assert row["pair_url"].endswith("0xabc")


# ------------------------------------------------------------------ launches: the age of the DEX pair

def test_an_established_token_the_worker_only_just_saw_is_not_a_new_launch():
    """Tether Gold (XAUt, pair 229 days old) and EVAA (410 days) were first SEEN by the worker today, so they passed
    the detected_at window and topped the new-launches card. The pair's age says what detected_at cannot."""
    young = NOW - 6 * 3600
    rows = [
        drop(1, 600, token_price_usd=3869.0, lp_usd=1.4e6, volume_24h=872_000.0, pair_created_at=NOW - 229 * 86400),  # XAUt
        drop(2, 600, token_price_usd=3.1, lp_usd=257_000.0, volume_24h=9e6, pair_created_at=NOW - 410 * 86400),       # EVAA
        drop(3, 600, token_price_usd=0.03, lp_usd=55_000.0, volume_24h=550_000.0, pair_created_at=young),             # a real launch
        drop(4, 600, token_price_usd=0.01, lp_usd=9_000.0, volume_24h=400_000.0),                                     # age not known yet
        drop(5, 600, token_price_usd=0.02, lp_usd=9_000.0, volume_24h=300_000.0, pair_created_at=NOW - 2 * 86400),    # within 3 days
        drop(6, 600, token_price_usd=0.02, lp_usd=9_000.0, volume_24h=200_000.0, pair_created_at=NOW - 4 * 86400),    # older than 3 days
    ]
    launches = api.compute_pulse_24h(rows, now=NOW)["top_launches"]
    assert [r["name"] for r in launches] == ["D3", "D4", "D5"]
