"""The Supabase loader: a response is capped at 1000 rows, so the "full load" used to return only the newest 1000 of
8,233 rows, and the cache started from the committed history.json snapshot and never refreshed most rows.
Uses an in-memory fake that behaves like PostgREST (cap, Range, ordering, updated_at filter)."""
import json
import os
from urllib.parse import parse_qs

import pytest

import db as db_module
from db import DatabaseManager


class Resp:
    def __init__(self, code, body, headers=None):
        self.status_code, self._body, self.headers, self.text = code, body, headers or {}, ""

    def json(self):
        return self._body


class FakeSupabase:
    """Rows are {contract, data, updated_at, block, static_data}. `cap` is the server's max-rows."""

    def __init__(self, n, cap=1000):
        self.cap = cap
        self.requests = []
        self.down = False
        self.timeouts = []
        self.raise_n = 0                       # the next N requests time out
        self.fail_range_start_from = None      # fail any page starting at or after this offset
        self.rows = {}
        for i in range(n):
            c = f"0x{i:040x}"
            self.rows[c] = {"contract": c, "block": 1000 + i, "updated_at": 100 + i, "static_data": None,
                            "data": {"contract": c, "name": f"T{i}", "block": 1000 + i, "market_cap": 1000.0 + i}}

    def touch(self, i, **fields):
        c = f"0x{i:040x}"
        self.rows[c]["data"].update(fields)
        self.rows[c]["updated_at"] = max(r["updated_at"] for r in self.rows.values()) + 1

    def get(self, path, headers=None, timeout=None):
        headers = headers or {}
        self.timeouts.append(timeout)
        if self.raise_n > 0:
            self.raise_n -= 1
            raise TimeoutError("The read operation timed out")
        self.requests.append((path, dict(headers)))
        if self.down:
            return Resp(500, [])
        q = parse_qs(path.split("?", 1)[1])
        rows = list(self.rows.values())
        if "updated_at" in q and q["updated_at"][0].startswith("gte."):
            thr = int(q["updated_at"][0][4:])
            rows = [r for r in rows if r["updated_at"] >= thr]
        order = q.get("order", [""])[0]
        if order.startswith("updated_at.desc"):
            rows.sort(key=lambda r: -r["updated_at"])
        elif order.startswith("updated_at.asc"):
            rows.sort(key=lambda r: (r["updated_at"], r["contract"]))
        elif order.startswith("block.desc"):
            rows.sort(key=lambda r: (-r["block"], r["contract"]))
        elif order.startswith("contract.asc"):
            rows.sort(key=lambda r: r["contract"])
        total = len(rows)
        start, end = 0, total - 1
        if headers.get("Range"):
            a, b = headers["Range"].split("-")
            start, end = int(a), int(b)
        if self.fail_range_start_from is not None and start >= self.fail_range_start_from:
            return Resp(500, [])
        count = min(self.cap, end - start + 1)
        if "limit" in q:
            count = min(count, int(q["limit"][0]))
        page = rows[start:start + count]
        sel = q.get("select", ["*"])[0]
        if sel != "*":
            cols = sel.split(",")
            page = [{k: r[k] for k in cols if k in r} for r in page]
        last = start + len(page) - 1
        return Resp(206 if len(page) < total else 200, json.loads(json.dumps(page)),
                    {"content-range": f"{start}-{max(last, start)}/{total}"})

    def post(self, *a, **k):
        return Resp(201, [])

    def close(self):
        pass


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "history.json"))
    monkeypatch.setattr(db_module, "FAVORITES_FILE", str(tmp_path / "fav.json"))
    monkeypatch.setattr(DatabaseManager, "seed_from_local_json", lambda self: None)

    def build(n=2500, cap=1000, history=None):
        if history is not None:
            (tmp_path / "history.json").write_text(json.dumps(history))
        monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
        monkeypatch.setenv("SUPABASE_KEY", "k")
        m = DatabaseManager(db_path=str(tmp_path / "t.db"))
        assert m.mode == "supabase"
        m._page_retry_sleep = lambda s: None
        fake = FakeSupabase(n, cap)
        m._client_instance, m._client_pid = fake, os.getpid()
        return m, fake
    return build


