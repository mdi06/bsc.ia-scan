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
    # the stored call is unusable, but the contract's own mint() works for anyone (probe v4 tries it)
    assert t["mint_call"] == "invalid" and t["gate"] == "open" and t["alt_call"] == "mint()"
    # with no public mint function in the code at all, it stays unknown
    t = monitor.probe_mint_terms(RevertRpc(code=code_with("deadbeef"), can_mint=[PROBE]), "0xabc", deploy, "0")
    assert t["mint_call"] == "invalid" and t["gate"] == "unknown" and "alt_call" not in t
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


def test_backfill_puts_the_newest_drops_first_even_if_older_ones_have_no_terms():
    now = time.time()
    ts = lambda sec_ago: time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now - sec_ago))
    base = {"name": "X", "standard": "ERC-721", "mint_price_str": "⭐ FREE"}
    history = [
        {**base, "contract": "0x" + "a" * 40, "block": 100, "detected_at": ts(40 * 86400)},                              # old, no terms
        {**base, "contract": "0x" + "b" * 40, "block": 900, "detected_at": ts(3600), "mint_terms": {"gate": "open"}},  # newest, v1 terms
        {**base, "contract": "0x" + "c" * 40, "block": 500, "detected_at": ts(20 * 86400)},                           # older, no terms
    ]
    worker._mint_terms_tried.clear()
    assert [x["contract"][2] for x in worker._mint_terms_backfill_candidates(history, now)] == ["b", "c", "a"]
    worker._mint_terms_tried.clear()


def test_alternative_mint_function_settles_the_gate_and_is_offered_as_repair():
    mint_sel = "1249c58b"
    deploy = "0x60806040523480156100" + "ab" * 1200
    # stored call is deployment bytecode; the contract's own mint() refuses a fresh wallet for its Agent ID
    rpc = RevertRpc(code=code_with(mint_sel), can_mint=[HOLDER], reverts={PROBE: _err("AgentIdRequired()")})
    t = monitor.probe_mint_terms(rpc, "0xabc", deploy, "0")
    assert t["mint_call"] == "invalid" and t["gate"] == "agent_id"
    assert t["alt_call"] == "mint()" and t["working_call"] == "0x" + mint_sel
    item = {"mint_calldata": deploy}
    worker._apply_mint_terms(item, t)
    assert item["mint_calldata"] == "0x1249c58b" and item["mint_terms"]["mint_call"] == "repaired"


def test_owner_only_stored_call_falls_back_to_public_mint():
    owner_fn = "fca3b5aa"
    rpc = RevertRpc(code=code_with(owner_fn, "1249c58b"))
    rpc.can_mint = set()
    # the owner-only stored call is refused with "not owner"; mint() works for anyone
    class R(RevertRpc):
        def call_with_revert(self, method, params):
            data = params[0]["data"]
            if data.startswith("0x" + owner_fn):
                return False, {"message": "execution reverted: not owner"}
            return True, "0x"
    rpc = R(code=code_with(owner_fn, "1249c58b"))
    t = monitor.probe_mint_terms(rpc, "0xabc", "0x" + owner_fn + "1".zfill(64), "0")
    assert t["gate"] == "open" and t["alt_call"] == "mint()"


def test_no_alternative_keeps_unknown_and_never_repairs():
    rpc = RevertRpc(code=code_with("1249c58b"), reverts={PROBE: _err("SoldOut()"), HOLDER: _err("SoldOut()")})
    t = monitor.probe_mint_terms(rpc, "0xabc", "0x1249c58b", "0")
    assert t["gate"] == "unknown" and "working_call" not in t and "alt_call" not in t
    item = {"mint_calldata": "0x1249c58b"}
    worker._apply_mint_terms(item, t)
    assert item["mint_calldata"] == "0x1249c58b"


def test_new_revert_phrases():
    c = monitor.classify_revert
    assert c({"message": "execution reverted: not on sale"}) == "not_open"
    assert c({"message": "execution reverted: Ownable: caller is not the owner"}) == "owner_only"
    assert c({"message": "execution reverted: BEP20: transfer amount exceeds balance"}) == "payment"
    assert c({"message": "execution reverted: Unauthorized: Agent ID required"}) == "agent_id"


def test_a_proven_gate_survives_a_recheck_that_cannot_decide():
    item = {"mint_terms": {"gate": "agent_id", "v": 3}}
    worker._apply_mint_terms(item, {"gate": "unknown", "state": "not_open", "v": monitor.MINT_TERMS_VERSION})
    assert item["mint_terms"]["gate"] == "agent_id" and item["mint_terms"]["gate_from"] == "earlier_check"
    assert item["mint_terms"]["state"] == "not_open"
    # a decisive new answer always wins
    item = {"mint_terms": {"gate": "agent_id"}}
    worker._apply_mint_terms(item, {"gate": "open"})
    assert item["mint_terms"]["gate"] == "open"
    # nothing proven before: unknown stays unknown
    item = {"mint_terms": {"gate": "unknown"}}
    worker._apply_mint_terms(item, {"gate": "unknown"})
    assert item["mint_terms"]["gate"] == "unknown"


# --- probe v5: what the remaining "unknown" drops showed on the live chain ---

def _err_args(sig, *args):
    return {"message": "execution reverted", "data": monitor.keccak_selector(sig) + "".join(format(a, "064x") for a in args)}


