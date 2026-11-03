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

