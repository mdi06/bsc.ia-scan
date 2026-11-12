#!/usr/bin/env python3
"""
Entry point: runs the ingestion worker (worker.py) and the web API (api.py) as separate processes.

- worker.py scans the chain, enriches drops and writes them through db.py.
- api.py serves reads from the db.py cache.
- This module starts the worker as a child process, restarts it if it exits, and runs the API in the main process.
  Use --worker-only or --api-only to run just one of them.
"""

import os
import sys
import time
import signal
import multiprocessing
import threading

import worker
import api

# Re-export app for ASGI / Uvicorn (e.g. uvicorn server:app)
app = api.app

# Names the tests import from this module.
run_discovery_loop = worker.run_discovery_loop
run_proxy_factory_watcher_loop = worker.run_proxy_factory_watcher_loop
run_market_dynamics_updater_loop = worker.run_market_dynamics_updater_loop
run_fast_track_progress_sweeper_loop = worker.run_fast_track_progress_sweeper_loop
run_element_nft_volume_sweeper_loop = worker.run_element_nft_volume_sweeper_loop
fast_sweep_contract = worker.fast_sweep_contract

history_lock = worker.history_lock


def run_decoupled_system(port=8080):
    """Runs the worker in a child process and the API in this one (no separate parent process, to stay under 512 MB)."""
    print("=" * 70, flush=True)
    print("🚀 Starting Agent ID Monitor (worker process + API server)", flush=True)
    print("   [Worker Process]: ingestion, GoPlus audit & sweepers")
    print(f"   [API Server]: FastAPI on http://0.0.0.0:{port}")
    print("=" * 70, flush=True)

    worker_proc = multiprocessing.Process(
        target=worker.start_worker,
        name="IngestionWorkerProcess"
    )
    worker_proc.start()

    # Restart the worker if it exits.
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

    def shutdown_processes(signum=None, frame=None):
        print("\n🛑 Shutdown signal received. Gracefully terminating worker process...", flush=True)
        if worker_proc.is_alive():
            worker_proc.terminate()
            worker_proc.join(timeout=3)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_processes)
    signal.signal(signal.SIGTERM, shutdown_processes)

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
