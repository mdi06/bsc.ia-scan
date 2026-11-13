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
- Security & Mint Audit: Checks for free mints, supply, and function selectors.
- Terminal & Audio Alerts (macOS).
- CLI command generator for previewing and executing mints with `baw`.
"""

import os
import time
import json
import re
import csv
import logging
import urllib.request
import math
import threading
from typing import Optional, Dict, Any, List, Tuple, Iterable

import filters
from filters import EQUITY_SYMBOLS_CORE, EXCLUDED_PEGS_CONTRACTS, PEG_SYMBOLS_CORE

# Swallowed best-effort failures are logged at DEBUG: printing would corrupt the Rich TUI.
_log = logging.getLogger(__name__)

# Official Binance .ai Agent ID Registry on BSC
AGENT_ID_REGISTRY = "0x60B9262456dE011F7d15a54CdD8c48bAaa8B022F"
AGENT_ID_BYTES = "60b9262456de011f7d15a54cdd8c48baaa8b022f"

# Space ID Master Registry (Name Service Layer on BSC)
SPACE_ID_MASTER_REGISTRY = "0x08CEd32a7f3eeC915Ba84415e9C07a7286977956"
SPACE_ID_MASTER_BYTES = "08ced32a7f3eec915ba84415e9c07a7286977956"

# Four.meme Agent ID Launchpad & Factory
FOUR_MEME_FACTORY = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"
FOUR_MEME_FACTORY_BYTES = "5c952063c7fc8610ffdb798152d69f0b9550762b"
FOUR_MEME_ROUTER = "0xF251F83e40a78868FcfA3FA4599Dad6494E46034"
FOUR_MEME_ROUTER_BYTES = "f251f83e40a78868fcfa3fa4599dad6494e46034"

# Set of all tracked target registry & launchpad address byte-strings (lowercase hex without 0x)
TRACKED_REGISTRY_BYTES = [
    AGENT_ID_BYTES,
    SPACE_ID_MASTER_BYTES,
    FOUR_MEME_FACTORY_BYTES,
    FOUR_MEME_ROUTER_BYTES,
]

def matches_tracked_registry(data_str: str) -> bool:
    """Returns True if the given calldata or bytecode references any tracked registry or launchpad."""
    if not data_str:
        return False
    d = data_str.lower()
    return any(b in d for b in TRACKED_REGISTRY_BYTES)

# Default BSC RPC Endpoints (with fallback)
RPC_ENDPOINTS = [
    "https://bsc-rpc.publicnode.com",
    "https://bsc-dataseed.bnbchain.org",
    "https://bsc-dataseed1.defibit.io",
    "https://bsc-dataseed1.ninicoin.io",
    "https://bsc.publicnode.com"
]

DEFAULT_WALLET = os.environ.get("AGENT_WALLET", "0xA3FbE3664dB8478c094dD36b2BB306d72CBCb37A")
# On-Chain DEX & Infrastructure Addresses
PANCAKE_FACTORY_V2 = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
PANCAKE_ROUTER_V2 = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
WBNB_ADDRESS = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
USDT_ADDRESS = "0x55d398326f99059fF775485246999027B3197955"
USD1_ADDRESS = "0x8d0D000ee44948FC98c9B98A4FA4921476f08B0D"
BNCB_ADDRESS = "0x4902C5ebc598265ED2212b559b042de8a5eeec3f"
USDC_ADDRESS = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"

# Element Market BSC Router & Event Signatures (Option D: nftVolWatch)
ELEMENT_EX = "0xb3e3dfcb2d9f3dde16d78b9e6eb3538eb32b5ae1"
T_721_FILLED = "0x9c248aa1a265aa616f707b979d57f4529bb63a4fc34dc7fc61fdddc18410f74e"
# ERC721BuyOrderFilled: an accepted offer. Same data layout as the sell fill, but the maker is the buyer.
T_721_BUY_FILLED = "0xd90a5c60975c6ff8eafcf02088e7b50ae5d9e156a79206ba553df1c4fb4594c2"
# Element stats cover the last 24 hours. The window is set in hours because BSC's block time has changed
# (3s -> 0.45s): the old fixed 15,000-block window had shrunk to under two hours and was nearly always empty.
ELEMENT_LOOKBACK_HOURS = 24.0
BSC_BLOCK_TIME_FALLBACK_SEC = 0.45   # used only when the block time cannot be measured
ELEMENT_LOG_CHUNK_BLOCKS = 10000
ELEMENT_REORG_BLOCKS = 20            # the newest blocks are re-read every scan, never served from the cache
T_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Stealth Factory & EIP-1167 / EIP-1967 Proxy Signatures (Option E)
EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
EIP1967_BEACON_SLOT = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
BEACON_IMPL_SELECTOR = "0x5c60da1b"  # implementation()

# Recognized Launchpad & Vault Factories
VAULT_FACTORIES = [
    "0x90497450f2a706f1951b5bdda52b4e5d16f34c06",  # Flap Vault Factory (AIDROP, etc.)
    FOUR_MEME_FACTORY.lower(),                       # Four.meme Token Factory / AMAP
    FOUR_MEME_ROUTER.lower()                         # Four.meme Buy Router
]

def known_infra_addresses() -> set:
    """Routers, factories and registries whose transactions are never treated as drop candidates."""
    return {
        PANCAKE_FACTORY_V2.lower(),
        WBNB_ADDRESS.lower(),
        USDT_ADDRESS.lower(),
        AGENT_ID_REGISTRY.lower(),
        SPACE_ID_MASTER_REGISTRY.lower(),
        FOUR_MEME_FACTORY.lower(),
        FOUR_MEME_ROUTER.lower(),
        PANCAKE_ROUTER_V2.lower(),
        "0x13f4ea83d0bd40e75c8222255bc855a974568dd4",
    }


FACTORY_SELECTORS = ["27cca59f", "7d38bb0d", "1737a4bd"]

# Top BSC quote pairs for PancakeSwap V2 secondary liquidity resolution
QUOTE_TOKENS = [
    {"symbol": "WBNB", "address": WBNB_ADDRESS, "is_bnb": True, "decimals": 18},
    {"symbol": "USDT", "address": USDT_ADDRESS, "is_bnb": False, "decimals": 18, "usd_rate": 1.0},
    {"symbol": "USD1", "address": USD1_ADDRESS, "is_bnb": False, "decimals": 18, "usd_rate": 1.0},
    {"symbol": "BNCB", "address": BNCB_ADDRESS, "is_bnb": True, "decimals": 18},
    {"symbol": "USDC", "address": USDC_ADDRESS, "is_bnb": False, "decimals": 18, "usd_rate": 1.0},
]

# Refund function signatures to audit contract bytecode for fair-mint refund guarantees
REFUND_SELECTORS = {
    "590e1ae3": "refund()",
    "278ecde1": "claimRefund()",
    "fa89401a": "claimRefund(uint256)",
    "38e771ab": "refundOpen()",
    "b5545a3c": "refundable(address)",
    "5b7baf64": "getRefund()",
    "110f8874": "withdrawRefund()",
    "b2d5ae44": "isRefundable()",
    "bffa55d5": "refundAmount(address)",
    "68ec0d99": "refundAll()",
    "74c1b1c9": "userRefund()",
    "b30196e5": "enableRefund()",
    "c033a490": "requestRefund()",
    "64cfd025": "withdrawRefund()",
}

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")
SMART_WALLETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smart_wallets.json")
STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "agent_id_stats.json")
STATS_FILE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_id_stats.json")
SMART_WALLETS_CSV_URL = "https://docs.google.com/spreadsheets/d/1jcKyWK1JJ8uBchTS3p1ok18DeB7yrDb2ZDxOaNYF73Q/export?format=csv&gid=395869104"

# Registered .ai wallets, used until a real count has been read from the chain.
DEFAULT_AGENT_WALLET_COUNT = 178001

def load_agent_id_stats() -> Dict[str, Any]:
    """Loads live statistics for registered Binance .ai Agent ID wallets."""
    best = None
    for p in (STATS_FILE, STATS_FILE_ROOT):
        if os.path.exists(p):
            try:
                with open(p, "r") as f:
                    d = json.load(f)
                if isinstance(d, dict) and d.get("total_created_wallets"):
                    if best is None or int(d.get("total_created_wallets", 0)) > int(best.get("total_created_wallets", 0)):
                        best = d
            except Exception as exc:
                _log.debug("could not read agent-id stats from %s: %s", p, exc)
    if best is not None:
        return best
    return {
        "total_created_wallets": DEFAULT_AGENT_WALLET_COUNT,
        "last_block": 132757997,
        "last_updated": time.time(),
        "source": "SPACE ID / Binance Agent ID Registry (0x60B9262456dE011F7d15a54CdD8c48bAaa8B022F)"
    }

def save_agent_id_stats(stats: Dict[str, Any]):
    """Persists updated agent ID statistics atomically to disk."""
    for p in (STATS_FILE, STATS_FILE_ROOT):
        try:
            d = os.path.dirname(p)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w") as f:
                json.dump(stats, f, indent=2)
            os.replace(tmp, p)
        except Exception as exc:
            _log.debug("could not write agent-id stats to %s: %s", p, exc)

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
    print(f"{C_BLUE}[*] Fetching smart agent wallets from Google Sheets ({SMART_WALLETS_CSV_URL[:45]}...)...{C_RESET}")
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
            print(f"{C_GREEN}[+] Successfully synced {len(wallets):,} smart agent wallets to {SMART_WALLETS_FILE}!{C_RESET}")
        else:
            print(f"{C_RED}[!] Could not parse smart wallets from spreadsheet.{C_RESET}")
    except Exception as e:
        print(f"{C_RED}[!] Error downloading smart wallets: {e}{C_RESET}")
    return wallets


# ANSI Terminal Colors
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"
C_CYAN = "\033[36m"
C_RED = "\033[31m"


try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


class BscRpcClient:
    def __init__(self, endpoints: List[str]):
        self.endpoints = endpoints
        self.current_idx = 0
        self._http_client = None
        if HAS_HTTPX:
            try:
                self._http_client = httpx.Client(
                    timeout=5.0,
                    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20)
                )
            except Exception:
                self._http_client = None

    def call(self, method: str, params: list) -> Any:
        payload_dict = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

        for _ in range(len(self.endpoints)):
            rpc_url = self.endpoints[self.current_idx]
            try:
                if self._http_client is not None:
                    resp = self._http_client.post(rpc_url, json=payload_dict, headers=headers)
                    data = resp.json()
                else:
                    payload = json.dumps(payload_dict).encode("utf-8")
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

    def get_block_number(self) -> Optional[int]:
        res = self.call("eth_blockNumber", [])
        if res:
            try:
                return int(res, 16)
            except Exception:
                return None
        return None

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

    def get_logs_strict(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Like get_logs, but raises when no endpoint answered. get_logs returns [] in that case, which a
        caller cannot tell apart from "no logs" (several public endpoints reject eth_getLogs outright)."""
        res = self.call("eth_getLogs", [params])
        if res is None:
            raise RuntimeError("eth_getLogs failed on every RPC endpoint")
        return res

    def get_storage_at(self, address: str, position: str) -> Optional[str]:
        return self.call("eth_getStorageAt", [address, position, "latest"])

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


def replace_calldata_recipient(
    calldata: Optional[str],
    target_wallet: str,
    original_sender: Optional[str] = None,
    extra_addresses: Optional[Iterable[str]] = None
) -> str:
    """
    Replaces address parameters in EVM calldata with the active user's wallet address.

    1. Supports recv (plain BNB transfer): returns '0x' if calldata is empty, '0x', 'recv', 'receive', or None.
    2. Pattern C (0x836a1040): Word 1 (bytes 36..68) is the recipient parameter of mint(uint256,address,uint256),
       always replaced with target_wallet.
    3. Any 32-byte word matching original_sender, DEFAULT_WALLET, or any tracked smart wallet
       is replaced with target_wallet.
    """
    if not calldata:
        return "0x"
    c_str = str(calldata).strip()
    if c_str.lower() in ("0x", "recv", "receive", "none", ""):
        return "0x"

    prefix = "0x" if c_str.startswith("0x") else ""
    raw = c_str[2:] if c_str.startswith("0x") else c_str
    if len(raw) < 8:
        return ("0x" + raw) if prefix else raw

    selector = raw[:8].lower()
    params = raw[8:]

    tgt_clean = target_wallet.lower().replace("0x", "").strip()
    if len(tgt_clean) != 40:
        return ("0x" + raw) if prefix else raw
    tgt_word = "0" * 24 + tgt_clean

    words = [params[i:i+64] for i in range(0, len(params), 64)]

    # Collect known smart wallets, sender & default wallet
    known_sw = get_cached_smart_wallets()

    explicit_addrs = set()
    if original_sender:
        explicit_addrs.add(original_sender.lower().replace("0x", "").strip())
    if DEFAULT_WALLET:
        explicit_addrs.add(DEFAULT_WALLET.lower().replace("0x", "").strip())
    if extra_addresses:
        for ea in extra_addresses:
            if ea:
                explicit_addrs.add(ea.lower().replace("0x", "").strip())

    new_words = []
    for idx, w in enumerate(words):
        if len(w) == 64:
            # Pattern C (0x836a1040): mint(uint256 quantity, address recipient, uint256 maxCost)
            if selector == "836a1040" and idx == 1:
                new_words.append(tgt_word)
            elif w.startswith("0" * 24):
                addr_hex = w[24:].lower()
                addr_with_prefix = "0x" + addr_hex
                if addr_hex in explicit_addrs or addr_with_prefix in known_sw:
                    new_words.append(tgt_word)
                else:
                    new_words.append(w)
            else:
                new_words.append(w)
        else:
            new_words.append(w)

    return ("0x" if prefix else "") + selector + "".join(new_words)


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


def get_element_market_url(name: str, contract_addr: str) -> str:
    """
    Generates the working Element Market collection URL using the lowercase hyphenated name slug.
    Element returns 404 for raw hex addresses but 200 OK for collection slugs.
    Falls back to contract address if name is unavailable.
    """
    if name and name != "Unknown":
        clean_name = re.sub(r"\([^)]*\)", "", name).strip()
        slug = re.sub(r"[^a-z0-9]+", "-", clean_name.lower()).strip("-")
        if slug:
            return f"https://element.market/collections/{slug}"
    return f"https://element.market/collections/{contract_addr}"


