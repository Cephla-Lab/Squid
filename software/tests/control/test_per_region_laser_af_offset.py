"""Unit tests for the per-region laser-AF offset feature.

Backend tests construct minimal MultiPointWorker-/FocusMapWidget-shaped stubs and
call the real methods in isolation, mirroring tests/control/test_MultiPointWorker_offsets.py.
"""

import pytest
from dataclasses import fields
from unittest.mock import MagicMock

from control.core.multi_point_utils import AcquisitionParameters, ScanPositionInformation
from control.core.multi_point_controller import MultiPointController
from control.core.multi_point_worker import MultiPointWorker
from control.widgets import FocusMapWidget


def test_acquisition_parameters_has_region_offsets_field():
    names = {f.name for f in fields(AcquisitionParameters)}
    assert "region_laser_af_offsets" in names


class _BuildParamsStub:
    """MultiPointController-shaped stub exercising build_params' offset threading in isolation."""

    def __init__(self):
        self.scanCoordinates = object()  # no 'format' attr -> default plate dims, no wellplate lookup
        self._log = MagicMock()
        self.experiment_ID = "exp"
        self.base_path = "/tmp"
        self.selected_configurations = []
        self.timestamp_acquisition_started = 0.0
        self.NX = self.NY = self.NZ = self.Nt = 1
        self.deltaX = self.deltaY = self.deltaZ = self.deltat = 0.0
        self.do_autofocus = False
        self.do_reflection_af = True
        self.apply_channel_offset = True
        self.use_piezo = False
        self.display_resolution_scaling = 1.0
        self.z_stacking_config = "FROM CENTER"
        self.z_range = (0.0, 0.0)
        self.use_fluidics = False
        self.skip_saving = False
        self.xy_mode = "Current Position"

    build_params = MultiPointController.build_params


def _empty_scan_position_information():
    return ScanPositionInformation(scan_region_coords_mm=[], scan_region_names=[], scan_region_fov_coords_mm={})


def test_build_params_threads_region_offsets():
    params = _BuildParamsStub().build_params(_empty_scan_position_information(), region_laser_af_offsets={"A1": 4.0})
    assert params.region_laser_af_offsets == {"A1": 4.0}


def test_build_params_defaults_offsets_to_empty_when_not_passed():
    # run_acquisition clears self.region_laser_af_offsets up-front and passes the snapshot
    # explicitly; a None/absent argument must yield {} (no leaked sticky state).
    params = _BuildParamsStub().build_params(_empty_scan_position_information())
    assert params.region_laser_af_offsets == {}


def test_region_offsets_default_factory_is_empty_dict():
    fld = next(f for f in fields(AcquisitionParameters) if f.name == "region_laser_af_offsets")
    assert fld.default_factory() == {}


class _AFStub:
    """MultiPointWorker-ish object with just what perform_autofocus's laser-AF branch reads."""

    def __init__(self, offsets, move_result=True):
        self.do_reflection_af = True
        self.region_laser_af_offsets = offsets
        self._log = MagicMock()
        self.laser_auto_focus_controller = MagicMock()
        self.laser_auto_focus_controller.move_to_target.return_value = move_result
        self._laser_af_successes = 0
        self._laser_af_failures = 0
        # Only touched on the exception path:
        self.base_path = "/tmp"
        self.experiment_ID = "exp"
        self.time_point = 0

    perform_autofocus = MultiPointWorker.perform_autofocus


def test_perform_autofocus_uses_region_offset():
    # Anchor closed-loop at the reference (move_to_target(0.0)), then apply the per-region
    # offset open-loop — driving move_to_target to a nonzero displacement would fail the
    # reference-crop verification and revert.
    w = _AFStub({"A1": 5.0})
    assert w.perform_autofocus("A1", 0) is True
    w.laser_auto_focus_controller.move_to_target.assert_called_once_with(0.0)
    w.laser_auto_focus_controller.apply_relative_offset_um.assert_called_once_with(5.0)
    assert w._laser_af_successes == 1


