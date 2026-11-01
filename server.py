#!/usr/bin/env python3
"""
Fly.io Autonomous Background Discovery Engine & Web/API Server
Runs 24/7 in the cloud:
1. Real-Time Smart Wallet Tracker (5,000 Tracked Wallets)
2. Block-0 Agent ID Registry Deployment Scanner
3. Instant Anti-Rug Security Audit & Dynamic history.json updates
4. Web Server on port 8080 serving live drops to Vercel
"""

import os
import sys
import time
import json
import re
import threading
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import monitor

history_lock = threading.Lock()


class AgentIdServerHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # Enable CORS for Vercel and cross-origin access
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.path in ("/api/sync", "/sync"):
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                try:
                    body = self.rfile.read(content_length).decode("utf-8")
                    incoming = json.loads(body)
                    if isinstance(incoming, list):
                        with history_lock:
                            current = monitor.load_history()
                            merged_dict = {x["contract"].lower(): x for x in current if x.get("contract")}
                            for item in incoming:
                                c = (item.get("contract") or "").lower()
                                if c:
                                    if c not in merged_dict:
                                        merged_dict[c] = item
                                    else:
                                        for k, v in item.items():
                                            if v is not None and (k not in merged_dict[c] or merged_dict[c][k] is None or k == "user_balance"):
                                                merged_dict[c][k] = v
                            new_history = list(merged_dict.values())
                            new_history.sort(key=lambda x: x.get("block", 0) or 0, reverse=True)
                            monitor.save_history(new_history)
                        self.send_response(200)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"success": True, "count": len(new_history)}).encode("utf-8"))
                        return
                except Exception as e:
                    self.send_response(500)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                    return
        self.send_response(404)
        self.end_headers()


