"""Tests for RecordZStackMultiPointWidget, the "Time at XY" tab.

The tab is one channel table (per row: exposure / gain / illumination, camera
settings, FPS / duration, and the Zi / R / Zf flags), one z block shared by
every phase, a record-plane picker and a focus row.  The business rules live in
``control.core.record_zstack_controller`` (covered without Qt in
``tests/core/test_record_zstack_model.py``); these tests check that the widget
reads and writes its controls correctly and delegates to those rules.

The widget is built on lightweight stubs with qtbot (pytest-qt manages the
QApplication).  ``_make_stub_controller`` and ``simulated_widget_deps`` are also
imported by ``tests/test_record_zstack_load_coordinates.py``.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from qtpy.QtWidgets import QMessageBox

from control.core.camera_geometry import CameraGeometry
from control.core.record_zstack_controller import FrameRateTestResult

BF = "BF LED matrix full"
FL = "Fluorescence 488 nm Ex"
FIXTURES = Path(__file__).parent / "files" / "record_zstack"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_channel(name: str = BF, exposure: float = 50.0, gain: float = 0.0, intensity: float = 50.0):
    """Return a minimal AcquisitionChannel with the given name."""
    from control.models.acquisition_config import AcquisitionChannel, CameraSettings, IlluminationSettings

    return AcquisitionChannel(
        name=name,
        camera_settings=CameraSettings(exposure_time_ms=exposure, gain_mode=gain),
        illumination_settings=IlluminationSettings(intensity=intensity),
    )


def _make_stub_live_controller():
    """Stub liveController whose get_channels() returns two fake AcquisitionChannels."""
    ctrl = MagicMock()
    ctrl.get_channels.return_value = [_make_channel(BF), _make_channel(FL)]
    ctrl.currentConfiguration = _make_channel(BF)
    return ctrl


def _make_stub_objective_store():
    store = MagicMock()
    store.current_objective = "10x"
    return store


def _make_stub_scan_coordinates():
    """Stub scanCoordinates reporting 1 selected well."""
    sc = MagicMock()
    sc.get_selected_wells.return_value = ["A1"]
    sc.region_centers = {}
    sc.region_shapes = {}
    return sc


def _make_stub_controller():
    """Stub RecordZStackController that records calls and reports not-in-progress.

    It has no frame_rate_test_finished signal (like the bare, Qt-free controller), so the widget
    hands the frame-rate test its own handler instead of a signal's emit.
    """
    ctrl = MagicMock()
    ctrl.acquisition_in_progress.return_value = False
    ctrl.frame_rate_test_finished = None
    return ctrl


@pytest.fixture
def simulated_widget_deps(tmp_path):
    """Provide lightweight stub dependencies for RecordZStackMultiPointWidget."""
    stage = MagicMock()
    stage.get_pos.return_value = MagicMock(x_mm=1.5, y_mm=2.5, z_mm=0.0)

    return dict(
        stage=stage,
        navigationViewer=MagicMock(),
        recordZStackController=_make_stub_controller(),
        liveController=_make_stub_live_controller(),
        objectiveStore=_make_stub_objective_store(),
        scanCoordinates=_make_stub_scan_coordinates(),
        well_selection_widget=None,
        tab_widget=None,
    )


def _widget(qtbot, deps):
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**deps)
    qtbot.addWidget(w)
    return w


def _make_valid_widget(qtbot, deps, *, zi=False, r=False, zf=True):
    """A widget with a base path and one BF row (a final z-stack only, unless flags say otherwise)."""
    w = _widget(qtbot, deps)
    w.lineEdit_savingDir.setText("/tmp/test_time_at_xy")
    w._add_channel_row(BF, zi=zi, r=r, zf=zf)
    return w


def _answer(reply, captured=None):
    """A QMessageBox.question stand-in that records what was asked and answers *reply*."""

    def fake_question(parent, title, text, *args, **kwargs):
        if captured is not None:
            captured.update(title=title, text=text)
        return reply

    return patch("control.widgets.QMessageBox.question", side_effect=fake_question)


def _harden_objective_info(w):
    """Give _build_objective_info() YAML-serialisable values instead of MagicMock leaves."""
    w.objectiveStore.objectives_dict = {"10x": {"magnification": 10.0}}
    w.objectiveStore.current_objective = "10x"
    w.objectiveStore.get_pixel_size_factor.return_value = 1.0
    camera = MagicMock()
    camera.get_binning.return_value = (1, 1)
    camera.get_pixel_size_binned_um.return_value = 0.5
    camera.get_max_frame_rate.return_value = 1000.0
    w.liveController.camera = camera
    return camera


# ---------------------------------------------------------------------------
# Channel table
# ---------------------------------------------------------------------------


def test_table_has_spencers_columns(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    headers = [w.channel_table.horizontalHeaderItem(c).text() for c in range(w.channel_table.columnCount())]
    assert headers == ["Channel", "Exp", "Gain", "Ill%", "Cam", "FPS", "Dur", "Zi", "R", "Zf", ""]
    assert w.channel_table.rowCount() == 0


def test_add_channel_seeds_the_row_from_the_channel_config_and_defaults_to_r_only(qtbot, simulated_widget_deps):
    simulated_widget_deps["liveController"].get_channels.return_value = [
        _make_channel(BF, 12.0, 3.0, 20.0),
        _make_channel(FL, 200.0, 10.0, 80.0),
    ]
    w = _widget(qtbot, simulated_widget_deps)
    w.combobox_add_channel.setCurrentText(FL)
    w.btn_add_channel.click()

    assert w._channel_row_names == [FL]
    v = w._get_row_values(FL)
    assert (v["exposure"], v["gain"], v["illumination"]) == pytest.approx((200.0, 10.0, 80.0))
    assert (v["zi"], v["r"], v["zf"]) == (False, True, False)
    assert (v["fps"], v["duration_s"]) == (10.0, 1.0)
    assert v["geometry"] == CameraGeometry()
    assert w.channel_table.cellWidget(0, w.COL_CAM).text() == "—"


def test_add_channel_row_deduplicates_and_shows_the_full_name_as_tooltip(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w._add_channel_row(BF)
    w._add_channel_row(BF)
    assert w._channel_row_names == [BF]
    assert w.channel_table.item(0, w.COL_NAME).toolTip() == BF


def test_remove_button_removes_the_row(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w._add_channel_row(BF)
    w._add_channel_row(FL)
    w.channel_table.cellWidget(0, w.COL_ACTIONS).click()
    assert w._channel_row_names == [FL]


def test_fps_and_duration_are_only_editable_for_a_recording_row(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w._add_channel_row(BF, r=False, zf=True)
    assert not w.channel_table.cellWidget(0, w.COL_FPS).isEnabled()
    assert not w.channel_table.cellWidget(0, w.COL_DUR).isEnabled()
    w._set_row_values(BF, r=True)
    assert w.channel_table.cellWidget(0, w.COL_FPS).isEnabled()
    assert w.channel_table.cellWidget(0, w.COL_DUR).isEnabled()


def test_start_button_needs_a_row_with_a_phase_ticked(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    assert not w.btn_startAcquisition.isEnabled()
    w._add_channel_row(BF, r=False)
    assert not w.btn_startAcquisition.isEnabled()
    w._set_row_values(BF, zi=True)
    assert w.btn_startAcquisition.isEnabled()
    w._remove_channel_row(BF)
    assert not w.btn_startAcquisition.isEnabled()


def test_exposure_editors_cannot_hold_zero(qtbot, simulated_widget_deps):
    """A 0 ms exposure is rejected by the camera mid-run; the editor's floor is the smallest value it can show."""
    w = _widget(qtbot, simulated_widget_deps)
    w._add_channel_row(BF)
    w._set_row_values(BF, exposure=0.0)
    assert w._get_row_values(BF)["exposure"] == pytest.approx(0.1)


