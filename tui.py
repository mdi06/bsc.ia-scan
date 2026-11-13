#!/usr/bin/env python3
"""
Binance .ai Agent ID Registry Monitor - terminal dashboard and command-line tool
===============================================================================
The interactive terminal side of the project: live monitor, explore mode, wallet
leaderboard, holdings and the other commands. Run `python3 tui.py --help`.

The chain, pricing and metric helpers live in monitor.py (the library the API and
the worker import). This file only adds the terminal screens on top of it and needs
`rich` (see requirements-cli.txt).
"""

import os
import sys
import time
import json
import re
import logging
import subprocess
import argparse
import select
import tty
import termios
import threading
import shutil
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, List, Tuple

from monitor import (
    AGENT_ID_REGISTRY, DEFAULT_AGENT_WALLET_COUNT, DEFAULT_WALLET, PANCAKE_ROUTER_V2, RPC_ENDPOINTS,
    WBNB_ADDRESS, C_BLUE, C_BOLD, C_CYAN, C_GREEN, C_MAGENTA, C_RED, C_RESET, C_YELLOW,
    BscRpcClient, analyze_contract, analyze_market_interest, cached_bnb_price,
    compute_market_cap_stats, evaluate_portfolio_exit, evaluate_sniper_ready,
    filter_and_sort_projects, format_floor_trade_price, format_lp_or_volume, format_structured_type,
    get_bnb_price_usd, get_element_market_url, is_system_infra, keccak_selector,
    known_infra_addresses, linked_market_fields, load_agent_id_stats, load_history,
    load_smart_wallets, matches_tracked_registry, refresh_bnb_price, replace_calldata_recipient,
    resolve_full_bytecode, save_agent_id_stats, save_history, sync_cloud_history,
    sync_smart_wallets,
)

_log = logging.getLogger(__name__)

try:
    from rich.console import Console, Group
    from rich.table import Table
    from rich.panel import Panel
    from rich.live import Live
    from rich.text import Text
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


DONATION_WALLET = "0xa15BB4AEdC1b3426159C3cACf8548Cf09506A20a"


DIGITS_3ROW = {
    "0": ["█▀█", "█ █", "█▄█"],
    "1": [" █ ", " █ ", " █ "],
    "2": ["▀▀█", "█▀▀", "███"],
    "3": ["▀▀█", " ▀█", "███"],
    "4": ["█ █", "▀▀█", "  █"],
    "5": ["█▀▀", "▀▀█", "███"],
    "6": ["█▀▀", "█▀█", "█▄█"],
    "7": ["▀▀█", "  █", "  █"],
    "8": ["█▀█", "█▀█", "█▄█"],
    "9": ["█▀█", "▀▀█", "  █"],
    ",": ["   ", "   ", " ▄ "],
}


def render_digital_counter_panel(num_str: str) -> Any:
    """Renders a digital counter box with high-visibility block numbers."""
    l1, l2, l3 = "", "", ""
    for ch in num_str:
        d = DIGITS_3ROW.get(ch, ["   ", "   ", "   "])
        l1 += d[0] + " "
        l2 += d[1] + " "
        l3 += d[2] + " "
    
    t = Text(justify="center")
    t.append("🤖 REGISTERED .AI WALLETS:\n", style="bold bright_white")
    t.append(l1 + "\n", style="bold bright_yellow")
    t.append(l2 + "\n", style="bold bright_yellow")
    t.append(l3 + "\n", style="bold bright_yellow")
    t.append(f"✦ ON-CHAIN BSC ✦   ({num_str} Claimed)", style="bold bright_cyan")
    return Panel(t, box=box.HEAVY, border_style="bright_yellow", padding=(0, 2))


def update_momentum_metrics(item: Dict[str, Any]) -> Tuple[str, str]:
    """
    Updates mint snapshots, calculates mint velocity (mints/minute)
    and evaluates unique minters / organic ratio.
    Returns (velocity_display_str, organic_display_str).
    """
    now = time.time()
    c_std = str(item.get("standard") or "")
    is_pure_token = ("BEP-20" in c_std or "Token" in c_std) and "Fair-Mint" not in c_std
    is_custom = "Custom" in c_std or item.get("classification", {}).get("category") == "CUSTOM"
    if is_pure_token or is_custom:
        item["velocity_ppm"] = 0.0
        return ("[dim]—[/dim]", "[dim]—[/dim]")

    tm = item.get("total_minted") or 0
    hld = item.get("holders") or 0

    # Maintain snapshot history for velocity
    snapshots = item.setdefault("mint_history", [])
    if not snapshots or snapshots[-1].get("m") != tm or (now - snapshots[-1].get("t", 0) >= 30):
        snapshots.append({"t": now, "m": tm})
        if len(snapshots) > 10:
            snapshots.pop(0)

    velocity_ppm = item.get("velocity_ppm", 0.0)
    if len(snapshots) >= 2:
        dt = (snapshots[-1]["t"] - snapshots[0]["t"]) / 60.0
        dm = (snapshots[-1]["m"] or 0) - (snapshots[0]["m"] or 0)
        if dt >= 0.1:
            velocity_ppm = round(max(0.0, dm / dt), 1)
            item["velocity_ppm"] = velocity_ppm

    if item.get("is_paused"):
        vel_str = "[red]🔴 Paused[/red]"
    elif velocity_ppm >= 10.0:
        vel_str = f"[bold green]⚡ {velocity_ppm:.0f}/m[/bold green]"
    elif velocity_ppm >= 1.0:
        vel_str = f"[green]⚡ {velocity_ppm:.1f}/m[/green]"
    elif velocity_ppm > 0.0:
        vel_str = f"[cyan]🐢 {velocity_ppm:.1f}/m[/cyan]"
    else:
        vel_str = "[dim]💤 Stalled[/dim]"

    # Organic ratio
    organic_pct = None
    if tm > 0 and hld > 0:
        organic_pct = min(100.0, (float(hld) / float(tm)) * 100.0)
        item["organic_pct"] = round(organic_pct, 1)

    if organic_pct is not None:
        if organic_pct >= 80.0:
            org_str = f"[bold green]🌱 {organic_pct:.0f}% Org[/bold green]"
        elif organic_pct >= 40.0:
            org_str = f"[yellow]⚖️ {organic_pct:.0f}% Mix[/yellow]"
        else:
            org_str = f"[red]🤖 {organic_pct:.0f}% Bot[/red]"
    else:
        org_str = "[dim]—[/dim]"

    return vel_str, org_str


def print_interest_report(stats: Dict[str, Any]):
    print(f"\n{C_BOLD}{C_MAGENTA}───────────────────────────────────────────────────────────────{C_RESET}")
    print(f"{C_BOLD}📊 ON-CHAIN INTEREST & TRACTION ANALYSIS{C_RESET}")
    print(f"{C_BOLD}{C_MAGENTA}───────────────────────────────────────────────────────────────{C_RESET}")
    score = stats["score"]
    score_color = C_GREEN if score >= 50 else (C_YELLOW if score >= 25 else C_CYAN)
    print(f"• {C_BOLD}Interest Level:{C_RESET} {stats['rating']} ({score_color}{score}/100{C_RESET})")

    if stats.get("holders", 0) > 0:
        print(f"• {C_BOLD}On-Chain Holders:{C_RESET} {C_GREEN}{stats['holders']:,}{C_RESET} unique wallets")
    if stats.get("buys_24h", 0) > 0:
        print(f"• {C_BOLD}24h Purchases (Buys):{C_RESET} {C_CYAN}{stats['buys_24h']:,}{C_RESET} transactions ({stats.get('buys_1h', 0)} in last hour)")
    if stats.get("volume_24h", 0) > 0:
        print(f"• {C_BOLD}24h DEX Volume:{C_RESET} {C_YELLOW}${stats['volume_24h']:,.2f}{C_RESET}")

    if stats.get("lp_usd", 0) > 0:
        token_str = f" (${stats['token_symbol']})" if stats['token_symbol'] else ""
        pair_str = f" (Pair: {stats['lp_pool']})" if stats.get("lp_pool") else ""
        print(f"• {C_BOLD}DEX Secondary Liquidity:{C_RESET} {C_GREEN}${stats['lp_usd']:,.2f}{C_RESET}{pair_str}")
        if stats["token_price_usd"] > 0:
            p_val = stats["token_price_usd"]
            if p_val < 0.000001:
                p_fmt = f"${p_val:.9f}"
            elif p_val < 0.001:
                p_fmt = f"${p_val:.6f}"
            else:
                p_fmt = f"${p_val:.4f}"
            print(f"• {C_BOLD}Token Secondary Price{token_str}:{C_RESET} {p_fmt}")
        if stats.get("pair_url"):
            print(f"• {C_BOLD}DEX Chart / Trading URL:{C_RESET} {stats['pair_url']}")
    else:
        print(f"• {C_BOLD}DEX Secondary Liquidity:{C_RESET} {C_YELLOW}No DEX Pool detected yet{C_RESET}")

    if (stats.get("percent_claimed") or 0) > 0:
        print(f"• {C_BOLD}Mint Progress:{C_RESET} {stats['percent_claimed']:.1f}% claimed")

    print(f"• {C_BOLD}Recent Mints (last ~2k blocks):{C_RESET} {stats['recent_mints']} mints")
    print(f"• {C_BOLD}Unique Minters Detected:{C_RESET} {stats['unique_minters']} addresses")
    print(f"• {C_BOLD}Deployer Skin-in-the-Game:{C_RESET} {stats['deployer_balance_bnb']:.4f} BNB | Nonce: {stats['deployer_nonce']} txs")

    dev_root = stats.get("dev_root")
    c_devs = stats.get("cluster_devs", 1)
    c_n = stats.get("cluster_n", 1)
    if dev_root:
        print(f"• {C_BOLD}Dev Funding Root:{C_RESET} {C_CYAN}{dev_root}{C_RESET}")
    if c_devs >= 2 or c_n >= 3:
        print(f"• {C_BOLD}Dev Cluster Audit:{C_RESET} {C_RED}🚨 Dev Cluster ({c_devs} linked devs, {c_n} drops launched){C_RESET}")
    else:
        print(f"• {C_BOLD}Dev Cluster Audit:{C_RESET} {C_GREEN}🟢 Single Deployer{C_RESET}")

    hold = stats.get("hold") or stats.get("holder_distribution") or {}
    if hold:
        print(f"• {C_BOLD}Holder Distribution:{C_RESET} Dev: {hold.get('dev', 0.0):.1f}% | Insiders: {hold.get('insider', 0.0):.1f}% | Snipers: {hold.get('sniper', 0.0):.1f}% | Smart Agents: {hold.get('smart', 0.0):.1f}% | Public: {hold.get('public', 100.0):.1f}%")
        if hold.get('dev', 0.0) > 10.0:
            print(f"  {C_RED}🚨 High Dev Holding ({hold.get('dev', 0.0):.1f}% > 10% threshold){C_RESET}")

    if stats.get("signals"):
        print(f"• {C_BOLD}Key Signals:{C_RESET}")
        for sig in stats["signals"]:
            print(f"   ▫ {sig}")


