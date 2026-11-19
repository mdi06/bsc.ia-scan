"""The API's record cache keeps cold fields (security report, checklist, long mint calldata, ...) as a compressed blob and
decodes them only when a response needs them (compact.py). What must hold:
  * a compact record is, to every caller, the same dict as the plain one (fields, key order, JSON);
  * every endpoint answers byte for byte as it did with plain records;
  * a list response decodes only the records it returns, and opening a modal costs no database request;
  * the full table load holds one page of raw rows at a time, and the records take a fraction of the memory."""
import gc
import json
import os
import time
import tracemalloc

import pytest
from fastapi.testclient import TestClient

import api
import compact
import db as db_module
import monitor
from compact import CompactRecord, compact_record
from db import DatabaseManager
from test_small_fixes import full_record
from test_supabase_loader import FakeSupabase

H = {"Accept-Encoding": "identity"}
NOW = 1_795_095_000.0


def calldata(n_words, seed=1):
    out, x = ["0x1249c58b"], seed
    for i in range(n_words):
        x = (x * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        out.append(f"{x:016x}" * 4 if i % 2 else f"{x % 65536:064x}")
    return "".join(out)


def record(i, **kw):
    r = full_record(i)
    r.update({"icon": "🟢", "rating": "A", "sniper_badge": "READY", "is_paused": False, "cluster_n": 2, "cluster_devs": 1,
              "mint_cost_usd": 1.5, "lp_wbnb": 3.2, "nft_surge_signal": None, "classification": {"category": "TOKEN"}})
    r.update(kw)
    return r


def long_calldata_record(i):
    return record(i, mint_calldata=calldata(60, seed=i))          # ~3.9 KB: above the 512-character list limit


# ------------------------------------------------------------------------------------------------ the record type

def test_a_compact_record_is_the_same_dict_to_every_caller():
    plain = long_calldata_record(7)
    c = compact_record(plain)
    assert type(c) is CompactRecord and plain["security"] and plain["mint_calldata"]
    assert c == plain and plain == c and not (c != plain)
    assert list(c) == list(plain) and list(c.keys()) == list(plain)         # the original key order
    assert list(c.items()) == list(plain.items()) and list(c.values()) == list(plain.values())
    assert json.dumps(c) == json.dumps(plain)                               # what every response is built from
    assert dict(c) == plain and list(dict(c)) == list(plain) and c.full() == plain and c.copy() == plain
    assert len(c) == len(plain) and bool(c)
    for k in plain:
        assert k in c and c[k] == plain[k] and c.get(k) == plain[k]
    assert "nope" not in c and c.get("nope") is None and c.get("nope", 5) == 5
    with pytest.raises(KeyError):
        c["nope"]
    assert c.get("security") is not plain["security"] and c.get("security") == plain["security"]


def test_hot_fields_never_decode_and_cold_ones_decode_on_demand():
    c = compact_record(long_calldata_record(3))
    before = compact.DECODES
    for k in ("contract", "name", "block", "lp_usd", "smart_wallets", "market_cap", "sniper_ready", "risk_level"):
        c.get(k)
        c[k]
        k in c
    c.get("a_field_no_record_has")
    "another" in c
    bool(c)
    c.hot()
    c.hot_get("security")
    assert compact.DECODES == before                                          # nothing above touched the blob
    assert c.hot_get("security") is None and c.hot_get("name") == "Token 3"
    assert c.get("security")["buy_tax"] == 0 and compact.DECODES == before + 1
    assert c["sniper_checklist"] and "mint_calldata" in c


def test_what_is_cold_and_what_stays_hot():
    short, long = compact_record(record(1)), compact_record(long_calldata_record(2))
    for k in ("security", "sniper_checklist", "tx_hash", "risk_flags", "hold", "holder_distribution", "dev_root", "icon"):
        assert k not in short.hot(), k
    assert short.hot_get("mint_calldata", "").startswith("0x1249c58b")       # a short calldata is read by list responses: hot
    assert long.hot_get("mint_calldata") is None and long["mint_calldata"].startswith("0x1249c58b")
    for k in ("contract", "name", "symbol", "block", "token_price_usd", "lp_usd", "market_cap", "volume_24h", "holders",
              "score", "smart_wallets", "smart_minters", "sniper_ready", "risk_level", "risk_badge", "standard", "market_url"):
        assert k in short.hot(), k                                            # filters, sorts, counters and tables read these


def test_writing_a_field_wins_over_the_stored_one_and_new_keys_follow_the_old_order():
    p = record(1)
    c = compact_record(p)
    c["security"] = {"replaced": True}
    c["brand_new"] = 1
    expect = dict(p)
    expect["security"] = {"replaced": True}
    expect["brand_new"] = 1
    assert c.full() == expect and list(c.full()) == list(expect)


def test_records_that_need_no_compacting_or_are_not_dicts_pass_through():
    assert compact_record(None) is None and compact_record([1]) == [1]
    c = compact_record({"contract": "0x1", "name": "x"})
    assert c == {"contract": "0x1", "name": "x"} and not c.has_cold()
    assert compact_record(c) is c                                              # already compact


def test_equal_lists_and_repeated_strings_are_stored_once():
    a, b = compact_record(record(1)), compact_record(record(2))
    assert a["smart_minters"] is a["smart_wallets"]                           # identical lists: one copy (output unchanged)
    assert a["smart_minters"] == record(1)["smart_minters"]
    assert a["standard"] is b["standard"] and a["risk_badge"] is b["risk_badge"]
    assert a._order is b._order                                                # one key-order tuple for all of them


def test_a_compact_record_survives_pickle_and_deepcopy_as_a_plain_dict():
    import copy
    import pickle
    c = compact_record(long_calldata_record(5))
    for out in (copy.deepcopy(c), pickle.loads(pickle.dumps(c))):
        assert out == c.full() and list(out) == list(c.full())


# ---------------------------------------------------------------------------------------- the same responses

def build_manager(tmp_path, monkeypatch, compact_on):
    for var in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))
    monkeypatch.setattr(db_module, "FAVORITES_FILE", str(tmp_path / "no_favorites.json"))
    m = DatabaseManager(db_path=str(tmp_path / f"t{int(compact_on)}.db"))
    rows = [record(i) for i in range(1, 61)]
    for i in range(1, 61, 5):
        rows[i - 1] = long_calldata_record(i)
    rows[1]["security"] = None
    rows[2].pop("mint_calldata")
    rows[3]["mint_calldata"] = "0x"
    rows[4]["name"] = "Token 5 Ünïcode ✓"
    rows[6]["sniper_checklist"] = []
    rows[7]["risk_flags"] = None
    rows[8]["security"] = {"has_refund": True, "is_serial_dev": False, "refund_methods": ["refund()"], "nan": float("nan")}
    # the Pulse counts the last 24 hours: make some of the records recent and a few of them refund-able
    for i in range(0, 20):
        rows[i]["detected_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(NOW - 3600 * (i + 1)))
    m.upsert_projects(rows)
    if compact_on:
        m.enable_compact_cache()
        with m._cache_lock:
            for c in list(m._projects_cache):
                m._projects_cache[c] = m._stored(m._projects_cache[c])
        assert all(isinstance(v, CompactRecord) for v in m._projects_cache.values())
    return m


QUERIES = [
    "/api/feed?limit=150", "/api/feed?limit=20&offset=20", "/api/feed?limit=30&view=list",
    "/api/feed?filter=secondary&limit=300", "/api/feed?filter=secondary&limit=300&view=list", "/api/feed?since_block=4960",
    "/api/stats", "/api/top-gainers", "/api/market-data", "/api/pulse", "/history.json",
    "/api/projects?search=0x{:040x}&limit=5".format(1),          # a record with long calldata
    "/api/projects?search=0x{:040x}&limit=5".format(2),
    "/api/projects?search=token&limit=50", "/api/projects?status=MINTING&limit=50", "/api/projects?sort_by=progress&limit=50",
    "/api/projects?sort_by=score&order=asc&limit=50&offset=20", "/api/projects?min_progress=10&limit=50",
]


def responses(tmp_path, monkeypatch, compact_on):
    m = build_manager(tmp_path, monkeypatch, compact_on)
    monkeypatch.setattr(api, "db", m)
    monkeypatch.setattr(api, "_current_snapshot", None)
    monkeypatch.setattr(api, "RATE_LIMIT_MAX", 10 ** 9)
    monkeypatch.setattr(time, "time", lambda: NOW)
    monkeypatch.setattr(monitor, "load_agent_id_stats", lambda: {"total_created_wallets": 178001, "last_block": 5})
    monkeypatch.setattr(monitor, "cached_bnb_price", lambda: 600.0)
    monkeypatch.setattr(monitor, "bnb_price_is_live", lambda: True)
    c = TestClient(api.app)
    return {q: (r.status_code, r.content) for q in QUERIES for r in [c.get(q, headers=H)]}, m


def test_every_endpoint_answers_byte_for_byte_as_with_plain_records(tmp_path, monkeypatch):
    plain, _ = responses(tmp_path, monkeypatch, False)
    new, m = responses(tmp_path, monkeypatch, True)
    assert plain.keys() == new.keys()
    for q in QUERIES:
        assert new[q][0] == plain[q][0], q
        assert new[q][1] == plain[q][1], q
    ok = [q for q in QUERIES if plain[q][0] == 200]
    assert len(ok) >= 14 and b"mint_calldata" in plain[QUERIES[0]][1] and b"sniper_checklist" in plain[QUERIES[0]][1]
    assert any(isinstance(v, CompactRecord) and v.has_cold() for v in m._projects_cache.values())


def test_a_list_response_decodes_only_the_records_it_returns(tmp_path, monkeypatch):
    plain, _ = responses(tmp_path, monkeypatch, False)       # (sets everything up the same way)
    m = build_manager(tmp_path, monkeypatch, True)
    monkeypatch.setattr(api, "db", m)
    monkeypatch.setattr(api, "_current_snapshot", None)
    monkeypatch.setattr(api, "RATE_LIMIT_MAX", 10 ** 9)
    monkeypatch.setattr(time, "time", lambda: NOW)
    monkeypatch.setattr(monitor, "load_agent_id_stats", lambda: {"total_created_wallets": 178001, "last_block": 5})
    monkeypatch.setattr(monitor, "cached_bnb_price", lambda: 600.0)
    monkeypatch.setattr(monitor, "bnb_price_is_live", lambda: True)
    c = TestClient(api.app)
    n = len(m._projects_cache)
    assert n == 60
    start = compact.DECODES
    c.get("/api/feed?limit=5", headers=H)                      # builds the snapshot: filters, counters, search structures
    build_decodes = compact.DECODES - start
    before = compact.DECODES
    r = c.get("/api/feed?limit=5&offset=10", headers=H)
    assert r.status_code == 200 and len(r.json()["projects"]) == 5
    assert compact.DECODES - before <= 5                       # the five records on the page, nothing else
    before = compact.DECODES
    r = c.get("/api/projects?search=0x{:040x}&limit=5".format(1), headers=H)
    assert r.status_code == 200 and r.json()["count"] == 1 and r.json()["projects"][0]["mint_calldata"].startswith("0x1249c58b")
    assert compact.DECODES - before == 1                       # the modal's record: one blob
    before = compact.DECODES
    for q in ("/api/stats", "/api/market-data", "/api/top-gainers"):
        c.get(q, headers=H)
    assert compact.DECODES - before <= 20                      # the Pulse reads one flag of each last-24h record, no more
    assert build_decodes < n                                   # building the snapshot over every record decoded fewer blobs than records


def test_opening_a_modal_asks_the_database_for_nothing(tmp_path, monkeypatch):
    """The full record is in the cache (cold fields compressed), so the on-demand path sends no Supabase request and
    adds no egress: the same /api/projects?search=<contract> call as before, answered from memory."""
    for var in ("SUPABASE_URL", "SUPABASE_KEY"):
        monkeypatch.setenv(var, "x")
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "h.json"))
    monkeypatch.setattr(db_module, "FAVORITES_FILE", str(tmp_path / "f.json"))
    monkeypatch.setattr(DatabaseManager, "seed_from_local_json", lambda self: None)
    m = DatabaseManager(db_path=str(tmp_path / "t.db"))
    fake = FakeSupabase(0)
    rows = {}
    for i in range(1, 41):
        r = long_calldata_record(i) if i % 4 == 0 else record(i)
        c = r["contract"]
        rows[c] = {"contract": c, "block": r["block"], "updated_at": 100 + i, "static_data": None, "data": r}
    fake.rows = rows
    m._client_instance, m._client_pid = fake, os.getpid()
    m.enable_compact_cache()
    monkeypatch.setattr(api, "db", m)
    monkeypatch.setattr(api, "_current_snapshot", None)
    monkeypatch.setattr(api, "RATE_LIMIT_MAX", 10 ** 9)
    monkeypatch.setattr(monitor, "cached_bnb_price", lambda: 600.0)
    monkeypatch.setattr(monitor, "bnb_price_is_live", lambda: True)
    client = TestClient(api.app)
    assert client.get("/api/feed?limit=10", headers=H).status_code == 200      # the full load happens here
    assert all(isinstance(v, CompactRecord) for v in m._projects_cache.values())
    m._last_cache_time = time.time() + 1e6                                      # the cache is fresh: no refresh either
    sent = len(fake.requests)
    for i in (4, 8, 12, 7):
        body = client.get(f"/api/projects?search=0x{i:040x}&limit=5", headers=H).json()
        want = long_calldata_record(i) if i % 4 == 0 else record(i)
        got = body["projects"][0]
        assert got["security"] == want["security"] and got["sniper_checklist"] == want["sniper_checklist"]
        assert got["mint_calldata"] == want["mint_calldata"]
    assert len(fake.requests) == sent                                           # zero extra requests per modal open


