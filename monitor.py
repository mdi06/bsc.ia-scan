#!/usr/bin/env python3
"""
Binance .ai Agent ID Registry Real-Time Monitor
===============================================
Monitors BNB Smart Chain (BSC) blocks in real-time to detect new smart contracts
and fair-mints that integrate with the official Space ID / Binance Agent ID registry:
Registry Address: 0x60B9262456dE011F7d15a54CdD8c48bAaa8B022F

Features:
- Block-0 Discovery: Catches contract deployments referencing the Agent ID registry.
- Automatic Asset Classification: Distinguishes between ERC-721 (NFT), ERC-1155, BEP-20 (Token), and Fair-Mint dispensaries.
- Under-the-hood Token Resolution: For fair-mint contracts (like Jager), extracts the underlying token being minted.
- Instant Security & Mint Audit: Checks for free mints, supply, and function selectors.
- Terminal & Audio Alerts (macOS).
- One-click CLI command generator for instant preview & execution with `baw`.
"""

import os
import sys
import time
import json
import urllib.request
import subprocess
import argparse
import select
import tty
import termios
import threading
import webbrowser
from typing import Optional, Dict, Any, List, Tuple

try:
    from rich.console import Console, Group
    from rich.table import Table
    from rich.panel import Panel
    from rich.live import Live
    from rich.text import Text
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# Official Binance .ai Agent ID Registry on BSC
AGENT_ID_REGISTRY = "0x60B9262456dE011F7d15a54CdD8c48bAaa8B022F"
AGENT_ID_BYTES = "60b9262456de011f7d15a54cdd8c48baaa8b022f"

# Default BSC RPC Endpoints (with fallback)
RPC_ENDPOINTS = [
    "https://bsc-dataseed.bnbchain.org",
    "https://bsc-dataseed1.defibit.io",
    "https://bsc-dataseed1.ninicoin.io"
]

DEFAULT_WALLET = "0xA3FbE3664dB8478c094dD36b2BB306d72CBCb37A"

# On-Chain DEX & Infrastructure Addresses
PANCAKE_FACTORY_V2 = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
WBNB_ADDRESS = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
USDT_ADDRESS = "0x55d398326f99059fF775485246999027B3197955"

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")

# ANSI Terminal Colors
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"
C_CYAN = "\033[36m"
C_RED = "\033[31m"


