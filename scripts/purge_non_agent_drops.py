#!/usr/bin/env python3
"""Find (and optionally delete) records that entered the feed only because a tracked smart wallet called an
unrelated contract: USD1, SOON, Tether Gold and the like.

Selection, all of which must hold:
  1. the record's signals say "Discovered via smart wallet ..." (so it did not come from the registry/deploy path);
  2. the contract is NOT linked to the Agent ID registry or a tracked launchpad on-chain
     (monitor.contract_links_to_registry, the same test the worker now applies before creating such a record).

Dry run by default: nothing is deleted, the candidates are printed and written to a CSV for review.

    python3 scripts/purge_non_agent_drops.py                       # dry run against production, check every candidate
    python3 scripts/purge_non_agent_drops.py --limit 200           # only look at the first 200 smart-wallet records
    ADMIN_TOKEN=... python3 scripts/purge_non_agent_drops.py --apply --max-delete 3000

--apply calls DELETE {base}/api/projects/{contract} with `Authorization: Bearer $ADMIN_TOKEN` (the endpoint that
already exists for the bogus-contract purge). --max-delete is a safety cap: the run stops before deleting anything
if more candidates than that were found.
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import monitor  # noqa: E402  (repo root)

DEFAULT_BASE = "https://bsc-ia-scan-onrender-com.onrender.com"
MARKER = "discovered via smart wallet"


def fetch_history(base: str) -> list:
    req = urllib.request.Request(base.rstrip("/") + "/history.json", headers={"User-Agent": "purge-script"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.load(r)
    return data if isinstance(data, list) else data.get("projects", [])


def smart_wallet_discovered(rec: dict) -> bool:
    return any(MARKER in str(s).lower() for s in (rec.get("signals") or []))


def find_candidates(records: list, rpc, workers: int = 8, limit: int = 0) -> list:
    pool = [r for r in records if smart_wallet_discovered(r) and str(r.get("contract") or "").startswith("0x")]
    if limit:
        pool = pool[:limit]

    def check(rec):
        return rec, monitor.contract_links_to_registry(rpc, rec["contract"])

    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (rec, linked) in enumerate(ex.map(check, pool), 1):
            if not linked:
                out.append(rec)
            if i % 200 == 0:
                print(f"  checked {i}/{len(pool)} (candidates so far: {len(out)})", file=sys.stderr, flush=True)
    return out


def write_csv(path: str, rows: list) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["contract", "symbol", "name", "market_cap", "detected_at", "signals"])
        for r in rows:
            w.writerow([r.get("contract"), r.get("symbol"), r.get("name"), r.get("market_cap"),
                        r.get("detected_at"), "; ".join(str(s) for s in (r.get("signals") or []))])


def delete_remote(base: str, token: str, contract: str) -> bool:
    req = urllib.request.Request(f"{base.rstrip('/')}/api/projects/{contract.lower()}", method="DELETE",
                                 headers={"Authorization": f"Bearer {token}", "User-Agent": "purge-script"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        print(f"  {contract}: HTTP {e.code}", file=sys.stderr)
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=DEFAULT_BASE, help="backend to read from and delete on")
    ap.add_argument("--limit", type=int, default=0, help="only check the first N smart-wallet records (0 = all)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--csv", default=os.path.join(ROOT, "purge_candidates.csv"), help="where the candidate list is written")
    ap.add_argument("--apply", action="store_true", help="actually delete (needs ADMIN_TOKEN in the environment)")
    ap.add_argument("--max-delete", type=int, default=500, help="refuse to delete more than this many records")
    args = ap.parse_args(argv)

    token = os.environ.get("ADMIN_TOKEN", "")
    if args.apply and not token:
        print("--apply needs ADMIN_TOKEN in the environment", file=sys.stderr)
        return 2

    records = fetch_history(args.base)
    print(f"{len(records)} records; {sum(map(smart_wallet_discovered, records))} were discovered via a smart wallet")
    rpc = monitor.BscRpcClient(monitor.RPC_ENDPOINTS)
    cands = find_candidates(records, rpc, args.workers, args.limit)
    write_csv(args.csv, cands)
    print(f"{len(cands)} candidates (not linked to the registry) -> {args.csv}")
    for r in cands[:25]:
        print(f"  {r.get('contract')}  {str(r.get('symbol') or '')[:12]:12}  {str(r.get('name') or '')[:34]}")
    if len(cands) > 25:
        print(f"  ... and {len(cands) - 25} more (see the CSV)")

    if not args.apply:
        print("Dry run: nothing deleted. Review the CSV, then re-run with --apply.")
        return 0
    if len(cands) > args.max_delete:
        print(f"{len(cands)} candidates exceeds --max-delete {args.max_delete}; nothing deleted.", file=sys.stderr)
        return 3
    done = 0
    for r in cands:
        done += delete_remote(args.base, token, r["contract"])
        time.sleep(0.05)
    print(f"Deleted {done} of {len(cands)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
