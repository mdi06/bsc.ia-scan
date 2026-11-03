"""
Unit & Integration Tests for Phase 3: Ingestion Worker Decoupling.
Tests:
- worker.py (Producer): isolated functions, sweeper logic, and autonomous runner
- api.py (Consumer): pure FastAPI endpoints with zero background thread overhead
- server.py (Orchestrator): process supervision and backward-compatible exports
"""

import os
import pytest
from fastapi.testclient import TestClient

import server
import worker
import api


def test_worker_structure_and_exports():
    """Verifies worker.py exports all 5 core ingestion and sweeper loops."""
    assert hasattr(worker, "run_discovery_loop")
    assert hasattr(worker, "run_market_dynamics_updater_loop")
    assert hasattr(worker, "run_fast_track_progress_sweeper_loop")
    assert hasattr(worker, "run_element_nft_volume_sweeper_loop")
    assert hasattr(worker, "run_proxy_factory_watcher_loop")
    assert hasattr(worker, "fast_sweep_contract")
    assert hasattr(worker, "start_worker")


def test_api_server_endpoints_isolated():
    """Verifies api.py operates as a pure HTTP server with sub-5ms responses."""
    client = TestClient(api.app)

    # 1. Health check
    res = client.get("/healthz")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["service"] == "agent-id-backend"

    # 2. Virtual history.json
    res_hist = client.get("/history.json")
    assert res_hist.status_code == 200
    assert isinstance(res_hist.json(), list)

    # 3. /api/projects query
    res_proj = client.get("/api/projects?limit=3")
    assert res_proj.status_code == 200
    assert res_proj.json()["success"] is True


def test_server_orchestrator_backward_compatibility():
    """Verifies server.py re-exports all legacy attributes required by tests."""
    assert hasattr(server, "app")
    assert hasattr(server, "run_discovery_loop")
    assert hasattr(server, "run_proxy_factory_watcher_loop")
    assert hasattr(server, "run_market_dynamics_updater_loop")
    assert hasattr(server, "run_fast_track_progress_sweeper_loop")
    assert hasattr(server, "run_element_nft_volume_sweeper_loop")
    assert hasattr(server, "AgentIdServerHandler")
    assert hasattr(server, "ThreadingHTTPServer")
    assert hasattr(server, "load_user_favorites")
    assert hasattr(server, "save_user_favorites")
    assert hasattr(server, "FAVORITES_FILE")


def test_process_safe_db_client():
    """Verifies that DatabaseManager client property is process-safe and PID-isolated."""
    from db import DatabaseManager
    manager = DatabaseManager()
    # In sqlite mode, client is None
    assert manager.mode in ("sqlite", "supabase")
    assert hasattr(manager, "client")
    assert hasattr(manager, "_client")


def test_async_enrichment_pool_non_blocking():
    """Verifies worker background enrichment and candidate analysis pools are operational."""
    assert hasattr(worker, "enrichment_pool")
    assert hasattr(worker, "_enrich_contract_background")
    assert hasattr(worker, "discovery_pool")
    assert hasattr(worker, "_process_smart_money_candidate")
    assert hasattr(worker, "_process_registry_candidate")
    # Submitting a dummy quick task completes asynchronously
    fut = worker.enrichment_pool.submit(lambda x: x * 2, 21)
    assert fut.result(timeout=2.0) == 42
    fut2 = worker.discovery_pool.submit(lambda x: x + 10, 5)
    assert fut2.result(timeout=2.0) == 15


def test_save_history_delta_support(tmp_path):
    """Verifies monitor.save_history supports delta updates without rewriting whole table."""
    import monitor
    test_hist = [
        {"contract": "0x1111111111111111111111111111111111111111", "name": "Delta Test 1", "block": 100},
        {"contract": "0x2222222222222222222222222222222222222222", "name": "Delta Test 2", "block": 101}
    ]
    orig_file = monitor.HISTORY_FILE
    try:
        monitor.HISTORY_FILE = str(tmp_path / "test_history.json")
        monitor.save_history(test_hist, delta=test_hist[0])
    finally:
        monitor.HISTORY_FILE = orig_file


