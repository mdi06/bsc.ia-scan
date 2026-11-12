"""
Unit & Integration Tests for Phase 1: Storage & Web Server Modernization.
Tests:
- DatabaseManager (SQLite WAL, Seeding, Batch Upserts, Favorites)
- FastAPI Endpoints (/healthz, /history.json, /api/projects, /api/favorites, /api/sync)
"""

import os
import json
import pytest
from fastapi.testclient import TestClient
from db import DatabaseManager
from server import app

# Disable background threads during tests
os.environ["DISABLE_BACKGROUND_WORKERS"] = "1"


@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_monitor.db")
    manager = DatabaseManager(db_path=db_file)
    return manager


def test_sqlite_wal_init_and_seed(temp_db):
    """Verifies that SQLite initializes properly in WAL mode and seeds records."""
    assert temp_db.mode == "sqlite"
    count = temp_db.get_project_count()
    assert count > 0  # auto-seeded from history.json

    # Test PRAGMA journal_mode is WAL
    conn = temp_db._get_sqlite_connection()
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode;")
    mode = cur.fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_db_batch_upsert_and_query(temp_db):
    """Tests batch upserting projects and querying with pagination & filtering."""
    new_projects = [
        {
            "contract": "0x1111111111111111111111111111111111111111",
            "name": "Alpha Agent",
            "symbol": "ALPHA",
            "block": 40000000,
            "progress": 85.5,
            "holders": 120,
            "status": "TRACKING",
            "sniper_ready": True
        },
        {
            "contract": "0x2222222222222222222222222222222222222222",
            "name": "Beta Token",
            "symbol": "BETA",
            "block": 40000001,
            "progress": 15.0,
            "holders": 10,
            "status": "COMPLETED",
            "sniper_ready": False
        }
    ]
    temp_db.upsert_projects(new_projects)

    # 1. Single lookup
    p1 = temp_db.get_project("0x1111111111111111111111111111111111111111")
    assert p1 is not None
    assert p1["name"] == "Alpha Agent"
    assert p1["sniper_ready"] is True

    # 2. Filter by search
    res = temp_db.get_projects(search="Alpha")
    assert res["success"] is True
    assert any(x["contract"] == "0x1111111111111111111111111111111111111111" for x in res["projects"])

    # 3. Filter by sniper_ready
    sniper_res = temp_db.get_projects(sniper_ready=True)
    assert all(x.get("sniper_ready") is True for x in sniper_res["projects"])

    # 4. Sort by progress descending
    sorted_res = temp_db.get_projects(sort_by="progress", order="desc")
    progs = [float(x.get("progress") or 0.0) for x in sorted_res["projects"]]
    assert progs == sorted(progs, reverse=True)


def test_db_favorites_crud(temp_db):
    """Tests saving and retrieving multi-wallet favorites in SQLite."""
    wallet = "0x9999999999999999999999999999999999999999"
    favs = ["0x105d904d64520d9bcefb66af936c2cce59037777", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"]
    
    saved = temp_db.save_favorites(wallet, favs, timestamp=1730001111)
    assert saved["wallet"] == wallet
    assert len(saved["favorites"]) == 2

    retrieved = temp_db.get_favorites(wallet)
    assert retrieved["wallet"] == wallet
    assert retrieved["favorites"] == favs
    assert retrieved["updated_at"] == 1730001111


def test_fastapi_endpoints(tmp_path, monkeypatch):
    """Tests the modernized FastAPI application using TestClient."""
    import monitor
    orig_file = monitor.HISTORY_FILE
    try:
        monitor.HISTORY_FILE = str(tmp_path / "test_history.json")
        client = TestClient(app)

        # 1. Health check
        res = client.get("/healthz")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "db_mode" in data
        assert "project_count" in data

        # 2. Virtual history.json endpoint
        res_hist = client.get("/history.json")
        assert res_hist.status_code == 200
        hist_data = res_hist.json()
        assert isinstance(hist_data, list)
        assert len(hist_data) > 0

        # 3. /api/projects with query filtering
        res_proj = client.get("/api/projects?limit=5&sort_by=block&order=desc")
        assert res_proj.status_code == 200
        proj_data = res_proj.json()
        assert proj_data["success"] is True
        assert proj_data["count"] <= 5
        assert len(proj_data["projects"]) <= 5

        # 4. /api/favorites validation (invalid wallet)
        res_bad = client.get("/api/favorites?wallet=invalid")
        assert res_bad.status_code == 400

        # 5. /api/favorites valid flow
        test_wallet = "0x8888888888888888888888888888888888888888"
        post_fav = client.post("/api/favorites", json={
            "wallet": test_wallet,
            "favorites": ["0x105d904d64520d9bcefb66af936c2cce59037777"]
        })
        assert post_fav.status_code == 200
        assert post_fav.json()["success"] is True

        get_fav = client.get(f"/api/favorites?wallet={test_wallet}")
        assert get_fav.status_code == 200
        assert get_fav.json()["favorites"] == ["0x105d904d64520d9bcefb66af936c2cce59037777"]

        # 6. /api/sync (admin-only since the A1 security change: needs bearer ADMIN_TOKEN)
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        sync_res = client.post("/api/sync", headers={"Authorization": "Bearer test-admin-token"}, json=[
            {"contract": "0x3333333333333333333333333333333333333333", "name": "Sync Test", "block": 50000000}
        ])
        assert sync_res.status_code == 200
        assert sync_res.json()["success"] is True
    finally:
        monitor.HISTORY_FILE = orig_file

