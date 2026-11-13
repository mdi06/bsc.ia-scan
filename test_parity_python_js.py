"""Python / JavaScript parity (metrics audit, Prompt 4: H4, H6-H9, M5, M10 and the exclusion-list drift).

The same records go through the Python implementation (filters.py, monitor.py) and the JavaScript one (app.js,
run with node); the outputs must be identical. Real records named in the audit are used as-is (trimmed to the
fields the rules read), plus hand-made edge cases. Cases with `then` apply the updates one after another, so the
ATH high-water mark is exercised across calls."""
import copy
import json
import math
import os
import subprocess

import pytest

import filters
import monitor

REPO = os.path.dirname(os.path.abspath(__file__))
BNB = 705.0


def rec(**kw):
    base = {"contract": "0x" + "9" * 40, "name": "Test Token", "symbol": "TST",
            "standard": "BEP-20 Token (18 decimals)"}
    base.update(kw)
    return base


# --------------------------------------------------------------------------------------------- the cases
CASES = {}


def case(name, item, then=()):
    CASES[name] = {"item": item, "then": list(then)}


# --- real records from the audit
case("dagou_dex_traded_68usd", rec(
    contract="0x39b58c3930d4352b2e4d8697ea6188c864455555", name="大狗币", symbol="DAGOU", mint_price_str="DEX Traded",
    mint_value_wei="0", mint_calldata="0x1249c58b", token_price_usd=69.65, lp_usd=13620.69, market_cap=41194.0,
    fdv=61472.0, total_minted=888.0, pair_address="0x32df7ebbff443425555cb0e22d673e7e34e07a41",
    pair_url="https://dexscreener.com/bsc/0x32df7ebbff443425555cb0e22d673e7e34e07a41", volume_24h=644.93))
case("pheromone_tbd_free_headline", rec(
    contract="0x25d82f57332779cd3c5f70ed93e1845a081f07b0", name="Pheromone", symbol="PHERO", mint_price_str="TBD",
    mint_value_wei="0", token_price_usd=0.001671, lp_usd=2100.0, market_cap=46.0, total_minted=27000.0,
    ath_price_usd=0.001671))
case("newwolf_tbd_with_calldata", rec(
    contract="0xc0c7a80f33f1e3658589dfad1092005e33ef4eb2", name="NewWolf", symbol="WOLF",
    standard="Fair-Mint Contract (Dispenses $WOLF Token)", mint_price_str="TBD", mint_value_wei="0",
    mint_calldata="0x1249c58b", token_price_usd=0.0004561, lp_usd=5917.53, market_cap=3127.0, fdv=3127.0,
    total_minted=6713, pair_url="https://dexscreener.com/bsc/0x178599932b5272c02aa906be900d4ed28a9e1911"))
case("bscjs_paid_1bnb_under_water", rec(
    contract="0xbe9be985cd95219091ef2aec431101ececda4265", name="BSCJS", symbol="BSCJS",
    standard="Fair-Mint Contract (Dispenses $BSCJS Token)", mint_price_str="1.0000 BNB",
    mint_value_wei="1000000000000000000", mint_price_bnb=1.0, mint_calldata="0x1249c58b", token_price_usd=2.845e-08,
    lp_usd=20351.19, market_cap=27012.0, fdv=27012.0, total_minted=5674),
    then=[{"token_price_usd": 3.1e-08}])
# the record as stored today: the old clamp wrote 0.0 for a loss. That 0.0 is kept as the previous ATH profit
# (max(previous, current)); only a clean-up can tell it from a real zero. Both languages must still agree.
case("bscjs_legacy_zero_ath_profit_as_stored", rec(
    contract="0xbe9be985cd95219091ef2aec431101ececda4265", name="BSCJS", symbol="BSCJS",
    standard="Fair-Mint Contract (Dispenses $BSCJS Token)", mint_price_str="1.0000 BNB",
    mint_value_wei="1000000000000000000", mint_price_bnb=1.0, token_price_usd=2.845e-08, lp_usd=20351.19,
    total_minted=5674, ath_profit_usd=0.0, current_profit_usd=0.0))
