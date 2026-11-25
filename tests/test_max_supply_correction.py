"""Fair-mint dispensers answer maxSupply() with a COUNT of mint slots; the worker scaled it by 10**18 like a token amount, so
max_supply read 2.2e-16 next to a total_minted of 1,040,000 and the drop showed "SOLD 100% 1.0M/0". Measured 2026-11-24:
2,914 of 13,138 records (22%) had such a cap, and only 3 of them are really full.

On chain (BSC, read-only):  ANTCO maxSupply()=220 totalMinted()=13 totalSupply()=1.04e24 (13 x 80,000 tokens per mint)
                            ASTUR 240 / 150 / 1.05e26 (150 x 700,000)      AYHA 200 / 142 / 1.42e24 (142 x 10,000)
"""
import pytest
from fastapi.testclient import TestClient

import api
import filters

STD = "BEP-20 Token (18 decimals)"


def dispenser(slots, minted_tokens, per_mint, **kw):
    d = {"contract": "0x" + "d" * 40, "name": "Disp", "symbol": "DSP", "standard": STD,
         "max_supply": slots / 1e18,                 # what the worker stored: the slot count scaled as a token amount
         "total_minted": float(minted_tokens), "tokens_per_mint": float(per_mint), "mint_price_str": "⭐ FREE",
         "detected_at": "2026-11-24 10:00:00", "block": 100}
    d.update(kw)
    return d


ANTCO = dispenser(220, 1_040_000, 80_000)             # 13 of 220 slots
ASTUR = dispenser(240, 105_000_000, 700_000)          # 150 of 240
AYHA = dispenser(200, 1_420_000, 10_000)              # 142 of 200


@pytest.mark.parametrize("d, cap", [(ANTCO, 17_600_000.0), (ASTUR, 168_000_000.0), (AYHA, 2_000_000.0)])
def test_the_cap_is_rescaled_into_the_units_total_minted_is_in(d, cap):
    assert filters.is_mint_count_cap(d)
    assert filters.corrected_max_supply(d) == pytest.approx(cap)
    assert d["total_minted"] <= cap


def test_real_progress_of_the_three_checked_tokens():
    pct = lambda d: d["total_minted"] / filters.corrected_max_supply(d) * 100
    assert pct(ANTCO) == pytest.approx(13 / 220 * 100)
    assert pct(ASTUR) == pytest.approx(150 / 240 * 100)
    assert pct(AYHA) == pytest.approx(142 / 200 * 100)
    assert [filters.mint_progress_bucket(d) for d in (ANTCO, ASTUR, AYHA)] == ["open", "momentum", "momentum"]      # 6%, 62%, 71%
    assert not any(filters.is_sold_out(d) for d in (ANTCO, ASTUR, AYHA))


def test_without_tokens_per_mint_the_cap_is_unknown_not_full():
    d = dispenser(220, 1_040_000, 80_000)
    d.pop("tokens_per_mint")
    assert filters.corrected_max_supply(d) == 0.0
    assert filters.mint_progress_bucket(d) is None and not filters.is_sold_out(d)


def test_a_dispenser_that_really_is_full_is_sold_out_not_pre_minted():
    full = dispenser(100, 8_000_000, 80_000)               # 100 of 100 slots
    assert filters.is_sold_out(full) and filters.mint_progress_bucket(full) == "sold_out"
    assert not filters.is_pre_minted_token(full)


def test_real_caps_are_left_alone():
    nft = {"standard": "ERC-721 (NFT Collection)", "max_supply": 3000.0, "total_minted": 508.0}
    token = {"standard": STD, "max_supply": 1_000_000_000.0, "total_minted": 1_000_000_000.0}
    uncapped = {"standard": STD, "max_supply": 1e30, "total_minted": 5.0}
    assert [filters.corrected_max_supply(d) for d in (nft, token, uncapped)] == [3000.0, 1_000_000_000.0, 0.0]
    assert not any(filters.is_mint_count_cap(d) for d in (nft, token, uncapped))
    assert filters.is_pre_minted_token(token) and filters.mint_progress_bucket(token) is None     # unchanged rule
    assert filters.corrected_max_supply({}) == 0.0 and filters.corrected_max_supply(None) == 0.0


def test_decimals_come_from_the_standard():
    d = dispenser(50, 5_000, 100, standard="BEP-20 Token (8 decimals)")
    d["max_supply"] = 50 / 1e8
    assert filters.corrected_max_supply(d) == pytest.approx(5_000.0)


def test_served_copy_carries_the_corrected_cap_and_the_stored_record_is_untouched():
    stored = dict(ANTCO)
    served = api.with_server_metrics(stored)
    assert served["max_supply"] == pytest.approx(17_600_000.0) and served["max_supply_corrected"] is True
    assert stored["max_supply"] == ANTCO["max_supply"] and "max_supply_corrected" not in stored
    unknown = dict(ANTCO)
    unknown.pop("tokens_per_mint")
    served = api.with_server_metrics(unknown)
    assert served["max_supply"] is None and served["max_supply_corrected"] is True
    fine = {"standard": STD, "max_supply": 1000.0, "total_minted": 10.0, "contract": "0x" + "1" * 40}
    assert api.with_server_metrics(fine)["max_supply"] == 1000.0 and "max_supply_corrected" not in api.with_server_metrics(fine)


def test_stats_no_longer_count_open_dispensers_as_sold_out():
    records = [dict(ANTCO, contract="0x" + "1" * 40), dict(ASTUR, contract="0x" + "2" * 40), dict(AYHA, contract="0x" + "3" * 40),
               dispenser(100, 8_000_000, 80_000, contract="0x" + "4" * 40),                    # really full
               {k: v for k, v in dispenser(220, 1, 1, contract="0x" + "5" * 40).items() if k != "tokens_per_mint"}]   # unknown
    mp = api.compute_mint_progress_stats(records)
    assert mp["capped"] == 4 and mp["sold_out"] == 1 and mp["fill_pct"] == pytest.approx(25.0)
    assert mp["momentum"] == 2 and mp["near_cap"] == 0         # ASTUR 62% and AYHA 71%


def test_projects_endpoint_reports_the_real_progress(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_MAX", 10 ** 9)
    rec = dict(ANTCO, contract="0x" + "a" * 40, name="Gannetcore")
    monkeypatch.setattr(api.db, "get_all_projects", lambda *a, **k: [dict(rec)])
    body = TestClient(api.app).get("/api/projects?search=gannetcore").json()
    p = body["projects"][0]
    assert p["progress"] == pytest.approx(13 / 220 * 100, rel=1e-6)
    assert p["status"] == "MINTING" and p["max_supply"] == pytest.approx(17_600_000.0)


def test_sniper_readiness_uses_the_real_progress():
    """The worker judged every slot-count token sold out (progress = 1.04e6 / 2.2e-16)."""
    import monitor
    verdict = lambda d: monitor.evaluate_sniper_ready(dict(d, lp_usd=0.0, risk_level="SAFE"))["sniper_status"]
    assert verdict(ANTCO) == "TRACKING"                              # 6%: nothing to snipe yet
    assert verdict(ASTUR) == "SNIPER_READY"                          # 62.5%: past the 60% rule
    assert verdict(dispenser(100, 8_000_000, 80_000)) == "SOLD_OUT"  # really full
    unknown = dict(ASTUR)
    unknown.pop("tokens_per_mint")
    assert verdict(unknown) == "TRACKING"                            # cap unknown: not "sold out", not ready either

