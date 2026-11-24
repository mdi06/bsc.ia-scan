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
    h = {"X-Forwarded-For": "203.0.113.9"}      # what Railway's edge sends: the address it saw
    for _ in range(3):
        assert client.get("/api/favorites?wallet=bad", headers=h).status_code == 400
    r = client.get("/api/favorites?wallet=bad", headers=h)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    # other clients and exempt health routes are unaffected
    assert client.get("/api/favorites?wallet=bad", headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 400
    assert client.get("/healthz", headers=h).status_code == 200
    api._rate_counts.clear()


def test_rate_limit_ignores_client_written_forwarded_entries(monkeypatch):
    """The edge appends the real address LAST; a client can only write the entries before it."""
    monkeypatch.setattr(api, "RATE_LIMIT_MAX", 2)
    api._rate_counts.clear()
    for i in range(2):
        h = {"X-Forwarded-For": f"198.51.100.{i}, 203.0.113.50"}      # a fresh fake address on every request
        assert client.get("/api/favorites?wallet=bad", headers=h).status_code == 400
    h = {"X-Forwarded-For": "198.51.100.99, 203.0.113.50"}
    assert client.get("/api/favorites?wallet=bad", headers=h).status_code == 429
    api._rate_counts.clear()


def test_rate_limit_ignores_cf_connecting_ip_unless_cloudflare_is_in_front(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_MAX", 2)
    api._rate_counts.clear()
    for i in range(2):                 # a different forged CF header each time must not buy a fresh allowance
        h = {"CF-Connecting-IP": f"198.51.100.{i}", "X-Forwarded-For": "203.0.113.60"}
        assert client.get("/api/favorites?wallet=bad", headers=h).status_code == 400
    h = {"CF-Connecting-IP": "198.51.100.77", "X-Forwarded-For": "203.0.113.60"}
    assert client.get("/api/favorites?wallet=bad", headers=h).status_code == 429
    api._rate_counts.clear()
    monkeypatch.setattr(api, "RATE_LIMIT_TRUST_CF", True)
    for i in range(2):
        h = {"CF-Connecting-IP": "198.51.100.5", "X-Forwarded-For": f"203.0.113.{i}"}
        assert client.get("/api/favorites?wallet=bad", headers=h).status_code == 400
    assert client.get("/api/favorites?wallet=bad", headers={"CF-Connecting-IP": "198.51.100.5"}).status_code == 429
    api._rate_counts.clear()


def test_client_ip_falls_back_to_the_socket_address(monkeypatch):
    class Req:
        def __init__(self, headers, host="10.0.0.7"):
            self.headers = headers
            self.client = type("C", (), {"host": host})() if host else None
    assert api._client_ip(Req({})) == "10.0.0.7"                              # no header: the socket peer
    assert api._client_ip(Req({"x-forwarded-for": " , "})) == "10.0.0.7"      # empty entries
    assert api._client_ip(Req({"x-forwarded-for": "1.1.1.1, 2.2.2.2"})) == "2.2.2.2"
    assert api._client_ip(Req({}, host=None)) == "unknown"
    monkeypatch.setattr(api, "RATE_LIMIT_PROXY_HOPS", 2)
    assert api._client_ip(Req({"x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3"})) == "2.2.2.2"
    assert api._client_ip(Req({"x-forwarded-for": "3.3.3.3"})) == "10.0.0.7"  # fewer entries than trusted hops


def test_clean_chain_text_strips_markup_and_control_chars():
    """Token names come from the contract deployer: markup and control characters never reach the database."""
    import monitor
    assert monitor.clean_chain_text("<img src=x onerror=alert(1)>Coin") == "img src=x onerror=alert(1)Coin"
    assert monitor.clean_chain_text("Don't Panic") == "Don't Panic"          # real names with quotes are kept
    assert monitor.clean_chain_text("黄仁勋人生") == "黄仁勋人生"
    assert monitor.clean_chain_text("a\x00b\x1fc`d\x7f") == "abcd"
    assert monitor.clean_chain_text("x" * 200) == "x" * 80
    assert monitor.clean_chain_text("") == "" and monitor.clean_chain_text(None) == ""
