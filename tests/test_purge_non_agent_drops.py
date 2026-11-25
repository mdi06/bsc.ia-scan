import importlib.util, os

spec = importlib.util.spec_from_file_location("purge", os.path.join(os.path.dirname(__file__), "scripts", "purge_non_agent_drops.py"))
purge = importlib.util.module_from_spec(spec); spec.loader.exec_module(purge)


class FakeRpc:
    """tx_hash -> calldata; a hash not in the table is an unknown transaction."""
    def __init__(self, inputs):
        self.inputs = inputs

    def call(self, method, params):
        inp = self.inputs.get(params[0])
        return {"input": inp} if inp is not None else None


def rec(c, signal, tx=None):
    return {"contract": c, "symbol": "S", "name": "N", "signals": [signal] if signal else [], "tx_hash": tx}


def test_only_approve_triggered_unlinked_smart_wallet_records_are_candidates(monkeypatch):
    junk = "0x" + "a" * 40          # approve-triggered, unlinked  -> candidate
    linked = "0x" + "b" * 40        # approve-triggered but linked -> kept
    minted = "0x" + "c" * 40        # mint-triggered               -> kept
    other_path = "0x" + "d" * 40    # not smart-wallet discovered  -> kept
    no_tx = "0x" + "e" * 40         # no trigger tx recorded       -> kept
    monkeypatch.setattr(purge.monitor, "contract_links_to_registry", lambda rpc, a: a == linked)
    rpc = FakeRpc({"t1": "0x095ea7b3" + "0" * 128, "t2": "0x095ea7b3" + "0" * 128, "t3": "0x1249c58b", "t4": "0xa9059cbb" + "0" * 128})
    sw = "Discovered via smart wallet x.ai"
    records = [rec(junk, sw, "t1"), rec(linked, sw, "t2"), rec(minted, sw, "t3"), rec(other_path, "Block-0 registry mint", "t4"), rec(no_tx, sw, None)]
    out = purge.find_candidates(records, rpc, workers=2)
    assert [r["contract"] for r in out] == [junk]


def test_limit_caps_the_records_checked(monkeypatch):
    monkeypatch.setattr(purge.monitor, "contract_links_to_registry", lambda rpc, a: False)
    approve = "0x095ea7b3" + "0" * 128
    rpc = FakeRpc({"t": approve})
    records = [rec("0x" + str(i) * 40, "Discovered via smart wallet z.ai", "t") for i in range(5)]
    assert len(purge.find_candidates(records, rpc, 2, limit=3)) == 3


def test_apply_needs_a_token_and_respects_the_cap(monkeypatch, capsys):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(purge, "fetch_history", lambda b: [])
    assert purge.main(["--apply"]) == 2
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    monkeypatch.setattr(purge, "fetch_history", lambda b: [rec("0x" + str(i) * 40, "Discovered via smart wallet a.ai", "t") for i in range(3)])
    monkeypatch.setattr(purge.monitor, "BscRpcClient", lambda *a, **k: FakeRpc({"t": "0x095ea7b3" + "0" * 128}))
    monkeypatch.setattr(purge.monitor, "contract_links_to_registry", lambda rpc, a: False)
    deleted = []
    monkeypatch.setattr(purge, "delete_remote", lambda b, t, c: deleted.append(c) or True)
    assert purge.main(["--apply", "--max-delete", "2", "--csv", os.devnull]) == 3
    assert deleted == []
