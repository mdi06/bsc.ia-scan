"""Real holder counts for NFT collections, counted when someone opens a token's card (GET /api/holders).

Why: the only holder source (GoPlus token_security) does not answer for NFT collections, so their `holders` stays at
the discovery placeholder (1) and the modal said "Too early" for collections with 1,000+ minted.

How: ask the contract who owns each token (ownerOf), 500 tokens per eth_call through Multicall3, and count the
distinct owners. About one RPC call per 500 minted (Mystery Pearl: 1,551 minted, 4 calls, ~2.6 s on a public node).

Request budget: counts run only for cards people open, a result is reused for TTL_SEC, concurrent opens of one
collection share one count, at most MAX_CONCURRENT counts run at once, and collections above MAX_MINTED are not counted
(FairSharesDailyNFT, 1.59M minted, would be ~3,200 calls). Contracts that do not answer ownerOf for sequential ids
(ERC-1155, scattered ids, no ownerOf) are remembered as unsupported and not tried again for UNSUPPORTED_RETRY_SEC.

Counts live in memory on the API process: they are lost on a restart and counted again on the next open.
"""
import re
import threading
import time
from typing import Any, Dict, List, Optional

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"   # same address on every EVM chain, BSC included
OWNER_OF = "6352211e"                                         # ownerOf(uint256)
AGGREGATE3 = "82ad56cb"                                       # aggregate3((address,bool,bytes)[])
BATCH = 500
MAX_MINTED = 10_000            # at most 20 eth_calls for one count
TTL_SEC = 600                  # a count is reused for 10 minutes
FAIL_RETRY_SEC = 120           # after an RPC failure, wait before counting the same collection again
UNSUPPORTED_RETRY_SEC = 6 * 3600
MAX_CONCURRENT = 2
WAIT_FOR_SLOT_SEC = 8          # a request waits this long for a free counting slot, then answers "busy"
MAX_FAILED_SHARE = 0.10        # more ids than this without an owner: ids are not sequential, not counted
MAX_ENTRIES = 50_000

_NOT_OWNERS = {"0x" + "0" * 40, "0x000000000000000000000000000000000000dead"}
_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_NFT_RE = re.compile(r"721|NFT")


class Unsupported(Exception):
    """The contract does not answer ownerOf for sequential token ids."""


def is_address(value) -> bool:
    return isinstance(value, str) and bool(_ADDR_RE.match(value))


def countable_minted(record: Dict[str, Any]) -> Optional[int]:
    """How many token ids to read for this record, or None when it is not counted here. Mirrors app.js
    holderCountable(): an ERC-721 style collection (not ERC-1155) with 1..MAX_MINTED minted."""
    std = str(record.get("standard") or "").upper()
    if not _NFT_RE.search(std) or "1155" in std:
        return None
    try:
        minted = int(float(record.get("total_minted") or 0))
    except (TypeError, ValueError, OverflowError):
        return None
    return minted if 1 <= minted <= MAX_MINTED else None


def _word(n: int) -> str:
    return format(n, "064x")


def encode_aggregate3(target: str, calldatas: List[str]) -> str:
    """Multicall3.aggregate3 calldata: every call to `target`, allowFailure = true."""
    n = len(calldatas)
    elems = []
    for cd in calldatas:
        body = cd[2:] if cd.startswith("0x") else cd
        length = len(body) // 2
        padded = body + "0" * ((64 - len(body) % 64) % 64)
        # (address target, bool allowFailure, bytes callData): head of 3 words, then the bytes (length + data)
        elems.append(_word(int(target, 16)) + _word(1) + _word(96) + _word(length) + padded)
    offsets, off = [], 32 * n
    for e in elems:
        offsets.append(_word(off))
        off += len(e) // 2
    return "0x" + AGGREGATE3 + _word(32) + _word(n) + "".join(offsets) + "".join(elems)


def decode_aggregate3(result_hex: str) -> List[Optional[bytes]]:
    """(bool success, bytes returnData)[] -> the return data of each call, None where it failed."""
    raw = bytes.fromhex(result_hex[2:] if result_hex.startswith("0x") else result_hex)

    def word(at: int) -> int:
        return int.from_bytes(raw[at:at + 32], "big")

    base = word(0)
    n = word(base)
    out: List[Optional[bytes]] = []
    for i in range(n):
        at = base + 32 + word(base + 32 + 32 * i)
        ok = word(at)
        data_at = at + word(at + 32)
        length = word(data_at)
        data = raw[data_at + 32:data_at + 32 + length]
        out.append(data if ok else None)
    return out


def _owner(data: Optional[bytes]) -> Optional[str]:
    if not data or len(data) < 32:
        return None
    return "0x" + data[12:32].hex()


def _owners_of(rpc, contract: str, ids) -> List[Optional[str]]:
    calldata = encode_aggregate3(contract, ["0x" + OWNER_OF + _word(i) for i in ids])
    res = rpc.call("eth_call", [{"to": MULTICALL3, "data": calldata}, "latest"])
    if not isinstance(res, str) or not res.startswith("0x") or len(res) < 130:
        raise RuntimeError("eth_call to Multicall3 failed")
    return [_owner(d) for d in decode_aggregate3(res)]