class BscRpcClient:
    def __init__(self, endpoints: List[str]):
        self.endpoints = endpoints
        self.current_idx = 0

    def call(self, method: str, params: list) -> Any:
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        
        for _ in range(len(self.endpoints)):
            rpc_url = self.endpoints[self.current_idx]
            try:
                req = urllib.request.Request(rpc_url, data=payload, headers=headers)
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if "error" in data:
                        err = data["error"]
                        err_msg = str(err).lower()
                        # Normal EVM reverts (e.g. function doesn't exist on contract)
                        if "revert" in err_msg or "execution reverted" in err_msg:
                            return None
                        raise RuntimeError(data["error"])
                    return data.get("result")
            except Exception:
                self.current_idx = (self.current_idx + 1) % len(self.endpoints)
                continue
        return None

    def get_block_number(self) -> int:
        res = self.call("eth_blockNumber", [])
        return int(res, 16)

    def get_block(self, block_num: int, full_tx: bool = True) -> Dict[str, Any]:
        return self.call("eth_getBlockByNumber", [hex(block_num), full_tx]) or {}

    def get_code(self, address: str) -> str:
        return (self.call("eth_getCode", [address, "latest"]) or "").lower()

    def get_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        return self.call("eth_getTransactionReceipt", [tx_hash])

    def get_balance(self, address: str) -> int:
        res = self.call("eth_getBalance", [address, "latest"])
        return int(res, 16) if res else 0

    def get_tx_count(self, address: str) -> int:
        res = self.call("eth_getTransactionCount", [address, "latest"])
        return int(res, 16) if res else 0

    def get_logs(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return self.call("eth_getLogs", [params]) or []

    def call_contract(self, to: str, data: str) -> Optional[str]:
        try:
            res = self.call("eth_call", [{"to": to, "data": data}, "latest"])
            return res if res and res != "0x" else None
        except Exception:
            return None


def keccak_selector(func_sig: str) -> str:
    common = {
        "name()": "0x06fdde03",
        "symbol()": "0x95d89b41",
        "decimals()": "0x313ce567",
        "totalSupply()": "0x18160ddd",
        "freeAvailable(address)": "0x9a5f5d31",
        "mint()": "0x1249c58b",
        "mint(uint8)": "0x6ecd2306",
        "quoteBNB(uint8)": "0x41aa70ca",
        "price()": "0xa035b1fe",
        "status()": "0x9508933b",
        "token()": "0xfc0c546a",
        "ownerOf(uint256)": "0x6352211e",
        "tokenURI(uint256)": "0xc87b56dd",
        "supportsInterface(bytes4)": "0x01ffc9a7",
        "paused()": "0x5c975abb",
        "MAX_SUPPLY()": "0x32cb6b0c",
        "totalMinted()": "0xa2309ff8",
        "agentKeyOf(address)": "0x1f1b9756"
    }
    if func_sig in common:
        return common[func_sig]
    try:
        from Crypto.Hash import keccak
        k = keccak.new(digest_bits=256)
        k.update(func_sig.encode("utf-8"))
        return "0x" + k.hexdigest()[:8]
    except ImportError:
        return ""


def decode_string(hex_res: str) -> str:
    try:
        raw = bytes.fromhex(hex_res[2:])
        if len(raw) < 64:
            return raw.decode("utf-8", errors="ignore").strip("\x00")
        offset = int.from_bytes(raw[0:32], "big")
        length = int.from_bytes(raw[offset:offset+32], "big")
        str_bytes = raw[offset+32:offset+32+length]
        return str_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def classify_asset_type(rpc: BscRpcClient, addr: str) -> Dict[str, Any]:
    """
    Rigorously detects whether a contract is:
    1. ERC-721 (NFT)
    2. ERC-1155 (Semi-Fungible NFT)
    3. BEP-20 (Fungible Token)
    4. Fair-Mint Contract (Dispenses an underlying BEP-20 token, e.g. Jager)
    """
    # 1. Check ERC-721 Interface via ERC-165 (0x80ac58cd)
    erc721_call = "0x01ffc9a780ac58cd00000000000000000000000000000000000000000000000000000000"
    res = rpc.call_contract(addr, erc721_call)
    if res and int(res, 16) == 1:
        return {
            "category": "NFT",
            "standard": "ERC-721 (NFT Collection)",
            "icon": "🎨",
            "is_nft": True,
            "underlying": None
        }

    # 2. Check ERC-1155 Interface via ERC-165 (0xd9b67a26)
    erc1155_call = "0x01ffc9a7d9b67a2600000000000000000000000000000000000000000000000000000000"
    res = rpc.call_contract(addr, erc1155_call)
    if res and int(res, 16) == 1:
        return {
            "category": "NFT",
            "standard": "ERC-1155 (Multi-Token NFT)",
            "icon": "🖼️",
            "is_nft": True,
            "underlying": None
        }

    # 3. Direct BEP-20: check decimals()
    dec = rpc.call_contract(addr, "0x313ce567")
    if dec and len(dec) >= 66:
        d_val = int(dec, 16)
        if 0 <= d_val <= 36:
            return {
                "category": "TOKEN",
                "standard": f"BEP-20 Token ({d_val} decimals)",
                "icon": "🪙",
                "is_nft": False,
                "decimals": d_val,
                "underlying": None
            }

    # 4. Check if it's a Fair-Mint contract dispensing a BEP-20 token (like Jager)
    token_res = rpc.call_contract(addr, "0xfc0c546a")  # token()
    if token_res and len(token_res) >= 66:
        underlying = "0x" + token_res[-40:].lower()
        underlying_dec = rpc.call_contract(underlying, "0x313ce567")
        if underlying_dec and len(underlying_dec) >= 66:
            d_val = int(underlying_dec, 16)
            name_res = rpc.call_contract(underlying, "0x06fdde03")
            sym_res = rpc.call_contract(underlying, "0x95d89b41")
            name = decode_string(name_res) if name_res else "Token"
            sym = decode_string(sym_res) if sym_res else ""
            return {
                "category": "TOKEN_FAIR_MINT",
                "standard": f"Fair-Mint Contract (Dispenses ${sym or name} Token)",
                "icon": "⚡",
                "is_nft": False,
                "decimals": d_val,
                "underlying": underlying,
                "underlying_name": name,
                "underlying_symbol": sym
            }

    # 5. Fallback for non-ERC165 compliant NFTs (check tokenURI)
    uri_res = rpc.call_contract(addr, "0xc87b56dd0000000000000000000000000000000000000000000000000000000000000001")
    if uri_res and uri_res != "0x":
        return {
            "category": "NFT",
            "standard": "ERC-721 (NFT Collection)",
            "icon": "🎨",
            "is_nft": True,
            "underlying": None
        }

    return {
        "category": "CUSTOM",
        "standard": "Custom Smart Contract",
        "icon": "⚙️",
        "is_nft": False,
        "underlying": None
    }


def analyze_contract(rpc: BscRpcClient, contract_addr: str, wallet_addr: str) -> Dict[str, Any]:
    classification = classify_asset_type(rpc, contract_addr)

    info = {
        "address": contract_addr,
        "name": "",
        "symbol": "",
        "classification": classification,
        "has_free_mint": False,
        "has_mint": False,
        "mint_price_bnb": 0.0,
        "mint_price_usd": 0.0,
        "mint_price_str": "TBD",
        "mint_value_wei": "0",
        "is_paused": None,
        "max_supply": None,
        "total_minted": None,
        "mint_calldata": "0x1249c58b",
        "mint_note": "",
        "website": "",
        "twitter": "",
        "market_url": ""
    }

    # Retrieve Name & Symbol
    name_res = rpc.call_contract(contract_addr, keccak_selector("name()"))
    if name_res:
        info["name"] = decode_string(name_res)

    symbol_res = rpc.call_contract(contract_addr, keccak_selector("symbol()"))
    if symbol_res:
        info["symbol"] = decode_string(symbol_res)

    # If it's a fair-mint contract without direct name, use the underlying token info
    if classification["category"] == "TOKEN_FAIR_MINT":
        if not info["name"]:
            info["name"] = classification.get("underlying_name", "")
        if not info["symbol"]:
            info["symbol"] = classification.get("underlying_symbol", "")

    # Check paused status
    pause_res = rpc.call_contract(contract_addr, keccak_selector("paused()"))
    if pause_res and len(pause_res) >= 66:
        info["is_paused"] = int(pause_res, 16) == 1

    # Check supply
    supply_res = rpc.call_contract(contract_addr, keccak_selector("MAX_SUPPLY()"))
    if supply_res and len(supply_res) >= 66:
        info["max_supply"] = int(supply_res, 16)
    minted_res = rpc.call_contract(contract_addr, keccak_selector("totalMinted()"))
    if minted_res and len(minted_res) >= 66:
        info["total_minted"] = int(minted_res, 16)

    code = rpc.get_code(contract_addr)
    param_wallet = wallet_addr[2:].lower().zfill(64)

    # Resolve marketplace URL
    if classification.get("category") == "NFT":
        info["market_url"] = f"https://element.market/assets/bsc/{contract_addr}"
    elif classification.get("category") == "TOKEN":
        info["market_url"] = f"https://pancakeswap.finance/swap?outputCurrency={contract_addr}"

    # Extract on-chain metadata URLs (contractURI / tokenURI)
    try:
        curi_res = rpc.call_contract(contract_addr, keccak_selector("contractURI()"))
        if curi_res:
            u = decode_string(curi_res)
            if u and u.startswith("http"):
                try:
                    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=1.5) as r:
                        meta = json.loads(r.read().decode("utf-8"))
                        info["website"] = meta.get("external_link") or meta.get("external_url") or meta.get("website") or ""
                        info["twitter"] = meta.get("twitter") or meta.get("twitter_username") or ""
                except Exception:
                    pass
    except Exception:
        pass

    if not info.get("website"):
        for tid in [1, 0]:
            try:
                turi_res = rpc.call_contract(contract_addr, keccak_selector("tokenURI(uint256)") + hex(tid)[2:].zfill(64))
                if turi_res:
                    u = decode_string(turi_res)
                    if u and u.startswith("http"):
                        m = re.match(r"(https?://[^/]+)", u)
                        if m and "ipfs" not in m.group(1):
                            info["website"] = m.group(1)
                        break
            except Exception:
                pass

    # If pure BEP-20 token with no public minting functions, price is DEX-driven
    if classification.get("category") == "TOKEN" and not any(m in code for m in ["1249c58b", "a0712d68", "836a1040", "6ecd2306"]):
        info["has_mint"] = False
        info["mint_price_str"] = "DEX Traded"
        info["mint_value_wei"] = "0"
        return info

    def check_universal_price_getters() -> Tuple[int, float, Optional[str]]:
        """Queries dynamic bonding curves, constants, and function quotes to find on-chain mint price."""
        PRICE_GETTERS = [
            ("currentPrice()", "0x9d1b464a"),
            ("PRICE()", "0x8d859f3e"),
            ("MINT_PRICE()", "0xc002d23d"),
            ("mintPrice()", "0x6817c76c"),
            ("price()", "0xa035b1fe"),
            ("fixedPriceWei()", "0xd4f456a1"),
            ("cost()", "0x13faede6"),
            ("cost(legacy)", "0x7856860a"),
            ("getPrice()", "0x98d5fdca"),
            ("getMintPrice()", "0xa7f93ebd"),
            ("bnbQuote(1)", "0x68bce8cd" + "1".zfill(64)),
            ("costOf(1)", "0xfd326592" + "1".zfill(64)),
            ("price(1)", "0x26a49e37" + "1".zfill(64)),
            ("cost(1)", "0x9097548d" + "1".zfill(64)),
            ("mintPrice(1)", "0xe6a72acf" + "1".zfill(64)),
            ("getPrice(1)", "0xe7572230" + "1".zfill(64)),
            ("mintFee()", "0x13966db5"),
            ("fee()", "0xddca3f43"),
            ("rate()", "0x2c4e722e"),
        ]
        for gname, calldata in PRICE_GETTERS:
            p_res = rpc.call_contract(contract_addr, calldata)
            if p_res and len(p_res) >= 66 and p_res != "0x" + "0"*64:
                val = int(p_res[:66], 16)
                if 0 < val < 10**22: # reasonable wei bound
                    bnb = val / 1e18
                    if bnb >= 0.0001:
                        p_str = f"{bnb:.4f} BNB"
                    elif bnb >= 0.000001:
                        p_str = f"{bnb:.6f} BNB"
                    else:
                        p_str = f"{val} wei"
                    return val, bnb, p_str
        return 0, 0.0, None

    # Pattern A: Worldygg / Hark / AI Frenzy style mint(uint256) -> selector 0xa0712d68
    if "a0712d68" in code:
        info["has_mint"] = True
        info["mint_calldata"] = "0xa0712d680000000000000000000000000000000000000000000000000000000000000001"
        info["mint_note"] = "mint(1)"
        
        # Check FREE_PER_WALLET()
        free_pw_res = rpc.call_contract(contract_addr, "0xcd90e5af")
        free_pw = int(free_pw_res, 16) if free_pw_res and len(free_pw_res) >= 66 else 0

        # Check quote(wallet, 1)
        quote_res = rpc.call_contract(contract_addr, "0x8f79306e" + param_wallet + "1".zfill(64))
        if quote_res and len(quote_res) >= 66:
            quote_val = int(quote_res, 16)
            if quote_val == 0 or free_pw > 0:
                info["has_free_mint"] = True
                info["mint_price_bnb"] = 0.0
                info["mint_price_str"] = "⭐ FREE"
                info["mint_value_wei"] = "0"
            else:
                info["mint_price_bnb"] = quote_val / 1e18
                info["mint_price_str"] = f"{quote_val / 1e18:.4f} BNB"
                info["mint_value_wei"] = str(quote_val)
        elif free_pw > 0:
            info["has_free_mint"] = True
            info["mint_price_str"] = "⭐ FREE"
            info["mint_value_wei"] = "0"

        # Check simulation with value 0 if still not confirmed free
        if not info["has_free_mint"]:
            try:
                sim_res = rpc.call("eth_estimateGas", [{
                    "from": wallet_addr,
                    "to": contract_addr,
                    "data": info["mint_calldata"],
                    "value": "0x0"
                }])
                if sim_res and isinstance(sim_res, str) and sim_res.startswith("0x"):
                    info["has_free_mint"] = True
                    info["mint_price_bnb"] = 0.0
                    info["mint_price_str"] = "⭐ FREE"
                    info["mint_value_wei"] = "0"
            except Exception:
                pass

        # Check secondary USD price if free, or primary USD price if paid
        usd_res = rpc.call_contract(contract_addr, "0x2bca65c3")
        if usd_res and len(usd_res) >= 66:
            usd_val = int(usd_res, 16) / 1e18
            if usd_val > 0:
                info["mint_price_usd"] = usd_val
                if info["has_free_mint"]:
                    info["mint_price_str"] = f"⭐ FREE (${usd_val:.2f})"
                else:
                    info["mint_price_str"] = f"${usd_val:.2f}"

        # If price still unresolved, check universal price getters (PRICE(), currentPrice(), etc.)
        if info["mint_price_str"] == "TBD" and not info["has_free_mint"]:
            val, bnb, p_str = check_universal_price_getters()
            if p_str:
                info["mint_price_bnb"] = bnb
                info["mint_price_str"] = p_str
                info["mint_value_wei"] = str(val)

    # Pattern B: Tiered Agent ID mint(uint8) -> selector 0x6ecd2306 (e.g. Mars Nexus)
    elif "6ecd2306" in code:
        info["has_mint"] = True
        info["mint_calldata"] = "0x6ecd23060000000000000000000000000000000000000000000000000000000000000001"
        info["mint_note"] = "Tier 1 Mint (mint(1))"
        quote1 = rpc.call_contract(contract_addr, keccak_selector("quoteBNB(uint8)") + "0000000000000000000000000000000000000000000000000000000000000001")
        if quote1 and len(quote1) >= 66:
            q_val = int(quote1, 16)
            if q_val == 0:
                info["has_free_mint"] = True
                info["mint_price_bnb"] = 0.0
                info["mint_price_str"] = "⭐ FREE"
                info["mint_value_wei"] = "0"
            else:
                info["mint_price_bnb"] = q_val / 1e18
                info["mint_price_str"] = f"{q_val / 1e18:.4f} BNB"
                info["mint_value_wei"] = str(q_val)

        # Check simulation with value 0
        if not info["has_free_mint"]:
            try:
                sim_res = rpc.call("eth_estimateGas", [{
                    "from": wallet_addr,
                    "to": contract_addr,
                    "data": info["mint_calldata"],
                    "value": "0x0"
                }])
                if sim_res and isinstance(sim_res, str) and sim_res.startswith("0x"):
                    info["has_free_mint"] = True
                    info["mint_price_bnb"] = 0.0
                    info["mint_price_str"] = "⭐ FREE"
                    info["mint_value_wei"] = "0"
            except Exception:
                pass

    # Pattern C: Fair-Mint Token Dispenser mint(uint256,address,uint256) -> selector 0x836a1040 (e.g. BNAGENT, BIBIAI)
    elif "836a1040" in code:
        info["has_mint"] = True
        quote_res = rpc.call_contract(contract_addr, "0x68bce8cd" + "1".zfill(64))
        quote_val = int(quote_res, 16) if quote_res and len(quote_res) >= 66 else 0
        if quote_val == 0:
            c_res = rpc.call_contract(contract_addr, "0xfd326592" + "1".zfill(64))
            quote_val = int(c_res, 16) if c_res and len(c_res) >= 66 else 0

        cents_res = rpc.call_contract(contract_addr, "0x30c951eb" + "1".zfill(64))
        cents = int(cents_res, 16) if cents_res and len(cents_res) >= 66 else 0

        if quote_val > 0:
            bnb = quote_val / 1e18
            info["mint_price_bnb"] = bnb
            info["mint_value_wei"] = str(quote_val)
            if cents > 0:
                info["mint_price_str"] = f"{bnb:.4f} BNB (${cents/100:.2f})"
            else:
                info["mint_price_str"] = f"{bnb:.4f} BNB"

        max_cost = quote_val * 11 // 10 if quote_val > 0 else 0
        info["mint_calldata"] = "0x836a1040" + "1".zfill(64) + param_wallet + hex(max_cost)[2:].zfill(64)
        info["mint_note"] = "mint(1, recipient, maxCost)"

    # Pattern D: Standard Fair Mint / ERC-721 mint() -> selector 0x1249c58b (e.g. Pup Pals, Mempalace, Quackle)
    else:
        info["mint_calldata"] = "0x1249c58b"
        free_sel = keccak_selector("freeAvailable(address)")
        if free_sel:
            free_res = rpc.call_contract(contract_addr, free_sel + param_wallet)
            if free_res and int(free_res, 16) == 1:
                info["has_free_mint"] = True
                info["mint_price_str"] = "⭐ FREE"
                info["mint_value_wei"] = "0"

        # Check simulation with value 0
        if not info["has_free_mint"]:
            try:
                sim_res = rpc.call("eth_estimateGas", [{
                    "from": wallet_addr,
                    "to": contract_addr,
                    "data": info["mint_calldata"],
                    "value": "0x0"
                }])
                if sim_res and isinstance(sim_res, str) and sim_res.startswith("0x"):
                    info["has_free_mint"] = True
                    info["mint_price_bnb"] = 0.0
                    info["mint_price_str"] = "⭐ FREE"
                    info["mint_value_wei"] = "0"
            except Exception:
                pass

        # Check general price methods if not free
        if not info["has_free_mint"]:
            # Check mintPriceUsd
            usd_res = rpc.call_contract(contract_addr, "0x2bca65c3")
            if usd_res and len(usd_res) >= 66:
                usd_val = int(usd_res, 16) / 1e18
                if usd_val > 0:
                    info["mint_price_usd"] = usd_val
                    info["mint_price_str"] = f"${usd_val:.2f}"

            if info["mint_price_str"] == "TBD":
                val, bnb, p_str = check_universal_price_getters()
                if p_str:
                    info["mint_price_bnb"] = bnb
                    info["mint_price_str"] = p_str
                    info["mint_value_wei"] = str(val)

    return info


