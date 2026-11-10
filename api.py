#!/usr/bin/env python3
"""
Dedicated High-Performance FastAPI Web Server (Consumer)
=========================================================
Runs purely as a non-blocking web server serving clients:
- Serves /healthz, /history.json, /api/projects, /api/favorites, /api/sync
- Serves static assets (index.html, app.js, style.css)
- Zero RPC polling or third-party API blocking: responds in < 5ms from cache/DB.
"""

import atexit
import logging
import os
import sys
import time
import json
import hashlib
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from fastapi import Depends, FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from posthog import Posthog, identify_context
from posthog.integrations.asgi import PosthogASGIMiddleware
import uvicorn

import monitor
from config import get_settings
from db import db

settings = get_settings()
posthog_client: Optional[Posthog] = None
posthog_log_provider: Optional[LoggerProvider] = None
posthog_log = logging.getLogger("posthog.exporter")
posthog_log.propagate = False

if settings.posthog_project_token and settings.posthog_host:
    posthog_client = Posthog(
        settings.posthog_project_token,
        host=settings.posthog_host,
        enable_exception_autocapture=True,
    )
    atexit.register(posthog_client.shutdown)

    posthog_log_provider = LoggerProvider(
        resource=Resource.create(
            {
                "service.name": "agent-id-backend",
                "deployment.environment": settings.environment,
            }
        )
    )
    posthog_log_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(
                endpoint=f"{settings.posthog_host.rstrip('/')}/i/v1/logs",
                headers={"Authorization": f"Bearer {settings.posthog_project_token}"},
            )
        )
    )
    posthog_log.addHandler(LoggingHandler(logger_provider=posthog_log_provider))
    posthog_log.setLevel(logging.INFO)
