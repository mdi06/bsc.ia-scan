"""The Agent ID wallet counter moved from a fake wallet row in user_favorites (writable through the favourites endpoint) to a
kv_stats table. Reads fall back to the old row and writes fall back to it while the table does not exist yet."""
import os

import db as db_module
from db import DatabaseManager

STATS = {"total_created_wallets": 263788, "last_block": 100, "last_updated": 1795000000, "source": "registry"}
OLD_ROW = {"wallet": "__system_agent_id_stats__", "favorites": [dict(STATS, total_created_wallets=111)], "updated_at": 5}


class Resp:
    def __init__(self, code, body=None):
        self.status_code, self._body, self.text, self.headers = code, body, "x", {}

    def json(self):
        return self._body


class Fake:
    """kv_stats present or missing, plus the legacy user_favorites row."""

    def __init__(self, kv_exists=True, legacy=True, kv_status=None):
        self.kv_exists, self.kv, self.legacy_row, self.kv_status = kv_exists, {}, dict(OLD_ROW) if legacy else None, kv_status
        self.log = []

    def get(self, path, headers=None, timeout=None):
        self.log.append(("GET", path))
        if "/kv_stats" in path:
            if self.kv_status:
                return Resp(self.kv_status)
            if not self.kv_exists:
                return Resp(404)
            k = path.split("key=eq.")[1].split("&")[0]
            return Resp(200, [{"value": self.kv[k], "updated_at": 1}] if k in self.kv else [])
        if "/user_favorites" in path:
            return Resp(200, [self.legacy_row] if self.legacy_row else [])
        return Resp(200, [])

    def post(self, path, json=None, headers=None):
        self.log.append(("POST", path))
        if "/kv_stats" in path:
            if self.kv_status:
                return Resp(self.kv_status)
            if not self.kv_exists:
                return Resp(404)
            self.kv[json["key"]] = json["value"]
            return Resp(201)
        if "/user_favorites" in path:
            self.legacy_row = {"wallet": json["wallet"], "favorites": json["favorites"], "updated_at": json["updated_at"]}
            return Resp(201)
        return Resp(201)

    def close(self):
        pass


def mgr(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))
    for k in list(os.environ):
        if k.startswith("SUPABASE"):
            monkeypatch.delenv(k)
    m = DatabaseManager(db_path=str(tmp_path / "t.db"))
    m.mode = "supabase"
    m._client_instance, m._client_pid = fake, os.getpid()
    m._flusher_pid = os.getpid()
    m._init_sqlite()
    return m


def test_the_counter_is_written_to_kv_stats_not_to_the_fake_wallet(tmp_path, monkeypatch):
    fake = Fake()
    m = mgr(tmp_path, monkeypatch, fake)
    m.save_agent_id_stats(STATS, force_cloud=True)
    assert fake.kv["agent_id_stats"]["total_created_wallets"] == 263788
    assert fake.legacy_row["favorites"][0]["total_created_wallets"] == 111          # the old row is no longer touched
    m._agent_stats_cache = None
    assert m.get_agent_id_stats(max_age_sec=0.0)["total_created_wallets"] == 263788


def test_without_the_table_the_counter_is_kept_locally_and_the_old_row_is_never_used(tmp_path, monkeypatch):
    """The fake wallet row was retired (migration 006): no table means local SQLite only, not a read or write of user_favorites."""
    fake = Fake(kv_exists=False)
    m = mgr(tmp_path, monkeypatch, fake)
    assert m.get_agent_id_stats(max_age_sec=0.0) is None                              # the legacy row (111) is not consulted
    m.save_agent_id_stats(STATS, force_cloud=True)
    assert fake.legacy_row["favorites"][0]["total_created_wallets"] == 111          # never written
    assert not any("user_favorites" in path for _, path in fake.log)
    assert m._kv_stats_is_off() is True
    m._agent_stats_cache = None
    assert m.get_agent_id_stats(max_age_sec=0.0)["total_created_wallets"] == 263788  # served from the local SQLite copy


def test_a_passing_server_error_does_not_park_the_counter_on_the_old_row(tmp_path, monkeypatch):
    fake = Fake(kv_status=503)
    m = mgr(tmp_path, monkeypatch, fake)
    m.get_agent_id_stats(max_age_sec=0.0)
    assert not m._kv_stats_is_off()


def test_sqlite_keeps_the_counter_in_kv_stats(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("SUPABASE"):
            monkeypatch.delenv(k)
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))
    m = DatabaseManager(db_path=str(tmp_path / "s.db"))
    m.save_agent_id_stats(STATS)
    m._agent_stats_cache = None
    assert m.get_agent_id_stats(max_age_sec=0.0)["total_created_wallets"] == 263788
    import sqlite3
    rows = sqlite3.connect(str(tmp_path / "s.db")).execute("SELECT key FROM kv_stats").fetchall()
    assert rows == [("agent_id_stats",)]


def test_the_table_is_picked_up_after_the_migration_without_a_restart(tmp_path, monkeypatch):
    fake = Fake(kv_exists=False)
    m = mgr(tmp_path, monkeypatch, fake)
    m.save_agent_id_stats(STATS, force_cloud=True)
    assert m._kv_stats_is_off() and "agent_id_stats" not in fake.kv
    fake.kv_exists = True                                            # the migration was run
    m._kv_stats_off_until = 0.0                                      # ... and the retry window has passed
    m.save_agent_id_stats(dict(STATS, total_created_wallets=263999), force_cloud=True)
    assert fake.kv["agent_id_stats"]["total_created_wallets"] == 263999 and not m._kv_stats_is_off()
