"""Does a contract reference the Agent ID registry or a tracked launchpad? Moved out of monitor.py unchanged; monitor.py
re-exports these names (the purge script and its tests patch contract_links_to_registry there)."""

from typing import Any, Dict, List, Optional

from chain.addresses import TRACKED_REGISTRY_BYTES
from chain.bytecode import BEACON_IMPL_SELECTOR, EIP1967_BEACON_SLOT, EIP1967_IMPL_SLOT
from chain.rpc import BscRpcClient



def matches_tracked_registry(data_str: str) -> bool:
    """Returns True if the given calldata or bytecode references any tracked registry or launchpad."""
    if not data_str:
        return False
    d = data_str.lower()
    return any(b in d for b in TRACKED_REGISTRY_BYTES)


def _linked_code(code: Optional[str]) -> bool:
    return bool(code and len(code) > 2 and matches_tracked_registry(code))


def contract_links_to_registry(rpc, contract_addr: str) -> bool:
    """True when the contract's bytecode, or the bytecode it delegates to, references the Agent ID registry or
    another tracked launchpad. This is what makes a contract an agent mint rather than any token that happens to be
    on BSC. "Delegates to" covers an EIP-1167 minimal clone (the implementation address is inside the code, which is
    how most agent mints are deployed), an EIP-1967 proxy (implementation slot) and an EIP-1967 beacon proxy. An RPC
    failure counts as "not linked": the contract is looked at again the next time a tracked wallet calls it."""
    try:
        code = rpc.get_code(contract_addr)
        if _linked_code(code):
            return True
        impls = []
        if code and code.startswith("0x363d3d373d3d3d363d73") and len(code) >= 2 + 22 * 2 + 40:
            impls.append("0x" + code[22:62])                       # EIP-1167: 0x363d3d373d3d3d363d73 <20-byte impl> ...
        slot = rpc.get_storage_at(contract_addr, EIP1967_IMPL_SLOT)
        if slot and len(slot) >= 42 and int(slot, 16) != 0:
            impls.append("0x" + slot[-40:])
        beacon = rpc.get_storage_at(contract_addr, EIP1967_BEACON_SLOT)
        if beacon and len(beacon) >= 42 and int(beacon, 16) != 0:
            b_addr = "0x" + beacon[-40:]
            res = rpc.call_contract(b_addr, BEACON_IMPL_SELECTOR)
            if res and len(res) >= 42 and int(res, 16) != 0:
                impls.append("0x" + res[-40:])
        return any(_linked_code(rpc.get_code(i)) for i in impls)
    except Exception:
        return False
