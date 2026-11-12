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