case("pearl_coin_stale_ath_347x", rec(
    contract="0x7e2da878217eb995f9e5d36ed32c9cea52442681", name="珍珠币", symbol="珍珠币",
    standard="BEP-20 Token (8 decimals)", mint_price_str="DEX Traded", mint_value_wei="0", token_price_usd=1.153e-05,
    lp_usd=1.26, market_cap=2482.0, total_minted=119999956.54, max_supply=1e9, ath_price_usd=0.002061,
    ath_profit_usd=0.002061, current_profit_usd=0.002061))
case("mlee_dao_1_9bn_mcap_lp_57k", rec(
    contract="0xc4bf2518aa953304170753388f83277eb9588a7a", name="MLEE DAO", symbol="MDAO", mint_price_str="DEX Traded",
    token_price_usd=0.1008, lp_usd=57526.62, market_cap=1892334511.0, fdv=1892334511.0, total_minted=18000000000.0,
    pair_address="0xff3f7ddedea24ddc702efed12e56ec89dbd77c24", volume_24h=0.82))
case("spacedog_pair_url_only", rec(
    contract="0x99487232829ac745b5be20c1d215f255335812ef", name="Space Dog", symbol="SPACEDOG",
    mint_price_str="DEX Traded", token_price_usd=8.951e-09, lp_usd=0.41, market_cap=21.0, fdv=21.0,
    total_minted=2367000000.0, volume_24h=0.13,
    pair_url="https://dexscreener.com/bsc/0xaf6ec85a0400c21c61a8fa01bcd0460122f3e691"))
case("rocketfi_name_contains_etf", rec(
    contract="0xf83565f1704474495bde1517298b8dace00642c8", name="Rocketfi", symbol="KFI", mint_price_str="⭐ FREE",
    has_free_mint=True, mint_calldata="0x1249c58b", total_minted=1e-18, max_supply=1e-14))
case("marsid_nft_tbd", rec(
    contract="0xcbac4fac33afb361e836e6a0120f5a5aac9122b3", name="Mars Nexus Identity", symbol="MARSID",
    standard="ERC-721 (NFT Collection)", mint_price_str="TBD", mint_value_wei="0", mint_price_bnb=0.000478250988556096,
    mint_calldata="0x1249c58b", total_minted=9903, max_supply=12000))
case("dont_444t_supply", rec(
    contract="0x29924cec2ed4731ef135a61614f3bd455b727777", name="don't buy", symbol="DONT", mint_price_str="DEX Traded",
    token_price_usd=2.713e-08, lp_usd=2614.56, total_minted=4.44e14, volume_24h=284.02))
case("dont_mcap_from_supply", rec(
    contract="0x29924cec2ed4731ef135a61614f3bd455b727777", name="don't buy", symbol="DONT", token_price_usd=2.713e-08,
    lp_usd=2614.56, total_minted=4.44e14))

# --- profit edge cases
case("free_mint_token_with_liquidity", rec(has_free_mint=True, mint_price_str="⭐ FREE", token_price_usd=0.5,
                                           lp_usd=5000.0, total_minted=1000, max_supply=1000))
case("free_mint_string_only", rec(mint_price_str="FREE", token_price_usd=0.2, lp_usd=3000.0))
case("paid_mint_wei_5x", rec(mint_price_str="0.0100 BNB", mint_value_wei="10000000000000000", mint_price_bnb=0.01,
                             token_price_usd=35.25 / 1000, lp_usd=8000.0, token_price_usd_unused=None))
case("paid_mint_bnb_only", rec(mint_price_bnb=0.02, mint_value_wei="0", token_price_usd=0.1, lp_usd=9000.0))
case("no_mint_info_at_all", rec(token_price_usd=0.1, lp_usd=9000.0))
case("nft_floor_usd_paid", rec(standard="ERC-721 (NFT Collection)", mint_price_bnb=0.01,
                               mint_value_wei="10000000000000000", floor_price_usd=35.25, max_supply=500,
                               total_minted=500))
