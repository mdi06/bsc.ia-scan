import worker


def test_scan_range_no_gap_when_slightly_behind():
    # 8 blocks behind: every block is scanned and last_block ends at the head
    assert worker._next_scan_range(100, 108) == (101, 108)


def test_scan_range_catches_up_in_steps_without_skipping():
    assert worker._next_scan_range(100, 125) == (101, 110)   # 25 behind: first 10, nothing skipped
    assert worker._next_scan_range(110, 125) == (111, 120)
    assert worker._next_scan_range(120, 125) == (121, 125)


def test_scan_range_jumps_when_far_behind():
    assert worker._next_scan_range(100, 200) == (191, 200)


def test_scan_range_single_block():
    assert worker._next_scan_range(100, 101) == (101, 101)


def test_known_infra_set_is_unchanged():
    import monitor
    assert worker.monitor.known_infra_addresses() == {
        "0xca143ce32fe78f1f7019d7d551a6402fc5350c73",  # Pancake factory
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
        "0x55d398326f99059ff775485246999027b3197955",  # USDT
        "0x60b9262456de011f7d15a54cdd8c48baaa8b022f",  # Agent ID registry
        "0x08ced32a7f3eec915ba84415e9c07a7286977956",  # Space ID master registry
        "0x5c952063c7fc8610ffdb798152d69f0b9550762b",  # four.meme factory
        "0xf251f83e40a78868fcfa3fa4599dad6494e46034",  # four.meme router
        "0x10ed43c718714eb63d5aa57b78b54704e256024e",  # Pancake router
        "0x13f4ea83d0bd40e75c8222255bc855a974568dd4",
    }
