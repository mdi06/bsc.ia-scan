#!/usr/bin/env python3
"""
Ingestion worker (write side). Runs as its own process, separate from api.py, and persists
what it finds through db.py. Background threads:
1. Discovery: scans new BSC blocks for smart-wallet calls and Agent ID registry deployments
2. Fast-track progress sweeper (every 15s): on-chain minted/supply for active drops
3. Market dynamics updater (every 120s): DexScreener / Pancake liquidity, price, volume
4. Element Market sweeper: NFT floor price and secondary volume
5. Proxy / factory watcher: clone-style drops
6. Agent ID counter: .ai wallet mint count
"""

import time
import json
import re
import threading
import concurrent.futures
import urllib.error
import urllib.request

import monitor
from common.runtime import run_memory_guard_loop
from db import db
from typing import Optional, List, Dict, Any

history_lock = threading.RLock()

# In-process copy of the drop list, refreshed from the DB at most every 15s.
SHARED_HISTORY = None
_last_shared_history_time = 0.0

def get_shared_history(force_refresh: bool = False) -> List[Dict[str, Any]]:
    global SHARED_HISTORY, _last_shared_history_time
    now = time.time()
    with history_lock:
        if force_refresh or SHARED_HISTORY is None or (now - _last_shared_history_time > 15.0):
            SHARED_HISTORY = monitor.load_history()
            _last_shared_history_time = now
        return SHARED_HISTORY

enrichment_pool = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="AlphaEnricher")
discovery_pool = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="CandidateAnalyzer")
fast_sweep_pool = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="FastSweeper")


def _enrich_contract_background(rpc: monitor.BscRpcClient, contract_addr: str, deployer_addr: Optional[str], det: dict):
    """Adds GoPlus security and DexScreener market data to a newly discovered drop."""
    try:
        mkt = monitor.analyze_market_interest(rpc, contract_addr, deployer_addr, det)
        with history_lock:
            history = get_shared_history()
            for item in history:
                if item.get("contract", "").lower() == contract_addr.lower():
                    item["score"] = mkt.get("score", item.get("score", 0))
                    item["rating"] = mkt.get("rating", item.get("rating", "🌱 EARLY"))
                    item["lp_usd"] = mkt.get("lp_usd", 0)
                    item["lp_wbnb"] = mkt.get("lp_wbnb", 0)
                    if mkt.get("token_symbol"):
                        item["token_symbol"] = mkt["token_symbol"]
                    item["token_price_usd"] = mkt.get("token_price_usd", 0)
                    item["holders"] = max(item.get("holders", 1), mkt.get("holders", 1))
                    item["buys_24h"] = max(item.get("buys_24h", 0), mkt.get("buys_24h", 0))
                    item["buys_1h"] = max(item.get("buys_1h", 0), mkt.get("buys_1h", 0))
                    item["sells_24h"] = mkt.get("sells_24h", 0)
                    item["volume_24h"] = mkt.get("volume_24h", 0.0)
                    item["market_cap"] = mkt.get("market_cap", 0.0)
                    item["fdv"] = mkt.get("fdv", 0.0)
                    item["price_change_24h"] = mkt.get("price_change_24h", 0.0)
                    if mkt.get("pair_url"):
                        item["pair_url"] = mkt["pair_url"]
                    if mkt.get("security"):
                        item["security"] = mkt["security"]
                    item["risk_level"] = mkt.get("risk_level", "SAFE")
                    item["risk_badge"] = mkt.get("risk_badge", "🟢 Clean")
                    item["risk_flags"] = mkt.get("risk_flags", [])
                    item["sniper_ready"] = mkt.get("sniper_ready", False)
                    item["sniper_status"] = mkt.get("sniper_status", "TRACKING")
                    item["sniper_badge"] = mkt.get("sniper_badge", "⏳ Tracking")
                    item["sniper_checklist"] = mkt.get("sniper_checklist", [])
                    if mkt.get("dev_root"):
                        item["dev_root"] = mkt["dev_root"]
                    if mkt.get("cluster_devs"):
                        item["cluster_devs"] = mkt["cluster_devs"]
                    if mkt.get("cluster_n"):
                        item["cluster_n"] = mkt["cluster_n"]
                    if mkt.get("hold"):
                        item["hold"] = mkt["hold"]
                        item["holder_distribution"] = mkt["hold"]
                    monitor.save_history(history, delta=item)
                    print(f"[🧠 Alpha Drop Enriched] {item.get('name')} ({contract_addr}) Score: {item['score']} | {item['risk_badge']}", flush=True)
                    break
    except Exception as e:
        print(f"[⚠️ Background Enrichment Notice {contract_addr}] {e}", flush=True)


def _process_smart_money_candidate(rpc, rcv, snd, aid, tx_h, b, tx_val, inp, wallet):
    """Analyzes a contract that a tracked smart wallet called, and records it as a new drop."""
    with history_lock:
        history = get_shared_history()
        if any(item.get("contract", "").lower() == rcv.lower() for item in history):
            return
    try:
        det = monitor.analyze_contract(rpc, rcv, wallet)
        cls = det.get("classification", {})
        if monitor.is_system_infra({"contract": rcv, "classification": cls, "name": det.get("name"), "symbol": det.get("symbol"), "max_supply": det.get("max_supply"), "total_minted": det.get("total_minted")}):
            return

        if monitor.is_synthetic_stock_or_peg(det.get("name"), det.get("symbol"), rcv):
            return

        if len(inp) < 10 or inp in ("", "0x"):
            mint_cd = "0x"
            val_str = str(tx_val)
            p_str = f"{tx_val/1e18:.4f} BNB" if tx_val > 0 else "⭐ FREE"
            bnb_val = tx_val / 1e18
            free_m = (tx_val == 0)
        else:
            base_cd = det.get("mint_calldata") if det.get("has_mint") else inp
            mint_cd = monitor.replace_calldata_recipient(base_cd, wallet, original_sender=snd)
            val_str = det.get("mint_value_wei") or str(tx_val)
            p_str = det.get("mint_price_str") or (f"{tx_val/1e18:.4f} BNB" if tx_val > 0 else "⭐ FREE")
            bnb_val = det.get("mint_price_bnb", 0.0) or (tx_val / 1e18)
            free_m = det.get("has_free_mint", False)

        new_entry = {
            "block": b,
            "contract": rcv,
            "deployer": "",
            "tx_hash": tx_h,
            "name": det.get("name") or "New Drop",
            "symbol": det.get("symbol") or "",
            "standard": cls.get("standard", ""),
            "icon": cls.get("icon", ""),
            "is_paused": det.get("is_paused"),
            "has_free_mint": free_m,
            "mint_price_str": p_str,
            "mint_price_bnb": bnb_val,
            "mint_value_wei": val_str,
            "max_supply": det.get("max_supply"),
            "total_minted": det.get("total_minted", 0),
            "score": 10,
            "rating": "🌱 EARLY",
            "lp_usd": 0,
            "lp_wbnb": 0,
            "token_symbol": det.get("symbol") or "",
            "token_price_usd": 0,
            "holders": 1,
            "buys_24h": 1,
            "buys_1h": 1,
            "sells_24h": 0,
            "volume_24h": 0.0,
            "market_url": det.get("market_url", ""),
            "pair_url": "",
            "mint_calldata": mint_cd,
            "signals": [f"Discovered via smart wallet {aid}"],
            "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "smart_wallets": [aid],
            "smart_minters": [aid],
            "has_refund": det.get("has_refund", False),
            "security": {},
            "risk_level": "SAFE",
            "risk_badge": "🟢 Clean",
            "risk_flags": [],
            "sniper_ready": False,
            "sniper_status": "TRACKING",
            "sniper_badge": "⏳ Tracking",
            "sniper_checklist": []
        }
        with history_lock:
            history = get_shared_history()
            if not any(item.get("contract", "").lower() == rcv.lower() for item in history):
                history.insert(0, new_entry)
                monitor.save_history(history, delta=new_entry)
                print(f"[🔥 NEW ALPHA DROP DISCOVERED] {det.get('name')} ({rcv}) via {aid}!", flush=True)

        _enrich_contract_background(rpc, rcv, None, det)
    except Exception as ex:
        print(f"[⚠️ Smart Money Analysis Notice {rcv}] {ex}", flush=True)