def test_perform_autofocus_defaults_to_zero_for_unmapped_region():
    w = _AFStub({"A1": 5.0})
    assert w.perform_autofocus("B2", 0) is True
    w.laser_auto_focus_controller.move_to_target.assert_called_once_with(0.0)
    # No offset for B2 → no open-loop move.
    w.laser_auto_focus_controller.apply_relative_offset_um.assert_not_called()


def test_perform_autofocus_offset_not_applied_when_anchor_fails():
    # If anchoring at the reference fails, the open-loop offset must NOT be applied.
    w = _AFStub({"A1": 5.0}, move_result=False)
    assert w.perform_autofocus("A1", 0) is False
    w.laser_auto_focus_controller.apply_relative_offset_um.assert_not_called()
    assert w._laser_af_failures == 1


def _laser_controller(displacement, has_reference=True, laser_af_range=200.0):
    c = MagicMock()
    c.laser_af_properties.has_reference = has_reference
    c.laser_af_properties.laser_af_range = laser_af_range
    c.measure_displacement.return_value = displacement
    return c


class _FMStub:
    """FocusMapWidget-ish object exposing just what the capture/persistence helpers read."""

    def __init__(self, *, enabled=True, controller=None, focus_points=None, offsets=None, live=False):
        self.capture_laser_af_offset_enabled = enabled
        self.laserAutofocusController = controller
        self.focus_points = focus_points if focus_points is not None else []
        self.region_laser_af_offsets = offsets if offsets is not None else {}
        self.status_label = MagicMock()
        self._log = MagicMock()
        self._offsets_reference_x = None
        self.liveController = MagicMock()
        self.liveController.is_live = live
        self.checkbox_perRegionLaserAFOffset = MagicMock()
        self.fit_method_combo = MagicMock()
        self.fit_method_combo.currentText.return_value = "constant"
        self.by_region_checkbox = MagicMock()
        self.by_region_checkbox.isChecked.return_value = True

    _capture_region_offset = FocusMapWidget._capture_region_offset
    _clear_region_offsets = FocusMapWidget._clear_region_offsets
    _sync_offsets_to_focus_points = FocusMapWidget._sync_offsets_to_focus_points
    _on_laser_af_reference_changed = FocusMapWidget._on_laser_af_reference_changed
    _on_per_region_offset_toggled = FocusMapWidget._on_per_region_offset_toggled
    _update_per_region_offset_enabled = FocusMapWidget._update_per_region_offset_enabled
    get_offsets_for_acquisition = FocusMapWidget.get_offsets_for_acquisition


def test_capture_stores_displacement_when_enabled():
    w = _FMStub(controller=_laser_controller(3.5))
    w._capture_region_offset("A1")
    assert w.region_laser_af_offsets == {"A1": 3.5}


def test_capture_shows_offset_on_status_line():
    # On a successful in-range capture, the focus-map status line reports the offset.
    w = _FMStub(controller=_laser_controller(2.3))
    w._capture_region_offset("A1")
    msg = w.status_label.setText.call_args[0][0]
    assert "A1" in msg and "2.3" in msg


def test_capture_suspends_main_live_during_measurement():
    # measure_displacement contends with the main live stream on the microcontroller serial
    # link; capture must stop live around the measurement and restore it after.
    w = _FMStub(controller=_laser_controller(2.3), live=True)
    w._capture_region_offset("A1")
    w.liveController.stop_live.assert_called_once()
    w.liveController.start_live.assert_called_once()
    assert w.region_laser_af_offsets == {"A1": 2.3}


def test_capture_does_not_toggle_live_when_not_live():
    w = _FMStub(controller=_laser_controller(2.3), live=False)
    w._capture_region_offset("A1")
    w.liveController.stop_live.assert_not_called()
    w.liveController.start_live.assert_not_called()