def run_discovery_loop():
    """Continuous 24/7 headless discovery scanning BSC blocks for smart money & registry drops."""
    print("[🚀 Fly.io Engine] Initializing 24/7 Autonomous Scanner...")
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

    print(f"[🚀 Fly.io Engine] Loaded {len(history)} historical drops and {len(smart_wallets)} smart wallets.")
    print(f"[📡 Fly.io Engine] Starting live listener from block #{last_block}...")

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
                    print(f"[⚠️ Block Fetch Notice #{b}] {e}")
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

                    # 1. Smart Money Activity Tracker (5k Tracked Wallets)
                    if snd in smart_wallets and rcv and rcv not in known_infra and len(inp) >= 10:
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
                                    monitor.save_history(history)
                                    print(f"[🧠 Smart Money Inflow] {aid} called {item.get('name')} ({rcv})")
                        if not matching:
                            try:
                                det = monitor.analyze_contract(rpc, rcv, wallet)
                                cls = det.get("classification", {})
                                if not monitor.is_system_infra({"contract": rcv, "classification": cls, "name": det.get("name"), "symbol": det.get("symbol"), "max_supply": det.get("max_supply"), "total_minted": det.get("total_minted")}):
                                    mkt = monitor.analyze_market_interest(rpc, rcv, None, det)
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
                                        "has_free_mint": det.get("has_free_mint"),
                                        "mint_price_str": det.get("mint_price_str", "TBD"),
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
                                        "buys_24h": mkt.get("buys_24h", 1),
                                        "buys_1h": mkt.get("buys_1h", 1),
                                        "sells_24h": mkt.get("sells_24h", 0),
                                        "volume_24h": mkt.get("volume_24h", 0.0),
                                        "market_url": det.get("market_url", ""),
                                        "pair_url": mkt.get("pair_url", ""),
                                        "mint_calldata": det.get("mint_calldata", inp),
                                        "signals": mkt.get("signals", [f"Discovered via smart wallet {aid}"]),
                                        "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                        "smart_wallets": [aid],
                                        "smart_minters": [aid]
                                    }
                                    history.insert(0, new_entry)
                                    monitor.save_history(history)
                                    print(f"[🔥 NEW ALPHA DROP DISCOVERED] {det.get('name')} ({rcv}) via {aid}!")
                            except Exception as ex:
                                print(f"[⚠️ Analysis Error] {ex}")

                    # 2. Block-0 Agent ID Registry Reference Scanner
                    if monitor.AGENT_ID_BYTES in inp:
                        try:
                            rcpt = rpc.get_receipt(tx_h)
                            if rcpt and int(rcpt.get("status", "0"), 16) == 1:
                                dep_addr = rcpt.get("contractAddress")
                                if not dep_addr:
                                    for log in rcpt.get("logs", []):
                                        log_addr = log.get("address")
                                        if log_addr:
                                            code = rpc.get_code(log_addr)
                                            if monitor.AGENT_ID_BYTES in (code or "").lower():
                                                dep_addr = log_addr
                                                break
                                if dep_addr and not any(x.get("contract", "").lower() == dep_addr.lower() for x in history):
                                    det = monitor.analyze_contract(rpc, dep_addr, wallet)
                                    cls = det.get("classification", {})
                                    mkt = monitor.analyze_market_interest(rpc, dep_addr, tx.get("from"), det)
                                    new_entry = {
                                        "block": b,
                                        "contract": dep_addr,
                                        "deployer": tx.get("from"),
                                        "tx_hash": tx_h,
                                        "name": det.get("name") or "New Contract",
                                        "symbol": det.get("symbol") or "",
                                        "standard": cls.get("standard", ""),
                                        "icon": cls.get("icon", ""),
                                        "is_paused": det.get("is_paused"),
                                        "has_free_mint": det.get("has_free_mint"),
                                        "mint_price_str": det.get("mint_price_str", "TBD"),
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
                                        "holders": mkt.get("holders", 0),
                                        "buys_24h": mkt.get("buys_24h", 0),
                                        "buys_1h": mkt.get("buys_1h", 0),
                                        "sells_24h": mkt.get("sells_24h", 0),
                                        "volume_24h": mkt.get("volume_24h", 0.0),
                                        "market_url": det.get("market_url", ""),
                                        "pair_url": mkt.get("pair_url", ""),
                                        "mint_calldata": det.get("mint_calldata", "0x1249c58b"),
                                        "signals": mkt.get("signals", ["Block-0 Agent ID Deployment"]),
                                        "detected_at": time.strftime("%Y-%m-%d %H:%M:%S")
                                    }
                                    with history_lock:
                                        history = monitor.load_history()
                                        if not any(x.get("contract", "").lower() == dep_addr.lower() for x in history):
                                            history.insert(0, new_entry)
                                            monitor.save_history(history)
                                            print(f"[🔥 NEW AGENT ID DROP DISCOVERED] {det.get('name')} ({dep_addr}) on block #{b}!")
                        except Exception as ex:
                            print(f"[⚠️ Block-0 Analysis Error] {ex}")

                # Clean in-memory processed transaction set to keep RAM footprint low (<150MB)
                if len(processed_txs) > 20000:
                    processed_txs.clear()

            last_block = end_block
            time.sleep(2)

        except Exception as e:
            print(f"[⚠️ Discovery Loop Error] {e}")
            time.sleep(2)


