#!/usr/bin/env python3
"""
FastAPI web server (read side).

Serves /healthz, /history.json, /api/feed, /api/projects, /api/favorites and /api/sync from the
db.py cache, plus the static frontend in local development. It does no chain scanning; that is
worker.py's job.
"""

import json
import os
import re
import secrets
import threading
import time
import traceback
from typing import Dict, Optional
from fastapi import FastAPI, Query, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

import monitor
from common.runtime import run_memory_guard_loop
from db import db
from filters import (
    BOGUS_SCAM_CONTRACTS,
    BOGUS_SET,
    EXCLUDED_PEGS_CONTRACTS,
    EXCLUDED_PEGS_SYMBOLS,
    SYNTHETIC_EQUITY_SYMBOLS,
)

app = FastAPI(
    title="Agent ID Monitor Backend",
    version="2.0.0",
    description="High-performance async FastAPI backend powered by Supabase / SQLite WAL"
)

# Compress responses to stay under Render's 5 GB monthly bandwidth cap.
app.add_middleware(GZipMiddleware, minimum_size=500)

# In-memory rate limiting: fixed 60s window per client IP.
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 120
RATE_LIMIT_MAX_TRACKED_IPS = 50000
RATE_LIMIT_EXEMPT = {"/healthz", "/health"}
_rate_window_id = 0
_rate_counts: Dict[str, int] = {}


def _client_ip(request: Request) -> str:
    # Cloudflare (in front of Render) overwrites CF-Connecting-IP, so clients cannot spoof it.
    # X-Forwarded-For is the last resort (its first entry is client-controlled).
    ip = request.headers.get("cf-connecting-ip")
    if not ip:
        xff = request.headers.get("x-forwarded-for", "")
        ip = xff.split(",")[0].strip() if xff else ""
    if not ip:
        ip = request.client.host if request.client else "unknown"
    return ip


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    global _rate_window_id
    path = request.url.path
    if request.method == "OPTIONS" or not path.startswith("/api/") or path in RATE_LIMIT_EXEMPT:
        return await call_next(request)

    now = time.time()
    window_id = int(now // RATE_LIMIT_WINDOW)
    if window_id != _rate_window_id:
        # New window: drop every old counter so the dict stays bounded.
        _rate_counts.clear()
        _rate_window_id = window_id
    ip = _client_ip(request)
    if ip not in _rate_counts and len(_rate_counts) >= RATE_LIMIT_MAX_TRACKED_IPS:
        _rate_counts.clear()  # hard cap against key-flooding; fails open for this window
    count = _rate_counts.get(ip, 0) + 1
    _rate_counts[ip] = count
    if count > RATE_LIMIT_MAX:
        retry_after = max(1, int((window_id + 1) * RATE_LIMIT_WINDOW - now) + 1)
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={"success": False, "error": "rate_limited"},
        )
    return await call_next(request)


# The Vercel-hosted frontend calls this API cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # public read-mostly API; admin uses bearer tokens, not cookies
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_admin(request: Request) -> None:
    """Gate for write/delete endpoints. Requires `Authorization: Bearer <ADMIN_TOKEN>`.

    If ADMIN_TOKEN is unset/empty the endpoints are disabled (503), never open.
    """
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="admin disabled")
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not secrets.compare_digest(token.strip().encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print(f"[🔥 API Unhandled Error] {request.method} {request.url.path}: {exc}\n{tb}", flush=True)
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "internal_error"}
    )


ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
MAX_FAVORITES = 500


@app.on_event("startup")
def startup_event():
    t = threading.Thread(target=run_memory_guard_loop, daemon=True, name="ApiMemoryGuard")
    t.start()


@app.post("/api/admin/purge-bogus", dependencies=[Depends(require_admin)])
@app.delete("/api/projects/{contract}", dependencies=[Depends(require_admin)])
def purge_bogus_endpoint(contract: Optional[str] = None):
    targets = [contract.lower()] if contract else BOGUS_SCAM_CONTRACTS
    cnt = db.delete_projects(targets)
    return {"success": True, "purged": cnt, "targets": targets}


