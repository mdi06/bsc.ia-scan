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
from typing import Optional, List
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

import monitor
from db import db

app = FastAPI(
    title="Agent ID Monitor Backend",
    version="2.0.0",
    description="High-performance async FastAPI backend powered by Supabase / SQLite WAL"
)

# CORS Middleware for Vercel, Render & cross-origin frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
def get_virtual_history_json():
    """Virtual high-speed endpoint serving full cached project list (eliminates 1MB disk read)."""
    projects = db.get_all_projects()
    return JSONResponse(
        content=projects,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


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

