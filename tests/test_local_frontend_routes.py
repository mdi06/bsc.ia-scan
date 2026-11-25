"""Local development: "/" serves index.html, so the files it requests by relative URL must answer at the root too.

On Render (RENDER set) none of these routes exist: the frontend is served by Vercel and the API must not expose files.
"""
import os
import re
import subprocess
import sys

from fastapi.testclient import TestClient

import api

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_root_serves_the_frontend_assets_locally():
    client = TestClient(api.app)
    for name in api.LOCAL_FRONTEND_FILES:
        if not os.path.isfile(os.path.join(HERE, name)):
            continue
        r = client.get("/" + name)
        assert r.status_code == 200, name
        with open(os.path.join(HERE, name), "rb") as f:
            assert r.content == f.read(), name
        assert r.headers["cache-control"] == "no-cache"
    r = client.get("/app.js")
    assert "javascript" in r.headers["content-type"]
    assert "css" in client.get("/style.css").headers["content-type"]


def test_every_asset_index_html_loads_by_relative_url_is_served():
    """index.html's own <script src> / <link href> files must be in the served list."""
    with open(os.path.join(HERE, "index.html"), encoding="utf-8") as f:
        html = f.read()
    local_refs = set(re.findall(r'(?:src|href)="([A-Za-z0-9_\-]+\.(?:js|css|svg))"', html))
    assert local_refs, "expected app.js / style.css / icon.svg"
    assert local_refs <= set(api.LOCAL_FRONTEND_FILES), local_refs - set(api.LOCAL_FRONTEND_FILES)


def test_only_the_listed_files_are_exposed_at_the_root():
    client = TestClient(api.app)
    for path in ("/monitor.py", "/api.py", "/db.py", "/worker.py", "/requirements.txt", "/README.md",
                 "/.gitignore", "/data/agent_monitor.db", "/package.json"):
        assert client.get(path).status_code == 404, path


def test_existing_routes_are_not_shadowed():
    client = TestClient(api.app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/history.json").headers["content-type"].startswith("application/json")
    root = client.get("/")
    assert root.status_code == 200
    assert root.headers["cache-control"] == "no-cache"


def test_the_workspace_is_never_mounted():
    """A /static mount of the whole directory served api.py, history.json and smart_wallets.json from the live API."""
    client = TestClient(api.app)
    for path in ("/static/api.py", "/static/history.json", "/static/smart_wallets.json", "/static/user_favorites.json",
                 "/features/../api.py", "/features/%2e%2e/api.py"):
        assert client.get(path).status_code == 404, path


def test_running_hosted():
    assert api.running_hosted({"RENDER": "true"})
    assert api.running_hosted({"RAILWAY_ENVIRONMENT_NAME": "production"})
    assert api.running_hosted({"RAILWAY_SERVICE_ID": "abc"})
    assert not api.running_hosted({"HOME": "/x", "RAILWAY_EMPTY": ""})
    assert not api.running_hosted({})


def _hosted_paths(extra_env: dict) -> str:
    code = (
        "import api\n"
        "paths = {getattr(r, 'path', None) for r in api.app.routes}\n"
        "bad = [p for p in ('/app.js', '/style.css', '/static', '/features', '/seeded_smart_drops.json') if p in paths]\n"
        "print('BAD:' + ','.join(bad))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SUPABASE", "RAILWAY_")) and k != "RENDER"}
    env.update(extra_env)
    out = subprocess.run([sys.executable, "-c", code], cwd=HERE, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    return out.stdout.strip().splitlines()[-1]


def test_no_frontend_routes_on_render():
    """Imported in a fresh interpreter with RENDER set, the way Render runs it."""
    assert _hosted_paths({"RENDER": "1"}) == "BAD:"


def test_no_frontend_routes_on_railway():
    """Railway sets RAILWAY_* variables, never RENDER: production runs here."""
    assert _hosted_paths({"RAILWAY_ENVIRONMENT_NAME": "production", "RAILWAY_SERVICE_ID": "svc"}) == "BAD:"


def test_index_html_has_no_call_to_a_missing_function():
    """The pulse card's "View All-Time" link used to call openAllTimeProfitModal(), which never existed."""
    with open(os.path.join(HERE, "index.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(HERE, "app.js"), encoding="utf-8") as f:
        js = f.read()
    assert "openAllTimeProfitModal" not in html
    assert "onclick=\"openHallOfFameModal('ALL')\"" in html
    assert re.search(r"^function openHallOfFameModal\(", js, re.M)
