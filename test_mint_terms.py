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


class RevertRpc(FakeRpc):
    """Also reports why a simulated mint reverts: `reverts` maps a sender to {"message": ..., "data": ...}."""

    def __init__(self, *a, reverts=None, **kw):
        super().__init__(*a, **kw)
        self.reverts = {k.lower(): v for k, v in (reverts or {}).items()}

    def call_with_revert(self, method, params):
        tx = params[0]
        if not self.sims_work:
            return None, None
        if tx["from"].lower() in self.can_mint:
            return True, "0x"
        return False, self.reverts.get(tx["from"].lower(), {"message": "execution reverted"})


def code_with(*selectors):
    return "0x6080" + "".join("63" + s for s in selectors) + "00"


def test_open_mint_with_limit_and_window():
    now = 1_795_000_000
    rpc = FakeRpc(code=code_with("1249c58b", "0f2cdd6c", "78e97925", "3197cbb6"),
                  getters={"0f2cdd6c": 10, "78e97925": now - 3600, "3197cbb6": now + 7200},
                  can_mint=[PROBE, HOLDER])
    t = monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0", cap_status="capped", now=now)
    assert t["gate"] == "open"
    assert t["per_wallet"] == 10 and t["per_wallet_getter"] == "MAX_PER_WALLET()"
    assert t["start"] == now - 3600 and t["end"] == now + 7200
    assert t["cap"] == "capped" and t["checked_at"] == now


def test_agent_id_gate_when_only_holder_can_mint():
    rpc = FakeRpc(code=code_with("1249c58b"), can_mint=[HOLDER])
    assert monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0")["gate"] == "agent_id"


def test_whitelist_gate_when_merkle_root_set():
    rpc = FakeRpc(code=code_with("1249c58b", "2eb4a7ab"), getters={"2eb4a7ab": 0xDEADBEEF}, can_mint=[HOLDER])
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
        {**base, "contract": "0x" + "3" * 40, "block": 7, "mint_terms": {"gate": "open", "v": monitor.MINT_TERMS_VERSION, "checked_at": now}},   # done
        {**base, "contract": "0x" + "4" * 40, "block": 8, "mint_price_str": "DEX Traded"},  # no mint
        {**base, "contract": "0x" + "5" * 40, "block": 6, "max_supply": 10, "total_minted": 10},  # sold out
        {**base, "contract": "0x" + "6" * 40, "block": 4, "detected_at": old},               # too old
    ]
    worker._mint_terms_tried.clear()
    picked = [x["contract"][2] for x in worker._mint_terms_backfill_candidates(history, now)]
    assert picked == ["2", "5", "1", "6"]   # sold-out and old drops are in scope too (the gate is shown on every row)
    worker._mint_terms_tried["0x" + "2" * 40] = now
    assert [x["contract"][2] for x in worker._mint_terms_backfill_candidates(history, now)] == ["5", "1", "6"]
    worker._mint_terms_tried.clear()


def _err(sig):
    return {"message": "execution reverted", "data": monitor.keccak_selector(sig)}


def test_classify_revert_strings_and_custom_errors():
    c = monitor.classify_revert
    assert c({"message": "execution reverted: Agent ID required"}) == "agent_id"
    assert c({"message": "execution reverted: No tokens to claim"}) == "whitelist"
    assert c({"message": "execution reverted: wrong fee"}) == "payment"
    assert c({"message": "execution reverted: Mint not started"}) == "not_open"
    assert c(_err("AgentIdRequired()")) == "agent_id"
    assert c(_err("NoAgent()")) == "agent_id"
    assert c(_err("NothingToClaim()")) == "whitelist"
    assert c(_err("InsufficientBNB()")) == "payment"
    assert c(_err("OverMaxCost()")) == "payment"
    err_string = "0x08c379a0" + "20".zfill(64) + "11".zfill(64) + b"Agent ID required".hex().ljust(64, "0")
    assert c({"message": "execution reverted", "data": err_string}) == "agent_id"
    assert c({"message": "execution reverted", "data": "0xcfa6d878"}) is None
    assert c(None) is None


def test_gate_from_revert_reason_even_when_holder_fails_on_price():
    # CHASTLE: fresh wallet "Agent ID required", Agent ID wallet "wrong fee" (price never read)
    rpc = RevertRpc(code=code_with("1249c58b"), reverts={PROBE: {"message": "execution reverted: Agent ID required"},
                                                         HOLDER: {"message": "execution reverted: wrong fee"}})
    t = monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0")
    assert t["gate"] == "agent_id" and t["paid"] is True and t["v"] == monitor.MINT_TERMS_VERSION


def test_claim_contract_is_whitelist():
    rpc = RevertRpc(code=code_with("4e71d92d"), reverts={PROBE: _err("NothingToClaim()"), HOLDER: _err("NothingToClaim()")})
    assert monitor.probe_mint_terms(rpc, "0xabc", "0x4e71d92d", "0")["gate"] == "whitelist"


def test_timing_refusal_sets_state_not_gate():
    rpc = RevertRpc(code=code_with("1249c58b"), reverts={PROBE: _err("MintEnded()"), HOLDER: _err("MintEnded()")})
    t = monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0")
    assert t["gate"] == "unknown" and t["state"] == "ended" and "paid" not in t


