"""A fair-mint dispenser's cap getter answers with a COUNT of mint slots. read_supply scaled it as a token amount (2.2e-16),
every such token then looked sold out, and the worker's sweeper stopped tracking it: of 14 checked on chain, 5 had stored
totals behind the chain (AYHA showed 250,000 for 1,500,000, PRBFR 21,001 for 333,001 = 60%, the sniper-ready line).
Now the slot count is kept as `mint_slots`, the cap in tokens is slots x tokens_per_mint, and the sweeper repairs old records.
"""
import pytest

import api
import filters
import monitor
import worker

C = "0x" + "1" * 40
STD = "BEP-20 Token (18 decimals)"


class FakeRpc:
    def __init__(self, **getters):
        self.replies = {monitor.keccak_selector(sig)[:10]: "0x" + hex(v)[2:].zfill(64) for sig, v in getters.items()}
        self.calls = []

    def call_contract(self, to, data):
        self.calls.append(data[:10])
        return self.replies.get(data[:10])


def dispenser_rpc(slots=220, minted=13, per_mint=80_000):
    return FakeRpc(**{"maxSupply()": slots, "totalMinted()": minted, "totalSupply()": minted * per_mint * 10 ** 18})


def stored(slots=220, minted_tokens=1_040_000.0, tpm=80_000.0, **kw):
    d = {"contract": C, "standard": STD, "max_supply": slots / 1e18, "total_minted": minted_tokens, "tokens_per_mint": tpm,
         "mint_price_str": "⭐ FREE"}
    d.update(kw)
    return d


# ------------------------------------------------------------------------------------------------------ read_supply

def test_read_supply_keeps_a_slot_count_apart_from_the_token_supply():
    meta = {}
    cap, minted = monitor.read_supply(dispenser_rpc(), [C], 18, meta=meta)
    assert cap is None and minted == pytest.approx(1_040_000.0)       # not 2.2e-16
    assert meta["cap_slots"] == 220 and meta["cap_status"] == "capped"


def test_read_supply_gives_the_cap_in_tokens_when_tokens_per_mint_is_known():
    meta = {}
    cap, minted = monitor.read_supply(dispenser_rpc(), [C], 18, meta=meta, tokens_per_mint=80_000.0)
    assert cap == pytest.approx(17_600_000.0) and minted == pytest.approx(1_040_000.0)
    assert meta["cap_slots"] == 220


def test_a_real_token_cap_and_counts_without_decimals_are_untouched():
    meta = {}
    cap, minted = monitor.read_supply(FakeRpc(**{"cap()": 1000 * 10 ** 18, "totalSupply()": 250 * 10 ** 18}), [C], 18, meta=meta)
    assert (cap, minted) == (1000.0, 250.0) and "cap_slots" not in meta
    # an NFT / fair-mint reads raw counts on both sides: nothing to convert
    meta = {}
    assert monitor.read_supply(FakeRpc(**{"maxSupply()": 220, "totalMinted()": 13}), [C], None, meta=meta) == (220, 13)
    assert "cap_slots" not in meta


def test_the_cap_probe_stops_after_the_first_answer_on_a_slot_count():
    rpc = dispenser_rpc()
    monitor.read_supply(rpc, [C, "0x" + "2" * 40], 18)
    cap_sels = [monitor.keccak_selector(s)[:10] for s in monitor.MAX_SUPPLY_GETTERS]
    assert sum(1 for sel in rpc.calls if sel in cap_sels) <= len(cap_sels), "no second round of cap probes on the companion"


# --------------------------------------------------------------------------------------------------- stored records

def test_repair_turns_a_stored_slot_cap_into_the_cap_in_tokens():
    item = stored()
    assert worker.repair_mint_count_cap(item) is True
    assert item["mint_slots"] == 220 and item["max_supply"] == pytest.approx(17_600_000.0)
    assert filters.mint_progress_bucket(item) == "open" and not filters.is_sold_out(item)
    assert worker.repair_mint_count_cap(item) is False, "idempotent"


