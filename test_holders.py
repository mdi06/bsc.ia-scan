"""Real NFT holder counts (holders.py) and /api/holders."""
import pytest
from fastapi.testclient import TestClient

import api
import db as db_module
import holders
import monitor


def _word(n):
    return format(n, "064x")


def encode_results(results):
    """ABI encoding of Multicall3.aggregate3's (bool success, bytes returnData)[] for the given owners."""
    elems = []
    for owner in results:
        data = ("0" * 24 + owner[2:]) if owner else ""
        ln = len(data) // 2
        elems.append(_word(1 if owner else 0) + _word(64) + _word(ln) + data)
    offs, off = [], 32 * len(results)
    for e in elems:
        offs.append(_word(off))
        off += len(e) // 2
    return "0x" + _word(32) + _word(len(results)) + "".join(offs) + "".join(elems)


def decode_owner_calls(calldata):
    """The token ids asked for in an aggregate3 calldata built by holders.encode_aggregate3."""
    body = calldata[2 + 8:]
    n = int(body[64:128], 16)
    ids = []
    for i in range(n):
        off = 128 + int(body[128 + 64 * i:192 + 64 * i], 16) * 2      # offsets count from after the length word
        el = body[off:]
        ln = int(el[64 * 3:64 * 4], 16)
        cd = el[64 * 4:64 * 4 + ln * 2]
        assert cd[:8] == holders.OWNER_OF
        ids.append(int(cd[8:], 16))
    return ids


class FakeRpc:
    def __init__(self, owner_of, fail=False):
        self.owner_of = owner_of          # token id -> owner address (missing = reverts)
        self.calls = 0
        self.fail = fail

    def call(self, method, params):
        assert method == "eth_call" and params[0]["to"] == holders.MULTICALL3
        self.calls += 1
        if self.fail:
            return None
        return encode_results([self.owner_of.get(i) for i in decode_owner_calls(params[0]["data"])])


def wallet(n):
    return "0x" + format(n, "040x")


def test_encoding_round_trip():
    ids = [0, 1, 7, 499]
    assert decode_owner_calls(holders.encode_aggregate3(wallet(5), ["0x6352211e" + _word(i) for i in ids])) == ids
    out = holders.decode_aggregate3(encode_results([wallet(9), None]))
    assert out[0][-20:].hex() == wallet(9)[2:] and out[1] is None


def test_counts_distinct_owners_from_token_one_in_batches():
    owners = {i: wallet(i % 300 + 1) for i in range(1, 1201)}            # 1,200 tokens, 300 wallets, ids from 1
    rpc = FakeRpc(owners)
    assert holders.count_holders(rpc, wallet(77), 1200) == 300
    assert rpc.calls == 1 + 3                                              # the 0/1 probe + 3 batches of 500


def test_burned_tokens_are_skipped_but_scattered_ids_are_unsupported():
    owners = {i: wallet(i + 1) for i in range(0, 100)}
    for i in (3, 4, 5):
        owners.pop(i)                                                      # burned
    owners[7] = "0x" + "0" * 40
    assert holders.count_holders(FakeRpc(owners), wallet(1), 100) == 96
    with pytest.raises(holders.Unsupported):
        holders.count_holders(FakeRpc({i: wallet(i + 1) for i in range(0, 50)}), wallet(1), 100)
    with pytest.raises(holders.Unsupported):
        holders.count_holders(FakeRpc({}), wallet(1), 10)                  # no ownerOf at all (ERC-1155, a token)


def test_countable_minted():
    nft = {"standard": "ERC-721 (NFT Collection)"}
    assert holders.countable_minted(dict(nft, total_minted=1551)) == 1551
    assert holders.countable_minted(dict(nft, total_minted=0)) is None
    assert holders.countable_minted(dict(nft, total_minted=1_589_678)) is None       # over the cap
    assert holders.countable_minted({"standard": "ERC-1155", "total_minted": 10}) is None
    assert holders.countable_minted({"standard": "BEP-20 Token (18 decimals)", "total_minted": 10}) is None