def test_capture_noop_when_mode_disabled():
    ctrl = _laser_controller(3.5)
    w = _FMStub(enabled=False, controller=ctrl)
    w._capture_region_offset("A1")
    assert w.region_laser_af_offsets == {}
    ctrl.measure_displacement.assert_not_called()


def test_capture_noop_when_no_controller():
    w = _FMStub(controller=None)
    w._capture_region_offset("A1")
    assert w.region_laser_af_offsets == {}


def test_capture_noop_when_no_reference():
    ctrl = _laser_controller(3.5, has_reference=False)
    w = _FMStub(controller=ctrl)
    w._capture_region_offset("A1")
    assert w.region_laser_af_offsets == {}
    ctrl.measure_displacement.assert_not_called()


def test_capture_does_not_store_nan():
    w = _FMStub(controller=_laser_controller(float("nan")))
    w._capture_region_offset("A1")
    assert "A1" not in w.region_laser_af_offsets


def test_capture_stores_but_warns_when_out_of_range():
    w = _FMStub(controller=_laser_controller(500.0, laser_af_range=200.0))
    w._capture_region_offset("A1")
    assert w.region_laser_af_offsets == {"A1": 500.0}
    assert w.status_label.setText.called
    assert "exceeds" in w.status_label.setText.call_args[0][0]


def test_capture_replaces_stale_entry_when_disabled():
    # Re-capturing with mode off must not leave a stale value for that region.
    w = _FMStub(enabled=False, controller=_laser_controller(3.5), offsets={"A1": 9.0})
    w._capture_region_offset("A1")
    assert "A1" not in w.region_laser_af_offsets


def test_clear_region_offsets():
    w = _FMStub(offsets={"A1": 1.0, "B2": 2.0})
    w._clear_region_offsets()
    assert w.region_laser_af_offsets == {}


def test_sync_drops_orphaned_offsets():
    w = _FMStub(focus_points=[("A1", 0.0, 0.0, 1.0)], offsets={"A1": 1.0, "B2": 2.0})
    w._sync_offsets_to_focus_points()
    assert w.region_laser_af_offsets == {"A1": 1.0}


def test_capture_sets_offsets_reference_x_on_success():
    ctrl = _laser_controller(3.5)
    ctrl.laser_af_properties.x_reference = 123.0
    w = _FMStub(controller=ctrl)
    w._capture_region_offset("A1")
    assert w._offsets_reference_x == 123.0


def test_capture_failure_logs_warning():
    # A failed re-capture (spot not detected) drops the offset AND logs a warning — not just
    # a transient status label — so silent data loss is visible.
    w = _FMStub(controller=_laser_controller(float("nan")), offsets={"A1": 4.0})
    w._capture_region_offset("A1")
    assert "A1" not in w.region_laser_af_offsets
    assert w._log.warning.called


def test_reference_change_clears_offsets_when_reference_actually_changed():
    ctrl = _laser_controller(0.0)
    ctrl.laser_af_properties.x_reference = 200.0  # reference is now different
    w = _FMStub(controller=ctrl, offsets={"A1": 1.0})
    w._offsets_reference_x = 100.0  # offsets were captured against the OLD reference
    w._on_laser_af_reference_changed(True)
    assert w.region_laser_af_offsets == {}
    assert w.status_label.setText.called


def test_reference_change_keeps_offsets_on_benign_reemit():
    # A config reload re-emits signal_reference_changed with the SAME reference; offsets must survive.
    ctrl = _laser_controller(0.0)
    ctrl.laser_af_properties.x_reference = 100.0
    w = _FMStub(controller=ctrl, offsets={"A1": 1.0})
    w._offsets_reference_x = 100.0  # same reference the offsets were captured against
    w._on_laser_af_reference_changed(True)
    assert w.region_laser_af_offsets == {"A1": 1.0}
    w.status_label.setText.assert_not_called()