def _process_registry_candidate(rpc, tx_h, b, snd, tx_val, inp, wallet, known_infra):
    """Analyzes a transaction that references the Agent ID registry and records the contract it deployed."""
    try:
        rcpt = rpc.get_receipt(tx_h)
        if not rcpt or int(rcpt.get("status", "0"), 16) != 1:
            return
        dep_addr = rcpt.get("contractAddress")
        if not dep_addr:
            for log in rcpt.get("logs", []):
                log_addr = log.get("address")
                if log_addr and len(log_addr) == 42 and log_addr.lower() not in known_infra:
                    code = rpc.get_code(log_addr)
                    if code and len(code) > 2 and monitor.matches_tracked_registry(code):
                        dep_addr = log_addr
                        break

        if not dep_addr:
            return
        dep_addr = dep_addr.lower()
        with history_lock:
            history = get_shared_history()
            if any(x.get("contract", "").lower() == dep_addr for x in history):
                return

        det = monitor.analyze_contract(rpc, dep_addr, wallet)
        cls = det.get("classification", {})
        if monitor.is_system_infra({"contract": dep_addr, "classification": cls, "name": det.get("name"), "symbol": det.get("symbol"), "max_supply": det.get("max_supply"), "total_minted": det.get("total_minted")}):
            return

        if len(inp) < 10 or inp in ("", "0x"):
            mint_cd = "0x"
            val_str = str(tx_val)
            p_str = f"{tx_val/1e18:.4f} BNB" if tx_val > 0 else "⭐ FREE"
            bnb_val = tx_val / 1e18
            free_m = (tx_val == 0)
        else:
            base_cd = det.get("mint_calldata") if det.get("has_mint") else inp
            mint_cd = monitor.replace_calldata_recipient(base_cd, wallet, original_sender=snd)
            val_str = det.get("mint_value_wei") or str(tx_val)
            p_str = det.get("mint_price_str") or (f"{tx_val/1e18:.4f} BNB" if tx_val > 0 else "⭐ FREE")
            bnb_val = det.get("mint_price_bnb", 0.0) or (tx_val / 1e18)
            free_m = det.get("has_free_mint", False)

        new_entry = {
            "block": b,
            "contract": dep_addr,
            "deployer": snd,
            "tx_hash": tx_h,
            "name": det.get("name") or "New Drop",
            "symbol": det.get("symbol") or "",
            "standard": cls.get("standard", ""),
            "icon": cls.get("icon", ""),
            "is_paused": det.get("is_paused"),
            "has_free_mint": free_m,
            "mint_price_str": p_str,
            "mint_price_bnb": bnb_val,
            "mint_value_wei": val_str,
            "max_supply": det.get("max_supply"),
            "total_minted": det.get("total_minted", 0),
            "score": 10,
            "rating": "🌱 EARLY",
            "lp_usd": 0,
            "lp_wbnb": 0,
            "token_symbol": det.get("symbol") or "",
            "token_price_usd": 0,
            "holders": 1,
            "buys_24h": 1,
            "buys_1h": 1,
            "sells_24h": 0,
            "volume_24h": 0.0,
            "market_url": det.get("market_url", ""),
            "pair_url": "",
            "mint_calldata": mint_cd,
            "signals": ["Block-0 Agent ID Registry Drop"],
            "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "has_refund": det.get("has_refund", False),
            "security": {},
            "risk_level": "SAFE",
            "risk_badge": "🟢 Clean",
            "risk_flags": [],
            "sniper_ready": False,
            "sniper_status": "TRACKING",
            "sniper_badge": "⏳ Tracking",
            "sniper_checklist": []
        }
        with history_lock:
            history = get_shared_history()
            if not any(x.get("contract", "").lower() == dep_addr for x in history):
                history.insert(0, new_entry)
                monitor.save_history(history, delta=new_entry)
                print(f"[🚨 BLOCK-0 AGENT ID DROP] {det.get('name')} ({dep_addr}) deployed by {snd}!", flush=True)

        _enrich_contract_background(rpc, dep_addr, snd, det)
    except Exception as e:
        print(f"[⚠️ Registry Candidate Notice {tx_h}] {e}", flush=True)


