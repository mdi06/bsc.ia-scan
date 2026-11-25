"""Profit per mint: tokens per mint read from the chain, sell tax, honeypots, pool size at the peak."""
import pytest

import monitor
import worker

C = "0x4ac4db3a2afc9a044de42dc4f63d5ca168d88888"
PAYER = "0xe79b1a1d254ca78cdc691516ae3c1e480d847f36"
POOL = "0x60593046bfd66e8c3ee881e36391d2d21983bad7"
T = monitor.TRANSFER_TOPIC


def topic(addr):
    return "0x" + "0" * 24 + addr[2:]


def transfer(token, frm, to, amount):
    return {"address": token, "topics": [T, topic(frm), topic(to)], "data": hex(amount)}


# neiro's real first mint (tx 0x9cd3...73c0): 0.003 BNB in, 50 tokens to the payer, 50 tokens + the BNB to the pool
NEIRO_TX = {"from": PAYER, "to": C, "value": hex(3 * 10**15)}
NEIRO_RECEIPT = {"status": "0x1", "logs": [
    transfer(C, C, POOL, 50 * 10**18),
    transfer("0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", "0x10ed43c718714eb63d5aa57b78b54704e256024e", POOL, 3 * 10**15),
    transfer(C, C, PAYER, 50 * 10**18),
]}


def test_tokens_from_a_real_mint_receipt():
    assert monitor.tokens_from_mint_receipt(NEIRO_TX, NEIRO_RECEIPT, [C], 3 * 10**15, 18) == 50.0


def test_several_mints_in_one_transaction_are_scaled_to_one():
    tx = dict(NEIRO_TX, value=hex(9 * 10**15))
    receipt = {"status": "0x1", "logs": [transfer(C, C, PAYER, 150 * 10**18)]}
    assert monitor.tokens_from_mint_receipt(tx, receipt, [C], 3 * 10**15, 18) == pytest.approx(50.0)


@pytest.mark.parametrize("tx,receipt", [
    (dict(NEIRO_TX, to=None), NEIRO_RECEIPT),                       # a deployment
    (dict(NEIRO_TX, value="0x0"), NEIRO_RECEIPT),                   # did not pay the mint price
    (NEIRO_TX, dict(NEIRO_RECEIPT, status="0x0")),                  # failed
    (NEIRO_TX, {"status": "0x1", "logs": [transfer(C, C, POOL, 10**18)]}),   # nothing to the payer
])
def test_a_transaction_that_is_not_a_paid_mint_gives_nothing(tx, receipt):
    assert monitor.tokens_from_mint_receipt(tx, receipt, [C], 3 * 10**15, 18) is None


class FakeRpc:
    def __init__(self, tx=None, receipt=None, getters=None):
        self.tx, self.receipt, self.getters = tx, receipt, getters or {}

    def call(self, method, params):
        return self.tx if method == "eth_getTransactionByHash" else self.receipt

    def call_contract(self, to, data):
        return self.getters.get(data)


def test_read_tokens_per_mint_receipt_then_getter():
    assert monitor.read_tokens_per_mint(FakeRpc(NEIRO_TX, NEIRO_RECEIPT), C, "0xabc", 3 * 10**15, 18) == (50.0, "receipt")
    sel = monitor.keccak_selector("MINT_AMOUNT()")
    rpc = FakeRpc(getters={sel: "0x" + format(1000 * 10**18, "064x")})
    assert monitor.read_tokens_per_mint(rpc, C, None, 3 * 10**15, 18) == (1000.0, "getter")
    assert monitor.read_tokens_per_mint(FakeRpc(), C, None, 3 * 10**15, 18) == (None, None)


def token(**kw):
    d = {"contract": C, "standard": "BEP-20 Token (18 decimals)", "mint_price_str": "0.0030 BNB",
         "mint_value_wei": "3000000000000000", "token_price_usd": 0.05, "lp_usd": 20000.0}
    d.update(kw)
    return d


def test_token_profit_is_per_mint_after_tax_and_pool():
    d = token(tokens_per_mint=50, security={"sell_tax": 3.0})
    r = monitor.update_post_mint_profit_metrics(d, bnb_price=600.0)          # mint = $1.80
    sell_now = 10000 * 2.5 / 10002.5 * 0.9975 * 0.97                          # 50 x $0.05 into a $20k pool, 3% tax
    assert r["profit"] == pytest.approx(sell_now - 1.8)
    assert r["current_multiplier"] == pytest.approx(2.5 / 1.8, abs=0.005)
    assert d["ath_lp_usd"] == 20000.0                                         # the pool when the peak was set
    assert r["units"] == 50 and r["sell_tax_pct"] == 3.0


def test_token_without_tokens_per_mint_has_no_profit():
    r = monitor.update_post_mint_profit_metrics(token(), bnb_price=600.0)
    assert r["profit"] is None and r["ath_profit_usd"] is None and r["ath_multiplier"] is None


def test_honeypot_has_no_profit():
    r = monitor.update_post_mint_profit_metrics(token(tokens_per_mint=50, security={"is_honeypot": True}), 600.0)
    assert r["profit"] is None and r["ath_profit_usd"] is None and r["can_sell"] is False


def test_peak_without_a_known_pool_is_not_capped():
    d = token(tokens_per_mint=50, token_price_usd=0.0, lp_usd=0.0, ath_price_usd=0.073)
    r = monitor.update_post_mint_profit_metrics(d, bnb_price=600.0)
    assert r["ath_profit_usd"] == pytest.approx(50 * 0.073 - 1.8)
    assert r["ath_multiplier"] == round(50 * 0.073 / 1.8, 2)


def test_worker_candidates_are_tokens_with_a_mint_and_no_value_yet():
    nft = {"contract": "0x" + "1" * 40, "standard": "ERC-721 (NFT Collection)", "mint_price_str": "0.001 BNB",
           "mint_value_wei": "1000000000000000"}
    known = token(contract="0x" + "2" * 40, tokens_per_mint=10)
    tried = token(contract="0x" + "3" * 40, tokens_per_mint_at=1_000_000)
    dex = token(contract="0x" + "4" * 40, mint_price_str="DEX Traded", mint_value_wei="0")
    priced = token(contract="0x" + "5" * 40, block=1)
    unpriced = token(contract="0x" + "6" * 40, block=99, token_price_usd=0.0)
    got = worker._tokens_per_mint_candidates([nft, known, tried, dex, unpriced, priced], now=1_000_100)
    assert [x["contract"] for x in got] == [priced["contract"], unpriced["contract"]]   # priced first
