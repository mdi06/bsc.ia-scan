"""Deciding what kind of asset a contract is (ERC-721 / ERC-1155 collection, BEP-20 token, fair-mint dispenser, ...) from its code and
function selectors. Moved out of monitor.py unchanged; monitor.py re-exports it."""

from typing import Any, Dict, List, Optional

from chain.rpc import BscRpcClient, clean_chain_text, decode_string, keccak_selector



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
            name = clean_chain_text(decode_string(name_res)) if name_res else "Token"
            sym = clean_chain_text(decode_string(sym_res)) if sym_res else ""
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