def by_contract(items):
    return {p["contract"]: p for p in items}


# ------------------------------------------------------------------------------------------------ the 1000-row cap
def test_full_load_reads_past_the_server_row_cap(make):
    m, fake = make(n=2500)
    items = m.get_all_projects()
    assert len(items) == 2500                                   # was 1000: the rest of the table was invisible
    ranges = [h["Range"] for _, h in fake.requests if "Range" in h]
    assert ranges == ["0-499", "500-999", "1000-1499", "1500-1999", "2000-2499"]


def test_a_server_cap_below_the_page_size_is_still_read_to_the_end(make):
    m, _ = make(n=1300, cap=400)
    assert len(m.get_all_projects()) == 1300


def test_a_table_of_exactly_one_page_or_a_few_rows(make):
    for n in (0, 1, 999, 1000, 1001):
        m, _ = make(n=n)
        assert len(m.get_all_projects()) == n, n


def test_delta_reads_every_changed_row_not_just_the_first_1000(make):
    m, fake = make(n=1500)
    m.get_all_projects()
    for i in range(1200):
        fake.touch(i, market_cap=None)                          # the worker rewrote 1,200 rows
    m._last_cache_time = 0                                      # TTL elapsed
    got = by_contract(m.get_all_projects())
    assert all(got[f"0x{i:040x}"]["market_cap"] is None for i in range(1200))
    assert got[f"0x{1300:040x}"]["market_cap"] == 2300.0       # untouched rows keep their value
    assert m._last_synced_updated_at == max(r["updated_at"] for r in fake.rows.values())


def test_a_failed_page_applies_nothing_and_does_not_move_the_sync_point(make):
    m, fake = make(n=1500)
    m.get_all_projects()
    synced = m._last_synced_updated_at
    for i in range(1500):
        fake.touch(i, market_cap=7.0)
    fake.fail_range_start_from = 1000                           # page 2 of the delta fails
    m._last_cache_time = 0
    got = by_contract(m.get_all_projects())
    assert got[f"0x{0:040x}"]["market_cap"] == 1000.0           # the first page was NOT half-applied
    assert m._last_synced_updated_at == synced
    fake.fail_range_start_from = None
    m._last_cache_time = 0
    got = by_contract(m.get_all_projects())
    assert all(p["market_cap"] == 7.0 for p in got.values())    # recovers completely on the next read


# ------------------------------------------------------------------------------------------------ history.json snapshot
def test_the_committed_snapshot_no_longer_pre_seeds_the_cache_on_supabase(make):
    stale = [{"contract": f"0x{i:040x}", "name": f"T{i}", "block": 1000 + i, "market_cap": 45_000_000.0} for i in range(5)]
    stale.append({"contract": "0x" + "f" * 40, "name": "Gone", "block": 1, "market_cap": 1.0})   # no longer in Supabase
    m, fake = make(n=5, history=stale)
    assert m._projects_cache == {} and m._cache_initialized is False
    got = by_contract(m.get_all_projects())
    assert len(got) == 5 and "0x" + "f" * 40 not in got
    assert got[f"0x{2:040x}"]["market_cap"] == 1002.0           # Supabase's value, not the snapshot's $45M


def test_cold_start_outage_serves_the_snapshot_then_recovers_with_a_full_load(make):
    snap = [{"contract": f"0x{i:040x}", "name": f"S{i}", "block": 1000 + i, "market_cap": 99.0} for i in range(3)]
    m, fake = make(n=3, history=snap)
    fake.down = True
    first = m.get_all_projects()
    assert len(first) == 3 and m._cache_initialized is False     # served, but not trusted as a loaded cache
    fake.down = False
    m._load_failed_until = 0.0                                   # cooldown over
    got = by_contract(m.get_all_projects())
    assert got[f"0x{1:040x}"]["market_cap"] == 1001.0 and m._cache_initialized is True


