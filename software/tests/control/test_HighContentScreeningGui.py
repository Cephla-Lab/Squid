import threading
import time
import unittest.mock

import pytest

import control._def

import control.gui_hcs
import control.widgets
from qtpy.QtCore import QCoreApplication, QEvent
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


def test_objective_calibration_refuses_during_an_objective_switch_or_autofocus(qtbot, confirm_exit_yes):
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    assert win.objective_calibration_busy_reason() is None
    win.objectivesWidget.dropdown.setEnabled(False)  # ObjectivesWidget's guard while a switch runs
    assert "objective switch" in win.objective_calibration_busy_reason()
    win.objectivesWidget.dropdown.setEnabled(True)
    win.autofocusController.autofocus_in_progress = True
    assert "Autofocus" in win.objective_calibration_busy_reason()


def test_objective_calibration_does_not_open_during_an_objective_switch(qtbot, confirm_exit_yes, monkeypatch):
    """The adapter snapshots the objective when it is built: opened mid-switch, the snapshot would be
    the old objective, and a later run could skip a switch it needs. Refuse before building it."""
    import control.widgets_objective_calibration as woc

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    shown, opened = [], []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(text))
    monkeypatch.setattr(woc.ObjectiveCalibrationDialog, "exec_", lambda self: opened.append(self) or 0)
    win.objectivesWidget.dropdown.setEnabled(False)  # a switch is running on the helper thread
    win.openObjectiveCalibration()
    assert opened == [] and "objective switch" in shown[0]
    win.objectivesWidget.dropdown.setEnabled(True)
    win.openObjectiveCalibration()
    assert len(opened) == 1


def test_a_simulated_calibration_never_touches_the_machine_calibration_file(qtbot, confirm_exit_yes, monkeypatch):
    """Under --simulation the dialog saves its synthetic calibration apart from machine_configs/: a fake
    camera key and pixel sizes saved there would replace the real machine's calibration."""
    import control.widgets_objective_calibration as woc

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    opened = []
    monkeypatch.setattr(woc.ObjectiveCalibrationDialog, "exec_", lambda self: opened.append(self) or 0)
    win.openObjectiveCalibration()
    repo = opened[0].config_repo
    assert repo.machine_configs_path != scope.config_repo.machine_configs_path
    assert repo.machine_configs_path.parts[-3:] == ("cache", "simulation", "machine_configs")
