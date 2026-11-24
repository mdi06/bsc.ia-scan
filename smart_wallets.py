"""The smart-wallet list: the wallets the radar treats as "smart money". smart_wallets.json in the repo is the default list; the
Google Sheet it was first built from can refresh it (sync_smart_wallets, a manual step). Moved out of monitor.py unchanged;
monitor.py re-exports these names, so `monitor.load_smart_wallets` keeps working for worker.py."""

import csv
import json
import logging
import os
import urllib.request
from typing import Dict, Optional

_log = logging.getLogger(__name__)



SMART_WALLETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smart_wallets.json")


SMART_WALLETS_CSV_URL = "https://docs.google.com/spreadsheets/d/1jcKyWK1JJ8uBchTS3p1ok18DeB7yrDb2ZDxOaNYF73Q/export?format=csv&gid=395869104"


def load_smart_wallets() -> Dict[str, str]:
    """Loads smart wallets mapping: {wallet_address_lower: agent_id_name}."""
    if os.path.exists(SMART_WALLETS_FILE):
        try:
            with open(SMART_WALLETS_FILE, "r") as f:
                return json.load(f)
        except Exception as exc:
            _log.debug("could not read %s, will re-sync: %s", SMART_WALLETS_FILE, exc)
    return sync_smart_wallets()


def sync_smart_wallets() -> Dict[str, str]:
    """Downloads/syncs the 5,000 tracked smart agent wallets from Google Sheets."""
    print(f"[*] Fetching smart agent wallets from Google Sheets ({SMART_WALLETS_CSV_URL[:45]}...)...")
    wallets = {}
    try:
        req = urllib.request.Request(SMART_WALLETS_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            lines = [line.decode("utf-8", errors="ignore") for line in resp.readlines()]
            reader = csv.reader(lines)
            for row in reader:
                if not row:
                    continue
                addr = None
                name = None
                for cell in row:
                    cell_s = cell.strip()
                    if cell_s.startswith("0x") and len(cell_s) == 42:
                        addr = cell_s.lower()
                    elif ".ai" in cell_s:
                        name = cell_s
                if addr:
                    wallets[addr] = name or "agent.ai"
        if wallets:
            with open(SMART_WALLETS_FILE, "w") as f:
                json.dump(wallets, f, indent=2)
            print(f"[+] Successfully synced {len(wallets):,} smart agent wallets to {SMART_WALLETS_FILE}!")
        else:
            print(f"[!] Could not parse smart wallets from spreadsheet.")
    except Exception as e:
        print(f"[!] Error downloading smart wallets: {e}")
    return wallets


_SMART_WALLETS_CACHE: Optional[Dict[str, str]] = None


def get_cached_smart_wallets() -> Dict[str, str]:
    """Returns memory-cached smart wallets mapping to prevent repeated file I/O."""
    global _SMART_WALLETS_CACHE
    if _SMART_WALLETS_CACHE is None:
        try:
            _SMART_WALLETS_CACHE = load_smart_wallets() or {}
        except Exception:
            _SMART_WALLETS_CACHE = {}
    return _SMART_WALLETS_CACHE
