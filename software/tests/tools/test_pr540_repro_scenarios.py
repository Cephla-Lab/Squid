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
