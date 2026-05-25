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
