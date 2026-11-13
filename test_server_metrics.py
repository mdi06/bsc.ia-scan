"""Task 5 step one: list and detail responses carry the server-computed metrics (api.with_server_metrics), and the
browser's four helpers return the same numbers whether they read them from the response or calculate them.

The audit's example records come from test_parity_python_js.CASES (the real records named in METRICS_AUDIT_REPORT.md,
plus hand-made edge cases)."""
import copy
import json
import math
import os
import subprocess

import pytest
from fastapi.testclient import TestClient

import api
import db as db_module
import filters
import monitor
from test_parity_python_js import CASES, BNB

REPO = os.path.dirname(os.path.abspath(__file__))
PROFIT_KEYS = ("sec_usd", "mint_usd", "profit", "ath_price_usd", "ath_profit_usd", "ath_multiplier",
               "current_multiplier", "is_sold", "is_free")


def served(name, bnb=BNB, live=True):
    return api.with_server_metrics(copy.deepcopy(CASES[name]["item"]), bnb, live)


# ------------------------------------------------------------------------------------ the audit's records
def test_server_metrics_follow_the_audit_rules():
    # H6: DEX Traded / TBD have no profit
    for name in ("dagou_dex_traded_68usd", "pheromone_tbd_free_headline", "newwolf_tbd_with_calldata"):
        p = served(name)["server_metrics"]["profit"]
        assert p["profit"] is None and p["mint_usd"] is None and p["ath_profit_usd"] is None, name
        assert p["is_free"] is False, name
    # MARSID: the mint price in BNB is known but nothing trades, so there is still no profit and no "free mint"
    p = served("marsid_nft_tbd")["server_metrics"]["profit"]
    assert p["profit"] is None and p["ath_profit_usd"] is None and p["is_free"] is False
    # H7: a loss is negative, and so is the ATH profit that starts from it
    p = served("bscjs_paid_1bnb_under_water")["server_metrics"]["profit"]
    assert p["profit"] == pytest.approx(2.845e-08 - BNB) and p["profit"] < 0
    assert p["ath_profit_usd"] == pytest.approx(p["profit"])
    # H8: the poisoned ATH of the pearl coin is not served
    s = served("pearl_coin_stale_ath_347x")
    assert s["server_metrics"]["profit"]["ath_price_usd"] in (None, s["ath_price_usd"])
    assert s["ath_price_usd"] != 0.002061 or s["lp_usd"] >= monitor.ATH_MIN_LP_USD
    # H9: MLEE DAO ($1.9B market cap on $57K liquidity) has no market cap; $60M on $300K liquidity keeps it
    assert served("mlee_dao_1_9bn_mcap_lp_57k")["server_metrics"]["market_cap"] is None
    assert served("mlee_dao_1_9bn_mcap_lp_57k")["market_cap"] == 1892334511.0          # the raw field is untouched
    assert served("mcap_60m_with_300k_lp_survives")["server_metrics"]["market_cap"] == 60e6
    # M5: pair_url alone makes SPACEDOG a secondary-market asset
    assert served("spacedog_pair_url_only")["server_metrics"]["secondary"] is True
    # exclusion lists
    for name in ("fist_symbol_only_in_python_before", "sol_symbol", "babab_equity", "bogus_printerai_contract",
                 "name_etf_as_a_word"):
        assert served(name)["server_metrics"]["excluded"] is True, name
    assert served("rocketfi_name_contains_etf")["server_metrics"]["excluded"] is False


def test_the_refreshed_profit_fields_equal_the_profit_block():
    s = served("paid_mint_wei_5x")
    p = s["server_metrics"]["profit"]
    assert s["current_profit_usd"] == pytest.approx(p["profit"])
    assert s["mint_cost_usd"] == pytest.approx(p["mint_usd"], abs=1e-4)
    assert s["secondary_price_usd"] == pytest.approx(p["sec_usd"])
    assert s["current_multiplier"] == p["current_multiplier"]
    assert s["ath_price_usd"] == p["ath_price_usd"] and s["ath_profit_usd"] == p["ath_profit_usd"]
    assert s["ath_multiplier"] == p["ath_multiplier"]