def get_bnb_price_usd(rpc: BscRpcClient) -> float:
    try:
        call_data = "0xe6a43905" + WBNB_ADDRESS[2:].lower().zfill(64) + USDT_ADDRESS[2:].lower().zfill(64)
        pair_res = rpc.call_contract(PANCAKE_FACTORY_V2, call_data)
        if not pair_res or len(pair_res) < 66:
            return 750.0
        pair_addr = "0x" + pair_res[-40:]
        res = rpc.call_contract(pair_addr, "0x0902f1ac")
        if not res or len(res) < 130:
            return 750.0
        r0 = int(res[2:66], 16)
        r1 = int(res[66:130], 16)
        t0 = rpc.call_contract(pair_addr, "0x0dfe1681")
        if USDT_ADDRESS[2:].lower() in (t0 or "").lower():
            return (r0 / 1e18) / (r1 / 1e18)
        else:
            return (r1 / 1e18) / (r0 / 1e18)
    except Exception:
        return 750.0


def fetch_token_market_data(token_addr: str) -> Dict[str, Any]:
    """
    Fetches real-time on-chain holders and DEX purchase metrics:
    - On-chain holder count via GoPlus Security API (free, official BSC endpoint)
    - 24h & 1h purchase count (buys), sells, and volume via DexScreener API
    """
    res = {
        "holders": 0,
        "buys_24h": 0,
        "buys_1h": 0,
        "sells_24h": 0,
        "volume_24h": 0.0,
        "pair_url": "",
        "market_price_usd": 0.0,
        "market_price_bnb": 0.0,
        "website": "",
        "twitter": ""
    }
    if not token_addr or token_addr.lower() == "0x" + "0"*40:
        return res
    token_addr = token_addr.lower()

    # 1. On-Chain Holders via GoPlus Token Security
    try:
        url = f"https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses={token_addr}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=2.5) as r:
            payload = json.loads(r.read().decode("utf-8"))
            data = payload.get("result", {}).get(token_addr, {})
            hc = data.get("holder_count")
            if hc is not None:
                res["holders"] = int(hc)
    except Exception:
        pass

    # 2. Purchases (buys) & 24h volume via DexScreener
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_addr}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=2.5) as r:
            payload = json.loads(r.read().decode("utf-8"))
            pairs = payload.get("pairs") or []
            if pairs:
                p = pairs[0]
                txns = p.get("txns", {})
                res["buys_24h"] = txns.get("h24", {}).get("buys", 0)
                res["buys_1h"] = txns.get("h1", {}).get("buys", 0)
                res["sells_24h"] = txns.get("h24", {}).get("sells", 0)
                res["volume_24h"] = float(p.get("volume", {}).get("h24", 0.0) or 0.0)
                res["pair_url"] = p.get("url", "")
                res["market_price_usd"] = float(p.get("priceUsd") or 0.0)
                res["market_price_bnb"] = float(p.get("priceNative") or 0.0)
                p_info = p.get("info") or {}
                ws = p_info.get("websites") or []
                if ws:
                    res["website"] = ws[0].get("url", "")
                soc = p_info.get("socials") or []
                tw = [s.get("url") for s in soc if s.get("type") in ("twitter", "x")]
                if tw:
                    res["twitter"] = tw[0]
    except Exception:
        pass

    return res


def analyze_market_interest(rpc: BscRpcClient, contract_addr: str, deployer_addr: Optional[str], details: Dict[str, Any]) -> Dict[str, Any]:
    stats = {
        "score": 0,
        "rating": "❄️ COLD / DORMANT",
        "deployer_balance_bnb": 0.0,
        "deployer_nonce": 0,
        "lp_pool": None,
        "lp_wbnb": 0.0,
        "lp_usd": 0.0,
        "token_price_usd": 0.0,
        "token_price_bnb": 0.0,
        "token_symbol": "",
        "website": "",
        "twitter": "",
        "market_url": "",
        "holders": 0,
        "buys_24h": 0,
        "buys_1h": 0,
        "sells_24h": 0,
        "volume_24h": 0.0,
        "pair_url": "",
        "recent_mints": 0,
        "unique_minters": 0,
        "percent_claimed": 0.0,
        "signals": []
    }

    # 1. Resolve deployer if missing
    if not deployer_addr:
        owner_res = rpc.call_contract(contract_addr, keccak_selector("owner()"))
        if owner_res and len(owner_res) >= 66:
            deployer_addr = "0x" + owner_res[-40:].lower()

    if deployer_addr:
        try:
            stats["deployer_balance_bnb"] = rpc.get_balance(deployer_addr) / 1e18
            stats["deployer_nonce"] = rpc.get_tx_count(deployer_addr)
        except Exception:
            pass

    # 2. Identify associated token for liquidity analysis
    target_token = None
    token_sym = details.get("symbol", "")
    cls = details.get("classification", {})
    if cls.get("category") == "TOKEN":
        target_token = contract_addr
    elif cls.get("underlying"):
        target_token = cls["underlying"]
        token_sym = cls.get("underlying_symbol", "") or token_sym
    else:
        mars_res = rpc.call_contract(contract_addr, "0x7419f946")  # marsToken()
        if mars_res and len(mars_res) >= 66 and mars_res[-40:] != "0"*40:
            target_token = "0x" + mars_res[-40:].lower()
            sym_res = rpc.call_contract(target_token, keccak_selector("symbol()"))
            if sym_res:
                token_sym = decode_string(sym_res)

    stats["token_symbol"] = token_sym

    # 3. Live Holders & DEX Purchase Analytics
    if target_token:
        mkt_data = fetch_token_market_data(target_token)
        stats["holders"] = mkt_data.get("holders", 0)
        stats["buys_24h"] = mkt_data.get("buys_24h", 0)
        stats["buys_1h"] = mkt_data.get("buys_1h", 0)
        stats["sells_24h"] = mkt_data.get("sells_24h", 0)
        stats["volume_24h"] = mkt_data.get("volume_24h", 0.0)
        stats["pair_url"] = mkt_data.get("pair_url", "")
        if mkt_data.get("market_price_usd"):
            stats["token_price_usd"] = mkt_data["market_price_usd"]
        if mkt_data.get("market_price_bnb"):
            stats["token_price_bnb"] = mkt_data["market_price_bnb"]
        if mkt_data.get("website"):
            stats["website"] = mkt_data["website"]
        if mkt_data.get("twitter"):
            stats["twitter"] = mkt_data["twitter"]

    # For direct NFT/token mints without DEX pair yet
    if stats["holders"] == 0 and details.get("total_minted"):
        stats["holders"] = details.get("total_minted", 0)
    if stats["buys_24h"] == 0 and details.get("total_minted"):
        stats["buys_24h"] = details.get("total_minted", 0)

    # 4. Liquidity check on PancakeSwap V2
    if target_token:
        try:
            bnb_price = get_bnb_price_usd(rpc)
            pair_call = "0xe6a43905" + target_token[2:].lower().zfill(64) + WBNB_ADDRESS[2:].lower().zfill(64)
            pair_res = rpc.call_contract(PANCAKE_FACTORY_V2, pair_call)
            if pair_res and len(pair_res) >= 66:
                lp_addr = "0x" + pair_res[-40:].lower()
                if lp_addr != "0x" + "0"*40:
                    stats["lp_pool"] = lp_addr
                    reserves = rpc.call_contract(lp_addr, "0x0902f1ac")
                    if reserves and len(reserves) >= 130:
                        r0 = int(reserves[2:66], 16)
                        r1 = int(reserves[66:130], 16)
                        t0 = rpc.call_contract(lp_addr, "0x0dfe1681")
                        if WBNB_ADDRESS[2:].lower() in (t0 or "").lower():
                            wbnb_r = r0 / 1e18
                            token_r = r1 / 1e18
                        else:
                            wbnb_r = r1 / 1e18
                            token_r = r0 / 1e18
                        
                        stats["lp_wbnb"] = wbnb_r
                        stats["lp_usd"] = wbnb_r * bnb_price * 2
                        if token_r > 0:
                            stats["token_price_usd"] = (wbnb_r * bnb_price) / token_r
                            stats["token_price_bnb"] = wbnb_r / token_r
                            if not stats.get("pair_url"):
                                stats["pair_url"] = f"https://dexscreener.com/bsc/{lp_addr}"
        except Exception:
            pass

    # Supply metrics
    max_sup = details.get("max_supply")
    minted = details.get("total_minted")
    if max_sup and max_sup > 0 and minted is not None:
        stats["percent_claimed"] = (minted / max_sup) * 100.0

    # 5. Score calculation (0-100)
    score = 0
    signals = []

    lp_usd = stats["lp_usd"]
    if lp_usd >= 20000:
        score += 35
        signals.append(f"Deep DEX Liquidity (${lp_usd:,.0f})")
    elif lp_usd >= 5000:
        score += 25
        signals.append(f"Solid Seed Liquidity (${lp_usd:,.0f})")
    elif lp_usd >= 1000:
        score += 15
        signals.append(f"Active Seed Pool (${lp_usd:,.0f})")
    elif lp_usd > 0:
        score += 8
        signals.append(f"Micro Pool (${lp_usd:,.0f})")
    else:
        signals.append("No DEX LP detected yet")

    if details.get("is_paused") is True:
        signals.append("Mint Paused (Pre-launch phase)")
    elif details.get("is_paused") is False:
        score += 10
        signals.append("Mint is LIVE / Active")

    pct = stats["percent_claimed"]
    if pct >= 80:
        score += 25
        signals.append(f"Near Sellout ({pct:.1f}% claimed)")
    elif pct >= 40:
        score += 18
        signals.append(f"Strong Demand ({pct:.1f}% claimed)")
    elif pct > 0:
        score += 10
        signals.append(f"Early Mints ({pct:.1f}% claimed)")

    hld = stats.get("holders", 0)
    buys = stats.get("buys_24h", 0)
    vol = stats.get("volume_24h", 0.0)

    if buys >= 1000:
        score += 25
        signals.append(f"Heavy Buy Volume ({buys:,} purchases / 24h)")
    elif buys >= 100:
        score += 15
        signals.append(f"Active Purchases ({buys:,} buys / 24h)")
    elif buys >= 10:
        score += 8
        signals.append(f"Early Purchases ({buys} buys / 24h)")

    if hld >= 10000:
        score += 20
        signals.append(f"Massive Holder Base ({hld:,} holders)")
    elif hld >= 1000:
        score += 12
        signals.append(f"Solid Community ({hld:,} holders)")
    elif hld >= 50:
        score += 6
        signals.append(f"Growing Holders ({hld} holders)")

    if vol >= 100000:
        score += 10
        signals.append(f"High 24h Volume (${vol:,.0f})")

    if stats["deployer_balance_bnb"] >= 1.0:
        score += 5
        signals.append(f"Funded Deployer ({stats['deployer_balance_bnb']:.2f} BNB)")
    if stats["deployer_nonce"] >= 5:
        score += 5
        signals.append(f"Active Deployer Nonce ({stats['deployer_nonce']} txs)")

    stats["score"] = min(100, score)
    stats["signals"] = signals

    if stats["score"] >= 75:
        stats["rating"] = "🚀 VIRAL RUSH / SNIPER FRENZY"
    elif stats["score"] >= 50:
        stats["rating"] = "🔥 HIGH TRACTION"
    elif stats["score"] >= 25:
        stats["rating"] = "🌱 WARMING UP / SEEDED"
    else:
        stats["rating"] = "❄️ COLD / DORMANT"

    return stats


