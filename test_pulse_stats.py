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
