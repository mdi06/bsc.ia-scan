#!/usr/bin/env python3
"""
Agent ID Monitor - Unified Master Process Orchestrator
======================================================
Coordinates decoupled Ingestion Worker (worker.py) & Web API Server (api.py).

Architecture (Phase 3 Decoupling):
- Producer (worker.py): 24/7 autonomous block scanner, GoPlus auditor, and DexScreener updater.
- Consumer (api.py): High-performance FastAPI server responding to web/mobile clients in < 5ms.
- Orchestrator (server.py): Spawns worker and api as separate operating system processes with
  isolated memory spaces and separate Python GILs, with graceful signal handling.
"""

import os
import sys
import time
import signal
import multiprocessing
import threading
import json
import urllib.request
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import monitor
from db import db
import worker
import api

# Re-export app for ASGI / Uvicorn (e.g. uvicorn server:app)
app = api.app

# Backward-compatible exports for existing unit tests
run_discovery_loop = worker.run_discovery_loop
run_proxy_factory_watcher_loop = worker.run_proxy_factory_watcher_loop
run_market_dynamics_updater_loop = worker.run_market_dynamics_updater_loop
run_fast_track_progress_sweeper_loop = worker.run_fast_track_progress_sweeper_loop
run_element_nft_volume_sweeper_loop = worker.run_element_nft_volume_sweeper_loop
fast_sweep_contract = worker.fast_sweep_contract

history_lock = worker.history_lock
favorites_lock = threading.Lock()
FAVORITES_FILE = os.path.join(os.path.dirname(__file__), "user_favorites.json")


def load_user_favorites() -> dict:
    if os.path.exists(FAVORITES_FILE):
        try:
            with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_user_favorites(data: dict):
    tmp = FAVORITES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, FAVORITES_FILE)
    try:
        for wallet, entry in data.items():
            if isinstance(entry, dict) and "favorites" in entry:
                db.save_favorites(wallet, entry["favorites"], entry.get("updated_at"))
    except Exception:
        pass


class AgentIdServerHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/healthz", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","service":"agent-id-backend"}')
            return

        if parsed.path in ("/api/favorites", "/favorites"):
            qs = urllib.parse.parse_qs(parsed.query)
            wallet = (qs.get("wallet", [""])[0] or "").lower().strip()
            if not wallet or not wallet.startswith("0x") or len(wallet) != 42:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Valid 0x... BSC wallet address required in ?wallet= parameter"}).encode("utf-8"))
                return

            with favorites_lock:
                fav_data = load_user_favorites()
                record = fav_data.get(wallet, {"favorites": [], "updated_at": 0})

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": True,
                "wallet": wallet,
                "favorites": record.get("favorites", []),
                "updated_at": record.get("updated_at", 0)
            }).encode("utf-8"))
            return

        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/api/favorites", "/favorites"):
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                try:
                    body = self.rfile.read(content_length).decode("utf-8")
                    incoming = json.loads(body)
                    wallet = (incoming.get("wallet") or "").lower().strip()
                    if not wallet or not wallet.startswith("0x") or len(wallet) != 42:
                        self.send_response(400)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": "Valid 0x... BSC wallet address required"}).encode("utf-8"))
                        return

                    raw_favs = incoming.get("favorites", [])
                    if not isinstance(raw_favs, list):
                        raw_favs = []
                    clean_favs = [c.lower() for c in raw_favs if isinstance(c, str) and c.startswith("0x") and len(c) == 42]
                    clean_favs = list(dict.fromkeys(clean_favs))
                    ts = int(incoming.get("timestamp") or time.time())

                    with favorites_lock:
                        fav_data = load_user_favorites()
                        fav_data[wallet] = {
                            "favorites": clean_favs,
                            "updated_at": ts
                        }
                        save_user_favorites(fav_data)

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "success": True,
                        "wallet": wallet,
                        "favorites": clean_favs,
                        "updated_at": ts
                    }).encode("utf-8"))
                    return
                except Exception as e:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                    return

        self.send_response(404)
        self.end_headers()


def start_server(port=8080):
    """Starts the API server directly (backwards-compatible with tests)."""
    api.start_api(port=port)


def run_decoupled_system(port=8080):
    """
    Decoupled 2-Process Architecture:
    - Worker Process (Producer): Autonomous ingestion, GoPlus audit, DexScreener & multicall sweepers.
    - Main Process (Consumer): FastAPI Web API server serving requests in < 5ms.
    Eliminates redundant parent process, saving ~70 MB of RAM to stay strictly within 512MB limits.
    """
    print("=" * 70, flush=True)
    print("🚀 Starting Agent ID Monitor (Phase 3 Decoupled 2-Process Architecture)", flush=True)
    print(f"   [Worker Process]: Autonomous Ingestion, GoPlus Audit & Sweepers")
    print(f"   [API Server]: FastAPI on http://0.0.0.0:{port}")
    print("=" * 70, flush=True)

    # 1. Spawn Worker Process (Producer)
    worker_proc = multiprocessing.Process(
        target=worker.start_worker,
        name="IngestionWorkerProcess"
    )
    worker_proc.start()

    # 2. Worker supervisor thread (in background of main process)
    def supervise_worker():
        nonlocal worker_proc
        while True:
            time.sleep(3)
            if not worker_proc.is_alive():
                print("[⚠️ Worker Supervisor] Worker process exited. Auto-restarting...", flush=True)
                worker_proc = multiprocessing.Process(target=worker.start_worker, name="IngestionWorkerProcess")
                worker_proc.start()

    sup_thread = threading.Thread(target=supervise_worker, daemon=True, name="WorkerSupervisor")
    sup_thread.start()

    # 3. Clean signal handling
    def shutdown_processes(signum=None, frame=None):
        print("\n🛑 Shutdown signal received. Gracefully terminating worker process...", flush=True)
        if worker_proc.is_alive():
            worker_proc.terminate()
            worker_proc.join(timeout=3)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_processes)
    signal.signal(signal.SIGTERM, shutdown_processes)

    # 4. Run API Server (Consumer) in current process
    try:
        api.start_api(port=port)
    finally:
        if worker_proc.is_alive():
            worker_proc.terminate()
            worker_proc.join(timeout=3)


if __name__ == "__main__":
    try:
        multiprocessing.set_start_method("spawn", force=False)
    except RuntimeError:
        pass

    port = int(os.environ.get("PORT", 8080))

    if "--worker-only" in sys.argv:
        worker.start_worker()
    elif "--api-only" in sys.argv:
        api.start_api(port=port)
    else:
        run_decoupled_system(port=port)