# -------------------------------------------------------------------------------------------------- the loader

def split_row(r, i, with_column=True):
    data, static, digest = DatabaseManager._split_static(r)
    row = {"contract": r["contract"], "block": r["block"], "updated_at": 100 + i, "data": data}
    if with_column:
        row["static_data"] = static
    return row


@pytest.fixture
def supa(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "history.json"))
    monkeypatch.setattr(db_module, "FAVORITES_FILE", str(tmp_path / "fav.json"))
    monkeypatch.setattr(DatabaseManager, "seed_from_local_json", lambda self: None)

    def build(rows, compact_on, cap=1000):
        monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
        monkeypatch.setenv("SUPABASE_KEY", "k")
        m = DatabaseManager(db_path=str(tmp_path / "t.db"))
        m._page_retry_sleep = lambda s: None
        fake = FakeSupabase(0, cap)
        fake.rows = rows
        m._client_instance, m._client_pid = fake, os.getpid()
        if compact_on:
            m.enable_compact_cache()
        return m, fake
    return build


def legacy_rows(n):
    out = {}
    for i in range(1, n + 1):
        r = long_calldata_record(i) if i % 3 == 0 else record(i)
        out[r["contract"]] = {"contract": r["contract"], "block": r["block"], "updated_at": 100 + i, "static_data": None, "data": r}
    return out


