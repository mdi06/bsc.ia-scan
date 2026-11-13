"""The per-version cache behind /api/feed, /api/stats and /api/market-data (api._FeedSnapshot)."""
import gc
import json
import threading
import weakref

import pytest
from fastapi.testclient import TestClient

import api
import db as db_module
import monitor


@pytest.fixture
def client(tmp_path, monkeypatch):
    for var in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))
    monkeypatch.setattr(db_module, "FAVORITES_FILE", str(tmp_path / "no_favorites.json"))
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(api, "db", manager)
    monkeypatch.setattr(api, "_current_snapshot", None)
    monkeypatch.setattr(api, "RATE_LIMIT_MAX", 10 ** 9)
    monkeypatch.setattr(monitor, "load_agent_id_stats", lambda: {"total_created_wallets": 178001, "last_block": 5})
    c = TestClient(api.app)
    c.db = manager
    return c


def rec(n, **kw):
    d = {"contract": f"0x{n:040x}", "name": f"Token {n}", "symbol": f"T{n}", "standard": "BEP-20 Token (18 decimals)",
         "block": 1000 + n, "detected_at": "2026-11-12 09:00:00", "token_price_usd": 0.0, "lp_usd": 0.0}
    d.update(kw)
    return d


def seed(client, n=12):
    rows = []
    for i in range(1, n + 1):
        extra = {}
        if i % 2 == 0:
            extra.update(token_price_usd=0.5, lp_usd=1000.0 * i, market_cap=10000.0 * i)   # secondary market
        if i % 3 == 0:
            extra.update(smart_wallets=["a.ai"])
        if i % 4 == 0:
            extra.update(sniper_ready=True)
        rows.append(rec(i, **extra))
    rows.append(rec(99, symbol="SOL", token_price_usd=150.0, lp_usd=9e6))   # excluded asset: never served by the feed
    client.db.upsert_projects(rows)


URLS = ["/api/feed", "/api/feed?limit=5&offset=2", "/api/feed?filter=secondary", "/api/feed?filter=smart",
        "/api/feed?filter=sniper_ready", "/api/feed?filter=proxy", "/api/feed?filter=nope",
        "/api/feed?since_block=1005", "/api/feed?since_block=0", "/api/feed?since_block=999999",
        "/api/stats", "/api/market-data"]


def fetch(client, url, **kw):
    r = client.get(url, **kw)
    return r.status_code, r.headers.get("etag"), r.content


def test_a_cached_response_equals_an_uncached_one(client, monkeypatch):
    seed(client)
    monkeypatch.setattr(api.time, "time", lambda: 1794300000.0)         # pulse "as_of" / "since" would otherwise tick
    for url in URLS:
        api._current_snapshot = None
        cold = fetch(client, url)
        warm = fetch(client, url)                                        # served from the snapshot built by `cold`
        assert cold == warm, url
        assert cold[0] == 200
    # and the cached stats are what the from-scratch computation gives
    api._current_snapshot = None
    body = client.get("/api/feed").json()
    assert body["stats"] == api.compute_global_overview_stats()
    assert body["total"] == 12                                           # the SOL record is filtered out


def test_the_filtered_and_sorted_list_per_filter_matches_the_direct_computation(client):
    seed(client)
    raw = client.db.get_all_projects()
    live = [p for p in raw if not api.is_bogus_or_synthetic_asset(p)]
    for name, pred in api.FEED_FILTERS.items():
        want = [d for d in live if pred(d)]
        if name == "secondary":
            want.sort(key=lambda x: api.get_sanitized_mcap(x) or 0.0, reverse=True)
        got = client.get(f"/api/feed?filter={name}&limit=500").json()
        assert [p["contract"] for p in got["projects"]] == [d["contract"] for d in want], name
        assert got["filter_total"] == len(want)


def test_repeated_requests_reuse_the_snapshot_and_a_new_content_version_rebuilds_it(client, monkeypatch):
    seed(client)
    built = []
    real = api._FeedSnapshot

    class Counting(real):
        def __init__(self, *a, **k):
            built.append(1)
            super().__init__(*a, **k)

    monkeypatch.setattr(api, "_FeedSnapshot", Counting)
    r1 = client.get("/api/feed")
    etag1 = r1.headers["etag"]
    for _ in range(5):
        assert client.get("/api/feed", headers={"If-None-Match": etag1}).status_code == 304
        client.get("/api/feed?filter=secondary")
        client.get("/api/stats")
        client.get("/api/market-data")
    assert len(built) == 1

    v0 = client.db.content_version()
    client.db.upsert_projects([rec(50, token_price_usd=2.0, lp_usd=90000.0, market_cap=70000.0)])
    assert client.db.content_version() > v0
    r2 = client.get("/api/feed", headers={"If-None-Match": etag1})
    assert r2.status_code == 200 and r2.headers["etag"] != etag1         # new content: never a stale 304
    assert r2.json()["total"] == 13 and r2.json()["stats"]["total"] == 13
    assert len(built) == 2
    assert any(p["contract"].endswith("32") for p in client.get("/api/feed?filter=secondary").json()["projects"])
    assert "0x" + f"{50:040x}" in client.get("/api/market-data").json()
    assert len(built) == 2

    # a deletion bumps the version too
    client.db.delete_projects([f"0x{50:040x}"])
    assert client.get("/api/feed").json()["total"] == 12
    assert len(built) == 3