def test_a_stale_stored_profit_is_replaced_in_the_served_copy_only():
    stored = copy.deepcopy(CASES["pearl_coin_stale_ath_347x"]["item"])
    stored["ath_timestamp"] = 111
    before = copy.deepcopy(stored)
    out = api.with_server_metrics(stored, BNB, True)
    assert stored == before                                  # the stored record (db cache) is never mutated
    assert out["current_profit_usd"] is None                 # "DEX Traded": no mint position, no profit
    assert out["ath_timestamp"] == 111                       # worker bookkeeping is not touched


def test_no_profit_block_while_the_servers_bnb_price_is_not_live():
    s = served("paid_mint_wei_5x", live=False)
    assert set(s["server_metrics"]) == {"excluded", "market_cap", "secondary"}
    assert "current_profit_usd" not in s                  # nothing refreshed
    s = served("paid_mint_wei_5x", bnb=0, live=True)
    assert "profit" not in s["server_metrics"]


def test_a_malformed_record_does_not_break_the_response():
    bad = {"contract": "0x" + "1" * 40, "symbol": "X", "name": "x", "mint_value_wei": "not a number",
           "token_price_usd": 0.1}
    out = api.with_server_metrics(bad, BNB, True)
    assert "profit" not in out["server_metrics"] and out["server_metrics"]["excluded"] is False


# ------------------------------------------------------------------- Python server value == JavaScript calculation
NODE = r"""
const app = require('./app.js');
const spec = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = {};
const shape = r => ({ sec_usd: r.secUsd, mint_usd: r.mintUsd, profit: r.profit, ath_price_usd: r.athPrice,
  ath_profit_usd: r.athProfit, ath_multiplier: r.multiplier, current_multiplier: r.currentMultiplier,
  is_sold: r.isSold, is_free: r.isFree });
for (const [name, c] of Object.entries(spec.cases)) {
  const raw = JSON.parse(JSON.stringify(c.raw));
  const srv = JSON.parse(JSON.stringify(c.served));
  out[name] = {
    calc: { profit: shape(app.computeItemProfit(raw, spec.bnb)), mcap: app.getSanitizedMcap(c.raw),
            secondary: app.isSecondaryMarketAsset(c.raw), excluded: app.isExcludedMajorAsset(c.raw) },
    fromServer: { profit: shape(app.computeItemProfit(srv, spec.bnb)), mcap: app.getSanitizedMcap(srv),
                  secondary: app.isSecondaryMarketAsset(srv), excluded: app.isExcludedMajorAsset(srv) },
  };
}
process.stdout.write(JSON.stringify(out));
"""


def same(a, b):
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b or math.isclose(a, b, rel_tol=1e-12)
    return a == b


@pytest.fixture(scope="module")
def js_results():
    spec = {"bnb": BNB, "cases": {}}
    for name, c in CASES.items():
        s = served(name)
        s = json.loads(json.dumps(s))
        spec["cases"][name] = {"raw": c["item"], "served": s}
    res = subprocess.run(["node", "-e", NODE], input=json.dumps(spec), capture_output=True, text=True, cwd=REPO)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@pytest.mark.parametrize("name", sorted(CASES))
def test_reading_the_server_value_equals_calculating_it_in_the_browser(js_results, name):
    r = js_results[name]
    calc, srv = r["calc"], r["fromServer"]
    for k in PROFIT_KEYS:
        assert same(calc["profit"][k], srv["profit"][k]), f"{name} profit.{k}: calc={calc['profit'][k]!r} server={srv['profit'][k]!r}"
    assert same(calc["mcap"], srv["mcap"]), name
    assert calc["secondary"] == srv["secondary"], name
    assert calc["excluded"] == srv["excluded"], name


# ------------------------------------------------------------------------------------------- the endpoints
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
    monkeypatch.setitem(monitor._bnb_price_state, "usd", BNB)
    monkeypatch.setitem(monitor._bnb_price_state, "live", True)
    monkeypatch.setitem(monitor._bnb_price_state, "ok_at", 4102444800.0)         # far future: always "recent"
    monkeypatch.setattr(monitor, "bnb_price_is_live", lambda: monitor._bnb_price_state["live"])
    c = TestClient(api.app)
    c.db = manager
    return c


def stored(name, block):
    d = copy.deepcopy(CASES[name]["item"])
    d["block"] = block
    d["detected_at"] = "2026-11-12 09:00:00"
    return d


