"""Reading how many of a contract have been minted and what its cap is, straight from the chain, with a short memo so a sweep that
asks twice does not hit the node twice. Moved out of monitor.py unchanged; monitor.py re-exports these names (and the shared
`_GETTER_MEMO` dict), so `monitor.read_supply` keeps working for worker.py and the tests."""

import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from chain.rpc import BscRpcClient, keccak_selector

_log = logging.getLogger(__name__)



MAX_SUPPLY_GETTERS = ("maxSupply()", "MAX_SUPPLY()", "max_supply()", "cap()", "MAX_TOKENS()", "maxTokens()",
                      "maxBoxes()", "MAX_BOXES()")


MINTED_GETTERS = ("totalMinted()", "_totalMinted()", "totalSupply()", "currentSupply()", "tokenCount()",
                  "nextTokenId()", "_nextTokenId()", "currentTokenId()", "lifetimeCommitted()", "miningCommitted()")


# Which getter answered for a contract, so the next read asks that one first instead of walking the whole list
# (each getter that does not exist is a wasted request). (address, "max" | "minted") -> (getter, expiry time).
# Entries expire after about an hour (spread out, so they do not all expire together): a contract that later
# gains an earlier-listed getter is then picked up again, and the first answering getter in list order wins as before.
_GETTER_MEMO: Dict[Tuple[str, str], Tuple[str, float]] = {}


GETTER_MEMO_TTL_SEC = 3600.0


def _memo_get(addr: str, kind: str) -> Optional[str]:
    ent = _GETTER_MEMO.get((addr.lower(), kind))
    if ent is None or time.time() >= ent[1]:
        return None
    return ent[0]


def _memo_put(addr: str, kind: str, getter: str) -> None:
    _GETTER_MEMO[(addr.lower(), kind)] = (getter, time.time() + GETTER_MEMO_TTL_SEC * (1.0 + 0.5 * random.random()))


def _memo_forget(addr: str, kind: str) -> None:
    _GETTER_MEMO.pop((addr.lower(), kind), None)


def _minted_kind(token_decimals: Optional[int]) -> str:
    return "minted" if token_decimals is None else "minted_tok"


def _memo_first(getters: Tuple[str, ...], remembered: Optional[str]) -> Tuple[str, ...]:
    """`getters` with the remembered one moved to the front (the rest keep their order)."""
    if remembered and remembered in getters:
        return (remembered,) + tuple(g for g in getters if g != remembered)
    return getters


