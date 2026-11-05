#!/usr/bin/env python3
"""
Dedicated High-Performance FastAPI Web Server (Consumer)
=========================================================
Runs purely as a non-blocking web server serving clients:
- Serves /healthz, /history.json, /api/projects, /api/favorites, /api/sync
- Serves static assets (index.html, app.js, style.css)
- Zero RPC polling or third-party API blocking: responds in < 5ms from cache/DB.
"""

import os
import sys
import time
import json
import hashlib
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

import monitor
from db import db

app = FastAPI(
    title="Agent ID Monitor Backend",
    version="2.0.0",
    description="High-performance async FastAPI backend powered by Supabase / SQLite WAL"
)

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


@app.on_event("startup")
def startup_event():
    import threading
    t = threading.Thread(target=run_api_memory_guard, daemon=True, name="ApiMemoryGuard")
    t.start()


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


@app.get("/api/feed")
def get_feed(
    limit: int = Query(150, ge=1, le=500),
    offset: int = Query(0, ge=0),
    since_block: Optional[int] = Query(None),
    filter: Optional[str] = Query(None)
):
    """
    Lightweight, high-speed feed endpoint for initial UI render (< 50ms) and delta updates.
    Returns the newest drops, total counts, and filter stats in a tiny payload (< 100 KB).
    """
    all_projs = db.get_all_projects()
    total = len(all_projs)
    latest_block = all_projs[0].get("block") if all_projs else None

    # Pre-calculated filter stats so frontend doesn't need all 10,000 drops just to show counts
    smart_count = sum(1 for d in all_projs if (d.get("smart_wallets") or d.get("smart_minters")))
    sniper_ready_count = sum(1 for d in all_projs if d.get("sniper_ready"))
    proxy_count = sum(1 for d in all_projs if d.get("is_proxy_clone"))
    nft_surge_count = sum(1 for d in all_projs if d.get("is_nft_surge"))

    stats = {
        "total": total,
        "smart": smart_count,
        "sniper_ready": sniper_ready_count,
        "proxy": proxy_count,
        "nft_surge": nft_surge_count,
        "latest_block": latest_block
    }

    if since_block is not None:
        # Delta mode: only return drops created after this block!
        delta = [p for p in all_projs if int(p.get("block") or 0) > since_block]
        return {
            "success": True,
            "mode": "delta",
            "total": total,
            "count": len(delta),
            "latest_block": latest_block,
            "projects": delta,
            "stats": stats
        }

    # Filter target list if requested
    target_list = all_projs
    if filter == "smart":
        target_list = [d for d in all_projs if (d.get("smart_wallets") or d.get("smart_minters"))]
    elif filter == "sniper_ready":
        target_list = [d for d in all_projs if d.get("sniper_ready")]
    elif filter == "proxy":
        target_list = [d for d in all_projs if d.get("is_proxy_clone")]
    elif filter == "nft_surge":
        target_list = [d for d in all_projs if d.get("is_nft_surge")]

    target_total = len(target_list)
    paginated = target_list[offset : offset + limit]
    return {
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
    return db.get_projects(
        limit=limit,
        offset=offset,
        search=search,
        status=status,
        sniper_ready=sniper_ready,
        min_progress=min_progress,
        sort_by=sort_by,
        order=order
    )


@app.get("/api/favorites")
@app.get("/favorites")
def get_favorites(wallet: str = Query(..., description="0x... BSC wallet address")):
    w = (wallet or "").lower().strip()
    if not w or not w.startswith("0x") or len(w) != 42:
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


@app.post("/api/favorites")
@app.post("/favorites")
async def post_favorites(request: Request):
    try:
        incoming = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    wallet = (incoming.get("wallet") or "").lower().strip()
    if not wallet or not wallet.startswith("0x") or len(wallet) != 42:
        raise HTTPException(status_code=400, detail="Valid 0x... BSC wallet address required")

    raw_favs = incoming.get("favorites", [])
    if not isinstance(raw_favs, list):
        raw_favs = []
    clean_favs = [c.lower() for c in raw_favs if isinstance(c, str) and c.startswith("0x") and len(c) == 42]
    clean_favs = list(dict.fromkeys(clean_favs))
    ts = int(incoming.get("timestamp") or time.time())

    saved = db.save_favorites(wallet, clean_favs, ts)

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
        return {"success": True, "count": len(new_history)}
    raise HTTPException(status_code=400, detail="Expected JSON array of projects")


# Mount static files at root if available
workspace_dir = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(workspace_dir, "index.html")):
    app.mount("/", StaticFiles(directory=workspace_dir, html=True), name="static")


def start_api(port=8080):
    """Starts the FastAPI / Uvicorn server."""
    print(f"[🌐 FastAPI & Uvicorn API Server] Serving on http://0.0.0.0:{port}...", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    start_api(port=port)

