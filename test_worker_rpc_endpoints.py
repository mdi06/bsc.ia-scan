"""BSC_RPC_URL (keyed provider, read from the worker's .env.worker) is tried first; without it the
endpoint lists are exactly the public lists they were before."""
import monitor
from monitor import PUBLIC_LOG_RPC_ENDPOINTS, PUBLIC_RPC_ENDPOINTS, build_rpc_endpoints

# A placeholder with no real key. The tests never contact it.
KEYED = "https://keyed-provider.test/rpc/PLACEHOLDER"


def test_unset_keeps_the_public_list():
    assert build_rpc_endpoints(PUBLIC_RPC_ENDPOINTS, env={}) == PUBLIC_RPC_ENDPOINTS


def test_blank_value_keeps_the_public_list():
    assert build_rpc_endpoints(PUBLIC_RPC_ENDPOINTS, env={"BSC_RPC_URL": "   "}) == PUBLIC_RPC_ENDPOINTS


def test_keyed_url_goes_first_for_block_reads():
    assert build_rpc_endpoints(PUBLIC_RPC_ENDPOINTS, env={"BSC_RPC_URL": KEYED}) == [KEYED] + PUBLIC_RPC_ENDPOINTS


def test_keyed_url_goes_first_for_log_scans_too():
    env = {"BSC_RPC_URL": " " + KEYED + "\n"}
    assert build_rpc_endpoints(PUBLIC_LOG_RPC_ENDPOINTS, env=env) == [KEYED] + PUBLIC_LOG_RPC_ENDPOINTS


def test_non_https_or_malformed_values_are_ignored():
    for bad in ["http://plain.test/rpc", "not-a-url", KEYED + " trailing words"]:
        assert build_rpc_endpoints(PUBLIC_RPC_ENDPOINTS, env={"BSC_RPC_URL": bad}) == PUBLIC_RPC_ENDPOINTS


def test_keyed_url_is_not_duplicated_when_also_public():
    env = {"BSC_RPC_URL": PUBLIC_RPC_ENDPOINTS[0]}
    assert build_rpc_endpoints(PUBLIC_RPC_ENDPOINTS, env=env) == PUBLIC_RPC_ENDPOINTS


class _Resp:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def test_client_tries_the_keyed_node_first_then_falls_back(monkeypatch):
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req.full_url)
        if req.full_url == KEYED:
            raise OSError("keyed node unreachable")
        return _Resp(b'{"jsonrpc":"2.0","id":1,"result":"0x2a"}')

    monkeypatch.setattr(monitor, "HAS_HTTPX", False)
    monkeypatch.setattr(monitor.urllib.request, "urlopen", fake_urlopen)
    client = monitor.BscRpcClient(build_rpc_endpoints(PUBLIC_RPC_ENDPOINTS, env={"BSC_RPC_URL": KEYED}))

    assert client.get_block_number() == 42
    assert seen == [KEYED, PUBLIC_RPC_ENDPOINTS[0]]