NAMES = ["paid_mint_wei_5x", "mlee_dao_1_9bn_mcap_lp_57k", "spacedog_pair_url_only", "dagou_dex_traded_68usd",
         "free_mint_token_with_liquidity"]


def seed(client):
    rows = []
    for i, n in enumerate(NAMES):
        d = stored(n, 2000 - i)
        d["contract"] = "0x" + f"{i + 1:040x}"       # the cases share a default contract
        rows.append(d)
    rows.append(stored("sol_symbol", 1000))           # excluded: never served
    client.db.upsert_projects(rows)
    return rows


def test_feed_records_carry_server_metrics(client):
    seed(client)
    body = client.get("/api/feed").json()
    by_sym = {p["symbol"]: p for p in body["projects"]}
    assert len(body["projects"]) == len(NAMES) and "SOL" not in by_sym
    for p in body["projects"]:
        sm = p["server_metrics"]
        assert set(sm) == {"excluded", "market_cap", "secondary", "profit"} and sm["excluded"] is False
        assert set(sm["profit"]) == set(PROFIT_KEYS)
    mlee = next(p for p in body["projects"] if p["symbol"] == "MDAO")
    assert mlee["server_metrics"]["market_cap"] is None and mlee["market_cap"] == 1892334511.0
    dagou = next(p for p in body["projects"] if p["symbol"] == "DAGOU")
    assert dagou["server_metrics"]["profit"]["profit"] is None
    paid = next(p for p in body["projects"] if p["symbol"] == "TST" and p["mint_price_str"] == "0.0100 BNB")
    assert paid["server_metrics"]["profit"]["mint_usd"] == pytest.approx(0.01 * BNB)


def test_feed_filters_and_delta_carry_them_too(client):
    seed(client)
    sec = client.get("/api/feed?filter=secondary").json()["projects"]
    assert sec and all(p["server_metrics"]["secondary"] is True for p in sec)
    delta = client.get("/api/feed?since_block=0").json()
    assert delta["mode"] == "delta" and delta["projects"]
    assert all("server_metrics" in p for p in delta["projects"])


def test_detail_endpoint_carries_them_and_keeps_the_full_record(client):
    rows = seed(client)
    rows[0]["mint_calldata"] = "0x" + "ab" * 600
    client.db.upsert_projects([rows[0]])
    body = client.get(f"/api/projects?search={rows[0]['contract']}").json()
    p = body["projects"][0]
    assert p["server_metrics"]["profit"]["mint_usd"] == pytest.approx(0.01 * BNB)
    assert p["mint_calldata"] == "0x" + "ab" * 600                       # the detail response is not slimmed
    assert p["progress"] is None or isinstance(p["progress"], float)    # derived fields still there
    feed = client.get("/api/feed").json()["projects"]
    f = next(x for x in feed if x["contract"] == rows[0]["contract"])
    assert "mint_calldata" not in f and f["has_mint_calldata"] is True and "server_metrics" in f


def test_stored_records_are_never_changed_by_serving_them(client):
    rows = seed(client)
    client.get("/api/feed")
    client.get("/api/projects")
    for r in rows[:-1]:
        db_rec = next(p for p in client.db.get_all_projects() if p["contract"] == r["contract"])
        assert "server_metrics" not in db_rec


def test_without_a_live_bnb_price_the_profit_block_is_left_out(client, monkeypatch):
    seed(client)
    monkeypatch.setitem(monitor._bnb_price_state, "live", False)
    for p in client.get("/api/feed").json()["projects"]:
        assert "profit" not in p["server_metrics"] and "market_cap" in p["server_metrics"]


def test_a_cached_feed_response_still_equals_an_uncached_one(client, monkeypatch):
    seed(client)
    monkeypatch.setattr(api.time, "time", lambda: 1794300000.0)
    for url in ("/api/feed", "/api/feed?filter=secondary", "/api/feed?since_block=0", "/api/feed?limit=2&offset=1"):
        api._current_snapshot = None
        cold = client.get(url)
        warm = client.get(url)
        assert cold.status_code == 200 and cold.content == warm.content and cold.headers["etag"] == warm.headers["etag"], url
        assert b"server_metrics" in cold.content


def test_304_responses_are_unchanged(client):
    seed(client)
    r = client.get("/api/feed")
    again = client.get("/api/feed", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
