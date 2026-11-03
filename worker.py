#!/usr/bin/env python3
"""
Dedicated Ingestion & Background Enrichment Worker (Producer)
==============================================================
Runs 24/7 as an isolated operating system process:
1. Real-Time Smart Wallet Tracker (5,000 Tracked Wallets)
2. Block-0 Agent ID Registry Deployment Scanner
3. 15-second High-Frequency Tiered Progress Sweeper
4. 24/7 Market Dynamics Updater (DexScreener & Pancake Reserves)
5. Element Market NFT Volume Sweeper
6. Stealth Proxy Clone & Factory Watcher

Decoupled from the web server: never blocked by incoming HTTP traffic.
Persists discoveries directly to Supabase / SQLite via db.py.
"""

import os
import sys
import time
import json
import re
import threading
import concurrent.futures
import urllib.request
import urllib.parse

import monitor
from db import db

history_lock = threading.Lock()


def run_discovery_loop():
    """Continuous 24/7 headless discovery scanning BSC blocks for smart money & registry drops."""
    print("[🚀 Worker Engine] Initializing 24/7 Autonomous Scanner...", flush=True)
    time.sleep(2)

    rpc = monitor.BscRpcClient(monitor.RPC_ENDPOINTS)
    wallet = monitor.DEFAULT_WALLET
    history = monitor.load_history()
    smart_wallets = monitor.load_smart_wallets()

    known_infra = {
        monitor.PANCAKE_FACTORY_V2.lower(),
        monitor.WBNB_ADDRESS.lower(),
        monitor.USDT_ADDRESS.lower(),
        monitor.AGENT_ID_REGISTRY.lower(),
        "0x10ed43c718714eb63d5aa57b78b54704e256024e",
        "0x13f4ea83d0bd40e75c8222255bc855a974568dd4"
    }

    last_block = rpc.get_block_number() - 1
    processed_txs = set()

    print(f"[🚀 Worker Engine] Loaded {len(history)} historical drops and {len(smart_wallets)} smart wallets.", flush=True)
    print(f"[📡 Worker Engine] Starting live listener from block #{last_block}...", flush=True)

    while True:
        try:
            curr_block = rpc.get_block_number()
            if curr_block <= last_block:
                time.sleep(1.0)
                continue

            # Process up to 10 blocks per tick to prevent starvation without skipping
            blocks_to_process = min(curr_block - last_block, 10)
            end_block = last_block + blocks_to_process

            for b in range(last_block + 1, end_block + 1):
                try:
                    block = rpc.get_block(b, full_tx=True)
                except Exception as e:
                    print(f"[⚠️ Block Fetch Notice #{b}] {e}", flush=True)
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

                    # 1. Smart Money Activity Tracker (5k Tracked Wallets)
                    if snd in smart_wallets and rcv and rcv not in known_infra and (len(inp) >= 10 or tx_val > 0):
                        aid = smart_wallets[snd]
                        with history_lock:
                            history = monitor.load_history()
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
                                    monitor.save_history(history)
                                    print(f"[🧠 Smart Money Inflow] {aid} called {item.get('name')} ({rcv})", flush=True)
                        if not matching:
                            try:
                                det = monitor.analyze_contract(rpc, rcv, wallet)
                                cls = det.get("classification", {})
                                if not monitor.is_system_infra({"contract": rcv, "classification": cls, "name": det.get("name"), "symbol": det.get("symbol"), "max_supply": det.get("max_supply"), "total_minted": det.get("total_minted")}):
                                    mkt = monitor.analyze_market_interest(rpc, rcv, None, det, history=history)
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
                                        "score": mkt.get("score", 0),
                                        "rating": mkt.get("rating", "N/A"),
                                        "lp_usd": mkt.get("lp_usd", 0),
                                        "lp_wbnb": mkt.get("lp_wbnb", 0),
                                        "token_symbol": mkt.get("token_symbol", ""),
                                        "token_price_usd": mkt.get("token_price_usd", 0),
                                        "holders": mkt.get("holders", 1),
                                        "buys_24h": mkt.get("buys_24h", 1),
                                        "buys_1h": mkt.get("buys_1h", 1),
                                        "sells_24h": mkt.get("sells_24h", 0),
                                        "volume_24h": mkt.get("volume_24h", 0.0),
                                        "market_url": det.get("market_url", ""),
                                        "pair_url": mkt.get("pair_url", ""),
                                        "mint_calldata": mint_cd,
                                        "signals": mkt.get("signals", [f"Discovered via smart wallet {aid}"]),
                                        "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                        "smart_wallets": [aid],
                                        "smart_minters": [aid],
                                        "has_refund": det.get("has_refund", False),
                                        "security": mkt.get("security", {}),
                                        "risk_level": mkt.get("risk_level", "SAFE"),
                                        "risk_badge": mkt.get("risk_badge", "🟢 Clean"),
                                        "risk_flags": mkt.get("risk_flags", []),
                                        "sniper_ready": mkt.get("sniper_ready", False),
                                        "sniper_status": mkt.get("sniper_status", "TRACKING"),
                                        "sniper_badge": mkt.get("sniper_badge", "⏳ Tracking"),
                                        "sniper_checklist": mkt.get("sniper_checklist", [])
                                    }
                                    history.insert(0, new_entry)
                                    monitor.save_history(history)
                                    print(f"[🔥 NEW ALPHA DROP DISCOVERED] {det.get('name')} ({rcv}) via {aid}!", flush=True)
                            except Exception as ex:
                                print(f"[⚠️ Analysis Error] {ex}", flush=True)

                    # 2. Block-0 Agent ID Registry Reference Scanner
                    if monitor.AGENT_ID_BYTES in inp:
                        try:
                            rcpt = rpc.get_receipt(tx_h)
                            if rcpt and int(rcpt.get("status", "0"), 16) == 1:
                                dep_addr = rcpt.get("contractAddress")
                                if not dep_addr:
                                    cand = tx.get("to")
                                    if cand and len(cand) == 42 and cand.startswith("0x") and cand.lower() not in known_infra:
                                        code = rpc.get_code(cand)
                                        if code and len(code) > 2:
                                            dep_addr = cand

                                if dep_addr:
                                    dep_addr = dep_addr.lower()
                                    with history_lock:
                                        history = monitor.load_history()
                                        if not any(x.get("contract", "").lower() == dep_addr for x in history):
                                            det = monitor.analyze_contract(rpc, dep_addr, wallet)
                                            cls = det.get("classification", {})
                                            if not monitor.is_system_infra({"contract": dep_addr, "classification": cls, "name": det.get("name"), "symbol": det.get("symbol"), "max_supply": det.get("max_supply"), "total_minted": det.get("total_minted")}):
                                                mkt = monitor.analyze_market_interest(rpc, dep_addr, None, det, history=history)

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
                                                    "score": mkt.get("score", 0),
                                                    "rating": mkt.get("rating", "N/A"),
                                                    "lp_usd": mkt.get("lp_usd", 0),
                                                    "lp_wbnb": mkt.get("lp_wbnb", 0),
                                                    "token_symbol": mkt.get("token_symbol", ""),
                                                    "token_price_usd": mkt.get("token_price_usd", 0),
                                                    "holders": mkt.get("holders", 1),
                                                    "buys_24h": mkt.get("buys_24h", 1),
                                                    "buys_1h": mkt.get("buys_1h", 1),
                                                    "sells_24h": mkt.get("sells_24h", 0),
                                                    "volume_24h": mkt.get("volume_24h", 0.0),
                                                    "market_url": det.get("market_url", ""),
                                                    "pair_url": mkt.get("pair_url", ""),
                                                    "mint_calldata": mint_cd,
                                                    "signals": mkt.get("signals", ["Block-0 Agent ID Registry Drop"]),
                                                    "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                    "has_refund": det.get("has_refund", False),
                                                    "security": mkt.get("security", {}),
                                                    "risk_level": mkt.get("risk_level", "SAFE"),
                                                    "risk_badge": mkt.get("risk_badge", "🟢 Clean"),
                                                    "risk_flags": mkt.get("risk_flags", []),
                                                    "sniper_ready": mkt.get("sniper_ready", False),
                                                    "sniper_status": mkt.get("sniper_status", "TRACKING"),
                                                    "sniper_badge": mkt.get("sniper_badge", "⏳ Tracking"),
                                                    "sniper_checklist": mkt.get("sniper_checklist", [])
                                                }
                                                history.insert(0, new_entry)
                                                monitor.save_history(history)
                                                print(f"[🚨 BLOCK-0 AGENT ID DROP] {det.get('name')} ({dep_addr}) deployed by {snd}!", flush=True)
                        except Exception as e:
                            print(f"[⚠️ Tx Error] {e}", flush=True)

                # Periodic cleanup of processed transactions set
                if len(processed_txs) > 10000:
                    processed_txs.clear()

            last_block = end_block

        except Exception as e:
            print(f"[⚠️ Engine Loop Error] {e}", flush=True)

        time.sleep(1.0)