@app.get("/healthz")
@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "agent-id-backend",
        "db_mode": db.mode,
        "project_count": db.get_project_count()
    }


@app.get("/history.json")
def get_virtual_history_json(request: Request):
    """Full project list as pre-encoded JSON bytes, with ETag/304 support."""
    data, etag = db.get_history_json()
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304)
    return Response(
        content=data,
        media_type="application/json",
        headers={
            "Cache-Control": "public, max-age=120, stale-while-revalidate=300",
            "ETag": etag
        }
    )


def _etag_matches(client_etag: Optional[str], target_etag: str) -> bool:
    """Matches weak (W/...) and strong ETags across Cloudflare and browsers."""
    if not client_etag or not target_etag:
        return False
    def normalize(tag: str) -> str:
        tag = tag.strip()
        if tag.startswith("W/"):
            tag = tag[2:]
        return tag.strip('"')
    return normalize(client_etag) == normalize(target_etag)


def is_bogus_or_synthetic_asset(d: dict) -> bool:
    if not d or not isinstance(d, dict):
        return True
    c = str(d.get("contract") or "").lower().strip()
    if c in BOGUS_SET or c in EXCLUDED_PEGS_CONTRACTS:
        return True
    sym = str(d.get("symbol") or "").upper().strip()
    if sym in EXCLUDED_PEGS_SYMBOLS or sym in SYNTHETIC_EQUITY_SYMBOLS:
        return True
    name = str(d.get("name") or "").upper().strip()
    if any(k in name for k in (
        'ETF', 'PROSHARES', 'DIREXION', 'BEAR 3X', 'BULL 3X', 'INVESQO', 'INVESCO', 
        'BSTOCKS', 'TOKENIZED STOCK', 'S&P 500', 'NASDAQ', 'ISHARES', 'VANGUARD',
        'PANCAKESWAP', 'BINANCE-PEG', 'WRAPPED BNB', 'TETHER USD', 'USD COIN', 'ZCASH', 'DOGEZILLA'
    )):
        return True
    return False

def get_sanitized_mcap(d: dict) -> float:
    if not d or not isinstance(d, dict):
        return 0.0
    try:
        mc = float(d.get("market_cap") or d.get("fdv") or 0.0)
        if mc == 0.0:
            px = float(d.get("token_price_usd") or d.get("market_price_usd") or 0.0)
            tm = float(d.get("total_minted") or 0.0)
            ms = float(d.get("max_supply") or 0.0)
            if px > 0:
                if 0 < tm < 1e14:
                    mc = tm * px
                elif 0 < ms < 1e14:
                    mc = ms * px
                elif tm >= 1e14:
                    mc = (tm / 1e18) * px
                elif ms >= 1e14:
                    mc = (ms / 1e18) * px
        if mc > 100_000_000 and float(d.get("lp_usd") or 0.0) < 50_000:
            return 0.0
        return mc
    except Exception:
        return 0.0

def is_secondary_market_asset(d: dict) -> bool:
    if not d or not isinstance(d, dict):
        return False
    try:
        c = str(d.get("contract") or "").lower().strip()
        sym = str(d.get("symbol") or "").upper().strip()
        name = str(d.get("name") or "").upper().strip()
        if c in EXCLUDED_PEGS_CONTRACTS or sym in EXCLUDED_PEGS_SYMBOLS or sym in SYNTHETIC_EQUITY_SYMBOLS:
            return False
        if "ZCASH" in name or "DOGEZILLA" in name or "BINANCE-PEG" in name or "PANCAKESWAP" in name:
            return False
        lp = float(d.get("lp_usd") or 0.0)
        mc = get_sanitized_mcap(d)
        px = float(d.get("token_price_usd") or 0.0)
        vol = float(d.get("volume_24h") or 0.0)
        sec_vol = float(d.get("secondary_vol_bnb") or 0.0)
        has_pair = bool(d.get("pair_address") or d.get("pair_url"))
        is_grad = bool(d.get("is_graduated"))
        return lp > 50.0 or mc > 500.0 or (px > 0 and vol > 0) or sec_vol > 0 or has_pair or is_grad
    except Exception:
        return False


