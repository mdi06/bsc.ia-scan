"""Turning a record's numbers into verdicts: the interest score and rating, and whether a drop is "snipe ready" (past 60% filled,
or a live crowd surge, with a refund guarantee, not already on a DEX, no serial dev or drain). Moved out of monitor.py unchanged;
monitor.py re-exports these names."""

import time
from typing import Any, Dict, List, Optional, Tuple

import filters



def evaluate_sniper_ready(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluates whether an asset qualifies for high-conviction assisted sniper entry:
    1. Standard-specific Progress (>=60% filled, or a "crowd surge": no cap known but 100+ mints and still minting)
    2. Bytecode Refund Guarantee (mandatory if paid drop)
    3. DEX Pre-Check (for tokens: if PancakeSwap pool is already open, trade on DEX instead)
    4. Anti-Rug & Anti-Spam (not serial dev >=3 drops, wallet not fresh <8h, no capital drain)
    5. Simulation Safety (mint not paused, no honeypot)
    """
    std = str(item.get("standard") or "")
    cls = item.get("classification") or {}
    is_nft = "NFT" in std or "721" in std or "1155" in std or bool(cls.get("is_nft"))
    is_token = "BEP-20" in std or "Fair-Mint" in std or cls.get("category") in ("TOKEN", "TOKEN_FAIR_MINT")

    ms = filters.corrected_max_supply(item) or None      # a fair-mint dispenser's slot-count cap, in token units
    tm = item.get("total_minted") or 0
    lp = float(item.get("lp_usd", 0) or 0)
    has_ref = bool(item.get("has_refund") or (item.get("security") or {}).get("has_refund"))
    risk = item.get("risk_level", "SAFE")
    sec = item.get("security") or {}
    is_serial = bool(item.get("is_serial_dev") or sec.get("is_serial_dev"))
    is_drained = bool(item.get("is_fund_drain") or sec.get("is_fund_drain"))
    is_fresh = bool(item.get("is_fresh_dev") or sec.get("is_fresh_dev"))
    is_paused = item.get("is_paused") is True

    val_wei = int(item.get("mint_value_wei", "0") or 0)
    price_bnb = float(item.get("mint_price_bnb", 0.0) or 0.0)
    is_paid = price_bnb > 0.001 or val_wei > 0

    checklist = []

    # 1. Progress calculation
    if is_token and not is_nft:
        if ms and tm and ms == tm and not item.get("sold_sel") and not item.get("cap") and not filters.is_mint_count_cap(item):
            prog = 0.0
        else:
            prog = (tm / ms) if (ms and ms > 0) else 0.0
    else:
        prog = (tm / ms) if (ms and ms > 0) else 0.0

    surge = filters.crowd_surge_mints(item)     # mints so far when a cap-less drop has a live crowd, else 0

    # Progress check
    is_sold_out = (prog >= 0.999) or (is_nft and bool(ms) and bool(tm) and tm >= ms)
    prog_pass = (0.60 <= prog < 0.999) or (prog == 0.0 and surge > 0)
    if is_sold_out:
        checklist.append("▫ [red]✖[/red] Progress: 100% Sold Out / Completed")
    elif prog >= 0.60:
        checklist.append(f"▫ [green]✔[/green] Progress: {prog*100:.1f}% (≥60% conviction threshold passed)")
    elif prog == 0.0 and surge > 0:
        checklist.append(f"▫ [green]✔[/green] Progress: Crowd Surge ({surge} mints so far, still minting)")
    else:
        p_pct_str = f"{prog*100:.1f}%" if prog > 0 else "0%"
        checklist.append(f"▫ [yellow]⏳[/yellow] Progress: {p_pct_str} (<60% threshold, tracking)")

    # Deployer check
    if is_serial:
        s_cnt = sec.get("serial_count") or item.get("serial_count") or 3
        checklist.append(f"▫ [red]✖[/red] Deployer: 🚨 Serial Dev ({s_cnt} contracts recorded from this deployer)")
    elif is_fresh:
        checklist.append("▫ [yellow]⚠️[/yellow] Deployer: Fresh wallet (<8h, low nonce)")
    else:
        checklist.append("▫ [green]✔[/green] Deployer: Clean reputation (No spam deployment)")

    # Capital drain check
    if risk == "RUG" or is_drained:
        checklist.append("▫ [red]✖[/red] Capital Drain: 🚨 Empty contract with no DEX LP (Funds pocketed)")
    else:
        checklist.append("▫ [green]✔[/green] Capital Safety: No fund drain detected")

    # Bytecode refund guarantee check
    if is_paid:
        if has_ref:
            ref_m = sec.get("refund_methods") or item.get("refund_methods") or ["refund()"]
            checklist.append(f"▫ [green]✔[/green] Refund Guarantee: Bytecode refund verified ({ref_m[0]})")
        else:
            checklist.append("▫ [red]✖[/red] Refund Guarantee: ⚠️ No refund method (Capital trapped if unfilled)")
    else:
        checklist.append("▫ [dim]▫ Refund Guarantee: N/A (Free Mint / Gas Only)[/dim]")

    # Secondary DEX pre-check (Tokens only)
    if is_token:
        if lp >= 500:
            checklist.append(f"▫ [yellow]✖[/yellow] Secondary Market: DEX LP already active (${lp:,.0f}) -> Trade on DEX instead of minting")
        else:
            checklist.append("▫ [green]✔[/green] Secondary Market: Primary bonding phase (No DEX LP yet)")

    # Final verdict
    if is_paused:
        res = {"is_ready": False, "status": "PAUSED", "badge": "⏸️ Paused", "prog": prog, "checklist": checklist}
    elif risk == "RUG" or is_drained:
        res = {"is_ready": False, "status": "RUG_RISK", "badge": "🚨 Rug Risk", "prog": prog, "checklist": checklist}
    elif is_serial:
        res = {"is_ready": False, "status": "SERIAL_DEV", "badge": "🚨 Serial Dev", "prog": prog, "checklist": checklist}
    elif is_token and lp >= 500:
        res = {"is_ready": False, "status": "GRADUATED_TO_DEX", "badge": "📈 On DEX", "prog": prog, "checklist": checklist}
    elif is_paid and not has_ref:
        res = {"is_ready": False, "status": "NO_REFUND", "badge": "⚠️ No Refund", "prog": prog, "checklist": checklist}
    elif is_sold_out:
        res = {"is_ready": False, "status": "SOLD_OUT", "badge": "🛑 Sold Out", "prog": prog, "checklist": checklist}
    elif prog_pass:
        res = {"is_ready": True, "status": "SNIPER_READY", "badge": "🎯 Snipe Ready", "prog": prog, "checklist": checklist}
    else:
        res = {
            "is_ready": False,
            "status": "TRACKING",
            "badge": f"⏳ Track ({prog*100:.0f}%)" if prog > 0 else "⏳ Tracking",
            "prog": prog,
            "checklist": checklist
        }
    res["sniper_ready"] = res["is_ready"]
    res["sniper_status"] = res["status"]
    res["sniper_badge"] = res["badge"]
    res["sniper_checklist"] = res["checklist"]
    return res


RATING_BY_TIER = {"SURGING": "🔥 SURGING", "HIGH": "🚀 HIGH", "MODERATE": "⚡ MODERATE", "EARLY": "🌱 EARLY"}


def compute_interest(item: Dict[str, Any]) -> Tuple[int, str]:
    """The single interest score (0-100) and rating label. analyze_market_interest and the worker's
    _recompute_interest both call this, so a stored score never depends on which of them ran last.
    Bands: liquidity (0-40), 24h volume (0-30), 24h buys (0-30), +5 per smart wallet. A honeypot scores 0 and a
    HIGH risk rating costs 25 points (the guard analyze_market_interest always applied). The JavaScript badge
    uses the same tiers (filters.interest_tier / interestTier in app.js)."""
    lp = float(item.get("lp_usd") or 0.0)
    vol = float(item.get("volume_24h") or 0.0)
    b24 = int(item.get("buys_24h") or 0)

    score = 0
    if lp > 50000: score += 40
    elif lp > 10000: score += 30
    elif lp > 2000: score += 20
    elif lp > 500: score += 10

    if vol > 20000: score += 30
    elif vol > 5000: score += 20
    elif vol > 500: score += 10

    if b24 > 500: score += 30
    elif b24 > 100: score += 20
    elif b24 > 20: score += 10

    if item.get("smart_wallets"):
        score += len(item["smart_wallets"]) * 5

    risk = item.get("risk_level")
    if risk == "RUG":
        score = 0
    elif risk == "HIGH":
        score = max(0, score - 25)

    score = min(100, score)
    return score, RATING_BY_TIER[filters.interest_tier(score)]
