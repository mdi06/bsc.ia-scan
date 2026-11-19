"""Finished response bodies are encoded and compressed once per content version and reused (api._BodyCache), the sorted
record list is kept until the cache changes (db._sorted_projects), and the per-version work no longer blocks other
requests. In every case the bytes a client receives are the bytes the uncached code produces."""
import gzip
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import api
import db as db_module
import monitor

GZ = {"Accept-Encoding": "gzip"}
PLAIN = {"Accept-Encoding": "identity"}


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
    monkeypatch.setattr(monitor, "cached_bnb_price", lambda: 600.0)
    monkeypatch.setattr(monitor, "bnb_price_is_live", lambda: True)       # the server metrics (profit) use the price
    monkeypatch.setattr(api.time, "time", lambda: 1794300000.0)          # pulse "as_of" / "since" would otherwise tick
    monkeypatch.setattr(api, "PULSE_CACHE_TTL_SEC", 10 ** 9)
    real_load_seed = api._load_seed
    monkeypatch.setattr(api, "_load_seed", lambda name: (
        ([{"contract": f"0x{i:040x}"} for i in range(0, 200)], None) if name == "seeded_smart_drops.json"
        else real_load_seed(name)))
    c = TestClient(api.app)
    c.db = manager
    return c


def rec(n, **kw):
    d = {"contract": f"0x{n:040x}", "name": f"Token {n}", "symbol": f"T{n}", "standard": "BEP-20 Token (18 decimals)",
         "block": 1000 + n, "detected_at": "2026-11-12 09:00:00", "token_price_usd": 0.0, "lp_usd": 0.0}
    d.update(kw)
    return d


def seed(client, n=40):
    rows = []
    for i in range(1, n + 1):
        extra = {}
        if i % 2 == 0:
            extra.update(token_price_usd=0.5, lp_usd=1000.0 * i, market_cap=10000.0 * i, volume_24h=500.0 * i,
                         mint_price_str="0.0100 BNB", mint_price_bnb=0.01, mint_value_wei="10000000000000000")
        if i % 3 == 0:
            extra.update(smart_wallets=["a.ai"])
        rows.append(rec(i, **extra))
    client.db.upsert_projects(rows)


URLS = ["/api/feed", "/api/feed?limit=5&offset=2", "/api/feed?limit=500", "/api/feed?filter=secondary&limit=300",
        "/api/feed?filter=smart", "/api/feed?filter=nope", "/api/feed?since_block=1005", "/api/feed?since_block=0",
        "/api/feed?since_block=999999", "/api/stats", "/api/overview", "/api/market-data"]


def snapshot_of(client, url, headers):
    r = client.get(url, headers=headers)
    return r.status_code, r.headers.get("content-type"), r.headers.get("etag"), r.headers.get("cache-control"), r.content


# ------------------------------------------------------------------------------------------ same bytes

@pytest.mark.parametrize("headers", [GZ, PLAIN])
def test_cached_responses_are_the_uncached_responses(client, monkeypatch, headers):
    """For every cached endpoint: status, content type, ETag, Cache-Control and the (decompressed) body are identical to
    what the code produced before the cache, for clients that accept gzip and for those that do not."""
    seed(client)
    for url in URLS:
        monkeypatch.setattr(api, "BODY_CACHE_ENABLED", False)
        api._current_snapshot = None
        want = snapshot_of(client, url, headers)
        monkeypatch.setattr(api, "BODY_CACHE_ENABLED", True)
        api._current_snapshot = None
        cold = snapshot_of(client, url, headers)          # builds and stores the body
        warm = snapshot_of(client, url, headers)          # served from the cache
        assert want[0] == 200, url
        assert cold == want, url
        assert warm == want, url
    json.loads(want[4])                                   # and it is real JSON


def test_a_gzip_client_gets_the_stored_compressed_bytes_exactly_once_compressed(client):
    seed(client)
    r = client.get("/api/feed?limit=40", headers=GZ)
    assert r.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in [v.strip() for v in r.headers["vary"].split(",")]   # newer Starlette adds Origin too
    assert int(r.headers["content-length"]) < len(r.content) / 3          # the wire bytes are the small ones
    assert json.loads(r.content)["count"] == 40                            # httpx removed exactly one gzip layer
    raw = api.get_feed.__globals__["_current_snapshot"].bodies._entries[("feed", None, 0, 40)][1].gz
    assert gzip.decompress(raw) == r.content and int(r.headers["content-length"]) == len(raw)


def test_a_client_without_gzip_gets_plain_bytes_and_small_bodies_are_never_compressed(client):
    seed(client)
    r = client.get("/api/feed?limit=40", headers=PLAIN)
    assert "content-encoding" not in r.headers and json.loads(r.content)["count"] == 40
    small = client.get("/api/feed?since_block=999999", headers=GZ)         # no new drops: a tiny body
    assert len(small.content) < api.GZIP_MIN_SIZE and "content-encoding" not in small.headers