def count_holders(rpc, contract: str, minted: int) -> int:
    """Distinct owners of token ids first..first+minted-1, where first is 0 or 1 (whichever has an owner).
    Raises Unsupported when the ids are not sequential, RuntimeError when the RPC does not answer."""
    probe = [o if o not in _NOT_OWNERS else None for o in _owners_of(rpc, contract, [0, 1])]
    if probe[0]:
        first = 0
    elif probe[1]:
        first = 1
    else:
        raise Unsupported("no owner for token 0 or 1")
    owners = set()
    failed = 0
    last = first + minted
    for start in range(first, last, BATCH):
        for owner in _owners_of(rpc, contract, range(start, min(last, start + BATCH))):
            if owner and owner not in _NOT_OWNERS:
                owners.add(owner)
            else:
                failed += 1            # burned, or an id that does not exist
    if failed > max(5, MAX_FAILED_SHARE * minted):
        raise Unsupported(f"{failed} of {minted} ids have no owner")
    return len(owners)


class HolderCounts:
    """The counts in memory, shared by every request of the API process."""

    def __init__(self, rpc_factory, now=time.time):
        self._rpc_factory = rpc_factory
        self._rpc = None
        self._now = now
        self._lock = threading.Lock()
        self._entries: Dict[str, Dict[str, Any]] = {}     # contract -> {status, holders, at}
        self._inflight: Dict[str, threading.Event] = {}
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT)
        self.version = 0                                   # bumped when a served count changes (feed caches key on it)

    def _rpc_client(self):
        if self._rpc is None:
            self._rpc = self._rpc_factory()
        return self._rpc

    def overlay(self, contract: str) -> Optional[Dict[str, Any]]:
        """What the served records carry for this contract: {"holders": n, "at": t} / {"unsupported": True} / None."""
        e = self._entries.get(str(contract or "").lower())
        if not e:
            return None
        if e.get("holders") is not None:          # "ok", or "error" still holding the last good count
            return {"holders": e["holders"], "at": e.get("counted_at", e["at"])}
        if e["status"] == "unsupported":
            return {"unsupported": True}
        return None

    def _served_key(self, c: str):
        e = self._entries.get(c)
        if not e:
            return None
        return ("n", e["holders"]) if e.get("holders") is not None else (e["status"] == "unsupported")

    def _fresh(self, e: Optional[Dict[str, Any]]) -> bool:
        if not e:
            return False
        age = self._now() - e["at"]
        limit = {"ok": TTL_SEC, "unsupported": UNSUPPORTED_RETRY_SEC, "error": FAIL_RETRY_SEC}[e["status"]]
        return age < limit

    def _answer(self, e: Dict[str, Any]) -> Dict[str, Any]:
        out = {"status": e["status"]}
        if e.get("holders") is not None:          # an "error" keeps the last good count while the RPC is failing
            out["holders"] = e["holders"]
            out["counted_at"] = e.get("counted_at", e["at"])
        return out

    def get(self, contract: str, minted: int) -> Dict[str, Any]:
        c = contract.lower()
        while True:
            with self._lock:
                e = self._entries.get(c)
                if self._fresh(e):
                    return self._answer(e)
                waiter = self._inflight.get(c)
                if waiter is None:
                    waiter = self._inflight[c] = threading.Event()
                    owner = True
                else:
                    owner = False
            if not owner:
                waiter.wait(timeout=60)          # someone else is counting this collection: use their result
                with self._lock:
                    e = self._entries.get(c)
                    if e is not None and self._fresh(e):
                        return self._answer(e)
                return {"status": "busy"}
            try:
                return self._count(c, minted)
            finally:
                with self._lock:
                    self._inflight.pop(c, None)
                waiter.set()

    def _count(self, c: str, minted: int) -> Dict[str, Any]:
        if not self._slots.acquire(timeout=WAIT_FOR_SLOT_SEC):
            return {"status": "busy"}
        try:
            now = self._now()
            try:
                n = count_holders(self._rpc_client(), c, minted)
                entry = {"status": "ok", "holders": n, "at": now, "counted_at": now}
            except Unsupported:
                entry = {"status": "unsupported", "holders": None, "at": now}
            except Exception:
                with self._lock:
                    prev = self._entries.get(c) or {}
                # `at` = when to retry from; the last good count (if any) keeps being served
                entry = {"status": "error", "holders": prev.get("holders"), "at": now,
                         "counted_at": prev.get("counted_at")}
        finally:
            self._slots.release()
        with self._lock:
            served_before = self._served_key(c)
            self._entries[c] = entry
            if len(self._entries) > MAX_ENTRIES:
                oldest = min(self._entries, key=lambda k: self._entries[k]["at"])
                self._entries.pop(oldest, None)
            if self._served_key(c) != served_before:
                self.version += 1
        return self._answer(entry)