# Predicates behind /api/feed?filter=<name>; also used for the per-filter counts in the overview stats.
FEED_FILTERS = {
    "secondary": is_secondary_market_asset,
    "smart": lambda d: d.get("smart_wallets") or d.get("smart_minters"),
    "sniper_ready": lambda d: d.get("sniper_ready"),
    "proxy": lambda d: d.get("is_proxy_clone"),
    "nft_surge": lambda d: d.get("is_nft_surge"),
}


def compute_global_overview_stats(all_projs=None) -> dict:
    try:
        if all_projs is None:
            raw_projs = db.get_all_projects()
            all_projs = [p for p in raw_projs if isinstance(p, dict) and not is_bogus_or_synthetic_asset(p)]
        else:
            all_projs = [p for p in all_projs if isinstance(p, dict) and not is_bogus_or_synthetic_asset(p)]
        total = len(all_projs)
        latest_block = all_projs[0].get("block") if all_projs else None

        counts = {name: sum(1 for d in all_projs if pred(d)) for name, pred in FEED_FILTERS.items()}
        smart_count = counts["smart"]
        sniper_ready_count = counts["sniper_ready"]
        proxy_count = counts["proxy"]
        nft_surge_count = counts["nft_surge"]
        secondary_count = counts["secondary"]

        local_stats = {}
        try:
            local_stats = monitor.load_agent_id_stats() or {}
        except Exception as e:
            print(f"[⚠️ Overview Stats] local agent-id stats unavailable: {e}", flush=True)
        cloud_stats = {}
        try:
            cloud_stats = db.get_agent_id_stats() or {}
        except Exception as e:
            print(f"[⚠️ Overview Stats] cloud agent-id stats unavailable: {e}", flush=True)

        local_count = int(local_stats.get("total_created_wallets") or 0)
        cloud_count = int(cloud_stats.get("total_created_wallets") or 0)
        if cloud_count > local_count:
            best_agent_stats = cloud_stats
        elif cloud_count == local_count and int(cloud_stats.get("last_block") or 0) > int(local_stats.get("last_block") or 0):
            best_agent_stats = cloud_stats
        else:
            best_agent_stats = local_stats

        total_agent_wallets = int(best_agent_stats.get("total_created_wallets") or monitor.DEFAULT_AGENT_WALLET_COUNT)
        agent_wallets_block = int(best_agent_stats.get("last_block") or 0)

        return {
            "total": total,
            "smart": smart_count,
            "sniper_ready": sniper_ready_count,
            "proxy": proxy_count,
            "nft_surge": nft_surge_count,
            "secondary": secondary_count,
            "latest_block": latest_block,
            "agent_wallets": total_agent_wallets,
            "total_created_wallets": total_agent_wallets,
            "agent_wallets_block": agent_wallets_block
        }
    except Exception as e:
        print(f"[⚠️ Overview Stats Fallback Error] {e}", flush=True)
        return {
            "total": len(all_projs) if all_projs else 0,
            "smart": 0,
            "sniper_ready": 0,
            "proxy": 0,
            "nft_surge": 0,
            "secondary": 0,
            "latest_block": None,
            "agent_wallets": monitor.DEFAULT_AGENT_WALLET_COUNT,
            "total_created_wallets": monitor.DEFAULT_AGENT_WALLET_COUNT,
            "agent_wallets_block": 0
        }


# Main route: /api/stats. /api/overview and /api/summary are aliases kept for external clients.
@app.get("/api/stats")
@app.get("/api/overview")
@app.get("/api/summary")
def get_overview_stats(request: Request):
    """Summary counts for the overview header, with ETag/304 support."""
    stats = compute_global_overview_stats()
    stats_etag = f'"{stats["total"]}-{stats["agent_wallets"]}-{stats.get("agent_wallets_block")}-{stats.get("latest_block")}"'
    client_etag = request.headers.get("if-none-match")
    if _etag_matches(client_etag, stats_etag):
        return Response(status_code=304, headers={"ETag": stats_etag, "Cache-Control": "public, max-age=5"})
    return JSONResponse(
        status_code=200,
        headers={"ETag": stats_etag, "Cache-Control": "public, max-age=5"},
        content={
            "success": True,
            "mode": "stats",
            "stats": stats,
            "agent_wallets": stats["agent_wallets"],
            "total_created_wallets": stats["total_created_wallets"],
            "agent_wallets_block": stats.get("agent_wallets_block", 0),
            "latest_block": stats.get("latest_block")
        }
    )


