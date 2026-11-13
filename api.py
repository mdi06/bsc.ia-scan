#!/usr/bin/env python3
"""
FastAPI web server (read side).

Serves /healthz, /history.json, /api/feed, /api/projects, /api/favorites and /api/sync from the
db.py cache, plus the static frontend in local development. It does no chain scanning; that is
worker.py's job.
"""

import json
import math
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

import filters
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

def sanitize_non_finite(obj):
    """Deep copy of `obj` with NaN / +-Infinity floats replaced by None. A non-finite float is not valid JSON:
    one of them in one record used to make /api/feed answer HTTP 500 for the whole page."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: sanitize_non_finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_non_finite(v) for v in obj]
    return obj


class SafeJSONResponse(JSONResponse):
    """JSONResponse that turns non-finite floats into null instead of raising (allow_nan=False makes the stock
    class fail with ValueError). The fast path costs nothing extra: the clean payload is encoded once."""

    def render(self, content) -> bytes:
        try:
            return super().render(content)
        except ValueError:
            return super().render(sanitize_non_finite(content))


_sanitized_history_cache = {"etag": None, "bytes": None}


def sanitize_history_bytes(data: bytes, etag: str) -> bytes:
    """/history.json is pre-encoded by db.py with json.dumps, which writes NaN / Infinity as bare tokens that
    JSON.parse in the browser rejects. Re-encode only when such a token is present (cached per ETag)."""
    if b"NaN" not in data and b"Infinity" not in data:
        return data
    if _sanitized_history_cache["etag"] == etag and _sanitized_history_cache["bytes"] is not None:
        return _sanitized_history_cache["bytes"]
    cleaned = json.dumps(sanitize_non_finite(json.loads(data)), separators=(",", ":"), allow_nan=False).encode("utf-8")
    _sanitized_history_cache.update(etag=etag, bytes=cleaned)
    return cleaned



app = FastAPI(
    title="Agent ID Monitor Backend",
    version="2.0.0",
    description="High-performance async FastAPI backend powered by Supabase / SQLite WAL",
    default_response_class=SafeJSONResponse,
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
        return SafeJSONResponse(
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
    return SafeJSONResponse(
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
        content=sanitize_history_bytes(data, etag),
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
    return filters.is_excluded_asset(d)


def get_sanitized_mcap(d: dict):
    """Market cap to show, or None (unknown / excluded / implausible). The one rule shared with app.js."""
    return filters.sanitized_mcap(d)


def is_secondary_market_asset(d: dict) -> bool:
    return filters.is_secondary_market(d)


# Predicates behind /api/feed?filter=<name>; also used for the per-filter counts in the overview stats.
FEED_FILTERS = {
    "secondary": is_secondary_market_asset,
    "smart": lambda d: d.get("smart_wallets") or d.get("smart_minters"),
    "sniper_ready": lambda d: d.get("sniper_ready"),
    "proxy": lambda d: d.get("is_proxy_clone"),
    "nft_surge": lambda d: d.get("is_nft_surge"),
}


PULSE_WINDOW_SEC = 24 * 3600


def compute_pulse_24h(projects, now=None) -> dict:
    """24h Pulse aggregates over the FULL dataset (not whatever page the browser has loaded).

    The window is time based: records whose `detected_at` ("YYYY-MM-DD HH:MM:SS", the server's local clock, the
    same clock that wrote it) is within the last 24 hours. `since` is that cutoff in the same format, so clients
    can filter their own lists with a plain string comparison and no timezone guessing."""
    now = time.time() if now is None else now
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - PULSE_WINDOW_SEC))
    new_drops = graduated = smart_inflows = clusters = clean = refunds = threats = 0
    dex_volume = 0.0
    hunters: Dict[str, int] = {}
    for d in projects:
        if not isinstance(d, dict) or is_bogus_or_synthetic_asset(d):
            continue
        if str(d.get("detected_at") or "") < since:
            continue
        new_drops += 1
        if is_secondary_market_asset(d) or d.get("is_graduated"):
            graduated += 1
        dex_volume += filters._num(d.get("volume_24h"))

        wallets = d.get("smart_wallets") or d.get("smart_minters") or []
        if wallets:
            smart_inflows += len(wallets)
            if len(wallets) >= 3:
                clusters += 1
            for w in wallets:
                key = str(w).lower()
                hunters[key] = hunters.get(key, 0) + 1

        level = str(d.get("risk_level") or "SAFE").upper()
        badge = str(d.get("risk_badge") or "").upper()
        if any(k in badge for k in ("HONEYPOT", "SERIAL", "DRAIN")) or level in ("RUG", "HIGH"):
            threats += 1
        else:
            clean += 1
        if isinstance(d.get("security"), dict) and d["security"].get("has_refund"):
            refunds += 1

    top_hunter, top_count = None, 0
    for w, cnt in hunters.items():
        if cnt > top_count:
            top_hunter, top_count = w, cnt
    return {
        "window_hours": 24,
        "since": since,
        "as_of": int(now),
        "new_drops": new_drops,
        "graduated": graduated,
        "dex_volume_usd": dex_volume,
        "smart_inflows": smart_inflows,
        "smart_clusters": clusters,
        "top_hunter": top_hunter,
        "top_hunter_count": top_count,
        "clean_mints": clean,
        "refunds": refunds,
        "threats_blocked": threats,
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
            "agent_wallets_block": agent_wallets_block,
            "pulse_24h": compute_pulse_24h(all_projs),
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
            "agent_wallets_block": 0,
            "pulse_24h": None,
        }


# Main route: /api/stats. /api/overview and /api/summary are aliases kept for external clients.
@app.get("/api/stats")
@app.get("/api/overview")
@app.get("/api/summary")
def get_overview_stats(request: Request):
    """Summary counts for the overview header, with ETag/304 support."""
    stats = compute_global_overview_stats()
    pulse = stats.get("pulse_24h") or {}
    pulse_tag = f'{pulse.get("new_drops")}.{pulse.get("graduated")}.{round(pulse.get("dex_volume_usd") or 0)}.{pulse.get("smart_inflows")}.{pulse.get("threats_blocked")}'
    stats_etag = f'"v{db.content_version()}-{stats["total"]}-{stats["agent_wallets"]}-{stats.get("agent_wallets_block")}-{stats.get("latest_block")}-{pulse_tag}"'
    client_etag = request.headers.get("if-none-match")
    if _etag_matches(client_etag, stats_etag):
        return Response(status_code=304, headers={"ETag": stats_etag, "Cache-Control": "public, max-age=5"})
    return SafeJSONResponse(
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
    return SafeJSONResponse(
        content={"success": True, "bnb_price_usd": price, "live": monitor.bnb_price_is_live(),
                 "as_of": monitor.bnb_price_as_of()},
        headers={"Cache-Control": "public, max-age=30"},
    )


MARKET_FIELDS = ("token_price_usd", "market_cap", "fdv", "lp_usd", "volume_24h", "price_change_24h",
                 "buys_24h", "sells_24h", "buys_1h")
# Freshness of those numbers (worker: market_updated_at / market_stale / market_source).
MARKET_FRESHNESS_FIELDS = ("market_stale", "market_updated_at", "market_source")


@app.get("/api/market-data")
def get_market_data():
    """Latest market numbers for every tracked contract that has a price or liquidity, keyed by address,
    plus the contracts whose market data the worker cleared as stale (fields None, market_stale True).

    Not filtered by the feed's exclusion lists on purpose: the frontend uses this to refresh the static
    seeded files (seeded_smart_drops.json), which contain drops that /api/feed never returns. Stale
    contracts are included so a seeded drop does not keep its frozen numbers once the live record is cleared."""
    out = {}
    for p in db.get_all_projects():
        c = (p.get("contract") or "").lower()
        if c and (p.get("token_price_usd") or p.get("lp_usd") or p.get("market_stale")):
            out[c] = {k: p.get(k) for k in MARKET_FIELDS + MARKET_FRESHNESS_FIELDS}
    return SafeJSONResponse(content=out, headers={"Cache-Control": "public, max-age=60"})


@app.get("/agent_id_stats.json")
@app.get("/data/agent_id_stats.json")
def get_agent_id_stats_json():
    """Agent ID counter, served at the paths the static frontend falls back to."""
    local_st = monitor.load_agent_id_stats() or {}
    cloud_st = db.get_agent_id_stats() or {}
    stats = cloud_st if int(cloud_st.get("total_created_wallets") or 0) > int(local_st.get("total_created_wallets") or 0) else local_st
    return SafeJSONResponse(
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
    content_version = db.content_version()   # read after the (possibly refreshing) load: the ETag follows the data served
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
            delta_etag = f'"v{content_version}-{total}-{total_agent_wallets}-delta-{since_block}"'
            if _etag_matches(client_etag, delta_etag):
                return Response(status_code=304, headers={"ETag": delta_etag, "Cache-Control": "public, max-age=5"})
            # No new drops: send a minimal payload without the nested stats.
            return SafeJSONResponse(
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

        delta_etag = f'"v{content_version}-{latest_block}-{total}-{total_agent_wallets}-delta-{since_block}"'
        return SafeJSONResponse(
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
            target_list.sort(key=lambda x: get_sanitized_mcap(x) or 0.0, reverse=True)

    target_total = len(target_list)
    paginated = target_list[offset : offset + limit]

    feed_etag = f'"v{content_version}-{latest_block}-{total}-{total_agent_wallets}-{offset}-{limit}-{filter or ""}"'
    if _etag_matches(client_etag, feed_etag):
        return Response(status_code=304, headers={"ETag": feed_etag, "Cache-Control": "public, max-age=5"})

    return SafeJSONResponse(
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


TOP_GAINERS_LIMIT = 50
# Record fields the frontend's leaderboard code reads from `item` (isValidTopMarketItem, modals, links).
TOP_GAINER_ITEM_FIELDS = (
    "contract", "name", "symbol", "standard", "market_url", "pair_url", "token_price_usd", "floor_price_usd",
    "floor_price_bnb", "ath_price_usd", "ath_profit_usd", "ath_multiplier", "mint_price_str", "mint_price_bnb",
    "mint_value_wei", "has_free_mint", "mint_calldata", "total_minted", "max_supply", "lp_usd", "holders",
    "block", "detected_at", "market_stale", "market_updated_at",
)


def compute_live_top_gainers(projects, limit: int = TOP_GAINERS_LIMIT) -> list:
    """Top post-mint gainers from the CURRENT records, ranked by the ATH multiplier.

    Only records whose mint cost is known (a paid mint, or a verified free one) and whose ATH profit is positive
    qualify (monitor.update_post_mint_profit_metrics decides, the same rules app.js uses). A free mint has no
    multiplier, so it ranks after every paid one, by ATH profit."""
    rows = []
    for p in projects:
        if not isinstance(p, dict) or is_bogus_or_synthetic_asset(p):
            continue
        d = dict(p)
        try:
            info = monitor.update_post_mint_profit_metrics(d)
        except Exception:
            continue
        ath_profit = d.get("ath_profit_usd")
        ath_price = d.get("ath_price_usd")
        if info["mint_usd"] is None or ath_profit is None or ath_profit <= 0 or not ath_price or ath_price <= 0:
            continue
        mult = d.get("ath_multiplier")
        rows.append({
            "name": d.get("name") or "Unknown",
            "symbol": d.get("symbol") or "",
            "contract": d.get("contract") or "",
            "standard": d.get("standard") or "",
            "secUsd": info["sec_usd"],
            "mintUsd": info["mint_usd"],
            "profit": info["profit"],
            "athPrice": ath_price,
            "athProfit": ath_profit,
            "multiplier": mult,
            "isSold": bool(info["is_sold"]),
            "isFree": bool(info["is_free"]),
            "holders": int(filters._num(d.get("holders"))),
            "item": {k: d[k] for k in TOP_GAINER_ITEM_FIELDS if k in d},
        })
    rows.sort(key=lambda r: (r["multiplier"] is not None, r["multiplier"] or 0.0, r["athProfit"]), reverse=True)
    return rows[:limit]


def _seed_file(name: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def _seed_as_of(path: str):
    """When the seed file was last written (unix seconds), the best "as of" the file itself offers."""
    try:
        return int(os.path.getmtime(path))
    except OSError:
        return None


def _load_seed(name: str):
    path = _seed_file(name)
    if not os.path.exists(path):
        return None, None
    with open(path, "r") as f:
        return json.load(f), _seed_as_of(path)


@app.get("/api/top-gainers")
def get_top_gainers():
    """Top post-mint gainers (Hall of Fame), computed live from the current records.

    Falls back to seeded_top_gainers.json only when no record qualifies yet; that response says so
    (`seeded: true`, `as_of` = the file's date) so the UI can label it."""
    try:
        monitor.refresh_bnb_price()
        live = compute_live_top_gainers(db.get_all_projects())
    except Exception as e:
        print(f"[⚠️ Top Gainers] live computation failed: {e}", flush=True)
        live = []
    if live:
        return {"success": True, "count": len(live), "gainers": live, "seeded": False,
                "as_of": int(time.time()), "bnb_price_live": monitor.bnb_price_is_live()}
    try:
        data, as_of = _load_seed("seeded_top_gainers.json")
    except Exception as e:
        return {"success": False, "error": str(e), "gainers": [], "seeded": True, "as_of": None}
    data = data or []
    return {"success": True, "count": len(data), "gainers": data, "seeded": True, "as_of": as_of}


@app.get("/api/smart-hunters")
def get_smart_hunters():
    """Smart-wallet hunters and win rates. Not computed live yet: served from seeded_smart_hunters.json and
    labelled as a seed (`seeded: true`, `as_of`). `items` and the older `hunters` key carry the same list."""
    try:
        data, as_of = _load_seed("seeded_smart_hunters.json")
    except Exception as e:
        return {"success": False, "error": str(e), "hunters": [], "items": [], "seeded": True, "as_of": None}
    data = data or []
    return {"success": True, "count": len(data), "seeded": True, "as_of": as_of, "items": data, "hunters": data}


@app.get("/api/smart-drops")
def get_smart_drops(shape: Optional[str] = Query(None)):
    """Historical smart-money drops. Not computed live yet: served from seeded_smart_drops.json, wrapped as
    {seeded: true, as_of, items}. `?shape=array` returns the old bare JSON array (compatibility path)."""
    try:
        data, as_of = _load_seed("seeded_smart_drops.json")
    except Exception as e:
        return SafeJSONResponse({"success": False, "error": str(e), "items": [], "drops": [], "seeded": True}, status_code=500)
    data = data or []
    if shape == "array":
        return SafeJSONResponse(content=data, headers={"Cache-Control": "public, max-age=300"})
    return SafeJSONResponse(
        content={"success": True, "count": len(data), "seeded": True, "as_of": as_of, "items": data, "drops": data},
        headers={"Cache-Control": "public, max-age=300"},
    )


# ----------------------------------------------------------------------------------------- /api/projects
SOLD_OUT_PCT = 99.9


def derive_progress(p: dict):
    """Mint progress in percent: total_minted / max_supply when the cap is real (not the 1e20+ "uncapped"
    sentinel), else a stored `progress`, else None. The worker never wrote `progress` into the record, so
    `?min_progress=` used to match nothing."""
    try:
        ms = float(p.get("max_supply") or 0.0)
        tm = p.get("total_minted")
        if tm is not None and 0.0 < ms < 1e20:
            return max(0.0, min(100.0, float(tm) / ms * 100.0))
    except (TypeError, ValueError):
        pass
    stored = p.get("progress")
    if isinstance(stored, (int, float)) and not isinstance(stored, bool) and math.isfinite(stored) and stored > 0:
        return float(stored)
    return None


def derive_status(p: dict, progress=None) -> str:
    """SOLD_OUT (the real cap is reached), TRADING (it trades on a DEX / Element), MINTING (progress below the
    cap), else TRACKING. An explicit non-default `status` stored on the record is kept."""
    stored = str(p.get("status") or "")
    if stored and stored.upper() != "TRACKING":
        return stored
    if progress is None:
        progress = derive_progress(p)
    if progress is not None and progress >= SOLD_OUT_PCT:
        return "SOLD_OUT"
    if is_secondary_market_asset(p):
        return "TRADING"
    if progress is not None and progress > 0:
        return "MINTING"
    return "TRACKING"


def with_derived_fields(p: dict) -> dict:
    """A copy of the record with `progress` and `status` filled in from its own data."""
    progress = derive_progress(p)
    out = dict(p)
    out["progress"] = progress
    out["status"] = derive_status(p, progress)
    return out


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
    """Project list with search, status/sniper/progress filters, sorting and pagination.

    Bogus / pegged / synthetic assets are removed FIRST, so `total` and `count` describe the list that is
    actually returned. `status` and `progress` are derived from each record (see derive_status)."""
    projs = [with_derived_fields(p) for p in db.get_all_projects() if isinstance(p, dict) and not is_bogus_or_synthetic_asset(p)]

    if search:
        q = search.lower().strip()
        projs = [p for p in projs if q in (p.get("name") or "").lower() or q in (p.get("symbol") or "").lower()
                 or q in (p.get("contract") or "").lower()]
    if status:
        st = status.lower().strip()
        projs = [p for p in projs if str(p.get("status") or "").lower() == st]
    if sniper_ready is not None:
        projs = [p for p in projs if bool(p.get("sniper_ready")) == sniper_ready]
    if min_progress is not None:
        projs = [p for p in projs if p["progress"] is not None and p["progress"] >= min_progress]

    reverse = order.lower() == "desc"
    if sort_by == "progress":
        known = sorted([p for p in projs if p["progress"] is not None], key=lambda x: x["progress"], reverse=reverse)
        projs = known + [p for p in projs if p["progress"] is None]          # unknown progress always last
    elif sort_by == "holders":
        projs.sort(key=lambda x: int(filters._num(x.get("holders"))), reverse=reverse)
    elif sort_by == "score":
        projs.sort(key=lambda x: filters._num(x.get("score")), reverse=reverse)
    else:
        projs.sort(key=lambda x: int(filters._num(x.get("block"))), reverse=reverse)

    total = len(projs)
    page = projs[offset: offset + limit]
    return {"success": True, "total": total, "count": len(page), "offset": offset, "limit": limit, "projects": page}


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

