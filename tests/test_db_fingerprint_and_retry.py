"""Regression tests for the metrics audit, Prompt 3: the bandwidth-guard fingerprint (M3) and failed Supabase
writes (M4). Uses an in-memory stand-in for Supabase; nothing touches the network."""
import copy
import os
import time

import pytest

import db as db_module
from db import DatabaseManager

A = "0x" + "a" * 40


class Resp:
    def __init__(self, code):
        self.status_code, self.text, self.headers = code, "x", {}


class FakeSupabase:
    """Scripted stand-in: `script` is a list of status codes (or exceptions) consumed one per POST, then 201."""
    def __init__(self, script=None):
        self.script = list(script or [])
        self.rows = {}          # contract -> data stored in "Supabase"
        self.posts = 0

    def post(self, path, json=None, headers=None):
        self.posts += 1
        step = self.script.pop(0) if self.script else 201
        if isinstance(step, Exception):
            raise step
        if step in (200, 201):
            for r in json:
                self.rows[r["contract"]] = copy.deepcopy(r["data"])
        return Resp(step)

    def get(self, *a, **k):
        return Resp(500)

    def close(self):
        pass


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))
    for k in list(os.environ):
        if k.startswith("SUPABASE"):
            monkeypatch.delenv(k)
    m = DatabaseManager(db_path=str(tmp_path / "t.db"))
    return m


@pytest.fixture
def sb(mgr):
    fake = FakeSupabase()
    mgr.mode = "supabase"
    mgr.SUPABASE_FLUSH_INTERVAL_SEC = 0  # these tests are about fingerprints and retries: send every change at once
    mgr._client_instance, mgr._client_pid = fake, os.getpid()
    mgr._init_sqlite()  # the local fallback table, as on a real deploy
    return mgr, fake


def rec(contract=A, **kw):
    d = {"contract": contract, "name": "S", "symbol": "S", "block": 3, "token_price_usd": 0.01, "score": 1}
    d.update(kw)
    return d


# ------------------------------------------------------------------------------------------------ M3

# every field the audit found missing from the fingerprint, with a change big enough to matter
M3_CASES = [
    ("buys_24h", 10, 11), ("sells_24h", 4, 5), ("buys_1h", 1, 2),
    ("price_change_24h", 5.0, 7.5), ("fdv", 100_000, 250_000), ("lp_wbnb", 10.0, 20.0),
    ("token_price_bnb", 0.0001, 0.0002), ("ath_price_usd", 0.02, 0.03), ("ath_profit_usd", 5.0, 5.01),
    ("current_profit_usd", 10.0, 20.0), ("current_multiplier", 2.0, 3.0), ("ath_multiplier", 3.0, 3.01),
    ("high_price_bnb", 0.5, 0.9), ("floor_price_bnb", 0.1, 0.2), ("secondary_vol_bnb", 1.0, 2.0),
    ("secondary_sales_count", 2, 3), ("market_stale", False, True),
]


@pytest.mark.parametrize("field,before,after", M3_CASES)
def test_change_to_any_audited_field_changes_the_fingerprint(field, before, after):
    f = DatabaseManager._compute_fingerprint
    assert f(rec(**{field: before})) != f(rec(**{field: after}))


@pytest.mark.parametrize("field,before,after", M3_CASES)
def test_change_to_an_audited_field_alone_is_written(sb, field, before, after):
    mgr, fake = sb
    mgr.upsert_projects([rec(**{field: before})])
    assert fake.posts == 1
    mgr.upsert_projects([rec(**{field: after})])
    assert fake.posts == 2 and fake.rows[A][field] == after


def test_cleared_market_is_not_confused_with_zero():
    f = DatabaseManager._compute_fingerprint
    assert f(rec(fdv=None, price_change_24h=None, buys_24h=None)) != f(rec(fdv=0, price_change_24h=0, buys_24h=0))
    assert f(rec(token_price_bnb=None)) != f(rec(token_price_bnb=0.0))


def test_micro_fluctuations_still_do_not_trigger_writes(sb):
    mgr, fake = sb
    base = dict(token_price_usd=0.004123456, lp_usd=10_000, market_cap=500_000, volume_24h=20_000,
                price_change_24h=5.04, fdv=500_000, lp_wbnb=15.01, token_price_bnb=0.0000061,
                current_profit_usd=12.3412, current_multiplier=2.0001, market_updated_at=1000.0)
    mgr.upsert_projects([rec(**base)])
    tick = dict(base, token_price_usd=0.004124, lp_usd=10_020, market_cap=500_300, volume_24h=20_300,
                price_change_24h=5.02, fdv=500_300, lp_wbnb=15.02, token_price_bnb=0.000006102,
                current_profit_usd=12.3499, current_multiplier=2.0002, market_updated_at=1120.0)
    mgr.upsert_projects([rec(**tick)])
    assert fake.posts == 1


def test_cache_takes_the_values_even_when_the_remote_write_is_skipped(sb):
    mgr, fake = sb
    mgr.upsert_projects([rec(token_price_usd=0.004123456, market_updated_at=1000.0)])
    mgr.upsert_projects([rec(token_price_usd=0.004124, market_updated_at=1120.0, website="https://x.test")])
    assert fake.posts == 1                                    # same bucket: no outbound write
    cached = mgr._projects_cache[A]
    assert cached["token_price_usd"] == 0.004124              # but the in-memory copy is current
    assert cached["market_updated_at"] == 1120.0 and cached["website"] == "https://x.test"
    assert mgr._cache_dirty is True


