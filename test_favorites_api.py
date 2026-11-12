import threading

import pytest
from fastapi.testclient import TestClient

import api
import db as db_module

WALLET = "0xa15bb4aedc1b3426159c3cacf8548cf09506a20a"
FAV_A = "0x105d904d64520d9bcefb66af936c2cce59037777"
FAV_B = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
FAV_C = "0x55d398326f99059ff775485246999027b3197955"


@pytest.fixture
def fav_db(tmp_path, monkeypatch):
    """A throwaway SQLite-backed DatabaseManager, installed as api.db for the test."""
    for var in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(var, raising=False)
    # Don't seed from the repo's real history.json / user_favorites.json.
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))
    monkeypatch.setattr(db_module, "FAVORITES_FILE", str(tmp_path / "no_favorites.json"))
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "fav.db"))
    monkeypatch.setattr(api, "db", manager)
    return manager


@pytest.fixture
def client(fav_db):
    return TestClient(api.app)


def test_db_save_and_load_favorites(fav_db):
    assert fav_db.get_favorites(WALLET)["favorites"] == []

    fav_db.save_favorites(WALLET, [FAV_A], 1730000000)
    record = fav_db.get_favorites(WALLET)
    assert record["favorites"] == [FAV_A]
    assert record["updated_at"] == 1730000000


def test_concurrent_favorites_write(fav_db):
    def writer(idx):
        fav_db.save_favorites(f"0x{idx:040x}", [f"0x{idx + 1:040x}"], idx + 1)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i in range(10):
        assert fav_db.get_favorites(f"0x{i:040x}")["favorites"] == [f"0x{i + 1:040x}"]


def test_http_favorites_endpoints(client):
    # Missing wallet parameter -> FastAPI validation error
    assert client.get("/api/favorites").status_code == 422
    # Malformed wallet
    assert client.get("/api/favorites", params={"wallet": "0x123"}).status_code == 400

    # Empty favorites for a new wallet
    res = client.get("/api/favorites", params={"wallet": WALLET})
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert res.json()["favorites"] == []

    # Save
    post = client.post(
        "/api/favorites",
        json={"wallet": WALLET, "favorites": [FAV_A, FAV_B], "timestamp": 1730500000},
    )
    assert post.status_code == 200
    assert post.json()["success"] is True
    assert len(post.json()["favorites"]) == 2

    # Read back
    got = client.get("/api/favorites", params={"wallet": WALLET}).json()
    assert got["favorites"] == [FAV_A, FAV_B]
    assert got["updated_at"] == 1730500000


def test_http_favorites_rejects_bad_input(client):
    assert client.post("/api/favorites", content=b"not json").status_code == 400
    assert client.post("/api/favorites", json=["a"]).status_code == 400
    assert client.post("/api/favorites", json={"wallet": "nope", "favorites": []}).status_code == 400

    too_many = [f"0x{i:040x}" for i in range(api.MAX_FAVORITES + 1)]
    assert client.post("/api/favorites", json={"wallet": WALLET, "favorites": too_many}).status_code == 413


def test_http_favorites_legacy_alias(client):
    """/favorites is kept as an alias of /api/favorites for older clients."""
    client.post("/favorites", json={"wallet": WALLET, "favorites": [FAV_A], "timestamp": 5})
    assert client.get("/favorites", params={"wallet": WALLET}).json()["favorites"] == [FAV_A]


def test_cross_device_sync_scenario(client):
    wallet = "0x6827d403df6b07ba405a6cfe0305dbd0fc727f55"

    # Desktop stars two tokens
    desktop_favs = [FAV_A, FAV_B]
    res1 = client.post("/api/favorites", json={"wallet": wallet, "favorites": desktop_favs, "timestamp": 1000})
    assert res1.status_code == 200

    # Mobile opens the app with the same wallet and fetches favorites
    mobile_data = client.get("/api/favorites", params={"wallet": wallet}).json()
    assert set(mobile_data["favorites"]) == set(desktop_favs)

    # Mobile stars one more token and pushes the merged list
    mobile_favs = list(set(mobile_data["favorites"]) | {FAV_C})
    res2 = client.post("/api/favorites", json={"wallet": wallet, "favorites": mobile_favs, "timestamp": 2000})
    assert res2.status_code == 200

    # Desktop re-syncs and sees all three
    final = client.get("/api/favorites", params={"wallet": wallet}).json()
    assert len(final["favorites"]) == 3
    assert FAV_C in final["favorites"]