def test_counts_are_reused_refreshed_and_kept_through_rpc_failures():
    now = [1000.0]
    rpc = FakeRpc({i: wallet(i % 10 + 1) for i in range(1, 51)})
    hc = holders.HolderCounts(lambda: rpc, now=lambda: now[0])
    assert hc.get(wallet(1), 50) == {"status": "ok", "holders": 10, "counted_at": 1000.0}
    assert hc.version == 1 and hc.overlay(wallet(1).upper().replace("0X", "0x")) == {"holders": 10, "at": 1000.0}
    calls = rpc.calls
    now[0] += holders.TTL_SEC - 1
    assert hc.get(wallet(1), 50)["holders"] == 10 and rpc.calls == calls        # reused, no RPC
    now[0] += 2
    rpc.fail = True
    r = hc.get(wallet(1), 50)                                                  # stale: recount fails
    assert r["status"] == "error" and r["holders"] == 10                       # the last good count is kept
    assert hc.overlay(wallet(1))["holders"] == 10 and hc.version == 1          # nothing served changed
    calls = rpc.calls
    hc.get(wallet(1), 50)
    assert rpc.calls == calls                                                  # no retry before FAIL_RETRY_SEC
    now[0] += holders.FAIL_RETRY_SEC
    rpc.fail = False
    rpc.owner_of[51] = wallet(99)
    assert hc.get(wallet(1), 51)["holders"] == 11 and hc.version == 2


def test_unsupported_is_remembered():
    rpc = FakeRpc({})
    hc = holders.HolderCounts(lambda: rpc)
    assert hc.get(wallet(2), 10) == {"status": "unsupported"}
    calls = rpc.calls
    assert hc.get(wallet(2), 10) == {"status": "unsupported"} and rpc.calls == calls
    assert hc.overlay(wallet(2)) == {"unsupported": True}


# ------------------------------------------------------------------------------------------------ /api/holders
@pytest.fixture
def client(tmp_path, monkeypatch):
    for var in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))
    monkeypatch.setattr(db_module, "FAVORITES_FILE", str(tmp_path / "no_favorites.json"))
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(api, "db", manager)
    monkeypatch.setattr(api, "_current_snapshot", None)
    monkeypatch.setattr(api, "RATE_LIMIT_MAX", 10 ** 9)
    monkeypatch.setattr(monitor, "load_agent_id_stats", lambda: {"total_created_wallets": 1, "last_block": 5})
    rpc = FakeRpc({i: wallet(i % 949 + 1) for i in range(1, 1552)})
    monkeypatch.setattr(api, "HOLDER_COUNTS", holders.HolderCounts(lambda: rpc))
    manager.upsert_projects([
        {"contract": wallet(0xA1), "name": "Mystery Pearl", "symbol": "PEARL", "standard": "ERC-721 (NFT Collection)",
         "block": 2000, "holders": 1, "total_minted": 1551, "max_supply": 50000},
        {"contract": wallet(0xB2), "name": "Tok", "symbol": "TOK", "standard": "BEP-20 Token (18 decimals)",
         "block": 1999, "holders": 1, "total_minted": 1e9},
    ])
    c = TestClient(api.app)
    c.rpc = rpc
    return c


def test_api_counts_on_demand_and_the_feed_serves_the_count(client):
    before = client.get("/api/feed")
    pearl = next(p for p in before.json()["projects"] if p["symbol"] == "PEARL")
    assert "holders_onchain" not in pearl
    r = client.get(f"/api/holders?contract={wallet(0xA1)}").json()
    assert r["status"] == "ok" and r["holders"] == 949
    after = client.get("/api/feed", headers={"If-None-Match": before.headers["etag"]})
    assert after.status_code == 200                                      # the etag moved with the new count
    pearl = next(p for p in after.json()["projects"] if p["symbol"] == "PEARL")
    assert pearl["holders_onchain"] == 949 and pearl["holders"] == 1   # the stored value itself is untouched
    calls = client.rpc.calls
    assert client.get(f"/api/holders?contract={wallet(0xA1)}").json()["holders"] == 949
    assert client.rpc.calls == calls                                     # cached


def test_api_refuses_what_it_cannot_count(client):
    assert client.get(f"/api/holders?contract={wallet(0xB2)}").json()["status"] == "unsupported"
    assert client.rpc.calls == 0
    assert client.get("/api/holders?contract=0x123").status_code == 400
    assert client.get(f"/api/holders?contract={wallet(0xC3)}").status_code == 404