def print_interest_report(stats: Dict[str, Any]):
    print(f"\n{C_BOLD}{C_MAGENTA}───────────────────────────────────────────────────────────────{C_RESET}")
    print(f"{C_BOLD}📊 ON-CHAIN INTEREST & TRACTION ANALYSIS{C_RESET}")
    print(f"{C_BOLD}{C_MAGENTA}───────────────────────────────────────────────────────────────{C_RESET}")
    score = stats["score"]
    score_color = C_GREEN if score >= 50 else (C_YELLOW if score >= 25 else C_CYAN)
    print(f"• {C_BOLD}Interest Level:{C_RESET} {stats['rating']} ({score_color}{score}/100{C_RESET})")

    if stats.get("holders", 0) > 0:
        print(f"• {C_BOLD}On-Chain Holders:{C_RESET} {C_GREEN}{stats['holders']:,}{C_RESET} unique wallets")
    if stats.get("buys_24h", 0) > 0:
        print(f"• {C_BOLD}24h Purchases (Buys):{C_RESET} {C_CYAN}{stats['buys_24h']:,}{C_RESET} transactions ({stats.get('buys_1h', 0)} in last hour)")
    if stats.get("volume_24h", 0) > 0:
        print(f"• {C_BOLD}24h DEX Volume:{C_RESET} {C_YELLOW}${stats['volume_24h']:,.2f}{C_RESET}")

    if stats.get("lp_pool"):
        token_str = f" (${stats['token_symbol']})" if stats['token_symbol'] else ""
        print(f"• {C_BOLD}PancakeSwap V2 Liquidity:{C_RESET} {C_GREEN}${stats['lp_usd']:,.2f}{C_RESET} ({stats['lp_wbnb']:.2f} WBNB)")
        if stats["token_price_usd"] > 0:
            print(f"• {C_BOLD}Token Implied Price{token_str}:{C_RESET} ${stats['token_price_usd']:.4f}")
        print(f"• {C_BOLD}LP Pair Address:{C_RESET} {stats['lp_pool']}")
    else:
        print(f"• {C_BOLD}PancakeSwap V2 Liquidity:{C_RESET} {C_YELLOW}No DEX Pool detected yet{C_RESET}")

    if stats.get("percent_claimed") > 0:
        print(f"• {C_BOLD}Mint Progress:{C_RESET} {stats['percent_claimed']:.1f}% claimed")

    print(f"• {C_BOLD}Recent Mints (last ~2k blocks):{C_RESET} {stats['recent_mints']} mints")
    print(f"• {C_BOLD}Unique Minters Detected:{C_RESET} {stats['unique_minters']} addresses")
    print(f"• {C_BOLD}Deployer Skin-in-the-Game:{C_RESET} {stats['deployer_balance_bnb']:.4f} BNB | Nonce: {stats['deployer_nonce']} txs")

    if stats.get("signals"):
        print(f"• {C_BOLD}Key Signals:{C_RESET}")
        for sig in stats["signals"]:
            print(f"   ▫ {sig}")


def print_banner():
    print(f"{C_BOLD}{C_CYAN}╔═══════════════════════════════════════════════════════════════╗{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}║     BNB Chain .ai Agent ID Registry Real-Time Monitor         ║{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}║  Target Registry: {C_YELLOW}0x60B9262456dE011F7d15a54CdD8c48bAaa8B022F{C_CYAN}  ║{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}╚═══════════════════════════════════════════════════════════════╝{C_RESET}\n")


def notify_alert():
    sys.stdout.write("\a")
    sys.stdout.flush()
    try:
        subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"], stderr=subprocess.DEVNULL)
    except Exception:
        pass


KNOWN_PROJECTS = [
    {
        "block": 130445602,
        "contract": "0x0f2b7a73873d9f46a5530d679671ce174e1cd2e0",
        "deployer": "0xf4b44ed777db4f2bec3f0cb5abe0c2ca0ee47381",
        "tx_hash": "0x7cb867fe9973b0b584c2fbe938a6d3070965831d2c2b36729eb0a571a78e2951",
        "name": "Pixelox Agent Pass 666",
        "symbol": "PXAP666",
        "standard": "ERC-721 (NFT Collection)",
        "icon": "🎨",
        "is_paused": False,
        "has_free_mint": False,
        "mint_price_str": "0.0027 BNB",
        "mint_price_bnb": 0.0027,
        "mint_value_wei": "2700000000000000",
        "max_supply": 666,
        "total_minted": 0,
        "score": 10,
        "rating": "❄️ COLD / DORMANT",
        "lp_usd": 0.0,
        "lp_wbnb": 0.0,
        "token_symbol": "PXAP666",
        "token_price_usd": 0.0,
        "holders": 0,
        "buys_24h": 0,
        "buys_1h": 0,
        "sells_24h": 0,
        "volume_24h": 0.0,
        "pair_url": "",
        "mint_calldata": "0xa0712d680000000000000000000000000000000000000000000000000000000000000001",
        "signals": [
            "Brand New Deployment (Block 130445602)",
            "No DEX LP detected yet",
            "Direct Integration with .ai Agent ID Registry"
        ],
        "detected_at": "2026-10-29 17:02:55"
    },
    {
        "block": 130419200,
        "contract": "0x2ED9E0a6992BA22B48c2fc2def6bB2365301A0D0",
        "deployer": "0x1af1d125cb90dae19247c945eb9bfced5cf8b47f",
        "tx_hash": "0x2e0f498c1a704a25c1cbcf635c4250ee637d4fdf800361ce27a29ee620244195",
        "name": "Worldygg Tree",
        "symbol": "TREE",
        "standard": "ERC-721 (NFT Collection)",
        "icon": "🎨",
        "is_paused": False,
        "has_free_mint": True,
        "mint_price_str": "⭐ FREE ($1.50)",
        "mint_value_wei": "0",
        "max_supply": 99999,
        "total_minted": 19600,
        "score": 60,
        "rating": "🔥 HIGH TRACTION",
        "lp_usd": 0.0,
        "lp_wbnb": 0.0,
        "token_symbol": "TREE",
        "token_price_usd": 0.0,
        "holders": 19600,
        "buys_24h": 19600,
        "buys_1h": 0,
        "sells_24h": 0,
        "volume_24h": 0.0,
        "pair_url": "",
        "mint_calldata": "0xa0712d680000000000000000000000000000000000000000000000000000000000000001",
        "signals": [
            "No DEX LP detected yet",
            "Early Mints (19.6% claimed)",
            "Heavy Buy Volume (19,600 purchases / 24h)",
            "Massive Holder Base (19,600 holders)",
            "Active Deployer Nonce (15 txs)"
        ],
        "detected_at": "2026-10-29 16:32:00"
    },
    {
        "block": 130439433,
        "contract": "0xb04c4b42475660c2ae7f4915b5c00535651e9cfe",
        "deployer": "0xc6f217270f6ec5fae208d6bfce8d7a409dcbb6f1",
        "tx_hash": "0xc9d7b6936d7cd68af48aedc484cf1028f5d209642056716df03a11b624954f21",
        "name": "AgentEpoch",
        "symbol": "AEPOCH",
        "standard": "ERC-721 (NFT Collection)",
        "icon": "🎨",
        "is_paused": False,
        "has_free_mint": True,
        "mint_price_str": "⭐ FREE",
        "mint_value_wei": "0",
        "max_supply": 6000,
        "total_minted": 450,
        "score": 40,
        "rating": "🌱 WARMING UP / SEEDED",
        "lp_usd": 0.0,
        "lp_wbnb": 0.0,
        "token_symbol": "AEPOCH",
        "token_price_usd": 0.0,
        "holders": 450,
        "buys_24h": 450,
        "buys_1h": 0,
        "sells_24h": 0,
        "volume_24h": 0.0,
        "pair_url": "",
        "mint_calldata": "0xa0712d680000000000000000000000000000000000000000000000000000000000000001",
        "signals": [
            "No DEX LP detected yet",
            "Early Mints (7.5% claimed)",
            "Active Purchases (450 buys / 24h)",
            "Growing Holders (450 holders)"
        ],
        "detected_at": "2026-10-29 16:18:19"
    }
]


def load_history() -> List[Dict[str, Any]]:
    items = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                items = json.load(f)
        except Exception:
            items = []
    
    # Ensure known seed projects are always tracked and have proper mint price
    existing_addrs = {x.get("contract", "").lower(): x for x in items}
    for kp in KNOWN_PROJECTS:
        k_addr = kp["contract"].lower()
        if k_addr not in existing_addrs:
            items.insert(0, dict(kp))
            existing_addrs[k_addr] = items[0]
        else:
            # Backfill verified mint price / calldata if missing
            ex = existing_addrs[k_addr]
            if not ex.get("mint_price_str") or ex.get("mint_price_str") == "TBD":
                ex["mint_price_str"] = kp["mint_price_str"]
                ex["has_free_mint"] = kp["has_free_mint"]
                ex["mint_value_wei"] = kp["mint_value_wei"]
                if kp.get("mint_calldata"):
                    ex["mint_calldata"] = kp["mint_calldata"]
    items.sort(key=lambda x: x.get("block", 0) or 0, reverse=True)
    return items


def save_history(history: List[Dict[str, Any]]):
    try:
        existing = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    existing = json.load(f)
            except Exception:
                existing = []
        
        # Merge by contract address
        merged_map = {}
        for item in existing:
            c = item.get("contract", "").lower()
            if c:
                merged_map[c] = item
        for item in history:
            c = item.get("contract", "").lower()
            if c:
                if c in merged_map:
                    # Guard: Never overwrite a previously resolved price with 'TBD' or empty
                    if merged_map[c].get("mint_price_str") not in (None, "", "TBD") and item.get("mint_price_str") in (None, "", "TBD"):
                        item["mint_price_str"] = merged_map[c]["mint_price_str"]
                        item["mint_price_bnb"] = merged_map[c].get("mint_price_bnb", 0.0)
                        item["mint_value_wei"] = merged_map[c].get("mint_value_wei", "0")
                        if merged_map[c].get("has_free_mint") is not None:
                            item["has_free_mint"] = merged_map[c]["has_free_mint"]
                    merged_map[c].update(item)
                else:
                    merged_map[c] = item

        seen = set()
        final_list = []
        for item in history:
            c = item.get("contract", "").lower()
            if c and c not in seen:
                seen.add(c)
                final_list.append(merged_map[c])
        for item in existing:
            c = item.get("contract", "").lower()
            if c and c not in seen:
                seen.add(c)
                final_list.append(merged_map[c])

        with open(HISTORY_FILE, "w") as f:
            json.dump(final_list, f, indent=2)
    except Exception:
        pass