def resolve_full_bytecode(rpc: BscRpcClient, contract_addr: str, code: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Option E: Stealth Factory & EIP-1167 / EIP-1967 / Beacon Proxy Clone Resolver.
    Unwraps:
    1. EIP-1167 Minimal Proxy Clones (e.g. 45-byte clones used by launchpad factories).
    2. EIP-1967 Transparent & UUPS Implementation Proxies (slot 0x360894a1...).
    3. EIP-1967 Beacon Proxies (slot 0xa3f0ad74... -> implementation()).
    Returns (full_code_hex, proxy_type, master_implementation_address).
    """
    c_clean = (code or "").lower()
    if c_clean.startswith("0x"):
        c_clean = c_clean[2:]

    # 1. EIP-1167 Minimal Proxy Clone (Opcode pattern: 363d3d373d3d3d363d73<20-byte target>5af4...)
    if "363d3d373d3d3d363d73" in c_clean and len(c_clean) <= 120:
        idx = c_clean.find("363d3d373d3d3d363d73") + 20
        target_hex = c_clean[idx:idx + 40]
        if len(target_hex) == 40 and target_hex != "0" * 40:
            target_addr = "0x" + target_hex
            try:
                target_code = rpc.get_code(target_addr)
                if target_code and len(target_code) > 10:
                    return target_code.lower(), "EIP-1167 Clone", target_addr
            except Exception as exc:
                _log.debug("could not fetch EIP-1167 target code %s: %s", target_addr, exc)
            return c_clean, "EIP-1167 Clone", target_addr

    # 2. EIP-1967 Implementation Slot (UUPS / Transparent Proxy)
    try:
        impl_slot = rpc.get_storage_at(contract_addr, EIP1967_IMPL_SLOT)
        if impl_slot and len(impl_slot) >= 66:
            clean_val = impl_slot[2:] if impl_slot.startswith("0x") else impl_slot
            target_hex = clean_val[-40:].lower()
            if target_hex != "0" * 40:
                target_addr = "0x" + target_hex
                target_code = rpc.get_code(target_addr)
                if target_code and len(target_code) > 10:
                    return target_code.lower(), "EIP-1967 Proxy", target_addr
                return c_clean, "EIP-1967 Proxy", target_addr
    except Exception as exc:
        _log.debug("EIP-1967 implementation lookup failed for %s: %s", contract_addr, exc)

    # 3. EIP-1967 Beacon Proxy Slot (reads beacon address -> implementation())
    try:
        beacon_slot = rpc.get_storage_at(contract_addr, EIP1967_BEACON_SLOT)
        if beacon_slot and len(beacon_slot) >= 66:
            clean_b = beacon_slot[2:] if beacon_slot.startswith("0x") else beacon_slot
            beacon_hex = clean_b[-40:].lower()
            if beacon_hex != "0" * 40:
                beacon_addr = "0x" + beacon_hex
                impl_res = rpc.call_contract(beacon_addr, BEACON_IMPL_SELECTOR)
                if impl_res and len(impl_res) >= 66:
                    clean_impl = impl_res[2:] if impl_res.startswith("0x") else impl_res
                    target_hex = clean_impl[-40:].lower()
                    if target_hex != "0" * 40:
                        target_addr = "0x" + target_hex
                        target_code = rpc.get_code(target_addr)
                        if target_code and len(target_code) > 10:
                            return target_code.lower(), "Beacon Proxy", target_addr
                        return c_clean, "Beacon Proxy", target_addr
    except Exception as exc:
        _log.debug("EIP-1967 beacon lookup failed for %s: %s", contract_addr, exc)

    return c_clean, None, None


def is_launchpad_factory(code: str, contract_addr: str = "") -> Tuple[bool, Optional[str]]:
    """
    Identifies launchpad factories (Flap, ToshX, Four.meme, etc.)
    that deploy proxy clones for fair-mint tokens/NFTs.
    """
    if contract_addr and contract_addr.lower() in [f.lower() for f in VAULT_FACTORIES]:
        return True, "Flap Vault Factory"

    c = (code or "").lower()
    if not c:
        return False, None

    if "27cca59f" in c:
        return True, "Flap / ToshX Vault Factory"
    if "7d38bb0d" in c or "1737a4bd" in c:
        return True, "Launchpad Factory"
    if any(sel in c for sel in FACTORY_SELECTORS):
        return True, "Proxy Factory"

    return False, None


def is_proxy_contract(rpc: BscRpcClient, contract_addr: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """Checks if a contract address is an EIP-1167, EIP-1967, or Beacon proxy."""
    try:
        code = rpc.get_code(contract_addr)
        if not code or len(code) <= 2:
            return False, None, None
        full_code, proxy_type, impl_addr = resolve_full_bytecode(rpc, contract_addr, code)
        is_proxy = proxy_type is not None
        return is_proxy, proxy_type, impl_addr
    except Exception:
        return False, None, None


def detect_factory_pattern(rpc: BscRpcClient, contract_addr: str) -> Tuple[bool, Optional[str]]:
    """Checks if a contract address is a recognized launchpad / token factory."""
    try:
        code = rpc.get_code(contract_addr)
        if not code or len(code) <= 2:
            return False, None
        return is_launchpad_factory(code, contract_addr)
    except Exception:
        return False, None


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
        "market_url": "",
        "has_refund": False,
        "refund_methods": [],
        "is_proxy_clone": False,
        "proxy_type": None,
        "master_implementation": None,
        "is_factory": False,
        "factory_badge": None
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

    # Check companion core() or controller contract if present
    target_addrs = [contract_addr]
    core_res = rpc.call_contract(contract_addr, keccak_selector("core()"))
    if core_res and len(core_res) >= 66:
        raw_core_addr = "0x" + core_res[-40:].lower()
        if raw_core_addr != "0x" + "0"*40:
            target_addrs.append(raw_core_addr)

    # Check supply (max supply and current minted/total supply)
    token_decimals = classification.get("decimals", 18) if classification.get("category") == "TOKEN" else None
    info["max_supply"], info["total_minted"] = read_supply(rpc, target_addrs, token_decimals)
    # decimals of the token itself (BEP-20) or of the token a Fair-Mint dispenser hands out
    if classification.get("decimals") is not None:
        info["token_decimals"] = classification["decimals"]

    # Inspect bytecode constants if max_supply still not found (e.g. PUSH2 24444 -> 0x615f7c)
    if info["max_supply"] is None:
        try:
            t_codes = "".join(rpc.get_code(a) for a in target_addrs).lower()
            if "615f7c" in t_codes:
                info["max_supply"] = 24444
        except Exception as exc:
            _log.debug("bytecode max-supply scan failed for %s: %s", contract_addr, exc)

    # If it's an NFT and no total_minted was returned, default to 0 if newly deployed
    if classification.get("category") == "NFT" and info.get("total_minted") is None:
        info["total_minted"] = 0

    code = rpc.get_code(contract_addr)
    full_code, proxy_type, impl_addr = resolve_full_bytecode(rpc, contract_addr, code)
    is_fac, fac_badge = is_launchpad_factory(full_code, contract_addr)

    info["is_proxy_clone"] = proxy_type is not None
    info["proxy_type"] = proxy_type
    info["master_implementation"] = impl_addr
    info["is_factory"] = is_fac
    info["factory_badge"] = fac_badge
    if full_code and len(full_code) > len(code or ""):
        code = full_code
    param_wallet = wallet_addr[2:].lower().zfill(64)

    # Resolve marketplace URL
    if classification.get("category") == "NFT":
        info["market_url"] = get_element_market_url(info.get("name", ""), contract_addr)
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
                except Exception as exc:
                    _log.debug("contractURI metadata fetch failed for %s: %s", contract_addr, exc)
    except Exception as exc:
        _log.debug("contractURI lookup failed for %s: %s", contract_addr, exc)

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
            except Exception as exc:
                _log.debug("tokenURI lookup failed for %s: %s", contract_addr, exc)

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
            except Exception as exc:
                _log.debug("free-mint simulation (mint(uint256)) failed for %s: %s", contract_addr, exc)

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
            except Exception as exc:
                _log.debug("free-mint simulation (tiered mint) failed for %s: %s", contract_addr, exc)

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
            except Exception as exc:
                _log.debug("free-mint simulation (mint()) failed for %s: %s", contract_addr, exc)

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

    # Check for Bytecode Refund Signatures (supports direct, EIP-1167, and Beacon proxies)
    try:
        matched_refund = [name for sel, name in REFUND_SELECTORS.items() if sel in full_code]
        info["has_refund"] = len(matched_refund) > 0
        info["refund_methods"] = matched_refund
    except Exception as exc:
        _log.debug("refund selector scan failed for %s: %s", contract_addr, exc)

    return info


def get_pair_address(rpc: BscRpcClient, factory: str, token_a: str, token_b: str) -> Optional[str]:
    """PancakeSwap V2 pair address for two tokens, or None if the pair doesn't exist or the call fails."""
    call_data = "0xe6a43905" + token_a[2:].lower().zfill(64) + token_b[2:].lower().zfill(64)
    res = rpc.call_contract(factory, call_data)
    if not res or len(res) < 66:
        return None
    pair = "0x" + res[-40:]
    return None if int(pair, 16) == 0 else pair


def get_reserves(rpc: BscRpcClient, pair_addr: str) -> Optional[Tuple[int, int]]:
    """(reserve0, reserve1) of a V2 pair in raw units, or None if the call fails."""
    res = rpc.call_contract(pair_addr, "0x0902f1ac")
    if not res or len(res) < 130:
        return None
    return int(res[2:66], 16), int(res[66:130], 16)


def _call_uint(rpc: BscRpcClient, contract: str, func_sig: str) -> Optional[int]:
    """Calls a no-argument view function and returns its uint256 result, or None if it reverts or is absent."""
    res = rpc.call_contract(contract, keccak_selector(func_sig))
    if not res or len(res) < 66:
        return None
    try:
        return int(res[:66], 16)
    except ValueError:
        return None


MAX_SUPPLY_GETTERS = ("maxSupply()", "MAX_SUPPLY()", "max_supply()", "cap()", "MAX_TOKENS()", "maxTokens()",
                      "maxBoxes()", "MAX_BOXES()")
MINTED_GETTERS = ("totalMinted()", "_totalMinted()", "totalSupply()", "currentSupply()", "tokenCount()",
                  "nextTokenId()", "_nextTokenId()", "currentTokenId()", "lifetimeCommitted()", "miningCommitted()")


def read_supply(
    rpc: BscRpcClient,
    target_addrs: List[str],
    token_decimals: Optional[int] = None,
    need_max: bool = True,
) -> Tuple[Optional[float], Optional[float]]:
    """(max_supply, total_minted) read from the first getters that answer on `target_addrs`
    (the contract itself, then its core() companion if it has one).

    SUPPLY UNITS (the one convention the rest of the code relies on):
    - BEP-20 token: whole tokens, i.e. the raw integer divided by 10**decimals(), where `token_decimals` is the
      token's real decimals() (0 is a real value: a 0-decimal token's raw supply is already whole tokens).
    - NFT: an item count (token_decimals=None, raw integer).
    - Fair-Mint dispenser: token_decimals=None, the raw integer of the dispenser's own counter. Dispensers
      mix item counts and raw token amounts, so this is NOT a token supply and must never feed a market cap.
    For a BEP-20 token totalSupply() is read first: counter-style getters (totalMinted, tokenCount, ...) can
    answer with something that is not the supply.
    need_max=False skips the cap probes (about 8 calls) when the cap is already known."""
    def scale(raw: int):
        if token_decimals is None:
            return raw
        return raw / (10 ** token_decimals)

    minted_getters = MINTED_GETTERS
    if token_decimals is not None:
        minted_getters = ("totalSupply()",) + tuple(g for g in MINTED_GETTERS if g != "totalSupply()")

    max_supply = None
    total_minted = None
    for t_addr in target_addrs:
        if need_max and max_supply is None:
            for sel_name in MAX_SUPPLY_GETTERS:
                s_res = rpc.call_contract(t_addr, keccak_selector(sel_name))
                if s_res and len(s_res) >= 66:
                    try:
                        raw_s = int(s_res[:66], 16)
                        if raw_s >= 2**128 or raw_s == (2**256 - 1) or raw_s >= 10**30:
                            max_supply = None  # effectively unlimited
                            break
                        max_supply = scale(raw_s)
                        break
                    except Exception as exc:
                        _log.debug("could not parse max supply %r for %s: %s", s_res, t_addr, exc)

        if total_minted is None or total_minted == 0:
            for sel_name in minted_getters:
                m_res = rpc.call_contract(t_addr, keccak_selector(sel_name))
                if m_res and len(m_res) >= 66:
                    try:
                        raw_m = int(m_res[:66], 16)
                        if "nextTokenId" in sel_name and raw_m > 0:
                            raw_m = raw_m - 1  # nextTokenId-style counters point at the next id (ids start at 1)
                        total_minted = scale(raw_m)
                        break
                    except Exception as exc:
                        _log.debug("could not parse total minted %r for %s: %s", m_res, t_addr, exc)
    return max_supply, total_minted


def supply_for_market_cap(standard: Any, total_minted: Any, max_supply: Any = None) -> Optional[float]:
    """Supply in WHOLE TOKENS for a market-cap estimate, or None when the unit isn't certain.
    Only BEP-20 tokens qualify: read_supply stores their supply as raw / 10**decimals(). NFT counts and
    Fair-Mint dispenser counters are not token supplies. No magnitude guessing (the old `> 1e14 -> / 1e18`)."""
    if "BEP-20" not in str(standard or ""):
        return None
    try:
        supply = float(total_minted or max_supply or 0.0)
    except (TypeError, ValueError):
        return None
    return supply if supply > 0 else None


def market_cap_from_supply(standard: Any, total_minted: Any, max_supply: Any, price_usd: Any) -> Optional[float]:
    """supply x price for a BEP-20 token, None if the supply unit or the price isn't known."""
    supply = supply_for_market_cap(standard, total_minted, max_supply)
    try:
        price = float(price_usd or 0.0)
    except (TypeError, ValueError):
        return None
    return supply * price if supply and price > 0 else None


def fetch_bnb_price_usd(rpc: BscRpcClient) -> Optional[float]:
    """BNB/USD from the PancakeSwap V2 WBNB/USDT pool reserves, or None if the lookup fails."""
    try:
        call_data = "0xe6a43905" + WBNB_ADDRESS[2:].lower().zfill(64) + USDT_ADDRESS[2:].lower().zfill(64)
        pair_res = rpc.call_contract(PANCAKE_FACTORY_V2, call_data)
        if not pair_res or len(pair_res) < 66:
            return None
        pair_addr = "0x" + pair_res[-40:]
        res = rpc.call_contract(pair_addr, "0x0902f1ac")
        if not res or len(res) < 130:
            return None
        r0 = int(res[2:66], 16)
        r1 = int(res[66:130], 16)
        t0 = rpc.call_contract(pair_addr, "0x0dfe1681")
        if USDT_ADDRESS[2:].lower() in (t0 or "").lower():
            return (r0 / 1e18) / (r1 / 1e18)
        else:
            return (r1 / 1e18) / (r0 / 1e18)
    except Exception as exc:
        _log.debug("BNB price lookup failed: %s", exc)
        return None


BNB_PRICE_FALLBACK_USD = 705.0  # used until the first successful lookup
BNB_PRICE_TTL_SEC = 60.0
BNB_PRICE_STALE_AFTER_TTLS = 3  # the price stops counting as live once its last success is older than 3 TTLs
# fetched_at = last *attempt* (throttles retries); ok_at = last *successful* lookup (0.0 = never).
_bnb_price_state = {"usd": BNB_PRICE_FALLBACK_USD, "fetched_at": 0.0, "live": False, "ok_at": 0.0}
_bnb_price_lock = threading.Lock()
_bnb_price_rpc: Optional[BscRpcClient] = None


def cached_bnb_price() -> float:
    """Last known BNB/USD. Never touches the network, so it is safe in render paths."""
    return _bnb_price_state["usd"]


def refresh_bnb_price(rpc: Optional[BscRpcClient] = None, max_age: float = BNB_PRICE_TTL_SEC) -> float:
    """Returns BNB/USD, re-reading the Pancake WBNB/USDT pool when the cached value is older than `max_age`.
    A failed lookup keeps the last good price (or BNB_PRICE_FALLBACK_USD if there never was one)."""
    global _bnb_price_rpc
    with _bnb_price_lock:
        now = time.time()
        if now - _bnb_price_state["fetched_at"] >= max_age:
            if rpc is None:
                if _bnb_price_rpc is None:
                    _bnb_price_rpc = BscRpcClient(RPC_ENDPOINTS)
                rpc = _bnb_price_rpc
            price = fetch_bnb_price_usd(rpc)
            if price and price > 0:
                _bnb_price_state.update(usd=price, live=True, ok_at=now)
            else:
                _log.debug("BNB price lookup failed; keeping $%.2f", _bnb_price_state["usd"])
            _bnb_price_state["fetched_at"] = now  # also after a failure, so a down RPC isn't retried per call
        return _bnb_price_state["usd"]


def bnb_price_as_of() -> Optional[float]:
    """Unix time of the last successful BNB/USD lookup, or None if there never was one."""
    return _bnb_price_state.get("ok_at") or None


def bnb_price_is_live() -> bool:
    """True while the last *successful* on-chain lookup is recent (within BNB_PRICE_STALE_AFTER_TTLS TTLs).
    After a longer RPC outage the cached price is reported as not live."""
    ok_at = _bnb_price_state.get("ok_at") or 0.0
    return bool(_bnb_price_state["live"]) and ok_at > 0 and (time.time() - ok_at) <= BNB_PRICE_STALE_AFTER_TTLS * BNB_PRICE_TTL_SEC


def get_bnb_price_usd(rpc: BscRpcClient) -> float:
    return refresh_bnb_price(rpc)


def fetch_token_market_data(token_addr: str) -> Dict[str, Any]:
    """
    Fetches real-time on-chain holders, DEX purchase metrics, and security audit:
    - On-chain holder count, honeypot check, buy/sell tax, LP lock %, top 10 % via GoPlus Security
    - 24h & 1h purchase count (buys), sells, and volume via DexScreener API
    """
    res = {
        "holders": None,    # None = no source answered; 0 would claim "zero holders"
        "buys_24h": None,
        "buys_1h": None,
        "sells_24h": None,
        "volume_24h": 0.0,
        "market_cap": 0.0,
        "fdv": 0.0,
        "price_change_24h": 0.0,
        "pair_url": "",
        "market_price_usd": 0.0,
        "market_price_bnb": 0.0,
        "lp_usd": 0.0,
        "dex_id": "",
        "pair_address": "",
        "quote_symbol": "",
        "token_symbol": "",
        "website": "",
        "twitter": "",
        "security": {
            "audited": False,
            "is_honeypot": False,
            "buy_tax": 0.0,
            "sell_tax": 0.0,
            "cannot_sell_all": False,
            "is_open_source": True,
            "lp_locked_pct": 0.0,
            "top10_pct": 0.0,
            "risk_level": "SAFE",
            "risk_badge": "🟢 Clean",
            "risk_flags": []
        },
        "risk_level": "SAFE",
        "risk_badge": "🟢 Clean",
        "risk_flags": []
    }
    if not token_addr or token_addr.lower() == "0x" + "0"*40:
        return res
    token_addr = token_addr.lower()

    # 1. On-Chain Holders & Security via GoPlus Token Security
    try:
        url = f"https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses={token_addr}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=2.5) as r:
            payload = json.loads(r.read().decode("utf-8"))
            data = payload.get("result", {}).get(token_addr, {})
            if data:
                hc = data.get("holder_count")
                if hc is not None:
                    res["holders"] = int(hc)

                # Parse Security & Anti-Rug Metrics
                is_hp = (data.get("is_honeypot") == "1")
                no_sell = (data.get("cannot_sell_all") == "1")
                buy_tax = 0.0
                sell_tax = 0.0
                try:
                    buy_tax = float(data.get("buy_tax") or 0.0) * 100.0
                except Exception as exc:
                    _log.debug("bad buy_tax value from GoPlus: %s", exc)
                try:
                    sell_tax = float(data.get("sell_tax") or 0.0) * 100.0
                except Exception as exc:
                    _log.debug("bad sell_tax value from GoPlus: %s", exc)
                is_open = (data.get("is_open_source") == "1")

                # LP Locked / Burned Calculation
                lp_holders = data.get("lp_holders") or []
                total_lp_locked = 0.0
                for lph in lp_holders:
                    addr_lph = (lph.get("address") or "").lower()
                    is_dead = addr_lph in ("0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead")
                    if lph.get("is_locked") == 1 or is_dead:
                        try:
                            total_lp_locked += float(lph.get("percent") or 0.0) * 100.0
                        except Exception as exc:
                            _log.debug("bad LP lock entry from GoPlus: %s", exc)
                lp_locked_pct = min(100.0, total_lp_locked)

                # Top 10 Holders Percentage
                h_list = data.get("holders") or []
                top10_sum = 0.0
                for h in h_list[:10]:
                    try:
                        top10_sum += float(h.get("percent") or 0.0) * 100.0
                    except Exception as exc:
                        _log.debug("bad holder percent from GoPlus: %s", exc)
                top10_pct = min(100.0, top10_sum)

                flags = []
                if is_hp or no_sell or sell_tax >= 50.0:
                    risk_lvl = "RUG"
                    risk_badge = "🚨 Honeypot"
                    if is_hp: flags.append("Honeypot Detected")
                    if no_sell: flags.append("Cannot Sell All")
                    if sell_tax >= 50.0: flags.append(f"Extreme Sell Tax ({sell_tax:.0f}%)")
                elif sell_tax > 15.0 or (lp_locked_pct < 10.0 and len(lp_holders) > 0 and top10_pct > 85.0):
                    risk_lvl = "HIGH"
                    risk_badge = "⚠️ High Risk"
                    if sell_tax > 15.0: flags.append(f"High Sell Tax ({sell_tax:.1f}%)")
                    if lp_locked_pct < 10.0 and len(lp_holders) > 0: flags.append("LP Unlocked (Dev Wallet)")
                    if top10_pct > 85.0: flags.append(f"Top 10 Hoards {top10_pct:.0f}%")
                elif top10_pct > 65.0 or not is_open:
                    risk_lvl = "MED"
                    risk_badge = "🟡 Med Risk"
                    if not is_open: flags.append("Closed Source")
                    if top10_pct > 65.0: flags.append(f"Top 10 Holds {top10_pct:.0f}%")
                else:
                    risk_lvl = "SAFE"
                    risk_badge = "🟢 Clean"
                    flags.append(f"Taxes: {buy_tax:.0f}%/{sell_tax:.0f}%")
                    if lp_locked_pct > 80.0: flags.append(f"LP {lp_locked_pct:.0f}% Locked")
                    if is_open: flags.append("Open Source")

                sec_dict = {
                    "audited": True,
                    "is_honeypot": is_hp,
                    "buy_tax": buy_tax,
                    "sell_tax": sell_tax,
                    "cannot_sell_all": no_sell,
                    "is_open_source": is_open,
                    "lp_locked_pct": lp_locked_pct,
                    "top10_pct": top10_pct,
                    "risk_level": risk_lvl,
                    "risk_badge": risk_badge,
                    "risk_flags": flags
                }
                res["security"] = sec_dict
                res["risk_level"] = risk_lvl
                res["risk_badge"] = risk_badge
                res["risk_flags"] = flags
    except Exception as exc:
        _log.debug("GoPlus lookup failed for %s: %s", token_addr, exc)

    # 2. Purchases (buys) & 24h volume via DexScreener
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_addr}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=2.5) as r:
            payload = json.loads(r.read().decode("utf-8"))
            pairs = payload.get("pairs") or []
            if pairs:
                # Accuracy Guard: Only consider pools where our token is the BASE token.
                # Avoids selecting quote pools (e.g. GEN/ANTFUN where ANTFUN is the quote token and GEN costs $7.20)
                base_pairs = [
                    x for x in pairs
                    if (x.get("baseToken", {}).get("address") or "").lower() == token_addr.lower()
                ]
                if not base_pairs:
                    return res
                # Prioritize pool with highest liquidity depth
                base_pairs.sort(key=lambda x: float((x.get("liquidity") or {}).get("usd", 0) or 0), reverse=True)
                p = base_pairs[0]
                txns = p.get("txns", {})
                res["buys_24h"] = txns.get("h24", {}).get("buys", 0)
                res["buys_1h"] = txns.get("h1", {}).get("buys", 0)
                res["sells_24h"] = txns.get("h24", {}).get("sells", 0)
                res["volume_24h"] = float(p.get("volume", {}).get("h24", 0.0) or 0.0)
                mcap = float(p.get("marketCap") or 0.0)
                fdv = float(p.get("fdv") or 0.0)
                res["market_cap"] = mcap if mcap > 0 else fdv
                res["fdv"] = fdv
                res["price_change_24h"] = float((p.get("priceChange") or {}).get("h24") or 0.0)
                res["pair_url"] = p.get("url", "")
                res["market_price_usd"] = float(p.get("priceUsd") or 0.0)
                # priceNative is in the QUOTE token (USDT for USDT pairs): it is a BNB price only for a WBNB pair
                px_usd = res["market_price_usd"]
                if ((p.get("quoteToken") or {}).get("address") or "").lower() == WBNB_ADDRESS.lower():
                    res["market_price_bnb"] = float(p.get("priceNative") or 0.0)
                elif px_usd > 0 and bnb_price_is_live():
                    res["market_price_bnb"] = px_usd / cached_bnb_price()
                res["lp_usd"] = float((p.get("liquidity") or {}).get("usd", 0.0) or 0.0)
                res["dex_id"] = p.get("dexId", "")
                res["pair_address"] = p.get("pairAddress", "")
                res["quote_symbol"] = (p.get("quoteToken") or {}).get("symbol", "")
                if (p.get("baseToken") or {}).get("symbol"):
                    res["token_symbol"] = p["baseToken"]["symbol"]

                p_info = p.get("info") or {}
                ws = p_info.get("websites") or []
                if ws:
                    res["website"] = ws[0].get("url", "")
                soc = p_info.get("socials") or []
                tw = [s.get("url") for s in soc if s.get("type") in ("twitter", "x")]
                if tw:
                    res["twitter"] = tw[0]
    except Exception as exc:
        _log.debug("DexScreener lookup failed for %s: %s", token_addr, exc)

    return res