def test_build_parameters_reads_every_row_in_table_order(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w.lineEdit_savingDir.setText("/tmp/x")
    w._add_channel_row(FL, 80.0, 0.5, 40.0, fps=25, duration_s=3, zi=True, r=True, zf=False)
    w._add_channel_row(BF, 5.0, 1.0, 10.0, zi=False, r=False, zf=True, geometry=CameraGeometry(binning=2))
    w._set_row_values(FL, exposure=90.0)

    params = w.build_parameters()

    assert [p.name for p in params.channels] == [FL, BF]
    first, second = params.channels
    assert first.channel.exposure_time == pytest.approx(90.0)  # the inline edit, not the channel config
    assert (first.channel.analog_gain, first.channel.illumination_intensity) == pytest.approx((0.5, 40.0))
    assert (first.fps, first.duration_s, first.zi, first.r, first.zf) == (25.0, 3.0, True, True, False)
    assert second.geometry == CameraGeometry(binning=2)
    assert [c.name for c in params.zi_channels] == [FL]
    assert [p.name for p in params.r_plans] == [FL]
    assert [c.name for c in params.zf_channels] == [BF]
    assert params.experiment_id == "record_zstack"


def test_build_parameters_does_not_mutate_the_channel_config(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    source = w.liveController.get_channels("10x")[0]
    w._add_channel_row(BF, 7.0)
    assert w.build_parameters().channels[0].channel.exposure_time == pytest.approx(7.0)
    assert source.exposure_time == pytest.approx(50.0)


def test_build_parameters_includes_xy_mode_and_scan_settings(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w.entry_scan_size.setValue(2.5)
    w.entry_overlap.setValue(20.0)
    params = w.build_parameters()
    assert (params.xy_mode, params.scan_size_mm, params.overlap_percent) == ("Select Wells", 2.5, 20.0)


def test_camera_cell_shows_the_pinned_settings(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w._add_channel_row(BF, geometry=CameraGeometry(binning=2, crop_w=1024, crop_h=1024, pixel_format="MONO8"))
    button = w.channel_table.cellWidget(0, w.COL_CAM)
    assert button.text() == "2×2 1024² M8"
    assert "binning 2×2" in button.toolTip()


def test_camera_geometry_dialog_round_trips_a_geometry(qtbot):
    from control.widgets import CameraGeometryDialog

    camera = MagicMock()
    camera.get_binning_options.return_value = [(1, 1), (2, 2)]
    camera.get_binning.return_value = (1, 1)
    camera.get_resolution.return_value = (2048, 2048)
    camera.get_available_pixel_formats.return_value = [MagicMock(), MagicMock()]
    camera.get_available_pixel_formats.return_value[0].name = "MONO8"
    camera.get_available_pixel_formats.return_value[1].name = "MONO16"
    geometry = CameraGeometry(binning=2, crop_w=512, crop_h=256, pixel_format="MONO16")
    dialog = CameraGeometryDialog(camera, geometry, BF)
    qtbot.addWidget(dialog)
    assert dialog.geometry() == geometry
    assert "1024 × 1024" in dialog.label_sensor.text()  # full sensor at the selected 2x2 binning
    dialog.btn_all_current.click()
    assert dialog.geometry() == CameraGeometry()


def test_validate_rejects_camera_settings_the_camera_cannot_deliver(qtbot, simulated_widget_deps):
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    camera = MagicMock()
    camera.get_binning_options.return_value = [(1, 1), (2, 2)]
    w.liveController.camera = camera
    w._set_row_values(BF, geometry=CameraGeometry(binning=4))
    assert "does not offer 4×4 binning" in w.validate()


def test_validate_rejects_a_colour_pixel_format_on_a_recording_row(qtbot, simulated_widget_deps):
    w = _make_valid_widget(qtbot, simulated_widget_deps, r=True, zf=False)
    w._set_row_values(BF, geometry=CameraGeometry(pixel_format="BAYER_RG8"))
    assert "recordings need a mono pixel format" in w.validate()


# ---------------------------------------------------------------------------
# Z block and record planes
# ---------------------------------------------------------------------------


def test_from_bottom_is_the_default_mode_and_nz_is_editable(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    assert w.combobox_z_mode.currentText() == "From Bottom"
    assert [w.combobox_z_mode.itemText(i) for i in range(3)] == ["From Bottom", "Set Range", "Relative"]
    assert w.entry_NZ.isEnabled()
    assert w.entry_deltaZ.value() == pytest.approx(1.0)  # no Nyquist default on this tab
    assert w.z_range_frame.isHidden() and w.relative_frame.isHidden()
    w.entry_NZ.setValue(5)
    plan = w._zstack_plan()
    assert (plan.mode, plan.nz, plan.dz_um) == ("From Bottom", 5, 1.0)


def test_relative_mode_derives_an_odd_nz_from_the_range(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w.combobox_z_mode.setCurrentText("Relative")
    assert not w.relative_frame.isHidden() and not w.entry_NZ.isEnabled()
    w.entry_deltaZ.setValue(1.0)
    w.entry_zRange.setValue(6.0)
    assert w.entry_NZ.value() == 7
    assert "3.00" in w.label_actual_range.text()
    w.entry_zRange.setValue(5.0)  # rounded up to the next odd count
    assert w.entry_NZ.value() == 7
    assert w._zstack_plan().z_range_um == pytest.approx(5.0)


def test_set_range_mode_uses_absolute_limits_and_switches_autofocus_off(qtbot, simulated_widget_deps):
    simulated_widget_deps["stage"].get_pos.return_value = MagicMock(x_mm=1.5, y_mm=2.5, z_mm=1.25)
    w = _widget(qtbot, simulated_widget_deps)
    w.checkbox_laser_af.setChecked(True)
    w.checkbox_withAutofocus.setChecked(True)

    w.combobox_z_mode.setCurrentText("Set Range")

    assert not w.z_range_frame.isHidden() and not w.entry_NZ.isEnabled()
    assert not w.checkbox_laser_af.isChecked() and not w.checkbox_laser_af.isEnabled()
    assert not w.checkbox_withAutofocus.isChecked() and not w.checkbox_withAutofocus.isEnabled()
    assert w.entry_minZ.value() == pytest.approx(1250.0) and w.entry_maxZ.value() == pytest.approx(1250.0)

    w.entry_deltaZ.setValue(2.0)
    w.entry_maxZ.setValue(1256.0)
    assert w.entry_NZ.value() == 4
    plan = w._zstack_plan()
    assert (plan.mode, plan.z_min_mm, plan.z_max_mm) == ("Set Range", pytest.approx(1.25), pytest.approx(1.256))

    w.entry_minZ.setValue(1300.0)  # min above max drags max along
    assert w.entry_maxZ.value() == pytest.approx(1300.0)

    w.combobox_z_mode.setCurrentText("From Bottom")
    assert w.checkbox_laser_af.isEnabled() and w.checkbox_withAutofocus.isEnabled()


def test_set_buttons_capture_the_stage_z(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w.combobox_z_mode.setCurrentText("Set Range")
    w.stage.get_pos.return_value = MagicMock(z_mm=2.0)
    w.set_maxZ_button.click()
    w.stage.get_pos.return_value = MagicMock(z_mm=1.9)
    w.set_minZ_button.click()
    assert (w.entry_minZ.value(), w.entry_maxZ.value()) == pytest.approx((1900.0, 2000.0))


def test_record_plane_buttons_start_all_on_and_follow_the_plane_count(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w.entry_NZ.setValue(7)
    assert w.btn_slice_bottom.isChecked() and w.btn_slice_mid.isChecked() and w.btn_slice_top.isChecked()
    assert w.label_slice_summary.text() == "3 of 7 planes"
    assert w._record_slice_selection().resolve(7) == [0, 3, 6]

    w.btn_slice_mid.click()
    assert w.label_slice_summary.text() == "2 of 7 planes"
    assert w._record_slice_selection().resolve(7) == [0, 6]

    w.entry_NZ.setValue(1)  # every preset is the same plane
    assert w.label_slice_summary.text() == "1 of 1 plane"


def test_select_dialog_choice_maps_onto_presets_and_extra_planes(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w.entry_NZ.setValue(7)
    w._apply_slice_choice({0, 2, 3}, 7)
    w._sync_slice_buttons()
    assert w.btn_slice_bottom.isChecked() and w.btn_slice_mid.isChecked() and not w.btn_slice_top.isChecked()
    assert w.btn_slice_select.isChecked()  # an explicit extra plane is selected
    assert w._record_slice_selection().resolve(7) == [0, 2, 3]
    assert w.build_parameters().record_slices.resolve(7) == [0, 2, 3]

    w.entry_NZ.setValue(2)  # plane 2 no longer exists: dropped, the presets stay
    assert w._record_slice_selection().resolve(2) == [0]
    assert not w.btn_slice_select.isChecked()


def test_record_slices_dialog_lists_every_plane(qtbot):
    from control.widgets import RecordSlicesDialog

    dialog = RecordSlicesDialog(["Bottom", "Mid", "Top"], ["-1", "0", "+1"], {0, 2})
    qtbot.addWidget(dialog)
    assert dialog.selected_indices() == {0, 2}
    dialog.set_checked(1, True)
    dialog.set_checked(0, False)
    assert dialog.selected_indices() == {1, 2}


def test_validate_requires_a_record_plane_for_a_recording(qtbot, simulated_widget_deps):
    w = _make_valid_widget(qtbot, simulated_widget_deps, r=True, zf=False)
    assert w.validate() is None
    for button in (w.btn_slice_bottom, w.btn_slice_mid, w.btn_slice_top):
        button.click()
    assert "Pick at least one plane to record" in w.validate()


# ---------------------------------------------------------------------------
# Focus row
# ---------------------------------------------------------------------------


def test_focus_row_has_the_three_multipoint_options(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    assert w.checkbox_withAutofocus.text() == "Contrast AF"
    assert w.checkbox_laser_af.text() == "Laser AF"
    assert w.checkbox_useFocusMap.text() == "Use Focus Map"
    params = w.build_parameters()
    assert (params.use_laser_af, params.use_contrast_af, params.use_focus_map) == (False, False, False)


def test_validate_laser_af_needs_a_reference(qtbot, simulated_widget_deps):
    laser = MagicMock()
    laser.laser_af_properties.has_reference = False
    simulated_widget_deps["laser_autofocus_controller"] = laser
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    w.checkbox_laser_af.setChecked(True)
    assert "no reference position" in w.validate()
    laser.laser_af_properties.has_reference = True
    assert w.validate() is None
    assert w.build_parameters().use_laser_af is True


def test_use_focus_map_passes_the_fitted_surface_and_region_offsets(qtbot, simulated_widget_deps):
    focus_map_widget = MagicMock()
    focus_map_widget.focusMap.is_fitted = True
    focus_map_widget.get_offsets_for_acquisition.return_value = {"A1": 1.5}
    simulated_widget_deps["focus_map_widget"] = focus_map_widget
    laser = MagicMock()
    laser.laser_af_properties.has_reference = True
    simulated_widget_deps["laser_autofocus_controller"] = laser
    w = _make_valid_widget(qtbot, simulated_widget_deps)

    assert w.build_parameters().focus_map is None  # not ticked: nothing from the Focus Map tab is used
    w.checkbox_laser_af.setChecked(True)
    w.checkbox_useFocusMap.setChecked(True)
    focus_map_widget.setEnabled.assert_called_with(True)

    params = w.build_parameters()
    assert params.use_focus_map and params.focus_map is focus_map_widget.focusMap
    assert params.region_laser_af_offsets == {"A1": 1.5}
    # The per-region offsets come from the Focus Map tab's own gating, as on the multipoint tabs.
    focus_map_widget.get_offsets_for_acquisition.assert_called_with(True)
    assert w.validate() is None


def test_validate_use_focus_map_needs_focus_points(qtbot, simulated_widget_deps):
    focus_map_widget = MagicMock()
    focus_map_widget.focusMap.is_fitted = False
    focus_map_widget.focus_points = []
    simulated_widget_deps["focus_map_widget"] = focus_map_widget
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    w.checkbox_useFocusMap.setChecked(True)
    assert "no focus surface is fitted" in w.validate()
    focus_map_widget.focus_points = [("A1", 1.0, 2.0, 3.0)]
    assert w.validate() is None


@pytest.mark.parametrize("interactive", [True, False])
def test_start_is_refused_when_the_focus_surface_cannot_be_fitted(qtbot, simulated_widget_deps, interactive):
    focus_map_widget = MagicMock()
    focus_map_widget.focusMap.is_fitted = True
    focus_map_widget.fit_surface.return_value = False
    simulated_widget_deps["focus_map_widget"] = focus_map_widget
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    w.checkbox_useFocusMap.setChecked(True)
    w.btn_startAcquisition.setChecked(True)

    with patch("control.widgets.QMessageBox.warning") as warn, _answer(QMessageBox.Yes):
        w.toggle_acquisition(True, interactive=interactive)

    assert warn.called is interactive
    assert not w.btn_startAcquisition.isChecked()
    w.recordZStackController.run_acquisition.assert_not_called()


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


def test_validate_requires_base_path(qtbot, simulated_widget_deps):
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    w.lineEdit_savingDir.setText("")
    assert "Base path" in w.validate()
    w.lineEdit_savingDir.setText("/tmp/x")
    assert w.validate() is None


def test_validate_requires_a_ticked_row(qtbot, simulated_widget_deps):
    w = _make_valid_widget(qtbot, simulated_widget_deps, zf=False)
    assert "tick Zi, R, or Zf" in w.validate()


def test_get_selected_well_count(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    sc = simulated_widget_deps["scanCoordinates"]
    sc.get_selected_wells.return_value = None  # glass slide: the current position counts as one
    assert w._get_selected_well_count() == 1
    sc.get_selected_wells.return_value = []
    assert w._get_selected_well_count() == 0
    sc.get_selected_wells.return_value = ["A1", "B2", "C3"]
    assert w._get_selected_well_count() == 3


def test_validate_rejects_no_wells_selected_except_at_the_current_position(qtbot, simulated_widget_deps):
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    simulated_widget_deps["scanCoordinates"].get_selected_wells.return_value = []
    assert "At least one well" in w.validate()
    w.combobox_xy_mode.setCurrentText("Current Position")
    assert w.validate() is None


# ---------------------------------------------------------------------------
# toggle_acquisition
# ---------------------------------------------------------------------------


def test_start_confirms_the_per_fov_plan_and_runs(qtbot, simulated_widget_deps):
    w = _make_valid_widget(qtbot, simulated_widget_deps, zi=True, r=True, zf=True)
    w.combobox_z_mode.setCurrentText("Relative")
    w.entry_zRange.setValue(4.0)
    w._set_row_values(BF, fps=20, duration_s=2)
    captured = {}

    with _answer(QMessageBox.Yes, captured):
        w.toggle_acquisition(True)

    assert captured["title"] == "Confirm Acquisition"
    text = captured["text"]
    assert "Focus: stage z at each position" in text
    assert "Zi: 1 ch × 5 planes (-2.0 … +2.0 µm rel. reference)" in text
    assert "R: 1 recording × 3 of 5 planes" in text and f"{BF} 20 fps × 2 s — 6.0 s/FOV" in text
    assert "Zf: 1 ch × 5 planes" in text
    ctrl = w.recordZStackController
    ctrl.run_acquisition.assert_called_once()
    params = ctrl.run_acquisition.call_args.args[0]
    assert params.base_path == "/tmp/test_time_at_xy" and params.zstack.mode == "Relative"
    assert [p.name for p in params.r_plans] == [BF]


def test_answering_no_does_not_start(qtbot, simulated_widget_deps):
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    w.btn_startAcquisition.setChecked(True)
    started = []
    w.signal_acquisition_started.connect(started.append)
    with _answer(QMessageBox.No):
        w.toggle_acquisition(True)
    w.recordZStackController.run_acquisition.assert_not_called()
    assert not w.btn_startAcquisition.isChecked() and started == []


def test_invalid_start_shows_a_warning_and_does_not_run(qtbot, simulated_widget_deps):
    w = _make_valid_widget(qtbot, simulated_widget_deps, zf=False)  # nothing ticked
    w.btn_startAcquisition.setChecked(True)
    started = []
    w.signal_acquisition_started.connect(started.append)
    with patch("control.widgets.QMessageBox.warning") as warn:
        w.toggle_acquisition(True)
    warn.assert_called_once()
    assert warn.call_args[0][1] == "Invalid Parameters"
    w.recordZStackController.run_acquisition.assert_not_called()
    assert not w.btn_startAcquisition.isChecked() and started == []


def test_start_is_refused_while_an_acquisition_is_running(qtbot, simulated_widget_deps):
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    w.recordZStackController.acquisition_in_progress.return_value = True
    w.btn_startAcquisition.setChecked(True)
    w.toggle_acquisition(True)
    w.recordZStackController.run_acquisition.assert_not_called()
    assert not w.btn_startAcquisition.isChecked()


def test_stop_requests_an_abort(qtbot, simulated_widget_deps):
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    w.toggle_acquisition(False)
    w.recordZStackController.request_abort.assert_called_once()


def test_started_signal_is_emitted_before_the_worker_can_finish(qtbot, simulated_widget_deps):
    """A one-frame acquisition could finish before toggle_acquisition returns; True must already be out."""
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    events = []
    w.signal_acquisition_started.connect(lambda started: events.append(("signal", started)))
    w.recordZStackController.run_acquisition.side_effect = lambda params: events.append(("run", None))
    with _answer(QMessageBox.Yes):
        w.toggle_acquisition(True)
    assert events == [("signal", True), ("run", None)]

    w.acquisition_is_finished()
    assert events[-1] == ("signal", False)
    assert not w.btn_startAcquisition.isChecked()


def test_started_signal_is_withdrawn_when_run_acquisition_raises(qtbot, simulated_widget_deps):
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    w.btn_startAcquisition.setChecked(True)
    w.recordZStackController.run_acquisition.side_effect = RuntimeError("boom")
    events = []
    w.signal_acquisition_started.connect(events.append)
    with _answer(QMessageBox.Yes):
        w.toggle_acquisition(True)
    assert events == [True, False]
    assert not w.btn_startAcquisition.isChecked()


def test_start_rebuilds_the_scan_regions_before_running(qtbot, simulated_widget_deps):
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    sc = simulated_widget_deps["scanCoordinates"]
    sc.reset_mock()
    order = []
    sc.set_well_coordinates.side_effect = lambda *a: order.append("regions")
    w.recordZStackController.run_acquisition.side_effect = lambda params: order.append("run")
    with _answer(QMessageBox.Yes):
        w.toggle_acquisition(True)
    assert order == ["regions", "run"]


def test_confirmation_warns_when_the_camera_cannot_reach_a_rows_fps(qtbot, simulated_widget_deps):
    """The camera's own max-rate estimate for a recording row's exposure goes into the confirmation
    dialog when the user asked for more, with the rate and frame count the run will really use."""
    w = _make_valid_widget(qtbot, simulated_widget_deps, r=True, zf=False)
    w._add_channel_row(FL, 5.0, r=True)
    camera = MagicMock()
    camera.get_max_frame_rate.side_effect = lambda exposure_ms: 28.0 if exposure_ms == 50.0 else 500.0
    w.liveController.camera = camera
    w._set_row_values(BF, fps=100, duration_s=3)
    w._set_row_values(FL, fps=100, duration_s=3)
    captured = {}

    with _answer(QMessageBox.Yes, captured):
        w.toggle_acquisition(True)

    assert f"Camera limit ({BF})" in captured["text"]
    assert "at most 28.0 fps, not 100" in captured["text"]
    assert "28.0 fps (84 frames for 3 s)" in captured["text"]
    assert f"Camera limit ({FL})" not in captured["text"]  # 100 fps is within its 500 fps limit
    w.recordZStackController.run_acquisition.assert_called_once()


def test_confirmation_has_no_limit_note_for_a_row_with_its_own_camera_settings(qtbot, simulated_widget_deps):
    """The camera can only answer for its current binning / ROI, not for the ones a row pins."""
    w = _make_valid_widget(qtbot, simulated_widget_deps, r=True, zf=False)
    camera = MagicMock()
    camera.get_max_frame_rate.return_value = 28.0
    camera.get_binning_options.return_value = [(1, 1), (2, 2)]
    w.liveController.camera = camera
    w._set_row_values(BF, fps=100, geometry=CameraGeometry(binning=2))
    captured = {}
    with _answer(QMessageBox.Yes, captured):
        w.toggle_acquisition(True)
    assert "Camera limit" not in captured["text"]
    assert "[binning 2×2, crop current, format current]" in captured["text"]


# ---------------------------------------------------------------------------
# Test FR
# ---------------------------------------------------------------------------


def test_frame_rate_test_uses_the_rows_settings_and_adopts_the_result(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    geometry = CameraGeometry(binning=2)
    w._add_channel_row(BF, 4.0, 2.0, 30.0, geometry=geometry)
    w._set_row_values(BF, zi=True)
    w.combobox_add_channel.setCurrentText(BF)
    ctrl = w.recordZStackController

    w.btn_test_fps.click()

    ctrl.run_frame_rate_test.assert_called_once()
    channel, duration = ctrl.run_frame_rate_test.call_args.args
    assert channel.name == BF and channel.exposure_time == pytest.approx(4.0)
    assert duration == w.FRAME_RATE_TEST_DURATION_S
    assert ctrl.run_frame_rate_test.call_args.kwargs["geometry"] == geometry
    assert not w.btn_test_fps.isEnabled() and not w.btn_startAcquisition.isEnabled()
    assert w.label_fps_result.text() == "testing…"

    result = FrameRateTestResult(
        channel_name=BF, exposure_ms=4.0, duration_s=5.0, frame_count=600, mean_fps=119.6, min_fps=80.0
    )
    ctrl.run_frame_rate_test.call_args.kwargs["on_finished"](result)

    assert w.label_fps_result.text() == "119.6 fps (min 80.0 over 5.0 s)"
    assert w._get_row_values(BF)["fps"] == 119.0  # rounded down: a rate the camera did deliver
    assert w.btn_test_fps.isEnabled() and w.btn_startAcquisition.isEnabled()


def test_frame_rate_test_failure_leaves_the_rows_fps_alone(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w._add_channel_row(BF, fps=30)
    w._fps_test_channel = BF
    w._on_frame_rate_test_finished(FrameRateTestResult(channel_name=BF, error="camera unplugged"))
    assert w.label_fps_result.text() == "failed: camera unplugged"
    assert w._get_row_values(BF)["fps"] == 30.0


def test_frame_rate_test_is_refused_during_an_acquisition(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w.recordZStackController.acquisition_in_progress.return_value = True
    with patch("control.widgets.QMessageBox.warning") as warn:
        w.btn_test_fps.click()
    warn.assert_called_once()
    w.recordZStackController.run_frame_rate_test.assert_not_called()


# ---------------------------------------------------------------------------
# XY / Time rows and the FOV grid
# ---------------------------------------------------------------------------


def test_xy_and_time_defaults(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    assert w.checkbox_xy.isChecked() and not w.checkbox_time.isChecked()
    modes = [w.combobox_xy_mode.itemText(i) for i in range(w.combobox_xy_mode.count())]
    assert modes == ["Current Position", "Select Wells", "Load Coordinates"]
    assert w.combobox_xy_mode.currentText() == "Select Wells"
    assert not w.xy_controls_frame.isHidden() and w.time_controls_frame.isHidden()
    assert w.entry_scan_size.value() == pytest.approx(0.1)


def test_grid_controls_are_only_shown_for_select_wells(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w.combobox_xy_mode.setCurrentText("Current Position")
    assert w.xy_controls_frame.isHidden()
    w.combobox_xy_mode.setCurrentText("Select Wells")
    assert not w.xy_controls_frame.isHidden()
    w.checkbox_xy.setChecked(False)
    assert w.xy_controls_frame.isHidden()


def test_unchecking_xy_forces_current_position_and_restores_on_recheck(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w.checkbox_xy.setChecked(False)
    assert w.combobox_xy_mode.currentText() == "Current Position" and not w.combobox_xy_mode.isEnabled()
    w.checkbox_xy.setChecked(True)
    assert w.combobox_xy_mode.currentText() == "Select Wells" and w.combobox_xy_mode.isEnabled()


def test_current_position_mode_uses_the_live_stage_position(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    sc = simulated_widget_deps["scanCoordinates"]
    sc.reset_mock()
    w.combobox_xy_mode.setCurrentText("Current Position")
    sc.add_region.assert_called_once_with("current", 1.5, 2.5, 0.01, 0, "Square")
    sc.set_well_coordinates.assert_not_called()


def test_toggling_xy_rebuilds_the_scan_region_exactly_once(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    sc = simulated_widget_deps["scanCoordinates"]
    sc.reset_mock()
    w.checkbox_xy.setChecked(False)
    assert sc.add_region.call_count == 1


@pytest.mark.parametrize("control", ["overlap", "scan_size", "shape"])
def test_grid_controls_retile_the_selected_wells(qtbot, simulated_widget_deps, control):
    w = _widget(qtbot, simulated_widget_deps)
    sc = simulated_widget_deps["scanCoordinates"]
    sc.has_regions.return_value = True
    sc.reset_mock()
    calls = []
    sc.clear_regions.side_effect = lambda: calls.append("clear")
    sc.set_well_coordinates.side_effect = lambda *a: calls.append(a)
    if control == "overlap":
        w.entry_overlap.setValue(25.0)
    elif control == "scan_size":
        w.entry_scan_size.setValue(3.0)
    else:
        w.combobox_shape.setCurrentText("Circle")
    # Cleared first: set_well_coordinates only adds wells, so the old tiles would otherwise survive.
    assert calls == ["clear", (w.entry_scan_size.value(), w.entry_overlap.value(), w.combobox_shape.currentText())]


def test_fov_grid_does_not_crash_without_scan_coordinates(qtbot, simulated_widget_deps):
    simulated_widget_deps["scanCoordinates"] = None
    w = _widget(qtbot, simulated_widget_deps)
    w.entry_overlap.setValue(30.0)
    assert w._get_selected_well_count() == 1


def test_well_selection_change_keeps_the_wells_already_tiled(qtbot, simulated_widget_deps):
    """selection_only: a drag-select must not clear and re-tile every well at each step."""
    w = _widget(qtbot, simulated_widget_deps)
    sc = simulated_widget_deps["scanCoordinates"]
    sc.has_regions.return_value = True
    sc.reset_mock()
    w.on_well_selection_changed()
    sc.clear_regions.assert_not_called()
    sc.set_well_coordinates.assert_called_once()


def test_well_selection_change_is_ignored_on_another_tab(qtbot, simulated_widget_deps):
    tabs = MagicMock()
    tabs.currentWidget.return_value = object()
    simulated_widget_deps["tab_widget"] = tabs
    w = _widget(qtbot, simulated_widget_deps)
    sc = simulated_widget_deps["scanCoordinates"]
    sc.reset_mock()
    w.on_well_selection_changed()
    sc.set_well_coordinates.assert_not_called()


def test_time_checkbox_shows_nt_dt_and_restores_them(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w.checkbox_time.setChecked(True)
    assert not w.time_controls_frame.isHidden()
    w.entry_Nt.setValue(5)
    w.entry_dt.setValue(30.0)
    w.checkbox_time.setChecked(False)
    assert (w.entry_Nt.value(), w.entry_dt.value()) == (1, 0.0)
    assert (w.build_parameters().Nt, w.build_parameters().dt_s) == (1, 0.0)
    w.checkbox_time.setChecked(True)
    assert (w.entry_Nt.value(), w.entry_dt.value()) == (5, 30.0)


# ---------------------------------------------------------------------------
# refresh_channel_list
# ---------------------------------------------------------------------------


def test_refresh_channel_list_repopulates_the_combo_and_drops_stale_rows(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    w._add_channel_row(BF, 7.0)
    w._add_channel_row(FL)
    w.liveController.get_channels.return_value = [_make_channel(BF), _make_channel("Fluorescence 561 nm Ex")]

    w.refresh_channel_list()

    assert [w.combobox_add_channel.itemText(i) for i in range(w.combobox_add_channel.count())] == [
        BF,
        "Fluorescence 561 nm Ex",
    ]
    assert w._channel_row_names == [BF]
    assert w._get_row_values(BF)["exposure"] == pytest.approx(7.0)  # the user's edit survives


@pytest.mark.parametrize("failure", ["raises", "empty"])
def test_refresh_channel_list_keeps_everything_on_a_failed_fetch(qtbot, simulated_widget_deps, failure):
    w = _widget(qtbot, simulated_widget_deps)
    w._add_channel_row(BF)
    if failure == "raises":
        w.liveController.get_channels.side_effect = RuntimeError("config repo busy")
    else:
        w.liveController.get_channels.return_value = []
    w.refresh_channel_list()
    assert w._channel_row_names == [BF]
    assert w.combobox_add_channel.count() == 2


# ---------------------------------------------------------------------------
# acquisition.yaml: apply, save, load
# ---------------------------------------------------------------------------


def _row(name, exposure=50.0, **fields):
    row = {
        "channel": {
            "name": name,
            "camera_settings": {"exposure_time_ms": exposure, "gain_mode": 1.0},
            "illumination_settings": {"intensity": 60.0},
        },
        "fps": 10.0,
        "duration_s": 1.0,
        "zi": False,
        "r": False,
        "zf": False,
    }
    row.update(fields)
    return row


def test_apply_yaml_settings_sets_every_control(qtbot, simulated_widget_deps):
    from control.acquisition_yaml_loader import RecordZStackYAMLData

    w = _widget(qtbot, simulated_widget_deps)
    w._add_channel_row(FL)  # replaced by the file's rows

    w._apply_yaml_settings(
        RecordZStackYAMLData(
            widget_type="record_zstack",
            xy_mode="Current Position",
            nt=4,
            delta_t_s=2.5,
            laser_af=True,
            contrast_af=True,
            use_focus_map=False,
            channels=[
                _row(
                    BF, 33.0, fps=25.0, duration_s=3.0, zi=True, r=True, camera={"binning": 2, "pixel_format": "mono8"}
                ),
                _row(FL, 80.0, zf=True),
            ],
            record_slice_roles=["mid"],
            record_slice_indices=[1],
            z_mode="Relative",
            z_dz_um=1.5,
            z_range_um=6.0,
            scan_size_mm=2.0,
            overlap_percent=15.0,
        )
    )

    assert (w.entry_Nt.value(), w.entry_dt.value()) == (4, pytest.approx(2.5))
    assert w.checkbox_time.isChecked() and not w.time_controls_frame.isHidden()
    assert w.checkbox_laser_af.isChecked() and w.checkbox_withAutofocus.isChecked()
    assert not w.checkbox_useFocusMap.isChecked()
    assert w._channel_row_names == [BF, FL]
    bf = w._get_row_values(BF)
    assert (bf["exposure"], bf["gain"], bf["illumination"]) == pytest.approx((33.0, 1.0, 60.0))
    assert (bf["fps"], bf["duration_s"], bf["zi"], bf["r"], bf["zf"]) == (25.0, 3.0, True, True, False)
    assert bf["geometry"] == CameraGeometry(binning=2, pixel_format="MONO8")
    assert w._get_row_values(FL)["zf"] is True
    assert w.combobox_z_mode.currentText() == "Relative"
    assert (w.entry_deltaZ.value(), w.entry_zRange.value(), w.entry_NZ.value()) == (1.5, 6.0, 5)
    assert not w.relative_frame.isHidden() and not w.entry_NZ.isEnabled()
    assert w._record_slice_selection().resolve(5) == [1, 2]
    assert w.combobox_xy_mode.currentText() == "Current Position" and w.xy_controls_frame.isHidden()
    assert (w.entry_scan_size.value(), w.entry_overlap.value()) == (2.0, 15.0)
    assert w.btn_startAcquisition.isEnabled()


def test_apply_yaml_settings_set_range_keeps_autofocus_off(qtbot, simulated_widget_deps):
    from control.acquisition_yaml_loader import RecordZStackYAMLData

    w = _widget(qtbot, simulated_widget_deps)
    w._apply_yaml_settings(
        RecordZStackYAMLData(
            widget_type="record_zstack",
            laser_af=True,
            contrast_af=True,
            channels=[_row(BF, zf=True)],
            z_mode="Set Range",
            z_dz_um=2.0,
            z_min_mm=1.0,
            z_max_mm=1.01,
        )
    )
    assert w.combobox_z_mode.currentText() == "Set Range"
    assert (w.entry_minZ.value(), w.entry_maxZ.value(), w.entry_NZ.value()) == (1000.0, 1010.0, 6)
    assert not w.checkbox_laser_af.isChecked() and not w.checkbox_withAutofocus.isChecked()


def test_apply_yaml_settings_skips_a_channel_this_objective_does_not_have(qtbot, simulated_widget_deps, caplog):
    from control.acquisition_yaml_loader import RecordZStackYAMLData

    w = _widget(qtbot, simulated_widget_deps)
    w._apply_yaml_settings(
        RecordZStackYAMLData(widget_type="record_zstack", channels=[_row("Cy5", r=True), _row(BF, zf=True)])
    )
    assert w._channel_row_names == [BF]
    assert any("'Cy5' not found" in r.getMessage() for r in caplog.records)


def test_apply_yaml_settings_without_rows_disables_start(qtbot, simulated_widget_deps):
    from control.acquisition_yaml_loader import RecordZStackYAMLData

    w = _make_valid_widget(qtbot, simulated_widget_deps)
    assert w.btn_startAcquisition.isEnabled()
    w._apply_yaml_settings(RecordZStackYAMLData(widget_type="record_zstack"))
    assert w._channel_row_names == [] and not w.btn_startAcquisition.isEnabled()
    assert not w.checkbox_time.isChecked() and w.time_controls_frame.isHidden()


def test_malformed_channel_row_is_a_load_error_and_leaves_the_tab_untouched(qtbot, simulated_widget_deps, tmp_path):
    """A row whose channel fails validation must not crash the drop/button slot, and must not be
    dropped silently either: the load is refused and the table keeps what it had."""
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    path = tmp_path / "malformed.yaml"
    path.write_text(
        "acquisition:\n  widget_type: record_zstack\n  xy_mode: Current Position\n"
        "channels:\n- channel:\n    name: Foo\n  r: true\n"  # no camera_settings -> pydantic ValidationError
    )
    with patch("control.widgets.QMessageBox.warning") as warn:
        assert w._load_acquisition_yaml(str(path)) is False
    warn.assert_called_once()
    assert warn.call_args[0][1] == "Load Error"
    assert w._channel_row_names == [BF]
    assert w.combobox_xy_mode.currentText() == "Select Wells"


def test_save_settings_button_writes_spencers_schema(qtbot, simulated_widget_deps, tmp_path, monkeypatch):
    """Save Settings runs the real chain to a real file; only the file dialog is replaced."""
    w = _make_valid_widget(qtbot, simulated_widget_deps, zi=True, r=True, zf=False)
    w._set_row_values(BF, geometry=CameraGeometry(binning=2))
    w.combobox_xy_mode.setCurrentText("Current Position")
    _harden_objective_info(w)
    save_path = tmp_path / "my_preset.yaml"
    monkeypatch.setattr("control.widgets.QFileDialog.getSaveFileName", lambda *a, **kw: (str(save_path), ""))

    w.btn_saveSettings.click()

    assert save_path.read_text().startswith("# Time at XY Acquisition Parameters - record_zstack\n")
    data = yaml.safe_load(save_path.read_text())
    assert list(data) == [
        "acquisition",
        "objective",
        "time_series",
        "autofocus",
        "channels",
        "record_slices",
        "z_stack",
    ]
    assert data["acquisition"]["widget_type"] == "record_zstack"
    assert data["autofocus"] == {"laser_af": False, "contrast_af": False, "use_focus_map": False}
    (row,) = data["channels"]
    assert list(row) == ["channel", "fps", "duration_s", "zi", "r", "zf", "camera"]
    assert row["channel"]["name"] == BF and (row["zi"], row["r"], row["zf"]) == (True, True, False)
    # A row that pins binning also records the pixel size it will have.
    assert row["camera"] == {"binning": 2, "pixel_size_um": pytest.approx(1.0)}
    assert data["record_slices"] == {"roles": ["bottom", "mid", "top"], "indices": []}
    assert list(data["z_stack"]) == ["mode", "dz_um", "nz", "z_range_um", "z_min_mm", "z_max_mm"]
    assert (data["z_stack"]["mode"], data["z_stack"]["dz_um"], data["z_stack"]["nz"]) == ("From Bottom", 1.0, 1)
    assert data["z_stack"]["z_range_um"] == 4.0
    assert data["objective"] == {"name": "10x", "magnification": 10.0, "pixel_size_um": 0.5, "camera_binning": [1, 1]}


def test_save_settings_button_shows_a_warning_when_the_write_fails(qtbot, simulated_widget_deps, tmp_path, monkeypatch):
    w = _make_valid_widget(qtbot, simulated_widget_deps)
    _harden_objective_info(w)
    missing = tmp_path / "no_such_dir" / "preset.yaml"
    monkeypatch.setattr("control.widgets.QFileDialog.getSaveFileName", lambda *a, **kw: (str(missing), ""))
    with patch("control.widgets.QMessageBox.warning") as warn:
        w.btn_saveSettings.click()
    warn.assert_called_once()
    assert warn.call_args[0][1] == "Save Error"


def _save_and_reload(qtbot, deps, tmp_path, source):
    """Save *source*'s settings with the real writer and load the file into a fresh widget."""
    from control.core.record_zstack_controller import _save_record_zstack_yaml

    path = tmp_path / "acquisition.yaml"
    scan = MagicMock()
    scan.region_centers = {"A1": [1.0, 2.0, 0.1]}
    scan.region_shapes = {"A1": "Square"}
    _save_record_zstack_yaml(source.build_parameters(), str(path), scan, {"name": "10x", "camera_binning": [1, 1]})
    target = _widget(qtbot, deps)
    target.liveController.camera.get_binning.return_value = (1, 1)
    with patch("control.widgets.QMessageBox.warning") as warn:
        assert target._load_acquisition_yaml(str(path)) is True, warn.call_args
    return target, path


def test_full_save_load_round_trip_preserves_every_setting(qtbot, simulated_widget_deps, tmp_path):
    w = _widget(qtbot, simulated_widget_deps)
    w.lineEdit_savingDir.setText(str(tmp_path))
    w._add_channel_row(FL, 80.0, 0.5, 40.0, fps=25, duration_s=3, zi=True, r=True, zf=False)
    w._add_channel_row(BF, 5.0, 1.0, 10.0, zi=False, r=False, zf=True, geometry=CameraGeometry(2, 512, 256, "MONO8"))
    w.checkbox_time.setChecked(True)
    w.entry_Nt.setValue(3)
    w.entry_dt.setValue(12.0)
    w.checkbox_withAutofocus.setChecked(True)
    w.combobox_z_mode.setCurrentText("Relative")
    w.entry_deltaZ.setValue(0.5)
    w.entry_zRange.setValue(3.0)
    w._apply_slice_choice({0, 1, 6}, 7)
    w._sync_slice_buttons()
    w.entry_scan_size.setValue(1.2)
    w.entry_overlap.setValue(5.0)
    before = w.build_parameters()

    reloaded, _path = _save_and_reload(qtbot, simulated_widget_deps, tmp_path, w)
    after = reloaded.build_parameters()

    assert after.channels == before.channels
    assert after.zstack == before.zstack
    assert after.record_slices.resolve(7) == before.record_slices.resolve(7) == [0, 1, 6]
    assert (after.Nt, after.dt_s) == (3, 12.0)
    assert (after.use_laser_af, after.use_contrast_af, after.use_focus_map) == (False, True, False)
    assert (after.xy_mode, after.scan_size_mm, after.overlap_percent) == ("Select Wells", 1.2, 5.0)


def test_load_settings_button_applies_a_file(qtbot, simulated_widget_deps, tmp_path, monkeypatch):
    source = _make_valid_widget(qtbot, simulated_widget_deps, zi=True)
    source.entry_NZ.setValue(4)
    _target, path = _save_and_reload(qtbot, simulated_widget_deps, tmp_path, source)

    w = _widget(qtbot, simulated_widget_deps)
    w.liveController.camera.get_binning.return_value = (1, 1)
    monkeypatch.setattr("control.widgets.QFileDialog.getOpenFileName", lambda *a, **kw: (str(path), ""))
    w.btn_loadSettings.click()
    assert w._channel_row_names == [BF] and w.entry_NZ.value() == 4
    assert w._get_row_values(BF)["zi"] is True


def test_a_file_saved_by_spencers_build_loads_unchanged(qtbot, simulated_widget_deps):
    """tests/files/record_zstack/spencer_time_at_xy.yaml has every key his _save_record_zstack_yaml writes."""
    w = _widget(qtbot, simulated_widget_deps)
    w.liveController.camera.get_binning.return_value = (2, 2)
    w.objectiveStore.current_objective = "20x"
    w.liveController.get_channels.return_value = [
        _make_channel("Fluorescence 488 nm Ex"),
        _make_channel("Fluorescence 561 nm Ex"),
        _make_channel("BF LED matrix full"),
    ]
    w.refresh_channel_list()
    laser = MagicMock()
    laser.laser_af_properties.has_reference = True
    w.laser_autofocus_controller = laser

    with patch("control.widgets.QMessageBox.warning") as warn:
        assert w._load_acquisition_yaml(str(FIXTURES / "spencer_time_at_xy.yaml")) is True, warn.call_args

    params = w.build_parameters()
    assert [(p.name, p.fps, p.duration_s, p.zi, p.r, p.zf) for p in params.channels] == [
        ("Fluorescence 488 nm Ex", 50.0, 10.0, True, True, True),
        ("Fluorescence 561 nm Ex", 20.0, 5.0, False, True, False),
        ("BF LED matrix full", 10.0, 1.0, True, False, True),
    ]
    assert params.channels[0].channel.exposure_time == pytest.approx(15.0)
    assert params.channels[0].geometry == CameraGeometry(binning=2, crop_w=1024, crop_h=1024, pixel_format="MONO8")
    assert params.channels[2].geometry == CameraGeometry()
    assert (params.zstack.mode, params.zstack.dz_um, params.zstack.z_range_um) == ("Relative", 1.5, 12.0)
    assert w.entry_NZ.value() == 9
    assert params.record_slices.resolve(9) == [0, 2, 4, 8]
    assert (params.use_laser_af, params.use_contrast_af, params.use_focus_map) == (True, False, False)
    assert (params.Nt, params.dt_s) == (6, 600.0)
    assert (params.xy_mode, params.scan_size_mm, params.overlap_percent) == ("Select Wells", 0.5, 10.0)


def test_a_file_from_the_previous_tab_loads_as_an_equivalent_acquisition(qtbot, simulated_widget_deps, caplog):
    """legacy_4dcell_recording_and_zstack.yaml was written by the Record + Z-Stack tab this one replaces:
    a 30 fps / 5 s recording of BF at the reference plane, then a -3..+3 µm z-stack of two channels."""
    w = _widget(qtbot, simulated_widget_deps)
    w.liveController.camera.get_binning.return_value = (2, 2)
    w.objectiveStore.current_objective = "20x"
    w.liveController.get_channels.return_value = [
        _make_channel("BF LED matrix full"),
        _make_channel("DF LED matrix"),
        _make_channel("Fluorescence 405 nm Ex"),
    ]
    w.refresh_channel_list()

    with patch("control.widgets.QMessageBox.warning") as warn:
        assert (
            w._load_acquisition_yaml(str(FIXTURES / "legacy_4dcell_recording_and_zstack.yaml")) is True
        ), warn.call_args

    params = w.build_parameters()
    assert [(p.name, p.zi, p.r, p.zf) for p in params.channels] == [
        ("BF LED matrix full", False, True, False),
        ("DF LED matrix", False, False, True),
        ("Fluorescence 405 nm Ex", False, False, True),
    ]
    rec = params.channels[0]
    assert (rec.fps, rec.duration_s, rec.channel.exposure_time) == (30.0, 5.0, 20.0)
    assert params.channels[1].channel.exposure_time == pytest.approx(35.0)
    # The old stack (-3..+3 µm in 1 µm steps about the reference) and the old recording plane (at the reference).
    from control.core.record_zstack_controller import recording_offsets_for_plan, zstack_offsets_for_mode

    assert zstack_offsets_for_mode(params.zstack, 0.0) == [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0]
    assert recording_offsets_for_plan(params.zstack, params.record_slices, 0.0) == [(3, 0.0)]
    assert (params.Nt, params.dt_s, params.use_laser_af) == (3, 60.0, False)
    assert (params.xy_mode, params.scan_size_mm, params.overlap_percent) == ("Select Wells", 0.5, 15.0)
    assert any("pre-table" in r.getMessage() for r in caplog.records)
    assert not any("pre-table acquisition.yaml:" in r.getMessage() for r in caplog.records)  # nothing was lost


def test_a_multi_plane_recording_file_from_the_previous_tab_keeps_its_planes_and_positions(
    qtbot, simulated_widget_deps
):
    """legacy_4dcell_multiplane_recording.yaml: laser AF, three recording planes at -2 / 0 / +2 µm, no
    z-stack, Load Coordinates with two positions."""
    from control.core.record_zstack_controller import recording_offsets_for_plan

    laser = MagicMock()
    laser.laser_af_properties.has_reference = True
    simulated_widget_deps["laser_autofocus_controller"] = laser
    w = _widget(qtbot, simulated_widget_deps)
    w.lineEdit_savingDir.setText("/tmp/x")
    w.liveController.camera.get_binning.return_value = (2, 2)
    w.objectiveStore.current_objective = "20x"

    with patch("control.widgets.QMessageBox.warning") as warn:
        assert (
            w._load_acquisition_yaml(str(FIXTURES / "legacy_4dcell_multiplane_recording.yaml")) is True
        ), warn.call_args

    params = w.build_parameters()
    assert [(p.name, p.fps, p.duration_s, p.zi, p.r, p.zf) for p in params.channels] == [
        (BF, 10.0, 2.0, False, True, False)
    ]
    assert recording_offsets_for_plan(params.zstack, params.record_slices, 0.0) == [(0, -2.0), (1, 0.0), (2, 2.0)]
    assert params.use_laser_af is True
    assert params.xy_mode == "Load Coordinates"
    assert w.loaded_positions() == [
        {"name": "target_0", "fovs": [[10.0, 12.0, 1.5]]},
        {"name": "target_1", "fovs": [[11.0, 12.5]]},
    ]
    assert w.validate() is None


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_widget_type_and_camera_for_the_binning_check(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    assert w._get_expected_widget_type() == "record_zstack"
    assert w._get_camera_for_binning_check() is w.liveController.camera
    assert w._get_other_widget_name("record_zstack") == "Time at XY"


def test_editors_and_labels_are_never_narrower_than_their_size_hints(qtbot, simulated_widget_deps):
    """Widths must come from the font in use, not from pixel counts tuned on one machine.

    Qt's sizeHint already fits each editor's widest value text in the current font, so no editor or
    label outside the channel table may end up narrower than its own hint.  (The table's cells are
    sized from the font metrics of each column's widest value instead: see _column_widths.)
    """
    from qtpy.QtWidgets import QAbstractSpinBox, QApplication, QComboBox, QLabel, QScrollArea

    w = _widget(qtbot, simulated_widget_deps)
    w.checkbox_time.setChecked(True)
    w._add_channel_row(BF)
    w.show()
    qtbot.waitExposed(w)
    QApplication.processEvents()

    # The tab scrolls, so its sizeHint is the scroll area's, not the content's.
    content = w.findChild(QScrollArea).widget()

    def fit_content():
        chrome = w.width() - content.width()
        w.resize(content.sizeHint().width() + chrome, w.sizeHint().height())
        QApplication.processEvents()

    def label(widget):
        shown = widget.currentText() if isinstance(widget, QComboBox) else widget.text()
        return f"{type(widget).__name__}({shown!r})"

    def too_narrow():
        in_table = set(w.channel_table.findChildren(QAbstractSpinBox))
        checked = [
            c for c in w.findChildren(QAbstractSpinBox) + w.findChildren(QLabel) if c.isVisible() and c not in in_table
        ]
        checked += [w.combobox_shape, w.combobox_xy_mode, w.combobox_z_mode]
        assert len(checked) > 15, "the tab's editors were not found"
        return [
            f"{label(c)}: {c.width()}px < hint {c.sizeHint().width()}px"
            for c in checked
            if c.width() < c.sizeHint().width()
        ]

    for mode in ("From Bottom", "Relative", "Set Range"):
        w.combobox_z_mode.setCurrentText(mode)
        fit_content()
        assert not too_narrow(), f"{mode}:\n" + "\n".join(too_narrow())


def test_channel_table_columns_fit_their_widest_values(qtbot, simulated_widget_deps):
    w = _widget(qtbot, simulated_widget_deps)
    fm = w.channel_table.fontMetrics()
    for col, sample in w._COLUMN_SAMPLES.items():
        assert w.channel_table.columnWidth(col) >= fm.horizontalAdvance(sample)
        assert w.channel_table.columnWidth(col) >= fm.horizontalAdvance(w._COLUMN_HEADERS[col])


def test_camera_tab_refresh_shows_what_the_camera_is_set_to(qtbot):
    """Per-channel camera settings change the camera behind the Camera tab's back; after a run or a
    frame-rate test the tab re-reads the camera instead of writing its own stale values to it."""
    import squid.camera.utils
    import squid.config
    from control.widgets import CameraSettingsWidget

    camera = squid.camera.utils.get_camera(squid.config.get_camera_config(), simulated=True)
    tab = CameraSettingsWidget(camera)
    qtbot.addWidget(tab)
    emitted = []
    tab.signal_binning_changed.connect(lambda: emitted.append(1))

    tab.refresh_from_camera()
    assert emitted == []  # the camera is as the tab shows it: nothing for listeners to redo

    shown = tab.dropdown_binning.currentText()
    other = next(b for b in (1, 2) if f"{b}x{b}" != shown)
    writes = []
    real_set_binning = camera.set_binning
    camera.set_binning = lambda x, y: writes.append((x, y)) or real_set_binning(x, y)
    real_set_binning(other, other)  # as the Time at XY worker does, not through the tab

    tab.refresh_from_camera()

    assert tab.dropdown_binning.currentText() == f"{other}x{other}"
    assert tab.entry_ROI_width.value() == camera.get_region_of_interest()[2]
    assert tab.entry_ROI_height.value() == camera.get_region_of_interest()[3]
    assert writes == []  # read back only
    assert emitted == [1]
