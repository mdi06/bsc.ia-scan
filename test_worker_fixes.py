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