case("nft_floor_bnb_free_no_lp", rec(standard="ERC-721 (NFT Collection)", mint_price_str="FREE", floor_price_bnb=0.02,
                                     lp_usd=0))
case("thin_pool_price_gets_no_ath", rec(mint_price_bnb=0.001, mint_value_wei="1000000000000000", token_price_usd=0.5,
                                        lp_usd=500.0))
case("poisoned_ath_is_dropped", rec(mint_price_bnb=0.001, mint_value_wei="1000000000000000", token_price_usd=0.01,
                                    lp_usd=400.0, ath_price_usd=5.0, ath_profit_usd=4.9, ath_multiplier=7000.0))
case("poisoned_ath_kept_on_deep_pool", rec(mint_price_bnb=0.001, mint_value_wei="1000000000000000",
                                           token_price_usd=0.01, lp_usd=40000.0, ath_price_usd=5.0,
                                           ath_profit_usd=4.9, ath_multiplier=7000.0))
case("ath_profit_is_a_running_max", rec(mint_price_bnb=0.01, mint_value_wei="10000000000000000",
                                        token_price_usd=20.0, lp_usd=50000.0),
     then=[{"token_price_usd": 40.0}, {"token_price_usd": 12.0}, {"token_price_usd": 9.0}])
case("ath_multiplier_follows_ath_price", rec(mint_price_bnb=0.01, mint_value_wei="10000000000000000",
                                             token_price_usd=7.05, lp_usd=50000.0, ath_multiplier=1.0),
     then=[{"token_price_usd": 14.1}, {"token_price_usd": 3.0}])
case("old_zero_ath_profit_not_falsy", rec(mint_price_bnb=1.0, mint_value_wei="1000000000000000000",
                                          token_price_usd=2.8e-08 * 1000, lp_usd=20000.0, ath_profit_usd=0.0,
                                          ath_price_usd=2.8e-05))
case("sold_out_cap", rec(has_free_mint=True, token_price_usd=0.3, lp_usd=4000.0, max_supply=1000, total_minted=1000))
case("astronomical_price_guard", rec(has_free_mint=True, token_price_usd=1e14, lp_usd=0.0002, total_minted=1.82e-15))
case("empty_pool_guard", rec(has_free_mint=True, token_price_usd=99749.99, lp_usd=0.0, total_minted=4.24e-16))

# --- exclusion lists
case("btcb_by_contract", rec(contract="0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c", symbol="BTCB",
                             token_price_usd=78000.0, lp_usd=9e6, mint_price_str="FREE"))
case("fist_symbol_only_in_python_before", rec(symbol="FIST", token_price_usd=0.26, lp_usd=7e6, has_free_mint=True))
case("sol_symbol", rec(symbol="SOL", token_price_usd=150.0, lp_usd=9e6, has_free_mint=True))
case("doge_symbol", rec(symbol="DOGE", token_price_usd=0.1, lp_usd=9e6, has_free_mint=True))
case("babab_equity", rec(symbol="BABAB", token_price_usd=90.0, lp_usd=9e6, has_free_mint=True))
case("skhyb_equity", rec(symbol="SKHYB", token_price_usd=50.0, lp_usd=9e6, has_free_mint=True))
case("bogus_printerai_contract", rec(contract="0xd0523ce816e2b404db5633d181572cded33d7fa9", symbol="PRINT",
                                     token_price_usd=3.0, lp_usd=90000.0, has_free_mint=True))
case("name_nasdaq_etf", rec(name="Nasdaq 100 ETF Token", symbol="QQ1", token_price_usd=3.0, lp_usd=90000.0,
                            has_free_mint=True))