class KeyReader:
    def __init__(self):
        self.is_tty = sys.stdin.isatty()
        self.old_settings = None
        if self.is_tty:
            try:
                self.old_settings = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
            except Exception:
                self.is_tty = False

    def get_key(self, timeout=0.1) -> Optional[str]:
        if not self.is_tty:
            time.sleep(timeout)
            return None
        try:
            rlist, _, _ = select.select([sys.stdin], [], [], timeout)
            if rlist:
                raw = os.read(sys.stdin.fileno(), 32)
                if not raw:
                    return None
                # ANSI arrow escape sequences (both normal and cursor application mode)
                if raw in (b'\x1b[A', b'\x1bOA'):
                    return 'UP'
                elif raw in (b'\x1b[B', b'\x1bOB'):
                    return 'DOWN'
                elif raw in (b'\x1b[C', b'\x1bOC'):
                    return 'RIGHT'
                elif raw in (b'\x1b[D', b'\x1bOD'):
                    return 'LEFT'
                elif raw == b'\x1b':
                    return 'ESC'
                elif raw in (b'\r', b'\n'):
                    return 'ENTER'
                elif raw == b' ':
                    return 'SPACE'
                
                # Single character keys
                ch = raw.decode("utf-8", errors="ignore").strip()
                if not ch:
                    return None
                ch_lower = ch.lower()
                if ch_lower in ('k', 'w'):
                    return 'UP'
                elif ch_lower in ('j', 's'):
                    return 'DOWN'
                elif ch in ('1', '2', '3', '4', '5', '6', '7', '8', '9'):
                    return ch
                return ch_lower
        except Exception:
            return None
        return None

    def close(self):
        if self.is_tty and self.old_settings:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)
            except Exception:
                pass

    def __del__(self):
        self.close()


def format_supply(val: Any) -> str:
    if val is None:
        return "N/A"
    try:
        n = float(val)
        # If it looks like raw 18-decimal token supply (> 1e15)
        if n >= 1e24:
            n = n / 1e18
        elif n >= 1e18:
            n = n / 1e18
        if n >= 1e12:
            return f"{n/1e12:.1f}T"
        elif n >= 1e9:
            return f"{n/1e9:.1f}B"
        elif n >= 1e6:
            return f"{n/1e6:.1f}M"
        elif n >= 1e3:
            return f"{n/1e3:.1f}K"
        return f"{int(n):,}"
    except Exception:
        return str(val)


def format_count(val: Any) -> str:
    if not val:
        return "0"
    try:
        n = float(val)
        if n >= 1e6:
            return f"{n/1e6:.1f}M"
        elif n >= 1e3:
            return f"{n/1e3:.1f}K"
        return str(int(n))
    except Exception:
        return "0"


def refresh_project_metrics(rpc: BscRpcClient, item: Dict[str, Any], wallet: str, fast: bool = False):
    addr = item.get("contract")
    if not addr:
        return
    try:
        details = analyze_contract(rpc, addr, wallet)
        
        # Update metadata if newly available
        if details.get("name") and (not item.get("name") or item.get("name") == "Unknown"):
            item["name"] = details["name"]
        if details.get("symbol") and not item.get("symbol"):
            item["symbol"] = details["symbol"]
        if details.get("max_supply") is not None:
            item["max_supply"] = details["max_supply"]
        if details.get("mint_calldata"):
            item["mint_calldata"] = details["mint_calldata"]

        # Live on-chain status & price
        item["is_paused"] = details.get("is_paused")
        item["has_free_mint"] = details.get("has_free_mint")
        item["mint_price_str"] = details.get("mint_price_str", "TBD")
        item["mint_price_bnb"] = details.get("mint_price_bnb", 0.0)
        item["mint_value_wei"] = details.get("mint_value_wei", "0")
        item["total_minted"] = details.get("total_minted", item.get("total_minted", 0))
        if details.get("website"):
            item["website"] = details["website"]
        if details.get("twitter"):
            item["twitter"] = details["twitter"]
        if details.get("market_url"):
            item["market_url"] = details["market_url"]

        if not fast:
            mkt = analyze_market_interest(rpc, addr, item.get("deployer"), details)
            # Live DEX, Holders & Purchase metrics
            item["holders"] = mkt.get("holders", item.get("holders", 0))
            item["buys_24h"] = mkt.get("buys_24h", item.get("buys_24h", 0))
            item["buys_1h"] = mkt.get("buys_1h", item.get("buys_1h", 0))
            item["sells_24h"] = mkt.get("sells_24h", item.get("sells_24h", 0))
            item["volume_24h"] = mkt.get("volume_24h", item.get("volume_24h", 0.0))
            if mkt.get("pair_url"):
                item["pair_url"] = mkt["pair_url"]
            if mkt.get("website"):
                item["website"] = mkt["website"]
            if mkt.get("twitter"):
                item["twitter"] = mkt["twitter"]
            if mkt.get("market_url"):
                item["market_url"] = mkt["market_url"]
            if mkt.get("token_price_bnb"):
                item["token_price_bnb"] = mkt["token_price_bnb"]

            item["score"] = mkt.get("score", item.get("score", 0))
            item["rating"] = mkt.get("rating", item.get("rating", "N/A"))
            item["lp_usd"] = mkt.get("lp_usd", item.get("lp_usd", 0.0))
            item["lp_wbnb"] = mkt.get("lp_wbnb", item.get("lp_wbnb", 0.0))
            item["token_price_usd"] = mkt.get("token_price_usd", item.get("token_price_usd", 0.0))
            if mkt.get("token_symbol"):
                item["token_symbol"] = mkt["token_symbol"]
            if mkt.get("signals"):
                item["signals"] = mkt["signals"]
    except Exception:
        pass


def refresh_all_metrics(rpc: BscRpcClient, history: List[Dict[str, Any]], wallet: str, fast: bool = False):
    for item in history:
        refresh_project_metrics(rpc, item, wallet, fast=fast)
    save_history(history)


def build_dashboard(history: List[Dict[str, Any]], curr_block: int, tx_count: int, wallet: str, status_msg: str = ""):
    return build_interactive_dashboard(history, 0, curr_block, tx_count, wallet, status_msg, show_modal=False)