def run_discovery_loop():
    """Scans each new BSC block for smart-wallet calls and Agent ID registry references."""
    print("[🚀 Worker Engine] Initializing 24/7 Autonomous Scanner...", flush=True)
    time.sleep(2)

    rpc = monitor.BscRpcClient(monitor.RPC_ENDPOINTS)
    wallet = monitor.DEFAULT_WALLET
    history = get_shared_history()
    smart_wallets = monitor.load_smart_wallets()

    known_infra = monitor.known_infra_addresses()

    cur = rpc.get_block_number()
    while cur is None:
        print("[⚠️ Worker Engine] Waiting for valid BSC block from RPC...", flush=True)
        time.sleep(2.0)
        cur = rpc.get_block_number()
    last_block = cur - 1
    processed_txs = set()
    queued_candidates = set()

    print(f"[🚀 Worker Engine] Loaded {len(history)} historical drops and {len(smart_wallets)} smart wallets.", flush=True)
    print(f"[📡 Worker Engine] Starting live listener from block #{last_block}...", flush=True)

    while True:
        try:
            curr_block = rpc.get_block_number()
            if curr_block is None:
                time.sleep(1.0)
                continue

            if curr_block <= last_block:
                time.sleep(1.0)
                continue

            # Fast catch-up if scanner was lagging behind real-time
            if curr_block - last_block > 30:
                print(f"[📡 Fast Catch-Up] Scanner was {curr_block - last_block} blocks behind. Jumping to #{curr_block - 10}...", flush=True)
                last_block = curr_block - 10

            # At most 10 blocks per tick, so one slow tick can't starve the rest of the loop.
            blocks_to_process = min(curr_block - last_block, 10)
            end_block = last_block + blocks_to_process

            for b in range(last_block + 1, end_block + 1):
                try:
                    block = rpc.get_block(b, full_tx=True)
                except Exception as e:
                    print(f"[⚠️ Block Fetch Notice #{b}] {e}", flush=True)
                    last_block = b
                    continue

                if not block or "transactions" not in block:
                    # Retry once before skipping
                    time.sleep(0.3)
                    try:
                        block = rpc.get_block(b, full_tx=True)
                    except Exception as e:
                        print(f"[⚠️ Block Retry Notice #{b}] {e}", flush=True)
                    if not block or "transactions" not in block:
                        last_block = b
                        continue

                txs = block.get("transactions", [])
                print(f"[📡 BSC Block #{b}] {len(txs)} txs", flush=True)

                for tx in txs:
                    tx_h = tx.get("hash")
                    if not tx_h or tx_h in processed_txs:
                        continue
                    processed_txs.add(tx_h)

                    snd = (tx.get("from") or "").lower()
                    rcv = (tx.get("to") or "").lower()
                    inp = (tx.get("input") or "").lower()
                    tx_val = int(tx.get("value") or "0", 16)

                    # Smart-wallet call into a contract we don't track yet, or a new smart wallet on a tracked one
                    if snd in smart_wallets and rcv and rcv not in known_infra and (len(inp) >= 10 or tx_val > 0):
                        aid = smart_wallets[snd]
                        with history_lock:
                            history = get_shared_history()
                            matching = [item for item in history if item.get("contract", "").lower() == rcv]
                            if matching:
                                item = matching[0]
                                cur_sm = set(item.get("smart_wallets") or [])
                                if aid not in cur_sm:
                                    cur_sm.add(aid)
                                    item["smart_wallets"] = list(cur_sm)
                                    item["smart_minters"] = list(cur_sm)
                                    if (not item.get("mint_calldata") or item.get("mint_calldata") in ("0x", "0x1249c58b")) and len(inp) >= 10:
                                        item["mint_calldata"] = monitor.replace_calldata_recipient(inp, wallet, original_sender=snd)
                                    monitor.save_history(history, delta=item)
                                    print(f"[🧠 Smart Money Inflow] {aid} called {item.get('name')} ({rcv})", flush=True)
                        if not matching and rcv not in queued_candidates:
                            queued_candidates.add(rcv)
                            # Analysis runs in the pool so the block loop never waits on it.
                            discovery_pool.submit(_process_smart_money_candidate, rpc, rcv, snd, aid, tx_h, b, tx_val, inp, wallet)

                    # Transaction data references the Agent ID registry
                    if monitor.matches_tracked_registry(inp) and tx_h not in queued_candidates:
                        queued_candidates.add(tx_h)
                        discovery_pool.submit(_process_registry_candidate, rpc, tx_h, b, snd, tx_val, inp, wallet, known_infra)

                last_block = b

                # Keep the dedupe sets bounded.
                if len(processed_txs) > 10000:
                    processed_txs.clear()
                if len(queued_candidates) > 5000:
                    queued_candidates.clear()

        except Exception as e:
            print(f"[⚠️ Engine Loop Error] {e}", flush=True)

        time.sleep(1.0)


def _sweep_kind(item: dict) -> Optional[str]:
    """Which kind of contract the fast sweeper is looking at, from the stored standard:
    "nft" (ERC-721), "token" (BEP-20) or "fair" (fair-mint dispenser); None for anything else
    (ERC-1155 supply is per id, custom contracts have no known supply getters)."""
    std = str(item.get("standard") or "")
    if "1155" in std:
        return None
    if "721" in std or "NFT" in std or item.get("is_nft"):
        return "nft"
    if "BEP-20" in std:
        return "token"
    if "Fair-Mint" in std:
        return "fair"
    return None


def _token_decimals(item: dict) -> Optional[int]:
    """Decimals recorded in a BEP-20 standard string like 'BEP-20 Token (18 decimals)'."""
    m = re.search(r"\((\d+) decimals\)", str(item.get("standard") or ""))
    return int(m.group(1)) if m else None


# contract -> time of the last supply-cap probe, so NFTs without a cap getter aren't re-probed every sweep
_supply_probe_at: Dict[str, float] = {}
SUPPLY_PROBE_INTERVAL_SEC = 3600.0
# contract -> its core() companion address (or None); analyze_contract reads supply from both
_core_addr_cache: Dict[str, Optional[str]] = {}


def _supply_targets(rpc: monitor.BscRpcClient, c: str) -> List[str]:
    """The contract plus its core() companion, the same addresses analyze_contract reads supply from."""
    if c not in _core_addr_cache:
        res = rpc.call_contract(c, monitor.keccak_selector("core()"))
        core = "0x" + res[-40:].lower() if res and len(res) >= 66 else None
        _core_addr_cache[c] = core if core and int(core, 16) != 0 else None
    core = _core_addr_cache[c]
    return [c, core] if core else [c]


def fast_sweep_contract(rpc: monitor.BscRpcClient, item: dict) -> tuple:
    """Reads minted/supply for one contract straight from the chain (no third-party APIs), the same
    way analyze_contract does, so fresh values stay comparable with what is already stored:
    - NFTs: raw counts; the cap is probed (at most hourly) when it isn't known yet.
    - BEP-20 tokens: supply scaled by the decimals in the standard string.
    - fair-mint dispensers: raw counts.
    Tokens and fair-mint contracts are swept only once they have a known cap, since "progress" means
    nothing without one. The holder estimate is NFT-only (a token's supply isn't a holder count).
    Returns (contract, minted, supply, holders, changed); anything else is reported unchanged."""
    c = item.get("contract")
    kind = _sweep_kind(item)
    if not c or not c.startswith("0x") or len(c) != 42 or kind is None:
        return c, None, None, None, False

    try:
        cur_minted = item.get("total_minted", 0)
        cur_supply = item.get("max_supply")
        has_cap = bool(cur_supply)

        decimals = None
        if kind == "token":
            decimals = _token_decimals(item)
            if decimals is None:
                return c, None, None, None, False  # can't scale without decimals
        if kind != "nft" and not has_cap:
            return c, None, None, None, False

        probe_cap = False
        if not has_cap:
            now = time.time()
            if now - _supply_probe_at.get(c, 0.0) >= SUPPLY_PROBE_INTERVAL_SEC:
                _supply_probe_at[c] = now
                probe_cap = True

        found_cap, minted = monitor.read_supply(rpc, _supply_targets(rpc, c), decimals, need_max=probe_cap)
        supply = cur_supply if has_cap else found_cap

        changed = False
        if minted is not None and minted != cur_minted:
            changed = True
        if supply is not None and supply != cur_supply:
            changed = True

        holders = item.get("holders", 1)
        if kind == "nft" and minted is not None and minted > 0:
            holders = max(holders, min(minted, int(minted * 0.85) + 1))

        return c, minted, supply, holders, changed
    except Exception:
        return c, None, None, None, False