case("name_etf_as_a_word", rec(name="Gold ETF", symbol="GE", token_price_usd=3.0, lp_usd=90000.0, has_free_mint=True))
case("name_pancakeswap_token", rec(name="PancakeSwap Token", symbol="XCK", token_price_usd=2.0, lp_usd=9e6))
case("name_bnb_ftetfx_is_not_etf", rec(name="Befetfy Coin", symbol="BFC", token_price_usd=0.2, lp_usd=9000.0,
                                       has_free_mint=True))

# --- market cap guard / secondary
case("mcap_60m_with_300k_lp_survives", rec(market_cap=60e6, lp_usd=300000.0, token_price_usd=1.0))
case("mcap_60m_with_200k_lp_hidden", rec(market_cap=60e6, lp_usd=200000.0, token_price_usd=1.0))
case("mcap_exactly_50m_low_lp", rec(market_cap=50e6, lp_usd=1000.0, token_price_usd=1.0))
case("mcap_from_fdv", rec(market_cap=0, fdv=123456.0, lp_usd=9000.0, token_price_usd=0.1))
case("mcap_from_market_price_alias", rec(market_price_usd=0.2, total_minted=1000.0, lp_usd=0))
case("mcap_unknown_unit_nft", rec(standard="ERC-721 (NFT Collection)", token_price_usd=0.5, total_minted=1e24))
case("mcap_fair_mint_counter", rec(standard="Fair-Mint Contract (Dispenses $X Token)", token_price_usd=0.03,
                                   total_minted=334157746779034348324247.0))
case("secondary_pair_address_only", rec(pair_address="0xabc", lp_usd=0, token_price_usd=0))
case("secondary_nothing", rec(lp_usd=0, token_price_usd=0, volume_24h=0))
case("secondary_graduated_only", rec(is_graduated=True))
case("secondary_element_sales_only", rec(standard="ERC-721 (NFT Collection)", secondary_vol_bnb=0.4))
case("secondary_price_and_volume", rec(token_price_usd=0.001, volume_24h=3.0, lp_usd=10.0))
case("secondary_excluded_even_with_pair", rec(symbol="USDT", pair_url="https://dexscreener.com/bsc/0x1", lp_usd=9e8))

SCORES = [0, 1, 19, 20, 24, 24.9, 25, 49, 49.9, 50, 69, 70, 74, 75, 79, 79.9, 80, 99, 100]

NODE = r"""
const app = require('./app.js');
const spec = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = {};
for (const [name, c] of Object.entries(spec.cases)) {
  const item = JSON.parse(JSON.stringify(c.item));
  const steps = [];
  const record = () => {
    const r = app.computeItemProfit(item, spec.bnb);
    steps.push({
      result: { sec_usd: r.secUsd, mint_usd: r.mintUsd, profit: r.profit, ath_price_usd: r.athPrice,
                ath_profit_usd: r.athProfit, ath_multiplier: r.multiplier, current_multiplier: r.currentMultiplier,
                is_sold: r.isSold, is_free: r.isFree },
      item: { ath_price_usd: item.ath_price_usd ?? null, ath_profit_usd: item.ath_profit_usd ?? null,
              ath_multiplier: item.ath_multiplier ?? null, current_profit_usd: item.current_profit_usd ?? null,
              current_multiplier: item.current_multiplier ?? null, mint_cost_usd: item.mint_cost_usd ?? null,
              secondary_price_usd: item.secondary_price_usd ?? null },
    });
  };
  record();
  for (const upd of c.then) { Object.assign(item, upd); record(); }
  const fresh = JSON.parse(JSON.stringify(c.item));
  out[name] = { steps, excluded: app.isExcludedMajorAsset(fresh), mcap: app.getSanitizedMcap(fresh),
                secondary: app.isSecondaryMarketAsset(fresh) };
}
out.__tiers = spec.scores.map(s => app.interestTier(s));
out.__consts = {
  pegs: [...app.EXCLUDED_PEGS_SYMBOLS].sort(), pegContracts: [...app.EXCLUDED_PEGS_CONTRACTS].sort(),
  bogus: [...app.BOGUS_SCAM_CONTRACTS].sort(), equity: [...app.SYNTHETIC_EQUITY_SYMBOLS].sort(),
  names: app.EXCLUDED_NAME_PHRASES.slice(),
};
process.stdout.write(JSON.stringify(out));
"""

