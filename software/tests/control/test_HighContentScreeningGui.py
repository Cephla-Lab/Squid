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


def _offset_calibration_for(win, dx_um, dy_um, dz_um):
    """A valid offset calibration for this simulated machine: its first two objectives, the current mountings
    and camera key, so only the offsets differ between two calls."""
    from control.core.objective_store import current_mountings
    from control.models.objective_calibration_config import (
        ObjectiveCalibrationConfig,
        ObjectiveRecords,
        OffsetCalibrationSection,
        OffsetRecord,
    )

    names = list(win.objectiveStore.objectives_dict)
    reference, other = names[0], names[1]
    mountings = current_mountings(names)
    section = OffsetCalibrationSection(
        reference_objective=reference,
        reference_mounting=mountings[reference],
        measured_at="2026-10-09T10:00:00",
        channel="BF",
        cycles=3,
        camera_key=win._objective_offset_camera_key(),
    )
    record = OffsetRecord(
        mounting=mountings[other],
        dx_um=dx_um,
        dy_um=dy_um,
        dz_um=dz_um,
        match_score=0.9,
        runner_up_ratio=0.4,
        focus_peak_rise=3.0,
    )
    return (
        ObjectiveCalibrationConfig(offset_calibration=section, objectives={other: ObjectiveRecords(offset=record)}),
        other,
    )


def test_xy_offset_change_clears_mosaic_and_live_regions_with_tab_inactive_in_performance_mode(
    qtbot, confirm_exit_yes, monkeypatch
):
    """Spec C §7.3 / §9 "Calibration change": after a save that changes the effective XY offsets, the mosaic's
    image layers and Manual ROI shapes are gone, shapes_mm is empty and no live-drawn FOVs remain, while well
    regions and an imported "Manual" region stay. Both named regression cases at once: the wellplate tab is
    not current, and performance mode has disconnected the napari connections."""
    import numpy as np

    from control.widgets_mosaic import MANUAL_ROI_LAYER, DisplayMode

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    mosaic, wellplate, scan = win.unifiedMosaicWidget, win.wellplateMultiPointWidget, win.scanCoordinates
    assert mosaic is not None and win.objectiveStore.offset_validity.z is False

    win.stage.move_x_to(30.0)  # well inside the XY limits, so the manual region's FOVs are kept
    win.stage.move_y_to(30.0)
    pos = win.stage.get_pos()
    # What the running app would have done by now: the movement updater (a 100 ms timer that has not fired
    # here) drew the current FOV, and an acquisition fixed the display dtype before its first tile.
    win.navigationViewer.draw_fov_current_location(pos)
    win.contrastManager.acquisition_dtype = np.uint16

    config, other = _offset_calibration_for(win, dx_um=12.0, dy_um=-7.0, dz_um=0.0)
    assert win.set_objective_offset_calibration(config).xy_offsets
    assert win.objectiveStore.offset_validity.xy

    # A tile and a drawn shape on the Full View, the shape turned into live-drawn FOVs for the current objective
    win.recordTabWidget.setCurrentWidget(wellplate)
    mosaic.mode = DisplayMode.MOSAIC
    wellplate.checkbox_xy.setChecked(True)  # the cached widget settings may have it off
    mosaic.updateTile(
        control.gui_hcs.MosaicTileUpdate(
            image=np.full((64, 64), 1000, dtype=np.uint16),
            x_mm=pos.x_mm,
            y_mm=pos.y_mm,
            channel_name="BF",
            objective=win.objectiveStore.current_objective,
            pixel_size_um=win.objectiveStore.get_pixel_size_factor() * win.camera.get_pixel_size_binned_um(),
        )
    )
    mosaic.enable_shape_drawing(True)
    assert MANUAL_ROI_LAYER in mosaic.viewer.layers
    half = 4.0  # larger than half a FOV at this objective and binning, so the FOV grid keeps a point
    square = np.array(
        [
            [pos.x_mm - half, pos.y_mm - half],
            [pos.x_mm + half, pos.y_mm - half],
            [pos.x_mm + half, pos.y_mm + half],
            [pos.x_mm - half, pos.y_mm + half],
        ]
    )
    wellplate.combobox_xy_mode.setCurrentText("Manual")
    wellplate.update_manual_shape([square])
    assert wellplate.shapes_mm is not None and "manual" in scan.region_centers
    assert scan.live_drawn_region_ids == {"manual"}
    # A well region and an imported "Manual" plan next to it
    scan.add_region("A1", pos.x_mm, pos.y_mm, 1.0, 10, "Square")
    scan.add_region_from_fovs("manual0", [(pos.x_mm, pos.y_mm), (pos.x_mm + 0.2, pos.y_mm)], shape="Manual")

    # The two regression cases: another tab is current, and performance mode is on
    win.recordTabWidget.setCurrentWidget(win.flexibleMultiPointWidget)
    assert win.recordTabWidget.currentWidget() is not wellplate
    win.performanceModeToggle.setChecked(True)
    win.togglePerformanceMode()
    assert win.performance_mode
    # onTabChanged rebuilt the plan for the flexible tab; put the live-drawn region back as the mosaic path leaves it
    scan.set_manual_coordinates([square], overlap_percent=10)
    scan.add_region("A1", pos.x_mm, pos.y_mm, 1.0, 10, "Square")
    scan.add_region_from_fovs("manual0", [(pos.x_mm, pos.y_mm), (pos.x_mm + 0.2, pos.y_mm)], shape="Manual")
    wellplate.shapes_mm = [square]
    assert "BF" in mosaic.viewer.layers and MANUAL_ROI_LAYER in mosaic.viewer.layers

    config, _ = _offset_calibration_for(win, dx_um=20.0, dy_um=-7.0, dz_um=0.0)  # a different XY offset
    change = win.set_objective_offset_calibration(config)
    assert change.xy_offsets

    assert "BF" not in mosaic.viewer.layers and MANUAL_ROI_LAYER not in mosaic.viewer.layers
    assert mosaic.shapes_mm == [] and not mosaic.layers_initialized
    assert wellplate.shapes_mm is None
    assert scan.live_drawn_region_ids == set()
    assert set(scan.region_centers) == {"A1", "manual0"}  # the well region and the imported plan survive

    # A Z-only change clears nothing
    scan.set_manual_coordinates([square], overlap_percent=10)
    config, _ = _offset_calibration_for(win, dx_um=20.0, dy_um=-7.0, dz_um=5.0)
    change = win.set_objective_offset_calibration(config)
    assert change.z_offsets and not change.xy_offsets
    assert "manual" in scan.region_centers


def test_startup_warns_once_when_the_saved_offsets_are_not_applied(qtbot, confirm_exit_yes, monkeypatch):
    """Spec C §4 / §9 "Validity": a recorded mounting that differs from the configuration applies nothing
    and warns at startup; a valid one is silent."""
    from control.models.objective_calibration_config import Mounting

    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append((title, text)))
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    assert shown == []  # no calibration file: nothing to warn about

    config, other = _offset_calibration_for(win, dx_um=12.0, dy_um=-7.0, dz_um=6.0)
    config.objectives[other].offset.mounting = Mounting(changer="nimotion_turret", position=2)  # not this machine's
    monkeypatch.setattr(scope.config_repo, "get_objective_calibration", lambda: config)
    win.load_objective_offset_calibration()
    assert len(shown) == 1 and "not applied" in shown[0][1] and "remounted" in shown[0][1]
    assert not win.objectiveStore.offset_validity.z
    assert win.objectiveStore.z_switch_step_mm(list(win.objectiveStore.objectives_dict)[0], other) == 0.0
