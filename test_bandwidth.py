"""Bandwidth (Render's 5 GB/month cap): batched Supabase updates, the static_data column split, and slim list
responses. Uses an in-memory stand-in for Supabase/PostgREST; nothing touches the network."""
import copy
import json
import os
import re
import time

import pytest
from fastapi.testclient import TestClient

import api
import db as db_module
from db import DatabaseManager

A = "0x" + "a" * 40
B = "0x" + "b" * 40
BIG_CALLDATA = "0x" + "ab" * 25_000          # 50 KB, like the largest real ones (up to ~98 KB)


class Resp:
    def __init__(self, code, body=None, text="x"):
        self.status_code, self._body, self.text, self.headers = code, body, text, {}

    def json(self):
        return self._body


class FakePostgrest:
    """Just enough PostgREST for db.py: merge-duplicates upserts that only touch the columns sent, the delta /
    full / in.() selects, and an optional missing static_data column. Counts request bytes like Render would."""

    def __init__(self, has_static_column=True):
        self.has_static_column = has_static_column
        self.rows = {}              # contract -> row (all columns)
        self.posts = []             # list of row lists, one per POST
        self.gets = []
        self.bytes_sent = 0

    def post(self, path, json=None, headers=None):
        rows = json
        if any("static_data" in r for r in rows) and not self.has_static_column:
            return Resp(400, text="Could not find the 'static_data' column of 'projects' in the schema cache")
        self.posts.append(copy.deepcopy(rows))
        self.bytes_sent += len(__import__("json").dumps(rows))
        for r in rows:
            cur = self.rows.setdefault(r["contract"], {})
            cur.update(copy.deepcopy(r))   # merge-duplicates: columns not sent are left as they are
        return Resp(201)

    def get(self, path, headers=None, timeout=None):
        self.gets.append(path)
        rows = list(self.rows.values())
        if "select=updated_at&order=updated_at.desc&limit=1" in path:
            rows.sort(key=lambda r: r.get("updated_at", 0), reverse=True)
            return Resp(200, [{"updated_at": r.get("updated_at", 0)} for r in rows[:1]])
        m = re.search(r"updated_at=gte\.(\d+)", path)
        if m:
            rows = [r for r in rows if r.get("updated_at", 0) >= int(m.group(1))]
        m = re.search(r"contract=in\.\(([^)]*)\)", path)
        if m:
            wanted = {c.strip('"') for c in m.group(1).split(",")}
            rows = [r for r in rows if r["contract"] in wanted]
        sel = re.search(r"select=([^&]+)", path).group(1)
        if sel != "*":
            cols = sel.split(",")
            rows = [{k: r.get(k) for k in cols} for r in rows]
        return Resp(200, copy.deepcopy(rows))

    def close(self):
        pass


def make_mgr(tmp_path, monkeypatch, fake, name="t.db", interval=600.0):
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))
    for k in list(os.environ):
        if k.startswith("SUPABASE"):
            monkeypatch.delenv(k)
    m = DatabaseManager(db_path=str(tmp_path / name))
    m.mode = "supabase"
    m.SUPABASE_FLUSH_INTERVAL_SEC = interval
    m._client_instance, m._client_pid = fake, os.getpid()
    m._flusher_pid = os.getpid()  # no background thread in tests; flushes are driven explicitly
    m._init_sqlite()
    return m


def rec(contract=A, **kw):
    d = {"contract": contract, "name": "S", "symbol": "S", "block": 3, "token_price_usd": 0.01, "score": 1,
         "mint_calldata": BIG_CALLDATA, "security": {"is_honeypot": "0", "holder_count": "12"},
         "tx_hash": "0x" + "1" * 64}
    d.update(kw)
    return d


# --------------------------------------------------------------------------------------------- batching

def test_new_drop_is_written_at_once(tmp_path, monkeypatch):
    fake = FakePostgrest()
    mgr = make_mgr(tmp_path, monkeypatch, fake)
    mgr.upsert_projects([rec()])
    assert len(fake.posts) == 1


