import pytest

import control._def

import control.gui_hcs
from qtpy.QtWidgets import QMessageBox

import control.microscope


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


# ---------------------------------------------------------------- pixel-size calibration changed (spec B §4.6)


def _calls(monkeypatch, obj, name):
    """Replace obj.name with a recorder; returns the list of call argument tuples. For a slot that
    make_connections binds directly (a bound method), patch the class before construction instead."""
    calls = []
    monkeypatch.setattr(obj, name, lambda *args, **kwargs: calls.append(args))
    return calls


def _redraw_fov_calls(monkeypatch):
    """NavigationViewer.redraw_fov is connected as a bound method, so it is recorded at the class, before
    the window exists. It also spares the real redraw, which needs a stage position the fresh GUI lacks."""
    from control.core.core import NavigationViewer

    calls = []
    monkeypatch.setattr(NavigationViewer, "redraw_fov", lambda self: calls.append(()))
    return calls


def _pixel_size_change():
    from control.core.objective_store import CalibrationChange

    return CalibrationChange(quantities=frozenset({"pixel_size"}), validity_flipped=True)


def test_pixel_size_change_refreshes_the_fov_listeners_and_nothing_else(qtbot, monkeypatch, confirm_exit_yes):
    """signal_fov_size_changed reaches the navigation FOV and the current acquisition tab's planning,
    marks the inactive tab dirty, clears the mosaic tiles but keeps drawn shapes, and does not reset
    laser AF or re-apply live mode (what signal_objective_changed would do). Tracking's listener is
    covered in test_widgets.py: with ENABLE_TRACKING the GUI does not construct (load_objects builds
    the TrackingController with imageDisplayWindow before that attribute exists)."""
    import numpy as np

    from control.widgets_mosaic import MANUAL_ROI_LAYER

    redraws = _redraw_fov_calls(monkeypatch)
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    assert win.laserAutofocusController is not None

    # the flexible tab is current: the wellplate tab is the inactive one, in Select Wells mode
    win.wellplateMultiPointWidget.combobox_xy_mode.setCurrentText("Select Wells")
    win.recordTabWidget.setCurrentWidget(win.flexibleMultiPointWidget)
    win.onTabChanged(win.recordTabWidget.currentIndex())
    assert not win.scanCoordinates.has_regions()

    redraws.clear()
    laser_af_resets = _calls(monkeypatch, win.laserAutofocusController, "on_settings_changed")
    live_mode_reapplied = _calls(monkeypatch, win.liveControlWidget, "select_new_microscope_mode_by_name")
    coverage_refreshed = _calls(monkeypatch, win.wellplateMultiPointWidget, "update_coverage_from_scan_size")

    mosaic = win.unifiedMosaicWidget
    assert mosaic is not None
    mosaic.viewer.add_image(np.zeros((8, 8), dtype=np.uint16), name="BF")
    mosaic.viewer.add_shapes([[[0, 0], [0, 1], [1, 1], [1, 0]]], shape_type="polygon", name=MANUAL_ROI_LAYER)
    mosaic.layers_initialized = True

    win._on_calibration_changed(_pixel_size_change())

    assert len(redraws) == 1
    assert laser_af_resets == [] and live_mode_reapplied == []
    assert [layer.name for layer in mosaic.viewer.layers] == [MANUAL_ROI_LAYER]
    assert mosaic.layers_initialized is False
    # the inactive wellplate tab is dirty and did not refresh or write the shared coordinates
    assert win.wellplateMultiPointWidget in win._fov_size_dirty_tabs
    assert win.flexibleMultiPointWidget not in win._fov_size_dirty_tabs
    assert coverage_refreshed == []
    assert not win.scanCoordinates.has_regions()

    # activation refreshes the dirty tab's coverage (Select Wells) as well as its grid: once, although
    # setCurrentWidget already ran onTabChanged through currentChanged before the explicit call
    win.recordTabWidget.setCurrentWidget(win.wellplateMultiPointWidget)
    win.onTabChanged(win.recordTabWidget.currentIndex())
    assert win.wellplateMultiPointWidget not in win._fov_size_dirty_tabs
    assert len(coverage_refreshed) == 1

    # with the wellplate tab current, the same change refreshes it immediately and dirties the other one
    win._on_calibration_changed(_pixel_size_change())
    assert len(coverage_refreshed) == 2
    assert win.flexibleMultiPointWidget in win._fov_size_dirty_tabs
    assert win.wellplateMultiPointWidget not in win._fov_size_dirty_tabs


def test_a_notification_without_pixel_size_leaves_the_fov_alone(qtbot, monkeypatch, confirm_exit_yes):
    from control.core.objective_store import CalibrationChange

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    emitted = []
    win.signal_fov_size_changed.connect(lambda: emitted.append(True))
    win._on_calibration_changed(CalibrationChange(quantities=frozenset({"xy_offsets"}), validity_flipped=True))
    win._on_calibration_changed(CalibrationChange(quantities=frozenset(), validity_flipped=False))
    assert emitted == []


def test_camera_settings_changes_recheck_the_calibration_validity(qtbot, monkeypatch, confirm_exit_yes):
    """Spec B §4.4 trigger. The factor is binning-free, so a binning change is a no-op for the FOV
    listeners unless validity flipped."""
    _redraw_fov_calls(monkeypatch)  # signal_binning_changed also redraws the FOV, which needs a position
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    refreshes = []
    monkeypatch.setattr(win.objectiveStore, "refresh_validity", lambda: refreshes.append(True) or _pixel_size_change())
    routed = _calls(monkeypatch, win, "_on_calibration_changed")
    win.cameraSettingWidget.signal_binning_changed.emit()
    assert len(refreshes) == 1 and len(routed) == 1


def test_startup_warns_once_about_an_invalid_saved_calibration(qtbot, monkeypatch, confirm_exit_yes):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: warnings.append((title, text)))
    scope = control.microscope.Microscope.build_from_global_config(True)
    monkeypatch.setattr(
        scope.objective_store,
        "calibration_warnings",
        lambda: ["20x: nominal pixel size in use (camera changed (old -> new))"],
    )
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    assert len(warnings) == 1
    assert warnings[0][0] == "Objective Calibration" and "20x: nominal pixel size in use" in warnings[0][1]


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
