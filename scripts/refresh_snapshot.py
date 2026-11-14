#!/usr/bin/env python3
"""Refresh feed_snapshot.json from the live backend.

app.js paints feed_snapshot.json first (and falls back to it while the Render backend is asleep), so a
stale snapshot means a stale first paint. This fetches /api/feed?limit=150 and writes its `projects`
array to feed_snapshot.json in the same format app.js reads: a bare JSON array of project objects.

    python3 scripts/refresh_snapshot.py                       # production backend
    python3 scripts/refresh_snapshot.py --base http://localhost:8080

Stdlib only. The timeout is long because a cold Render instance takes ~35 s to answer.
"""

import argparse
import json
import os
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASE = "https://bsc-ia-scan.onrender.com"
DEFAULT_OUT = os.path.join(ROOT, "feed_snapshot.json")


def fetch_projects(base: str, limit: int, timeout: float) -> list:
    url = f"{base.rstrip('/')}/api/feed?limit={limit}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "refresh-snapshot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        payload = json.load(res)
    projects = payload if isinstance(payload, list) else (payload or {}).get("projects")
    if not isinstance(projects, list) or not projects:
        raise SystemExit(f"{url} returned no projects; feed_snapshot.json left unchanged")
    return projects


def write_atomic(path: str, projects: list) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".feed_snapshot.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(projects, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"backend base URL (default {DEFAULT_BASE})")
    ap.add_argument("--limit", type=int, default=150, help="rows to fetch (default 150, what app.js loads first)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output file (default feed_snapshot.json in the repo root)")
    ap.add_argument("--timeout", type=float, default=90.0, help="request timeout in seconds (default 90)")
    args = ap.parse_args()

    projects = fetch_projects(args.base, args.limit, args.timeout)
    write_atomic(args.out, projects)
    newest = max((int(p.get("block") or 0) for p in projects), default=0)
    print(f"wrote {len(projects)} projects to {args.out} (newest block {newest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
