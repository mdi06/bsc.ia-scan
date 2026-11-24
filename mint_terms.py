"""What a free-mint hunter checks before minting: slots left (from supply), the per-wallet limit, the mint window, the price, and
who is allowed to mint (open, Agent ID holders only, whitelist, owner only). The probe simulates the mint call and reads the revert
reason. Moved out of monitor.py unchanged; monitor.py re-exports these names."""

import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from chain.addresses import DEFAULT_WALLET
from chain.bytecode import replace_calldata_recipient, resolve_full_bytecode
from chain.rpc import BscRpcClient, keccak_selector

_log = logging.getLogger(__name__)

PER_WALLET_GETTERS = (
    ("MAX_PER_WALLET()", "0f2cdd6c"), ("maxPerWallet()", "453c2310"), ("maxMintPerWallet()", "b228d925"),
    ("MAX_MINT_PER_WALLET()", "b19960e6"), ("maxMintsPerWallet()", "f516a2e6"), ("MAX_MINTS_PER_WALLET()", "e78fba22"),
    ("mintLimitPerWallet()", "e0f9b479"), ("walletLimit()", "3c8463a1"), ("maxPerAddress()", "639814e0"),
    ("MAX_PER_ADDRESS()", "0aaef285"), ("maxMintPerAddress()", "572849c4"), ("perWalletLimit()", "de42c69e"),
    ("FREE_PER_WALLET()", "cd90e5af"), ("maxFreePerWallet()", "a7027357"), ("MAX_FREE_PER_WALLET()", "98710d1e"),
    ("freePerWallet()", "5b74efcf"),
)

MINT_START_GETTERS = (
    ("mintStart()", "255e4685"), ("mintStartTime()", "931e2e49"), ("startTime()", "78e97925"),
    ("saleStartTime()", "1cbaee2d"), ("publicSaleStart()", "3360caa0"), ("publicSaleStartTime()", "6bb7b1d9"),
    ("startTimestamp()", "e6fd48bc"), ("mintStartTimestamp()", "a0e389de"), ("saleStart()", "ab0bcc41"),
)

MINT_END_GETTERS = (
    ("mintEnd()", "ea2b4ab2"), ("mintEndTime()", "717a002b"), ("endTime()", "3197cbb6"),
    ("saleEndTime()", "ed338ff1"), ("publicSaleEnd()", "ec9b5cb2"), ("publicSaleEndTime()", "1e4d185f"),
    ("endTimestamp()", "a85adeab"), ("mintEndTimestamp()", "cc6ac2f5"), ("saleEnd()", "c10b9358"),
)

MERKLE_ROOT_GETTERS = (
    ("merkleRoot()", "2eb4a7ab"), ("whitelistMerkleRoot()", "aa98e0c6"), ("allowlistMerkleRoot()", "293108e0"),
    ("whitelistRoot()", "386bfc98"), ("allowlistRoot()", "9c9c6669"), ("presaleMerkleRoot()", "22212e2b"),
)

# A wallet with no Agent ID, no whitelist entry and no history: if it can mint, anyone can.
MINT_PROBE_ADDRESS = "0x000000000000000000000000000000000000c0de"

_PER_WALLET_MAX = 100_000             # above this a "limit" is a sentinel for "no limit"

_TS_MIN, _TS_MAX = 1_500_000_000, 4_000_000_000   # plausible unix seconds (2017 .. 2096)


def _first_uint_getter(rpc: BscRpcClient, contract: str, code: str, getters, accept) -> Optional[Tuple[str, int]]:
    """(name, value) of the first getter present in `code` whose answer passes `accept`, else None."""
    for name, sel in getters:
        if ("63" + sel) not in code:
            continue
        res = rpc.call_contract(contract, "0x" + sel)
        if not res or len(res) < 66:
            continue
        try:
            val = int(res[:66], 16)
        except ValueError:
            continue
        if accept(val):
            return name, val
    return None


def _sels(names) -> Dict[str, str]:
    """{selector hex without 0x: name} for custom-error / getter names (keccak; empty if unavailable)."""
    out = {}
    for n in names:
        sel = keccak_selector(n)
        if sel:
            out[sel[2:].lower()] = n
    return out