class ValueRpc(RevertRpc):
    """Mints go through only for `can_mint` senders who send at least `price` wei; below it, `low` is the refusal."""

    def __init__(self, *a, price=0, low=None, others=None, **kw):
        super().__init__(*a, **kw)
        self.price, self.low, self.others, self.values = price, low, others, []

    def call_with_revert(self, method, params):
        tx = params[0]
        v = int(tx["value"], 16)
        self.values.append(v)
        if v < self.price:
            return False, self.low
        if tx["from"].lower() in self.can_mint:
            return True, "0x"
        return False, self.others or {"message": "execution reverted"}


def test_price_named_in_the_refusal_is_retried():
    # Cinnavault: EitherPrice(low, high, sent) for 0 BNB; with the price only the Agent ID wallet mints
    price = 10**15
    rpc = ValueRpc(code=code_with("a0712d68"), can_mint=[HOLDER], price=price,
                   low=_err_args("EitherPrice(uint256,uint256,uint256)", 10**14, price, 0),
                   others=_err("AgentIdRequired()"))
    t = monitor.probe_mint_terms(rpc, "0xabc", "0xa0712d68" + "1".zfill(64), "0")
    assert t["gate"] == "agent_id" and t["price_wei"] == price
    assert price in rpc.values


def test_payment_refusal_without_a_price_is_not_retried():
    rpc = ValueRpc(code=code_with("a0712d68"), price=10**30, low=_err("InsufficientBNB()"))
    t = monitor.probe_mint_terms(rpc, "0xabc", "0xa0712d68" + "1".zfill(64), "0")
    assert t["gate"] == "unknown" and "price_wei" not in t and set(rpc.values) == {0}


def test_free_mint_refusing_bnb_is_retried_without_value():
    # RUMBLE: mintFree() is not payable, so the stored 0.001 BNB is refused with a bare revert
    class R(RevertRpc):
        def call_with_revert(self, method, params):
            tx = params[0]
            if int(tx["value"], 16) > 0:
                return False, {"message": "execution reverted"}
            if tx["from"].lower() == HOLDER:
                return True, "0x"
            return False, {"message": "execution reverted: Agent ID required"}
    t = monitor.probe_mint_terms(R(code=code_with("8ab53447")), "0xabc", "0x8ab53447", str(10**15))
    assert t["gate"] == "agent_id"


def test_has_agent_id_function_in_bytecode():
    sel = monitor.keccak_selector("hasAgentId(address)")[2:]
    assert monitor.has_agent_id_check(code_with(sel))
    rpc = RevertRpc(code=code_with(sel, "a0712d68"), reverts={PROBE: _err("SoldOut()"), HOLDER: _err("SoldOut()")})
    t = monitor.probe_mint_terms(rpc, "0xabc", "0xa0712d68" + "1".zfill(64), "0")
    assert t["gate"] == "agent_id" and t["gate_from"] == "bytecode"


def test_claim_by_token_id_is_holders_only():
    # Vitaliq: claim(tokenId) on a contract with claimedById(uint256); NotOwner() means "not that NFT's owner"
    claim = monitor.keccak_selector("claim(uint256)")[2:]
    marker = monitor.keccak_selector("claimedById(uint256)")[2:]
    rpc = RevertRpc(code=code_with(claim, marker), reverts={PROBE: _err("NotOwner()"), HOLDER: _err("NotOwner()")})
    t = monitor.probe_mint_terms(rpc, "0xabc", "0x" + claim + "1".zfill(64), "0")
    assert t["gate"] == "whitelist" and t["gate_from"] == "holders"


def test_owner_only_claim_without_per_id_tracking_stays_admin():
    claim = monitor.keccak_selector("claim(uint256)")[2:]
    rpc = RevertRpc(code=code_with(claim), reverts={PROBE: _err("NotOwner()"), HOLDER: _err("NotOwner()")})
    assert monitor.probe_mint_terms(rpc, "0xabc", "0x" + claim + "1".zfill(64), "0")["gate"] == "unknown"


def test_holder_required_and_not_token_owner_errors():
    assert monitor.classify_revert(_err("NotTokenOwner()")) == "holder_only"
    assert monitor.classify_revert(_err("HolderRequired()")) == "holder_only"
    assert monitor.classify_revert({"message": "execution reverted: mint over"}) == "ended"
    assert monitor.classify_revert(_err("GameNotSet()")) == "not_open"
    assert monitor.classify_revert(_err_args("PayRequired(uint256,uint256,uint256)", 1, 2, 0)) == "payment"


def test_start_time_named_in_a_not_open_refusal():
    start = 1_800_000_000
    rpc = RevertRpc(code=code_with("a0712d68"), reverts={PROBE: _err_args("NotOpenYet(uint256)", start),
                                                         HOLDER: _err_args("NotOpenYet(uint256)", start)})
    t = monitor.probe_mint_terms(rpc, "0xabc", "0xa0712d68" + "1".zfill(64), "0")
    assert t["gate"] == "unknown" and t["state"] == "not_open" and t["start"] == start


def test_no_public_mint_function():
    # the Agent ID registry contracts: no stored call, a plain transfer refused, no mint function in the code
    rpc = RevertRpc(code=code_with("12345678"))
    t = monitor.probe_mint_terms(rpc, "0xabc", "", "0")
    assert t["gate"] == "unknown" and t["mint_call"] == "none"
    # with a mint function present it is tried instead, and never marked "none"
    t2 = monitor.probe_mint_terms(RevertRpc(code=code_with("1249c58b")), "0xabc", "", "0")
    assert t2.get("mint_call") != "none"
