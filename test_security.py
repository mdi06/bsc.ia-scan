import pytest
from fastapi.testclient import TestClient

import api

client = TestClient(api.app, raise_server_exceptions=False)

ADMIN_ROUTES = [
    ("post", "/api/admin/purge-bogus"),
    ("delete", "/api/projects/0x" + "a" * 40),
    ("post", "/api/sync"),
    ("post", "/sync"),
]


def _call(method, path, headers=None):
    kwargs = {"headers": headers or {}}
    if method == "post" and "sync" in path:
        kwargs["json"] = []
    return getattr(client, method)(path, **kwargs)


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_admin_disabled_when_token_unset(method, path, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert _call(method, path).status_code == 503
    # Even a bearer header must not open an unconfigured endpoint
    assert _call(method, path, {"Authorization": "Bearer anything"}).status_code == 503
    monkeypatch.setenv("ADMIN_TOKEN", "")
    assert _call(method, path, {"Authorization": "Bearer "}).status_code == 503


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_missing_token_is_401(method, path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    assert _call(method, path).status_code == 401


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_wrong_token_is_401(method, path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    assert _call(method, path, {"Authorization": "Bearer wrong"}).status_code == 401
    assert _call(method, path, {"Authorization": "Basic s3cret"}).status_code == 401


def test_correct_token_passes_auth(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    monkeypatch.setattr(api.db, "delete_projects", lambda targets: len(targets))
    r = client.post("/api/admin/purge-bogus", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_unhandled_error_does_not_leak_details(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret-internal-detail")

    monkeypatch.setattr(api.db, "get_project_count", boom)
    r = client.get("/healthz")
    assert r.status_code == 500
    assert r.json() == {"success": False, "error": "internal_error"}
    assert "secret-internal-detail" not in r.text
    assert "traceback" not in r.text.lower()


WALLET = "0x" + "a" * 40


def test_favorites_cap_returns_413(monkeypatch):
    monkeypatch.setattr(api.db, "save_favorites", lambda *a, **k: True)
    favs = [f"0x{i:040x}" for i in range(501)]
    r = client.post("/api/favorites", json={"wallet": WALLET, "favorites": favs})
    assert r.status_code == 413
    ok = client.post("/api/favorites", json={"wallet": WALLET, "favorites": favs[:500]})
    assert ok.status_code == 200 and len(ok.json()["favorites"]) == 500


def test_favorites_address_validation(monkeypatch):
    monkeypatch.setattr(api.db, "save_favorites", lambda *a, **k: True)
    assert client.post("/api/favorites", json={"wallet": "0x" + "z" * 40, "favorites": []}).status_code == 400
    assert client.post("/api/favorites", json={"wallet": WALLET[:-1], "favorites": []}).status_code == 400
    good = "0x" + "B" * 40
    bad = ["0x" + "g" * 40, "0x" + "c" * 39, "0x" + "c" * 40 + "\n"]
    r = client.post("/api/favorites", json={"wallet": WALLET, "favorites": [good] + bad})
    assert r.status_code == 200
    assert r.json()["favorites"] == [good.lower()]


def test_startup_does_not_purge(monkeypatch):
    called = []
    monkeypatch.setattr(api.db, "delete_projects", lambda t: called.append(t) or 0)
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    api.startup_event()
    assert called == []


def test_rate_limit_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_MAX", 3)
    api._rate_counts.clear()
    h = {"Fly-Client-IP": "203.0.113.9"}
    for _ in range(3):
        assert client.get("/api/favorites?wallet=bad", headers=h).status_code == 400
    r = client.get("/api/favorites?wallet=bad", headers=h)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    # other clients and exempt health routes are unaffected
    assert client.get("/api/favorites?wallet=bad", headers={"Fly-Client-IP": "203.0.113.10"}).status_code == 400
    assert client.get("/healthz", headers=h).status_code == 200
    api._rate_counts.clear()
