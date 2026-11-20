"""Smart-wallet calls only create a drop for contracts linked to the Agent ID registry (and not for plain
approve/transfer calls). Regression for USD1 / SOON / Tether Gold appearing as drops."""
import monitor
import worker

REG = monitor.AGENT_ID_REGISTRY.lower()[2:]
IMPL_SLOT = monitor.EIP1967_IMPL_SLOT


class FakeRpc:
    def __init__(self, code=None, slots=None):
        self.code = {k.lower(): v for k, v in (code or {}).items()}
        self.slots = {k.lower(): v for k, v in (slots or {}).items()}

    def get_code(self, a):
        return self.code.get(a.lower(), "0x")

    def get_storage_at(self, a, slot):
        return self.slots.get((a.lower(), slot), self.slots.get(a.lower(), "0x" + "0" * 64) if slot == IMPL_SLOT else "0x" + "0" * 64)

    def call_contract(self, to, data):
        return None


def test_non_mint_selectors():
    approve = "0x095ea7b3" + "0" * 128
    transfer = "0xa9059cbb" + "0" * 128
    assert monitor.is_non_mint_call(approve)
    assert monitor.is_non_mint_call(transfer.upper())
    assert not monitor.is_non_mint_call("0x1249c58b")          # mint()
    assert not monitor.is_non_mint_call("0xa0712d68" + "0" * 64)  # mint(uint256)
    assert not monitor.is_non_mint_call("0x") and not monitor.is_non_mint_call("")


def test_plain_token_has_no_registry_link():
    rpc = FakeRpc(code={"0xaaa": "0x6080604052" + "ab" * 50})
    assert monitor.contract_links_to_registry(rpc, "0xaaa") is False


def test_contract_referencing_registry_is_linked():
    rpc = FakeRpc(code={"0xaaa": "0x6080" + "11" * 10 + REG + "22" * 10})
    assert monitor.contract_links_to_registry(rpc, "0xaaa") is True


def test_proxy_is_linked_through_its_implementation():
    impl = "0x" + "bb" * 20
    rpc = FakeRpc(code={"0xaaa": "0x363d3d37", impl: "0x6080" + REG},
                  slots={"0xaaa": "0x" + "0" * 24 + "bb" * 20})
    assert monitor.contract_links_to_registry(rpc, "0xaaa") is True


def test_proxy_to_plain_implementation_is_not_linked():
    impl = "0x" + "bb" * 20
    rpc = FakeRpc(code={"0xaaa": "0x363d3d37", impl: "0x6080" + "cd" * 40},
                  slots={"0xaaa": "0x" + "0" * 24 + "bb" * 20})
    assert monitor.contract_links_to_registry(rpc, "0xaaa") is False


def test_rpc_failure_is_not_linked():
    class Boom:
        def get_code(self, a):
            raise RuntimeError("rpc down")
    assert monitor.contract_links_to_registry(Boom(), "0xaaa") is False


def _run_candidate(monkeypatch, rpc, inp):
    """Runs _process_smart_money_candidate; returns True when it went on to analyze the contract."""
    called = []
    monkeypatch.setattr(worker.monitor, "analyze_contract", lambda *a, **k: called.append(1) or (_ for _ in ()).throw(RuntimeError("stop")))
    worker._process_smart_money_candidate(rpc, "0xaaa", "0xsnd", "x.ai", "0xtx", 1, 0, inp, "0xwallet")
    return bool(called)


def test_gate_blocks_approve_and_transfer_but_allows_mint_calls(monkeypatch):
    rpc = FakeRpc(code={"0xaaa": "0x6080" + "cd" * 40})   # no registry link: launchpad tokens look like this
    assert _run_candidate(monkeypatch, rpc, "0x095ea7b3" + "0" * 128) is False   # approve
    assert _run_candidate(monkeypatch, rpc, "0xa9059cbb" + "0" * 128) is False   # transfer
    assert _run_candidate(monkeypatch, rpc, "0x1249c58b") is True                # mint(): analyzed as before
    assert _run_candidate(monkeypatch, rpc, "0x") is True                        # plain payable send: analyzed as before