def fast_sweep_contract(rpc: monitor.BscRpcClient, item: dict) -> tuple:
    """Fast-check supply & holders for a single contract without slow third-party API calls."""
    c = item.get("contract")
    if not c or not c.startswith("0x") or len(c) != 42:
        return c, None, None, None, False

    try:
        cur_minted = item.get("total_minted", 0)
        cur_supply = item.get("max_supply")

        # 1. Direct on-chain totalMinted / totalSupply query
        minted = monitor.get_token_count(rpc, c)
        if minted is None or minted == 0:
            minted = monitor.get_total_supply(rpc, c)

        if cur_supply is None or cur_supply == 0:
            supply = monitor.get_max_supply(rpc, c)
        else:
            supply = cur_supply

        changed = False
        if minted is not None and minted != cur_minted:
            changed = True
        if supply is not None and supply != cur_supply:
            changed = True

        # Quick estimate of progress
        prog = 0.0
        if supply and supply > 0 and minted is not None:
            prog = min(100.0, (minted / supply) * 100.0)

        # 2. Estimate on-chain holders
        holders = item.get("holders", 1)
        if minted is not None and minted > 0:
            holders = max(holders, min(minted, int(minted * 0.85) + 1))

        return c, minted, supply, holders, changed
    except Exception:
        return c, None, None, None, False


