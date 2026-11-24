"""Who really deployed a contract, and which risk flags that supports: launchpad addresses are never a developer wallet,
owner() counts only when it is a plain wallet, a deployer's history excludes proxy clones and factory records, and the
Fund Drain rule needs a real paid mint. Moved out of monitor.py unchanged; monitor.py re-exports these names."""

from typing import Any, Dict, List, Optional

import filters
from chain.addresses import VAULT_FACTORIES, known_infra_addresses
from chain.rpc import keccak_selector



def launchpad_addresses() -> set:
    """Addresses that deploy or own other people's contracts: never a developer wallet."""
    return set(known_infra_addresses()) | {a.lower() for a in VAULT_FACTORIES}


def resolve_deployer_from_owner(rpc: "BscRpcClient", contract_addr: str) -> Optional[str]:
    """owner() of the contract, only when it is a plain wallet. A contract owner (a launchpad factory, a multisig) says
    nothing about who deployed it, so it is no deployer at all (None)."""
    owner_res = rpc.call_contract(contract_addr, keccak_selector("owner()"))
    if not owner_res or len(owner_res) < 66:
        return None
    owner = "0x" + owner_res[-40:].lower()
    if owner == "0x" + "0" * 40 or owner in launchpad_addresses():
        return None
    code = rpc.get_code(owner)
    if code and len(code) > 2:
        return None
    return owner


def count_prior_deployments(history: List[Dict[str, Any]], deployer: str, contract: str) -> List[Dict[str, Any]]:
    """The records whose deployer is known to be `deployer`, other than `contract`. Proxy clones and factory records are
    left out: their stored `deployer` is the first caller the worker saw, not whoever created the contract."""
    deployer, contract = (deployer or "").lower(), (contract or "").lower()
    return [
        h for h in history
        if (h.get("deployer") or "").lower() == deployer
        and (h.get("contract") or "").lower() != contract
        and not h.get("is_proxy_clone") and not h.get("is_factory")
    ]


def fund_drain_applies(details: Dict[str, Any], stats: Dict[str, Any], is_paid_mint: bool, contract_bnb: float) -> bool:
    """True when "paid mint, contract empty, no pool" really says the mint money went missing. It does not for:
    an NFT collection (no DEX pool is expected, and the owner withdrawing revenue is normal), or a token that is not a
    mint at all (a pre-minted supply with the creation fee recorded as a "mint price"). It needs a capped mint that is
    not pre-minted, or a fair-mint dispenser, with real mints, no DEX liquidity and no pool of any kind."""
    cls = details.get("classification") or {}
    std = str(cls.get("standard") or details.get("standard") or "")
    if cls.get("is_nft") or filters._NFT_STANDARD_RE.search(std.upper()):
        return False
    shape = {"standard": std, "max_supply": details.get("max_supply"), "total_minted": details.get("total_minted"),
             "tokens_per_mint": details.get("tokens_per_mint")}
    if filters.mint_progress_bucket(shape) is None and "FAIR-MINT" not in std.upper():
        return False
    tot_minted = details.get("total_minted", 0) or 0
    return bool(tot_minted > 20 and is_paid_mint and contract_bnb < 0.01
                and (stats.get("lp_usd", 0) or 0) < 100 and not stats.get("pair_url"))
