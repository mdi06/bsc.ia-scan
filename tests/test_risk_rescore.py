"""The risk re-score loop: it re-checks the records the old Serial Dev / Fund Drain rules flagged, through the worker's own copy
of the list. Default is a dry run; apply touches risk fields and sniper flags only; a failed GoPlus lookup never lowers a risk."""
import pytest

import monitor
import worker

C = "0x" + "1" * 40
BOT = "0xd21264527854ee9d59600b6b0606f544315b5a01"
NOW = 1_800_000_000.0


def flagged(i=1, badge="🚨 Serial Dev", **kw):
    d = {"contract": f"0x{i:040x}", "name": f"T{i}", "block": 1000 + i, "risk_level": "RUG", "risk_badge": badge,
         "deployer": BOT, "is_proxy_clone": True, "standard": "BEP-20 Token (18 decimals)", "lp_usd": 1234.0,
         "token_price_usd": 0.5, "volume_24h": 777.0, "market_cap": 9999.0, "security": {"is_serial_dev": True}}
    d.update(kw)
    return d


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    worker._risk_rescore_tried.clear()
    worker.RISK_RESCORE_STATS.clear()
    yield
    worker._risk_rescore_tried.clear()
    worker.RISK_RESCORE_STATS.clear()


def stub_analysis(monkeypatch, mkt, seen=None):
    monkeypatch.setattr(monitor, "analyze_contract", lambda rpc, c, w: {"classification": {}})

    def fake(rpc, c, deployer, det, history=None):
        if seen is not None:
            seen.append({"deployer": deployer, "history_len": len(history or [])})
        return dict(mkt)
    monkeypatch.setattr(monitor, "analyze_market_interest", fake)


# --------------------------------------------------------------------------------------------------------- candidates

def test_candidates_are_the_records_the_old_rules_flagged_newest_first():
    history = [flagged(1), flagged(2, badge="🚨 Fund Drain"), flagged(3, risk_badge="🟢 Clean", security={}),     # not flagged
               flagged(4, security={"is_fund_drain": True}, risk_badge="🚨 Honeypot"),                           # flagged via security
               flagged(5, risk_rules_v=worker.RISK_RULES_VERSION),                                                # already re-checked
               flagged(6, risk_badge="🚨 Honeypot", security={"is_honeypot": True})]                              # a different rule
    got = worker._risk_rescore_candidates(history, NOW, batch=10)
    assert [x["contract"] for x in got] == [f"0x{i:040x}" for i in (4, 2, 1)]


def test_candidates_respect_the_batch_size_and_the_retry_wait():
    history = [flagged(i) for i in range(1, 30)]
    assert len(worker._risk_rescore_candidates(history, NOW, batch=8)) == 8
    worker._risk_rescore_tried[history[-1]["contract"].lower()] = NOW - 60            # tried a minute ago
    assert history[-1] not in worker._risk_rescore_candidates(history, NOW, batch=50)
    assert history[-1] in worker._risk_rescore_candidates(history, NOW + worker.RISK_RESCORE_RETRY_SEC + 1, batch=50)


# ------------------------------------------------------------------------------------------------------- rescore_record

def test_dry_run_reports_the_change_and_writes_nothing(monkeypatch):
    stub_analysis(monkeypatch, {"risk_level": "SAFE", "risk_badge": "🟢 Clean", "risk_flags": []})
    item = flagged()
    before = dict(item)
    assert worker.rescore_record(None, item, [], apply=False) == "RUG->SAFE (🟢 Clean)"
    assert item == before


def test_apply_writes_risk_fields_only_and_stamps_the_version(monkeypatch):
    stub_analysis(monkeypatch, {"risk_level": "SAFE", "risk_badge": "🟢 Clean", "risk_flags": ["Open Source"],
                                "security": {"audited": True, "is_honeypot": False}, "lp_usd": 0.0, "token_price_usd": 0.0,
                                "volume_24h": 0.0, "market_cap": 0.0})
    item = flagged()
    assert worker.rescore_record(None, item, [], apply=True) == "RUG->SAFE (🟢 Clean)"
    assert (item["risk_level"], item["risk_badge"], item["risk_flags"]) == ("SAFE", "🟢 Clean", ["Open Source"])
    assert item["risk_rules_v"] == worker.RISK_RULES_VERSION
    assert item["deployer"] == "", "a proxy's stored deployer was its first caller"
    # market data is not the re-score's business: the fresh analysis said 0, the record keeps what it had
    assert (item["lp_usd"], item["token_price_usd"], item["volume_24h"], item["market_cap"]) == (1234.0, 0.5, 777.0, 9999.0)


