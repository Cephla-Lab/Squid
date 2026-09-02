"""Tests for the transport-neutral half of startup progress reporting."""

import time

import pytest

from control.startup_progress import (
    NULL_REPORTER,
    StartupAborted,
    StartupCoreDeviceError,
    StartupFailed,
    StartupReporter,
    StepState,
    declare_expected_steps,
    state_color,
    state_label,
    wait_until,
)


@pytest.fixture
def reporter():
    return StartupReporter(device_timeout_s=90.0)


# ── step() outcomes ────────────────────────────────────────────────────────


def test_successful_step_becomes_ready(reporter):
    with reporter.step("a", label="Device A"):
        pass
    assert reporter.get("a").state is StepState.READY
    assert reporter.failures == []


def test_independent_failure_is_recorded_and_swallowed(reporter):
    """The whole point: one dead device must not stop the sweep."""
    with reporter.step("a", label="Device A"):
        raise RuntimeError("boom")
    # Execution continues here - no exception escaped.
    with reporter.step("b", label="Device B"):
        pass

    assert reporter.get("a").state is StepState.FAILED
    assert "RuntimeError: boom" in reporter.get("a").detail
    assert reporter.get("b").state is StepState.READY
    assert [f.key for f in reporter.failures] == ["a"]


def test_core_failure_stops_the_sweep(reporter):
    with pytest.raises(StartupCoreDeviceError) as excinfo:
        with reporter.step("mcu", label="Microcontroller", core=True):
            raise RuntimeError("no such port")

    assert excinfo.value.step_key == "mcu"
    assert isinstance(excinfo.value.cause, RuntimeError)
    assert reporter.get("mcu").state is StepState.FAILED


def test_step_keeps_a_terminal_state_the_body_already_set(reporter):
    """A driver that classified the failure itself must not be overwritten."""
    with reporter.step("a", label="Device A") as progress:
        progress.not_found("no serial port found for SN='X'")
        raise RuntimeError("stand-in for SerialPortNotFoundError")

    result = reporter.get("a")
    assert result.state is StepState.NOT_FOUND
    assert "no serial port" in result.detail


def test_abort_passes_through_unswallowed(reporter):
    reporter.request_abort()
    with pytest.raises(StartupAborted) as excinfo:
        with reporter.step("a", label="Device A"):
            pytest.fail("body must not run once abort was requested")

    assert excinfo.value.step_key == "a"
    assert "Device A" in str(excinfo.value)


def test_abort_mid_step_is_not_recorded_as_a_device_failure(reporter):
    with pytest.raises(StartupAborted):
        with reporter.step("a", label="Device A"):
            reporter.request_abort()
            reporter.raise_if_aborted("a")

    # Aborting is a user action, not a broken device.
    assert reporter.failures == []


def test_warmup_detail_is_cleared_once_ready(reporter):
    with reporter.step("ldi", label="LDI") as progress:
        progress.on_warmup_retry("ERR=System in Warmup State", 12.0)
        assert reporter.get("ldi").state is StepState.WARMING_UP

    result = reporter.get("ldi")
    assert result.state is StepState.READY
    assert result.detail == "", "a Ready row must not still show warm-up chatter"


def test_informational_detail_survives_a_successful_step(reporter):
    with reporter.step("engine", label="Laser engine") as progress:
        progress.detail("started; warms up in the background")
    assert reporter.get("engine").detail == "started; warms up in the background"


def test_warning_is_not_a_failure(reporter):
    """The paths that already swallow errors and boot anyway must keep booting."""
    with reporter.step("fw", label="Filter wheel") as progress:
        progress.warning("homing failed - position unknown")

    assert reporter.get("fw").state is StepState.WARNING
    assert reporter.failures == []


