"""How many tokens one mint pays out: read from a mint receipt's Transfer events, or from the contract's own getters. Moved out of
monitor.py unchanged; monitor.py re-exports these names."""

import logging
from typing import Any, Dict, List, Optional, Tuple

from chain.rpc import BscRpcClient, keccak_selector

_log = logging.getLogger(__name__)



# --- Tokens per mint ------------------------------------------------------------------------------------------------
# A BEP-20 mint pays once and receives many tokens, so its mint price PER TOKEN is mint cost / tokens per mint. That
# number is read from the chain once per drop (worker.run_tokens_per_mint_loop): first from a real mint (the receipt of
# the drop's stored transaction: the tokens sent to the payer), else from a getter the contract exposes.
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


TOKENS_PER_MINT_GETTERS = ("amountPerMint()", "AMOUNT_PER_MINT()", "MINT_AMOUNT()", "mintAmount()", "perMint()",
                           "tokensPerMint()", "TOKENS_PER_MINT()", "MINT_TOKENS()")


def tokens_from_mint_receipt(tx: Optional[dict], receipt: Optional[dict], token_addrs: List[str], mint_value_wei: int,
                             decimals: int) -> Optional[float]:
    """Tokens ONE mint gives, from a real mint transaction: the token Transfer(s) to the payer, scaled to one mint's
    price when the payer bought several (value = k x mint price). None when the transaction is not a paid mint of this
    drop (a deployment, a free claim of another contract, a failed transaction)."""
    if not tx or not receipt or str(receipt.get("status") or "").lower() not in ("0x1", "1"):
        return None
    targets = {a.lower() for a in token_addrs if a}
    if str(tx.get("to") or "").lower() not in targets:
        return None
    payer = str(tx.get("from") or "").lower()[2:]
    value = int(str(tx.get("value") or "0x0"), 16)
    if mint_value_wei > 0 and value < mint_value_wei:
        return None
    if mint_value_wei == 0 and value != 0:
        return None
    raw = 0
    for lg in receipt.get("logs") or []:
        t = lg.get("topics") or []
        if (len(t) == 3 and str(t[0]).lower() == TRANSFER_TOPIC and str(lg.get("address") or "").lower() in targets
                and str(t[2]).lower()[-40:] == payer):
            try:
                raw += int(str(lg.get("data") or "0x0"), 16)
            except ValueError:
                return None
    if raw <= 0:
        return None
    tokens = raw / (10 ** decimals)
    if mint_value_wei > 0 and value > mint_value_wei:
        tokens = tokens * mint_value_wei / value          # bought k mints in one transaction
    return tokens if 0 < tokens < 1e18 else None


def read_tokens_per_mint(rpc: "BscRpcClient", contract: str, tx_hash: Optional[str], mint_value_wei: int, decimals: int,
                         token_addrs: Optional[List[str]] = None) -> Tuple[Optional[float], Optional[str]]:
    """(tokens one mint gives, where it came from: "receipt" / "getter"), or (None, None)."""
    addrs = [contract] + [a for a in (token_addrs or []) if a and a.lower() != contract.lower()]
    if tx_hash:
        try:
            tx = rpc.call("eth_getTransactionByHash", [tx_hash])
            receipt = rpc.call("eth_getTransactionReceipt", [tx_hash]) if tx else None
            tokens = tokens_from_mint_receipt(tx, receipt, addrs, mint_value_wei, decimals)
            if tokens:
                return tokens, "receipt"
        except Exception as exc:
            _log.debug("tokens-per-mint receipt read failed for %s: %s", contract, exc)
    for sig in TOKENS_PER_MINT_GETTERS:
        res = rpc.call_contract(contract, keccak_selector(sig))
        if not res or len(res) < 66:
            continue
        try:
            raw = int(res[2:66], 16)
        except ValueError:
            continue
        tokens = raw / (10 ** decimals)
        if 1e-6 <= tokens < 1e15:
            return tokens, "getter"
    return None, None