def test_304s_are_unchanged(client):
    seed(client)
    for url in ("/api/feed?limit=40", "/api/stats", "/api/feed?since_block=999999"):
        first = client.get(url, headers=GZ)
        again = client.get(url, headers={**GZ, "If-None-Match": first.headers["etag"]})
        assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == first.headers["etag"], url


# ------------------------------------------------------------------------------------- when it rebuilds

def test_a_body_is_built_once_per_version_and_rebuilt_when_an_input_changes(client, monkeypatch):
    seed(client)
    builds = []
    real = api.serve_records
    monkeypatch.setattr(api, "serve_records", lambda *a, **k: builds.append(1) or real(*a, **k))
    for _ in range(6):
        client.get("/api/feed?limit=40", headers=GZ)
    assert len(builds) == 1                                                # five repeats cost no re-encoding

    monkeypatch.setattr(monitor, "cached_bnb_price", lambda: 650.0)        # an input of every record's server metrics
    body_new = client.get("/api/feed?limit=40", headers=GZ).content
    assert len(builds) == 2
    monkeypatch.setattr(monitor, "cached_bnb_price", lambda: 600.0)
    body_old = client.get("/api/feed?limit=40", headers=GZ).content
    assert len(builds) == 3 and body_new != body_old                       # never the other price's bytes

    monkeypatch.setattr(monitor, "load_agent_id_stats", lambda: {"total_created_wallets": 178002, "last_block": 6})
    moved = client.get("/api/feed?limit=40", headers=GZ).json()            # the agent-wallet counter moved
    assert moved["stats"]["agent_wallets"] == 178002 and len(builds) == 4

    client.db.upsert_projects([rec(500, token_price_usd=2.0, lp_usd=9000.0)])   # new content version
    after = client.get("/api/feed?limit=40", headers=GZ).json()
    assert after["total"] == 41 and len(builds) == 5


def test_the_pulse_changing_changes_the_body(client, monkeypatch):
    seed(client)
    first = client.get("/api/stats", headers=GZ).json()
    snap, _ = api._load_snapshot()
    snap._pulse = dict(snap._pulse, new_drops=999)                         # what a recomputation with new data gives
    second = client.get("/api/stats", headers=GZ).json()
    assert first["stats"]["pulse_24h"]["new_drops"] != 999 and second["stats"]["pulse_24h"]["new_drops"] == 999


# ----------------------------------------------------------------------------------------- bounded

def test_the_cache_is_bounded_and_keeps_the_most_recent_entries():
    cache = api._BodyCache(max_bytes=4000)
    import os
    big = lambda n: os.urandom(n)                       # incompressible: gzip size ~ plain size
    for i in range(10):
        cache.get_or_build(("k", i), None, lambda i=i: big(1500))
    s = cache.stats()
    assert s["bytes"] <= 4000 and 1 <= s["entries"] <= 2
    assert cache._get(("k", 9), None) is not None and cache._get(("k", 0), None) is None   # LRU: oldest went first
    # a single body bigger than the whole budget is still served (and replaces the rest), never an error
    assert cache.get_or_build(("huge",), None, lambda: big(9000)).gz is not None


def test_entries_are_served_only_for_equal_inputs():
    cache = api._BodyCache(max_bytes=10 ** 6)
    built = []
    mk = lambda tag: (lambda: built.append(tag) or (b"x" * 900 + tag.encode()))
    a = cache.get_or_build(("k",), (1, True), mk("a"))
    assert cache.get_or_build(("k",), (1, True), mk("b")) is a and built == ["a"]
    b = cache.get_or_build(("k",), (2, True), mk("c"))                     # other input: rebuilt, old entry replaced
    assert built == ["a", "c"] and b is not a and cache.stats()["entries"] == 1


def test_a_total_cache_budget_holds_across_many_distinct_pages(client):
    seed(client)
    for offset in range(0, 40, 1):
        for limit in (3, 7):
            client.get(f"/api/feed?limit={limit}&offset={offset}", headers=GZ)
    snap, _ = api._load_snapshot()
    assert snap.bodies.stats()["bytes"] <= api.BODY_CACHE_MAX_BYTES


def test_concurrent_first_requests_for_a_page_build_it_once():
    cache = api._BodyCache(max_bytes=10 ** 6)
    built, gate = [], threading.Event()

    def build():
        built.append(1)
        gate.wait(2)
        return b"y" * 2000

    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get_or_build(("p",), None, build))) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join()
    assert len(built) == 1 and len({id(r) for r in results}) == 1


# ------------------------------------------------------------------- per-version work does not block

