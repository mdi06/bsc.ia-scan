"""Reading a contract's code: following proxies and clones to the real implementation, recognising launchpad factories, and
rewriting the recipient inside captured mint calldata. Moved out of monitor.py unchanged; monitor.py re-exports these names."""

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from chain.addresses import DEFAULT_WALLET, VAULT_FACTORIES
from chain.rpc import BscRpcClient, keccak_selector
from smart_wallets import get_cached_smart_wallets

_log = logging.getLogger(__name__)



# Stealth Factory & EIP-1167 / EIP-1967 Proxy Signatures (Option E)
EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"


EIP1967_BEACON_SLOT = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"


BEACON_IMPL_SELECTOR = "0x5c60da1b"  # implementation()


FACTORY_SELECTORS = ["27cca59f", "7d38bb0d", "1737a4bd"]


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