def test_repair_keeps_the_slots_and_drops_the_lie_while_tokens_per_mint_is_unknown():
    item = stored()
    item.pop("tokens_per_mint")
    assert worker.repair_mint_count_cap(item) is True
    assert item["mint_slots"] == 220 and item["max_supply"] is None
    assert worker._slots_pending(item) and worker._cap_of(item) is None
    item["tokens_per_mint"] = 80_000.0                      # the tokens-per-mint loop learns it later
    assert worker.repair_mint_count_cap(item) is True
    assert item["max_supply"] == pytest.approx(17_600_000.0)


def test_repair_leaves_every_other_record_alone():
    for item in ({"contract": C, "standard": "ERC-721 (NFT Collection)", "max_supply": 3000.0, "total_minted": 5},
                 {"contract": C, "standard": STD, "max_supply": 1e9, "total_minted": 1e9},
                 {"contract": C, "standard": STD, "max_supply": None, "total_minted": 1e9}):
        before = dict(item)
        assert worker.repair_mint_count_cap(item) is False and item == before


def test_new_records_carry_mint_slots_only_when_there_are_some():
    assert worker._mint_slots_field({"mint_slots": 220}) == {"mint_slots": 220}
    assert worker._mint_slots_field({}) == {} and worker._mint_slots_field({"mint_slots": None}) == {}


# ------------------------------------------------------------------------------------------------------- the sweeper

def test_the_sweeper_reads_the_corrected_cap_and_flags_the_old_record_for_repair(monkeypatch):
    monkeypatch.setattr(worker, "_core_addr_cache", {C: None})
    item = stored(minted_tokens=250_000.0)                  # stale: the chain has 142 mints
    rpc = FakeRpc(**{"totalSupply()": 1_420_000 * 10 ** 18})
    c, minted, supply, holders, changed = worker.fast_sweep_contract(rpc, item)
    assert c == C and minted == pytest.approx(1_420_000.0)
    assert supply == pytest.approx(17_600_000.0) and changed is True


def test_the_sweeper_flags_a_repair_even_when_nothing_was_minted_since(monkeypatch):
    monkeypatch.setattr(worker, "_core_addr_cache", {C: None})
    item = stored()
    *_, changed = worker.fast_sweep_contract(FakeRpc(**{"totalSupply()": 1_040_000 * 10 ** 18}), item)
    assert changed is True                                  # the stored cap is a slot count: rewrite it
    worker.repair_mint_count_cap(item)
    *_, changed_again = worker.fast_sweep_contract(FakeRpc(**{"totalSupply()": 1_040_000 * 10 ** 18}), item)
    assert changed_again is False


def test_a_token_with_slots_but_no_cap_in_tokens_is_still_swept(monkeypatch):
    monkeypatch.setattr(worker, "_core_addr_cache", {C: None})
    item = stored()
    item.pop("tokens_per_mint")
    worker.repair_mint_count_cap(item)                      # slots kept, cap None
    c, minted, supply, holders, changed = worker.fast_sweep_contract(FakeRpc(**{"totalSupply()": 2_000_000 * 10 ** 18}), item)
    assert minted == pytest.approx(2_000_000.0) and supply is None and changed is True
    # before: no cap meant "not swept at all"
    plain = {"contract": C, "standard": STD, "max_supply": None, "total_minted": 5.0}
    assert worker.fast_sweep_contract(FakeRpc(**{"totalSupply()": 9 * 10 ** 18}), plain)[1] is None


def test_the_batched_sweep_path_gives_the_same_answer(monkeypatch):
    monkeypatch.setattr(worker, "_core_addr_cache", {C: None})
    item = stored()
    monkeypatch.setattr(monitor, "read_minted_batch", lambda rpc, jobs: [1_040_000.0 for _ in jobs])
    results = worker.fast_sweep_batch(object(), [item])
    assert results == [(C, 1_040_000.0, pytest.approx(17_600_000.0), None, True)]


# --------------------------------------------------------------------------------------------------------------- API

def test_the_api_serves_a_cap_for_a_record_in_the_new_shape():
    item = stored(max_supply=None, mint_slots=220)
    served = api.with_server_metrics(item)
    assert served["max_supply"] == pytest.approx(17_600_000.0) and served["max_supply_corrected"] is True
    assert item["max_supply"] is None, "the stored record is not touched"
    waiting = stored(max_supply=None, mint_slots=220)
    waiting.pop("tokens_per_mint")
    assert api.with_server_metrics(waiting)["max_supply"] is None