def test_updates_to_a_known_record_are_coalesced_until_the_interval(tmp_path, monkeypatch):
    fake = FakePostgrest()
    mgr = make_mgr(tmp_path, monkeypatch, fake)
    mgr.upsert_projects([rec()])
    for px in (0.02, 0.03, 0.04, 0.05):              # four market sweeps inside the interval
        mgr.upsert_projects([rec(token_price_usd=px)])
    assert len(fake.posts) == 1                       # nothing sent yet
    assert mgr.get_project(A)["token_price_usd"] == 0.05  # the API cache already has the latest value

    mgr._last_remote_flush -= 601                     # the interval has passed
    mgr.upsert_projects([rec(token_price_usd=0.06)])
    assert len(fake.posts) == 2 and len(fake.posts[1]) == 1
    assert fake.rows[A]["data"]["token_price_usd"] == 0.06  # one write, the latest version


def test_queued_updates_go_out_without_further_upserts(tmp_path, monkeypatch):
    """The background flusher calls flush_remote; nothing else needs to offer the record again."""
    fake = FakePostgrest()
    mgr = make_mgr(tmp_path, monkeypatch, fake)
    mgr.upsert_projects([rec()])
    mgr.upsert_projects([rec(token_price_usd=0.5)])
    mgr.flush_remote()                                # not due yet
    assert len(fake.posts) == 1
    mgr.flush_remote(force=True)
    assert len(fake.posts) == 2 and fake.rows[A]["data"]["token_price_usd"] == 0.5
    assert not mgr._pending_remote


def test_failed_flush_keeps_the_record_queued(tmp_path, monkeypatch):
    fake = FakePostgrest()
    mgr = make_mgr(tmp_path, monkeypatch, fake)
    mgr.upsert_projects([rec()])
    mgr.upsert_projects([rec(token_price_usd=0.7)])
    real_post = fake.post
    fake.post = lambda *a, **k: Resp(503)
    mgr.flush_remote(force=True)
    assert A in mgr._pending_remote
    fake.post = real_post
    mgr.flush_remote(force=True)
    assert fake.rows[A]["data"]["token_price_usd"] == 0.7 and not mgr._pending_remote


def test_interval_zero_sends_every_change_at_once(tmp_path, monkeypatch):
    fake = FakePostgrest()
    mgr = make_mgr(tmp_path, monkeypatch, fake, interval=0)
    mgr.upsert_projects([rec()])
    mgr.upsert_projects([rec(token_price_usd=0.2)])
    assert len(fake.posts) == 2


# ----------------------------------------------------------------------------------------- static split

def test_static_fields_are_sent_once_and_left_out_of_market_updates(tmp_path, monkeypatch):
    fake = FakePostgrest()
    mgr = make_mgr(tmp_path, monkeypatch, fake, interval=0)
    mgr.upsert_projects([rec()])
    first = fake.posts[0][0]
    assert first["static_data"]["mint_calldata"] == BIG_CALLDATA
    assert "mint_calldata" not in first["data"]

    mgr.upsert_projects([rec(token_price_usd=0.2)])
    second = fake.posts[1][0]
    assert "static_data" not in second                # untouched column, not resent
    assert "mint_calldata" not in second["data"]
    assert len(json.dumps(second)) < 1000 < len(json.dumps(first))
    assert fake.rows[A]["static_data"]["mint_calldata"] == BIG_CALLDATA  # still stored

    mgr.upsert_projects([rec(token_price_usd=0.2, mint_calldata="0x1249c58b")])  # calldata changed
    assert fake.posts[2][0]["static_data"]["mint_calldata"] == "0x1249c58b"


def test_missing_static_column_falls_back_to_full_records(tmp_path, monkeypatch):
    fake = FakePostgrest(has_static_column=False)
    mgr = make_mgr(tmp_path, monkeypatch, fake, interval=0)
    mgr.upsert_projects([rec()])
    assert mgr._static_split is False
    assert fake.rows[A]["data"]["mint_calldata"] == BIG_CALLDATA   # nothing lost
    assert not mgr._pending_remote and not mgr._retry_queue


