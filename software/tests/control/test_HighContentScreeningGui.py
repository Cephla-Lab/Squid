import threading
import unittest.mock

import pytest

import control._def

import control.gui_hcs
from qtpy.QtCore import Qt
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


def _set_z_flags(monkeypatch, *, homing_z, pi_focus):
    """Microscope.home_xyz and squid.stage.utils read the flags through control._def; gui_hcs star-imports
    them, so its module-level bindings are patched too."""
    for module in (control._def, control.gui_hcs):
        monkeypatch.setattr(module, "HOMING_ENABLED_Z", homing_z)
        monkeypatch.setattr(module, "USE_PI_FOCUS_STAGE", pi_focus)
    monkeypatch.setattr(control.gui_hcs, "HOMING_ENABLED_X", True)
    monkeypatch.setattr(control.gui_hcs, "HOMING_ENABLED_Y", True)
    if pi_focus:
        monkeypatch.setattr(control._def, "SIMULATE_PI_FOCUS_STAGE", True)


def _build_gui_with_cached_position(qtbot, monkeypatch, cached):
    """Build the GUI as if `cached` were in cache/last_coords.txt, with the stage's absolute moves mocked
    so a test can assert what startup commanded."""
    monkeypatch.setattr(squid.stage.utils, "get_cached_position", lambda *args, **kwargs: cached)
    scope = control.microscope.Microscope.build_from_global_config(True)
    moves = {axis: unittest.mock.MagicMock(name=f"move_{axis}_to") for axis in ("x", "y", "z")}
    for axis, mock in moves.items():
        monkeypatch.setattr(scope.stage, f"move_{axis}_to", mock)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)
    return gui, moves


def test_startup_restores_cached_xy_but_not_z_when_z_is_not_referenced(qtbot, monkeypatch, confirm_exit_yes):
    """An XY-only Cephla stage (e.g. the Aries/Dragonfly config) runs with homing_enabled_z = False: its
    cached X/Y must still be restored, and its meaningless cached Z must not be commanded."""
    _set_z_flags(monkeypatch, homing_z=False, pi_focus=False)
    cached = squid.abc.Pos(x_mm=23.0, y_mm=31.0, z_mm=1.5, theta_rad=None)

    _, moves = _build_gui_with_cached_position(qtbot, monkeypatch, cached)

    moves["x"].assert_called_once_with(cached.x_mm)
    moves["y"].assert_called_once_with(cached.y_mm)
    moves["z"].assert_not_called()


def test_pi_focus_stage_is_a_referenced_z_even_without_cephla_z_homing(qtbot, monkeypatch, confirm_exit_yes):
    """A Cephla XY stage with a PI V-308 as its Z has no Cephla Z to home, so it runs with homing_enabled_z
    off; Z is still restored at startup and validated at shutdown."""
    _set_z_flags(monkeypatch, homing_z=False, pi_focus=True)
    cached = squid.abc.Pos(x_mm=23.0, y_mm=31.0, z_mm=0.3, theta_rad=None)
    gui, moves = _build_gui_with_cached_position(qtbot, monkeypatch, cached)
    moves["z"].assert_called_once_with(cached.z_mm)

    cache_position = unittest.mock.MagicMock()
    monkeypatch.setattr(squid.stage.utils, "cache_position", cache_position)
    gui.closeEvent = lambda event: event.accept()  # keep teardown from re-running cleanup
    gui._cleanup_common(for_restart=True)

    cache_position.assert_called_once()
    assert cache_position.call_args.kwargs["validate_z"] is True


class _GatedFocusServo:
    """A V-308 stand-in whose retract and restore block until the test opens their gates."""

    def __init__(self):
        self.retract_entered = threading.Event()
        self.retract_gate = threading.Event()
        self.restore_entered = threading.Event()
        self.restore_gate = threading.Event()

    def get_pos(self):
        return squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=2.0, theta_rad=None)

    def home(self, x, y, z, theta, blocking=True):
        self.retract_entered.set()
        assert self.retract_gate.wait(timeout=10)

    def set_servo(self, enabled):
        pass

    def move_z_to(self, abs_mm, blocking=True):
        self.restore_entered.set()
        assert self.restore_gate.wait(timeout=10)


def test_objective_swap_blocks_stage_focus_and_acquisition_controls(qtbot, monkeypatch):
    """While a manual V-308 swap is retracting or restoring Z off the GUI thread, autofocus,
    acquisition starts and manual stage moves must all be blocked, and restored afterwards."""

    def question(parent, title, text, *args, **kwargs):
        return QMessageBox.Ok if title == "Change Objective" else QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "question", question)

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    servo = _GatedFocusServo()
    win.objectivesWidget.focus_servo = servo
    blocked = [
        getattr(win, name) for name in win._OBJECTIVE_SWAP_BLOCKED_WIDGETS if getattr(win, name, None) is not None
    ]
    enabled_before = [w.isEnabled() for w in blocked]
    assert win.autofocusWidget.isEnabled() and win.recordTabWidget.isEnabled()
    start_pos = win.stage.get_pos()
    # What the GUI's position timer does at runtime; the objective-changed redraw needs a current location.
    win.navigationViewer.draw_fov_current_location(start_pos)

    def attempt_everything():
        assert not any(w.isEnabled() for w in blocked)
        # Disabled buttons ignore clicks: neither autofocus nor an acquisition can start.
        qtbot.mouseClick(win.autofocusWidget.btn_autofocus, Qt.LeftButton)
        assert not win.autofocusController.autofocus_in_progress
        start_widget = win.recordTabWidget.currentWidget()
        if hasattr(start_widget, "btn_startAcquisition"):
            qtbot.mouseClick(start_widget.btn_startAcquisition, Qt.LeftButton)
            assert not start_widget.btn_startAcquisition.isChecked()
        assert not win.multipointController.acquisition_in_progress()
        # Click-to-move, well-selection moves and scroll-Z go through these handlers.
        win.move_to_mm(start_pos.x_mm + 1.0, start_pos.y_mm + 1.0)
        win.move_from_click_mm(start_pos.x_mm + 1.0, start_pos.y_mm + 1.0)
        win.move_z_from_scroll(5.0)
        pos = win.stage.get_pos()
        assert (pos.x_mm, pos.y_mm, pos.z_mm) == pytest.approx((start_pos.x_mm, start_pos.y_mm, start_pos.z_mm))

    new_objective = next(o for o in win.objectiveStore.objectives_dict if o != win.objectiveStore.current_objective)
    win.objectivesWidget.dropdown.setCurrentText(new_objective)

    assert servo.retract_entered.wait(timeout=5)  # phase 1 is blocked mid-retract
    attempt_everything()
    servo.retract_gate.set()

    qtbot.waitUntil(servo.restore_entered.is_set, timeout=5000)  # dialog answered; phase 2 blocked mid-restore
    attempt_everything()
    servo.restore_gate.set()

    qtbot.waitUntil(win.objectivesWidget.dropdown.isEnabled, timeout=5000)
    assert [w.isEnabled() for w in blocked] == enabled_before
    assert not win._objective_swap_active
    assert win.objectiveStore.current_objective == new_objective
