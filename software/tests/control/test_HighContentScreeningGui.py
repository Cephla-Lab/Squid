import unittest.mock

import pytest

import control._def

import control.gui_hcs
from qtpy.QtWidgets import QMessageBox

import control.microscope
import squid.abc
import squid.stage.utils


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


def test_create_simulated_hcs_with_dragonfly(qtbot, monkeypatch, confirm_exit_yes):
    """Regression: with a Dragonfly as the spinning disk unit, GUI construction wired the
    X-Light-only iris signals onto DragonflyConfocalWidget and raised AttributeError."""
    import control.widgets
    from tests.control.spinning_disk_test_utils import enable_spinning_disk

    enable_spinning_disk(monkeypatch, control.gui_hcs, dragonfly=True)

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    assert isinstance(win.spinningDiskConfocalWidget, control.widgets.DragonflyConfocalWidget)
    assert win.cameraTabWidget.indexOf(win.spinningDiskConfocalWidget) >= 0

    # The widget's toggle drives the live controller's confocal mode, as the X-Light one does.
    assert scope.live_controller.is_confocal_mode() is False
    win.spinningDiskConfocalWidget.btn_toggle_confocal.click()
    assert scope.addons.dragonfly.get_modality() == "CONFOCAL"
    assert scope.live_controller.is_confocal_mode() is True


def test_gui_cleanup_closes_the_dragonfly(qtbot, monkeypatch, confirm_exit_yes):
    """closeEvent/restart go through _cleanup_common, which closes devices itself and never
    calls Microscope.close(); the Dragonfly serial port must be released on that path too."""
    import control.serial_peripherals
    from tests.control.spinning_disk_test_utils import enable_spinning_disk

    enable_spinning_disk(monkeypatch, control.gui_hcs, dragonfly=True)

    scope = control.microscope.Microscope.build_from_global_config(True)
    closed = []
    monkeypatch.setattr(scope.addons.dragonfly, "close", lambda: closed.append("dragonfly"))
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)
    gui.closeEvent = lambda event: event.accept()  # keep teardown from re-running cleanup

    gui._cleanup_common(for_restart=True)

    assert closed == ["dragonfly"]


def _stub_stage_moves(monkeypatch, stage):
    """Replace the stage's absolute moves with mocks, so a test can assert what startup commanded."""
    mocks = {axis: unittest.mock.MagicMock(name=f"move_{axis}_to") for axis in ("x", "y", "z")}
    for axis, mock in mocks.items():
        monkeypatch.setattr(stage, f"move_{axis}_to", mock)
    return mocks


def test_startup_restores_cached_xy_but_not_z_when_z_homing_is_disabled(qtbot, monkeypatch, confirm_exit_yes):
    """An XY-only Cephla stage (e.g. the Aries/Dragonfly config) runs with homing_enabled_z = False.
    Its cached X/Y must still be restored at startup. Z is never homed there, so the cached Z has
    no absolute meaning and must not be commanded."""
    # Microscope.home_xyz reads the flag through control._def; gui_hcs star-imports the flags, so
    # its module-level bindings are patched separately before construction.
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", False)
    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", False)
    monkeypatch.setattr(control.gui_hcs, "HOMING_ENABLED_X", True)
    monkeypatch.setattr(control.gui_hcs, "HOMING_ENABLED_Y", True)
    monkeypatch.setattr(control.gui_hcs, "HOMING_ENABLED_Z", False)
    cached = squid.abc.Pos(x_mm=23.0, y_mm=31.0, z_mm=1.5, theta_rad=None)
    monkeypatch.setattr(squid.stage.utils, "get_cached_position", lambda *args, **kwargs: cached)

    scope = control.microscope.Microscope.build_from_global_config(True)
    moves = _stub_stage_moves(monkeypatch, scope.stage)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)

    moves["x"].assert_any_call(cached.x_mm)
    moves["y"].assert_any_call(cached.y_mm)
    moves["z"].assert_not_called()


def test_startup_restores_cached_z_when_z_homing_is_enabled(qtbot, monkeypatch, confirm_exit_yes):
    """The other half of the startup gate: with Z homed, a cached Z above Z_HOME_SAFETY_POINT is
    restored as before."""
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", False)
    monkeypatch.setattr(control.gui_hcs, "HOMING_ENABLED_X", True)
    monkeypatch.setattr(control.gui_hcs, "HOMING_ENABLED_Y", True)
    monkeypatch.setattr(control.gui_hcs, "HOMING_ENABLED_Z", True)
    monkeypatch.setattr(control.gui_hcs, "USE_PI_FOCUS_STAGE", False)
    safety_z_mm = int(control.gui_hcs.Z_HOME_SAFETY_POINT) / 1000.0
    cached = squid.abc.Pos(x_mm=23.0, y_mm=31.0, z_mm=safety_z_mm + 0.5, theta_rad=None)
    monkeypatch.setattr(squid.stage.utils, "get_cached_position", lambda *args, **kwargs: cached)

    scope = control.microscope.Microscope.build_from_global_config(True)
    moves = _stub_stage_moves(monkeypatch, scope.stage)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)

    moves["z"].assert_any_call(cached.z_mm)


def _configure_pi_focus_stage_without_cephla_z_homing(monkeypatch):
    """A Cephla XY stage with a PI V-308 as its Z: there is no Cephla Z to home, so homing_enabled_z
    is off, yet Z is real and Microscope.home_xyz references it at every start."""
    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", True)
    monkeypatch.setattr(control._def, "SIMULATE_PI_FOCUS_STAGE", True)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", False)
    monkeypatch.setattr(control.gui_hcs, "USE_PI_FOCUS_STAGE", True)
    monkeypatch.setattr(control.gui_hcs, "HOMING_ENABLED_X", True)
    monkeypatch.setattr(control.gui_hcs, "HOMING_ENABLED_Y", True)
    monkeypatch.setattr(control.gui_hcs, "HOMING_ENABLED_Z", False)


def test_startup_restores_cached_z_on_a_pi_focus_stage_without_cephla_z_homing(qtbot, monkeypatch, confirm_exit_yes):
    _configure_pi_focus_stage_without_cephla_z_homing(monkeypatch)
    cached = squid.abc.Pos(x_mm=23.0, y_mm=31.0, z_mm=0.3, theta_rad=None)
    monkeypatch.setattr(squid.stage.utils, "get_cached_position", lambda *args, **kwargs: cached)

    scope = control.microscope.Microscope.build_from_global_config(True)
    moves = _stub_stage_moves(monkeypatch, scope.stage)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)

    moves["z"].assert_any_call(cached.z_mm)


def test_cleanup_validates_z_on_a_pi_focus_stage_without_cephla_z_homing(qtbot, monkeypatch, confirm_exit_yes):
    _configure_pi_focus_stage_without_cephla_z_homing(monkeypatch)
    scope = control.microscope.Microscope.build_from_global_config(True)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)
    gui.closeEvent = lambda event: event.accept()  # keep teardown from re-running cleanup

    cache_calls = []
    monkeypatch.setattr(squid.stage.utils, "cache_position", lambda **kwargs: cache_calls.append(kwargs))
    gui._cleanup_common(for_restart=True)

    assert [call["validate_z"] for call in cache_calls] == [True]