@pytest.mark.parametrize("layout", ["full_records", "split_with_static_data_column"])
def test_the_loader_gives_the_same_records_plain_or_compact_in_both_database_layouts(supa, layout):
    """Whether the `static_data` column exists (migration 002) or the rows are still whole records, the cache holds the
    same records; only their form differs."""
    if layout == "full_records":
        rows = legacy_rows(1300)
    else:
        rows = {}
        for i in range(1, 1301):
            r = long_calldata_record(i) if i % 3 == 0 else record(i)
            rows[r["contract"]] = split_row(r, i)
    plain, _ = supa(json.loads(json.dumps(rows)), False, cap=500)
    new, fake = supa(json.loads(json.dumps(rows)), True, cap=500)
    a, b = plain.get_all_projects(), new.get_all_projects()
    assert len(a) == len(b) == 1300
    assert [p["contract"] for p in a] == [p["contract"] for p in b]
    assert all(isinstance(p, CompactRecord) for p in b) and not any(isinstance(p, CompactRecord) for p in a)
    for x, y in zip(a, b):
        assert y == x and json.dumps(y) == json.dumps(x)
        assert y.get("security") == x.get("security") and y.get("mint_calldata") == x.get("mint_calldata")
    assert sum(1 for p in b if p.has_cold()) == 1300