# Why a simulated mint was refused, from its revert: an Error(string) message or a custom error selector.
# Seen on live drops: "Agent ID required", AgentIdRequired(), NoAgent(); "No tokens to claim", NothingToClaim();
# "wrong fee", InsufficientBNB(), OverMaxCost() (the price, not who may mint).
_REVERT_KINDS = (
    # only holders of the project's own NFT / token may claim (seen: NotTokenOwner() on claim(tokenId), HolderRequired())
    ("holder_only", ("not token owner", "not the token owner", "not nft owner", "holder required", "holders only",
                     "only holders", "not a holder", "not holder")),
    ("agent_id", ("agent id", "agentid", "agent required", "not agent", "no agent", "not an agent", "agent only",
                  "only agent", "requires agent", "need agent", "must hold", "not registered")),
    # an admin-only function: the stored call is not a public mint (seen: "not owner" on MACHINES)
    ("owner_only", ("not owner", "not the owner", "caller is not the owner", "ownable", "only owner", "onlyowner",
                    "not authorized", "unauthorized", "access denied", "forbidden", "not minter", "caller is not a minter")),
    ("whitelist", ("whitelist", "allowlist", "allow list", "allow-list", "merkle", "invalid proof", "not eligible",
                   "ineligible", "no tokens to claim", "nothing to claim", "not allowed")),
    ("not_open", ("not started", "not yet started", "hasn't started", "has not started", "not open", "not live",
                  "not on sale", "not for sale", "sale not", "sale is not",
                  "mint closed", "sale closed", "not active", "inactive", "paused")),
    ("ended", ("ended", "has ended", "is over", "mint over", "sale over", "minting over", "expired", "finished")),
    ("sold_out", ("sold out", "max supply", "exceeds supply", "supply exceeded", "exceeds max supply", "minted out")),
    ("payment", ("fee", "price", "payment", "insufficient", "value", "bnb", "cost", "funds", "pay",
                 "exceeds balance", "exceeds allowance")),
)

_REVERT_ERROR_SELECTORS = {
    **{k: "owner_only" for k in _sels(["OwnableUnauthorizedAccount(address)", "Unauthorized()", "NotOwner()",
                                       "OnlyOwner()", "NotMinter()", "CallerNotOwner()", "NotAuthorized()"])},
    **{k: "agent_id" for k in _sels(["AgentIdRequired()", "NoAgent()", "NotAgent()", "NotAnAgent()", "NoAgentId()",
                                     "NotAgentHolder()", "AgentRequired()", "MissingAgentId()", "NotRegistered()",
                                     "OnlyAgents()"])},
    # Unnamed custom errors seen on live drops where a fresh wallet gets the error and an Agent ID wallet mints:
    # 0xea11784e on 10 drops, 0x3cb97240 on 4 (one-off codes are left out rather than guessed).
    "ea11784e": "agent_id",
    "3cb97240": "agent_id",
    **{k: "whitelist" for k in _sels(["NotWhitelisted()", "NotAllowlisted()", "NotAllowListed()", "InvalidProof()",
                                      "InvalidMerkleProof()", "NotEligible()", "NothingToClaim()", "NoTokensToClaim()"])},
    **{k: "not_open" for k in _sels(["MintNotStarted()", "SaleNotStarted()", "MintNotActive()", "MintNotOpen()",
                                     "SaleNotActive()", "NotStarted()", "NotActive()", "EnforcedPause()", "Paused()",
                                     # the start time is the argument (read into terms["start"])
                                     "NotOpenYet(uint256)", "TooEarly(uint256)", "MintNotStarted(uint256)",
                                     "SaleNotStarted(uint256)",
                                     # the creator has not configured the mint yet (Predict Agent)
                                     "GameNotSet()"])},
    **{k: "holder_only" for k in _sels(["NotTokenOwner()", "HolderRequired()", "NotHolder()", "NotNFTOwner()",
                                        "NotTokenHolder()"])},
    **{k: "ended" for k in _sels(["MintEnded()", "SaleEnded()", "MintClosed()", "Ended()"])},
    **{k: "sold_out" for k in _sels(["SoldOut()", "MaxSupplyReached()", "MaxSupplyExceeded()", "ExceedsMaxSupply()"])},
    **{k: "payment" for k in _sels(["InsufficientBNB()", "OverMaxCost()", "InsufficientPayment()", "WrongPrice()",
                                    "IncorrectPayment()", "InvalidPayment()", "InsufficientFunds()", "WrongValue()",
                                    "InsufficientValue()", "IncorrectPrice()", "InsufficientFee()", "WrongFee()",
                                    # the price is in the arguments: the probe retries with it (see _price_from_revert)
                                    "FeeRequired(uint256,uint256)", "TollRequired(uint256,uint256)",
                                    "EitherPrice(uint256,uint256,uint256)", "PayRequired(uint256,uint256,uint256)",
                                    "ShortPayment(uint256,uint256)", "InsufficientPayment(uint256,uint256)",
                                    "IncorrectPayment(uint256)", "WrongPayment(uint256)"])},
}