def test_building_a_filter_list_does_not_block_other_snapshot_readers(client, monkeypatch):
    seed(client)
    snap, _ = api._load_snapshot()
    started, release = threading.Event(), threading.Event()
    real_pred = api.FEED_FILTERS["smart"]

    def slow(d):
        started.set()
        release.wait(3)
        return real_pred(d)

    monkeypatch.setitem(api.FEED_FILTERS, "smart", slow)
    t = threading.Thread(target=lambda: snap.filtered("smart"))
    t.start()
    assert started.wait(2)
    t0 = time.perf_counter()
    snap.newer_than(1005)                                                  # takes the snapshot lock
    snap.pulse()
    assert time.perf_counter() - t0 < 0.5                                  # not waiting for the 3 s filter build
    release.set()
    t.join()
    assert len(snap.filtered("smart")) > 0


def test_while_one_thread_recomputes_the_pulse_others_get_the_previous_value(client, monkeypatch):
    seed(client)
    snap, _ = api._load_snapshot()
    first = snap.pulse()
    monkeypatch.setattr(api, "PULSE_CACHE_TTL_SEC", 0.0)                   # every call finds the value expired
    started, release = threading.Event(), threading.Event()
    real = api.compute_pulse_24h

    def slow(projects, now=None):
        started.set()
        release.wait(3)
        return real(projects)

    monkeypatch.setattr(api, "compute_pulse_24h", slow)
    out = []
    t = threading.Thread(target=lambda: out.append(snap.pulse()))
    t.start()
    assert started.wait(2)
    t0 = time.perf_counter()
    assert snap.pulse() is first                                           # served at once from the previous value
    assert time.perf_counter() - t0 < 0.5
    release.set()
    t.join()
    assert out[0] is not first and out[0] == first


def test_the_very_first_pulse_is_waited_for_not_skipped(client, monkeypatch):
    seed(client)
    snap, _ = api._load_snapshot()
    snap._pulse = None
    started, release = threading.Event(), threading.Event()
    real = api.compute_pulse_24h
    monkeypatch.setattr(api, "compute_pulse_24h", lambda p, now=None: (started.set(), release.wait(3), real(p))[2])
    results = []
    threads = [threading.Thread(target=lambda: results.append(snap.pulse())) for _ in range(3)]
    for t in threads:
        t.start()
    assert started.wait(2)
    time.sleep(0.2)
    assert results == []                                                   # nobody got a None
    release.set()
    for t in threads:
        t.join()
    assert len(results) == 3 and all(isinstance(r, dict) for r in results)


# ------------------------------------------------------------------------------------------ warmer

def test_warm_builds_the_heavy_parts(client):
    seed(client)
    snap, _ = api._load_snapshot()
    assert snap._by_filter == {} and snap._market_body is None and snap._top_gainers is None and snap._pulse is None
    snap.warm(600.0)
    assert "secondary" in snap._by_filter and snap._market_body is not None and snap._top_gainers is not None
    assert snap._pulse is not None


def test_the_warmer_works_only_while_the_data_endpoints_are_in_use(client, monkeypatch):
    seed(client)
    monkeypatch.setattr(api, "SNAPSHOT_WARM_INTERVAL_SEC", 0.0)
    warmed, ticks = [], []
    monkeypatch.setattr(api._FeedSnapshot, "warm", lambda self, price: warmed.append(self))

    class Stop(Exception):
        pass

    def fake_sleep(s):
        ticks.append(1)
        if len(ticks) > 3:
            raise Stop()

    monkeypatch.setattr(api.time, "sleep", fake_sleep)
    monkeypatch.setattr(api, "_last_data_request", -10 ** 9)             # no data request for ages: idle
    with pytest.raises(Stop):
        api._snapshot_warmer_loop()
    assert warmed == [] and api._current_snapshot is None                  # no Supabase read, no snapshot, nothing built

    ticks.clear()
    monkeypatch.setattr(api.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(api, "_last_data_request", 99.0)                 # a visitor just used the API
    with pytest.raises(Stop):
        api._snapshot_warmer_loop()
    assert len(warmed) == 1                                                # warmed once; the same snapshot is not redone
    assert api._last_data_request == 99.0                                  # and the warmer is not counted as traffic


# ------------------------------------------------------------------------------- sorted record list

def test_the_sorted_list_is_kept_until_the_cache_changes(client):
    d = client.db
    seed(client)
    first = d.get_all_projects()
    blocks = [p["block"] for p in first]
    assert blocks == sorted(blocks, reverse=True)
    held = d._sorted_items
    second = d.get_all_projects()
    assert d._sorted_items is held and second == first and second is not first   # no re-sort; each caller gets its own list
    second.clear()
    second_again = d.get_all_projects()
    assert len(second_again) == len(first)                                  # a caller mutating its list changes nothing

    d.upsert_projects([rec(7, block=5000)])                                 # a record moves to the top
    third = d.get_all_projects()
    assert third[0]["contract"] == f"0x{7:040x}" and d._sorted_items is not held
    d.delete_projects([f"0x{7:040x}"])
    assert f"0x{7:040x}" not in [p["contract"] for p in d.get_all_projects()]