def test_only_one_version_is_held(client):
    seed(client)
    client.get("/api/feed")
    old = weakref.ref(api._current_snapshot)
    client.db.upsert_projects([rec(60)])
    client.get("/api/feed")
    gc.collect()
    assert old() is None                                                 # the superseded snapshot is gone
    assert api._current_snapshot.version == client.db.content_version()


def test_a_version_that_moves_during_the_load_is_served_but_not_kept(client, monkeypatch):
    seed(client)
    real = client.db.get_all_projects

    def refreshing(*a, **k):
        client.db._content_version += 1                                  # what a cache refresh inside the call does
        return real(*a, **k)

    monkeypatch.setattr(client.db, "get_all_projects", refreshing)
    r = client.get("/api/feed")
    assert r.status_code == 200 and r.json()["total"] == 12
    assert api._current_snapshot is None
    assert r.headers["etag"].startswith(f'"v{client.db.content_version()}-')   # the ETag follows the version after the load


def test_the_agent_wallet_counter_is_not_frozen_into_the_snapshot(client, monkeypatch):
    seed(client)
    etag1 = client.get("/api/feed?since_block=999999").headers["etag"]
    monkeypatch.setattr(monitor, "load_agent_id_stats", lambda: {"total_created_wallets": 178002, "last_block": 6})
    r = client.get("/api/feed?since_block=999999", headers={"If-None-Match": etag1})
    assert r.status_code == 200 and r.json()["agent_wallets"] == 178002  # same content version, new counter


def test_the_pulse_is_cached_for_a_few_seconds_then_recomputed(client, monkeypatch):
    seed(client)
    calls = []
    real = api.compute_pulse_24h
    monkeypatch.setattr(api, "compute_pulse_24h", lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(4):
        client.get("/api/stats")
    assert len(calls) == 1
    monkeypatch.setattr(api, "PULSE_CACHE_TTL_SEC", 0.0)
    client.get("/api/stats")
    client.get("/api/stats")
    assert len(calls) == 3


def test_concurrent_first_requests_build_the_snapshot_once(client, monkeypatch):
    seed(client)
    built = []
    real = api._FeedSnapshot

    class Counting(real):
        def __init__(self, *a, **k):
            built.append(1)
            super().__init__(*a, **k)

    monkeypatch.setattr(api, "_FeedSnapshot", Counting)
    results, errors = [], []

    def worker(i):
        try:
            url = ["/api/feed", "/api/feed?filter=secondary", "/api/stats", "/api/market-data", "/api/feed?since_block=1003"][i % 5]
            r = client.get(url)
            results.append((url, r.status_code, r.content if "stats" not in url and "since" not in url else r.status_code))
        except Exception as e:                                           # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(results) == 20
    assert all(status == 200 for _, status, _ in results)
    assert len(built) == 1
    by_url = {}
    for url, _, body in results:
        by_url.setdefault(url, set()).add(body)
    assert all(len(v) == 1 for k, v in by_url.items() if k in ("/api/feed", "/api/feed?filter=secondary", "/api/market-data"))


def test_delta_by_bisect_equals_the_linear_scan(client):
    seed(client)
    snap, _ = api._load_snapshot()
    for since in (-5, 0, 1000, 1001, 1006, 1012, 1013, 5000):
        want = [p for p in snap.projects if int(p.get("block") or 0) > since]
        assert snap.newer_than(since) == want, since
    # a list that is not block-descending falls back to the plain scan
    scrambled = list(reversed(client.db.get_all_projects()))
    odd = api._FeedSnapshot(client.db, 0, scrambled)
    for since in (0, 1005, 1012):
        assert odd.newer_than(since) == [p for p in odd.projects if int(p.get("block") or 0) > since]


def test_market_data_body_is_the_json_the_endpoint_always_sent(client):
    seed(client)
    client.db.upsert_projects([rec(70, token_price_usd=float("nan"), lp_usd=5.0)])   # a non-finite float becomes null
    r = client.get("/api/market-data")
    assert r.headers["content-type"] == "application/json" and r.headers["cache-control"] == "public, max-age=60"
    want = {}
    for p in client.db.get_all_projects():
        c = (p.get("contract") or "").lower()
        if c and (p.get("token_price_usd") or p.get("lp_usd") or p.get("market_stale")):
            want[c] = {k: p.get(k) for k in api.MARKET_FIELDS + api.MARKET_FRESHNESS_FIELDS}
    assert r.content == api.SafeJSONResponse(content=want).body
    assert json.loads(r.content)[f"0x{70:040x}"]["token_price_usd"] is None