def classify_revert(revert: Any) -> Optional[str]:
    """"agent_id" / "whitelist" / "holder_only" / "owner_only" / "not_open" / "ended" / "sold_out" / "payment" for a revert from
    BscRpcClient.call_with_revert, or None when it says nothing we recognise. Agent / whitelist phrases are checked
    before payment ones: "Agent ID required" must not read as a price problem."""
    if not isinstance(revert, dict):
        return None
    data = revert.get("data")
    if isinstance(data, dict):            # some nodes nest it: {"data": {"data": "0x..."}}
        data = data.get("data")
    text = str(revert.get("message") or "")
    if isinstance(data, str) and data.startswith("0x") and len(data) >= 10:
        sel = data[2:10].lower()
        if sel == "08c379a0":             # Error(string)
            try:
                text += " " + bytes.fromhex(data[138:]).rstrip(b"\0").decode("utf-8", "ignore")
            except ValueError:
                pass
        elif sel in _REVERT_ERROR_SELECTORS:
            return _REVERT_ERROR_SELECTORS[sel]
    low = text.lower()
    for kind, phrases in _REVERT_KINDS:
        if any(p in low for p in phrases):
            return kind
    return None


def _revert_data(revert: Any) -> str:
    """The revert's hex data ("0x..."), or "" when it has none."""
    if not isinstance(revert, dict):
        return ""
    data = revert.get("data")
    if isinstance(data, dict):
        data = data.get("data")
    return data.lower() if isinstance(data, str) and data.startswith("0x") else ""


def _revert_uint_args(revert: Any) -> List[int]:
    """The 32-byte arguments of a custom-error revert (not Error(string)), as ints."""
    data = _revert_data(revert)
    if len(data) < 74 or data[2:10] == "08c379a0":
        return []
    body = data[10:]
    out = []
    for i in range(0, len(body) - 63, 64):
        try:
            out.append(int(body[i:i + 64], 16))
        except ValueError:
            return []
    return out

_PRICE_MIN_WEI, _PRICE_MAX_WEI = 10**12, 10**20     # 0.000001 .. 100 BNB


def _price_from_revert(revert: Any) -> Optional[int]:
    """The price a payment refusal names in its arguments (e.g. PayRequired(min, max, sent)), or None. The largest
    plausible amount is taken: with it, a mint that takes one of several prices still goes through."""
    vals = [v for v in _revert_uint_args(revert) if _PRICE_MIN_WEI <= v <= _PRICE_MAX_WEI]
    return max(vals) if vals else None


def _start_from_revert(revert: Any) -> Optional[int]:
    """A start time named in a not-open refusal (e.g. NotOpenYet(start)), or None."""
    vals = [v for v in _revert_uint_args(revert) if _TS_MIN <= v <= _TS_MAX]
    return vals[0] if vals else None


