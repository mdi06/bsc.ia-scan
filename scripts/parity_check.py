#!/usr/bin/env python3
"""Old-versus-new check for refactors that must not change behaviour (the monitor.py split).

Run it in two checkouts of the repo (for example `git worktree add ../old <commit-before-the-refactor>`), then compare:

    python3 scripts/parity_check.py dump /tmp/new.json            # in the refactored checkout
    (cd ../old && python3 scripts/parity_check.py dump /tmp/old.json)
    python3 scripts/parity_check.py compare /tmp/old.json /tmp/new.json

`dump` feeds the stored records of history.json (read only, nothing is written back, no network, no Supabase) through the pure
functions of monitor.py / filters.py / api.py with a frozen clock and a fixed BNB price, and writes every result. `compare` lists
any record or function whose result differs. With --live it also reads a few real BSC contracts through the public RPC (read only).
"""
import copy
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for var in list(os.environ):
    if var.startswith("SUPABASE"):
        del os.environ[var]                     # never talk to Supabase from a parity run

NOW = 1795600000.0
BNB = 600.0
time.time = lambda: NOW                         # a frozen clock, so "within 24h" means the same in both runs

import monitor  # noqa: E402
import filters  # noqa: E402
import api      # noqa: E402


def _plain(o):
    """Sets print in a different order on every run (hash randomisation): compare them sorted."""
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    return str(o)


def digest(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=_plain).encode()).hexdigest()[:16]


def safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as exc:                    # an exception is a result too: it must be the same in both checkouts
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def load_records():
    path = os.path.join(ROOT, "history.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("projects", [])


def dump(out_path, live=False):
    records = load_records()
    monitor._bnb_price_state.update(usd=BNB, live=True, ok_at=NOW, fetched_at=NOW)
    results = {"records": len(records), "per_record": {}, "aggregate": {}}
    for rec in records:
        key = str(rec.get("contract"))
        r = copy.deepcopy(rec)
        out = {
            "sniper": safe(monitor.evaluate_sniper_ready, copy.deepcopy(rec)),
            "interest": safe(monitor.compute_interest, copy.deepcopy(rec)),
            "profit": safe(monitor.update_post_mint_profit_metrics, r, BNB),
            "profit_item": r,
            "infra": safe(monitor.is_system_infra, rec),
            "synthetic": safe(monitor.is_synthetic_stock_or_peg, rec),
            "drain": safe(monitor.fund_drain_applies, rec, {"lp_usd": rec.get("lp_usd") or 0, "pair_url": rec.get("pair_url") or ""}, True, 0.0),
            "serial": safe(lambda: [h.get("contract") for h in monitor.count_prior_deployments(records[:400], rec.get("deployer") or "", rec.get("contract") or "")]),
            "supply_cap": safe(filters.corrected_max_supply, rec),
            "crowd": safe(filters.crowd_surge_mints, rec, NOW),
            "server": safe(api.with_server_metrics, copy.deepcopy(rec)),
            "derived": safe(api.with_derived_fields, copy.deepcopy(rec)),
            "runner": safe(api.pulse_runner_row, rec),
            "launch": safe(api.pulse_launch_row, rec, NOW),
            "nft_mint": safe(api.pulse_nft_mint_row, rec),
        }
        results["per_record"][key] = digest(out)
        results.setdefault("detail", {})[key] = out if len(results["per_record"]) <= 200 else None   # keep a readable sample
    results["aggregate"] = {
        "market_cap_stats": digest(safe(monitor.compute_market_cap_stats, copy.deepcopy(records), BNB)),
        "pulse": digest(safe(api.compute_pulse_24h, copy.deepcopy(records), NOW)),
        "mint_progress": digest(safe(api.compute_mint_progress_stats, copy.deepcopy(records))),
        "selectors": digest([monitor.is_non_mint_call("0x095ea7b3" + "0" * 64), monitor.matches_tracked_registry(monitor.AGENT_ID_BYTES),
                             sorted(monitor.known_infra_addresses()), sorted(monitor.launchpad_addresses()),
                             monitor.keccak_selector("owner()"), monitor.keccak_selector("maxSupply()"),
                             monitor.clean_chain_text("a<b>`c\x00d"), monitor.build_rpc_endpoints(["https://a", "https://b"], env={})]),
        "constants": digest({k: getattr(monitor, k) for k in (
            "AGENT_ID_REGISTRY", "FOUR_MEME_FACTORY", "WBNB_ADDRESS", "USDT_ADDRESS", "PANCAKE_FACTORY_V2", "ELEMENT_EX", "T_TRANSFER",
            "MINT_TERMS_VERSION", "RATING_BY_TIER", "BNB_PRICE_TTL_SEC", "REFUND_SELECTORS", "NON_MINT_SELECTORS", "FACTORY_SELECTORS")}),
        "known_projects": digest(monitor.KNOWN_PROJECTS),
        "public_names": digest(sorted(n for n in dir(monitor) if not n.startswith("__"))),
    }
    if live:
        results["live"] = dump_live()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, default=str)
    print(f"wrote {out_path}: {len(records)} records, {len(results['per_record'])} digests")


LIVE_CONTRACTS = [          # long-settled contracts: their chain state does not move between two runs a minute apart
    "0x60B9262456dE011F7d15a54CdD8c48bAaa8B022F",   # the Agent ID registry itself
    "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",   # WBNB
    "0x55d398326f99059fF775485246999027B3197955",   # USDT
]


def dump_live():
    rpc = monitor.BscRpcClient(monitor.RPC_ENDPOINTS)
    out = {}
    for addr in LIVE_CONTRACTS:
        out[addr] = {
            "classify": safe(monitor.classify_asset_type, rpc, addr),
            "supply": safe(monitor.read_supply, rpc, addr, 18),
            "code_len": safe(lambda: len(rpc.get_code(addr) or "")),
        }
    out["bnb_price_is_number"] = isinstance(safe(monitor.fetch_bnb_price_usd, rpc), float)
    return out


def compare(a_path, b_path):
    a, b = json.load(open(a_path)), json.load(open(b_path))
    bad = 0
    if a["records"] != b["records"]:
        print(f"record count differs: {a['records']} vs {b['records']}")
        bad += 1
    diff = [k for k, v in a["per_record"].items() if b["per_record"].get(k) != v]
    print(f"records compared: {len(a['per_record'])}, different: {len(diff)}")
    for k in diff[:10]:
        print("  differs:", k)
        da, db_ = (a.get("detail") or {}).get(k), (b.get("detail") or {}).get(k)
        if da and db_:
            for fn in da:
                if da[fn] != db_[fn]:
                    print("     in", fn)
    bad += bool(diff)
    for k, v in a["aggregate"].items():
        same = b["aggregate"].get(k) == v
        print(f"  {'same' if same else 'DIFFERENT':9s} {k}")
        bad += not same
    if "live" in a or "live" in b:
        same = a.get("live") == b.get("live")
        print(f"  {'same' if same else 'DIFFERENT':9s} live chain reads")
        bad += not same
    print("PARITY OK" if not bad else f"PARITY BROKEN ({bad} differences)")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "dump":
        dump(sys.argv[2], live="--live" in sys.argv)
    elif len(sys.argv) == 4 and sys.argv[1] == "compare":
        sys.exit(compare(sys.argv[2], sys.argv[3]))
    else:
        print(__doc__)
        sys.exit(2)