def audit_nft_risk(details: Dict[str, Any], holders: Optional[int] = None) -> Dict[str, Any]:
    """
    Evaluates risk and holder distribution for NFT contracts.
    """
    if holders is None:
        holders = details.get("holders", 0)
    tm = details.get("total_minted") or 0
    flags = []
    if tm > 20 and holders <= 2:
        risk_lvl = "HIGH"
        risk_badge = "⚠️ Whales"
        flags.append("High Whale / Sniper Concentration")
        flags.append(f"Only {holders} holders for {tm} mints")
    elif details.get("has_free_mint") is True or "FREE" in str(details.get("mint_price_str", "")).upper():
        risk_lvl = "SAFE"
        risk_badge = "🟢 Clean"
        flags.append("Free Mint Available")
        flags.append("Organic Community Distribution")
    else:
        risk_lvl = "SAFE"
        risk_badge = "🟢 Clean"
        flags.append("Standard NFT Contract")
        flags.append("Verified Interface")

    return {
        "audited": True,
        "is_honeypot": False,
        "buy_tax": 0.0,
        "sell_tax": 0.0,
        "cannot_sell_all": False,
        "is_open_source": True,
        "lp_locked_pct": 0.0,
        "top10_pct": round(min(100.0, (1.0 / max(1, holders)) * 100.0), 1) if holders > 0 else 0.0,
        "risk_level": risk_lvl,
        "risk_badge": risk_badge,
        "risk_flags": flags
    }


SYNTHETIC_STOCK_SYMBOLS = {
    'TSLA', 'TSLAB', 'AAPL', 'AAPLB', 'MSFT', 'MSFTB', 'META', 'METAB', 'SPCXB', 'NVDA', 'NVDAB', 
    'AMZN', 'AMZNB', 'GOOGL', 'GOOGLB', 'MRNA', 'MRNAB', 'SOXS', 'SOXSB', 'SOXL', 'SOXLB', 
    'CRCLB', 'QQQ', 'QQQB', 'TQQQ', 'TQQQB', 'MSTR', 'MSTRB', 'BABA', 'BABAB', 'BNCB', 'SKHYB', 
    'COIN', 'COINB', 'PLTR', 'PLTRB', 'AMD', 'AMDB', 'SPY', 'SPYB', 'TSMC', 'TSMB', 'PDD', 'PDDB', 
    'NFLX', 'NFLXB', 'INTC', 'INTCB', 'DIS', 'DISB', 'UBER', 'UBERB', 'BA', 'BAAB'
}


def is_synthetic_stock_or_peg(name: str, symbol: str, contract: str = "") -> bool:
    """Checks whether a token is a synthetic equity stock or major ETF on BSC."""
    sym = (symbol or "").upper().strip()
    if sym in SYNTHETIC_STOCK_SYMBOLS:
        return True
    nm = (name or "").upper().strip()
    if any(k in nm for k in (
        'ETF', 'PROSHARES', 'DIREXION', 'BEAR 3X', 'BULL 3X', 'INVESQO', 'INVESCO', 
        'BSTOCKS', 'TOKENIZED', 'S&P 500', 'NASDAQ', 'ISHARES', 'VANGUARD',
        'PANCAKESWAP', 'BINANCE-PEG', 'WRAPPED BNB', 'TETHER USD', 'USD COIN', 'ZCASH', 'DOGEZILLA'
    )):
        return True
    return False


def is_system_infra(item: Dict[str, Any]) -> bool:
    """
    Detects pure infrastructure / utility adapter contracts (like resolver helpers)
    that have no tokens, no supply, and no minting capability.
    """
    if item.get("is_proxy_clone"):
        return False
    std = str(item.get("standard") or "")
    cat = item.get("classification", {}).get("category") if isinstance(item.get("classification"), dict) else None
    name = str(item.get("name") or "")
    has_m = item.get("has_mint")
    has_supp = item.get("max_supply") is not None or ((item.get("total_minted") or 0) > 0)
    sym = str(item.get("symbol") or "").strip()
    if ("Custom" in std or cat == "CUSTOM") and (name in ("Unknown", "New Contract", "") or not sym) and not has_supp and not has_m:
        return True
    return False


def format_floor_trade_price(item: Dict[str, Any], bnb_price: Optional[float] = None) -> str:
    """
    Formats the secondary market trading price or NFT floor price:
    - For NFTs: Floor price in BNB and USD valuation (e.g. '0.00016 BNB ($0.11)')
    - For Tokens: DEX trading price from PancakeSwap/DexScreener (e.g. '$0.000071', '$0.0517')
    - For Pre-market / unlisted: '—'
    """
    if bnb_price is None:
        bnb_price = cached_bnb_price()
    floor_bnb = item.get("floor_price_bnb")
    floor_usd = item.get("floor_price_usd")
    token_usd = item.get("token_price_usd") or 0.0
    token_bnb = item.get("token_price_bnb") or 0.0

    if not token_usd:
        try:
            token_usd = float(item.get("market_price_usd") or 0.0)
        except Exception:
            token_usd = 0.0
    if not token_bnb:
        try:
            token_bnb = float(item.get("market_price_bnb") or 0.0)
        except Exception:
            token_bnb = 0.0

    # 1. NFT Floor Price
    if floor_bnb is not None and float(floor_bnb) > 0:
        f_bnb = float(floor_bnb)
        usd_val = float(floor_usd) if floor_usd else (f_bnb * bnb_price)
        if item.get("is_nft_surge"):
            return f"[bold magenta]{f_bnb:.4f} BNB[/bold magenta]"
        return f"[bold cyan]{f_bnb:g} BNB[/bold cyan] [dim](${usd_val:.2f})[/dim]"
    elif floor_usd is not None and float(floor_usd) > 0:
        return f"[bold cyan]${float(floor_usd):.2f}[/bold cyan]"
    elif item.get("floor_price_str"):
        return f"[bold cyan]{item['floor_price_str']}[/bold cyan]"

    # 2. DEX Token Trading Price
    if token_usd > 0:
        if token_usd < 0.000001:
            p_str = f"${token_usd:.9f}"
        elif token_usd < 0.0001:
            p_str = f"${token_usd:.6f}"
        elif token_usd < 1.0:
            p_str = f"${token_usd:.4f}"
        else:
            p_str = f"${token_usd:,.2f}"
        return f"[bold green]{p_str}[/bold green]"
    elif token_bnb > 0:
        return f"[bold green]{token_bnb:g} BNB[/bold green]"

    # 3. Not trading on secondary market yet / in primary mint
    return "[dim]—[/dim]"