def run_market_dynamics_updater_loop():
    """
    Continuous 24/7 background market & mint dynamics updater:
    1. DexScreener batch lookup (30 tokens/request) for live price, LP depth, 24h volume, and tx counts across ALL tracked contracts.
    2. Direct PancakeSwap V2 RPC reserve checks for tokens without DexScreener pairs.
    3. Live on-chain mint progress and supply sync (totalSupply, totalMinted, tokenCount).
    4. Dynamic interest score and rating adjustments based on live liquidity, trading velocity, and sell-out %.
    5. Thread-safe atomic persistence to history.json.
    """
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

            bnb_price = monitor.get_bnb_price_usd(rpc)
            all_contracts = [x.get("contract").lower() for x in history if x.get("contract")]

            # 1. DexScreener Batch Sync (30 contracts per API call)
            best_pairs = {}
            for i in range(0, len(all_contracts), 30):
                batch = all_contracts[i:i+30]
                batch_str = ",".join(batch)
                url = f"https://api.dexscreener.com/latest/dex/tokens/{batch_str}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                try:
                    with urllib.request.urlopen(req, timeout=6.0) as r:
                        data = json.loads(r.read().decode("utf-8"))
                        for p in data.get("pairs") or []:
                            base_addr = (p.get("baseToken", {}).get("address") or "").lower()
                            liq = float(p.get("liquidity", {}).get("usd") or 0.0)
                            if base_addr not in best_pairs or liq > best_pairs[base_addr]["liq"]:
                                best_pairs[base_addr] = {"pair": p, "liq": liq}
                except Exception as dex_err:
                    print(f"[⚠️ DexScreener Batch Warning] {dex_err}", flush=True)
                time.sleep(0.3)

            dex_updated = 0
            pcs_updated = 0
            mint_updated = 0

            for item in history:
                c = (item.get("contract") or "").lower()
                std = str(item.get("standard") or "")

                # 1. Apply DexScreener match
                if c in best_pairs:
                    p = best_pairs[c]["pair"]
                    item["token_price_usd"] = float(p.get("priceUsd") or 0.0)
                    item["token_price_bnb"] = float(p.get("priceNative") or 0.0)
                    item["lp_usd"] = float(p.get("liquidity", {}).get("usd") or 0.0)
                    item["volume_24h"] = float(p.get("volume", {}).get("h24") or 0.0)
                    txns = p.get("txns", {})
                    item["buys_24h"] = int(txns.get("h24", {}).get("buys") or 0)
                    item["buys_1h"] = int(txns.get("h1", {}).get("buys") or 0)
                    item["sells_24h"] = int(txns.get("h24", {}).get("sells") or 0)
                    if p.get("url"):
                        item["pair_url"] = p.get("url")
                    dex_updated += 1

                # 2. Direct PancakeSwap V2 Check if LP is still 0
                if ("BEP-20" in std or "Token" in std) and float(item.get("lp_usd", 0) or 0) == 0:
                    try:
                        pair_call = "0xe6a43905" + c[2:].zfill(64) + monitor.WBNB_ADDRESS[2:].lower().zfill(64)
                        res = rpc.call_contract(monitor.PANCAKE_FACTORY_V2, pair_call)
                        if res and len(res) >= 66:
                            lp_addr = "0x" + res[-40:].lower()
                            if lp_addr != "0x" + "0"*40:
                                reserves = rpc.call_contract(lp_addr, "0x0902f1ac")
                                if reserves and len(reserves) >= 130:
                                    r0 = int(reserves[2:66], 16)
                                    r1 = int(reserves[66:130], 16)
                                    t0 = rpc.call_contract(lp_addr, "0x0dfe1681")
                                    if monitor.WBNB_ADDRESS[2:].lower() in (t0 or "").lower():
                                        wbnb_r = r0 / 1e18
                                        tok_r = r1 / 1e18
                                    else:
                                        wbnb_r = r1 / 1e18
                                        tok_r = r0 / 1e18
                                    item["lp_wbnb"] = wbnb_r
                                    item["lp_usd"] = wbnb_r * bnb_price * 2
                                    if tok_r > 0:
                                        item["token_price_usd"] = (wbnb_r * bnb_price) / tok_r
                                        item["token_price_bnb"] = wbnb_r / tok_r
                                    if not item.get("pair_url"):
                                        item["pair_url"] = f"https://dexscreener.com/bsc/{lp_addr}"
                                    pcs_updated += 1
                    except Exception:
                        pass

                # 3. Live On-Chain Mint Progress Check
                ms = item.get("max_supply")
                tm = item.get("total_minted")
                if ms is None or (isinstance(ms, (int, float)) and (tm is None or tm < ms)):
                    for sel_name in ["totalMinted()", "_totalMinted()", "totalSupply()", "tokenCount()", "nextTokenId()"]:
                        m_res = rpc.call_contract(c, monitor.keccak_selector(sel_name))
                        if m_res and len(m_res) >= 66:
                            try:
                                val = int(m_res[:66], 16)
                                if "nextTokenId" in sel_name and val > 0:
                                    val = val - 1
                                if "BEP-20" in std:
                                    dec = 18
                                    m_dec = re.search(r"(\d+)\s*decimals?", std, re.I)
                                    if m_dec:
                                        dec = int(m_dec.group(1))
                                    val = val / (10 ** dec)
                                if val != tm and val > 0:
                                    item["total_minted"] = val
                                    mint_updated += 1
                                break
                            except Exception:
                                pass

                # 4. Dynamic Interest Score & Rating Calculation
                cur_lp = float(item.get("lp_usd", 0) or 0)
                cur_vol = float(item.get("volume_24h", 0) or 0)
                cur_tm = float(item.get("total_minted", 0) or 0)
                cur_ms = float(item.get("max_supply", 0) or 0)
                cur_hld = int(item.get("holders", 0) or 0)

                score = 0
                if cur_lp >= 20000:
                    score += 35
                elif cur_lp >= 5000:
                    score += 25
                elif cur_lp >= 1000:
                    score += 15
                elif cur_lp > 0:
                    score += 8

                if cur_vol >= 50000:
                    score += 20
                elif cur_vol >= 5000:
                    score += 10
                elif cur_vol > 0:
                    score += 5

                if cur_ms > 0:
                    pct = (cur_tm / cur_ms) * 100.0
                    if pct >= 99.9:
                        score += 30
                    elif pct >= 80.0:
                        score += 20
                    elif pct >= 50.0:
                        score += 10

                if cur_hld >= 2000:
                    score += 20
                elif cur_hld >= 500:
                    score += 15
                elif cur_hld >= 100:
                    score += 10

                risk_lvl = item.get("risk_level", "SAFE")
                if risk_lvl == "RUG":
                    score = 0
                elif risk_lvl == "HIGH":
                    score = max(0, score - 25)
                elif risk_lvl == "SAFE" and cur_lp > 0:
                    score = min(100, score + 10)

                score = max(0, min(100, score))
                item["score"] = score

                if score >= 70:
                    item["rating"] = "🚀 VIRAL RUSH"
                elif score >= 50:
                    item["rating"] = "🔥 HIGH TRACTION"
                elif score >= 25:
                    item["rating"] = "🌱 WARM INTEREST"
                else:
                    item["rating"] = "❄️ COLD / DORMANT"

            # 5. Thread-safe Atomic Persistence
            with history_lock:
                monitor.save_history(history)

            print(f"[📈 Market Dynamics Sync] Complete: {dex_updated} DEX pairs, {pcs_updated} PancakeSwap pools, {mint_updated} mint updates across {len(history)} assets.", flush=True)

        except Exception as e:
            print(f"[⚠️ Market Updater Error] {e}", flush=True)

        # Polling frequency: 60 seconds
        time.sleep(60)


def start_server(port=8080):
    """Starts the HTTP server on port 8080."""
    server_address = ("", port)
    httpd = ThreadingHTTPServer(server_address, AgentIdServerHandler)
    print(f"[🌐 Web & API Server] Serving on http://0.0.0.0:{port}...")
    httpd.serve_forever()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))

    # 1. Start background block scanner thread
    scanner_thread = threading.Thread(target=run_discovery_loop, daemon=True)
    scanner_thread.start()

    # 2. Start background market dynamics updater thread
    market_thread = threading.Thread(target=run_market_dynamics_updater_loop, daemon=True)
    market_thread.start()

    # 3. Start HTTP server on main thread
    start_server(port=port)