def test_sqlite_mode_still_pre_seeds_from_history_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "h.json"))
    (tmp_path / "h.json").write_text(json.dumps([{"contract": "0x" + "1" * 40, "name": "S"}]))
    for var in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(DatabaseManager, "seed_from_local_json", lambda self: None)
    m = DatabaseManager(db_path=str(tmp_path / "t.db"))
    assert "0x" + "1" * 40 in m._projects_cache and m._cache_initialized is True


def test_a_full_refresh_keeps_a_record_that_only_exists_locally(make):
    m, fake = make(n=10)
    m.get_all_projects()
    local = "0x" + "a" * 40
    m._projects_cache[local] = {"contract": local, "name": "new drop"}
    m._pending_remote.add(local)                                # queued, not yet written to Supabase
    got = by_contract(m.get_all_projects(force_refresh=True))
    assert local in got and len(got) == 11


# ------------------------------------------------------------------------------------------------ strict loader + scripts
def test_load_all_strict_returns_everything_or_raises(make):
    m, fake = make(n=2500)
    assert len(m.load_all_strict()) == 2500
    fake.down = True
    with pytest.raises(RuntimeError):
        m.load_all_strict()


@pytest.mark.parametrize("script", ["cleanup_counts_and_supply", "cleanup_foreign_pairs"])
def test_cleanup_scripts_abort_instead_of_reporting_zero_records(script, monkeypatch, capsys):
    class Broken:
        mode = "supabase"

        def load_all_strict(self):
            raise RuntimeError("incomplete read: got 1004 projects but the table has 8233 rows")

    monkeypatch.setattr(db_module, "db", Broken())
    mod = __import__(script)
    assert mod.main([]) == 2
    out = capsys.readouterr().out
    assert "ABORT" in out and "8233" in out and "record(s)" not in out


# ------------------------------------------------------------------------------------------------ timeouts / retries
def test_a_page_that_times_out_is_retried_and_the_load_completes(make):
    m, fake = make(n=1200)
    fake.raise_n = 2                                            # the first page times out twice, then answers
    assert len(m.get_all_projects()) == 1200
    assert m._cache_initialized is True


def test_reads_use_a_longer_timeout_than_the_shared_clients_default(make):
    m, fake = make(n=600)
    m.get_all_projects()
    assert fake.timeouts and all(t is not None and t >= 30 for t in fake.timeouts)


def test_the_full_load_is_ordered_by_the_primary_key(make):
    m, fake = make(n=10)
    m.get_all_projects()
    assert any("order=contract.asc" in path for path, _ in fake.requests)


def test_a_failed_full_load_is_not_retried_by_every_request(make):
    snap = [{"contract": f"0x{i:040x}", "name": f"S{i}", "block": 1000 + i} for i in range(3)]
    m, fake = make(n=3000, history=snap)
    fake.raise_n = 10_000                                       # the database times out on everything
    for _ in range(5):
        assert len(m.get_all_projects()) == 3                   # the snapshot is served...
    assert len(fake.timeouts) == m.SUPABASE_PAGE_ATTEMPTS     # ...after ONE load attempt, not five
    fake.raise_n = 0
    m._load_failed_until = 0.0
    assert len(m.get_all_projects()) == 3000                    # and it recovers once the cooldown is over


def test_concurrent_requests_share_one_full_load(make):
    import threading, time as _t
    m, fake = make(n=1500)
    orig = fake.get
    def slow(path, headers=None, timeout=None):
        _t.sleep(0.05)
        return orig(path, headers, timeout)
    fake.get = slow
    out = []
    ts = [threading.Thread(target=lambda: out.append(len(m.get_all_projects()))) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert out == [1500] * 6
    full_loads = [h for _, h in fake.requests if h.get("Range", "").startswith("0-")]
    assert len(full_loads) == 1
