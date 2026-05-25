from tools.pr540_repro_scenarios import ScenarioResult, FakeMicrocontroller


def test_scenario_result_defaults():
    r = ScenarioResult(name="A", verdict="PASS", summary="ok")
    assert r.name == "A"
    assert r.verdict == "PASS"
    assert r.summary == "ok"
    assert r.iterations == 0
    assert r.fast_fail_count == 0
    assert r.suspect_fast_ack_count == 0
    assert r.normal_count == 0
    assert r.elapsed_seconds == 0.0
    assert r.details == []


def test_fake_microcontroller_records_calls():
    fake = FakeMicrocontroller(firmware_version=(1, 2))
    fake.move_w_usteps(100)
    fake.wait_till_operation_is_completed()
    assert fake.calls == [("move_w_usteps", 100), ("wait",)]
    assert fake.firmware_version == (1, 2)


from tools.pr540_repro_scenarios import scenario_a_pre_init_move
from control.microcontroller import CommandAborted


def _captured_log():
    lines = []
    return lines, lambda s: lines.append(s)


def test_scenario_a_post_fix_path_raises_command_aborted():
    fake = FakeMicrocontroller(firmware_version=(1, 2))
    # Both MOVE_W and MOVE_W2 raise CommandAborted with CMD_EXECUTION_ERROR reason.
    fake.queue_wait(raises=CommandAborted(command_id=1, reason="firmware reported CMD_EXECUTION_ERROR"), duration=0.02)
    fake.queue_wait(raises=CommandAborted(command_id=2, reason="firmware reported CMD_EXECUTION_ERROR"), duration=0.02)
    lines, log = _captured_log()
    result = scenario_a_pre_init_move(fake, log)
    assert result.verdict == "PASS"
    assert result.fast_fail_count == 2
    assert ("move_w_usteps", 100) in fake.calls
    assert ("move_w2_usteps", 100) in fake.calls


def test_scenario_a_pre_fix_silent_complete_is_observed_bug():
    fake = FakeMicrocontroller(firmware_version=(1, 1))
    # Pre-fix firmware: wait returns silently (no exception) within ms.
    fake.queue_wait(raises=None, duration=0.005)
    fake.queue_wait(raises=None, duration=0.005)
    lines, log = _captured_log()
    result = scenario_a_pre_init_move(fake, log)
    assert result.verdict == "OBSERVED-BUG"
    assert result.suspect_fast_ack_count == 2


def test_scenario_a_unexpected_timeout_is_fail():
    fake = FakeMicrocontroller(firmware_version=(1, 2))
    fake.queue_wait(raises=TimeoutError("ack timeout"), duration=0.5)
    fake.queue_wait(raises=None, duration=0.005)
    lines, log = _captured_log()
    result = scenario_a_pre_init_move(fake, log)
    assert result.verdict == "FAIL"


from tools.pr540_repro_scenarios import (
    measure_baseline,
    scenario_b_rapid_burst,
    scenario_c_soak,
)


def test_measure_baseline_returns_elapsed_seconds():
    fake = FakeMicrocontroller(firmware_version=(1, 2))
    fake.queue_wait(duration=0.0)  # init wait
    fake.queue_wait(duration=0.12)  # forward move wait — this is the measurement
    fake.queue_wait(duration=0.0)  # backward move wait
    lines, log = _captured_log()
    t = measure_baseline(fake, usteps_per_slot=1600, log_cb=log)
    assert 0.10 < t < 0.15


def test_scenario_b_post_fix_with_fast_fail_is_pass():
    fake = FakeMicrocontroller(firmware_version=(1, 2))
    # Burst size 3, iterations 2; only the awaited (final) command of each burst calls wait.
    fake.queue_wait(raises=CommandAborted(command_id=10, reason="firmware reported CMD_EXECUTION_ERROR"), duration=0.01)
    fake.queue_wait(raises=None, duration=0.15)
    lines, log = _captured_log()
    r = scenario_b_rapid_burst(
        fake, log, burst_size=3, iterations=2, usteps_per_slot=1600, t_baseline=0.15, threshold=0.5
    )
    assert r.verdict == "PASS"
    assert r.fast_fail_count == 1
    assert r.normal_count == 1


def test_scenario_b_pre_fix_silent_fast_ack_is_observed_bug():
    fake = FakeMicrocontroller(firmware_version=(1, 1))
    # Both iterations: ack returns silently in 30ms — well below 0.5 * 150ms = 75ms threshold.
    fake.queue_wait(raises=None, duration=0.03)
    fake.queue_wait(raises=None, duration=0.03)
    lines, log = _captured_log()
    r = scenario_b_rapid_burst(
        fake, log, burst_size=3, iterations=2, usteps_per_slot=1600, t_baseline=0.15, threshold=0.5
    )
    assert r.verdict == "OBSERVED-BUG"
    assert r.suspect_fast_ack_count == 2


def test_scenario_c_soak_no_anomalies_is_pass():
    fake = FakeMicrocontroller(firmware_version=(1, 2))
    for _ in range(5):
        fake.queue_wait(raises=None, duration=0.15)
    lines, log = _captured_log()
    r = scenario_c_soak(fake, log, iterations=5, usteps_per_slot=1600, t_baseline=0.15, threshold=0.5)
    assert r.verdict == "PASS"
    assert r.normal_count == 5


def test_scenario_c_soak_intermittent_fast_ack_is_observed_bug():
    fake = FakeMicrocontroller(firmware_version=(1, 1))
    # 4 normal, 1 anomalous fast-ack.
    durations = [0.15, 0.15, 0.02, 0.15, 0.15]
    for d in durations:
        fake.queue_wait(raises=None, duration=d)
    lines, log = _captured_log()
    r = scenario_c_soak(fake, log, iterations=5, usteps_per_slot=1600, t_baseline=0.15, threshold=0.5)
    assert r.verdict == "OBSERVED-BUG"
    assert r.suspect_fast_ack_count == 1
    assert r.normal_count == 4