PROFIT_KEYS = ("sec_usd", "mint_usd", "profit", "ath_price_usd", "ath_profit_usd", "ath_multiplier",
               "current_multiplier", "is_sold", "is_free")
ITEM_KEYS = ("ath_price_usd", "ath_profit_usd", "ath_multiplier", "current_profit_usd", "current_multiplier",
             "mint_cost_usd", "secondary_price_usd")


def run_python():
    out = {}
    for name, c in CASES.items():
        item = copy.deepcopy(c["item"])
        steps = []

        def record():
            r = monitor.update_post_mint_profit_metrics(item, BNB)
            steps.append({"result": {k: r[k] for k in PROFIT_KEYS},
                          "item": {k: item.get(k) for k in ITEM_KEYS}})
        record()
        for upd in c["then"]:
            item.update(upd)
            record()
        fresh = copy.deepcopy(c["item"])
        out[name] = {"steps": steps, "excluded": filters.is_excluded_asset(fresh),
                     "mcap": filters.sanitized_mcap(fresh), "secondary": filters.is_secondary_market(fresh)}
    return out


@pytest.fixture(scope="module")
def both():
    payload = json.dumps({"cases": CASES, "bnb": BNB, "scores": SCORES})
    res = subprocess.run(["node", "-e", NODE], input=payload, capture_output=True, text=True, cwd=REPO)
    assert res.returncode == 0, res.stderr
    return run_python(), json.loads(res.stdout)


def same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is b
        return a == b or (math.isclose(a, b, rel_tol=1e-12, abs_tol=0.0))
    return a == b


@pytest.mark.parametrize("name", sorted(CASES))
def test_python_and_javascript_agree(both, name):
    py, js = both[0][name], both[1][name]
    assert len(py["steps"]) == len(js["steps"])
    for i, (p, j) in enumerate(zip(py["steps"], js["steps"])):
        for k in PROFIT_KEYS:
            assert same(p["result"][k], j["result"][k]), f"{name} step {i} result.{k}: py={p['result'][k]!r} js={j['result'][k]!r}"
        for k in ITEM_KEYS:
            assert same(p["item"][k], j["item"][k]), f"{name} step {i} item.{k}: py={p['item'][k]!r} js={j['item'][k]!r}"
    assert py["excluded"] == js["excluded"], f"{name} excluded"
    assert same(py["mcap"], js["mcap"]), f"{name} mcap: py={py['mcap']!r} js={js['mcap']!r}"
    assert py["secondary"] == js["secondary"], f"{name} secondary"


def test_interest_tiers_agree(both):
    assert [filters.interest_tier(s) for s in SCORES] == both[1]["__tiers"]
    assert both[1]["__tiers"][SCORES.index(75)] == "HIGH"       # 75 is the same tier in every table batch


def test_exclusion_lists_are_identical(both):
    c = both[1]["__consts"]
    assert set(c["pegs"]) == set(filters.EXCLUDED_PEGS_SYMBOLS)
    assert set(c["pegContracts"]) == set(filters.EXCLUDED_PEGS_CONTRACTS)
    assert set(c["bogus"]) == set(filters.BOGUS_SET)
    assert set(c["equity"]) == set(filters.SYNTHETIC_EQUITY_SYMBOLS)
    assert tuple(c["names"]) == filters.EXCLUDED_NAME_PHRASES


def test_at_least_thirty_records_are_compared():
    assert len(CASES) >= 30


# ----------------------------------------------------------------------- the audit's named expectations
def result(name, step=-1):
    return run_python()[name]["steps"][step]["result"]