def _next_cold_batch(items: list, offset: int, size: int = 50):
    """The next `size` items, wrapping around the list, and the offset to continue from."""
    if len(items) <= size:
        return list(items), 0
    start = offset % len(items)
    return (items + items)[start:start + size], start + size


def run_fast_track_progress_sweeper_loop():
    """Every 15s, refreshes on-chain minted/supply for active drops. Drops past 45% minted or with
    50+ holders are swept every cycle; the rest in batches of 50 every 4th cycle."""
    print("[⚡ Fast-Track Sweeper] Initializing 15-second Tiered Progress Sweeper...", flush=True)
    time.sleep(3)
    rpc = monitor.BscRpcClient(monitor.RPC_ENDPOINTS)

    lane2_counter = 0
    lane2_offset = 0

    while True:
        try:
            with history_lock:
                history = get_shared_history()

            if not history:
                time.sleep(15)
                continue

            active_contracts = [
                x for x in history
                if not monitor.is_system_infra(x)
                and (_sweep_kind(x) == "nft" or x.get("max_supply"))  # tokens/fair-mint need a known cap
                and (
                    not x.get("max_supply")
                    or (x.get("total_minted") or 0) < (x.get("max_supply") or 0)
                )
            ]

            if not active_contracts:
                time.sleep(15)
                continue

            lane1_items = []
            lane2_items = []

            for item in active_contracts:
                tot = item.get("total_minted", 0) or 0
                sup = item.get("max_supply") or 0
                holders = item.get("holders", 0) or 0
                pct = (tot / sup * 100.0) if sup > 0 else 0.0

                if pct >= 45.0 or holders >= 50:
                    lane1_items.append(item)
                else:
                    lane2_items.append(item)

            lane2_counter += 1
            sweep_targets = list(lane1_items)
            if lane2_counter >= 4 or not lane1_items:
                # A bounded batch of cold contracts per cycle, to avoid exhausting the RPC pool. The batch
                # moves along the list each time, so every cold contract is eventually swept.
                batch, lane2_offset = _next_cold_batch(lane2_items, lane2_offset)
                sweep_targets.extend(batch)
                lane2_counter = 0

            if sweep_targets:
                updates_count = 0
                sniper_alerts = []
                changed_items = []

                futures = [fast_sweep_pool.submit(fast_sweep_contract, rpc, it) for it in sweep_targets]
                sweep_results = []
                for fut in concurrent.futures.as_completed(futures):
                    try:
                        sweep_results.append(fut.result())
                    except Exception as e:
                        print(f"[⚠️ Fast-Track Sweeper Result Error] {e}", flush=True)

                # Apply under the lock: other threads read and mutate the same history items.
                with history_lock:
                    for c_addr, minted, supply, holders, changed in sweep_results:
                        if not c_addr or not changed:
                            continue
                        for it in history:
                            if it.get("contract", "").lower() != c_addr.lower():
                                continue
                            old_minted = it.get("total_minted", 0) or 0
                            old_sup = it.get("max_supply") or 0
                            old_prog = (old_minted / old_sup * 100.0) if old_sup > 0 else 0.0

                            if minted is not None:
                                it["total_minted"] = minted
                            if supply is not None:
                                it["max_supply"] = supply
                            if holders is not None:
                                it["holders"] = max(it.get("holders", 0) or 0, holders)

                            new_sup = it.get("max_supply") or 0
                            new_minted = it.get("total_minted", 0) or 0
                            new_prog = (new_minted / new_sup * 100.0) if new_sup > 0 else 0.0

                            if old_prog < 45.0 and new_prog >= 45.0:
                                it["signals"] = list(set((it.get("signals") or []) + ["⚡ Surging: 45%+ Minted"]))
                            if old_prog < 60.0 and new_prog >= 60.0:
                                it["signals"] = list(set((it.get("signals") or []) + ["🎯 SNIPER ALERT: 60%+ Sold Out!"]))

                            eva = monitor.evaluate_sniper_readiness(it)
                            it["sniper_ready"] = eva.get("sniper_ready", False)
                            it["sniper_status"] = eva.get("sniper_status", "TRACKING")
                            it["sniper_badge"] = eva.get("sniper_badge", "⏳ Tracking")
                            it["sniper_checklist"] = eva.get("sniper_checklist", [])

                            if not it.get("sniper_ready_alerted") and it["sniper_ready"]:
                                it["sniper_ready_alerted"] = True
                                sniper_alerts.append(it)

                            updates_count += 1
                            changed_items.append(it)
                            break

                if updates_count > 0:
                    with history_lock:
                        monitor.save_history(history, delta=changed_items)
                    print(f"[⚡ Fast-Track Sweeper] Updated on-chain progress for {updates_count} contracts.", flush=True)

                for alert_item in sniper_alerts:
                    print(
                        f"\n🎯 [HIGH-PRIORITY SNIPER ALERT] {alert_item.get('name')} ({alert_item.get('contract')})\n"
                        f"   Progress: {alert_item.get('total_minted')}/{alert_item.get('max_supply')} | "
                        f"Status: {alert_item.get('sniper_badge')} | Rating: {alert_item.get('rating')}\n",
                        flush=True
                    )

        except Exception as e:
            print(f"[⚠️ Fast-Track Sweeper Error] {e}", flush=True)

        time.sleep(15)


# One request covers up to 30 token addresses and returns the most liquid pair for each. The older
# /latest/dex/tokens/ endpoint returns at most 30 pairs *in total*, so a token with many pairs
# (e.g. FIST) used up the whole response and most of the other 29 tokens got no update at all.
DEXSCREENER_TOKENS_URL = "https://api.dexscreener.com/tokens/v1/bsc/"
DEXSCREENER_PAIRS_URL = "https://api.dexscreener.com/latest/dex/pairs/bsc/"


