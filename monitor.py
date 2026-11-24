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
import random
from typing import Optional, Dict, Any, List, Tuple, Iterable

import filters
from filters import EQUITY_SYMBOLS_CORE, EXCLUDED_PEGS_CONTRACTS, PEG_SYMBOLS_CORE
from chain.rpc import (  # noqa: F401  (moved to chain/rpc.py; re-exported so `monitor.X` keeps working)
    BscRpcClient, build_rpc_endpoints, clean_chain_text, decode_string, keccak_selector, _UNSAFE_TEXT_RE,
)
from chain.addresses import (  # noqa: F401  (moved to chain/addresses.py; re-exported so `monitor.X` keeps working)
    AGENT_ID_BYTES, AGENT_ID_REGISTRY, BNCB_ADDRESS, DEFAULT_WALLET, FOUR_MEME_FACTORY, FOUR_MEME_FACTORY_BYTES, FOUR_MEME_ROUTER,
    FOUR_MEME_ROUTER_BYTES, PANCAKE_FACTORY_V2, PANCAKE_ROUTER_V2, SPACE_ID_MASTER_BYTES, SPACE_ID_MASTER_REGISTRY,
    TRACKED_REGISTRY_BYTES, USD1_ADDRESS, USDC_ADDRESS, USDT_ADDRESS, VAULT_FACTORIES, WBNB_ADDRESS, known_infra_addresses,
)
from risk import (  # noqa: F401  (moved to risk.py; re-exported so `monitor.X` keeps working)
    count_prior_deployments, fund_drain_applies, launchpad_addresses, resolve_deployer_from_owner,
)
from market.element import (  # noqa: F401  (moved to market/element.py; re-exported so `monitor.X` keeps working)
    BSC_BLOCK_TIME_FALLBACK_SEC, ELEMENT_EX, ELEMENT_LOG_ATTEMPTS, ELEMENT_LOG_CHUNK_BLOCKS, ELEMENT_LOG_RETRY_SEC,
    ELEMENT_LOOKBACK_HOURS, ELEMENT_REORG_BLOCKS, NFT_ACTIVE_MIN_SALES, T_721_BUY_FILLED, T_721_FILLED, T_TRANSFER,
    _element_fills, element_lookback_blocks, evaluate_nft_surge, get_element_market_url, parse_element_trade_log,
    scan_element_nft_volume,
)
from chain.rpc import (  # noqa: F401  (moved to chain/rpc.py; re-exported so `monitor.X` keeps working)
    LOG_RPC_ENDPOINTS, PUBLIC_LOG_RPC_ENDPOINTS, PUBLIC_RPC_ENDPOINTS, RPC_ENDPOINTS,
)
from market.bnb_price import (  # noqa: F401  (moved to market/bnb_price.py; re-exported so `monitor.X` keeps working)
    BNB_PRICE_FALLBACK_USD, BNB_PRICE_STALE_AFTER_TTLS, BNB_PRICE_TTL_SEC, _bnb_price_lock, _bnb_price_rpc, _bnb_price_state,
    fetch_bnb_price_usd, refresh_bnb_price,
)
from smart_wallets import (  # noqa: F401  (moved to smart_wallets.py; re-exported so `monitor.X` keeps working)
    SMART_WALLETS_CSV_URL, SMART_WALLETS_FILE, _SMART_WALLETS_CACHE, get_cached_smart_wallets, load_smart_wallets, sync_smart_wallets,
)
from chain.bytecode import (  # noqa: F401  (moved to chain/bytecode.py; re-exported so `monitor.X` keeps working)
    BEACON_IMPL_SELECTOR, EIP1967_BEACON_SLOT, EIP1967_IMPL_SLOT, FACTORY_SELECTORS, detect_factory_pattern, is_launchpad_factory,
    is_proxy_contract, replace_calldata_recipient, resolve_full_bytecode,
)
from mint_terms import (  # noqa: F401  (moved to mint_terms.py; re-exported so `monitor.X` keeps working)
    ALT_MINT_CALLS, MERKLE_ROOT_GETTERS, MINT_END_GETTERS, MINT_OPEN_GETTERS, MINT_PROBE_ADDRESS, MINT_START_GETTERS,
    MINT_TERMS_VERSION, PER_WALLET_GETTERS, _AGENT_ERROR_SELECTORS, _AGENT_FUNCTION_SELECTORS, _AGENT_TEXT_MARKERS, _ALT_MINT_MAX,
    _CLAIM_BY_ID_SEL, _CLAIM_CARD_SEL, _PER_ID_CLAIM_MARKERS, _PER_WALLET_MAX, _PRICE_MAX_WEI, _PRICE_MIN_WEI, _QTY,
    _REVERT_ERROR_SELECTORS, _REVERT_KINDS, _TS_MAX, _TS_MIN, _first_uint_getter, _gate_for_call, _holder_claim_kind,
    _price_from_revert, _revert_data, _revert_text, _revert_uint_args, _sels, _simulate, _start_from_revert, _to, classify_revert,
    has_agent_id_check, mint_call_is_usable, probe_mint_terms, simulate_mint_detail,
)
from chain.supply import (  # noqa: F401  (moved to chain/supply.py; re-exported so `monitor.X` keeps working)
     GETTER_MEMO_TTL_SEC, MAX_SUPPLY_GETTERS, MINTED_GETTERS, _GETTER_MEMO, _memo_first, _memo_forget, _memo_get, _memo_put,
     _minted_kind, read_minted_batch, read_supply,
)
from known_projects import KNOWN_PROJECTS  # noqa: F401  (moved to known_projects.py)
from chain.classify import classify_asset_type  # noqa: F401  (moved to chain/classify.py)
from scoring import (  # noqa: F401  (moved to scoring.py; re-exported so `monitor.X` keeps working)
     RATING_BY_TIER, compute_interest, evaluate_sniper_ready,
)
from chain.registry import (  # noqa: F401  (moved to chain/registry.py; re-exported so `monitor.X` keeps working)
     _linked_code, contract_links_to_registry, matches_tracked_registry,
)
from chain.receipts import (  # noqa: F401  (moved to chain/receipts.py; re-exported so `monitor.X` keeps working)
     TOKENS_PER_MINT_GETTERS, TRANSFER_TOPIC, read_tokens_per_mint, tokens_from_mint_receipt,
)