def test_reference_lost_clears_offsets():
    ctrl = _laser_controller(0.0, has_reference=False)
    w = _FMStub(controller=ctrl, offsets={"A1": 1.0})
    w._offsets_reference_x = 100.0
    w._on_laser_af_reference_changed(False)
    assert w.region_laser_af_offsets == {}


def test_reference_change_no_status_when_nothing_to_clear():
    w = _FMStub()
    w._on_laser_af_reference_changed(True)
    w.status_label.setText.assert_not_called()


def test_csv_roundtrip_includes_offsets(tmp_path):
    src = _FMStub(
        focus_points=[("A1", 1.0, 2.0, 0.5), ("B2", 3.0, 4.0, 0.6)],
        offsets={"A1": 7.0},  # B2 intentionally has no offset
    )
    src._write_focus_points_csv = FocusMapWidget._write_focus_points_csv.__get__(src)
    path = str(tmp_path / "fp.csv")
    src._write_focus_points_csv(path)

    dst = _FMStub()
    dst._read_focus_points_csv = FocusMapWidget._read_focus_points_csv.__get__(dst)
    points, offsets = dst._read_focus_points_csv(path)
    assert points == [("A1", 1.0, 2.0, 0.5), ("B2", 3.0, 4.0, 0.6)]
    assert offsets == {"A1": 7.0}