def _fetch_dexscreener_pairs(url: str, attempts: int = 3) -> List[Dict[str, Any]]:
    """GET a DexScreener URL and return its list of pair objects (the API answers with either a bare list
    or {"pairs": [...]}). On HTTP 429 it waits (the server's Retry-After, else 5s, 10s, ...) and retries."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == attempts:
                raise
            try:
                wait = float(e.headers.get("Retry-After")) if e.headers else 5.0 * attempt
            except (TypeError, ValueError):
                wait = 5.0 * attempt
            time.sleep(min(max(wait, 1.0), 30.0))
    if isinstance(data, list):
        return data
    return data.get("pairs") or []


def _collect_dex_pairs(pairs: List[Dict[str, Any]], wanted_tokens: set, dex_updates: Dict[str, Dict[str, Any]]) -> None:
    """Records pairs in `dex_updates`, keyed by pair address and by base-token address. Only pairs where one
    of our tokens is the BASE token count for the token key (otherwise e.g. GEN's metrics would be assigned
    to ANTFUN in a GEN/ANTFUN pair); the most liquid such pair wins."""
    for pair in pairs:
        b_addr = ((pair.get("baseToken") or {}).get("address") or "").lower()
        p_addr = (pair.get("pairAddress") or "").lower()
        if b_addr and b_addr in wanted_tokens:
            pair_liq = float((pair.get("liquidity") or {}).get("usd") or 0.0)
            existing = dex_updates.get(b_addr)
            if not existing or pair_liq > float((existing.get("liquidity") or {}).get("usd") or 0.0):
                dex_updates[b_addr] = pair
        if p_addr:
            dex_updates[p_addr] = pair


def _needs_pancake_fallback(item: Dict[str, Any]) -> bool:
    """The Pancake WBNB-pool estimate is only for drops DexScreener doesn't know. It counts one pool only
    (FIST: $88K from that pool vs $7.1M real liquidity), so it must never overwrite liquidity or a DexScreener
    pair we already have, or a single missed DexScreener answer would corrupt good data."""
    if _sweep_kind(item) not in ("token", "fair"):
        return False  # NFTs and custom contracts have no WBNB pool to find
    active = (item.get("total_minted") or 0) > 0 or item.get("smart_wallets") or (item.get("score") or 0) > 0
    known_pair = "dexscreener.com" in str(item.get("pair_url") or "")
    return bool(active) and not (item.get("lp_usd") or 0) > 0 and not known_pair


def _apply_dex_pair(item: Dict[str, Any], pair: Dict[str, Any]) -> None:
    """Copies a DexScreener pair's price, liquidity, volume, market cap and trade counts onto a drop.
    Raises if the pair has malformed numbers."""
    lp_info = pair.get("liquidity", {})
    item["lp_usd"] = float(lp_info.get("usd") or 0.0)
    if pair.get("priceUsd"):
        item["token_price_usd"] = float(pair.get("priceUsd"))
    if pair.get("baseToken", {}).get("symbol"):
        item["token_symbol"] = pair["baseToken"]["symbol"]
    if pair.get("url"):
        item["pair_url"] = pair["url"]

    item["volume_24h"] = float((pair.get("volume", {}) or {}).get("h24") or 0.0)

    mcap = float(pair.get("marketCap") or 0.0)
    fdv = float(pair.get("fdv") or 0.0)
    if mcap == 0 and fdv == 0 and (item.get("token_price_usd") or 0.0) > 0:
        supply_val = float(item.get("total_minted") or item.get("max_supply") or 0.0)
        if supply_val > 0.0:
            if supply_val > 1e14:
                supply_val = supply_val / 1e18
            mcap = supply_val * float(item["token_price_usd"])
    item["market_cap"] = mcap if mcap > 0 else fdv
    item["fdv"] = fdv

    item["price_change_24h"] = float((pair.get("priceChange", {}) or {}).get("h24") or 0.0)

    txns = pair.get("txns", {}) or {}
    item["buys_24h"] = int((txns.get("h24", {}) or {}).get("buys") or 0)
    item["sells_24h"] = int((txns.get("h24", {}) or {}).get("sells") or 0)
    item["buys_1h"] = int((txns.get("h1", {}) or {}).get("buys") or 0)


def _recompute_interest(item: Dict[str, Any]) -> None:
    """Interest score, rating and sniper readiness from the drop's current metrics."""
    score = 0
    lp = float(item.get("lp_usd") or 0.0)
    vol = float(item.get("volume_24h") or 0.0)
    b24 = int(item.get("buys_24h") or 0)

    if lp > 50000: score += 40
    elif lp > 10000: score += 30
    elif lp > 2000: score += 20
    elif lp > 500: score += 10

    if vol > 20000: score += 30
    elif vol > 5000: score += 20
    elif vol > 500: score += 10

    if b24 > 500: score += 30
    elif b24 > 100: score += 20
    elif b24 > 20: score += 10

    if item.get("smart_wallets"):
        score += len(item["smart_wallets"]) * 5

    item["score"] = min(100, score)
    if score >= 80: item["rating"] = "🔥 SURGING"
    elif score >= 50: item["rating"] = "🚀 HIGH"
    elif score >= 25: item["rating"] = "⚡ MODERATE"
    else: item["rating"] = "🌱 EARLY"

    eva = monitor.evaluate_sniper_readiness(item)
    item["sniper_ready"] = eva.get("sniper_ready", False)
    item["sniper_status"] = eva.get("sniper_status", "TRACKING")
    item["sniper_badge"] = eva.get("sniper_badge", "⏳ Tracking")
    item["sniper_checklist"] = eva.get("sniper_checklist", [])


# contract -> time of its last Pancake pool lookup, and how many unlisted drops to look up per sweep
_pancake_checked_at: Dict[str, float] = {}
PANCAKE_RECHECK_SEC = 3600.0
PANCAKE_FALLBACK_PER_SWEEP = 25


def _apply_pancake_estimate(rpc, item: Dict[str, Any]) -> bool:
    """Sets pair_url, lp_wbnb and lp_usd from the token's PancakeSwap V2 WBNB pool. True if it changed the item."""
    c = (item.get("contract") or "").lower()
    pair_addr = monitor.get_pair_address(rpc, monitor.PANCAKE_FACTORY_V2, c, monitor.WBNB_ADDRESS)
    if not pair_addr:
        return False
    item["pair_url"] = f"https://pancakeswap.finance/info/v2/pairs/{pair_addr}"
    reserves = monitor.get_reserves(rpc, pair_addr)
    if not reserves:
        return True  # the pair link alone is still new information
    r0, r1 = reserves
    # token0 is the lower address: WBNB's reserve is r1 when our token sorts below it
    wbnb_reserve = (r1 if c < monitor.WBNB_ADDRESS.lower() else r0) / 1e18
    item["lp_wbnb"] = wbnb_reserve
    item["lp_usd"] = wbnb_reserve * monitor.refresh_bnb_price(rpc)
    return True