def test_the_delta_refresh_stores_compact_records_too_and_keeps_static_fields(supa):
    rows = {}
    for i in range(1, 31):
        r = record(i)
        rows[r["contract"]] = split_row(r, i)
    m, fake = supa(rows, True)
    m.get_all_projects()
    c5 = f"0x{5:040x}"
    new_data = dict(rows[c5]["data"], market_cap=987654.0, name="Renamed")
    rows[c5] = dict(rows[c5], data=new_data, updated_at=999)           # the market data moved; static_data is unchanged
    m._last_cache_time = 0.0
    m.get_all_projects()
    got = m._projects_cache[c5]
    assert isinstance(got, CompactRecord) and got["market_cap"] == 987654.0 and got["name"] == "Renamed"
    assert got["security"] == record(5)["security"] and got["sniper_checklist"] == record(5)["sniper_checklist"]


def test_a_full_load_hands_each_page_on_and_keeps_none_of_the_raw_rows(supa):
    m, fake = supa(legacy_rows(1250), True, cap=500)
    pages = []
    out = m._fetch_rows_paged(fake, "/rest/v1/projects?select=*&order=contract.asc", sink=lambda b: pages.append(len(b)))
    assert pages == [500, 500, 250] and len(out) == 1250 and all(x is None for x in out)    # counted, not collected
    again = m._fetch_rows_paged(fake, "/rest/v1/projects?select=*&order=contract.asc")
    assert len(again) == 1250 and isinstance(again[0], dict)                                  # without a sink: as before