def test_deploy_bytecode_or_foreign_selector_is_not_a_mint_call():
    deploy = "0x60806040523480156100" + "ab" * 1200
    rpc = RevertRpc(code=code_with("1249c58b"), can_mint=[PROBE])
    t = monitor.probe_mint_terms(rpc, "0xabc", deploy, "0")
    assert t["mint_call"] == "invalid" and t["gate"] == "unknown"
    assert monitor.mint_call_is_usable("0xdeadbeef", code_with("1249c58b")) is False
    assert monitor.mint_call_is_usable("0x1249c58b", code_with("1249c58b")) is True
    assert monitor.mint_call_is_usable("0x", code_with()) is True
    assert monitor.mint_call_is_usable("0xdeadbeef", "") is True, "unknown bytecode: benefit of the doubt"


def test_mint_open_switch():
    sel = monitor.keccak_selector("mintOpen()")[2:]
    closed = monitor.probe_mint_terms(FakeRpc(code=code_with(sel), getters={sel: 0}, can_mint=[PROBE]), "0xabc", "0x1249c58b", "0")
    assert closed["open"] is False
    opened = monitor.probe_mint_terms(FakeRpc(code=code_with(sel), getters={sel: 1}, can_mint=[PROBE]), "0xabc", "0x1249c58b", "0")
    assert opened["open"] is True


def test_backfill_reprobes_old_versions_and_stale_recent_terms():
    now = time.time()
    ts = lambda sec_ago: time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now - sec_ago))
    base = {"name": "X", "standard": "ERC-721", "mint_price_str": "⭐ FREE"}
    v = monitor.MINT_TERMS_VERSION
    history = [
        {**base, "contract": "0x" + "1" * 40, "block": 1, "detected_at": ts(3600), "mint_terms": {"gate": "open", "checked_at": now - 60}},          # v1 -> re-probe
        {**base, "contract": "0x" + "2" * 40, "block": 2, "detected_at": ts(3600), "mint_terms": {"gate": "open", "v": v, "checked_at": now - 3 * 3600}},  # recent, stale
        {**base, "contract": "0x" + "3" * 40, "block": 3, "detected_at": ts(3600), "mint_terms": {"gate": "open", "v": v, "checked_at": now - 60}},  # fresh
        {**base, "contract": "0x" + "4" * 40, "block": 4, "detected_at": ts(7 * 86400), "mint_terms": {"gate": "open", "v": v, "checked_at": now - 9 * 3600}},  # 7d old: no refresh
        {**base, "contract": "0x" + "5" * 40, "block": 5, "detected_at": ts(3600)},                                                                     # missing: first
    ]
    worker._mint_terms_tried.clear()
    assert [x["contract"][2] for x in worker._mint_terms_backfill_candidates(history, now)] == ["5", "2", "1"]
    worker._mint_terms_tried.clear()


def test_bytecode_agent_check_is_the_fallback_when_simulation_cannot_decide():
    agent_err = monitor.keccak_selector("AgentIdRequired()")[2:]
    # sold out: both wallets refused for supply; the code itself carries the Agent ID error
    rpc = RevertRpc(code=code_with("1249c58b", agent_err), reverts={PROBE: _err("SoldOut()"), HOLDER: _err("SoldOut()")})
    t = monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0")
    assert t["gate"] == "agent_id" and t["gate_from"] == "bytecode" and t["state"] == "sold_out"
    # the "Agent ID" revert text in the bytecode counts too
    text_code = code_with("1249c58b") + "Agent ID required".encode().hex()
    rpc = RevertRpc(code=text_code, reverts={PROBE: {"message": "execution reverted"}, HOLDER: {"message": "execution reverted"}})
    assert monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0")["gate"] == "agent_id"
    # a drop anyone can mint stays open even if its code mentions an Agent ID
    rpc = RevertRpc(code=code_with("1249c58b", agent_err), can_mint=[PROBE])
    t = monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0")
    assert t["gate"] == "open" and "gate_from" not in t
    # no marker, nothing decided: unknown
    rpc = RevertRpc(code=code_with("1249c58b"), reverts={PROBE: _err("SoldOut()"), HOLDER: _err("SoldOut()")})
    assert monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0")["gate"] == "unknown"


def test_learned_agent_error_codes():
    assert monitor.classify_revert({"message": "execution reverted", "data": "0xea11784e"}) == "agent_id"
    assert monitor.classify_revert({"message": "execution reverted", "data": "0x3cb97240"}) == "agent_id"


def test_backfill_covers_every_drop_with_a_mint_of_any_age():
    now = time.time()
    old = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now - 60 * 86400))
    base = {"name": "X", "standard": "ERC-721", "mint_price_str": "⭐ FREE", "detected_at": old}
    history = [
        {**base, "contract": "0x" + "1" * 40, "block": 1},                                         # 60 days old: probed
        {**base, "contract": "0x" + "2" * 40, "block": 2, "max_supply": 5, "total_minted": 5},     # sold out: probed
        {**base, "contract": "0x" + "3" * 40, "block": 3, "mint_price_str": "DEX Traded"},         # no mint: skipped
    ]
    worker._mint_terms_tried.clear()
    assert [x["contract"][2] for x in worker._mint_terms_backfill_candidates(history, now)] == ["2", "1"]
    worker._mint_terms_tried.clear()
