"""projects.progress and projects.status held a constant on every row and nothing read them (the API derives both from the record).
The writers no longer send them, and a local SQLite file created with the old columns keeps working (they have defaults)."""
import sqlite3

import db as db_module


def _env(tmp_path, monkeypatch):
    for var in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))


def test_the_supabase_row_has_no_progress_or_status(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    m = db_module.DatabaseManager(db_path=str(tmp_path / "t.db"))
    row = m._supabase_base_row({"contract": "0x" + "a" * 40, "progress": 55.0, "status": "MINTING"}, 1)
    assert "progress" not in row and "status" not in row and row["holders"] == 0


def test_a_fresh_sqlite_file_has_no_such_columns_and_an_old_one_still_accepts_writes(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    fresh = tmp_path / "fresh.db"
    m = db_module.DatabaseManager(db_path=str(fresh))
    m.upsert_projects([{"contract": "0x" + "1" * 40, "name": "A", "block": 1}])
    cols = {r[1] for r in sqlite3.connect(str(fresh)).execute("PRAGMA table_info(projects)")}
    assert "progress" not in cols and "status" not in cols

    old = tmp_path / "old.db"
    con = sqlite3.connect(str(old))
    con.execute("""CREATE TABLE projects (contract TEXT PRIMARY KEY, name TEXT, symbol TEXT, token_id TEXT, block INTEGER DEFAULT 0,
        progress REAL DEFAULT 0.0, holders INTEGER DEFAULT 0, status TEXT DEFAULT 'TRACKING', risk_level TEXT DEFAULT 'SAFE',
        risk_badge TEXT DEFAULT 'x', sniper_ready INTEGER DEFAULT 0, is_proxy_clone INTEGER DEFAULT 0, score REAL DEFAULT 0.0,
        raw_data TEXT NOT NULL, detected_at TEXT, updated_at INTEGER DEFAULT 0)""")
    con.commit(); con.close()
    m2 = db_module.DatabaseManager(db_path=str(old))
    m2.upsert_projects([{"contract": "0x" + "2" * 40, "name": "B", "block": 2}])
    assert [p["name"] for p in m2.get_all_projects()] == ["B"]
