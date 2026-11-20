"""monitor.probe_mint_terms / read_supply cap status / worker mint-terms backfill, against a fake RPC."""
import time

import monitor
import worker

PROBE = monitor.MINT_PROBE_ADDRESS.lower()
HOLDER = monitor.DEFAULT_WALLET.lower()
WORD = lambda v: "0x" + format(v, "064x")


class FakeRpc:
    """Answers getters from `getters` (selector hex -> int) and mint simulations from `can_mint` (senders that succeed)."""

    def __init__(self, code="", getters=None, can_mint=(), sims_work=True):
        self.code = code
        self.getters = getters or {}
        self.can_mint = {w.lower() for w in can_mint}
        self.sims_work = sims_work
        self.calls = []

    def get_code(self, address):
        return self.code

    def call_contract(self, to, data):
        self.calls.append(data)
        sel = data[2:10]
        return WORD(self.getters[sel]) if sel in self.getters else None

    def call(self, method, params):
        assert method == "eth_call"
        tx = params[0]
        assert len(params) == 3 and tx["from"] in params[2], "the sender gets a balance override"
        if not self.sims_work:
            return None
        return "0x" if tx["from"].lower() in self.can_mint else None


def code_with(*selectors):
    return "0x6080" + "".join("63" + s for s in selectors) + "00"


def test_open_mint_with_limit_and_window():
    now = 1_795_000_000
    rpc = FakeRpc(code=code_with("0f2cdd6c", "78e97925", "3197cbb6"),
                  getters={"0f2cdd6c": 10, "78e97925": now - 3600, "3197cbb6": now + 7200},
                  can_mint=[PROBE, HOLDER])
    t = monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0", cap_status="capped", now=now)
    assert t["gate"] == "open"
    assert t["per_wallet"] == 10 and t["per_wallet_getter"] == "MAX_PER_WALLET()"
    assert t["start"] == now - 3600 and t["end"] == now + 7200
    assert t["cap"] == "capped" and t["checked_at"] == now


def test_agent_id_gate_when_only_holder_can_mint():
    rpc = FakeRpc(code=code_with(), can_mint=[HOLDER])
    assert monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0")["gate"] == "agent_id"


def test_whitelist_gate_when_merkle_root_set():
    rpc = FakeRpc(code=code_with("2eb4a7ab"), getters={"2eb4a7ab": 0xDEADBEEF}, can_mint=[HOLDER])
    assert monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0")["gate"] == "whitelist"


def test_unknown_gate_when_nothing_can_mint_or_rpc_down():
    assert monitor.probe_mint_terms(FakeRpc(code=code_with()), "0xabc", "0x1249c58b", "0")["gate"] == "unknown"
    rpc = FakeRpc(code=code_with(), can_mint=[PROBE, HOLDER], sims_work=False)
    assert monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0")["gate"] == "unknown"


def test_getters_absent_from_bytecode_are_never_called():
    rpc = FakeRpc(code=code_with(), getters={"0f2cdd6c": 10}, can_mint=[PROBE])
    t = monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0")
    assert "per_wallet" not in t and rpc.calls == []


def test_implausible_values_are_ignored():
    rpc = FakeRpc(code=code_with("0f2cdd6c", "78e97925", "3197cbb6"),
                  getters={"0f2cdd6c": 2**255, "78e97925": 0, "3197cbb6": 42}, can_mint=[PROBE])
    t = monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0")
    assert "per_wallet" not in t and "start" not in t and "end" not in t


class SupplyRpc:
    def __init__(self, answers):
        self.answers = answers

    def call_contract(self, to, data):
        return self.answers.get(data)


def test_read_supply_reports_cap_status():
    max_sel = monitor.keccak_selector("maxSupply()")
    meta = {}
    assert monitor.read_supply(SupplyRpc({max_sel: WORD(1000)}), ["0xabc"], meta=meta)[0] == 1000
    assert meta["cap_status"] == "capped"
    meta = {}
    assert monitor.read_supply(SupplyRpc({max_sel: WORD(2**256 - 1)}), ["0xabc"], meta=meta)[0] is None
    assert meta["cap_status"] == "unlimited"
    meta = {}
    monitor.read_supply(SupplyRpc({}), ["0xabc"], meta=meta)
    assert meta["cap_status"] == "unknown"


def test_backfill_picks_recent_open_mints_without_terms():
    now = time.time()
    fresh = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now - 3600))
    old = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now - 30 * 86400))
    base = {"name": "X", "standard": "ERC-721", "mint_price_str": "⭐ FREE", "detected_at": fresh}
    history = [
        {**base, "contract": "0x" + "1" * 40, "block": 5},
        {**base, "contract": "0x" + "2" * 40, "block": 9},
        {**base, "contract": "0x" + "3" * 40, "block": 7, "mint_terms": {"gate": "open"}},   # done
        {**base, "contract": "0x" + "4" * 40, "block": 8, "mint_price_str": "DEX Traded"},  # no mint
        {**base, "contract": "0x" + "5" * 40, "block": 6, "max_supply": 10, "total_minted": 10},  # sold out
        {**base, "contract": "0x" + "6" * 40, "block": 4, "detected_at": old},               # too old
    ]
    worker._mint_terms_tried.clear()
    picked = [x["contract"][2] for x in worker._mint_terms_backfill_candidates(history, now)]
    assert picked == ["2", "1"]
    worker._mint_terms_tried["0x" + "2" * 40] = now
    assert [x["contract"][2] for x in worker._mint_terms_backfill_candidates(history, now)] == ["1"]
    worker._mint_terms_tried.clear()
