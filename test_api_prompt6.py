"""Metrics audit, Prompt 6: live top gainers and labelled seeds (M9), /api/projects filters, NaN safety, ETags."""
import json
import math
import os

import pytest
from fastapi.testclient import TestClient

import api
import db as db_module
import monitor

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def client(tmp_path, monkeypatch):
    for var in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))
    monkeypatch.setattr(db_module, "FAVORITES_FILE", str(tmp_path / "no_favorites.json"))
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(api, "db", manager)
    monkeypatch.setattr(monitor, "cached_bnb_price", lambda: 700.0)
    monkeypatch.setattr(monitor, "refresh_bnb_price", lambda *a, **k: 700.0)
    monkeypatch.setattr(monitor, "bnb_price_is_live", lambda: True)
    c = TestClient(api.app)
    c.db = manager
    return c


def rec(n, **kw):
    d = {"contract": f"0x{n:040x}", "name": f"Token {n}", "symbol": f"T{n}", "standard": "BEP-20 Token (18 decimals)",
         "block": 1000 + n, "detected_at": "2026-11-12 09:00:00", "token_price_usd": 0.0, "lp_usd": 0.0}
    d.update(kw)
    return d


def paid(n, price, lp=20000.0, mint_bnb=0.01, **kw):
    return rec(n, token_price_usd=price, lp_usd=lp, mint_price_bnb=mint_bnb, mint_value_wei=str(int(mint_bnb * 1e18)),
               mint_price_str=f"{mint_bnb:.4f} BNB", **kw)


# ----------------------------------------------------------------------------------------- M9: top gainers
def test_top_gainers_are_computed_live_and_ranked_by_ath_multiplier(client):
    client.db.upsert_projects([
        paid(1, 14.1),                                   # 14.1 / 7.0 = 2.01x
        paid(2, 35.25),                                  # 5.04x
        rec(3, token_price_usd=0.3, lp_usd=4000.0, has_free_mint=True, mint_price_str="FREE"),   # free: no multiplier
        rec(4, token_price_usd=69.65, lp_usd=13000.0, mint_price_str="DEX Traded"),              # no mint position
        rec(5, token_price_usd=0.001, lp_usd=3000.0, mint_price_str="TBD", mint_calldata="0x1249c58b"),  # unknown cost
        paid(6, 2.0),                                    # under water: below its mint cost
        paid(7, 40.0, lp=300.0),                         # thin pool: no trustworthy ATH
        rec(8, symbol="SOL", token_price_usd=150.0, lp_usd=9e6, has_free_mint=True),             # excluded asset
    ])
    body = client.get("/api/top-gainers").json()
    assert body["seeded"] is False and isinstance(body["as_of"], int)
    names = [g["name"] for g in body["gainers"]]
    assert names == ["Token 2", "Token 1", "Token 3"]
    assert [g["multiplier"] for g in body["gainers"]] == [5.04, 2.01, None]
    assert body["gainers"][2]["isFree"] is True and body["gainers"][0]["isFree"] is False
    assert body["gainers"][0]["item"]["mint_price_str"] == "0.0100 BNB"
    assert body["count"] == 3


def test_top_gainers_is_no_longer_the_seed_file(client):
    client.db.upsert_projects([paid(1, 14.1)])
    live = client.get("/api/top-gainers").json()
    seed = json.load(open(os.path.join(HERE, "seeded_top_gainers.json")))
    assert live["gainers"] != seed and "Token 1" == live["gainers"][0]["name"]


def test_top_gainers_fall_back_to_the_labelled_seed_when_nothing_qualifies(client):
    client.db.upsert_projects([rec(1, mint_price_str="DEX Traded", token_price_usd=1.0, lp_usd=9000.0)])
    body = client.get("/api/top-gainers").json()
    seed = json.load(open(os.path.join(HERE, "seeded_top_gainers.json")))
    assert body["seeded"] is True and body["gainers"] == seed
    assert body["as_of"] == int(os.path.getmtime(os.path.join(HERE, "seeded_top_gainers.json")))