def test_csv_read_back_compat_without_offset_column(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text("Region_ID,X_mm,Y_mm,Z_um\nA1,1.0,2.0,0.5\n")
    dst = _FMStub()
    dst._read_focus_points_csv = FocusMapWidget._read_focus_points_csv.__get__(dst)
    points, offsets = dst._read_focus_points_csv(str(path))
    assert points == [("A1", 1.0, 2.0, 0.5)]
    assert offsets == {}


def test_csv_read_rejects_missing_required_columns(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Region_ID,X_mm\nA1,1.0\n")
    dst = _FMStub()
    dst._read_focus_points_csv = FocusMapWidget._read_focus_points_csv.__get__(dst)

    with pytest.raises(ValueError):
        dst._read_focus_points_csv(str(path))


def test_csv_read_skips_nonfinite_offsets_and_coords(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text(
        "Region_ID,X_mm,Y_mm,Z_um,Offset_um\n"
        "A1,1.0,2.0,0.5,3.0\n"  # good
        "B2,1.0,2.0,0.5,nan\n"  # good coords, NaN offset -> point kept, offset rejected
        "C3,nan,2.0,0.5,4.0\n"  # NaN coordinate -> whole row dropped
        "D4,1.0,2.0,0.5,inf\n"  # inf offset -> point kept, offset rejected
    )
    dst = _FMStub()
    dst._read_focus_points_csv = FocusMapWidget._read_focus_points_csv.__get__(dst)
    points, offsets = dst._read_focus_points_csv(str(path))
    assert [p[0] for p in points] == ["A1", "B2", "D4"]  # C3 dropped (non-finite coord)
    assert offsets == {"A1": 3.0}  # NaN/inf offsets rejected


# ---------------------------------------------------------------------------
# _on_per_region_offset_toggled
# ---------------------------------------------------------------------------


def test_toggle_on_sets_capture_enabled():
    w = _FMStub(enabled=False)
    w._on_per_region_offset_toggled(True)
    assert w.capture_laser_af_offset_enabled is True


def test_toggle_off_disables_capture_but_retains_offsets():
    # Unchecking only stops new captures; it must NOT discard already-captured offsets
    # (those are cleared only on reference change or focus-point edits). The acquisition
    # gate returns {} while unchecked, so retaining them is safe and avoids data loss when
    # an unrelated state change (e.g. another tab) incidentally unchecks the box.
    w = _FMStub(enabled=True, offsets={"A1": 1.0, "B2": 2.0})
    w._on_per_region_offset_toggled(False)
    assert w.capture_laser_af_offset_enabled is False
    assert w.region_laser_af_offsets == {"A1": 1.0, "B2": 2.0}


# ---------------------------------------------------------------------------
# _update_per_region_offset_enabled — gated ONLY on the shared focus-map controls
# (constant + Fit by Region), NOT on any tab's Reflection AF checkbox, so a tab
# toggle cannot clobber the shared widget. Reflection AF is enforced at acquisition.
# ---------------------------------------------------------------------------


def test_update_per_region_offset_disabled_when_method_is_spline():
    w = _FMStub()
    w.fit_method_combo.currentText.return_value = "spline"
    w.by_region_checkbox.isChecked.return_value = True
    w.checkbox_perRegionLaserAFOffset.isChecked.return_value = False
    w._update_per_region_offset_enabled()
    w.checkbox_perRegionLaserAFOffset.setEnabled.assert_called_with(False)


def test_update_per_region_offset_disabled_when_by_region_unchecked():
    w = _FMStub()
    w.fit_method_combo.currentText.return_value = "constant"
    w.by_region_checkbox.isChecked.return_value = False
    w.checkbox_perRegionLaserAFOffset.isChecked.return_value = False
    w._update_per_region_offset_enabled()
    w.checkbox_perRegionLaserAFOffset.setEnabled.assert_called_with(False)


def test_update_per_region_offset_enabled_when_constant_and_by_region():
    w = _FMStub()
    w.fit_method_combo.currentText.return_value = "constant"
    w.by_region_checkbox.isChecked.return_value = True
    w.checkbox_perRegionLaserAFOffset.isChecked.return_value = False
    w._update_per_region_offset_enabled()
    w.checkbox_perRegionLaserAFOffset.setEnabled.assert_called_with(True)


def test_update_per_region_offset_unchecks_when_conditions_not_met_and_was_checked():
    """When conditions fail and the checkbox was checked, it must be unchecked."""
    w = _FMStub()
    w.fit_method_combo.currentText.return_value = "rbf"
    w.by_region_checkbox.isChecked.return_value = True
    w.checkbox_perRegionLaserAFOffset.isChecked.return_value = True
    w._update_per_region_offset_enabled()
    w.checkbox_perRegionLaserAFOffset.setEnabled.assert_called_with(False)
    w.checkbox_perRegionLaserAFOffset.setChecked.assert_called_once_with(False)


def test_get_offsets_for_acquisition_returns_offsets_when_active():
    w = _FMStub(offsets={"A1": 3.0})
    w.checkbox_perRegionLaserAFOffset.isChecked.return_value = True
    result = w.get_offsets_for_acquisition(reflection_af_active=True)
    assert result == {"A1": 3.0}
    assert result is not w.region_laser_af_offsets  # returns a copy, not the live dict


def test_get_offsets_for_acquisition_empty_when_reflection_af_off():
    w = _FMStub(offsets={"A1": 3.0})
    w.checkbox_perRegionLaserAFOffset.isChecked.return_value = True
    assert w.get_offsets_for_acquisition(reflection_af_active=False) == {}


def test_get_offsets_for_acquisition_empty_when_checkbox_off():
    w = _FMStub(offsets={"A1": 3.0})
    w.checkbox_perRegionLaserAFOffset.isChecked.return_value = False
    assert w.get_offsets_for_acquisition(reflection_af_active=True) == {}


def test_get_offsets_warns_about_regions_missing_offset():
    # A scan region with no captured offset falls back to the reference plane — warn at run
    # start so a silently-missing well is visible.
    w = _FMStub(offsets={"A1": 3.0})
    w.checkbox_perRegionLaserAFOffset.isChecked.return_value = True
    w.scanCoordinates = MagicMock()
    w.scanCoordinates.region_centers.keys.return_value = ["A1", "B2"]
    result = w.get_offsets_for_acquisition(reflection_af_active=True)
    assert result == {"A1": 3.0}
    assert w._log.warning.called  # B2 has no offset