def test_h6_dex_traded_and_tbd_have_no_profit():
    for name in ("dagou_dex_traded_68usd", "pheromone_tbd_free_headline", "newwolf_tbd_with_calldata",
                 "marsid_nft_tbd", "no_mint_info_at_all"):
        r = result(name)
        assert r["profit"] is None and r["ath_profit_usd"] is None and r["ath_multiplier"] is None, name
        assert r["is_free"] is False, name


def test_h6_gainers_exclude_unknown_cost_records():
    history = [copy.deepcopy(CASES[n]["item"]) for n in
               ("dagou_dex_traded_68usd", "pheromone_tbd_free_headline", "newwolf_tbd_with_calldata",
                "free_mint_token_with_liquidity")]
    stats = monitor.compute_market_cap_stats(history, BNB)
    assert [g["symbol"] for g in stats["all_gainers"]] == ["TST"]       # only the verified free mint


def test_h7_bscjs_loss_is_the_same_in_both_languages(both):
    p = both[0]["bscjs_paid_1bnb_under_water"]["steps"]
    j = both[1]["bscjs_paid_1bnb_under_water"]["steps"]
    assert p[0]["result"]["profit"] == pytest.approx(2.845e-08 - 705.0)
    assert p[0]["item"]["current_profit_usd"] < 0 and p[0]["item"]["ath_profit_usd"] < 0
    assert p == j or all(same(p[i]["result"]["profit"], j[i]["result"]["profit"]) for i in range(len(p)))
    assert p[1]["item"]["ath_profit_usd"] >= p[0]["item"]["ath_profit_usd"]


def test_h7_ath_profit_never_decreases_and_first_value_is_kept():
    steps = run_python()["ath_profit_is_a_running_max"]["steps"]
    ath = [s["item"]["ath_profit_usd"] for s in steps]
    assert ath == sorted(ath) and ath[1] > ath[0] and ath[3] == ath[1]
    assert steps[3]["item"]["current_profit_usd"] < ath[3]


def test_h8_thin_pool_never_sets_an_ath_and_poisoned_ath_is_dropped():
    assert run_python()["thin_pool_price_gets_no_ath"]["steps"][0]["item"]["ath_price_usd"] is None
    dropped = run_python()["poisoned_ath_is_dropped"]["steps"][0]["item"]
    assert dropped["ath_price_usd"] is None and dropped["ath_profit_usd"] is None and dropped["ath_multiplier"] is None
    kept = run_python()["poisoned_ath_kept_on_deep_pool"]["steps"][0]["item"]
    assert kept["ath_price_usd"] == 5.0                                  # a deep pool backs it: not treated as poisoned


def test_h8_free_mint_multiplier_is_null():
    r = result("free_mint_token_with_liquidity")
    assert r["is_free"] and r["ath_multiplier"] is None and r["current_multiplier"] is None and r["profit"] == 0.5


def test_h8_ath_multiplier_follows_the_ath_price():
    steps = run_python()["ath_multiplier_follows_ath_price"]["steps"]
    assert [s["item"]["ath_multiplier"] for s in steps] == [1.0, 2.0, 2.0]     # price 7.05 -> mint 7.05 USD
    assert steps[2]["item"]["current_multiplier"] == pytest.approx(0.43, abs=0.005)


def test_h9_mlee_dao_hidden_in_both_and_60m_with_300k_lp_survives(both):
    assert both[0]["mlee_dao_1_9bn_mcap_lp_57k"]["mcap"] is None and both[1]["mlee_dao_1_9bn_mcap_lp_57k"]["mcap"] is None
    assert both[0]["mcap_60m_with_300k_lp_survives"]["mcap"] == 60e6 == both[1]["mcap_60m_with_300k_lp_survives"]["mcap"]
    assert both[0]["mcap_60m_with_300k_lp_survives"]["secondary"] is True          # not dropped as a "major asset"


def test_m5_spacedog_is_secondary_in_both(both):
    assert both[0]["spacedog_pair_url_only"]["secondary"] is True
    assert both[1]["spacedog_pair_url_only"]["secondary"] is True