def _simulate(rpc: BscRpcClient, contract: str, calldata: Optional[str], value_wei: Any,
              sender: str) -> Tuple[Optional[bool], Optional[str], Any]:
    """simulate_mint_detail plus the raw revert (for its arguments)."""
    data = replace_calldata_recipient(calldata, sender) if calldata else "0x"
    try:
        value = int(value_wei or 0)
    except (TypeError, ValueError):
        value = 0
    params = [{"from": sender, "to": contract, "data": data or "0x", "value": hex(value)}, "latest",
              {sender: {"balance": hex(10**21)}}]
    try:
        if hasattr(rpc, "call_with_revert"):
            ok, res = rpc.call_with_revert("eth_call", params)
            if ok is None:
                return None, None, None
            return (True, None, None) if ok else (False, classify_revert(res), res)
        return (True, None, None) if rpc.call("eth_call", params) is not None else (False, None, None)
    except Exception:
        return None, None, None


def simulate_mint_detail(rpc: BscRpcClient, contract: str, calldata: Optional[str], value_wei: Any,
                         sender: str) -> Tuple[Optional[bool], Optional[str]]:
    """(True, None) if a mint from `sender` would go through right now; (False, kind) for a refusal, with kind
    from classify_revert (None if unrecognised); (None, None) when no RPC endpoint answered. eth_call with a state
    override giving the sender a large balance, so a known price never fails it."""
    return _simulate(rpc, contract, calldata, value_wei, sender)[:2]


def mint_call_is_usable(calldata: Optional[str], code: str) -> bool:
    """False when the stored mint call cannot be a call to this contract: contract creation bytecode (a deploy
    transaction's input, 0x6080... / 0x6101..., kilobytes long), or a function selector absent from the contract's
    (proxy-resolved) bytecode. A plain value transfer ("0x") is fine. Unknown bytecode is given the benefit of the doubt."""
    cd = str(calldata or "").lower()
    if cd in ("", "0x", "recv", "receive", "none"):
        return True
    if not cd.startswith("0x") or len(cd) < 10:
        return True
    if len(cd) > 2000 and cd[2:4] in ("60", "61") and "6040" in cd[2:20]:
        return False
    sel = cd[2:10]
    if not code:
        return True
    return ("63" + sel) in code or (sel.startswith("00") and ("62" + sel[2:]) in code)

# A boolean "is the mint switched on" getter (seen: mintOpen() on 8 of 29 live drops). 0 = closed, 1 = open.
MINT_OPEN_GETTERS = tuple((n, sel) for sel, n in _sels([
    "mintOpen()", "isMintOpen()", "mintActive()", "isMintActive()", "mintEnabled()", "mintingEnabled()",
    "saleActive()", "isSaleActive()", "saleIsActive()", "publicSaleActive()", "isPublicSaleActive()",
    "mintIsActive()", "publicMintOpen()", "claimOpen()", "isClaimOpen()",
]).items())

MINT_TERMS_VERSION = 5

# Agent ID checks written into the drop's own bytecode: an agent custom error (PUSH4 <selector>) or the
# "Agent ID" revert text. On 53 live drops of known gate this matched none of the 20 open ones (no false alarm)
# and 11 of the 33 Agent-ID-gated ones: the others call a shared checker contract, so the simulated mint's revert
# stays the first signal and this is only the fallback when the simulation can't decide (sold out, paid with an
# unread price, unusable mint call).
_AGENT_ERROR_SELECTORS = tuple(k for k, v in _REVERT_ERROR_SELECTORS.items() if v == "agent_id")

# ... or a hasAgentId(address) function, which asks the Agent ID registry (seen on RUMBLE, Logger: 0 for a fresh
# wallet, 1 for an Agent ID holder; both mints answer "Agent ID required").
_AGENT_FUNCTION_SELECTORS = tuple(_sels(["hasAgentId(address)", "hasAgentID(address)"]).keys())

_AGENT_TEXT_MARKERS = tuple(t.encode().hex() for t in ("Agent ID", "agent ID", "Agent Id", "AgentID required"))