def build_interactive_dashboard(
    history: List[Dict[str, Any]],
    selected_idx: int,
    curr_block: int,
    tx_count: int,
    wallet: str,
    status_msg: str = "",
    show_modal: bool = False
):
    # Full Modal Inspection View
    if show_modal and history and 0 <= selected_idx < len(history):
        item = history[selected_idx]
        name = item.get("name", "Unknown")
        sym = item.get("symbol", "")
        c_addr = item.get("contract", "")
        dep = item.get("deployer", "")
        block = item.get("block", "")
        tx_h = item.get("tx_hash", "")
        icon = item.get("icon", "")
        std = item.get("standard", "")
        tm = item.get("total_minted", 0)
        ms = item.get("max_supply", "N/A")
        paused = item.get("is_paused")
        free = item.get("has_free_mint")
        score = item.get("score", 0)
        rating = item.get("rating", "N/A")
        lp_usd = item.get("lp_usd", 0)
        lp_wbnb = item.get("lp_wbnb", 0)
        t_price = item.get("token_price_usd", 0)
        t_sym = item.get("token_symbol", "")
        val_wei = item.get("mint_value_wei", "0")
        calldata = item.get("mint_calldata", "0x1249c58b")

        m_text = Text()
        m_text.append(f"PROJECT: {name} ({sym})\n\n", style="bold cyan")
        m_text.append(f"• Contract Address: {c_addr}\n", style="bold white")
        m_text.append(f"• Deployer Wallet:  {dep}\n", style="dim")
        m_text.append(f"• Deployment Block: #{block}  |  Tx: {tx_h}\n", style="dim")
        m_text.append(f"• Asset Standard:   {icon} {std}\n", style="magenta")
        
        m_text.append("• Mint Status:      ")
        if paused:
            m_text.append("🔴 PAUSED (Pre-launch phase)", style="bold red")
        else:
            m_text.append("🟢 ACTIVE / LIVE", style="bold green")
        m_text.append(f"  |  Supply: {format_supply(tm)} / {format_supply(ms)}\n")
        
        val_wei = item.get("mint_value_wei", "0")
        price_str = item.get("mint_price_str") or ("⭐ FREE" if free else "Paid / TBD")
        m_text.append(f"• Mint Price:       {price_str} (Required Value: {val_wei} wei)\n", style="bold green" if "FREE" in price_str else "bold yellow")
        m_text.append(f"• Free Mint (Agent): {'⭐ YES (Available for nimus.ai)' if free else 'No'}\n")
        
        website = item.get("website", "")
        twitter = item.get("twitter", "")
        mkt_url = item.get("market_url") or item.get("pair_url", "")
        if not mkt_url and "NFT" in std:
            mkt_url = f"https://element.market/assets/bsc/{c_addr}"
        elif not mkt_url and ("BEP-20" in std or "Token" in std):
            mkt_url = f"https://pancakeswap.finance/swap?outputCurrency={c_addr}"

        t_price = item.get("token_price_usd", 0.0)
        t_price_bnb = item.get("token_price_bnb", 0.0)

        if website or twitter or mkt_url:
            m_text.append("\n• Official Links & Social Media:\n", style="bold white")
            if website:
                m_text.append(f"   ▫ 🌐 Website:     {website} (Press 'w' to open)\n", style="cyan")
            if twitter:
                m_text.append(f"   ▫ 🐦 X (Twitter): {twitter} (Press 'x' to open)\n", style="cyan")
            if mkt_url:
                m_label = "DexScreener" if "dexscreener" in mkt_url else ("Element NFT" if "element" in mkt_url else "Marketplace")
                m_text.append(f"   ▫ 🛒 {m_label}:   {mkt_url} (Press 'm' to open)\n", style="cyan")

        if t_price > 0:
            m_text.append(f"\n• Secondary Market Price: ${t_price:.6f} USD", style="bold green")
            if t_price_bnb > 0:
                m_text.append(f" ({t_price_bnb:.8f} BNB)", style="bold green")
            m_text.append("\n")

        m_text.append(f"• Interest Score:   {score}/100 ({rating})\n", style="bold yellow")
        if lp_usd > 0:
            m_text.append(f"• PancakeSwap LP:   ${lp_usd:,.2f} ({lp_wbnb:.2f} WBNB)\n", style="bold green")
            if t_price > 0:
                m_text.append(f"• Implied Price:    ${t_price:.4f} (${t_sym})\n", style="green")
        else:
            m_text.append("• PancakeSwap LP:   No DEX Pool detected yet\n", style="dim")

        if item.get("signals"):
            m_text.append("\n• Key Reverse-Engineering Signals:\n", style="bold white")
            for sig in item["signals"]:
                m_text.append(f"   ▫ {sig}\n", style="dim white")

        hld = item.get("holders", 0)
        buys = item.get("buys_24h", 0)
        buys_1h = item.get("buys_1h", 0)
        sells = item.get("sells_24h", 0)
        vol = item.get("volume_24h", 0.0)
        
        m_text.append("\n• On-Chain Holders & Purchases (24h Activity):\n", style="bold white")
        if hld > 0 or buys > 0:
            m_text.append(f"   ▫ On-Chain Holders:     {hld:,} unique wallets\n", style="bold green")
            m_text.append(f"   ▫ 24h Purchases (Buys):  {buys:,} buy transactions\n", style="cyan")
            m_text.append(f"   ▫ 1h Purchases (Buys):   {buys_1h:,} buy transactions (recent momentum)\n", style="cyan")
            if sells > 0:
                net = buys - sells
                net_str = f"+{net:,}" if net >= 0 else f"{net:,}"
                net_style = "bold green" if net >= 0 else "bold red"
                m_text.append(f"   ▫ 24h Sells:            {sells:,} sell transactions (Net Flow: [{net_style}]{net_str}[/{net_style}])\n")
            if vol > 0:
                m_text.append(f"   ▫ 24h DEX Volume:       ${vol:,.2f}\n", style="bold yellow")
        else:
            m_text.append("   ▫ No holders or DEX purchases recorded yet (Pre-launch phase)\n", style="dim")

        m_text.append("\n[Quick Snipe Command]\n", style="bold yellow")
        m_text.append(f"baw contract-call preview --binanceChainId 56 --from {wallet} --to {c_addr} --value {val_wei} --inputData {calldata} --json\n", style="cyan")

        modal_panel = Panel(
            m_text,
            title=f"🔍 Full Project Inspection: {name} ({sym})",
            border_style="magenta",
            expand=True
        )
        footer = Text("⌨️  [Enter/ESC] Close Modal  |  [w] Website  |  [x] Twitter/X  |  [m] Market  |  [b] BscScan", style="bold white on blue")
        return Group(modal_panel, footer)

    # Standard Interactive Table View
    h_text = Text()
    h_text.append("🚀 BNB Chain .ai Agent ID Radar", style="bold cyan")
    h_text.append(f"  |  Registry: 0x60B9...022F  |  Wallet: {wallet[:6]}...{wallet[-4:]} (nimus.ai)", style="dim")
    h_panel = Panel(h_text, border_style="cyan", padding=(0, 1))

    table = Table(box=box.ROUNDED, expand=True, header_style="bold cyan")
    table.add_column(" ", width=2, justify="center", no_wrap=True)
    table.add_column("Block", justify="right", style="cyan", width=8, no_wrap=True)
    table.add_column("Project", style="bold white", min_width=14, no_wrap=True)
    table.add_column("Type", style="magenta", width=10, no_wrap=True)
    table.add_column("Interest", style="yellow", width=12, no_wrap=True)
    table.add_column("Holders / Buys", style="cyan", min_width=14, justify="center", no_wrap=True)
    table.add_column("DEX LP", style="green", width=9, no_wrap=True)
    table.add_column("Mint Progress", style="white", min_width=16, justify="center", no_wrap=True)
    table.add_column("Mint Price", min_width=15, justify="center", no_wrap=True)

    for i, item in enumerate(history):
        is_sel = (i == selected_idx)
        sel_marker = "[bold yellow]▶[/bold yellow]" if is_sel else " "
        row_style = "bold on grey23" if is_sel else ""

        # Compact Mint Status & Progress
        if item.get("is_paused"):
            if item.get("max_supply"):
                ms_str = format_supply(item["max_supply"])
                tm = item.get("total_minted")
                if tm and tm > 0:
                    tm_str = format_supply(tm)
                    p_status = f"[red]🔴 Paused[/red] [dim]({tm_str}/{ms_str})[/dim]"
                else:
                    p_status = f"[red]🔴 Paused[/red] [dim]({ms_str})[/dim]"
            else:
                p_status = "[red]🔴 Paused[/red]"
        else:
            if item.get("max_supply"):
                tm = item.get("total_minted")
                tm_str = format_supply(tm) if tm is not None else "0"
                ms_str = format_supply(item["max_supply"])
                p_status = f"[green]🟢[/green] [bold white]{tm_str}[/bold white][dim]/{ms_str}[/dim]"
            else:
                p_status = "[green]🟢 Live[/green]"

        score = item.get("score", 0)
        score_style = "bold green" if score >= 50 else ("bold yellow" if score >= 25 else "cyan")
        r_raw = item.get("rating", "N/A")
        if "VIRAL" in r_raw:
            r_str = "🚀 Viral"
        elif "HIGH" in r_raw:
            r_str = "🔥 High"
        elif "WARM" in r_raw:
            r_str = "🌱 Warm"
        else:
            r_str = "❄️ Cold"
        interest_str = f"[{score_style}]{r_str} ({score})[/{score_style}]"

        hld = item.get("holders", 0)
        buys = item.get("buys_24h", 0)
        if hld == 0 and item.get("total_minted"):
            hld = item["total_minted"]
        if buys == 0 and item.get("total_minted") and ("NFT" in item.get("standard", "")):
            buys = item["total_minted"]

        if hld > 0 or buys > 0:
            h_str = format_count(hld)
            b_str = format_count(buys)
            hb_str = f"[bold green]{h_str}[/bold green] [dim]/[/dim] [cyan]{b_str}[/cyan]"
        else:
            hb_str = "[dim]0 / 0[/dim]"

        lp_usd = item.get("lp_usd", 0)
        lp_wbnb = item.get("lp_wbnb", 0)
        lp_str = f"${lp_usd:,.0f}" if lp_usd > 0 else "[dim]None[/dim]"
        
        price_val = item.get("mint_price_str") or ("⭐ FREE" if item.get("has_free_mint") else "TBD")
        if "FREE" in price_val:
            price_formatted = f"[bold green]{price_val}[/bold green]"
        elif "DEX" in price_val:
            price_formatted = f"[cyan]{price_val}[/cyan]"
        elif price_val != "TBD":
            price_formatted = f"[bold yellow]{price_val}[/bold yellow]"
        else:
            price_formatted = "[dim]TBD[/dim]"

        p_name = item.get("name", "Unknown")[:14]
        p_sym = item.get("symbol", "")[:8]
        p_icon = item.get("icon", "")
        std_full = item.get("standard", "")
        if "NFT" in std_full or "ERC-721" in std_full:
            p_std = "ERC-721"
        elif "BEP-20" in std_full:
            p_std = "BEP-20"
        elif "Fair-Mint" in std_full:
            p_std = "Fair-Mint"
        else:
            p_std = "Custom"

        blk_str = f"#{str(item.get('block', ''))[-6:]}" if item.get('block') else ""

        table.add_row(
            sel_marker,
            blk_str,
            f"{p_name} ({p_sym})",
            f"{p_icon} {p_std}",
            interest_str,
            hb_str,
            lp_str,
            p_status,
            price_formatted,
            style=row_style
        )

    # Deep dive for selected project
    sel_item = history[selected_idx] if (history and 0 <= selected_idx < len(history)) else (history[0] if history else None)
    if sel_item:
        d_text = Text()
        name = sel_item.get("name", "Unknown")
        sym = sel_item.get("symbol", "")
        c_addr = sel_item.get("contract", "")
        dep = sel_item.get("deployer", "")
        s_icon = sel_item.get("icon", "")
        s_std = sel_item.get("standard", "")
        s_tm = sel_item.get("total_minted", 0)
        s_ms = sel_item.get("max_supply", "N/A")
        lp_usd = sel_item.get("lp_usd", 0)
        lp_wbnb = sel_item.get("lp_wbnb", 0)
        t_price = sel_item.get("token_price_usd", 0)
        calldata = sel_item.get("mint_calldata", "0x1249c58b")
        val_wei = sel_item.get("mint_value_wei", "0")
        hld = sel_item.get("holders", 0)
        buys = sel_item.get("buys_24h", 0)
        buys_1h = sel_item.get("buys_1h", 0)
        vol = sel_item.get("volume_24h", 0.0)

        d_text.append(f"• Contract: {c_addr}  |  Deployer: {dep}\n", style="bold cyan")
        d_text.append(f"• Asset Standard: {s_icon} {s_std}  |  Supply: {format_supply(s_tm)} / {format_supply(s_ms)}\n")
        
        price_val = sel_item.get("mint_price_str") or ("⭐ FREE" if sel_item.get("has_free_mint") else "Paid / TBD")
        d_text.append(f"• Mint Price: {price_val}  |  Required Value: {val_wei} wei\n", style="bold green" if "FREE" in price_val else "bold yellow")

        if hld > 0 or buys > 0:
            d_text.append(f"• Traction: {hld:,} holders  |  {buys:,} buys/24h ({buys_1h} in 1h)  |  Vol: ${vol:,.0f}\n", style="bold cyan")
        else:
            d_text.append("• Traction: 0 holders  |  0 buys (Pre-launch / No DEX trades yet)\n", style="dim")

        website = sel_item.get("website", "")
        twitter = sel_item.get("twitter", "")
        mkt_url = sel_item.get("market_url") or sel_item.get("pair_url", "")
        if not mkt_url and "NFT" in s_std:
            mkt_url = f"https://element.market/assets/bsc/{c_addr}"
        elif not mkt_url and ("BEP-20" in s_std or "Token" in s_std):
            mkt_url = f"https://pancakeswap.finance/swap?outputCurrency={c_addr}"

        t_price_bnb = sel_item.get("token_price_bnb", 0.0)

        if t_price > 0:
            if t_price_bnb > 0:
                d_text.append(f"• Market Price: ${t_price:.6f} USD ({t_price_bnb:.8f} BNB)  |  DEX Pool: ${lp_usd:,.0f}\n", style="bold green")
            else:
                d_text.append(f"• Market Price: ${t_price:.4f} USD  |  DEX Pool: ${lp_usd:,.0f}\n", style="bold green")
        elif lp_usd > 0:
            d_text.append(f"• DEX LP: ${lp_usd:,.2f} ({lp_wbnb:.2f} WBNB)\n", style="green")
        else:
            d_text.append("• Secondary Market: No DEX pool active yet\n", style="dim")

        links_parts = []
        if website:
            links_parts.append(f"🌐 [w] {website}")
        if twitter:
            links_parts.append(f"🐦 [x] {twitter}")
        if mkt_url:
            m_label = "DexScreener" if "dexscreener" in mkt_url else ("Element NFT" if "element" in mkt_url else "Market")
            links_parts.append(f"🛒 [m] {m_label}")
        
        if links_parts:
            d_text.append("• Links: " + "  |  ".join(links_parts) + "\n", style="cyan")
        
        cmd = f"baw contract-call preview --binanceChainId 56 --from {wallet} --to {c_addr} --value {val_wei} --inputData {calldata} --json"
        d_text.append(f"• Quick Snipe Command:\n  {cmd}", style="yellow")
        
        deep_panel = Panel(d_text, title=f"🔍 Deep Dive: {name} ({sym}) [Press Enter for Full Modal]", border_style="yellow", padding=(0, 1))
    else:
        deep_panel = Panel("Waiting for .ai Agent ID contract deployments on BSC...", border_style="dim", padding=(0, 1))

    msg = f" | {status_msg}" if status_msg else ""
    nav_bar = Text("⌨️  [↑/↓ or 1-9] Navigate  |  [Enter] Modal  |  [w] Website  |  [x] Twitter/X  |  [m] Market  |  [b] BscScan  |  [r] Refresh  |  [q] Quit", style="bold white on blue")
    status_bar = Text(f"📡 BSC Block {curr_block} | {tx_count} txs | Tracked: {len(history)}{msg}", style="dim green")

    return Group(h_panel, table, deep_panel, nav_bar, status_bar)