def run_market_dynamics_updater_loop():
    """Every 120s, refreshes liquidity, price and volume for recent drops from DexScreener (Pancake reserves as fallback)."""
    print("[📈 Market Engine] Initializing 24/7 Market Dynamics Updater...", flush=True)
    time.sleep(5)
    rpc = monitor.BscRpcClient(monitor.RPC_ENDPOINTS)

    while True:
        try:
            with history_lock:
                history = get_shared_history()

            if not history:
                time.sleep(20)
                continue

            # Only the newest 150 drops plus older ones that already have liquidity or a price:
            # polling every dead contract would waste DexScreener calls.
            recent_drops = [
                x for x in history[:150]
                if not monitor.is_system_infra(x) and x.get("contract") and x["contract"].startswith("0x") and len(x["contract"]) == 42
            ]
            active_old = [
                x for x in history[150:]
                if not monitor.is_system_infra(x) and (x.get("lp_usd", 0) > 0 or x.get("token_price_usd", 0) > 0)
            ]
            contracts_to_check = recent_drops + active_old

            batch_size = 30
            dex_updates = {}
            dex_failures = 0
            for i in range(0, len(contracts_to_check), batch_size):
                chunk = contracts_to_check[i:i + batch_size]
                chunk_addrs = [x["contract"] for x in chunk if x.get("contract")]
                chunk_addrs_set = {x.lower() for x in chunk_addrs}
                # Also collect any known pair addresses from pair_url
                chunk_pairs = []
                for x in chunk:
                    p_url = x.get("pair_url") or ""
                    m = re.search(r'dexscreener\.com/bsc/(0x[a-fA-F0-9]{40})', p_url)
                    if m:
                        chunk_pairs.append(m.group(1).lower())
                try:
                    if chunk_addrs:
                        _collect_dex_pairs(_fetch_dexscreener_pairs(DEXSCREENER_TOKENS_URL + ",".join(chunk_addrs)),
                                           chunk_addrs_set, dex_updates)
                    if chunk_pairs:
                        _collect_dex_pairs(_fetch_dexscreener_pairs(DEXSCREENER_PAIRS_URL + ",".join(chunk_pairs)),
                                           chunk_addrs_set, dex_updates)
                except Exception as e:
                    dex_failures += 1  # best-effort: the next sweep retries
                    last_dex_error = e
                    if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                        break  # still rate limited after retries: stop and keep what we already have
                time.sleep(1.0)
            if dex_failures:
                print(f"[⚠️ Market Engine] DexScreener failed for {dex_failures} batch(es): {last_dex_error}", flush=True)

            updated_count = 0
            changed_items = []
            pancake_candidates = []
            now = time.time()
            for item in history:
                c = (item.get("contract") or "").lower()
                pair_addr = ""
                m = re.search(r'dexscreener\.com/bsc/(0x[a-fA-F0-9]{40})', item.get("pair_url") or "")
                if m:
                    pair_addr = m.group(1).lower()

                # Guard: Candidate pool must have item contract as baseToken
                pair = dex_updates.get(c)
                if not pair and pair_addr:
                    cand_p = dex_updates.get(pair_addr)
                    if cand_p and (cand_p.get("baseToken", {}).get("address") or "").lower() == c:
                        pair = cand_p
                item_changed = False

                if pair:
                    try:
                        _apply_dex_pair(item, pair)
                        updated_count += 1
                        item_changed = True
                    except Exception as e:
                        print(f"[⚠️ Market Engine] bad DexScreener data for {c}: {e}", flush=True)
                elif _needs_pancake_fallback(item) and now - _pancake_checked_at.get(c, 0.0) >= PANCAKE_RECHECK_SEC:
                    pancake_candidates.append(item)

                try:
                    _recompute_interest(item)
                except Exception as e:
                    print(f"[⚠️ Market Engine] score update failed for {c}: {e}", flush=True)

                if item_changed:
                    changed_items.append(item)

            # Save the DexScreener results before the slow RPC pass below.
            if changed_items:
                with history_lock:
                    monitor.save_history(history, delta=changed_items)
                print(f"[📈 Market Engine] Synced live market & mint metrics for {updated_count} contracts.", flush=True)

            # Pancake pool estimate for drops DexScreener doesn't list: a few per sweep, each re-checked at most
            # hourly. One RPC round trip per item, so doing every unlisted drop each sweep took minutes.
            pancake_changed = []
            for item in pancake_candidates[:PANCAKE_FALLBACK_PER_SWEEP]:
                c = (item.get("contract") or "").lower()
                _pancake_checked_at[c] = time.time()
                try:
                    if _apply_pancake_estimate(rpc, item):
                        _recompute_interest(item)
                        pancake_changed.append(item)
                except Exception as e:
                    print(f"[⚠️ Market Engine] Pancake lookup failed for {c}: {e}", flush=True)
            if pancake_changed:
                with history_lock:
                    monitor.save_history(history, delta=pancake_changed)
                print(f"[📈 Market Engine] Estimated Pancake liquidity for {len(pancake_changed)} unlisted contracts.", flush=True)

        except Exception as e:
            print(f"[⚠️ Market Engine Error] {e}", flush=True)

        # 120s between sweeps keeps egress within Render's free tier.
        time.sleep(120)


ELEMENT_SURGE_FIELDS = (
    "secondary_sales_count", "secondary_vol_bnb", "floor_price_bnb", "high_price_bnb",
    "secondary_transfers", "is_nft_surge", "nft_surge_badge",
)


def _sweep_element_nft_once(rpc, history) -> List[Dict[str, Any]]:
    """Updates NFT drops in `history` with Element secondary-market stats (sales, volume, floor, surge flag)
    read from on-chain Element fills. Returns the items that changed.

    monitor.scan_element_nft_volume works on copies, so the network scan runs without the lock and the
    results are applied to the shared items under history_lock."""
    nft_contracts = [
        x for x in history
        if not monitor.is_system_infra(x) and (
            "721" in str(x.get("standard", ""))
            or "NFT" in str(x.get("standard", ""))
            or x.get("is_nft")
        ) and x.get("contract") and x["contract"].startswith("0x") and len(x["contract"]) == 42
    ]
    if not nft_contracts:
        return []

    work = [dict(x) for x in nft_contracts]
    results = monitor.scan_element_nft_volume(rpc, work)

    changed_items = []
    with history_lock:
        for item, scanned in zip(nft_contracts, work):
            before = tuple(item.get(k) or 0 for k in ELEMENT_SURGE_FIELDS)  # absent, 0, "" and False all count as "nothing"
            for k in ELEMENT_SURGE_FIELDS:
                if k in scanned:
                    item[k] = scanned[k]
            if tuple(item.get(k) or 0 for k in ELEMENT_SURGE_FIELDS) == before:
                continue
            floor = float(item.get("floor_price_bnb") or 0.0)
            if floor > 0:
                item["floor_price_str"] = f"{floor:.3f} BNB"
            signal = (results.get(item["contract"].lower()) or {}).get("badge_plain")
            if item.get("is_nft_surge") and signal:
                item["nft_surge_signal"] = signal
                kept = [s for s in (item.get("signals") or []) if not s.startswith("🦋 SURGE")]
                item["signals"] = kept + [signal]
            changed_items.append(item)
    return changed_items


