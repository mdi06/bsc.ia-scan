import db as db_module
from db import DatabaseManager


def _supabase_mode_manager(tmp_path):
    """Supabase mode whose SQLite folder does not exist (a fresh Render deploy: data/ is gitignored)."""
    mgr = DatabaseManager(db_path=str(tmp_path / "init.db"))  # constructor runs in SQLite mode
    mgr.mode = "supabase"
    mgr.db_path = str(tmp_path / "missing_dir" / "agent_monitor.db")
    assert not (tmp_path / "missing_dir").exists()
    return mgr


def test_sqlite_connection_creates_missing_directory(tmp_path):
    mgr = _supabase_mode_manager(tmp_path)
    mgr._get_sqlite_connection().close()
    assert (tmp_path / "missing_dir" / "agent_monitor.db").exists()


def test_get_agent_id_stats_falls_back_without_crashing(tmp_path, monkeypatch):
    mgr = _supabase_mode_manager(tmp_path)
    monkeypatch.setattr(db_module, "HAS_HTTPX", False)  # no cloud row -> SQLite fallback path
    assert mgr.get_agent_id_stats(max_age_sec=0.0) is None