def run_live_monitor(rpc: BscRpcClient, wallet: str, plain: bool = False):
    history = load_history()

    if not RICH_AVAILABLE or plain or not sys.stdin.isatty():
        print_banner()
        print(f"{C_BOLD}{C_GREEN}[*] Live Monitor Started.{C_RESET} Listening to new BSC blocks...")
        print(f"[*] Watching for newly deployed contracts referencing the .ai Agent ID registry.")
        print(f"[*] Classification Engine: Distinguishes {C_YELLOW}Tokens (BEP-20){C_RESET} vs {C_MAGENTA}NFTs (ERC-721){C_RESET}.")
        print(f"[*] Your Agent Wallet: {C_YELLOW}{wallet}{C_RESET}\n")

        last_block = rpc.get_block_number() - 1
        processed_txs = set()

        while True:
            try:
                curr_block = rpc.get_block_number()
                if curr_block <= last_block:
                    time.sleep(1.0)
                    continue

                # Auto-sync catchup if behind by >25 blocks
                if curr_block - last_block > 25:
                    last_block = curr_block - 5

                for block_num in range(last_block + 1, curr_block + 1):
                    block = rpc.get_block(block_num, full_tx=True)
                    txs = block.get("transactions", [])

                    print(f"\r{C_BLUE}--> Block {block_num} | {len(txs)} txs{C_RESET}", end="", flush=True)

                    for tx in txs:
                        tx_hash = tx.get("hash")
                        if not tx_hash or tx_hash in processed_txs:
                            continue
                        processed_txs.add(tx_hash)

                        input_data = (tx.get("input") or "").lower()
                        # Fast in-memory check: ignores 99.999% of non-registry transactions instantly
                        if AGENT_ID_BYTES not in input_data:
                            continue

                        rcpt = rpc.get_receipt(tx_hash)
                        if not rcpt or int(rcpt.get("status", "0"), 16) != 1:
                            continue

                        deployed_addr = rcpt.get("contractAddress")
                        if not deployed_addr:
                            for log in rcpt.get("logs", []):
                                log_addr = log.get("address")
                                if log_addr:
                                    code = rpc.get_code(log_addr)
                                    if AGENT_ID_BYTES in (code or "").lower():
                                        deployed_addr = log_addr
                                        break

                        if deployed_addr:
                                details = analyze_contract(rpc, deployed_addr, wallet)
                                cls = details["classification"]
                                mkt = analyze_market_interest(rpc, deployed_addr, tx.get('from'), details)

                                print(f"\n\n{C_BOLD}{C_GREEN}═══════════════════════════════════════════════════════════════{C_RESET}")
                                print(f"{C_BOLD}{C_YELLOW}🚀 NEW .AI AGENT ID PROJECT DETECTED!{C_RESET}")
                                print(f"{C_BOLD}{C_GREEN}═══════════════════════════════════════════════════════════════{C_RESET}")
                                print(f"• {C_BOLD}Block:{C_RESET} {block_num}")
                                print(f"• {C_BOLD}Contract Address:{C_RESET} {C_CYAN}{deployed_addr}{C_RESET}")
                                print(f"• {C_BOLD}Deployer:{C_RESET} {tx.get('from')}")
                                print(f"• {C_BOLD}Tx Hash:{C_RESET} {tx_hash}")
                                print(f"• {C_BOLD}Asset Type:{C_RESET} {cls['icon']} {C_BOLD}{cls['standard']}{C_RESET}")
                                if details["name"] or details["symbol"]:
                                    print(f"• {C_BOLD}Name / Symbol:{C_RESET} {details['name']} ({details['symbol']})")
                                if details.get("is_paused") is not None:
                                    p_str = f"{C_RED}Paused{C_RESET}" if details["is_paused"] else f"{C_GREEN}Active{C_RESET}"
                                    print(f"• {C_BOLD}Mint Status:{C_RESET} {p_str}")
                                print(f"• {C_BOLD}Market Interest:{C_RESET} {mkt['rating']} ({mkt['score']}/100)")
                                if mkt.get("lp_pool"):
                                    print(f"• {C_BOLD}DEX Liquidity:{C_RESET} {C_GREEN}${mkt['lp_usd']:,.2f}{C_RESET} ({mkt['lp_wbnb']:.2f} WBNB)")
                                if details["has_free_mint"]:
                                    print(f"• {C_BOLD}{C_GREEN}★ FREE MINT AVAILABLE FOR YOUR WALLET! ★{C_RESET}")
                                print(f"• {C_BOLD}BscScan:{C_RESET} https://bscscan.com/address/{deployed_addr}")
                                print(f"\n{C_BOLD}Quick CLI Snipe Command:{C_RESET}")
                                print(f"{C_CYAN}baw contract-call preview --binanceChainId 56 --from {wallet} --to {deployed_addr} --value 0 --inputData {details['mint_calldata']} --json{C_RESET}\n")
                                notify_alert()

                last_block = curr_block
                time.sleep(2)

            except KeyboardInterrupt:
                print(f"\n{C_YELLOW}[!] Monitor stopped by user.{C_RESET}")
                break
            except Exception:
                time.sleep(2)
        return

    # Interactive Rich Live Dashboard Mode
    key_reader = KeyReader()
    selected_idx = 0
    show_modal = False
    status_msg = ""
    status_timer = 0
    stop_event = threading.Event()

    latest_block = [rpc.get_block_number() - 1]
    latest_tx_count = [0]

    def background_listener():
        nonlocal history, status_msg, status_timer
        processed_txs = set()
        refresh_counter = 0

        while not stop_event.is_set():
            try:
                curr_b = rpc.get_block_number()
                if curr_b <= latest_block[0]:
                    time.sleep(1.0)
                    continue

                # Auto-sync catchup if behind by >25 blocks
                if curr_b - latest_block[0] > 25:
                    latest_block[0] = curr_b - 5

                for b in range(latest_block[0] + 1, curr_b + 1):
                    block = rpc.get_block(b, full_tx=True)
                    txs = block.get("transactions", [])
                    latest_tx_count[0] = len(txs)
                    latest_block[0] = b
                    refresh_counter += 1

                    for tx in txs:
                        tx_h = tx.get("hash")
                        if not tx_h or tx_h in processed_txs:
                            continue
                        processed_txs.add(tx_h)

                        inp = (tx.get("input") or "").lower()
                        # Fast in-memory check: ignores 99.999% of non-registry transactions instantly
                        if AGENT_ID_BYTES not in inp:
                            continue

                        rcpt = rpc.get_receipt(tx_h)
                        if not rcpt or int(rcpt.get("status", "0"), 16) != 1:
                            continue
                        dep_addr = rcpt.get("contractAddress")
                        if not dep_addr:
                            for log in rcpt.get("logs", []):
                                log_addr = log.get("address")
                                if log_addr:
                                    code = rpc.get_code(log_addr)
                                    if AGENT_ID_BYTES in (code or "").lower():
                                        dep_addr = log_addr
                                        break

                        if dep_addr:
                                try:
                                    det = analyze_contract(rpc, dep_addr, wallet)
                                except Exception:
                                    det = {
                                        "name": "New Contract",
                                        "symbol": "",
                                        "classification": {"standard": "Custom Smart Contract", "icon": "⚙️"},
                                        "has_free_mint": False,
                                        "mint_calldata": "0x1249c58b"
                                    }
                                try:
                                    mkt = analyze_market_interest(rpc, dep_addr, tx.get("from"), det)
                                except Exception:
                                    mkt = {"score": 0, "rating": "❄️ COLD", "lp_usd": 0, "lp_wbnb": 0}

                                cls = det.get("classification", {})
                                new_entry = {
                                    "block": b,
                                    "contract": dep_addr,
                                    "deployer": tx.get("from"),
                                    "tx_hash": tx_h,
                                    "name": det.get("name") or "Unknown",
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
                                    "pair_url": mkt.get("pair_url", ""),
                                    "mint_calldata": det.get("mint_calldata", "0x1249c58b"),
                                    "signals": mkt.get("signals", []),
                                    "detected_at": time.strftime("%Y-%m-%d %H:%M:%S")
                                }
                                history = [h for h in history if h.get("contract", "").lower() != dep_addr.lower()]
                                history.insert(0, new_entry)
                                save_history(history)
                                p_title = det.get("name") or "New Project"
                                status_msg = f"🔔 NEW DROP DETECTED: {p_title} added to table!"
                                status_timer = time.time()
                                notify_alert()

                    if refresh_counter >= 20:
                        refresh_counter = 0
                        for item in history[:5]:
                            try:
                                c = item.get("contract")
                                if c:
                                    p_res = rpc.call_contract(c, keccak_selector("paused()"))
                                    if p_res and len(p_res) >= 66:
                                        item["is_paused"] = int(p_res, 16) == 1
                                    m_res = rpc.call_contract(c, keccak_selector("totalMinted()"))
                                    if m_res and len(m_res) >= 66:
                                        item["total_minted"] = int(m_res, 16)
                            except Exception:
                                pass

                        # Auto-resolve prices for any TBD items on-chain
                        for item in history:
                            if item.get("mint_price_str") in (None, "", "TBD"):
                                try:
                                    c = item.get("contract")
                                    if c:
                                        det = analyze_contract(rpc, c, wallet)
                                        p_str = det.get("mint_price_str")
                                        if p_str and p_str != "TBD":
                                            item["mint_price_str"] = p_str
                                            item["mint_price_bnb"] = det.get("mint_price_bnb", 0.0)
                                            item["mint_value_wei"] = det.get("mint_value_wei", "0")
                                            if det.get("has_free_mint") is not None:
                                                item["has_free_mint"] = det["has_free_mint"]
                                            if det.get("mint_calldata"):
                                                item["mint_calldata"] = det["mint_calldata"]
                                            needs_update = True
                                except Exception:
                                    pass
                        save_history(history)

                time.sleep(2)
            except Exception:
                time.sleep(2)

    listener_thread = threading.Thread(target=background_listener, daemon=True)
    listener_thread.start()

    # Immediate background resolver thread for existing projects with TBD prices
    def auto_resolve_tbd_prices():
        nonlocal history, needs_update
        changed = False
        for item in list(history):
            if stop_event.is_set():
                break
            if item.get("mint_price_str") in (None, "", "TBD"):
                try:
                    c = item.get("contract")
                    if c:
                        det = analyze_contract(rpc, c, wallet)
                        p_str = det.get("mint_price_str")
                        if p_str and p_str != "TBD":
                            item["mint_price_str"] = p_str
                            item["mint_price_bnb"] = det.get("mint_price_bnb", 0.0)
                            item["mint_value_wei"] = det.get("mint_value_wei", "0")
                            if det.get("has_free_mint") is not None:
                                item["has_free_mint"] = det["has_free_mint"]
                            if det.get("mint_calldata"):
                                item["mint_calldata"] = det["mint_calldata"]
                            changed = True
                            needs_update = True
                except Exception:
                    pass
        if changed:
            save_history(history)

    resolver_thread = threading.Thread(target=auto_resolve_tbd_prices, daemon=True)
    resolver_thread.start()

    last_rendered_block = -1
    last_render_time = time.time()
    needs_update = True

    try:
        with Live(
            build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal),
            refresh_per_second=6,
            screen=True
        ) as live:
            while True:
                key = key_reader.get_key(timeout=0.08)
                if key in ('q', 'Q'):
                    break
                elif key == 'ESC':
                    if show_modal:
                        show_modal = False
                        needs_update = True
                elif key == 'UP':
                    if not show_modal:
                        selected_idx = max(0, selected_idx - 1)
                        needs_update = True
                elif key == 'DOWN':
                    if not show_modal:
                        selected_idx = min(max(0, len(history) - 1), selected_idx + 1)
                        needs_update = True
                elif key in ('1', '2', '3', '4', '5', '6', '7', '8', '9'):
                    target_idx = int(key) - 1
                    if 0 <= target_idx < len(history):
                        selected_idx = target_idx
                        needs_update = True
                elif key in ('ENTER', 'SPACE', ' '):
                    show_modal = not show_modal
                    needs_update = True
                elif key in ('r', 'R'):
                    sel_name = history[selected_idx].get("name", "selected project") if history else "projects"
                    status_msg = f"🔄 Refreshing on-chain metrics for {sel_name}..."
                    live.update(build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal))
                    
                    # Refresh selected project first for instant feedback (<1s)
                    if history and 0 <= selected_idx < len(history):
                        refresh_project_metrics(rpc, history[selected_idx], wallet)
                        save_history(history)
                        status_msg = f"✅ Updated {sel_name}! Updating rest..."
                        live.update(build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal))
                    
                    # Refresh remaining projects
                    for i, it in enumerate(history):
                        if i != selected_idx:
                            refresh_project_metrics(rpc, it, wallet)
                    save_history(history)
                    status_msg = "✅ All metrics updated on-chain!"
                    status_timer = time.time()
                    needs_update = True
                elif key in ('b', 'B'):
                    if history and 0 <= selected_idx < len(history):
                        c_addr = history[selected_idx].get("contract")
                        url = f"https://bscscan.com/address/{c_addr}"
                        subprocess.Popen(["open", url])
                        status_msg = f"🌐 Opened BscScan in browser!"
                        status_timer = time.time()
                        needs_update = True
                elif key in ('w', 'W'):
                    if history and 0 <= selected_idx < len(history):
                        w_url = history[selected_idx].get("website")
                        if w_url:
                            subprocess.Popen(["open", w_url])
                            status_msg = f"🌐 Opened Website in browser: {w_url}"
                        else:
                            status_msg = "⚠️ No official website link found for this project."
                        status_timer = time.time()
                        needs_update = True
                elif key in ('x', 'X'):
                    if history and 0 <= selected_idx < len(history):
                        x_url = history[selected_idx].get("twitter")
                        if x_url:
                            subprocess.Popen(["open", x_url])
                            status_msg = f"🐦 Opened X / Twitter in browser: {x_url}"
                        else:
                            status_msg = "⚠️ No X / Twitter account found for this project."
                        status_timer = time.time()
                        needs_update = True
                elif key in ('m', 'M'):
                    if history and 0 <= selected_idx < len(history):
                        m_url = history[selected_idx].get("market_url") or history[selected_idx].get("pair_url")
                        c_addr = history[selected_idx].get("contract", "")
                        std = history[selected_idx].get("standard", "")
                        if not m_url and "NFT" in std:
                            m_url = f"https://element.market/assets/bsc/{c_addr}"
                        elif not m_url and ("BEP-20" in std or "Token" in std):
                            m_url = f"https://pancakeswap.finance/swap?outputCurrency={c_addr}"
                        if m_url:
                            subprocess.Popen(["open", m_url])
                            status_msg = f"🛒 Opened Marketplace in browser!"
                        else:
                            status_msg = "⚠️ No secondary marketplace link available."
                        status_timer = time.time()
                        needs_update = True

                now = time.time()
                if status_msg and status_timer and (now - status_timer > 3):
                    status_msg = ""
                    status_timer = 0
                    needs_update = True

                if latest_block[0] != last_rendered_block:
                    last_rendered_block = latest_block[0]
                    needs_update = True

                if needs_update or (now - last_render_time >= 0.4):
                    live.update(build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal))
                    last_render_time = now
                    needs_update = False
    finally:
        stop_event.set()
        key_reader.close()