def test_reader_rebuilds_records_and_fetches_static_only_when_it_changed(tmp_path, monkeypatch):
    fake = FakePostgrest()
    writer = make_mgr(tmp_path, monkeypatch, fake, name="w.db", interval=0)
    writer.upsert_projects([rec(), rec(contract=B, mint_calldata="0x1249c58b")])

    reader = make_mgr(tmp_path, monkeypatch, fake, name="r.db")
    reader._cache_initialized = False
    got = {p["contract"]: p for p in reader.get_all_projects(force_refresh=True)}
    assert got[A]["mint_calldata"] == BIG_CALLDATA and got[A]["token_price_usd"] == 0.01
    assert "_static_v" not in got[A]

    # a market update: the delta read does not download static_data and the record keeps its calldata
    time.sleep(1.1)  # updated_at has 1 s resolution
    writer.upsert_projects([rec(token_price_usd=0.9)])
    fake.gets.clear()
    reader._last_cache_time = 0
    got = {p["contract"]: p for p in reader.get_all_projects()}
    assert got[A]["token_price_usd"] == 0.9 and got[A]["mint_calldata"] == BIG_CALLDATA
    assert not any("static_data" in g for g in fake.gets)

    # the calldata itself changes: the reader fetches that one record's static_data
    time.sleep(1.1)
    writer.upsert_projects([rec(token_price_usd=0.9, mint_calldata="0xdeadbeef")])
    fake.gets.clear()
    reader._last_cache_time = 0
    got = {p["contract"]: p for p in reader.get_all_projects()}
    assert got[A]["mint_calldata"] == "0xdeadbeef"
    static_gets = [g for g in fake.gets if "static_data" in g]
    assert len(static_gets) == 1 and A in static_gets[0] and B not in static_gets[0]


def test_reader_handles_rows_written_before_the_split(tmp_path, monkeypatch):
    fake = FakePostgrest()
    fake.rows[A] = {"contract": A, "data": rec(), "updated_at": 5}   # legacy row: everything in data
    reader = make_mgr(tmp_path, monkeypatch, fake)
    reader._cache_initialized = False
    got = {p["contract"]: p for p in reader.get_all_projects(force_refresh=True)}
    assert got[A]["mint_calldata"] == BIG_CALLDATA


def test_delta_read_keeps_a_queued_local_update(tmp_path, monkeypatch):
    """A record waiting in the batch is newer locally than in Supabase: a refresh must not roll it back."""
    fake = FakePostgrest()
    mgr = make_mgr(tmp_path, monkeypatch, fake)
    mgr.upsert_projects([rec()])
    mgr.upsert_projects([rec(token_price_usd=0.33)])
    mgr._last_synced_updated_at = 0
    mgr._last_cache_time = 0
    got = {p["contract"]: p for p in mgr.get_all_projects()}
    assert got[A]["token_price_usd"] == 0.33


def test_bytes_sent_for_an_hour_of_market_sweeps(tmp_path, monkeypatch):
    """30 two-minute sweeps of one live token: before, 30 full records; now one record per 10 minutes, without
    its static fields."""
    before_fake = FakePostgrest(has_static_column=False)
    before = make_mgr(tmp_path, monkeypatch, before_fake, name="b.db", interval=0)
    after_fake = FakePostgrest()
    after = make_mgr(tmp_path, monkeypatch, after_fake, name="a.db")
    for mgr in (before, after):
        mgr.upsert_projects([rec()])            # the first insert is the same full record on both sides
    before_fake.bytes_sent = after_fake.bytes_sent = 0
    for sweep in range(1, 31):
        for mgr in (before, after):
            mgr.upsert_projects([rec(token_price_usd=0.01 * (1 + sweep / 10))])
        if sweep % 5 == 0:
            after._last_remote_flush -= 601
    assert after_fake.bytes_sent * 100 < before_fake.bytes_sent
    print(f"bytes for an hour of sweeps: before {before_fake.bytes_sent}, after {after_fake.bytes_sent}")


# --------------------------------------------------------------------------------------- list responses

@pytest.fixture
def api_db(tmp_path, monkeypatch):
    for var in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))
    monkeypatch.setattr(db_module, "FAVORITES_FILE", str(tmp_path / "no_favorites.json"))
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(api, "db", manager)
    return manager


def test_feed_leaves_out_oversized_calldata_and_flags_it(api_db):
    api_db.upsert_projects([rec(block=10), rec(contract=B, block=9, mint_calldata="0x1249c58b")])
    client = TestClient(api.app)
    feed = {p["contract"]: p for p in client.get("/api/feed?limit=50").json()["projects"]}
    assert "mint_calldata" not in feed[A] and feed[A]["has_mint_calldata"] is True
    assert feed[B]["mint_calldata"] == "0x1249c58b" and "has_mint_calldata" not in feed[B]
    full = client.get(f"/api/projects?search={A}").json()["projects"]
    assert full[0]["mint_calldata"] == BIG_CALLDATA      # the modal's on-demand lookup gets it
    assert api_db.get_project(A)["mint_calldata"] == BIG_CALLDATA  # the stored record is untouched
