"""
Storage & Database Modernization Layer for Agent ID Monitor.
Supports:
1. Supabase (Remote PostgreSQL via PostgREST REST API) - Perfect for Render Free (no data loss on reboot)
2. SQLite (WAL Mode, Thread-Safe) - Native fallback for local development and offline environments
"""

import os
import json
import time
import sqlite3
import threading
from typing import List, Dict, Any, Optional
from pathlib import Path

# Try importing httpx for Supabase REST calls; fallback to urllib if needed
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    import urllib.request
    import urllib.parse
    HAS_HTTPX = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "agent_monitor.db")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
FAVORITES_FILE = os.path.join(BASE_DIR, "user_favorites.json")


class DatabaseManager:
    def __init__(self, db_path: Optional[str] = None):
        self.supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        if self.supabase_url.endswith("/rest/v1"):
            self.supabase_url = self.supabase_url[:-8].rstrip("/")
        self.supabase_key = os.getenv("SUPABASE_KEY", os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_ANON_KEY", ""))).strip()
        
        self.mode = "supabase" if (self.supabase_url and self.supabase_key) else "sqlite"
        self.db_path = db_path or os.getenv("DB_PATH", DEFAULT_DB_PATH)
        
        # Thread locks for in-memory and local SQLite operations
        self._lock = threading.RLock()
        self._cache_lock = threading.RLock()
        
        # In-memory fast cache: {contract_lower: project_dict}
        # 60s TTL: Supabase egress only triggers once per minute max instead of every 3s.
        # New drops upserted in-memory immediately so the UI stays fresh without polling.
        self._projects_cache: Dict[str, Dict[str, Any]] = {}
        self._p_fingerprints: Dict[str, str] = {}
        self._cache_initialized = False
        self._last_cache_time = 0.0
        self._last_synced_updated_at = 0
        self._cache_ttl = 60.0  # seconds — was 3s, caused ~1,200 Supabase hits/hr
        self._cached_json_bytes: Optional[bytes] = None
        self._cache_dirty = True

        # Process-safe HTTP client state for Supabase
        self._client_instance = None
        self._client_pid = None

        # Pre-seed in-memory cache from local history (0-byte egress on cold start)
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    seed_drops = json.load(f)
                if isinstance(seed_drops, list):
                    for d in seed_drops:
                        c = (d.get("contract") or "").lower()
                        if c:
                            self._projects_cache[c] = d
                    if self._projects_cache:
                        self._cache_initialized = True
            except Exception:
                pass

        if self.mode == "sqlite":
            self._init_sqlite()
        
        # Auto-seed on startup if needed
        self.seed_from_local_json()

    @property
    def client(self) -> Optional["httpx.Client"]:
        """Process-safe and thread-safe httpx.Client provider.
        Automatically recreates a fresh client if invoked in a newly forked child process.
        """
        if self.mode != "supabase" or not HAS_HTTPX:
            return None
        cur_pid = os.getpid()
        with self._lock:
            if getattr(self, "_client_pid", None) != cur_pid or getattr(self, "_client_instance", None) is None:
                old = getattr(self, "_client_instance", None)
                if old is not None:
                    try:
                        old.close()
                    except Exception:
                        pass
                self._client_instance = httpx.Client(
                    base_url=self.supabase_url,
                    headers={
                        "apikey": self.supabase_key,
                        "Authorization": f"Bearer {self.supabase_key}",
                        "Content-Type": "application/json",
                        "Prefer": "return=representation"
                    },
                    timeout=httpx.Timeout(10.0, connect=5.0, read=10.0, write=10.0),
                    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20)
                )
                self._client_pid = cur_pid
            return self._client_instance

    @property
    def _client(self):
        """Backward compatibility for any references to self._client."""
        return self.client

    # -------------------------------------------------------------------------
    # SQLite Initialization & Connection
    # -------------------------------------------------------------------------
    def _init_sqlite(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = self._get_sqlite_connection()
            try:
                cur = conn.cursor()
                # Enable WAL mode for zero-lock concurrent reads and writes
                cur.execute("PRAGMA journal_mode = WAL;")
                cur.execute("PRAGMA synchronous = NORMAL;")
                cur.execute("PRAGMA busy_timeout = 5000;")
                
                # Projects table
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS projects (
                        contract TEXT PRIMARY KEY,
                        name TEXT,
                        symbol TEXT,
                        token_id TEXT,
                        block INTEGER DEFAULT 0,
                        progress REAL DEFAULT 0.0,
                        holders INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'TRACKING',
                        risk_level TEXT DEFAULT 'SAFE',
                        risk_badge TEXT DEFAULT '🟢 Clean',
                        sniper_ready INTEGER DEFAULT 0,
                        is_proxy_clone INTEGER DEFAULT 0,
                        score REAL DEFAULT 0.0,
                        raw_data TEXT NOT NULL,
                        detected_at TEXT,
                        updated_at INTEGER DEFAULT 0
                    );
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_projects_block ON projects(block DESC);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_projects_progress ON projects(progress DESC);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_projects_sniper ON projects(sniper_ready);")
                
                # User Favorites table
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_favorites (
                        wallet TEXT PRIMARY KEY,
                        favorites TEXT NOT NULL,
                        updated_at INTEGER DEFAULT 0
                    );
                """)
                conn.commit()
            finally:
                conn.close()

    def _get_sqlite_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # -------------------------------------------------------------------------
    # Seeding & Migration
    # -------------------------------------------------------------------------
    def seed_from_local_json(self):
        """Seeds SQLite / Supabase from history.json and user_favorites.json if DB is empty."""
        count = self.get_project_count()
        if count == 0 and os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list) and len(data) > 0:
                    print(f"[{self.mode.upper()} DB] Auto-seeding {len(data)} projects from history.json...", flush=True)
                    self.upsert_projects(data)
            except Exception as e:
                print(f"[⚠️ Seed Warning] Failed to seed from {HISTORY_FILE}: {e}", flush=True)

        # Seed favorites if present
        if os.path.exists(FAVORITES_FILE):
            try:
                with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
                    fav_data = json.load(f)
                if isinstance(fav_data, dict):
                    for wallet, entry in fav_data.items():
                        if isinstance(entry, dict) and "favorites" in entry:
                            self.save_favorites(wallet, entry["favorites"], entry.get("updated_at"))
            except Exception as e:
                print(f"[⚠️ Seed Warning] Failed to seed favorites: {e}", flush=True)

    # -------------------------------------------------------------------------
    # Projects CRUD Operations
    # -------------------------------------------------------------------------
    def get_project_count(self) -> int:
        if self.mode == "supabase":
            try:
                if HAS_HTTPX and self._client:
                    resp = self._client.get("/rest/v1/projects?select=contract", headers={"Range": "0-0", "Prefer": "count=exact"})
                    content_range = resp.headers.get("content-range", "")
                    if "/" in content_range:
                        return int(content_range.split("/")[1])
                return len(self.get_all_projects())
            except Exception:
                return len(self._projects_cache)

        with self._lock:
            conn = self._get_sqlite_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM projects")
                row = cur.fetchone()
                return row[0] if row else 0
            except Exception:
                return 0
            finally:
                conn.close()

    def get_all_projects(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Returns all projects as dictionaries, sorted by block descending.
        Uses in-memory cache with incremental delta sync to avoid memory churn and re-allocations.
        """
        now = time.time()
        with self._cache_lock:
            if not force_refresh and self._cache_initialized and self._projects_cache and (now - self._last_cache_time < self._cache_ttl):
                items = list(self._projects_cache.values())
                items.sort(key=lambda x: x.get("block", 0) or 0, reverse=True)
                return items

        if self.mode == "supabase":
            try:
                client = self.client
                if HAS_HTTPX and client:
                    # 1. Quick 40-byte timestamp check if cache already holds data
                    if not force_refresh and self._cache_initialized and self._projects_cache:
                        check_resp = client.get("/rest/v1/projects?select=updated_at&order=updated_at.desc&limit=1")
                        if check_resp.status_code == 200:
                            top_records = check_resp.json()
                            if top_records:
                                latest_ts = int(top_records[0].get("updated_at") or 0)
                                if latest_ts <= self._last_synced_updated_at:
                                    # Zero new drops or updates: return existing in-memory cache with zero allocation
                                    with self._cache_lock:
                                        self._last_cache_time = now
                                        items = list(self._projects_cache.values())
                                        items.sort(key=lambda x: x.get("block", 0) or 0, reverse=True)
                                        return items

                    # 2. Only fetch delta (rows updated since last sync) or initial full load
                    if not self._cache_initialized or not self._projects_cache or force_refresh:
                        query_path = "/rest/v1/projects?select=*&order=block.desc&limit=10000"
                    else:
                        query_path = f"/rest/v1/projects?updated_at=gte.{self._last_synced_updated_at}&select=*&limit=10000"

                    resp = client.get(query_path)
                    if resp.status_code == 200:
                        records = resp.json()
                        with self._cache_lock:
                            max_ts = self._last_synced_updated_at
                            for r in records:
                                raw = r.get("data") or {}
                                if isinstance(raw, str):
                                    try:
                                        raw = json.loads(raw)
                                    except Exception:
                                        raw = {}
                                c = (r.get("contract") or raw.get("contract", "")).lower()
                                if c:
                                    raw["contract"] = c
                                    self._projects_cache[c] = raw
                                ts = int(r.get("updated_at") or 0)
                                if ts > max_ts:
                                    max_ts = ts
                            self._last_synced_updated_at = max_ts
                            self._cache_initialized = True
                            self._cache_dirty = True
                            self._last_cache_time = now
                            items = list(self._projects_cache.values())
                            items.sort(key=lambda x: x.get("block", 0) or 0, reverse=True)
                            return items
            except Exception as e:
                print(f"[⚠️ Supabase Read Error] {e}", flush=True)

        # Fallback / SQLite with incremental check
        with self._lock:
            conn = self._get_sqlite_connection()
            try:
                cur = conn.cursor()
                if not force_refresh and self._cache_initialized and self._projects_cache:
                    cur.execute("SELECT MAX(updated_at) FROM projects")
                    row = cur.fetchone()
                    latest_ts = row[0] if row and row[0] is not None else 0
                    if latest_ts <= self._last_synced_updated_at:
                        with self._cache_lock:
                            self._last_cache_time = now
                            items = list(self._projects_cache.values())
                            items.sort(key=lambda x: x.get("block", 0) or 0, reverse=True)
                            return items

                if not self._cache_initialized or not self._projects_cache or force_refresh:
                    cur.execute("SELECT raw_data, updated_at FROM projects ORDER BY block DESC")
                    rows = cur.fetchall()
                else:
                    cur.execute("SELECT raw_data, updated_at FROM projects WHERE updated_at >= ?", (self._last_synced_updated_at,))
                    rows = cur.fetchall()

                with self._cache_lock:
                    max_ts = self._last_synced_updated_at
                    for row in rows:
                        try:
                            p = json.loads(row["raw_data"])
                            c = (p.get("contract") or "").lower()
                            if c:
                                self._projects_cache[c] = p
                            ts = row["updated_at"] or 0
                            if ts > max_ts:
                                max_ts = ts
                        except Exception:
                            continue
                    self._last_synced_updated_at = max_ts
                    self._cache_initialized = True
                    self._cache_dirty = True
                    self._last_cache_time = now
                    items = list(self._projects_cache.values())
                    items.sort(key=lambda x: x.get("block", 0) or 0, reverse=True)
                    return items
            finally:
                conn.close()

    def get_history_json_bytes(self) -> bytes:
        """Returns pre-encoded, minified UTF-8 JSON bytes of all projects.
        Caches the bytes in memory to avoid repetitive json.dumps allocations during HTTP requests.
        """
        self.get_all_projects()
        with self._cache_lock:
            if not self._cached_json_bytes or self._cache_dirty:
                items = list(self._projects_cache.values())
                items.sort(key=lambda x: x.get("block", 0) or 0, reverse=True)
                self._cached_json_bytes = json.dumps(items, separators=(',', ':')).encode("utf-8")
                self._cache_dirty = False
            return self._cached_json_bytes

    def get_projects(
        self,
        limit: int = 100,
        offset: int = 0,
        search: Optional[str] = None,
        status: Optional[str] = None,
        sniper_ready: Optional[bool] = None,
        min_progress: Optional[float] = None,
        sort_by: str = "block",
        order: str = "desc"
    ) -> Dict[str, Any]:
        """Queries projects with filtering, pagination, and sorting."""
        all_projs = self.get_all_projects()
        
        filtered = all_projs
        if search:
            s = search.lower().strip()
            filtered = [
                p for p in filtered
                if s in p.get("name", "").lower()
                or s in p.get("symbol", "").lower()
                or s in p.get("contract", "").lower()
            ]

        if status:
            st = status.lower().strip()
            filtered = [p for p in filtered if p.get("status", "").lower() == st]

        if sniper_ready is not None:
            filtered = [p for p in filtered if bool(p.get("sniper_ready")) == sniper_ready]

        if min_progress is not None:
            filtered = [p for p in filtered if float(p.get("progress") or 0.0) >= min_progress]

        reverse = (order.lower() == "desc")
        if sort_by == "progress":
            filtered.sort(key=lambda x: float(x.get("progress") or 0.0), reverse=reverse)
        elif sort_by == "holders":
            filtered.sort(key=lambda x: int(x.get("holders") or 0), reverse=reverse)
        elif sort_by == "score":
            filtered.sort(key=lambda x: float(x.get("score") or 0.0), reverse=reverse)
        else:
            filtered.sort(key=lambda x: int(x.get("block") or 0), reverse=reverse)

        total = len(filtered)
        paginated = filtered[offset : offset + limit]
        
        return {
            "success": True,
            "total": total,
            "count": len(paginated),
            "offset": offset,
            "limit": limit,
            "projects": paginated
        }

    def get_project(self, contract: str) -> Optional[Dict[str, Any]]:
        c = (contract or "").lower().strip()
        if not c:
            return None
        with self._cache_lock:
            if c in self._projects_cache:
                return self._projects_cache[c]

        all_p = self.get_all_projects()
        with self._cache_lock:
            return self._projects_cache.get(c)

    @staticmethod
    def _compute_fingerprint(p: Dict[str, Any]) -> str:
        """Computes a compact signature to detect if mutable metrics have actually changed."""
        mc_k = int(float(p.get("market_cap") or 0.0) / 100)
        vol_k = int(float(p.get("volume_24h") or 0.0) / 100)
        return (
            f"{p.get('progress')}_{p.get('holders')}_{p.get('score')}_"
            f"{p.get('sniper_ready')}_{p.get('is_proxy_clone')}_{p.get('is_nft_surge')}_"
            f"{p.get('risk_level')}_{p.get('risk_badge')}_{p.get('status')}_"
            f"{p.get('token_price_usd')}_{p.get('token_symbol')}_{p.get('lp_usd')}_"
            f"{mc_k}_{vol_k}_{p.get('mint_value_wei')}_{p.get('mint_price_str')}"
        )

    def upsert_projects(self, projects: List[Dict[str, Any]]):
        """Batch upserts projects into SQLite or Supabase and updates in-memory cache.
        Includes Bandwidth Guard: Filters out unchanged records to avoid redundant Supabase REST calls."""
        if not projects:
            return

        clean_records = []
        for p in projects:
            contract = (p.get("contract") or "").lower().strip()
            if not contract or not contract.startswith("0x"):
                continue
            clean_records.append(p)

        if not clean_records:
            return

        now_ts = int(time.time())

        # 1. Update in-memory cache and isolate truly modified/dirty records
        changed_records = []
        with self._cache_lock:
            for p in clean_records:
                c = p["contract"].lower()
                fp = self._compute_fingerprint(p)
                if c in self._p_fingerprints and self._p_fingerprints[c] == fp:
                    # Clean/identical project record - skip outbound network I/O
                    continue
                self._p_fingerprints[c] = fp
                changed_records.append(p)
                if c in self._projects_cache:
                    self._projects_cache[c].update(p)
                else:
                    self._projects_cache[c] = dict(p)

            if changed_records:
                self._cache_initialized = True
                self._cache_dirty = True
                self._last_cache_time = now_ts
                if now_ts > self._last_synced_updated_at:
                    self._last_synced_updated_at = now_ts

        # Bandwidth Guard: If zero projects changed, abort outbound network writes immediately
        if not changed_records:
            return

        # 2. Supabase Cloud Sync (ONLY sync truly modified records)
        if self.mode == "supabase":
            try:
                rows = []
                for p in changed_records:
                    rows.append({
                        "contract": p["contract"].lower(),
                        "name": str(p.get("name", "")),
                        "symbol": str(p.get("symbol", "")),
                        "token_id": str(p.get("token_id", "")),
                        "block": int(p.get("block") or 0),
                        "progress": float(p.get("progress") or 0.0),
                        "holders": int(p.get("holders") or 0),
                        "status": str(p.get("status", "TRACKING")),
                        "risk_level": str(p.get("risk_level", "SAFE")),
                        "risk_badge": str(p.get("risk_badge", "🟢 Clean")),
                        "sniper_ready": bool(p.get("sniper_ready", False)),
                        "is_proxy_clone": bool(p.get("is_proxy_clone", False)),
                        "score": float(p.get("score") or 0.0),
                        "data": p,
                        "detected_at": str(p.get("detected_at", "")),
                        "updated_at": now_ts
                    })
                client = self.client
                if HAS_HTTPX and client:
                    # Supabase upsert requires Prefer: resolution=merge-duplicates
                    # Batch in chunks of 50 to avoid request size limits and connection timeouts
                    batch_size = 50
                    all_ok = True
                    for i in range(0, len(rows), batch_size):
                        chunk = rows[i : i + batch_size]
                        resp = client.post(
                            "/rest/v1/projects?on_conflict=contract",
                            json=chunk,
                            headers={"Prefer": "resolution=merge-duplicates"}
                        )
                        if resp.status_code not in (200, 201):
                            all_ok = False
                            print(f"[⚠️ Supabase Chunk Error] HTTP {resp.status_code}: {resp.text[:120]}", flush=True)
                            break
                    if all_ok:
                        return
            except Exception as e:
                print(f"[⚠️ Supabase Upsert Error] {e} - Falling back to local SQLite", flush=True)

        # 3. SQLite Persistence (WAL Mode)
        with self._lock:
            conn = self._get_sqlite_connection()
            try:
                cur = conn.cursor()
                payloads = []
                for p in changed_records:
                    payloads.append((
                        p["contract"].lower(),
                        p.get("name", ""),
                        p.get("symbol", ""),
                        p.get("token_id", ""),
                        int(p.get("block") or 0),
                        float(p.get("progress") or 0.0),
                        int(p.get("holders") or 0),
                        p.get("status", "TRACKING"),
                        p.get("risk_level", "SAFE"),
                        p.get("risk_badge", "🟢 Clean"),
                        1 if p.get("sniper_ready") else 0,
                        1 if p.get("is_proxy_clone") else 0,
                        float(p.get("score") or 0.0),
                        json.dumps(p),
                        p.get("detected_at", ""),
                        now_ts
                    ))
                cur.executemany("""
                    INSERT INTO projects (
                        contract, name, symbol, token_id, block, progress, holders,
                        status, risk_level, risk_badge, sniper_ready, is_proxy_clone,
                        score, raw_data, detected_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(contract) DO UPDATE SET
                        name=excluded.name,
                        symbol=excluded.symbol,
                        token_id=excluded.token_id,
                        block=excluded.block,
                        progress=excluded.progress,
                        holders=excluded.holders,
                        status=excluded.status,
                        risk_level=excluded.risk_level,
                        risk_badge=excluded.risk_badge,
                        sniper_ready=excluded.sniper_ready,
                        is_proxy_clone=excluded.is_proxy_clone,
                        score=excluded.score,
                        raw_data=excluded.raw_data,
                        detected_at=excluded.detected_at,
                        updated_at=excluded.updated_at;
                """, payloads)
                conn.commit()
            except Exception as e:
                print(f"[⚠️ SQLite Upsert Error] {e}", flush=True)
            finally:
                conn.close()

    def upsert_project(self, project: Dict[str, Any]):
        self.upsert_projects([project])

    def delete_projects(self, contracts: List[str]) -> int:
        """Deletes projects by contract from Supabase and SQLite, invalidating cache."""
        clean = [c.lower().strip() for c in contracts if c and isinstance(c, str)]
        if not clean:
            return 0
        
        deleted_count = 0
        # 1. Supabase Cloud Delete
        if self.mode == "supabase":
            try:
                client = self.client
                if HAS_HTTPX and client:
                    batch_size = 50
                    for i in range(0, len(clean), batch_size):
                        chunk = clean[i : i + batch_size]
                        in_clause = ",".join(f'"{c}"' for c in chunk)
                        resp = client.delete(f"/rest/v1/projects?contract=in.({in_clause})")
                        if resp.status_code in (200, 204):
                            deleted_count += len(chunk)
            except Exception as e:
                print(f"[⚠️ Supabase Delete Error] {e}", flush=True)

        # 2. SQLite Delete
        try:
            conn = self._get_sqlite_connection()
            with self._lock:
                cur = conn.cursor()
                placeholders = ",".join("?" for _ in clean)
                cur.execute(f"DELETE FROM projects WHERE contract IN ({placeholders})", clean)
                conn.commit()
                deleted_count = max(deleted_count, cur.rowcount)
            conn.close()
        except Exception as e:
            print(f"[⚠️ SQLite Delete Error] {e}", flush=True)

        # 3. In-memory cache purge
        with self._cache_lock:
            for c in clean:
                self._projects_cache.pop(c, None)
                self._p_fingerprints.pop(c, None)
            self._cached_json_bytes = None
            self._last_cache_time = 0.0

        return deleted_count

    # -------------------------------------------------------------------------
    # User Favorites Operations
    # -------------------------------------------------------------------------
    def get_favorites(self, wallet: str) -> Dict[str, Any]:
        w = (wallet or "").lower().strip()
        if not w:
            return {"wallet": "", "favorites": [], "updated_at": 0}

        if self.mode == "supabase":
            try:
                if HAS_HTTPX and self._client:
                    resp = self._client.get(f"/rest/v1/user_favorites?wallet=eq.{w}&select=*")
                    if resp.status_code == 200:
                        records = resp.json()
                        if records:
                            r = records[0]
                            favs = r.get("favorites", [])
                            if isinstance(favs, str):
                                favs = json.loads(favs)
                            return {
                                "wallet": w,
                                "favorites": favs,
                                "updated_at": r.get("updated_at", 0)
                            }
            except Exception as e:
                print(f"[⚠️ Supabase Favorites Error] {e}", flush=True)

        with self._lock:
            conn = self._get_sqlite_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT favorites, updated_at FROM user_favorites WHERE wallet = ?", (w,))
                row = cur.fetchone()
                if row:
                    favs = json.loads(row["favorites"]) if row["favorites"] else []
                    return {
                        "wallet": w,
                        "favorites": favs,
                        "updated_at": row["updated_at"]
                    }
                return {"wallet": w, "favorites": [], "updated_at": 0}
            finally:
                conn.close()

    def save_favorites(self, wallet: str, favorites: List[str], timestamp: Optional[int] = None) -> Dict[str, Any]:
        w = (wallet or "").lower().strip()
        clean_favs = [c.lower() for c in favorites if isinstance(c, str) and c.startswith("0x") and len(c) == 42]
        clean_favs = list(dict.fromkeys(clean_favs))
        ts = int(timestamp or time.time())

        if self.mode == "supabase":
            try:
                if HAS_HTTPX and self._client:
                    resp = self._client.post(
                        "/rest/v1/user_favorites?on_conflict=wallet",
                        json={"wallet": w, "favorites": clean_favs, "updated_at": ts},
                        headers={"Prefer": "resolution=merge-duplicates"}
                    )
                    if resp.status_code in (200, 201):
                        return {"wallet": w, "favorites": clean_favs, "updated_at": ts}
            except Exception as e:
                print(f"[⚠️ Supabase Save Favorites Error] {e}", flush=True)

        with self._lock:
            conn = self._get_sqlite_connection()
            try:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO user_favorites (wallet, favorites, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(wallet) DO UPDATE SET
                        favorites=excluded.favorites,
                        updated_at=excluded.updated_at;
                """, (w, json.dumps(clean_favs), ts))
                conn.commit()
            finally:
                conn.close()

        return {"wallet": w, "favorites": clean_favs, "updated_at": ts}


# Global Singleton Instance
db = DatabaseManager()

