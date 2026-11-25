"""Moving the worker off Render: one shared block download for the two block-scanning loops, and the API-only switch.
No network."""
import threading
import time

import server
import worker


class FakeRpc:
    def __init__(self, fail_first=0, delay=0.0):
        self.calls = []
        self.fail_first = fail_first
        self.delay = delay

    def get_block(self, num, full_tx=True):
        assert full_tx is True
        self.calls.append(num)
        if self.delay:
            time.sleep(self.delay)
        if self.fail_first > 0:
            self.fail_first -= 1
            return {}
        return {"number": hex(num), "transactions": [{"hash": f"0x{num:064x}"}]}


def test_two_loops_reading_the_same_block_download_it_once():
    src, rpc = worker.SharedBlockSource(), FakeRpc()
    a = src.get(rpc, 100, "discovery")
    b = src.get(rpc, 100, "proxy")
    assert a is b and rpc.calls == [100]
    assert 100 not in src._blocks          # every reader took it: memory released


def test_concurrent_requests_share_one_download():
    src, rpc = worker.SharedBlockSource(), FakeRpc(delay=0.2)
    out = {}
    threads = [threading.Thread(target=lambda r=r: out.__setitem__(r, src.get(rpc, 7, r))) for r in ("discovery", "proxy")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert rpc.calls == [7] and out["discovery"] is out["proxy"]


def test_a_failed_answer_is_not_kept_so_the_retry_downloads_again():
    src, rpc = worker.SharedBlockSource(), FakeRpc(fail_first=1)
    assert src.get(rpc, 5, "discovery") == {}
    assert src.get(rpc, 5, "discovery")["transactions"]       # the discovery loop's own retry
    assert rpc.calls == [5, 5]
    assert src.get(rpc, 5, "proxy")["transactions"] and rpc.calls == [5, 5]


def test_unread_blocks_are_capped():
    """If one loop stops reading, the cache keeps only the newest max_blocks."""
    src, rpc = worker.SharedBlockSource(max_blocks=4), FakeRpc()
    for n in range(10):
        src.get(rpc, n, "discovery")
    assert sorted(src._blocks) == [6, 7, 8, 9]
    src.get(rpc, 2, "proxy")                    # evicted: downloaded again, nothing breaks
    assert rpc.calls.count(2) == 2


def test_both_loops_use_the_shared_source():
    import inspect
    for fn in (worker.run_discovery_loop, worker.run_proxy_factory_watcher_loop):
        src = inspect.getsource(fn)
        assert "SHARED_BLOCKS.get(" in src and "rpc.get_block(" not in src


def test_run_worker_0_means_api_only(monkeypatch):
    for v in ("0", "false", "off", "no"):
        monkeypatch.setenv("RUN_WORKER", v)
        assert server.api_only_from_env() is True
    for v in ("1", "true", ""):
        monkeypatch.setenv("RUN_WORKER", v)
        assert server.api_only_from_env() is False
    monkeypatch.delenv("RUN_WORKER")
    assert server.api_only_from_env() is False   # default: unchanged (worker + API in one service)
