"""The agent-wallet counter is shown as a fact: no source (or an ancient one) means "unknown", never a made-up number."""
import time

import pytest
from fastapi.testclient import TestClient

import api
import monitor

DEFAULT = monitor.DEFAULT_AGENT_WALLET_COUNT


def fresh_record(count, ago=60):
    return {"total_created_wallets": count, "last_block": 135_000_000, "last_updated": time.time() - ago}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_MAX", 10 ** 9)
    monkeypatch.setattr(api.db, "get_all_projects", lambda *a, **k: [])
    return TestClient(api.app)


def sources(monkeypatch, local, cloud):
    monkeypatch.setattr(monitor, "load_agent_id_stats", lambda: local)
    monkeypatch.setattr(api.db, "get_agent_id_stats", lambda *a, **k: cloud)


def test_the_built_in_default_is_marked_and_never_looks_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "STATS_FILE", str(tmp_path / "none.json"))
    monkeypatch.setattr(monitor, "STATS_FILE_ROOT", str(tmp_path / "none2.json"))
    d = monitor.load_agent_id_stats()
    assert d["is_default"] is True and d["total_created_wallets"] == DEFAULT and d["last_updated"] == 0


def test_no_source_at_all_is_unknown_not_178001(monkeypatch):
    builtin = {"is_default": True, "total_created_wallets": DEFAULT, "last_block": 132757997, "last_updated": 0}
    for local in ({}, builtin):
        sources(monkeypatch, local, {})
        assert api._agent_wallet_stats() == (None, 0, False)


def test_a_failing_source_is_treated_as_missing(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(monitor, "load_agent_id_stats", boom)
    monkeypatch.setattr(api.db, "get_agent_id_stats", boom)
    assert api._agent_wallet_stats() == (None, 0, False)


def test_the_newest_real_count_wins_and_live_follows_its_age(monkeypatch):
    sources(monkeypatch, fresh_record(182_449, ago=3 * 86400), fresh_record(263_727, ago=45))
    assert api._agent_wallet_stats() == (263_727, 135_000_000, True)
    sources(monkeypatch, {}, fresh_record(263_700, ago=2 * 3600))
    total, block, live = api._agent_wallet_stats()
    assert total == 263_700 and live is False, "a count from 2 hours ago is shown, but not as live"


def test_a_count_older_than_a_week_is_unknown(monkeypatch):
    """The file the API ships with said 182,449 when the real number was ~264,000."""
    sources(monkeypatch, fresh_record(182_449, ago=11 * 86400), {})
    assert api._agent_wallet_stats() == (None, 0, False)
    sources(monkeypatch, fresh_record(182_449, ago=6 * 86400), {})
    assert api._agent_wallet_stats()[0] == 182_449


def test_a_record_without_a_timestamp_is_still_shown(monkeypatch):
    sources(monkeypatch, {"total_created_wallets": 178_002, "last_block": 6}, {})
    assert api._agent_wallet_stats() == (178_002, 6, False)


def test_stats_endpoint_says_unknown_when_nothing_is_known(client, monkeypatch):
    sources(monkeypatch, {}, {})
    body = client.get("/api/stats").json()
    assert body["agent_wallets"] is None and body["total_created_wallets"] is None
    assert body["stats"]["agent_wallets"] is None and body["stats"]["agent_wallets_live"] is False
    assert str(DEFAULT) not in client.get("/api/stats").text
    feed = client.get("/api/feed?limit=1").json()
    assert feed["stats"]["agent_wallets"] is None and str(DEFAULT) not in client.get("/api/feed?limit=1").text


def test_stats_endpoint_serves_a_real_count_with_its_liveness(client, monkeypatch):
    sources(monkeypatch, {}, fresh_record(263_727))
    body = client.get("/api/stats").json()
    assert body["agent_wallets"] == 263_727 and body["stats"]["agent_wallets_live"] is True


def test_overview_fallback_does_not_invent_a_count():
    fb = api._overview_stats_fallback([])
    assert fb["agent_wallets"] is None and fb["total_created_wallets"] is None and fb["agent_wallets_live"] is False


def test_agent_id_stats_json_unknown_and_known(client, monkeypatch):
    sources(monkeypatch, {}, {})
    r = client.get("/agent_id_stats.json")
    assert r.json()["total_created_wallets"] is None and str(DEFAULT) not in r.text
    sources(monkeypatch, {}, fresh_record(263_727))
    assert client.get("/data/agent_id_stats.json").json()["total_created_wallets"] == 263_727


def test_a_stale_bigger_record_cannot_hide_a_fresh_smaller_one(monkeypatch):
    """Each source is checked before the choice: the ancient database row has the larger count."""
    sources(monkeypatch, fresh_record(178_100, ago=120), fresh_record(263_727, ago=20 * 86400))
    assert api._agent_wallet_stats()[0] == 178_100
    sources(monkeypatch, fresh_record(182_449, ago=20 * 86400), fresh_record(263_727, ago=20 * 86400))
    assert api._agent_wallet_stats() == (None, 0, False)