def run_explore_mode(rpc: BscRpcClient, wallet: str):
    history = load_history()
    if not history:
        print("[!] No projects in history to explore. Start the monitor with 'python3 monitor.py live' first.")
        return

    if not RICH_AVAILABLE or not sys.stdin.isatty():
        list_projects(rpc, wallet)
        return

    key_reader = KeyReader()
    selected_idx = 0
    show_modal = False
    status_msg = ""
    status_timer = 0
    curr_b = rpc.get_block_number()

    needs_update = True
    last_render_time = time.time()

    try:
        with Live(
            build_interactive_dashboard(history, selected_idx, curr_b, 0, wallet, status_msg, show_modal),
            refresh_per_second=6,
            screen=True
        ) as live:
            while True:
                key = key_reader.get_key(timeout=0.08)
                if key in ('q', 'Q'):
                    break
                elif key == 'ESC':
                    if show_modal:
                        show_modal = False
                        needs_update = True
                elif key == 'UP':
                    if not show_modal:
                        selected_idx = max(0, selected_idx - 1)
                        needs_update = True
                elif key == 'DOWN':
                    if not show_modal:
                        selected_idx = min(max(0, len(history) - 1), selected_idx + 1)
                        needs_update = True
                elif key in ('1', '2', '3', '4', '5', '6', '7', '8', '9'):
                    target_idx = int(key) - 1
                    if 0 <= target_idx < len(history):
                        selected_idx = target_idx
                        needs_update = True
                elif key in ('ENTER', 'SPACE', ' '):
                    show_modal = not show_modal
                    needs_update = True
                elif key in ('r', 'R'):
                    sel_name = history[selected_idx].get("name", "project")
                    status_msg = f"🔄 Refreshing {sel_name} on-chain..."
                    live.update(build_interactive_dashboard(history, selected_idx, curr_b, 0, wallet, status_msg, show_modal))
                    refresh_project_metrics(rpc, history[selected_idx], wallet)
                    save_history(history)
                    status_msg = f"✅ Updated {sel_name}!"
                    status_timer = time.time()
                    needs_update = True
                elif key in ('b', 'B'):
                    if history and 0 <= selected_idx < len(history):
                        c_addr = history[selected_idx].get("contract")
                        subprocess.Popen(["open", f"https://bscscan.com/address/{c_addr}"])
                        status_msg = "🌐 Opened BscScan in browser!"
                        status_timer = time.time()
                        needs_update = True

                now = time.time()
                if status_msg and status_timer and (now - status_timer > 3):
                    status_msg = ""
                    status_timer = 0
                    needs_update = True

                if needs_update or (now - last_render_time >= 0.5):
                    live.update(build_interactive_dashboard(history, selected_idx, curr_b, 0, wallet, status_msg, show_modal))
                    last_render_time = now
                    needs_update = False
    finally:
        key_reader.close()


def list_projects(rpc: BscRpcClient, wallet: str):
    history = load_history()
    if not history:
        print("No .ai Agent ID projects detected yet.")
        return
    if RICH_AVAILABLE:
        console = Console()
        dashboard = build_dashboard(history, rpc.get_block_number(), 0, wallet)
        console.print(dashboard)
    else:
        print_banner()
        for item in history:
            print(f"• #{item.get('block')} | {item.get('name')} ({item.get('symbol')}) | {item.get('contract')}")


def inspect_contract(rpc: BscRpcClient, contract_addr: str, wallet: str):
    print_banner()
    print(f"[*] Inspecting Contract: {C_CYAN}{contract_addr}{C_RESET}\n")
    code = rpc.get_code(contract_addr)
    if not code or code == "0x":
        print(f"{C_RED}[!] Address has no bytecode on BSC.{C_RESET}")
        return

    links_to_registry = AGENT_ID_BYTES in code
    print(f"• {C_BOLD}References .ai Agent ID Registry?{C_RESET} {C_GREEN if links_to_registry else C_RED}{links_to_registry}{C_RESET}")

    details = analyze_contract(rpc, contract_addr, wallet)
    cls = details["classification"]

    print(f"• {C_BOLD}Asset Type:{C_RESET} {cls['icon']} {C_BOLD}{cls['standard']}{C_RESET}")
    print(f"• {C_BOLD}Category:{C_RESET} {C_MAGENTA if cls['is_nft'] else C_YELLOW}{cls['category']}{C_RESET}")
    print(f"• {C_BOLD}Name:{C_RESET} {details['name'] or 'N/A'}")
    print(f"• {C_BOLD}Symbol:{C_RESET} {details['symbol'] or 'N/A'}")
    
    if cls.get("underlying"):
        print(f"• {C_BOLD}Underlying Token Address:{C_RESET} {cls['underlying']}")

    if details.get("is_paused") is not None:
        p_str = f"{C_RED}Paused (Minting not yet opened){C_RESET}" if details["is_paused"] else f"{C_GREEN}Active / Live{C_RESET}"
        print(f"• {C_BOLD}Mint Status:{C_RESET} {p_str}")

    if details.get("max_supply"):
        print(f"• {C_BOLD}Max Supply / Total Minted:{C_RESET} {details.get('total_minted', 0)} / {details['max_supply']}")

    print(f"• {C_BOLD}Free Mint for your wallet?{C_RESET} {C_GREEN if details['has_free_mint'] else C_YELLOW}{details['has_free_mint']}{C_RESET}")
    print(f"• {C_BOLD}Mint Price:{C_RESET} {details.get('mint_price_str', 'TBD')} ({details.get('mint_value_wei', '0')} wei)")
    print(f"• {C_BOLD}BscScan Explorer:{C_RESET} https://bscscan.com/address/{contract_addr}")

    # Market & Community Interest Analysis
    mkt = analyze_market_interest(rpc, contract_addr, None, details)
    print_interest_report(mkt)

    print(f"\n{C_BOLD}Quick CLI Snipe Command:{C_RESET}")
    print(f"{C_CYAN}baw contract-call preview --binanceChainId 56 --from {wallet} --to {contract_addr} --value {details.get('mint_value_wei', '0')} --inputData {details['mint_calldata']} --json{C_RESET}\n")


def main():
    parser = argparse.ArgumentParser(description="Binance .ai Agent ID Fair-Mint & Contract Monitor")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    live_parser = subparsers.add_parser("live", help="Start real-time block-by-block monitor")
    live_parser.add_argument("--wallet", default=DEFAULT_WALLET, help="Your Binance Agentic Wallet address")
    live_parser.add_argument("--plain", action="store_true", help="Use plain scrolling text mode instead of live table")

    explore_parser = subparsers.add_parser("explore", help="Interactive TUI table navigation, deep-dive modal, and live metric refresh")
    explore_parser.add_argument("--wallet", default=DEFAULT_WALLET, help="Your Binance Agentic Wallet address")

    list_parser = subparsers.add_parser("list", help="Display live table of tracked projects")
    list_parser.add_argument("--wallet", default=DEFAULT_WALLET, help="Your Binance Agentic Wallet address")

    refresh_parser = subparsers.add_parser("refresh", help="Fetch fresh on-chain metrics for all tracked projects")
    refresh_parser.add_argument("--wallet", default=DEFAULT_WALLET, help="Your Binance Agentic Wallet address")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a specific contract address")
    inspect_parser.add_argument("contract", help="Contract address to inspect")
    inspect_parser.add_argument("--wallet", default=DEFAULT_WALLET, help="Your Binance Agentic Wallet address")

    args = parser.parse_args()
    rpc = BscRpcClient(RPC_ENDPOINTS)

    if args.command == "live":
        run_live_monitor(rpc, args.wallet, plain=args.plain)
    elif args.command == "explore":
        run_explore_mode(rpc, args.wallet)
    elif args.command == "list":
        list_projects(rpc, args.wallet)
    elif args.command == "refresh":
        history = load_history()
        print(f"[*] Refreshing on-chain metrics for {len(history)} tracked projects...")
        refresh_all_metrics(rpc, history, args.wallet)
        list_projects(rpc, args.wallet)
    elif args.command == "inspect":
        inspect_contract(rpc, args.contract, args.wallet)
    else:
        run_live_monitor(rpc, DEFAULT_WALLET, plain=False)


if __name__ == "__main__":
    main()