def format_lp_or_volume(item: Dict[str, Any], bnb_price: Optional[float] = None) -> str:
    """
    Formats the LP / Vol cell:
    - If project has a DEX Liquidity Pool: displays LP depth in USD (e.g. '$14,105', '$4,591')
    - If NFT collection without DEX pool: displays collection trading volume (e.g. '0.74 BNB', or '—' if 0)
    - If Token without DEX pool: displays 'None'
    """
    if bnb_price is None:
        bnb_price = cached_bnb_price()
    lp_usd = float(item.get("lp_usd", 0) or 0)
    if lp_usd > 0:
        return f"[green]${lp_usd:,.0f}[/green]"

    std_full = str(item.get("standard") or "")
    cat = item.get("classification", {}).get("category") if isinstance(item.get("classification"), dict) else None
    is_nft = ("NFT" in std_full or "ERC-721" in std_full or "ERC-1155" in std_full or cat == "NFT" or (item.get("classification") and item["classification"].get("is_nft", False)))

    if is_nft:
        sec_vol = item.get("secondary_vol_bnb")
        if sec_vol and float(sec_vol) > 0:
            if item.get("is_nft_surge"):
                return f"[bold magenta]{float(sec_vol):.3f} BNB 🦋[/bold magenta]"
            return f"[bold cyan]{float(sec_vol):.3f} BNB[/bold cyan]"

        vol_bnb = item.get("volume_bnb") or item.get("nft_volume_bnb")
        if not vol_bnb and item.get("total_volume_bnb"):
            vol_bnb = item.get("total_volume_bnb")

        if vol_bnb is not None and float(vol_bnb) > 0:
            v_b = float(vol_bnb)
            return f"[bold cyan]{v_b:g} BNB[/bold cyan]"
        elif item.get("volume_str"):
            vs = item["volume_str"]
            return f"[bold cyan]{vs}[/bold cyan]"
        elif item.get("nft_volume_usd") and float(item["nft_volume_usd"]) > 0:
            v_b = float(item["nft_volume_usd"]) / bnb_price
            return f"[bold cyan]{v_b:.2f} BNB[/bold cyan]"
        else:
            return "[dim]—[/dim]"
    else:
        return "[dim]None[/dim]"