def test_smart_hunters_and_drops_are_labelled_seeds(client):
    hunters_seed = json.load(open(os.path.join(HERE, "seeded_smart_hunters.json")))
    drops_seed = json.load(open(os.path.join(HERE, "seeded_smart_drops.json")))
    h = client.get("/api/smart-hunters").json()
    assert h["seeded"] is True and isinstance(h["as_of"], int)
    assert h["items"] == hunters_seed and h["hunters"] == hunters_seed       # `hunters` kept for older clients
    d = client.get("/api/smart-drops").json()
    assert d["seeded"] is True and isinstance(d["as_of"], int) and d["items"] == drops_seed and d["count"] == len(drops_seed)
    legacy = client.get("/api/smart-drops?shape=array").json()               # the compatibility path: the old bare array
    assert legacy == drops_seed


# ----------------------------------------------------------------------------------------- /api/projects
def test_projects_status_and_min_progress_filters_work_on_records_without_those_keys(client):
    client.db.upsert_projects([
        rec(1, max_supply=1000, total_minted=1000),                       # sold out
        rec(2, max_supply=1000, total_minted=400),                        # minting
        rec(3, max_supply=1e30, total_minted=5),                          # uncapped sentinel: progress unknown
        rec(4, token_price_usd=0.5, lp_usd=9000.0),                       # trades
        rec(5),                                                           # nothing known
    ])
    one = client.get("/api/projects?min_progress=1").json()               # used to be total 0
    assert one["total"] == 2 and {p["name"] for p in one["projects"]} == {"Token 1", "Token 2"}
    assert [p["progress"] for p in sorted(one["projects"], key=lambda p: p["name"])] == [100.0, 40.0]
    by = {s: client.get(f"/api/projects?status={s}").json() for s in ("SOLD_OUT", "MINTING", "TRADING", "TRACKING")}
    assert [p["name"] for p in by["SOLD_OUT"]["projects"]] == ["Token 1"]
    assert [p["name"] for p in by["MINTING"]["projects"]] == ["Token 2"]
    assert [p["name"] for p in by["TRADING"]["projects"]] == ["Token 4"]
    assert {p["name"] for p in by["TRACKING"]["projects"]} == {"Token 3", "Token 5"}
    ranked = client.get("/api/projects?sort_by=progress&order=desc").json()["projects"]
    assert [p["name"] for p in ranked][:2] == ["Token 1", "Token 2"]       # unknown progress sorts last


def test_projects_total_and_count_are_computed_after_the_bogus_filter(client):
    client.db.upsert_projects([rec(1), rec(2), rec(3, symbol="SOL"), rec(4, contract="0xd0523ce816e2b404db5633d181572cded33d7fa9")])
    body = client.get("/api/projects?limit=10").json()
    assert body["total"] == 2 and body["count"] == 2 and len(body["projects"]) == 2   # was total 4 / count 4
    paged = client.get("/api/projects?limit=1&offset=1").json()
    assert paged["total"] == 2 and paged["count"] == 1


def test_an_explicit_stored_status_is_kept(client):
    client.db.upsert_projects([rec(1, status="PAUSED", max_supply=10, total_minted=10)])
    assert client.get("/api/projects?status=paused").json()["total"] == 1


# ----------------------------------------------------------------------------------------- NaN / Infinity
def poison_cache(client, contract_suffix):
    """Puts non-finite floats into the in-memory records, as a database read can deliver them (a SQLite raw_data
    value written by json.dumps, or a Supabase row)."""
    for c, p in client.db._projects_cache.items():
        if c.endswith(contract_suffix):
            p.update(token_price_usd=float("nan"), fdv=float("inf"), lp_usd=float("-inf"))
    client.db._cache_dirty = True
    client.db._content_version += 1


