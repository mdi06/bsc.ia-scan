"""Element (the BSC NFT marketplace): reading its trade logs to count real secondary sales, and the "NFT surge" signal built from
them. Moved out of monitor.py unchanged; monitor.py re-exports these names, so `monitor.scan_element_nft_volume` and friends keep
working for worker.py and the scripts."""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from chain.addresses import DEFAULT_WALLET, WBNB_ADDRESS
from chain.rpc import BscRpcClient

_log = logging.getLogger(__name__)

# Element Market BSC Router & Event Signatures (Option D: nftVolWatch)
ELEMENT_EX = "0xb3e3dfcb2d9f3dde16d78b9e6eb3538eb32b5ae1"

T_721_FILLED = "0x9c248aa1a265aa616f707b979d57f4529bb63a4fc34dc7fc61fdddc18410f74e"

# ERC721BuyOrderFilled: an accepted offer. Same data layout as the sell fill, but the maker is the buyer.
T_721_BUY_FILLED = "0xd90a5c60975c6ff8eafcf02088e7b50ae5d9e156a79206ba553df1c4fb4594c2"

# Element stats cover the last 24 hours. The window is set in hours because BSC's block time has changed
# (3s -> 0.45s): the old fixed 15,000-block window had shrunk to under two hours and was nearly always empty.
ELEMENT_LOOKBACK_HOURS = 24.0

NFT_ACTIVE_MIN_SALES = 1             # sales in that window that mark a collection active (is_nft_surge)

BSC_BLOCK_TIME_FALLBACK_SEC = 0.45   # used only when the block time cannot be measured

ELEMENT_LOG_CHUNK_BLOCKS = 10000

ELEMENT_REORG_BLOCKS = 20            # the newest blocks are re-read every scan, never served from the cache

ELEMENT_LOG_ATTEMPTS = 3             # tries per log chunk: public endpoints time out now and then

ELEMENT_LOG_RETRY_SEC = 1.0

T_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


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
    - Flags the collection (is_nft_surge) when it had at least one BNB sale in the window: the BSC NFT market
      is thin enough that any trading at all marks an active collection. (The rule used to need 5 sales or
      0.5 BNB, 3 sales for a held collection.) user_held and prev_stats no longer change the result.
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

    is_hot = sales_cnt >= NFT_ACTIVE_MIN_SALES

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

    def _remember(read_to: int) -> None:
        """Caches what was read up to block `read_to`, minus the newest blocks (still open to a reorg)."""
        settled = min(read_to, head - ELEMENT_REORG_BLOCKS)
        if cache is not None and settled >= from_b:
            cache.update(from_block=from_b, to_block=settled, fills=[f for f in fills if f["block"] <= settled])

    last_exc: Optional[Exception] = None
    for b in range(start, head + 1, ELEMENT_LOG_CHUNK_BLOCKS):
        to_b = min(b + ELEMENT_LOG_CHUNK_BLOCKS - 1, head)
        params = {
            "address": ELEMENT_EX,
            "topics": [[T_721_FILLED, T_721_BUY_FILLED]],
            "fromBlock": hex(b),
            "toBlock": hex(to_b)
        }
        logs = None
        for attempt in range(ELEMENT_LOG_ATTEMPTS):
            try:
                logs = fetch(params)
                break
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < ELEMENT_LOG_ATTEMPTS:
                    time.sleep(ELEMENT_LOG_RETRY_SEC)
        if logs is None:
            _log.warning("Element fill logs failed for blocks %s-%s (%s); keeping the previous secondary-market stats",
                         b, to_b, last_exc)
            _remember(b - 1)      # the chunks that did answer are kept, so the next scan resumes from here
            return None
        for l in logs:
            parsed = parse_element_trade_log(l)
            if parsed and parsed["is_bnb"]:  # fills paid in another token have no BNB price: not a BNB sale
                fills.append(parsed)

    _remember(head)
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