# ------------------------------------------------------------------------------------------------ saved counts
def sqlite_db(tmp_path, monkeypatch):
    for var in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(db_module, "HISTORY_FILE", str(tmp_path / "no_history.json"))
    monkeypatch.setattr(db_module, "FAVORITES_FILE", str(tmp_path / "no_favorites.json"))
    return db_module.DatabaseManager(db_path=str(tmp_path / "t.db"))


def test_sqlite_saves_and_loads_counts(tmp_path, monkeypatch):
    d = sqlite_db(tmp_path, monkeypatch)
    assert d.load_holder_counts() == {}
    assert d.save_holder_count(wallet(0xA1).upper().replace("0X", "0x"), 949, 1000.5)
    d.save_holder_count(wallet(0xA1), 950, 2000.0)                       # replaces
    d.save_holder_count(wallet(0xB2), 12, 1500.0)
    assert d.load_holder_counts() == {wallet(0xA1): {"holders": 950, "counted_at": 2000.0},
                                      wallet(0xB2): {"holders": 12, "counted_at": 1500.0}}


class FakeSupabase:
    def __init__(self, status):
        self.status = status
        self.posts = []

    def post(self, url, json=None, headers=None):
        self.posts.append((url, json))
        return type("R", (), {"status_code": self.status})()

    def get(self, url):
        return type("R", (), {"status_code": self.status, "json": lambda self: []})()


def test_supabase_without_the_table_falls_back_to_sqlite_and_warns_once(tmp_path, monkeypatch, capsys):
    import os
    d = sqlite_db(tmp_path, monkeypatch)
    fake = FakeSupabase(404)
    d.mode, d._client_pid, d._client_instance = "supabase", os.getpid(), fake
    assert d.save_holder_count(wallet(0xA1), 949, 1000.0)
    assert d.save_holder_count(wallet(0xA1), 950, 1001.0)
    assert len(fake.posts) == 1                                          # not retried after the 404
    assert capsys.readouterr().out.count("003_holder_counts.sql") == 1
    assert d.load_holder_counts() == {wallet(0xA1): {"holders": 950, "counted_at": 1001.0}}


def test_supabase_write_goes_to_its_own_table(tmp_path, monkeypatch):
    import os
    d = sqlite_db(tmp_path, monkeypatch)
    fake = FakeSupabase(201)
    d.mode, d._client_pid, d._client_instance = "supabase", os.getpid(), fake
    assert d.save_holder_count(wallet(0xA1), 949, 1000.0)
    assert fake.posts == [("/rest/v1/holder_counts?on_conflict=contract",
                           {"contract": wallet(0xA1), "holders": 949, "counted_at": 1000.0})]


class MemStore:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})

    def save_holder_count(self, c, n, at):
        self.rows[c] = {"holders": n, "counted_at": at}

    def load_holder_counts(self):
        return dict(self.rows)


def test_counts_are_saved_and_come_back_after_a_restart():
    now = [5000.0]
    store = MemStore()
    rpc = FakeRpc({i: wallet(i % 7 + 1) for i in range(1, 30)})
    hc = holders.HolderCounts(lambda: rpc, now=lambda: now[0], store=store)
    hc.get(wallet(1), 29)
    assert store.rows == {wallet(1): {"holders": 7, "counted_at": 5000.0}}
    # a new process: loads the saved count, serves it at once, no RPC
    rpc2 = FakeRpc({})
    restarted = holders.HolderCounts(lambda: rpc2, now=lambda: now[0] + 60, store=store)
    assert restarted.load_saved() == 1 and restarted.version == 1
    assert restarted.overlay(wallet(1)) == {"holders": 7, "at": 5000.0}
    assert restarted.get(wallet(1), 29)["holders"] == 7 and rpc2.calls == 0
    # a count made before the load finished is not replaced by an older saved one
    late = holders.HolderCounts(lambda: rpc, now=lambda: 9000.0, store=MemStore({wallet(1): {"holders": 3, "counted_at": 1.0}}))
    late.get(wallet(1), 29)
    late.load_saved()
    assert late.overlay(wallet(1))["holders"] == 7


def test_a_failing_store_does_not_break_counting():
    class Broken:
        def save_holder_count(self, *a):
            raise RuntimeError("db down")

        def load_holder_counts(self):
            raise RuntimeError("db down")

    hc = holders.HolderCounts(lambda: FakeRpc({1: wallet(5)}), store=Broken())
    assert hc.load_saved() == 0
    assert hc.get(wallet(1), 1)["holders"] == 1
