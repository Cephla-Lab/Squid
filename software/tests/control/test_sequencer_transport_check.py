"""The soak verdict of tools/sequencer_transport_check.py: what spread of run times is healthy."""

from tools.sequencer_transport_check import soak_verdict


def runs(n, typical=385.0):
    return [typical + (i % 9) for i in range(n)]  # the ~10 ms spread the status-packet cadence gives


def test_a_tight_spread_is_healthy():
    assert soak_verdict(runs(200)) == ""


def test_one_slow_run_in_a_long_soak_is_the_host_not_the_controller():
    # Bench 2026-09-20, Windows, 24,000 runs: 379 / 385 / 597 ms. One ~200 ms hiccup in three hours
    # is OS scheduling or USB; failing the whole overnight soak on it would be noise.
    assert soak_verdict(runs(24_000) + [597.4]) == ""


def test_a_run_that_hangs_for_a_second_fails_even_once():
    problem = soak_verdict(runs(24_000) + [1600.0])
    assert "1600" in problem and "stalled" in problem


def test_frequent_slow_runs_fail():
    # 1 % of the runs 150 ms late is a pattern, not a hiccup
    problem = soak_verdict(runs(10_000) + [540.0] * 100)
    assert "100 of 10100" in problem


def test_a_short_soak_tolerates_a_single_slow_run():
    assert soak_verdict(runs(50) + [520.0]) == ""
