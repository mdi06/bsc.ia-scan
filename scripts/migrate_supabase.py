#!/usr/bin/env python3
"""
Supabase Zero-Data-Loss Migration Tool for Agent ID Monitor.

Usage:
  1. Full export from Old Supabase (optional, history.json is already a complete local backup):
     python3 migrate_supabase.py export --url "https://old.supabase.co" --key "old_key"

  2. Seed / Import into New Supabase:
     python3 migrate_supabase.py import --url "https://new.supabase.co" --key "new_key"

  3. Direct 1-step migration between Old and New Supabase:
     python3 migrate_supabase.py migrate --old-url "..." --old-key "..." --new-url "..." --new-key "..."
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error

def clean_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[:-8].rstrip("/")
    return url

def make_request(url: str, key: str, method: str = "GET", payload: list = None, headers: dict = None):
    req_headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "AgentID-Migrator/1.0"
    }
    if headers:
        req_headers.update(headers)
    
    data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        if body:
            return json.loads(body)
        return None

def export_old_supabase(old_url: str, old_key: str):
    base_url = clean_url(old_url)
    print(f"\n[📦 Exporting] Connecting to Old Supabase: {base_url}...")
    
    # 1. Export User Favorites
    try:
        fav_url = f"{base_url}/rest/v1/user_favorites?select=*"
        favs = make_request(fav_url, old_key) or []
        with open("user_favorites_backup.json", "w") as f:
            json.dump(favs, f, indent=2)
        print(f"✔ Exported {len(favs)} user favorite records to user_favorites_backup.json")
    except Exception as e:
        print(f"⚠️ Could not export user_favorites (may be empty or RLS protected): {e}")
        favs = []

    # 2. Export Projects
    try:
        projs_url = f"{base_url}/rest/v1/projects?select=*&limit=10000"
        projs = make_request(projs_url, old_key) or []
        with open("projects_backup.json", "w") as f:
            json.dump(projs, f, indent=2)
        print(f"✔ Exported {len(projs)} projects to projects_backup.json")
    except Exception as e:
        print(f"⚠️ Could not export projects: {e}")
        projs = []

    return favs, projs

def import_into_new_supabase(new_url: str, new_key: str, source_file: str = "history.json"):
    base_url = clean_url(new_url)
    print(f"\n[🚀 Importing] Seeding New Supabase: {base_url}...")

    # Load drops
    if os.path.exists("projects_backup.json"):
        with open("projects_backup.json") as f:
            raw_backup = json.load(f)
            # Normalize to drop objects
            drops = [r.get("data") or r for r in raw_backup]
            print(f"✔ Loaded {len(drops)} drops from projects_backup.json")
    elif os.path.exists(source_file):
        with open(source_file) as f:
            drops = json.load(f)
            print(f"✔ Loaded {len(drops)} drops from {source_file}")
    else:
        print(f"❌ Error: Neither projects_backup.json nor {source_file} found!")
        return False

    # Format rows for Supabase
    now_ts = int(time.time())
    rows = []
    seen = set()
    for d in drops:
        c = (d.get("contract") or "").lower()
        if not c or c in seen:
            continue
        seen.add(c)
        rows.append({
            "contract": c,
            "block": int(d.get("block") or 0),
            "updated_at": now_ts,
            "data": d
        })

    print(f"[📤 Uploading] Upserting {len(rows)} unique projects in batches of 50...")
    batch_size = 50
    success_count = 0
    upsert_url = f"{base_url}/rest/v1/projects?on_conflict=contract"
    prefer_headers = {"Prefer": "resolution=merge-duplicates"}

    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        try:
            make_request(upsert_url, new_key, method="POST", payload=chunk, headers=prefer_headers)
            success_count += len(chunk)
            print(f"  Progress: {success_count}/{len(rows)} projects upserted...", end="\r", flush=True)
        except Exception as e:
            print(f"\n⚠️ Chunk error at index {i}: {e}")
            time.sleep(1)

    print(f"\n✔ Successfully imported {success_count}/{len(rows)} projects into new Supabase!")

    # Import favorites if backup exists
    fav_file = "user_favorites_backup.json" if os.path.exists("user_favorites_backup.json") else "user_favorites.json"
    if os.path.exists(fav_file):
        try:
            with open(fav_file) as f:
                fav_data = json.load(f)
            fav_rows = []
            if isinstance(fav_data, list):
                fav_rows = fav_data
            elif isinstance(fav_data, dict):
                for w, favs in fav_data.items():
                    fav_rows.append({"wallet": w.lower(), "favorites": favs, "updated_at": now_ts})
            if fav_rows:
                fav_upsert_url = f"{base_url}/rest/v1/user_favorites?on_conflict=wallet"
                make_request(fav_upsert_url, new_key, method="POST", payload=fav_rows, headers=prefer_headers)
                print(f"✔ Restored {len(fav_rows)} user favorites records.")
        except Exception as e:
            print(f"⚠️ Favorites restore note: {e}")

    # Verify Count
    try:
        check_url = f"{base_url}/rest/v1/projects?select=contract"
        res = make_request(check_url, new_key)
        print(f"\n🎉 Verification: New Supabase currently contains {len(res)} projects!")
    except Exception as e:
        print(f"Verification query: {e}")

    return True

def main():
    parser = argparse.ArgumentParser(description="Zero-Data-Loss Migration Tool for Agent ID Monitor")
    subparsers = parser.add_subparsers(dest="command")

    # Export
    p_exp = subparsers.add_parser("export", help="Export data from old Supabase")
    p_exp.add_argument("--url", required=True, help="Old Supabase URL")
    p_exp.add_argument("--key", required=True, help="Old Supabase Anon or Service Key")

    # Import
    p_imp = subparsers.add_parser("import", help="Import data into new Supabase")
    p_imp.add_argument("--url", required=True, help="New Supabase URL")
    p_imp.add_argument("--key", required=True, help="New Supabase Anon or Service Key")
    p_imp.add_argument("--source", default="history.json", help="Local source JSON (default: history.json)")

    # Direct Migrate
    p_mig = subparsers.add_parser("migrate", help="Direct 1-step migration from old to new")
    p_mig.add_argument("--old-url", required=True, help="Old Supabase URL")
    p_mig.add_argument("--old-key", required=True, help="Old Supabase Key")
    p_mig.add_argument("--new-url", required=True, help="New Supabase URL")
    p_mig.add_argument("--new-key", required=True, help="New Supabase Key")

    args = parser.parse_args()

    if args.command == "export":
        export_old_supabase(args.url, args.key)
    elif args.command == "import":
        import_into_new_supabase(args.url, args.key, args.source)
    elif args.command == "migrate":
        export_old_supabase(args.old_url, args.old_key)
        import_into_new_supabase(args.new_url, args.new_key)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()