def has_agent_id_check(code: str) -> bool:
    """True if the (proxy-resolved) bytecode itself carries an Agent ID check (see above)."""
    code = (code or "").lower()
    return (any(("63" + sel) in code for sel in _AGENT_ERROR_SELECTORS + _AGENT_FUNCTION_SELECTORS)
            or any(m in code for m in _AGENT_TEXT_MARKERS))


def probe_mint_terms(
    rpc: BscRpcClient,
    contract: str,
    calldata: Optional[str],
    value_wei: Any,
    code: Optional[str] = None,
    cap_status: Optional[str] = None,
    holder_wallets: Iterable[str] = (),
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """The mint terms of one drop (see the block comment above). `code` is the (proxy-resolved) bytecode if the
    caller already has it; `holder_wallets` are Agent ID holders to test the mint from (DEFAULT_WALLET first).

    Gate, from simulated mints and why they were refused:
      - a fresh wallet can mint                                   -> "open"
      - a fresh wallet is refused for its Agent ID / allow list   -> "agent_id" / "whitelist" (from the revert)
      - a fresh wallet can't, an Agent ID holder can             -> "whitelist" if a Merkle root is set, else "agent_id"
      - otherwise                                                 -> "unknown"
    A refusal for the price, timing or supply says nothing about who may mint, so it never sets the gate; timing
    and supply refusals are recorded as "state" ("not_open" / "ended" / "sold_out").
    "open": False when a mintOpen()-style switch reads 0. "mint_call": "invalid" when the stored call can't work."""
    now = time.time() if now is None else now
    terms: Dict[str, Any] = {"checked_at": int(now), "v": MINT_TERMS_VERSION}
    if cap_status in ("capped", "unlimited", "unknown"):
        terms["cap"] = cap_status
    if code is None:
        raw = rpc.get_code(contract)
        try:
            code = resolve_full_bytecode(rpc, contract, raw)[0] or raw
        except Exception:
            code = raw
    code = (code or "").lower()

    pw = _first_uint_getter(rpc, contract, code, PER_WALLET_GETTERS, lambda v: 0 < v <= _PER_WALLET_MAX)
    if pw:
        terms["per_wallet_getter"], terms["per_wallet"] = pw[0], int(pw[1])
    start = _first_uint_getter(rpc, contract, code, MINT_START_GETTERS, lambda v: _TS_MIN <= v <= _TS_MAX)
    if start:
        terms["start"] = int(start[1])
    end = _first_uint_getter(rpc, contract, code, MINT_END_GETTERS, lambda v: _TS_MIN <= v <= _TS_MAX)
    if end and (not start or end[1] > start[1]):
        terms["end"] = int(end[1])
    switch = _first_uint_getter(rpc, contract, code, MINT_OPEN_GETTERS, lambda v: v in (0, 1))
    if switch:
        terms["open"] = bool(switch[1])
    merkle = _first_uint_getter(rpc, contract, code, MERKLE_ROOT_GETTERS, lambda v: v != 0)

    agent_in_code = has_agent_id_check(code)
    holders = []
    for w in [DEFAULT_WALLET, *holder_wallets]:
        if isinstance(w, str) and len(w) == 42 and w.startswith("0x") and w.lower() not in (h.lower() for h in holders):
            holders.append(w)
    holders = holders[:2]

    reasons: List[Optional[str]] = []
    info: Dict[str, Any] = {}              # price / start time named in a refusal, holder-only claims
    gate = "unknown"
    stored_ok = mint_call_is_usable(calldata, code)
    if stored_ok:
        gate, why, worked = _gate_for_call(rpc, contract, calldata, value_wei, holders, merkle, code, info)
        reasons += why
    else:
        terms["mint_call"] = "invalid"

    # The stored call gave no verdict (unusable, admin-only, or refused for a reason that says nothing about who may
    # mint): try the contract's own public mint functions. On the live "unknown" drops this settled 12 of 42 (the
    # stored call was often the deployment bytecode or an owner-only function).
    tried = 0
    if gate == "unknown":
        stored_sel = str(calldata or "").lower()[2:10]
        for sig, sel, build in ALT_MINT_CALLS:
            if tried >= _ALT_MINT_MAX or sel == stored_sel or ("63" + sel) not in code:
                continue
            tried += 1
            alt = "0x" + sel + build(DEFAULT_WALLET)
            alt_gate, why, worked = _gate_for_call(rpc, contract, alt, value_wei, holders, merkle, code, info)
            reasons += why
            if alt_gate != "unknown":
                gate = alt_gate
                terms["alt_call"] = sig
                if worked:
                    terms["working_call"] = alt   # a public mint that went through: the worker repairs the stored call
                break

    # No mint function the radar knows: no stored call (a plain transfer was refused without a reason) and none of the usual
    # mint functions in the contract. Seen: the Agent ID registry contracts themselves. The site hides the Mint button.
    if (gate == "unknown" and tried == 0 and stored_ok and str(calldata or "").lower() in ("", "0x")
            and not any(reasons)):
        terms["mint_call"] = "none"
    if gate == "unknown" and "whitelist" in reasons:
        gate = "whitelist"                 # e.g. a claim contract: no allocation for either wallet
    if gate == "unknown" and "holder_only" in reasons:
        gate = "whitelist"                 # only holders of the project's NFT / token may claim
        info["holders"] = True
    if gate == "whitelist" and info.get("holders"):
        terms["gate_from"] = "holders"
    if info.get("price_wei"):
        terms["price_wei"] = int(info["price_wei"])   # read from a payment refusal (e.g. PayRequired(min, max, sent))
    if info.get("start") and "start" not in terms:
        terms["start"] = int(info["start"])           # read from a not-open refusal (e.g. NotOpenYet(start))
    if gate == "unknown" and agent_in_code:
        gate = "agent_id"                  # no simulation decided; the bytecode carries an Agent ID check
        terms["gate_from"] = "bytecode"
    terms["gate"] = gate
    for state in ("sold_out", "ended", "not_open"):
        if state in reasons:
            terms["state"] = state
            break
    if "payment" in reasons and gate != "open":
        terms["paid"] = True               # refused for the price: a paid mint whose price was not read
    return terms


def _gate_for_call(rpc: BscRpcClient, contract: str, calldata: Optional[str], value_wei: Any, holders: List[str],
                   merkle: Any, code: str = "", info: Optional[Dict[str, Any]] = None,
                   ) -> Tuple[str, List[Optional[str]], bool]:
    """(gate, refusal kinds, whether any wallet's mint went through) for one mint call, from a fresh wallet and up
    to two Agent ID wallets. A refusal for the price, timing, supply or an admin-only function never sets the gate.

    Retried once each: with the price a payment refusal names (sending 0 BNB, every wallet fails on the price before
    the Agent ID check; seen on Cinnavault, Prismfolio), and with no value after a bare revert (a free mint function
    that is not payable refuses any BNB without a reason; seen on RUMBLE). `info` collects what refusals reveal."""
    info = {} if info is None else info
    reasons: List[Optional[str]] = []
    try:
        value = int(value_wei or 0)
    except (TypeError, ValueError):
        value = 0
    ok, why, res = _simulate(rpc, contract, calldata, value, MINT_PROBE_ADDRESS)
    if ok:
        return "open", reasons, True
    if ok is False:
        if why == "payment" and "price_wei" not in info:
            price = _price_from_revert(res)
            if price and price > value:
                info["price_wei"] = price
                return _gate_for_call(rpc, contract, calldata, price, holders, merkle, code, info)
        if why is None and value > 0 and not _revert_data(res) and not info.get("_no_value"):
            info["_no_value"] = True
            g, r2, worked = _gate_for_call(rpc, contract, calldata, 0, holders, merkle, code, info)
            if g != "unknown" or worked:
                return g, r2, worked
        if why == "not_open" and not info.get("start"):
            start = _start_from_revert(res)
            if start:
                info["start"] = start
        why = _holder_claim_kind(why, res, calldata, code)
    reasons.append(why)
    gate = why if why in ("agent_id", "whitelist") else "whitelist" if why == "holder_only" else "unknown"
    if why == "holder_only":
        info["holders"] = True
    for w in holders:
        h_ok, h_why, h_res = _simulate(rpc, contract, calldata, value, w)
        if h_ok:
            if gate == "unknown" and ok is False:
                gate = "whitelist" if merkle else "agent_id"
            return gate, reasons, True
        reasons.append(_holder_claim_kind(h_why, h_res, calldata, code) if h_ok is False else h_why)
    return gate, reasons, False

# Claims by token id: claim(uint256) on a contract that tracks claims per id, or claimCard(uint256). Their "not owner"
# / "already claimed" refusals mean "only the holder of that NFT may claim", not an admin-only function.
# Seen: Vitaliq, Contextgraph (NotOwner()), Meow Token ("card already claimed").
_CLAIM_BY_ID_SEL = (keccak_selector("claim(uint256)") or "0x")[2:]

_CLAIM_CARD_SEL = (keccak_selector("claimCard(uint256)") or "0x")[2:]

_PER_ID_CLAIM_MARKERS = tuple(_sels(["claimedById(uint256)", "claimable(uint256)", "isClaimed(uint256)",
                                     "claimed(uint256)"]).keys())


def _revert_text(revert: Any) -> str:
    """The revert's message plus its decoded Error(string), lowercased."""
    if not isinstance(revert, dict):
        return ""
    text = str(revert.get("message") or "")
    data = _revert_data(revert)
    if data[2:10] == "08c379a0":
        try:
            text += " " + bytes.fromhex(data[138:]).rstrip(b"\0").decode("utf-8", "ignore")
        except ValueError:
            pass
    return text.lower()


def _holder_claim_kind(why: Optional[str], revert: Any, calldata: Optional[str], code: str) -> Optional[str]:
    """"holder_only" for an owner / already-claimed refusal of a claim by token id (see above), else `why`."""
    if why not in ("owner_only", None):
        return why
    sel = str(calldata or "").lower()[2:10]
    code = (code or "").lower()
    by_id = sel == _CLAIM_CARD_SEL or (sel == _CLAIM_BY_ID_SEL and any(("63" + m) in code for m in _PER_ID_CLAIM_MARKERS))
    if not by_id:
        return why
    if why == "owner_only" or "already claimed" in _revert_text(revert):
        return "holder_only"
    return why

_QTY = "1".zfill(64)

_to = lambda w: w[2:].lower().zfill(64)

# Public mint entry points tried when the stored call gives no verdict, most common first. A recipient argument is
# DEFAULT_WALLET (replace_calldata_recipient swaps it for the user's wallet at mint time). Only functions present in
# the bytecode are tried, at most _ALT_MINT_MAX per drop.
ALT_MINT_CALLS = tuple((sig, keccak_selector(sig)[2:], build) for sig, build in (
    ("mint()", lambda w: ""),
    ("mint(uint256)", lambda w: _QTY),
    ("freeMint()", lambda w: ""),
    ("mintFree()", lambda w: ""),
    ("publicMint()", lambda w: ""),
    ("publicMint(uint256)", lambda w: _QTY),
    ("freeMint(uint256)", lambda w: _QTY),
    ("mintPass()", lambda w: ""),
    ("claim()", lambda w: ""),
    ("claim(uint256)", lambda w: _QTY),
    ("mint(uint256,address)", lambda w: _QTY + _to(w)),
    ("mint(address)", lambda w: _to(w)),
    ("mintTo(address)", lambda w: _to(w)),
    ("mint(uint8)", lambda w: _QTY),
    ("mintNFT()", lambda w: ""),
    ("buy(uint256)", lambda w: _QTY),
    ("buy(uint256,address)", lambda w: _QTY + _to(w)),
    ("purchase(uint256)", lambda w: _QTY),
    ("mintCard(uint256)", lambda w: _QTY),
    ("claimCard(uint256)", lambda w: _QTY),
    ("join()", lambda w: ""),
    ("participate()", lambda w: ""),
) if keccak_selector(sig))

_ALT_MINT_MAX = 4
