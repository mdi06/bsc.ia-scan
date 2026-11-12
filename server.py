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