def test_one_non_finite_float_does_not_break_the_feed_or_projects(client):
    client.db.upsert_projects([rec(1), rec(2), rec(3)])
    poison_cache(client, "02")
    feed = client.get("/api/feed?limit=10")
    assert feed.status_code == 200                                         # was HTTP 500 for the whole page
    bad = next(p for p in feed.json()["projects"] if p["contract"].endswith("02"))
    assert bad["token_price_usd"] is None and bad["fdv"] is None and bad["lp_usd"] is None
    assert len(feed.json()["projects"]) == 3
    proj = client.get("/api/projects")
    assert proj.status_code == 200 and len(proj.json()["projects"]) == 3
    assert client.get("/api/feed?since_block=0").status_code == 200
    assert client.get("/api/feed?filter=secondary").status_code == 200


def test_history_json_is_valid_json_with_non_finite_values(client):
    client.db.upsert_projects([rec(1), rec(2)])
    poison_cache(client, "01")
    res = client.get("/history.json")
    assert res.status_code == 200

    def refuse(token):
        raise AssertionError(f"non-JSON token {token} in /history.json")
    data = json.loads(res.content, parse_constant=refuse)              # strict: NaN / Infinity are rejected
    assert len(data) == 2 and all(math.isfinite(v) for d in data for v in d.values() if isinstance(v, float))
    # a second request (cached sanitized bytes) is identical
    assert client.get("/history.json").content == res.content


def test_upsert_scrubs_non_finite_floats_instead_of_failing_the_batch(client):
    """db._compute_fingerprint used to raise OverflowError on inf, losing every record of the batch."""
    client.db.upsert_projects([rec(1), rec(2, token_price_usd=float("nan"), fdv=float("inf"), lp_usd=float("-inf"),
                                          security={"x": [float("nan")]}), rec(3)])
    stored = client.db.get_project(f"0x{2:040x}")
    assert stored["token_price_usd"] is None and stored["fdv"] is None and stored["lp_usd"] is None
    assert stored["security"]["x"] == [None]
    assert client.db.get_project(f"0x{1:040x}") and client.db.get_project(f"0x{3:040x}")


def test_sanitize_non_finite_is_deep():
    out = api.sanitize_non_finite({"a": [1.0, float("nan"), {"b": float("-inf")}], "c": (float("inf"), 2)})
    assert out == {"a": [1.0, None, {"b": None}], "c": [None, 2]}


# ----------------------------------------------------------------------------------------- ETags
def test_feed_etag_follows_the_content_not_just_counts_and_blocks(client):
    client.db.upsert_projects([rec(1, token_price_usd=1.0, lp_usd=9000.0), rec(2)])
    first = client.get("/api/feed?limit=10")
    etag = first.headers["etag"]
    # the same request with the same ETag is a 304 while nothing changed
    assert client.get("/api/feed?limit=10", headers={"If-None-Match": etag}).status_code == 304
    # a price change keeps the block, the total and the agent-wallet counter identical: the old ETag stayed valid
    client.db.upsert_projects([rec(1, token_price_usd=50.0, lp_usd=9000.0)])
    changed = client.get("/api/feed?limit=10", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag
    assert next(p for p in changed.json()["projects"] if p["contract"].endswith("01"))["token_price_usd"] == 50.0


def test_delta_and_stats_etags_follow_the_content_too(client):
    client.db.upsert_projects([rec(1)])
    d1 = client.get("/api/feed?since_block=9999999")
    s1 = client.get("/api/stats")
    client.db.upsert_projects([rec(1, token_price_usd=7.0, lp_usd=9000.0)])
    assert client.get("/api/feed?since_block=9999999", headers={"If-None-Match": d1.headers["etag"]}).headers["etag"] != d1.headers["etag"]
    assert client.get("/api/stats", headers={"If-None-Match": s1.headers["etag"]}).headers["etag"] != s1.headers["etag"]


def test_content_version_counts_changes_and_deletes(client):
    v0 = client.db.content_version()
    client.db.upsert_projects([rec(1)])
    v1 = client.db.content_version()
    assert v1 > v0
    client.db.upsert_projects([rec(1)])                                     # identical: nothing changed
    assert client.db.content_version() == v1
    client.db.delete_projects([f"0x{1:040x}"])
    assert client.db.content_version() > v1