elif settings.environment != "production":
    missing_variable = (
        "POSTHOG_PROJECT_TOKEN"
        if not settings.posthog_project_token
        else "POSTHOG_HOST"
    )
    print(
        f"{missing_variable} variable required by PostHog is missing or un-configured, "
        f"this causes events to be silently missed. This error stops appearing once "
        f"{missing_variable} is configured",
        file=sys.stderr,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if posthog_client:
        try:
            posthog_client.flush()
        except Exception:
            pass
    if posthog_log_provider:
        try:
            posthog_log_provider.force_flush(timeout_millis=5000)
        except Exception:
            pass
        finally:
            try:
                posthog_log_provider.shutdown()
            except Exception:
                pass


app = FastAPI(
    title="Agent ID Monitor Backend",
    version="2.0.0",
    description="High-performance async FastAPI backend powered by Supabase / SQLite WAL",
    lifespan=lifespan,
)

if posthog_client:
    app.add_middleware(PosthogASGIMiddleware, client=posthog_client)

# GZip compression middleware: shrinks JSON/text responses by 80-90% to protect Render 5GB bandwidth
app.add_middleware(GZipMiddleware, minimum_size=500)

# CORS Middleware for Vercel, Render & cross-origin frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def run_api_memory_guard():
    """Periodically trims heap memory and runs garbage collection in the API process."""
    import gc
    while True:
        time.sleep(300)
        gc.collect()
        try:
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass


BOGUS_SCAM_CONTRACTS = [
    "0xd0523ce816e2b404db5633d181572cded33d7fa9",  # PrinterAi ($100T fake price on $0.0002 LP)
    "0xc5a5f6337bd9d1ee1a7367a804fce07094956922",  # The Bank ($99.7K fake price on $0.00 LP)
    "0x2170ed0880ac9a755fd29b2688956bd959f933f8",  # Binance-Peg ETH (Layer 1 crypto)
    "0x7425889fe94f9d693e8daefe88bcced6acfef4c0",  # Meta Platforms (METAB)
    "0xbe9d156892e55e7154bcd3cb0fea677f9d3103e1",  # SpaceX (SPCXB)
    "0x80106cb3ead06659a5ad19df39d9b4733863b9b0",  # Microsoft (MSFTB)
    "0x5b1910eaad6450e50f816082aa078c41f10c292f",  # Tesla, Inc. (TSLAB)
    "0x431a6136ee1b2f76378c8a14b53ef11ba9099b7a",  # Apple (AAPLB)
    "0x360d0b00c3c3a9d70e6e7372cfbe85e68b95973c",  # DogeZilla (Legacy meme clone with $0 mcap / sub-decimal display bug)
]
BOGUS_SET = set(c.lower() for c in BOGUS_SCAM_CONTRACTS)


@app.on_event("startup")
def startup_event():
    import threading
    t = threading.Thread(target=run_api_memory_guard, daemon=True, name="ApiMemoryGuard")
    t.start()
    purged = 0
    try:
        purged = db.delete_projects(BOGUS_SCAM_CONTRACTS)
        if purged:
            print(f"[FastAPI Startup] Automatically purged {purged} bogus scam/stock contracts from database", flush=True)
    except Exception as e:
        print(f"[FastAPI Startup Purge Error] {e}", flush=True)
    posthog_log.info(
        "api startup completed",
        extra={"event": "api_started", "purged_project_count": purged},
    )


@app.post("/api/admin/purge-bogus")
@app.delete("/api/projects/{contract}")
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
    """Virtual high-speed endpoint serving full pre-encoded JSON bytes with 304 ETag caching."""
    data = db.get_history_json_bytes()
    etag = f'"{hashlib.md5(data[:128] + str(len(data)).encode()).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
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
    c = client_etag.strip().lstrip("W/").strip('"')
    t = target_etag.strip().lstrip("W/").strip('"')
    return c == t


EXCLUDED_PEGS_SYMBOLS = {
    'BTCB', 'WBTC', 'BTC', 'ETH', 'WETH', 'WBNB', 'BNB', 'USDT', 'USDC', 'BUSD', 
    'DAI', 'TUSD', 'FDUSD', 'CAKE', 'U', 'OSK', 'FIST',
    'SOL', 'XRP', 'ADA', 'DOGE', 'DOT', 'AVAX', 'MATIC', 'POL', 'LINK', 'LTC', 'SHIB', 'UNI',
    'ZEC', 'BCH', 'XLM', 'EOS', 'ATOM', 'TRX', 'FIL', 'NEAR', 'XMR', 'ETC', 'APT', 'SUI', 'PEPE', 'FLOKI', 'DOGEZILLA'
}
EXCLUDED_PEGS_CONTRACTS = {
    '0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c',
    '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c',
    '0x55d398326f99059ff775485246999027b3197955',
    '0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d',
    '0xe9e7cea3dedca5984780bafc599bd69add087d56',
    '0x2170ed0880ac9a755fd29b2688956bd959f933f8',
    '0x14016e85a25a55f2e084741d69d9d82361f38e2b',
    '0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82'
}
SYNTHETIC_EQUITY_SYMBOLS = {
    'TSLAB', 'AAPLB', 'MSFTB', 'METAB', 'SPCXB', 'NVDA', 'NVDAB', 'AMZNB', 'GOOGLB', 'MRNAB', 'SOXSB', 'CRCLB', 'QQQB', 'MSTRB'
}

def get_sanitized_mcap(d: dict) -> float:
    if not d:
        return 0.0
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

def is_secondary_market_asset(d: dict) -> bool:
    if not d:
        return False
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


@app.get("/api/feed")
def get_feed(
    request: Request,
    limit: int = Query(150, ge=1, le=500),
    offset: int = Query(0, ge=0),
    since_block: Optional[int] = Query(None),
    filter: Optional[str] = Query(None)
):
    """
    Lightweight, high-speed feed endpoint for initial UI render (< 50ms) and delta updates.
    Includes Bandwidth Guard: HTTP 304 Not Modified & tiny delta payloads to preserve free hosting.
    """
    raw_projs = db.get_all_projects()
    all_projs = [p for p in raw_projs if (p.get("contract") or "").lower() not in BOGUS_SET]
    total = len(all_projs)
    latest_block = all_projs[0].get("block") if all_projs else None

    # Pre-calculated filter stats so frontend doesn't need all 10,000 drops just to show counts
    smart_count = sum(1 for d in all_projs if (d.get("smart_wallets") or d.get("smart_minters")))
    sniper_ready_count = sum(1 for d in all_projs if d.get("sniper_ready"))
    proxy_count = sum(1 for d in all_projs if d.get("is_proxy_clone"))
    nft_surge_count = sum(1 for d in all_projs if d.get("is_nft_surge"))
    secondary_count = sum(1 for d in all_projs if is_secondary_market_asset(d))

    agent_id_stats = monitor.load_agent_id_stats()
    total_agent_wallets = int(agent_id_stats.get("total_created_wallets", 175842))

    stats = {
        "total": total,
        "smart": smart_count,
        "sniper_ready": sniper_ready_count,
        "proxy": proxy_count,
        "nft_surge": nft_surge_count,
        "secondary": secondary_count,
        "latest_block": latest_block,
        "agent_wallets": total_agent_wallets
    }

    client_etag = request.headers.get("if-none-match")

    if since_block is not None:
        # Delta mode: only return drops created after this block!
        delta = [p for p in all_projs if int(p.get("block") or 0) > since_block]

        if not delta:
            delta_etag = f'"{total}-delta-{since_block}"'
            # Bandwidth Guard 1: HTTP 304 Not Modified (0 bytes body) if client already has this state
            if _etag_matches(client_etag, delta_etag):
                return Response(status_code=304, headers={"ETag": delta_etag, "Cache-Control": "public, max-age=15"})
            # Bandwidth Guard 2: Minimal 0-drop payload without redundant nested stats (~80 bytes vs 20 KB)
            return JSONResponse(
                status_code=200,
                headers={"ETag": delta_etag, "Cache-Control": "public, max-age=15"},
                content={
                    "success": True,
                    "mode": "delta",
                    "total": total,
                    "count": 0,
                    "latest_block": latest_block,
                    "agent_wallets": total_agent_wallets,
                    "projects": []
                }
            )

        delta_etag = f'"{latest_block}-{total}-delta-{since_block}"'
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
                "projects": delta,
                "stats": stats
            }
        )

    # Filter target list if requested
    target_list = all_projs
    if filter == "secondary":
        target_list = [d for d in all_projs if is_secondary_market_asset(d)]
        target_list.sort(key=lambda x: get_sanitized_mcap(x), reverse=True)
    elif filter == "smart":
        target_list = [d for d in all_projs if (d.get("smart_wallets") or d.get("smart_minters"))]
    elif filter == "sniper_ready":
        target_list = [d for d in all_projs if d.get("sniper_ready")]
    elif filter == "proxy":
        target_list = [d for d in all_projs if d.get("is_proxy_clone")]
    elif filter == "nft_surge":
        target_list = [d for d in all_projs if d.get("is_nft_surge")]

    target_total = len(target_list)
    paginated = target_list[offset : offset + limit]

    feed_etag = f'"{latest_block}-{total}-{offset}-{limit}-{filter or ""}"'
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
    """
    Dedicated endpoint returning saved persistent top post-mint gainers / Hall of Fame markets.
    Zero-overhead: reads from cached persistent memory without scanning 2k+ projects.
    """
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
    """
    Dedicated endpoint returning top smart wallet hunters and win rates from cached persistent memory.
    """
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
    """
    Dedicated endpoint returning historical smart money drops for instantaneous hunter activity tracking.
    """
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
    """Modernized REST endpoint with query filtering, pagination, and sorting."""
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
        res["projects"] = [p for p in res["projects"] if (p.get("contract") or "").lower() not in BOGUS_SET]
    return res