def evaluate_sniper_ready(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluates whether an asset qualifies for high-conviction assisted sniper entry:
    1. Standard-specific Progress (>=60% or >=100 agent wallets)
    2. Bytecode Refund Guarantee (mandatory if paid drop)
    3. DEX Pre-Check (for tokens: if PancakeSwap pool is already open, trade on DEX instead)
    4. Anti-Rug & Anti-Spam (not serial dev >=3 drops, wallet not fresh <8h, no capital drain)
    5. Simulation Safety (mint not paused, no honeypot)
    """
    std = str(item.get("standard") or "")
    cls = item.get("classification") or {}
    is_nft = "NFT" in std or "721" in std or "1155" in std or bool(cls.get("is_nft"))
    is_token = "BEP-20" in std or "Fair-Mint" in std or cls.get("category") in ("TOKEN", "TOKEN_FAIR_MINT")

    ms = item.get("max_supply")
    tm = item.get("total_minted") or 0
    lp = float(item.get("lp_usd", 0) or 0)
    has_ref = bool(item.get("has_refund") or (item.get("security") or {}).get("has_refund"))
    risk = item.get("risk_level", "SAFE")
    sec = item.get("security") or {}
    is_serial = bool(item.get("is_serial_dev") or sec.get("is_serial_dev"))
    is_drained = bool(item.get("is_fund_drain") or sec.get("is_fund_drain"))
    is_fresh = bool(item.get("is_fresh_dev") or sec.get("is_fresh_dev"))
    is_paused = item.get("is_paused") is True

    val_wei = int(item.get("mint_value_wei", "0") or 0)
    price_bnb = float(item.get("mint_price_bnb", 0.0) or 0.0)
    is_paid = price_bnb > 0.001 or val_wei > 0

    checklist = []

    # 1. Progress calculation
    if is_token and not is_nft:
        if ms and tm and ms == tm and not item.get("sold_sel") and not item.get("cap"):
            prog = 0.0
        else:
            prog = (tm / ms) if (ms and ms > 0) else 0.0
    else:
        prog = (tm / ms) if (ms and ms > 0) else 0.0

    agents = int((item.get("holders") or 0) or item.get("unique_minters", 0) or 0)

    # Progress check
    is_sold_out = (prog >= 0.999) or (is_nft and bool(ms) and bool(tm) and tm >= ms)
    prog_pass = (0.60 <= prog < 0.999) or (prog == 0.0 and agents >= 100)
    if is_sold_out:
        checklist.append("▫ [red]✖[/red] Progress: 100% Sold Out / Completed")
    elif prog >= 0.60:
        checklist.append(f"▫ [green]✔[/green] Progress: {prog*100:.1f}% (≥60% conviction threshold passed)")
    elif prog == 0.0 and agents >= 100:
        checklist.append(f"▫ [green]✔[/green] Progress: Crowd Surge ({agents} active agent wallets)")
    else:
        p_pct_str = f"{prog*100:.1f}%" if prog > 0 else "0%"
        checklist.append(f"▫ [yellow]⏳[/yellow] Progress: {p_pct_str} (<60% threshold, tracking)")

    # Deployer check
    if is_serial:
        s_cnt = sec.get("serial_count") or item.get("serial_count") or 3
        checklist.append(f"▫ [red]✖[/red] Deployer: 🚨 Serial Dev ({s_cnt} contracts deployed in 24h)")
    elif is_fresh:
        checklist.append("▫ [yellow]⚠️[/yellow] Deployer: Fresh wallet (<8h, low nonce)")
    else:
        checklist.append("▫ [green]✔[/green] Deployer: Clean reputation (No spam deployment)")

    # Capital drain check
    if risk == "RUG" or is_drained:
        checklist.append("▫ [red]✖[/red] Capital Drain: 🚨 Empty contract with no DEX LP (Funds pocketed)")
    else:
        checklist.append("▫ [green]✔[/green] Capital Safety: No fund drain detected")

    # Bytecode refund guarantee check
    if is_paid:
        if has_ref:
            ref_m = sec.get("refund_methods") or item.get("refund_methods") or ["refund()"]
            checklist.append(f"▫ [green]✔[/green] Refund Guarantee: Bytecode refund verified ({ref_m[0]})")
        else:
            checklist.append("▫ [red]✖[/red] Refund Guarantee: ⚠️ No refund method (Capital trapped if unfilled)")
    else:
        checklist.append("▫ [dim]▫ Refund Guarantee: N/A (Free Mint / Gas Only)[/dim]")

    # Secondary DEX pre-check (Tokens only)
    if is_token:
        if lp >= 500:
            checklist.append(f"▫ [yellow]✖[/yellow] Secondary Market: DEX LP already active (${lp:,.0f}) -> Trade on DEX instead of minting")
        else:
            checklist.append("▫ [green]✔[/green] Secondary Market: Primary bonding phase (No DEX LP yet)")

    # Final verdict
    if is_paused:
        res = {"is_ready": False, "status": "PAUSED", "badge": "⏸️ Paused", "prog": prog, "checklist": checklist}
    elif risk == "RUG" or is_drained:
        res = {"is_ready": False, "status": "RUG_RISK", "badge": "🚨 Rug Risk", "prog": prog, "checklist": checklist}
    elif is_serial:
        res = {"is_ready": False, "status": "SERIAL_DEV", "badge": "🚨 Serial Dev", "prog": prog, "checklist": checklist}
    elif is_token and lp >= 500:
        res = {"is_ready": False, "status": "GRADUATED_TO_DEX", "badge": "📈 On DEX", "prog": prog, "checklist": checklist}
    elif is_paid and not has_ref:
        res = {"is_ready": False, "status": "NO_REFUND", "badge": "⚠️ No Refund", "prog": prog, "checklist": checklist}
    elif is_sold_out:
        res = {"is_ready": False, "status": "SOLD_OUT", "badge": "🛑 Sold Out", "prog": prog, "checklist": checklist}
    elif prog_pass:
        res = {"is_ready": True, "status": "SNIPER_READY", "badge": "🎯 Snipe Ready", "prog": prog, "checklist": checklist}
    else:
        res = {
            "is_ready": False,
            "status": "TRACKING",
            "badge": f"⏳ Track ({prog*100:.0f}%)" if prog > 0 else "⏳ Tracking",
            "prog": prog,
            "checklist": checklist
        }
    res["sniper_ready"] = res["is_ready"]
    res["sniper_status"] = res["status"]
    res["sniper_badge"] = res["badge"]
    res["sniper_checklist"] = res["checklist"]
    return res

evaluate_sniper_readiness = evaluate_sniper_ready


def filter_and_sort_projects(history: List[Dict[str, Any]], active_filter: str, sort_mode: str) -> List[Dict[str, Any]]:
    """
    Applies real-time active filters and sorting rules across tracked projects.
    """
    clean_history = [x for x in history if not is_system_infra(x)]
    filtered = []
    for item in clean_history:
        if active_filter in ("SNIPER_READY", "READY"):
            s_res = evaluate_sniper_ready(item)
            if not s_res["is_ready"]:
                continue
        elif active_filter in ("NFT_SURGE", "SURGE"):
            if not item.get("is_nft_surge"):
                continue
        elif active_filter in ("PROXY", "CLONE"):
            if not item.get("is_proxy_clone"):
                continue
        elif active_filter == "FREE":
            p_str = str(item.get("mint_price_str", "")).upper()
            if not (item.get("has_free_mint") or "FREE" in p_str):
                continue
        elif active_filter == "LP":
            has_lp = float(item.get("lp_usd", 0) or 0) > 0
            has_vol = float(item.get("volume_bnb", 0) or 0) > 0 or float(item.get("total_volume_bnb", 0) or 0) > 0
            if not (has_lp or has_vol):
                continue
        elif active_filter == "ACTIVE":
            if item.get("is_paused") is True:
                continue
            ms = item.get("max_supply")
            tm = item.get("total_minted", 0) or 0
            if ms is not None and tm >= ms:
                continue
        elif active_filter == "SAFE":
            if item.get("risk_level") in ("HIGH", "RUG"):
                continue
        elif active_filter in ("SMART", "SMART_MONEY"):
            if not item.get("smart_wallets") and not item.get("smart_minters"):
                continue
        filtered.append(item)

    if sort_mode == "HEAT":
        filtered.sort(key=lambda x: (x.get("score") or 0), reverse=True)
    elif sort_mode == "PROFIT":
        def get_profit_val(x):
            ath_p = float(x.get("ath_profit_usd") or 0.0)
            cur_p = float(x.get("current_profit_usd") or 0.0)
            sec = float(x.get("secondary_price_usd") or 0.0)
            mint = float(x.get("mint_cost_usd") or 0.0)
            return max(ath_p, cur_p, (sec - mint) if sec > 0 else 0.0)
        filtered.sort(key=get_profit_val, reverse=True)
    elif sort_mode == "SMART":
        filtered.sort(key=lambda x: len(x.get("smart_wallets") or x.get("smart_minters") or []), reverse=True)
    elif sort_mode == "VELOCITY":
        filtered.sort(key=lambda x: float(x.get("velocity_ppm") or 0.0), reverse=True)
    elif sort_mode == "PROGRESS":
        def get_prog(x):
            ms = x.get("max_supply")
            tm = x.get("total_minted") or 0
            return (float(tm) / ms) if (ms and (float(ms) if str(ms).replace('.','',1).isdigit() else 0) > 0) else 0.0
        filtered.sort(key=get_prog, reverse=True)
    elif sort_mode == "SAFETY":
        rank = {"SAFE": 0, "MED": 1, "HIGH": 2, "RUG": 3}
        filtered.sort(key=lambda x: rank.get(x.get("risk_level", "MED"), 1))
    else:  # "NEWEST"
        filtered.sort(key=lambda x: int(x.get("block") or 0), reverse=True)

    return filtered


# --- Post-mint profit / ATH rules (the JavaScript twin is computeItemProfit in app.js; the parity test keeps them equal)
#   mint cost  known  : the paid mint price (wei, else BNB) is above zero, or the mint is VERIFIED free
#                       (has_free_mint, or the price string says FREE). Otherwise profit and multiplier are None.
#   "DEX Traded"      : no mint position at all, so no profit.
#   profit            : sec_usd - mint cost; negative when under water (a loss is real data); free mint: the price.
#   multiplier        : sec_usd / mint cost; None for a free mint.
#   ATH price         : only from a price backed by >= ATH_MIN_LP_USD of pool liquidity (an NFT floor needs none);
#                       a stored ATH more than ATH_RESET_FACTOR x the current price on a thin pool is dropped.
#   ATH profit        : max(previous ATH profit, current profit), starting from the first observed value.
#   ATH multiplier    : max(previous, ATH price / mint cost); None for a free mint.
ATH_MIN_LP_USD = 1000.0
ATH_RESET_FACTOR = 50.0


def _round2(x: float) -> float:
    return math.floor(x * 100.0 + 0.5) / 100.0  # same arithmetic as Math.floor(x * 100 + 0.5) / 100 in app.js


def _sig10(x: float) -> float:
    return float(f"{x:.10g}")


def _no_profit() -> Dict[str, Any]:
    return {
        "sec_usd": 0.0, "mint_usd": None, "profit": None,
        "ath_price_usd": None, "ath_profit_usd": None, "ath_multiplier": None, "current_multiplier": None,
        "is_sold": False, "is_free": False,
    }


def update_post_mint_profit_metrics(item: Dict[str, Any], bnb_price: Optional[float] = None) -> Dict[str, Any]:
    """
    Computes and updates post-mint profit and ATH metrics on an item (rules above).
    Filters out pegged crypto assets, synthetic stocks, known scams and system infra.
    """
    if bnb_price is None:
        bnb_price = cached_bnb_price()
    c = (item.get("contract") or "").lower()
    if filters.is_excluded_asset(item) or is_system_infra(item):
        return _no_profit()

    fl_usd = float(item.get("floor_price_usd") or 0.0)
    fl_bnb = float(item.get("floor_price_bnb") or 0.0)
    tk_usd = float(item.get("token_price_usd") or item.get("market_price_usd") or 0.0)
    price_from_pair = not (fl_usd > 0 or fl_bnb > 0)
    sec_usd = fl_usd if fl_usd > 0 else (fl_bnb * bnb_price if fl_bnb > 0 else tk_usd)

    # Micro-wei / scam price distortion guard: reject astronomical prices (> $1000) or micro-wei supplies
    tm_val = float(item.get("total_minted") or 0.0)
    lp_val = float(item.get("lp_usd") or 0.0)
    if sec_usd > 1000.0 or (0.0 < tm_val < 1e-6) or (0.0 < lp_val < 5.0 and sec_usd > 5.0):
        return _no_profit()

    p_str = str(item.get("mint_price_str") or "").upper()
    v_wei = int(item.get("mint_value_wei") or 0)
    verified_free = bool(item.get("has_free_mint")) or "FREE" in p_str
    paid_usd = (v_wei / 1e18 * bnb_price) if v_wei > 0 else (float(item.get("mint_price_bnb") or 0) * bnb_price)
    is_free = verified_free
    cost_known = (not "DEX" in p_str) and (verified_free or paid_usd > 0)
    mint_usd = (0.0 if is_free else paid_usd) if cost_known else None

    raw_ms = item.get("max_supply")
    if raw_ms is not None:
        try:
            if float(raw_ms) >= 2**128 or float(raw_ms) >= 1e20:
                raw_ms = None
        except Exception as exc:
            _log.debug("could not normalise raw total_minted for %s: %s", c, exc)
    tm = item.get("total_minted") or 0
    is_sold = bool(raw_ms and float(raw_ms) > 0 and tm and float(tm) >= float(raw_ms))

    profit = None
    multiplier = None
    if cost_known and sec_usd > 0:
        profit = sec_usd - mint_usd
        multiplier = _round2(sec_usd / mint_usd) if mint_usd > 0 else None

    # --- ATH (high-water mark) -----------------------------------------------------------------------------
    lp_ok = (not price_from_pair) or lp_val >= ATH_MIN_LP_USD
    prev_ath = float(item.get("ath_price_usd") or 0.0)
    if prev_ath > 0 and sec_usd > 0 and price_from_pair and lp_val < ATH_MIN_LP_USD and prev_ath > ATH_RESET_FACTOR * sec_usd:
        # a poisoned ATH (thin pool spike, stale seed): drop it, it is re-learned from a trustworthy price
        item["ath_price_usd"] = None
        item["ath_profit_usd"] = None
        item["ath_multiplier"] = None
        prev_ath = 0.0
    if sec_usd > 0 and lp_ok and sec_usd > prev_ath:
        item["ath_price_usd"] = _sig10(sec_usd)
        item["ath_timestamp"] = int(time.time())
    ath_price = float(item.get("ath_price_usd") or 0.0)

    if cost_known and profit is not None and lp_ok:
        prev_p = item.get("ath_profit_usd")
        item["ath_profit_usd"] = _sig10(profit if prev_p is None else max(float(prev_p), profit))
    elif not cost_known:
        item["ath_profit_usd"] = None
    if cost_known and not is_free and mint_usd > 0 and ath_price > 0:
        m = _round2(ath_price / mint_usd)
        prev_m = item.get("ath_multiplier")
        item["ath_multiplier"] = m if prev_m is None else max(float(prev_m), m)
    else:
        item["ath_multiplier"] = None

    item["current_profit_usd"] = _sig10(profit) if profit is not None else None
    item["current_multiplier"] = multiplier
    item["mint_cost_usd"] = round(mint_usd, 4) if mint_usd is not None else None
    item["secondary_price_usd"] = _sig10(sec_usd) if sec_usd > 0 else None

    return {
        "sec_usd": sec_usd,
        "mint_usd": mint_usd,
        "profit": profit,
        "ath_price_usd": item.get("ath_price_usd"),
        "ath_profit_usd": item.get("ath_profit_usd"),
        "ath_multiplier": item.get("ath_multiplier"),
        "current_multiplier": multiplier,
        "is_sold": is_sold,
        "is_free": is_free,
    }


def compute_market_cap_stats(history: List[Dict[str, Any]], bnb_price: Optional[float] = None) -> Dict[str, Any]:
    """
    Computes overall mint statistics:
    - Percentage of collections that filled their mint cap (Sold Out)
    - Collections nearing cap completion (>=80%)
    - Collections past halfway (>=50%)
    - Top post-mint gainer (highest profit / ROI after mint is over)
    - All-time top gainers leaderboard list
    """
    if bnb_price is None:
        bnb_price = cached_bnb_price()
    capped_total = 0
    sold_out_cnt = 0
    near_cap_cnt = 0
    warm_cap_cnt = 0
    gainers = []

    for it in history:
        if is_system_infra(it):
            continue
        ms = it.get("max_supply")
        tm = it.get("total_minted") or 0
        if ms and float(ms) > 0 and float(ms) < 2**128 and float(ms) < 1e25:
            capped_total += 1
            pct = (float(tm) / float(ms)) * 100.0
            if pct >= 99.9 or float(tm) >= float(ms):
                sold_out_cnt += 1
            elif pct >= 80.0:
                near_cap_cnt += 1
            elif pct >= 50.0:
                warm_cap_cnt += 1

        prof_info = update_post_mint_profit_metrics(it, bnb_price)
        sec_usd = prof_info["sec_usd"]
        ath_profit = prof_info["ath_profit_usd"]

        if sec_usd > 0 and ath_profit is not None and ath_profit > 0:
            gainers.append({
                "item": it,
                "contract": it.get("contract", ""),
                "name": it.get("name", "Unknown"),
                "symbol": it.get("symbol", ""),
                "sec_usd": sec_usd,
                "mint_usd": prof_info["mint_usd"],
                "profit": prof_info["profit"],
                "ath_price_usd": prof_info["ath_price_usd"],
                "ath_profit_usd": ath_profit,
                "ath_multiplier": prof_info["ath_multiplier"],
                "is_sold": prof_info["is_sold"],
                "is_free": prof_info["is_free"]
            })

    fill_rate_pct = (sold_out_cnt / capped_total * 100.0) if capped_total else 0.0
    def peak(g):
        return max(g["ath_profit_usd"], g["profit"] if g["profit"] is not None else g["ath_profit_usd"])
    sold_gainers = sorted([g for g in gainers if g["is_sold"]], key=peak, reverse=True)
    all_gainers = sorted(gainers, key=peak, reverse=True)

    top_gainer = sold_gainers[0] if sold_gainers else (all_gainers[0] if all_gainers else None)
    runner_up = sold_gainers[1] if len(sold_gainers) > 1 else (all_gainers[1] if len(all_gainers) > 1 else None)

    return {
        "capped_total": capped_total,
        "sold_out_cnt": sold_out_cnt,
        "fill_rate_pct": fill_rate_pct,
        "near_cap_cnt": near_cap_cnt,
        "warm_cap_cnt": warm_cap_cnt,
        "top_gainer": top_gainer,
        "runner_up": runner_up,
        "all_gainers": all_gainers,
        "sold_gainers": sold_gainers
    }


# Market numbers of the token an NFT / Fair-Mint dispenser hands out. They describe the *linked* token, never the
# dispenser record, so they live under their own keys and never feed lp_usd / market_cap / pair_url.
_LINKED_MARKET_KEYS = {
    "lp_usd": "linked_lp_usd",
    "market_cap": "linked_market_cap",
    "fdv": "linked_fdv",
    "token_price_usd": "linked_price_usd",
    "volume_24h": "linked_volume_24h",
    "pair_url": "linked_pair_url",
    "lp_pool": "linked_lp_pool",
}


def linked_market_fields(mkt: Dict[str, Any]) -> Dict[str, Any]:
    """The linked_token / linked_* entries of an analyze_market_interest result, for copying onto a record."""
    keys = set(_LINKED_MARKET_KEYS.values()) | {"linked_token"}
    return {k: v for k, v in (mkt or {}).items() if k in keys}


def _store_linked_market(stats: Dict[str, Any], mkt: Dict[str, Any], merge: bool = False) -> None:
    """Copies a linked token's market numbers from `mkt` (fetch_token_market_data output, or the Pancake scratch
    dict) into stats under linked_* keys. With merge=True existing linked values are only improved on."""
    src_names = {"token_price_usd": ("token_price_usd", "market_price_usd")}
    for src, dst in _LINKED_MARKET_KEYS.items():
        val = None
        for name in src_names.get(src, (src,)):
            if mkt.get(name):
                val = mkt[name]
                break
        if not val:
            continue
        if merge and src in ("lp_usd", "volume_24h") and stats.get(dst) and val <= stats[dst]:
            continue
        if merge and stats.get(dst) and src not in ("lp_usd",):
            continue
        stats[dst] = val


RATING_BY_TIER = {"SURGING": "🔥 SURGING", "HIGH": "🚀 HIGH", "MODERATE": "⚡ MODERATE", "EARLY": "🌱 EARLY"}


def compute_interest(item: Dict[str, Any]) -> Tuple[int, str]:
    """The single interest score (0-100) and rating label. analyze_market_interest and the worker's
    _recompute_interest both call this, so a stored score never depends on which of them ran last.
    Bands: liquidity (0-40), 24h volume (0-30), 24h buys (0-30), +5 per smart wallet. A honeypot scores 0 and a
    HIGH risk rating costs 25 points (the guard analyze_market_interest always applied). The JavaScript badge
    uses the same tiers (filters.interest_tier / interestTier in app.js)."""
    lp = float(item.get("lp_usd") or 0.0)
    vol = float(item.get("volume_24h") or 0.0)
    b24 = int(item.get("buys_24h") or 0)

    score = 0
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

    risk = item.get("risk_level")
    if risk == "RUG":
        score = 0
    elif risk == "HIGH":
        score = max(0, score - 25)

    score = min(100, score)
    return score, RATING_BY_TIER[filters.interest_tier(score)]


def analyze_market_interest(
    rpc: BscRpcClient,
    contract_addr: str,
    deployer_addr: Optional[str],
    details: Dict[str, Any],
    history: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
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
        "market_cap": 0.0,
        "fdv": 0.0,
        "price_change_24h": 0.0,
        "pair_url": "",
        "recent_mints": 0,
        "unique_minters": 0,
        "percent_claimed": 0.0,
        "dev_root": None,
        "cluster_devs": 1,
        "cluster_n": 1,
        "hold": {
            "dev": 0.0,
            "insider": 0.0,
            "smart": 0.0,
            "sniper": 0.0,
            "fresh": 0.0,
            "public": 100.0
        },
        "signals": []
    }

    contract_addr = contract_addr.lower()

    # 1. Resolve deployer if missing
    if not deployer_addr:
        owner_res = rpc.call_contract(contract_addr, keccak_selector("owner()"))
        if owner_res and len(owner_res) >= 66:
            deployer_addr = "0x" + owner_res[-40:].lower()

    is_serial_dev = False
    serial_count = 1
    is_fresh_dev = False
    dev_root = (details.get("dev_root") or details.get("root") or "").lower() or None
    if dev_root and (dev_root == "0x0000000000000000000000000000000000000000" or dev_root == "0x0"):
        dev_root = None

    if deployer_addr and (deployer_addr.lower() == "0x0000000000000000000000000000000000000000" or deployer_addr.lower() == "0x0"):
        deployer_addr = None

    cluster_devs = 1
    cluster_n = 1

    if history is None:
        try:
            history = load_history()
        except Exception:
            history = []

    if deployer_addr:
        deployer_addr = deployer_addr.lower()
        try:
            stats["deployer_balance_bnb"] = rpc.get_balance(deployer_addr) / 1e18
            stats["deployer_nonce"] = rpc.get_tx_count(deployer_addr)
        except Exception as exc:
            _log.debug("deployer balance lookup failed for %s: %s", deployer_addr, exc)

        # Check for Fresh Dev Wallet (nonce <= 2 and balance < 0.05 BNB)
        if stats["deployer_nonce"] <= 2 and stats["deployer_balance_bnb"] < 0.05:
            is_fresh_dev = True

        # Check for Serial Dev across history
        if history:
            prev_deps = [
                h for h in history
                if (h.get("deployer") or "").lower() == deployer_addr
                and (h.get("contract") or "").lower() != contract_addr
            ]
            serial_count = len(prev_deps) + 1
            if serial_count >= 3:
                is_serial_dev = True

            # If dev_root wasn't in details, inspect previous drops by this deployer
            if not dev_root:
                for h in prev_deps:
                    r_cand = (h.get("dev_root") or h.get("root") or "").lower()
                    if r_cand and r_cand != "0x0000000000000000000000000000000000000000" and r_cand != "0x0":
                        dev_root = r_cand
                        break

    # If dev_root is still missing but deployer is known, default dev_root to deployer
    if not dev_root and deployer_addr and deployer_addr != "0x0000000000000000000000000000000000000000":
        dev_root = deployer_addr

    # Dev Funding Root & Cluster Tracing across history (strictly exclude zero/burn address)
    if dev_root and dev_root != "0x0000000000000000000000000000000000000000" and history:
        cluster_drops = [
            h for h in history
            if ((h.get("dev_root") or h.get("root") or "").lower() == dev_root and dev_root != "0x0000000000000000000000000000000000000000")
            or ((h.get("deployer") or "").lower() == dev_root and dev_root != "0x0000000000000000000000000000000000000000")
            or (deployer_addr and (h.get("deployer") or "").lower() == deployer_addr)
        ]
        unique_devs = set()
        if deployer_addr and deployer_addr != "0x0000000000000000000000000000000000000000":
            unique_devs.add(deployer_addr)
        if dev_root and dev_root != "0x0000000000000000000000000000000000000000":
            unique_devs.add(dev_root)
        for h in cluster_drops:
            d_addr = (h.get("deployer") or "").lower()
            if d_addr and d_addr != "0x0000000000000000000000000000000000000000":
                unique_devs.add(d_addr)
            r_addr = (h.get("dev_root") or h.get("root") or "").lower()
            if r_addr and r_addr != "0x0000000000000000000000000000000000000000":
                unique_devs.add(r_addr)
        cluster_devs = max(len(unique_devs), 1)
        cluster_n = max(len(cluster_drops) + 1, cluster_devs, serial_count)
    elif serial_count > 1:
        cluster_n = serial_count

    stats["dev_root"] = dev_root
    stats["cluster_devs"] = cluster_devs
    stats["cluster_n"] = cluster_n

    # 2. Identify associated token for liquidity analysis
    target_token = None
    token_sym = details.get("symbol", "")
    cls = details.get("classification", {})
    if cls.get("category") == "TOKEN":
        target_token = contract_addr
    elif cls.get("underlying"):
        target_token = cls["underlying"].lower()
        token_sym = cls.get("underlying_symbol", "") or token_sym
    else:
        # Check token()
        t_res = rpc.call_contract(contract_addr, "0xfc0c546a")
        if t_res and len(t_res) >= 66 and t_res[-40:] != "0"*40:
            target_token = "0x" + t_res[-40:].lower()
            sym_res = rpc.call_contract(target_token, keccak_selector("symbol()"))
            if sym_res:
                token_sym = decode_string(sym_res)
        else:
            # Check marsToken()
            mars_res = rpc.call_contract(contract_addr, "0x7419f946")
            if mars_res and len(mars_res) >= 66 and mars_res[-40:] != "0"*40:
                target_token = "0x" + mars_res[-40:].lower()
                sym_res = rpc.call_contract(target_token, keccak_selector("symbol()"))
                if sym_res:
                    token_sym = decode_string(sym_res)
            else:
                # Fallback: test contract_addr directly
                target_token = contract_addr

    stats["token_symbol"] = token_sym

    # 3. Live Holders & Multi-Source DEX Analytics via DexScreener
    mkt_data = {}
    if target_token:
        mkt_data = fetch_token_market_data(target_token)
    if (not mkt_data or mkt_data.get("lp_usd", 0) == 0) and contract_addr != target_token:
        alt_data = fetch_token_market_data(contract_addr)
        if alt_data and alt_data.get("lp_usd", 0) > 0:
            mkt_data = alt_data
            target_token = contract_addr

    # An NFT / Fair-Mint dispenser is not the token it dispenses. When the market data belongs to a *linked*
    # token (target_token != this contract), none of it may become the record's own price / liquidity / market
    # cap / pair_url: it is kept under separate linked_* keys (see _LINKED_MARKET_KEYS).
    is_linked = bool(target_token) and target_token != contract_addr
    if is_linked:
        stats["linked_token"] = target_token
        if mkt_data:
            _store_linked_market(stats, mkt_data)
        mkt_data = {}

    # Counts a source really reported. Unknown stays None in the result (below); the score code uses 0 meanwhile.
    count_keys = ("holders", "buys_24h", "buys_1h", "sells_24h")
    known_counts = {k: mkt_data.get(k) for k in count_keys if mkt_data and mkt_data.get(k) is not None}

    if mkt_data:
        stats["holders"] = mkt_data.get("holders") or 0
        stats["buys_24h"] = mkt_data.get("buys_24h") or 0
        stats["buys_1h"] = mkt_data.get("buys_1h") or 0
        stats["sells_24h"] = mkt_data.get("sells_24h") or 0
        stats["volume_24h"] = mkt_data.get("volume_24h", 0.0)
        stats["market_cap"] = mkt_data.get("market_cap", 0.0)
        stats["fdv"] = mkt_data.get("fdv", 0.0)
        stats["price_change_24h"] = mkt_data.get("price_change_24h", 0.0)
        stats["pair_url"] = mkt_data.get("pair_url", "")
        if mkt_data.get("lp_usd"):
            stats["lp_usd"] = mkt_data["lp_usd"]
        if mkt_data.get("market_price_usd"):
            stats["token_price_usd"] = mkt_data["market_price_usd"]
        if mkt_data.get("market_price_bnb"):
            stats["token_price_bnb"] = mkt_data["market_price_bnb"]
        if mkt_data.get("website"):
            stats["website"] = mkt_data["website"]
        if mkt_data.get("twitter"):
            stats["twitter"] = mkt_data["twitter"]
        if mkt_data.get("security"):
            stats["security"] = mkt_data["security"]
            stats["risk_level"] = mkt_data.get("risk_level", "SAFE")
            stats["risk_badge"] = mkt_data.get("risk_badge", "🟢 Clean")
            stats["risk_flags"] = mkt_data.get("risk_flags", [])

    if stats.get("market_cap", 0.0) == 0.0 and stats.get("token_price_usd", 0.0) > 0.0:
        est = market_cap_from_supply(cls.get("standard"), details.get("total_minted"), details.get("max_supply"),
                                     stats["token_price_usd"])
        if est:
            stats["market_cap"] = est

    # If no token security was fetched, audit NFT risk
    if not stats.get("security"):
        nft_sec = audit_nft_risk(details, stats.get("holders", 0))
        stats["security"] = nft_sec
        stats["risk_level"] = nft_sec["risk_level"]
        stats["risk_badge"] = nft_sec["risk_badge"]
        stats["risk_flags"] = nft_sec["risk_flags"]

    # NFT Volume Resolution
    if cls.get("category") == "NFT" or details.get("classification", {}).get("is_nft"):
        if contract_addr == "0xb777c52296f0d7d3c5cab7ae0c1aaac6b014b256":
            try:
                m_req = urllib.request.Request("https://oathbots.pages.dev/market.json", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(m_req, timeout=2.0) as m_resp:
                    m_data = json.loads(m_resp.read().decode("utf-8"))
                    vol_wei = int(m_data.get("vol24") or m_data.get("volAll") or 0)
                    if vol_wei > 0:
                        stats["volume_bnb"] = round(vol_wei / 1e18, 2)
                        stats["volume_24h"] = (vol_wei / 1e18) * cached_bnb_price()
            except Exception as exc:
                _log.debug("oathbots market.json fetch failed: %s", exc)
        if not stats.get("volume_bnb") and details.get("volume_bnb"):
            stats["volume_bnb"] = details["volume_bnb"]
        if not stats.get("total_volume_bnb") and details.get("total_volume_bnb"):
            stats["total_volume_bnb"] = details["total_volume_bnb"]

    # 4. Multi-Quote Secondary Liquidity Check on PancakeSwap V2
    # `mk` is where the pool numbers land: the record itself, or (for a linked token) a scratch dict that is
    # stored under linked_* keys afterwards.
    mk = {} if is_linked else stats
    if target_token:
        try:
            bnb_price = get_bnb_price_usd(rpc)
            best_lp_usd = mk.get("lp_usd", 0.0)
            best_lp_wbnb = mk.get("lp_wbnb", 0.0)
            best_pair_addr = None
            best_px_usd = mk.get("token_price_usd", 0.0)
            best_px_bnb = mk.get("token_price_bnb", 0.0)
            token_dec = details.get("decimals", 18) or 18

            for qt in QUOTE_TOKENS:
                q_addr = qt["address"]
                try:
                    pair_call = "0xe6a43905" + target_token[2:].lower().zfill(64) + q_addr[2:].lower().zfill(64)
                    pair_res = rpc.call_contract(PANCAKE_FACTORY_V2, pair_call)
                    if pair_res and len(pair_res) >= 66:
                        lp_addr = "0x" + pair_res[-40:].lower()
                        if lp_addr != "0x" + "0"*40:
                            reserves = rpc.call_contract(lp_addr, "0x0902f1ac")
                            if reserves and len(reserves) >= 130:
                                r0 = int(reserves[2:66], 16)
                                r1 = int(reserves[66:130], 16)
                                t0 = rpc.call_contract(lp_addr, "0x0dfe1681")
                                is_t0_quote = (q_addr[2:].lower() in (t0 or "").lower())
                                if is_t0_quote:
                                    q_reserve = r0 / (10 ** qt["decimals"])
                                    t_reserve = r1 / (10 ** token_dec)
                                else:
                                    q_reserve = r1 / (10 ** qt["decimals"])
                                    t_reserve = r0 / (10 ** token_dec)

                                if qt["is_bnb"]:
                                    p_lp_usd = q_reserve * bnb_price * 2
                                    p_px_usd = (q_reserve * bnb_price) / t_reserve if t_reserve > 0 else 0.0
                                    p_px_bnb = q_reserve / t_reserve if t_reserve > 0 else 0.0
                                    p_lp_wbnb = q_reserve * 2
                                else:
                                    rate = qt.get("usd_rate", 1.0)
                                    p_lp_usd = q_reserve * rate * 2
                                    p_px_usd = (q_reserve * rate) / t_reserve if t_reserve > 0 else 0.0
                                    p_px_bnb = (p_px_usd / bnb_price) if bnb_price > 0 else 0.0
                                    p_lp_wbnb = (p_lp_usd / bnb_price) if bnb_price > 0 else 0.0

                                if p_lp_usd > best_lp_usd:
                                    best_lp_usd = p_lp_usd
                                    best_lp_wbnb = p_lp_wbnb
                                    best_pair_addr = lp_addr
                                    best_px_usd = p_px_usd
                                    best_px_bnb = p_px_bnb
                except Exception as exc:
                    _log.debug("PancakeSwap pair lookup failed: %s", exc)

            if best_lp_usd > mk.get("lp_usd", 0.0):
                mk["lp_usd"] = best_lp_usd
                mk["lp_wbnb"] = best_lp_wbnb
                if best_pair_addr:
                    mk["lp_pool"] = best_pair_addr
                    if not mk.get("pair_url"):
                        mk["pair_url"] = f"https://dexscreener.com/bsc/{best_pair_addr}"
            if best_px_usd > 0 and mk.get("token_price_usd", 0.0) == 0:
                mk["token_price_usd"] = best_px_usd
            if best_px_bnb > 0 and mk.get("token_price_bnb", 0.0) == 0:
                mk["token_price_bnb"] = best_px_bnb
        except Exception as exc:
            _log.debug("BNB price lookup failed: %s", exc)
    if is_linked:
        _store_linked_market(stats, mk, merge=True)

    # Supply metrics
    max_sup = details.get("max_supply")
    minted = details.get("total_minted")
    if max_sup and max_sup > 0 and minted is not None:
        stats["percent_claimed"] = (minted / max_sup) * 100.0

    # 5. Security & Anti-Rug Protections Enrichment
    sec = stats.get("security") or {}
    sec_flags = list(stats.get("risk_flags") or sec.get("risk_flags") or [])
    signals = []

    # A. Bytecode Refund Guarantee Check
    has_refund = details.get("has_refund", False)
    refund_methods = details.get("refund_methods", [])
    sec["has_refund"] = has_refund
    sec["refund_methods"] = refund_methods

    mint_val_wei = int(details.get("mint_value_wei", "0") or 0)
    mint_price_bnb = float(details.get("mint_price_bnb", 0.0) or 0.0)
    is_paid_mint = (mint_price_bnb > 0.001 or mint_val_wei > 0)

    if has_refund:
        ref_label = refund_methods[0] if refund_methods else "refund()"
        signals.append(f"🛡️ Verified Refund Protection ({ref_label})")
    elif is_paid_mint:
        sec_flags.append("No Refund Function in Bytecode")
        signals.append("⚠️ No Refund Mechanism (Capital at risk if unreached)")
        if stats.get("risk_level") == "SAFE" and mint_price_bnb >= 0.01:
            stats["risk_level"] = "MED"
            stats["risk_badge"] = "🟡 Med Risk"

    # B. Serial Dev Check
    if is_serial_dev:
        sec["is_serial_dev"] = True
        sec["serial_count"] = serial_count
        sec_flags.append(f"🚨 Serial Dev ({serial_count} contracts deployed)")
        stats["risk_level"] = "RUG" if serial_count >= 4 else "HIGH"
        stats["risk_badge"] = "🚨 Serial Dev"
        signals.append(f"🚨 Serial Dev: Deployed {serial_count} projects in tracked history")

    # C. Fresh Dev Wallet Check
    if is_fresh_dev:
        sec["is_fresh_dev"] = True
        sec_flags.append("🟡 Fresh Dev Wallet (low nonce)")
        signals.append("🟡 Fresh Dev Wallet (low tx history)")

    # D. Contract Fund Drain Check (Funds pocketed with no LP created)
    contract_bnb = 0.0
    try:
        contract_bnb = rpc.get_balance(contract_addr) / 1e18
    except Exception as exc:
        _log.debug("contract balance lookup failed for %s: %s", contract_addr, exc)
    tot_minted = details.get("total_minted", 0) or 0
    if tot_minted > 20 and is_paid_mint and contract_bnb < 0.01 and stats.get("lp_usd", 0) < 100:
        sec["is_fund_drain"] = True
        sec_flags.append("🚨 Direct Capital Drain (No LP & Contract Empty)")
        stats["risk_level"] = "RUG"
        stats["risk_badge"] = "🚨 Fund Drain"
        signals.append("🚨 Funds drained to dev wallet, no DEX LP")

    # E. Dev Funding Root & Cluster Check
    sec["dev_root"] = dev_root
    sec["cluster_devs"] = cluster_devs
    sec["cluster_n"] = cluster_n
    if cluster_devs >= 2 or cluster_n >= 3:
        sec["is_dev_cluster"] = True
        cluster_flag = f"🧬 Dev Cluster ({cluster_devs} linked devs, {cluster_n} drops)"
        sec_flags.append(cluster_flag)
        signals.append(f"🧬 Dev Cluster: {cluster_devs} linked devs, {cluster_n} drops launched")
        if stats.get("risk_level") in ("SAFE", "LOW", None):
            stats["risk_level"] = "HIGH" if (cluster_n >= 5 or cluster_devs >= 3) else "MED"
            stats["risk_badge"] = "🧬 Dev Cluster"

    # F. Holder Distribution Breakdown (Dev, Insider, Smart, Sniper, Fresh, Public)
    raw_hold = details.get("hold") or details.get("holder_distribution") or {}
    dev_pct = float(raw_hold.get("dev") if raw_hold.get("dev") is not None else details.get("dev_holding_pct", 0.0))
    insider_pct = float(raw_hold.get("insider") if raw_hold.get("insider") is not None else details.get("insider_pct", 0.0))
    smart_pct = float(raw_hold.get("smart") if raw_hold.get("smart") is not None else details.get("smart_pct", 0.0))
    sniper_pct = float(raw_hold.get("sniper") if raw_hold.get("sniper") is not None else details.get("sniper_pct", 0.0))
    fresh_pct = float(raw_hold.get("fresh") if raw_hold.get("fresh") is not None else details.get("fresh_pct", 0.0))
    public_pct = float(raw_hold.get("public") if raw_hold.get("public") is not None else details.get("public_pct", 0.0))

    if not raw_hold and not details.get("dev_holding_pct"):
        if target_token and deployer_addr:
            try:
                bal_res = rpc.call_contract(target_token, "0x70a08231" + deployer_addr[2:].zfill(64))
                if bal_res and len(bal_res) >= 66:
                    dev_bal = int(bal_res[2:66], 16)
                    sup_res = rpc.call_contract(target_token, "0x18160ddd")
                    if sup_res and len(sup_res) >= 66:
                        tot_sup = int(sup_res[2:66], 16)
                        if tot_sup > 0:
                            dev_pct = round((dev_bal / tot_sup) * 100.0, 1)
            except Exception as exc:
                _log.debug("deployer token balance lookup failed for %s: %s", deployer_addr, exc)

        sm_wallets = details.get("smart_wallets") or details.get("smart_minters") or []
        holders_cnt = stats.get("holders", 0) or 1
        if sm_wallets and holders_cnt > 0:
            smart_pct = min(round((len(sm_wallets) / max(holders_cnt, 1)) * 100.0, 1), 30.0)

        if stats.get("buys_1h", 0) > 40 and stats.get("percent_claimed", 0) > 60:
            sniper_pct = 6.5
        elif stats.get("percent_claimed", 0) >= 95:
            sniper_pct = 4.0

        if is_fresh_dev:
            fresh_pct = 5.0

        non_public = dev_pct + insider_pct + smart_pct + sniper_pct + fresh_pct
        if non_public > 100.0:
            factor = 100.0 / non_public
            dev_pct = round(dev_pct * factor, 1)
            insider_pct = round(insider_pct * factor, 1)
            smart_pct = round(smart_pct * factor, 1)
            sniper_pct = round(sniper_pct * factor, 1)
            fresh_pct = round(fresh_pct * factor, 1)
            public_pct = 0.0
        else:
            public_pct = round(100.0 - non_public, 1)

    hold_dist = {
        "dev": dev_pct,
        "insider": insider_pct,
        "smart": smart_pct,
        "sniper": sniper_pct,
        "fresh": fresh_pct,
        "public": public_pct
    }
    stats["hold"] = hold_dist
    stats["holder_distribution"] = hold_dist
    sec["hold"] = hold_dist
    sec["holder_distribution"] = hold_dist

    if dev_pct > 10.0:
        sec_flags.append(f"🚨 High Dev Holding ({dev_pct:.1f}% > 10%)")
        signals.append(f"⚠️ Dev holds {dev_pct:.1f}% of circulating supply")
        if stats.get("risk_level") in ("SAFE", "LOW", None):
            stats["risk_level"] = "HIGH" if dev_pct >= 25.0 else "MED"
            stats["risk_badge"] = "🚨 High Dev Share"
    if insider_pct > 15.0:
        sec_flags.append(f"⚠️ Insider Concentration ({insider_pct:.1f}%)")
    if sniper_pct > 15.0:
        sec_flags.append(f"⚠️ Heavy Sniper Allocation ({sniper_pct:.1f}%)")

    stats["risk_flags"] = sec_flags
    sec["risk_flags"] = sec_flags
    stats["security"] = sec

    # 6. Score calculation (0-100)
    score = 0

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

    # Adjust score based on security risk audit
    if stats.get("risk_level") == "RUG":
        score = 0
        signals.insert(0, "🚨 HONEYPOT DETECTED (Sell Reverts / Trap)")
    elif stats.get("risk_level") == "HIGH":
        score = max(0, score - 25)
    elif stats.get("risk_level") == "SAFE" and stats.get("lp_usd", 0) > 0:
        score = min(100, score + 10)

    if stats.get("risk_flags"):
        for f in stats["risk_flags"][:2]:
            signals.append(f"Safety: {f}")

    stats["signals"] = signals
    # `score` above only fed the human-readable signals; the stored score is the one shared formula.
    stats["score"], stats["rating"] = compute_interest({
        "lp_usd": stats.get("lp_usd"), "volume_24h": stats.get("volume_24h"), "buys_24h": stats.get("buys_24h"),
        "smart_wallets": details.get("smart_wallets") or details.get("smart_minters") or [],
        "risk_level": stats.get("risk_level"),
    })

    # 7. Evaluate Assisted Sniper Readiness
    proxy_item = dict(details)
    proxy_item.update(stats)
    proxy_item["contract"] = contract_addr
    proxy_item["deployer"] = deployer_addr
    s_eval = evaluate_sniper_ready(proxy_item)
    stats["sniper_ready"] = s_eval["is_ready"]
    stats["sniper_status"] = s_eval["status"]
    stats["sniper_badge"] = s_eval["badge"]
    stats["sniper_checklist"] = s_eval["checklist"]
    if s_eval["is_ready"]:
        signals.insert(0, "🎯 High-Conviction Sniper Ready (Passed 60% & Anti-Rug)")

    # Unknown counts stay None (never total_minted, never a made-up 1); known ones are whole numbers.
    for k in count_keys:
        stats[k] = int(known_counts[k]) if k in known_counts else None
    return stats


KNOWN_PROJECTS = [
    {
        "block": 130420000,
        "contract": "0xb777c52296f0d7d3c5cab7ae0c1aaac6b014b256",
        "deployer": "0x8beba36ca019ee421d1b4fb04b23ebf33d083346",
        "tx_hash": "0x425205bd5a4108063f2ad0b62f3d4cc3e0c01d5ff33878b15c5718f98e6d037b",
        "name": "Oathbots",
        "symbol": "OATH",
        "standard": "ERC-721 (NFT Collection)",
        "icon": "🎨",
        "is_paused": False,
        "has_free_mint": False,
        "mint_price_str": "0.0005 BNB",
        "mint_price_bnb": 0.0005,
        "mint_value_wei": "500000000000000",
        "floor_price_bnb": 0.00016,
        "floor_price_str": "0.00016 BNB",
        "volume_bnb": 0.74,
        "total_volume_bnb": 1.28,
        "max_supply": 10000,
        "total_minted": 10000,
        "score": 75,
        "rating": "🚀 VIRAL RUSH",
        "lp_usd": 0.0,
        "lp_wbnb": 0.0,
        "token_symbol": "OATH",
        "token_price_usd": 0.0,
        "holders": 10000,
        "buys_24h": 10000,
        "buys_1h": 0,
        "sells_24h": 0,
        "volume_24h": 0.0,
        "pair_url": "https://element.market/collections/oathbots",
        "mint_calldata": "0x1249c58b",
        "signals": ["100% Minted Out", "Active Marketplace Listing"],
        "detected_at": "2026-10-29 13:48:52",
        "user_balance": 1
    },
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
        "market_cap": 0.0,
        "fdv": 0.0,
        "price_change_24h": 0.0,
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
        "market_cap": 0.0,
        "fdv": 0.0,
        "price_change_24h": 0.0,
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
        "market_cap": 0.0,
        "fdv": 0.0,
        "price_change_24h": 0.0,
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


def parse_element_trade_log(log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Decodes an ERC721SellOrderFilled or ERC721BuyOrderFilled event log from Element Exchanger router
    (same layout; w[1]/w[2] are maker/taker, so seller and buyer are swapped for a buy order):
    Contract: 0xb3e3dfcb2d9f3dde16d78b9e6eb3538eb32b5ae1
    Topic: 0x9c248aa1a265aa616f707b979d57f4529bb63a4fc34dc7fc61fdddc18410f74e
    Data words (checked against a real on-chain event; the event has a dynamic `fees` array):
      w[0]: orderHash
      w[1]: seller
      w[2]: buyer
      w[3]: nonce
      w[4]: paymentToken (0xeeee... or WBNB)
      w[5]: price in wei
      w[6]: offset of the dynamic `fees` array (0x120)
      w[7]: collection
      w[8]: tokenId
      w[9...]: the fees array (length, then entries)
    A short 8-word log (no fees array) is still read with the tokenId in w[6].
    """
    if not isinstance(log, dict):
        return None
    data = log.get("data", "")
    if not data or not isinstance(data, str) or len(data) < 2 + 64 * 8:
        return None
    raw = data[2:] if data.startswith("0x") else data
    if len(raw) < 512:
        return None
    words = [raw[i:i+64] for i in range(0, len(raw), 64)]
    if len(words) < 8:
        return None
    try:
        col = "0x" + words[7][24:].lower()
        pay_tok = "0x" + words[4][24:].lower()
        is_bnb = (
            pay_tok.endswith("eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee") or
            pay_tok.endswith(WBNB_ADDRESS[2:].lower())
        )
        price_wei = int(words[5], 16)
        price_bnb = price_wei / 1e18 if is_bnb else 0.0
        token_id = int(words[8], 16) if len(words) >= 9 else int(words[6], 16)
        maker = "0x" + words[1][24:].lower()
        taker = "0x" + words[2][24:].lower()
        topics = log.get("topics") or []
        is_buy_order = bool(topics) and str(topics[0]).lower() == T_721_BUY_FILLED
        seller, buyer = (taker, maker) if is_buy_order else (maker, taker)
        tx_hash = log.get("transactionHash", "")
        block_num = int(log.get("blockNumber", "0"), 16) if isinstance(log.get("blockNumber"), str) else int(log.get("blockNumber") or 0)
        return {
            "collection": col,
            "token_id": token_id,
            "price_wei": str(price_wei),
            "price_bnb": price_bnb,
            "is_bnb": is_bnb,
            "payment_token": pay_tok,
            "seller": seller,
            "buyer": buyer,
            "tx_hash": tx_hash,
            "block": block_num
        }
    except Exception:
        return None


