"""BSC JSON-RPC access: a client that rotates between endpoints and batches calls, plus the small helpers every other module
uses to talk to the chain (function selectors, string decoding, text cleaning). Moved out of monitor.py unchanged; monitor.py
re-exports these names, so `monitor.BscRpcClient` and friends keep working."""

import json
import os
import re
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False



def build_rpc_endpoints(public: List[str], env: Optional[Dict[str, str]] = None) -> List[str]:
    """Public endpoints, with the keyed provider from BSC_RPC_URL (.env.worker) placed first when set.

    The value is used only if it is one https URL with no whitespace. Anything else is ignored, so a
    half-pasted line cannot break the worker. The URL is never logged: the endpoint list is never printed.
    """
    env = os.environ if env is None else env
    keyed = (env.get("BSC_RPC_URL") or "").strip()
    endpoints: List[str] = []
    if keyed.startswith("https://") and not any(ch.isspace() for ch in keyed):
        endpoints.append(keyed)
    for url in public:
        if url not in endpoints:
            endpoints.append(url)
    return endpoints


class BscRpcClient:
    def __init__(self, endpoints: List[str]):
        self.endpoints = endpoints
        self.current_idx = 0
        self._http_client = None
        if HAS_HTTPX:
            try:
                self._http_client = httpx.Client(
                    timeout=5.0,
                    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20)
                )
            except Exception:
                self._http_client = None

    def call(self, method: str, params: list) -> Any:
        payload_dict = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

        for _ in range(len(self.endpoints)):
            rpc_url = self.endpoints[self.current_idx]
            try:
                if self._http_client is not None:
                    resp = self._http_client.post(rpc_url, json=payload_dict, headers=headers)
                    data = resp.json()
                else:
                    payload = json.dumps(payload_dict).encode("utf-8")
                    req = urllib.request.Request(rpc_url, data=payload, headers=headers)
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        data = json.loads(resp.read().decode("utf-8"))

                if "error" in data:
                    err = data["error"]
                    err_msg = str(err).lower()
                    # Normal EVM reverts (e.g. function doesn't exist on contract)
                    if "revert" in err_msg or "execution reverted" in err_msg:
                        return None
                    raise RuntimeError(data["error"])
                return data.get("result")
            except Exception:
                self.current_idx = (self.current_idx + 1) % len(self.endpoints)
                continue
        return None

    def call_with_revert(self, method: str, params: list) -> Tuple[Optional[bool], Any]:
        """Like call(), but tells a revert apart from "no endpoint answered":
        (True, result) on success, (False, {"message": ..., "data": ...}) on a revert, (None, None) if every
        endpoint failed. Used to read why a simulated mint is refused (an Error(string) or a custom error)."""
        payload_dict = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        for _ in range(len(self.endpoints)):
            rpc_url = self.endpoints[self.current_idx]
            try:
                if self._http_client is not None:
                    data = self._http_client.post(rpc_url, json=payload_dict, headers=headers).json()
                else:
                    req = urllib.request.Request(rpc_url, data=json.dumps(payload_dict).encode("utf-8"), headers=headers)
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                if "error" in data:
                    err = data["error"] if isinstance(data["error"], dict) else {"message": str(data["error"])}
                    msg = str(err.get("message") or "")
                    if "revert" in msg.lower():
                        return False, {"message": msg, "data": err.get("data")}
                    raise RuntimeError(msg)
                return True, data.get("result")
            except Exception:
                self.current_idx = (self.current_idx + 1) % len(self.endpoints)
        return None, None

    def get_block_number(self) -> Optional[int]:
        res = self.call("eth_blockNumber", [])
        if res:
            try:
                return int(res, 16)
            except Exception:
                return None
        return None

    def get_block(self, block_num: int, full_tx: bool = True) -> Dict[str, Any]:
        return self.call("eth_getBlockByNumber", [hex(block_num), full_tx]) or {}

    def get_code(self, address: str) -> str:
        return (self.call("eth_getCode", [address, "latest"]) or "").lower()

    def get_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        return self.call("eth_getTransactionReceipt", [tx_hash])

    def get_balance(self, address: str) -> int:
        res = self.call("eth_getBalance", [address, "latest"])
        return int(res, 16) if res else 0

    def get_tx_count(self, address: str) -> int:
        res = self.call("eth_getTransactionCount", [address, "latest"])
        return int(res, 16) if res else 0

    def get_logs(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return self.call("eth_getLogs", [params]) or []

    def get_logs_strict(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Like get_logs, but raises when no endpoint answered. get_logs returns [] in that case, which a
        caller cannot tell apart from "no logs" (several public endpoints reject eth_getLogs outright)."""
        res = self.call("eth_getLogs", [params])
        if res is None:
            raise RuntimeError("eth_getLogs failed on every RPC endpoint")
        return res

    def get_storage_at(self, address: str, position: str) -> Optional[str]:
        return self.call("eth_getStorageAt", [address, position, "latest"])

    def call_contract(self, to: str, data: str) -> Optional[str]:
        try:
            res = self.call("eth_call", [{"to": to, "data": data}, "latest"])
            return res if res and res != "0x" else None
        except Exception:
            return None

    # JSON-RPC requests sent in one HTTP request by call_batch. Public endpoints differ in how many they accept;
    # when one refuses, call_batch falls back to single calls, so a too-large value costs speed, not answers.
    BATCH_CHUNK = 50
    BATCH_RETRY_AFTER_SEC = 60.0
    _batch_off_until = 0.0

    def _post_batch(self, part: List[Tuple[str, list]]) -> Optional[List[Any]]:
        """One JSON-RPC batch request. Returns the results in the order of `part` (None for an item that errored or
        reverted), or None when no endpoint answered with a usable batch. A batch where every item came back
        empty is treated as a refusal, not as an answer, so the caller re-asks one by one."""
        if self._http_client is None or time.time() < self._batch_off_until:
            return None
        payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(part)]
        headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        for _ in range(len(self.endpoints)):
            rpc_url = self.endpoints[self.current_idx]
            try:
                resp = self._http_client.post(rpc_url, json=payload, headers=headers)
                if getattr(resp, "status_code", 200) != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                data = resp.json()
                if not isinstance(data, list):
                    raise RuntimeError("endpoint does not accept batches")
                by_id = {d.get("id"): d for d in data if isinstance(d, dict)}
                results = []
                for i in range(len(part)):
                    d = by_id.get(i)
                    results.append(None if d is None or "error" in d else d.get("result"))
                if len(part) > 1 and all(r is None for r in results):
                    raise RuntimeError("batch answered with nothing")
                return results
            except Exception:
                self.current_idx = (self.current_idx + 1) % len(self.endpoints)
        self._batch_off_until = time.time() + self.BATCH_RETRY_AFTER_SEC
        return None

    def call_batch(self, calls: List[Tuple[str, list]], chunk: Optional[int] = None) -> List[Any]:
        """Runs many (method, params) calls with one HTTP request per `chunk` of them. Returns one result per call,
        in order, with None where a call errored or reverted (the same as call()). If batches are not supported, the
        calls go out one by one through call(), so the answers are the same either way."""
        n = len(calls)
        out: List[Any] = [None] * n
        size = max(1, chunk or self.BATCH_CHUNK)
        for start in range(0, n, size):
            part = calls[start:start + size]
            res = self._post_batch(part)
            if res is None:
                res = [self.call(m, p) for m, p in part]
            out[start:start + len(part)] = res
        return out


def keccak_selector(func_sig: str) -> str:
    common = {
        "name()": "0x06fdde03",
        "symbol()": "0x95d89b41",
        "decimals()": "0x313ce567",
        "totalSupply()": "0x18160ddd",
        "freeAvailable(address)": "0x9a5f5d31",
        "mint()": "0x1249c58b",
        "mint(uint8)": "0x6ecd2306",
        "quoteBNB(uint8)": "0x41aa70ca",
        "price()": "0xa035b1fe",
        "status()": "0x9508933b",
        "token()": "0xfc0c546a",
        "ownerOf(uint256)": "0x6352211e",
        "tokenURI(uint256)": "0xc87b56dd",
        "supportsInterface(bytes4)": "0x01ffc9a7",
        "paused()": "0x5c975abb",
        "MAX_SUPPLY()": "0x32cb6b0c",
        "totalMinted()": "0xa2309ff8",
        "agentKeyOf(address)": "0x1f1b9756"
    }
    if func_sig in common:
        return common[func_sig]
    try:
        from Crypto.Hash import keccak
        k = keccak.new(digest_bits=256)
        k.update(func_sig.encode("utf-8"))
        return "0x" + k.hexdigest()[:8]
    except ImportError:
        return ""


def decode_string(hex_res: str) -> str:
    try:
        raw = bytes.fromhex(hex_res[2:])
        if len(raw) < 64:
            return raw.decode("utf-8", errors="ignore").strip("\x00")
        offset = int.from_bytes(raw[0:32], "big")
        length = int.from_bytes(raw[offset:offset+32], "big")
        str_bytes = raw[offset+32:offset+32+length]
        return str_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return ""


_UNSAFE_TEXT_RE = re.compile(r"[<>`\x00-\x1f\x7f]")


def clean_chain_text(text: str, max_len: int = 80) -> str:
    """A token name / symbol read from a contract, made safe to display: whoever deploys a contract chooses its
    name(), so markup characters (< > `) and control characters are dropped and the length is capped. The page
    escapes these too; this keeps them out of the database and every client in the first place."""
    return _UNSAFE_TEXT_RE.sub("", text or "").strip()[:max_len]
