"""/api/projects no longer derives progress / status for every record on every call: a full contract address is looked up
directly, and everything else works on structures built once per content version (api._FeedSnapshot.search_index,
project_rows, sort_keys). The results must stay exactly what the old code returned; `reference_projects` below is the
old implementation, kept here as the oracle."""
import itertools
import random
import threading

import pytest
from fastapi.testclient import TestClient

import api
import db as db_module
import filters
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
    monkeypatch.setattr(monitor, "cached_bnb_price", lambda: 600.0)
    c = TestClient(api.app)
    c.db = manager
    yield c
    api._rate_counts.clear()      # this file makes hundreds of requests: do not leave them on the next test's rate limit


def contract(n):
    return f"0x{n:040x}"


ADDR = contract(0xABCDEF)            # a record whose address is searched for
ADDR_IN_NAME = contract(0xBEEF)      # an address that appears inside another record's name


def records():
    rng = random.Random(7)
    rows = []
    for i in range(1, 61):
        d = {"contract": contract(i), "name": f"Agent {i}" if i % 3 else f"Dog {i}", "symbol": f"T{i % 9}",
             "standard": "ERC-721 (NFT Collection)" if i % 4 == 0 else "BEP-20 Token (18 decimals)",
             "block": 5000 - (i // 3),                      # many equal blocks: the order of ties must be kept
             "detected_at": "2026-11-12 09:00:00", "holders": rng.choice([0, 1, 5, 5, 40, 40, 900]),
             "score": rng.choice([0, 10, 10, 55, 80.5]), "token_price_usd": 0.0, "lp_usd": 0.0}
        if i % 5 == 0:
            d.update(total_minted=i * 10, max_supply=1000)                   # MINTING
        if i % 11 == 0:
            d.update(total_minted=1000, max_supply=1000)                     # SOLD_OUT
        if i % 6 == 0:
            d.update(token_price_usd=0.5, lp_usd=2000.0, market_cap=9000.0, pair_url="https://dexscreener.com/bsc/0x1")
        if i % 7 == 0:
            d.update(sniper_ready=True)
        if i == 13:
            d.update(status="WATCHING")                                      # an explicit stored status is kept
        if i == 14:
            d.update(name=None, symbol=None)                                 # null search fields
        if i == 15:
            d.update(max_supply=1.2e30, total_minted=5)                      # the "uncapped" sentinel
        if i == 16:
            d.update(progress=42.5)                                          # a stored progress
        rows.append(d)
    rows.append({"contract": ADDR, "name": "Findme", "symbol": "FIND", "block": 4990, "standard": "BEP-20 Token (18 decimals)",
                 "total_minted": 300, "max_supply": 1000, "holders": 5, "score": 10})
    rows.append({"contract": ADDR_IN_NAME, "name": "Plain", "symbol": "PL", "block": 4991, "holders": 1})
    rows.append({"contract": contract(0xC0FFEE), "name": f"claim {ADDR_IN_NAME.upper()} now", "symbol": "SCAM", "block": 4992})
    rows.append({"contract": contract(0x5011), "name": "Stock", "symbol": "SOL", "block": 4993})   # an excluded asset
    return rows


def reference_projects(raw, limit=50, offset=0, search=None, status=None, sniper_ready=None, min_progress=None,
                       sort_by="block", order="desc"):
    """The implementation this change replaced (api.get_projects before): derive every record, then filter, sort, page."""
    projs = [api.with_derived_fields(p) for p in raw if isinstance(p, dict) and not api.is_bogus_or_synthetic_asset(p)]
    if search:
        q = search.lower().strip()
        projs = [p for p in projs if q in (p.get("name") or "").lower() or q in (p.get("symbol") or "").lower()
                 or q in (p.get("contract") or "").lower()]
    if status:
        st = status.lower().strip()
        projs = [p for p in projs if str(p.get("status") or "").lower() == st]
    if sniper_ready is not None:
        projs = [p for p in projs if bool(p.get("sniper_ready")) == sniper_ready]
    if min_progress is not None:
        projs = [p for p in projs if p["progress"] is not None and p["progress"] >= min_progress]
    reverse = order.lower() == "desc"
    if sort_by == "progress":
        known = sorted([p for p in projs if p["progress"] is not None], key=lambda x: x["progress"], reverse=reverse)
        projs = known + [p for p in projs if p["progress"] is None]
    elif sort_by == "holders":
        projs.sort(key=lambda x: int(filters._num(x.get("holders"))), reverse=reverse)
    elif sort_by == "score":
        projs.sort(key=lambda x: filters._num(x.get("score")), reverse=reverse)
    else:
        projs.sort(key=lambda x: int(filters._num(x.get("block"))), reverse=reverse)
    total = len(projs)
    page = api.serve_records(projs[offset: offset + limit], slim=False)
    return {"success": True, "total": total, "count": len(page), "offset": offset, "limit": limit, "projects": page}


def query_string(**params):
    return "&".join(f"{k}={v}" for k, v in params.items() if v is not None)


def same(client, **params):
    ref = {k: ((v == "true") if k == "sniper_ready" and isinstance(v, str) else v) for k, v in params.items()}   # the query string says "true"
    want = reference_projects(client.db.get_all_projects(), **ref)
    got = client.get("/api/projects?" + query_string(**params))
    assert got.status_code == 200, params
    assert got.json() == want, params
    return got.json()


# ------------------------------------------------------------------------------------- same results

SEARCHES = [None, "agent", "AGENT ", "  dog", "t3", "find", "zzzz", "0x", ADDR, ADDR.upper(), f" {ADDR} ",
            ADDR_IN_NAME, ADDR_IN_NAME.upper(), contract(0x99999), contract(0x5011), "sol"]


def test_address_lookups_match_the_old_results(client):
    client.db.upsert_projects(records())
    for q in (ADDR, ADDR.upper(), f"  {ADDR}  ", ADDR_IN_NAME, contract(0x77777), contract(0x5011), contract(3)):
        for extra in ({}, {"status": "MINTING"}, {"status": "TRACKING"}, {"sort_by": "score"}, {"sort_by": "progress", "order": "asc"},
                      {"min_progress": 10}, {"sniper_ready": "true"}, {"offset": 1}, {"limit": 1}):
            same(client, search=q, **extra)
    found = same(client, search=ADDR, limit=5)
    assert found["total"] == 1 and found["projects"][0]["name"] == "Findme" and found["projects"][0]["progress"] == 30.0
    assert found["projects"][0]["status"] == "MINTING"
    both = same(client, search=ADDR_IN_NAME)
    assert [p["name"] for p in both["projects"]] == [f"claim {ADDR_IN_NAME.upper()} now", "Plain"]   # newest block first, as before


def test_a_random_sample_of_every_parameter_combination_matches_the_old_results(client):
    client.db.upsert_projects(records())
    rng = random.Random(3)
    space = list(itertools.product(
        SEARCHES, [None, "TRADING", "minting", "SOLD_OUT", "tracking", "WATCHING", "nope"], [None, "true", "false"],
        [None, 0, 30, 50, 100], ["block", "progress", "holders", "score", "bogus"], ["desc", "asc"],
        [(50, 0), (5, 0), (7, 3), (3, 1000)]))
    for search, status, sniper, minp, sort_by, order, (limit, offset) in rng.sample(space, 500):
        same(client, search=search, status=status, sniper_ready=sniper, min_progress=minp, sort_by=sort_by, order=order,
             limit=limit, offset=offset)
    for sort_by, order in itertools.product(["block", "progress", "holders", "score"], ["desc", "asc"]):   # and every sort, plainly
        same(client, sort_by=sort_by, order=order, limit=500)


def test_results_follow_a_data_change(client):
    client.db.upsert_projects(records())
    same(client, search="agent")
    same(client, search=ADDR)
    client.db.upsert_projects([{"contract": contract(0x4242), "name": "Agent New", "symbol": "NEW", "block": 9000, "total_minted": 10,
                                "max_supply": 10}])
    got = same(client, search="agent", limit=3)
    assert got["projects"][0]["name"] == "Agent New" and got["projects"][0]["status"] == "SOLD_OUT"
    client.db.upsert_projects([{**[r for r in records() if r["contract"] == ADDR][0], "total_minted": 900}])
    assert same(client, search=ADDR)["projects"][0]["progress"] == 90.0
    client.db.delete_projects([ADDR])
    assert same(client, search=ADDR)["total"] == 0


def test_the_response_keeps_its_shape_and_the_stored_records_are_untouched(client):
    client.db.upsert_projects(records())
    before = {c: dict(p) for c, p in client.db._projects_cache.items()}
    body = client.get(f"/api/projects?search={ADDR}&limit=5").json()
    assert set(body) == {"success", "total", "count", "offset", "limit", "projects"}
    assert {c: dict(p) for c, p in client.db._projects_cache.items()} == before     # no progress / status written back


# ------------------------------------------------------------------------------------ the work saved

def count_derivations(monkeypatch):
    """Counts derive_status calls: exactly one per record whose fields are derived."""
    calls = {"progress": 0}
    real = api.derive_status

    def counting(p, progress=None):
        calls["progress"] += 1
        return real(p, progress)

    monkeypatch.setattr(api, "derive_status", counting)
    return calls


def test_an_address_lookup_derives_only_the_records_it_matches(client, monkeypatch):
    client.db.upsert_projects(records())
    calls = count_derivations(monkeypatch)
    body = client.get(f"/api/projects?search={ADDR}&limit=5").json()
    assert body["total"] == 1
    assert calls["progress"] <= 2                  # the one match (before: all 63 records, on every call)
    calls["progress"] = 0
    client.get(f"/api/projects?search={ADDR_IN_NAME}").json()          # also matches a record by its name
    assert calls["progress"] <= 4


def test_derived_fields_are_computed_once_per_version_not_once_per_call(client, monkeypatch):
    client.db.upsert_projects(records())
    n = len(api._load_snapshot()[0].projects)
    calls = count_derivations(monkeypatch)
    for qs in ("limit=5", "status=TRADING", "sort_by=score", "min_progress=10", "search=agent&sort_by=progress"):
        client.get("/api/projects?" + qs)
    assert calls["progress"] == n                  # once for the version (the page's own copies need no more)
    client.db.upsert_projects([{"contract": contract(0x4343), "name": "x", "symbol": "x", "block": 1}])
    calls["progress"] = 0
    client.get("/api/projects?limit=5")
    assert calls["progress"] == n + 1              # a new version builds its own


def test_concurrent_first_calls_build_the_structures_once(client, monkeypatch):
    client.db.upsert_projects(records())
    n = len(api._load_snapshot()[0].projects)
    calls = count_derivations(monkeypatch)
    results = []
    threads = [threading.Thread(target=lambda: results.append(client.get("/api/projects?limit=5&status=TRADING").json()))
               for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls["progress"] == n and all(r == results[0] for r in results)


def test_the_projects_endpoint_uses_the_cached_snapshot_not_the_database_each_time(client, monkeypatch):
    client.db.upsert_projects(records())
    client.get("/api/projects?limit=5")
    seen = []
    real = api._FeedSnapshot.__init__
    monkeypatch.setattr(api._FeedSnapshot, "__init__", lambda self, *a, **k: (seen.append(1), real(self, *a, **k))[1])
    for _ in range(4):
        client.get(f"/api/projects?search={ADDR}")
    assert seen == []                              # no snapshot rebuilt: nothing changed


def test_the_warmer_builds_the_search_index_so_the_first_lookup_of_a_version_is_cheap(client):
    client.db.upsert_projects(records())
    snap, _ = api._load_snapshot()
    assert snap._search_index is None and snap._rows is None
    snap.warm(600.0)
    assert snap._search_index is not None and snap._rows is None       # the index only: the derived rows are for list queries
    names, symbols, contracts, positions = snap.search_index()
    assert len(names) == len(snap.projects) and positions[ADDR.lower()] == [snap.projects.index(
        [p for p in snap.projects if p["contract"] == ADDR][0])]