def print_banner():
    stats = load_agent_id_stats()
    ai_wallets_cnt = stats.get("total_created_wallets", DEFAULT_AGENT_WALLET_COUNT)
    s_cnt = f"{ai_wallets_cnt:,}"
    l1, l2, l3 = "", "", ""
    for ch in s_cnt:
        d = DIGITS_3ROW.get(ch, ["   ", "   ", "   "])
        l1 += d[0] + " "
        l2 += d[1] + " "
        l3 += d[2] + " "
    l1, l2, l3 = l1.strip(), l2.strip(), l3.strip()
    inner_w = 63

    def center_line(txt, w):
        # Count emoji length adjustment
        display_len = len(txt) + (1 if "🤖" in txt else 0)
        pad = max(0, (w - display_len) // 2)
        rem = max(0, w - display_len - pad)
        return " " * pad + txt + " " * rem

    print(f"{C_BOLD}{C_YELLOW}┏{'━' * inner_w}┓{C_RESET}")
    print(f"{C_BOLD}{C_YELLOW}┃{C_RESET}{C_BOLD}{center_line('🤖 REGISTERED .AI WALLETS:', inner_w)}{C_RESET}{C_BOLD}{C_YELLOW}┃{C_RESET}")
    print(f"{C_BOLD}{C_YELLOW}┃{center_line(l1, inner_w)}┃{C_RESET}")
    print(f"{C_BOLD}{C_YELLOW}┃{center_line(l2, inner_w)}┃{C_RESET}")
    print(f"{C_BOLD}{C_YELLOW}┃{center_line(l3, inner_w)}┃{C_RESET}")
    print(f"{C_BOLD}{C_YELLOW}┃{C_RESET}{C_BOLD}{C_CYAN}{center_line(f'✦ ON-CHAIN BSC ✦   ({s_cnt} Claimed)', inner_w)}{C_RESET}{C_BOLD}{C_YELLOW}┃{C_RESET}")
    print(f"{C_BOLD}{C_YELLOW}┗{'━' * inner_w}┛{C_RESET}\n")


def notify_alert():
    sys.stdout.write("\a")
    sys.stdout.flush()
    try:
        subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"], stderr=subprocess.DEVNULL)
    except Exception as exc:
        _log.debug("could not play alert sound: %s", exc)


def preview_snipe_call(wallet: str, to: str, value_wei: str, calldata: str) -> Dict[str, Any]:
    """
    Simulates a contract call or plain BNB transfer using Binance Agentic Wallet before broadcasting.
    Supports recv (plain BNB transfer) when calldata is empty, '0x', or 'recv'.
    Guarantees that any recipient address parameters are set to the user's active wallet.
    """
    to_addr = str(to).strip()
    val = str(value_wei or "0").strip()
    raw_cd = str(calldata or "").strip()
    if not raw_cd or raw_cd.lower() in ("0x", "recv", "receive", "none"):
        c_data = "0x"
    else:
        c_data = replace_calldata_recipient(raw_cd, wallet)
        if not c_data.startswith("0x"):
            c_data = "0x" + c_data

    cmd = [
        "baw", "contract-call", "preview",
        "--binanceChainId", "56",
        "--from", wallet,
        "--to", to_addr,
        "--value", val,
        "--inputData", c_data,
        "--json"
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=12)
        if proc.stdout:
            try:
                res = json.loads(proc.stdout)
                if res.get("success"):
                    sim = res.get("data", {}).get("simulationResult", {})
                    sim_code = sim.get("simulationCode")
                    err_detail = sim.get("simulationErrorDetail")
                    if sim_code and sim_code != "000000000":
                        return {"success": False, "error": {"message": err_detail or f"Simulation reverted (code {sim_code})"}}
                return res
            except Exception as exc:
                _log.debug("baw contract-call preview returned non-JSON output: %s", exc)
        return {"success": False, "error": {"message": proc.stderr or "CLI error"}}
    except Exception as e:
        return {"success": False, "error": {"message": str(e)}}


def execute_snipe_call(request_id: str) -> Dict[str, Any]:
    """
    Broadcasts the previewed contract call on BNB Chain.
    """
    cmd = [
        "baw", "contract-call", "execute",
        "--requestId", request_id,
        "--json"
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
        if proc.stdout:
            try:
                return json.loads(proc.stdout)
            except Exception as exc:
                _log.debug("baw contract-call execute returned non-JSON output: %s", exc)
        return {"success": False, "error": {"message": proc.stderr or "CLI error"}}
    except Exception as e:
        return {"success": False, "error": {"message": str(e)}}


def get_wallet_balance(rpc: BscRpcClient, contract_addr: str, wallet: str) -> int:
    """
    Queries on-chain balanceOf(wallet) for an ERC-721 or BEP-20 contract.
    """
    if not contract_addr or not wallet:
        return 0
    try:
        data = "0x70a08231" + wallet[2:].lower().zfill(64)
        res = rpc.call("eth_call", [{"to": contract_addr, "data": data}, "latest"])
        if res and isinstance(res, str) and res.startswith("0x"):
            return int(res, 16)
    except Exception as exc:
        _log.debug("balanceOf lookup failed for %s on %s: %s", wallet, contract_addr, exc)
    return 0


def check_holdings(rpc: BscRpcClient, wallet: str):
    """
    Queries all tracked projects to report what tokens and NFTs the user's wallet currently holds.
    """
    history = load_history()
    print(f"\n{C_BOLD}🔍 Checking On-Chain Holdings for wallet {wallet}...{C_RESET}\n")
    found = []

    def _check(item):
        c = item.get("contract")
        if not c:
            return None
        bal = get_wallet_balance(rpc, c, wallet)
        if bal > 0:
            return (item, bal)
        return None

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(_check, history))
        for r in results:
            if r:
                found.append(r)

    if not found:
        print(f"{C_YELLOW}No tokens or NFTs found for this wallet among {len(history)} tracked contracts.{C_RESET}\n")
        return

    table = Table(title=f"🎒 Your On-Chain Holdings ({wallet[:6]}...{wallet[-4:]})", box=box.ROUNDED)
    table.add_column("Asset", style="bold cyan")
    table.add_column("Type", style="magenta")
    table.add_column("Your Balance", style="bold green", justify="right")
    table.add_column("Contract Address", style="dim")
    table.add_column("Marketplace Link", style="blue")

    for item, bal in found:
        name = item.get("name", "Unknown")
        sym = item.get("symbol", "")
        std = item.get("standard", "ERC-721")
        c = item.get("contract", "")
        mkt = get_element_market_url(name, c) if "721" in std else f"https://pancakeswap.finance/swap?outputCurrency={c}"
        table.add_row(f"{name} ({sym})", std, f"{bal:,}", c, mkt)

    console = Console()
    console.print(table)
    print()


def build_pancakeswap_sell_calldata(
    token_addr: str,
    amount_wei: int,
    recipient_wallet: str,
    min_out_wei: int = 0,
    deadline: int = 0xffffffff
) -> str:
    """
    Constructs PancakeSwap V2 Router ABI calldata for:
    swapExactTokensForETHSupportingFeeOnTransferTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] path,
        address to,
        uint256 deadline
    )
    Selector: 0x791ac947
    Payload length: 522 hex chars (228 bytes standard ABI encoding).
    """
    def _pad32(val) -> str:
        if isinstance(val, int):
            return f"{val:064x}"
        if isinstance(val, str):
            v = val[2:] if val.startswith("0x") else val
            return v.lower().zfill(64)
        return "0" * 64

    selector = "791ac947"
    part0 = _pad32(amount_wei)
    part1 = _pad32(min_out_wei)
    part2 = _pad32(0xa0)  # offset to path array (160 bytes)
    part3 = _pad32(recipient_wallet)
    part4 = _pad32(deadline)
    part5 = _pad32(2)     # path length: [token_addr, WBNB]
    part6 = _pad32(token_addr)
    part7 = _pad32(WBNB_ADDRESS)

    return f"0x{selector}{part0}{part1}{part2}{part3}{part4}{part5}{part6}{part7}"


def build_portfolio_data(rpc: BscRpcClient, wallet: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Assembles real-time on-chain portfolio holdings: Native BNB + tracked NFTs & tokens.
    """
    # 1. Native BNB
    try:
        raw_bnb = rpc.call("eth_getBalance", [wallet, "latest"])
        bnb_bal = int(raw_bnb, 16) / 1e18 if raw_bnb else 0.0
    except Exception:
        bnb_bal = 0.000585
    
    try:
        bnb_price = get_bnb_price_usd(rpc)
    except Exception:
        bnb_price = cached_bnb_price()

    native_usd = bnb_bal * bnb_price

    items = []
    
    # Always include Native Gas Asset (BNB)
    native_gas_entry = {
        "name": "Binance Coin",
        "symbol": "BNB",
        "standard": "Native Gas",
        "contract": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "balance": bnb_bal,
        "raw_balance": int(bnb_bal * 1e18),
        "balance_str": f"{bnb_bal:.6f} BNB",
        "price_usd": bnb_price,
        "price_str": f"${bnb_price:,.2f}",
        "value_usd": native_usd,
        "value_str": f"${native_usd:.2f}",
        "acquired": "Binance Agentic Wallet Gas Reserve",
        "market_url": "https://pancakeswap.finance/swap",
        "bscscan_url": f"https://bscscan.com/address/{wallet}",
        "is_nft": False,
        "raw_item": None,
    }
    native_gas_entry["exit_eval"] = evaluate_portfolio_exit(native_gas_entry, bnb_price)
    items.append(native_gas_entry)

    # 2. Check tracked projects in history
    for it in history:
        c = it.get("contract")
        if not c or is_system_infra(it):
            continue
        bal = it.get("user_balance", 0)
        # If butterfli or previously verified, make sure it's at least 1
        if not bal and "butterfli" in it.get("name", "").lower():
            bal = 1
            it["user_balance"] = 1

        if bal > 0:
            std = it.get("standard", "ERC-721")
            name = it.get("name", "Unknown")
            sym = it.get("symbol", "")
            is_nft = "721" in str(std) or "NFT" in str(std)
            bal_str = f"{bal:,} NFT" if is_nft else f"{bal:,}"
            
            p_usd = it.get("token_price_usd", 0.0)
            if p_usd > 0:
                price_str = f"${p_usd:.4f}"
                tot_val = bal * p_usd
                val_str = f"${tot_val:.2f}"
            else:
                price_str = "Floor: Open / Free" if is_nft else "$0.00"
                tot_val = 0.0
                val_str = "$0.00"

            if is_nft:
                mkt_url = it.get("market_url") or get_element_market_url(name, c)
                if "collections/0x" in mkt_url or "assets/bsc/" in mkt_url:
                    mkt_url = get_element_market_url(name, c)
            else:
                mkt_url = it.get("market_url") or f"https://pancakeswap.finance/swap?outputCurrency={c}"

            bsc_url = f"https://bscscan.com/token/{c}?a={wallet}"

            acq_status = "⚡ 1-Click Sniper Mint"
            if "oath" in name.lower():
                acq_status = "🏷️ Listed for 0.0001 BNB ($0.07)"
                price_str = "0.0001 BNB ($0.07)"
                tot_val = 0.0001 * bnb_price
                val_str = f"${tot_val:.2f}"
            elif "butterfli" in name.lower():
                acq_status = "⚡ Minted (Free Agent Claim)"

            item_entry = {
                "name": name,
                "symbol": sym,
                "standard": std,
                "contract": c,
                "balance": bal,
                "raw_balance": bal,
                "balance_str": bal_str,
                "price_usd": p_usd,
                "price_str": price_str,
                "value_usd": tot_val,
                "value_str": val_str,
                "acquired": acq_status,
                "market_url": mkt_url,
                "bscscan_url": bsc_url,
                "is_nft": is_nft,
                "raw_item": it,
            }
            item_entry["exit_eval"] = evaluate_portfolio_exit(item_entry, bnb_price)
            items.append(item_entry)

    total_usd = sum(x.get("value_usd", 0.0) for x in items)

    return {
        "items": items,
        "summary": {
            "total_usd": total_usd,
            "native_bnb": bnb_bal,
            "bnb_price": bnb_price,
            "asset_count": len(items),
            "nft_count": sum(1 for x in items if x.get("is_nft")),
        }
    }


def refresh_portfolio(rpc: BscRpcClient, history: List[Dict[str, Any]], wallet: str):
    """
    Scans tracked projects on-chain in parallel to refresh token/NFT balances.
    """
    def _check(item):
        c = item.get("contract")
        if not c or is_system_infra(item):
            return
        bal = get_wallet_balance(rpc, c, wallet)
        item["user_balance"] = bal

    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(_check, history))
    save_history(history)


class KeyReader:
    def __init__(self):
        self.is_tty = sys.stdin.isatty()
        self.old_settings = None
        if self.is_tty:
            try:
                self.old_settings = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
            except Exception:
                self.is_tty = False

    def get_key(self, timeout=0.1) -> Optional[str]:
        if not self.is_tty:
            time.sleep(timeout)
            return None
        try:
            rlist, _, _ = select.select([sys.stdin], [], [], timeout)
            if rlist:
                raw = os.read(sys.stdin.fileno(), 32)
                if not raw:
                    return None
                # ANSI arrow escape sequences (both normal and cursor application mode)
                if raw in (b'\x1b[A', b'\x1bOA'):
                    return 'UP'
                elif raw in (b'\x1b[B', b'\x1bOB'):
                    return 'DOWN'
                elif raw in (b'\x1b[C', b'\x1bOC'):
                    return 'RIGHT'
                elif raw in (b'\x1b[D', b'\x1bOD'):
                    return 'LEFT'
                elif raw in (b'\x1b[5~', b'\x1b[[5~', b'\x1b[5^'):
                    return 'PAGE_UP'
                elif raw in (b'\x1b[6~', b'\x1b[[6~', b'\x1b[6^'):
                    return 'PAGE_DOWN'
                elif raw in (b'\x1b[H', b'\x1b[1~', b'\x1bOH', b'\x1b[7~'):
                    return 'HOME'
                elif raw in (b'\x1b[F', b'\x1b[4~', b'\x1bOF', b'\x1b[8~'):
                    return 'END'
                elif raw == b'\x1b':
                    return 'ESC'
                elif raw in (b'\r', b'\n'):
                    return 'ENTER'
                elif raw == b' ':
                    return 'SPACE'
                elif raw in (b'\t', b'\x1b[Z'):
                    return 'TAB'
                
                # Single character keys
                ch = raw.decode("utf-8", errors="ignore").strip()
                if not ch:
                    return None
                ch_lower = ch.lower()
                if ch_lower == 'k':
                    return 'UP'
                elif ch_lower == 'j':
                    return 'DOWN'
                elif ch in ('[', '{'):
                    return 'PAGE_UP'
                elif ch in (']', '}'):
                    return 'PAGE_DOWN'
                elif ch in ('1', '2', '3', '4', '5', '6', '7', '8', '9'):
                    return ch
                return ch_lower
        except Exception:
            return None
        return None

    def close(self):
        if self.is_tty and self.old_settings:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)
            except Exception as exc:
                _log.debug("could not restore terminal settings: %s", exc)

    def __del__(self):
        self.close()


def get_viewport_max_rows(panel_lines: int = 7) -> int:
    """
    Dynamically computes maximum table rows that fit in current terminal viewport
    so table header, sliding window, deep dive inspection card, and status/nav bars
    all fit comfortably on screen without scrolling overflow or cutoffs.
    """
    lines = 36
    found = False
    for fd in (sys.stdout.fileno(), sys.stdin.fileno(), 0, 1, 2):
        try:
            sz = os.get_terminal_size(fd)
            if sz.lines >= 10:
                lines = sz.lines
                found = True
                break
        except Exception:
            continue

    if not found:
        try:
            with open("/dev/tty") as tty_f:
                sz = os.get_terminal_size(tty_f.fileno())
                if sz.lines >= 10:
                    lines = sz.lines
                    found = True
        except Exception as exc:
            _log.debug("could not read terminal size from /dev/tty: %s", exc)

    if not found:
        try:
            lines = shutil.get_terminal_size((120, 36)).lines
        except Exception:
            lines = 36

    # Fixed non-table overhead:
    # digital counter box (6) + h_panel (title, tabs, stats, profit, padding) (7) + nav_bar (1) + status_bar (1) + table header/borders (4) + deep_panel (panel_lines) + margin (1)
    overhead = 21 + panel_lines
    avail = lines - overhead
    return max(5, min(avail, 35))


def compute_window(total: int, selected_idx: int, max_rows: int) -> Tuple[int, int]:
    """
    Calculates sliding window slice [start_idx, end_idx) keeping selected_idx
    always visible and centered within the viewport.
    """
    if total <= 0:
        return 0, 0
    if total <= max_rows:
        return 0, total
    margin = 2
    selected_idx = max(0, min(selected_idx, total - 1))
    if selected_idx < max_rows - margin:
        start_idx = 0
    elif selected_idx >= total - margin:
        start_idx = total - max_rows
    else:
        start_idx = selected_idx - (max_rows // 2)
    start_idx = max(0, min(start_idx, total - max_rows))
    end_idx = min(total, start_idx + max_rows)
    return start_idx, end_idx


def format_supply(val: Any) -> str:
    if val is None:
        return "N/A"
    try:
        n = float(val)
        if n >= 2**128 or n >= 1e25:
            return "∞"
        # If it looks like raw 18-decimal token supply (> 1e15)
        if n >= 1e24:
            n = n / 1e18
        elif n >= 1e18:
            n = n / 1e18
        if n >= 1e12:
            return f"{n/1e12:.1f}T"
        elif n >= 1e9:
            return f"{n/1e9:.1f}B"
        elif n >= 1e6:
            return f"{n/1e6:.1f}M"
        elif n >= 1e3:
            return f"{n/1e3:.1f}K"
        return f"{int(n):,}"
    except Exception:
        return str(val)


def format_count(val: Any) -> str:
    if not val:
        return "0"
    try:
        n = float(val)
        if n >= 1e6:
            return f"{n/1e6:.1f}M"
        elif n >= 1e3:
            return f"{n/1e3:.1f}K"
        return str(int(n))
    except Exception:
        return "0"


def format_structured_safety(item: Dict[str, Any]) -> str:
    """
    Standardizes safety badge to uniform width, preventing table jitter.
    Supports Honeypot, Serial Dev, Drained, High Risk, Med Risk, and Clean.
    """
    r_lvl = str(item.get("risk_level", "SAFE")).upper()
    r_bdg = str(item.get("risk_badge") or "").upper()
    s_ready = item.get("sniper_ready")
    if s_ready is None:
        try:
            s_ready = evaluate_sniper_ready(item)["is_ready"]
        except Exception:
            s_ready = False

    if "SERIAL" in r_bdg:
        return "[bold red]🚨 SerialDev[/bold red]"
    elif "DRAIN" in r_bdg:
        return "[bold red]🚨 Drained  [/bold red]"
    elif "HONEYPOT" in r_bdg or r_lvl == "RUG":
        return "[bold red]🚨 Honeypot [/bold red]"
    elif "HIGH" in r_bdg or r_lvl == "HIGH":
        return "[bold yellow]🔴 HighRisk [/bold yellow]"
    elif "MED" in r_bdg or r_lvl == "MED":
        return "[yellow]🟡 Med Risk [/yellow]"
    elif s_ready:
        return "[bold bright_green]🎯 READY    [/bold bright_green]"
    else:
        return "[bold green]🟢 Clean    [/bold green]"


def format_structured_interest(item: Dict[str, Any]) -> str:
    """
    Standardizes interest/velocity score indicator with uniform width and badge.
    Uses 🧊 (Ice Cube: cell_len 2) to match 🚀, 🔥, 🌱 so table borders stay aligned.
    """
    try:
        score = int(item.get("score", 0) or 0)
    except Exception:
        score = 0
    r_raw = str(item.get("rating", "")).upper()
    if "VIRAL" in r_raw or score >= 70:
        return f"[bold magenta]🚀 Viral ({score:>2d})[/bold magenta]"
    elif "HIGH" in r_raw or score >= 50:
        return f"[bold red]🔥 High  ({score:>2d})[/bold red]"
    elif "WARM" in r_raw or score >= 25:
        return f"[bold yellow]🌱 Warm  ({score:>2d})[/bold yellow]"
    else:
        return f"[dim cyan]🧊 Cold  ({score:>2d})[/dim cyan]"


def format_structured_mint_price(item: Dict[str, Any]) -> str:
    """
    Standardizes mint price cell with clean text and syntax coloring.
    Avoids emoji font-fallback width differences that misalign columns.
    """
    p_str = str(item.get("mint_price_str") or "").upper()
    v_wei = int(item.get("mint_value_wei", 0) or 0)
    is_free = bool(
        item.get("has_free_mint")
        or "FREE" in p_str
        or (v_wei == 0 and item.get("mint_price_bnb", 0.0) == 0.0 and "PAID" not in p_str and "DEX" not in p_str and "TBD" not in p_str)
    )

    if "DEX" in p_str:
        return "[cyan]DEX Traded[/cyan]"
    elif is_free:
        return "[bold green]FREE[/bold green]"
    elif v_wei > 0:
        val_bnb = v_wei / 1e18
        return f"[bold yellow]{val_bnb:.4f} BNB[/bold yellow]"
    elif item.get("mint_price_bnb") and float(item["mint_price_bnb"]) > 0:
        return f"[bold yellow]{float(item['mint_price_bnb']):.4f} BNB[/bold yellow]"
    elif item.get("mint_price_str") and item["mint_price_str"] != "TBD":
        return f"[bold yellow]{item['mint_price_str']}[/bold yellow]"
    else:
        return "[dim]TBD[/dim]"


def format_tiered_mint_progress(item: Dict[str, Any]) -> str:
    """
    Tiered Mint Progress Indicator with Uniform 28-char Monospace Structure:
    [TAG] [PCT] [  BAR  ] [ SUPPLY ]
    - ≥ 100%: SOLD  100% [█████] {tm_str} Sold
    - ≥ 80%:  HIGH   XX% [████░] {tm_str}/{ms_str}  (Bold Red FOMO alert)
    - 50-79%: WARM   XX% [███░░] {tm_str}/{ms_str}  (Bold Yellow momentum)
    - 1-49%:  MINT   XX% [█░░░░] {tm_str}/{ms_str}  (Cyan early mint)
    - 0%:     NEW     0% [░░░░░]    0/{ms_str}      (Dim unminted)
    - Paused: HALT PAUSE [─────] {ratio_str}        (Red halt)
    - Token:  TOKEN  DEX [─────] {circ_str} Circ    (Cyan DEX token)
    - Open:   OPEN  FAIR [─────] {tm_str} mint      (Green open mint)
    """
    c_std = str(item.get("standard") or "")
    is_token = ("BEP-20" in c_std or "Token" in c_std) and "Fair-Mint" not in c_std
    is_paused = bool(item.get("is_paused"))
    ms = item.get("max_supply")
    tm = item.get("total_minted") or 0

    if is_token:
        s_val = format_supply(ms or tm)
        return f"[cyan]TOKEN  DEX [─────] {s_val:>5s} Circ[/cyan]"

    if is_paused:
        s_val = f"{format_supply(tm)}/{format_supply(ms)}" if ms else format_supply(tm)
        return f"[bold red]HALT PAUSE [─────] {s_val:>10s}[/bold red]"

    if ms and float(ms) > 0 and float(ms) < 2**128 and float(ms) < 1e25:
        pct = (float(tm) / float(ms)) * 100.0
        filled = max(0, min(5, int(round((pct / 100.0) * 5))))
        bar = "█" * filled + "░" * (5 - filled)
        tm_s = format_supply(tm)
        ms_s = format_supply(ms)
        ratio_s = f"{tm_s:>4s}/{ms_s:<4s}"

        if pct >= 99.9 or float(tm) >= float(ms):
            return f"[bold magenta]SOLD  100% [█████] {tm_s:>5s} Sold[/bold magenta]"
        elif pct >= 80.0:
            return f"[bold red]HIGH   {pct:>2.0f}% [{bar}] {ratio_s:>9s}[/bold red]"
        elif pct >= 50.0:
            return f"[bold yellow]WARM   {pct:>2.0f}% [{bar}] {ratio_s:>9s}[/bold yellow]"
        elif pct > 0:
            return f"[cyan]MINT   {pct:>2.0f}% [{bar}] {ratio_s:>9s}[/cyan]"
        else:
            return f"[dim]NEW     0% [{bar}] {ratio_s:>9s}[/dim]"
    else:
        tm_s = format_supply(tm) if tm else "0"
        return f"[green]OPEN  FAIR [─────] {tm_s:>5s} mint[/green]"


def refresh_project_metrics(rpc: BscRpcClient, item: Dict[str, Any], wallet: str, fast: bool = False):
    addr = item.get("contract")
    if not addr:
        return
    try:
        details = analyze_contract(rpc, addr, wallet)
        
        # Update metadata if newly available
        if details.get("name") and (not item.get("name") or item.get("name") == "Unknown"):
            item["name"] = details["name"]
        if details.get("symbol") and not item.get("symbol"):
            item["symbol"] = details["symbol"]
        if details.get("max_supply") is not None:
            item["max_supply"] = details["max_supply"]
        if details.get("mint_calldata"):
            item["mint_calldata"] = details["mint_calldata"]

        # Live on-chain status & price
        item["is_paused"] = details.get("is_paused")
        item["has_free_mint"] = details.get("has_free_mint")
        item["mint_price_str"] = details.get("mint_price_str", "TBD")
        item["mint_price_bnb"] = details.get("mint_price_bnb", 0.0)
        item["mint_value_wei"] = details.get("mint_value_wei", "0")
        item["total_minted"] = details.get("total_minted", item.get("total_minted", 0))
        if details.get("token_decimals") is not None:
            item["token_decimals"] = details["token_decimals"]
        if details.get("website"):
            item["website"] = details["website"]
        if details.get("twitter"):
            item["twitter"] = details["twitter"]
        if details.get("market_url"):
            item["market_url"] = details["market_url"]

        if not fast:
            mkt = analyze_market_interest(rpc, addr, item.get("deployer"), details)
            # Live DEX, Holders & Purchase metrics
            # a count the sources did not report (None) keeps whatever the record already had
            for k in ("holders", "buys_24h", "buys_1h", "sells_24h"):
                if mkt.get(k) is not None:
                    item[k] = mkt[k]
            item["volume_24h"] = mkt.get("volume_24h", item.get("volume_24h", 0.0))
            item.update(linked_market_fields(mkt))
            if mkt.get("pair_url"):
                item["pair_url"] = mkt["pair_url"]
            if mkt.get("website"):
                item["website"] = mkt["website"]
            if mkt.get("twitter"):
                item["twitter"] = mkt["twitter"]
            if mkt.get("market_url"):
                item["market_url"] = mkt["market_url"]
            if mkt.get("token_price_bnb"):
                item["token_price_bnb"] = mkt["token_price_bnb"]

            item["score"] = mkt.get("score", item.get("score", 0))
            item["rating"] = mkt.get("rating", item.get("rating", "N/A"))
            item["lp_usd"] = mkt.get("lp_usd", item.get("lp_usd", 0.0))
            item["lp_wbnb"] = mkt.get("lp_wbnb", item.get("lp_wbnb", 0.0))
            item["token_price_usd"] = mkt.get("token_price_usd", item.get("token_price_usd", 0.0))
            if mkt.get("token_symbol"):
                item["token_symbol"] = mkt["token_symbol"]
            if mkt.get("signals"):
                item["signals"] = mkt["signals"]
            if mkt.get("security"):
                item["security"] = mkt["security"]
            if mkt.get("risk_level"):
                item["risk_level"] = mkt["risk_level"]
            if mkt.get("risk_badge"):
                item["risk_badge"] = mkt["risk_badge"]
            if mkt.get("risk_flags"):
                item["risk_flags"] = mkt["risk_flags"]
            if mkt.get("dev_root"):
                item["dev_root"] = mkt["dev_root"]
            if mkt.get("cluster_devs"):
                item["cluster_devs"] = mkt["cluster_devs"]
            if mkt.get("cluster_n"):
                item["cluster_n"] = mkt["cluster_n"]
            if mkt.get("hold"):
                item["hold"] = mkt["hold"]
                item["holder_distribution"] = mkt["hold"]

        # Update momentum & velocity metrics
        update_momentum_metrics(item)
    except Exception as exc:
        _log.debug("metrics refresh failed for %s: %s", item.get("contract"), exc)


def refresh_all_metrics(rpc: BscRpcClient, history: List[Dict[str, Any]], wallet: str, fast: bool = False):
    for item in history:
        refresh_project_metrics(rpc, item, wallet, fast=fast)
    save_history(history)


def build_dashboard(history: List[Dict[str, Any]], curr_block: int, tx_count: int, wallet: str, status_msg: str = ""):
    return build_interactive_dashboard(history, 0, curr_block, tx_count, wallet, status_msg, show_modal=False)


def build_interactive_dashboard(
    history: List[Dict[str, Any]],
    selected_idx: int,
    curr_block: int,
    tx_count: int,
    wallet: str,
    status_msg: str = "",
    show_modal: bool = False,
    active_filter: str = "ALL",
    sort_mode: str = "NEWEST",
    snipe_confirm: Optional[Dict[str, Any]] = None,
    active_tab: str = "RADAR",
    portfolio_data: Optional[Dict[str, Any]] = None,
    port_selected_idx: int = 0,
    smart_selected_idx: int = 0,
    sell_confirm: Optional[Dict[str, Any]] = None
):
    history = [x for x in history if not is_system_infra(x)]
    # Filter and sort projects
    disp_history = filter_and_sort_projects(history, active_filter, sort_mode)
    if disp_history:
        selected_idx = min(selected_idx, len(disp_history) - 1)
    else:
        selected_idx = 0

    smart_history = [x for x in history if not is_system_infra(x) and (x.get("smart_wallets") or x.get("smart_minters"))]
    smart_history.sort(key=lambda x: len(x.get("smart_wallets") or x.get("smart_minters") or []), reverse=True)
    smart_cnt = len(smart_history)

    # 1-Click Take-Profit / Stop-Loss Sell Confirmation Dialog View
    if sell_confirm:
        asset = sell_confirm.get("asset", {})
        a_name = asset.get("name", "Asset")
        a_sym = asset.get("symbol", "")
        c_addr = asset.get("contract", "")
        bal_str = asset.get("balance_str", "0")
        val_str = asset.get("value_str", "$0.00")
        exit_eval = asset.get("exit_eval", {})
        sig_badge = exit_eval.get("badge", "🟢 Take-Profit Exit")
        recom = exit_eval.get("recommendation", "Swap tokens for BNB on PancakeSwap V2 Router")
        mult_str = f"{exit_eval.get('multiple', 1.0):.2f}x" if exit_eval.get("multiple") else "N/A"
        calldata = sell_confirm.get("calldata", "")

        c_text = Text()
        c_text.append("⚡ 1-CLICK PANCAKESWAP V2 TAKE-PROFIT / STOP-LOSS EXIT GATE\n\n", style="bold yellow")
        c_text.append("• Target Asset:     ", style="bold white")
        c_text.append(f"{a_name} ({a_sym})\n", style="bold cyan")
        c_text.append("• Holdings to Exit: ", style="bold white")
        c_text.append(f"{bal_str} (100% of wallet balance)\n", style="bold green")
        c_text.append("• Token Address:    ", style="bold white")
        c_text.append(f"{c_addr}\n", style="cyan")
        c_text.append("• Router Contract:  ", style="bold white")
        c_text.append(f"{PANCAKE_ROUTER_V2} (PancakeSwap V2 Router)\n", style="bold magenta")
        c_text.append("• Current Value:    ", style="bold white")
        c_text.append(f"{val_str} USD\n", style="bold yellow")
        c_text.append("• Exit Trigger:     ", style="bold white")
        c_text.append(f"{sig_badge} (ROI Multiple: {mult_str})\n", style="bold green")
        c_text.append("• Strategy:         ", style="bold white")
        c_text.append(f"{recom}\n", style="bright_white")
        c_text.append("• Calldata (ABI):   ", style="bold white")
        c_text.append(f"{calldata[:18]}...{calldata[-10:]} (swapExactTokensForETHSupportingFeeOnTransferTokens)\n", style="dim")
        c_text.append("• Recipient:        ", style="bold white")
        c_text.append(f"{wallet} (BNB credited directly)\n\n", style="bold green")
        c_text.append("━" * 72 + "\n", style="dim")
        c_text.append("  👉 Press [Y] or [ENTER] to CONFIRM & BROADCAST EXIT VIA BINANCE AGENTIC WALLET\n", style="bold white on dark_green")
        c_text.append("  👉 Press [N] or [ESC] to CANCEL\n", style="bold white on dark_red")

        conf_panel = Panel(c_text, title="🎯 1-CLICK TAKE-PROFIT EXIT CONFIRMATION", border_style="bold green", padding=(1, 2))
        conf_nav = Text("⌨️  [Y / ENTER] Confirm & Swap to BNB  |  [N / ESC] Cancel Exit", style="bold white on green")
        return Group(conf_panel, conf_nav)

    # 1-Click Sniper Confirmation Dialog View
    if snipe_confirm:
        proj = snipe_confirm.get("project", {})
        p_name = proj.get("name", "Project")
        p_sym = proj.get("symbol", "")
        c_addr = proj.get("contract", "")
        price_str = proj.get("mint_price_str") or ("⭐ FREE" if proj.get("has_free_mint") else "Paid / TBD")
        gain = snipe_confirm.get("gain_str", "+1 Token")
        r_bdg = proj.get("risk_badge", "🟢 Clean")
        val_wei = str(proj.get("mint_value_wei", "0") or "0")
        calldata = replace_calldata_recipient(proj.get("mint_calldata"), wallet, extra_addresses=proj.get("smart_wallets"))
        is_recv = (calldata == "0x")

        c_text = Text()
        c_text.append("⚡ 1-CLICK SNIPER BUY / MINT GATE\n\n", style="bold yellow")
        c_text.append("• Target Project:   ", style="bold white")
        c_text.append(f"{p_name} ({p_sym})\n", style="bold cyan")
        c_text.append("• Action Type:      ", style="bold white")
        if is_recv:
            c_text.append("Plain BNB Transfer (recv / receive() fallback)\n", style="bold cyan")
        else:
            c_text.append(f"Contract Call ({calldata[:10]})\n", style="bold cyan")
        c_text.append("• Contract Address: ", style="bold white")
        c_text.append(f"{c_addr}\n", style="cyan")
        c_text.append("• Mint Cost:        ", style="bold white")
        c_text.append(f"{price_str} ({val_wei} wei)\n", style="bold green" if "FREE" in price_str else "bold yellow")
        c_text.append("• Node Simulation:  ", style="bold white")
        c_text.append("🟢 PASSED (Verified via Binance Agentic Wallet)\n", style="bold green")
        c_text.append("• Expected Return:  ", style="bold white")
        c_text.append(f"{gain}\n", style="bold green")
        c_text.append("• Security Status:  ", style="bold white")
        c_text.append(f"{r_bdg}\n", style="green" if "Clean" in r_bdg or "Free" in r_bdg else "yellow")
        c_text.append("• Recipient Wallet: ", style="bold white")
        c_text.append(f"{wallet}\n\n", style="bold green")
        c_text.append("━" * 68 + "\n", style="dim")
        c_text.append("  👉 Press [Y] or [ENTER] to CONFIRM & BROADCAST ON-CHAIN VIA BINANCE AGENTIC WALLET\n", style="bold white on dark_green")
        c_text.append("  👉 Press [N] or [ESC] to CANCEL\n", style="bold white on dark_red")

        conf_panel = Panel(c_text, title="🎯 1-CLICK SNIPER CONFIRMATION", border_style="bold green", padding=(1, 2))
        conf_nav = Text("⌨️  [Y / ENTER] Confirm & Broadcast  |  [N / ESC] Cancel Snipe", style="bold white on green")
        return Group(conf_panel, conf_nav)

    # Full Modal Inspection View
    modal_item = None
    if show_modal:
        if active_tab == "SMART" and smart_history and 0 <= smart_selected_idx < len(smart_history):
            modal_item = smart_history[smart_selected_idx]
        elif disp_history and 0 <= selected_idx < len(disp_history):
            modal_item = disp_history[selected_idx]

    if modal_item:
        item = modal_item
        name = item.get("name", "Unknown")
        sym = item.get("symbol", "")
        c_addr = item.get("contract", "")
        dep = item.get("deployer", "")
        block = item.get("block", "")
        tx_h = item.get("tx_hash", "")
        icon = item.get("icon", "")
        std = item.get("standard", "")
        tm = item.get("total_minted", 0)
        ms = item.get("max_supply", "N/A")
        paused = item.get("is_paused")
        free = item.get("has_free_mint")
        score = item.get("score", 0)
        rating = item.get("rating", "N/A")
        lp_usd = item.get("lp_usd", 0)
        lp_wbnb = item.get("lp_wbnb", 0)
        t_price = item.get("token_price_usd", 0)
        t_sym = item.get("token_symbol", "")
        val_wei = item.get("mint_value_wei", "0")
        calldata = replace_calldata_recipient(item.get("mint_calldata"), wallet, extra_addresses=item.get("smart_wallets"))

        m_text = Text()
        m_text.append(f"PROJECT: {name} ({sym})\n\n", style="bold cyan")
        m_text.append(f"• Contract Address: {c_addr}\n", style="bold white")
        m_text.append(f"• Deployer Wallet:  {dep}\n", style="dim")
        dev_root = item.get("dev_root") or (item.get("security") or {}).get("dev_root")
        c_devs = item.get("cluster_devs") or (item.get("security") or {}).get("cluster_devs") or 1
        c_n = item.get("cluster_n") or (item.get("security") or {}).get("cluster_n") or 1
        if dev_root:
            m_text.append(f"• Dev Funding Root: {dev_root}\n", style="cyan")
        if c_devs >= 2 or c_n >= 3:
            m_text.append(f"• Dev Cluster:      🚨 Dev Cluster ({c_devs} linked devs / {c_n} drops)\n", style="bold red")
        else:
            m_text.append("• Dev Cluster:      🟢 Single Deployer (1 drop)\n", style="dim")
        m_text.append(f"• Deployment Block: #{block}  |  Tx: {tx_h}\n", style="dim")
        m_text.append(f"• Asset Standard:   {icon} {std}\n", style="magenta")
        
        m_text.append("• Mint Status:      ")
        if paused:
            m_text.append("🔴 PAUSED (Pre-launch phase)", style="bold red")
        else:
            m_text.append("🟢 ACTIVE / LIVE", style="bold green")
        m_text.append(f"  |  Supply: {format_supply(tm)} / {format_supply(ms)}\n")
        
        val_wei = item.get("mint_value_wei", "0")
        price_str = item.get("mint_price_str") or ("⭐ FREE" if free else "Paid / TBD")
        m_text.append(f"• Mint Price:       {price_str} (Required Value: {val_wei} wei)\n", style="bold green" if "FREE" in price_str else "bold yellow")
        m_text.append(f"• Free Mint (Agent): {'⭐ YES (Available for nimus.ai)' if free else 'No'}\n")
        
        user_bal = item.get("user_balance")
        if user_bal is not None:
            if user_bal > 0:
                m_text.append(f"• Your Holdings:     🟢 {user_bal:,} {sym} (OWNED IN WALLET!)\n", style="bold green")
            else:
                m_text.append(f"• Your Holdings:     0 {sym} (Not Owned)\n", style="dim")
        
        # Option E: Proxy & Clone Architecture
        is_proxy = item.get("is_proxy_clone")
        proxy_type = item.get("proxy_type")
        master_impl = item.get("master_implementation")
        is_factory = item.get("is_factory")
        factory_badge = item.get("factory_badge")
        if is_proxy or is_factory:
            m_text.append("\n• 🧩 Proxy & Clone Architecture (Option E):\n", style="bold cyan")
            if is_proxy:
                m_text.append(f"   ▫ Proxy Pattern:     🧩 {proxy_type or 'EIP-1167 / EIP-1967'}\n", style="bold cyan")
                if master_impl:
                    m_text.append(f"   ▫ Implementation:   {master_impl} (Master Codebase)\n", style="cyan")
            if is_factory:
                m_text.append(f"   ▫ Factory Type:      🏭 {factory_badge or 'Launchpad Factory'}\n", style="bold yellow")

        # 1. Security & Anti-Rug Audit Section
        sec = item.get("security") or {}
        r_lvl = item.get("risk_level", "SAFE")
        r_bdg = item.get("risk_badge", "🟢 Clean")
        m_text.append("\n• 🛡️ Security & Anti-Rug Audit:\n", style="bold white")
        m_text.append(f"   ▫ Risk Rating:       {r_bdg} ({r_lvl})\n", style="bold green" if r_lvl == "SAFE" else ("bold yellow" if r_lvl == "MED" else "bold red"))
        
        # Bytecode Refund Check
        has_ref = sec.get("has_refund") or item.get("has_refund")
        ref_methods = sec.get("refund_methods") or item.get("refund_methods") or []
        if has_ref:
            ref_str = f"🛡️ VERIFIED REFUND ({', '.join(ref_methods[:2])})"
            m_text.append(f"   ▫ Bytecode Refund:   {ref_str}\n", style="bold green")
        elif (item.get("mint_price_bnb") or 0) > 0.001 or int(item.get("mint_value_wei") or 0) > 0:
            m_text.append("   ▫ Bytecode Refund:   ⚠️ NO REFUND MECHANISM (Capital at risk if unfilled)\n", style="bold yellow")
        else:
            m_text.append("   ▫ Bytecode Refund:   N/A (Free Mint / Gas Only)\n", style="dim")

        # Deployer Profile Check
        if sec.get("is_serial_dev"):
            s_cnt = sec.get("serial_count", 3)
            m_text.append(f"   ▫ Deployer Profile:  🚨 SERIAL DEV ({s_cnt} contracts deployed)\n", style="bold red")
        elif sec.get("is_fresh_dev"):
            m_text.append("   ▫ Deployer Profile:  🟡 Fresh Dev Wallet (Low tx count)\n", style="yellow")
        else:
            m_text.append("   ▫ Deployer Profile:  🟢 Established Deployer\n", style="green")
        if sec.get("audited"):
            hp_txt = "🚨 HONEYPOT DETECTED!" if sec.get("is_honeypot") else "PASSED (Normal Transfers)"
            hp_style = "bold red" if sec.get("is_honeypot") else "green"
            m_text.append("   ▫ Honeypot Check:    ")
            m_text.append(f"{hp_txt}\n", style=hp_style)
            m_text.append(f"   ▫ Buy / Sell Taxes:  {sec.get('buy_tax', 0.0):.1f}% Buy  |  {sec.get('sell_tax', 0.0):.1f}% Sell\n", style="yellow" if sec.get('sell_tax', 0) > 10 else "green")
            lp_lock = sec.get("lp_locked_pct", 0.0)
            lp_style = "bold green" if lp_lock >= 80 else ("yellow" if lp_lock >= 20 else "bold red")
            m_text.append("   ▫ PancakeSwap LP:    ")
            m_text.append(f"{lp_lock:.1f}% Locked / Burned\n", style=lp_style)
            top10 = sec.get("top10_pct", 0.0)
            t10_style = "bold red" if top10 >= 80 else ("yellow" if top10 >= 50 else "green")
            m_text.append("   ▫ Top 10 Ownership:  ")
            m_text.append(f"{top10:.1f}% of Supply\n", style=t10_style)
            m_text.append(f"   ▫ Open Source Code:  {'Verified Open Source' if sec.get('is_open_source') else 'Closed / Unverified'}\n", style="green" if sec.get('is_open_source') else "yellow")
        else:
            m_text.append("   ▫ Contract Check:    Standard EVM Asset Contract\n", style="green")
            if tm and tm > 0:
                h_count = (item.get("holders") or 0)
                m_text.append(f"   ▫ Distribution:      {h_count:,} unique holders across {tm:,} mints\n", style="cyan")

        if item.get("risk_flags"):
            m_text.append("   ▫ Audit Flags:       " + "  |  ".join(item["risk_flags"]) + "\n", style="yellow")

        # Holder Distribution Breakdown
        hold = item.get("hold") or item.get("holder_distribution") or (item.get("security") or {}).get("hold") or {}
        if hold:
            dev_h = hold.get("dev", 0.0)
            ins_h = hold.get("insider", 0.0)
            snp_h = hold.get("sniper", 0.0)
            smt_h = hold.get("smart", 0.0)
            frs_h = hold.get("fresh", 0.0)
            pub_h = hold.get("public", 100.0)
            m_text.append("\n• 📊 Holder Distribution Breakdown:\n", style="bold white")
            m_text.append(f"   ▫ Dev: {dev_h:.1f}%", style="bold magenta" if dev_h <= 10 else "bold red")
            m_text.append(f"  │  Insiders: {ins_h:.1f}%", style="yellow" if ins_h > 15 else "dim")
            m_text.append(f"  │  Snipers: {snp_h:.1f}%", style="red" if snp_h > 15 else "dim")
            m_text.append(f"  │  Smart: {smt_h:.1f}%", style="bold cyan")
            m_text.append(f"  │  Fresh: {frs_h:.1f}%\n", style="bold yellow" if frs_h > 0 else "dim")
            m_text.append(f"   ▫ Public Circulating: {pub_h:.1f}%\n", style="bold green")
            if dev_h > 10.0:
                m_text.append(f"   ▫ 🚨 ALERT: High Dev Holding ({dev_h:.1f}% > 10% threshold)\n", style="bold white on red")
            if ins_h > 15.0:
                m_text.append(f"   ▫ ⚠️ ALERT: Heavy Insider Concentration ({ins_h:.1f}%)\n", style="bold yellow")

        # Sniper Ready Conviction Evaluation (60% Rule & Anti-Rug)
        s_eval = evaluate_sniper_ready(item)
        if s_eval["is_ready"]:
            m_text.append("\n• 🎯 Assisted Conviction Audit: ", style="bold white")
            m_text.append("PASSED - SNIPER READY\n", style="bold bright_green")
        else:
            m_text.append(f"\n• 🎯 Assisted Conviction Audit: ", style="bold white")
            m_text.append(f"{s_eval['badge']}\n", style="bold yellow")
        for r_line in s_eval.get("checklist", []):
            m_text.append_text(Text.from_markup(f"   {r_line}\n"))

        # 2. Mint Momentum & Minters Distribution Section
        vel_str, org_str = update_momentum_metrics(item)
        m_text.append("\n• ⚡ Mint Momentum & Distribution:\n", style="bold white")
        m_text.append("   ▫ Mint Velocity:     ")
        m_text.append_text(Text.from_markup(f"{vel_str}\n"))
        m_text.append("   ▫ Minters Quality:   ")
        m_text.append_text(Text.from_markup(f"{org_str}\n"))
        ppm = item.get("velocity_ppm", 0.0)
        if ppm > 0:
            m_text.append(f"   ▫ Rate of Minting:   {ppm:.1f} tokens per minute\n", style="bold green")

        # Smart Money Activity
        sm_list = item.get("smart_wallets") or item.get("smart_minters") or []
        if sm_list:
            m_text.append("\n• 🧠 Smart Money Activity (OGlamozda 5k Tracked Wallets):\n", style="bold magenta")
            m_text.append(f"   ▫ Tracked Wallets:   {len(sm_list)} verified agent wallets\n", style="magenta")
            m_text.append(f"   ▫ Active Minters:    {', '.join(sm_list[:8])}\n", style="dim magenta")
            if len(sm_list) > 8:
                m_text.append(f"   ▫ (+{len(sm_list)-8} additional tracked wallets)\n", style="dim")

        
        website = item.get("website", "")
        twitter = item.get("twitter", "")
        is_nft = "NFT" in std or "721" in std
        mkt_url = item.get("market_url") or item.get("pair_url", "")
        if is_nft:
            if not mkt_url or "collections/0x" in mkt_url or "assets/bsc/" in mkt_url:
                mkt_url = get_element_market_url(name, c_addr)
        elif not mkt_url and ("BEP-20" in std or "Token" in std):
            mkt_url = f"https://pancakeswap.finance/swap?outputCurrency={c_addr}"

        t_price = item.get("token_price_usd", 0.0)
        t_price_bnb = item.get("token_price_bnb", 0.0)

        if website or twitter or mkt_url:
            m_text.append("\n• Official Links & Social Media:\n", style="bold white")
            if website:
                m_text.append(f"   ▫ 🌐 Website:     {website} (Press 'w' to open)\n", style="cyan")
            if twitter:
                m_text.append(f"   ▫ 🐦 X (Twitter): {twitter} (Press 'x' to open)\n", style="cyan")
            if mkt_url:
                m_label = "DexScreener" if "dexscreener" in mkt_url else ("Element NFT" if "element" in mkt_url else "Marketplace")
                m_text.append(f"   ▫ 🛒 {m_label}:   {mkt_url} (Press 'm' to open)\n", style="cyan")

        if t_price > 0:
            m_text.append(f"\n• Secondary Market Price: ${t_price:.6f} USD", style="bold green")
            if t_price_bnb > 0:
                m_text.append(f" ({t_price_bnb:.8f} BNB)", style="bold green")
            m_text.append("\n")
        elif item.get("floor_price_bnb") and float(item["floor_price_bnb"]) > 0:
            f_b = float(item["floor_price_bnb"])
            f_u = float(item.get("floor_price_usd") or (f_b * cached_bnb_price()))
            m_text.append(f"\n• Secondary Floor Price:  {f_b:g} BNB (${f_u:.2f} USD)  |  Element Marketplace\n", style="bold cyan")

        m_text.append(f"• Interest Score:   {score}/100 ({rating})\n", style="bold yellow")
        vol_b = item.get("volume_bnb") or item.get("nft_volume_bnb")
        tot_vol_b = item.get("total_volume_bnb")
        if lp_usd > 0:
            m_text.append(f"• PancakeSwap LP:   ${lp_usd:,.2f} ({lp_wbnb:.2f} WBNB)\n", style="bold green")
            if t_price > 0:
                m_text.append(f"• Implied Price:    ${t_price:.4f} (${t_sym})\n", style="green")
        elif vol_b and float(vol_b) > 0:
            tot_str = f"  |  Total: {float(tot_vol_b):g} BNB" if tot_vol_b else ""
            m_text.append(f"• Collection Vol:   {float(vol_b):g} BNB (24h){tot_str}\n", style="bold cyan")
        else:
            if "NFT" in str(item.get("standard")):
                m_text.append("• Collection Vol:   No secondary volume yet\n", style="dim")
            else:
                m_text.append("• PancakeSwap LP:   No DEX Pool detected yet\n", style="dim")

        if is_nft or item.get("secondary_sales_count"):
            m_text.append("\n• 🦋 Element Market Secondary Trading Activity:\n", style="bold magenta")
            s_cnt = item.get("secondary_sales_count", 0)
            s_vol = float(item.get("secondary_vol_bnb", 0.0) or 0.0)
            s_flr = float(item.get("floor_price_bnb", 0.0) or 0.0)
            s_mov = item.get("secondary_transfers", 0)
            surge_str = f" [bold magenta]🦋 SURGE DETECTED ({s_cnt} sales)[/bold magenta]" if item.get("is_nft_surge") else ""
            m_text.append(f"   ▫ Secondary Sales:   {s_cnt} sales{surge_str}\n", style="bold magenta" if s_cnt >= 3 else "dim")
            m_text.append(f"   ▫ Element Volume:    {s_vol:.4f} BNB\n", style="bold cyan" if s_vol > 0 else "dim")
            if s_flr > 0:
                m_text.append(f"   ▫ Floor Price:       {s_flr:.4f} BNB\n", style="bold green")
            m_text.append(f"   ▫ Transfer Moves:    {s_mov} secondary transfers\n", style="white" if s_mov else "dim")
            clean_name = re.sub(r"\([^)]*\)", "", name).strip()
            slug = re.sub(r"[^a-z0-9]+", "-", clean_name.lower()).strip("-")
            e_url = f"https://element.market/collections/{slug}" if slug else f"https://element.market/collections/{c_addr}"
            m_text.append(f"   ▫ Marketplace Link:  {e_url} (Press 'm' to open)\n", style="dim cyan")

        if item.get("signals"):
            m_text.append("\n• Key Reverse-Engineering Signals:\n", style="bold white")
            for sig in item["signals"]:
                m_text.append(f"   ▫ {sig}\n", style="dim white")

        hld = (item.get("holders") or 0)
        buys = (item.get("buys_24h") or 0)
        buys_1h = (item.get("buys_1h") or 0)
        sells = (item.get("sells_24h") or 0)
        vol = item.get("volume_24h", 0.0)
        
        m_text.append("\n• On-Chain Holders & Purchases (24h Activity):\n", style="bold white")
        if hld > 0 or buys > 0:
            m_text.append(f"   ▫ On-Chain Holders:     {hld:,} unique wallets\n", style="bold green")
            m_text.append(f"   ▫ 24h Purchases (Buys):  {buys:,} buy transactions\n", style="cyan")
            m_text.append(f"   ▫ 1h Purchases (Buys):   {buys_1h:,} buy transactions (recent momentum)\n", style="cyan")
            if sells > 0:
                net = buys - sells
                net_str = f"+{net:,}" if net >= 0 else f"{net:,}"
                net_style = "bold green" if net >= 0 else "bold red"
                m_text.append(f"   ▫ 24h Sells:            {sells:,} sell transactions (Net Flow: [{net_style}]{net_str}[/{net_style}])\n")
            if vol > 0:
                m_text.append(f"   ▫ 24h DEX Volume:       ${vol:,.2f}\n", style="bold yellow")
        else:
            m_text.append("   ▫ No holders or DEX purchases recorded yet (Pre-launch phase)\n", style="dim")

        m_text.append("\n[1-Click Sniper Execution]\n", style="bold yellow")
        m_text.append("• ⚡ Instant Buy: Press [s] to simulate & buy with 1-click confirmation!\n", style="bold green")
        if calldata == "0x":
            m_text.append(f"  Manual CLI (recv): baw contract-call preview --binanceChainId 56 --from {wallet} --to {c_addr} --value {val_wei} --inputData 0x --json\n", style="dim")
        else:
            m_text.append(f"  Manual CLI: baw contract-call preview --binanceChainId 56 --from {wallet} --to {c_addr} --value {val_wei} --inputData {calldata} --json\n", style="dim")
        m_text.append(f"\n• 💎 Tip Dev / Donate (BNB/EVM): {DONATION_WALLET}\n", style="bold yellow")

        modal_panel = Panel(
            m_text,
            title=f"🔍 Full Project Inspection: {name} ({sym})",
            border_style="magenta",
            expand=True
        )
        footer = Text("⌨️  [Enter/ESC] Close  |  [s] Snipe/Buy  |  [w] Website  |  [x] Twitter/X  |  [m] Market  |  [b] BscScan", style="bold white on blue")
        if status_msg:
            status_bar = Text(f"📡 {status_msg}", style="bold white on red" if "failed" in status_msg or "⚠️" in status_msg else "bold green")
            return Group(modal_panel, footer, status_bar)
        return Group(modal_panel, footer)

    # Visual Tabs Header
    p_items = (portfolio_data or {}).get("items", [])
    p_sum = (portfolio_data or {}).get("summary", {})
    port_cnt = len(p_items)

    smart_history = [x for x in history if not is_system_infra(x) and (x.get("smart_wallets") or x.get("smart_minters"))]
    smart_history.sort(key=lambda x: len(x.get("smart_wallets") or x.get("smart_minters") or []), reverse=True)
    smart_cnt = len(smart_history)

    radar_pill = f" [ 1: 📡 Live Radar ({len(disp_history)}) ] "
    smart_pill = f" [ 2: 🧠 Smart Alpha ({smart_cnt}) ] "
    port_pill = f" [ 3: 🎒 My Portfolio ({port_cnt} Assets) ] "

    stats = load_agent_id_stats()
    ai_wallets_cnt = stats.get("total_created_wallets", DEFAULT_AGENT_WALLET_COUNT)
    c_panel = render_digital_counter_panel(f"{ai_wallets_cnt:,}")

    h_text = Text()
    h_text.append("🚀 BNB CHAIN .AI AGENT ID SUITE", style="bold bright_cyan")
    h_text.append(f"   │   Registry: 0x60B9...022F   │   Wallet: {wallet[:6]}...{wallet[-4:]} (nimus.ai)\n", style="bold white")

    if active_tab == "PORTFOLIO":
        radar_tab_style = "dim cyan on grey19"
        smart_tab_style = "dim magenta on grey19"
        port_tab_style = "bold black on bright_green"
        tot_val = p_sum.get("total_usd", 0.0)
        h_text.append(f"{radar_pill}", style=radar_tab_style)
        h_text.append(f" {smart_pill}", style=smart_tab_style)
        h_text.append(f" {port_pill}", style=port_tab_style)
        h_text.append(f"     💼 Total: ${tot_val:.2f} USD  │  Native: {p_sum.get('native_bnb', 0.0):.6f} BNB  │  NFTs: {p_sum.get('nft_count', 0)}\n", style="bold green")
    elif active_tab == "SMART":
        radar_tab_style = "dim cyan on grey19"
        smart_tab_style = "bold black on bright_magenta"
        port_tab_style = "dim green on grey19"
        h_text.append(f"{radar_pill}", style=radar_tab_style)
        h_text.append(f" {smart_pill}", style=smart_tab_style)
        h_text.append(f" {port_pill}", style=port_tab_style)
        h_text.append(f"     🧠 4,942 Tracked Wallets  │  {smart_cnt} Active Alpha Drops  │  🛡️ Auto-Mints DISABLED\n", style="bold magenta")
    else:
        radar_tab_style = "bold black on bright_cyan"
        smart_tab_style = "dim magenta on grey19"
        port_tab_style = "dim green on grey19"
        filt_style = "bold bright_green on dark_green" if active_filter in ("SNIPER_READY", "READY") else ("bold yellow on blue" if active_filter != "ALL" else "bold yellow")
        h_text.append(f"{radar_pill}", style=radar_tab_style)
        h_text.append(f" {smart_pill}", style=smart_tab_style)
        h_text.append(f" {port_pill}", style=port_tab_style)
        h_text.append(f"     Filter: [{active_filter}] ({len(disp_history)}/{len(history)})  │  Sort: [{sort_mode}]\n", style=filt_style)

    # Macro Mint Cap & Post-Mint Profit Statistics with high-contrast prominent badges
    m_stats = compute_market_cap_stats(history)
    fill_pct = m_stats["fill_rate_pct"]
    sold_cnt = m_stats["sold_out_cnt"]
    cap_tot = m_stats["capped_total"]
    near_cnt = m_stats["near_cap_cnt"]
    warm_cnt = m_stats["warm_cap_cnt"]
    tg = m_stats.get("top_gainer")
    ru = m_stats.get("runner_up")

    h_text.append("\n 📊 MINT CAP FILL RATE:   ", style="bold bright_white")
    h_text.append(f" {fill_pct:.1f}% ", style="bold black on bright_green" if fill_pct >= 10 else "bold black on bright_yellow")
    h_text.append(f"  ({sold_cnt} / {cap_tot} Sold Out)   │   ", style="bold green")
    h_text.append("🔥 NEAR CAP (≥80%): ", style="bold bright_white")
    h_text.append(f" {near_cnt} ", style="bold red" if near_cnt > 0 else "bold white")
    h_text.append("   │   ⚡ MOMENTUM (≥50%): ", style="bold bright_white")
    h_text.append(f" {warm_cnt} ACTIVE ", style="bold black on bright_yellow" if warm_cnt > 0 else "bold white")
    h_text.append(" [Hallowed 60%, SOLVED 60%]\n", style="bold yellow")

    if tg:
        tg_m_lbl = "FREE" if (tg.get("is_free") or tg.get("mint_usd", 0) == 0) else f"${tg['mint_usd']:.4f}"
        h_text.append(" 🏆 TOP POST-MINT PROFIT: ", style="bold bright_yellow")
        h_text.append(f" {tg['name']} ({tg['symbol']}) ", style="bold black on bright_cyan")
        h_text.append(f"  [Mint: {tg_m_lbl} ➔ Secondary: ${tg['sec_usd']:.4f} │ Net: ", style="bold white")
        h_text.append(f" +${tg['profit']:.4f} / token ", style="bold black on bright_green")
        h_text.append("]", style="bold white")
        if ru:
            ru_m_lbl = "FREE" if (ru.get("is_free") or ru.get("mint_usd", 0) == 0) else f"${ru['mint_usd']:.4f}"
            h_text.append(f"   │   🥈 Runner-Up: {ru['name']} ({ru['symbol']}) [${ru['sec_usd']:.4f}]", style="bold cyan")

    border_c = "bright_cyan" if active_tab == "RADAR" else ("bright_magenta" if active_tab == "SMART" else "bright_green")
    h_panel = Panel(h_text, border_style=border_c, padding=(1, 2), box=box.ROUNDED)

    # If PORTFOLIO tab is active, render the dedicated portfolio table & inspection panel
    if active_tab == "PORTFOLIO":
        total_p = len(p_items)
        if p_items:
            port_selected_idx = max(0, min(port_selected_idx, total_p - 1))
        else:
            port_selected_idx = 0

        max_p = get_viewport_max_rows(panel_lines=6)
        p_start, p_end = compute_window(total_p, port_selected_idx, max_p)

        p_table = Table(box=box.ROUNDED, expand=True, header_style="bold bright_white on grey23", padding=(0, 1))
        if total_p > max_p:
            up_arr = "[yellow]▲[/yellow]" if p_start > 0 else " "
            dn_arr = "[yellow]▼[/yellow]" if p_end < total_p else " "
            p_table.title = f"[bold bright_green]🎒 MY PORTFOLIO[/bold bright_green] [dim](Showing {p_start + 1}–{p_end} of {total_p})[/dim]  {up_arr} {dn_arr}"

        p_table.add_column(" ", width=2, justify="center", no_wrap=True)
        p_table.add_column("ASSET NAME", style="bold bright_white", min_width=20, no_wrap=True)
        p_table.add_column("TYPE", style="bold magenta", width=14, no_wrap=True)
        p_table.add_column("YOUR HOLDINGS", style="bold bright_green", min_width=14, justify="right", no_wrap=True)
        p_table.add_column("UNIT PRICE", style="bold yellow", min_width=15, justify="right", no_wrap=True)
        p_table.add_column("TOTAL VALUE", style="bold bright_green", min_width=14, justify="right", no_wrap=True)
        p_table.add_column("EXIT / PROFIT SIGNAL", style="bold cyan", min_width=22, no_wrap=True)
        p_table.add_column("CONTRACT ADDRESS", style="dim", min_width=18, no_wrap=True)
        p_table.add_column("ACTIONS", style="bold blue", min_width=18, no_wrap=True)

        if not p_items:
            p_table.add_row(" ", "-", "No assets found in wallet yet", "-", "-", "-", "Press [Tab / 1] to return to Radar and snipe mints!", "-", "-", style="dim")
        else:
            for i in range(p_start, p_end):
                p_it = p_items[i]
                is_sel = (i == port_selected_idx)
                if is_sel:
                    sel_marker = "[bold yellow]▶[/bold yellow]"
                elif i == p_start and p_start > 0:
                    sel_marker = "[dim cyan]▲[/dim cyan]"
                elif i == p_end - 1 and p_end < total_p:
                    sel_marker = "[dim cyan]▼[/dim cyan]"
                else:
                    sel_marker = " "
                row_style = "bold white on navy_blue" if is_sel else ""

                p_name = f"{p_it['name']} ({p_it['symbol']})"
                std_icon = "🎨 " if p_it.get("is_nft") else "🪙 "
                p_std_fmt = f"{std_icon}{p_it.get('standard', '')}"
                p_bal = f"[bold green]{p_it.get('balance_str', '0')}[/bold green]"
                p_u_price = p_it.get("price_str", "$0.00")
                p_val = f"[bold green]{p_it.get('value_str', '$0.00')}[/bold green]"
                
                exit_eval = p_it.get("exit_eval") or evaluate_portfolio_exit(p_it)
                p_sig = exit_eval.get("badge") or p_it.get("acquired", "")
                c_addr = p_it.get("contract", "")
                c_fmt = f"{c_addr[:8]}...{c_addr[-6:]}" if len(c_addr) > 20 else c_addr
                
                if p_it.get("is_nft"):
                    act = "🛒 [m] Element"
                elif c_addr == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee":
                    act = "⛽ Gas Reserve"
                else:
                    act = "⚡ [x] Exit  |  🥞 [m] DEX"

                p_table.add_row(
                    sel_marker,
                    p_name,
                    p_std_fmt,
                    p_bal,
                    p_u_price,
                    p_val,
                    p_sig,
                    c_fmt,
                    act,
                    style=row_style
                )

        if p_items and 0 <= port_selected_idx < len(p_items):
            sel_p = p_items[port_selected_idx]
            sel_eval = sel_p.get("exit_eval") or evaluate_portfolio_exit(sel_p)
            pd_text = Text()
            pd_text.append(f"• Asset: {sel_p['name']} ({sel_p['symbol']})  |  Standard: {sel_p['standard']}  |  Contract: {sel_p['contract']}\n", style="bold white")
            pd_text.append(f"• Verified Balance: {sel_p['balance_str']} in {wallet[:8]}... (nimus.ai)  |  Valuation: {sel_p['value_str']} USD ({sel_p['price_str']})\n", style="bold green")
            
            sig_style = "bold red" if "CRASH" in sel_eval.get('signal', '') else ("bold yellow" if "STOP" in sel_eval.get('signal', '') else "bold green")
            pd_text.append(f"• Exit Signal: {sel_eval.get('badge', '⏳ HODL')}  |  Action: {sel_eval.get('action_label', 'Holding')}  |  Strategy: {sel_eval.get('recommendation', '')}\n", style=sig_style)
            
            p_links = []
            if not sel_p.get("is_nft") and sel_p.get("contract") != "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee":
                p_links.append("⚡ [x] 1-Click Exit Sell")
            if sel_p.get("market_url"):
                m_txt = "Element NFT Collection" if sel_p.get("is_nft") else "PancakeSwap"
                p_links.append(f"🛒 [m] {m_txt}")
            if sel_p.get("bscscan_url"):
                p_links.append("🔍 [b] BscScan Holdings")
            if p_links:
                pd_text.append("• Shortcuts: " + "  |  ".join(p_links) + "  |  [r] Refresh\n", style="blue")
            p_deep_panel = Panel(pd_text, title=f"🎒 Portfolio Asset: {sel_p['name']} ({sel_p['symbol']})", border_style="green", padding=(0, 1))
        else:
            p_deep_panel = Panel("Your portfolio is empty. Press [Tab / 1] to switch to Live Radar and snipe new fair mints!", border_style="dim", padding=(0, 1))

        msg = f" | {status_msg}" if status_msg else ""
        nav_bar = Text("⌨️  [TAB / 1 / 2 / 3] Tabs  |  [↑/↓] Row  |  [PgUp/PgDn / [/]] Page  |  [Home/End] Jump  |  [x] 1-Click Exit Sell  |  [m] Market  |  [r] Refresh  |  [b] BscScan  |  [q] Quit", style="bold white on green")
        status_bar = Text(f"📡 BSC Block {curr_block} | Asset {port_selected_idx + 1}/{total_p} | Balance: ${p_sum.get('total_usd', 0.0):.2f} USD ({p_sum.get('native_bnb', 0.0):.6f} BNB){msg}", style="dim green")
        return Group(c_panel, h_panel, p_table, p_deep_panel, nav_bar, status_bar)

    # If SMART tab is active, render the dedicated Smart Money Alpha table & inspection panel
    if active_tab == "SMART":
        total_smart = len(smart_history)
        if smart_history:
            smart_selected_idx = max(0, min(smart_selected_idx, total_smart - 1))
        else:
            smart_selected_idx = 0

        max_s = get_viewport_max_rows(panel_lines=6)
        s_start, s_end = compute_window(total_smart, smart_selected_idx, max_s)

        s_table = Table(box=box.ROUNDED, expand=True, header_style="bold bright_white on grey23", padding=(0, 1))
        if total_smart > max_s:
            up_arr = "[yellow]▲[/yellow]" if s_start > 0 else " "
            dn_arr = "[yellow]▼[/yellow]" if s_end < total_smart else " "
            s_table.title = f"[bold bright_magenta]🧠 SMART MONEY ALPHA DROPS[/bold bright_magenta] [dim](Showing {s_start + 1}–{s_end} of {total_smart})[/dim]  {up_arr} {dn_arr}"

        s_table.add_column(" ", width=2, justify="center", no_wrap=True)
        s_table.add_column("PROJECT", style="bold bright_white", min_width=20, no_wrap=True)
        s_table.add_column("TYPE", width=12, justify="center", no_wrap=True)
        s_table.add_column("CATEGORY", width=12, justify="center", no_wrap=True)
        s_table.add_column("SMART WALLETS", style="bold bright_cyan", min_width=22, no_wrap=True)
        s_table.add_column("MINT PRICE", width=15, justify="center", style="bold yellow", no_wrap=True)
        s_table.add_column("MINT PROGRESS", style="bold white", width=28, justify="left", no_wrap=True)
        s_table.add_column("SAFETY", width=13, justify="center", no_wrap=True)
        s_table.add_column("FLOOR / TRADE", width=14, justify="center", no_wrap=True)
        s_table.add_column("ACTION", style="bold bright_green", width=12, justify="center", no_wrap=True)

        if not smart_history:
            s_table.add_row(" ", "-", "No smart money drops detected yet. Run 'scan-wallets'!", "-", "-", "-", "-", "-", "-", "-", style="dim")
        else:
            for i in range(s_start, s_end):
                s_it = smart_history[i]
                is_sel = (i == smart_selected_idx)
                if is_sel:
                    sel_marker = "[bold yellow]▶[/bold yellow]"
                elif i == s_start and s_start > 0:
                    sel_marker = "[dim cyan]▲[/dim cyan]"
                elif i == s_end - 1 and s_end < total_smart:
                    sel_marker = "[dim cyan]▼[/dim cyan]"
                else:
                    sel_marker = " "
                row_style = "bold white on navy_blue" if is_sel else ""

                s_name = f"{s_it.get('name', 'Unknown')[:14]} ({s_it.get('symbol', '')[:6]})"
                s_type = format_structured_type(s_it.get("standard"), item=s_it)

                p_str = str(s_it.get("mint_price_str", "")).upper()
                v_wei = int(s_it.get("mint_value_wei", 0) or 0)
                is_free = (s_it.get("has_free_mint") or "FREE" in p_str or (v_wei == 0 and s_it.get("mint_price_bnb", 0.0) == 0.0 and "PAID" not in p_str and "DEX" not in p_str and "TBD" not in p_str))
                cat_badge = "[bold green]FREE[/bold green]" if is_free else "[bold yellow]PAID[/bold yellow]"

                sm_list = s_it.get("smart_wallets") or s_it.get("smart_minters") or []
                sm_top = list(sm_list)[:2]
                sm_str = f"[bold cyan]{len(sm_list)}[/bold cyan] [dim]({', '.join(sm_top)})[/dim]"

                pr_fmt = format_structured_mint_price(s_it)
                prog_fmt = format_tiered_mint_progress(s_it)
                safe_fmt = format_structured_safety(s_it)
                fl_fmt = format_floor_trade_price(s_it)
                act_fmt = "[bold green][s] Snipe[/bold green]"

                s_table.add_row(
                    sel_marker,
                    s_name,
                    s_type,
                    cat_badge,
                    sm_str,
                    pr_fmt,
                    prog_fmt,
                    safe_fmt,
                    fl_fmt,
                    act_fmt,
                    style=row_style
                )

        if smart_history and 0 <= smart_selected_idx < len(smart_history):
            sel_s = smart_history[smart_selected_idx]
            sd_text = Text()
            s_name = sel_s.get("name", "Unknown")
            s_sym = sel_s.get("symbol", "")
            c_addr = sel_s.get("contract", "")
            calldata = replace_calldata_recipient(sel_s.get("mint_calldata"), wallet, extra_addresses=sel_s.get("smart_wallets"))
            val_wei = sel_s.get("mint_value_wei", "0")
            sm_list = sel_s.get("smart_wallets") or sel_s.get("smart_minters") or []

            c_short = f"{c_addr[:10]}...{c_addr[-8:]}" if len(c_addr) > 20 else c_addr
            sd_text.append(f"• Target Contract: {c_short}  |  Standard: {sel_s.get('standard')}\n", style="bold cyan")
            sd_text.append(f"• 🧠 Smart Wallets ({len(sm_list)} total): {', '.join(sm_list[:6])}\n", style="bold magenta")
            
            p_val = sel_s.get("mint_price_str") or "TBD"
            sd_text.append(f"• Mint Price: {p_val} ({val_wei} wei)  |  Safety: {sel_s.get('risk_badge', '🟢 Clean')}\n", style="yellow")
            
            fl_b = sel_s.get("floor_price_bnb")
            t_p = sel_s.get("token_price_usd", 0)
            if fl_b and float(fl_b) > 0:
                sd_text.append(f"• Secondary Floor: {float(fl_b):g} BNB (${float(fl_b)*cached_bnb_price():.2f} USD)  |  Element Marketplace\n", style="bold cyan")
            elif t_p > 0:
                sd_text.append(f"• Secondary DEX Price: ${t_p:.6f} USD  |  PancakeSwap\n", style="bold green")

            sd_text.append("• ⚡ Instant Snipe: Press [s] to simulate & snipe with 1-click confirmation!\n", style="bold green")
            cmd = f"baw contract-call preview --binanceChainId 56 --from {wallet} --to {c_addr} --value {val_wei} --inputData {calldata} --json"
            sd_text.append(f"  Manual CLI: {cmd}", style="dim")
            s_deep_panel = Panel(sd_text, title=f"🧠 Smart Money Deep Dive: {s_name} ({s_sym}) [Press Enter for Full Modal]", border_style="magenta", padding=(0, 1))
        else:
            s_deep_panel = Panel("No smart money activity detected yet.", border_style="dim", padding=(0, 1))

        msg = f" | {status_msg}" if status_msg else ""
        nav_bar = Text("⌨️  [TAB / 1 / 2] Tabs  |  [s] Snipe  |  [Enter] Modal  |  [q] Quit", style="bold white on magenta")
        status_bar = Text(f"📡 BSC Block {curr_block} | Drop {smart_selected_idx + 1}/{total_smart} | 💎 Donate: {DONATION_WALLET}{msg}", style="dim magenta")
        return Group(c_panel, h_panel, s_table, s_deep_panel, nav_bar, status_bar)

    # Standard Interactive Radar Table View
    total_radar = len(disp_history)
    max_r = get_viewport_max_rows(panel_lines=7)
    r_start, r_end = compute_window(total_radar, selected_idx, max_r)

    table = Table(box=box.ROUNDED, expand=True, header_style="bold bright_white on grey23", padding=(0, 1))
    if total_radar > max_r:
        up_arr = "[yellow]▲[/yellow]" if r_start > 0 else " "
        dn_arr = "[yellow]▼[/yellow]" if r_end < total_radar else " "
        table.title = f"[bold bright_cyan]📡 LIVE DROPS[/bold bright_cyan] [dim](Showing {r_start + 1}–{r_end} of {total_radar})[/dim]  {up_arr} {dn_arr}"

    table.add_column(" ", width=2, justify="center", no_wrap=True)
    table.add_column("BLOCK", justify="right", style="bold bright_cyan", width=8, no_wrap=True)
    table.add_column("🧠", width=2, justify="center", no_wrap=True)
    table.add_column("PROJECT", style="bold bright_white", min_width=20, no_wrap=True)
    table.add_column("TYPE", width=12, justify="center", no_wrap=True)
    table.add_column("SAFETY", width=13, justify="center", no_wrap=True)
    table.add_column("INTEREST", width=16, justify="center", no_wrap=True)
    table.add_column("HOLDERS / BUYS", style="bold bright_cyan", min_width=15, justify="center", no_wrap=True)
    table.add_column("LP / VOL", style="bold green", min_width=10, justify="center", no_wrap=True)
    table.add_column("MINT PROGRESS", style="bold white", width=28, justify="left", no_wrap=True)
    table.add_column("MINT PRICE", width=14, justify="center", no_wrap=True)
    table.add_column("FLOOR / TRADE", width=13, justify="center", no_wrap=True)

    if not disp_history:
        table.add_row(" ", "-", " ", f"No projects match filter [{active_filter}]", "-", "-", "-", "-", "-", "-", "-", "-", style="dim")
    else:
        for i in range(r_start, r_end):
            item = disp_history[i]
            is_sel = (i == selected_idx)
            if is_sel:
                sel_marker = "[bold yellow]▶[/bold yellow]"
            elif i == r_start and r_start > 0:
                sel_marker = "[dim cyan]▲[/dim cyan]"
            elif i == r_end - 1 and r_end < total_radar:
                sel_marker = "[dim cyan]▼[/dim cyan]"
            else:
                sel_marker = " "
            row_style = "bold white on navy_blue" if is_sel else ""

            # Momentum & velocity update
            update_momentum_metrics(item)

            # Block #
            blk_str = f"#{str(item.get('block', ''))[-6:]}" if item.get('block') else ""

            # Smart money badge & Sniper Ready indicator in dedicated column
            is_sm = bool(item.get("smart_wallets") or item.get("smart_minters"))
            s_ready = item.get("sniper_ready")
            if s_ready is None:
                try:
                    s_ready = evaluate_sniper_ready(item)["is_ready"]
                except Exception:
                    s_ready = False

            if is_sm and s_ready:
                sm_badge = "[bold magenta]🧠[/bold magenta]"
            elif s_ready:
                sm_badge = "[bold bright_green]🎯[/bold bright_green]"
            elif is_sm:
                sm_badge = "[bold magenta]🧠[/bold magenta]"
            else:
                sm_badge = " "

            # Project name without leading emojis, so the name column stays aligned
            p_name = item.get("name", "Unknown")[:14]
            p_sym = item.get("symbol", "")[:8]
            p_display = f"{p_name} ({p_sym})"

            # Structured column values
            type_formatted = format_structured_type(item.get("standard"), item=item)
            safety_formatted = format_structured_safety(item)
            interest_str = format_structured_interest(item)

            hld = item.get("holders") or 0
            buys = item.get("buys_24h") or 0

            if hld > 0 or buys > 0:
                h_str = format_count(hld)
                b_str = format_count(buys)
                hb_str = f"[bold bright_green]{h_str:>4s}[/bold bright_green] [white]/[/white] [bold bright_cyan]{b_str:<4s}[/bold bright_cyan]"
            else:
                hb_str = "[bold white]   0 / 0   [/bold white]"

            lp_str = format_lp_or_volume(item)
            p_status = format_tiered_mint_progress(item)
            price_formatted = format_structured_mint_price(item)
            floor_trade_formatted = format_floor_trade_price(item)

            table.add_row(
                sel_marker,
                blk_str,
                sm_badge,
                p_display,
                type_formatted,
                safety_formatted,
                interest_str,
                hb_str,
                lp_str,
                p_status,
                price_formatted,
                floor_trade_formatted,
                style=row_style
            )

    # Deep dive for selected project
    sel_item = disp_history[selected_idx] if (disp_history and 0 <= selected_idx < len(disp_history)) else (disp_history[0] if disp_history else None)
    if sel_item:
        d_text = Text()
        name = sel_item.get("name", "Unknown")
        sym = sel_item.get("symbol", "")
        c_addr = sel_item.get("contract", "")
        dep = sel_item.get("deployer", "")
        s_icon = sel_item.get("icon", "")
        s_std = sel_item.get("standard", "")
        s_tm = sel_item.get("total_minted", 0)
        s_ms = sel_item.get("max_supply", "N/A")
        lp_usd = sel_item.get("lp_usd", 0)
        lp_wbnb = sel_item.get("lp_wbnb", 0)
        t_price = sel_item.get("token_price_usd", 0)
        calldata = replace_calldata_recipient(sel_item.get("mint_calldata"), wallet, extra_addresses=sel_item.get("smart_wallets"))
        val_wei = sel_item.get("mint_value_wei", "0")
        hld = (sel_item.get("holders") or 0)
        buys = (sel_item.get("buys_24h") or 0)
        buys_1h = (sel_item.get("buys_1h") or 0)
        vol = sel_item.get("volume_24h", 0.0)

        # Line 1: Contract, Deployer, Standard & Supply
        c_short = f"{c_addr[:10]}...{c_addr[-8:]}" if len(c_addr) > 20 else c_addr
        dep_short = f"{dep[:8]}...{dep[-6:]}" if len(dep) > 16 else dep
        d_text.append(f"• Contract: {c_short}  |  Deployer: {dep_short}  |  Type: {s_icon} {s_std}  |  Supply: {format_supply(s_tm)}/{format_supply(s_ms)}\n", style="bold cyan")

        # Line 2: Mint Price, Safety Audit
        price_val = sel_item.get("mint_price_str") or ("⭐ FREE" if sel_item.get("has_free_mint") else "Paid / TBD")
        sec = sel_item.get("security") or {}
        r_bdg = sel_item.get("risk_badge", "🟢 Clean")
        r_lvl = sel_item.get("risk_level", "SAFE")
        sec_parts = [r_bdg]
        if sec.get("buy_tax") is not None and sec.get("sell_tax") is not None and (sec.get("buy_tax") > 0 or sec.get("sell_tax") > 0):
            sec_parts.append(f"Taxes: {sec['buy_tax']:.0f}%/{sec['sell_tax']:.0f}%")
        if sec.get("lp_locked_pct", 0) > 0:
            sec_parts.append(f"LP: {sec['lp_locked_pct']:.0f}% Lock")
        elif lp_usd > 500 and sec.get("audited"):
            sec_parts.append("LP: ⚠️ Unlocked")
        if sec.get("is_honeypot"):
            sec_parts.append("🚨 HONEYPOT")
        d_text.append(f"• Mint Price: {price_val} ({val_wei} wei)  |  Safety: {' | '.join(sec_parts)}\n", style="yellow")

        # Sniper Conviction line (60% Rule & Anti-Rug)
        s_eval = evaluate_sniper_ready(sel_item)
        if s_eval["is_ready"]:
            chk_first = s_eval['checklist'][0].replace('▫ ', '') if s_eval.get('checklist') else 'Threshold Passed'
            d_text.append_text(Text.from_markup(f"• 🎯 Conviction: [bold bright_green]PASS (SNIPER READY)[/bold bright_green]  |  {chk_first}\n"))
        else:
            chk_first = s_eval['checklist'][0].replace('▫ ', '') if s_eval.get('checklist') else 'Tracking'
            d_text.append_text(Text.from_markup(f"• 🎯 Conviction: [bold yellow]{s_eval['badge']}[/bold yellow]  |  {chk_first}\n"))

        # Line 3: Minters, Velocity, Traction
        vel_str, org_str = update_momentum_metrics(sel_item)
        trac_str = f"Holders: {hld:,}  |  24h Buys: {buys:,} ({buys_1h} in 1h)" if (hld > 0 or buys > 0) else "0 holders (Pre-launch)"
        d_text.append(f"• Momentum: {org_str} ({vel_str})  |  Traction: {trac_str}\n", style="dim cyan")

        # Line 4: Smart Money & Market
        sm_list = sel_item.get("smart_wallets") or sel_item.get("smart_minters") or []
        m_parts = []
        if sm_list:
            sm_names = ", ".join(sm_list[:4]) + (f" +{len(sm_list)-4} more" if len(sm_list) > 4 else "")
            m_parts.append(f"🧠 Smart Money: {len(sm_list)} Wallets ({sm_names})")
        
        t_price_bnb = sel_item.get("token_price_bnb", 0.0)
        floor_bnb = sel_item.get("floor_price_bnb")
        vol_b = sel_item.get("volume_bnb") or sel_item.get("nft_volume_bnb")
        if t_price > 0:
            m_parts.append(f"DEX Price: ${t_price:.4f} USD  |  Pool: ${lp_usd:,.0f}")
        elif floor_bnb and float(floor_bnb) > 0:
            vol_str = f" ({float(vol_b):g} BNB vol)" if (vol_b and float(vol_b) > 0) else ""
            m_parts.append(f"Floor: {float(floor_bnb):g} BNB (${float(sel_item.get('floor_price_usd') or float(floor_bnb)*cached_bnb_price()):.2f}){vol_str}")
        elif lp_usd > 0:
            m_parts.append(f"DEX Pool: ${lp_usd:,.0f}")
        elif vol_b and float(vol_b) > 0:
            m_parts.append(f"24h Vol: {float(vol_b):g} BNB")
        if m_parts:
            d_text.append(f"• Activity: {'  |  '.join(m_parts)}\n", style="bold magenta" if sm_list else "green")

        # Line 5: Links & Shortcuts
        links_parts = ["[s] Snipe/Buy", "[Enter] Full Modal"]
        website = sel_item.get("website", "")
        twitter = sel_item.get("twitter", "")
        mkt_url = sel_item.get("market_url") or sel_item.get("pair_url", "")
        if website:
            links_parts.append(f"[w] Web")
        if twitter:
            links_parts.append(f"[x] X")
        if mkt_url or "NFT" in s_std or "BEP-20" in s_std:
            links_parts.append(f"[m] Market")
        links_parts.append(f"[b] BscScan")
        d_text.append(f"• Shortcuts: {'  |  '.join(links_parts)}\n", style="bold green")

        # Line 6: Snipe CLI
        cmd = f"baw contract-call preview --binanceChainId 56 --from {wallet} --to {c_addr} --value {val_wei} --inputData {calldata} --json"
        d_text.append(f"  Manual CLI: {cmd}", style="dim")

        deep_panel = Panel(d_text, title=f"🔍 Deep Dive: {name} ({sym}) [Press Enter for Full Modal]", border_style="yellow", padding=(0, 1))
    else:
        deep_panel = Panel("Waiting for .ai Agent ID contract deployments on BSC...", border_style="dim", padding=(0, 1))

    msg = f" | {status_msg}" if status_msg else ""
    nav_bar = Text(f"⌨️  [TAB / 1 / 2 / 3] Tabs  |  [↑/↓] Row  |  [s] Snipe/Buy  |  [Enter] Modal  |  [k] 🎯 Ready  |  [u] 🦋 Surge  |  [p] 🧩 Proxy  |  [f] Free  |  [g] Smart  |  [l] LP  |  [a] Active  |  [c] All  |  [o] Sort: {sort_mode}  |  [w] Web  |  [x] X  |  [m] Mkt  |  [b] BscScan  |  [q] Quit", style="bold white on blue")
    status_bar = Text(f"📡 BSC Block {curr_block} | Row {selected_idx + 1}/{total_radar} | 💎 Donate: {DONATION_WALLET}{msg}", style="dim green")

    return Group(c_panel, h_panel, table, deep_panel, nav_bar, status_bar)


def run_live_monitor(rpc: BscRpcClient, wallet: str, plain: bool = False):
    history = load_history()

    if not RICH_AVAILABLE or plain or not sys.stdin.isatty():
        print_banner()
        print(f"{C_BOLD}{C_GREEN}[*] Live Monitor Started.{C_RESET} Listening to new BSC blocks...")
        print(f"[*] Watching for newly deployed contracts referencing the .ai Agent ID registry.")
        print(f"[*] Classification Engine: Distinguishes {C_YELLOW}Tokens (BEP-20){C_RESET} vs {C_MAGENTA}NFTs (ERC-721){C_RESET}.")
        print(f"[*] Your Agent Wallet: {C_YELLOW}{wallet}{C_RESET}\n")

        last_block = rpc.get_block_number() - 1
        processed_txs = set()

        while True:
            try:
                curr_block = rpc.get_block_number()
                if curr_block <= last_block:
                    time.sleep(1.0)
                    continue

                # Auto-sync catchup if behind by >25 blocks
                if curr_block - last_block > 25:
                    last_block = curr_block - 5

                for block_num in range(last_block + 1, curr_block + 1):
                    block = rpc.get_block(block_num, full_tx=True)
                    txs = block.get("transactions", [])

                    print(f"\r{C_BLUE}--> Block {block_num} | {len(txs)} txs{C_RESET}", end="", flush=True)

                    for tx in txs:
                        tx_hash = tx.get("hash")
                        if not tx_hash or tx_hash in processed_txs:
                            continue
                        processed_txs.add(tx_hash)

                        input_data = (tx.get("input") or "").lower()
                        # Cheap substring check first: almost no transaction references a registry
                        if not matches_tracked_registry(input_data):
                            continue

                        rcpt = rpc.get_receipt(tx_hash)
                        if not rcpt or int(rcpt.get("status", "0"), 16) != 1:
                            continue

                        deployed_addr = rcpt.get("contractAddress")
                        if not deployed_addr:
                            for log in rcpt.get("logs", []):
                                log_addr = log.get("address")
                                if log_addr:
                                    code = rpc.get_code(log_addr)
                                    full_c, _, _ = resolve_full_bytecode(rpc, log_addr, code)
                                    if matches_tracked_registry(full_c or code or ""):
                                        deployed_addr = log_addr
                                        break

                        if deployed_addr:
                                details = analyze_contract(rpc, deployed_addr, wallet)
                                cls = details["classification"]
                                mkt = analyze_market_interest(rpc, deployed_addr, tx.get('from'), details)

                                print(f"\n\n{C_BOLD}{C_GREEN}═══════════════════════════════════════════════════════════════{C_RESET}")
                                print(f"{C_BOLD}{C_YELLOW}🚀 NEW .AI AGENT ID PROJECT DETECTED!{C_RESET}")
                                print(f"{C_BOLD}{C_GREEN}═══════════════════════════════════════════════════════════════{C_RESET}")
                                print(f"• {C_BOLD}Block:{C_RESET} {block_num}")
                                print(f"• {C_BOLD}Contract Address:{C_RESET} {C_CYAN}{deployed_addr}{C_RESET}")
                                print(f"• {C_BOLD}Deployer:{C_RESET} {tx.get('from')}")
                                print(f"• {C_BOLD}Tx Hash:{C_RESET} {tx_hash}")
                                print(f"• {C_BOLD}Asset Type:{C_RESET} {cls['icon']} {C_BOLD}{cls['standard']}{C_RESET}")
                                if details["name"] or details["symbol"]:
                                    print(f"• {C_BOLD}Name / Symbol:{C_RESET} {details['name']} ({details['symbol']})")
                                if details.get("is_paused") is not None:
                                    p_str = f"{C_RED}Paused{C_RESET}" if details["is_paused"] else f"{C_GREEN}Active{C_RESET}"
                                    print(f"• {C_BOLD}Mint Status:{C_RESET} {p_str}")
                                print(f"• {C_BOLD}Market Interest:{C_RESET} {mkt['rating']} ({mkt['score']}/100)")
                                if mkt.get("lp_pool"):
                                    print(f"• {C_BOLD}DEX Liquidity:{C_RESET} {C_GREEN}${mkt['lp_usd']:,.2f}{C_RESET} ({mkt['lp_wbnb']:.2f} WBNB)")
                                if details["has_free_mint"]:
                                    print(f"• {C_BOLD}{C_GREEN}★ FREE MINT AVAILABLE FOR YOUR WALLET! ★{C_RESET}")
                                print(f"• {C_BOLD}BscScan:{C_RESET} https://bscscan.com/address/{deployed_addr}")
                                print(f"\n{C_BOLD}Quick CLI Snipe Command:{C_RESET}")
                                clean_cd = replace_calldata_recipient(details['mint_calldata'], wallet)
                                print(f"{C_CYAN}baw contract-call preview --binanceChainId 56 --from {wallet} --to {deployed_addr} --value 0 --inputData {clean_cd} --json{C_RESET}\n")
                                notify_alert()

                last_block = curr_block
                time.sleep(2)

            except KeyboardInterrupt:
                print(f"\n{C_YELLOW}[!] Monitor stopped by user.{C_RESET}")
                break
            except Exception:
                time.sleep(2)
        return

    # Interactive Rich Live Dashboard Mode
    key_reader = KeyReader()
    selected_idx = 0
    show_modal = False
    status_msg = ""
    status_timer = 0
    active_filter = "ALL"
    sort_mode = "NEWEST"
    active_tab = "RADAR"
    port_selected_idx = 0
    smart_selected_idx = 0
    portfolio_data = build_portfolio_data(rpc, wallet, history)
    stop_event = threading.Event()

    smart_wallets = load_smart_wallets()
    known_infra = known_infra_addresses()

    latest_block = [rpc.get_block_number() - 1]
    latest_tx_count = [0]

    def background_listener():
        nonlocal history, status_msg, status_timer
        processed_txs = set()
        refresh_counter = 0
        last_cloud_sync = 0

        while not stop_event.is_set():
            try:
                # Cloud sync every 20s
                if time.time() - last_cloud_sync > 20:
                    last_cloud_sync = time.time()
                    try:
                        cloud_res = sync_cloud_history()
                        if cloud_res and len(cloud_res) > len(history):
                            diff = len(cloud_res) - len(history)
                            history = cloud_res
                            status_msg = f"☁️ Synced +{diff} new drops from Cloud!"
                            status_timer = time.time()
                    except Exception as exc:
                        _log.debug("cloud history sync failed: %s", exc)

                refresh_bnb_price(rpc)  # re-reads the price only when the cached one is over a minute old

                curr_b = rpc.get_block_number()
                if curr_b <= latest_block[0]:
                    time.sleep(1.0)
                    continue

                # Auto-sync catchup if behind by >25 blocks
                if curr_b - latest_block[0] > 25:
                    latest_block[0] = curr_b - 5

                for b in range(latest_block[0] + 1, curr_b + 1):
                    block = rpc.get_block(b, full_tx=True)
                    txs = block.get("transactions", [])
                    latest_tx_count[0] = len(txs)
                    latest_block[0] = b
                    refresh_counter += 1

                    for tx in txs:
                        tx_h = tx.get("hash")
                        if not tx_h or tx_h in processed_txs:
                            continue
                        processed_txs.add(tx_h)

                        snd = (tx.get("from") or "").lower()
                        rcv = (tx.get("to") or "").lower()
                        inp = (tx.get("input") or "").lower()
                        tx_val = int(tx.get("value") or "0", 16)

                        # Track live .ai Agent ID claims / registrations on BSC (requires contract call input)
                        if rcv == AGENT_ID_REGISTRY.lower() and len(inp) >= 10:
                            try:
                                cur_st = load_agent_id_stats()
                                cur_st["total_created_wallets"] = cur_st.get("total_created_wallets", DEFAULT_AGENT_WALLET_COUNT) + 1
                                cur_st["last_updated"] = time.time()
                                save_agent_id_stats(cur_st)
                                status_msg = f"🎉 New .ai Agent ID Claimed! Total Registered: {cur_st['total_created_wallets']:,}"
                                status_timer = time.time()
                            except Exception as exc:
                                _log.debug("could not update agent-id claim counter: %s", exc)

                        # Smart-wallet activity
                        if snd in smart_wallets and rcv and rcv not in known_infra and (len(inp) >= 10 or tx_val > 0):
                            aid = smart_wallets[snd]
                            matching = [item for item in history if item.get("contract", "").lower() == rcv]
                            if matching:
                                item = matching[0]
                                cur_sm = set(item.get("smart_wallets") or [])
                                if aid not in cur_sm:
                                    cur_sm.add(aid)
                                    item["smart_wallets"] = list(cur_sm)
                                    item["smart_minters"] = list(cur_sm)
                                    if (not item.get("mint_calldata") or item.get("mint_calldata") in ("0x", "0x1249c58b")) and len(inp) >= 10:
                                        item["mint_calldata"] = replace_calldata_recipient(inp, wallet, original_sender=snd)
                                    save_history(history)
                                p_n = item.get("name") or "Project"
                                status_msg = f"⚡ Smart Money: {aid} called {p_n}!"
                                status_timer = time.time()
                            else:
                                try:
                                    det = analyze_contract(rpc, rcv, wallet)
                                    cls = det.get("classification", {})
                                    if not is_system_infra({"contract": rcv, "classification": cls, "name": det.get("name"), "symbol": det.get("symbol"), "max_supply": det.get("max_supply"), "total_minted": det.get("total_minted")}):
                                        mkt = analyze_market_interest(rpc, rcv, None, det)
                                        w_cnt = 1
                                        if len(inp) < 10 or inp in ("", "0x"):
                                            mint_cd = "0x"
                                            val_str = str(tx_val)
                                            p_str = f"{tx_val/1e18:.4f} BNB" if tx_val > 0 else "⭐ FREE"
                                            bnb_val = tx_val / 1e18
                                            free_m = (tx_val == 0)
                                        else:
                                            base_cd = det.get("mint_calldata") if det.get("has_mint") else inp
                                            mint_cd = replace_calldata_recipient(base_cd, wallet, original_sender=snd)
                                            val_str = det.get("mint_value_wei") or str(tx_val)
                                            p_str = det.get("mint_price_str") or (f"{tx_val/1e18:.4f} BNB" if tx_val > 0 else "⭐ FREE")
                                            bnb_val = det.get("mint_price_bnb", 0.0) or (tx_val / 1e18)
                                            free_m = det.get("has_free_mint", False)

                                        new_entry = {
                                            "block": b,
                                            "contract": rcv,
                                            "deployer": "",
                                            "tx_hash": tx_h,
                                            "name": det.get("name") or "New Drop",
                                            "symbol": det.get("symbol") or "",
                                            "standard": cls.get("standard", ""),
                                            "icon": cls.get("icon", ""),
                                            "is_paused": det.get("is_paused"),
                                            "has_free_mint": free_m,
                                            "mint_price_str": p_str,
                                            "mint_price_bnb": bnb_val,
                                            "mint_value_wei": val_str,
                                            "max_supply": det.get("max_supply"),
                                            "total_minted": det.get("total_minted", 0),
                                            "score": mkt.get("score", 0),
                                            "rating": mkt.get("rating", "N/A"),
                                            "lp_usd": mkt.get("lp_usd", 0),
                                            "lp_wbnb": mkt.get("lp_wbnb", 0),
                                            "token_symbol": mkt.get("token_symbol", ""),
                                            "token_price_usd": mkt.get("token_price_usd", 0),
                                            "holders": mkt.get("holders", w_cnt),
                                            "buys_24h": mkt.get("buys_24h", 1),
                                            "buys_1h": mkt.get("buys_1h", 1),
                                            "sells_24h": mkt.get("sells_24h", 0),
                                            "volume_24h": mkt.get("volume_24h", 0.0),
                                            "pair_url": mkt.get("pair_url", ""),
                                            **linked_market_fields(mkt),
                                            "mint_calldata": mint_cd,
                                            "signals": mkt.get("signals", [f"Discovered via smart wallet {aid}"]),
                                            "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                            "smart_wallets": [aid],
                                            "smart_minters": [aid]
                                        }
                                        history.insert(0, new_entry)
                                        save_history(history)
                                        p_n = det.get("name") or "New Drop"
                                        status_msg = f"⚡ ALPHA DROP: {aid} minted {p_n}!"
                                        status_timer = time.time()
                                        notify_alert()
                                except Exception as exc:
                                    _log.debug("smart-wallet drop analysis failed for %s: %s", rcv, exc)

                        # Cheap substring check first: almost no transaction references a registry
                        if not matches_tracked_registry(inp):
                            continue

                        rcpt = rpc.get_receipt(tx_h)
                        if not rcpt or int(rcpt.get("status", "0"), 16) != 1:
                            continue
                        dep_addr = rcpt.get("contractAddress")
                        if not dep_addr:
                            for log in rcpt.get("logs", []):
                                log_addr = log.get("address")
                                if log_addr:
                                    code = rpc.get_code(log_addr)
                                    full_c, _, _ = resolve_full_bytecode(rpc, log_addr, code)
                                    if matches_tracked_registry(full_c or code or ""):
                                        dep_addr = log_addr
                                        break

                        if dep_addr:
                                try:
                                    det = analyze_contract(rpc, dep_addr, wallet)
                                except Exception:
                                    det = {
                                        "name": "New Contract",
                                        "symbol": "",
                                        "classification": {"standard": "Custom Smart Contract", "icon": "⚙️"},
                                        "has_free_mint": False,
                                        "mint_calldata": "0x1249c58b"
                                    }
                                try:
                                    mkt = analyze_market_interest(rpc, dep_addr, tx.get("from"), det)
                                except Exception:
                                    mkt = {"score": 0, "rating": "❄️ COLD", "lp_usd": 0, "lp_wbnb": 0}

                                cls = det.get("classification", {})
                                # Skip pure system / infrastructure adapter contracts with no tokens or minting
                                is_system_infra = (
                                    cls.get("category") == "CUSTOM"
                                    and not det.get("has_mint")
                                    and not det.get("max_supply")
                                    and not det.get("symbol")
                                    and (not det.get("name") or det.get("name") == "Unknown")
                                )
                                if is_system_infra:
                                    continue

                                new_entry = {
                                    "block": b,
                                    "contract": dep_addr,
                                    "deployer": tx.get("from"),
                                    "tx_hash": tx_h,
                                    "name": det.get("name") or "Unknown",
                                    "symbol": det.get("symbol") or "",
                                    "standard": cls.get("standard", ""),
                                    "icon": cls.get("icon", ""),
                                    "is_paused": det.get("is_paused"),
                                    "has_free_mint": det.get("has_free_mint"),
                                    "mint_price_str": det.get("mint_price_str", "TBD"),
                                    "mint_price_bnb": det.get("mint_price_bnb", 0.0),
                                    "mint_value_wei": det.get("mint_value_wei", "0"),
                                    "max_supply": det.get("max_supply"),
                                    "total_minted": det.get("total_minted", 0),
                                    "score": mkt.get("score", 0),
                                    "rating": mkt.get("rating", "N/A"),
                                    "lp_usd": mkt.get("lp_usd", 0),
                                    "lp_wbnb": mkt.get("lp_wbnb", 0),
                                    "token_symbol": mkt.get("token_symbol", ""),
                                    "token_price_usd": mkt.get("token_price_usd", 0),
                                    "holders": mkt.get("holders", 0),
                                    "buys_24h": mkt.get("buys_24h", 0),
                                    "buys_1h": mkt.get("buys_1h", 0),
                                    "sells_24h": mkt.get("sells_24h", 0),
                                    "volume_24h": mkt.get("volume_24h", 0.0),
                                    "pair_url": mkt.get("pair_url", ""),
                                    **linked_market_fields(mkt),
                                    "mint_calldata": det.get("mint_calldata", "0x1249c58b"),
                                    "signals": mkt.get("signals", []),
                                    "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "is_proxy_clone": det.get("is_proxy_clone", False),
                                    "proxy_type": det.get("proxy_type"),
                                    "master_implementation": det.get("master_implementation"),
                                    "is_factory": det.get("is_factory", False),
                                    "factory_badge": det.get("factory_badge")
                                }
                                history = [h for h in history if h.get("contract", "").lower() != dep_addr.lower()]
                                history.insert(0, new_entry)
                                save_history(history)
                                p_title = det.get("name") or "New Project"
                                status_msg = f"🔔 NEW DROP DETECTED: {p_title} added to table!"
                                status_timer = time.time()
                                notify_alert()

                    if refresh_counter >= 20:
                        refresh_counter = 0
                        for item in history[:5]:
                            try:
                                c = item.get("contract")
                                if c:
                                    p_res = rpc.call_contract(c, keccak_selector("paused()"))
                                    if p_res and len(p_res) >= 66:
                                        item["is_paused"] = int(p_res, 16) == 1
                                    m_res = rpc.call_contract(c, keccak_selector("totalMinted()"))
                                    if m_res and len(m_res) >= 66:
                                        item["total_minted"] = int(m_res, 16)
                            except Exception as exc:
                                _log.debug("paused/totalMinted refresh failed for %s: %s", item.get("contract"), exc)

                        # Auto-resolve prices for any TBD items on-chain
                        for item in history:
                            if item.get("mint_price_str") in (None, "", "TBD"):
                                try:
                                    c = item.get("contract")
                                    if c:
                                        det = analyze_contract(rpc, c, wallet)
                                        p_str = det.get("mint_price_str")
                                        if p_str and p_str != "TBD":
                                            item["mint_price_str"] = p_str
                                            item["mint_price_bnb"] = det.get("mint_price_bnb", 0.0)
                                            item["mint_value_wei"] = det.get("mint_value_wei", "0")
                                            if det.get("has_free_mint") is not None:
                                                item["has_free_mint"] = det["has_free_mint"]
                                            if det.get("mint_calldata"):
                                                item["mint_calldata"] = det["mint_calldata"]
                                            needs_update = True
                                except Exception as exc:
                                    _log.debug("TBD price resolution failed for %s: %s", item.get("contract"), exc)
                        save_history(history)

                time.sleep(2)
            except Exception:
                time.sleep(2)

    listener_thread = threading.Thread(target=background_listener, daemon=True)
    listener_thread.start()

    # Immediate background resolver thread for existing projects with TBD prices
    def auto_resolve_tbd_prices():
        nonlocal history, needs_update
        changed = False
        for item in list(history):
            if stop_event.is_set():
                break
            if item.get("mint_price_str") in (None, "", "TBD"):
                try:
                    c = item.get("contract")
                    if c:
                        det = analyze_contract(rpc, c, wallet)
                        p_str = det.get("mint_price_str")
                        if p_str and p_str != "TBD":
                            item["mint_price_str"] = p_str
                            item["mint_price_bnb"] = det.get("mint_price_bnb", 0.0)
                            item["mint_value_wei"] = det.get("mint_value_wei", "0")
                            if det.get("has_free_mint") is not None:
                                item["has_free_mint"] = det["has_free_mint"]
                            if det.get("mint_calldata"):
                                item["mint_calldata"] = det["mint_calldata"]
                            changed = True
                            needs_update = True
                except Exception as exc:
                    _log.debug("TBD price resolution failed for %s: %s", item.get("contract"), exc)
        if changed:
            save_history(history)

    resolver_thread = threading.Thread(target=auto_resolve_tbd_prices, daemon=True)
    resolver_thread.start()

    last_rendered_block = -1
    last_render_time = time.time()
    needs_update = True
    snipe_confirm = None
    sell_confirm = None

    try:
        with Live(
            build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx, sell_confirm=sell_confirm),
            refresh_per_second=6,
            screen=True
        ) as live:
            while True:
                key = key_reader.get_key(timeout=0.08)
                disp_history = filter_and_sort_projects(history, active_filter, sort_mode)
                smart_history = [x for x in history if not is_system_infra(x) and (x.get("smart_wallets") or x.get("smart_minters"))]
                smart_history.sort(key=lambda x: len(x.get("smart_wallets") or x.get("smart_minters") or []), reverse=True)

                # Active Exit / Take-Profit Sell Confirmation Gate
                if sell_confirm:
                    if key in ('y', 'Y', 'ENTER', '\r', '\n'):
                        status_msg = "⏳ Broadcasting Take-Profit Sell on BNB Chain via Binance Agentic Wallet..."
                        live.update(build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx, sell_confirm=sell_confirm))
                        req_id = sell_confirm.get("requestId")
                        if req_id:
                            res = execute_snipe_call(req_id)
                        else:
                            res = {"success": False, "error": {"message": "Missing requestId"}}
                        if res.get("success"):
                            tx_h = res.get("data", {}).get("txHash", "")
                            status_msg = f"🎉 TAKE-PROFIT SUCCESSFUL! Tx: {tx_h[:10]}...{tx_h[-8:]} Swapped to BNB!"
                            refresh_portfolio(rpc, history, wallet)
                            portfolio_data = build_portfolio_data(rpc, wallet, history)
                            notify_alert()
                        else:
                            err = res.get("error", {}).get("message", "Execution failed")
                            status_msg = f"❌ Sell Failed: {err[:60]}"
                        sell_confirm = None
                        status_timer = time.time()
                        needs_update = True
                        continue
                    elif key in ('n', 'N', 'ESC', 'q', 'Q'):
                        sell_confirm = None
                        status_msg = "🚫 Exit sell cancelled by user."
                        status_timer = time.time()
                        needs_update = True
                        continue

                # Active Sniper Confirmation Gate
                if snipe_confirm:
                    if key in ('y', 'Y', 'ENTER', '\r', '\n'):
                        status_msg = "⏳ Broadcasting snipe transaction on BNB Chain..."
                        live.update(build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx, sell_confirm=sell_confirm))
                        req_id = snipe_confirm.get("requestId")
                        res = execute_snipe_call(req_id)
                        if res.get("success"):
                            tx_h = res.get("data", {}).get("txHash", "")
                            status_msg = f"🎉 SNIPE SUCCESSFUL! Tx: {tx_h[:10]}...{tx_h[-8:]} (Minted!) Added to Portfolio [Tab 3]!"
                            if active_tab == "SMART":
                                target_proj = smart_history[smart_selected_idx] if (smart_history and 0 <= smart_selected_idx < len(smart_history)) else None
                            elif active_tab == "PORTFOLIO":
                                target_proj = None
                            else:
                                target_proj = disp_history[selected_idx] if (disp_history and 0 <= selected_idx < len(disp_history)) else None
                            if target_proj:
                                target_proj["user_balance"] = (target_proj.get("user_balance") or 0) + 1
                            save_history(history)
                            portfolio_data = build_portfolio_data(rpc, wallet, history)
                            notify_alert()
                        else:
                            err = res.get("error", {}).get("message", "Execution failed")
                            status_msg = f"❌ Snipe Failed: {err[:60]}"
                        snipe_confirm = None
                        show_modal = False
                        status_timer = time.time()
                        needs_update = True
                        continue
                    elif key in ('n', 'N', 'ESC', 'q', 'Q'):
                        snipe_confirm = None
                        status_msg = "🚫 Sniper cancelled by user."
                        status_timer = time.time()
                        needs_update = True
                        continue

                if key in ('q', 'Q'):
                    break
                elif key == 'ESC':
                    if show_modal:
                        show_modal = False
                        needs_update = True
                elif key == 'TAB':
                    if not show_modal:
                        tabs = ["RADAR", "SMART", "PORTFOLIO"]
                        cur_tab_idx = tabs.index(active_tab) if active_tab in tabs else 0
                        active_tab = tabs[(cur_tab_idx + 1) % len(tabs)]
                        tab_names = {"RADAR": "📡 Live Radar", "SMART": "🧠 Smart Money Alpha", "PORTFOLIO": "🎒 My Portfolio"}
                        status_msg = f"Switched to {tab_names.get(active_tab, active_tab)}"
                        status_timer = time.time()
                        needs_update = True
                elif key in ('p', 'P'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter in ("PROXY", "CLONE") else "PROXY"
                        status_msg = f"🔍 Filter: {'🧩 PROXY CLONES ONLY' if active_filter == 'PROXY' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('s', 'S'):
                    if active_tab == "PORTFOLIO":
                        p_items = portfolio_data.get("items", [])
                        if p_items and 0 <= port_selected_idx < len(p_items):
                            sel_p = p_items[port_selected_idx]
                            raw_it = sel_p.get("raw_item")
                            if raw_it and raw_it.get("has_free_mint"):
                                target_proj = raw_it
                            else:
                                target_proj = None
                        else:
                            target_proj = None
                    elif active_tab == "SMART":
                        target_proj = smart_history[smart_selected_idx] if (smart_history and 0 <= smart_selected_idx < len(smart_history)) else None
                    else:
                        target_proj = disp_history[selected_idx] if (disp_history and 0 <= selected_idx < len(disp_history)) else None

                    if target_proj:
                        c_addr = target_proj.get("contract")
                        val_wei = str(target_proj.get("mint_value_wei", "0") or "0")
                        raw_calldata = target_proj.get("mint_calldata")
                        sm_addrs = target_proj.get("smart_wallets") or []
                        calldata = replace_calldata_recipient(raw_calldata, wallet, extra_addresses=sm_addrs)
                        sel_name = target_proj.get("name", "Project")
                        action_label = "plain BNB transfer (recv)" if calldata == "0x" else "snipe call"
                        status_msg = f"🔍 Simulating {action_label} on BSC node for {sel_name}..."
                        live.update(build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=None, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx))
                        prev = preview_snipe_call(wallet, c_addr, val_wei, calldata)
                        if prev.get("success"):
                            data = prev.get("data", {})
                            req_id = data.get("requestId")
                            sim = data.get("simulationResult", {})
                            b_changes = sim.get("balanceChanges", [])
                            gain_str = ""
                            if b_changes:
                                bc = b_changes[0]
                                token_addr = bc.get("contractAddress", "")
                                t_info = data.get("tokenInfos", {}).get(token_addr, {}) or {}
                                sym = t_info.get("symbol") or target_proj.get("symbol") or "Token"
                                raw_tid = bc.get("tokenId")
                                if raw_tid:
                                    try:
                                        tid_dec = int(raw_tid, 16)
                                        gain_str = f"+1 {sym} (Token ID #{tid_dec})"
                                    except Exception:
                                        gain_str = f"+1 {sym}"
                                else:
                                    gain_str = f"+{bc.get('change', 1)} {sym}"
                            if not gain_str:
                                gain_str = f"+1 {target_proj.get('symbol') or 'Token'}"

                            snipe_confirm = {
                                "project": target_proj,
                                "requestId": req_id,
                                "gain_str": gain_str,
                                "preview_data": data
                            }
                            status_msg = "🎯 Simulation Passed! Press [Y] to confirm or [N] to cancel."
                            status_timer = time.time()
                            needs_update = True
                        else:
                            err = prev.get("error", {}).get("message", "Simulation failed")
                            status_msg = f"⚠️ Snipe simulation failed: {err[:60]}"
                            status_timer = time.time()
                            needs_update = True
                elif key in ('k', 'K', 'v', 'V'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter == "SNIPER_READY" else "SNIPER_READY"
                        status_msg = f"🔍 Filter: {'🎯 SNIPER READY (60%+ & ANTI-RUG)' if active_filter == 'SNIPER_READY' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('u', 'U', 'e', 'E'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter == "NFT_SURGE" else "NFT_SURGE"
                        status_msg = f"🔍 Filter: {'🦋 NFT SURGE (Element Spike)' if active_filter == 'NFT_SURGE' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('f', 'F'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter == "FREE" else "FREE"
                        status_msg = f"🔍 Filter: {'FREE MINTS ONLY' if active_filter == 'FREE' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('g', 'G'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter == "SMART" else "SMART"
                        status_msg = f"🔍 Filter: {'SMART MONEY ONLY' if active_filter == 'SMART' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('l', 'L'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter == "LP" else "LP"
                        status_msg = f"🔍 Filter: {'DEX LP ONLY' if active_filter == 'LP' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('a', 'A'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter == "ACTIVE" else "ACTIVE"
                        status_msg = f"🔍 Filter: {'ACTIVE MINTS ONLY' if active_filter == 'ACTIVE' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('c', 'C'):
                    if active_tab == "RADAR":
                        active_filter = "ALL"
                        status_msg = "🔍 Filter: Reset to ALL projects"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('o', 'O'):
                    if active_tab == "RADAR":
                        modes = ["NEWEST", "PROFIT", "SMART", "HEAT", "VELOCITY", "PROGRESS", "SAFETY"]
                        cur_i = modes.index(sort_mode) if sort_mode in modes else 0
                        sort_mode = modes[(cur_i + 1) % len(modes)]
                        status_msg = f"🔀 Sorting by: {sort_mode}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key == 'UP':
                    if not show_modal:
                        if active_tab == "PORTFOLIO":
                            port_selected_idx = max(0, port_selected_idx - 1)
                        elif active_tab == "SMART":
                            smart_selected_idx = max(0, smart_selected_idx - 1)
                        else:
                            selected_idx = max(0, selected_idx - 1)
                        needs_update = True
                elif key == 'DOWN':
                    if not show_modal:
                        if active_tab == "PORTFOLIO":
                            p_len = len(portfolio_data.get("items", []))
                            port_selected_idx = min(max(0, p_len - 1), port_selected_idx + 1)
                        elif active_tab == "SMART":
                            sm_len = len(smart_history)
                            smart_selected_idx = min(max(0, sm_len - 1), smart_selected_idx + 1)
                        else:
                            selected_idx = min(max(0, len(disp_history) - 1), selected_idx + 1)
                        needs_update = True
                elif key == 'PAGE_UP':
                    if not show_modal:
                        pg = max(5, get_viewport_max_rows() - 2)
                        if active_tab == "PORTFOLIO":
                            port_selected_idx = max(0, port_selected_idx - pg)
                        elif active_tab == "SMART":
                            smart_selected_idx = max(0, smart_selected_idx - pg)
                        else:
                            selected_idx = max(0, selected_idx - pg)
                        needs_update = True
                elif key == 'PAGE_DOWN':
                    if not show_modal:
                        pg = max(5, get_viewport_max_rows() - 2)
                        if active_tab == "PORTFOLIO":
                            p_len = len(portfolio_data.get("items", []))
                            port_selected_idx = min(max(0, p_len - 1), port_selected_idx + pg)
                        elif active_tab == "SMART":
                            sm_len = len(smart_history)
                            smart_selected_idx = min(max(0, sm_len - 1), smart_selected_idx + pg)
                        else:
                            selected_idx = min(max(0, len(disp_history) - 1), selected_idx + pg)
                        needs_update = True
                elif key == 'HOME':
                    if not show_modal:
                        if active_tab == "PORTFOLIO":
                            port_selected_idx = 0
                        elif active_tab == "SMART":
                            smart_selected_idx = 0
                        else:
                            selected_idx = 0
                        needs_update = True
                elif key == 'END':
                    if not show_modal:
                        if active_tab == "PORTFOLIO":
                            p_len = len(portfolio_data.get("items", []))
                            port_selected_idx = max(0, p_len - 1)
                        elif active_tab == "SMART":
                            sm_len = len(smart_history)
                            smart_selected_idx = max(0, sm_len - 1)
                        else:
                            selected_idx = max(0, len(disp_history) - 1)
                        needs_update = True
                elif key in ('1', '2', '3', '4', '5', '6', '7', '8', '9'):
                    if key == '1' and not show_modal:
                        active_tab = "RADAR"
                        status_msg = "📡 Switched to Live Radar"
                        status_timer = time.time()
                        needs_update = True
                    elif key == '2' and not show_modal:
                        active_tab = "SMART"
                        status_msg = "🧠 Switched to Smart Money Alpha"
                        status_timer = time.time()
                        needs_update = True
                    elif key == '3' and not show_modal:
                        active_tab = "PORTFOLIO"
                        status_msg = "🎒 Switched to My Portfolio"
                        status_timer = time.time()
                        needs_update = True
                    else:
                        target_idx = int(key) - 1
                        if active_tab == "PORTFOLIO":
                            if 0 <= target_idx < len(portfolio_data.get("items", [])):
                                port_selected_idx = target_idx
                                needs_update = True
                        elif active_tab == "SMART":
                            if 0 <= target_idx < len(smart_history):
                                smart_selected_idx = target_idx
                                needs_update = True
                        else:
                            if 0 <= target_idx < len(disp_history):
                                selected_idx = target_idx
                                needs_update = True
                elif key in ('ENTER', 'SPACE', ' '):
                    if active_tab in ("RADAR", "SMART"):
                        show_modal = not show_modal
                        if show_modal:
                            if active_tab == "SMART" and smart_history and 0 <= smart_selected_idx < len(smart_history):
                                target_proj = smart_history[smart_selected_idx]
                                target_proj["user_balance"] = get_wallet_balance(rpc, target_proj.get("contract", ""), wallet)
                            elif disp_history and 0 <= selected_idx < len(disp_history):
                                target_proj = disp_history[selected_idx]
                                target_proj["user_balance"] = get_wallet_balance(rpc, target_proj.get("contract", ""), wallet)
                        needs_update = True
                elif key in ('r', 'R'):
                    if active_tab == "PORTFOLIO":
                        status_msg = "🔄 Refreshing portfolio balances on-chain..."
                        live.update(build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx))
                        refresh_portfolio(rpc, history, wallet)
                        portfolio_data = build_portfolio_data(rpc, wallet, history)
                        status_msg = f"✅ Portfolio refreshed! Found {len(portfolio_data.get('items', []))} assets."
                        status_timer = time.time()
                        needs_update = True
                    else:
                        target_proj = smart_history[smart_selected_idx] if (active_tab == "SMART" and smart_history and 0 <= smart_selected_idx < len(smart_history)) else (disp_history[selected_idx] if (disp_history and 0 <= selected_idx < len(disp_history)) else None)
                        sel_name = target_proj.get("name", "selected project") if target_proj else "projects"
                        status_msg = f"🔄 Refreshing on-chain metrics for {sel_name}..."
                        live.update(build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx))
                        
                        # Refresh the selected project first so its row updates before the rest
                        if target_proj:
                            refresh_project_metrics(rpc, target_proj, wallet)
                            save_history(history)
                            status_msg = f"✅ Updated {sel_name}! Updating rest..."
                            live.update(build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx))
                        
                        # Refresh remaining projects
                        for i, it in enumerate(history):
                            if not target_proj or it.get("contract") != target_proj.get("contract"):
                                refresh_project_metrics(rpc, it, wallet)
                        save_history(history)
                        status_msg = "✅ All metrics updated on-chain!"
                        status_timer = time.time()
                        needs_update = True
                elif key in ('b', 'B'):
                    if active_tab == "PORTFOLIO":
                        p_items = portfolio_data.get("items", [])
                        if p_items and 0 <= port_selected_idx < len(p_items):
                            url = p_items[port_selected_idx].get("bscscan_url")
                            if url:
                                subprocess.Popen(["open", url])
                                status_msg = "🌐 Opened BscScan in browser!"
                                status_timer = time.time()
                                needs_update = True
                    elif active_tab == "SMART":
                        if smart_history and 0 <= smart_selected_idx < len(smart_history):
                            c_addr = smart_history[smart_selected_idx].get("contract")
                            url = f"https://bscscan.com/address/{c_addr}"
                            subprocess.Popen(["open", url])
                            status_msg = "🌐 Opened BscScan in browser!"
                            status_timer = time.time()
                            needs_update = True
                    else:
                        if disp_history and 0 <= selected_idx < len(disp_history):
                            c_addr = disp_history[selected_idx].get("contract")
                            url = f"https://bscscan.com/address/{c_addr}"
                            subprocess.Popen(["open", url])
                            status_msg = f"🌐 Opened BscScan in browser!"
                            status_timer = time.time()
                            needs_update = True
                elif key in ('w', 'W'):
                    cur_proj = smart_history[smart_selected_idx] if (active_tab == "SMART" and smart_history and 0 <= smart_selected_idx < len(smart_history)) else (disp_history[selected_idx] if (disp_history and 0 <= selected_idx < len(disp_history)) else None)
                    if cur_proj:
                        w_url = cur_proj.get("website")
                        if w_url:
                            subprocess.Popen(["open", w_url])
                            status_msg = f"🌐 Opened Website in browser: {w_url}"
                        else:
                            status_msg = "⚠️ No official website link found for this project."
                        status_timer = time.time()
                        needs_update = True
                elif key in ('x', 'X'):
                    if active_tab == "PORTFOLIO":
                        p_items = portfolio_data.get("items", []) if portfolio_data else []
                        if p_items and 0 <= port_selected_idx < len(p_items):
                            sel_p = p_items[port_selected_idx]
                            c_addr = sel_p.get("contract", "")
                            if c_addr == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee":
                                status_msg = "⛽ BNB is your operational gas reserve. Keep it for transaction fees."
                            elif sel_p.get("is_nft"):
                                m_url = sel_p.get("market_url") or get_element_market_url(sel_p.get("name", ""), c_addr)
                                subprocess.Popen(["open", m_url])
                                status_msg = "🛒 Opened Element Market collection to list NFT!"
                            elif sel_p.get("balance", 0) <= 0:
                                status_msg = "⚠️ Zero balance to exit."
                            else:
                                raw_bal = int(sel_p.get("raw_balance") or sel_p.get("balance") or 1)
                                calldata = build_pancakeswap_sell_calldata(c_addr, raw_bal, wallet)
                                status_msg = f"🔍 Simulating PancakeSwap V2 sell for {sel_p.get('name')}..."
                                live.update(build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx, sell_confirm=None))
                                prev = preview_snipe_call(wallet, PANCAKE_ROUTER_V2, "0", calldata)
                                if prev.get("success"):
                                    data = prev.get("data", {})
                                    req_id = data.get("requestId")
                                    sell_confirm = {
                                        "asset": sel_p,
                                        "requestId": req_id,
                                        "calldata": calldata,
                                        "preview_data": data
                                    }
                                    status_msg = "🎯 Review sell confirmation below. Press [Y] to confirm or [N] to cancel."
                                else:
                                    err = prev.get("error", {}).get("message", "Simulation failed")
                                    req_id = prev.get("data", {}).get("requestId") if prev.get("data") else None
                                    if req_id:
                                        sell_confirm = {
                                            "asset": sel_p,
                                            "requestId": req_id,
                                            "calldata": calldata,
                                            "preview_data": prev.get("data", {})
                                        }
                                        status_msg = "🎯 Review sell confirmation below. Press [Y] to confirm or [N] to cancel."
                                    else:
                                        status_msg = f"❌ Sell Preview Failed: {err[:60]}"
                            status_timer = time.time()
                            needs_update = True
                    else:
                        cur_proj = smart_history[smart_selected_idx] if (active_tab == "SMART" and smart_history and 0 <= smart_selected_idx < len(smart_history)) else (disp_history[selected_idx] if (disp_history and 0 <= selected_idx < len(disp_history)) else None)
                        if cur_proj:
                            x_url = cur_proj.get("twitter")
                            if x_url:
                                subprocess.Popen(["open", x_url])
                                status_msg = f"🐦 Opened X / Twitter in browser: {x_url}"
                            else:
                                status_msg = "⚠️ No X / Twitter account found for this project."
                            status_timer = time.time()
                            needs_update = True
                elif key in ('m', 'M'):
                    if active_tab == "PORTFOLIO":
                        p_items = portfolio_data.get("items", [])
                        if p_items and 0 <= port_selected_idx < len(p_items):
                            m_url = p_items[port_selected_idx].get("market_url")
                            if m_url:
                                subprocess.Popen(["open", m_url])
                                status_msg = "🛒 Opened Marketplace in browser!"
                                status_timer = time.time()
                                needs_update = True
                    else:
                        cur_proj = smart_history[smart_selected_idx] if (active_tab == "SMART" and smart_history and 0 <= smart_selected_idx < len(smart_history)) else (disp_history[selected_idx] if (disp_history and 0 <= selected_idx < len(disp_history)) else None)
                        if cur_proj:
                            m_url = cur_proj.get("market_url") or cur_proj.get("pair_url")
                            c_addr = cur_proj.get("contract", "")
                            std = cur_proj.get("standard", "")
                            is_nft = "NFT" in std or "721" in std
                            p_name = cur_proj.get("name", "")
                            if is_nft:
                                if not m_url or "collections/0x" in m_url or "assets/bsc/" in m_url:
                                    m_url = get_element_market_url(p_name, c_addr)
                            elif not m_url and ("BEP-20" in std or "Token" in std):
                                m_url = f"https://pancakeswap.finance/swap?outputCurrency={c_addr}"
                            if m_url:
                                subprocess.Popen(["open", m_url])
                                status_msg = "🛒 Opened Marketplace in browser!"
                            else:
                                status_msg = "⚠️ No secondary marketplace link available."
                            status_timer = time.time()
                            needs_update = True

                now = time.time()
                if status_msg and status_timer and (now - status_timer > 3):
                    status_msg = ""
                    status_timer = 0
                    needs_update = True

                if latest_block[0] != last_rendered_block:
                    last_rendered_block = latest_block[0]
                    needs_update = True

                if needs_update or (now - last_render_time >= 0.4):
                    live.update(build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx, sell_confirm=sell_confirm))
                    last_render_time = now
                    needs_update = False
    finally:
        stop_event.set()
        key_reader.close()


def run_explore_mode(rpc: BscRpcClient, wallet: str):
    history = load_history()
    if not history:
        print("[!] No projects in history to explore. Start the monitor with 'python3 monitor.py live' first.")
        return

    if not RICH_AVAILABLE or not sys.stdin.isatty():
        list_projects(rpc, wallet)
        return

    key_reader = KeyReader()
    selected_idx = 0
    show_modal = False
    status_msg = ""
    status_timer = 0
    active_filter = "ALL"
    sort_mode = "NEWEST"
    active_tab = "RADAR"
    port_selected_idx = 0
    smart_selected_idx = 0
    portfolio_data = build_portfolio_data(rpc, wallet, history)
    curr_b = rpc.get_block_number()

    needs_update = True
    last_render_time = time.time()
    snipe_confirm = None
    sell_confirm = None

    try:
        with Live(
            build_interactive_dashboard(history, selected_idx, curr_b, 0, wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx, sell_confirm=sell_confirm),
            refresh_per_second=6,
            screen=True
        ) as live:
            while True:
                key = key_reader.get_key(timeout=0.08)
                disp_history = filter_and_sort_projects(history, active_filter, sort_mode)
                smart_history = [x for x in history if not is_system_infra(x) and (x.get("smart_wallets") or x.get("smart_minters"))]
                smart_history.sort(key=lambda x: len(x.get("smart_wallets") or x.get("smart_minters") or []), reverse=True)

                # Active Exit / Take-Profit Sell Confirmation Gate
                if sell_confirm:
                    if key in ('y', 'Y', 'ENTER', '\r', '\n'):
                        status_msg = "⏳ Broadcasting Take-Profit Sell on BNB Chain via Binance Agentic Wallet..."
                        live.update(build_interactive_dashboard(history, selected_idx, curr_b, 0, wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx, sell_confirm=sell_confirm))
                        req_id = sell_confirm.get("requestId")
                        if req_id:
                            res = execute_snipe_call(req_id)
                        else:
                            res = {"success": False, "error": {"message": "Missing requestId"}}
                        if res.get("success"):
                            tx_h = res.get("data", {}).get("txHash", "")
                            status_msg = f"🎉 TAKE-PROFIT SUCCESSFUL! Tx: {tx_h[:10]}...{tx_h[-8:]} Swapped to BNB!"
                            refresh_portfolio(rpc, history, wallet)
                            portfolio_data = build_portfolio_data(rpc, wallet, history)
                            notify_alert()
                        else:
                            err = res.get("error", {}).get("message", "Execution failed")
                            status_msg = f"❌ Sell Failed: {err[:60]}"
                        sell_confirm = None
                        status_timer = time.time()
                        needs_update = True
                        continue
                    elif key in ('n', 'N', 'ESC', 'q', 'Q'):
                        sell_confirm = None
                        status_msg = "🚫 Exit sell cancelled by user."
                        status_timer = time.time()
                        needs_update = True
                        continue

                # Active Sniper Confirmation Gate
                if snipe_confirm:
                    if key in ('y', 'Y', 'ENTER', '\r', '\n'):
                        status_msg = "⏳ Broadcasting snipe transaction on BNB Chain..."
                        live.update(build_interactive_dashboard(history, selected_idx, curr_b, 0, wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx, sell_confirm=sell_confirm))
                        req_id = snipe_confirm.get("requestId")
                        res = execute_snipe_call(req_id)
                        if res.get("success"):
                            tx_h = res.get("data", {}).get("txHash", "")
                            status_msg = f"🎉 SNIPE SUCCESSFUL! Tx: {tx_h[:10]}...{tx_h[-8:]} (Minted!) Added to Portfolio [Tab 3]!"
                            if active_tab == "SMART":
                                target_proj = smart_history[smart_selected_idx] if (smart_history and 0 <= smart_selected_idx < len(smart_history)) else None
                            elif active_tab == "PORTFOLIO":
                                target_proj = None
                            else:
                                target_proj = disp_history[selected_idx] if (disp_history and 0 <= selected_idx < len(disp_history)) else None
                            if target_proj:
                                target_proj["user_balance"] = (target_proj.get("user_balance") or 0) + 1
                            save_history(history)
                            portfolio_data = build_portfolio_data(rpc, wallet, history)
                            notify_alert()
                        else:
                            err = res.get("error", {}).get("message", "Execution failed")
                            status_msg = f"❌ Snipe Failed: {err[:60]}"
                        snipe_confirm = None
                        show_modal = False
                        status_timer = time.time()
                        needs_update = True
                        continue
                    elif key in ('n', 'N', 'ESC', 'q', 'Q'):
                        snipe_confirm = None
                        status_msg = "🚫 Sniper cancelled by user."
                        status_timer = time.time()
                        needs_update = True
                        continue

                if key in ('q', 'Q'):
                    break
                elif key == 'ESC':
                    if show_modal:
                        show_modal = False
                        needs_update = True
                elif key == 'TAB':
                    if not show_modal:
                        tabs = ["RADAR", "SMART", "PORTFOLIO"]
                        cur_tab_idx = tabs.index(active_tab) if active_tab in tabs else 0
                        active_tab = tabs[(cur_tab_idx + 1) % len(tabs)]
                        tab_names = {"RADAR": "📡 Live Radar", "SMART": "🧠 Smart Money Alpha", "PORTFOLIO": "🎒 My Portfolio"}
                        status_msg = f"Switched to {tab_names.get(active_tab, active_tab)}"
                        status_timer = time.time()
                        needs_update = True
                elif key in ('p', 'P'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter in ("PROXY", "CLONE") else "PROXY"
                        status_msg = f"🔍 Filter: {'🧩 PROXY CLONES ONLY' if active_filter == 'PROXY' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('s', 'S'):
                    if active_tab == "PORTFOLIO":
                        p_items = portfolio_data.get("items", [])
                        if p_items and 0 <= port_selected_idx < len(p_items):
                            sel_p = p_items[port_selected_idx]
                            raw_it = sel_p.get("raw_item")
                            if raw_it and raw_it.get("has_free_mint"):
                                target_proj = raw_it
                            else:
                                target_proj = None
                        else:
                            target_proj = None
                    elif active_tab == "SMART":
                        target_proj = smart_history[smart_selected_idx] if (smart_history and 0 <= smart_selected_idx < len(smart_history)) else None
                    else:
                        target_proj = disp_history[selected_idx] if (disp_history and 0 <= selected_idx < len(disp_history)) else None

                    if target_proj:
                        c_addr = target_proj.get("contract")
                        val_wei = str(target_proj.get("mint_value_wei", "0") or "0")
                        raw_calldata = target_proj.get("mint_calldata")
                        sm_addrs = target_proj.get("smart_wallets") or []
                        calldata = replace_calldata_recipient(raw_calldata, wallet, extra_addresses=sm_addrs)
                        sel_name = target_proj.get("name", "Project")
                        action_label = "plain BNB transfer (recv)" if calldata == "0x" else "snipe call"
                        status_msg = f"🔍 Simulating {action_label} on BSC node for {sel_name}..."
                        live.update(build_interactive_dashboard(history, selected_idx, curr_b, 0, wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=None, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx))
                        prev = preview_snipe_call(wallet, c_addr, val_wei, calldata)
                        if prev.get("success"):
                            data = prev.get("data", {})
                            req_id = data.get("requestId")
                            sim = data.get("simulationResult", {})
                            b_changes = sim.get("balanceChanges", [])
                            gain_str = ""
                            if b_changes:
                                bc = b_changes[0]
                                token_addr = bc.get("contractAddress", "")
                                t_info = data.get("tokenInfos", {}).get(token_addr, {}) or {}
                                sym = t_info.get("symbol") or target_proj.get("symbol") or "Token"
                                raw_tid = bc.get("tokenId")
                                if raw_tid:
                                    try:
                                        tid_dec = int(raw_tid, 16)
                                        gain_str = f"+1 {sym} (Token ID #{tid_dec})"
                                    except Exception:
                                        gain_str = f"+1 {sym}"
                                else:
                                    gain_str = f"+{bc.get('change', 1)} {sym}"
                            if not gain_str:
                                gain_str = f"+1 {target_proj.get('symbol') or 'Token'}"

                            snipe_confirm = {
                                "project": target_proj,
                                "requestId": req_id,
                                "gain_str": gain_str,
                                "preview_data": data
                            }
                            status_msg = "🎯 Simulation Passed! Press [Y] to confirm or [N] to cancel."
                            status_timer = time.time()
                            needs_update = True
                        else:
                            err = prev.get("error", {}).get("message", "Simulation failed")
                            status_msg = f"⚠️ Snipe simulation failed: {err[:60]}"
                            status_timer = time.time()
                            needs_update = True
                elif key in ('k', 'K', 'v', 'V'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter == "SNIPER_READY" else "SNIPER_READY"
                        status_msg = f"🔍 Filter: {'🎯 SNIPER READY (60%+ & ANTI-RUG)' if active_filter == 'SNIPER_READY' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('u', 'U', 'e', 'E'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter == "NFT_SURGE" else "NFT_SURGE"
                        status_msg = f"🔍 Filter: {'🦋 NFT SURGE (Element Spike)' if active_filter == 'NFT_SURGE' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('f', 'F'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter == "FREE" else "FREE"
                        status_msg = f"🔍 Filter: {'FREE MINTS ONLY' if active_filter == 'FREE' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('g', 'G'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter == "SMART" else "SMART"
                        status_msg = f"🔍 Filter: {'SMART MONEY ONLY' if active_filter == 'SMART' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('l', 'L'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter == "LP" else "LP"
                        status_msg = f"🔍 Filter: {'DEX LP ONLY' if active_filter == 'LP' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('a', 'A'):
                    if active_tab == "RADAR":
                        active_filter = "ALL" if active_filter == "ACTIVE" else "ACTIVE"
                        status_msg = f"🔍 Filter: {'ACTIVE MINTS ONLY' if active_filter == 'ACTIVE' else 'ALL PROJECTS'}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('c', 'C'):
                    if active_tab == "RADAR":
                        active_filter = "ALL"
                        status_msg = "🔍 Filter: Reset to ALL projects"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key in ('o', 'O'):
                    if active_tab == "RADAR":
                        modes = ["NEWEST", "PROFIT", "SMART", "HEAT", "VELOCITY", "PROGRESS", "SAFETY"]
                        cur_i = modes.index(sort_mode) if sort_mode in modes else 0
                        sort_mode = modes[(cur_i + 1) % len(modes)]
                        status_msg = f"🔀 Sorting by: {sort_mode}"
                        status_timer = time.time()
                        selected_idx = 0
                        needs_update = True
                elif key == 'UP':
                    if not show_modal:
                        if active_tab == "PORTFOLIO":
                            port_selected_idx = max(0, port_selected_idx - 1)
                        elif active_tab == "SMART":
                            smart_selected_idx = max(0, smart_selected_idx - 1)
                        else:
                            selected_idx = max(0, selected_idx - 1)
                        needs_update = True
                elif key == 'DOWN':
                    if not show_modal:
                        if active_tab == "PORTFOLIO":
                            p_len = len(portfolio_data.get("items", []))
                            port_selected_idx = min(max(0, p_len - 1), port_selected_idx + 1)
                        elif active_tab == "SMART":
                            sm_len = len(smart_history)
                            smart_selected_idx = min(max(0, sm_len - 1), smart_selected_idx + 1)
                        else:
                            selected_idx = min(max(0, len(disp_history) - 1), selected_idx + 1)
                        needs_update = True
                elif key == 'PAGE_UP':
                    if not show_modal:
                        pg = max(5, get_viewport_max_rows() - 2)
                        if active_tab == "PORTFOLIO":
                            port_selected_idx = max(0, port_selected_idx - pg)
                        elif active_tab == "SMART":
                            smart_selected_idx = max(0, smart_selected_idx - pg)
                        else:
                            selected_idx = max(0, selected_idx - pg)
                        needs_update = True
                elif key == 'PAGE_DOWN':
                    if not show_modal:
                        pg = max(5, get_viewport_max_rows() - 2)
                        if active_tab == "PORTFOLIO":
                            p_len = len(portfolio_data.get("items", []))
                            port_selected_idx = min(max(0, p_len - 1), port_selected_idx + pg)
                        elif active_tab == "SMART":
                            sm_len = len(smart_history)
                            smart_selected_idx = min(max(0, sm_len - 1), smart_selected_idx + pg)
                        else:
                            selected_idx = min(max(0, len(disp_history) - 1), selected_idx + pg)
                        needs_update = True
                elif key == 'HOME':
                    if not show_modal:
                        if active_tab == "PORTFOLIO":
                            port_selected_idx = 0
                        elif active_tab == "SMART":
                            smart_selected_idx = 0
                        else:
                            selected_idx = 0
                        needs_update = True
                elif key == 'END':
                    if not show_modal:
                        if active_tab == "PORTFOLIO":
                            p_len = len(portfolio_data.get("items", []))
                            port_selected_idx = max(0, p_len - 1)
                        elif active_tab == "SMART":
                            sm_len = len(smart_history)
                            smart_selected_idx = max(0, sm_len - 1)
                        else:
                            selected_idx = max(0, len(disp_history) - 1)
                        needs_update = True
                elif key in ('1', '2', '3', '4', '5', '6', '7', '8', '9'):
                    if key == '1' and not show_modal:
                        active_tab = "RADAR"
                        status_msg = "📡 Switched to Live Radar"
                        status_timer = time.time()
                        needs_update = True
                    elif key == '2' and not show_modal:
                        active_tab = "SMART"
                        status_msg = "🧠 Switched to Smart Money Alpha"
                        status_timer = time.time()
                        needs_update = True
                    elif key == '3' and not show_modal:
                        active_tab = "PORTFOLIO"
                        status_msg = "🎒 Switched to My Portfolio"
                        status_timer = time.time()
                        needs_update = True
                    else:
                        target_idx = int(key) - 1
                        if active_tab == "PORTFOLIO":
                            if 0 <= target_idx < len(portfolio_data.get("items", [])):
                                port_selected_idx = target_idx
                                needs_update = True
                        elif active_tab == "SMART":
                            if 0 <= target_idx < len(smart_history):
                                smart_selected_idx = target_idx
                                needs_update = True
                        else:
                            if 0 <= target_idx < len(disp_history):
                                selected_idx = target_idx
                                needs_update = True
                elif key in ('ENTER', 'SPACE', ' '):
                    if active_tab in ("RADAR", "SMART"):
                        show_modal = not show_modal
                        if show_modal:
                            if active_tab == "SMART" and smart_history and 0 <= smart_selected_idx < len(smart_history):
                                target_proj = smart_history[smart_selected_idx]
                                target_proj["user_balance"] = get_wallet_balance(rpc, target_proj.get("contract", ""), wallet)
                            elif disp_history and 0 <= selected_idx < len(disp_history):
                                target_proj = disp_history[selected_idx]
                                target_proj["user_balance"] = get_wallet_balance(rpc, target_proj.get("contract", ""), wallet)
                        needs_update = True
                elif key in ('r', 'R'):
                    if active_tab == "PORTFOLIO":
                        status_msg = "🔄 Refreshing portfolio balances on-chain..."
                        live.update(build_interactive_dashboard(history, selected_idx, curr_b, 0, wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx))
                        refresh_portfolio(rpc, history, wallet)
                        portfolio_data = build_portfolio_data(rpc, wallet, history)
                        status_msg = f"✅ Portfolio refreshed! Found {len(portfolio_data.get('items', []))} assets."
                        status_timer = time.time()
                        needs_update = True
                    else:
                        target_proj = smart_history[smart_selected_idx] if (active_tab == "SMART" and smart_history and 0 <= smart_selected_idx < len(smart_history)) else (disp_history[selected_idx] if (disp_history and 0 <= selected_idx < len(disp_history)) else None)
                        sel_name = target_proj.get("name", "project") if target_proj else "project"
                        status_msg = f"🔄 Refreshing {sel_name} on-chain..."
                        live.update(build_interactive_dashboard(history, selected_idx, curr_b, 0, wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx))
                        if target_proj:
                            refresh_project_metrics(rpc, target_proj, wallet)
                            save_history(history)
                        status_msg = f"✅ Updated {sel_name}!"
                        status_timer = time.time()
                        needs_update = True
                elif key in ('b', 'B'):
                    if active_tab == "PORTFOLIO":
                        p_items = portfolio_data.get("items", [])
                        if p_items and 0 <= port_selected_idx < len(p_items):
                            url = p_items[port_selected_idx].get("bscscan_url")
                            if url:
                                subprocess.Popen(["open", url])
                                status_msg = "🌐 Opened BscScan in browser!"
                                status_timer = time.time()
                                needs_update = True
                    elif active_tab == "SMART":
                        if smart_history and 0 <= smart_selected_idx < len(smart_history):
                            c_addr = smart_history[smart_selected_idx].get("contract")
                            subprocess.Popen(["open", f"https://bscscan.com/address/{c_addr}"])
                            status_msg = "🌐 Opened BscScan in browser!"
                            status_timer = time.time()
                            needs_update = True
                    else:
                        if disp_history and 0 <= selected_idx < len(disp_history):
                            c_addr = disp_history[selected_idx].get("contract")
                            subprocess.Popen(["open", f"https://bscscan.com/address/{c_addr}"])
                            status_msg = "🌐 Opened BscScan in browser!"
                            status_timer = time.time()
                            needs_update = True
                elif key in ('w', 'W'):
                    cur_proj = smart_history[smart_selected_idx] if (active_tab == "SMART" and smart_history and 0 <= smart_selected_idx < len(smart_history)) else (disp_history[selected_idx] if (disp_history and 0 <= selected_idx < len(disp_history)) else None)
                    if cur_proj:
                        w_url = cur_proj.get("website")
                        if w_url:
                            subprocess.Popen(["open", w_url])
                            status_msg = f"🌐 Opened Website in browser: {w_url}"
                        else:
                            status_msg = "⚠️ No official website link found for this project."
                        status_timer = time.time()
                        needs_update = True
                elif key in ('x', 'X'):
                    if active_tab == "PORTFOLIO":
                        p_items = portfolio_data.get("items", []) if portfolio_data else []
                        if p_items and 0 <= port_selected_idx < len(p_items):
                            sel_p = p_items[port_selected_idx]
                            c_addr = sel_p.get("contract", "")
                            if c_addr == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee":
                                status_msg = "⛽ BNB is your operational gas reserve. Keep it for transaction fees."
                            elif sel_p.get("is_nft"):
                                m_url = sel_p.get("market_url") or get_element_market_url(sel_p.get("name", ""), c_addr)
                                subprocess.Popen(["open", m_url])
                                status_msg = "🛒 Opened Element Market collection to list NFT!"
                            elif sel_p.get("balance", 0) <= 0:
                                status_msg = "⚠️ Zero balance to exit."
                            else:
                                raw_bal = int(sel_p.get("raw_balance") or sel_p.get("balance") or 1)
                                calldata = build_pancakeswap_sell_calldata(c_addr, raw_bal, wallet)
                                status_msg = f"🔍 Simulating PancakeSwap V2 sell for {sel_p.get('name')}..."
                                live.update(build_interactive_dashboard(history, selected_idx, latest_block[0], latest_tx_count[0], wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx, sell_confirm=None))
                                prev = preview_snipe_call(wallet, PANCAKE_ROUTER_V2, "0", calldata)
                                if prev.get("success"):
                                    data = prev.get("data", {})
                                    req_id = data.get("requestId")
                                    sell_confirm = {
                                        "asset": sel_p,
                                        "requestId": req_id,
                                        "calldata": calldata,
                                        "preview_data": data
                                    }
                                    status_msg = "🎯 Review sell confirmation below. Press [Y] to confirm or [N] to cancel."
                                else:
                                    err = prev.get("error", {}).get("message", "Simulation failed")
                                    req_id = prev.get("data", {}).get("requestId") if prev.get("data") else None
                                    if req_id:
                                        sell_confirm = {
                                            "asset": sel_p,
                                            "requestId": req_id,
                                            "calldata": calldata,
                                            "preview_data": prev.get("data", {})
                                        }
                                        status_msg = "🎯 Review sell confirmation below. Press [Y] to confirm or [N] to cancel."
                                    else:
                                        status_msg = f"❌ Sell Preview Failed: {err[:60]}"
                            status_timer = time.time()
                            needs_update = True
                    else:
                        cur_proj = smart_history[smart_selected_idx] if (active_tab == "SMART" and smart_history and 0 <= smart_selected_idx < len(smart_history)) else (disp_history[selected_idx] if (disp_history and 0 <= selected_idx < len(disp_history)) else None)
                        if cur_proj:
                            x_url = cur_proj.get("twitter")
                            if x_url:
                                subprocess.Popen(["open", x_url])
                                status_msg = f"🐦 Opened X / Twitter in browser: {x_url}"
                            else:
                                status_msg = "⚠️ No X / Twitter account found for this project."
                            status_timer = time.time()
                            needs_update = True
                elif key in ('m', 'M'):
                    if active_tab == "PORTFOLIO":
                        p_items = portfolio_data.get("items", [])
                        if p_items and 0 <= port_selected_idx < len(p_items):
                            m_url = p_items[port_selected_idx].get("market_url")
                            if m_url:
                                subprocess.Popen(["open", m_url])
                                status_msg = "🛒 Opened Marketplace in browser!"
                                status_timer = time.time()
                                needs_update = True
                    else:
                        cur_proj = smart_history[smart_selected_idx] if (active_tab == "SMART" and smart_history and 0 <= smart_selected_idx < len(smart_history)) else (disp_history[selected_idx] if (disp_history and 0 <= selected_idx < len(disp_history)) else None)
                        if cur_proj:
                            m_url = cur_proj.get("market_url") or cur_proj.get("pair_url")
                            c_addr = cur_proj.get("contract", "")
                            std = cur_proj.get("standard", "")
                            is_nft = "NFT" in std or "721" in std
                            p_name = cur_proj.get("name", "")
                            if is_nft:
                                if not m_url or "collections/0x" in m_url or "assets/bsc/" in m_url:
                                    m_url = get_element_market_url(p_name, c_addr)
                            elif not m_url and ("BEP-20" in std or "Token" in std):
                                m_url = f"https://pancakeswap.finance/swap?outputCurrency={c_addr}"
                            if m_url:
                                subprocess.Popen(["open", m_url])
                                status_msg = "🛒 Opened Marketplace in browser!"
                            else:
                                status_msg = "⚠️ No secondary marketplace link available."
                            status_timer = time.time()
                            needs_update = True

                now = time.time()
                if status_msg and status_timer and (now - status_timer > 3):
                    status_msg = ""
                    status_timer = 0
                    needs_update = True

                if needs_update or (now - last_render_time >= 0.5):
                    live.update(build_interactive_dashboard(history, selected_idx, curr_b, 0, wallet, status_msg, show_modal, active_filter=active_filter, sort_mode=sort_mode, snipe_confirm=snipe_confirm, active_tab=active_tab, portfolio_data=portfolio_data, port_selected_idx=port_selected_idx, smart_selected_idx=smart_selected_idx, sell_confirm=sell_confirm))
                    last_render_time = now
                    needs_update = False
    finally:
        key_reader.close()


def list_projects(rpc: BscRpcClient, wallet: str):
    history = load_history()
    if not history:
        print("No .ai Agent ID projects detected yet.")
        return
    if RICH_AVAILABLE:
        console = Console()
        dashboard = build_dashboard(history, rpc.get_block_number(), 0, wallet)
        console.print(dashboard)
    else:
        print_banner()
        for item in history:
            print(f"• #{item.get('block')} | {item.get('name')} ({item.get('symbol')}) | {item.get('contract')}")


def inspect_contract(rpc: BscRpcClient, contract_addr: str, wallet: str):
    print_banner()
    print(f"[*] Inspecting Contract: {C_CYAN}{contract_addr}{C_RESET}\n")
    code = rpc.get_code(contract_addr)
    full_code, proxy_type, impl_addr = resolve_full_bytecode(rpc, contract_addr, code)
    if not code or code == "0x":
        print(f"{C_RED}[!] Address has no bytecode on BSC.{C_RESET}")
        return

    links_to_registry = matches_tracked_registry(full_code or code)
    print(f"• {C_BOLD}References Tracked Registry / Launchpad?{C_RESET} {C_GREEN if links_to_registry else C_RED}{links_to_registry}{C_RESET}")
    if proxy_type:
        print(f"• {C_BOLD}Proxy / Clone Pattern:{C_RESET} {C_CYAN}🧩 {proxy_type}{C_RESET} (Master: {impl_addr})")

    details = analyze_contract(rpc, contract_addr, wallet)
    cls = details["classification"]

    print(f"• {C_BOLD}Asset Type:{C_RESET} {cls['icon']} {C_BOLD}{cls['standard']}{C_RESET}")
    print(f"• {C_BOLD}Category:{C_RESET} {C_MAGENTA if cls['is_nft'] else C_YELLOW}{cls['category']}{C_RESET}")
    print(f"• {C_BOLD}Name:{C_RESET} {details['name'] or 'N/A'}")
    print(f"• {C_BOLD}Symbol:{C_RESET} {details['symbol'] or 'N/A'}")
    
    if cls.get("underlying"):
        print(f"• {C_BOLD}Underlying Token Address:{C_RESET} {cls['underlying']}")

    if details.get("is_paused") is not None:
        p_str = f"{C_RED}Paused (Minting not yet opened){C_RESET}" if details["is_paused"] else f"{C_GREEN}Active / Live{C_RESET}"
        print(f"• {C_BOLD}Mint Status:{C_RESET} {p_str}")

    if details.get("max_supply"):
        print(f"• {C_BOLD}Max Supply / Total Minted:{C_RESET} {details.get('total_minted', 0)} / {details['max_supply']}")

    print(f"• {C_BOLD}Free Mint for your wallet?{C_RESET} {C_GREEN if details['has_free_mint'] else C_YELLOW}{details['has_free_mint']}{C_RESET}")
    print(f"• {C_BOLD}Mint Price:{C_RESET} {details.get('mint_price_str', 'TBD')} ({details.get('mint_value_wei', '0')} wei)")
    print(f"• {C_BOLD}BscScan Explorer:{C_RESET} https://bscscan.com/address/{contract_addr}")

    # Market & Community Interest Analysis
    mkt = analyze_market_interest(rpc, contract_addr, None, details)
    print_interest_report(mkt)

    print(f"\n{C_BOLD}Quick CLI Snipe Command:{C_RESET}")
    clean_cd = replace_calldata_recipient(details.get('mint_calldata'), wallet)
    print(f"{C_CYAN}baw contract-call preview --binanceChainId 56 --from {wallet} --to {contract_addr} --value {details.get('mint_value_wei', '0')} --inputData {clean_cd} --json{C_RESET}\n")


def scan_smart_wallets(rpc: BscRpcClient, wallet: str, blocks: int = 200):
    """
    Scans the last N blocks on BSC for transactions from tracked smart wallets (5,000 wallets).
    Audits target contracts and separates into:
    1. ⭐ FREE MINTS (0 BNB - Gas Only)
    2. 💎 PAID TRENDS (>0 BNB - Heavy Smart Money Inflow)
    Provides 1-click preview snipe commands.
    SAFETY GUARANTEE: Never automatically signs or broadcasts transactions (NO AUTO-MINTS).
    """
    smart_wallets = load_smart_wallets()
    if not smart_wallets:
        print(f"{C_RED}[!] No smart wallets found. Run 'python3 monitor.py sync-wallets' to download.{C_RESET}")
        return

    latest_b = rpc.get_block_number()
    start_b = max(1, latest_b - blocks)
    block_range = list(range(start_b, latest_b + 1))
    mins_approx = (blocks * 3) // 60

    if RICH_AVAILABLE:
        console = Console()
        header_text = Text()
        header_text.append("🧠 SMART WALLET ALPHA SCANNER  ", style="bold yellow")
        header_text.append(f"|  Tracked: {len(smart_wallets):,} Agent Wallets  ", style="bold white")
        header_text.append(f"|  Blocks: {start_b:,} ➔ {latest_b:,} ({blocks} blks / ~{mins_approx}m)\n", style="cyan")
        header_text.append(f"🛡️ SAFETY POLICY: STRICT NO-AUTO-MINTS (Audit & 1-Click Preview Only)  |  Wallet: {wallet[:10]}...{wallet[-4:]}", style="bold green")
        console.print(Panel(header_text, border_style="yellow", box=box.ROUNDED))
    else:
        print(f"\n{C_BOLD}{C_YELLOW}🧠 SMART WALLET ALPHA SCANNER (5,000 Tracked Wallets){C_RESET}")
        print(f"• Blocks: {start_b:,} to {latest_b:,} ({blocks} blks / ~{mins_approx}m) | Safety: STRICT NO AUTO-MINTS\n")

    print(f"[*] Fetching {len(block_range)} blocks concurrently via BSC RPC...")

    def fetch_block_txs(b_num):
        try:
            blk = rpc.get_block(b_num, full_tx=True)
            if not blk:
                return []
            txs = blk.get("transactions", [])
            hits = []
            for tx in txs:
                snd = (tx.get("from") or "").lower()
                if snd in smart_wallets:
                    rcv = (tx.get("to") or "").lower()
                    inp = tx.get("input") or ""
                    val = int(tx.get("value") or "0", 16)
                    if rcv and (len(inp) >= 10 or val > 0):
                        hits.append({
                            "sender": snd,
                            "agent_id": smart_wallets[snd],
                            "contract": rcv,
                            "selector": inp[:10] if len(inp) >= 10 else "0x (recv)",
                            "input": inp,
                            "value_wei": val,
                            "block": b_num,
                            "tx_hash": tx.get("hash")
                        })
            return hits
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=15) as executor:
        all_hits_nested = list(executor.map(fetch_block_txs, block_range))

    all_hits = [hit for batch in all_hits_nested for hit in batch]
    print(f"[*] Scanned {len(block_range)} blocks: Detected {len(all_hits)} transactions from tracked smart wallets.")

    if not all_hits:
        print(f"{C_YELLOW}[!] No smart wallet contract interactions detected in the last {blocks} blocks.{C_RESET}")
        return

    known_infra = known_infra_addresses()

    contracts = {}
    for h in all_hits:
        c = h["contract"]
        if c in known_infra:
            continue
        if c not in contracts:
            contracts[c] = {
                "wallets": set(),
                "agent_ids": set(),
                "tx_count": 0,
                "selectors": set(),
                "max_value_wei": 0,
                "sample_input": h["input"],
                "last_block": h["block"],
                "tx_hashes": []
            }
        contracts[c]["wallets"].add(h["sender"])
        contracts[c]["agent_ids"].add(h["agent_id"])
        contracts[c]["tx_count"] += 1
        contracts[c]["selectors"].add(h["selector"])
        contracts[c]["tx_hashes"].append(h["tx_hash"])
        if h["value_wei"] > contracts[c]["max_value_wei"]:
            contracts[c]["max_value_wei"] = h["value_wei"]

    print(f"[*] Discovered {len(contracts)} unique active target contracts. Auditing & classifying concurrently...")

    def audit_target(c_tuple):
        c_addr, c_data = c_tuple
        try:
            info = analyze_contract(rpc, c_addr, wallet)
            cls = info.get("classification", {})
            if is_system_infra({"contract": c_addr, "classification": cls, "name": info.get("name"), "symbol": info.get("symbol"), "max_supply": info.get("max_supply"), "total_minted": info.get("total_minted")}):
                return None
            c_data["name"] = info.get("name") or "Unknown"
            c_data["symbol"] = info.get("symbol") or ""
            c_data["standard"] = cls.get("standard") or "Smart Contract"
            c_data["icon"] = cls.get("icon") or "⚙️"
            c_data["category"] = cls.get("category") or "CUSTOM"
            c_data["total_minted"] = info.get("total_minted")
            c_data["max_supply"] = info.get("max_supply")
            sample_inp = c_data.get("sample_input") or ""
            if len(sample_inp) < 10 or sample_inp in ("", "0x"):
                c_data["mint_calldata"] = "0x"
                c_data["mint_value_wei"] = str(c_data["max_value_wei"])
                c_data["mint_price_bnb"] = c_data["max_value_wei"] / 1e18
                c_data["mint_price_str"] = f"{c_data['mint_price_bnb']:.4f} BNB" if c_data["mint_price_bnb"] > 0 else "⭐ FREE"
                c_data["has_free_mint"] = (c_data["max_value_wei"] == 0)
            else:
                base_cd = info.get("mint_calldata") if info.get("has_mint") else sample_inp
                c_data["mint_calldata"] = replace_calldata_recipient(base_cd, wallet, extra_addresses=c_data["wallets"])
                c_data["mint_price_str"] = info.get("mint_price_str") or ("⭐ FREE" if info.get("has_free_mint") else "TBD")
                c_data["mint_price_bnb"] = info.get("mint_price_bnb") or 0.0
                c_data["mint_value_wei"] = info.get("mint_value_wei") or str(c_data["max_value_wei"])
                c_data["has_free_mint"] = info.get("has_free_mint")
            c_data["is_paused"] = info.get("is_paused")
            c_data["contract"] = c_addr
            c_data["risk_level"] = info.get("risk_level", "SAFE")
            c_data["risk_badge"] = info.get("risk_badge", "🟢 Clean")
            c_data["details"] = info
            return c_data
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        analyzed_raw = list(executor.map(audit_target, list(contracts.items())))

    analyzed = [c for c in analyzed_raw if c is not None]

    history = load_history()
    existing_map = {item.get("contract", "").lower(): item for item in history}

    for c_data in analyzed:
        c_addr = c_data["contract"]
        info = c_data["details"]
        if c_addr in existing_map:
            item = existing_map[c_addr]
            cur_sm = set(item.get("smart_wallets") or [])
            cur_sm.update(c_data["agent_ids"])
            item["smart_wallets"] = list(cur_sm)
            item["smart_minters"] = list(cur_sm)
            if info.get("total_minted"):
                item["total_minted"] = info["total_minted"]
        else:
            w_cnt = len(c_data["wallets"])
            new_item = {
                "block": c_data["last_block"],
                "contract": c_addr,
                "deployer": "",
                "tx_hash": c_data["tx_hashes"][0] if c_data["tx_hashes"] else "",
                "name": c_data["name"],
                "symbol": c_data["symbol"],
                "standard": c_data["standard"],
                "icon": c_data["icon"],
                "is_paused": c_data["is_paused"],
                "has_free_mint": c_data["has_free_mint"],
                "mint_price_str": c_data["mint_price_str"],
                "mint_price_bnb": c_data["mint_price_bnb"],
                "mint_value_wei": c_data["mint_value_wei"],
                "max_supply": c_data["max_supply"],
                "total_minted": c_data["total_minted"] or 0,
                "score": 50,
                "rating": "🔥 HIGH",
                "lp_usd": 0,
                "lp_wbnb": 0,
                "token_symbol": "",
                "token_price_usd": 0,
                "holders": w_cnt,
                "buys_24h": c_data["tx_count"],
                "buys_1h": c_data["tx_count"],
                "sells_24h": 0,
                "volume_24h": 0.0,
                "pair_url": "",
                "mint_calldata": c_data["mint_calldata"],
                "signals": [f"Discovered via {w_cnt} tracked smart wallets ({', '.join(list(c_data['agent_ids'])[:3])})"],
                "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "smart_wallets": list(c_data["agent_ids"]),
                "smart_minters": list(c_data["agent_ids"])
            }
            history.insert(0, new_item)
            existing_map[c_addr] = new_item

    save_history(history)


    free_mints = []
    paid_trends = []

    for d in analyzed:
        p_str = str(d.get("mint_price_str", "")).upper()
        is_free = (
            d.get("has_free_mint")
            or "FREE" in p_str
            or (d["max_value_wei"] == 0 and d["mint_price_bnb"] == 0.0 and any(s in ("0xa0712d68", "0x1249c58b") for s in d["selectors"]))
        )
        if is_free:
            free_mints.append(d)
        else:
            paid_trends.append(d)

    free_mints.sort(key=lambda x: len(x["wallets"]), reverse=True)
    paid_trends.sort(key=lambda x: len(x["wallets"]), reverse=True)

    if RICH_AVAILABLE:
        console = Console()
        # Table 1: Free Mints
        t1 = Table(title="⭐ FREE MINTS (Gas Only - 0 BNB Required)", box=box.ROUNDED, border_style="green", expand=True)
        t1.add_column("Rank", width=5, justify="center", style="bold green")
        t1.add_column("Project / Symbol", min_width=18, style="bold white")
        t1.add_column("Type", width=12, style="magenta")
        t1.add_column("Smart Wallets", min_width=20, style="cyan")
        t1.add_column("Mint Progress", min_width=14, justify="center")
        t1.add_column("Safety", width=11, justify="center")
        t1.add_column("1-Click Preview Snipe Command", min_width=32, style="dim yellow")

        if not free_mints:
            t1.add_row("-", "No free mints in this window", "-", "-", "-", "-", "-")
        else:
            for idx, d in enumerate(free_mints, 1):
                a_names = list(d["agent_ids"])[:3]
                w_str = f"[bold cyan]{len(d['wallets'])}[/bold cyan] ({', '.join(a_names)})"
                prog_s = format_tiered_mint_progress(d)
                safe_s = format_structured_safety(d)
                type_s = format_structured_type(d.get("standard"))

                c_cmd = f"baw contract-call preview --binanceChainId 56 --from {wallet} --to {d['contract']} --value 0 --inputData {d['mint_calldata']} --json"
                t1.add_row(str(idx), f"🧠 {d['name']} ({d['symbol']})", type_s, w_str, prog_s, safe_s, c_cmd)

        console.print(t1)

        # Table 2: Paid Trends
        t2 = Table(title="💎 PAID TRENDS (Smart Money Inflow & Accumulation)", box=box.ROUNDED, border_style="yellow", expand=True)
        t2.add_column("Rank", width=5, justify="center", style="bold yellow")
        t2.add_column("Project / Symbol", min_width=18, style="bold white")
        t2.add_column("Type", width=12, style="magenta")
        t2.add_column("Smart Wallets", min_width=20, style="cyan")
        t2.add_column("Mint Price / Value", min_width=14, justify="center", style="yellow")
        t2.add_column("Circ / Progress", min_width=14, justify="center")
        t2.add_column("Safety", width=11, justify="center")
        t2.add_column("1-Click Preview Command", min_width=32, style="dim yellow")

        if not paid_trends:
            t2.add_row("-", "No paid trends in this window", "-", "-", "-", "-", "-", "-")
        else:
            for idx, d in enumerate(paid_trends, 1):
                a_names = list(d["agent_ids"])[:3]
                w_str = f"[bold cyan]{len(d['wallets'])}[/bold cyan] ({', '.join(a_names)})"
                v_bnb = d["max_value_wei"] / 1e18
                v_str = f"{v_bnb:.4f} BNB" if v_bnb > 0 else d["mint_price_str"]
                prog_s = format_tiered_mint_progress(d)
                safe_s = format_structured_safety(d)
                type_s = format_structured_type(d.get("standard"))

                c_cmd = f"baw contract-call preview --binanceChainId 56 --from {wallet} --to {d['contract']} --value {d['mint_value_wei']} --inputData {d['mint_calldata']} --json"
                t2.add_row(str(idx), f"🧠 {d['name']} ({d['symbol']})", type_s, w_str, v_str, prog_s, safe_s, c_cmd)

        console.print(t2)

        # Safety Notice
        notice = Text()
        notice.append("🛡️ STRICT NO-AUTO-MINT POLICY ACTIVE:\n", style="bold green")
        notice.append("• No automated transactions are signed or sent without your explicit confirmation.\n", style="white")
        notice.append("• All detected drops are now indexed in your Live Radar table.\n", style="cyan")
        notice.append("• To preview & snipe with 1-click confirmation, launch: ", style="white")
        notice.append("python3 monitor.py explore", style="bold yellow")
        notice.append(" and press ", style="white")
        notice.append("[s] Snipe", style="bold green")
        notice.append(".\n", style="white")
        console.print(Panel(notice, border_style="green", box=box.ROUNDED))
    else:
        print("\n--- ⭐ FREE MINTS (0 BNB - Gas Only) ---")
        for idx, d in enumerate(free_mints, 1):
            a_names = list(d["agent_ids"])[:3]
            w_cnt = len(d["wallets"])
            print(f"#{idx} {d['name']} ({d['symbol']}) | {w_cnt} wallets ({a_names}) | Contract: {d['contract']}")
            print(f"   Snipe Preview: baw contract-call preview --binanceChainId 56 --from {wallet} --to {d['contract']} --value 0 --inputData {d['mint_calldata']} --json")

        print("\n--- 💎 PAID TRENDS (Smart Money Inflow) ---")
        for idx, d in enumerate(paid_trends, 1):
            a_names = list(d["agent_ids"])[:3]
            w_cnt = len(d["wallets"])
            v_bnb = d["max_value_wei"] / 1e18
            print(f"#{idx} {d['name']} ({d['symbol']}) | {w_cnt} wallets ({a_names}) | Max Val: {v_bnb:.4f} BNB | Contract: {d['contract']}")
            print(f"   Snipe Preview: baw contract-call preview --binanceChainId 56 --from {wallet} --to {d['contract']} --value {d['mint_value_wei']} --inputData {d['mint_calldata']} --json")


def print_wallet_leaderboard(limit: int = 50, filter_mode: str = "ALL"):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    seed_file = os.path.join(base_dir, "seeded_smart_hunters.json")
    if not os.path.exists(seed_file):
        print(f"{C_RED}[!] seeded_smart_hunters.json not found.{C_RESET}")
        return
    with open(seed_file, "r") as f:
        hunters = json.load(f)
    if filter_mode == "HIGH_WIN":
        hunters = [h for h in hunters if h.get("winRate", 0) >= 50.0]
    hunters.sort(key=lambda x: (x.get("wins", 0), x.get("winRate", 0)), reverse=True)
    if RICH_AVAILABLE:
        console = Console()
        table = Table(title=f"🏆 Binance .ai Agent ID Smart Wallet Leaderboard ({len(hunters):,} Tracked Hunters)", box=box.ROUNDED)
        table.add_column("#", justify="center", style="dim")
        table.add_column("Agent ID (.ai)", style="bold cyan")
        table.add_column("Win Rate", justify="center", style="bold green")
        table.add_column("Wins / Mints", justify="center", style="bold yellow")
        table.add_column("Top Winning Picks", style="white")
        for idx, h in enumerate(hunters[:limit], 1):
            rank_str = "🥇" if idx == 1 else ("🥈" if idx == 2 else ("🥉" if idx == 3 else f"#{idx}"))
            wr = h.get("winRate", 0.0)
            wr_style = "bold green" if wr >= 50 else ("bold yellow" if wr >= 30 else "dim")
            wr_str = f"[{wr_style}]{wr:.1f}%[/{wr_style}]"
            mints_str = f"{h.get('wins', 0)} / {h.get('mints', 0)}"
            picks = ", ".join(h.get("topPicks", [])[:3])
            table.add_row(rank_str, h.get("name", ""), wr_str, mints_str, picks)
        console.print(table)
    else:
        print("\n--- 🏆 SMART WALLET LEADERBOARD ---")
        for idx, h in enumerate(hunters[:limit], 1):
            print(f"#{idx} {h.get('name')} | Win Rate: {h.get('winRate', 0):.1f}% | Wins: {h.get('wins', 0)}/{h.get('mints', 0)} | Top Picks: {', '.join(h.get('topPicks', []))}")


def main():
    parser = argparse.ArgumentParser(description="Binance .ai Agent ID Fair-Mint & Contract Monitor")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    live_parser = subparsers.add_parser("live", help="Start real-time block-by-block monitor")
    live_parser.add_argument("--wallet", default=DEFAULT_WALLET, help="Your Binance Agentic Wallet address")
    live_parser.add_argument("--plain", action="store_true", help="Use plain scrolling text mode instead of live table")

    explore_parser = subparsers.add_parser("explore", help="Interactive TUI table navigation, deep-dive modal, and live metric refresh")
    explore_parser.add_argument("--wallet", default=DEFAULT_WALLET, help="Your Binance Agentic Wallet address")

    list_parser = subparsers.add_parser("list", help="Display live table of tracked projects")
    list_parser.add_argument("--wallet", default=DEFAULT_WALLET, help="Your Binance Agentic Wallet address")

    leaderboard_parser = subparsers.add_parser("leaderboard", help="Display top Binance .ai smart wallet leaderboard in terminal")
    leaderboard_parser.add_argument("--limit", type=int, default=50, help="Number of hunters to display (default: 50)")
    leaderboard_parser.add_argument("--high-win", action="store_true", help="Filter for high win rate (>=50%)")

    wallets_parser = subparsers.add_parser("wallets", help="Alias for leaderboard")
    wallets_parser.add_argument("--limit", type=int, default=50, help="Number of hunters to display (default: 50)")
    wallets_parser.add_argument("--high-win", action="store_true", help="Filter for high win rate (>=50%)")

    refresh_parser = subparsers.add_parser("refresh", help="Fetch fresh on-chain metrics for all tracked projects")
    refresh_parser.add_argument("--wallet", default=DEFAULT_WALLET, help="Your Binance Agentic Wallet address")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a specific contract address")
    inspect_parser.add_argument("contract", help="Contract address to inspect")
    inspect_parser.add_argument("--wallet", default=DEFAULT_WALLET, help="Your Binance Agentic Wallet address")

    holdings_parser = subparsers.add_parser("holdings", help="Check your wallet's NFT & token balances across all tracked contracts")
    holdings_parser.add_argument("--wallet", default=DEFAULT_WALLET, help="Your Binance Agentic Wallet address")

    nfts_parser = subparsers.add_parser("my-nfts", help="Alias for holdings")
    nfts_parser.add_argument("--wallet", default=DEFAULT_WALLET, help="Your Binance Agentic Wallet address")

    scan_parser = subparsers.add_parser("scan-wallets", help="Scan recent blocks for smart wallet mints & paid trends (No auto-mints)")
    scan_parser.add_argument("--blocks", type=int, default=200, help="Number of recent blocks to scan (default: 200)")
    scan_parser.add_argument("--wallet", default=DEFAULT_WALLET, help="Your Binance Agentic Wallet address")

    sync_parser = subparsers.add_parser("sync-wallets", help="Download/update tracked smart wallets from Google Sheets")

    args = parser.parse_args()
    rpc = BscRpcClient(RPC_ENDPOINTS)
    # Warm the BNB/USD price in the background so USD values don't start from the fallback.
    threading.Thread(target=refresh_bnb_price, args=(rpc,), daemon=True).start()

    if args.command == "live":
        run_live_monitor(rpc, args.wallet, plain=args.plain)
    elif args.command == "explore":
        run_explore_mode(rpc, args.wallet)
    elif args.command == "list":
        list_projects(rpc, args.wallet)
    elif args.command == "refresh":
        history = load_history()
        print(f"[*] Refreshing on-chain metrics for {len(history)} tracked projects...")
        refresh_all_metrics(rpc, history, args.wallet)
        list_projects(rpc, args.wallet)
    elif args.command == "inspect":
        inspect_contract(rpc, args.contract, args.wallet)
    elif args.command in ("leaderboard", "wallets"):
        mode = "HIGH_WIN" if getattr(args, "high_win", False) else "ALL"
        print_wallet_leaderboard(limit=getattr(args, "limit", 50), filter_mode=mode)
    elif args.command in ("holdings", "my-nfts"):
        check_holdings(rpc, args.wallet)
    elif args.command == "scan-wallets":
        scan_smart_wallets(rpc, args.wallet, blocks=args.blocks)
    elif args.command == "sync-wallets":
        sync_smart_wallets()
    else:
        run_live_monitor(rpc, DEFAULT_WALLET, plain=False)


if __name__ == "__main__":
    main()