def run_element_nft_volume_sweeper_loop():
    """Every 60s, refreshes Element Market secondary stats (sales, volume, floor, surge) for NFT drops."""
    print("[💎 Element NFT Sweeper] Initializing 24/7 Element Market Sweeper...", flush=True)
    time.sleep(7)
    rpc = monitor.BscRpcClient(monitor.RPC_ENDPOINTS)

    while True:
        try:
            with history_lock:
                history = get_shared_history()

            if not history:
                time.sleep(60)
                continue

            changed_items = _sweep_element_nft_once(rpc, history)
            if changed_items:
                with history_lock:
                    monitor.save_history(history, delta=changed_items)
                print(f"[💎 Element NFT Sweeper] Synced secondary volume/floor for {len(changed_items)} NFT drops.", flush=True)

        except Exception as e:
            print(f"[⚠️ Element NFT Sweeper Error] {e}", flush=True)

        time.sleep(60)


def _next_scan_range(last_block: int, curr_block: int, max_blocks: int = 10, max_lag: int = 30):
    """Next (first, last) block numbers to scan. At most `max_blocks` per tick; if the scanner is more
    than `max_lag` blocks behind it skips ahead to the newest `max_blocks`, like run_discovery_loop does."""
    if curr_block - last_block > max_lag:
        last_block = curr_block - max_blocks
    return last_block + 1, last_block + min(curr_block - last_block, max_blocks)


def run_proxy_factory_watcher_loop():
    """Watches new blocks for calls into proxy/factory contracts and records clone-style drops."""
    print("[🧩 Proxy Factory Watcher] Initializing Stealth Factory & Proxy Clone Watcher...", flush=True)
    time.sleep(4)
    rpc = monitor.BscRpcClient(monitor.RPC_ENDPOINTS)
    curr_b = rpc.get_block_number()
    while curr_b is None:
        time.sleep(2.0)
        curr_b = rpc.get_block_number()
    last_block = curr_b - 1

    known_infra = monitor.known_infra_addresses()

    while True:
        try:
            curr_block = rpc.get_block_number()
            if curr_block is None or curr_block <= last_block:
                time.sleep(2.0)
                continue
            if curr_block > last_block:
                first_block, end_block = _next_scan_range(last_block, curr_block)
                if first_block > last_block + 1:
                    print(f"[🧩 Proxy Factory Watcher] {curr_block - last_block} blocks behind; jumping to #{first_block}", flush=True)
                for b in range(first_block, end_block + 1):
                    try:
                        blk = rpc.get_block(b, full_tx=True)
                    except Exception:
                        continue
                    txs = blk.get("transactions", [])
                    for tx in txs:
                        snd = (tx.get("from") or "").lower()
                        rcv = (tx.get("to") or "").lower()
                        inp = (tx.get("input") or "").lower()
                        tx_h = tx.get("hash")

                        if rcv in known_infra:
                            continue

                        # Selectors/registry references that suggest a proxy deploy or clone
                        is_proxy_call = any(sig in inp for sig in ["0x5c19a95c", "0x3644e515", "0x5824c65a", "0x485cc955"])
                        is_proxy_ref = monitor.matches_tracked_registry(inp)

                        if (is_proxy_call or is_proxy_ref) and rcv:
                            cand = rcv
                            is_proxy, proxy_type, impl_addr = monitor.is_proxy_contract(rpc, cand)
                            is_fac, fac_badge = monitor.detect_factory_pattern(rpc, cand)

                            if is_proxy or is_fac:
                                with history_lock:
                                    history = get_shared_history()
                                    existing = [x for x in history if x.get("contract", "").lower() == cand.lower()]
                                    if existing:
                                        ex_item = existing[0]
                                        changed = False
                                        if is_proxy and not ex_item.get("is_proxy_clone"):
                                            ex_item["is_proxy_clone"] = True
                                            ex_item["proxy_type"] = proxy_type
                                            ex_item["master_implementation"] = impl_addr
                                            changed = True
                                        if is_fac and not ex_item.get("is_factory"):
                                            ex_item["is_factory"] = True
                                            ex_item["factory_badge"] = fac_badge
                                            changed = True
                                        if changed:
                                            monitor.save_history(history, delta=ex_item)
                                            print(f"[🧩 PROXY / FACTORY UPDATED] {ex_item.get('name')} ({cand}) Proxy: {proxy_type} | Factory: {fac_badge}", flush=True)
                                        continue

                                det = monitor.analyze_contract(rpc, cand, monitor.DEFAULT_WALLET)
                                cls = det.get("classification", {})
                                if not monitor.is_system_infra({"contract": cand, "classification": cls, "name": det.get("name"), "symbol": det.get("symbol"), "max_supply": det.get("max_supply"), "total_minted": det.get("total_minted")}):
                                    new_entry = {
                                        "block": b,
                                        "contract": cand,
                                        "deployer": snd,
                                        "tx_hash": tx_h,
                                        "name": det.get("name") or ("Proxy Clone Drop" if is_proxy else "Factory Drop"),
                                        "symbol": det.get("symbol") or "",
                                        "standard": cls.get("standard", "Proxy Contract" if is_proxy else "Custom Contract"),
                                        "icon": cls.get("icon", "🧩" if is_proxy else "🏭"),
                                        "is_paused": det.get("is_paused"),
                                        "has_free_mint": det.get("has_free_mint", False),
                                        "mint_price_str": det.get("mint_price_str", "⭐ FREE"),
                                        "mint_price_bnb": det.get("mint_price_bnb", 0.0),
                                        "mint_value_wei": det.get("mint_value_wei", "0"),
                                        "max_supply": det.get("max_supply"),
                                        "total_minted": det.get("total_minted", 0),
                                        "score": 10,
                                        "rating": "🌱 EARLY",
                                        "lp_usd": 0,
                                        "lp_wbnb": 0,
                                        "token_symbol": det.get("symbol") or "",
                                        "token_price_usd": 0,
                                        "holders": 1,
                                        "buys_24h": 0,
                                        "buys_1h": 0,
                                        "sells_24h": 0,
                                        "volume_24h": 0.0,
                                        "market_url": det.get("market_url", ""),
                                        "pair_url": "",
                                        "mint_calldata": det.get("mint_calldata", "0x1249c58b"),
                                        "signals": [f"Factory Clone: {proxy_type or 'Proxy'} via {fac_badge or 'Factory'}"],
                                        "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                        "has_refund": det.get("has_refund", False),
                                        "security": {},
                                        "risk_level": "SAFE",
                                        "risk_badge": "🟢 Clean",
                                        "risk_flags": [],
                                        "sniper_ready": False,
                                        "sniper_status": "TRACKING",
                                        "sniper_badge": "⏳ Tracking",
                                        "sniper_checklist": [],
                                        "is_proxy_clone": proxy_type is not None,
                                        "proxy_type": proxy_type,
                                        "master_implementation": impl_addr,
                                        "is_factory": is_fac,
                                        "factory_badge": fac_badge
                                    }
                                    with history_lock:
                                        history = get_shared_history()
                                        if not any(x.get("contract", "").lower() == cand.lower() for x in history):
                                            history.insert(0, new_entry)
                                            monitor.save_history(history, delta=new_entry)
                                            print(f"[🧩 NEW PROXY CLONE DETECTED] {new_entry['name']} ({cand}) [{proxy_type}] Master: {impl_addr}", flush=True)

                                            enrichment_pool.submit(_enrich_contract_background, rpc, cand, snd, det)

                last_block = end_block
        except Exception as e:
            print(f"[⚠️ Proxy Factory Watcher Error] {e}", flush=True)

        time.sleep(1.0)


