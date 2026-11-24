"""Discovery seeded holders = 1 on every new record, so about half of the stored tokens carried a holder count nobody measured."""
import worker


def rec(**kw):
    d = {"contract": "0x" + "1" * 40, "holders": 1, "buys_24h": 1, "buys_1h": 1}
    d.update(kw)
    return d


def test_a_record_with_no_market_data_goes_back_to_unknown():
    r = rec()
    assert worker.clear_placeholder_counts([r]) == [r]
    assert r["holders"] is None and r["buys_24h"] is None and r["buys_1h"] is None
    z = rec(buys_24h=0, buys_1h=0)
    worker.clear_placeholder_counts([z])
    assert z["holders"] is None and z["buys_24h"] is None


def test_measured_counts_are_left_alone():
    keep = [rec(market_source="dexscreener"), rec(market_updated_at=1.0), rec(pair_url="https://dexscreener.com/bsc/0x1"),
            rec(lp_usd=900.0), rec(holders_counted_at=5.0), rec(holders=2), rec(holders=None), rec(buys_24h=44)]
    before = [dict(r) for r in keep]
    changed = worker.clear_placeholder_counts(keep)
    assert changed == [keep[-1]]            # the 44-buys record has no market source: its holders = 1 is a placeholder too
    assert keep[-1]["holders"] is None and keep[-1]["buys_24h"] == 44
    assert keep[:-1] == before[:-1]


def test_nothing_to_do_the_second_time():
    r = rec()
    worker.clear_placeholder_counts([r])
    assert worker.clear_placeholder_counts([r]) == []
