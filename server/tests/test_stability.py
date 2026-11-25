"""Stable web-service status. No database."""

from app.models import ResultStatus
from app.services.stability import BALANCED, RELAXED, STRICT, StabilityConfig, cell_quorum, latency_limit, next_state

OK = ResultStatus.ok
SLOW = ResultStatus.degraded
DOWN = ResultStatus.fail


def test_one_bad_sample_stays_ok():
    assert next_state(OK, [SLOW, OK, OK, OK, OK], BALANCED) == OK
    assert next_state(OK, [DOWN], BALANCED) == OK


def test_three_bad_moves_state():
    assert next_state(OK, [SLOW, SLOW, OK, SLOW, OK], BALANCED) == SLOW
    assert next_state(OK, [DOWN, DOWN, OK, DOWN, OK], BALANCED) == DOWN


def test_recovery_needs_three_straight_ok():
    assert next_state(SLOW, [OK, OK, OK, SLOW, SLOW], BALANCED) == OK
    assert next_state(SLOW, [SLOW, OK, OK, SLOW, OK], BALANCED) == SLOW
    assert next_state(DOWN, [OK, OK, DOWN], BALANCED) == DOWN


def test_alternating_never_leaves_ok():
    assert next_state(OK, [OK, SLOW, OK, SLOW, OK], BALANCED) == OK
    assert next_state(OK, [DOWN, OK, DOWN, OK, OK], BALANCED) == OK


def test_latency_limit_respects_absolute_floor():
    assert latency_limit(100, 50, 40) == 150
    assert latency_limit(5, 50, 40) == 45
    assert latency_limit(5, 50, 40) > 12


def test_quorum_one_of_eight_stays_ok():
    statuses = [DOWN] + [OK] * 7
    status, bad, total = cell_quorum(statuses, BALANCED)
    assert (status, bad, total) == ("ok", 1, 8)


def test_quorum_fail_and_degraded_thresholds():
    four_down = [DOWN] * 4 + [OK] * 4
    assert cell_quorum(four_down, BALANCED)[0] == "fail"
    two_slow = [SLOW, SLOW] + [OK] * 6
    assert cell_quorum(two_slow, BALANCED)[0] == "degraded"
    assert cell_quorum([DOWN] * 8, BALANCED)[0] == "fail"


def test_presets_differ():
    assert RELAXED.pct_floor == 75
    assert STRICT.window_size == 3
    assert next_state(OK, [SLOW, SLOW, OK, OK, OK], RELAXED) == OK
    assert next_state(OK, [SLOW, SLOW, OK], STRICT) == SLOW


def test_custom_window_is_honored():
    cfg = StabilityConfig(window_size=4, degraded_count=4, fail_count=4, recover_count=2)
    assert next_state(OK, [SLOW, SLOW, SLOW, OK], cfg) == OK
    assert next_state(OK, [SLOW, SLOW, SLOW, SLOW], cfg) == SLOW


if __name__ == "__main__":
    test_one_bad_sample_stays_ok()
    test_three_bad_moves_state()
    test_recovery_needs_three_straight_ok()
    test_alternating_never_leaves_ok()
    test_latency_limit_respects_absolute_floor()
    test_quorum_one_of_eight_stays_ok()
    test_quorum_fail_and_degraded_thresholds()
    test_presets_differ()
    test_custom_window_is_honored()
    print("ok")


def test_one_slow_result_does_not_restart_recovery():
    # Four good results out of five, none failed, the newest good: recovered, as a healthy check would read.
    assert next_state(DOWN, [OK, OK, OK, SLOW, OK], RELAXED) == OK
    assert next_state(SLOW, [OK, OK, OK, SLOW, OK], RELAXED) == OK
    # Still failing somewhere in the window, or the newest result is not good: not yet.
    assert next_state(DOWN, [OK, OK, OK, DOWN, OK], RELAXED) == DOWN
    assert next_state(DOWN, [SLOW, OK, OK, OK, OK], RELAXED) == DOWN
    assert next_state(DOWN, [SLOW, OK, OK, OK, DOWN], RELAXED) == DOWN
    # Too few good results for the preset.
    assert next_state(DOWN, [OK, SLOW, OK, SLOW, OK], RELAXED) == DOWN
    assert next_state(SLOW, [OK, SLOW, OK, SLOW, OK], RELAXED) == SLOW