def run_agent_id_counter_loop():
    """Counts new Binance .ai Agent ID mints (registry Transfer-from-zero logs) and persists the total."""
    print("[🤖 Agent ID Tracker] Starting live SPACE ID (.ai) mint counter loop...", flush=True)
    # publicnode allows eth_getLogs over up to 50k blocks per call.
    logs_rpc = monitor.BscRpcClient([
        "https://bsc-rpc.publicnode.com",
        "https://bsc.publicnode.com"
    ])
    transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    zero_from_topic = "0x0000000000000000000000000000000000000000000000000000000000000000"
    registry_addr = monitor.AGENT_ID_REGISTRY

    # Start from whichever baseline is further along: local JSON or cloud DB (the local file is lost on Render restarts).
    local_st = monitor.load_agent_id_stats() or {}
    try:
        cloud_st = db.get_agent_id_stats(max_age_sec=0.0) or {}
    except Exception as e:
        # A failed baseline lookup must not kill the counter thread (the supervisor would restart it forever).
        print(f"[🤖 Agent ID Tracker] Could not load cloud baseline, using local only: {e}", flush=True)
        cloud_st = {}
    if int(cloud_st.get("total_created_wallets") or 0) > int(local_st.get("total_created_wallets") or 0):
        cur_st = dict(cloud_st)
    elif int(cloud_st.get("total_created_wallets") or 0) == int(local_st.get("total_created_wallets") or 0) and int(cloud_st.get("last_block") or 0) > int(local_st.get("last_block") or 0):
        cur_st = dict(cloud_st)
    else:
        cur_st = dict(local_st)

    total_wallets = int(cur_st.get("total_created_wallets") or monitor.DEFAULT_AGENT_WALLET_COUNT)
    last_block = int(cur_st.get("last_block") or 0)
    startup_synced = False

    while True:
        try:
            curr_block = logs_rpc.get_block_number()
            if curr_block:
                if last_block <= 0:
                    last_block = curr_block - 1

                # Guard against stale baselines exceeding publicnode log retention (~250k blocks)
                start_block = max(last_block + 1, curr_block - 200000)
                if start_block <= curr_block:
                    chunk_size = 40000
                    f_blk = start_block
                    total_added = 0
                    failed_chunk = False
                    while f_blk <= curr_block:
                        t_blk = min(f_blk + chunk_size - 1, curr_block)
                        res = logs_rpc.call("eth_getLogs", [{
                            "fromBlock": hex(f_blk),
                            "toBlock": hex(t_blk),
                            "address": registry_addr,
                            "topics": [transfer_topic, zero_from_topic]
                        }])
                        if res is None:
                            failed_chunk = True
                            break
                        total_added += len(res)
                        last_block = t_blk
                        f_blk = t_blk + 1

                    if total_added > 0 or not startup_synced or not failed_chunk:
                        if total_added > 0:
                            total_wallets += total_added
                            print(f"[🤖 Agent IDs] +{total_added} new .ai wallets! Total: {total_wallets:,} (Block #{last_block})", flush=True)
                        cur_st = {
                            "total_created_wallets": total_wallets,
                            "last_block": last_block,
                            "last_updated": time.time(),
                            "source": "SPACE ID / Binance Agent ID Registry (0x60B9262456dE011F7d15a54CdD8c48bAaa8B022F)"
                        }
                        monitor.save_agent_id_stats(cur_st)
                        db.save_agent_id_stats(cur_st, force_cloud=(not startup_synced))
                        startup_synced = True
        except Exception as e:
            print(f"[⚠️ Agent ID Tracker Error] {e}", flush=True)

        time.sleep(12)


def start_worker():
    """Starts every worker loop in its own thread and restarts any thread that dies."""
    print("[⚙️ Worker Process] Starting all 6 autonomous discovery & sweeper loops...", flush=True)
    task_specs = [
        (run_discovery_loop, "DiscoveryLoop"),
        (run_market_dynamics_updater_loop, "MarketUpdater"),
        (run_fast_track_progress_sweeper_loop, "FastSweeper"),
        (run_element_nft_volume_sweeper_loop, "ElementSweeper"),
        (run_proxy_factory_watcher_loop, "ProxyWatcher"),
        (run_agent_id_counter_loop, "AgentIdCounter"),
        (run_memory_guard_loop, "MemoryGuard")
    ]
    threads = []
    for fn, name in task_specs:
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        threads.append(t)

    try:
        while True:
            time.sleep(5.0)
            for idx, (fn, name) in enumerate(task_specs):
                if not threads[idx].is_alive():
                    print(f"[⚠️ Worker Supervisor] Thread '{name}' died! Auto-restarting...", flush=True)
                    new_t = threading.Thread(target=fn, name=name, daemon=True)
                    new_t.start()
                    threads[idx] = new_t
    except (KeyboardInterrupt, SystemExit):
        print("[⚙️ Worker Process] Stopping worker...", flush=True)


if __name__ == "__main__":
    start_worker()

