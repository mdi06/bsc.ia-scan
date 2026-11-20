import importlib.util, os

spec = importlib.util.spec_from_file_location("purge", os.path.join(os.path.dirname(__file__), "scripts", "purge_non_agent_drops.py"))
purge = importlib.util.module_from_spec(spec); spec.loader.exec_module(purge)


def rec(c, signal):
    return {"contract": c, "symbol": "S", "name": "N", "signals": [signal] if signal else []}


def test_only_smart_wallet_discovered_unlinked_records_are_candidates(monkeypatch):
    linked = "0x" + "a" * 40
    unlinked = "0x" + "b" * 40
    registry_found = "0x" + "c" * 40          # found by the deploy path: never a candidate, even if unlinked
    monkeypatch.setattr(purge.monitor, "contract_links_to_registry", lambda rpc, a: a == linked)
    records = [rec(linked, "Discovered via smart wallet x.ai"), rec(unlinked, "Discovered via smart wallet y.ai"), rec(registry_found, "Block-0 registry mint")]
    out = purge.find_candidates(records, rpc=None, workers=2)
    assert [r["contract"] for r in out] == [unlinked]


def test_limit_caps_the_records_checked(monkeypatch):
    monkeypatch.setattr(purge.monitor, "contract_links_to_registry", lambda rpc, a: False)
    records = [rec("0x" + str(i) * 40, "Discovered via smart wallet z.ai") for i in range(5)]
    assert len(purge.find_candidates(records, None, 2, limit=3)) == 3


def test_apply_needs_a_token_and_respects_the_cap(monkeypatch, capsys):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(purge, "fetch_history", lambda b: [])
    assert purge.main(["--apply"]) == 2
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    monkeypatch.setattr(purge, "fetch_history", lambda b: [rec("0x" + str(i) * 40, "Discovered via smart wallet a.ai") for i in range(3)])
    monkeypatch.setattr(purge.monitor, "contract_links_to_registry", lambda rpc, a: False)
    deleted = []
    monkeypatch.setattr(purge, "delete_remote", lambda b, t, c: deleted.append(c) or True)
    assert purge.main(["--apply", "--max-delete", "2", "--csv", os.devnull]) == 3
    assert deleted == []