@app.get("/api/bnb-price")
def get_bnb_price():
    """Live BNB/USD from the Pancake WBNB/USDT pool (cached 60s). `live` is false until a lookup has succeeded."""
    price = monitor.refresh_bnb_price()
    return JSONResponse(
        content={"success": True, "bnb_price_usd": price, "live": monitor.bnb_price_is_live()},
        headers={"Cache-Control": "public, max-age=30"},
    )


MARKET_FIELDS = ("token_price_usd", "market_cap", "fdv", "lp_usd", "volume_24h", "price_change_24h",
                 "buys_24h", "sells_24h", "buys_1h")


@app.get("/api/market-data")
def get_market_data():
    """Latest market numbers for every tracked contract that has a price or liquidity, keyed by address.

    Not filtered by the feed's exclusion lists on purpose: the frontend uses this to refresh the static
    seeded files (seeded_smart_drops.json), which contain drops that /api/feed never returns."""
    out = {}
    for p in db.get_all_projects():
        c = (p.get("contract") or "").lower()
        if c and (p.get("token_price_usd") or p.get("lp_usd")):
            out[c] = {k: p.get(k) for k in MARKET_FIELDS}
    return JSONResponse(content=out, headers={"Cache-Control": "public, max-age=60"})


@app.get("/agent_id_stats.json")
@app.get("/data/agent_id_stats.json")
def get_agent_id_stats_json():
    """Agent ID counter, served at the paths the static frontend falls back to."""
    local_st = monitor.load_agent_id_stats() or {}
    cloud_st = db.get_agent_id_stats() or {}
    stats = cloud_st if int(cloud_st.get("total_created_wallets") or 0) > int(local_st.get("total_created_wallets") or 0) else local_st
    return JSONResponse(
        content=stats,
        headers={"Cache-Control": "public, max-age=15", "Access-Control-Allow-Origin": "*"}
    )


@app.get("/api/feed")
def get_feed(
    request: Request,
    limit: int = Query(150, ge=1, le=500),
    offset: int = Query(0, ge=0),
    since_block: Optional[int] = Query(None),
    filter: Optional[str] = Query(None)
):
    """Paged feed for the UI, or only the drops newer than `since_block` (delta mode).

    Returns 304 / empty payloads when the client is up to date, to save bandwidth.
    """
    raw_projs = db.get_all_projects()
    all_projs = [p for p in raw_projs if not is_bogus_or_synthetic_asset(p)]
    total = len(all_projs)
    latest_block = all_projs[0].get("block") if all_projs else None

    # Filter counts are computed here so the frontend doesn't need every drop just to show them.
    stats = compute_global_overview_stats(all_projs)
    total_agent_wallets = stats["agent_wallets"]
    agent_wallets_block = stats.get("agent_wallets_block", 0)

    client_etag = request.headers.get("if-none-match")

    if since_block is not None:
        delta = [p for p in all_projs if int(p.get("block") or 0) > since_block]

        if not delta:
            delta_etag = f'"{total}-{total_agent_wallets}-delta-{since_block}"'
            if _etag_matches(client_etag, delta_etag):
                return Response(status_code=304, headers={"ETag": delta_etag, "Cache-Control": "public, max-age=5"})
            # No new drops: send a minimal payload without the nested stats.
            return JSONResponse(
                status_code=200,
                headers={"ETag": delta_etag, "Cache-Control": "public, max-age=5"},
                content={
                    "success": True,
                    "mode": "delta",
                    "total": total,
                    "count": 0,
                    "latest_block": latest_block,
                    "agent_wallets": total_agent_wallets,
                    "agent_wallets_block": agent_wallets_block,
                    "projects": []
                }
            )

        delta_etag = f'"{latest_block}-{total}-{total_agent_wallets}-delta-{since_block}"'
        return JSONResponse(
            status_code=200,
            headers={"ETag": delta_etag, "Cache-Control": "public, max-age=5"},
            content={
                "success": True,
                "mode": "delta",
                "total": total,
                "count": len(delta),
                "latest_block": latest_block,
                "agent_wallets": total_agent_wallets,
                "agent_wallets_block": agent_wallets_block,
                "projects": delta,
                "stats": stats
            }
        )

    target_list = all_projs
    predicate = FEED_FILTERS.get(filter)
    if predicate:
        target_list = [d for d in all_projs if predicate(d)]
        if filter == "secondary":
            target_list.sort(key=lambda x: get_sanitized_mcap(x), reverse=True)

    target_total = len(target_list)
    paginated = target_list[offset : offset + limit]

    feed_etag = f'"{latest_block}-{total}-{total_agent_wallets}-{offset}-{limit}-{filter or ""}"'
    if _etag_matches(client_etag, feed_etag):
        return Response(status_code=304, headers={"ETag": feed_etag, "Cache-Control": "public, max-age=5"})

    return JSONResponse(
        status_code=200,
        headers={"ETag": feed_etag, "Cache-Control": "public, max-age=5"},
        content={
            "success": True,
            "mode": "feed",
            "total": total,
            "filter_total": target_total,
            "count": len(paginated),
            "offset": offset,
            "limit": limit,
            "has_more": (offset + limit) < target_total,
            "latest_block": latest_block,
            "projects": paginated,
            "stats": stats
        }
    )


