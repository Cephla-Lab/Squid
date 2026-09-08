import pytest

import control._def

import control.gui_hcs
from qtpy.QtWidgets import QMessageBox

import control.microscope
import control.startup_progress_window
from control.startup_progress import (
    StartupCoreDeviceError,
    StartupReporter,
    StepState,
    declare_expected_steps,
)


@pytest.fixture(autouse=True)
def no_stray_startup_dialog(monkeypatch):
    """A startup summary dialog reaching exec_() in a test would hang CI forever."""

    def fail(self, *args, **kwargs):
        raise RuntimeError(f"Unexpected startup summary dialog: {self.details_text()}")

    monkeypatch.setattr(control.startup_progress_window.StartupSummaryDialog, "exec_", fail)


@pytest.fixture
def confirm_exit_yes(monkeypatch):
    """Auto-accept the 'Confirm Exit' dialog GUI shutdown shows, or teardown hangs forever."""

    def confirm_exit(parent, title, text, *args, **kwargs):
        if title == "Confirm Exit":
            return QMessageBox.Yes
        raise RuntimeError(f"Unexpected QMessageBox: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "question", confirm_exit)


def test_create_simulated_hcs_with_or_without_piezo(qtbot, confirm_exit_yes):
    # This just tests to make sure we can successfully create a simulated hcs gui with or without
    # the piezo objective.
    control._def.HAS_OBJECTIVE_PIEZO = True
    scope_with = control.microscope.Microscope.build_from_global_config(True)
    with_piezo = control.gui_hcs.HighContentScreeningGui(microscope=scope_with, is_simulation=True)
    qtbot.add_widget(with_piezo)

    control._def.HAS_OBJECTIVE_PIEZO = False
    scope_without = control.microscope.Microscope.build_from_global_config(True)
    without_piezo = control.gui_hcs.HighContentScreeningGui(microscope=scope_without, is_simulation=True)
    qtbot.add_widget(without_piezo)


def test_tab_change_to_simple_recording_does_not_raise(qtbot, monkeypatch, confirm_exit_yes):
    """Regression: onTabChanged used to call emit_selected_channels() on every record
    tab and toggleAcquisitionStart called display_progress_bar() on the current tab,
    but RecordingWidget (Simple Recording) has neither method, so selecting the tab
    raised AttributeError on machines with ENABLE_RECORDING on."""

    # gui_hcs star-imports _def, so patch its module-level binding before construction
    # to get the "Simple Recording" tab added.
    monkeypatch.setattr(control.gui_hcs, "ENABLE_RECORDING", True)

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    recording_index = win.recordTabWidget.indexOf(win.recordingControlWidget)
    assert recording_index >= 0, "Simple Recording tab was not added despite ENABLE_RECORDING"

    # Selecting the tab fires currentChanged -> onTabChanged; must not raise.
    win.recordTabWidget.setCurrentIndex(recording_index)
    win.onTabChanged(recording_index)

    # The same widget must also survive an acquisition start/stop notification
    # (workflow/TCP acquisitions can start while a non-multipoint tab is current).
    win.toggleAcquisitionStart(True)
    win.toggleAcquisitionStart(False)


def test_acquisition_start_emits_selected_channels(qtbot, confirm_exit_yes):
    """The napari multichannel viewer is initialized from signal_acquisition_channels.
    That signal must be emitted when the acquisition starts (alongside
    signal_acquisition_shape), for both the button path and the TCP path."""

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    widget = win.wellplateMultiPointWidget
    emitted = []
    widget.signal_acquisition_channels.connect(emitted.append)

    # _set_ui_acquisition_running is the shared start path (button + TCP/invokeMethod).
    widget._set_ui_acquisition_running(nz=1, delta_z_um=1.0)
    try:
        assert emitted, "signal_acquisition_channels was not emitted at acquisition start"
        assert emitted[-1] == widget.channel_sequence.ordered_selected_names()
    finally:
        # Unwind the acquisition-running UI state (signal_acquisition_started=True
        # disabled the other tabs via toggleAcquisitionStart).
        win.toggleAcquisitionStart(False)


def test_image_display_signals_connected_once(qtbot, monkeypatch, confirm_exit_yes):
    """Regression: make_connections and makeNapariConnections both used to wire the
    non-Napari image-display signals, causing slots to fire twice per click/scroll."""

    # Patch slots at the class level *before* construction so signal-slot bindings
    # made inside __init__ resolve to these counters.
    z_calls = []
    click_calls = []
    monkeypatch.setattr(
        control.gui_hcs.HighContentScreeningGui, "move_z_from_scroll", lambda self, delta_um: z_calls.append(delta_um)
    )
    monkeypatch.setattr(
        control.gui_hcs.HighContentScreeningGui,
        "move_from_click_image",
        lambda self, *args, **kwargs: click_calls.append(args),
    )

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    win.imageDisplayWindow.signal_z_um_delta.emit(1.0)
    win.imageDisplayWindow.image_click_coordinates.emit(0.0, 0.0, 0, 0)

    assert len(z_calls) == 1, f"signal_z_um_delta wired {len(z_calls)} times, expected 1"
    assert len(click_calls) == 1, f"image_click_coordinates wired {len(click_calls)} times, expected 1"


def test_cleanup_closes_stage_before_microcontroller(qtbot, monkeypatch, confirm_exit_yes):
    """The stage may own its own transport (e.g. the PI C-414 serial handle), so cleanup
    must call stage.close() — before the microcontroller, mirroring Microscope.close()."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)

    calls = []
    # Shadow the inherited no-op close() so the test can observe the call.
    gui.stage.close = lambda: calls.append("stage")
    original_micro_close = gui.microcontroller.close

    def recording_micro_close():
        calls.append("microcontroller")
        original_micro_close()

    monkeypatch.setattr(gui.microcontroller, "close", recording_micro_close)

    # Keep teardown's closeEvent from re-running cleanup against closed devices. Instance
    # attr, not monkeypatch: it must outlive monkeypatch teardown, which runs before qtbot
    # closes widgets.
    gui.closeEvent = lambda event: event.accept()

    gui._cleanup_common(for_restart=True)

    assert calls == ["stage", "microcontroller"]


def test_startup_reporter_records_every_step(qtbot, confirm_exit_yes):
    """End-to-end: a healthy simulated startup leaves no row unfinished."""
    reporter = StartupReporter()
    scope = control.microscope.Microscope.build_from_global_config(True, reporter=reporter)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True, reporter=reporter)
    qtbot.add_widget(win)

    assert reporter.failures == []
    unfinished = [r.key for r in reporter.results if not r.finished]
    assert unfinished == [], f"steps left hanging: {unfinished}"
    assert reporter.get("camera").state is StepState.READY
    assert reporter.get("gui_widgets").state is StepState.READY


def test_independent_failure_does_not_stop_the_sweep(qtbot, monkeypatch, confirm_exit_yes):
    """The whole point of the feature: report every problem in one pass.

    A dead spinning disk must not prevent the cameras and the illumination
    controller from being probed, so the operator gets one list instead of
    discovering the next failure on the next launch.
    """

    def boom(*args, **kwargs):
        raise RuntimeError("spinning disk is powered off")

    # Force the peripheral on rather than inheriting it from whatever .ini the run picked up:
    # the committed configurations all ship enable_spinning_disk_confocal = False, so without
    # this the step never runs and the assertion below silently has nothing to find.
    monkeypatch.setattr(control._def, "ENABLE_SPINNING_DISK_CONFOCAL", True)
    monkeypatch.setattr(control._def, "USE_DRAGONFLY", False)
    monkeypatch.setattr(control.microscope.serial_peripherals, "XLight_Simulation", boom)

    reporter = StartupReporter()
    scope = control.microscope.Microscope.build_from_global_config(True, reporter=reporter)
    try:
        assert [f.key for f in reporter.failures] == ["spinning_disk"]
        # Everything after the failure still ran.
        assert reporter.get("camera").state is StepState.READY
        assert reporter.get("illumination_controller").state is StepState.READY
        assert reporter.get("prep_camera").state is StepState.READY
    finally:
        scope.close()


def test_core_failure_stops_the_sweep(qtbot, monkeypatch, confirm_exit_yes):
    """A device nothing can work without stops immediately, still legibly."""

    def boom(*args, **kwargs):
        raise RuntimeError("camera not found")

    monkeypatch.setattr(control.microscope.squid.camera.utils, "get_camera", boom)

    reporter = StartupReporter()
    # Declare up front, as main_hcs does, so rows for steps that never run still
    # exist and can be seen sitting at Pending.
    declare_expected_steps(reporter, simulated=True, skip_init=False)
    with pytest.raises(StartupCoreDeviceError) as excinfo:
        control.microscope.Microscope.build_from_global_config(True, reporter=reporter)

    assert excinfo.value.step_key == "camera"
    # Steps after the core failure never ran, and say so rather than claiming success.
    assert reporter.get("illumination_controller").state is StepState.PENDING


def test_devices_are_registered_for_teardown(qtbot, confirm_exit_yes):
    """An abort mid-build has no Microscope to close, so the reporter's registry
    is the only handle on what was opened."""
    reporter = StartupReporter()
    scope = control.microscope.Microscope.build_from_global_config(True, reporter=reporter)
    try:
        labels = [label for label, _ in reporter.opened]
        assert "microcontroller" in labels
        assert "main camera" in labels
        # Construction order, so reversing it tears down in the required order.
        assert labels.index("microcontroller") < labels.index("main camera")
    finally:
        scope.close()
