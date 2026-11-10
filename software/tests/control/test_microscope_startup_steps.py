"""Microscope.build_from_global_config reports each device through the startup reporter (simulation)."""

import pytest

import control._def
import control.microscope
import control.serial_peripherals as serial_peripherals
import squid.camera.utils
from control.startup_progress import (
    NULL_REPORTER,
    StartupAborted,
    StartupCoreDeviceError,
    StartupReporter,
    StepState,
    declare_expected_steps,
)

# Rows that main_hcs.py and the GUI report, not the Microscope build.
NOT_MICROSCOPE_STEPS = {
    "imports",
    "gui_objects",
    "gui_hardware",
    "gui_widgets",
    "gui_layout",
    "gui_connections",
    "gui_position",
}


class RecordingReporter(StartupReporter):
    def __init__(self):
        super().__init__()
        self.visited = []  # step keys in the order they started

    def _on_changed(self, result):
        if result.state == StepState.CONNECTING and result.key not in self.visited:
            self.visited.append(result.key)


def test_simulated_build_visits_exactly_the_declared_steps_in_order():
    reporter = RecordingReporter()
    declare_expected_steps(reporter, simulated=True)
    declared = [r.key for r in reporter.results if r.key not in NOT_MICROSCOPE_STEPS]

    scope = control.microscope.Microscope.build_from_global_config(True, reporter=reporter)
    try:
        # Every row the window showed up front was visited, nothing ran that it did not show, same order.
        assert reporter.visited == declared
        for key in ("microcontroller", "stage_xy", "camera", "illumination_controller", "config_profiles"):
            assert key in reporter.visited
        assert {"prep_watchdog", "prep_camera", "prep_home_xyz"} <= set(reporter.visited)
        assert reporter.failures == []
        for key in declared:
            assert reporter.get(key).state in (StepState.READY, StepState.WARNING), key
        # What an abort would have to release, in construction order.
        opened = [label for label, _ in reporter.opened]
        assert opened[:3] == ["microcontroller serial port", "microcontroller", "XY stage"]
        assert "main camera" in opened
    finally:
        scope.close()


def test_skip_init_build_visits_exactly_the_declared_steps():
    reporter = RecordingReporter()
    declare_expected_steps(reporter, simulated=True, skip_init=True)
    declared = [r.key for r in reporter.results if r.key not in NOT_MICROSCOPE_STEPS]

    scope = control.microscope.Microscope.build_from_global_config(True, skip_init=True, reporter=reporter)
    try:
        assert reporter.visited == declared
        assert "prep_home_xyz" not in reporter.visited
    finally:
        scope.close()


def test_build_without_a_reporter_leaves_the_shared_null_reporter_empty():
    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        assert NULL_REPORTER.results == []
        assert NULL_REPORTER.opened == []
    finally:
        scope.close()


@pytest.fixture
def failing_cellx(monkeypatch):
    """A machine configured for a CellX whose (simulated) driver cannot come up."""

    def boom(*args, **kwargs):
        raise serial_peripherals.SerialPortNotFoundError("CellX laser combiner", SN="X1")

    monkeypatch.setattr(control._def, "ENABLE_CELLX", True)
    monkeypatch.setattr(serial_peripherals, "CellX_Simulation", boom)


def test_an_independent_device_failure_is_collected_and_the_sweep_continues(failing_cellx):
    reporter = RecordingReporter()
    scope = control.microscope.Microscope.build_from_global_config(True, reporter=reporter)
    try:
        assert [r.key for r in reporter.failures] == ["cellx"]
        assert "no serial port found" in reporter.get("cellx").detail
        assert scope.addons.cellx is None
        # The rest of the checklist still ran, so one summary can list everything that is off.
        assert reporter.get("camera").state == StepState.READY
        assert reporter.get("prep_camera").state == StepState.READY
    finally:
        scope.close()


def test_without_a_reporter_the_same_failure_still_raises(failing_cellx):
    with pytest.raises(serial_peripherals.SerialPortNotFoundError):
        control.microscope.Microscope.build_from_global_config(True)


def test_a_core_failure_stops_the_build_and_what_was_opened_can_be_released(monkeypatch):
    def no_camera(*args, **kwargs):
        raise RuntimeError("camera not found")

    monkeypatch.setattr(squid.camera.utils, "get_camera", no_camera)
    reporter = RecordingReporter()

    with pytest.raises(StartupCoreDeviceError) as excinfo:
        control.microscope.Microscope.build_from_global_config(True, reporter=reporter)

    assert excinfo.value.step_key == "camera"
    assert "illumination_controller" not in reporter.visited
    opened = [label for label, _ in reporter.opened]
    assert "microcontroller" in opened and "main camera" not in opened
    assert reporter.close_all() == []  # the microcontroller and its port close cleanly
    assert reporter.opened == []


def test_abort_between_steps_stops_the_build(monkeypatch):
    class AbortAfterStage(RecordingReporter):
        def _on_changed(self, result):
            super()._on_changed(result)
            if result.key == "stage_xy" and result.state == StepState.READY:
                self.request_abort()

    reporter = AbortAfterStage()
    with pytest.raises(StartupAborted):
        control.microscope.Microscope.build_from_global_config(True, reporter=reporter)

    assert "camera" not in reporter.visited
    assert reporter.close_all() == []


def test_filter_wheel_homing_failure_is_a_warning_row(monkeypatch):
    class Wheel:
        def initialize(self, indices):
            pass

        def home(self, index=None):
            raise RuntimeError("wheel stalled")

    addons = control.microscope.MicroscopeAddons(emission_filter_wheel=Wheel())
    monkeypatch.setattr(control.microscope.squid.config, "get_filter_wheel_config", lambda: _FwConfig())
    reporter = RecordingReporter()

    addons.prepare_for_use(reporter=reporter)

    row = reporter.get("prep_filter_wheel")
    assert row.state == StepState.WARNING
    assert "re-home" in row.detail
    assert reporter.failures == []


class _FwConfig:
    indices = [1]