def run_fast_track_progress_sweeper_loop():
    """15-second Tiered Progress Sweeper."""
    print("[⚡ Fast-Track Sweeper] Initializing 15-second Tiered Progress Sweeper...", flush=True)
    time.sleep(3)
    rpc = monitor.BscRpcClient(monitor.RPC_ENDPOINTS)

    lane2_counter = 0

    while True:
        try:
            with history_lock:
                history = monitor.load_history()

            if not history:
                time.sleep(15)
                continue

            active_contracts = [
                x for x in history
                if not monitor.is_system_infra(x)
                and (
                    not x.get("max_supply")
                    or (x.get("total_minted", 0) or 0) < x.get("max_supply")
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
                sweep_targets.extend(lane2_items)
                lane2_counter = 0

            if sweep_targets:
                updates_count = 0
                sniper_alerts = []

                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                    futures = [executor.submit(fast_sweep_contract, rpc, it) for it in sweep_targets]
                    for fut in concurrent.futures.as_completed(futures):
                        try:
                            c_addr, minted, supply, holders, changed = fut.result()
                            if not c_addr or not changed:
                                continue

                            for it in history:
                                if it.get("contract", "").lower() == c_addr.lower():
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

                                    # Recalculate sniper checklist
                                    eva = monitor.evaluate_sniper_readiness(it)
                                    it["sniper_ready"] = eva.get("sniper_ready", False)
                                    it["sniper_status"] = eva.get("sniper_status", "TRACKING")
                                    it["sniper_badge"] = eva.get("sniper_badge", "⏳ Tracking")
                                    it["sniper_checklist"] = eva.get("sniper_checklist", [])

                                    if not it.get("sniper_ready_alerted") and it["sniper_ready"]:
                                        it["sniper_ready_alerted"] = True
                                        sniper_alerts.append(it)

                                    updates_count += 1
                                    break
                        except Exception:
                            pass

                if updates_count > 0:
                    with history_lock:
                        monitor.save_history(history)
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


def run_market_dynamics_updater_loop():
    """Continuous 24/7 background market & mint dynamics updater."""
    print("[📈 Market Engine] Initializing 24/7 Market Dynamics Updater...", flush=True)
    time.sleep(5)
    rpc = monitor.BscRpcClient(monitor.RPC_ENDPOINTS)

    while True:
        try:
            with history_lock:
                history = monitor.load_history()

            if not history:
                time.sleep(20)
                continue

            contracts_to_check = [
                x for x in history
                if not monitor.is_system_infra(x) and x.get("contract") and x["contract"].startswith("0x") and len(x["contract"]) == 42
            ]

            batch_size = 30
            dex_updates = {}
            for i in range(0, len(contracts_to_check), batch_size):
                chunk = contracts_to_check[i:i + batch_size]
                chunk_addrs = [x["contract"] for x in chunk]
                try:
                    url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(chunk_addrs)}"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = json.loads(resp.read().decode())
                        pairs = data.get("pairs") or []
                        for pair in pairs:
                            b_tok = pair.get("baseToken", {})
                            b_addr = (b_tok.get("address") or "").lower()
                            if b_addr and b_addr not in dex_updates:
                                dex_updates[b_addr] = pair
                except Exception:
                    pass
                time.sleep(0.5)

            updated_count = 0
            for item in history:
                c = (item.get("contract") or "").lower()
                pair = dex_updates.get(c)

                if pair:
                    try:
                        lp_info = pair.get("liquidity", {})
                        lp_usd = float(lp_info.get("usd") or 0.0)
                        item["lp_usd"] = lp_usd
                        if pair.get("priceUsd"):
                            item["token_price_usd"] = float(pair.get("priceUsd"))
                        if pair.get("baseToken", {}).get("symbol"):
                            item["token_symbol"] = pair["baseToken"]["symbol"]
                        if pair.get("url"):
                            item["pair_url"] = pair["url"]

                        vol = pair.get("volume", {})
                        item["volume_24h"] = float(vol.get("h24") or 0.0)

                        txns = pair.get("txns", {})
                        h24 = txns.get("h24", {})
                        item["buys_24h"] = int(h24.get("buys") or 0)
                        item["sells_24h"] = int(h24.get("sells") or 0)

                        h1 = txns.get("h1", {})
                        item["buys_1h"] = int(h1.get("buys") or 0)

                        updated_count += 1
                    except Exception:
                        pass
                else:
                    try:
                        pair_addr = monitor.get_pair_address(rpc, monitor.PANCAKE_FACTORY_V2, c, monitor.WBNB_ADDRESS)
                        if pair_addr:
                            item["pair_url"] = f"https://pancakeswap.finance/info/v2/pairs/{pair_addr}"
                            r0, r1 = monitor.get_reserves(rpc, pair_addr)
                            wbnb_reserve = (r1 if c < monitor.WBNB_ADDRESS.lower() else r0) / 1e18
                            item["lp_wbnb"] = wbnb_reserve
                            item["lp_usd"] = wbnb_reserve * 705.0
                            updated_count += 1
                    except Exception:
                        pass

                # Dynamic interest score recalculation
                try:
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

                except Exception:
                    pass

            if updated_count > 0:
                with history_lock:
                    monitor.save_history(history)
                print(f"[📈 Market Engine] Synced live market & mint metrics for {updated_count} contracts.", flush=True)

        except Exception as e:
            print(f"[⚠️ Market Engine Error] {e}", flush=True)

        time.sleep(30)


def run_element_nft_volume_sweeper_loop():
    """Continuous 24/7 background Element Market secondary volume and floor price sweeper."""
    print("[💎 Element NFT Sweeper] Initializing 24/7 Element Market Sweeper...", flush=True)
    time.sleep(7)
    rpc = monitor.BscRpcClient(monitor.RPC_ENDPOINTS)

    while True:
        try:
            with history_lock:
                history = monitor.load_history()

            if not history:
                time.sleep(60)
                continue

            nft_contracts = [
                x for x in history
                if not monitor.is_system_infra(x) and (
                    "721" in str(x.get("standard", ""))
                    or "NFT" in str(x.get("standard", ""))
                    or x.get("is_nft")
                ) and x.get("contract") and x["contract"].startswith("0x") and len(x["contract"]) == 42
            ]

            if not nft_contracts:
                time.sleep(60)
                continue

            updated_count = 0
            for item in nft_contracts:
                c = (item.get("contract") or "").lower()
                try:
                    data = monitor.fetch_element_market_collection(c)
                    if data:
                        floor = data.get("floor_price_bnb", 0.0)
                        vol = data.get("volume_bnb", 0.0)
                        sales = data.get("secondary_sales_count", 0)

                        if floor > 0:
                            item["floor_price_bnb"] = floor
                            item["floor_price_str"] = f"{floor:.3f} BNB"
                        if vol > 0:
                            item["volume_bnb"] = vol
                        if sales > 0:
                            item["secondary_sales_count"] = sales

                        eva = monitor.evaluate_nft_surge(item)
                        if eva.get("is_nft_surge"):
                            item["is_nft_surge"] = True
                            item["nft_surge_signal"] = eva.get("signal")
                            item["signals"] = list(set((item.get("signals") or []) + [eva["signal"]]))

                        updated_count += 1
                except Exception:
                    pass
                time.sleep(1.0)

            if updated_count > 0:
                with history_lock:
                    monitor.save_history(history)
                print(f"[💎 Element NFT Sweeper] Synced secondary volume/floor for {updated_count} NFT drops.", flush=True)

        except Exception as e:
            print(f"[⚠️ Element NFT Sweeper Error] {e}", flush=True)

        time.sleep(60)


def run_proxy_factory_watcher_loop():
    """Continuous 24/7 background listener detecting stealth factory & proxy clone drops."""
    print("[🧩 Proxy Factory Watcher] Initializing Stealth Factory & Proxy Clone Watcher...", flush=True)
    time.sleep(4)
    rpc = monitor.BscRpcClient(monitor.RPC_ENDPOINTS)
    last_block = rpc.get_block_number() - 1

    known_infra = {
        monitor.PANCAKE_FACTORY_V2.lower(),
        monitor.WBNB_ADDRESS.lower(),
        monitor.USDT_ADDRESS.lower(),
        monitor.AGENT_ID_REGISTRY.lower(),
        "0x10ed43c718714eb63d5aa57b78b54704e256024e",
        "0x13f4ea83d0bd40e75c8222255bc855a974568dd4"
    }

    while True:
        try:
            curr_block = rpc.get_block_number()
            if curr_block > last_block:
                for b in range(last_block + 1, min(curr_block + 1, last_block + 6)):
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

                        # Check for proxy deploy / clone patterns
                        is_proxy_call = any(sig in inp for sig in ["0x5c19a95c", "0x3644e515", "0x5824c65a", "0x485cc955"])
                        is_proxy_ref = monitor.AGENT_ID_BYTES in inp

                        if (is_proxy_call or is_proxy_ref) and rcv:
                            cand = rcv
                            is_proxy, proxy_type, impl_addr = monitor.is_proxy_contract(rpc, cand)
                            is_fac, fac_badge = monitor.detect_factory_pattern(rpc, cand)

                            if is_proxy or is_fac:
                                with history_lock:
                                    history = monitor.load_history()
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
                                            monitor.save_history(history)
                                            print(f"[🧩 PROXY / FACTORY UPDATED] {ex_item.get('name')} ({cand}) Proxy: {proxy_type} | Factory: {fac_badge}", flush=True)
                                        continue

                                det = monitor.analyze_contract(rpc, cand, monitor.DEFAULT_WALLET)
                                cls = det.get("classification", {})
                                if not monitor.is_system_infra({"contract": cand, "classification": cls, "name": det.get("name"), "symbol": det.get("symbol"), "max_supply": det.get("max_supply"), "total_minted": det.get("total_minted")}):
                                    mkt = monitor.analyze_market_interest(rpc, cand, None, det, history=history)
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
                                        "score": mkt.get("score", 0),
                                        "rating": mkt.get("rating", "N/A"),
                                        "lp_usd": mkt.get("lp_usd", 0),
                                        "lp_wbnb": mkt.get("lp_wbnb", 0),
                                        "token_symbol": mkt.get("token_symbol", ""),
                                        "token_price_usd": mkt.get("token_price_usd", 0),
                                        "holders": mkt.get("holders", 1),
                                        "buys_24h": mkt.get("buys_24h", 0),
                                        "buys_1h": mkt.get("buys_1h", 0),
                                        "sells_24h": mkt.get("sells_24h", 0),
                                        "volume_24h": mkt.get("volume_24h", 0.0),
                                        "market_url": det.get("market_url", ""),
                                        "pair_url": mkt.get("pair_url", ""),
                                        "mint_calldata": det.get("mint_calldata", "0x1249c58b"),
                                        "signals": mkt.get("signals", [f"Factory Clone: {proxy_type or 'Proxy'} via {fac_badge or 'Factory'}"]),
                                        "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                        "has_refund": det.get("has_refund", False),
                                        "security": mkt.get("security", {}),
                                        "risk_level": mkt.get("risk_level", "SAFE"),
                                        "risk_badge": mkt.get("risk_badge", "🟢 Clean"),
                                        "risk_flags": mkt.get("risk_flags", []),
                                        "sniper_ready": mkt.get("sniper_ready", False),
                                        "sniper_status": mkt.get("sniper_status", "TRACKING"),
                                        "sniper_badge": mkt.get("sniper_badge", "⏳ Tracking"),
                                        "sniper_checklist": mkt.get("sniper_checklist", []),
                                        "is_proxy_clone": proxy_type is not None,
                                        "proxy_type": proxy_type,
                                        "master_implementation": impl_addr,
                                        "is_factory": is_fac,
                                        "factory_badge": fac_badge
                                    }
                                    with history_lock:
                                        history = monitor.load_history()
                                        if not any(x.get("contract", "").lower() == cand.lower() for x in history):
                                            history.insert(0, new_entry)
                                            monitor.save_history(history)
                                            print(f"[🧩 NEW PROXY CLONE DETECTED] {new_entry['name']} ({cand}) [{proxy_type}] Master: {impl_addr}", flush=True)

                last_block = curr_block
        except Exception as e:
            print(f"[⚠️ Proxy Factory Watcher Error] {e}", flush=True)

        time.sleep(20)


def start_worker():
    """Starts all 5 background worker threads in this isolated producer process."""
    print("[⚙️ Worker Process] Starting all 5 autonomous discovery & sweeper loops...", flush=True)
    threads = [
        threading.Thread(target=run_discovery_loop, name="DiscoveryLoop", daemon=True),
        threading.Thread(target=run_market_dynamics_updater_loop, name="MarketUpdater", daemon=True),
        threading.Thread(target=run_fast_track_progress_sweeper_loop, name="FastSweeper", daemon=True),
        threading.Thread(target=run_element_nft_volume_sweeper_loop, name="ElementSweeper", daemon=True),
        threading.Thread(target=run_proxy_factory_watcher_loop, name="ProxyWatcher", daemon=True)
    ]
    for t in threads:
        t.start()

    # Keep worker process alive
    try:
        while True:
            time.sleep(1.0)
    except (KeyboardInterrupt, SystemExit):
        print("[⚙️ Worker Process] Stopping worker...", flush=True)


if __name__ == "__main__":
    start_worker()

