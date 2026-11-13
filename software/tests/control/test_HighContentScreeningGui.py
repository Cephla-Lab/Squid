import logging
import threading
import time
import unittest.mock

import pytest

import control._def

import control.gui_hcs
import control.utils
import control.widgets
from qtpy.QtCore import QCoreApplication, QEvent
from qtpy.QtGui import QCloseEvent
from qtpy.QtWidgets import QMessageBox

import control.microcontroller
import control.microscope
import squid.abc
import squid.stage.cephla
import squid.stage.utils
from control._def import STARTUP_STATE_MIN_FIRMWARE
from control.firmware_sim_serial import FirmwareSimSerial
from control.startup_homing import StartupHoming


@pytest.fixture
def confirm_exit_yes(monkeypatch):
    """Auto-accept the 'Confirm Exit' dialog GUI shutdown shows, or teardown hangs forever."""

    def confirm_exit(parent, title, text, *args, **kwargs):
        if title == "Confirm Exit":
            return QMessageBox.Yes
        raise RuntimeError(f"Unexpected QMessageBox: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "question", confirm_exit)


@pytest.fixture(autouse=True)
def isolated_gallery_state(monkeypatch, tmp_path):
    """No GUI test writes the real acquisition history or gallery cache, or gets the first-launch import offer."""
    import control.widgets_gallery

    monkeypatch.setenv("SQUID_ACQUISITION_HISTORY_DIR", str(tmp_path / "history"))
    marker = tmp_path / "cache" / "gallery_import_offered.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("offered\n")
    monkeypatch.setattr(control.widgets_gallery, "IMPORT_OFFER_MARKER", str(marker))
    monkeypatch.setattr(
        control.widgets_gallery, "LAST_GALLERY_VIEW_CACHE", str(tmp_path / "cache" / "last_gallery_view.yaml")
    )


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


def test_destroying_the_gui_neither_crashes_nor_leaves_timers_firing_at_deleted_widgets(qtbot, confirm_exit_yes):
    """Regression: FlexibleMultiPointWidget added one QSpacerItem to several layouts, so the
    layouts freed it once each (a segfault when Qt ran the window's DeferredDelete), and
    ImageDisplayWindow's unparented update timer kept firing at the deleted QLabels."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)

    win.close()
    win.deleteLater()
    # Run the pending delete now, rather than whenever a later event loop happens to service it.
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    # The update timer fires every 100 ms; a RuntimeError ("wrapped C/C++ object ... has been
    # deleted") in its slot is reported by pytest-qt as a failure of this test.
    qtbot.wait(500)


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


def test_acquisition_finished_flushes_napari_multichannel_viewer(qtbot, monkeypatch, confirm_exit_yes):
    """The multichannel viewer coalesces refreshes and shows 2D while acquiring; the end of the
    acquisition must reach it to draw the final plane and restore the user's 2D/3D mode."""
    calls = []
    monkeypatch.setattr(control.widgets.NapariMultiChannelWidget, "finishAcquisition", lambda self: calls.append(self))

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    win.multipointController.acquisition_finished.emit()
    assert calls == [win.napariMultiChannelWidget]

    # Performance Mode disconnects the napari display feeds; enabled mid-acquisition it must
    # not cut the viewer off from the end of the run (it would stay in 2D).
    win.performanceModeToggle.setChecked(True)
    win.togglePerformanceMode()
    win.multipointController.acquisition_finished.emit()
    assert calls == [win.napariMultiChannelWidget] * 2

    # Leaving Performance Mode reconnects the feeds without connecting this a second time.
    win.performanceModeToggle.setChecked(False)
    win.togglePerformanceMode()
    win.multipointController.acquisition_finished.emit()
    assert calls == [win.napariMultiChannelWidget] * 3


def _gui_with_live_started_from_the_button(qtbot):
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    win.liveControlWidget.btn_live.click()
    assert win.liveController.is_live and win.liveControlWidget.btn_live.isChecked()
    return win


def test_live_button_shows_live_off_when_an_acquisition_stops_it(qtbot, confirm_exit_yes):
    """MultiPointController stops live itself, for GUI, TCP and fluidics runs alike: the button must follow."""
    win = _gui_with_live_started_from_the_button(qtbot)
    button = win.liveControlWidget.btn_live

    win.liveController.stop_live()  # what run_acquisition does before the worker starts
    win.multipointController.signal_acquisition_start.emit()

    assert not button.isChecked()
    assert button.text() == "Live"


def test_live_button_follows_live_at_the_end_of_an_acquisition(qtbot, confirm_exit_yes):
    """A run that fails to start reports only the finish; an INI-enabled resume turns live back on before it."""
    win = _gui_with_live_started_from_the_button(qtbot)
    button = win.liveControlWidget.btn_live

    win.liveController.stop_live()
    win.multipointController.acquisition_finished.emit()
    assert not button.isChecked()
    assert button.text() == "Live"

    win.liveController.start_live()  # resume_live_after_acquisition = True
    try:
        win.multipointController.acquisition_finished.emit()
        assert button.isChecked()
        assert button.text() == "Stop"
        assert button.isEnabled()
        assert not win.liveControlWidget.btn_snap.isEnabled()  # no snapping while live
    finally:
        win.liveController.stop_live()


def test_live_and_snap_are_locked_while_an_acquisition_runs(qtbot, confirm_exit_yes):
    """The acquisition owns the camera and the light: neither button may start live or snap until it finishes."""
    win = _gui_with_live_started_from_the_button(qtbot)
    live_widget = win.liveControlWidget

    win.liveController.stop_live()
    win.multipointController.signal_acquisition_start.emit()
    assert not live_widget.btn_live.isEnabled()
    assert not live_widget.btn_snap.isEnabled()

    win.multipointController.acquisition_finished.emit()
    assert live_widget.btn_live.isEnabled()
    assert live_widget.btn_snap.isEnabled()


def test_a_run_on_another_thread_waits_for_live_and_snap_to_lock_before_preparing(qtbot, confirm_exit_yes, monkeypatch):
    """A TCP run prepares on the server's thread while the GUI still takes clicks. Its preparing callback must not
    return until the GUI thread has locked Live and Snap, or the run could stop live and use the camera first."""
    win = _gui_with_live_started_from_the_button(qtbot)
    live_widget = win.liveControlWidget
    order = []
    set_running = live_widget.set_acquisition_running

    def recording_set_running(running):
        order.append(f"lock={running}")
        set_running(running)

    monkeypatch.setattr(live_widget, "set_acquisition_running", recording_set_running)

    def server_thread():
        win.multipointController.callbacks.signal_acquisition_preparing()
        order.append("preparing returned")

    try:
        thread = threading.Thread(target=server_thread)
        thread.start()
        time.sleep(0.3)  # the GUI thread is busy: anything queued to it cannot run yet
        qtbot.waitUntil(lambda: not thread.is_alive(), timeout=5000)

        assert order == ["lock=True", "preparing returned"]
        assert not live_widget.btn_live.isEnabled()
        assert not live_widget.btn_snap.isEnabled()
        assert live_widget.btn_live.isChecked()  # live is still on: the run has not stopped it yet

        win.liveController.stop_live()
        win.multipointController.acquisition_finished.emit()  # every failed start sends it too
        assert live_widget.btn_live.isEnabled()
        assert live_widget.btn_snap.isEnabled()
    finally:
        win.liveController.stop_live()


def test_a_run_on_the_gui_thread_locks_live_and_snap_without_waiting(qtbot, confirm_exit_yes):
    """GUI-started and fluidics runs prepare on the GUI thread: the lock is direct there (a blocking call to the
    thread's own event loop would never run)."""
    win = _gui_with_live_started_from_the_button(qtbot)
    live_widget = win.liveControlWidget
    try:
        win.multipointController.callbacks.signal_acquisition_preparing()

        assert not live_widget.btn_live.isEnabled()
        assert not live_widget.btn_snap.isEnabled()
    finally:
        win.liveController.stop_live()


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


def test_cleanup_continues_when_the_light_source_fails_to_close(qtbot, monkeypatch, confirm_exit_yes):
    """A light source that errors on shut_down (e.g. its link dropped) is only logged, even on a full
    shutdown, so the stage and microcontroller are still closed."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)

    class DroppedLightSource:
        def shut_down(self):
            raise ConnectionError("light source link dropped")

    scope.illumination_controller.light_source = DroppedLightSource()
    calls = []
    gui.stage.close = lambda: calls.append("stage")
    original_micro_close = gui.microcontroller.close

    def recording_micro_close():
        calls.append("microcontroller")
        original_micro_close()

    monkeypatch.setattr(gui.microcontroller, "close", recording_micro_close)
    gui.closeEvent = lambda event: event.accept()  # keep teardown from re-running cleanup

    gui._cleanup_common(for_restart=False)

    assert calls == ["stage", "microcontroller"]


def test_cleanup_stops_the_versalase_tab_before_shutting_the_lasers_down(qtbot, monkeypatch, confirm_exit_yes):
    """No status read may use the lasers while they are shut down and their devices closed."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)
    order = []

    class RecordingLightSource:
        def shut_down(self):
            order.append("lasers shut down")

    class RecordingTab:
        def stop(self):
            order.append("tab stopped")

    scope.illumination_controller.light_source = RecordingLightSource()
    gui.versaLaseWidget = RecordingTab()
    gui.closeEvent = lambda event: event.accept()  # keep teardown from re-running cleanup

    gui._cleanup_common(for_restart=True)

    assert order == ["tab stopped", "lasers shut down"]


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
        monkeypatch.setattr(module, "HOMING_ENABLED_X", True)
        monkeypatch.setattr(module, "HOMING_ENABLED_Y", True)
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


def test_there_is_one_utils_menu_with_every_utility(qtbot, monkeypatch, confirm_exit_yes):
    # main_hcs.py used to add a second "Utils" menu next to the one the GUI builds (two "Utils" in the menu bar)
    monkeypatch.setattr(control.gui_hcs, "USE_OBJECTIVE_TURRET", True)
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    menus = [action for action in win.menuBar().actions() if action.text() == "Utils"]
    assert len(menus) == 1
    items = [action.text() for action in menus[0].menu().actions()]
    assert items[:3] == ["Stage Utils", "Reset Objective Turret", "Workflow Runner..."]


# --- Home stage, Home X/Y and the homing lock ---


@pytest.fixture
def piezo_only(monkeypatch):
    monkeypatch.setattr(control._def, "IS_PIEZO_ONLY", True)


def _gui(qtbot, **build_kwargs):
    scope = control.microscope.Microscope.build_from_global_config(True, **build_kwargs)
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)
    return gui