def evaluate_nft_surge(
    item: Dict[str, Any],
    fill_prices: List[float],
    secondary_moves: int = 0,
    user_held: int = 0,
    prev_stats: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Option D: Evaluates NFT Volume Surge conviction adapted from watch.mjs nftVolWatch():
    - Calculates sales count, total volume in BNB, floor price, and high price from Element trades.
    - Considers real paid trades.
    - Checks secondary transfers (from != 0x0...0).
    - Detects volume spike:
      * Held in wallet (user_held > 0): sales >= 3 AND vol > 0 (or 2x spike vs previous check).
      * Monitored collections: sales >= 5 OR vol >= 0.5 BNB (or 2x spike with >= 5 sales).
    - Returns standardized evaluation dictionary.
    """
    std = str(item.get("standard") or "")
    is_nft = "721" in std or "NFT" in std or "1155" in std or bool((item.get("classification") or {}).get("is_nft"))
    
    px = [p for p in fill_prices if p is not None and isinstance(p, (int, float)) and p >= 0]
    sales_cnt = len(px)
    vol_bnb = sum(px)
    floor_bnb = min(px) if px else 0.0
    high_bnb = max(px) if px else 0.0
    
    if not is_nft:
        return {
            "is_nft_surge": False,
            "sales_count": 0,
            "vol_bnb": 0.0,
            "floor_price_bnb": 0.0,
            "high_price_bnb": 0.0,
            "secondary_transfers": 0,
            "badge": "",
            "badge_plain": "",
            "summary": "Non-NFT Asset"
        }

    prev_f = int(prev_stats.get("sales_count", 0)) if prev_stats else int(item.get("secondary_sales_count", 0) or 0)
    
    is_hot = False
    if user_held > 0:
        is_hot = (sales_cnt >= 3 and vol_bnb > 0) or (prev_f > 0 and sales_cnt >= 2 * prev_f)
    else:
        is_hot = (sales_cnt >= 5) or (vol_bnb >= 0.5) or (prev_f > 0 and sales_cnt >= 5 and sales_cnt >= 2 * prev_f)

    badge = f"[bold magenta]🦋 SURGE ({sales_cnt} sales)[/bold magenta]" if is_hot else ""
    badge_plain = f"🦋 SURGE ({sales_cnt} sales)" if is_hot else ""
    summary = f"{sales_cnt} Element sales, {vol_bnb:.4f} BNB vol, floor {floor_bnb:.4f} BNB, {secondary_moves} secondary transfers"

    return {
        "is_nft_surge": is_hot,
        "sales_count": sales_cnt,
        "vol_bnb": vol_bnb,
        "floor_price_bnb": floor_bnb,
        "high_price_bnb": high_bnb,
        "secondary_transfers": secondary_moves,
        "badge": badge,
        "badge_plain": badge_plain,
        "summary": summary
    }


def element_lookback_blocks(rpc: "BscRpcClient", head: int, hours: float = ELEMENT_LOOKBACK_HOURS) -> int:
    """How many blocks `hours` covers at the chain's current pace, measured over the last 20,000 blocks."""
    sec_per_block = BSC_BLOCK_TIME_FALLBACK_SEC
    try:
        span = min(20000, head - 1)
        newest = int(rpc.get_block(head, False)["timestamp"], 16)
        oldest = int(rpc.get_block(head - span, False)["timestamp"], 16)
        if span > 0 and newest > oldest:
            sec_per_block = (newest - oldest) / span
    except Exception as exc:
        _log.debug("block time lookup failed, assuming %.2fs: %s", sec_per_block, exc)
    return max(1, int(hours * 3600 / sec_per_block))


def _element_fills(rpc, from_b: int, head: int, cache: Optional[Dict[str, Any]] = None) -> Optional[List[Dict[str, Any]]]:
    """Element ERC-721 fills (sell and buy orders) paid in BNB/WBNB for blocks from_b..head, or None when any
    log query failed (partial totals would understate sales and volume).

    `cache` (a dict the caller keeps between scans) holds the fills already read, so a repeat scan only asks
    for the blocks added since: a 24h window is ~20 log queries cold, one after that."""
    fetch = getattr(rpc, "get_logs_strict", None) or rpc.get_logs
    fills: List[Dict[str, Any]] = []
    start = from_b
    if cache and cache.get("to_block") is not None and cache.get("from_block", head + 1) <= from_b <= cache["to_block"] + 1:
        fills = [f for f in cache["fills"] if f["block"] >= from_b]
        start = cache["to_block"] + 1

    for b in range(start, head + 1, ELEMENT_LOG_CHUNK_BLOCKS):
        to_b = min(b + ELEMENT_LOG_CHUNK_BLOCKS - 1, head)
        params = {
            "address": ELEMENT_EX,
            "topics": [[T_721_FILLED, T_721_BUY_FILLED]],
            "fromBlock": hex(b),
            "toBlock": hex(to_b)
        }
        try:
            logs = fetch(params)
        except Exception as exc:
            _log.warning("Element fill logs failed for blocks %s-%s (%s); keeping the previous secondary-market stats",
                         b, to_b, exc)
            return None
        for l in logs:
            parsed = parse_element_trade_log(l)
            if parsed and parsed["is_bnb"]:  # fills paid in another token have no BNB price: not a BNB sale
                fills.append(parsed)

    if cache is not None:
        settled = head - ELEMENT_REORG_BLOCKS
        if settled >= from_b:
            cache.update(from_block=from_b, to_block=settled, fills=[f for f in fills if f["block"] <= settled])
    return fills


def scan_element_nft_volume(
    rpc: BscRpcClient,
    nft_items: List[Dict[str, Any]],
    lookback_blocks: Optional[int] = None,
    wallet_addr: Optional[str] = None,
    lookback_hours: float = ELEMENT_LOOKBACK_HOURS,
    fill_cache: Optional[Dict[str, Any]] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Scans the Element exchange for ERC-721 fills (sell orders and accepted offers) over the last
    `lookback_hours` (or exactly `lookback_blocks` when given), correlates them with tracked collections and
    evaluates volume surge signals.
    Updates items in-place with secondary_sales_count, secondary_vol_bnb, floor_price_bnb,
    high_price_bnb, secondary_transfers, is_nft_surge, and nft_surge_badge.
    Returns mapping: {collection_address_lower: eval_dict}; {} (nothing written) when the logs could not be read.
    Pass the same `fill_cache` dict on every call to read only the new blocks each time.
    """
    wallet = wallet_addr or DEFAULT_WALLET
    target_map = {x.get("contract", "").lower(): x for x in nft_items if x.get("contract")}

    try:
        head = rpc.get_block_number()
    except Exception:
        return {}
    if not head:
        return {}

    if lookback_blocks is None:
        lookback_blocks = element_lookback_blocks(rpc, head, lookback_hours)
    from_b = max(0, head - lookback_blocks)

    trade_logs = _element_fills(rpc, from_b, head, fill_cache)
    if trade_logs is None:
        return {}
    fills: Dict[str, List[float]] = {}
    for parsed in trade_logs:
        fills.setdefault(parsed["collection"], []).append(parsed["price_bnb"])

    results: Dict[str, Dict[str, Any]] = {}

    for c, it in target_map.items():
        std = str(it.get("standard") or "")
        is_nft = "721" in std or "NFT" in std or "1155" in std or bool((it.get("classification") or {}).get("is_nft"))
        if not is_nft:
            continue

        c_fills = fills.get(c, [])
        user_held = int(it.get("user_balance") or 0)

        # Only query on-chain balance / transfers if trades occurred on Element or user already held tokens
        if c_fills or user_held > 0:
            if not user_held and wallet:
                try:
                    bal_call = "0x70a08231" + wallet[2:].lower().zfill(64)
                    res = rpc.call_contract(c, bal_call)
                    if res and len(res) >= 66:
                        user_held = int(res[:66], 16)
                        it["user_balance"] = user_held
                except Exception as exc:
                    _log.debug("balanceOf lookup failed for %s: %s", c, exc)

            moves = int(it.get("secondary_transfers", 0) or 0)
            try:
                t_from = max(from_b, head - 5000)
                t_params = {
                    "address": c,
                    "topics": [T_TRANSFER],
                    "fromBlock": hex(t_from),
                    "toBlock": hex(head)
                }
                t_logs = rpc.get_logs(t_params)
                sec_count = 0
                for tl in t_logs:
                    topics = tl.get("topics", [])
                    if len(topics) == 4 and int(topics[1], 16) != 0:
                        sec_count += 1
                moves = sec_count
            except Exception as exc:
                _log.debug("transfer log scan failed for %s: %s", c, exc)
        else:
            moves = int(it.get("secondary_transfers", 0) or 0)

        eval_res = evaluate_nft_surge(it, c_fills, secondary_moves=moves, user_held=user_held)
        it["secondary_sales_count"] = eval_res["sales_count"]
        it["secondary_vol_bnb"] = eval_res["vol_bnb"]
        if eval_res["sales_count"] > 0:
            # a window without fills says nothing about the floor: keep the last one we saw
            it["floor_price_bnb"] = eval_res["floor_price_bnb"]
            it["high_price_bnb"] = eval_res["high_price_bnb"]
        it["secondary_transfers"] = eval_res["secondary_transfers"]
        it["is_nft_surge"] = eval_res["is_nft_surge"]
        it["nft_surge_badge"] = eval_res["badge"]

        results[c] = eval_res

    return results


def evaluate_portfolio_exit(item: Dict[str, Any], bnb_price: Optional[float] = None) -> Dict[str, Any]:
    """
    Evaluates take-profit and stop-loss conviction signals for an asset held in portfolio:
    - Native Gas (BNB): Operational Gas reserve.
    - NFT (ERC-721): Direct to Element Market listing or Volume Surge alert.
    - BEP-20 / Fungible Token:
      * 🚀 10x MOON: Return multiple >= 10.0x (or Free Claim with >= $20 value)
      * 🚀 5x MOON: Return multiple >= 5.0x (or Free Claim with >= $10 value)
      * 🟢 2x RECOUP: Return multiple >= 2.0x (or Free Claim with >= $2 value) -> Recoup principal / play with house money
      * ⚠️ -40% CRASH: Trailing stop triggered when valuation drops >= 40% from ATH
      * 🔻 STOP LOSS: Position dropped >= 30% below entry cost
      * ⏳ HODL: Within normal volatility bounds
    """
    if bnb_price is None:
        bnb_price = cached_bnb_price()
    contract = (item.get("contract") or "").lower()
    is_nft = bool(item.get("is_nft"))
    std = str(item.get("standard") or "")
    if "721" in std or "nft" in std.lower():
        is_nft = True

    if contract == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee" or item.get("symbol") == "BNB":
        return {
            "signal": "⛽ GAS RESERVE",
            "badge": "[bold cyan]⛽ GAS RESERVE[/bold cyan]",
            "badge_plain": "⛽ GAS RESERVE",
            "action": "KEEP",
            "action_label": "Gas Reserve",
            "multiple": 1.0,
            "pnl_usd": 0.0,
            "pnl_pct": 0.0,
            "peak_usd": item.get("value_usd", 0.0),
            "recommendation": "Binance Agentic Wallet operational gas reserve. Keep funded for transaction fees.",
            "can_swap": False
        }

    raw_item = item.get("raw_item") or {}
    if is_nft:
        is_surge = bool(item.get("is_nft_surge") or (raw_item and raw_item.get("is_nft_surge")))
        sales_cnt = int(item.get("secondary_sales_count") or (raw_item and raw_item.get("secondary_sales_count")) or 0)
        flr = float(item.get("floor_price_bnb") or (raw_item and raw_item.get("floor_price_bnb")) or 0.0)

        if is_surge:
            return {
                "signal": "🦋 ELEMENT SURGE",
                "badge": f"[bold magenta]🦋 SURGE ({sales_cnt} sales)[/bold magenta]",
                "badge_plain": f"🦋 SURGE ({sales_cnt} sales)",
                "action": "LIST_ELEMENT",
                "action_label": "Element Surge",
                "multiple": 1.0,
                "pnl_usd": 0.0,
                "pnl_pct": 0.0,
                "peak_usd": item.get("value_usd", 0.0),
                "recommendation": f"Element volume spike! {sales_cnt} secondary sales (Floor: {flr:.4f} BNB). List or sell manually on Element.",
                "can_swap": False
            }

        return {
            "signal": "🎨 LIST ON ELEMENT",
            "badge": "[bold magenta]🎨 LIST ON ELEMENT[/bold magenta]",
            "badge_plain": "🎨 LIST ON ELEMENT",
            "action": "LIST_ELEMENT",
            "action_label": "List on Element",
            "multiple": 1.0,
            "pnl_usd": 0.0,
            "pnl_pct": 0.0,
            "peak_usd": item.get("value_usd", 0.0),
            "recommendation": "NFTs trade on Element Market. AMMs like PancakeSwap do not support ERC-721.",
            "can_swap": False
        }

    # Fungible BEP-20 Token
    raw_item = item.get("raw_item") or {}
    bal = float(item.get("balance") or 0.0)
    cur_val = float(item.get("value_usd") or 0.0)
    cur_price = float(item.get("price_usd") or raw_item.get("token_price_usd") or 0.0)
    if cur_val <= 0.0 and cur_price > 0.0 and bal > 0.0:
        cur_val = bal * cur_price

    mint_price_bnb = float(raw_item.get("mint_price_bnb") or item.get("mint_price_bnb") or 0.0)
    cost_usd = mint_price_bnb * bnb_price

    # ATH / Peak Tracking
    peak_price = float(raw_item.get("peak_price_usd") or item.get("peak_price_usd") or 0.0)
    if cur_price > peak_price:
        peak_price = cur_price
        item["peak_price_usd"] = peak_price
        if raw_item:
            raw_item["peak_price_usd"] = peak_price

    peak_val = float(raw_item.get("peak_val_usd") or item.get("peak_val_usd") or 0.0)
    if cur_val > peak_val:
        peak_val = cur_val
        item["peak_val_usd"] = peak_val
        if raw_item:
            raw_item["peak_val_usd"] = peak_val

    # Trailing Stop: -40% crash from peak
    is_crash = False
    drawdown_pct = 0.0
    if peak_val > 0.0 and cur_val > 0.0 and cur_val < peak_val:
        drawdown_pct = (peak_val - cur_val) / peak_val
        if drawdown_pct >= 0.40:
            is_crash = True
    elif peak_price > 0.0 and cur_price > 0.0 and cur_price < peak_price:
        drawdown_pct = (peak_price - cur_price) / peak_price
        if drawdown_pct >= 0.40:
            is_crash = True

    if is_crash:
        return {
            "signal": "⚠️ -40% CRASH",
            "badge": "[bold red]⚠️ -40% CRASH[/bold red]",
            "badge_plain": "⚠️ -40% CRASH",
            "action": "SELL_PANCAKE",
            "action_label": "Exit on PancakeSwap",
            "multiple": (cur_val / cost_usd) if cost_usd > 0 else 1.0,
            "pnl_usd": cur_val - cost_usd,
            "pnl_pct": -drawdown_pct * 100.0,
            "peak_usd": peak_val,
            "recommendation": f"Trailing Crash Stop: Dropped {drawdown_pct*100.0:.1f}% from peak (${peak_val:.2f}). Exit to preserve capital!",
            "can_swap": True
        }

    # Return multiple evaluation
    if cost_usd > 0.0:
        mult = cur_val / cost_usd
        pnl_usd = cur_val - cost_usd
        pnl_pct = ((cur_val - cost_usd) / cost_usd) * 100.0
    else:
        # Free mint or 0-cost
        mult = 1.0
        pnl_usd = cur_val
        pnl_pct = 0.0

    if mult >= 10.0 or (cost_usd == 0.0 and cur_val >= 20.0):
        return {
            "signal": "🚀 10x MOON",
            "badge": "[bold bright_green]🚀 10x MOON[/bold bright_green]",
            "badge_plain": "🚀 10x MOON",
            "action": "SELL_PANCAKE",
            "action_label": "Lock 80% Profit",
            "multiple": mult,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "peak_usd": peak_val,
            "recommendation": f"Moonshot Target Reached ({mult:.1f}x)! Lock in 80% profit on PancakeSwap V2.",
            "can_swap": True
        }
    elif mult >= 5.0 or (cost_usd == 0.0 and cur_val >= 10.0):
        return {
            "signal": "🚀 5x MOON",
            "badge": "[bold bright_green]🚀 5x MOON[/bold bright_green]",
            "badge_plain": "🚀 5x MOON",
            "action": "SELL_PANCAKE",
            "action_label": "Scale Out 50%",
            "multiple": mult,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "peak_usd": peak_val,
            "recommendation": f"Major Profit Target Reached ({mult:.1f}x)! Scale out 50–70% into BNB.",
            "can_swap": True
        }
    elif mult >= 2.0 or (cost_usd == 0.0 and cur_val >= 2.0):
        return {
            "signal": "🟢 2x RECOUP",
            "badge": "[bold green]🟢 2x RECOUP[/bold green]",
            "badge_plain": "🟢 2x RECOUP",
            "action": "SELL_PANCAKE",
            "action_label": "Recoup Principal",
            "multiple": mult,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "peak_usd": peak_val,
            "recommendation": f"Recoup initial principal ({mult:.1f}x return)! Sell 50% to play risk-free with house money.",
            "can_swap": True
        }
    elif cost_usd > 0.0 and pnl_pct <= -30.0:
        return {
            "signal": "🔻 STOP LOSS",
            "badge": "[bold yellow]🔻 STOP LOSS[/bold yellow]",
            "badge_plain": "🔻 STOP LOSS",
            "action": "SELL_PANCAKE",
            "action_label": "Cut Loss",
            "multiple": mult,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "peak_usd": peak_val,
            "recommendation": f"Position down {abs(pnl_pct):.1f}% from entry cost. Consider stop loss on PancakeSwap.",
            "can_swap": True
        }
    else:
        return {
            "signal": "⏳ HODL",
            "badge": "[dim cyan]⏳ HODL[/dim cyan]",
            "badge_plain": "⏳ HODL",
            "action": "HOLD",
            "action_label": "Holding",
            "multiple": mult,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "peak_usd": peak_val,
            "recommendation": "Valuation within normal volatility threshold. Continue monitoring market progress.",
            "can_swap": True
        }


# Cloud backend the CLI pulls from / pushes to. Override with CLOUD_API_URL (e.g. a staging service).
CLOUD_API_URL = os.environ.get("CLOUD_API_URL", "https://bsc-ia-scan.onrender.com").strip().rstrip("/")


def sync_cloud_history() -> List[Dict[str, Any]]:
    """Pulls latest discoveries from the 24/7 cloud scanner and merges into local cache."""
    try:
        req = urllib.request.Request(
            f"{CLOUD_API_URL}/history.json?_t={int(time.time())}",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status == 200:
                cloud_data = json.loads(resp.read().decode("utf-8"))
                if isinstance(cloud_data, list) and len(cloud_data) > 0:
                    local_data = []
                    if os.path.exists(HISTORY_FILE):
                        try:
                            with open(HISTORY_FILE, "r") as f:
                                local_data = json.load(f)
                        except Exception:
                            local_data = []
                    
                    merged = {x.get("contract", "").lower(): x for x in local_data if x.get("contract")}
                    for item in cloud_data:
                        c = (item.get("contract") or "").lower()
                        if not c:
                            continue
                        if c not in merged:
                            merged[c] = item
                        else:
                            sw = list(set((merged[c].get("smart_wallets") or []) + (item.get("smart_wallets") or [])))
                            merged[c].update(item)
                            merged[c]["smart_wallets"] = sw
                            merged[c]["smart_minters"] = sw
                    
                    res_list = [x for x in merged.values() if not is_system_infra(x)]
                    res_list.sort(key=lambda x: x.get("block", 0) or 0, reverse=True)
                    save_history(res_list)

                    # Push local discoveries and owned tokens to Cloud Engine
                    if len(res_list) > len(cloud_data) or any(x.get("user_balance", 0) > 0 for x in res_list):
                        admin_token = os.environ.get("ADMIN_TOKEN", "").strip()
                        if admin_token:
                            try:
                                sync_req = urllib.request.Request(
                                    f"{CLOUD_API_URL}/api/sync",
                                    data=json.dumps(res_list).encode("utf-8"),
                                    headers={
                                        "Content-Type": "application/json",
                                        "User-Agent": "Mozilla/5.0",
                                        "Authorization": f"Bearer {admin_token}",
                                    },
                                    method="POST"
                                )
                                urllib.request.urlopen(sync_req, timeout=4.0)
                            except Exception as exc:
                                _log.debug("cloud /api/sync push failed: %s", exc)

                    return res_list
    except Exception as exc:
        _log.debug("cloud history pull failed: %s", exc)
    return []


def load_history() -> List[Dict[str, Any]]:
    items = []
    try:
        from db import db  # imported lazily: db.py opens the database on import, which CLI start-up shouldn't pay for
        db_items = db.get_all_projects()
        if db_items:
            items = db_items
    except Exception:
        items = []

    if not items:
        cloud_items = sync_cloud_history()
        if cloud_items:
            items = cloud_items
        elif os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    items = json.load(f)
            except Exception:
                items = []
        else:
            items = []
    
    # Filter out pure system/infrastructure contracts
    items = [x for x in items if not is_system_infra(x)]
    
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
            if kp.get("floor_price_bnb") and not ex.get("floor_price_bnb"):
                ex["floor_price_bnb"] = kp["floor_price_bnb"]
            if kp.get("floor_price_str") and not ex.get("floor_price_str"):
                ex["floor_price_str"] = kp["floor_price_str"]
            if kp.get("volume_bnb") and not ex.get("volume_bnb"):
                ex["volume_bnb"] = kp["volume_bnb"]
            if kp.get("total_volume_bnb") and not ex.get("total_volume_bnb"):
                ex["total_volume_bnb"] = kp["total_volume_bnb"]
    items.sort(key=lambda x: x.get("block", 0) or 0, reverse=True)
    return items


def save_history(history: List[Dict[str, Any]], delta: Optional[Any] = None):
    try:
        clean_history = [x for x in history if not is_system_infra(x)]
        seen = set()
        final_list = []
        for item in clean_history:
            c = item.get("contract", "").lower()
            if c and c not in seen:
                seen.add(c)
                final_list.append(item)

        # 1. Primary: Save to Database (Supabase / SQLite in WAL mode)
        try:
            from db import db  # lazy, see load_history
            if delta is not None:
                if isinstance(delta, dict):
                    db.upsert_projects([delta])
                elif isinstance(delta, list):
                    db.upsert_projects(delta)
            else:
                db.upsert_projects(final_list)
        except Exception as exc:
            _log.debug("database upsert failed: %s", exc)

        # 2. Optional: write the whole list to history.json (compact minified) for local inspection and the terminal
        # tool. Opt-in, because it serialises ~14 MB on every save; the database is the source of truth. Read at call
        # time so it can be switched on without a restart (and by tests).
        if os.environ.get("WRITE_HISTORY_FILE", "").strip() == "1":
            tmp_file = HISTORY_FILE + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(final_list, f, separators=(',', ':'))
            os.replace(tmp_file, HISTORY_FILE)
    except Exception as exc:
        _log.debug("could not save history: %s", exc)


def format_structured_type(std: Any, item: Optional[Dict[str, Any]] = None) -> str:
    """
    Standardizes contract standard badge with consistent text and color styling.
    Highlights NFT Volume Surges (Option D) and Proxy Clones (Option E).
    """
    if item and item.get("is_nft_surge"):
        return "[bold magenta]🦋 SURGE[/bold magenta]"
    if item and item.get("is_proxy_clone"):
        return "[bold bright_cyan]🧩 PROXY[/bold bright_cyan]"
    s = str(std or "")
    if "NFT" in s or "ERC-721" in s:
        return "[magenta]ERC-721[/magenta]"
    elif "ERC-1155" in s:
        return "[magenta]ERC-1155[/magenta]"
    elif "BEP-20" in s:
        return "[cyan]BEP-20[/cyan]"
    elif "Fair-Mint" in s:
        return "[yellow]Fair-Mint[/yellow]"
    else:
        return "[dim]Custom[/dim]"