async def get_connected_wallet(request: Request) -> str:
    """Identify the current request by its validated connected-wallet id."""
    if request.method == "GET":
        wallet = request.query_params.get("wallet", "")
    else:
        try:
            incoming = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        wallet = incoming.get("wallet", "") if isinstance(incoming, dict) else ""

    wallet = str(wallet).lower().strip()
    if not wallet or not wallet.startswith("0x") or len(wallet) != 42:
        detail = (
            "Valid 0x... BSC wallet address required in ?wallet= parameter"
            if request.method == "GET"
            else "Valid 0x... BSC wallet address required"
        )
        raise HTTPException(status_code=400, detail=detail)

    if posthog_client:
        identify_context(wallet)
    return wallet


@app.get("/api/favorites")
@app.get("/favorites")
def get_favorites(wallet: str = Depends(get_connected_wallet)):
    record = db.get_favorites(wallet)
    return {
        "success": True,
        "wallet": wallet,
        "favorites": record.get("favorites", []),
        "updated_at": record.get("updated_at", 0)
    }


@app.post("/api/favorites")
@app.post("/favorites")
async def post_favorites(
    request: Request, wallet: str = Depends(get_connected_wallet)
):
    incoming = await request.json()
    raw_favs = incoming.get("favorites", [])
    if not isinstance(raw_favs, list):
        raw_favs = []
    clean_favs = [c.lower() for c in raw_favs if isinstance(c, str) and c.startswith("0x") and len(c) == 42]
    clean_favs = list(dict.fromkeys(clean_favs))
    ts = int(incoming.get("timestamp") or time.time())

    saved = db.save_favorites(wallet, clean_favs, ts)

    if posthog_client:
        posthog_client.capture(
            "favorites_saved",
            properties={"favorites_count": len(clean_favs)},
        )
    posthog_log.info(
        "favorites saved",
        extra={"event": "favorites_saved", "favorites_count": len(clean_favs)},
    )

    return {
        "success": True,
        "wallet": wallet,
        "favorites": clean_favs,
        "updated_at": ts
    }


@app.post("/api/sync")
@app.post("/sync")
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
        posthog_log.info(
            "history synchronized",
            extra={"event": "history_synchronized", "project_count": len(new_history)},
        )
        return {"success": True, "count": len(new_history)}
    raise HTTPException(status_code=400, detail="Expected JSON array of projects")


@app.get("/")
def root_handler(request: Request):
    """
    On Render cloud production, redirect human visitors and search crawlers to Vercel (https://agent-id-monitor.vercel.app).
    Vercel has 100 GB global edge CDN caching, saving Render's limited 5 GB monthly bandwidth.
    """
    accept = request.headers.get("accept", "")
    host = request.headers.get("host", "")
    if os.environ.get("RENDER") or "onrender.com" in host or "fly.dev" in host:
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
    """Redirect 7.5 MB video assets to Vercel CDN instead of streaming through Render's 5 GB quota."""
    return RedirectResponse(url="https://agent-id-monitor.vercel.app/bnb_agent_id_screen_studio.mp4", status_code=302)


# Mount static files at root if in local development mode
workspace_dir = os.path.dirname(os.path.abspath(__file__))
if not os.environ.get("RENDER") and os.path.exists(os.path.join(workspace_dir, "index.html")):
    app.mount("/static", StaticFiles(directory=workspace_dir, html=False), name="static")


def start_api(port=8080):
    """Starts the FastAPI / Uvicorn server."""
    print(f"[🌐 FastAPI & Uvicorn API Server] Serving on http://0.0.0.0:{port}...", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    start_api(port=port)

