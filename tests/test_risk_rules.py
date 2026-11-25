"""Serial Dev and Fund Drain flagged 22% of all records RUG. Checked on chain (2026-11-24), they were wrong in specific ways:

* a proxy clone's stored `deployer` was the first CALLER the worker saw (a bot with 14,642 transactions "deployed" 294
  tokens); the real creator is the Four.meme factory, which is also the token's owner();
* owner() is often a contract, not the person who deployed it;
* "no LP and an empty contract" is true of every NFT collection whose owner withdrew the revenue, and of every
  1,000,000,000-supply launch token whose "mint price" is just the creation fee.
"""
from unittest.mock import MagicMock

import pytest

import monitor

FOUR_MEME = monitor.FOUR_MEME_FACTORY.lower()
BOT = "0xd21264527854ee9d59600b6b0606f544315b5a01"          # the "deployer" of 294 tokens in 24h; nonce 14,642
WALLET = "0x69a130b5c5433e55091433bf2e622fede819a1ad"       # a real deployer of 100+ collections
CONTRACT = "0x" + "1" * 40


def padded(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network in tests")
    monkeypatch.setattr(monitor.urllib.request, "urlopen", boom)


def rpc_with_owner(owner=None, owner_code="0x"):
    rpc = MagicMock()
    rpc.get_balance.return_value = 0
    rpc.get_tx_count.return_value = 500
    rpc.get_code.return_value = owner_code
    owner_sel = monitor.keccak_selector("owner()")
    rpc.call_contract.side_effect = lambda to, data: padded(owner) if (data == owner_sel and owner) else None
    return rpc


# ------------------------------------------------------------------------------------------------ deployer resolution

def test_owner_is_a_deployer_only_when_it_is_a_plain_wallet():
    assert monitor.resolve_deployer_from_owner(rpc_with_owner(WALLET), CONTRACT) == WALLET
    assert monitor.resolve_deployer_from_owner(rpc_with_owner(WALLET, owner_code="0x6080604052"), CONTRACT) is None   # a contract
    assert monitor.resolve_deployer_from_owner(rpc_with_owner(FOUR_MEME), CONTRACT) is None                          # a launchpad
    assert monitor.resolve_deployer_from_owner(rpc_with_owner("0x" + "0" * 40), CONTRACT) is None                    # renounced
    assert monitor.resolve_deployer_from_owner(rpc_with_owner(None), CONTRACT) is None                               # no owner()


def test_launchpad_addresses_include_the_factories_the_worker_knows():
    assert FOUR_MEME in monitor.launchpad_addresses()
    assert monitor.VAULT_FACTORIES[0].lower() in monitor.launchpad_addresses()


# ------------------------------------------------------------------------------------------------- serial deployments

def test_proxy_clones_and_factory_records_do_not_count_as_a_deployers_history():
    history = [
        {"contract": f"0x{i:040x}", "deployer": BOT, "is_proxy_clone": True} for i in range(1, 30)
    ] + [
        {"contract": "0x" + "f" * 40, "deployer": BOT, "is_factory": True},
        {"contract": "0x" + "a" * 40, "deployer": BOT},                       # one record that is a real deployment
        {"contract": CONTRACT, "deployer": BOT},                              # the contract itself is never counted
        {"contract": "0x" + "b" * 40, "deployer": WALLET},
    ]
    assert [h["contract"] for h in monitor.count_prior_deployments(history, BOT.upper().replace("0X", "0x"), CONTRACT)] == ["0x" + "a" * 40]
    assert monitor.count_prior_deployments([], BOT, CONTRACT) == []


def test_a_bot_that_only_called_proxy_clones_is_not_a_serial_dev():
    history = [{"contract": f"0x{i:040x}", "deployer": BOT, "is_proxy_clone": True} for i in range(1, 300)]
    details = {"name": "Meme", "symbol": "MEME", "classification": {"category": "TOKEN", "standard": "BEP-20"}}
    stats = monitor.analyze_market_interest(rpc_with_owner(FOUR_MEME), CONTRACT, BOT, details, history=history)
    assert not (stats.get("security") or {}).get("is_serial_dev")
    assert stats["risk_level"] != "RUG" and "Serial" not in str(stats["risk_badge"])


def test_a_clone_owned_by_the_launchpad_gets_no_deployer_at_all():
    details = {"name": "Meme", "symbol": "MEME", "classification": {"category": "TOKEN", "standard": "BEP-20"}}
    history = [{"contract": f"0x{i:040x}", "deployer": FOUR_MEME} for i in range(1, 50)]       # whatever was stored before
    stats = monitor.analyze_market_interest(rpc_with_owner(FOUR_MEME), CONTRACT, None, details, history=history)
    assert not (stats.get("security") or {}).get("is_serial_dev")
    assert not stats.get("deployer_balance_bnb")                 # no wallet was looked up: there is none to look up


def test_a_launchpad_passed_in_as_the_deployer_is_ignored_too():
    details = {"name": "Meme", "symbol": "MEME", "classification": {"category": "TOKEN", "standard": "BEP-20"}}
    history = [{"contract": f"0x{i:040x}", "deployer": FOUR_MEME} for i in range(1, 50)]
    stats = monitor.analyze_market_interest(rpc_with_owner(None), CONTRACT, FOUR_MEME, details, history=history)
    assert not (stats.get("security") or {}).get("is_serial_dev")


def test_a_real_serial_deployer_is_still_flagged():
    """The signal stays where it is evidence: 100+ collections deployed by one wallet."""
    history = [{"contract": f"0x{i:040x}", "deployer": WALLET} for i in range(1, 100)]
    details = {"name": "AGENT", "symbol": "AG", "classification": {"category": "NFT", "standard": "ERC-721 (NFT Collection)", "is_nft": True}}
    stats = monitor.analyze_market_interest(rpc_with_owner(WALLET), CONTRACT, WALLET, details, history=history)
    assert stats["security"]["is_serial_dev"] is True and stats["security"]["serial_count"] == 100
    assert stats["risk_level"] == "RUG" and "Serial" in stats["risk_badge"]


def test_owner_wallet_fallback_still_finds_a_serial_deployer():
    history = [{"contract": f"0x{i:040x}", "deployer": WALLET} for i in range(1, 6)]
    details = {"name": "AGENT", "symbol": "AG", "classification": {"category": "NFT", "standard": "ERC-721 (NFT Collection)", "is_nft": True}}
    stats = monitor.analyze_market_interest(rpc_with_owner(WALLET), CONTRACT, None, details, history=history)
    assert stats["security"]["is_serial_dev"] is True


# ----------------------------------------------------------------------------------------------------- fund drain

def paid(**kw):
    d = {"mint_price_bnb": 0.01, "mint_value_wei": "10000000000000000", "total_minted": 500.0}
    d.update(kw)
    return d


NFT = {"category": "NFT", "standard": "ERC-721 (NFT Collection)", "is_nft": True}
FAIR = {"category": "TOKEN_FAIR_MINT", "standard": "Fair-Mint Contract (Dispenses $X Token)"}
TOKEN = {"category": "TOKEN", "standard": "BEP-20 Token (18 decimals)"}


@pytest.mark.parametrize("details, applies", [
    (paid(classification=NFT), False),                                                    # revenue withdrawn: normal, and no pool expected
    (paid(classification=NFT, max_supply=5000.0, total_minted=4900.0), False),            # the real "...AGENT" farms: capped, 98% minted
    (paid(classification=NFT, max_supply=10000.0, total_minted=9900.0), False),
    (paid(classification=TOKEN, max_supply=None, total_minted=1e9), False),               # a launch token: the "price" is the creation fee
    (paid(classification=TOKEN, max_supply=1e9, total_minted=1e9), False),                # pre-minted supply
    (paid(classification=FAIR), True),                                                    # a paid fair mint with nothing held and no pool
    (paid(classification=TOKEN, max_supply=1000.0, total_minted=500.0), True),            # a capped paid mint, half filled
    (paid(classification=FAIR, total_minted=10.0), False),                                # too few mints to say
])
def test_fund_drain_needs_a_real_paid_mint(details, applies):
    assert monitor.fund_drain_applies(details, {"lp_usd": 0, "pair_url": ""}, True, 0.0) is applies


def test_fund_drain_is_off_when_any_money_or_pool_is_there():
    d = paid(classification=FAIR)
    assert monitor.fund_drain_applies(d, {"lp_usd": 0}, True, 0.0)
    assert not monitor.fund_drain_applies(d, {"lp_usd": 500}, True, 0.0)                  # a funded pool
    assert not monitor.fund_drain_applies(d, {"lp_usd": 0, "pair_url": "https://pancakeswap.finance/info/v2/pairs/0x1"}, True, 0.0)
    assert not monitor.fund_drain_applies(d, {"lp_usd": 0}, True, 1.5)                    # the contract still holds the money
    assert not monitor.fund_drain_applies(d, {"lp_usd": 0}, False, 0.0)                   # a free mint has no money to drain


def test_an_nft_collection_with_withdrawn_revenue_is_not_a_drain():
    details = paid(classification=NFT, total_minted=4900.0, name="SPARAGENT", symbol="SPAR")
    stats = monitor.analyze_market_interest(rpc_with_owner(WALLET), CONTRACT, WALLET, details, history=[])
    assert not (stats.get("security") or {}).get("is_fund_drain")
    assert stats["risk_level"] != "RUG"


def test_a_launch_token_with_a_creation_fee_is_not_a_drain():
    details = paid(classification=TOKEN, max_supply=None, total_minted=1e9, name="Meme", symbol="MEME")
    stats = monitor.analyze_market_interest(rpc_with_owner(FOUR_MEME), CONTRACT, None, details, history=[])
    assert not (stats.get("security") or {}).get("is_fund_drain") and stats["risk_level"] != "RUG"


def test_a_real_drain_is_high_not_rug_and_never_hides_a_proven_rug():
    details = paid(classification=FAIR, name="Fair", symbol="FAIR")
    stats = monitor.analyze_market_interest(rpc_with_owner(WALLET), CONTRACT, WALLET, details, history=[])
    assert stats["security"]["is_fund_drain"] is True
    assert stats["risk_level"] == "HIGH" and stats["risk_badge"] == "🚨 Fund Drain"
    # a serial deployer is a proven RUG: the drain check must neither downgrade it nor replace its badge
    history = [{"contract": f"0x{i:040x}", "deployer": WALLET} for i in range(1, 10)]
    stats = monitor.analyze_market_interest(rpc_with_owner(WALLET), CONTRACT, WALLET, details, history=history)
    assert stats["risk_level"] == "RUG" and "Serial" in stats["risk_badge"]