def test_a_failed_page_applies_nothing_and_keeps_the_static_fingerprints(supa):
    rows = {}
    for i in range(1, 1301):
        r = record(i)
        rows[r["contract"]] = split_row(r, i)
    m, fake = supa(rows, True, cap=500)
    m._static_fp["0xold"] = "keep"
    fake.fail_range_start_from = 1000
    items = m.get_all_projects()
    assert items == [] and m._projects_cache == {}
    assert m._static_fp == {"0xold": "keep"}                          # the failed attempt left no half-applied state
    fake.fail_range_start_from = None
    m._load_failed_until = 0.0
    assert len(m.get_all_projects()) == 1300 and len(m._static_fp) == 1301      # 1300 rows + the marker


def test_the_workers_cache_is_not_compacted_unless_asked(supa):
    m, _ = supa(legacy_rows(10), False)
    assert not m.compact_cache and not any(isinstance(p, CompactRecord) for p in m.get_all_projects())
    m.enable_compact_cache(False)
    assert not m.compact_cache


# ----------------------------------------------------------------------------------------------------- memory

def measure(factory, n=600):
    gc.collect()
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    store = {}
    for i in range(1, n + 1):
        r = long_calldata_record(i) if i % 7 == 0 else record(i)
        r = json.loads(json.dumps(r))                       # a freshly parsed record, like the loader's
        r["contract"] = f"0x{i:040x}"
        store[r["contract"]] = factory(r)
    gc.collect()
    size = tracemalloc.get_traced_memory()[0] - base
    tracemalloc.stop()
    return size, store


def test_the_records_take_less_than_half_the_memory():
    plain, a = measure(lambda r: r)
    small, b = measure(compact_record)
    assert len(a) == len(b) == 600
    assert small < 0.5 * plain, (plain / 600, small / 600)
    assert small / 600 < 5200                                  # bytes a record occupies; plain records here take about 11 KB


def test_a_full_load_peaks_far_lower_with_compact_records(supa):
    rows = legacy_rows(2000)

    def peak(compact_on):
        m, _ = supa(json.loads(json.dumps(rows)), compact_on, cap=500)
        gc.collect()
        tracemalloc.start()
        m.get_all_projects()
        _, top = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return top, m

    plain_peak, _ = peak(False)
    new_peak, m = peak(True)
    assert len(m._projects_cache) == 2000
    assert new_peak < 0.7 * plain_peak, (plain_peak, new_peak)