def test_sniper_flags_follow_the_new_verdict(monkeypatch):
    stub_analysis(monkeypatch, {"risk_level": "SAFE", "risk_badge": "🟢 Clean", "risk_flags": []})
    item = flagged(sniper_ready=False, sniper_status="RUG_RISK", standard="ERC-721 (NFT Collection)", is_proxy_clone=False,
                   max_supply=1000.0, total_minted=700.0, lp_usd=0.0, mint_price_str="⭐ FREE")
    worker.rescore_record(None, item, [], apply=True)
    assert item["sniper_status"] != "RUG_RISK"
    assert item["sniper_ready"] is True and item["sniper_status"] == "SNIPER_READY"      # 70% filled, no longer a rug


def test_a_proxy_is_scored_without_its_stored_caller_but_a_real_deployer_is_kept(monkeypatch):
    seen = []
    stub_analysis(monkeypatch, {"risk_level": "SAFE", "risk_badge": "🟢 Clean"}, seen)
    worker.rescore_record(None, flagged(is_proxy_clone=True), [], apply=False)
    worker.rescore_record(None, flagged(is_proxy_clone=False, deployer="0x" + "7" * 40), [], apply=False)
    worker.rescore_record(None, flagged(is_proxy_clone=False, is_factory=True), [{}] * 5, apply=False)
    assert [s["deployer"] for s in seen] == [None, "0x" + "7" * 40, None]
    assert seen[2]["history_len"] == 5


def test_a_failed_goplus_lookup_never_lowers_a_risk(monkeypatch):
    """The old record had a GoPlus audit (it may hold a real honeypot warning); the new lookup got no answer."""
    stub_analysis(monkeypatch, {"risk_level": "SAFE", "risk_badge": "🟢 Clean", "security": {}})
    item = flagged(security={"audited": True, "is_honeypot": True, "is_serial_dev": True})
    before = dict(item)
    assert worker.rescore_record(None, item, [], apply=True) == "skipped: GoPlus unavailable"
    assert item == before


def test_a_record_that_never_had_an_audit_may_change(monkeypatch):
    stub_analysis(monkeypatch, {"risk_level": "HIGH", "risk_badge": "🚨 Fund Drain", "security": {}})
    assert worker.rescore_record(None, flagged(security={"is_serial_dev": True}), [], apply=False) == "RUG->HIGH (🚨 Fund Drain)"


def test_a_badge_only_change_says_so(monkeypatch):
    """A real serial deployer: still RUG, but the reason is now Serial Dev, not Fund Drain."""
    stub_analysis(monkeypatch, {"risk_level": "RUG", "risk_badge": "🚨 Serial Dev"})
    verdict = worker.rescore_record(None, flagged(badge="🚨 Fund Drain"), [], apply=False)
    assert verdict == "RUG stays, badge 🚨 Fund Drain -> 🚨 Serial Dev"


def test_unchanged_is_reported_as_unchanged(monkeypatch):
    stub_analysis(monkeypatch, {"risk_level": "RUG", "risk_badge": "🚨 Serial Dev"})
    assert worker.rescore_record(None, flagged(), [], apply=False) == "unchanged"


# ------------------------------------------------------------------------------------------------------------ the loop

def test_the_loop_only_runs_in_dry_run_or_apply_mode():
    assert worker.RISK_RESCORE_MODE == "dry-run", "the default writes nothing"


def test_start_worker_registers_the_loop_only_when_it_will_run(monkeypatch, capsys):
    started = []

    class FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            self.name = name
            started.append(name)

        def start(self):
            pass

        def is_alive(self):
            raise KeyboardInterrupt            # leave the supervisor loop at once

    monkeypatch.setattr(worker.threading, "Thread", FakeThread)
    for mode, expected in (("off", False), ("dry-run", True), ("apply", True)):
        started.clear()
        monkeypatch.setattr(worker, "RISK_RESCORE_MODE", mode)
        worker.start_worker()
        assert ("RiskRescore" in started) is expected, mode
    assert "TokensPerMint" in started and "MemoryGuard" in started