@app.get("/api/top-gainers")
def get_top_gainers():
    """Top post-mint gainers (Hall of Fame), read from seeded_top_gainers.json."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    seed_file = os.path.join(base_dir, "seeded_top_gainers.json")
    if os.path.exists(seed_file):
        try:
            with open(seed_file, "r") as f:
                data = json.load(f)
            return {"success": True, "count": len(data), "gainers": data}
        except Exception as e:
            return {"success": False, "error": str(e), "gainers": []}
    return {"success": True, "count": 0, "gainers": []}


@app.get("/api/smart-hunters")
def get_smart_hunters():
    """Top smart-wallet hunters and win rates, read from seeded_smart_hunters.json."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    seed_file = os.path.join(base_dir, "seeded_smart_hunters.json")
    if os.path.exists(seed_file):
        try:
            with open(seed_file, "r") as f:
                data = json.load(f)
            return {"success": True, "count": len(data), "hunters": data}
        except Exception as e:
            return {"success": False, "error": str(e), "hunters": []}
    return {"success": True, "count": 0, "hunters": []}


@app.get("/api/smart-drops")
def get_smart_drops():
    """Historical smart-money drops, read from seeded_smart_drops.json."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    seed_file = os.path.join(base_dir, "seeded_smart_drops.json")
    if os.path.exists(seed_file):
        try:
            with open(seed_file, "r") as f:
                data = json.load(f)
            return Response(
                content=json.dumps(data),
                media_type="application/json",
                headers={"Cache-Control": "public, max-age=300"}
            )
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e), "drops": []}, status_code=500)
    return JSONResponse({"success": True, "count": 0, "drops": []})


@app.get("/api/projects")
def get_projects(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    sniper_ready: Optional[bool] = Query(None),
    min_progress: Optional[float] = Query(None),
    sort_by: str = Query("block"),
    order: str = Query("desc")
):
    """Project list with search, status/sniper/progress filters, sorting and pagination."""
    res = db.get_projects(
        limit=limit,
        offset=offset,
        search=search,
        status=status,
        sniper_ready=sniper_ready,
        min_progress=min_progress,
        sort_by=sort_by,
        order=order
    )
    if isinstance(res, dict) and "projects" in res:
        res["projects"] = [p for p in res["projects"] if not is_bogus_or_synthetic_asset(p)]
    return res


# Main route: /api/favorites. /favorites is an alias kept for older clients.
@app.get("/api/favorites")
@app.get("/favorites")
def get_favorites(wallet: str = Query(..., description="0x... BSC wallet address")):
    w = (wallet or "").lower().strip()
    if not ADDRESS_RE.fullmatch(w):
        raise HTTPException(
            status_code=400,
            detail="Valid 0x... BSC wallet address required in ?wallet= parameter"
        )
    record = db.get_favorites(w)
    return {
        "success": True,
        "wallet": w,
        "favorites": record.get("favorites", []),
        "updated_at": record.get("updated_at", 0)
    }


# Main route: /api/favorites. /favorites is an alias kept for older clients.
@app.post("/api/favorites")
@app.post("/favorites")
async def post_favorites(request: Request):
    try:
        incoming = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(incoming, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    wallet = str(incoming.get("wallet") or "").lower().strip()
    if not ADDRESS_RE.fullmatch(wallet):
        raise HTTPException(status_code=400, detail="Valid 0x... BSC wallet address required")

    raw_favs = incoming.get("favorites", [])
    if not isinstance(raw_favs, list):
        raw_favs = []
    if len(raw_favs) > MAX_FAVORITES:
        raise HTTPException(status_code=413, detail=f"Too many favorites (max {MAX_FAVORITES})")
    clean_favs = [c.lower() for c in raw_favs if isinstance(c, str) and ADDRESS_RE.fullmatch(c)]
    clean_favs = list(dict.fromkeys(clean_favs))
    ts = int(incoming.get("timestamp") or time.time())

    db.save_favorites(wallet, clean_favs, ts)

    return {
        "success": True,
        "wallet": wallet,
        "favorites": clean_favs,
        "updated_at": ts
    }


# Main route: /api/sync. /sync is an alias kept for older clients.
@app.post("/api/sync", dependencies=[Depends(require_admin)])
@app.post("/sync", dependencies=[Depends(require_admin)])
async def post_sync(request: Request):
    try:
        incoming = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    if isinstance(incoming, list):
        current = monitor.load_history()
        merged_dict = {x["contract"].lower(): x for x in current if x.get("contract")}
        for item in incoming:
            c = (item.get("contract") or "").lower()
            if c:
                if c not in merged_dict:
                    merged_dict[c] = item
                else:
                    for k, v in item.items():
                        if v is not None and (k not in merged_dict[c] or merged_dict[c][k] is None or k == "user_balance"):
                            merged_dict[c][k] = v
        new_history = list(merged_dict.values())
        new_history.sort(key=lambda x: x.get("block", 0) or 0, reverse=True)
        monitor.save_history(new_history)
        return {"success": True, "count": len(new_history)}
    raise HTTPException(status_code=400, detail="Expected JSON array of projects")


@app.get("/")
def root_handler(request: Request):
    """On Render, send browsers and crawlers to the Vercel frontend so page loads don't use Render's bandwidth."""
    accept = request.headers.get("accept", "")
    host = request.headers.get("host", "")
    if os.environ.get("RENDER") or "onrender.com" in host:
        if "application/json" in accept and "text/html" not in accept:
            return {"status": "ok", "service": "agent-id-backend", "frontend": "https://agent-id-monitor.vercel.app"}
        return RedirectResponse(url="https://agent-id-monitor.vercel.app", status_code=302)

    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    index_path = os.path.join(workspace_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"status": "ok", "service": "agent-id-backend"}


@app.get("/bnb_agent_id_screen_studio.mp4")
@app.get("/bnb_agent_id_screen_studio_silent.mp4")
def redirect_heavy_videos():
    """Redirect the 7.5 MB videos to Vercel so they don't count against Render's bandwidth."""
    return RedirectResponse(url="https://agent-id-monitor.vercel.app/bnb_agent_id_screen_studio.mp4", status_code=302)


# Local development only: serve the frontend files from this directory.
workspace_dir = os.path.dirname(os.path.abspath(__file__))
if not os.environ.get("RENDER") and os.path.exists(os.path.join(workspace_dir, "index.html")):
    app.mount("/static", StaticFiles(directory=workspace_dir, html=False), name="static")


def start_api(port=8080):
    """Runs the API under uvicorn."""
    print(f"[🌐 FastAPI & Uvicorn API Server] Serving on http://0.0.0.0:{port}...", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    start_api(port=port)