# Swallowed best-effort failures are logged at DEBUG: printing would flood the worker's output.
_log = logging.getLogger(__name__)

# Calls that move or approve an existing token / NFT but cannot mint one. A tracked wallet making one of these is
# trading or managing something that already exists, so it is never evidence of a new agent drop.
NON_MINT_SELECTORS = frozenset({
    "095ea7b3",  # approve(address,uint256)
    "a9059cbb",  # transfer(address,uint256)
    "23b872dd",  # transferFrom(address,address,uint256)
    "a22cb465",  # setApprovalForAll(address,bool)
    "42842e0e",  # safeTransferFrom(address,address,uint256)
    "b88d4fde",  # safeTransferFrom(address,address,uint256,bytes)
    "39509351",  # increaseAllowance(address,uint256)
    "a457c2d7",  # decreaseAllowance(address,uint256)
    "d505accf",  # permit(...)
    "2e1a7d4d",  # withdraw(uint256)
    "d0e30db0",  # deposit()
})


def is_non_mint_call(calldata: str) -> bool:
    """True when the calldata's 4-byte selector is a plain transfer/approve-style call (see NON_MINT_SELECTORS)."""
    d = (calldata or "").lower()
    if d.startswith("0x"):
        d = d[2:]
    return len(d) >= 8 and d[:8] in NON_MINT_SELECTORS


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
STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "agent_id_stats.json")
STATS_FILE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_id_stats.json")
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
    # The starting point the worker counts forward from, not a count taken now: `is_default` lets the API tell, and
    # last_updated is 0 so it never passes for fresh.
    return {
        "is_default": True,
        "total_created_wallets": DEFAULT_AGENT_WALLET_COUNT,
        "last_block": 132757997,
        "last_updated": 0,
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
        info["name"] = clean_chain_text(decode_string(name_res))

    symbol_res = rpc.call_contract(contract_addr, keccak_selector("symbol()"))
    if symbol_res:
        info["symbol"] = clean_chain_text(decode_string(symbol_res))

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
    supply_meta: Dict[str, Any] = {}
    info["max_supply"], info["total_minted"] = read_supply(rpc, target_addrs, token_decimals, meta=supply_meta)
    info["cap_status"] = supply_meta.get("cap_status", "unknown")
    if supply_meta.get("cap_slots"):
        info["mint_slots"] = supply_meta["cap_slots"]       # the cap in tokens follows once tokens_per_mint is read
    # decimals of the token itself (BEP-20) or of the token a Fair-Mint dispenser hands out
    if classification.get("decimals") is not None:
        info["token_decimals"] = classification["decimals"]

    # Inspect bytecode constants if max_supply still not found (e.g. PUSH2 24444 -> 0x615f7c)
    if info["max_supply"] is None and not supply_meta.get("cap_slots"):
        try:
            t_codes = "".join(rpc.get_code(a) for a in target_addrs).lower()
            if "615f7c" in t_codes:
                info["max_supply"] = 24444
                info["cap_status"] = "capped"
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
    info["_code"] = code   # for probe_mint_terms; not stored (records are built field by field)
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


# ---------------------------------------------------------------------------------------------- mint terms
# What a free-mint hunter checks before minting: slots left (from supply), the per-wallet limit, when the mint
# opens / closes, and who may mint. Read once per drop (probe_mint_terms) and stored as record["mint_terms"]:
#
#   {"cap": "capped"|"unlimited"|"unknown", "per_wallet": 10, "per_wallet_getter": "MAX_PER_WALLET()",
#    "start": 1795000000, "end": 1795600000, "gate": "open"|"agent_id"|"whitelist"|"unknown", "checked_at": ...}
#
# Keys with no answer are left out: the UI shows "unknown" rather than a guess. A getter is only called when its
# selector is in the bytecode (PUSH4 <selector>), so a contract without these functions costs no extra calls.

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


def cached_bnb_price() -> float:
    """Last known BNB/USD. Never touches the network, so it is safe in render paths."""
    return _bnb_price_state["usd"]


def bnb_price_as_of() -> Optional[float]:
    """Unix time of the last successful BNB/USD lookup, or None if there never was one."""
    return _bnb_price_state.get("ok_at") or None


def live_bnb_price() -> Optional[float]:
    """BNB/USD while the last successful lookup is recent, else None. For USD figures that get stored or served: the
    built-in BNB_PRICE_FALLBACK_USD is only a placeholder (it was $705 when BNB was $553), and a profit / ATH built on it
    is kept as a running maximum."""
    return cached_bnb_price() if bnb_price_is_live() else None


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


evaluate_sniper_readiness = evaluate_sniper_ready


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


# --- Profit per mint -------------------------------------------------------------------------------------------------
SWAP_FEE = 0.0025      # PancakeSwap v2 fee on the sell


def mint_units(item: Dict[str, Any]) -> Optional[float]:
    """What one mint gives: 1 for an NFT, tokens_per_mint for a token (None while it is not known)."""
    std = str(item.get("standard") or "").upper()
    if any(k in std for k in ("721", "1155", "NFT")):
        return 1.0
    try:
        n = float(item.get("tokens_per_mint") or 0)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def can_sell(item: Dict[str, Any]) -> bool:
    sec = item.get("security") if isinstance(item.get("security"), dict) else {}
    return not (sec.get("is_honeypot") or sec.get("cannot_sell_all") or item.get("is_honeypot"))


def sell_tax_pct(item: Dict[str, Any]) -> float:
    sec = item.get("security") if isinstance(item.get("security"), dict) else {}
    try:
        t = float(sec.get("sell_tax") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return min(max(t, 0.0), 100.0)


def sell_value_usd(price_usd: float, units: float, tax_pct: float, pool_usd: Optional[float]) -> float:
    """What selling one mint's worth (units at price_usd) returns: through the pool when its size is known (constant
    product: half the pool is the paired side, minus the swap fee), then minus the sell tax."""
    value = price_usd * units
    if pool_usd and pool_usd > 0:
        side = pool_usd / 2.0
        value = side * value / (side + value) * (1.0 - SWAP_FEE)
    return value * (1.0 - tax_pct / 100.0)


def _no_profit() -> Dict[str, Any]:
    return {
        "sec_usd": 0.0, "mint_usd": None, "profit": None,
        "ath_price_usd": None, "ath_profit_usd": None, "ath_multiplier": None, "current_multiplier": None,
        "is_sold": False, "is_free": False,
        "units": None, "sell_tax_pct": 0.0, "can_sell": True, "ath_lp_usd": None,
    }


def update_post_mint_profit_metrics(item: Dict[str, Any], bnb_price: Optional[float] = None) -> Dict[str, Any]:
    """
    Computes and updates post-mint profit and ATH metrics on an item (rules above).
    Filters out pegged crypto assets, synthetic stocks, known scams and system infra.
    """
    if bnb_price is None:
        bnb_price = live_bnb_price()
        if bnb_price is None:
            return _no_profit()       # no live BNB/USD: no profit rather than one built on a placeholder price
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

    # Profit is per MINT: one mint gives `units` (1 NFT, or tokens_per_mint tokens). A token whose tokens per mint is
    # not known yet has no profit: its per-token price says nothing about what one mint paid. A token that cannot be
    # sold (honeypot) has none either. Selling goes through the pool (when its size is known) and pays the sell tax.
    units = mint_units(item)
    sellable = can_sell(item)
    tax = sell_tax_pct(item)
    profit = None
    multiplier = None
    if cost_known and sec_usd > 0 and units and sellable:
        profit = sell_value_usd(sec_usd, units, tax, lp_val if price_from_pair else None) - mint_usd
        multiplier = _round2(sec_usd * units / mint_usd) if mint_usd > 0 else None

    # --- ATH (high-water mark) -----------------------------------------------------------------------------
    lp_ok = (not price_from_pair) or lp_val >= ATH_MIN_LP_USD
    prev_ath = float(item.get("ath_price_usd") or 0.0)
    if prev_ath > 0 and sec_usd > 0 and price_from_pair and lp_val < ATH_MIN_LP_USD and prev_ath > ATH_RESET_FACTOR * sec_usd:
        # a poisoned ATH (thin pool spike, stale seed): drop it, it is re-learned from a trustworthy price
        item["ath_price_usd"] = None
        item["ath_profit_usd"] = None
        item["ath_multiplier"] = None
        item["ath_lp_usd"] = None
        prev_ath = 0.0
    if sec_usd > 0 and lp_ok and sec_usd > prev_ath:
        item["ath_price_usd"] = _sig10(sec_usd)
        item["ath_timestamp"] = int(time.time())
        # the pool size when the peak was set: what one mint could really have been sold for at that moment
        item["ath_lp_usd"] = round(lp_val, 2) if price_from_pair and lp_val > 0 else None
    ath_price = float(item.get("ath_price_usd") or 0.0)

    # Peak profit and multiplier come from the peak price and TODAY's mint cost, every time. Only the peak price is a
    # high-water mark: keeping these two as running maxima froze values computed from a mint cost that was later
    # corrected (neiro: 14,176x next to a peak 96% below the mint cost).
    if cost_known and ath_price > 0 and units and sellable:
        ath_pool = item.get("ath_lp_usd")
        item["ath_profit_usd"] = _sig10(sell_value_usd(ath_price, units, tax, float(ath_pool) if ath_pool else None)
                                        - mint_usd)
    else:
        item["ath_profit_usd"] = None
    if cost_known and not is_free and mint_usd > 0 and ath_price > 0 and units and sellable:
        item["ath_multiplier"] = _round2(ath_price * units / mint_usd)
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
        "units": units,
        "sell_tax_pct": tax,
        "can_sell": sellable,
        "ath_lp_usd": item.get("ath_lp_usd"),
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


# ------------------------------------------------------------------------------------------ who deployed it, and drains
# The "Serial Dev" and "Fund Drain" checks flagged 22% of all records RUG. Checked on chain (2026-11-24), the two rules
# were wrong in different ways:
#   * a proxy clone / factory record is created when the worker first SEES a call to the contract, so its `deployer` was
#     whoever made that call (a trading bot with 14,642 transactions "deployed" 294 tokens); the real creator is the
#     launchpad factory, which is also the contract's owner();
#   * owner() of a contract is often a contract (Four.meme's factory), not the person who deployed it;
#   * "no LP and an empty contract" is true of every NFT collection whose owner withdrew the mint revenue, and of every
#     1,000,000,000-supply launch token whose "mint price" is just the creation fee.

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

    # 1. Resolve deployer if missing: owner() only counts when it is a plain wallet (see resolve_deployer_from_owner)
    if not deployer_addr:
        deployer_addr = resolve_deployer_from_owner(rpc, contract_addr)
    if deployer_addr and deployer_addr.lower() in launchpad_addresses():
        deployer_addr = None        # a launchpad / factory deploys for everyone: its history is nobody's reputation

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
            prev_deps = count_prior_deployments(history, deployer_addr, contract_addr)
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
                token_sym = clean_chain_text(decode_string(sym_res))
        else:
            # Check marsToken()
            mars_res = rpc.call_contract(contract_addr, "0x7419f946")
            if mars_res and len(mars_res) >= 66 and mars_res[-40:] != "0"*40:
                target_token = "0x" + mars_res[-40:].lower()
                sym_res = rpc.call_contract(target_token, keccak_selector("symbol()"))
                if sym_res:
                    token_sym = clean_chain_text(decode_string(sym_res))
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
                        bnb_now = live_bnb_price()
                        if bnb_now:                       # in USD only with a live BNB/USD; volume_bnb is kept either way
                            stats["volume_24h"] = (vol_wei / 1e18) * bnb_now
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
            if not bnb_price_is_live():
                bnb_price = 0.0       # the placeholder is not a price: BNB-quoted pools are skipped, BNB figures left unset
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
    if fund_drain_applies(details, stats, is_paid_mint, contract_bnb):
        sec["is_fund_drain"] = True
        sec_flags.append("🚨 Direct Capital Drain (No LP & Contract Empty)")
        # HIGH, not RUG: the money's whereabouts are not traced (that would need transaction traces), so this is
        # "paid mints, nothing held, no pool", a reason to be careful rather than proof of a rug.
        if stats.get("risk_level") != "RUG":          # a proven RUG (honeypot, serial dev) keeps its level and its badge
            stats["risk_level"] = "HIGH"
            stats["risk_badge"] = "🚨 Fund Drain"
        signals.append("🚨 Mint funds are not in the contract and there is no DEX pool")

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


def load_history() -> List[Dict[str, Any]]:
    items = []
    try:
        from db import db  # imported lazily: db.py opens the database on import
        db_items = db.get_all_projects()
        if db_items:
            items = db_items
    except Exception:
        items = []

    if not items:
        if os.path.exists(HISTORY_FILE):
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

