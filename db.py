"""
Storage layer for Agent ID Monitor.
Backends:
1. Supabase (PostgreSQL via the PostgREST API): used when SUPABASE_URL and a key are set, so data survives Render restarts.
2. SQLite (WAL mode): fallback for local development, and when a Supabase write fails.
"""

import os
import hashlib
import json
import time
import sqlite3
import threading
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

# Supabase calls need httpx; without it everything falls back to SQLite.
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "agent_monitor.db")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
FAVORITES_FILE = os.path.join(BASE_DIR, "user_favorites.json")


def scrub_non_finite(obj):
    """Replaces NaN / +-Infinity floats inside `obj` (dicts / lists, in place) with None and returns obj.
    A non-finite float cannot be stored as JSON, and it made the change-detection fingerprint raise, which
    dropped the whole upsert batch."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, float):
                if v != v or v in (float("inf"), float("-inf")):
                    obj[k] = None
            elif isinstance(v, (dict, list)):
                scrub_non_finite(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, float):
                if v != v or v in (float("inf"), float("-inf")):
                    obj[i] = None
            elif isinstance(v, (dict, list)):
                scrub_non_finite(v)
    return obj


class DatabaseManager:
    def __init__(self, db_path: Optional[str] = None):
        self.supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        if self.supabase_url.endswith("/rest/v1"):
            self.supabase_url = self.supabase_url[:-8].rstrip("/")
        self.supabase_key = os.getenv("SUPABASE_KEY", os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_ANON_KEY", ""))).strip()
        
        if (self.supabase_key
                and "SUPABASE_KEY" not in os.environ
                and "SUPABASE_SERVICE_ROLE_KEY" not in os.environ
                and "SUPABASE_ANON_KEY" in os.environ):
            print("[⚠️ DB Warning] Using SUPABASE_ANON_KEY as the backend key. Writes will fail once RLS "
                  "is tightened; set SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_KEY) to the service role key.",
                  flush=True)

        self.mode = "supabase" if (self.supabase_url and self.supabase_key) else "sqlite"
        self.db_path = db_path or os.getenv("DB_PATH", DEFAULT_DB_PATH)
        
        self._lock = threading.RLock()
        self._cache_lock = threading.RLock()
        
        # In-memory cache: {contract_lower: project_dict}. The 60s TTL caps Supabase reads at one per minute;
        # upserts update the cache immediately so new drops show up without waiting for a refresh.
        self._projects_cache: Dict[str, Dict[str, Any]] = {}
        self._p_fingerprints: Dict[str, str] = {}
        self._p_written_at: Dict[str, float] = {}  # contract -> when it was last durably written
        self._retry_queue: set = set()  # contracts whose last remote write failed; resent by the next upsert
        self._cache_initialized = False
        self._last_cache_time = 0.0
        self._last_synced_updated_at = 0
        self._cache_ttl = 60.0  # seconds
        self._cached_json_bytes: Optional[bytes] = None
        self._cached_json_etag: Optional[str] = None
        self._sqlite_ready = False
        self._cache_dirty = True
        # Bumped whenever the project data changes (an upsert / delete here, or a refresh that brought new data in).
        # HTTP ETags are built from it, so a conditional GET can never get a 304 for changed content.
        self._content_version = 0

        # httpx client, recreated when the PID changes (see `client`)
        self._client_instance = None
        self._client_pid = None

        # Agent ID telemetry cache & throttling
        self._agent_stats_cache: Optional[Dict[str, Any]] = None
        self._agent_stats_cache_time: float = 0.0
        self._last_stats_cloud_write: float = 0.0

        # Pre-seed the cache from history.json so a cold start needs no Supabase read.
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
            except Exception as e:
                print(f"[⚠️ DB Warning] Could not pre-seed cache from {HISTORY_FILE}: {e}", flush=True)

        if self.mode == "sqlite":
            self._init_sqlite()
        
        self.seed_from_local_json()

    @property
    def client(self) -> Optional["httpx.Client"]:
        """Shared httpx.Client, recreated in a forked child process (a client must not be shared across PIDs)."""
        if self.mode != "supabase" or not HAS_HTTPX:
            return None
        cur_pid = os.getpid()
        with self._lock:
            if self._client_pid != cur_pid or self._client_instance is None:
                old = self._client_instance
                if old is not None:
                    try:
                        old.close()
                    except Exception:
                        pass  # the old client may belong to the parent process; closing is best-effort
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

    # -------------------------------------------------------------------------
    # SQLite Initialization & Connection
    # -------------------------------------------------------------------------
    def _init_sqlite(self):
        """Creates the SQLite schema once per process (idempotent)."""
        with self._lock:
            if self._sqlite_ready:
                return
            conn = self._open_sqlite()
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
                self._sqlite_ready = True
            finally:
                conn.close()

    def _open_sqlite(self) -> sqlite3.Connection:
        # The (gitignored) data/ dir may not exist on a fresh deploy.
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_sqlite_connection(self) -> sqlite3.Connection:
        # In Supabase mode the schema is not created at start-up, but the SQLite fallback paths still
        # run (a throttled or failed cloud write), so make sure the tables exist before every use.
        if not self._sqlite_ready:
            self._init_sqlite()
        return self._open_sqlite()

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
                client = self.client
                if HAS_HTTPX and client:
                    resp = client.get("/rest/v1/projects?select=contract", headers={"Range": "0-0", "Prefer": "count=exact"})
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
                            self._content_version += 1
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
                    self._content_version += 1
                    self._last_cache_time = now
                    items = list(self._projects_cache.values())
                    items.sort(key=lambda x: x.get("block", 0) or 0, reverse=True)
                    return items
            finally:
                conn.close()

    def content_version(self) -> int:
        """Counter that changes whenever the project data changes (see _content_version)."""
        return self._content_version

    def get_history_json(self) -> Tuple[bytes, str]:
        """Returns (minified UTF-8 JSON of all projects, ETag of those exact bytes).
        Both are cached until the project cache changes, so requests neither re-encode nor re-hash."""
        self.get_all_projects()
        with self._cache_lock:
            if not self._cached_json_bytes or self._cache_dirty:
                items = list(self._projects_cache.values())
                items.sort(key=lambda x: x.get("block", 0) or 0, reverse=True)
                self._cached_json_bytes = json.dumps(items, separators=(',', ':')).encode("utf-8")
                self._cached_json_etag = f'"{hashlib.md5(self._cached_json_bytes).hexdigest()}"'
                self._cache_dirty = False
            return self._cached_json_bytes, self._cached_json_etag

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
                if s in (p.get("name") or "").lower()
                or s in (p.get("symbol") or "").lower()
                or s in (p.get("contract") or "").lower()
            ]

        if status:
            st = status.lower().strip()
            filtered = [p for p in filtered if (p.get("status") or "").lower() == st]

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

    # A record is written again after this long even if its fingerprint did not change, so fields that are only
    # bucketed (price, LP, volume) and market_updated_at cannot stay stale in the database forever.
    FORCE_WRITE_AFTER_SEC = float(os.getenv("DB_FORCE_WRITE_AFTER_SEC", 30 * 60))
    SUPABASE_CHUNK_SIZE = 50

    @staticmethod
    def _sig(v: Any, digits: int = 3) -> str:
        """`digits` significant digits of a number, "-" for None / non-numbers (so None != 0)."""
        try:
            if v is None or isinstance(v, bool):
                return "-"
            f = float(v)
            return "0" if f == 0 else f"{f:.{digits}g}"
        except (TypeError, ValueError):
            return "-"

    @staticmethod
    def _exact(v: Any) -> str:
        """repr of a number or flag, exact; "-" for None."""
        return "-" if v is None else repr(v)

    @staticmethod
    def _compute_fingerprint(p: Dict[str, Any]) -> str:
        """Computes a compact signature to detect if mutable metrics have actually changed significantly.
        Quantizes floating-point values (price, LP, mcap, volume) to prevent micro-fluctuations (sub-cent / sub-1%)
        from triggering continuous multi-megabyte outbound REST syncs to Supabase on Render.

        Counts, flags and all-time-high fields are compared exactly; continuous values are bucketed. Fields that
        move on every tick without meaning anything new (market_updated_at) are left out and covered by
        FORCE_WRITE_AFTER_SEC instead."""
        sig, exact = DatabaseManager._sig, DatabaseManager._exact

        # Quantize price to 3 significant digits (e.g., 0.00412 vs 0.004123456)
        px = float(p.get("token_price_usd") or 0.0)
        px_bucket = f"{px:.3g}" if px > 0 else "0"

        # Quantize LP into $500 buckets ($0, $500, $1k, $1.5k...)
        lp = float(p.get("lp_usd") or 0.0)
        lp_k = int(lp / 500) if lp > 1000 else int(lp / 50)

        # Quantize Market Cap and Volume into $1,000 buckets
        mc = float(p.get("market_cap") or 0.0)
        mc_k = int(mc / 1000) if mc > 5000 else int(mc / 100)

        vol = float(p.get("volume_24h") or 0.0)
        vol_k = int(vol / 1000) if vol > 5000 else int(vol / 100)

        holders = int(p.get("holders") or 0)
        prog = round(float(p.get("progress") or 0.0), 1)

        # Mint progress in 1% steps: the sweeper changes total_minted without touching "progress".
        tm = float(p.get("total_minted") or 0.0)
        ms = float(p.get("max_supply") or 0.0)
        mint_pct = int(tm / ms * 100) if ms > 0 else 0

        # None (unknown) must differ from 0, and a cleared market must differ from a priced one
        fdv = p.get("fdv")
        fdv_k = "-" if fdv is None else (int(float(fdv) / 1000) if float(fdv) > 5000 else int(float(fdv) / 100))
        chg = p.get("price_change_24h")
        chg_s = "-" if chg is None else f"{float(chg):.1f}"

        return (
            f"{prog}_{holders}_{p.get('score')}_"
            f"{p.get('sniper_ready')}_{p.get('is_proxy_clone')}_{p.get('is_nft_surge')}_"
            f"{p.get('risk_level')}_{p.get('risk_badge')}_{p.get('status')}_"
            f"{px_bucket}_{p.get('token_symbol')}_{lp_k}_"
            f"{mc_k}_{vol_k}_{p.get('mint_value_wei')}_{p.get('mint_price_str')}_"
            f"{p.get('secondary_sales_count')}_{float(p.get('floor_price_bnb') or 0.0):.3g}_{mint_pct}"
            # counts: exact
            f"|{exact(p.get('buys_24h'))}_{exact(p.get('sells_24h'))}_{exact(p.get('buys_1h'))}"
            # continuous: bucketed
            f"|{chg_s}_{fdv_k}_{sig(p.get('lp_wbnb'))}_{sig(p.get('token_price_bnb'))}"
            f"_{sig(p.get('high_price_bnb'))}_{sig(p.get('secondary_vol_bnb'))}"
            # all-time-high fields: exact. Current profit / multiplier follow the price (and BNB/USD) on every
            # tick, so they are bucketed like the price they derive from; exact would defeat the whole guard.
            f"|{exact(p.get('ath_price_usd'))}_{exact(p.get('ath_profit_usd'))}_{exact(p.get('ath_multiplier'))}"
            f"_{sig(p.get('current_profit_usd'))}_{sig(p.get('current_multiplier'))}"
            # freshness flag: exact
            f"|{exact(p.get('market_stale'))}"
        )

    def upsert_projects(self, projects: List[Dict[str, Any]]):
        """Batch upserts projects into SQLite or Supabase and updates in-memory cache.
        Includes Bandwidth Guard: Filters out unchanged records to avoid redundant Supabase REST calls.

        - The in-memory cache always takes the offered values, even when the remote write is skipped.
        - A record's fingerprint is remembered only after its write succeeded. A failed chunk leaves its
          records dirty, so the next call offers them again, and the chunks after it are still sent.
        - A record is also rewritten when its last write is older than FORCE_WRITE_AFTER_SEC."""
        if not projects and not self._retry_queue:
            return

        clean_records = []
        for p in projects or []:
            contract = (p.get("contract") or "").lower().strip()
            if not contract or not contract.startswith("0x"):
                continue
            scrub_non_finite(p)
            clean_records.append(p)

        if not clean_records and not self._retry_queue:
            return

        now_ts = int(time.time())
        mono = time.time()

        # 1. Update in-memory cache and isolate truly modified/dirty records
        changed_records = []
        pending_fp: Dict[str, str] = {}
        cache_changed = False
        with self._cache_lock:
            for p in clean_records:
                c = p["contract"].lower()
                fp = self._compute_fingerprint(p)

                cached = self._projects_cache.get(c)
                if cached is None:
                    self._projects_cache[c] = dict(p)
                    cache_changed = True
                elif cached is not p and any(cached.get(k) != v for k, v in p.items()):
                    cached.update(p)
                    cache_changed = True

                unchanged = c in self._p_fingerprints and self._p_fingerprints[c] == fp
                fresh_write = (mono - self._p_written_at.get(c, 0.0)) < self.FORCE_WRITE_AFTER_SEC
                if unchanged and fresh_write:
                    # Clean/identical project record - skip outbound network I/O
                    continue
                pending_fp[c] = fp
                changed_records.append(p)

            # Records whose earlier remote write failed are resent even if nobody offers them again.
            offered = {p["contract"].lower() for p in changed_records}
            for c in sorted(self._retry_queue - offered):
                queued = self._projects_cache.get(c)
                if queued is not None:
                    pending_fp[c] = self._compute_fingerprint(queued)
                    changed_records.append(queued)
                else:
                    self._retry_queue.discard(c)  # deleted in the meantime

            if changed_records or cache_changed:
                self._cache_initialized = True
                self._cache_dirty = True
                self._content_version += 1
            if changed_records:
                self._last_cache_time = now_ts
                if now_ts > self._last_synced_updated_at:
                    self._last_synced_updated_at = now_ts

        # Bandwidth Guard: If zero projects changed, abort outbound network writes immediately
        if not changed_records:
            return

        def mark_written(records: List[Dict[str, Any]]):
            with self._cache_lock:
                for rec in records:
                    c = rec["contract"].lower()
                    self._p_fingerprints[c] = pending_fp[c]
                    self._p_written_at[c] = mono
                    self._retry_queue.discard(c)

        # 2. Supabase Cloud Sync (ONLY sync truly modified records)
        to_sqlite = changed_records
        if self.mode == "supabase":
            failed: List[Dict[str, Any]] = []
            client = self.client
            for i in range(0, len(changed_records), self.SUPABASE_CHUNK_SIZE):
                recs = changed_records[i : i + self.SUPABASE_CHUNK_SIZE]
                if not (HAS_HTTPX and client):
                    failed.extend(recs)
                    continue
                try:
                    rows = [{
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
                    } for p in recs]
                    # Supabase upsert requires Prefer: resolution=merge-duplicates
                    resp = client.post(
                        "/rest/v1/projects?on_conflict=contract",
                        json=rows,
                        headers={"Prefer": "resolution=merge-duplicates"}
                    )
                    if resp.status_code in (200, 201):
                        mark_written(recs)
                    else:
                        failed.extend(recs)
                        print(f"[⚠️ Supabase Chunk Error] HTTP {resp.status_code}: {resp.text[:120]}", flush=True)
                except Exception as e:
                    failed.extend(recs)
                    print(f"[⚠️ Supabase Upsert Error] {e}", flush=True)
            if not failed:
                return
            # Only the records Supabase did not take go to the local fallback; they stay dirty (no fingerprint)
            # so the next call offers them again.
            print(f"[⚠️ Supabase] {len(failed)} record(s) not written; will retry on the next upsert", flush=True)
            to_sqlite = failed
            sqlite_commits_fingerprint = False
            with self._cache_lock:
                self._retry_queue.update(p["contract"].lower() for p in failed)
        else:
            sqlite_commits_fingerprint = True

        # 3. SQLite Persistence (WAL Mode)
        with self._lock:
            conn = self._get_sqlite_connection()
            try:
                cur = conn.cursor()
                payloads = []
                for p in to_sqlite:
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
                if sqlite_commits_fingerprint:
                    mark_written(to_sqlite)
            except Exception as e:
                print(f"[⚠️ SQLite Upsert Error] {e}", flush=True)
                if sqlite_commits_fingerprint:
                    with self._cache_lock:
                        self._retry_queue.update(p["contract"].lower() for p in to_sqlite)
            finally:
                conn.close()

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
            with self._lock:
                conn = self._get_sqlite_connection()
                try:
                    cur = conn.cursor()
                    placeholders = ",".join("?" for _ in clean)
                    cur.execute(f"DELETE FROM projects WHERE contract IN ({placeholders})", clean)
                    conn.commit()
                    deleted_count = max(deleted_count, cur.rowcount)
                finally:
                    conn.close()
        except Exception as e:
            print(f"[⚠️ SQLite Delete Error] {e}", flush=True)

        # 3. In-memory cache purge
        with self._cache_lock:
            for c in clean:
                self._projects_cache.pop(c, None)
                self._content_version += 1
                self._p_fingerprints.pop(c, None)
                self._p_written_at.pop(c, None)
                self._retry_queue.discard(c)
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
                client = self.client
                if HAS_HTTPX and client:
                    resp = client.get(f"/rest/v1/user_favorites?wallet=eq.{w}&select=*")
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
                client = self.client
                if HAS_HTTPX and client:
                    resp = client.post(
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

    # -------------------------------------------------------------------------
    # Persistent .ai Agent ID Telemetry Counter
    # -------------------------------------------------------------------------
    def get_agent_id_stats(self, max_age_sec: float = 15.0) -> Optional[Dict[str, Any]]:
        """Loads persisted .ai wallet counter from Supabase or SQLite with lightweight TTL cache."""
        now = time.time()
        with self._cache_lock:
            if self._agent_stats_cache and (now - self._agent_stats_cache_time < max_age_sec):
                return dict(self._agent_stats_cache)

        sentinel = "__system_agent_id_stats__"
        record = None
        if self.mode == "supabase":
            try:
                client = self.client
                if HAS_HTTPX and client:
                    resp = client.get(f"/rest/v1/user_favorites?wallet=eq.{sentinel}&select=favorites,updated_at")
                    if resp.status_code == 200:
                        rows = resp.json()
                        if rows:
                            favs = rows[0].get("favorites")
                            if isinstance(favs, str):
                                favs = json.loads(favs)
                            if isinstance(favs, list) and favs and isinstance(favs[0], dict):
                                record = favs[0]
                            elif isinstance(favs, dict):
                                record = favs
            except Exception as e:
                print(f"[⚠️ Supabase Agent ID Stats Error] {e}", flush=True)

        if record is None:
            with self._lock:
                conn = self._get_sqlite_connection()
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT favorites FROM user_favorites WHERE wallet = ?", (sentinel,))
                    row = cur.fetchone()
                    if row and row["favorites"]:
                        favs = json.loads(row["favorites"])
                        if isinstance(favs, list) and favs and isinstance(favs[0], dict):
                            record = favs[0]
                        elif isinstance(favs, dict):
                            record = favs
                except Exception as e:
                    print(f"[⚠️ SQLite Agent ID Stats Error] {e}", flush=True)
                finally:
                    conn.close()

        if record and record.get("total_created_wallets"):
            with self._cache_lock:
                self._agent_stats_cache = dict(record)
                self._agent_stats_cache_time = now
            return dict(record)
        return None

    def save_agent_id_stats(self, stats: Dict[str, Any], force_cloud: bool = False) -> None:
        """Persists .ai wallet counter to Supabase (throttled every 60s) and SQLite."""
        if not stats or not stats.get("total_created_wallets"):
            return

        now = time.time()
        clean_stats = {
            "total_created_wallets": int(stats["total_created_wallets"]),
            "last_block": int(stats.get("last_block") or 0),
            "last_updated": int(stats.get("last_updated") or now),
            "source": stats.get("source", "SPACE ID / Binance Agent ID Registry (0x60B9262456dE011F7d15a54CdD8c48bAaa8B022F)")
        }

        with self._cache_lock:
            self._agent_stats_cache = clean_stats
            self._agent_stats_cache_time = now

        sentinel = "__system_agent_id_stats__"
        payload = [clean_stats]

        if self.mode == "supabase":
            if force_cloud or (now - self._last_stats_cloud_write >= 60.0):
                try:
                    client = self.client
                    if HAS_HTTPX and client:
                        resp = client.post(
                            "/rest/v1/user_favorites?on_conflict=wallet",
                            json={"wallet": sentinel, "favorites": payload, "updated_at": clean_stats["last_updated"]},
                            headers={"Prefer": "resolution=merge-duplicates"}
                        )
                        if resp.status_code in (200, 201):
                            self._last_stats_cloud_write = now
                            return
                except Exception as e:
                    print(f"[⚠️ Supabase Save Agent ID Stats Error] {e}", flush=True)

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
                """, (sentinel, json.dumps(payload), clean_stats["last_updated"]))
                conn.commit()
            except Exception as e:
                print(f"[⚠️ SQLite Save Agent ID Stats Error] {e}", flush=True)
            finally:
                conn.close()


# Module-level singleton shared by api.py, worker.py and monitor.py.
db = DatabaseManager()

