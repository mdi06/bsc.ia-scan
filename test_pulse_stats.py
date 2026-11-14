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