def read_supply(
    rpc: BscRpcClient,
    target_addrs: List[str],
    token_decimals: Optional[int] = None,
    need_max: bool = True,
    meta: Optional[Dict[str, Any]] = None,
    tokens_per_mint: Optional[float] = None,
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
    A BEP-20 cap under one whole token (maxSupply() = 220 on a token with 18 decimals) is not a supply but a COUNT of mint
    slots: a fair-mint dispenser counts mints in its cap getter while totalSupply() is a token amount. Scaled by
    10**decimals it read 2.2e-16 next to a total of 1,040,000, so 2,914 drops looked sold out and the sweeper dropped them.
    It is returned as meta["cap_slots"]; the cap in tokens is slots x `tokens_per_mint` when that is known, else None.
    need_max=False skips the cap probes (about 8 calls) when the cap is already known.
    `meta`, if given, gets "cap_status": "capped" (a cap was read), "unlimited" (a cap getter answered with an
    effectively infinite value) or "unknown" (no cap getter answered), so "no cap" can be told apart from "not read"."""
    def scale(raw: int):
        if token_decimals is None:
            return raw
        return raw / (10 ** token_decimals)

    minted_getters = MINTED_GETTERS
    if token_decimals is not None:
        minted_getters = ("totalSupply()",) + tuple(g for g in MINTED_GETTERS if g != "totalSupply()")

    # tokens try totalSupply() first, so what answered is remembered apart from the NFT / dispenser order
    minted_kind = _minted_kind(token_decimals)
    max_supply = None
    total_minted = None
    cap_status = "unknown"
    cap_slots = None
    for t_addr in target_addrs:
        if need_max and max_supply is None and cap_slots is None and cap_status != "unlimited":
            remembered = _memo_get(t_addr, "max")
            answered = None
            for sel_name in _memo_first(MAX_SUPPLY_GETTERS, remembered):
                s_res = rpc.call_contract(t_addr, keccak_selector(sel_name))
                if s_res and len(s_res) >= 66:
                    try:
                        raw_s = int(s_res[:66], 16)
                        if raw_s >= 2**128 or raw_s == (2**256 - 1) or raw_s >= 10**30:
                            max_supply = None  # effectively unlimited
                            cap_status = "unlimited"
                            answered = sel_name
                            break
                        if token_decimals is not None and 0 < raw_s < 10 ** token_decimals:
                            cap_slots = raw_s                     # a count of mint slots, not a token amount
                            if tokens_per_mint and tokens_per_mint > 0:
                                max_supply = raw_s * float(tokens_per_mint)
                        else:
                            max_supply = scale(raw_s)
                        cap_status = "capped"
                        answered = sel_name
                        break
                    except Exception as exc:
                        _log.debug("could not parse max supply %r for %s: %s", s_res, t_addr, exc)
            if answered is None:
                if remembered:
                    _memo_forget(t_addr, "max")      # the remembered getter stopped answering: probe afresh next time
            elif answered != remembered:
                _memo_put(t_addr, "max", answered)

        if total_minted is None or total_minted == 0:
            remembered = _memo_get(t_addr, minted_kind)
            answered = None
            for sel_name in _memo_first(minted_getters, remembered):
                m_res = rpc.call_contract(t_addr, keccak_selector(sel_name))
                if m_res and len(m_res) >= 66:
                    try:
                        raw_m = int(m_res[:66], 16)
                        if "nextTokenId" in sel_name and raw_m > 0:
                            raw_m = raw_m - 1  # nextTokenId-style counters point at the next id (ids start at 1)
                        total_minted = scale(raw_m)
                        answered = sel_name
                        break
                    except Exception as exc:
                        _log.debug("could not parse total minted %r for %s: %s", m_res, t_addr, exc)
            if answered is None:
                if remembered:
                    _memo_forget(t_addr, minted_kind)   # the remembered getter stopped answering: probe afresh next time
            elif answered != remembered:
                _memo_put(t_addr, minted_kind, answered)
    if meta is not None:
        meta["cap_status"] = cap_status
        if cap_slots is not None:
            meta["cap_slots"] = cap_slots
    return max_supply, total_minted


def read_minted_batch(rpc, jobs: List[Tuple[List[str], Optional[int]]]) -> List[Optional[float]]:
    """total_minted for many contracts with a handful of batched requests, for the sweeper's hot path.

    `jobs` is a list of (target_addrs, token_decimals), as for read_supply. A job is answered here only when its
    first target has a remembered minted getter (see _GETTER_MEMO) and that getter answers; the result is then the
    same number read_supply would give. Everything else is None, and the caller reads those with read_supply, which
    also learns the getter: new contracts, expired or failed memos, and contracts whose companion (core()) has to be
    asked when the first answers nothing or zero.
    `rpc` needs call_batch(); without it the same calls go out one by one."""
    out: List[Optional[float]] = [None] * len(jobs)
    asked: List[Tuple[int, str, str]] = []          # (job index, getter, address)
    for i, (targets, _dec) in enumerate(jobs):
        if not targets:
            continue
        getter = _memo_get(targets[0], _minted_kind(_dec))
        if getter:
            asked.append((i, getter, targets[0]))
    if not asked:
        return out

    batch = getattr(rpc, "call_batch", None)
    if batch is not None:
        calls = [("eth_call", [{"to": to, "data": keccak_selector(g)}, "latest"]) for _i, g, to in asked]
        replies = batch(calls)
    else:
        replies = [rpc.call_contract(to, keccak_selector(g)) for _i, g, to in asked]

    for (i, getter, addr), res in zip(asked, replies):
        targets, dec = jobs[i]
        if not res or res == "0x" or len(res) < 66:
            _memo_forget(addr, _minted_kind(dec))   # stopped answering: read_supply probes afresh
            continue
        try:
            raw = int(res[:66], 16)
        except ValueError:
            _memo_forget(addr, _minted_kind(dec))
            continue
        if "nextTokenId" in getter and raw > 0:
            raw -= 1
        minted = raw if dec is None else raw / (10 ** dec)
        if len(targets) > 1 and not minted:
            continue                                # read_supply would go on to the companion
        out[i] = minted
    return out