def test_exclusion_drift_cases(both):
    for name in ("fist_symbol_only_in_python_before", "sol_symbol", "doge_symbol", "babab_equity", "skhyb_equity",
                 "bogus_printerai_contract", "name_nasdaq_etf", "name_etf_as_a_word"):
        assert both[0][name]["excluded"] and both[1][name]["excluded"], name
    for name in ("rocketfi_name_contains_etf", "name_bnb_ftetfx_is_not_etf"):
        assert not both[0][name]["excluded"] and not both[1][name]["excluded"], name


def test_h4_supply_guess_is_gone_in_both(both):
    assert both[0]["dont_mcap_from_supply"]["mcap"] == pytest.approx(1.2046e7, rel=1e-3)
    assert same(both[0]["dont_mcap_from_supply"]["mcap"], both[1]["dont_mcap_from_supply"]["mcap"])
    for name in ("mcap_unknown_unit_nft", "mcap_fair_mint_counter"):
        assert both[0][name]["mcap"] is None and both[1][name]["mcap"] is None, name


def test_api_uses_the_shared_rules():
    import api
    d = copy.deepcopy(CASES["mlee_dao_1_9bn_mcap_lp_57k"]["item"])
    assert api.get_sanitized_mcap(d) is None
    assert api.is_secondary_market_asset(copy.deepcopy(CASES["spacedog_pair_url_only"]["item"]))
    assert api.is_bogus_or_synthetic_asset(copy.deepcopy(CASES["fist_symbol_only_in_python_before"]["item"]))


def test_one_interest_formula_for_worker_and_analyze():
    import worker
    item = {"lp_usd": 60000, "volume_24h": 25000, "buys_24h": 600, "smart_wallets": ["a.ai"]}
    score, rating = monitor.compute_interest(item)
    assert (score, rating) == (100, "🔥 SURGING")
    worker._recompute_interest(item)
    assert (item["score"], item["rating"]) == (100, "🔥 SURGING")
    assert monitor.compute_interest({"lp_usd": 3000, "volume_24h": 600, "buys_24h": 30}) == (40, "⚡ MODERATE")
    assert monitor.compute_interest({"lp_usd": 90000, "buys_24h": 900, "risk_level": "RUG"})[0] == 0
    assert monitor.compute_interest({"lp_usd": 3000, "risk_level": "HIGH"})[0] == 0


# ------------------------------------------------------------------------------ small JavaScript-only fixes
def node_json(script):
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=REPO)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_overview_fallback_counts_nft_surges():
    """applyOverviewStatsFallback read evaluateNftSurge(d).surge, a key that does not exist."""
    out = node_json("""
      const app = require('./app.js');
      const hot = { contract: '0x' + 'a'.repeat(40), standard: 'ERC-721 (NFT Collection)', secondary_sales_count: 6,
                    secondary_vol_bnb: 1.0 };
      const cold = { contract: '0x' + 'b'.repeat(40), standard: 'ERC-721 (NFT Collection)', secondary_sales_count: 1 };
      app.setAllDrops([hot, cold]);
      app.setGlobalStats(null);
      app.applyOverviewStatsFallback();
      console.log(JSON.stringify({ nft: app.getGlobalStats().nft_surge }));
    """)
    assert out["nft"] == 1


def test_interest_badge_is_one_threshold_set_with_the_real_score():
    out = node_json("""
      const app = require('./app.js');
      console.log(JSON.stringify([75, 5, 19, 20, 49, 50, 80, 100].map(s => app.interestBadgeHtml(s))));
    """)
    text = [b.split(">")[1].split("<")[0] for b in out]
    assert text == ["🚀 High (75)", "🌱 Early (5)", "🌱 Early (19)", "🌱 Early (20)", "⚡ Moderate (49)",
                    "🚀 High (50)", "🔥 Surging (80)", "🔥 Surging (100)"]   # 75 reads the same everywhere; no "Cold (0)"
