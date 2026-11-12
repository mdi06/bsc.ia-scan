import pytest
from fastapi.testclient import TestClient

import api
import db as db_module


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    for var in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))
    monkeypatch.setattr(db_module, "FAVORITES_FILE", str(tmp_path / "no_favorites.json"))
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(api, "db", manager)
    return manager


def _proj(n, **kw):
    d = {"contract": f"0x{n:040x}", "block": n, "name": f"N{n}", "symbol": f"S{n}"}
    d.update(kw)
    return d


def test_history_etag_changes_when_body_changes_with_same_length_and_prefix(tmp_db):
    # Both bodies have identical length and an identical first 128 bytes; only a late field differs.
    tmp_db.upsert_projects([_proj(1, holders=1, symbol="AAAA", name="x" * 200)])
    client = TestClient(api.app)
    first = client.get("/history.json")
    tmp_db.upsert_projects([_proj(1, holders=2, symbol="AAAA", name="x" * 200)])
    second = client.get("/history.json")
    assert first.headers["etag"] != second.headers["etag"]
    assert first.content != second.content


def test_history_etag_304_roundtrip(tmp_db):
    tmp_db.upsert_projects([_proj(1)])
    client = TestClient(api.app)
    etag = client.get("/history.json").headers["etag"]
    assert client.get("/history.json", headers={"If-None-Match": etag}).status_code == 304
    assert client.get("/history.json", headers={"If-None-Match": "W/" + etag}).status_code == 304


def test_etag_matching_strips_prefix_not_characters():
    assert api._etag_matches('W/"abc"', '"abc"')
    assert not api._etag_matches('"Wabc"', '"abc"')


def test_search_survives_null_fields(tmp_db):
    tmp_db.upsert_projects([_proj(1, name=None, symbol=None, status=None), _proj(2, name="Match")])
    res = tmp_db.get_projects(search="match", status="tracking")
    assert res["total"] == 0  # no crash; status filter excludes both
    res = tmp_db.get_projects(search="match")
    assert [p["name"] for p in res["projects"]] == ["Match"]


def test_delete_projects_closes_connection_on_error(tmp_db, monkeypatch):
    closed = []

    class FakeConn:
        def cursor(self):
            raise RuntimeError("boom")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(tmp_db, "_get_sqlite_connection", lambda: FakeConn())
    assert tmp_db.delete_projects(["0x" + "1" * 40]) == 0
    assert closed == [True]