def test_elapsed_is_recorded(reporter, monkeypatch):
    # A settable clock rather than a fixed sequence, so the test does not depend
    # on how many times step() happens to read the clock.
    clock = {"t": 100.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
    with reporter.step("a", label="Device A"):
        clock["t"] = 103.5
    assert reporter.get("a").elapsed_s == pytest.approx(3.5)


# ── declaration and ordering ───────────────────────────────────────────────


def test_declared_order_is_preserved(reporter):
    for key in ("c", "a", "b"):
        reporter.declare(key, key.upper())
    assert [r.key for r in reporter.results] == ["c", "a", "b"]


def test_step_on_an_undeclared_key_appends_a_row(reporter):
    reporter.declare("a", "A")
    with reporter.step("late", label="Late arrival"):
        pass
    assert [r.key for r in reporter.results] == ["a", "late"]


def test_declare_expected_steps_matches_the_machine_config():
    reporter = StartupReporter()
    declare_expected_steps(reporter, simulated=False, skip_init=False)
    keys = [r.key for r in reporter.results]

    # Always present regardless of configuration.
    for expected in ("imports", "microcontroller", "stage_xy", "camera", "illumination_controller"):
        assert expected in keys
    # Ordering matters: the checklist should read in the order things happen.
    assert keys.index("microcontroller") < keys.index("camera")
    assert keys.index("camera") < keys.index("gui_widgets")
    assert all(r.state is StepState.PENDING for r in reporter.results)


def test_skip_init_drops_the_homing_row():
    with_homing = StartupReporter()
    declare_expected_steps(with_homing, simulated=False, skip_init=False)
    without = StartupReporter()
    declare_expected_steps(without, simulated=False, skip_init=True)

    assert "prep_home_xyz" in [r.key for r in with_homing.results]
    assert "prep_home_xyz" not in [r.key for r in without.results]


def test_simulation_declares_no_external_light_source():
    """Nothing may sit at Pending forever: simulation never builds the LDI."""
    reporter = StartupReporter()
    declare_expected_steps(reporter, simulated=True, skip_init=False)
    assert "light_source" not in [r.key for r in reporter.results]


def test_simulation_marks_everything_interruptible():
    reporter = StartupReporter()
    declare_expected_steps(reporter, simulated=True, skip_init=False)
    assert all(r.interruptible for r in reporter.results)


# ── the opened-device registry ─────────────────────────────────────────────


def test_close_all_runs_in_reverse_registration_order(reporter):
    closed = []
    for name in ("microcontroller", "stage", "camera"):
        reporter.register_opened(name, lambda n=name: closed.append(n))

    assert reporter.close_all() == []
    # Reverse of construction order, which is what the teardown ordering in
    # tests/conftest.py requires.
    assert closed == ["camera", "stage", "microcontroller"]


def test_close_all_continues_past_a_failing_closer(reporter):
    closed = []
    reporter.register_opened("microcontroller", lambda: closed.append("microcontroller"))
    reporter.register_opened("wedged device", lambda: (_ for _ in ()).throw(RuntimeError("port stuck")))
    reporter.register_opened("camera", lambda: closed.append("camera"))

    errors = reporter.close_all()

    assert closed == ["camera", "microcontroller"], "one bad closer must not strand the rest"
    assert len(errors) == 1
    assert "wedged device" in errors[0]


def test_close_all_is_idempotent(reporter):
    calls = []
    reporter.register_opened("camera", lambda: calls.append(1))
    reporter.close_all()
    reporter.close_all()
    assert len(calls) == 1


# ── abort plumbing ─────────────────────────────────────────────────────────


def test_cancel_fn_tracks_the_abort_flag(reporter):
    cancel = reporter.cancel_fn()
    assert cancel() is False
    reporter.request_abort()
    assert cancel() is True


def test_raise_if_aborted_is_quiet_until_requested(reporter):
    reporter.declare("a", "A")
    reporter.raise_if_aborted("a")  # must not raise
    reporter.request_abort()
    with pytest.raises(StartupAborted):
        reporter.raise_if_aborted("a")


# ── wait_until ─────────────────────────────────────────────────────────────


def test_wait_until_returns_true_when_the_predicate_fires():
    calls = {"n": 0}

    def ready():
        calls["n"] += 1
        return calls["n"] >= 3

    assert wait_until(ready, timeout_s=10.0, sleep_fn=lambda s: None) is True


def test_wait_until_returns_false_at_the_deadline(monkeypatch):
    clock = {"t": 0.0}

    def fake_monotonic():
        clock["t"] += 1.0
        return clock["t"]

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    assert wait_until(lambda: False, timeout_s=3.0, sleep_fn=lambda s: None) is False


def test_wait_until_returns_false_on_cancel():
    assert wait_until(lambda: False, timeout_s=10.0, cancel_fn=lambda: True, sleep_fn=lambda s: None) is False


def test_wait_until_reports_progress_on_each_tick():
    ticks = []
    calls = {"n": 0}

    def ready():
        calls["n"] += 1
        return calls["n"] >= 4

    wait_until(ready, timeout_s=10.0, on_tick=ticks.append, sleep_fn=lambda s: None)
    assert len(ticks) == 3


# ── the no-op default ──────────────────────────────────────────────────────


def test_null_reporter_is_usable_and_inert():
    """Existing positional callers get this and must pay nothing for it."""
    with NULL_REPORTER.step("anything"):
        pass
    assert NULL_REPORTER.is_abort_requested() is False
    NULL_REPORTER.pump()
    NULL_REPORTER.sleep(0)


# ── presentation helpers ───────────────────────────────────────────────────


@pytest.mark.parametrize("state", list(StepState))
def test_every_state_has_a_label_and_a_colour(state):
    assert state_label(state) and not state_label(state).startswith("StepState")
    assert state_color(state).startswith("#")


def test_startup_failed_summarises_only_the_failures():
    reporter = StartupReporter()
    with reporter.step("a", label="Device A"):
        raise RuntimeError("boom")
    with reporter.step("b", label="Device B"):
        pass

    error = StartupFailed(reporter.results)
    assert [r.key for r in error.failures] == ["a"]
    assert "Device A" in str(error)
    assert "Device B" not in str(error)