def test_unchanged_record_is_skipped_but_forced_after_the_age_limit(sb, monkeypatch):
    mgr, fake = sb
    monkeypatch.setattr(DatabaseManager, "FORCE_WRITE_AFTER_SEC", 1800.0)
    mgr.upsert_projects([rec(market_updated_at=1000.0)])
    mgr.upsert_projects([rec(market_updated_at=1120.0)])
    assert fake.posts == 1
    mgr._p_written_at[A] -= 1801.0                             # last write is now older than the limit
    mgr.upsert_projects([rec(market_updated_at=3000.0)])
    assert fake.posts == 2 and fake.rows[A]["market_updated_at"] == 3000.0   # the fresh timestamp reached the DB
    mgr.upsert_projects([rec(market_updated_at=3120.0)])
    assert fake.posts == 2                                     # and the clock restarted


# ------------------------------------------------------------------------------------------------ M4

def test_record_reaches_supabase_after_a_failed_write(sb):
    """The audit's reproduction: 500 first, then healthy; the same record is offered again."""
    mgr, fake = sb
    fake.script = [500]
    mgr.upsert_projects([rec()])
    assert fake.rows == {}                                     # the outage swallowed it
    mgr.upsert_projects([rec()])                               # worker offers the identical record again
    assert A in fake.rows and fake.posts == 2


def test_network_exception_is_retried_too(sb):
    mgr, fake = sb
    fake.script = [OSError("connection reset")]
    mgr.upsert_projects([rec()])
    mgr.upsert_projects([rec()])
    assert A in fake.rows


def test_failed_chunk_does_not_block_the_chunks_after_it(sb):
    mgr, fake = sb
    contracts = [f"0x{i:040x}" for i in range(1, 121)]       # 3 chunks of 50, 50, 20
    fake.script = [201, 500, 201]                               # the middle chunk fails
    mgr.upsert_projects([rec(c) for c in contracts])
    assert fake.posts == 3
    assert set(fake.rows) == set(contracts[:50]) | set(contracts[100:])     # chunk 3 was still sent

    mgr.upsert_projects([rec(c) for c in contracts])           # next sweep offers everything again
    assert fake.posts == 4                                      # only ONE more post: the 50 that failed
    assert set(fake.rows) == set(contracts)


def test_failed_records_fall_back_to_sqlite_but_stay_dirty(sb):
    mgr, fake = sb
    fake.script = [500]
    mgr.upsert_projects([rec()])
    import sqlite3
    row = sqlite3.connect(mgr.db_path).execute("select count(*) from projects where contract=?", (A,)).fetchone()
    assert row[0] == 1
    assert A not in mgr._p_fingerprints                         # not marked as written


def test_successful_chunks_are_not_resent_or_sent_to_sqlite(sb):
    mgr, fake = sb
    mgr.upsert_projects([rec()])
    import sqlite3
    assert sqlite3.connect(mgr.db_path).execute("select count(*) from projects").fetchone()[0] == 0
    mgr.upsert_projects([rec()])
    assert fake.posts == 1


def test_sqlite_mode_still_dedupes_after_a_successful_write(mgr):
    mgr._init_sqlite()
    calls = []
    real = mgr._get_sqlite_connection
    mgr._get_sqlite_connection = lambda: calls.append(1) or real()
    mgr.upsert_projects([rec()])
    mgr.upsert_projects([rec()])
    assert len(calls) == 1


def test_failed_record_is_resent_even_if_the_worker_never_offers_it_again(sb):
    """The worker only offers records that changed; a record whose write failed must not wait for that."""
    mgr, fake = sb
    other = "0x" + "b" * 40
    fake.script = [500]
    mgr.upsert_projects([rec()])                      # A fails
    assert A not in fake.rows
    mgr.upsert_projects([rec(other)])                 # an unrelated upsert: A rides along from the cache
    assert A in fake.rows and other in fake.rows
    posts = fake.posts
    mgr.upsert_projects([rec(other)])
    assert fake.posts == posts                        # the queue is empty again


def test_deleted_record_leaves_the_retry_queue(sb):
    mgr, fake = sb
    fake.script = [500]
    mgr.upsert_projects([rec()])
    assert A in mgr._retry_queue
    mgr.delete_projects([A])
    assert A not in mgr._retry_queue
    mgr.upsert_projects([rec("0x" + "d" * 40)])
    assert A not in fake.rows


def test_sqlite_write_failure_is_retried(mgr):
    """A failing local write commits no fingerprint and queues the record for the next upsert."""
    mgr._init_sqlite()
    real = mgr._get_sqlite_connection

    class BrokenConn:
        def __init__(self, conn):
            self.conn = conn

        def cursor(self):
            raise RuntimeError("database is locked")

        def close(self):
            self.conn.close()

    state = {"broken": True}
    mgr._get_sqlite_connection = lambda: BrokenConn(real()) if state["broken"] else real()
    mgr.upsert_projects([rec()])
    assert A not in mgr._p_fingerprints and A in mgr._retry_queue
    state["broken"] = False
    mgr.upsert_projects([rec("0x" + "b" * 40)])
    import sqlite3
    n = sqlite3.connect(mgr.db_path).execute("select count(*) from projects where contract=?", (A,)).fetchone()[0]
    assert n == 1 and A in mgr._p_fingerprints and A not in mgr._retry_queue
