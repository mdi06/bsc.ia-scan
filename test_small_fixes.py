"""Two small back-end fixes: /healthz answers from the in-memory cache (no Supabase call), and /api/feed?view=list serves
a slimmer record for list views (the Secondary tab). A request without view=list must keep getting today's shape."""
import gzip
import glob
import os
import re

import pytest
from fastapi.testclient import TestClient

import api
import db as db_module
import monitor

GZ = {"Accept-Encoding": "gzip"}


class Resp:
    def __init__(self, code=200, headers=None):
        self.status_code, self.headers = code, headers or {}


class CountingSupabase:
    def __init__(self, count_header="0-0/123", fail=False):
        self.calls, self.count_header, self.fail = [], count_header, fail

    def get(self, path, headers=None, **kw):
        self.calls.append(path)
        if self.fail:
            raise RuntimeError("supabase is down")
        return Resp(206, {"content-range": self.count_header})

    def close(self):
        pass


@pytest.fixture
def manager(tmp_path, monkeypatch):
    for var in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))
    monkeypatch.setattr(db_module, "FAVORITES_FILE", str(tmp_path / "no_favorites.json"))
    m = db_module.DatabaseManager(db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(api, "db", m)
    monkeypatch.setattr(api, "_current_snapshot", None)
    monkeypatch.setattr(api, "RATE_LIMIT_MAX", 10 ** 9)
    monkeypatch.setattr(monitor, "load_agent_id_stats", lambda: {"total_created_wallets": 178001, "last_block": 5})
    monkeypatch.setattr(monitor, "cached_bnb_price", lambda: 600.0)
    monkeypatch.setattr(monitor, "bnb_price_is_live", lambda: True)
    return m


def as_supabase(m, fake):
    m.mode = "supabase"
    m._client_instance, m._client_pid = fake, os.getpid()


# --------------------------------------------------------------------------------------------- /healthz

def test_healthz_answers_from_the_cache_without_a_supabase_call(manager):
    fake = CountingSupabase()
    as_supabase(manager, fake)
    manager._projects_cache = {f"0x{i:040x}": {"contract": f"0x{i:040x}", "block": i} for i in range(1, 8)}
    manager._cache_initialized = True
    body = TestClient(api.app).get("/healthz").json()
    assert body == {"status": "ok", "service": "agent-id-backend", "db_mode": "supabase", "project_count": 7}   # same shape
    assert fake.calls == []                                   # nothing asked of Supabase


def test_healthz_asks_the_database_only_while_the_cache_is_empty(manager):
    fake = CountingSupabase("0-0/123")
    as_supabase(manager, fake)
    assert manager.get_project_count() == 123 and len(fake.calls) == 1          # cold start: the count query, as before
    manager._projects_cache = {"0x" + "1" * 40: {"contract": "0x" + "1" * 40}}
    manager._cache_initialized = True
    assert manager.get_project_count() == 1 and len(fake.calls) == 1            # loaded: no more queries


def test_healthz_survives_an_empty_cache_and_a_database_error(manager):
    as_supabase(manager, CountingSupabase(fail=True))
    assert manager.get_project_count() == 0
    assert TestClient(api.app).get("/healthz").json()["status"] == "ok"


def test_the_sqlite_count_is_unchanged(manager):
    manager.upsert_projects([{"contract": f"0x{i:040x}", "block": i} for i in range(1, 4)])
    assert manager.mode == "sqlite" and manager.get_project_count() == 3


# ---------------------------------------------------------------------------------------- view=list

def full_record(i, **kw):
    d = {"contract": f"0x{i:040x}", "name": f"Token {i}", "symbol": f"T{i}", "standard": "BEP-20 Token (18 decimals)",
         "block": 5000 - i, "detected_at": "2026-11-12 09:00:00", "token_price_usd": 0.5, "lp_usd": 9000.0 + i,
         "market_cap": 50000.0 + i, "volume_24h": 800.0, "holders": 40, "score": 55,
         "pair_url": "https://dexscreener.com/bsc/0xabc", "market_url": "https://pancakeswap.finance/swap",
         "smart_wallets": ["a.ai", "b.ai"], "smart_minters": ["a.ai", "b.ai"], "signals": ["four.meme"],
         "risk_level": "SAFE", "risk_badge": "🟢 Clean", "sniper_ready": True,
         # what only the modal reads, or nothing reads
         "sniper_checklist": [{"label": f"check {n}", "ok": True, "detail": "x" * 40} for n in range(8)],
         "tx_hash": "0x" + "ab" * 32, "token_price_bnb": 0.0008, "sniper_status": "READY",
         "risk_flags": ["🟡 Mint is open", "🟡 Owner can pause"], "hold": {"dev": 3, "smart": 1},
         "holder_distribution": {"dev": 3, "smart": 1, "public": 96}, "dev_root": "0x" + "cd" * 20,
         "mint_calldata": "0x1249c58b0000000000000000000000000000000000000000000000000000000000000001",
         "security": {"has_refund": True, "is_serial_dev": False, "is_fund_drain": False, "is_fresh_dev": True,
                      "serial_count": 2, "creator_percent": 0.03, "cluster_devs": 1, "cluster_n": 2,
                      "refund_methods": ["refund()"], "audited": True, "buy_tax": 0, "sell_tax": 0, "top10_pct": 31.5,
                      "risk_badge": "🟢 Clean", "risk_flags": ["x"], "is_honeypot": False, "lp_locked_pct": 100,
                      "is_open_source": True, "cannot_sell_all": False, "hold": {"dev": 3}, "dev_root": "0xcd",
                      "holder_distribution": {"dev": 3}}}
    d.update(kw)
    return d


@pytest.fixture
def client(manager):
    rows = [full_record(i) for i in range(1, 41)]
    rows[0]["mint_calldata"] = "0x"                     # a receive-style mint: nothing to load later
    rows[1].pop("mint_calldata")                        # a record without calldata
    rows[2]["security"] = None                          # and one without a security report
    manager.upsert_projects(rows)
    c = TestClient(api.app)
    c.db = manager
    return c


def feed(client, query="", headers=GZ):
    r = client.get("/api/feed?" + query, headers=headers)
    assert r.status_code == 200, query
    return r


def by_contract(body):
    return {p["contract"]: p for p in body["projects"]}


def test_the_list_view_drops_what_only_the_modal_reads_and_keeps_the_rest(client):
    full = by_contract(feed(client, "limit=40").json())
    slim = by_contract(feed(client, "limit=40&view=list").json())
    assert set(full) == set(slim)
    for c, p in slim.items():
        f = full[c]
        for gone in api.LIST_VIEW_DROP:
            assert gone not in p, gone
        assert p["_slim"] is True and "_slim" not in f
        extra = set(p) - set(f)
        assert extra <= {"_slim", "has_mint_calldata"}
        for k, v in p.items():                                     # whatever is kept has the same value
            if k not in ("_slim", "has_mint_calldata", "security", "mint_calldata"):
                assert v == f[k], k
        if isinstance(f.get("security"), dict):
            assert set(p["security"]) <= api.LIST_VIEW_SECURITY_KEEP
            assert all(p["security"][k] == f["security"][k] for k in p["security"])
    first = slim[f"0x{4:040x}"]
    assert first["has_mint_calldata"] is True and "mint_calldata" not in first           # loaded on demand
    assert set(first["security"]) <= {"has_refund", "refund_methods", "is_serial_dev", "is_fund_drain", "is_fresh_dev", "serial_count",
                                      "is_honeypot", "cannot_sell_all", "sell_tax"}   # the last three: profit per mint
    assert {"has_refund", "refund_methods", "is_serial_dev", "is_fund_drain", "is_fresh_dev", "serial_count"} <= set(first["security"])
    assert slim[f"0x{3:040x}"]["security"] is None                                       # no report stays none
    assert slim[f"0x{1:040x}"]["mint_calldata"] == "0x" and "has_mint_calldata" not in slim[f"0x{1:040x}"]
    assert "mint_calldata" not in slim[f"0x{2:040x}"] and "has_mint_calldata" not in slim[f"0x{2:040x}"]
    assert first["smart_wallets"] == ["a.ai", "b.ai"] and first["smart_minters"] == ["a.ai", "b.ai"]


def test_without_view_list_the_shape_is_unchanged(client):
    plain = feed(client, "limit=40")
    for q in ("limit=40&view=bogus", "limit=40&view=", "limit=40&view=LIST"):
        assert feed(client, q).content == plain.content, q                     # an unknown view is ignored
    body = plain.json()
    assert "view" not in body and all("_slim" not in p for p in body["projects"])
    p = by_contract(body)[f"0x{4:040x}"]
    assert p["sniper_checklist"] and p["security"]["buy_tax"] == 0 and p["holder_distribution"] and p["risk_flags"]
    assert p["mint_calldata"].startswith("0x1249c58b")


def test_the_list_view_is_marked_and_has_its_own_etag_and_cache_entry(client):
    plain, slim = feed(client, "limit=40"), feed(client, "limit=40&view=list")
    assert slim.json()["view"] == "list"
    assert plain.headers["etag"] != slim.headers["etag"] and slim.headers["etag"].endswith('-list"')
    again = client.get("/api/feed?limit=40&view=list", headers={**GZ, "If-None-Match": slim.headers["etag"]})
    assert again.status_code == 304
    crossed = client.get("/api/feed?limit=40", headers={**GZ, "If-None-Match": slim.headers["etag"]})
    assert crossed.status_code == 200 and crossed.content == plain.content     # a list-view ETag is not the full page's
    assert feed(client, "limit=40").content == plain.content                    # the cached list body did not leak into it


def test_the_list_view_works_with_filters_offsets_and_the_delta_poll_keeps_full_records(client):
    s = feed(client, "filter=secondary&limit=10&offset=5&view=list").json()
    assert s["count"] == 10 and s["offset"] == 5 and all(p["_slim"] for p in s["projects"])
    delta = feed(client, "since_block=4980&view=list").json()                   # new drops are always sent whole
    assert delta["mode"] == "delta" and delta["count"] > 0
    assert all("_slim" not in p and "sniper_checklist" in p for p in delta["projects"])


def test_the_stored_records_are_not_changed_and_the_body_is_much_smaller(client):
    before = {c: dict(p) for c, p in client.db._projects_cache.items()}
    full = feed(client, "limit=40", {"Accept-Encoding": "identity"}).content
    slim = feed(client, "limit=40&view=list", {"Accept-Encoding": "identity"}).content
    assert {c: dict(p) for c, p in client.db._projects_cache.items()} == before
    assert len(gzip.compress(slim)) < 0.8 * len(gzip.compress(full))     # identical synthetic records compress unusually well
    assert len(slim) < 0.6 * len(full)


# ------------------------------------------------- the frontend does not read what the list view leaves out

def top_level_owner_lines(src):
    """[(enclosing top-level function, line)] for every line of app.js (and of the feature modules app.js loads)."""
    owner, out = None, []
    for line in src.split("\n"):
        m = re.match(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_]+)\s*\(", line)
        if m:
            owner = m.group(1)
        out.append((owner, line))
    return out


MODAL_ONLY = {"openModal", "computeHolderDistribution"}


def test_nothing_outside_the_modal_reads_a_field_the_list_view_drops():
    """If a table row, filter or Pulse card ever starts reading one of these, a slim record would silently lack it:
    this fails first. (They are read only by the token modal; the page loads the full record when it opens.)"""
    here = os.path.dirname(os.path.abspath(__file__))
    files = [os.path.join(here, "app.js")] + sorted(glob.glob(os.path.join(here, "features", "**", "*.js"), recursive=True))
    src = "\n".join(open(f, encoding="utf-8").read() for f in files)
    lines = top_level_owner_lines(src)
    for field in api.LIST_VIEW_DROP:
        pat = re.compile(r"(\.|\[['\"])" + re.escape(field) + r"\b")
        readers = {owner for owner, line in lines if pat.search(line)}
        assert readers <= MODAL_ONLY, (field, readers - MODAL_ONLY)
    sec_pat = re.compile(r"\b(?:security|sec)\.([A-Za-z_]+)")
    for owner, line in lines:
        if owner in MODAL_ONLY:
            continue
        for key in sec_pat.findall(line):
            assert key in api.LIST_VIEW_SECURITY_KEEP, (owner, key)


# ------------------------------------------------- tokens that are not agent mints never reach the browser

NON_AGENT_TOKENS = {
    "0xb9e1fd5a02d3a33b25a14d661414e6ed6954a721": ("SOON Token", "SOON"),   # added via a smart wallet's transfer
    "0x2f8a339b5889ffac4c5a956787cda593b3c36867": ("CoinMarketCap 20 Index DTF", "CMC20"),
}


def test_soon_and_cmc20_are_dropped_from_feed_secondary_and_search(manager):
    rows = [full_record(i) for i in range(1, 6)]
    for n, (contract, (name, sym)) in enumerate(NON_AGENT_TOKENS.items()):
        rows.append(full_record(100 + n, contract=contract, name=name, symbol=sym, lp_usd=550_000.0, holders=20_000,
                                pair_url="https://dexscreener.com/bsc/0x" + "ee" * 20, secondary_price_usd=0.5))
    manager.upsert_projects(rows)
    client = TestClient(api.app)
    for path in ("/api/feed?limit=150", "/api/feed?filter=secondary&limit=300", "/api/feed?view=list&limit=150",
                 "/api/projects?search=SOON", "/api/projects?search=CMC20",
                 *(f"/api/projects?search={c}" for c in NON_AGENT_TOKENS)):
        r = client.get(path)
        assert r.status_code == 200, path
        body = r.json()
        items = body if isinstance(body, list) else body.get("projects", [])
        found = {str(p.get("contract", "")).lower() for p in items} & set(NON_AGENT_TOKENS)
        assert not found, (path, found)