def _auto_confirm(monkeypatch):
    """Accept every homing confirmation (the generic OK/Cancel box and the piezo-only prompt). A refusal would open a
    modal information box and hang the test: it raises instead."""
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: QMessageBox.Ok)
    monkeypatch.setattr(control.widgets, "confirm_nosepiece_lowered", lambda parent, decline_text: True)

    def refused(parent, title, text, *args, **kwargs):
        raise AssertionError(f"Unexpected refusal: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "information", refused)


def _capture_warnings(monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *args, **kwargs: warnings.append(text))
    return warnings


def _capture_refusals(monkeypatch):
    refusals = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *args, **kwargs: refusals.append(text))
    return refusals


def _start_a_move_that_stays_under_way(gui):
    """The controller keeps reporting the last command in progress (a move still under way)."""
    sim = gui.microscope.low_level_drivers.microcontroller._serial
    sim.hold_in_progress = True
    gui.stage.move_x(0.5, blocking=False)
    return sim


def _run(fn, **kw):
    """As threaded_operation_helper calls its callback: (True, None) or (False, the error)."""
    try:
        fn(**kw)
        return True, None
    except Exception as e:
        return False, str(e)


def test_home_stage_blocker_refuses_autofocus_but_not_the_host_busy_flag(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    monkeypatch.setattr(gui.autofocusController, "autofocus_in_progress", True)
    assert "autofocus" in gui._stage_homing_blocker()
    monkeypatch.setattr(gui.autofocusController, "autofocus_in_progress", False)
    # The host's flag is set by every command (live's included): it is not evidence that the stage moves.
    monkeypatch.setattr(gui.stage, "get_state", lambda: squid.abc.StageStage(busy=True))
    assert gui._stage_homing_blocker() is None


def test_home_stage_proceeds_with_only_the_host_busy_flag_set(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    micro = gui.microscope.low_level_drivers.microcontroller
    qtbot.waitUntil(lambda: gui.stage.reports_stopped(), timeout=2000)  # a fresh COMPLETED report
    micro.mcu_cmd_execution_in_progress = True  # e.g. live's last command, its ack not read yet
    finished = []
    gui.signal_home_stage_finished.connect(lambda ok, error: finished.append((ok, error)))
    gui._on_home_stage_requested()  # a refusal would raise (_auto_confirm)
    qtbot.waitUntil(lambda: bool(finished), timeout=10000)
    assert finished == [(True, "")]
    qtbot.waitUntil(lambda: not gui.microscope.stage_homing_in_progress, timeout=5000)


def test_home_stage_refused_while_the_stage_is_still_moving(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    refusals = _capture_refusals(monkeypatch)
    dispatched = []
    monkeypatch.setattr(control.utils, "threaded_operation_helper", lambda *args, **kwargs: dispatched.append(args))
    gui.liveController.start_live()
    sim = _start_a_move_that_stays_under_way(gui)
    gui._on_home_stage_requested()
    assert refusals == ["Homing refused: the stage is still moving."]
    assert dispatched == []  # nothing homed
    # The claim stays held until the stage stops: live paused, controls locked
    assert gui.microscope.stage_homing_in_progress and not gui.liveController.is_live
    assert not gui.navigationWidget.isEnabled()
    sim.hold_in_progress = False
    sim.send_status_packet()
    qtbot.waitUntil(
        lambda: not gui.microscope.stage_homing_in_progress
        and gui.liveController.is_live
        and gui.navigationWidget.isEnabled(),
        timeout=5000,
    )


def test_home_stage_claims_before_dispatch_and_restores_live(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    dispatched = []
    monkeypatch.setattr(
        control.utils,
        "threaded_operation_helper",
        lambda fn, cb, **kw: dispatched.append(gui.microscope.stage_homing_in_progress)
        or threading.Thread(target=lambda: cb(*_run(fn, **kw))).start(),
    )
    gui.liveController.start_live()
    gui._on_home_stage_requested()
    assert dispatched == [True] and not gui.liveController.is_live
    qtbot.waitUntil(lambda: not gui.microscope.stage_homing_in_progress, timeout=10000)
    qtbot.waitUntil(lambda: gui.liveController.is_live and gui.navigationWidget.isEnabled(), timeout=5000)


def test_home_stage_timeout_keeps_gui_locked_until_stopped(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    warnings = _capture_warnings(monkeypatch)
    sim = gui.microscope.low_level_drivers.microcontroller._serial
    monkeypatch.setattr(squid.stage.cephla.CephlaStage, "_calc_move_timeout", staticmethod(lambda d, s: 0.3))
    sim.hold_in_progress = True
    gui._on_home_stage_requested()
    qtbot.waitUntil(lambda: bool(warnings), timeout=5000)
    assert "still reports motion" in warnings[0]
    assert not gui.navigationWidget.isEnabled() and gui.microscope.stage_homing_in_progress
    sim.hold_in_progress = False
    sim.send_status_packet()
    # Until the claim clears too: the window refuses to close while the stage homes
    qtbot.waitUntil(
        lambda: gui.navigationWidget.isEnabled() and not gui.microscope.stage_homing_in_progress, timeout=5000
    )


def test_home_x_pauses_live(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    seen = []
    gui.microscope.add_stage_homing_listener(lambda active: seen.append((active, gui.liveController.is_live)))
    gui.liveController.start_live()
    gui.stageUtils.home_x()
    assert seen[0] == (True, False) and gui.liveController.is_live


def test_home_x_refused_while_the_stage_is_still_moving(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    refusals = _capture_refusals(monkeypatch)
    homes = []
    monkeypatch.setattr(gui.stage, "home", lambda **kw: homes.append(kw))
    gui.liveController.start_live()
    sim = _start_a_move_that_stays_under_way(gui)
    gui.stageUtils.home_x()
    assert refusals == ["Homing refused: the stage is still moving."]
    assert homes == []  # nothing homed
    assert gui.microscope.stage_homing_in_progress and not gui.liveController.is_live
    sim.hold_in_progress = False
    sim.send_status_packet()
    qtbot.waitUntil(lambda: not gui.microscope.stage_homing_in_progress and gui.liveController.is_live, timeout=5000)


def test_home_x_on_piezo_only_asks_and_passes_the_confirmation(qtbot, confirm_exit_yes, monkeypatch, piezo_only):
    gui = _gui(qtbot, confirm_objective_lowered=lambda stage_homes: True)
    _auto_confirm(monkeypatch)
    homes = []
    monkeypatch.setattr(gui.stage, "home", lambda **kw: homes.append(kw))
    gui.stageUtils.home_x()
    assert homes == [dict(x=True, y=False, z=False, theta=False, objective_lowered=True)]

    # Cancel at the stop sign: nothing moves
    asked = []
    monkeypatch.setattr(
        control.widgets, "confirm_nosepiece_lowered", lambda parent, decline_text: asked.append(decline_text) or False
    )
    gui.stageUtils.home_y()
    assert asked == ["Cancel"] and len(homes) == 1


def test_home_stage_on_piezo_only_asks_at_the_stop_sign(qtbot, confirm_exit_yes, monkeypatch, piezo_only):
    gui = _gui(qtbot, confirm_objective_lowered=lambda stage_homes: True)
    _auto_confirm(monkeypatch)
    asked = []
    monkeypatch.setattr(
        control.widgets, "confirm_nosepiece_lowered", lambda parent, decline_text: asked.append(decline_text) or False
    )
    home_stage = unittest.mock.MagicMock()
    monkeypatch.setattr(gui.microscope, "home_stage", home_stage)
    gui._on_home_stage_requested()
    assert asked == ["Cancel"]
    home_stage.assert_not_called()
    assert not gui.microscope.stage_homing_in_progress


def test_tcp_home_locks_the_gui(qtbot, confirm_exit_yes):
    gui = _gui(qtbot)
    claim = gui.microscope.claim_stage_homing("TCP home")
    qtbot.waitUntil(lambda: not gui.navigationWidget.isEnabled(), timeout=2000)
    assert gui._stage_moves_blocked_by_objective_swap("Click to move")
    assert gui.multipointController.start_blocker() == "the stage is homing"
    claim.release_when_stopped()
    qtbot.waitUntil(lambda: gui.navigationWidget.isEnabled(), timeout=2000)


def test_homing_claimed_off_the_gui_thread_locks_the_gui_before_the_claim_returns(qtbot, confirm_exit_yes):
    """A TCP home claims from the server's thread: the GUI (and the control panel lock command) must be locked
    before that thread sends its first homing command, and unlocked only after the claim's release."""
    gui = _gui(qtbot)
    seen = {}

    def tcp_home():
        claim = gui.microscope.claim_stage_homing("TCP home")
        seen["locked_when_claimed"] = not gui.navigationWidget.isEnabled()
        claim.release_when_stopped()
        seen["done"] = True

    threading.Thread(target=tcp_home).start()
    qtbot.waitUntil(lambda: "done" in seen, timeout=5000)
    assert seen["locked_when_claimed"]
    qtbot.waitUntil(lambda: gui.navigationWidget.isEnabled(), timeout=2000)


@pytest.mark.parametrize("at_the_cached_xy", [True, False])
def test_already_homed_leaves_xy_and_restores_z_only_at_the_cached_xy(
    qtbot, confirm_exit_yes, monkeypatch, caplog, at_the_cached_xy
):
    """A warm relaunch: the controller vouched that the stage is still homed, so X/Y stay where the stage stands.
    Z comes back as after a homed startup only while X/Y are where the position was cached (within 0.1 mm on each
    axis). After a crash, or a panel jog while Squid was closed, the cached Z is another place's focus height: Z stays
    where it is. Either way the Wellplate tab takes the Z the stage ends at."""
    _set_z_flags(monkeypatch, homing_z=True, pi_focus=False)
    monkeypatch.setattr(control._def, "ENABLE_MEMORY_PROFILING", True)  # the RAM monitor appears with the window
    # strict=False: the GUI also sends commands the sim's protocol whitelist lacks (SET_WATCHDOG_TIMEOUT, HEARTBEAT)
    sim = FirmwareSimSerial(strict=False, firmware_version=STARTUP_STATE_MIN_FIRMWARE)
    monkeypatch.setattr(control.microcontroller, "get_microcontroller_serial_device", lambda **kw: sim)
    earlier = control.microscope.Microscope.build_from_global_config(True)  # homes and stores the signature
    earlier.stage.move_x_to(10.0)
    earlier.stage.move_y_to(10.0)
    earlier.stage.move_z_to(0.8)  # where that session left Z (it died before a clean close retracted it)
    earlier.close()
    sim.reconnect(1)  # the controller stayed powered

    safety_z_mm = int(control._def.Z_HOME_SAFETY_POINT) / 1000.0
    # Above the safety point, so a restore moves Z to the cached Z itself; Y 0.2 mm off is beyond the 0.1 mm tolerance
    cached_y_mm = 10.0 if at_the_cached_xy else 10.2
    cached = squid.abc.Pos(x_mm=10.0, y_mm=cached_y_mm, z_mm=safety_z_mm + 0.5, theta_rad=None)
    monkeypatch.setattr(squid.stage.utils, "get_cached_position", lambda *args, **kwargs: cached)
    scope = control.microscope.Microscope.build_from_global_config(True)
    assert scope.startup_homing is StartupHoming.ALREADY_HOMED
    z_before = scope.stage.get_pos().z_mm
    assert z_before == pytest.approx(0.8, abs=1e-3)  # a warm start kept it
    moves = _record_xy_moves(monkeypatch, scope.stage)
    _spy_moves(monkeypatch, moves, scope.stage, "move_z_to", "z")
    init_z_calls = []
    real_init_z = control.widgets.WellplateMultiPointWidget.init_z

    def init_z(self, *args, **kwargs):
        init_z_calls.append(type(self).__name__)
        return real_init_z(self, *args, **kwargs)

    monkeypatch.setattr(control.widgets.WellplateMultiPointWidget, "init_z", init_z)
    with caplog.at_level(logging.INFO):
        gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)

    pos = scope.stage.get_pos()
    assert (pos.x_mm, pos.y_mm) == pytest.approx((10.0, 10.0), abs=1e-3)  # to a microstep
    if at_the_cached_xy:
        assert moves == [("z", cached.z_mm)] and pos.z_mm == pytest.approx(cached.z_mm, abs=1e-3)
        assert "Cached position was saved at another XY" not in caplog.text
    else:
        assert moves == [] and pos.z_mm == z_before
        assert "Cached position was saved at another XY: Z left where it is" in caplog.text
    # init_z runs either way: the Wellplate Z range starts at the Z the stage ends at (the Flexible tab has none)
    assert init_z_calls == ["WellplateMultiPointWidget"]
    assert gui.wellplateMultiPointWidget.entry_minZ.value() == pytest.approx(pos.z_mm * 1000, abs=1)

    # The message shows once the window does, and the status-bar widgets that appear with it do not cover it
    gui.show()
    qtbot.waitExposed(gui)
    assert gui.statusBar().currentMessage() == "Stage still homed: startup homing skipped"
    assert not gui.ramMonitorWidget.isVisible()


def _z_lift_system_closed_at_loading(monkeypatch, tmp_path):
    """A Z lift system (V-308 focus, the lift on the Cephla Z) whose last session was closed at the loading position.
    Returns its 1.9 controller, patched in as every Microscope's."""
    _set_z_flags(monkeypatch, homing_z=False, pi_focus=True)
    for name, value in (
        ("USE_Z_LIFT_STAGE", True),
        ("Z_LIFT_MIN_MM", 0.0),
        ("Z_LIFT_MAX_MM", 8.0),
        ("Z_LIFT_FOCUS_WHEEL_DRIVES_PI", False),
        ("Z_LIFT_HEIGHTS_PATH", str(tmp_path / "heights.json")),
        ("Z_LIFT_LOADING_STATE_PATH", str(tmp_path / "loading_state.json")),
        ("USE_OBJECTIVE_TURRET", False),
        ("USE_XERYON", False),
    ):
        monkeypatch.setattr(control._def, name, value)
    (tmp_path / "loading_state.json").write_text(
        '{"scanning_z_mm": null, "lift_mm": 2.0, "lift_format": "96 well plate"}'
    )
    # strict=False: the GUI also sends commands the sim's protocol whitelist lacks (SET_WATCHDOG_TIMEOUT, HEARTBEAT)
    sim = FirmwareSimSerial(strict=False, firmware_version=STARTUP_STATE_MIN_FIRMWARE)
    monkeypatch.setattr(control.microcontroller, "get_microcontroller_serial_device", lambda **kw: sim)
    return sim


def _spy_moves(monkeypatch, moves, owner, method, label):
    """Append (label, target) to `moves` for each call of owner.<method>(target, ...) from now on, still carried out."""
    real = getattr(owner, method)

    def spy(mm, *args, **kwargs):
        moves.append((label, mm))
        return real(mm, *args, **kwargs)

    monkeypatch.setattr(owner, method, spy)


def _record_xy_moves(monkeypatch, stage):
    """The absolute X/Y moves sent through `stage` from now on, each still carried out."""
    moves = []
    for axis in ("x", "y"):
        _spy_moves(monkeypatch, moves, stage, f"move_{axis}_to", axis)
    return moves


def _record_moves(monkeypatch, scope):
    """The absolute X, Y and Z (objective) moves sent through scope.stage and the Z lift's moves, from now on, in
    order, each still carried out."""
    moves = _record_xy_moves(monkeypatch, scope.stage)
    _spy_moves(monkeypatch, moves, scope.stage, "move_z_to", "z")
    _spy_moves(monkeypatch, moves, scope.z_lift, "move_to", "lift")
    return moves


def _gui_on(qtbot, scope):
    gui = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(gui)
    gui.show()
    qtbot.waitExposed(gui)
    qtbot.waitUntil(lambda: gui._loading_position_locked, timeout=5000)  # the restored loading state locks it
    return gui


@pytest.mark.parametrize("left_at_loading_xy", [True, False])
def test_already_homed_at_the_loading_position_moves_xy_only_if_not_there(
    qtbot, confirm_exit_yes, monkeypatch, tmp_path, left_at_loading_xy
):
    """A warm start on a system closed at the loading position moves nothing when the stage is still at the loading
    XY (within 0.1 mm on each axis); a session that died on the way (elsewhere) still goes there. Either way the
    loading lock applies and the warm start says so."""
    sim = _z_lift_system_closed_at_loading(monkeypatch, tmp_path)
    earlier = control.microscope.Microscope.build_from_global_config(True)  # homes and stores the signature
    if left_at_loading_xy:
        squid.stage.utils.move_xy_to_loading_position(earlier.stage, is_wellplate=True)
    else:
        earlier.stage.move_x_to(10.0)
        earlier.stage.move_y_to(10.0)
    earlier.close()
    sim.reconnect(1)  # the controller stayed powered

    scope = control.microscope.Microscope.build_from_global_config(True)
    assert scope.startup_homing is StartupHoming.ALREADY_HOMED
    moves = _record_moves(monkeypatch, scope)
    gui = _gui_on(qtbot, scope)

    loading = (control._def.SLIDE_POSITION.LOADING_X_MM, control._def.SLIDE_POSITION.LOADING_Y_MM)
    pos = scope.stage.get_pos()
    assert (pos.x_mm, pos.y_mm) == pytest.approx(loading, abs=1e-3)
    assert (moves == []) is left_at_loading_xy  # at the loading XY nothing moves: not XY, the objective or the lift
    assert gui.stageUtils.slide_position == "loading" and not gui.navigationWidget.isEnabled()
    assert gui.statusBar().currentMessage() == "Stage still homed: startup homing skipped"


def test_already_homed_away_from_the_loading_position_clears_the_sample_before_xy(
    qtbot, confirm_exit_yes, monkeypatch, tmp_path
):
    """A session that died on its way back to scanning (the lift raised, the objective lowered, the loading state not
    yet cleared): a warm relaunch retracts the objective and lowers the lift before XY travels back to the loading
    position, as the move there does. The Z and lift height to return to stay the restored state's."""
    sim = _z_lift_system_closed_at_loading(monkeypatch, tmp_path)
    state_path = tmp_path / "loading_state.json"
    state_path.write_text('{"scanning_z_mm": 1.2, "lift_mm": 2.0, "lift_format": "96 well plate"}')
    state_before = state_path.read_text()
    earlier = control.microscope.Microscope.build_from_global_config(True)  # homes and stores the signature
    earlier.stage.move_x_to(10.0)
    earlier.stage.move_y_to(10.0)
    earlier.z_lift.move_to(3.0)  # the return raised the lift
    earlier.close()
    sim.reconnect(1)  # the controller stayed powered

    scope = control.microscope.Microscope.build_from_global_config(True)
    assert scope.startup_homing is StartupHoming.ALREADY_HOMED
    assert scope.z_lift.get_position_mm() == pytest.approx(3.0, abs=1e-3)  # still raised: a warm start keeps it
    scope.stage.move_z_to(1.5)  # and the return had lowered the objective to its working height
    moves = _record_moves(monkeypatch, scope)
    real_home = scope.stage.home

    def spy_home(**kwargs):
        moves.append(("z_home", kwargs["z"]))
        real_home(**kwargs)
        moves.append(("z_at", scope.stage.get_pos().z_mm))

    monkeypatch.setattr(scope.stage, "home", spy_home)
    gui = _gui_on(qtbot, scope)

    first_xy = next(i for i, (label, _) in enumerate(moves) if label in ("x", "y"))
    assert moves[:first_xy] == [
        ("z_home", True),
        ("z_at", pytest.approx(control._def.OBJECTIVE_RETRACTED_POS_MM, abs=1e-3)),
        ("lift", scope.z_lift.lowered_mm),
    ]
    loading_x, loading_y = control._def.SLIDE_POSITION.LOADING_X_MM, control._def.SLIDE_POSITION.LOADING_Y_MM
    assert moves[first_xy:] == [("y", 15), ("x", 35), ("y", loading_y), ("x", loading_x)]
    assert scope.z_lift.is_lowered()
    # Not recorded again, and the heights to return to are the restored ones (not Z 1.5, lift 3.0)
    assert state_path.read_text() == state_before
    assert scope.stage._scanning_position_z_mm == 1.2
    assert scope.stage._scanning_position_lift_mm == (2.0, "96 well plate")
    assert gui.stageUtils.slide_position == "loading" and not gui.navigationWidget.isEnabled()
    assert gui.statusBar().currentMessage() == "Stage still homed: startup homing skipped"


def test_homed_start_closed_at_the_loading_position_moves_back_there(qtbot, confirm_exit_yes, monkeypatch, tmp_path):
    """A homing startup on a system closed at the loading position goes back there by the loading path, XY only:
    homing already lowered the lift and retracted the objective."""
    _z_lift_system_closed_at_loading(monkeypatch, tmp_path)
    scope = control.microscope.Microscope.build_from_global_config(True)  # a first launch: homes
    assert scope.startup_homing is StartupHoming.HOMED
    moves = _record_xy_moves(monkeypatch, scope.stage)
    gui = _gui_on(qtbot, scope)

    loading_x, loading_y = control._def.SLIDE_POSITION.LOADING_X_MM, control._def.SLIDE_POSITION.LOADING_Y_MM
    assert moves == [("y", 15), ("x", 35), ("y", loading_y), ("x", loading_x)]
    assert gui.stageUtils.slide_position == "loading" and not gui.navigationWidget.isEnabled()
    assert gui.statusBar().currentMessage() == ""


# --- One exclusion for every claim, failures shown, close refused while homing ---


@pytest.mark.parametrize("busy", ["objective_swap", "loading_position", "acquisition"])
def test_tcp_home_is_refused_while_the_gui_drives_the_stage(qtbot, confirm_exit_yes, busy):
    """A TCP home, on the server's thread, is refused with the GUI's reason while an objective swap, the loading
    position or an acquisition holds the stage: nothing moves and nothing is claimed, so the homing lock never
    nests inside those locks."""
    from control.microscope_control_server import MicroscopeControlServer
    from tests.control.test_startup_homing import _spy_commands

    gui = _gui(qtbot)
    server = MicroscopeControlServer(gui.microscope, multipoint_controller=gui.multipointController, gui=gui)
    acquiring = unittest.mock.patch.object(gui.multipointController, "acquisition_in_progress", return_value=True)
    reason, start, end = {
        "objective_swap": (
            "an objective change is in progress",
            lambda: gui._on_objective_swap_active(True),
            lambda: gui._on_objective_swap_active(False),
        ),
        "loading_position": (
            "the stage is at (or moving to or from) the loading position",
            gui._lock_stage_at_loading_position,
            gui._unlock_stage_after_loading_position,
        ),
        "acquisition": ("an acquisition is running", acquiring.start, acquiring.stop),
    }[busy]
    start()
    try:
        events = []
        gui.microscope.add_stage_homing_listener(events.append)
        sent = _spy_commands(gui.microscope.low_level_drivers.microcontroller)
        responses = []
        threading.Thread(target=lambda: responses.append(server._execute("home", {"objective_lowered": True}))).start()
        qtbot.waitUntil(lambda: bool(responses), timeout=5000)
        assert responses == [{"success": False, "error": f"Home XYZ refused: {reason}"}]
        assert sent == [] and events == [] and not gui.microscope.stage_homing_in_progress
    finally:
        qtbot.waitUntil(lambda: not gui.microscope.stage_homing_in_progress, timeout=5000)
        end()


def test_old_firmware_panel_lock_warning_is_logged_once(qtbot, confirm_exit_yes, caplog):
    gui = _gui(qtbot)
    assert not gui.microcontroller.supports_panel_lock()  # the simulated controller's firmware is older than 1.8
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            gui.microscope.claim_stage_homing("TCP home").release_when_stopped()
            qtbot.waitUntil(lambda: not gui.microscope.stage_homing_in_progress, timeout=2000)
        gui._lock_stage_at_loading_position()  # the loading lock still asks for the panel lock
        gui._unlock_stage_after_loading_position()
    assert len([r for r in caplog.records if "cannot lock the control panel" in r.getMessage()]) == 1


def test_home_x_timeout_says_homing_did_not_finish(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    warnings = _capture_warnings(monkeypatch)
    sim = gui.microscope.low_level_drivers.microcontroller._serial
    monkeypatch.setattr(squid.stage.cephla.CephlaStage, "_calc_move_timeout", staticmethod(lambda d, s: 0.3))
    sim.hold_in_progress = True
    gui.stageUtils.home_x()  # the failure is shown, not raised out of the click
    assert len(warnings) == 1 and warnings[0].startswith("Homing did not finish (")
    assert warnings[0].endswith("The stage still reports motion: motion controls stay locked until it stops.")
    assert gui.microscope.stage_homing_in_progress and not gui.navigationWidget.isEnabled()
    sim.hold_in_progress = False
    sim.send_status_packet()
    qtbot.waitUntil(
        lambda: not gui.microscope.stage_homing_in_progress and gui.navigationWidget.isEnabled(), timeout=5000
    )


def test_home_y_failure_after_release_says_homing_failed(qtbot, confirm_exit_yes, monkeypatch):
    gui = _gui(qtbot)
    _auto_confirm(monkeypatch)
    warnings = _capture_warnings(monkeypatch)

    def link_lost(**kwargs):
        raise ConnectionError("link lost")  # before anything was sent: the stage still reports stopped

    monkeypatch.setattr(gui.stage, "home", link_lost)
    gui.stageUtils.home_y()
    assert warnings == ["Homing failed: link lost"]
    assert not gui.microscope.stage_homing_in_progress


def test_close_while_homing_is_refused_and_moves_nothing(qtbot, confirm_exit_yes, monkeypatch):
    """The cleanup would cache a mid-sweep position and send the Z retract from the GUI thread while the homing
    thread still drives the controller."""
    from tests.control.test_startup_homing import _spy_commands

    gui = _gui(qtbot)
    refusals = _capture_refusals(monkeypatch)
    cleanups = []
    spawned = []
    monkeypatch.setattr(control.gui_hcs.subprocess, "Popen", lambda *args, **kwargs: spawned.append(args))
    gui._cleanup_common = lambda **kw: cleanups.append(kw)
    try:
        claim = gui.microscope.claim_stage_homing("Home stage")
        sent = _spy_commands(gui.microscope.low_level_drivers.microcontroller)
        event = QCloseEvent()
        gui.closeEvent(event)
        gui.restart_application()
        assert not event.isAccepted() and cleanups == [] and spawned == [] and sent == []
        assert refusals == [
            "The stage is homing. Close Squid once it has finished.",
            "The stage is homing. Restart Squid once it has finished.",
        ]
        claim.release_when_stopped()
        qtbot.waitUntil(lambda: not gui.microscope.stage_homing_in_progress, timeout=2000)
    finally:
        del gui._cleanup_common  # the teardown's own close runs the real cleanup


def test_reset_objective_turret_is_locked_and_refused_while_the_stage_homes(qtbot, confirm_exit_yes, monkeypatch):
    """The turret reset retracts and restores Z and turns the turret: it must not run under a homing claim (a
    background Home stage still sweeps XY). Its menu action is disabled for the claim and gets back the state it
    had; a reset that still gets through is refused, with nothing sent to the changer."""
    monkeypatch.setattr(control.gui_hcs, "USE_OBJECTIVE_TURRET", True)
    gui = _gui(qtbot)
    changer = unittest.mock.MagicMock(name="objective_changer")
    monkeypatch.setattr(gui, "objective_changer", changer)
    refusals = _capture_refusals(monkeypatch)
    (utils,) = [action for action in gui.menuBar().actions() if action.text() == "Utils"]
    (reset_action,) = [action for action in utils.menu().actions() if action.text() == "Reset Objective Turret"]

    for enabled_before in (True, False):
        reset_action.setEnabled(enabled_before)
        claim = gui.microscope.claim_stage_homing("Home stage")
        try:
            assert not reset_action.isEnabled()
            gui.resetObjectiveTurret()
            assert changer.mock_calls == []
        finally:
            claim.release_when_stopped()
        qtbot.waitUntil(lambda: not gui.microscope.stage_homing_in_progress, timeout=2000)
        assert reset_action.isEnabled() is enabled_before

    assert refusals == ["The stage is homing. Reset the objective turret once it has finished."] * 2


@pytest.mark.parametrize("start_on_piezo", [False, True])
def test_wellplate_yaml_with_a_piezo_range_reloads_set_range_and_reaches_the_controller(
    qtbot, monkeypatch, confirm_exit_yes, start_on_piezo
):
    from control.acquisition_yaml_loader import AcquisitionYAMLData

    monkeypatch.setattr(control._def, "HAS_OBJECTIVE_PIEZO", True)
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    w = win.wellplateMultiPointWidget
    w.checkbox_usePiezo.setChecked(start_on_piezo)

    w._apply_yaml_settings(
        AcquisitionYAMLData(
            widget_type="wellplate", nz=4, delta_z_um=2.0, use_piezo=True, piezo_z_range_um=(20.0, 26.0)
        )
    )

    assert w.combobox_z_mode.currentText() == "Set Range"
    assert (w.entry_minZ.value(), w.entry_maxZ.value(), w.entry_NZ.value()) == (20.0, 26.0, 4)
    assert w.entry_maxZ.maximum() == scope.addons.piezo_stage.range_um
    assert not w.checkbox_withReflectionAutofocus.isEnabled()

    w.configure_controller_from_ui()  # what Start runs
    assert w.multipointController.use_piezo
    assert tuple(w.multipointController.piezo_z_range_um) == (20.0, 26.0)


def test_wellplate_yaml_moving_the_stack_to_the_stepper_gives_the_entries_the_stepper_limits(
    qtbot, monkeypatch, confirm_exit_yes
):
    from control.acquisition_yaml_loader import AcquisitionYAMLData

    monkeypatch.setattr(control._def, "HAS_OBJECTIVE_PIEZO", True)
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    w = win.wellplateMultiPointWidget
    w.checkbox_usePiezo.setChecked(True)

    w._apply_yaml_settings(AcquisitionYAMLData(widget_type="wellplate", nz=4, delta_z_um=2.0, use_piezo=False))

    assert w.entry_minZ.maximum() == control._def.SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000


def test_a_robot_loading_move_is_refused_during_a_manual_objective_swap(qtbot, confirm_exit_yes):
    # Without a Z lift there is no lift guard to hold: the swap lock itself has to reach Stage Utils
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    assert win.stageUtils.lift_motion_guard is None

    win.objectivesWidget.signal_swap_active.emit(True)  # a manual swap starts (the GUI's swap lock follows it)
    with unittest.mock.patch("control.widgets.move_to_loading_position") as move:
        refusal = win.stageUtils.start_unattended_position_move(True, 1)
    assert "objective" in refusal
    move.assert_not_called()
    win.objectivesWidget.signal_swap_active.emit(False)


def _gallery_row(gallery, entry_id):
    return next(row for row in gallery.rows() if row.entry.id == entry_id)


def test_gallery_is_wired_to_the_history_viewer_and_launcher(qtbot, confirm_exit_yes, monkeypatch, tmp_path):
    """The Gallery tab shows the history the controller records into; opening an acquisition goes to the NDViewer
    tab or an ndviewer_light window, loading its settings to the multipoint tab it was run from."""
    import os
    import socket

    from qtpy.QtWidgets import QWidget

    from control.ndviewer_launcher import LauncherResolution
    from squid.acquisition_history import HistoryStore
    from tests.control.gallery_fixtures import write_acquisition

    monkeypatch.setenv("SQUID_ACQUISITION_HISTORY_DIR", str(tmp_path / "history"))
    resolutions = [LauncherResolution(["ndviewer-light"], "path", "ndviewer-light on the PATH")]
    monkeypatch.setattr(control.gui_hcs, "resolve_ndviewer_light", lambda preference="": resolutions[-1])
    launched = []
    exit_codes = [None]  # what the launched process's poll() reports when the launch check runs

    class FakeProcess:
        def poll(self):
            return exit_codes[-1]

    def fake_launch(resolution, folder, log_path=None):
        launched.append((resolution, folder, log_path))
        return FakeProcess()

    monkeypatch.setattr(control.gui_hcs, "launch_ndviewer_light", fake_launch)

    done = write_acquisition(tmp_path, "done_2026-11-07_10-00-00.000000")
    (tmp_path / "live").mkdir()
    seed = HistoryStore(tmp_path / "history")
    seed.append({"type": "started", "id": "done", "kind": "run", "name": "done", "folder": str(done), "at": 1000.0})
    seed.append({"type": "finished", "id": "done", "outcome": "complete", "fraction_done": 1.0, "at": 1100.0})
    # Running in another Squid: left alone. Running here in a process that is gone: closed at startup.
    live = {"type": "started", "id": "live", "kind": "run", "name": "live", "folder": str(tmp_path / "live")}
    seed.append({**live, "at": 2000.0, "machine": "another-host", "pid": 1})
    orphan = {"type": "started", "id": "orphan", "kind": "run", "name": "orphan", "folder": str(tmp_path / "orphan")}
    seed.append({**orphan, "at": 1500.0, "machine": socket.gethostname(), "pid": None})

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    assert (
        win.multipointController.history_recorder is win.historyRecorder
        and win.historyStore.path.parent == tmp_path / "history"
    )
    assert win.historyStore.entry("orphan").outcome == "failed"  # reconciled at startup
    assert win.historyStore.entry("live").outcome == "running"
    gallery = win.acquisitionGalleryWidget
    assert gallery._store is win.historyStore
    assert win.imageDisplayTabs.tabText(win.imageDisplayTabs.indexOf(gallery)) == "Gallery"
    assert win._acquisition_saving_root() == win.multipointController.base_path

    gallery.reload()
    qtbot.waitUntil(lambda: {r.entry.id for r in gallery.rows()} == {"done", "live", "orphan"}, timeout=10000)
    assert gallery.select_entry("done")
    assert gallery.btn_open_in_window.isEnabled()

    # Open in Squid: the NDViewer tab shows the folder and comes to the front.
    class FakeViewerTab(QWidget):
        def __init__(self):
            super().__init__()
            self.opened = []

        def set_dataset_path(self, path):
            self.opened.append(path)

    viewer = FakeViewerTab()
    win.imageDisplayTabs.addTab(viewer, "NDViewer")
    win.ndviewerTab = viewer
    gallery.signal_open_in_squid.emit(str(tmp_path))
    assert viewer.opened == [str(tmp_path)]
    assert win.imageDisplayTabs.currentWidget() is viewer

    # Open in window: ndviewer_light is launched with the resolved command, its output going to the history
    # folder's log; a viewer still running after the check is fine, one that exited is reported with its log.
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append((args[1], args[2])))
    win.NDVIEWER_LAUNCH_CHECK_MS = 50
    log_path = str(tmp_path / "history" / "ndviewer_light.log")
    gallery.signal_open_in_window.emit(str(done))
    assert launched == [(resolutions[0], str(done), log_path)]
    qtbot.wait(300)
    assert warnings == []

    exit_codes.append(1)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("".join(f"noise {i}\n" for i in range(20)) + "ModuleNotFoundError: No module named 'PyQt6'\n")
    gallery.signal_open_in_window.emit(str(done))
    qtbot.waitUntil(lambda: len(warnings) == 1, timeout=5000)
    title, text = warnings[0]
    assert title == "ndviewer_light exited" and "No module named 'PyQt6'" in text and "noise 5" not in text
    assert "ndviewer_light exited (code 1)" in gallery.status_label.text()
    warnings.clear()

    def failing_launch(resolution, folder, log_path=None):
        raise ValueError("bad command line")

    monkeypatch.setattr(control.gui_hcs, "launch_ndviewer_light", failing_launch)
    gallery.signal_open_in_window.emit(str(done))
    assert warnings == [("Cannot open ndviewer_light", "bad command line")]
    warnings.clear()

    # Launcher unavailable: re-resolved when the tab is shown, the button says why.
    resolutions.append(LauncherResolution(None, "none", "ndviewer_light is not installed"))
    gallery.signal_shown.emit()
    assert not gallery.btn_open_in_window.isEnabled()
    assert gallery.btn_open_in_window.toolTip() == "ndviewer_light is not installed"

    # During an acquisition the NDViewer tab is busy with the running one.
    mpc = win.multipointController
    assert gallery.btn_import.isEnabled() and not win.historyRecorder._busy
    mpc.signal_acquisition_start.emit()
    assert not gallery.btn_open_in_squid.isEnabled()
    assert gallery.btn_open_in_squid.toolTip() == "Wait for the running acquisition to finish"
    # ... and no import or preview work runs during it.
    assert not gallery.btn_import.isEnabled()
    assert gallery.btn_import.toolTip() == "Wait for the running acquisition to finish"
    assert win.historyRecorder._busy
    qtbot.waitUntil(lambda: not gallery._loading, timeout=10000)  # the reload the start asked for

    # Progress: region 1 of 2, FOV 2 of 4, one timepoint -> 2 of 8 FOVs.
    mpc.signal_acquisition_progress.emit(1, 2, 0)
    mpc.signal_region_progress.emit(2, 4)
    assert _gallery_row(gallery, "live").status_label == "Running 25%"

    mpc.acquisition_finished.emit()
    assert gallery.btn_open_in_squid.isEnabled()
    assert not gallery.btn_open_in_squid.isHidden()
    assert gallery.btn_import.isEnabled() and not win.historyRecorder._busy

    # Performance mode turns the Gallery tab off like the other display tabs.
    win.performanceModeToggle.setChecked(True)
    win.togglePerformanceMode()
    assert not win.imageDisplayTabs.isTabEnabled(win.imageDisplayTabs.indexOf(gallery))
    win.performanceModeToggle.setChecked(False)
    win.togglePerformanceMode()
    assert win.imageDisplayTabs.isTabEnabled(win.imageDisplayTabs.indexOf(gallery))

    # Load settings: to the multipoint tab the acquisition was run from, not the current one.
    loaded = {}
    for name, widget in (("wellplate", win.wellplateMultiPointWidget), ("flexible", win.flexibleMultiPointWidget)):

        def record_load(path, name=name):
            loaded[name] = path
            return True

        monkeypatch.setattr(widget, "_load_acquisition_yaml", record_load)
    flexible = write_acquisition(tmp_path, "flex_2026-11-07_10-00-00.000000", widget_type="flexible")
    win.recordTabWidget.setCurrentWidget(win.wellplateMultiPointWidget)

    gallery.signal_load_settings.emit(str(flexible))

    assert loaded == {"flexible": os.path.join(str(flexible), "acquisition.yaml")}
    assert win.recordTabWidget.currentWidget() is win.flexibleMultiPointWidget
    assert warnings == []

    monkeypatch.setattr(win.multipointController, "acquisition_in_progress", lambda: True)
    assert win._load_acquisition_settings_from_folder(str(flexible)) is False
    assert [title for title, _text in warnings] == ["Acquisition Running"]
    assert len(loaded) == 1


def _preview_timing_gui(qtbot, monkeypatch, tmp_path):
    """A simulated GUI whose recorder records make_preview calls, with an overview save expected and a 500 ms wait;
    the controller's own history_entry_id reads None (a short run has already cleared it)."""
    monkeypatch.setenv("SQUID_ACQUISITION_HISTORY_DIR", str(tmp_path / "history"))
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    calls = []
    monkeypatch.setattr(
        win.historyRecorder,
        "make_preview",
        lambda entry_id, folder, replace=False: calls.append((entry_id, folder, replace)),
    )
    win.GALLERY_PREVIEW_WAIT_MS = 500
    monkeypatch.setattr(control._def, "SAVE_DOWNSAMPLED_OVERVIEW", True)
    mosaic = win.unifiedMosaicWidget
    assert mosaic is not None
    monkeypatch.setattr(mosaic, "layers_initialized", True)
    monkeypatch.setattr(mosaic, "save_for_timepoint", lambda time_point: None)  # the saves are emitted by hand
    monkeypatch.setattr(type(win.multipointController), "history_entry_id", property(lambda self: None))
    return win, calls


def test_preview_waits_for_the_final_overview_save(qtbot, confirm_exit_yes, monkeypatch, tmp_path):
    """The preview is made from the last timepoint's mosaic overview once it is written, or after a timeout (then
    remade when the overview lands); without an overview to wait for it is made when the acquisition ends."""
    import os

    win, calls = _preview_timing_gui(qtbot, monkeypatch, tmp_path)
    mpc = win.multipointController
    mosaic = win.unifiedMosaicWidget

    def folder(entry_id):
        return str(tmp_path / f"run_{entry_id}")

    def start(entry_id):
        mpc.signal_acquisition_start.emit()
        mpc.signal_acquisition_save_target.emit(folder(entry_id))
        mpc.signal_history_run_started.emit(entry_id, folder(entry_id))

    def saved(entry_id, time_point):
        mosaic.signal_view_saved.emit(os.path.join(folder(entry_id), str(time_point), "mosaic_view"))

    # (a) The final save comes after the end; an earlier timepoint's late save is not the one awaited.
    start("e1")
    mpc.timepoint_finished.emit(0)
    mpc.timepoint_finished.emit(1)
    mpc.acquisition_finished.emit()
    assert calls == []
    saved("e1", 0)
    assert calls == []
    saved("e1", 1)
    assert calls == [("e1", folder("e1"), False)]
    qtbot.wait(700)
    assert calls == [("e1", folder("e1"), False)]

    # (b) The overview never reports: the preview is made anyway once the wait is over.
    calls.clear()
    start("e2")
    mpc.timepoint_finished.emit(0)
    mpc.acquisition_finished.emit()
    assert calls == []
    qtbot.waitUntil(lambda: calls == [("e2", folder("e2"), False)], timeout=1000)
    qtbot.wait(800)  # past the second wait too
    assert calls == [("e2", folder("e2"), False)]
    saved("e2", 0)  # too late to be waited for
    assert calls == [("e2", folder("e2"), False)]

    # (c) The overview lands after the timeout: the provisional preview is replaced.
    calls.clear()
    start("e3")
    mpc.timepoint_finished.emit(0)
    mpc.acquisition_finished.emit()
    qtbot.waitUntil(lambda: calls == [("e3", folder("e3"), False)], timeout=1000)
    saved("e3", 0)
    assert calls == [("e3", folder("e3"), False), ("e3", folder("e3"), True)]
    saved("e3", 0)
    assert len(calls) == 2

    # (d) The overview is written before the acquisition ends: the preview is made at the end, without waiting.
    calls.clear()
    start("e4")
    mpc.timepoint_finished.emit(0)
    saved("e4", 0)
    assert calls == []
    mpc.acquisition_finished.emit()
    assert calls == [("e4", folder("e4"), False)]
    qtbot.wait(700)
    assert calls == [("e4", folder("e4"), False)]

    # (e) No overview expected: the preview is made when the acquisition ends.
    calls.clear()
    monkeypatch.setattr(mosaic, "layers_initialized", False)
    start("e5")
    mpc.timepoint_finished.emit(0)
    mpc.acquisition_finished.emit()
    assert calls == [("e5", folder("e5"), False)]
    qtbot.wait(700)
    assert calls == [("e5", folder("e5"), False)]


def test_a_back_to_back_start_keeps_the_previous_runs_preview(qtbot, confirm_exit_yes, monkeypatch, tmp_path):
    """Run N still waiting for its last overview save when run N+1 starts gets its preview: on its save (even one
    landing during run N+1), or on its timeout and then replaced by the late save."""
    import os

    win, calls = _preview_timing_gui(qtbot, monkeypatch, tmp_path)
    mpc = win.multipointController
    mosaic = win.unifiedMosaicWidget

    def run(entry_id):
        folder = str(tmp_path / f"run_{entry_id}")
        mpc.signal_acquisition_start.emit()
        mpc.signal_history_run_started.emit(entry_id, folder)
        mpc.timepoint_finished.emit(0)
        return folder

    def saved(folder):
        mosaic.signal_view_saved.emit(os.path.join(folder, "0", "mosaic_view"))

    # Its save lands while the next run is going.
    f6 = run("e6")
    mpc.acquisition_finished.emit()
    f7 = run("e7")
    assert calls == []
    saved(f6)
    assert calls == [("e6", f6, False)]

    # The next run's own wait is unaffected; its save never comes, so its timeout makes it.
    mpc.acquisition_finished.emit()
    f8 = run("e8")
    qtbot.waitUntil(lambda: ("e7", f7, False) in calls, timeout=1000)
    # ... and the save landing during the run after it still replaces the provisional picture.
    saved(f7)
    assert calls == [("e6", f6, False), ("e7", f7, False), ("e7", f7, True)]
    mpc.acquisition_finished.emit()
    saved(f8)
    assert calls[-1] == ("e8", f8, False) and len(calls) == 4


def test_the_preview_uses_the_history_id_captured_at_start(qtbot, confirm_exit_yes, monkeypatch, tmp_path):
    """A TCP-started run can be over (and the controller's id cleared) before a queued GUI slot runs: the id travels
    with signal_history_run_started instead."""
    win, calls = _preview_timing_gui(qtbot, monkeypatch, tmp_path)
    monkeypatch.setattr(win.unifiedMosaicWidget, "layers_initialized", False)
    mpc = win.multipointController
    folder = str(tmp_path / "run_e9")
    assert mpc.history_entry_id is None

    mpc.signal_history_run_started.emit("e9", folder)
    mpc.signal_acquisition_start.emit()
    mpc.timepoint_finished.emit(0)
    mpc.acquisition_finished.emit()

    assert calls == [("e9", folder, False)]


def test_previews_wait_for_the_running_acquisition_and_flush_when_it_ends(
    qtbot, confirm_exit_yes, monkeypatch, tmp_path
):
    """No preview work during a run: the recorder queues requests from acquisition start, and the end of the run
    submits them, the finished run's own one included."""
    monkeypatch.setenv("SQUID_ACQUISITION_HISTORY_DIR", str(tmp_path / "history"))
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    submitted = []
    monkeypatch.setattr(win.historyRecorder, "_submit", lambda entry_id, folder: submitted.append((entry_id, folder)))
    monkeypatch.setattr(win.unifiedMosaicWidget, "layers_initialized", False)  # no overview save to wait for
    mpc = win.multipointController
    folder_a, folder_b = str(tmp_path / "run_a"), str(tmp_path / "run_b")

    mpc.signal_history_run_started.emit("a", folder_a)
    mpc.signal_acquisition_start.emit()
    win.historyRecorder.make_preview("b", folder_b)  # e.g. an earlier run's late overview save landing now
    assert submitted == []
    mpc.acquisition_finished.emit()

    assert sorted(submitted) == [("a", folder_a), ("b", folder_b)]
